#!/usr/bin/env python3
"""FTF-R005 -- MLflow tracking/registry projection (no-network unit/fault tests).

Runs without an MLflow server. The real-server evidence (reconcile, outage,
forged tags, tenant isolation against a live `mlflow server`) is
`tests/integration/test_mlflow_projection.py`. This suite proves, with a
scripted client and a loopback HTTP stub:

R005-C  client.py wire behaviour: strict JSON, error-class mapping, no
        redirects, identifier-only filter values, approved-source refusal
        before any request, bounded batches and pagination.
R005-R  reconcile.py: deterministic, tenant-separated run key; TrackedRun
        validation incl. sealed refusal; idempotent outbox state machine
        (pending/synced/failed/ambiguous) and drift refusal.
R005-B  registry_binding.py: bind/verify field-by-field drift, tenant-derived
        names, read-only fixed projection that ignores forged tags/aliases.

Run directly: python3 tests/tasks/test_ftf_r005.py
"""

from __future__ import annotations

import ipaddress
import json
import sys
import threading
import uuid
from collections.abc import Mapping
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from packages.integrations.mlflow import client as client_mod  # noqa: E402
from packages.integrations.mlflow.client import (  # noqa: E402
    MAX_PAGES,
    MlflowAlreadyExistsError,
    MlflowClient,
    MlflowNotFoundError,
    TrackingRejectedError,
    TrackingUnavailableError,
    is_approved_source,
    parse_strict_json,
)
from packages.integrations.mlflow.reconcile import (  # noqa: E402
    TAG_KEY,
    TAG_ORG,
    TAG_PAYLOAD,
    InMemoryOutbox,
    RunConflictError,
    SealedExportRefusedError,
    SyncState,
    TrackedRun,
    experiment_name,
    logical_run_key,
    sync_pending,
    sync_run,
)
from packages.integrations.mlflow.registry_binding import (  # noqa: E402
    PROJECTION_FIELDS,
    TAG_ARTIFACT,
    TAG_MODEL_VERSION,
    RegistryBinding,
    RegistryDriftError,
    bind_model_version,
    project_registry_binding,
    registered_model_name,
    verify_binding,
)
from packages.integrations.mlflow.registry_binding import (  # noqa: E402
    TAG_ORG as REG_TAG_ORG,
)
from packages.security.url_policy import ConnectionPolicy  # noqa: E402

FAILURES: list[str] = []
PREFIXES = ("s3://sovra-model-artifacts/",)
ORG_A, ORG_B, JOB, MV = uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
RUN_ID = "a" * 32


def check(criterion_id: str, description: str, condition: bool) -> None:
    if not condition:
        FAILURES.append(f"FAIL [{criterion_id}] {description}")


def raises(exc: type[BaseException], fn: Any, *args: Any, **kwargs: Any) -> bool:
    try:
        fn(*args, **kwargs)
    except exc:
        return True
    except Exception:
        return False
    return False


# -- loopback HTTP stub ---------------------------------------------------


class Stub:
    def __init__(self) -> None:
        self.script: list[tuple[int, bytes, dict[str, str]]] = []
        self.seen: list[dict[str, Any]] = []
        stub = self

        class Handler(BaseHTTPRequestHandler):
            def _serve(self) -> None:
                length = int(self.headers.get("Content-Length") or 0)
                stub.seen.append(
                    {
                        "method": self.command,
                        "path": self.path,
                        "auth": self.headers.get("Authorization"),
                        "body": self.rfile.read(length) if length else b"",
                    }
                )
                status, body, headers = stub.script.pop(0) if stub.script else (200, b"{}", {})
                self.send_response(status)
                for key, value in headers.items():
                    self.send_header(key, value)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            do_GET = do_POST = _serve

            def log_message(self, *args: Any) -> None:
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def client(self, **kw: Any) -> MlflowClient:
        policy = ConnectionPolicy(
            name="stub",
            allowed_schemes=frozenset({"http"}),
            allowed_ports=frozenset({self.port}),
            explicit_private_allowlist=(ipaddress.ip_network("127.0.0.0/8"),),
        )
        return MlflowClient(f"http://127.0.0.1:{self.port}", policy, **kw)

    def reply(self, status: int, payload: Any, headers: dict[str, str] | None = None) -> None:
        raw = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
        self.script.append((status, raw, headers or {}))


