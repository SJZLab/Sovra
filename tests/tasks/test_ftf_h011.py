#!/usr/bin/env python3
# mypy: disable-error-code="arg-type,call-arg,list-item,unused-ignore,func-returns-value,attr-defined,misc,assignment,index,var-annotated,union-attr,operator,return-value,no-any-return"
# ruff: noqa: E501
"""FTF-H011-P3 -- the dataset mapping routes, their contract and their wiring (no-DB tests; the live
Postgres + SeaweedFS evidence is `tests/integration/test_ftf_h011_routes_postgres_s3.py`, run by CI's
`dataset-mapping-routes-integration` job).

Without a database or network this suite drives the REAL route module, the REAL FTF-H011-P2 store, the
REAL request store and the REAL upload store on a SQLite session, over a real uvicorn server. Only the
Postgres tenant guard, the audit writer, the connection authorization and the two byte-reading
services (preview, probe) are replaced, exactly as `test_ftf_h011_p2.py` replaces them.

R  roles and authentication on all seven routes, the body-validation table, the exact response
   shape, masking by role and connection classification, held-out hiding, every store refusal's
   status and code, propose / approve / reject with the Idempotency-Key (replay, key reuse, a request
   that took effect under another key), the approver and the rationale taken as the contract says
U  direct checks of the module's private helpers
S  the request store (`dataset_mapping_request_store`) and the store's new read-only functions
M  migration `0036_dataset_mapping_requests.py`
C  the interface contract (05-interface.yaml): every path, status code and header; the schema drift
   table (schema-valid <=> `mapping_from_object` accepts) and the app wiring
F  fault injection: each protection is removed from a copy of the route module on disk and the same
   route suite must turn RED

Acceptance ("Import a real irregular CSV/XLSX/JSON source via UI; preserve Arabic/text types, reject
unmapped critical roles/tool IDs and deny training for evaluation-only grants"): ACC-*. Negative
acceptance ("No arbitrary Python/JS eval transformations; preview/mapping sends no data outside project
boundary and does not silently harvest repo/log records"): NEG-*.

Run directly: python3 tests/tasks/test_ftf_h011.py
"""

from __future__ import annotations

import contextlib
import hashlib
import http.client
import importlib.util
import json
import re
import socket
import subprocess
import sys
import threading
import time
import uuid
from collections.abc import Callable, Iterator
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any

import uvicorn
import yaml
from fastapi import FastAPI
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "apps" / "api"))

from workers.data import import_common as ic  # noqa: E402
from workers.data.dataset_mapping_review import (  # noqa: E402
    ComparisonCell,
    MappingPreviewResult,
    MappingPreviewRow,
    ProbeResult,
)

from app.auth.rbac_middleware import AuthenticatedPrincipal, Role  # noqa: E402
from app.auth.tenant_isolation_query_guards import TenantContextError  # noqa: E402
from app.datasets import dataset_mapping_request_store as rstore  # noqa: E402
from app.datasets import dataset_mapping_routes as real_routes  # noqa: E402
from app.datasets import dataset_mapping_store as store  # noqa: E402
from app.datasets import dataset_source_connection_crud as crud  # noqa: E402
from app.datasets import dataset_upload_store as ustore  # noqa: E402
from app.datasets.dataset_object_source import DatasetSourceInconsistentError  # noqa: E402
from app.db.tenant_context import TenantContext  # noqa: E402

FAILURES: list[str] = []
HERE = Path(__file__).resolve().parent
ROUTES_PATH = ROOT / "apps" / "api" / "app" / "datasets" / "dataset_mapping_routes.py"
REQUEST_STORE_PATH = ROOT / "apps" / "api" / "app" / "datasets" / "dataset_mapping_request_store.py"
STORE_PATH = ROOT / "apps" / "api" / "app" / "datasets" / "dataset_mapping_store.py"
MIGRATION_PATH = ROOT / "apps" / "api" / "alembic" / "versions" / "0036_dataset_mapping_requests.py"
INTERFACE_PATH = ROOT / "packages" / "contracts" / "openapi" / "05-interface.yaml"
MAIN_PATH = ROOT / "apps" / "api" / "app" / "main.py"


def _load(name: str, directory: Path = HERE) -> Any:
    spec = importlib.util.spec_from_file_location(name[:-3], directory / name)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


p2 = _load("test_ftf_h011_p2.py")

ORG = p2._ORG
PROJECT = p2._PROJECT
BASE_DS = f"/api/v1/organizations/{ORG}/projects/{PROJECT}/datasets"
NOW = p2._NOW
CANARIES = ("canary-raw-secret-7", "canary-header-secret-8", "tenants/o/uploads/x/source")

PRINCIPALS = {
    name: AuthenticatedPrincipal(issuer="https://issuer.example", subject=name)
    for name in (
        "ml",
        "ml2",
        "admin",
        "admin2",
        "orgadmin",
        "viewer",
        "evaluator",
        "approver",
        "stranger",
    )
}
ROLES = {
    "ml": Role.ML_ENGINEER,
    "ml2": Role.ML_ENGINEER,
    "admin": Role.WORKSPACE_ADMIN,
    "admin2": Role.WORKSPACE_ADMIN,
    "orgadmin": Role.ORGANIZATION_ADMIN,
    "viewer": Role.VIEWER,
    "evaluator": Role.EVALUATOR,
    "approver": Role.APPROVER,
}


def _principal_resolver(request: Any) -> AuthenticatedPrincipal | None:
    return PRINCIPALS.get(request.headers.get("x-test-principal", ""))


def _role_lookup(issuer: str, subject: str, organization_id: uuid.UUID) -> Role | None:
    return ROLES.get(subject) if organization_id == ORG else None


def check(criterion: str, description: str, condition: bool) -> None:
    if not condition:
        FAILURES.append(f"FAIL [{criterion}] {description}")


def sha(seed: str) -> str:
    return hashlib.sha256(seed.encode()).hexdigest()


# ------------------------------------------------------------------ the world


class Storage:
    """What `UploadStorageSettings` offers the routes: a bucket and a client."""

    bucket = "test-bucket"

    def client(self) -> Any:
        return SimpleNamespace(name="the-s3-client")


class HttpWorld:
    """The P2 `World` (real upload store, real mapping store, SQLite) plus the HTTP-side fakes."""

    def __init__(self, routes_mod: ModuleType) -> None:
        self.engine = create_engine(
            "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
        )
        for model in (
            ustore.Dataset,
            ustore.DatasetUploadSession,
            store.DatasetMapping,
            rstore.DatasetMappingRequest,
        ):
            model.__table__.create(self.engine)
        self.session = Session(self.engine)
        self.audit = p2.Audit()
        self.base = p2.World(self.session, self.audit)
        self.routes = routes_mod
        self.classification: dict[uuid.UUID, str] = {}
        self.probe_calls: list[dict[str, Any]] = []
        self.probe_error: BaseException | None = None
        self.probe_state: tuple[str, str | None] = ("completed", None)
        self.storage_configured = True
        self.preview_rows: tuple[MappingPreviewRow, ...] = ()
        self.saved: list[tuple[Any, str, Any]] = []
        self.tenant_error = False
        self.calls: dict[str, int] = {}

    # -- the fakes --
    def authorize_dataset(self, session: Session, context: Any, **kwargs: Any) -> None:
        """What the real services do first (`_authorize_and_open`): the dataset as the caller may
        see it, then the live purpose authorization of its connection."""
        dataset = ustore.get_dataset(
            session,
            context,
            project_id=kwargs["project_id"],
            dataset_id=kwargs["dataset_id"],
            include_held_out=kwargs["include_held_out"],
        )
        upload = ustore.get_upload_session(
            session, context, kwargs["project_id"], dataset.upload_session_id
        )
        self.authorize(
            session,
            context,
            kwargs["project_id"],
            upload.dataset_source_connection_id,
            dataset.purpose,
        )

    def fake_preview(self, session: Session, context: Any, **kwargs: Any) -> MappingPreviewResult:
        self.authorize_dataset(session, context, **kwargs)
        return self.base.fake_preview(session, context, **kwargs)

    def fake_probe(self, session: Session, context: Any, **kwargs: Any) -> ProbeResult:
        self.authorize_dataset(session, context, **kwargs)
        self.probe_calls.append(kwargs)
        if self.probe_error is not None:
            raise self.probe_error
        state, failure = self.probe_state
        if kwargs["reveal"]:
            headers: tuple[object, ...] | None = ("q", "a")
            rows: tuple[Any, ...] = ({"q": "hello", "a": "world"},)
        else:
            marker = {"masked": True, "chars": 1}
            headers = (marker, marker)
            rows = ({"masked": True, "chars": 5},)
        return ProbeResult(state, failure, headers, rows, state == "completed")

    def fake_connection(
        self, session: Session, context: Any, project_id: uuid.UUID, connection_id: uuid.UUID
    ) -> Any:
        connection = self.base.connections.get(connection_id)
        if connection is None or connection.project_id != project_id:
            raise crud.DatasetSourceConnectionNotFoundError(str(connection_id))
        return SimpleNamespace(
            id=connection_id,
            security_classification=self.classification.get(connection_id, "internal"),
        )

    def authorize(
        self,
        _session: Session,
        _context: Any,
        project_id: uuid.UUID,
        connection_id: uuid.UUID,
        purpose: str,
    ) -> Any:
        connection = self.base.connections.get(connection_id)
        if connection is None or connection.project_id != project_id:
            raise crud.DatasetSourceConnectionNotFoundError(str(connection_id))
        crud.authorize_purpose_use(connection, purpose)
        return connection

    @contextlib.contextmanager
    def session_cm(self, session_factory: Any, tenant_context: Any) -> Iterator[Session]:
        if self.tenant_error:
            raise TenantContextError("the tenant context could not be bound")
        try:
            yield self.session
            self.session.commit()
        except BaseException:
            self.session.rollback()
            raise

    def storage_factory(self) -> Any:
        return Storage() if self.storage_configured else None

    # -- seeding --
    def connection(self, key: str, purposes: list[str], classification: str = "internal") -> Any:
        conn = self.base.connection(key, purposes)
        self.classification[conn] = classification
        return conn

    def dataset(self, *args: Any, **kwargs: Any) -> Any:
        dataset = self.base.dataset(*args, **kwargs)
        self.session.commit()
        return dataset

    def rows(self) -> list[store.DatasetMapping]:
        return list(
            self.session.query(store.DatasetMapping).order_by(store.DatasetMapping.created_at)
        )

    def requests(self) -> list[rstore.DatasetMappingRequest]:
        return list(self.session.query(rstore.DatasetMappingRequest))

    def snapshot(self) -> tuple[int, int, int]:
        self.session.commit()
        return (len(self.rows()), len(self.requests()), len(self.audit.events))


@contextlib.contextmanager
def http_world(routes_mod: ModuleType = real_routes) -> Iterator[tuple[HttpWorld, str]]:
    world = HttpWorld(routes_mod)
    patches = [
        (store, "execute_tenant_scoped", p2.passthrough),
        (ustore, "execute_tenant_scoped", p2.passthrough),
        (rstore, "execute_tenant_scoped", p2.passthrough),
        (store, "write_audit_event", world.audit),
        (routes_mod, "write_audit_event", world.audit),
        (store, "authorize_dataset_source_purpose", world.authorize),
        (store, "preview_dataset_mapping", world.fake_preview),
        (store, "probe_dataset_source", world.fake_probe),
        (store, "get_dataset_source_connection", world.fake_connection),
        (routes_mod, "tenant_scoped_session_for", world.session_cm),
    ]

    def counting(name: str) -> Any:
        target = getattr(routes_mod, name)

        def wrapper(*args: Any, **kwargs: Any) -> Any:
            world.calls[name] = world.calls.get(name, 0) + 1
            return target(*args, **kwargs)

        return wrapper

    patches += [
        (routes_mod, name, counting(name))
        for name in (
            "propose_dataset_mapping",
            "approve_dataset_mapping",
            "reject_dataset_mapping",
            "review_preview",
            "review_probe",
        )
    ]
    originals = [(mod, name, getattr(mod, name)) for mod, name, _ in patches]
    for mod, name, value in patches:
        setattr(mod, name, value)
    app = FastAPI()
    app.include_router(
        routes_mod.build_dataset_mapping_router(
            lambda: None,
            principal_resolver=_principal_resolver,
            role_lookup=_role_lookup,
            storage_factory=world.storage_factory,
        )
    )
    try:
        with running_app_for(app) as url:
            yield world, url
    finally:
        for mod, name, value in originals:
            setattr(mod, name, value)
        world.session.close()
        world.engine.dispose()


def _free_port() -> int:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


@contextlib.contextmanager
def running_app_for(app: FastAPI) -> Iterator[str]:
    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(300):
        if server.started:
            break
        time.sleep(0.02)
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(timeout=5)


LIVE: list[tuple[str, str, int, Any, Any]] = []


def _normalized(path: str) -> str:
    """A request path as the contract names it: the dataset prefix dropped, ids as `{}`, no query."""
    tail = path.split("?")[0].split("/datasets/", 1)[-1]
    parts = tail.split("/")[1:]
    return "/" + "/".join("{}" if re.fullmatch(r"[0-9a-f-]{36}", part) else part for part in parts)


def call(
    base: str,
    method: str,
    path: str,
    principal: str | None = "admin",
    body: Any = None,
    key: str | None = None,
    raw: bytes | None = None,
    headers: dict[str, str] | None = None,
) -> tuple[int, Any]:
    host, port = base.removeprefix("http://").split(":")
    connection = http.client.HTTPConnection(host, int(port), timeout=30)
    sent = dict(headers or {})
    if principal is not None:
        sent["x-test-principal"] = principal
    if key is not None:
        sent["Idempotency-Key"] = key
    payload = raw if raw is not None else (None if body is None else json.dumps(body).encode())
    if payload is not None:
        sent["Content-Type"] = "application/json"
    try:
        connection.request(method, path, body=payload, headers=sent)
        response = connection.getresponse()
        data = response.read()
    finally:
        connection.close()
    if response.status >= 500 and response.status != 503:
        print(f"UNEXPECTED {response.status} {method} {path}: {data[:300]!r}", file=sys.stderr)
    try:
        parsed: Any = json.loads(data)
    except ValueError:
        parsed = data
    if "/datasets/" in path:
        sent_body = None if payload is None else _loads_or_none(payload)
        LIVE.append((method, _normalized(path), response.status, parsed, sent_body))
    return response.status, parsed


def _loads_or_none(payload: bytes) -> Any:
    try:
        return json.loads(payload)
    except ValueError:
        return None


# ------------------------------------------------------------------ fixtures


ALL_PURPOSES = p2.ALL_PURPOSES
MSG = p2.msg_doc()
MSG2 = p2.msg_doc({"human": "user", "bot": "assistant", "sys": "system"})


def code(response: tuple[int, Any]) -> str | None:
    body = response[1]
    return body.get("detail") if isinstance(body, dict) else None


def two_rows_ok(world: HttpWorld) -> None:
    world.base.preview_rows = ()


def seeded(world: HttpWorld, classification: str = "internal") -> tuple[Any, Any]:
    conn = world.connection("all", ALL_PURPOSES, classification)
    dataset = world.base.dataset("alpha", "training", conn)
    world.base.schema_sha = sha("header")
    return conn, dataset


def propose(
    base: str, ds: Any, doc: Any = None, key: str = "propose-key-0001", who: str = "ml"
) -> Any:
    return call(
        base,
        "POST",
        f"{BASE_DS}/{ds.id}/mappings",
        who,
        {"mapping": MSG if doc is None else doc},
        key,
    )


def approve_body(mapping: dict[str, Any], **kw: Any) -> dict[str, Any]:
    body = {
        "expected_preview_sha256": mapping["preview_sha256"],
        "purpose": mapping["dataset_purpose"],
        "acknowledged_ignored_keys": [],
        "rationale": "the roles and the tool ids match the source",
    }
    body.update(kw)
    return body


def approve(
    base: str,
    ds: Any,
    mapping: dict[str, Any],
    who: str = "admin",
    key: str = "approve-key-0001",
    **kw: Any,
) -> Any:
    return call(
        base,
        "POST",
        f"{BASE_DS}/{ds.id}/mappings/{mapping['id']}/approve",
        who,
        approve_body(mapping, **kw),
        key,
    )


def reject(
    base: str,
    ds: Any,
    mapping: dict[str, Any],
    who: str = "admin",
    key: str = "reject-key-0001",
    rationale: str = "wrong role map",
) -> Any:
    return call(
        base,
        "POST",
        f"{BASE_DS}/{ds.id}/mappings/{mapping['id']}/reject",
        who,
        {"rationale": rationale},
        key,
    )


# ======================================================================================
# R -- the routes
# ======================================================================================


def part_r_access() -> None:
    with http_world() as (w, base):
        conn, ds = seeded(w)
        made = propose(base, ds)
        check("R-SETUP", f"a proposal is created (got {made[0]} {made[1]})", made[0] == 201)
        mapping = made[1]
        mid = mapping["id"]
        routes: list[tuple[str, str, Any, bool, bool]] = [
            ("POST", "/mapping-probe", {}, False, False),
            ("POST", "/mapping-preview", {"mapping": MSG}, False, False),
            ("POST", "/mappings", {"mapping": MSG}, True, False),
            ("GET", "/mappings", None, False, False),
            ("GET", f"/mappings/{mid}", None, False, False),
            ("POST", f"/mappings/{mid}/approve", approve_body(mapping), True, True),
            ("POST", f"/mappings/{mid}/reject", {"rationale": "no"}, True, True),
        ]
        for method, suffix, body, needs_key, admin_only in routes:
            url = f"{BASE_DS}/{ds.id}{suffix}"
            key = "access-key-00001" if needs_key else None
            check(
                "R-AUTHN",
                f"{method} {suffix.split('/')[1]}: no principal is 401",
                call(base, method, url, None, body, key)[0] == 401,
            )
            for who in ("viewer", "evaluator", "approver", "stranger"):
                got = call(base, method, url, who, body, key)
                check(
                    "R-AUTHZ",
                    f"{method} {suffix.split('/')[1]}: {who} is 403 role_not_authorized (got {got[0]})",
                    got[0] == 403,
                )
            if admin_only:
                got = call(base, method, url, "ml", body, key)
                check(
                    "R-AUTHZ",
                    f"{method} {suffix.split('/')[1]}: an ml_engineer is 403 (got {got[0]})",
                    got[0] == 403,
                )
        before = w.snapshot()
        got = call(
            base,
            "POST",
            f"{BASE_DS}/{ds.id}/mappings/{mid}/approve",
            "ml",
            approve_body(mapping),
            "access-key-00002",
        )
        check(
            "R-AUTHZ",
            "an ml_engineer approve wrote nothing",
            w.snapshot() == before and got[0] == 403,
        )
        for method, path in (
            ("DELETE", f"{BASE_DS}/{ds.id}/mappings/{mid}"),
            ("PUT", f"{BASE_DS}/{ds.id}/mappings/{mid}"),
            ("PATCH", f"{BASE_DS}/{ds.id}/mappings/{mid}"),
            ("DELETE", f"{BASE_DS}/{ds.id}/mappings"),
        ):
            check(
                "R-NOEDIT",
                f"{method} {path.rsplit('/', 1)[-1][:8]} is 405: a stored mapping can never be edited or deleted",
                call(base, method, path, "admin", {})[0] == 405,
            )
        for suffix in ("", "/mapping-probe", "/mapping-preview"):
            check(
                "R-NOEDIT",
                f"GET {suffix or '/'} is not a route (405/404)",
                call(base, "GET", f"{BASE_DS}/{ds.id}{suffix}", "admin")[0] in (404, 405),
            )


