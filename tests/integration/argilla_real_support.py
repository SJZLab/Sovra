"""The REAL argilla-server (v2.8.0, `infra/docker/argilla/docker-compose.yml`) behind the small
surface the FTF-R006-P2 review-queue suite inspects
(`tests/integration/test_argilla_review_queue.py`).

Nothing here simulates Argilla. Every value is read from, and every edit is made on, the real
server over its own `/api/v1` REST API:

- `records` / `datasets` / `responses` / `users` are live reads with the provisioning (owner) key;
- `holds_bytes` is a real full-record scan plus the real `records/search` endpoint, run with the
  owner key and with every given annotator key, across every dataset the key can see;
- `edit_record_fields` / `inject_response` are real writes made behind the Factory's back;
- `stop` / `start` really stop and start the server container (`docker stop` / `docker start`,
  never a dead port).

`review_calls` is the one client-side record: every request made through a client built by
`client()` (method, path, API key), so a test can prove WHICH key the Factory used for a write.
The request still goes to the real server unchanged.
"""

from __future__ import annotations

import ipaddress
import os
import subprocess
import threading
import time
from collections.abc import Iterable, Mapping
from typing import Any
from urllib.parse import urlsplit

from packages.integrations.argilla.client import (
    ArgillaClient,
    ArgillaRejectedError,
    ArgillaTransport,
    ArgillaUnavailableError,
)
from packages.security.url_policy import ConnectionPolicy

_PAGE = 1000  # LIST_DATASET_RECORDS_LIMIT_LE in argilla-server 2.8.0


def _policy(port: int) -> ConnectionPolicy:
    return ConnectionPolicy(
        name="real-argilla-review-queue-ci",
        allowed_schemes=frozenset({"http"}),
        allowed_ports=frozenset({port}),
        explicit_private_allowlist=(ipaddress.ip_network("127.0.0.0/8"),),
    )


class _LoggingTransport(ArgillaTransport):
    """The real P1a transport; it only appends (method, path, key) before each real call."""

    def __init__(
        self,
        log: list[tuple[str, str, str | None]],
        lock: threading.Lock,
        base_url: str,
        policy: ConnectionPolicy,
        *,
        api_key: str | None,
        timeout: float,
    ) -> None:
        super().__init__(base_url, policy, api_key=api_key, timeout=timeout)
        self._log, self._log_lock, self._key = log, lock, api_key

    def call(self, method: str, path: str, **kw: Any) -> Any:
        with self._log_lock:
            self._log.append((method, f"/api/v1{path}", self._key))
        return super().call(method, path, **kw)