def test_client_wire() -> None:
    stub = Stub()
    c = stub.client(token="tok")
    stub.reply(200, {"experiment": {"experiment_id": "7", "lifecycle_stage": "active"}})
    check("R005-C", "get experiment id", c.get_or_create_experiment("sovra/x") == "7")
    check("R005-C", "bearer token sent", stub.seen[-1]["auth"] == "Bearer tok")
    check(
        "R005-C",
        "GET path is the 2.0 REST path",
        "/api/2.0/mlflow/experiments/get-by-name" in stub.seen[-1]["path"],
    )
    # not found -> create
    stub.reply(404, {"error_code": "RESOURCE_DOES_NOT_EXIST"})
    stub.reply(200, {"experiment_id": "9"})
    check("R005-C", "create after not-found", c.get_or_create_experiment("sovra/y") == "9")
    # deleted experiment is refused, not reused
    stub.reply(200, {"experiment": {"experiment_id": "7", "lifecycle_stage": "deleted"}})
    check(
        "R005-C",
        "deleted experiment refused",
        raises(TrackingRejectedError, c.get_or_create_experiment, "sovra/z"),
    )
    # create race: already exists -> re-get
    stub.reply(404, {"error_code": "RESOURCE_DOES_NOT_EXIST"})
    stub.reply(400, {"error_code": "RESOURCE_ALREADY_EXISTS"})
    stub.reply(200, {"experiment": {"experiment_id": "11", "lifecycle_stage": "active"}})
    check("R005-C", "create race re-reads", c.get_or_create_experiment("sovra/r") == "11")
    # error mapping
    stub.reply(500, {})
    check("R005-C", "5xx is unavailable", raises(TrackingUnavailableError, c.get_run, RUN_ID))
    stub.reply(404, {"error_code": "RESOURCE_DOES_NOT_EXIST"})
    check("R005-C", "404 code is NotFound", raises(MlflowNotFoundError, c.get_run, RUN_ID))
    stub.reply(400, {"error_code": "RESOURCE_ALREADY_EXISTS"})
    check(
        "R005-C",
        "exists code is AlreadyExists",
        raises(MlflowAlreadyExistsError, c.get_run, RUN_ID),
    )
    stub.reply(403, {"error_code": "PERMISSION_DENIED"})
    check(
        "R005-C",
        "403 is rejected not unavailable",
        raises(TrackingRejectedError, c.get_run, RUN_ID),
    )
    stub.reply(400, b"not json")
    check("R005-C", "non-JSON error is rejected", raises(TrackingRejectedError, c.get_run, RUN_ID))
    stub.reply(200, b'{"run": {"a": 1}, "run": {}}')
    check("R005-C", "duplicate key rejected", raises(TrackingRejectedError, c.get_run, RUN_ID))
    stub.reply(200, b'{"run": NaN}')
    check("R005-C", "NaN rejected", raises(TrackingRejectedError, c.get_run, RUN_ID))
    stub.reply(200, b"[]")
    check("R005-C", "non-object rejected", raises(TrackingRejectedError, c.get_run, RUN_ID))
    stub.reply(200, {"nothing": 1})
    check("R005-C", "missing run rejected", raises(TrackingRejectedError, c.get_run, RUN_ID))
    # redirects are never followed
    n = len(stub.seen)
    stub.reply(302, b"", {"Location": f"http://127.0.0.1:{stub.port}/elsewhere"})
    check(
        "R005-C",
        "redirect not followed",
        raises(TrackingRejectedError, c.get_run, RUN_ID) and len(stub.seen) == n + 1,
    )
    # unreachable
    dead = Stub()
    dead_client = dead.client()
    dead.server.shutdown()
    dead.server.server_close()
    check(
        "R005-C",
        "refused connection is unavailable",
        raises(TrackingUnavailableError, dead_client.get_run, RUN_ID),
    )
    # policy denial is a rejection, not an outage
    denied = MlflowClient(
        f"http://127.0.0.1:{stub.port}",
        ConnectionPolicy(
            name="deny", allowed_schemes=frozenset({"http"}), allowed_ports=frozenset({stub.port})
        ),
    )
    check(
        "R005-C",
        "policy-denied destination is rejected",
        raises(TrackingRejectedError, denied.get_run, RUN_ID),
    )
    # base_url / timeout validation
    pol = ConnectionPolicy(name="p")
    for bad in ("https://h/path", "https://h?q=1", "https://u@h", "https://h#f"):
        check("R005-C", f"base_url {bad} refused", raises(ValueError, MlflowClient, bad, pol))
    for bad_t in (0, -1, True, "5"):
        check(
            "R005-C",
            f"timeout {bad_t!r} refused",
            raises(ValueError, MlflowClient, "https://h", pol, timeout=bad_t),
        )
    stub.server.shutdown()


def test_client_inputs() -> None:
    stub = Stub()
    c = stub.client()
    n = len(stub.seen)
    check(
        "R005-C",
        "quote in tag value refused",
        raises(TrackingRejectedError, c.search_runs_by_tag, "1", "k", "x' OR '1'='1"),
    )
    check(
        "R005-C",
        "backtick in tag key refused",
        raises(TrackingRejectedError, c.search_runs_by_tag, "1", "k`", "v"),
    )
    check(
        "R005-C",
        "empty experiment id refused",
        raises(TrackingRejectedError, c.search_runs_by_tag, "", "k", "v"),
    )
    check("R005-C", "bad run id refused", raises(TrackingRejectedError, c.get_run, "xyz"))
    check("R005-C", "uppercase run id refused", raises(TrackingRejectedError, c.get_run, "A" * 32))
    check(
        "R005-C",
        "bad status refused",
        raises(TrackingRejectedError, c.finish_run, RUN_ID, status="RUNNING", end_time_ms=1),
    )
    for bad in (float("nan"), float("inf"), True, "1"):
        check(
            "R005-C",
            f"metric {bad!r} refused",
            raises(TrackingRejectedError, c.log_batch, RUN_ID, metrics=[("m", bad, 1, 0)]),
        )
    check(
        "R005-C",
        "1001 metrics refused",
        raises(
            TrackingRejectedError,
            c.log_batch,
            RUN_ID,
            metrics=[(f"m{i}", 1.0, 1, 0) for i in range(1001)],
        ),
    )
    check(
        "R005-C",
        "1000 metrics allowed boundary",
        not raises(
            TrackingRejectedError,
            c.log_batch,
            RUN_ID,
            metrics=[(f"m{i}", 1.0, 1, 0) for i in range(1000)],
        ),
    )
    check(
        "R005-C",
        "101 params refused",
        raises(
            TrackingRejectedError, c.log_batch, RUN_ID, params={f"p{i}": "v" for i in range(101)}
        ),
    )
    check(
        "R005-C",
        "101 tags refused",
        raises(TrackingRejectedError, c.log_batch, RUN_ID, tags={f"t{i}": "v" for i in range(101)}),
    )
    check(
        "R005-C",
        "non-str tag value refused",
        raises(TrackingRejectedError, c.log_batch, RUN_ID, tags={"t": 1}),
    )
    sent = len(stub.seen)
    check("R005-C", "rejected inputs sent nothing", sent == n + 1)
    # approved-source refusal happens before any request
    before = len(stub.seen)
    check(
        "R005-C",
        "unapproved source refused",
        raises(
            TrackingRejectedError,
            c.create_model_version,
            "m",
            source="https://evil/",
            run_id=RUN_ID,
            tags={},
            approved_source_prefixes=PREFIXES,
        ),
    )
    check("R005-C", "unapproved source sent no request", len(stub.seen) == before)
    stub.reply(200, {"model_version": {"version": "1"}})
    ok = c.create_model_version(
        "m",
        source="s3://sovra-model-artifacts/o/",
        run_id=RUN_ID,
        tags={"a": "b"},
        approved_source_prefixes=PREFIXES,
    )
    check("R005-C", "approved source creates", ok.get("version") == "1")
    stub.reply(200, {})
    check(
        "R005-C",
        "missing model_version rejected",
        raises(
            TrackingRejectedError,
            c.create_model_version,
            "m",
            source="s3://sovra-model-artifacts/o/",
            run_id=RUN_ID,
            tags={},
            approved_source_prefixes=PREFIXES,
        ),
    )
    # pagination: repeated token and page cap
    stub.reply(200, {"runs": [], "next_page_token": "t"})
    stub.reply(200, {"runs": [], "next_page_token": "t"})
    check(
        "R005-C",
        "repeated page token refused",
        raises(TrackingRejectedError, c.search_runs_by_tag, "1", "k", "v"),
    )
    for i in range(MAX_PAGES + 2):
        stub.reply(200, {"runs": [], "next_page_token": f"t{i}"})
    check(
        "R005-C",
        "page bound enforced",
        raises(TrackingRejectedError, c.search_runs_by_tag, "1", "k", "v"),
    )
    stub.script.clear()
    stub.reply(200, {"runs": [{"info": {"run_id": RUN_ID}}], "next_page_token": "n"})
    stub.reply(200, {"runs": [{"info": {"run_id": "b" * 32}}]})
    found = c.search_runs_by_tag("1", "k", "v")
    check(
        "R005-C",
        "pagination concatenates pages",
        [r["info"]["run_id"] for r in found] == [RUN_ID, "b" * 32],
    )
    stub.reply(200, {"runs": "nope"})
    check(
        "R005-C",
        "malformed run list refused",
        raises(TrackingRejectedError, c.search_runs_by_tag, "1", "k", "v"),
    )
    # filter string and request shape
    stub.script.clear()
    stub.reply(200, {"runs": []})
    c.search_runs_by_tag("5", "factory.logical_run_key", "run-1")
    body = json.loads(stub.seen[-1]["body"])
    check(
        "R005-C",
        "filter binds key and value",
        body["filter"] == "tags.`factory.logical_run_key` = 'run-1'"
        and body["experiment_ids"] == ["5"],
    )
    stub.server.shutdown()