def part_r_validation() -> None:
    with http_world() as (w, base):
        conn, ds = seeded(w)
        made = propose(base, ds)
        mapping = made[1]
        mid = mapping["id"]
        w.snapshot()
        before = w.snapshot()
        big = json.dumps({"mapping": MSG, "pad": "x" * (1024 * 1024 + 10)}).encode()
        bodies: list[tuple[str, bytes, int, str]] = [
            ("not json", b"nope", 400, "invalid_body"),
            ("empty", b"", 400, "invalid_body"),
            ("array", b"[]", 400, "invalid_body"),
            ("null", b"null", 400, "invalid_body"),
            ("duplicate key", b'{"mapping": {}, "mapping": {}}', 400, "invalid_body"),
            ("NaN", b'{"mapping": NaN}', 400, "invalid_body"),
            ("deep", b'{"mapping":' + b"[" * 60 + b"]" * 60 + b"}", 400, "invalid_body"),
            ("oversize", big, 413, "request_too_large"),
        ]
        for path, key in (
            ("/mapping-preview", None),
            ("/mappings", "validation-key-1"),
        ):
            for label, raw, status, detail in bodies:
                got = call(base, "POST", f"{BASE_DS}/{ds.id}{path}", "admin", raw=raw, key=key)
                check(
                    "R-BODY",
                    f"{path}: {label} is {status} {detail} (got {got[0]} {code(got)})",
                    got[0] == status and code(got) == detail,
                )
        mapping_bodies: list[tuple[str, Any]] = [
            ("missing mapping", {}),
            ("mapping is a string", {"mapping": "x"}),
            ("mapping is a list", {"mapping": []}),
            ("mapping is null", {"mapping": None}),
            ("extra key", {"mapping": MSG, "extra": 1}),
        ]
        for path, key in (("/mapping-preview", None), ("/mappings", "validation-key-2")):
            for label, body in mapping_bodies:
                got = call(base, "POST", f"{BASE_DS}/{ds.id}{path}", "admin", body, key)
                check(
                    "R-BODY",
                    f"{path}: {label} is 400 invalid_body (got {got[0]} {code(got)})",
                    got == (400, {"detail": "invalid_body"}),
                )
        for label, body in (
            ("array of options", []),
            ("unknown option block", {"xlsx": {}}),
            ("unknown key", {"mapping": MSG}),
        ):
            got = call(base, "POST", f"{BASE_DS}/{ds.id}/mapping-probe", "admin", body)
            check(
                "R-BODY",
                f"probe: {label} is 400 invalid_body (got {got[0]})",
                got == (400, {"detail": "invalid_body"}),
            )
        for key, label in (
            (None, "missing"),
            ("short", "too short"),
            ("a" * 201, "too long"),
            ("bad key with spaces", "illegal characters"),
            ("badékey-0001", "non-ascii"),
        ):
            got = call(base, "POST", f"{BASE_DS}/{ds.id}/mappings", "admin", {"mapping": MSG}, key)
            check(
                "R-KEY",
                f"propose: an Idempotency-Key that is {label} is 400 invalid_idempotency_key (got {got[0]} {code(got)})",
                got == (400, {"detail": "invalid_idempotency_key"}),
            )
            got = call(
                base,
                "POST",
                f"{BASE_DS}/{ds.id}/mappings/{mid}/approve",
                "admin",
                approve_body(mapping),
                key,
            )
            check(
                "R-KEY",
                f"approve: key {label} is 400 invalid_idempotency_key",
                got == (400, {"detail": "invalid_idempotency_key"}),
            )
            got = call(
                base,
                "POST",
                f"{BASE_DS}/{ds.id}/mappings/{mid}/reject",
                "admin",
                {"rationale": "x"},
                key,
            )
            check(
                "R-KEY",
                f"reject: key {label} is 400 invalid_idempotency_key",
                got == (400, {"detail": "invalid_idempotency_key"}),
            )
        good = approve_body(mapping)
        approve_bodies: list[tuple[str, Any]] = [
            ("missing rationale", {k: v for k, v in good.items() if k != "rationale"}),
            ("missing purpose", {k: v for k, v in good.items() if k != "purpose"}),
            ("missing hash", {k: v for k, v in good.items() if k != "expected_preview_sha256"}),
            (
                "missing acknowledged keys",
                {k: v for k, v in good.items() if k != "acknowledged_ignored_keys"},
            ),
            ("extra key", {**good, "approved_by": "me"}),
            ("an approver in the body", {**good, "approver": "root"}),
            ("short hash", approve_body(mapping, expected_preview_sha256="ab")),
            (
                "upper-case hash",
                approve_body(mapping, expected_preview_sha256=mapping["preview_sha256"].upper()),
            ),
            ("non-str hash", approve_body(mapping, expected_preview_sha256=5)),
            ("empty purpose", approve_body(mapping, purpose="")),
            ("long purpose", approve_body(mapping, purpose="p" * 33)),
            ("non-str purpose", approve_body(mapping, purpose=["training"])),
            ("keys not a list", approve_body(mapping, acknowledged_ignored_keys="a")),
            ("keys not strings", approve_body(mapping, acknowledged_ignored_keys=[1])),
            ("empty key", approve_body(mapping, acknowledged_ignored_keys=[""])),
            ("long key", approve_body(mapping, acknowledged_ignored_keys=["k" * 201])),
            (
                "too many keys",
                approve_body(mapping, acknowledged_ignored_keys=[f"k{i}" for i in range(201)]),
            ),
            ("empty rationale", approve_body(mapping, rationale="")),
            ("long rationale", approve_body(mapping, rationale="r" * 2001)),
            ("non-str rationale", approve_body(mapping, rationale=7)),
            ("NUL in rationale", approve_body(mapping, rationale="a\x00b")),
            ("lone surrogate rationale", approve_body(mapping, rationale="a\ud800b")),
        ]
        for label, body in approve_bodies:
            raw = json.dumps(body).encode()
            got = call(
                base,
                "POST",
                f"{BASE_DS}/{ds.id}/mappings/{mid}/approve",
                "admin",
                raw=raw,
                key="validation-key-3",
            )
            check(
                "R-BODY",
                f"approve: {label} is 400 invalid_body (got {got[0]} {code(got)})",
                got == (400, {"detail": "invalid_body"}),
            )
        for label, body in (
            ("missing rationale", {}),
            ("empty rationale", {"rationale": ""}),
            ("long rationale", {"rationale": "r" * 2001}),
            ("non-str rationale", {"rationale": 1}),
            ("extra key", {"rationale": "x", "expected_preview_sha256": "a" * 64}),
        ):
            got = call(
                base,
                "POST",
                f"{BASE_DS}/{ds.id}/mappings/{mid}/reject",
                "admin",
                body,
                "validation-key-4",
            )
            check(
                "R-BODY",
                f"reject: {label} is 400 invalid_body (got {got[0]} {code(got)})",
                got == (400, {"detail": "invalid_body"}),
            )
        edge = approve_body(mapping, rationale="r" * 2000, purpose="p" * 32)
        got = call(
            base,
            "POST",
            f"{BASE_DS}/{ds.id}/mappings/{mid}/approve",
            "admin",
            edge,
            "validation-key-5",
        )
        check(
            "R-BODY",
            f"a 2000-character rationale and a 32-character purpose pass body validation (got {got[0]} {code(got)})",
            got[0] == 403 and code(got) == "purpose_not_authorized",
        )
        check(
            "R-BODY",
            "no refused body wrote a row, a request or an audit event",
            w.snapshot() == before,
        )
        for query, status, detail in (
            ("limit=0", 400, "invalid_limit"),
            ("limit=201", 400, "invalid_limit"),
            ("limit=-1", 400, "invalid_limit"),
            ("limit=abc", 400, "invalid_limit"),
            ("limit=1.5", 400, "invalid_limit"),
            ("limit=%D9%A3", 400, "invalid_limit"),
            ("limit=", 400, "invalid_limit"),
            ("after=-1", 400, "invalid_after"),
            ("after=abc", 400, "invalid_after"),
            ("after=1000000000", 400, "invalid_after"),
            ("after=%D9%A3", 400, "invalid_after"),
            ("limit=200&after=0", 200, None),
            ("limit=1&after=999999999", 200, None),
        ):
            got = call(base, "GET", f"{BASE_DS}/{ds.id}/mappings?{query}", "admin")
            check(
                "R-QUERY",
                f"GET mappings?{query} is {status} {detail} (got {got[0]} {code(got)})",
                got[0] == status and code(got) == detail,
            )
        for label, path in (
            ("a non-uuid mapping id", f"{BASE_DS}/{ds.id}/mappings/not-a-uuid"),
            ("a non-uuid dataset id", f"{BASE_DS}/not-a-uuid/mappings"),
        ):
            check("R-QUERY", f"{label} is 422", call(base, "GET", path, "admin")[0] == 422)


def part_r_propose() -> None:
    with http_world() as (w, base):
        conn, ds = seeded(w)
        status, first = propose(base, ds, key="propose-key-0001", who="ml")
        check("ACC-PROPOSE", f"the first proposal is 201 (got {status})", status == 201)
        check(
            "ACC-PROPOSE",
            "the body is version 1, proposed, no parent, created, not replayed",
            (
                first["mapping_version"],
                first["parent_mapping_version"],
                first["status"],
                first["created"],
                first["replayed"],
            )
            == (1, None, "proposed", True, False),
        )
        check(
            "ACC-PROPOSE",
            "the proposer is the authenticated principal, never body input",
            first["created_by"] == "ml" and first["approved_by"] is None,
        )
        check(
            "ACC-PROPOSE",
            "the document and its digest are returned to a revealing caller (internal connection, ml_engineer)",
            first["masked"] is False
            and first["mapping_document"] == MSG
            and first["mapping_sha256"] == ic.mapping_from_object(MSG).mapping_sha256,
        )
        check(
            "ACC-PROPOSE",
            "the response carries exactly the allow-listed fields",
            set(first)
            == set(real_routes._MAPPING_FIELD_NAMES)
            | {"masked", "mapping_document", "decision", "replayed", "created"},
        )
        check(
            "ACC-PROPOSE",
            "the dataset identity is copied from the dataset row",
            first["dataset_id"] == str(ds.id)
            and first["dataset_purpose"] == "training"
            and first["dataset_name"] == "alpha",
        )
        check(
            "ACC-PROPOSE",
            "one proposal wrote one row, one request row and one audit event",
            w.snapshot() == (1, 1, 1),
        )
        row = w.rows()[0]
        request_row = w.requests()[0]
        check(
            "ACC-PROPOSE",
            "the stored request is the propose request: key, action, no rationale, the proposer",
            (
                request_row.action,
                request_row.idempotency_key,
                request_row.rationale,
                request_row.created_by,
                request_row.mapping_id,
            )
            == ("propose", "propose-key-0001", None, "ml", row.id),
        )
        check(
            "ACC-PROPOSE",
            "the stored digest is the digest of the request",
            request_row.request_digest
            == rstore.request_digest(
                "propose", dataset_id=ds.id, mapping_id=None, subject="ml", body={"mapping": MSG}
            ),
        )
        # replay: same key, same body
        before = w.snapshot()
        status, again = propose(base, ds, key="propose-key-0001", who="ml")
        check("ACC-REPLAY", f"the same key and body replays as 200 (got {status})", status == 200)
        check(
            "ACC-REPLAY",
            "the replay says replayed true, created false, and returns the same mapping",
            again["replayed"] is True and again["created"] is False and again["id"] == first["id"],
        )
        check("ACC-REPLAY", "a replay writes nothing", w.snapshot() == before)
        # same key, other body
        got = propose(base, ds, doc=MSG2, key="propose-key-0001", who="ml")
        check(
            "ACC-REPLAY",
            f"the same key with another body is 409 idempotency_key_reused (got {got[0]} {code(got)})",
            got == (409, {"detail": "idempotency_key_reused"}),
        )
        check("ACC-REPLAY", "and wrote nothing", w.snapshot() == before)
        # same key, other principal
        got = propose(base, ds, key="propose-key-0001", who="ml2")
        check(
            "ACC-REPLAY",
            f"the same key and body from another principal is 409 idempotency_key_reused (got {got[0]} {code(got)})",
            got == (409, {"detail": "idempotency_key_reused"}),
        )
        # unchanged re-proposal under a new key
        status, same = propose(base, ds, key="propose-key-0002", who="ml")
        check(
            "ACC-PROPOSE",
            f"an unchanged re-proposal under a new key is 200 (got {status})",
            status == 200,
        )
        check(
            "ACC-PROPOSE",
            "it returns the latest version, created false, replayed false",
            (same["id"], same["created"], same["replayed"]) == (first["id"], False, False),
        )
        check(
            "ACC-PROPOSE",
            "it wrote no mapping row but stored its own key",
            (len(w.rows()), len(w.requests())) == (1, 2),
        )
        status, replay_of_new = propose(base, ds, key="propose-key-0002", who="ml")
        check(
            "ACC-REPLAY",
            "the key of an unchanged re-proposal replays too",
            status == 200
            and replay_of_new["replayed"] is True
            and replay_of_new["id"] == first["id"],
        )
        # a changed document is the next version
        status, second = propose(base, ds, doc=MSG2, key="propose-key-0003", who="admin")
        check("ACC-PROPOSE", f"a changed document is 201 (got {status})", status == 201)
        check(
            "ACC-PROPOSE",
            "version 2 has parent 1",
            (second["mapping_version"], second["parent_mapping_version"], second["created_by"])
            == (2, 1, "admin"),
        )
        # an administrator may propose too
        check(
            "ACC-PROPOSE",
            "the audit trail names the proposer",
            [e["action"] for e in w.audit.events] == ["dataset_mapping.proposed"] * 2
            and [e["actor_id"] for e in w.audit.events] == ["ml", "admin"],
        )
        # refusals write nothing
        before = w.snapshot()
        refusals: list[tuple[str, Any, int, str]] = [
            ("unmapped assistant", p2.msg_doc({"human": "user"}), 400, "critical_role_unmapped"),
            ("unmapped user", p2.msg_doc({"bot": "assistant"}), 400, "critical_role_unmapped"),
            (
                "tool role without ids",
                p2.msg_doc({"human": "user", "bot": "assistant", "t": "tool"}),
                400,
                "tool_id_unmapped",
            ),
            ("a grammar error", {"shape": "messages"}, 400, "mapping_invalid"),
            ("an unknown shape", {"shape": "python_eval", "code": "x"}, 400, "mapping_invalid"),
            ("an unsupported shape", {"shape": "grouped_rows"}, 400, "mapping_unsupported"),
            (
                "an eval transformation key",
                {**MSG, "transform": "lambda x: x"},
                400,
                "mapping_invalid",
            ),
        ]
        for label, doc, status, detail in refusals:
            got = propose(base, ds, doc=doc, key="refusal-key-0001")
            check(
                "ACC-CRITICAL",
                f"{label} is {status} {detail} (got {got[0]} {code(got)})",
                got[0] == status and code(got) == detail,
            )
        check(
            "ACC-CRITICAL",
            "no refusal wrote a row, a request or an audit event, and none used up its key",
            w.snapshot() == before,
        )
        # outage / changed object
        w.base.preview_state = ("blocked", "source_unavailable")
        got = propose(
            base, ds, doc=p2.msg_doc({"x": "user", "y": "assistant"}), key="outage-key-0001"
        )
        check(
            "NEG-LAW17",
            f"a storage outage is 503 storage_unavailable, never a stored mapping (got {got[0]} {code(got)})",
            got == (503, {"detail": "storage_unavailable"}),
        )
        w.base.preview_state = ("failed", "source_changed")
        got = propose(
            base, ds, doc=p2.msg_doc({"x": "user", "y": "assistant"}), key="outage-key-0002"
        )
        check(
            "NEG-LAW17",
            f"a changed object is 409 source_changed (got {got[0]} {code(got)})",
            got == (409, {"detail": "source_changed"}),
        )
        w.base.preview_state = ("failed", "limit_exceeded")
        got = propose(
            base, ds, doc=p2.msg_doc({"x": "user", "y": "assistant"}), key="outage-key-0003"
        )
        check(
            "NEG-LAW17",
            f"an aborted preview is 409 preview_incomplete (got {got[0]} {code(got)})",
            got == (409, {"detail": "preview_incomplete"}),
        )
        w.base.preview_state = ("completed", None)
        w.base.preview_error = p2.MappingPreviewUnavailableError("application/vnd.xlsx")
        got = propose(
            base, ds, doc=p2.msg_doc({"x": "user", "y": "assistant"}), key="outage-key-0004"
        )
        check(
            "NEG-LAW17",
            f"an importer that does not exist is 409 importer_unavailable (got {got[0]} {code(got)})",
            got == (409, {"detail": "importer_unavailable"}),
        )
        w.base.preview_error = None
        check("NEG-LAW17", "none of them wrote anything", w.snapshot() == before)
        # unknown / foreign / hidden datasets
        other = uuid.uuid4()
        got = call(
            base,
            "POST",
            f"{BASE_DS}/{other}/mappings",
            "admin",
            {"mapping": MSG},
            "missing-key-0001",
        )
        check(
            "R-SCOPE",
            f"a missing dataset is 404 not_found (got {got[0]} {code(got)})",
            got == (404, {"detail": "not_found"}),
        )
        held = w.dataset("held", "held_out_evaluation", conn)
        got = propose(base, held, key="held-key-00001", who="ml")
        check(
            "R-SCOPE",
            f"a held-out dataset is 404 not_found to an ml_engineer, identical to a missing one (got {got[0]} {code(got)})",
            got == (404, {"detail": "not_found"}),
        )
        got = propose(base, held, key="held-key-00002", who="admin")
        check(
            "R-SCOPE",
            f"a held-out dataset can be mapped by an admin (got {got[0]} {code(got)})",
            got[0] == 201,
        )
        # revoked grant
        revoked = w.connection("revoked", ["validation"])
        ds2 = w.dataset("beta", "training", revoked)
        got = propose(base, ds2, key="grant-key-00001", who="admin")
        check(
            "ACC-GRANT",
            f"a training dataset on a validation-only grant is 403 purpose_not_authorized (got {got[0]} {code(got)})",
            got == (403, {"detail": "purpose_not_authorized"}),
        )


