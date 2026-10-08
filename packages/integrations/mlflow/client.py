"""FTF-R005 -- versioned internal MLflow tracking/registry REST adapter
(Section 77.1, 78.3).

The one place Factory code speaks to the self-hosted MLflow server. It is
a thin, fail-closed REST 2.0 client, not a second tracker and not the MLflow
SDK (a new runtime dependency would be ask-first, and Section 78.3 allows the
"pinned SDK/REST surface"). Every request goes through
`packages.security.outbound_guard.guarded_fetch` with a caller-supplied
`ConnectionPolicy` (the shared SSRF guard, no redirects, pinned address), so a
tracking URI can never be steered to an arbitrary or metadata destination.

Error classes keep the three outcomes apart so a caller can never turn one
into another (Law 17): `TrackingUnavailableError` (network, timeout, 5xx --
retryable, reported as pending), `TrackingRejectedError` (the server
understood and refused -- never retried as-is), `MlflowNotFoundError` and
`MlflowAlreadyExistsError` (the two MLflow error codes the reconciler
branches on). A malformed, duplicate-key, NaN/Infinity or oversized body is
`TrackingRejectedError`, never repaired.

Authority: nothing read back from MLflow (tags, aliases, stages) is ever an
approval. This client exposes no alias or stage write at all (Section 78.3:
"no writable promotion alias from normal UI").
"""

from __future__ import annotations

import json
import math
import re
from collections.abc import Mapping, Sequence
from typing import Any
from urllib.parse import urlencode, urlsplit

from packages.security.outbound_guard import OutboundGuardError, guarded_fetch
from packages.security.url_policy import ConnectionPolicy, OutboundPolicyError

__all__ = [
    "MAX_RESPONSE_BYTES",
    "MlflowAlreadyExistsError",
    "MlflowClient",
    "MlflowClientError",
    "MlflowNotFoundError",
    "TrackingRejectedError",
    "TrackingUnavailableError",
]

MAX_RESPONSE_BYTES = 4 * 1024 * 1024
MAX_PAGES = 20
_API = "/api/2.0/mlflow"
# Identifier-shaped values only: they are interpolated into MLflow filter
# strings, so a quote or backtick must never reach the query.
_SAFE_VALUE = re.compile(r"^[A-Za-z0-9._:/-]{1,256}$")
_SAFE_KEY = re.compile(r"^[A-Za-z0-9._/-]{1,250}$")
_RUN_ID = re.compile(r"^[0-9a-f]{32}$")
# Whitespace, control characters (incl. DEL) and backslashes never appear in an
# approved internal artifact location.
_UNSAFE_SOURCE_CHARS = re.compile(r"[\x00-\x20\x7f\\]")
_MAX_METRICS, _MAX_PARAMS, _MAX_TAGS = 1000, 100, 100


class MlflowClientError(Exception):
    """Base for every MLflow adapter failure."""


class TrackingUnavailableError(MlflowClientError):
    """Network failure, timeout or 5xx: the outcome is unknown, retry later."""


class TrackingRejectedError(MlflowClientError):
    """The server refused or returned something this adapter will not trust."""


class MlflowNotFoundError(TrackingRejectedError):
    """MLflow RESOURCE_DOES_NOT_EXIST."""


class MlflowAlreadyExistsError(TrackingRejectedError):
    """MLflow RESOURCE_ALREADY_EXISTS."""


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in pairs:
        if key in out:
            raise TrackingRejectedError(f"duplicate key {key!r} in MLflow response")
        out[key] = value
    return out


def _reject_constant(name: str) -> Any:
    raise TrackingRejectedError(f"non-finite constant {name} in MLflow response")