def test_strict_json_and_sources() -> None:
    check("R005-C", "valid object parses", parse_strict_json(b'{"a": 1}') == {"a": 1})
    check(
        "R005-C", "invalid utf8 refused", raises(TrackingRejectedError, parse_strict_json, b"\xff")
    )
    check(
        "R005-C",
        "oversize refused",
        raises(
            TrackingRejectedError, parse_strict_json, b" " * (client_mod.MAX_RESPONSE_BYTES + 1)
        ),
    )
    check(
        "R005-C",
        "-Infinity refused",
        raises(TrackingRejectedError, parse_strict_json, b'{"a": -Infinity}'),
    )
    check(
        "R005-C",
        "deep nesting refused",
        raises(TrackingRejectedError, parse_strict_json, b"[" * 100000 + b"]" * 100000),
    )
    good = "s3://sovra-model-artifacts/org/mv/"
    check("R005-C", "approved prefix accepted", is_approved_source(good, PREFIXES))
    for bad in (
        "",
        None,
        "s3://other/x/",
        "s3://sovra-model-artifacts",
        "s3://sovra-model-artifacts/a/../b/",
        "s3://sovra-model-artifacts/a/%2e%2e/b/",
        "s3://sovra-model-artifacts/a?x=1",
        "s3://sovra-model-artifacts/a#f",
        "s3://u@sovra-model-artifacts/a/",
        "s3://sovra-model-artifacts//a/",
        "s3://sovra-model-artifacts/a b/",
        "s3://sovra-model-artifacts/a\\b/",
        "s3://sovra-model-artifacts/a\n/",
        5,
    ):
        check("R005-C", f"source {bad!r} refused", not is_approved_source(bad, PREFIXES))
    check("R005-C", "no prefixes refuses all", not is_approved_source(good, ()))
    check(
        "R005-C",
        "prefix without trailing slash never matches",
        not is_approved_source(
            "s3://sovra-model-artifacts-evil/x/", ("s3://sovra-model-artifacts",)
        ),
    )
    check("R005-C", "file scheme refused", not is_approved_source("file:///etc/passwd", PREFIXES))


# -- scripted client for reconcile/registry --------------------------------


def tag_list(d: Mapping[str, str]) -> list[dict[str, str]]:
    return [{"key": k, "value": v} for k, v in d.items()]


class Fake(MlflowClient):
    def __init__(self) -> None:
        self.runs: list[dict[str, Any]] = []
        self.calls: list[str] = []
        self.fail: dict[str, BaseException] = {}
        self.versions: list[dict[str, Any]] = []
        self.models: dict[str, Mapping[str, str]] = {}
        self.after_create: Any = None

    def _maybe(self, name: str) -> None:
        self.calls.append(name)
        if name in self.fail:
            raise self.fail[name]

    def get_or_create_experiment(self, name: str) -> str:
        self._maybe("experiment")
        return "1"

    def create_run(self, experiment_id: str, *, run_name: str, tags: Mapping[str, str]) -> str:
        self._maybe("create_run")
        run_id = f"{len(self.runs) + 1:032x}"
        self.runs.append({"info": {"run_id": run_id}, "data": {"tags": tag_list(tags)}})
        return run_id

    def search_runs_by_tag(self, experiment_id: str, key: str, value: str) -> list[dict[str, Any]]:
        self._maybe("search")
        return [
            r
            for r in self.runs
            if {t["key"]: t["value"] for t in r["data"]["tags"]}.get(key) == value
        ]

    def log_batch(
        self, run_id: str, *, metrics: Any = (), params: Any = None, tags: Any = None
    ) -> None:
        self._maybe("log_batch")
        self.calls.append(f"log:{sorted((tags or {}).keys())}")
        for run in self.runs:
            if run["info"]["run_id"] == run_id:
                run["data"]["tags"].extend(tag_list(tags or {}))
                run.setdefault("logged", []).append((list(metrics), dict(params or {})))

    def finish_run(self, run_id: str, *, status: str, end_time_ms: int) -> None:
        self._maybe("finish")

    def ensure_registered_model(self, name: str, tags: Mapping[str, str]) -> None:
        self._maybe("ensure_model")
        self.models[name] = tags

    def search_model_versions_by_tag(self, name: str, key: str, value: str) -> list[dict[str, Any]]:
        self._maybe("search_versions")
        return [
            v
            for v in self.versions
            if v["_n"] == name and {t["key"]: t["value"] for t in v["tags"]}.get(key) == value
        ]

    def create_model_version(
        self,
        name: str,
        *,
        source: str,
        run_id: str,
        tags: Mapping[str, str],
        approved_source_prefixes: Any,
    ) -> dict[str, Any]:
        self._maybe("create_version")
        record = {
            "_n": name,
            "name": name,
            "version": str(len(self.versions) + 1),
            "source": source,
            "run_id": run_id,
            "tags": tag_list(tags),
        }
        self.versions.append(record)
        if self.after_create is not None:
            self.after_create(record)
        return record

    def get_model_version(self, name: str, version: str) -> dict[str, Any]:
        self._maybe("get_version")
        for v in self.versions:
            if v["_n"] == name and v["version"] == version:
                return v
        raise MlflowNotFoundError("nope")