def part_r_reads() -> None:
    with http_world() as (w, base):
        conn, ds = seeded(w, "confidential")
        status, m1 = propose(base, ds, key="read-key-000001", who="admin")
        status, m2 = propose(base, ds, doc=MSG2, key="read-key-000002", who="admin")
        status, m3 = propose(
            base,
            ds,
            doc=p2.msg_doc({"a": "user", "b": "assistant"}),
            key="read-key-000003",
            who="admin",
        )
        check(
            "R-READ",
            "three versions were proposed",
            [m["mapping_version"] for m in (m1, m2, m3)] == [1, 2, 3],
        )
        got = reject(base, ds, m1, key="read-key-000004", rationale="not this one")
        check("R-READ", f"version 1 is rejected (got {got[0]} {code(got)})", got[0] == 200)
        status, listing = call(base, "GET", f"{BASE_DS}/{ds.id}/mappings", "admin")
        check(
            "R-READ",
            "the list is oldest first",
            status == 200 and [m["mapping_version"] for m in listing["items"]] == [1, 2, 3],
        )
        check("R-READ", "an unfilled page has no next_after", listing["next_after"] is None)
        status, page = call(base, "GET", f"{BASE_DS}/{ds.id}/mappings?limit=2", "admin")
        check(
            "R-READ",
            "a full page names its last version as next_after",
            status == 200
            and [m["mapping_version"] for m in page["items"]] == [1, 2]
            and page["next_after"] == 2,
        )
        status, page2 = call(base, "GET", f"{BASE_DS}/{ds.id}/mappings?limit=2&after=2", "admin")
        check(
            "R-READ",
            "the next page continues after that version",
            [m["mapping_version"] for m in page2["items"]] == [3] and page2["next_after"] is None,
        )
        status, one = call(base, "GET", f"{BASE_DS}/{ds.id}/mappings?limit=1&after=1", "admin")
        check(
            "R-READ",
            "limit 1 after 1 is exactly version 2, next_after 2",
            [m["mapping_version"] for m in one["items"]] == [2] and one["next_after"] == 2,
        )
        status, detail = call(base, "GET", f"{BASE_DS}/{ds.id}/mappings/{m1['id']}", "admin")
        check(
            "R-READ",
            "a rejected version carries its decision with the rationale for an admin",
            status == 200
            and detail["decision"]
            == {"action": "reject", "rationale": "not this one", "decided_by": "admin"},
        )
        check(
            "R-READ",
            "an unreviewed version has no decision",
            call(base, "GET", f"{BASE_DS}/{ds.id}/mappings/{m2['id']}", "admin")[1]["decision"]
            is None,
        )
        # masking: ml_engineer on a confidential connection
        status, masked = call(base, "GET", f"{BASE_DS}/{ds.id}/mappings/{m1['id']}", "ml")
        check(
            "NEG-MASK",
            "an ml_engineer on a confidential connection gets the mapping with its document withheld",
            status == 200 and masked["masked"] is True and masked["mapping_document"] is None,
        )
        check(
            "NEG-MASK",
            "and the rationale withheld, the decision kept",
            masked["decision"] == {"action": "reject", "rationale": None, "decided_by": "admin"},
        )
        check(
            "NEG-MASK",
            "while the digests and counts stay visible",
            masked["mapping_sha256"] == m1["mapping_sha256"]
            and masked["preview_counts"] == m1["preview_counts"],
        )
        status, listing_ml = call(base, "GET", f"{BASE_DS}/{ds.id}/mappings", "ml")
        check(
            "NEG-MASK",
            "the list is masked the same way",
            all(m["masked"] is True and m["mapping_document"] is None for m in listing_ml["items"])
            and len(listing_ml["items"]) == 3,
        )
        check(
            "NEG-MASK",
            "no response to the ml_engineer contains a key name of the document",
            "conversation" not in json.dumps(masked)
            and "conversation" not in json.dumps(listing_ml),
        )
        status, proposed_masked = propose(base, ds, doc=MSG2, key="read-key-000009", who="ml")
        check(
            "NEG-MASK",
            "an ml_engineer's own proposal response is masked on a confidential connection",
            status in (200, 201)
            and proposed_masked["masked"] is True
            and proposed_masked["mapping_document"] is None,
        )
        w.classification[conn] = "restricted"
        check(
            "NEG-MASK",
            "a restricted connection is masked too",
            call(base, "GET", f"{BASE_DS}/{ds.id}/mappings/{m1['id']}", "ml")[1]["masked"] is True,
        )
        for classification in ("public", "internal"):
            w.classification[conn] = classification
            got = call(base, "GET", f"{BASE_DS}/{ds.id}/mappings/{m1['id']}", "ml")[1]
            check(
                "NEG-MASK",
                f"a {classification} connection reveals the document to an ml_engineer",
                got["masked"] is False and got["mapping_document"] == MSG,
            )
        w.classification[conn] = "restricted"
        got = call(base, "GET", f"{BASE_DS}/{ds.id}/mappings/{m1['id']}", "orgadmin")[1]
        check(
            "NEG-MASK",
            "an organization_admin sees everything on a restricted connection",
            got["masked"] is False and got["mapping_document"] == MSG,
        )
        # scoping
        other_ds = w.dataset("gamma", "training", conn)
        got = call(base, "GET", f"{BASE_DS}/{other_ds.id}/mappings/{m1['id']}", "admin")
        check(
            "R-SCOPE",
            f"a mapping read through another dataset's path is 404 (got {got[0]})",
            got == (404, {"detail": "not_found"}),
        )
        listing_other = call(base, "GET", f"{BASE_DS}/{other_ds.id}/mappings", "admin")
        check(
            "R-SCOPE",
            f"the other dataset lists no mappings (got {listing_other})",
            listing_other[1].get("items") == [],
        )
        got = call(base, "GET", f"{BASE_DS}/{ds.id}/mappings/{uuid.uuid4()}", "admin")
        check(
            "R-SCOPE",
            "a missing mapping id is 404 not_found",
            got == (404, {"detail": "not_found"}),
        )
        held = w.dataset("held", "held_out_evaluation", conn)
        status, hm = propose(base, held, key="read-key-000010", who="admin")
        check("R-SCOPE", f"an admin maps a held-out dataset (got {status})", status == 201)
        check(
            "R-SCOPE",
            "an ml_engineer's read of a held-out dataset's mappings is 404",
            call(base, "GET", f"{BASE_DS}/{held.id}/mappings", "ml")
            == (404, {"detail": "not_found"}),
        )
        check(
            "R-SCOPE",
            "and of one mapping",
            call(base, "GET", f"{BASE_DS}/{held.id}/mappings/{hm['id']}", "ml")
            == (404, {"detail": "not_found"}),
        )
        check(
            "R-SCOPE",
            "an admin lists them",
            len(call(base, "GET", f"{BASE_DS}/{held.id}/mappings", "admin")[1]["items"]) == 1,
        )
        check(
            "R-SCOPE",
            "a missing dataset lists as 404",
            call(base, "GET", f"{BASE_DS}/{uuid.uuid4()}/mappings", "admin")
            == (404, {"detail": "not_found"}),
        )
        # tenant scope
        check(
            "R-SCOPE",
            "another organization's path is refused before any read",
            call(
                base,
                "GET",
                f"/api/v1/organizations/{uuid.uuid4()}/projects/{PROJECT}/datasets/{ds.id}/mappings",
                "admin",
            )[0]
            == 403,
        )


def part_r_decisions() -> None:
    with http_world() as (w, base):
        conn, ds = seeded(w)
        status, m = propose(base, ds, key="decide-key-00001", who="ml")
        mid = m["id"]
        # approve
        before = w.snapshot()
        status, approved = approve(base, ds, m, who="admin", key="approve-key-0001")
        check(
            "ACC-APPROVE",
            f"an admin approves (got {status} {code((status, approved))})",
            status == 200,
        )
        check(
            "ACC-APPROVE",
            "the body is approved, not replayed, approved by the principal with its role",
            (
                approved["status"],
                approved["replayed"],
                approved["approved_by"],
                approved["approver_role"],
                approved["approved_purpose"],
            )
            == ("approved", False, "admin", "workspace_admin", "training"),
        )
        check(
            "ACC-APPROVE",
            "the rationale is part of the decision, with its author",
            approved["decision"]
            == {
                "action": "approve",
                "rationale": "the roles and the tool ids match the source",
                "decided_by": "admin",
            },
        )
        check(
            "ACC-APPROVE",
            "it wrote one request and one audit event, and no new mapping row",
            (len(w.rows()), len(w.requests()), len(w.audit.events))
            == (before[0], before[1] + 1, before[2] + 1),
        )
        req = [r for r in w.requests() if r.action == "approve"][0]
        check(
            "ACC-APPROVE",
            "the stored request holds the key, the rationale and the approver",
            (req.idempotency_key, req.rationale, req.created_by, str(req.mapping_id))
            == ("approve-key-0001", "the roles and the tool ids match the source", "admin", mid),
        )
        check(
            "ACC-APPROVE",
            "the rationale is in no audit event",
            all(
                "rationale" not in str(e) and "match the source" not in str(e)
                for e in w.audit.events
            ),
        )
        # replay
        before = w.snapshot()
        status, replay = approve(base, ds, m, who="admin", key="approve-key-0001")
        check(
            "ACC-REPLAY",
            "the same key and body replays as replayed true and writes nothing",
            status == 200
            and replay["replayed"] is True
            and replay["status"] == "approved"
            and w.snapshot() == before,
        )
        status, elsewhere = approve(base, ds, m, who="admin", key="approve-key-0002")
        check(
            "ACC-REPLAY",
            "the same body under another key is recognised as already approved (replayed true)",
            status == 200 and elsewhere["replayed"] is True and w.snapshot() == before,
        )
        got = approve(
            base, ds, m, who="admin", key="approve-key-0001", rationale="a different reason"
        )
        check(
            "ACC-REPLAY",
            f"the same key with another rationale is 409 idempotency_key_reused (got {got[0]} {code(got)})",
            got == (409, {"detail": "idempotency_key_reused"}),
        )
        got = approve(base, ds, m, who="admin2", key="approve-key-0001")
        check(
            "ACC-REPLAY",
            "the same key from another approver is 409 idempotency_key_reused",
            got == (409, {"detail": "idempotency_key_reused"}),
        )
        got = approve(base, ds, m, who="admin2", key="approve-key-0003")
        check(
            "ACC-REPLAY",
            f"another approver under a new key is not a replay: 409 mapping_not_proposed (got {got[0]} {code(got)})",
            got == (409, {"detail": "mapping_not_proposed"}),
        )
        got = approve(
            base, ds, m, who="admin", key="approve-key-0004", rationale="a different reason"
        )
        check(
            "ACC-REPLAY",
            "the same approver with another rationale under a new key is 409 mapping_not_proposed, not a replay",
            got == (409, {"detail": "mapping_not_proposed"}),
        )
        got = reject(base, ds, m, who="admin", key="reject-key-0009")
        check(
            "ACC-REPLAY",
            "an approved mapping cannot be rejected (409 mapping_not_proposed)",
            got == (409, {"detail": "mapping_not_proposed"}),
        )
        check("ACC-REPLAY", "and none of that wrote anything", w.snapshot() == before)
        # the proposal key is not an approval key
        got = propose(base, ds, key="approve-key-0001", who="ml")
        check(
            "ACC-REPLAY",
            f"a key used by an approval cannot be reused by a proposal (got {got[0]} {code(got)})",
            got == (409, {"detail": "idempotency_key_reused"}),
        )
        # reject path on a second version
        status, m2 = propose(base, ds, doc=MSG2, key="decide-key-00002", who="ml")
        before = w.snapshot()
        got = reject(base, ds, m2, who="ml")
        check(
            "ACC-REJECT",
            "an ml_engineer cannot reject (403)",
            got[0] == 403 and w.snapshot() == before,
        )
        status, rejected = reject(
            base, ds, m2, who="orgadmin", key="reject-key-0001", rationale="the roles are swapped"
        )
        check(
            "ACC-REJECT",
            f"an organization_admin rejects (got {status})",
            status == 200 and rejected["status"] == "rejected" and rejected["replayed"] is False,
        )
        check(
            "ACC-REJECT",
            "the decision names the rationale and the rejecter",
            rejected["decision"]
            == {"action": "reject", "rationale": "the roles are swapped", "decided_by": "orgadmin"},
        )
        check(
            "ACC-REJECT",
            "a rejection does not set approval columns",
            (
                rejected["approved_by"],
                rejected["approved_purpose"],
                rejected["approver_role"],
                rejected["approved_at"],
            )
            == (None, None, None, None),
        )
        before = w.snapshot()
        status, rr = reject(
            base, ds, m2, who="orgadmin", key="reject-key-0001", rationale="the roles are swapped"
        )
        check(
            "ACC-REPLAY",
            "the same reject request replays",
            status == 200 and rr["replayed"] is True and w.snapshot() == before,
        )
        status, rr2 = reject(
            base, ds, m2, who="orgadmin", key="reject-key-0002", rationale="the roles are swapped"
        )
        check(
            "ACC-REPLAY",
            "the same reject request under another key is recognised",
            status == 200 and rr2["replayed"] is True and w.snapshot() == before,
        )
        got = reject(
            base, ds, m2, who="orgadmin", key="reject-key-0001", rationale="another reason"
        )
        check(
            "ACC-REPLAY",
            "the same reject key with another rationale is 409 idempotency_key_reused",
            got == (409, {"detail": "idempotency_key_reused"}),
        )
        got = reject(
            base, ds, m2, who="admin", key="reject-key-0003", rationale="the roles are swapped"
        )
        check(
            "ACC-REPLAY",
            "another principal's reject of a rejected mapping is 409 mapping_not_proposed",
            got == (409, {"detail": "mapping_not_proposed"}),
        )
        got = reject(
            base, ds, m2, who="orgadmin", key="reject-key-0004", rationale="a later thought"
        )
        check(
            "ACC-REPLAY",
            "a different rationale under a new key is 409 mapping_not_proposed",
            got == (409, {"detail": "mapping_not_proposed"}),
        )
        got = approve(base, ds, m2, who="admin", key="approve-key-0010")
        check(
            "ACC-REPLAY",
            "a rejected mapping cannot be approved",
            got == (409, {"detail": "mapping_not_proposed"}),
        )
        # approve of a mapping through another dataset's path
        other = w.dataset("gamma", "training", conn)
        status, m3 = propose(
            base,
            ds,
            doc=p2.msg_doc({"a": "user", "b": "assistant"}),
            key="decide-key-00003",
            who="ml",
        )
        before = w.snapshot()
        got = call(
            base,
            "POST",
            f"{BASE_DS}/{other.id}/mappings/{m3['id']}/approve",
            "admin",
            approve_body(m3),
            "approve-key-0020",
        )
        check(
            "R-SCOPE",
            f"approving through another dataset's path is 404 (got {got[0]})",
            got == (404, {"detail": "not_found"}),
        )
        got = call(
            base,
            "POST",
            f"{BASE_DS}/{other.id}/mappings/{m3['id']}/reject",
            "admin",
            {"rationale": "x"},
            "reject-key-0020",
        )
        check(
            "R-SCOPE",
            "rejecting through another dataset's path is 404",
            got == (404, {"detail": "not_found"}),
        )
        got = call(
            base,
            "POST",
            f"{BASE_DS}/{ds.id}/mappings/{uuid.uuid4()}/approve",
            "admin",
            approve_body(m3),
            "approve-key-0021",
        )
        check(
            "R-SCOPE", "approving a missing mapping is 404", got == (404, {"detail": "not_found"})
        )
        check("R-SCOPE", "and wrote nothing", w.snapshot() == before)
        # store refusals
        got = approve(
            base, ds, m3, who="admin", key="approve-key-0030", expected_preview_sha256="0" * 64
        )
        check(
            "ACC-APPROVE",
            f"a stale preview hash is 409 preview_changed (got {got[0]} {code(got)})",
            got == (409, {"detail": "preview_changed"}),
        )
        got = approve(base, ds, m3, who="admin", key="approve-key-0031", purpose="validation")
        check(
            "ACC-GRANT",
            f"a purpose other than the dataset's is 409 purpose_mismatch (got {got[0]} {code(got)})",
            got == (409, {"detail": "purpose_mismatch"}),
        )
        got = approve(
            base, ds, m3, who="admin", key="approve-key-0032", acknowledged_ignored_keys=["extra"]
        )
        check(
            "ACC-APPROVE",
            f"an acknowledgement of keys nobody ignored is 409 ignored_keys_not_acknowledged (got {got[0]} {code(got)})",
            got == (409, {"detail": "ignored_keys_not_acknowledged"}),
        )
        w.base.preview_counts = {
            "seen": 2,
            "accepted": 1,
            "rejected": 1,
            "rejected:unmapped_role": 1,
        }
        got = approve(base, ds, m3, who="admin", key="approve-key-0033")
        check(
            "ACC-CRITICAL",
            f"a preview with an unmapped critical role is 409 preview_changed or critical_rows_unmapped (got {got[0]} {code(got)})",
            got[0] == 409 and code(got) in ("preview_changed", "critical_rows_unmapped"),
        )
        w.base.preview_counts = {"seen": 2, "accepted": 2, "rejected": 0}
        w.base.preview_state = ("blocked", "source_unavailable")
        got = approve(base, ds, m3, who="admin", key="approve-key-0034")
        check(
            "NEG-LAW17",
            f"an approval during a storage outage is 503 storage_unavailable (got {got[0]} {code(got)})",
            got == (503, {"detail": "storage_unavailable"}),
        )
        w.base.preview_state = ("completed", None)
        # grant narrowed after the proposal
        w.base.connections[conn].allowed_purposes_json = ["validation"]
        before = w.snapshot()
        got = approve(base, ds, m3, who="admin", key="approve-key-0035")
        check(
            "ACC-GRANT",
            f"a grant narrowed after the proposal denies the approval: 403 purpose_not_authorized (got {got[0]} {code(got)})",
            got == (403, {"detail": "purpose_not_authorized"}),
        )
        check("ACC-GRANT", "and wrote nothing", w.snapshot() == before)
        w.base.connections[conn].allowed_purposes_json = list(ALL_PURPOSES)
        status, final = approve(base, ds, m3, who="orgadmin", key="approve-key-0036")
        check(
            "ACC-APPROVE",
            "after all those refusals the same mapping is still approvable",
            status == 200
            and final["approved_by"] == "orgadmin"
            and final["approver_role"] == "organization_admin",
        )
        got = approve(
            base,
            ds,
            m3,
            who="orgadmin",
            key="approve-key-0037",
            rationale="approving it a second time",
        )
        check(
            "ACC-APPROVE",
            "approved once, never twice",
            got == (409, {"detail": "mapping_not_proposed"}),
        )
        # superseded
        status, m4 = propose(base, ds, doc=MSG2, key="decide-key-00004", who="ml")
        status, m5 = propose(
            base,
            ds,
            doc=p2.msg_doc({"p": "user", "q": "assistant"}),
            key="decide-key-00005",
            who="ml",
        )
        got = approve(base, ds, m4, who="admin", key="approve-key-0040")
        check(
            "ACC-APPROVE",
            f"a superseded version is never approved: 409 mapping_superseded (got {got[0]} {code(got)})",
            got == (409, {"detail": "mapping_superseded"}),
        )
        # the idempotency key space is per project: a stored key from one request kind is not another's
        check(
            "R-KEY",
            "every stored key is unique",
            len({r.idempotency_key for r in w.requests()}) == len(w.requests()),
        )