def parse_strict_json(raw: bytes) -> dict[str, Any]:
    if len(raw) > MAX_RESPONSE_BYTES:
        raise TrackingRejectedError("MLflow response exceeds the size bound")
    try:
        text = raw.decode("utf-8")
        parsed = json.loads(
            text, object_pairs_hook=_reject_duplicate_keys, parse_constant=_reject_constant
        )
    except TrackingRejectedError:
        raise
    except (UnicodeDecodeError, ValueError, RecursionError) as exc:
        raise TrackingRejectedError(f"MLflow response is not strict JSON: {exc}") from exc
    if not isinstance(parsed, dict):
        raise TrackingRejectedError("MLflow response is not a JSON object")
    return parsed


def _safe_value(value: object, *, name: str) -> str:
    if not isinstance(value, str) or not _SAFE_VALUE.fullmatch(value):
        raise TrackingRejectedError(f"{name} is not an identifier-shaped string")
    return value


def _safe_key(value: object, *, name: str) -> str:
    if not isinstance(value, str) or not _SAFE_KEY.fullmatch(value):
        raise TrackingRejectedError(f"{name} is not an identifier-shaped key")
    return value


def _tag_list(tags: Mapping[str, str] | None) -> list[dict[str, str]]:
    out = []
    for key, value in (tags or {}).items():
        if not isinstance(value, str):
            raise TrackingRejectedError(f"tag {key!r} value must be a string")
        out.append({"key": _safe_key(key, name="tag key"), "value": value})
    if len(out) > _MAX_TAGS:
        raise TrackingRejectedError("too many tags in one request")
    return out