def make_run(**over: Any) -> TrackedRun:
    fields: dict[str, Any] = {
        "organization_id": ORG_A,
        "job_id": JOB,
        "completed_at_ms": 5,
        "metrics": (("m", 1.0),),
        "params": (("p", "v"),),
        "hashes": (("h", "a" * 64),),
        "data_purpose": "development_eval",
    }
    fields.update(over)
    return TrackedRun(**fields)


def test_run_key_and_validation() -> None:
    k = logical_run_key(ORG_A, JOB)
    check("R005-R", "key deterministic", k == logical_run_key(ORG_A, JOB))
    check("R005-R", "key differs by org", k != logical_run_key(ORG_B, JOB))
    check("R005-R", "key differs by job", k != logical_run_key(ORG_A, uuid.uuid4()))
    check("R005-R", "key shape", k.startswith("run-") and len(k) == 68)
    check("R005-R", "str org refused", raises(ValueError, logical_run_key, str(ORG_A), JOB))
    check("R005-R", "str job refused", raises(ValueError, logical_run_key, ORG_A, str(JOB)))
    check(
        "R005-R",
        "experiment per org",
        experiment_name(ORG_A) == f"sovra/{ORG_A}"
        and experiment_name(ORG_A) != experiment_name(ORG_B),
    )
    check("R005-R", "experiment str refused", raises(ValueError, experiment_name, str(ORG_A)))
    base = make_run()
    check("R005-R", "valid run constructs", base.payload_sha256() == make_run().payload_sha256())
    for purpose in ("final_qualification", "sealed_final"):
        check(
            "R005-R",
            f"sealed purpose {purpose} refused",
            raises(SealedExportRefusedError, make_run, data_purpose=purpose),
        )
    for cls in ("raw_trace", "", "METRICS_ONLY", "sealed_trace"):
        check(
            "R005-R",
            f"export class {cls!r} refused",
            raises(SealedExportRefusedError, make_run, export_class=cls),
        )
    check(
        "R005-R",
        "redacted metadata allowed",
        not raises(SealedExportRefusedError, make_run, export_class="redacted_metadata"),
    )
    bads: dict[str, dict[str, Any]] = {
        "nan metric": {"metrics": (("m", float("nan")),)},
        "inf metric": {"metrics": (("m", float("inf")),)},
        "bool metric": {"metrics": (("m", True),)},
        "str metric": {"metrics": (("m", "1"),)},
        "dup metric": {"metrics": (("m", 1.0), ("m", 2.0))},
        "bad metric key": {"metrics": (("m k", 1.0),)},
        "dup param": {"params": (("p", "a"), ("p", "b"))},
        "bad param key": {"params": (("p'", "a"),)},
        "int param": {"params": (("p", 1),)},
        "long param": {"params": (("p", "x" * 501),)},
        "bad hash": {"hashes": (("h", "A" * 64),)},
        "short hash": {"hashes": (("h", "a" * 63),)},
        "dup hash": {"hashes": (("h", "a" * 64), ("h", "b" * 64))},
        "bad hash key": {"hashes": (("h h", "a" * 64),)},
        "neg time": {"completed_at_ms": -1},
        "bool time": {"completed_at_ms": True},
        "float time": {"completed_at_ms": 1.5},
        "bad status": {"status": "RUNNING"},
        "str org": {"organization_id": str(ORG_A)},
        "str job": {"job_id": str(JOB)},
    }
    for label, over in bads.items():
        check("R005-R", f"{label} refused", raises(ValueError, make_run, **over))
    check(
        "R005-R",
        "500-char param allowed",
        not raises(ValueError, make_run, params=(("p", "x" * 500),)),
    )
    check("R005-R", "zero time allowed", not raises(ValueError, make_run, completed_at_ms=0))
    h = base.payload_sha256()
    variants: dict[str, dict[str, Any]] = {
        "org": {"organization_id": ORG_B},
        "job": {"job_id": uuid.uuid4()},
        "time": {"completed_at_ms": 6},
        "metric": {"metrics": (("m", 2.0),)},
        "param": {"params": (("p", "w"),)},
        "hash": {"hashes": (("h", "b" * 64),)},
        "status": {"status": "FAILED"},
    }
    for label, over in variants.items():
        check("R005-R", f"payload hash binds {label}", make_run(**over).payload_sha256() != h)
    check(
        "R005-R",
        "payload hash is order-independent",
        make_run(metrics=(("a", 1.0), ("b", 2.0))).payload_sha256()
        == make_run(metrics=(("b", 2.0), ("a", 1.0))).payload_sha256(),
    )