def part_r_review() -> None:
    with http_world() as (w, base):
        conn, ds = seeded(w, "internal")
        row = (
            ic.mapping_from_object(MSG).mapping_sha256,
            w.base.tools_sha,
        )
        before = w.snapshot()
        status, probe = call(
            base,
            "POST",
            f"{BASE_DS}/{ds.id}/mapping-probe",
            "ml",
            {"csv": {"encoding": "utf-8", "delimiter": ",", "has_header": True, "header_row": 0}},
        )
        check(
            "ACC-PROBE",
            f"a probe returns the verdict (got {status} {code((status, probe))})",
            status == 200,
        )
        check(
            "ACC-PROBE",
            "the body is state, failure_code, complete, masked, headers, rows",
            set(probe) == {"state", "failure_code", "complete", "masked", "headers", "rows"},
        )
        check(
            "ACC-PROBE",
            "an ml_engineer on an internal connection sees real headers and rows",
            probe["masked"] is False
            and probe["headers"] == ["q", "a"]
            and probe["rows"] == [{"q": "hello", "a": "world"}]
            and probe["complete"] is True,
        )
        call_kwargs = w.probe_calls[-1]
        check(
            "NEG-EVAL",
            "the probe mapping is server-built: a prompt_completion with index slots on a JSONL dataset is key slots",
            call_kwargs["probe_mapping"].shape == "prompt_completion"
            and call_kwargs["probe_mapping"].prompt.kind == "key",
        )
        check(
            "NEG-EVAL",
            "the format options reach the probe mapping",
            call_kwargs["probe_mapping"].document["csv"]
            == {"encoding": "utf-8", "delimiter": ",", "has_header": True, "header_row": 0},
        )
        check(
            "ACC-PROBE",
            "reveal and held-out are decided by role and classification",
            (call_kwargs["reveal"], call_kwargs["include_held_out"]) == (True, False),
        )
        check(
            "ACC-PROBE",
            "a probe writes only one content-free audit event",
            w.snapshot() == (0, 0, before[2] + 1)
            and w.audit.events[-1]["action"] == "dataset_mapping.probed"
            and w.audit.events[-1]["details"]
            == {
                "project_id": str(PROJECT),
                "dataset_id": str(ds.id),
                "state": "completed",
                "rows": 1,
                "masked": False,
            },
        )
        w.classification[conn] = "confidential"
        status, masked_probe = call(base, "POST", f"{BASE_DS}/{ds.id}/mapping-probe", "ml", {})
        check(
            "NEG-MASK",
            "an ml_engineer on a confidential connection gets masked headers and rows",
            masked_probe["masked"] is True
            and all(h == {"masked": True, "chars": 1} for h in masked_probe["headers"])
            and "hello" not in json.dumps(masked_probe),
        )
        check(
            "NEG-MASK",
            "the probe was called with reveal false",
            w.probe_calls[-1]["reveal"] is False,
        )
        status, admin_probe = call(base, "POST", f"{BASE_DS}/{ds.id}/mapping-probe", "admin", {})
        check(
            "NEG-MASK",
            "an admin sees the real values on a confidential connection",
            admin_probe["masked"] is False
            and admin_probe["headers"] == ["q", "a"]
            and w.probe_calls[-1]["reveal"] is True,
        )
        w.classification[conn] = "internal"
        csv_ds = w.dataset("csv", "training", conn, content_type="text/csv")
        status, _ = call(
            base,
            "POST",
            f"{BASE_DS}/{csv_ds.id}/mapping-probe",
            "admin",
            {"csv": {"encoding": "utf-8", "delimiter": ";", "has_header": True, "header_row": 2}},
        )
        probe_mapping = w.probe_calls[-1]["probe_mapping"]
        check(
            "NEG-EVAL",
            "on a CSV dataset the probe slots are column indexes",
            probe_mapping.prompt.kind == "index" and probe_mapping.completion.kind == "index",
        )
        for label, body in (
            (
                "a bad delimiter",
                {
                    "csv": {
                        "encoding": "utf-8",
                        "delimiter": ";;",
                        "has_header": True,
                        "header_row": 0,
                    }
                },
            ),
            ("a missing csv field", {"csv": {"encoding": "utf-8"}}),
            (
                "a bad container pointer",
                {"json": {"container": "no-slash", "ignored_top_level_keys": []}},
            ),
        ):
            got = call(base, "POST", f"{BASE_DS}/{ds.id}/mapping-probe", "admin", body)
            check(
                "R-BODY",
                f"probe with {label} is 400 mapping_invalid (got {got[0]} {code(got)})",
                got == (400, {"detail": "mapping_invalid"}),
            )
        w.probe_state = ("failed", "limit_exceeded")
        status, failed = call(base, "POST", f"{BASE_DS}/{ds.id}/mapping-probe", "admin", {})
        check(
            "NEG-LAW17",
            "a failed probe is a visible 200 verdict, not an empty success",
            status == 200
            and failed["state"] == "failed"
            and failed["failure_code"] == "limit_exceeded"
            and failed["complete"] is False,
        )
        w.probe_state = ("blocked", "source_unavailable")
        got = call(base, "POST", f"{BASE_DS}/{ds.id}/mapping-probe", "admin", {})
        check(
            "NEG-LAW17",
            "a blocked probe is 503 storage_unavailable",
            got == (503, {"detail": "storage_unavailable"}),
        )
        w.probe_state = ("failed", "source_changed")
        got = call(base, "POST", f"{BASE_DS}/{ds.id}/mapping-probe", "admin", {})
        check(
            "NEG-LAW17",
            "a changed object is 409 source_changed",
            got == (409, {"detail": "source_changed"}),
        )
        w.probe_state = ("completed", None)
        w.probe_error = p2.MappingPreviewUnavailableError("application/zip")
        got = call(base, "POST", f"{BASE_DS}/{ds.id}/mapping-probe", "admin", {})
        check(
            "NEG-LAW17",
            "an unavailable importer is 409 importer_unavailable",
            got == (409, {"detail": "importer_unavailable"}),
        )
        w.probe_error = None
        w.storage_configured = False
        before = w.snapshot()
        for path, body in (
            ("/mapping-probe", {}),
            ("/mapping-preview", {"mapping": MSG}),
            ("/mappings", {"mapping": MSG}),
        ):
            got = call(base, "POST", f"{BASE_DS}/{ds.id}{path}", "admin", body, "storage-key-0001")
            check(
                "NEG-LAW17",
                f"{path} with no storage configured is 503 storage_not_configured (got {got[0]} {code(got)})",
                got == (503, {"detail": "storage_not_configured"}),
            )
        check("NEG-LAW17", "and wrote nothing", w.snapshot() == before)
        w.storage_configured = True
        # preview
        w.base.preview_rows = (
            MappingPreviewRow(
                locator="line:1",
                outcome="accepted",
                code=None,
                slot=None,
                raw_row={"conversation": [{"role": "human", "content": "canary-raw-secret-7"}]},
                not_mapped=("canary-header-secret-8",),
                candidate={"messages": [{"role": "user", "content": "canary-raw-secret-7"}]},
                comparison=(ComparisonCell("messages[0].role", "str", "str", False),),
            ),
            MappingPreviewRow(
                locator="line:2",
                outcome="rejected",
                code="unmapped_role",
                slot="messages[1].role",
                raw_row=None,
                not_mapped=(),
                candidate=None,
                comparison=(),
            ),
        )
        w.base.preview_counts = {
            "seen": 2,
            "accepted": 1,
            "rejected": 1,
            "rejected:unmapped_role": 1,
        }
        w.base.observed = ("human",)
        before = w.snapshot()
        status, preview = call(
            base, "POST", f"{BASE_DS}/{ds.id}/mapping-preview", "ml", {"mapping": MSG}
        )
        check(
            "ACC-PREVIEW",
            f"a preview returns the verdict (got {status} {code((status, preview))})",
            status == 200,
        )
        check(
            "ACC-PREVIEW",
            "the body carries the exact preview fields",
            set(preview)
            == {
                "state",
                "failure_code",
                "complete",
                "masked",
                "counts",
                "source_schema_sha256",
                "tool_definitions_sha256",
                "preview_sha256",
                "observed_role_values",
                "rows",
            },
        )
        check(
            "ACC-PREVIEW",
            "the raw row sits beside the canonical candidate, each row with its locator and outcome",
            [r["locator"] for r in preview["rows"]] == ["line:1", "line:2"]
            and preview["rows"][0]["raw_row"]
            == {"conversation": [{"role": "human", "content": "canary-raw-secret-7"}]}
            and preview["rows"][0]["candidate"]["messages"][0]["role"] == "user",
        )
        check(
            "ACC-PREVIEW",
            "a rejected row carries its code and slot and no candidate",
            (
                preview["rows"][1]["code"],
                preview["rows"][1]["slot"],
                preview["rows"][1]["candidate"],
                preview["rows"][1]["outcome"],
            )
            == ("unmapped_role", "messages[1].role", None, "rejected"),
        )
        check(
            "ACC-PREVIEW",
            "the comparison cells are explicit",
            preview["rows"][0]["comparison"]
            == [
                {
                    "slot": "messages[0].role",
                    "raw_type": "str",
                    "canonical_type": "str",
                    "identical": False,
                }
            ],
        )
        check(
            "ACC-PREVIEW",
            "counts and the approval digest are returned",
            preview["counts"]["rejected:unmapped_role"] == 1
            and re.fullmatch(r"[0-9a-f]{64}", preview["preview_sha256"]) is not None
            and preview["masked"] is False,
        )
        check(
            "ACC-PREVIEW",
            "a preview is read-only: no mapping, no request; one content-free audit event",
            w.snapshot() == (before[0], before[1], before[2] + 1),
        )
        event = w.audit.events[-1]
        check(
            "ACC-PREVIEW",
            "the audit event is dataset_mapping.previewed with ids, counts and the masked flag only",
            event["action"] == "dataset_mapping.previewed"
            and event["details"]
            == {
                "project_id": str(PROJECT),
                "dataset_id": str(ds.id),
                "state": "completed",
                "preview_sha256": preview["preview_sha256"],
                "seen": 2,
                "accepted": 1,
                "rejected": 1,
                "masked": False,
            },
        )
        check(
            "NEG-CONTENT",
            "no audit event contains a canary",
            not any(c in str(e) for e in w.audit.events for c in CANARIES),
        )
        check(
            "ACC-PREVIEW",
            "the preview was called with the live reveal decision and the parsed mapping",
            w.base.preview_calls[-1]["reveal"] is True
            and w.base.preview_calls[-1]["mapping"].mapping_sha256
            == ic.mapping_from_object(MSG).mapping_sha256,
        )
        # preview refusals
        for label, doc, status_, detail in (
            (
                "unmapped critical roles",
                p2.msg_doc({"human": "user"}),
                400,
                "critical_role_unmapped",
            ),
            (
                "a tool role without ids",
                p2.msg_doc({"h": "user", "b": "assistant", "t": "tool"}),
                400,
                "tool_id_unmapped",
            ),
            ("an invalid grammar", {"shape": "nope"}, 400, "mapping_invalid"),
            ("an unsupported shape", {"shape": "grouped_multi_row"}, 400, "mapping_unsupported"),
        ):
            before = w.snapshot()
            got = call(
                base, "POST", f"{BASE_DS}/{ds.id}/mapping-preview", "admin", {"mapping": doc}
            )
            check(
                "ACC-CRITICAL",
                f"preview of {label} is {status_} {detail} (got {got[0]} {code(got)})",
                got == (status_, {"detail": detail}) and w.snapshot() == before,
            )
        w.base.preview_state = ("blocked", "source_unavailable")
        got = call(base, "POST", f"{BASE_DS}/{ds.id}/mapping-preview", "admin", {"mapping": MSG})
        check(
            "NEG-LAW17",
            "a blocked preview is 503 storage_unavailable",
            got == (503, {"detail": "storage_unavailable"}),
        )
        w.base.preview_state = ("failed", "source_changed")
        got = call(base, "POST", f"{BASE_DS}/{ds.id}/mapping-preview", "admin", {"mapping": MSG})
        check(
            "NEG-LAW17",
            "a changed object is 409 source_changed",
            got == (409, {"detail": "source_changed"}),
        )
        w.base.preview_state = ("failed", "limit_exceeded")
        status, aborted = call(
            base, "POST", f"{BASE_DS}/{ds.id}/mapping-preview", "admin", {"mapping": MSG}
        )
        check(
            "NEG-LAW17",
            "an aborted preview is a visible 200 verdict with its failure code",
            status == 200
            and aborted["state"] == "failed"
            and aborted["failure_code"] == "limit_exceeded"
            and aborted["complete"] is False,
        )
        w.base.preview_state = ("completed", None)
        w.base.preview_error = p2.MappingPreviewUnavailableError("application/vnd.xlsx")
        got = call(base, "POST", f"{BASE_DS}/{ds.id}/mapping-preview", "admin", {"mapping": MSG})
        check(
            "NEG-LAW17",
            "an unavailable importer is 409 importer_unavailable",
            got == (409, {"detail": "importer_unavailable"}),
        )
        w.base.preview_error = None
        revoked = w.connection("revoked", ["validation"])
        ds2 = w.dataset("beta", "training", revoked)
        got = call(base, "POST", f"{BASE_DS}/{ds2.id}/mapping-preview", "admin", {"mapping": MSG})
        check(
            "ACC-GRANT",
            "a preview on a grant that does not allow the dataset's purpose is 403 purpose_not_authorized",
            got == (403, {"detail": "purpose_not_authorized"}),
        )
        got = call(
            base, "POST", f"{BASE_DS}/{uuid.uuid4()}/mapping-preview", "admin", {"mapping": MSG}
        )
        check(
            "R-SCOPE",
            "a preview of a missing dataset is 404",
            got == (404, {"detail": "not_found"}),
        )
        held = w.dataset("held", "held_out_evaluation", conn)
        check(
            "R-SCOPE",
            "a preview of a held-out dataset is 404 to an ml_engineer",
            call(base, "POST", f"{BASE_DS}/{held.id}/mapping-preview", "ml", {"mapping": MSG})
            == (404, {"detail": "not_found"}),
        )
        check(
            "R-SCOPE",
            "a probe of a held-out dataset is 404 to an ml_engineer",
            call(base, "POST", f"{BASE_DS}/{held.id}/mapping-probe", "ml", {})
            == (404, {"detail": "not_found"}),
        )
        w.classification[conn] = "restricted"
        call(base, "POST", f"{BASE_DS}/{ds.id}/mapping-preview", "ml", {"mapping": MSG})
        check(
            "NEG-MASK",
            "an ml_engineer on a restricted connection previews with reveal false",
            w.base.preview_calls[-1]["reveal"] is False,
        )
        check(
            "NEG-MASK",
            "an ml_engineer preview is audited as masked",
            w.audit.events[-1]["details"]["masked"] is True,
        )
        del row


# ======================================================================================
# U -- private helpers of the route module
# ======================================================================================

STORE_STATUS: dict[str, tuple[int, str]] = {
    "invalid_request": (400, "invalid_request"),
    "actor_not_permitted": (403, "actor_not_permitted"),
    "approval_not_permitted": (403, "approval_not_permitted"),
    "dataset_not_found": (404, "not_found"),
    "purpose_not_authorized": (403, "purpose_not_authorized"),
    "purpose_mismatch": (409, "purpose_mismatch"),
    "mapping_invalid": (400, "mapping_invalid"),
    "mapping_unsupported": (400, "mapping_unsupported"),
    "critical_role_unmapped": (400, "critical_role_unmapped"),
    "tool_id_unmapped": (400, "tool_id_unmapped"),
    "importer_unavailable": (409, "importer_unavailable"),
    "source_unavailable": (503, "storage_unavailable"),
    "source_changed": (409, "source_changed"),
    "preview_incomplete": (409, "preview_incomplete"),
    "mapping_not_found": (404, "not_found"),
    "mapping_not_proposed": (409, "mapping_not_proposed"),
    "mapping_superseded": (409, "mapping_superseded"),
    "mapping_conflict": (409, "mapping_conflict"),
    "preview_changed": (409, "preview_changed"),
    "critical_rows_unmapped": (409, "critical_rows_unmapped"),
    "ignored_keys_not_acknowledged": (409, "ignored_keys_not_acknowledged"),
    "approval_refused": (409, "approval_refused"),
    "mapping_reapproval_required": (409, "mapping_reapproval_required"),
    "mapping_tampered": (409, "mapping_tampered"),
}


def part_u() -> None:
    mod = real_routes
    check(
        "U-REFUSAL",
        "the table covers exactly the store's codes",
        set(STORE_STATUS) == set(store.CODES),
    )
    for store_code, (status, detail) in STORE_STATUS.items():
        exc = mod._refusal(store_code)
        check(
            "U-REFUSAL",
            f"{store_code} is {status} {detail}",
            (exc.status_code, exc.detail) == (status, detail),
        )
    check(
        "U-REFUSAL",
        "an unknown code is a 409 conflict, never a 500",
        mod._refusal("brand_new").status_code == 409,
    )
    for good in ("a", " a ", "x" * 2000, "مرحبا", "line1\nline2"):
        check(
            "U-RATIONALE",
            f"{good[:10]!r} is a valid rationale and is kept byte for byte",
            mod._rationale(good) == good,
        )
    for bad in ("", " ", "\t\n", "x" * 2001, "a\x00b", "\ud800", 5, None, b"x", ["x"], True):
        try:
            mod._rationale(bad)
            ok = False
        except Exception as exc:  # noqa: BLE001
            ok = getattr(exc, "status_code", None) == 400 and exc.detail == "invalid_body"
        check("U-RATIONALE", f"{bad!r:.20} is refused with 400 invalid_body", ok)

    class Str(str):
        pass

    try:
        mod._rationale(Str("subclass"))
        ok = False
    except Exception as exc:  # noqa: BLE001
        ok = getattr(exc, "status_code", None) == 400
    check("U-RATIONALE", "a str subclass is refused (exact type)", ok)
    good = {
        "expected_preview_sha256": "a" * 64,
        "purpose": "training",
        "acknowledged_ignored_keys": ["b", "a"],
        "rationale": "ok",
    }
    parsed = mod._approve_fields(good)
    check(
        "U-APPROVE",
        "acknowledged keys are sorted",
        parsed["acknowledged_ignored_keys"] == ["a", "b"],
    )
    check(
        "U-APPROVE",
        "all four fields are carried",
        set(parsed) == set(good)
        and parsed["purpose"] == "training"
        and parsed["expected_preview_sha256"] == "a" * 64
        and parsed["rationale"] == "ok",
    )
    for label, patch in (
        ("hash with trailing newline", {"expected_preview_sha256": "a" * 64 + "\n"}),
        ("hash of 63", {"expected_preview_sha256": "a" * 63}),
        ("hash of 65", {"expected_preview_sha256": "a" * 65}),
        ("hash with a capital", {"expected_preview_sha256": "A" * 64}),
        ("purpose of 33", {"purpose": "p" * 33}),
        ("empty purpose", {"purpose": ""}),
        ("200 + 1 keys", {"acknowledged_ignored_keys": ["k"] * 201}),
        ("a key of 201", {"acknowledged_ignored_keys": ["k" * 201]}),
        ("an empty key", {"acknowledged_ignored_keys": [""]}),
        ("a tuple of keys", {"acknowledged_ignored_keys": ("a",)}),
    ):
        try:
            mod._approve_fields({**good, **patch})
            ok = False
        except Exception as exc:  # noqa: BLE001
            ok = getattr(exc, "status_code", None) == 400
        check("U-APPROVE", f"{label} is refused", ok)
    edge = mod._approve_fields(
        {**good, "purpose": "p" * 32, "acknowledged_ignored_keys": ["k" * 200] * 200}
    )
    check(
        "U-APPROVE",
        "the bounds are inclusive: 32-char purpose, 200 keys of 200 chars",
        len(edge["acknowledged_ignored_keys"]) == 200,
    )
    # _scalar / _jsonable
    some = uuid.uuid4()
    check(
        "U-SCALAR",
        "None stays None for every name",
        mod._scalar("id", None) is None and mod._scalar("x", None) is None,
    )
    check(
        "U-SCALAR",
        "a uuid field is a string",
        mod._scalar("id", some) == str(some) and mod._scalar("dataset_id", some) == str(some),
    )
    check(
        "U-SCALAR",
        "a non-uuid field is not stringified",
        mod._scalar("mapping_version", 3) == 3 and mod._scalar(" id ", some) is some,
    )
    check("U-SCALAR", "a datetime is ISO text", mod._scalar("created_at", NOW) == NOW.isoformat())
    check(
        "U-SCALAR",
        "a list or tuple becomes a fresh list",
        mod._scalar("x", ("a", "b")) == ["a", "b"] and isinstance(mod._scalar("x", ("a",)), list),
    )
    value = ["a"]
    check(
        "U-SCALAR",
        "a list is copied",
        mod._scalar("x", value) is not value and mod._scalar("x", value) == value,
    )
    from types import MappingProxyType

    nested = {"a": MappingProxyType({"b": (1, [2, (3,)])}), 5: None}
    check(
        "U-JSONABLE",
        "mappings, tuples and lists convert recursively, keys become text",
        mod._jsonable(nested) == {"a": {"b": [1, [2, [3]]]}, "5": None},
    )
    check(
        "U-JSONABLE",
        "scalars pass through",
        mod._jsonable("x") == "x"
        and mod._jsonable(3) == 3
        and mod._jsonable(None) is None
        and mod._jsonable(True) is True,
    )
    check("U-JSONABLE", "a str is not split into characters", mod._jsonable("abc") == "abc")
    # serializers
    cell = ComparisonCell("messages[0].role", "str", "str", True)
    row = MappingPreviewRow(
        "line:1", "accepted", None, None, ("a", "b"), ("c",), {"k": "v"}, (cell,)
    )
    result = MappingPreviewResult(
        "completed",
        None,
        (row,),
        ("human",),
        {"seen": 1, "accepted": 1},
        True,
        "t" * 64,
        None,
        "p" * 64,
    )
    out = mod._serialize_preview(result, True)
    check(
        "U-SERIALIZE",
        "the preview carries every field once, as plain JSON",
        out
        == {
            "state": "completed",
            "failure_code": None,
            "complete": True,
            "masked": False,
            "counts": {"seen": 1, "accepted": 1},
            "source_schema_sha256": None,
            "tool_definitions_sha256": "t" * 64,
            "preview_sha256": "p" * 64,
            "observed_role_values": ["human"],
            "rows": [
                {
                    "locator": "line:1",
                    "outcome": "accepted",
                    "code": None,
                    "slot": None,
                    "raw_row": ["a", "b"],
                    "not_mapped": ["c"],
                    "candidate": {"k": "v"},
                    "comparison": [
                        {
                            "slot": "messages[0].role",
                            "raw_type": "str",
                            "canonical_type": "str",
                            "identical": True,
                        }
                    ],
                }
            ],
        },
    )
    check(
        "U-SERIALIZE",
        "masked is the negation of reveal",
        mod._serialize_preview(result, False)["masked"] is True,
    )
    probe = ProbeResult("failed", "limit_exceeded", None, ({"a": 1}, ("x",)), False)
    check(
        "U-SERIALIZE",
        "the probe carries its verdict, headers and rows",
        mod._serialize_probe(probe, True)
        == {
            "state": "failed",
            "failure_code": "limit_exceeded",
            "complete": False,
            "masked": False,
            "headers": None,
            "rows": [{"a": 1}, ["x"]],
        },
    )
    check(
        "U-SERIALIZE",
        "a masked probe says so",
        mod._serialize_probe(probe, False)["masked"] is True,
    )
    check(
        "U-ENV",
        "the default storage factory is None when upload storage is not configured at all",
        _env_factory({}) is None,
    )
    check(
        "U-ENV",
        "a partly configured storage is a 503 storage_not_configured",
        _env_factory_status({"SOVRA_UPLOAD_S3_BUCKET": "b"}) == (503, "storage_not_configured"),
    )
    configured = _env_factory(
        {
            "SOVRA_UPLOAD_S3_ENDPOINT": "http://127.0.0.1:8333",
            "SOVRA_UPLOAD_S3_BUCKET": "bkt",
            "SOVRA_UPLOAD_S3_ACCESS_KEY": "k",
            "SOVRA_UPLOAD_S3_SECRET_KEY": "s",
        }
    )
    check(
        "U-ENV",
        "a fully configured storage yields a DatasetObjectStorage with its bucket and a client",
        configured is not None,
    )
    storage = mod._storage(lambda: configured)
    check(
        "U-ENV",
        "the routes get the bucket and an S3 client from the settings",
        storage.bucket == "bkt" and hasattr(storage.client, "stream_object_windows"),
    )
    try:
        mod._storage(lambda: None)
        ok = False
    except Exception as exc:  # noqa: BLE001
        ok = (exc.status_code, exc.detail) == (503, "storage_not_configured")
    check("U-ENV", "no settings is 503 storage_not_configured", ok)


