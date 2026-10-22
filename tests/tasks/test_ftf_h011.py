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
from app.datasets import dataset_mapping_request_store as rstore  # noqa: E402
from app.datasets import dataset_mapping_routes as real_routes  # noqa: E402
from app.datasets import dataset_mapping_store as store  # noqa: E402
from app.datasets import dataset_source_connection_crud as crud  # noqa: E402
from app.datasets import dataset_upload_store as ustore  # noqa: E402
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
        return response.status, json.loads(data)
    except ValueError:
        return response.status, data


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


def main() -> None:
    part_r_access()
    part_r_validation()
    part_r_propose()
    part_r_reads()
    part_r_decisions()
    part_r_review()
    if FAILURES:
        for failure in FAILURES:
            print(failure, file=sys.stderr)
        print(f"{len(FAILURES)} check(s) failed", file=sys.stderr)
        sys.exit(1)
    print("all checks passed")


if __name__ == "__main__":
    main()