def test_outbox_and_sync() -> None:
    box = InMemoryOutbox()
    a = box.add(make_run())
    check("R005-R", "add is idempotent", box.add(make_run()) is a)
    check(
        "R005-R",
        "conflicting payload refused",
        raises(RunConflictError, box.add, make_run(metrics=(("m", 9.0),))),
    )
    check("R005-R", "new entries pending", box.pending() == [a] and a.state == SyncState.PENDING)
    fake = Fake()
    done = sync_run(fake, a)
    check(
        "R005-R",
        "sync succeeds",
        done.state == SyncState.SYNCED and done.mlflow_run_id is not None and done.attempts == 1,
    )
    check(
        "R005-R",
        "completion marker is logged last",
        fake.calls[-1] == f"log:{[TAG_PAYLOAD]}"
        and fake.calls.index("finish") < len(fake.calls) - 1,
    )
    check(
        "R005-R",
        "run carries key and org tags",
        {t["key"]: t["value"] for t in fake.runs[0]["data"]["tags"]}[TAG_ORG] == str(ORG_A),
    )
    check(
        "R005-R",
        "result written with deterministic ts",
        fake.runs[0]["logged"][0][0] == [("m", 1.0, 5, 0)],
    )
    check("R005-R", "synced entry leaves pending", box.pending() == [])
    n = len(fake.calls)
    again = sync_run(fake, a)
    check(
        "R005-R",
        "replay is a no-op write-wise",
        again.state == SyncState.SYNCED
        and not any(c in ("create_run", "finish") for c in fake.calls[n:])
        and not any(c.startswith("log:") for c in fake.calls[n:]),
    )
    check("R005-R", "replay makes no second run", len(fake.runs) == 1)
    # drift: completed run holds a different payload
    fake.runs[0]["data"]["tags"] = [
        t for t in fake.runs[0]["data"]["tags"] if t["key"] != TAG_PAYLOAD
    ] + tag_list({TAG_PAYLOAD: "0" * 64})
    drift = sync_run(fake, a)
    check(
        "R005-R",
        "different completed payload is FAILED",
        drift.state == SyncState.FAILED and "different payload" in (drift.last_error or ""),
    )
    # outage at every step -> pending, result kept
    for step in ("experiment", "search", "create_run", "log_batch", "finish"):
        f2, b2 = Fake(), InMemoryOutbox()
        e2 = b2.add(make_run())
        f2.fail[step] = TrackingUnavailableError("down")
        out = sync_run(f2, e2)
        check(
            "R005-R",
            f"outage at {step} stays pending",
            out.state == SyncState.PENDING
            and out.last_error == "down"
            and out.run.metrics == (("m", 1.0),),
        )
    # outage after run creation never marks synced and retains the run id
    f3, b3 = Fake(), InMemoryOutbox()
    e3 = b3.add(make_run())
    f3.fail["finish"] = TrackingUnavailableError("down")
    sync_run(f3, e3)
    check(
        "R005-R",
        "partial run id retained",
        e3.mlflow_run_id is not None and e3.state == SyncState.PENDING,
    )
    f3.fail.clear()
    sync_pending(f3, b3)
    check(
        "R005-R",
        "restore completes without duplicate run",
        e3.state == SyncState.SYNCED and len(f3.runs) == 1,
    )
    # rejection and ambiguity are visible, not success
    f4, b4 = Fake(), InMemoryOutbox()
    e4 = b4.add(make_run())
    f4.fail["log_batch"] = TrackingRejectedError("no")
    check("R005-R", "rejection is FAILED", sync_run(f4, e4).state == SyncState.FAILED)
    check("R005-R", "failed entry is not pending", b4.pending() == [])
    f5, b5 = Fake(), InMemoryOutbox()
    e5 = b5.add(make_run())
    key = logical_run_key(ORG_A, JOB)
    for _ in range(2):
        f5.create_run("1", run_name=key, tags={TAG_KEY: key, TAG_ORG: str(ORG_A)})
    amb = sync_run(f5, e5)
    check(
        "R005-R",
        "two claimants is AMBIGUOUS",
        amb.state == SyncState.AMBIGUOUS and amb.mlflow_run_id is None,
    )
    check(
        "R005-R",
        "ambiguity writes nothing",
        not any(c in ("log_batch", "finish") for c in f5.calls),
    )
    # foreign tenant tag
    f6, b6 = Fake(), InMemoryOutbox()
    e6 = b6.add(make_run())
    f6.create_run("1", run_name=key, tags={TAG_KEY: key, TAG_ORG: str(ORG_B)})
    check("R005-R", "foreign-tenant run is FAILED", sync_run(f6, e6).state == SyncState.FAILED)
    check("R005-R", "foreign-tenant run untouched", "log_batch" not in f6.calls)
    f6b, b6b = Fake(), InMemoryOutbox()
    f6b.runs.append({"info": {}, "data": {"tags": tag_list({TAG_KEY: key, TAG_ORG: str(ORG_A)})}})
    check(
        "R005-R",
        "run without id is FAILED",
        sync_run(f6b, b6b.add(make_run())).state == SyncState.FAILED,
    )
    # sync_pending stops at the first outage and keeps order
    f7, b7 = Fake(), InMemoryOutbox()
    first, second = b7.add(make_run()), b7.add(make_run(job_id=uuid.uuid4()))
    f7.fail["experiment"] = TrackingUnavailableError("down")
    res = sync_pending(f7, b7)
    check(
        "R005-R",
        "sync_pending stops on outage",
        len(res) == 1 and first.attempts == 1 and second.attempts == 0,
    )
    f7.fail.clear()
    check(
        "R005-R",
        "sync_pending drains after recovery",
        all(e.state == SyncState.SYNCED for e in sync_pending(f7, b7)) and b7.pending() == [],
    )
    # a non-pending failure does not stop the rest
    f8, b8 = Fake(), InMemoryOutbox()
    bad_e, good_e = b8.add(make_run()), b8.add(make_run(job_id=uuid.uuid4()))
    f8.fail["log_batch"] = TrackingRejectedError("no")
    res8 = sync_pending(f8, b8)
    check(
        "R005-R",
        "failed entry does not stop the batch",
        len(res8) == 2 and bad_e.state == good_e.state == SyncState.FAILED,
    )


def make_binding(client: Fake) -> RegistryBinding:
    return bind_model_version(
        client,
        organization_id=ORG_A,
        model_version_id=MV,
        run_id=RUN_ID,
        artifact_sha256="c" * 64,
        source_uri=f"s3://sovra-model-artifacts/{ORG_A}/{MV}/",
        approved_source_prefixes=PREFIXES,
    )