class MlflowClient:
    """REST 2.0 adapter for one internal MLflow server."""

    def __init__(
        self,
        base_url: str,
        policy: ConnectionPolicy,
        *,
        token: str | None = None,
        timeout: float = 10.0,
    ) -> None:
        parts = urlsplit(base_url)
        if parts.path not in ("", "/") or parts.query or parts.fragment or parts.username:
            raise ValueError("base_url must be scheme://host[:port] only")
        if not isinstance(timeout, int | float) or isinstance(timeout, bool) or timeout <= 0:
            raise ValueError("timeout must be a positive number")
        self._base = f"{parts.scheme}://{parts.netloc}"
        self._policy = policy
        self._token = token
        self._timeout = float(timeout)

    # -- transport ---------------------------------------------------------

    def _call(
        self, method: str, path: str, *, query: Mapping[str, str] | None = None, body: Any = None
    ) -> dict[str, Any]:
        url = self._base + _API + path
        if query:
            url += "?" + urlencode(query)
        headers = {"Accept": "application/json"}
        payload: bytes | None = None
        if body is not None:
            payload = json.dumps(body, allow_nan=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        try:
            response = guarded_fetch(
                url,
                self._policy,
                method=method,
                headers=headers,
                body=payload,
                timeout=self._timeout,
            )
        except OutboundPolicyError as exc:
            raise TrackingRejectedError(f"destination denied by policy: {exc}") from exc
        except (OutboundGuardError, OSError) as exc:
            raise TrackingUnavailableError(f"MLflow unreachable: {exc}") from exc
        status = response.status
        if status >= 500:
            raise TrackingUnavailableError(f"MLflow returned HTTP {status}")
        if status != 200:
            code = ""
            try:
                code = str(parse_strict_json(response.body).get("error_code", ""))
            except TrackingRejectedError:
                pass
            if code == "RESOURCE_DOES_NOT_EXIST":
                raise MlflowNotFoundError(f"HTTP {status} {code}")
            if code == "RESOURCE_ALREADY_EXISTS":
                raise MlflowAlreadyExistsError(f"HTTP {status} {code}")
            raise TrackingRejectedError(
                f"MLflow returned HTTP {status}" + (f" {code}" if code else "")
            )
        return parse_strict_json(response.body)

    # -- experiments -------------------------------------------------------

    def get_or_create_experiment(self, name: str) -> str:
        _safe_key(name, name="experiment name")
        try:
            found = self._call("GET", "/experiments/get-by-name", query={"experiment_name": name})
            experiment = found.get("experiment")
            if isinstance(experiment, dict) and experiment.get("lifecycle_stage") == "active":
                return _safe_value(experiment.get("experiment_id"), name="experiment_id")
            raise TrackingRejectedError("experiment exists but is not active")
        except MlflowNotFoundError:
            pass
        try:
            created = self._call("POST", "/experiments/create", body={"name": name})
        except MlflowAlreadyExistsError:
            return self.get_or_create_experiment(name)
        return _safe_value(created.get("experiment_id"), name="experiment_id")

    # -- runs --------------------------------------------------------------

    def create_run(self, experiment_id: str, *, run_name: str, tags: Mapping[str, str]) -> str:
        body = {
            "experiment_id": _safe_value(experiment_id, name="experiment_id"),
            "run_name": _safe_key(run_name, name="run_name"),
            "start_time": 0,
            "tags": _tag_list(tags),
        }
        created = self._call("POST", "/runs/create", body=body)
        run = created.get("run")
        info = run.get("info") if isinstance(run, dict) else None
        run_id = info.get("run_id") if isinstance(info, dict) else None
        if not isinstance(run_id, str) or not _RUN_ID.fullmatch(run_id):
            raise TrackingRejectedError("MLflow returned no valid run_id")
        return run_id

    def get_run(self, run_id: str) -> dict[str, Any]:
        _require_run_id(run_id)
        found = self._call("GET", "/runs/get", query={"run_id": run_id})
        run = found.get("run")
        if not isinstance(run, dict):
            raise TrackingRejectedError("MLflow returned no run")
        return run

    def search_runs_by_tag(self, experiment_id: str, key: str, value: str) -> list[dict[str, Any]]:
        _safe_key(key, name="tag key")
        _safe_value(value, name="tag value")
        runs: list[dict[str, Any]] = []
        token: str | None = None
        for _ in range(MAX_PAGES):
            body: dict[str, Any] = {
                "experiment_ids": [_safe_value(experiment_id, name="experiment_id")],
                "filter": f"tags.`{key}` = '{value}'",
                "max_results": 100,
            }
            if token:
                body["page_token"] = token
            page = self._call("POST", "/runs/search", body=body)
            batch = page.get("runs", [])
            if not isinstance(batch, list) or not all(isinstance(r, dict) for r in batch):
                raise TrackingRejectedError("MLflow returned a malformed run list")
            runs.extend(batch)
            next_token = page.get("next_page_token")
            if not next_token:
                return runs
            if not isinstance(next_token, str) or next_token == token:
                raise TrackingRejectedError("MLflow pagination did not advance")
            token = next_token
        raise TrackingRejectedError("MLflow run search exceeded the page bound")

    def log_batch(
        self,
        run_id: str,
        *,
        metrics: Sequence[tuple[str, float, int, int]] = (),
        params: Mapping[str, str] | None = None,
        tags: Mapping[str, str] | None = None,
    ) -> None:
        """`metrics` are (key, value, timestamp_ms, step); timestamps are the
        caller's deterministic ones so a replay is a no-op row-for-row."""
        _require_run_id(run_id)
        if len(metrics) > _MAX_METRICS or len(params or {}) > _MAX_PARAMS:
            raise TrackingRejectedError("batch exceeds MLflow's per-request limits")
        metric_rows = []
        for key, value, timestamp, step in metrics:
            if (
                isinstance(value, bool)
                or not isinstance(value, int | float)
                or not math.isfinite(value)
            ):
                raise TrackingRejectedError(f"metric {key!r} is not a finite number")
            metric_rows.append(
                {
                    "key": _safe_key(key, name="metric key"),
                    "value": float(value),
                    "timestamp": int(timestamp),
                    "step": int(step),
                }
            )
        self._call(
            "POST",
            "/runs/log-batch",
            body={
                "run_id": run_id,
                "metrics": metric_rows,
                "params": _tag_list(params),
                "tags": _tag_list(tags),
            },
        )

    def finish_run(self, run_id: str, *, status: str, end_time_ms: int) -> None:
        _require_run_id(run_id)
        if status not in ("FINISHED", "FAILED", "KILLED"):
            raise TrackingRejectedError(f"unsupported run status {status!r}")
        self._call(
            "POST",
            "/runs/update",
            body={"run_id": run_id, "status": status, "end_time": int(end_time_ms)},
        )

    # -- registry ----------------------------------------------------------

    def ensure_registered_model(self, name: str, tags: Mapping[str, str]) -> None:
        _safe_key(name, name="model name")
        try:
            self._call("GET", "/registered-models/get", query={"name": name})
            return
        except MlflowNotFoundError:
            pass
        try:
            self._call(
                "POST", "/registered-models/create", body={"name": name, "tags": _tag_list(tags)}
            )
        except MlflowAlreadyExistsError:
            return

    def create_model_version(
        self,
        name: str,
        *,
        source: str,
        run_id: str,
        tags: Mapping[str, str],
        approved_source_prefixes: Sequence[str],
    ) -> dict[str, Any]:
        """Refuses any `source` outside the approved internal artifact
        prefixes before a byte is sent (Section 78.3: a registry entry
        resolves only approved internal artifact locations)."""
        _require_run_id(run_id)
        if not is_approved_source(source, approved_source_prefixes):
            raise TrackingRejectedError("model source is outside the approved artifact prefixes")
        created = self._call(
            "POST",
            "/model-versions/create",
            body={
                "name": _safe_key(name, name="model name"),
                "source": source,
                "run_id": run_id,
                "tags": _tag_list(tags),
            },
        )
        version = created.get("model_version")
        if not isinstance(version, dict):
            raise TrackingRejectedError("MLflow returned no model_version")
        return version

    def search_model_versions_by_tag(self, name: str, key: str, value: str) -> list[dict[str, Any]]:
        _safe_key(name, name="model name")
        _safe_key(key, name="tag key")
        _safe_value(value, name="tag value")
        found = self._call(
            "GET",
            "/model-versions/search",
            query={"filter": f"name = '{name}'", "max_results": "200"},
        )
        versions = found.get("model_versions", [])
        if not isinstance(versions, list) or not all(isinstance(v, dict) for v in versions):
            raise TrackingRejectedError("MLflow returned a malformed version list")
        out = []
        for version in versions:
            tag_map = _tags_of(version)
            if tag_map.get(key) == value:
                out.append(version)
        return out

    def get_model_version(self, name: str, version: str) -> dict[str, Any]:
        found = self._call(
            "GET",
            "/model-versions/get",
            query={
                "name": _safe_key(name, name="model name"),
                "version": _safe_value(version, name="version"),
            },
        )
        record = found.get("model_version")
        if not isinstance(record, dict):
            raise TrackingRejectedError("MLflow returned no model_version")
        return record


def _require_run_id(run_id: object) -> None:
    if not isinstance(run_id, str) or not _RUN_ID.fullmatch(run_id):
        raise TrackingRejectedError("run_id is not a 32-hex MLflow run id")


def _tags_of(record: Mapping[str, Any]) -> dict[str, str]:
    raw = record.get("tags", [])
    if isinstance(raw, dict):
        return {str(k): str(v) for k, v in raw.items()}
    out: dict[str, str] = {}
    if isinstance(raw, list):
        for item in raw:
            if isinstance(item, dict) and "key" in item:
                out[str(item["key"])] = str(item.get("value", ""))
    return out


def run_tags(run: Mapping[str, Any]) -> dict[str, str]:
    data = run.get("data")
    return _tags_of(data) if isinstance(data, dict) else {}


def is_approved_source(source: object, prefixes: Sequence[str]) -> bool:
    """True only for a source that starts with an approved internal prefix
    and carries no traversal, userinfo, query or fragment."""
    if not isinstance(source, str) or not source or not prefixes:
        return False
    if _UNSAFE_SOURCE_CHARS.search(source):
        return False
    parts = urlsplit(source)
    if parts.query or parts.fragment or parts.username is not None:
        return False
    if ".." in source.split("/") or "%2e" in source.lower() or "//" in source.split("://", 1)[-1]:
        return False
    return any(source.startswith(p) and p.endswith("/") for p in prefixes)