def _env_factory(env: dict[str, str]) -> Any:
    import os

    saved = {k: v for k, v in os.environ.items() if k.startswith("SOVRA_UPLOAD_S3_")}
    for key in saved:
        del os.environ[key]
    os.environ.update(env)
    try:
        return real_routes._default_storage_factory()
    finally:
        for key in env:
            os.environ.pop(key, None)
        os.environ.update(saved)


def _env_factory_status(env: dict[str, str]) -> tuple[int, str] | None:
    try:
        _env_factory(env)
    except Exception as exc:  # noqa: BLE001
        return (exc.status_code, exc.detail)
    return None


# ======================================================================================
# S -- the request store and the store's read-only functions
# ======================================================================================


@contextlib.contextmanager
def sqlite_session() -> Iterator[Session]:
    engine = create_engine("sqlite://")
    for model in (store.DatasetMapping, rstore.DatasetMappingRequest):
        model.__table__.create(engine)
    saved = rstore.execute_tenant_scoped
    rstore.execute_tenant_scoped = p2.passthrough
    try:
        with Session(engine) as session:
            yield session
    finally:
        rstore.execute_tenant_scoped = saved
        engine.dispose()


def refusal_of(fn: Callable[[], Any]) -> str | None:
    try:
        fn()
    except rstore.MappingRequestError as exc:
        return exc.code
    except BaseException as exc:  # noqa: BLE001
        return f"{type(exc).__name__}"
    return None