def test_registry_binding() -> None:
    fake = Fake()
    b = make_binding(fake)
    name = registered_model_name(ORG_A, MV)
    check(
        "R005-B",
        "name derives from org and ModelVersion",
        b.registered_model_name == name == f"sovra-{ORG_A}-{MV}",
    )
    check("R005-B", "name differs per org", registered_model_name(ORG_B, MV) != name)
    check("R005-B", "name str refused", raises(ValueError, registered_model_name, str(ORG_A), MV))
    check(
        "R005-B",
        "model carries identity tags",
        fake.models[name] == {REG_TAG_ORG: str(ORG_A), TAG_MODEL_VERSION: str(MV)},
    )
    check(
        "R005-B",
        "version carries artifact digest tag",
        {t["key"]: t["value"] for t in fake.versions[0]["tags"]}[TAG_ARTIFACT] == "c" * 64,
    )
    check(
        "R005-B",
        "replay finds the existing version",
        make_binding(fake) == b and len(fake.versions) == 1,
    )
    verify_binding(fake, b, approved_source_prefixes=PREFIXES)
    # forged approval-looking tags / aliases do not change verification or projection
    fake.versions[0]["tags"] += tag_list({"factory.approved": "true", "stage": "Production"})
    fake.versions[0]["aliases"] = ["champion"]
    verify_binding(fake, b, approved_source_prefixes=PREFIXES)
    body = project_registry_binding(b, drift=None)
    check("R005-B", "projection fixed field set", tuple(body) == PROJECTION_FIELDS)
    check(
        "R005-B",
        "projection carries no forged state",
        "champion" not in json.dumps(body) and "Production" not in json.dumps(body),
    )
    check(
        "R005-B",
        "projection authority is factory",
        body["approval_authority"] == "factory"
        and body["registry"] == "mlflow"
        and body["drift"] is None,
    )
    check(
        "R005-B",
        "projection values",
        body["model_version_id"] == str(MV)
        and body["run_id"] == RUN_ID
        and body["artifact_sha256"] == "c" * 64
        and body["registry_version"] == "1",
    )
    check(
        "R005-B",
        "projection reports drift text",
        project_registry_binding(b, drift="x")["drift"] == "x",
    )

    # field-by-field drift
    def mutate(field: str, value: Any) -> None:
        f = Fake()
        bb = make_binding(f)
        rec = f.versions[0]
        if field.startswith("tag:"):
            key = field[4:]
            rec["tags"] = [t for t in rec["tags"] if t["key"] != key] + (
                [] if value is None else tag_list({key: value})
            )
        else:
            rec[field] = value
        check(
            "R005-B",
            f"drift on {field}={value!r}",
            raises(RegistryDriftError, verify_binding, f, bb, approved_source_prefixes=PREFIXES),
        )

    mutate(f"tag:{TAG_ARTIFACT}", "d" * 64)
    mutate(f"tag:{TAG_ARTIFACT}", None)
    mutate(f"tag:{REG_TAG_ORG}", str(ORG_B))
    mutate(f"tag:{REG_TAG_ORG}", None)
    mutate(f"tag:{TAG_MODEL_VERSION}", str(uuid.uuid4()))
    mutate(f"tag:{TAG_MODEL_VERSION}", None)
    mutate("run_id", "b" * 32)
    mutate("source", f"s3://sovra-model-artifacts/{ORG_A}/other/")
    mutate("name", "other")
    f9 = Fake()
    b9 = make_binding(f9)
    f9.versions[0]["source"] = b9.source_uri
    check(
        "R005-B",
        "unapproved registry source is drift",
        raises(
            RegistryDriftError,
            verify_binding,
            f9,
            b9,
            approved_source_prefixes=("s3://elsewhere/",),
        ),
    )
    forged = RegistryBinding(**{**b.__dict__, "organization_id": ORG_B})
    check(
        "R005-B",
        "binding under another org refused",
        raises(RegistryDriftError, verify_binding, fake, forged, approved_source_prefixes=PREFIXES),
    )
    renamed = RegistryBinding(**{**b.__dict__, "registered_model_name": "free-name"})
    n = len(fake.calls)
    check(
        "R005-B",
        "non-derived name refused before any read",
        raises(RegistryDriftError, verify_binding, fake, renamed, approved_source_prefixes=PREFIXES)
        and len(fake.calls) == n,
    )
    # bind input validation: nothing is created
    f10 = Fake()
    kw: dict[str, Any] = dict(
        organization_id=ORG_A,
        model_version_id=MV,
        run_id=RUN_ID,
        artifact_sha256="c" * 64,
        source_uri=b9.source_uri,
        approved_source_prefixes=PREFIXES,
    )
    for label, over in {
        "bad digest": {"artifact_sha256": "C" * 64},
        "short digest": {"artifact_sha256": "c" * 63},
        "bad run": {"run_id": "xyz"},
        "uppercase run": {"run_id": "A" * 32},
        "str org": {"organization_id": str(ORG_A)},
    }.items():
        check(
            "R005-B",
            f"bind {label} refused",
            raises(ValueError, bind_model_version, f10, **{**kw, **over}),
        )
    check(
        "R005-B",
        "bind unapproved source refused",
        raises(
            TrackingRejectedError,
            bind_model_version,
            f10,
            **{**kw, "source_uri": "https://evil/x/"},
        ),
    )
    check("R005-B", "refused binds touched nothing", f10.calls == [] and f10.versions == [])
    # two claimants for one ModelVersion
    f11 = Fake()
    make_binding(f11)
    f11.versions.append({**f11.versions[0], "version": "2"})
    check("R005-B", "two claimants is drift", raises(RegistryDriftError, make_binding, f11))