class RealArgillaServer:
    def __init__(self, base_url: str, owner_key: str, container: str) -> None:
        self.base_url = base_url
        self.port = urlsplit(base_url).port or 6900
        self.policy = _policy(self.port)
        self.owner_key = owner_key
        self.container = container
        self.review_calls: list[tuple[str, str, str | None]] = []
        self._lock = threading.Lock()
        self._owner = ArgillaClient(base_url, self.policy, api_key=owner_key, timeout=30.0)
        self.OWNER_USER_ID = str(self._owner._call("GET", "/me")["id"])

    # -- clients --------------------------------------------------------------------------

    def client(self, *, api_key: str | None = None, timeout: float = 15.0) -> ArgillaClient:
        key = self.owner_key if api_key is None else api_key
        made = ArgillaClient(self.base_url, self.policy, api_key=key, timeout=timeout)
        logged = _LoggingTransport(
            self.review_calls,
            self._lock,
            self.base_url,
            self.policy,
            api_key=key,
            timeout=timeout,
        )
        made.transport = logged
        return made

    def raw(self, api_key: str, method: str, path: str, **kw: Any) -> Any:
        return ArgillaClient(self.base_url, self.policy, api_key=api_key, timeout=30.0)._call(
            method, path, **kw
        )

    # -- live reads ------------------------------------------------------------------------

    @property
    def datasets(self) -> dict[str, dict[str, Any]]:
        found = self._owner._call("GET", "/me/datasets")
        return {str(d["id"]): d for d in found["items"]}

    def _dataset_records(self, api_key: str, dataset_id: str) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        offset = 0
        while True:
            page = self.raw(
                api_key,
                "GET",
                f"/datasets/{dataset_id}/records",
                query={"include": "responses", "offset": str(offset), "limit": str(_PAGE)},
            )
            out.extend(page["items"])
            if len(page["items"]) < _PAGE:
                return out
            offset += _PAGE

    @property
    def records(self) -> dict[str, dict[str, Any]]:
        found: dict[str, dict[str, Any]] = {}
        for dataset_id in self.datasets:
            for record in self._dataset_records(self.owner_key, dataset_id):
                record.setdefault("dataset_id", dataset_id)
                found[str(record["id"])] = record
        return found

    @property
    def responses(self) -> dict[str, dict[str, Any]]:
        found: dict[str, dict[str, Any]] = {}
        for record in self.records.values():
            for response in record.get("responses") or []:
                found[str(response["id"])] = {**response, "record_id": str(record["id"])}
        return found

    @property
    def users(self) -> dict[str, dict[str, Any]]:
        listed = self._owner._call("GET", "/users")
        return {str(u["id"]): u for u in listed["items"]}

    def holds_bytes(self, needle: bytes, *, annotator_keys: Iterable[str] = ()) -> bool:
        """True if `needle` is in ANY record field/metadata the owner key can list, or if the real
        `records/search` endpoint returns any record for it, for the owner or any given annotator,
        in any dataset that key can see (§78.4: sealed questions stay out of review search)."""
        text = needle.decode()
        for dataset_id in self.datasets:
            for record in self._dataset_records(self.owner_key, dataset_id):
                if text in repr(record.get("fields")) or text in repr(record.get("metadata")):
                    return True
        for key in (self.owner_key, *annotator_keys):
            visible = self.raw(key, "GET", "/me/datasets")["items"]
            for dataset in visible:
                if dataset.get("status") != "ready":
                    # A draft dataset has no search index and cannot hold records (the server
                    # refuses records before publish); the full scan above still covers it.
                    continue
                hits = self.raw(
                    key,
                    "POST",
                    f"/me/datasets/{dataset['id']}/records/search",
                    body={"query": {"text": {"q": text}}},
                )
                if hits.get("items"):
                    return True
        return False

    # -- real edits behind the Factory's back ------------------------------------------------

    def edit_record_fields(self, record_id: str, field: str, value: str) -> None:
        self._owner._call("PATCH", f"/records/{record_id}", body={"fields": {field: value}})

    def inject_response(self, record_id: str, user_id: str, decision: str) -> None:
        if user_id != self.OWNER_USER_ID:
            raise ValueError("the real server only lets a key write its own user's response")
        self._owner._call(
            "POST",
            f"/records/{record_id}/responses",
            body={"values": {"decision": {"value": decision}}, "status": "submitted"},
        )

    # -- a really stopped server -----------------------------------------------------------

    def stop(self) -> None:
        subprocess.run(
            ["docker", "stop", "-t", "5", self.container], check=True, capture_output=True
        )

    def start(self) -> None:
        subprocess.run(["docker", "start", self.container], check=True, capture_output=True)
        probe = ArgillaClient(self.base_url, self.policy, api_key=None, timeout=3.0)
        for _ in range(180):
            try:
                probe.get_version()
                self._owner._call("GET", "/me")
                return
            except (ArgillaUnavailableError, ArgillaRejectedError):
                time.sleep(1.0)
        raise RuntimeError("the real Argilla server did not come back after docker start")

    def shutdown(self) -> None:
        """Nothing to tear down: the server belongs to the CI job (compose stack)."""


def from_env() -> RealArgillaServer:
    owner_key = os.environ.get("ARGILLA_OWNER_API_KEY")
    if not owner_key:
        raise SystemExit("BLOCKED_EXTERNAL: ARGILLA_OWNER_API_KEY is not set (real Argilla stack)")
    return RealArgillaServer(
        os.environ.get("ARGILLA_BASE_URL", "http://127.0.0.1:6900"),
        owner_key,
        os.environ.get("ARGILLA_SERVER_CONTAINER", "argilla-server"),
    )


def annotator_keys(users: Mapping[str, Mapping[str, Any]]) -> list[str]:
    return [str(u["api_key"]) for u in users.values() if u.get("role") == "annotator"]