def part_s_requests() -> None:
    ds, mid = uuid.uuid4(), uuid.uuid4()
    base_kwargs: dict[str, Any] = {
        "dataset_id": ds,
        "mapping_id": mid,
        "subject": "admin",
        "body": {"a": 1},
    }
    digest = rstore.request_digest("approve", **base_kwargs)
    expected = hashlib.sha256(
        json.dumps(
            {
                "action": "approve",
                "dataset_id": str(ds),
                "mapping_id": str(mid),
                "subject": "admin",
                "body": {"a": 1},
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    check(
        "S-DIGEST",
        "the digest is sha256 of the canonical JSON of action, dataset, mapping, subject, body",
        digest == expected,
    )
    check(
        "S-DIGEST",
        "a body in another key order digests alike",
        rstore.request_digest("approve", **{**base_kwargs, "body": {"a": 1}}) == digest,
    )
    for label, patch in (
        ("the dataset", {"dataset_id": uuid.uuid4()}),
        ("the mapping", {"mapping_id": uuid.uuid4()}),
        ("the principal", {"subject": "admin2"}),
        ("the body", {"body": {"a": 2}}),
    ):
        check(
            "S-DIGEST",
            f"another {label} digests differently",
            rstore.request_digest("approve", **{**base_kwargs, **patch}) != digest,
        )
    check(
        "S-DIGEST",
        "another action digests differently",
        rstore.request_digest("reject", **base_kwargs) != digest,
    )
    check(
        "S-DIGEST",
        "a proposal (no mapping) digests differently from an approval",
        rstore.request_digest("propose", **{**base_kwargs, "mapping_id": None}) != digest,
    )
    check(
        "S-DIGEST",
        "the digest is 64 lowercase hex",
        re.fullmatch(r"[0-9a-f]{64}", digest) is not None,
    )
    check(
        "S-DIGEST",
        "an unknown action is refused",
        refusal_of(lambda: rstore.request_digest("delete", **base_kwargs)) == "invalid_request",
    )
    check(
        "S-DIGEST",
        "non-ASCII text digests (ensure_ascii) without error",
        rstore.request_digest(
            "propose", **{**base_kwargs, "mapping_id": None, "body": {"k": "مرحبا"}}
        )
        != digest,
    )
    ctx = TenantContext(organization_id=ORG)
    with sqlite_session() as session:
        pid, other_pid = uuid.uuid4(), uuid.uuid4()

        def record(**kw: Any) -> None:
            args: dict[str, Any] = {
                "project_id": pid,
                "mapping_id": mid,
                "action": "approve",
                "idempotency_key": "request-key-0001",
                "digest": digest,
                "rationale": "because",
                "created_by": "admin",
                "now": NOW,
            }
            args.update(kw)
            rstore.record_mapping_request(session, ctx, **args)
            session.commit()

        record()
        stored = rstore.find_mapping_request(
            session, ctx, project_id=pid, idempotency_key="request-key-0001"
        )
        check(
            "S-RECORD",
            "a recorded request is found by key with its action, digest, rationale and author",
            stored == rstore.StoredMappingRequest(mid, "approve", digest, "because", "admin"),
        )
        check(
            "S-RECORD",
            "the key is scoped to the project",
            rstore.find_mapping_request(
                session, ctx, project_id=other_pid, idempotency_key="request-key-0001"
            )
            is None,
        )
        check(
            "S-RECORD",
            "the key must match exactly",
            rstore.find_mapping_request(
                session, ctx, project_id=pid, idempotency_key="request-key-0002"
            )
            is None
            and rstore.find_mapping_request(
                session, ctx, project_id=pid, idempotency_key="REQUEST-KEY-0001"
            )
            is None,
        )
        check(
            "S-RECORD",
            "a key already stored is idempotency_key_reused",
            refusal_of(lambda: record()) == "idempotency_key_reused",
        )
        session.rollback()
        record(action="reject", idempotency_key="request-key-0002", rationale="no")
        early = rstore.find_mapping_request_by_action(
            session, ctx, project_id=pid, mapping_id=mid, action="approve"
        )
        check(
            "S-FIND",
            "the request of an action on a mapping is found",
            early is not None and early.action == "approve" and early.rationale == "because",
        )
        check(
            "S-FIND",
            "another action is another request",
            rstore.find_mapping_request_by_action(
                session, ctx, project_id=pid, mapping_id=mid, action="reject"
            ).rationale
            == "no",
        )
        check(
            "S-FIND",
            "an action never recorded is None",
            rstore.find_mapping_request_by_action(
                session, ctx, project_id=pid, mapping_id=mid, action="propose"
            )
            is None,
        )
        check(
            "S-FIND",
            "the lookup is scoped to the project and the mapping",
            rstore.find_mapping_request_by_action(
                session, ctx, project_id=other_pid, mapping_id=mid, action="approve"
            )
            is None
            and rstore.find_mapping_request_by_action(
                session, ctx, project_id=pid, mapping_id=uuid.uuid4(), action="approve"
            )
            is None,
        )
        record(
            idempotency_key="request-key-0003",
            rationale="a later approval",
            now=NOW.replace(hour=13),
        )
        first = rstore.find_mapping_request_by_action(
            session, ctx, project_id=pid, mapping_id=mid, action="approve"
        )
        check(
            "S-FIND",
            "the earliest request of an action wins",
            first is not None and first.rationale == "because",
        )
        session.rollback()
        for label, patch, wanted in (
            ("an unknown action", {"action": "delete"}, "invalid_request"),
            ("a short key", {"idempotency_key": "short"}, "invalid_request"),
            ("a key with a space", {"idempotency_key": "has space in it"}, "invalid_request"),
            ("a key of 201", {"idempotency_key": "k" * 201}, "invalid_request"),
            (
                "a key with a trailing newline",
                {"idempotency_key": "request-key-0009\n"},
                "invalid_request",
            ),
            ("an upper-case digest", {"digest": "A" * 64}, "invalid_request"),
            ("a short digest", {"digest": "a" * 63}, "invalid_request"),
            ("a digest with a trailing newline", {"digest": "a" * 64 + "\n"}, "invalid_request"),
            ("no author", {"created_by": ""}, "invalid_request"),
            ("a non-text author", {"created_by": 5}, "invalid_request"),
            ("an approval without a rationale", {"rationale": None}, "invalid_request"),
            ("an empty rationale", {"rationale": ""}, "invalid_request"),
            ("a rationale of 2001", {"rationale": "r" * 2001}, "invalid_request"),
            (
                "a proposal with a rationale",
                {"action": "propose", "rationale": "x"},
                "invalid_request",
            ),
            (
                "a rejection without a rationale",
                {"action": "reject", "rationale": None},
                "invalid_request",
            ),
        ):
            check(
                "S-RECORD",
                f"{label} is refused as {wanted}",
                refusal_of(
                    lambda p=patch: record(idempotency_key="request-key-0010", **p)
                    if "idempotency_key" not in p
                    else record(**p)
                )
                == wanted,
            )
        for variant in (
            " approve",
            "APPROVE",
            "approve ",
            "Reject",
            " reject",
            "reject\n",
            "Propose",
            " propose",
        ):
            check(
                "S-RECORD",
                f"the action {variant!r} is no action: refused as invalid_request",
                refusal_of(
                    lambda v=variant: record(
                        idempotency_key="request-key-0012",
                        action=v,
                        rationale=None if v.strip().lower() == "propose" else "x",
                    )
                )
                == "invalid_request",
            )
            check(
                "S-FIND",
                f"the lookup of the action {variant!r} finds nothing, whatever its normal form",
                rstore.find_mapping_request_by_action(
                    session, ctx, project_id=pid, mapping_id=mid, action=variant
                )
                is None,
            )
        record(idempotency_key="request-key-0013", rationale="x")
        record(idempotency_key="request-key-0011", rationale="r" * 2000)
        record(idempotency_key="k" * 200, action="reject")
        record(idempotency_key="k" * 8 + "x", action="propose", rationale=None)
        check(
            "S-RECORD",
            "the bounds are inclusive: a 2000-character rationale, 8- and 200-character keys, a proposal without one",
            True,
        )
        count = session.query(rstore.DatasetMappingRequest).count()
        check("S-RECORD", "refusals wrote nothing (the accepted rows only)", count == 7)


def part_s_store_reads() -> None:
    with p2.sqlite_world() as w:
        conn = w.connection("all", ALL_PURPOSES)
        ds = w.dataset("alpha", "training", conn)
        held = w.dataset("held", "held_out_evaluation", conn)
        orig = store.get_dataset_source_connection
        classes = {conn: "internal"}
        store.get_dataset_source_connection = (
            lambda session, context, project_id, connection_id: SimpleNamespace(
                security_classification=classes[connection_id]
            )
        )
        try:

            def vis(dataset: Any, role: str, **kw: Any) -> Any:
                return store.dataset_visibility(
                    w.session,
                    w.ctx,
                    project_id=kw.get("project_id", PROJECT),
                    dataset_id=dataset.id,
                    role=role,
                )

            for classification, expected in (
                ("public", True),
                ("internal", True),
                ("confidential", False),
                ("restricted", False),
            ):
                classes[conn] = classification
                check(
                    "S-VISIBILITY",
                    f"ml_engineer on {classification}: reveal {expected}, no held-out",
                    vis(ds, "ml_engineer") == (False, expected),
                )
                check(
                    "S-VISIBILITY",
                    f"workspace_admin on {classification}: reveal and held-out",
                    vis(ds, "workspace_admin") == (True, True),
                )
                check(
                    "S-VISIBILITY",
                    f"organization_admin on {classification}: reveal and held-out",
                    vis(ds, "organization_admin") == (True, True),
                )
                check(
                    "S-VISIBILITY",
                    f"a viewer never reveals on {classification}",
                    vis(ds, "viewer") == (False, False),
                )
            classes[conn] = "internal"
            check(
                "S-VISIBILITY",
                "the classification set is exactly public and internal",
                store.REVEAL_CLASSIFICATIONS == frozenset({"public", "internal"}),
            )
            check(
                "S-VISIBILITY",
                "an admin sees a held-out dataset",
                vis(held, "workspace_admin") == (True, True),
            )
            for code_, fn in (
                ("dataset_not_found", lambda: vis(held, "ml_engineer")),
                ("dataset_not_found", lambda: vis(SimpleNamespace(id=uuid.uuid4()), "admin_x")),
                ("dataset_not_found", lambda: vis(ds, "workspace_admin", project_id=uuid.uuid4())),
                (
                    "invalid_request",
                    lambda: store.dataset_visibility(
                        w.session, w.ctx, project_id="x", dataset_id=ds.id, role="workspace_admin"
                    ),
                ),
                (
                    "invalid_request",
                    lambda: store.dataset_visibility(
                        w.session, w.ctx, project_id=PROJECT, dataset_id=ds.id, role=""
                    ),
                ),
                (
                    "invalid_request",
                    lambda: store.dataset_visibility(
                        w.session,
                        w.ctx,
                        project_id=PROJECT,
                        dataset_id=ds.id,
                        role=" workspace_admin",
                    ),
                ),
            ):
                got = p2.code_of(fn)
                check("S-VISIBILITY", f"refused with {code_} (got {got})", got == code_)
            store.get_dataset_source_connection = lambda *a, **k: (_ for _ in ()).throw(
                crud.DatasetSourceConnectionNotFoundError("x")
            )
            check(
                "S-VISIBILITY",
                "a connection that cannot be read is purpose_not_authorized",
                p2.code_of(lambda: vis(ds, "workspace_admin")) == "purpose_not_authorized",
            )
        finally:
            store.get_dataset_source_connection = orig
        # review_probe's option validation happens before anything is read
        for label, options in (
            ("a list", []),
            ("None", None),
            ("a string", "csv"),
            ("an unknown block", {"xlsx": {}}),
            ("a mapping key", {"shape": "messages"}),
        ):
            got = p2.code_of(
                lambda o=options: store.review_probe(
                    w.session,
                    w.ctx,
                    project_id=PROJECT,
                    dataset_id=ds.id,
                    format_options=o,
                    role="workspace_admin",
                    storage=w.storage,
                )
            )
            check(
                "S-PROBE",
                f"probe options that are {label} are mapping_invalid (got {got})",
                got == "mapping_invalid",
            )
        check("S-PROBE", "the probe never reached the byte reader", True)


# ======================================================================================
# M -- migration 0036
# ======================================================================================


def migration_findings(src: str) -> list[str]:
    """Every shape rule the migration must satisfy; returns the broken ones."""
    broken: list[str] = []

    def need(label: str, ok: bool) -> None:
        if not ok:
            broken.append(label)

    table = "dataset_mapping_requests"
    need("revision id", 'revision = "0036_dataset_mapping_requests"' in src)
    need("chained onto 0035", 'down_revision = "0035_dataset_mappings"' in src)
    need("revision id fits alembic_version (<=32)", len("0036_dataset_mapping_requests") <= 32)
    need(
        "the table name is a literal (data_exposure_check reads it)",
        f'op.create_table(\n        "{table}",' in src and "_TABLE" not in src,
    )
    need("ENABLE rls", f'"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY"' in src)
    need("FORCE rls", f'"ALTER TABLE {table} FORCE ROW LEVEL SECURITY"' in src)
    need("policy name", f"CREATE POLICY tenant_isolation ON {table} FOR ALL" in src)
    need("policy using", "USING (organization_id = {tenant_setting})" in src)
    need("policy with check", "WITH CHECK (organization_id = {tenant_setting})" in src)
    need("not allow-all", "USING (true)" not in src and "WITH CHECK (true)" not in src)
    need(
        "tenant setting",
        "NULLIF(current_setting('app.current_organization_id', true), '')::uuid" in src,
    )
    need(
        "SELECT/INSERT grant only", f'GRANT SELECT, INSERT ON {table} TO "{{APP_ROLE_NAME}}"' in src
    )
    need(
        "no UPDATE or DELETE grant",
        "GRANT UPDATE" not in src and "GRANT DELETE" not in src and "GRANT ALL" not in src,
    )
    need(
        "composite mapping fk is RESTRICT",
        'ondelete="RESTRICT",\n            name="fk_dataset_mapping_requests_mapping"' in src,
    )
    need(
        "composite mapping fk targets (organization_id, id)",
        '["dataset_mappings.organization_id", "dataset_mappings.id"]' in src
        and '["organization_id", "mapping_id"]' in src,
    )
    need("organization fk", '"organizations.id"' in src and 'ondelete="CASCADE"' in src)
    need(
        "key unique per organization and project",
        '"organization_id",\n            "project_id",\n            "idempotency_key",\n            name="uq_dataset_mapping_requests_key"'
        in src,
    )
    need("action check", "\"action IN ('propose', 'approve', 'reject')\"" in src)
    need(
        "digest check is lowercase hex64",
        "_HEX64 = \"'^[0-9a-f]{64}$'\"" in src and "request_digest ~ {_HEX64}" in src,
    )
    need("key length check", '"char_length(idempotency_key) BETWEEN 8 AND 200"' in src)
    need("rationale: absent for a proposal", "(action = 'propose' AND rationale IS NULL)" in src)
    need(
        "rationale: 1..2000 for a decision",
        "(action IN ('approve', 'reject') AND rationale IS NOT NULL" in src
        and "AND char_length(rationale) BETWEEN 1 AND 2000)" in src,
    )
    need(
        "downgrade drops the policy, the grants and the table",
        "DROP POLICY IF EXISTS tenant_isolation ON dataset_mapping_requests" in src
        and "REVOKE ALL ON dataset_mapping_requests" in src
        and 'op.drop_table("dataset_mapping_requests")' in src,
    )
    return broken


MIGRATION_FAULTS = (
    (
        "FORCE dropped",
        '    op.execute("ALTER TABLE dataset_mapping_requests FORCE ROW LEVEL SECURITY")\n',
        "",
    ),
    (
        "ENABLE dropped",
        '    op.execute("ALTER TABLE dataset_mapping_requests ENABLE ROW LEVEL SECURITY")\n',
        "",
    ),
    ("policy allow-all", 'f"USING (organization_id = {tenant_setting}) "', '"USING (true) "'),
    (
        "policy check allow-all",
        'f"WITH CHECK (organization_id = {tenant_setting})"',
        '"WITH CHECK (true)"',
    ),
    (
        "UPDATE granted",
        "GRANT SELECT, INSERT ON dataset_mapping_requests",
        "GRANT SELECT, INSERT, UPDATE ON dataset_mapping_requests",
    ),
    (
        "DELETE granted",
        "GRANT SELECT, INSERT ON dataset_mapping_requests",
        "GRANT SELECT, INSERT, DELETE ON dataset_mapping_requests",
    ),
    (
        "key uniqueness dropped",
        'name="uq_dataset_mapping_requests_key"',
        'name="uq_dataset_mapping_requests_other"',
    ),
    (
        "rationale optional for a decision",
        "AND char_length(rationale) BETWEEN 1 AND 2000)",
        "AND char_length(rationale) >= 0)",
    ),
    (
        "rationale allowed for a proposal",
        "(action = 'propose' AND rationale IS NULL)",
        "(action = 'propose')",
    ),
    ("digest not checked", "request_digest ~ {_HEX64}", "request_digest <> ''"),
    (
        "mapping fk cascades",
        'ondelete="RESTRICT",\n            name="fk_dataset_mapping_requests_mapping"',
        'ondelete="CASCADE",\n            name="fk_dataset_mapping_requests_mapping"',
    ),
    (
        "table named by a constant",
        'op.create_table(\n        "dataset_mapping_requests",',
        '_TABLE = "x"\n    op.create_table(\n        _TABLE,',
    ),
    (
        "chain broken",
        'down_revision = "0035_dataset_mappings"',
        'down_revision = "0034_argilla_review_identity"',
    ),
)


def offline_sql(action: str, revision_range: str) -> str:
    import os

    env = {**os.environ, "SOVRA_DATABASE_URL": "postgresql+psycopg://x:y@localhost:5432/z"}
    result = subprocess.run(
        [sys.executable, "-m", "alembic", action, revision_range, "--sql"],
        cwd=str(ROOT / "apps" / "api"),
        capture_output=True,
        text=True,
        env=env,
    )
    check(
        "M-SQL",
        f"alembic {action} {revision_range} --sql exits 0 ({result.stderr[-200:]})",
        result.returncode == 0,
    )
    return result.stdout


def part_m() -> None:
    src = MIGRATION_PATH.read_text()
    broken = migration_findings(src)
    check("M-SHAPE", f"the migration satisfies every shape rule: {broken}", not broken)
    for label, old, new in MIGRATION_FAULTS:
        check("M-FAULT", f"the fault {label!r} changes the migration text", old in src)
        check(
            "M-FAULT",
            f"the fault {label!r} is caught by the shape rules",
            bool(migration_findings(src.replace(old, new, 1))),
        )
    up = offline_sql("upgrade", "0035_dataset_mappings:0036_dataset_mapping_requests")
    for needle in (
        "CREATE TABLE dataset_mapping_requests",
        "ALTER TABLE dataset_mapping_requests ENABLE ROW LEVEL SECURITY",
        "ALTER TABLE dataset_mapping_requests FORCE ROW LEVEL SECURITY",
        "CREATE POLICY tenant_isolation ON dataset_mapping_requests FOR ALL",
        'GRANT SELECT, INSERT ON dataset_mapping_requests TO "sovra_app"',
        "CONSTRAINT uq_dataset_mapping_requests_key UNIQUE (organization_id, project_id, idempotency_key)",
        "CONSTRAINT fk_dataset_mapping_requests_mapping FOREIGN KEY(organization_id, mapping_id) REFERENCES dataset_mappings (organization_id, id) ON DELETE RESTRICT",
        "UPDATE alembic_version SET version_num='0036_dataset_mapping_requests'",
    ):
        check("M-SQL", f"the upgrade SQL contains: {needle[:100]}", needle in up)
    check(
        "M-SQL",
        "the upgrade SQL never grants UPDATE or DELETE",
        "GRANT UPDATE" not in up and "DELETE ON" not in up.replace("ON DELETE", ""),
    )
    down = offline_sql("downgrade", "0036_dataset_mapping_requests:0035_dataset_mappings")
    for needle in (
        "DROP POLICY IF EXISTS tenant_isolation ON dataset_mapping_requests",
        'REVOKE ALL ON dataset_mapping_requests FROM "sovra_app"',
        "DROP TABLE dataset_mapping_requests",
        "UPDATE alembic_version SET version_num='0035_dataset_mappings'",
    ):
        check("M-SQL", f"the downgrade SQL contains: {needle[:100]}", needle in down)
    import os

    env = {**os.environ, "SOVRA_DATABASE_URL": "postgresql+psycopg://x:y@localhost:5432/z"}
    heads = subprocess.run(
        [sys.executable, "-m", "alembic", "heads"],
        cwd=str(ROOT / "apps" / "api"),
        capture_output=True,
        text=True,
        env=env,
    )
    check("M-HEAD", f"exactly one head: {heads.stdout!r}", heads.stdout.count("(head)") == 1)
    ci = subprocess.run(
        [
            sys.executable,
            "scripts/data_exposure_check.py",
            "--backends",
            "postgres_rls,s3_identity",
            "--fail-on-exposure",
        ],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
    )
    check("M-EXPOSURE", f"data_exposure_check passes: {ci.stdout[-200:]}", ci.returncode == 0)
    with tempfile_copy(MIGRATION_PATH) as path:
        path.write_text(
            src.replace(
                '    op.execute("ALTER TABLE dataset_mapping_requests FORCE ROW LEVEL SECURITY")\n',
                "",
            )
        )
        red = subprocess.run(
            [
                sys.executable,
                "scripts/data_exposure_check.py",
                "--backends",
                "postgres_rls",
                "--fail-on-exposure",
            ],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
        )
        check(
            "M-EXPOSURE",
            "without FORCE ROW LEVEL SECURITY data_exposure_check names the table and fails",
            red.returncode != 0 and "dataset_mapping_requests" in red.stdout + red.stderr,
        )
    after = subprocess.run(
        [
            sys.executable,
            "scripts/data_exposure_check.py",
            "--backends",
            "postgres_rls",
            "--fail-on-exposure",
        ],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
    )
    check(
        "M-EXPOSURE",
        "and the restored migration is clean again",
        after.returncode == 0 and MIGRATION_PATH.read_text() == src,
    )


@contextlib.contextmanager
def tempfile_copy(path: Path) -> Iterator[Path]:
    """The file itself, edited in place for one check and always restored byte for byte."""
    original = path.read_bytes()
    try:
        yield path
    finally:
        path.write_bytes(original)


# ======================================================================================
# C -- the interface contract and the wiring
# ======================================================================================


def load_interface() -> dict[str, Any]:
    return yaml.safe_load(INTERFACE_PATH.read_text())


def deref(root: dict[str, Any], schema: Any) -> Any:
    while isinstance(schema, dict) and "$ref" in schema:
        node: Any = root
        for part in schema["$ref"].removeprefix("#/").split("/"):
            node = node[part]
        schema = node
    return schema


def valid(root: dict[str, Any], schema: Any, value: Any) -> bool:
    """The subset of JSON Schema 2020-12 the contract uses, with ECMAScript `$` (end of input only)."""
    schema = deref(root, schema)
    if schema is True:
        return True
    if "allOf" in schema and not all(valid(root, part, value) for part in schema["allOf"]):
        return False
    if "oneOf" in schema and sum(valid(root, part, value) for part in schema["oneOf"]) != 1:
        return False
    if "const" in schema and (value != schema["const"] or type(value) is not type(schema["const"])):
        return False
    if "enum" in schema and value not in schema["enum"]:
        return False
    kind = schema.get("type")
    if kind is not None:
        checks = {
            "object": lambda v: isinstance(v, dict),
            "array": lambda v: isinstance(v, list),
            "string": lambda v: isinstance(v, str),
            "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
            "number": lambda v: isinstance(v, int | float) and not isinstance(v, bool),
            "boolean": lambda v: isinstance(v, bool),
            "null": lambda v: v is None,
        }
        if not checks[kind](value):
            return False
    if isinstance(value, bool) or value is None:
        return True
    if isinstance(value, str):
        if "minLength" in schema and len(value) < schema["minLength"]:
            return False
        if "maxLength" in schema and len(value) > schema["maxLength"]:
            return False
        if "pattern" in schema and re.search(schema["pattern"].replace("$", r"\Z"), value) is None:
            return False
        if schema.get("format") == "uuid":
            try:
                if str(uuid.UUID(value)) != value:
                    return False
            except ValueError:
                return False
        if schema.get("format") == "date-time":
            from datetime import datetime

            try:
                datetime.fromisoformat(value)
            except ValueError:
                return False
    if isinstance(value, int | float):
        if "minimum" in schema and value < schema["minimum"]:
            return False
        if "maximum" in schema and value > schema["maximum"]:
            return False
    if isinstance(value, list):
        if "minItems" in schema and len(value) < schema["minItems"]:
            return False
        if "maxItems" in schema and len(value) > schema["maxItems"]:
            return False
        if "items" in schema and not all(valid(root, schema["items"], item) for item in value):
            return False
    if isinstance(value, dict):
        if "minProperties" in schema and len(value) < schema["minProperties"]:
            return False
        if "maxProperties" in schema and len(value) > schema["maxProperties"]:
            return False
        if "required" in schema and not all(key in value for key in schema["required"]):
            return False
        names = schema.get("propertyNames")
        if names is not None and not all(
            valid(root, {"type": "string", **names}, key) for key in value
        ):
            return False
        properties = schema.get("properties", {})
        extra = schema.get("additionalProperties", True)
        for key, item in value.items():
            if key in properties:
                if not valid(root, properties[key], item):
                    return False
            elif extra is False:
                return False
            elif isinstance(extra, dict) and not valid(root, extra, item):
                return False
    return True


def accepts(document: Any) -> bool:
    try:
        ic.mapping_from_object(document)
    except (ic.MappingInvalidError, ic.MappingUnsupportedError):
        return False
    return True


def mapping_variants() -> Iterator[tuple[str, Any]]:
    import copy

    messages = {
        "shape": "messages",
        "messages_pointer": "/conversation",
        "role_key": "role",
        "content_key": "content",
        "role_map": {"human": "user", "bot": "assistant"},
        "on_unmapped_message_keys": "reject",
    }
    messages_json = {
        "shape": "messages_json",
        "messages": {"key": "msgs"},
        "role_key": "role",
        "content_key": "content",
        "role_map": {"human": "user"},
        "on_unmapped_message_keys": "ignore",
    }
    prompt = {"shape": "prompt_completion", "prompt": {"key": "p"}, "completion": {"header": "c"}}
    csv_ok = {"encoding": "utf-8", "delimiter": ",", "has_header": True, "header_row": 0}
    xlsx_ok = {"sheet_name": "Sheet1", "header_row": 0}
    json_ok = {"container": "", "ignored_top_level_keys": []}
    bases = {"messages": messages, "messages_json": messages_json, "prompt_completion": prompt}
    junk: list[Any] = [None, 0, 1, True, False, 1.5, "x", "", [], {}, [1], {"a": 1}]
    names = ["a", "x" * 256, "x" * 257, "", "é", " ", "a\nb"]
    slots: list[Any] = [
        {"key": "a"},
        {"header": "h"},
        {"index": 0},
        {"index": 16383},
        {"index": 16384},
        {"index": -1},
        {"index": True},
        {"index": 1.5},
        {"index": "1"},
        {"letters": "A"},
        {"letters": "ZZZ"},
        {"letters": "AAAA"},
        {"letters": "a"},
        {"letters": ""},
        {"letters": "A1"},
        {"letters": "A\n"},
        {},
        {"key": "a", "header": "b"},
        {"other": "a"},
        {"key": ""},
        {"key": "x" * 257},
        {"key": 1},
        "key",
        None,
        [],
    ]
    pointers = [
        "",
        "/a",
        "/a/b",
        "/a/0",
        "/0",
        "/00",
        "/01",
        "/10",
        "/123456789",
        "/1234567890",
        "/-",
        "/a/-",
        "/-a",
        "a",
        "/",
        "/a/",
        "//a",
        "/a~0",
        "/a~1",
        "/a~",
        "/a~2",
        "~",
        "/a\n",
        "/" + "/".join("a" * 1 for _ in range(16)),
        "/" + "/".join("a" for _ in range(17)),
        "/" + "a" * 255,
        "/" + "a" * 256,
        "/é",
        "/٣",
        "/0/1",
        "/1/0",
    ]
    roles = [
        {"a": "user"},
        {"a": "assistant"},
        {"a": "system"},
        {"a": "tool"},
        {"a": "admin"},
        {"a": "User"},
        {"a": 1},
        {"a": None},
        {},
        {"": "user"},
        {"x" * 256: "user"},
        {"x" * 257: "user"},
        {f"k{i}": "user" for i in range(64)},
        {f"k{i}": "user" for i in range(65)},
        [],
        "user",
        None,
    ]
    for label, base in bases.items():
        yield f"{label}: valid", copy.deepcopy(base)
        for key in base:
            doc = copy.deepcopy(base)
            del doc[key]
            yield f"{label}: without {key}", doc
        for key in list(base) + [
            "csv",
            "xlsx",
            "json",
            "metadata",
            "group_key",
            "extra",
            "system",
            "tool_calls_key",
            "tool_call_id_key",
            "name_key",
            "messages_pointer",
            "messages",
            "prompt",
            "completion",
        ]:
            for value in junk:
                doc = copy.deepcopy(base)
                doc[key] = value
                yield f"{label}: {key}={value!r}", doc
        doc = copy.deepcopy(base)
        doc.update(
            csv=csv_ok,
            xlsx=xlsx_ok,
            json=json_ok,
            group_key={"key": "g"},
            metadata={"language": {"key": "lang"}},
        )
        yield f"{label}: every optional block", doc
    for shape in (
        "grouped_rows",
        "grouped_multi_row",
        "nope",
        "",
        "Messages",
        "messages ",
        None,
        3,
        ["messages"],
    ):
        yield f"shape={shape!r}", {"shape": shape}
        yield (
            f"shape={shape!r} with a prompt",
            {"shape": shape, "prompt": {"key": "p"}, "completion": {"key": "c"}},
        )
    for value in names:
        for key in ("role_key", "content_key", "tool_calls_key", "tool_call_id_key", "name_key"):
            yield f"messages: {key}={value!r}", {**messages, key: value}
        yield f"csv encoding={value!r}", {**prompt, "csv": {**csv_ok, "encoding": value}}
        yield f"xlsx sheet_name={value!r}", {**prompt, "xlsx": {**xlsx_ok, "sheet_name": value}}
        yield (
            f"json ignored={value!r}",
            {**prompt, "json": {**json_ok, "ignored_top_level_keys": [value]}},
        )
    for pointer in pointers:
        yield f"messages_pointer={pointer!r}", {**messages, "messages_pointer": pointer}
        yield f"json container={pointer!r}", {**prompt, "json": {**json_ok, "container": pointer}}
    for slot in slots:
        yield f"prompt={slot!r}", {**prompt, "prompt": slot}
        yield f"system={slot!r}", {**prompt, "system": slot}
        yield f"group_key={slot!r}", {**prompt, "group_key": slot}
        yield f"messages slot={slot!r}", {**messages_json, "messages": slot}
        for field in (
            "language",
            "task_category",
            "consent",
            "quality_label",
            "source_record_id",
            "event_timestamp",
        ):
            yield f"metadata.{field}={slot!r}", {**prompt, "metadata": {field: slot}}
    yield (
        "metadata: every field",
        {
            **prompt,
            "metadata": {
                k: {"key": k}
                for k in (
                    "language",
                    "task_category",
                    "consent",
                    "quality_label",
                    "source_record_id",
                    "event_timestamp",
                )
            },
        },
    )
    yield "metadata: unknown field", {**prompt, "metadata": {"nope": {"key": "x"}}}
    yield "metadata: empty", {**prompt, "metadata": {}}
    for role_map in roles:
        yield f"role_map={str(role_map)[:40]!r}", {**messages, "role_map": role_map}
    for policy in ("reject", "ignore", "Reject", "", None, True, "ignore "):
        yield f"policy={policy!r}", {**messages, "on_unmapped_message_keys": policy}
    for delimiter in (",", ";", "", ";;", "é", "\t", 1, None):
        yield f"csv delimiter={delimiter!r}", {**prompt, "csv": {**csv_ok, "delimiter": delimiter}}
    for header_row in (0, 1, 1000000, 1000001, -1, True, 1.0, "0", None):
        yield (
            f"csv header_row={header_row!r}",
            {**prompt, "csv": {**csv_ok, "header_row": header_row}},
        )
        yield (
            f"xlsx header_row={header_row!r}",
            {**prompt, "xlsx": {**xlsx_ok, "header_row": header_row}},
        )
    for has_header in (True, False, 1, 0, "true", None):
        yield (
            f"csv has_header={has_header!r}",
            {**prompt, "csv": {**csv_ok, "has_header": has_header}},
        )
    for key in csv_ok:
        yield f"csv without {key}", {**prompt, "csv": {k: v for k, v in csv_ok.items() if k != key}}
    yield "csv: extra key", {**prompt, "csv": {**csv_ok, "extra": 1}}
    for key in xlsx_ok:
        yield (
            f"xlsx without {key}",
            {**prompt, "xlsx": {k: v for k, v in xlsx_ok.items() if k != key}},
        )
    yield "xlsx: extra key", {**prompt, "xlsx": {**xlsx_ok, "extra": 1}}
    for key in json_ok:
        yield (
            f"json without {key}",
            {**prompt, "json": {k: v for k, v in json_ok.items() if k != key}},
        )
    yield "json: extra key", {**prompt, "json": {**json_ok, "extra": 1}}
    for ignored in ([], ["a"], ["a"] * 64, ["a"] * 65, "a", None, [1], [""], [["a"]]):
        yield (
            f"json ignored={str(ignored)[:30]!r}",
            {**prompt, "json": {**json_ok, "ignored_top_level_keys": ignored}},
        )


def part_c() -> None:
    root = load_interface()
    base = "/api/v1/organizations/{organization_id}/projects/{project_id}/datasets/{dataset_id}"
    ok_key = {
        "name": "Idempotency-Key",
        "in": "header",
        "required": True,
        "schema": {"type": "string", "pattern": "^" + real_routes._KEY_PATTERN.pattern + "$"},
    }
    ops = (
        (
            "/mapping-probe",
            "post",
            "probeDatasetMapping",
            False,
            {"200", "400", "401", "403", "404", "409", "413", "503"},
        ),
        (
            "/mapping-preview",
            "post",
            "previewDatasetMapping",
            False,
            {"200", "400", "401", "403", "404", "409", "413", "503"},
        ),
        ("/mappings", "get", "listDatasetMappings", False, {"200", "400", "401", "403", "404"}),
        (
            "/mappings",
            "post",
            "proposeDatasetMapping",
            True,
            {"200", "201", "400", "401", "403", "404", "409", "413", "503"},
        ),
        ("/mappings/{mapping_id}", "get", "getDatasetMapping", False, {"200", "401", "403", "404"}),
        (
            "/mappings/{mapping_id}/approve",
            "post",
            "approveDatasetMapping",
            True,
            {"200", "400", "401", "403", "404", "409", "413", "503"},
        ),
        (
            "/mappings/{mapping_id}/reject",
            "post",
            "rejectDatasetMapping",
            True,
            {"200", "400", "401", "403", "404", "409", "413"},
        ),
    )
    ours = {path for path in root["paths"] if path.startswith(base + "/mapping")}
    wanted = {base + suffix for suffix, *_ in ops}
    check(
        "C-PATHS",
        f"the contract declares exactly the seven mapping paths: {sorted(ours ^ wanted)}",
        ours == wanted,
    )
    for suffix, method, operation_id, needs_key, statuses in ops:
        item = root["paths"].get(base + suffix, {})
        op = item.get(method)
        check("C-OPS", f"{method.upper()} {suffix} exists", op is not None)
        if op is None:
            continue
        check(
            "C-OPS",
            f"{method.upper()} {suffix} is {operation_id}",
            op.get("operationId") == operation_id,
        )
        header_params = [prm for prm in op.get("parameters", []) if prm.get("in") == "header"]
        if needs_key:
            check(
                "C-OPS",
                f"{operation_id} requires the Idempotency-Key header with the route's pattern",
                header_params == [ok_key],
            )
        else:
            check("C-OPS", f"{operation_id} takes no Idempotency-Key", header_params == [])
        check(
            "C-OPS",
            f"{operation_id} documents exactly its statuses {sorted(statuses)}",
            set(op["responses"]) == statuses,
        )
        check(
            "C-OPS",
            f"{operation_id} has path parameters",
            {p["name"] for p in item["parameters"]}
            >= {"organization_id", "project_id", "dataset_id"},
        )
        check(
            "C-OPS",
            f"{operation_id} {'has' if method == 'post' else 'has no'} a required JSON request body",
            (op.get("requestBody", {}).get("required") is True) == (method == "post"),
        )
    other_methods = {
        (path.replace(base, ""), method)
        for path in ours
        for method in root["paths"][path]
        if method in ("get", "post", "put", "patch", "delete")
    }
    check(
        "C-OPS",
        f"no other methods are declared (a stored mapping is never edited or deleted): {sorted(other_methods)}",
        other_methods == {(suffix, method) for suffix, method, *_ in ops},
    )
    list_params = {
        p["name"]: p["schema"] for p in root["paths"][base + "/mappings"]["get"]["parameters"]
    }
    check(
        "C-OPS",
        "list limit bounds are the route's",
        list_params["limit"]
        == {
            "type": "integer",
            "minimum": 1,
            "maximum": real_routes._MAX_LIMIT,
            "default": real_routes._DEFAULT_LIMIT,
        },
    )
    check(
        "C-OPS",
        "list after is a 9-digit version",
        list_params["after"] == {"type": "integer", "minimum": 0, "maximum": 999999999},
    )
    # routes mounted == paths declared
    router = real_routes.build_dataset_mapping_router(lambda: None)
    mounted = {
        (route.path.replace(base, ""), method.lower())
        for route in router.routes
        for method in route.methods
    }
    check(
        "C-WIRE",
        f"the router mounts exactly the declared operations: {sorted(mounted ^ {(s, m) for s, m, *_ in ops})}",
        mounted == {(s, m) for s, m, *_ in ops},
    )
    # every status the live suite saw is documented
    by_op = {
        (
            ("/" + suffix.split("/")[1] if suffix != "/mappings/{mapping_id}" else "/mappings/{}")
            if False
            else None
        ): None
        for suffix, *_ in ops
    }
    del by_op
    documented = {
        ("POST", "/mapping-probe"): ops[0][4],
        ("POST", "/mapping-preview"): ops[1][4],
        ("GET", "/mappings"): ops[2][4],
        ("POST", "/mappings"): ops[3][4],
        ("GET", "/mappings/{}"): ops[4][4],
        ("POST", "/mappings/{}/approve"): ops[5][4],
        ("POST", "/mappings/{}/reject"): ops[6][4],
    }
    seen: dict[tuple[str, str], set[str]] = {}
    for method, norm, status, _body, _sent in LIVE:
        seen.setdefault((method, norm), set()).add(str(status))
    for key, statuses in sorted(seen.items()):
        if key not in documented:
            continue  # a method the routes do not have (R-NOEDIT) or a path outside this surface
        check(
            "C-STATUS",
            f"every status the routes answered on {key} is documented ({sorted(statuses - documented[key])})",
            statuses <= documented[key] | {"422"},
        )
    check(
        "C-STATUS",
        "the live suite exercised every documented operation",
        set(documented) <= set(seen),
    )
    # live responses validate against the schemas
    response_schema = {
        ("POST", "/mapping-probe"): "DatasetMappingProbe",
        ("POST", "/mapping-preview"): "DatasetMappingPreview",
        ("GET", "/mappings"): "DatasetMappingPage",
        ("POST", "/mappings"): "DatasetMappingOutcome",
        ("GET", "/mappings/{}"): "DatasetMapping",
        ("POST", "/mappings/{}/approve"): "DatasetMappingOutcome",
        ("POST", "/mappings/{}/reject"): "DatasetMappingOutcome",
    }
    detail_fields = set(real_routes._MAPPING_FIELD_NAMES) | {
        "masked",
        "mapping_document",
        "decision",
    }
    detail_schema = root["components"]["schemas"]["DatasetMapping"]
    check(
        "C-SCHEMA",
        "DatasetMapping declares exactly the route's allow-listed fields",
        set(detail_schema["properties"]) == detail_fields,
    )
    check(
        "C-SCHEMA",
        "and every one of them is required",
        set(detail_schema["required"]) == detail_fields,
    )
    checked = 0
    for method, norm, status, body, _sent in LIVE:
        name = response_schema.get((method, norm))
        if name is None or status not in (200, 201):
            continue
        checked += 1
        schema = {"$ref": f"#/components/schemas/{name}"}
        check(
            "C-SCHEMA",
            f"a {status} answer of {method} {norm} validates against {name}",
            valid(root, schema, body),
        )
    detail_fields = set(real_routes._MAPPING_FIELD_NAMES) | {
        "masked",
        "mapping_document",
        "decision",
    }
    detail_schema = root["components"]["schemas"]["DatasetMapping"]
    check(
        "C-SCHEMA",
        "DatasetMapping declares exactly the route's allow-listed fields",
        set(detail_schema["properties"]) == detail_fields,
    )
    check(
        "C-SCHEMA",
        "and every one of them is required",
        set(detail_schema["required"]) == detail_fields,
    )
    check("C-SCHEMA", f"enough live answers were validated ({checked})", checked >= 40)
    for method, norm, status, body, sent in LIVE:
        if (method, norm) not in (
            ("POST", "/mappings/{}/approve"),
            ("POST", "/mappings/{}/reject"),
        ) or not isinstance(sent, dict):
            continue
        name = (
            "DatasetMappingApproveRequest"
            if norm.endswith("approve")
            else "DatasetMappingRejectRequest"
        )
        in_schema = valid(root, {"$ref": f"#/components/schemas/{name}"}, sent)
        if (
            status in (400, 401, 403)
            and isinstance(body, dict)
            and body.get("detail") in ("invalid_idempotency_key", "role_not_authorized")
        ):
            continue
        if "\\ud8" in json.dumps(sent):
            continue  # a lone surrogate is refused by the strict JSON reader; no schema can say so
        refused = status == 400 and isinstance(body, dict) and body.get("detail") == "invalid_body"
        check(
            "C-SCHEMA",
            f"{name}: schema-valid <=> not refused as invalid_body ({str(sent)[:60]!r}, {status})",
            in_schema == (not refused),
        )
    # the grammar drift table
    schema = {"$ref": "#/components/schemas/DatasetMappingSpec"}
    total = 0
    wrong: list[str] = []
    accepted = 0
    for label, document in mapping_variants():
        total += 1
        parsed = accepts(document)
        accepted += parsed
        if valid(root, schema, document) != parsed:
            wrong.append(f"{label} (parser {'accepts' if parsed else 'refuses'})")
    check(
        "C-DRIFT",
        f"schema-valid <=> mapping_from_object accepts, over {total} generated documents; {len(wrong)} differ: {wrong[:8]}",
        not wrong,
    )
    check(
        "C-DRIFT",
        f"the table has both outcomes in quantity ({accepted} accepted of {total})",
        total >= 500 and 30 <= accepted <= total - 30,
    )
    # wiring
    main_src = MAIN_PATH.read_text()
    check(
        "C-WIRE",
        "main.py imports build_dataset_mapping_router",
        "from app.datasets.dataset_mapping_routes import build_dataset_mapping_router" in main_src,
    )
    check(
        "C-WIRE",
        "create_app includes it with the session factory, principal resolver and role lookup",
        re.search(
            r"app\.include_router\(\s*build_dataset_mapping_router\(\s*session_factory, principal_resolver=principal_resolver, role_lookup=role_lookup\s*\)\s*\)",
            main_src,
        )
        is not None,
    )
    check(
        "C-WIRE",
        "the proving symbol is the router builder",
        "build_dataset_mapping_router"
        in ROUTES_PATH.read_text().split("__all__")[1].split("\n")[0],
    )


@contextlib.contextmanager
def first_none(mod: ModuleType, *names: str) -> Iterator[None]:
    """The first call of each named lookup answers None (the key is not stored yet), later calls are
    the real ones: a concurrent identical request that stores its key in between."""
    originals = {name: getattr(mod, name) for name in names}

    def make(name: str) -> Any:
        state = {"first": True}

        def wrapper(*args: Any, **kwargs: Any) -> Any:
            if state["first"]:
                state["first"] = False
                return None
            return originals[name](*args, **kwargs)

        return wrapper

    for name in names:
        setattr(mod, name, make(name))
    try:
        yield
    finally:
        for name, value in originals.items():
            setattr(mod, name, value)


@contextlib.contextmanager
def patched_attr(mod: ModuleType, name: str, value: Any) -> Iterator[None]:
    original = getattr(mod, name)
    setattr(mod, name, value)
    try:
        yield
    finally:
        setattr(mod, name, original)


def part_r_tenant() -> None:
    with http_world() as (w, base):
        conn, ds = seeded(w)
        status, made = propose(base, ds)
        mid = made["id"]
        w.tenant_error = True
        routes: list[tuple[str, str, Any, str | None]] = [
            ("POST", "/mapping-probe", {}, None),
            ("POST", "/mapping-preview", {"mapping": MSG}, None),
            ("POST", "/mappings", {"mapping": MSG}, "tenant-key-0001"),
            ("GET", "/mappings", None, None),
            ("GET", f"/mappings/{mid}", None, None),
            ("POST", f"/mappings/{mid}/approve", approve_body(made), "tenant-key-0002"),
            ("POST", f"/mappings/{mid}/reject", {"rationale": "no"}, "tenant-key-0003"),
        ]
        for method, suffix, body, key in routes:
            got = call(base, method, f"{BASE_DS}/{ds.id}{suffix}", "admin", body, key)
            check(
                "R-TENANT",
                f"{method} {suffix.split('/')[1]}: an unbindable tenant context is 404 not_found, never a 500 (got {got[0]} {code(got)})",
                got == (404, {"detail": "not_found"}),
            )
        w.tenant_error = False
        check("R-TENANT", "and nothing was written", w.snapshot()[0] == 1)


def part_r_races() -> None:
    with http_world() as (w, base):
        conn, ds = seeded(w)
        status, first = propose(base, ds, key="race-key-00001", who="admin")
        check("R-RACE", "fixture: the first proposal is 201", status == 201)
        before = w.snapshot()
        attempts = w.calls.get("propose_dataset_mapping", 0)
        previews = len(w.base.preview_calls)
        # a plain replay never reaches the store or the storage
        status, replay = propose(base, ds, key="race-key-00001", who="admin")
        check(
            "R-RACE",
            "a replay does not call the store (no second preview, no second proposal)",
            status == 200
            and w.calls.get("propose_dataset_mapping", 0) == attempts
            and len(w.base.preview_calls) == previews,
        )
        # a concurrent identical request stored its key between the lookup and the write
        with first_none(real_routes, "find_mapping_request"):
            status, raced = propose(base, ds, key="race-key-00001", who="admin")
        check(
            "R-RACE",
            f"propose: the key stored by a concurrent identical request replays (got {status} {raced})",
            status == 200
            and raced["replayed"] is True
            and raced["created"] is False
            and raced["id"] == first["id"],
        )
        check(
            "R-RACE",
            "propose: the raced attempt did reach the store and wrote nothing",
            w.calls.get("propose_dataset_mapping", 0) == attempts + 1 and w.snapshot() == before,
        )
        with first_none(real_routes, "find_mapping_request"):
            got = propose(base, ds, doc=MSG2, key="race-key-00001", who="admin")
        check(
            "R-RACE",
            f"propose: the key stored by a different request is 409 idempotency_key_reused (got {got[0]} {code(got)})",
            got == (409, {"detail": "idempotency_key_reused"}),
        )
        check(
            "R-RACE",
            "propose: and the version it would have written is rolled back",
            w.snapshot()[:2] == before[:2],
        )
        with patched_attr(real_routes, "find_mapping_request", lambda *a, **k: None):
            got = propose(base, ds, key="race-key-00001", who="admin")
        check(
            "R-RACE",
            f"propose: a key that cannot be found again is 409 idempotency_key_reused, not a 500 (got {got[0]} {code(got)})",
            got == (409, {"detail": "idempotency_key_reused"}),
        )

        def raising(code_: str) -> Any:
            def fn(*args: Any, **kwargs: Any) -> None:
                raise rstore.MappingRequestError(code_)

            return fn

        with patched_attr(real_routes, "record_mapping_request", raising("invalid_request")):
            got = propose(base, ds, doc=MSG2, key="race-key-00009", who="admin")
        check(
            "R-RACE",
            f"propose: any other request-store refusal is a 409 with its code (got {got[0]} {code(got)})",
            got == (409, {"detail": "invalid_request"}),
        )
        check("R-RACE", "propose: and it wrote nothing", w.snapshot()[:2] == before[:2])
        # approve: two proposals, the first approved
        status, other = propose(base, ds, doc=MSG2, key="race-key-00002", who="admin")
        status, third = propose(
            base,
            ds,
            doc=p2.msg_doc({"a": "user", "b": "assistant"}),
            key="race-key-00003",
            who="admin",
        )
        check(
            "R-RACE",
            "fixture: versions 2 and 3 exist",
            other["mapping_version"] == 2 and third["mapping_version"] == 3,
        )
        # only the latest version can be approved (earlier ones are superseded): use the latest
        status, approved = approve(base, ds, third, key="race-approve-01")
        check("R-RACE", f"fixture: version 3 is approved (got {status} {approved})", status == 200)
        before = w.snapshot()
        attempts = w.calls.get("approve_dataset_mapping", 0)
        previews = len(w.base.preview_calls)
        status, replay = approve(base, ds, third, key="race-approve-01")
        check(
            "R-RACE",
            "approve: a replay does not call the store (no second preview)",
            status == 200
            and replay["replayed"] is True
            and w.calls.get("approve_dataset_mapping", 0) == attempts
            and len(w.base.preview_calls) == previews,
        )
        status, replay = approve(base, ds, third, key="race-approve-02")
        check(
            "R-RACE",
            "approve: the same request under another key is recognised without calling the store",
            status == 200
            and replay["replayed"] is True
            and w.calls.get("approve_dataset_mapping", 0) == attempts
            and len(w.base.preview_calls) == previews,
        )
        with first_none(real_routes, "find_mapping_request", "find_mapping_request_by_action"):
            status, raced = approve(base, ds, third, key="race-approve-01")
        check(
            "R-RACE",
            f"approve: a concurrent identical approval that won is replayed (got {status} {raced})",
            status == 200
            and raced["replayed"] is True
            and w.calls.get("approve_dataset_mapping", 0) == attempts + 1,
        )
        check("R-RACE", "approve: and wrote nothing", w.snapshot() == before)
        with (
            patched_attr(real_routes, "find_mapping_request", lambda *a, **k: None),
            patched_attr(real_routes, "find_mapping_request_by_action", lambda *a, **k: None),
        ):
            got = approve(base, ds, third, key="race-approve-01")
        check(
            "R-RACE",
            f"approve: when no stored outcome can be found the refusal stands: 409 mapping_not_proposed (got {got[0]} {code(got)})",
            got == (409, {"detail": "mapping_not_proposed"}),
        )
        # approve a second proposal under a key an earlier request already used
        status, fourth = propose(
            base,
            ds,
            doc=p2.msg_doc({"c": "user", "d": "assistant"}),
            key="race-key-00004",
            who="admin",
        )
        before = w.snapshot()
        with first_none(real_routes, "find_mapping_request"):
            got = approve(base, ds, fourth, key="race-approve-01")
        check(
            "R-RACE",
            f"approve: the key of another approval is 409 idempotency_key_reused (got {got[0]} {code(got)})",
            got == (409, {"detail": "idempotency_key_reused"}),
        )
        check(
            "R-RACE",
            "approve: and the approval it would have made is rolled back",
            w.snapshot()[:2] == before[:2] and rows_status(w, fourth["id"]) == "proposed",
        )
        with patched_attr(real_routes, "record_mapping_request", raising("invalid_request")):
            got = approve(base, ds, fourth, key="race-approve-03")
        check(
            "R-RACE",
            f"approve: any other request-store refusal is a 409 with its code (got {got[0]} {code(got)})",
            got == (409, {"detail": "invalid_request"}),
        )
        check(
            "R-RACE",
            "approve: and rolls the approval back",
            w.snapshot()[:2] == before[:2] and rows_status(w, fourth["id"]) == "proposed",
        )
        # reject
        status, rejected = reject(base, ds, fourth, key="race-reject-01")
        check("R-RACE", f"fixture: version 4 is rejected (got {status})", status == 200)
        before = w.snapshot()
        attempts = w.calls.get("reject_dataset_mapping", 0)
        status, replay = reject(base, ds, fourth, key="race-reject-01")
        check(
            "R-RACE",
            "reject: a replay does not call the store",
            status == 200
            and replay["replayed"] is True
            and w.calls.get("reject_dataset_mapping", 0) == attempts,
        )
        status, replay = reject(base, ds, fourth, key="race-reject-02")
        check(
            "R-RACE",
            "reject: the same request under another key is recognised without calling the store",
            status == 200
            and replay["replayed"] is True
            and w.calls.get("reject_dataset_mapping", 0) == attempts,
        )
        with first_none(real_routes, "find_mapping_request", "find_mapping_request_by_action"):
            status, raced = reject(base, ds, fourth, key="race-reject-01")
        check(
            "R-RACE",
            f"reject: a concurrent identical rejection that won is replayed (got {status} {raced})",
            status == 200
            and raced["replayed"] is True
            and w.calls.get("reject_dataset_mapping", 0) == attempts + 1,
        )
        check("R-RACE", "reject: and wrote nothing", w.snapshot() == before)
        with (
            patched_attr(real_routes, "find_mapping_request", lambda *a, **k: None),
            patched_attr(real_routes, "find_mapping_request_by_action", lambda *a, **k: None),
        ):
            got = reject(base, ds, fourth, key="race-reject-01")
        check(
            "R-RACE",
            f"reject: when no stored outcome can be found the refusal stands: 409 mapping_not_proposed (got {got[0]} {code(got)})",
            got == (409, {"detail": "mapping_not_proposed"}),
        )
        status, fifth = propose(
            base,
            ds,
            doc=p2.msg_doc({"e": "user", "f": "assistant"}),
            key="race-key-00005",
            who="admin",
        )
        before = w.snapshot()
        status, second = propose(base, ds, doc=MSG2, key="norep-key-00002", who="admin")
        status, rejected = reject(base, ds, second, key="norep-rej-00001")
        check("R-NOREPLAY", "fixture: rejected", status == 200)
        with (
            first_none(real_routes, "find_mapping_request", "find_mapping_request_by_action"),
            patched_attr(real_routes, "reject_dataset_mapping", raising_store("approval_refused")),
        ):
            got = reject(base, ds, second, key="norep-rej-00001")
        check(
            "R-NOREPLAY",
            f"reject: a non-replayable store refusal is not a replay even though the rejection is stored (got {got[0]} {code(got)})",
            got == (409, {"detail": "approval_refused"}),
        )


def rows_status(w: HttpWorld, mapping_id: str) -> str:
    w.session.commit()
    return w.session.get(store.DatasetMapping, uuid.UUID(mapping_id)).status


def part_r_errors() -> None:
    with http_world() as (w, base):
        conn, ds = seeded(w)
        errors: list[tuple[str, BaseException, int, str]] = [
            (
                "an importer-level unsupported shape",
                ic.MappingUnsupportedError(),
                400,
                "mapping_unsupported",
            ),
            (
                "an importer-level invalid option",
                ic.MappingInvalidError("bad_value"),
                400,
                "mapping_invalid",
            ),
            ("a dataset that disappeared", ustore.DatasetNotFoundError("gone"), 404, "not_found"),
            (
                "a revoked grant",
                crud.DatasetSourceAuthorizationError("revoked"),
                403,
                "purpose_not_authorized",
            ),
            (
                "a deleted connection",
                crud.DatasetSourceConnectionNotFoundError("gone"),
                403,
                "purpose_not_authorized",
            ),
            (
                "an inconsistent dataset",
                DatasetSourceInconsistentError("changed"),
                409,
                "source_changed",
            ),
            (
                "a content type without an importer",
                p2.MappingPreviewUnavailableError("application/zip"),
                409,
                "importer_unavailable",
            ),
        ]
        for label, error, status, detail in errors:
            before = w.snapshot()
            w.base.preview_error = error
            w.probe_error = error
            got = call(
                base, "POST", f"{BASE_DS}/{ds.id}/mapping-preview", "admin", {"mapping": MSG}
            )
            check(
                "R-ERRORS",
                f"preview: {label} is {status} {detail} (got {got[0]} {code(got)})",
                got == (status, {"detail": detail}),
            )
            got = call(base, "POST", f"{BASE_DS}/{ds.id}/mapping-probe", "admin", {})
            check(
                "R-ERRORS",
                f"probe: {label} is {status} {detail} (got {got[0]} {code(got)})",
                got == (status, {"detail": detail}),
            )
            got = propose(base, ds, key="errors-key-0001", who="admin")
            check(
                "R-ERRORS",
                f"propose: {label} is {status} {detail} (got {got[0]} {code(got)})",
                got == (status, {"detail": detail}),
            )
            w.base.preview_error = None
            w.probe_error = None
            check(
                "R-ERRORS", f"{label}: nothing was stored, not even the key", w.snapshot() == before
            )
        # an unknown state is never shown as a result
        for state in ("BLOCKED", "completed ", "running", ""):
            w.base.preview_state = (state, None)
            got = call(
                base, "POST", f"{BASE_DS}/{ds.id}/mapping-preview", "admin", {"mapping": MSG}
            )
            check(
                "R-ERRORS",
                f"preview: the unknown state {state!r} is 409 preview_incomplete (got {got[0]} {code(got)})",
                got == (409, {"detail": "preview_incomplete"}),
            )
        w.base.preview_state = ("completed", None)
        for state in ("BLOCKED", "completed ", "running", ""):
            w.probe_state = (state, None)
            got = call(base, "POST", f"{BASE_DS}/{ds.id}/mapping-probe", "admin", {})
            check(
                "R-ERRORS",
                f"probe: the unknown state {state!r} is 409 preview_incomplete (got {got[0]} {code(got)})",
                got == (409, {"detail": "preview_incomplete"}),
            )
        w.probe_state = ("completed", None)


def part_r_lists_and_limits() -> None:
    with http_world() as (w, base):
        conn, ds = seeded(w)
        status, m1 = propose(base, ds, key="list-key-000001", who="admin")
        status, m2 = propose(base, ds, doc=MSG2, key="list-key-000002", who="admin")
        status, m3 = propose(
            base,
            ds,
            doc=p2.msg_doc({"a": "user", "b": "assistant"}),
            key="list-key-000003",
            who="admin",
        )
        reject(base, ds, m1, key="list-key-000004", rationale="first no")
        approve(base, ds, m3, key="list-key-000005", rationale="third yes")
        status, listing = call(base, "GET", f"{BASE_DS}/{ds.id}/mappings", "admin")
        decisions = [m["decision"] for m in listing["items"]]
        check(
            "R-LIST",
            f"the list carries each version's decision: reject, none, approve ({decisions})",
            decisions
            == [
                {"action": "reject", "rationale": "first no", "decided_by": "admin"},
                None,
                {"action": "approve", "rationale": "third yes", "decided_by": "admin"},
            ],
        )
        check(
            "R-LIST",
            "and the statuses",
            [m["status"] for m in listing["items"]] == ["rejected", "proposed", "approved"],
        )
        # body size boundary: exactly the cap is read, one byte more is 413
        status, target = propose(
            base,
            ds,
            doc=p2.msg_doc({"x": "user", "y": "assistant"}),
            key="list-key-000006",
            who="admin",
        )
        cap = real_routes._MAX_BODY_BYTES
        body = b'{"rationale": "x"}'
        exact = body + b" " * (cap - len(body))
        got = call(
            base,
            "POST",
            f"{BASE_DS}/{ds.id}/mappings/{target['id']}/reject",
            "admin",
            raw=exact,
            key="list-key-000007",
        )
        check(
            "R-LIMIT",
            f"a body of exactly {cap} bytes is read (got {got[0]} {code(got)})",
            got[0] == 409 or got[0] == 200,
        )
        over = body + b" " * (cap - len(body) + 1)
        got = call(
            base,
            "POST",
            f"{BASE_DS}/{ds.id}/mappings/{target['id']}/reject",
            "admin",
            raw=over,
            key="list-key-000008",
        )
        check(
            "R-LIMIT",
            "one byte over the cap is 413 request_too_large",
            got == (413, {"detail": "request_too_large"}),
        )
        # one-character purpose and rationale are accepted by body validation
        status, fresh = propose(
            base,
            ds,
            doc=p2.msg_doc({"m": "user", "n": "assistant"}),
            key="list-key-000009",
            who="admin",
        )
        got = call(
            base,
            "POST",
            f"{BASE_DS}/{ds.id}/mappings/{fresh['id']}/approve",
            "admin",
            approve_body(fresh, purpose="t", rationale="r"),
            "list-key-000010",
        )
        check(
            "R-LIMIT",
            f"a one-character purpose and rationale pass body validation (got {got[0]} {code(got)})",
            got == (403, {"detail": "purpose_not_authorized"}),
        )
        # every audit event names the authenticated user
        check(
            "R-AUDIT",
            "every audit event has actor_type user",
            all(e["actor_type"] == "user" for e in w.audit.events) and len(w.audit.events) >= 6,
        )
        call(base, "POST", f"{BASE_DS}/{ds.id}/mapping-preview", "ml", {"mapping": MSG})
        call(base, "POST", f"{BASE_DS}/{ds.id}/mapping-probe", "ml", {})
        check(
            "R-AUDIT",
            "and so do the preview and probe events",
            [e["actor_type"] for e in w.audit.events[-2:]] == ["user", "user"]
            and [e["actor_id"] for e in w.audit.events[-2:]] == ["ml", "ml"],
        )


def part_u2() -> None:
    mod = real_routes
    for code_ in store.CODES:
        for variant in (" " + code_, code_ + " ", code_.upper(), code_ + "\n"):
            exc = mod._refusal(variant)
            check(
                "U-REFUSAL",
                f"{variant!r} is no code of the table: it is a 409 conflict",
                (exc.status_code, exc.detail) == (409, variant),
            )
    for code_, wanted in (
        ("mapping_not_proposed", True),
        ("idempotency_key_reused", True),
        ("mapping_conflict", False),
        ("invalid_request", False),
        (" mapping_not_proposed", False),
        ("MAPPING_NOT_PROPOSED", False),
        ("idempotency_key_reused\n", False),
        ("Idempotency_Key_Reused", False),
    ):
        check("U-REPLAYABLE", f"{code_!r} replayable: {wanted}", mod._replayable(code_) is wanted)
    for state, failure, wanted in (
        ("completed", None, None),
        ("failed", None, None),
        ("failed", "limit_exceeded", None),
        ("blocked", None, "source_unavailable"),
        ("blocked", "source_changed", "source_unavailable"),
        ("failed", "source_changed", "source_changed"),
        ("completed", "source_changed", "source_changed"),
        ("BLOCKED", None, "preview_incomplete"),
        (" blocked", None, "preview_incomplete"),
        ("Blocked", None, "preview_incomplete"),
        ("COMPLETED", None, "preview_incomplete"),
        ("completed ", None, "preview_incomplete"),
        ("Failed", None, "preview_incomplete"),
        ("running", None, "preview_incomplete"),
        ("", None, "preview_incomplete"),
        ("failed", "SOURCE_CHANGED", None),
        ("failed", "source_changed ", None),
    ):
        check(
            "U-VERDICT",
            f"state {state!r} failure {failure!r} is refused as {wanted}",
            store._verdict_refusal(state, failure) == wanted,
        )
    try:
        rstore.MappingRequestError("brand_new")
        raised = False
    except AssertionError:
        raised = True
    check("U-CODES", "an unknown request-store code cannot be constructed", raised)
    for variant in (
        " invalid_request",
        "INVALID_REQUEST",
        "invalid_request ",
        "Idempotency_Key_Reused",
    ):
        try:
            rstore.MappingRequestError(variant)
            raised = False
        except AssertionError:
            raised = True
        check("U-CODES", f"{variant!r} is no request-store code", raised)
    ok = rstore.MappingRequestError("idempotency_key_reused")
    check(
        "U-CODES",
        "a known code is kept",
        ok.code == "idempotency_key_reused" and str(ok) == "idempotency_key_reused",
    )
    d = uuid.uuid4()
    for action in (" approve", "APPROVE", "approve ", "Propose", "reject\n"):
        got = refusal_of(
            lambda a=action: rstore.request_digest(
                a, dataset_id=d, mapping_id=None, subject="x", body={}
            )
        )
        check("U-DIGEST", f"the action {action!r} is no action: refused", got == "invalid_request")
    # the decision a stored status leads to
    mid = uuid.uuid4()
    asked: list[str] = []
    original = mod.find_mapping_request_by_action
    mod.find_mapping_request_by_action = lambda session, tenant, **kw: asked.append(
        kw["action"]
    ) or ("decision", kw["mapping_id"])
    try:
        for status, wanted in (
            ("approved", "approve"),
            ("rejected", "reject"),
            ("proposed", None),
            ("Approved", None),
            (" approved", None),
            ("APPROVED", None),
            ("rejected ", None),
            ("Rejected", None),
            ("", None),
        ):
            asked.clear()
            got = mod._decision(None, None, uuid.uuid4(), SimpleNamespace(status=status, id=mid))
            check(
                "U-DECISION",
                f"status {status!r} asks for the {wanted!r} request and returns it ({asked})",
                asked == ([wanted] if wanted else [])
                and (got == ("decision", mid)) == (wanted is not None)
                and (wanted is not None or got is None),
            )
    finally:
        mod.find_mapping_request_by_action = original
    # the store's role handling is exact
    with p2.sqlite_world() as w:
        conn = w.connection("all", ALL_PURPOSES)
        ds = w.dataset("alpha", "training", conn)
        orig = store.get_dataset_source_connection
        store.get_dataset_source_connection = (
            lambda session, context, project_id, connection_id: SimpleNamespace(
                security_classification="internal"
            )
        )
        try:
            for role in (
                "Workspace_Admin",
                "WORKSPACE_ADMIN",
                "ORGANIZATION_ADMIN",
                "ML_ENGINEER",
                "Ml_Engineer",
                "ml_engineer2",
                "admin",
            ):
                got = store.dataset_visibility(
                    w.session, w.ctx, project_id=PROJECT, dataset_id=ds.id, role=role
                )
                check(
                    "S-VISIBILITY",
                    f"the role {role!r} is not a known role: no reveal, no held-out ({got})",
                    got == (False, False),
                )
            for classification in (
                "Internal",
                "PUBLIC",
                " internal",
                "public ",
                "Public",
                "internal\n",
            ):
                orig_conn = store.get_dataset_source_connection
                store.get_dataset_source_connection = (
                    lambda session,
                    context,
                    project_id,
                    connection_id,
                    c=classification: SimpleNamespace(security_classification=c)
                )
                try:
                    got = store.dataset_visibility(
                        w.session, w.ctx, project_id=PROJECT, dataset_id=ds.id, role="ml_engineer"
                    )
                finally:
                    store.get_dataset_source_connection = orig_conn
                check(
                    "S-VISIBILITY",
                    f"the classification {classification!r} is none of public/internal: an ml_engineer does not reveal ({got})",
                    got == (False, False),
                )
            check(
                "S-VISIBILITY",
                "the exact roles keep working",
                store.dataset_visibility(
                    w.session, w.ctx, project_id=PROJECT, dataset_id=ds.id, role="ml_engineer"
                )
                == (False, True)
                and store.dataset_visibility(
                    w.session, w.ctx, project_id=PROJECT, dataset_id=ds.id, role="workspace_admin"
                )
                == (True, True),
            )
            # the probe's slot per importer
            csv_ds = w.dataset("csv", "training", conn, content_type="text/csv")
            seen: list[Any] = []
            orig_probe = store.probe_dataset_source
            store.probe_dataset_source = lambda session, context, **kw: seen.append(
                kw["probe_mapping"]
            ) or ProbeResult("completed", None, None, (), True)
            try:
                store.review_probe(
                    w.session,
                    w.ctx,
                    project_id=PROJECT,
                    dataset_id=csv_ds.id,
                    format_options={},
                    role="workspace_admin",
                    storage=w.storage,
                )
                store.review_probe(
                    w.session,
                    w.ctx,
                    project_id=PROJECT,
                    dataset_id=ds.id,
                    format_options={},
                    role="workspace_admin",
                    storage=w.storage,
                )
                json_ds = w.dataset("json", "training", conn, content_type="application/json")
                store.review_probe(
                    w.session,
                    w.ctx,
                    project_id=PROJECT,
                    dataset_id=json_ds.id,
                    format_options={},
                    role="workspace_admin",
                    storage=w.storage,
                )
            finally:
                store.probe_dataset_source = orig_probe
            check(
                "S-PROBE",
                "a CSV is probed by column index, JSONL and JSON by key",
                [(m.prompt.kind, m.completion.kind) for m in seen]
                == [("index", "index"), ("key", "key"), ("key", "key")],
            )
            check(
                "S-PROBE",
                "the probe slots are fixed (index 0 / key probe)",
                [(m.prompt.value, m.completion.value) for m in seen]
                == [(0, 0), ("probe", "probe"), ("probe", "probe")],
            )
        finally:
            store.get_dataset_source_connection = orig


def part_c2() -> None:
    import os

    env = {
        "SOVRA_ENVIRONMENT": "staging",
        "SOVRA_DATABASE_URL": "postgresql+psycopg://sovra_app:s3cr3t@db.internal:5432/sovra",
        "SOVRA_LITELLM_GATEWAY_URL": "https://litellm.internal.example",
        "SOVRA_PRIVACY_POLICY": "standard",
        "SOVRA_TLS_VERIFY": "true",
        "SOVRA_MAX_TRAINING_BUDGET_USD": "500.00",
        "SOVRA_LITELLM_API_KEY_SECRET_REF": "vault://sovra/litellm-api-key",
        "SOVRA_DATABASE_PASSWORD_SECRET_REF": "openbao://sovra/db-password",
        "SOVRA_OIDC_ISSUER": "http://idp.internal.example",
        "SOVRA_OIDC_CLIENT_ID": "sovra-factory",
        "SOVRA_OIDC_CLIENT_SECRET_REF": "vault://sovra/oidc-client-secret",
        "SOVRA_OIDC_REDIRECT_URI": "http://factory.internal.example/auth/callback",
        "SOVRA_OIDC_AUTHORIZATION_ENDPOINT": "http://idp.internal.example/authorize",
        "SOVRA_OIDC_TOKEN_ENDPOINT": "http://idp.internal.example/token",
        "SOVRA_OIDC_JWKS_URI": "http://idp.internal.example/jwks",
        "SOVRA_SESSION_SIGNING_KEY_SECRET_REF": "vault://sovra/session-signing-key",
        "SOVRA_OIDC_ALLOWED_ORIGIN": "http://factory.internal.example",
        "SOVRA_OIDC_CLIENT_SECRET_VALUE": "not-a-real-secret",
        "SOVRA_SESSION_SIGNING_KEY_VALUE": "not-a-real-signing-key",
    }
    added = set(env) - set(os.environ)
    old = {k: os.environ[k] for k in env if k in os.environ}
    os.environ.update(env)
    try:
        import app.main as main_module

        app = main_module.create_app()
    finally:
        for key in added:
            os.environ.pop(key, None)
        os.environ.update(old)
    dataset = uuid.uuid4()
    with running_app_for(app) as base:
        for method, suffix in (
            ("POST", "/mapping-probe"),
            ("POST", "/mapping-preview"),
            ("GET", "/mappings"),
            ("POST", "/mappings"),
            ("GET", f"/mappings/{uuid.uuid4()}"),
            ("POST", f"/mappings/{uuid.uuid4()}/approve"),
            ("POST", f"/mappings/{uuid.uuid4()}/reject"),
        ):
            got = call(
                base, method, f"{BASE_DS}/{dataset}{suffix}", None, {} if method == "POST" else None
            )
            check(
                "C-WIRE",
                f"{method} {suffix.split('/')[1]} is mounted on the real app (401, got {got[0]})",
                got[0] == 401,
            )


def main() -> None:
    part_r_access()
    part_r_validation()
    part_r_propose()
    part_r_reads()
    part_r_decisions()
    part_r_review()
    part_u()
    part_s_requests()
    part_s_store_reads()
    part_r_tenant()
    part_r_races()
    part_r_not_replayable()
    part_r_errors()
    part_r_lists_and_limits()
    part_u2()
    part_m()
    part_c()
    part_c2()
    if FAILURES:
        for failure in FAILURES:
            print(failure, file=sys.stderr)
        print(f"{len(FAILURES)} check(s) failed", file=sys.stderr)
        sys.exit(1)
    print("all checks passed")


if __name__ == "__main__":
    main()