def test_mutation_closers() -> None:
    mx = client_mod.MAX_RESPONSE_BYTES
    check(
        "R005-C",
        "case-distinct keys are not duplicates",
        parse_strict_json(b'{"a": 1, "A": 2}') == {"a": 1, "A": 2},
    )
    check(
        "R005-C",
        "whitespace-distinct keys are not duplicates",
        parse_strict_json(b'{"a": 1, " a": 2}') == {"a": 1, " a": 2},
    )
    prefix, suffix = b'{"a":"', b'"}'
    check(
        "R005-C",
        "oversize valid JSON refused",
        raises(TrackingRejectedError, parse_strict_json, prefix + b"x" * mx + suffix),
    )
    exact = prefix + b"x" * (mx - len(prefix) - len(suffix)) + suffix
    check(
        "R005-C",
        "exactly-bound valid JSON accepted",
        len(exact) == mx and "a" in parse_strict_json(exact),
    )
    stub = Stub()
    c = stub.client()
    stub.reply(200, {})
    check(
        "R005-C",
        "100 tags is the allowed boundary",
        not raises(
            TrackingRejectedError, c.log_batch, RUN_ID, tags={f"t{i}": "v" for i in range(100)}
        ),
    )
    stub.reply(400, b"not json")
    try:
        c.get_run(RUN_ID)
    except TrackingRejectedError as exc:
        check(
            "R005-C",
            "empty error code leaves a clean message",
            str(exc) == "MLflow returned HTTP 400",
        )
    n = len(stub.seen)
    stub.reply(200, {"experiment": {"experiment_id": "7", "lifecycle_stage": "deleted"}})
    check(
        "R005-C",
        "deleted experiment is refused without a create call",
        raises(TrackingRejectedError, c.get_or_create_experiment, "sovra/d")
        and len(stub.seen) == n + 1,
    )
    stub.reply(200, {"experiment": {"lifecycle_stage": "active"}})
    try:
        c.get_or_create_experiment("sovra/e")
    except TrackingRejectedError as exc:
        check("R005-C", "missing experiment_id is named", "experiment_id" in str(exc))
    for bad in (None, 5, "xyz", "A" * 32, "a" * 31):
        stub.reply(200, {"run": {"info": {"run_id": bad}}})
        check(
            "R005-C",
            f"create_run refuses run_id {bad!r}",
            raises(TrackingRejectedError, c.create_run, "1", run_name="n", tags={}),
        )
    stub.reply(200, {"run": {"info": {"run_id": RUN_ID}}})
    check(
        "R005-C",
        "create_run returns a valid id",
        c.create_run("1", run_name="n", tags={}) == RUN_ID,
    )
    stub.reply(200, {"run": "x"})
    check(
        "R005-C",
        "create_run refuses a non-object run",
        raises(TrackingRejectedError, c.create_run, "1", run_name="n", tags={}),
    )
    # registered model: get -> create sequence, exists -> get only, race tolerated
    n = len(stub.seen)
    stub.reply(404, {"error_code": "RESOURCE_DOES_NOT_EXIST"})
    stub.reply(200, {})
    c.ensure_registered_model("m", {"a": "b"})
    paths = [x["path"] for x in stub.seen[n:]]
    check(
        "R005-C",
        "missing model is created",
        len(paths) == 2
        and "registered-models/get" in paths[0]
        and "registered-models/create" in paths[1],
    )
    created = json.loads(stub.seen[-1]["body"])
    check(
        "R005-C",
        "create carries name and tags",
        created == {"name": "m", "tags": [{"key": "a", "value": "b"}]},
    )
    n = len(stub.seen)
    stub.reply(200, {})
    c.ensure_registered_model("m", {})
    check("R005-C", "existing model is not re-created", len(stub.seen) == n + 1)
    stub.reply(404, {"error_code": "RESOURCE_DOES_NOT_EXIST"})
    stub.reply(400, {"error_code": "RESOURCE_ALREADY_EXISTS"})
    check(
        "R005-C",
        "create race is tolerated",
        not raises(MlflowAlreadyExistsError, c.ensure_registered_model, "m", {}),
    )
    # create_model_version validates the run id before any request
    n = len(stub.seen)
    check(
        "R005-C",
        "bad run id refused before request",
        raises(
            TrackingRejectedError,
            c.create_model_version,
            "m",
            source="s3://sovra-model-artifacts/o/",
            run_id="zz",
            tags={},
            approved_source_prefixes=PREFIXES,
        )
        and len(stub.seen) == n,
    )
    # version search filters by tag and validates shape
    n = len(stub.seen)
    stub.reply(
        200,
        {
            "model_versions": [
                {"version": "1", "tags": [{"key": "k", "value": "v"}]},
                {"version": "2", "tags": [{"key": "k", "value": "w"}]},
                {"version": "3"},
            ]
        },
    )
    found = c.search_model_versions_by_tag("m", "k", "v")
    check("R005-C", "only matching versions are returned", [v["version"] for v in found] == ["1"])
    check(
        "R005-C",
        "search is scoped to the model name",
        "name+%3D+%27m%27" in stub.seen[n]["path"]
        or "name%20%3D%20%27m%27" in stub.seen[n]["path"],
    )
    stub.reply(200, {"model_versions": "x"})
    check(
        "R005-C",
        "non-list versions refused",
        raises(TrackingRejectedError, c.search_model_versions_by_tag, "m", "k", "v"),
    )
    stub.reply(200, {"model_versions": [1]})
    check(
        "R005-C",
        "non-object version refused",
        raises(TrackingRejectedError, c.search_model_versions_by_tag, "m", "k", "v"),
    )
    stub.reply(200, {})
    check(
        "R005-C",
        "no versions is an empty list",
        c.search_model_versions_by_tag("m", "k", "v") == [],
    )
    stub.reply(200, {"model_version": {"version": "1"}})
    check(
        "R005-C",
        "get_model_version returns the record",
        c.get_model_version("m", "1") == {"version": "1"},
    )
    check(
        "R005-C",
        "get_model_version names model and version",
        "name=m" in stub.seen[-1]["path"] and "version=1" in stub.seen[-1]["path"],
    )
    stub.reply(200, {"model_version": "x"})
    check(
        "R005-C",
        "non-object model_version refused",
        raises(TrackingRejectedError, c.get_model_version, "m", "1"),
    )
    stub.reply(200, {})
    check(
        "R005-C",
        "missing model_version refused",
        raises(TrackingRejectedError, c.get_model_version, "m", "1"),
    )
    stub.server.shutdown()
    tags_of = client_mod._tags_of
    check("R005-C", "dict-form tags are read", tags_of({"tags": {"a": "b"}}) == {"a": "b"})
    check(
        "R005-C",
        "list-form tags skip junk items",
        tags_of({"tags": ["key", {"value": "x"}, {"key": "k", "value": "v"}, {"key": "n"}]})
        == {"k": "v", "n": ""},
    )
    check("R005-C", "absent tags are empty", tags_of({}) == {} and tags_of({"tags": 5}) == {})

    refused_purposes: tuple[Any, ...] = (
        "final_qualification",
        " final_qualification",
        "Final_Qualification",
        "sealed_final",
        "FINAL_QUALIFICATION\n",
        "",
        "unknown",
        None,
        [],
        5,
    )
    for purpose in refused_purposes:
        check(
            "R005-R",
            f"purpose {purpose!r} refused",
            raises(SealedExportRefusedError, make_run, data_purpose=purpose),
        )
    for purpose in ("train", "validation", "development_eval"):
        check(
            "R005-R",
            f"purpose {purpose} allowed",
            not raises(Exception, make_run, data_purpose=purpose),
        )
    check(
        "R005-R",
        "case-distinct metric keys allowed",
        not raises(ValueError, make_run, metrics=(("m", 1.0), ("M", 2.0))),
    )
    check(
        "R005-R",
        "case-distinct param keys allowed",
        not raises(ValueError, make_run, params=(("p", "a"), ("P", "b"))),
    )
    check(
        "R005-R",
        "case-distinct hash keys allowed",
        not raises(ValueError, make_run, hashes=(("h", "a" * 64), ("H", "b" * 64))),
    )
    fake = Fake()
    entry = InMemoryOutbox().add(make_run())
    sync_run(fake, entry)
    digest = entry.run.payload_sha256()
    fake.runs[0]["data"]["tags"] = [
        t for t in fake.runs[0]["data"]["tags"] if t["key"] != TAG_PAYLOAD
    ] + tag_list({TAG_PAYLOAD: digest.upper()})
    again = sync_run(fake, entry)
    check("R005-R", "case-variant completion marker is a conflict", again.state == SyncState.FAILED)
    # a registry version that drifts right after creation is caught by bind itself
    f = Fake()
    f.after_create = lambda rec: rec.update(run_id="b" * 32)
    check(
        "R005-B",
        "bind verifies the record it just created",
        raises(RegistryDriftError, make_binding, f),
    )


def test_mutation_closers_2() -> None:
    stub = Stub()
    c = stub.client()
    stub.reply(400, {"error_code": "BAD_THING"})
    try:
        c.get_run(RUN_ID)
    except TrackingRejectedError as exc:
        check(
            "R005-C",
            "error message carries status and code",
            str(exc) == "MLflow returned HTTP 400 BAD_THING",
        )
    stub.reply(200, {"runs": [1]})
    check(
        "R005-C",
        "non-object run in a page refused",
        raises(TrackingRejectedError, c.search_runs_by_tag, "1", "k", "v"),
    )
    stub.reply(200, {"runs": [], "next_page_token": "t"})
    stub.reply(200, {"runs": [], "next_page_token": "T"})
    stub.reply(200, {"runs": []})
    check("R005-C", "case-distinct page tokens advance", c.search_runs_by_tag("1", "k", "v") == [])
    n = len(stub.seen)
    check(
        "R005-C",
        "log_batch refuses a bad run id before any request",
        raises(TrackingRejectedError, c.log_batch, "zz", tags={"a": "b"}) and len(stub.seen) == n,
    )
    check(
        "R005-C",
        "finish_run refuses a bad run id before any request",
        raises(TrackingRejectedError, c.finish_run, "zz", status="FINISHED", end_time_ms=1)
        and len(stub.seen) == n,
    )
    stub.reply(200, {})
    check(
        "R005-C",
        "100 params is the allowed boundary",
        not raises(
            TrackingRejectedError, c.log_batch, RUN_ID, params={f"p{i}": "v" for i in range(100)}
        ),
    )
    for bad in (" FINISHED", "finished", "FINISHED ", "Failed"):
        check(
            "R005-C",
            f"status {bad!r} refused",
            raises(TrackingRejectedError, c.finish_run, RUN_ID, status=bad, end_time_ms=1),
        )
    for good in ("FINISHED", "FAILED", "KILLED"):
        stub.reply(200, {})
        check(
            "R005-C",
            f"status {good} accepted",
            not raises(TrackingRejectedError, c.finish_run, RUN_ID, status=good, end_time_ms=1),
        )
    stub.reply(200, {"model_versions": [{"version": "1", "tags": [{"key": "k", "value": "V"}]}]})
    check(
        "R005-C",
        "tag value match is exact and case-sensitive",
        [v["version"] for v in c.search_model_versions_by_tag("m", "k", "V")] == ["1"],
    )
    stub.server.shutdown()
    root = "s3://sovra-model-artifacts/"
    check("R005-C", "bang is a legal path character", is_approved_source(root + "a!b/", PREFIXES))
    for bad in (
        root + "a/ ",
        root + "a/\n",
        root + "a/\t",
        root + "a\x7f/",
        root + "a\x00/",
        root + "a\x1f/",
        root + "a\x20b/",
    ):
        check("R005-C", f"source {bad!r} refused", not is_approved_source(bad, PREFIXES))
    check("R005-C", "tilde is a legal path character", is_approved_source(root + "a~b/", PREFIXES))
    for purpose in (" train", "TRAIN", "train\n", "Validation", "development_eval "):
        check(
            "R005-R",
            f"purpose {purpose!r} must match exactly",
            raises(SealedExportRefusedError, make_run, data_purpose=purpose),
        )


def main() -> None:
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
    if FAILURES:
        print(f"FTF-R005 MLflow projection tests: {len(FAILURES)} failed")
        for failure in FAILURES:
            print(f"  {failure}")
        sys.exit(1)
    print("FTF-R005 MLflow projection tests: all checks passed")
    sys.exit(0)


if __name__ == "__main__":
    main()
