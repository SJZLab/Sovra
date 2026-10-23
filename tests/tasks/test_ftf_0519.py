#!/usr/bin/env python3
# ruff: noqa: E501
"""FTF-0519-P2 -- the dataset split manifest routes, their contract and their wiring (no-DB tests;
the live Postgres evidence is `tests/integration/test_ftf_0519_routes_postgres.py`, run by CI's
`dataset-split-manifest-routes-integration` job).

Without any database this suite proves, on a real uvicorn server with the store replaced by a
small state machine (the route module's own store calls are the only thing patched):

R  roles and authentication on all four routes (an ml_engineer may read but never approve or
   reject; no create route exists), pagination and query validation, the exact response shape from
   an explicit allow-list (a canary placed in every column a response must not carry -- finding
   record ids, group ids, review text, an object key -- never appears), the body-validation table
   (wrong types, unknown keys, bad hashes, bad Idempotency-Key, oversize), the reviewer taken from the
   authenticated principal, every store refusal's status and code, and replay (the same request
   after it took effect is a 200 `replayed: true` with no second write; a different body, approver
   or expected hash is not a replay);
C  the interface contract (05-interface.yaml) and the app wiring;
F  fault injection: each protection is removed from a copy of the route module on disk and the
   same route suite must turn RED.

Run directly: python3 tests/tasks/test_ftf_0519.py
"""

from __future__ import annotations

import contextlib
import hashlib
import http.client
import importlib.util
import json
import os
import re
import socket
import sys
import tempfile
import threading
import time
import urllib.parse
import uuid
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any, cast

import uvicorn
import yaml
from fastapi import FastAPI
from sqlalchemy.orm import Session

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "apps" / "api"))

from app.auth.rbac_middleware import AuthenticatedPrincipal, Role  # noqa: E402
from app.auth.tenant_isolation_query_guards import TenantContextError  # noqa: E402
from app.datasets import dataset_split_manifest_routes as real_routes  # noqa: E402
from app.datasets import dataset_split_manifest_store as store  # noqa: E402
from app.db.tenant_context import TenantContext  # noqa: E402

FAILURES: list[str] = []
CID = "FTF-0519-P2"
ROUTES_PATH = ROOT / "apps" / "api" / "app" / "datasets" / "dataset_split_manifest_routes.py"
INTERFACE_PATH = ROOT / "packages" / "contracts" / "openapi" / "05-interface.yaml"
MAIN_PATH = ROOT / "apps" / "api" / "app" / "main.py"

ORG = uuid.uuid4()
PROJECT = uuid.uuid4()
MANIFEST = uuid.uuid4()
OTHER_MANIFEST = uuid.uuid4()
BASE = f"/api/v1/organizations/{ORG}/projects/{PROJECT}/dataset-splits"
PROPOSAL = "a" * 64
NOW = datetime(2026, 10, 16, 12, 0, tzinfo=UTC)
HELD_OUT_DATASET = uuid.UUID(int=0x4E1D)
HELD_OUT_CONNECTION = uuid.UUID(int=0x4E1E)
HELD_OUT_REPORT = uuid.UUID(int=0x4E1F)
CANARIES = (
    "ds-9f3a:12",
    "group-secret-77",
    "finding-leak-42",
    "review-text-secret",
    "tenants/o/projects/p/uploads/x/source",
)

PRINCIPALS = {
    name: AuthenticatedPrincipal(issuer="https://issuer.example", subject=name)
    for name in ("ml", "admin", "orgadmin", "viewer", "evaluator", "approver", "admin2")
}
ROLES = {
    "ml": Role.ML_ENGINEER,
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


class Checker:
    def __init__(self) -> None:
        self.fails: list[str] = []

    def __call__(self, criterion: str, description: str, condition: bool) -> None:
        if not condition:
            self.fails.append(f"FAIL [{criterion}] {description}")


# ------------------------------------------------------------------ fake store


_UNSET: Any = object()


class FakeStore:
    """A state machine standing in for the store: proposed -> locked | rejected."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.raise_code: str | None = None
        self.raise_other: BaseException | None = None
        self.states: dict[uuid.UUID, dict[str, Any]] = {}
        self.stored_reviews_override: Any = _UNSET
        self.requests: dict[tuple[uuid.UUID, str], store.StoredRequest] = {}
        self.hide_key_once = False
        self.reset()

    def reset(self) -> None:
        self.calls.clear()
        self.raise_code = None
        self.raise_other = None
        self.stored_reviews_override = _UNSET
        self.requests = {}
        self.states = {
            MANIFEST: self.fresh(MANIFEST),
            OTHER_MANIFEST: self.fresh(OTHER_MANIFEST),
        }

    @staticmethod
    def fresh(manifest_id: uuid.UUID) -> dict[str, Any]:
        return {
            "id": manifest_id,
            "status": "proposed",
            "approved_by": None,
            "reviews": [],
            "frozen": None,
        }

    def count(self, name: str) -> int:
        return sum(1 for n, _ in self.calls if n == name)

    def last(self, name: str) -> dict[str, Any]:
        return [kw for n, kw in self.calls if n == name][-1]

    def view(self, state: dict[str, Any]) -> store.ManifestView:
        values: dict[str, Any] = {
            "id": state["id"],
            "organization_id": ORG,
            "project_id": PROJECT,
            "name": "campaign",
            "version": 1,
            "parent_version": None,
            "strategy": "sha256_group_hash_v1",
            "seed": "seed-0519",
            "ratios": [70, 10, 10, 10],
            "cutoff": None,
            "normalization_version": "text-normalization-v1",
            "tokenizer_identity": None,
            "split_manifest_sha256": "b" * 64,
            "proposal_sha256": PROPOSAL,
            "leakage_report_sha256": "c" * 64,
            "leakage_status": "clear",
            "reviewed_leakage_report_sha256": "d" * 64 if state["status"] == "locked" else None,
            "migrated_from_legacy_sha256": None,
            "status": state["status"],
            "frozen_manifest_sha256": state["frozen"],
            "approved_by": state["approved_by"],
            "locked_at": NOW if state["status"] == "locked" else None,
            "created_by": "proposer",
            "created_at": NOW,
            "updated_at": NOW,
            # columns a response must never carry:
            "leakage_report": {
                "findings": [{"record_ids": ["ds-9f3a:12"], "group": "group-secret-77"}]
            },
            "leakage_reviews": [
                {"finding_id": "finding-leak-42", "rationale": "review-text-secret"}
            ],
            "dataset_object_key": "tenants/o/projects/p/uploads/x/source",
        }
        summary = {
            "status": "clear",
            "finding_counts": {"exact_hash": {"review_required": 2}},
            "methods_run": ["exact_hash"],
            "methods_not_run": [],
            "review_count": len(state["reviews"]),
            "leakage_report_sha256": "c" * 64,
            "reviewed_leakage_report_sha256": values["reviewed_leakage_report_sha256"],
        }
        return store.ManifestView(values=values, leakage_summary=summary)

    def summary(self, state: dict[str, Any]) -> store.ManifestSummary:
        roles = tuple(
            SimpleNamespace(
                role=role,
                purpose=purpose,
                record_count=10 + i,
                group_count=5 + i,
                role_sha256=chr(ord("1") + i) * 64,
                record_ids=["ds-9f3a:12"],
            )
            for i, (role, purpose) in enumerate(
                [
                    ("train", "training"),
                    ("validation", "validation"),
                    ("development_eval", "candidate_development"),
                    ("final_qualification", "held_out_evaluation"),
                ]
            )
        )
        sources = (
            SimpleNamespace(
                dataset_id=uuid.UUID(int=1),
                dataset_name="train-a",
                dataset_purpose="training",
                dataset_version=1,
                dataset_sha256="e" * 64,
                dataset_source_connection_id=uuid.UUID(int=2),
                allowed_purposes_snapshot=["training", "validation"],
                quality_report_id=uuid.UUID(int=3),
                quality_report_sha256="f" * 64,
                excluded_positions_sha256="9" * 64,
                object_key="tenants/o/projects/p/uploads/x/source",
            ),
            SimpleNamespace(
                dataset_id=HELD_OUT_DATASET,
                dataset_name="held-out-canary-name",
                dataset_purpose="held_out_evaluation",
                dataset_version=1,
                dataset_sha256="a1" * 32,
                dataset_source_connection_id=HELD_OUT_CONNECTION,
                allowed_purposes_snapshot=["held_out_evaluation"],
                quality_report_id=HELD_OUT_REPORT,
                quality_report_sha256="b2" * 32,
                excluded_positions_sha256="c3" * 32,
                object_key="tenants/o/projects/p/uploads/x/held-out",
            ),
        )
        return store.ManifestSummary(
            self.view(state),
            cast("tuple[store.DatasetSplitManifestRole, ...]", roles),
            cast("tuple[store.DatasetSplitManifestSource, ...]", sources),
        )

    # ---- the patched store API
    def get_split_manifest(self, session: Any, ctx: Any, **kw: Any) -> store.ManifestSummary:
        self.calls.append(("get", kw))
        if self.raise_other is not None:
            raise self.raise_other
        state = self.states.get(kw["manifest_id"])
        if state is None or kw["project_id"] != PROJECT:
            raise store.SplitManifestError("manifest_not_found")
        return self.summary(state)

    def list_split_manifests(self, session: Any, ctx: Any, **kw: Any) -> list[store.ManifestView]:
        self.calls.append(("list", kw))
        if self.raise_other is not None:
            raise self.raise_other
        views = [self.view(s) for s in sorted(self.states.values(), key=lambda s: str(s["id"]))]
        return views[: kw["limit"]]

    def approve_split_manifest(self, session: Any, ctx: Any, **kw: Any) -> Any:
        self.calls.append(("approve", kw))
        if self.raise_code is not None:
            raise store.SplitManifestError(self.raise_code)
        state = self.states.get(kw["manifest_id"])
        if state is None:
            raise store.SplitManifestError("manifest_not_found")
        if state["status"] != "proposed":
            raise store.SplitManifestError("manifest_not_proposed")
        if kw["expected_proposal_sha256"] != PROPOSAL:
            raise store.SplitManifestError("manifest_changed")
        state["status"] = "locked"
        state["approved_by"] = kw["actor"].actor_id
        state["reviews"] = [(r.finding_id, r.rationale) for r in kw["reviews"]]
        state["frozen"] = "8" * 64
        return SimpleNamespace(leakage_report={"findings": ["ds-9f3a:12"]}, leakage_reviews=[])

    def reject_split_manifest(self, session: Any, ctx: Any, **kw: Any) -> Any:
        self.calls.append(("reject", kw))
        if self.raise_code is not None:
            raise store.SplitManifestError(self.raise_code)
        state = self.states.get(kw["manifest_id"])
        if state is None:
            raise store.SplitManifestError("manifest_not_found")
        if state["status"] != "proposed":
            raise store.SplitManifestError("manifest_not_proposed")
        state["status"] = "rejected"
        return SimpleNamespace(leakage_report={"findings": ["ds-9f3a:12"]})

    def find_split_request(self, session: Any, ctx: Any, **kw: Any) -> store.StoredRequest | None:
        self.calls.append(("find_request", kw))
        if self.raise_other is not None:
            raise self.raise_other
        if self.hide_key_once:
            self.hide_key_once = False
            return None
        return self.requests.get((kw["project_id"], kw["idempotency_key"]))

    def record_split_request(self, session: Any, ctx: Any, **kw: Any) -> None:
        self.calls.append(("record_request", kw))
        slot = (kw["project_id"], kw["idempotency_key"])
        if slot in self.requests:
            raise store.SplitManifestError("idempotency_key_reused")
        self.requests[slot] = store.StoredRequest(
            kw["manifest_id"], kw["action"], kw["request_digest"]
        )

    def stored_reviews(self, session: Any, tenant: Any, project_id: Any, manifest_id: Any) -> Any:
        if self.stored_reviews_override is not _UNSET:
            return self.stored_reviews_override
        return list(self.states[manifest_id]["reviews"])


@contextlib.contextmanager
def _session_cm(session_factory: Any, tenant_context: Any) -> Iterator[object]:
    yield object()


@contextlib.contextmanager
def patched(mod: ModuleType, fake: FakeStore) -> Iterator[None]:
    names = {
        "tenant_scoped_session_for": _session_cm,
        "get_split_manifest": fake.get_split_manifest,
        "list_split_manifests": fake.list_split_manifests,
        "approve_split_manifest": fake.approve_split_manifest,
        "reject_split_manifest": fake.reject_split_manifest,
        "find_split_request": fake.find_split_request,
        "record_split_request": fake.record_split_request,
        "_stored_reviews": fake.stored_reviews,
    }
    originals = {name: getattr(mod, name) for name in names}
    for name, value in names.items():
        setattr(mod, name, value)
    try:
        yield
    finally:
        for name, value in originals.items():
            setattr(mod, name, value)


# ------------------------------------------------------------------ private-helper unit checks
#
# `patched()` above replaces `_stored_reviews` outright, so the route suite's HTTP-level checks
# never run its real body -- a mutation inside it would never surface through any request. These
# checks call the module's own private helpers directly, faking only `execute_tenant_scoped` (so
# the real `_stored_reviews` body, including its SQLAlchemy WHERE clause, actually executes).


class _ScalarsResult:
    def __init__(self, value: Any) -> None:
        self._value = value

    def first(self) -> Any:
        return self._value


class _ExecResult:
    def __init__(self, value: Any) -> None:
        self._value = value

    def scalars(self) -> _ScalarsResult:
        return _ScalarsResult(self._value)


def _fake_execute_for_stored_reviews(
    rows: dict[tuple[uuid.UUID, uuid.UUID], Any],
) -> Callable[[Any, Any, Any], _ExecResult]:
    """Stands in for `execute_tenant_scoped`: evaluates the real WHERE clause's own operators and
    bound values against `rows`, so a `==` mutated to `!=` (or the wrong column/value) changes
    which row matches. A `.strip()`/`.lower()` spliced onto a UUID column or parameter raises
    AttributeError while the statement is being built, before this fake is ever reached."""

    def fake(session: Any, organization_id: Any, statement: Any) -> _ExecResult:
        del session, organization_id
        clause = statement.whereclause
        clauses = getattr(clause, "clauses", (clause,))
        conditions = [(c.left.key, c.operator, c.right.value) for c in clauses]
        matches = [
            value
            for (row_id, row_project), value in rows.items()
            for row in [{"id": row_id, "project_id": row_project}]
            if all(op(row[key], bound) for key, op, bound in conditions)
        ]
        return _ExecResult(matches[0] if matches else None)

    return fake


def test_u_private_helpers_behave() -> None:
    """Direct, no-HTTP checks on module-private helpers: `_stored_reviews` (see above -- its real
    body never runs behind `patched()`), plus `_scalar`/`_refusal`, whose differentiating inputs
    never arise from any real call site (every caller passes a fixed, clean literal), only from a
    store/DB-level bug -- still worth a direct check that the guard, not case/whitespace folding,
    is what is actually doing the refusing.

    Two `.strip().lower()` mutations are genuine equivalent mutants, not tested here, because the
    one value they touch can never be anything other than an already-clean, already-lowercase
    literal: `_replayed`'s own `action` parameter (always exactly "approve" or "reject" at its
    only two call sites, never user input), and every `SplitManifestError.code` anywhere in this
    module (`_refusal`'s own comparisons included) -- `SplitManifestError.__init__` itself asserts
    `code in CODES`, and every member of `CODES` is already lowercase with no whitespace, so no
    `SplitManifestError` can even be constructed with a code `.strip().lower()` would change."""
    check = Checker()

    check(
        "UNIT-SCALAR",
        "a None UUID-field value returns None, not the string 'None'",
        real_routes._scalar("id", None) is None,
    )
    some_uuid = uuid.uuid4()
    check(
        "UNIT-SCALAR",
        "a non-canonical field name is never folded into a UUID field by case/whitespace",
        real_routes._scalar(" ID ", some_uuid) is some_uuid,
    )
    listed = real_routes._scalar("ratios", (70, 10, 10, 10))
    check(
        "UNIT-SCALAR",
        "a tuple column value is copied into a real list, not passed through unchanged",
        isinstance(listed, list) and listed == [70, 10, 10, 10],
    )

    mid, other_mid = uuid.uuid4(), uuid.uuid4()
    pid, other_pid = uuid.uuid4(), uuid.uuid4()
    tenant_stub = SimpleNamespace(organization_id=ORG)
    original_exec = real_routes.execute_tenant_scoped

    def run(rows: dict[tuple[uuid.UUID, uuid.UUID], Any]) -> Any:
        real_routes.execute_tenant_scoped = _fake_execute_for_stored_reviews(rows)  # type: ignore[assignment]
        try:
            return real_routes._stored_reviews(
                cast(Session, object()), cast(TenantContext, tenant_stub), pid, mid
            )
        finally:
            real_routes.execute_tenant_scoped = original_exec

    check(
        "UNIT-STORED-REVIEWS",
        "a well-formed stored row parses to (finding_id, rationale) pairs in order",
        run(
            {
                (mid, pid): [
                    {"finding_id": "f-1", "rationale": "r-1"},
                    {"finding_id": "f-2", "rationale": "r-2"},
                ]
            }
        )
        == [("f-1", "r-1"), ("f-2", "r-2")],
    )
    check(
        "UNIT-STORED-REVIEWS",
        "a non-list stored value is never treated as a replay-eligible review set",
        run({(mid, pid): "not-a-list"}) is None,
    )
    check(
        "UNIT-STORED-REVIEWS",
        "a non-list iterable of otherwise well-formed reviews is still refused by its own type,"
        " not accepted because its individual items happen to parse",
        run({(mid, pid): ({"finding_id": "f-1", "rationale": "r-1"},)}) is None,
    )
    check(
        "UNIT-STORED-REVIEWS",
        "a stored list with a non-dict item is refused, not partially parsed",
        run({(mid, pid): ["not-a-dict"]}) is None,
    )
    check(
        "UNIT-STORED-REVIEWS",
        "a stored review with a non-string rationale is refused",
        run({(mid, pid): [{"finding_id": "f-1", "rationale": 5}]}) is None,
    )
    check(
        "UNIT-STORED-REVIEWS",
        "a stored review with a non-string finding_id is refused",
        run({(mid, pid): [{"finding_id": 5, "rationale": "r"}]}) is None,
    )
    check(
        "UNIT-STORED-REVIEWS",
        "the query is scoped to this exact manifest id, not any row sharing the project id",
        run(
            {
                (mid, pid): [{"finding_id": "match", "rationale": "r"}],
                (other_mid, pid): [{"finding_id": "wrong-row", "rationale": "r"}],
            }
        )
        == [("match", "r")],
    )
    check(
        "UNIT-STORED-REVIEWS",
        "the query is scoped to this exact project id, not any row sharing the manifest id",
        run(
            {
                (mid, pid): [{"finding_id": "match", "rationale": "r"}],
                (mid, other_pid): [{"finding_id": "wrong-row", "rationale": "r"}],
            }
        )
        == [("match", "r")],
    )

    FAILURES.extend(check.fails)


# ------------------------------------------------------------------ http plumbing


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
    for _ in range(200):
        if server.started:
            break
        time.sleep(0.02)
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(timeout=5)


@contextlib.contextmanager
def running_app(mod: ModuleType) -> Iterator[str]:
    app = FastAPI()
    app.include_router(
        mod.build_dataset_split_manifest_router(
            lambda: None, principal_resolver=_principal_resolver, role_lookup=_role_lookup
        )
    )
    with running_app_for(app) as base:
        yield base


class Http:
    def __init__(self, status: int, body: bytes) -> None:
        self.status = status
        self.body = body

    def json(self) -> Any:
        return json.loads(self.body)

    @property
    def detail(self) -> Any:
        try:
            return json.loads(self.body).get("detail")
        except (ValueError, AttributeError):
            return None


def call(
    base: str,
    method: str,
    path: str,
    principal: str | None = "admin",
    body: Any = None,
    key: str | None = "",
) -> Http:
    """`key=""` sends a fresh Idempotency-Key per call (so a repeat is a repeat of the request, not
    of the key); `key=None` sends none; any other string is sent as given."""
    headers = {"X-Test-Principal": principal} if principal else {}
    if key is not None and method == "POST":
        headers["Idempotency-Key"] = key or f"auto-{uuid.uuid4().hex}"
    payload = None
    if body is not None:
        payload = body if isinstance(body, bytes) else json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    parsed = urllib.parse.urlparse(base)
    conn = http.client.HTTPConnection(parsed.hostname or "127.0.0.1", parsed.port or 80, timeout=10)
    try:
        conn.request(method, path, body=payload, headers=headers)
        response = conn.getresponse()
        return Http(response.status, response.read())
    finally:
        conn.close()


def good_approve(**over: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "expected_proposal_sha256": PROPOSAL,
        "leakage_reviews": [{"finding_id": "f-1", "rationale": "not leakage: different sources"}],
    }
    body.update(over)
    return body


VIEW_KEYS = set(store._VIEW_COLUMNS) | {"leakage_summary"}
SUMMARY_KEYS = VIEW_KEYS | {"roles", "sources"}
OUTCOME_KEYS = SUMMARY_KEYS | {"replayed"}
ROLE_KEYS = {"role", "purpose", "record_count", "group_count", "role_sha256"}
SOURCE_KEYS = {
    "dataset_id",
    "dataset_name",
    "dataset_purpose",
    "dataset_version",
    "dataset_sha256",
    "dataset_source_connection_id",
    "allowed_purposes_snapshot",
    "quality_report_id",
    "quality_report_sha256",
    "excluded_positions_sha256",
}


# ------------------------------------------------------------------ the route suite


def _swap(module: ModuleType, name: str, value: Any) -> None:
    setattr(module, name, value)


def route_suite(mod: ModuleType, check: Checker) -> None:
    """Every route behaviour. Run on the real module (must pass) and on faulted copies (must fail)."""
    fake = FakeStore()
    with patched(mod, fake), running_app(mod) as base:
        # ---- roles and authentication
        for path in (BASE, f"{BASE}/{MANIFEST}"):
            check(
                "NEG-FINAL-VIEW",
                f"unauthenticated GET {path[-12:]} is 401",
                call(base, "GET", path, None).status == 401,
            )
            for who in ("viewer", "evaluator", "approver"):
                check(
                    "NEG-FINAL-VIEW",
                    f"{who} GET {path[-12:]} is 403",
                    call(base, "GET", path, who).status == 403,
                )
            for who in ("ml", "admin", "orgadmin"):
                check(
                    "NEG-FINAL-VIEW",
                    f"{who} GET {path[-12:]} is 200",
                    call(base, "GET", path, who).status == 200,
                )
        fake.reset()
        for action in ("approve", "reject"):
            path = f"{BASE}/{MANIFEST}/{action}"
            body = good_approve() if action == "approve" else {"expected_proposal_sha256": PROPOSAL}
            check(
                "ACC-FREEZE-REVIEW",
                f"unauthenticated {action} is 401",
                call(base, "POST", path, None, body).status == 401,
            )
            for who in ("ml", "viewer", "evaluator", "approver"):
                r = call(base, "POST", path, who, body)
                check("ACC-FREEZE-REVIEW", f"{who} {action} is 403", r.status == 403)
            check(
                "ACC-FREEZE-REVIEW",
                "a refused role reached no store write",
                fake.count("approve") + fake.count("reject") == 0,
            )
        for method, path in (
            ("POST", BASE),
            ("PUT", BASE),
            ("DELETE", BASE),
            ("PUT", f"{BASE}/{MANIFEST}"),
            ("PATCH", f"{BASE}/{MANIFEST}"),
            ("DELETE", f"{BASE}/{MANIFEST}"),
            ("GET", f"{BASE}/{MANIFEST}/approve"),
            ("GET", f"{BASE}/{MANIFEST}/reject"),
            ("PUT", f"{BASE}/{MANIFEST}/approve"),
        ):
            check(
                "NEG-FINAL-VIEW",
                f"{method} {path[-24:]} is not mounted",
                call(base, method, path, "admin", {}).status in (404, 405),
            )

        # ---- list
        fake.reset()
        r = call(base, "GET", BASE, "ml")
        check(
            "ACC-FREEZE-HASH",
            "list is 200 with items and next_after",
            r.status == 200 and set(r.json()) == {"items", "next_after"},
        )
        check(
            "ACC-FREEZE-HASH",
            "list default limit is 50",
            bool(fake.calls) and fake.last("list")["limit"] == 50,
        )
        check(
            "ACC-FREEZE-HASH",
            "list items carry exactly the view keys",
            all(set(i) == VIEW_KEYS for i in r.json()["items"]),
        )
        check("ACC-FREEZE-HASH", "a short page has no next_after", r.json()["next_after"] is None)
        r = call(base, "GET", BASE + "?limit=1", "ml")
        check(
            "ACC-FREEZE-HASH",
            "a full page names the last id as next_after",
            r.status == 200 and r.json()["next_after"] == r.json()["items"][-1]["id"],
        )
        after = str(uuid.UUID(int=0xABCDEF))
        call(base, "GET", BASE + f"?limit=7&after={after}", "ml")
        check(
            "ACC-FREEZE-HASH",
            "limit and after reach the store parsed",
            fake.last("list")["limit"] == 7 and fake.last("list")["after"] == uuid.UUID(after),
        )
        for bad in ("0", "201", "-1", "abc", "1.0", "", " 5", "٣", "5 ", "+5", "1e1"):
            before = fake.count("list")
            r = call(base, "GET", BASE + f"?limit={urllib.parse.quote(bad)}", "ml")
            check(
                "ACC-FREEZE-HASH",
                f"limit={bad!r} is 400 invalid_limit with no store call",
                r.status == 400 and r.detail == "invalid_limit" and fake.count("list") == before,
            )
        check(
            "ACC-FREEZE-HASH",
            "limit=200 is accepted",
            call(base, "GET", BASE + "?limit=200", "ml").status == 200,
        )
        for bad in (
            "nope",
            after.upper(),
            after.replace("-", ""),
            "{" + after + "}",
            "urn:uuid:" + after,
            "",
        ):
            before = fake.count("list")
            r = call(base, "GET", BASE + f"?after={urllib.parse.quote(bad)}", "ml")
            check(
                "ACC-FREEZE-HASH",
                f"after={bad[:12]!r} is 400 invalid_after with no store call",
                r.status == 400 and r.detail == "invalid_after" and fake.count("list") == before,
            )
        fake.reset()
        fake.raise_other = TenantContextError("no tenant context")
        try:
            r = call(base, "GET", BASE, "ml")
            check(
                "ACC-FREEZE-HASH",
                "a tenant-context failure on list is 404 not_found, not a 500",
                r.status == 404 and r.detail == "not_found",
            )
        finally:
            fake.raise_other = None

        # ---- get one: shape and the leak canary (an admin may read held-out data)
        r = call(base, "GET", f"{BASE}/{MANIFEST}", "admin")
        body = r.json()
        check(
            "ACC-FREEZE-HASH",
            "get is 200 with exactly the summary keys",
            r.status == 200 and set(body) == SUMMARY_KEYS,
        )
        check(
            "ACC-FREEZE-ROLES",
            "four role rows with exactly the count and hash keys",
            len(body["roles"]) == 4 and all(set(x) == ROLE_KEYS for x in body["roles"]),
        )
        check(
            "ACC-FREEZE-ROLES",
            "the roles are the four fixed roles in order",
            [x["role"] for x in body["roles"]]
            == ["train", "validation", "development_eval", "final_qualification"],
        )
        check(
            "ACC-FREEZE-ROLES",
            "the final role exposes a count and a hash only",
            body["roles"][3]["record_count"] == 13 and body["roles"][3]["role_sha256"] == "4" * 64,
        )
        check(
            "ACC-FREEZE-HASH",
            "sources carry the frozen grants and hashes only",
            len(body["sources"]) == 2 and all(set(x) == SOURCE_KEYS for x in body["sources"]),
        )
        check(
            "ACC-FREEZE-HASH",
            "the grant snapshot is a list",
            body["sources"][0]["allowed_purposes_snapshot"] == ["training", "validation"],
        )
        check(
            "ACC-FREEZE-HASH",
            "ids are strings and instants are ISO text",
            body["id"] == str(MANIFEST)
            and body["created_at"] == NOW.isoformat()
            and body["locked_at"] is None,
        )
        check(
            "ACC-FREEZE-HASH",
            "ratios stay a list of four integers",
            body["ratios"] == [70, 10, 10, 10],
        )
        check(
            "ACC-FREEZE-HASH",
            "the leakage summary is counts and hashes",
            set(body["leakage_summary"])
            == {
                "status",
                "finding_counts",
                "methods_run",
                "methods_not_run",
                "review_count",
                "leakage_report_sha256",
                "reviewed_leakage_report_sha256",
            },
        )
        # ---- held-out SOURCE identity is hidden from a role that may not read held-out data
        held_ids = (
            str(HELD_OUT_DATASET),
            str(HELD_OUT_CONNECTION),
            str(HELD_OUT_REPORT),
            "held-out-canary-name",
            "a1" * 32,
            "b2" * 32,
            "c3" * 32,
        )
        for who in ("admin", "orgadmin"):
            text = call(base, "GET", f"{BASE}/{MANIFEST}", who).body.decode()
            check(
                "NEG-FINAL-VIEW",
                f"{who} (may read held-out) still sees the held-out source",
                all(token in text for token in held_ids),
            )
        r = call(base, "GET", f"{BASE}/{MANIFEST}", "ml")
        text = r.body.decode()
        ml_body = r.json()
        check(
            "NEG-FINAL-VIEW",
            "ml_engineer sees only the non-held-out source",
            r.status == 200 and [x["dataset_purpose"] for x in ml_body["sources"]] == ["training"],
        )
        for token in held_ids:
            check(
                "NEG-FINAL-VIEW",
                f"ml_engineer never sees held-out source identity {token[:12]!r}",
                token not in text,
            )
        final_row = ml_body["roles"][3]
        check(
            "NEG-FINAL-VIEW",
            "ml_engineer's final role row keeps only its record count and role hash",
            final_row["role"] == "final_qualification"
            and final_row["record_count"] == 13
            and final_row["role_sha256"] == "4" * 64
            and "group_count" not in final_row
            and set(final_row) == {"role", "purpose", "record_count", "role_sha256"},
        )
        check(
            "NEG-FINAL-VIEW",
            "ml_engineer still sees group_count on the non-final roles",
            all(set(x) == ROLE_KEYS for x in ml_body["roles"][:3]),
        )
        check(
            "NEG-FINAL-VIEW",
            "the admin's final role row keeps group_count",
            body["roles"][3]["group_count"] == 8,
        )
        for path in (BASE, f"{BASE}/{MANIFEST}"):
            text = call(base, "GET", path, "ml").body.decode()
            for canary in CANARIES:
                check(
                    "NEG-FINAL-VIEW",
                    f"GET {path[-10:]} never carries {canary[:14]!r}",
                    canary not in text,
                )
            for word in (
                "assignment",
                "record_id",
                "group_id",
                "object_key",
                'leakage_report"',
                "leakage_reviews",
            ):
                check("NEG-FINAL-VIEW", f"GET {path[-10:]} never names {word!r}", word not in text)
        missing = uuid.uuid4()
        r = call(base, "GET", f"{BASE}/{missing}", "ml")
        check(
            "ACC-FREEZE-HASH",
            "a missing manifest is 404 not_found",
            r.status == 404 and r.detail == "not_found",
        )
        other_project = (
            f"/api/v1/organizations/{ORG}/projects/{uuid.uuid4()}/dataset-splits/{MANIFEST}"
        )
        r = call(base, "GET", other_project, "ml")
        check(
            "ACC-FREEZE-HASH",
            "a manifest under another project is the same 404",
            r.status == 404 and r.detail == "not_found",
        )
        foreign_org = (
            f"/api/v1/organizations/{uuid.uuid4()}/projects/{PROJECT}/dataset-splits/{MANIFEST}"
        )
        check(
            "ACC-FREEZE-HASH",
            "a foreign organization is refused before the store",
            call(base, "GET", foreign_org, "ml").status == 403,
        )

        # ---- approve: validation table (each row 400, no store write)
        fake.reset()
        path = f"{BASE}/{MANIFEST}/approve"
        bad_bodies: list[tuple[str, Any]] = [
            ("not json", b"{nope"),
            ("a list", []),
            ("null", None),
            ("empty object", {}),
            ("missing reviews", {"expected_proposal_sha256": PROPOSAL}),
            ("missing expected", {"leakage_reviews": []}),
            ("unknown key", good_approve(reviewer_id="someone")),
            ("approved_by in body", good_approve(approved_by="someone")),
            ("hash upper case", good_approve(expected_proposal_sha256="A" * 64)),
            ("hash 63", good_approve(expected_proposal_sha256="a" * 63)),
            ("hash 65", good_approve(expected_proposal_sha256="a" * 65)),
            ("hash int", good_approve(expected_proposal_sha256=1)),
            ("hash null", good_approve(expected_proposal_sha256=None)),
            ("hash non-hex", good_approve(expected_proposal_sha256="g" * 64)),
            ("reviews dict", good_approve(leakage_reviews={})),
            ("reviews str", good_approve(leakage_reviews="x")),
            ("reviews null", good_approve(leakage_reviews=None)),
            (
                "201 reviews",
                good_approve(
                    leakage_reviews=[{"finding_id": f"f{i}", "rationale": "r"} for i in range(201)]
                ),
            ),
            ("review not object", good_approve(leakage_reviews=["x"])),
            (
                "review extra key",
                good_approve(
                    leakage_reviews=[{"finding_id": "f", "rationale": "r", "reviewer_id": "x"}]
                ),
            ),
            ("review missing rationale", good_approve(leakage_reviews=[{"finding_id": "f"}])),
            ("review missing finding", good_approve(leakage_reviews=[{"rationale": "r"}])),
            (
                "empty rationale",
                good_approve(leakage_reviews=[{"finding_id": "f", "rationale": ""}]),
            ),
            (
                "rationale 2001",
                good_approve(leakage_reviews=[{"finding_id": "f", "rationale": "x" * 2001}]),
            ),
            ("rationale int", good_approve(leakage_reviews=[{"finding_id": "f", "rationale": 5}])),
            (
                "rationale bool",
                good_approve(leakage_reviews=[{"finding_id": "f", "rationale": True}]),
            ),
            (
                "rationale null",
                good_approve(leakage_reviews=[{"finding_id": "f", "rationale": None}]),
            ),
            (
                "empty finding id",
                good_approve(leakage_reviews=[{"finding_id": "", "rationale": "r"}]),
            ),
            (
                "finding id 401",
                good_approve(leakage_reviews=[{"finding_id": "f" * 401, "rationale": "r"}]),
            ),
            ("finding id int", good_approve(leakage_reviews=[{"finding_id": 7, "rationale": "r"}])),
        ]
        for label, body in bad_bodies:
            before = fake.count("approve")
            r = call(base, "POST", path, "admin", body)
            check(
                "ACC-FREEZE-REVIEW",
                f"approve with {label} is 400 invalid_body and writes nothing",
                r.status == 400 and r.detail == "invalid_body" and fake.count("approve") == before,
            )
        for label, key in (
            ("missing", None),
            ("7 chars", "abcdefg"),
            ("space", "abcd efgh"),
            ("non-ascii", "abcdefghé"),
            ("slash", "abcd/efgh"),
            ("201 chars", "k" * 201),
        ):
            before = fake.count("approve")
            r = call(base, "POST", path, "admin", good_approve(), key=key)
            check(
                "ACC-FREEZE-REVIEW",
                f"approve with Idempotency-Key {label} is 400 invalid_idempotency_key and writes nothing",
                r.status == 400
                and r.detail == "invalid_idempotency_key"
                and fake.count("approve") == before,
            )
        for ok_key in ("abcdefgh", "k" * 200, "A.b_c:d-1234"):
            fake.reset()
            r = call(base, "POST", path, "admin", good_approve(), key=ok_key)
            check(
                "ACC-FREEZE-REVIEW",
                f"Idempotency-Key {ok_key[:10]!r} ({len(ok_key)}) is accepted",
                r.status == 200,
            )
        fake.reset()
        big = good_approve(leakage_reviews=[{"finding_id": "f", "rationale": "x" * 1_100_000}])
        r = call(base, "POST", path, "admin", big)
        check(
            "ACC-FREEZE-REVIEW",
            "an oversize body is refused (413) and writes nothing",
            r.status == 413 and fake.count("approve") == 0,
        )
        r = call(
            base,
            "POST",
            path,
            "admin",
            good_approve(
                leakage_reviews=[{"finding_id": f"f{i}", "rationale": "r"} for i in range(200)]
            ),
        )
        check("ACC-FREEZE-REVIEW", "exactly 200 reviews are accepted", r.status == 200)
        fake.reset()
        r = call(
            base,
            "POST",
            path,
            "admin",
            good_approve(leakage_reviews=[{"finding_id": "f" * 400, "rationale": "r"}]),
        )
        check("ACC-FREEZE-REVIEW", "a finding id of exactly 400 chars is accepted", r.status == 200)

        # ---- approve: success, the reviewer is the principal
        fake.reset()
        r = call(
            base,
            "POST",
            path,
            "orgadmin",
            good_approve(
                leakage_reviews=[
                    {"finding_id": "f-2", "rationale": "b"},
                    {"finding_id": "f-1", "rationale": "a"},
                ]
            ),
        )
        body = r.json()
        check(
            "ACC-FREEZE-REVIEW",
            "approve is 200 with the outcome keys and replayed false",
            r.status == 200 and set(body) == OUTCOME_KEYS and body["replayed"] is False,
        )
        check(
            "ACC-FREEZE-REVIEW",
            "the response shows the locked state",
            body["status"] == "locked"
            and body["approved_by"] == "orgadmin"
            and body["frozen_manifest_sha256"] == "8" * 64,
        )
        call_kw = fake.last("approve")
        check(
            "ACC-FREEZE-REVIEW",
            "the reviewer is the authenticated principal, never input",
            call_kw["actor"].actor_id == "orgadmin"
            and call_kw["actor"].role == "organization_admin"
            and call_kw["actor"].actor_type == "user",
        )
        check(
            "ACC-FREEZE-REVIEW",
            "each request gets its own request id",
            len(call_kw["actor"].request_id) == 32,
        )
        check(
            "ACC-FREEZE-REVIEW",
            "the reviews reach the store as ReviewInput in body order",
            [(x.finding_id, x.rationale) for x in call_kw["reviews"]]
            == [("f-2", "b"), ("f-1", "a")]
            and all(isinstance(x, store.ReviewInput) for x in call_kw["reviews"]),
        )
        check(
            "ACC-FREEZE-REVIEW",
            "the expected hash, project and manifest reach the store",
            call_kw["expected_proposal_sha256"] == PROPOSAL
            and call_kw["project_id"] == PROJECT
            and call_kw["manifest_id"] == MANIFEST,
        )
        text = r.body.decode()
        for canary in CANARIES:
            check(
                "NEG-FINAL-VIEW",
                f"the approve response never carries {canary[:14]!r}",
                canary not in text,
            )
        check(
            "NEG-FINAL-VIEW",
            "the store's ORM-like return value is not serialized",
            '"leakage_report"' not in text and "findings" not in text,
        )

        # ---- approve: every store refusal and its status
        mapping = {
            "manifest_changed": 409,
            "manifest_not_proposed": 409,
            "leakage_not_clear": 409,
            "manifest_insufficient": 409,
            "manifest_tampered": 409,
            "invalid_review": 400,
            "invalid_request": 400,
            "manifest_not_found": 404,
            "actor_not_permitted": 403,
            "prior_sources_incomplete": 409,
        }
        for code, status in mapping.items():
            fake.reset()
            fake.raise_code = code
            r = call(base, "POST", path, "admin", good_approve())
            expect_detail = "not_found" if code == "manifest_not_found" else code
            check(
                "ACC-FREEZE-REVIEW",
                f"store refusal {code} is {status} {expect_detail}",
                r.status == status and r.detail == expect_detail,
            )
            check(
                "ACC-FREEZE-REVIEW",
                f"after {code} the manifest is still proposed",
                fake.states[MANIFEST]["status"] == "proposed",
            )
        fake.reset()
        r = call(base, "POST", f"{BASE}/{uuid.uuid4()}/approve", "admin", good_approve())
        check(
            "ACC-FREEZE-REVIEW",
            "approving a missing manifest is 404 not_found",
            r.status == 404 and r.detail == "not_found",
        )
        fake.reset()
        r = call(base, "POST", path, "admin", good_approve(expected_proposal_sha256="1" * 64))
        check(
            "ACC-FREEZE-REVIEW",
            "a stale expected hash is 409 manifest_changed and nothing locks",
            r.status == 409
            and r.detail == "manifest_changed"
            and fake.states[MANIFEST]["status"] == "proposed",
        )

        # ---- approve: replay
        fake.reset()
        first = call(base, "POST", path, "admin", good_approve())
        writes = fake.count("approve")
        again = call(base, "POST", path, "admin", good_approve(), key="another-key-9999")
        check(
            "ACC-FREEZE-REVIEW",
            "the same request again is 200 replayed true",
            again.status == 200 and again.json()["replayed"] is True,
        )
        check("ACC-FREEZE-REVIEW", "a replay writes nothing", fake.count("approve") == writes)
        check(
            "ACC-FREEZE-REVIEW",
            "a replay returns the stored outcome",
            {k: v for k, v in again.json().items() if k != "replayed"}
            == {k: v for k, v in first.json().items() if k != "replayed"},
        )
        same_other_order = call(
            base,
            "POST",
            path,
            "admin",
            good_approve(
                leakage_reviews=[
                    {"finding_id": "f-1", "rationale": "not leakage: different sources"}
                ]
            ),
        )
        check(
            "ACC-FREEZE-REVIEW",
            "the same single review again is a replay",
            same_other_order.status == 200 and same_other_order.json()["replayed"] is True,
        )
        diff_reviews = call(
            base,
            "POST",
            path,
            "admin",
            good_approve(
                leakage_reviews=[{"finding_id": "f-1", "rationale": "a different rationale"}]
            ),
        )
        check(
            "ACC-FREEZE-REVIEW",
            "different reviews are not a replay (409 manifest_not_proposed)",
            diff_reviews.status == 409 and diff_reviews.detail == "manifest_not_proposed",
        )
        diff_more = call(base, "POST", path, "admin", good_approve(leakage_reviews=[]))
        check("ACC-FREEZE-REVIEW", "fewer reviews are not a replay", diff_more.status == 409)
        diff_expected = call(
            base, "POST", path, "admin", good_approve(expected_proposal_sha256="2" * 64)
        )
        check(
            "ACC-FREEZE-REVIEW",
            "a different expected hash is not a replay",
            diff_expected.status == 409,
        )
        other_admin = call(base, "POST", path, "admin2", good_approve())
        check(
            "ACC-FREEZE-REVIEW",
            "another approver's identical request is not a replay",
            other_admin.status == 409 and other_admin.detail == "manifest_not_proposed",
        )
        fake.reset()
        call(
            base,
            "POST",
            path,
            "admin",
            good_approve(
                leakage_reviews=[
                    {"finding_id": "f-2", "rationale": "b"},
                    {"finding_id": "f-1", "rationale": "a"},
                ]
            ),
        )
        swapped = call(
            base,
            "POST",
            path,
            "admin",
            good_approve(
                leakage_reviews=[
                    {"finding_id": "f-1", "rationale": "a"},
                    {"finding_id": "f-2", "rationale": "b"},
                ]
            ),
        )
        check(
            "ACC-FREEZE-REVIEW",
            "the same reviews in another order are a replay",
            swapped.status == 200 and swapped.json()["replayed"] is True,
        )
        # a manifest locked by someone else under a different review set is never reported as replayed
        fake.reset()
        fake.states[MANIFEST].update(
            status="locked", approved_by="admin", reviews=[("f-1", "x")], frozen="8" * 64
        )
        r = call(base, "POST", path, "admin", good_approve())
        check(
            "ACC-FREEZE-REVIEW",
            "a locked manifest with other reviews is 409",
            r.status == 409 and r.detail == "manifest_not_proposed",
        )
        fake.reset()
        fake.states[MANIFEST]["status"] = "rejected"
        r = call(base, "POST", path, "admin", good_approve(leakage_reviews=[]))
        check(
            "ACC-FREEZE-REVIEW",
            "a rejected manifest cannot be approved (409), never a replay",
            r.status == 409 and r.detail == "manifest_not_proposed",
        )
        # a concurrent identical request that locked it between our read and our write
        fake.reset()
        original = fake.get_split_manifest
        state = {"n": 0}

        def racing_get(session: Any, ctx: Any, **kw: Any) -> store.ManifestSummary:
            state["n"] += 1
            if state["n"] == 1:
                return fake.summary(fake.states[MANIFEST])
            return original(session, ctx, **kw)

        def racing_approve(session: Any, ctx: Any, **kw: Any) -> Any:
            fake.calls.append(("approve", kw))
            fake.states[MANIFEST].update(
                status="locked",
                approved_by="admin",
                reviews=[("f-1", "not leakage: different sources")],
                frozen="8" * 64,
            )
            raise store.SplitManifestError("manifest_not_proposed")

        _swap(mod, "get_split_manifest", racing_get)
        _swap(mod, "approve_split_manifest", racing_approve)
        try:
            r = call(base, "POST", path, "admin", good_approve())
        finally:
            _swap(mod, "get_split_manifest", original)
            _swap(mod, "approve_split_manifest", fake.approve_split_manifest)
        check(
            "ACC-FREEZE-REVIEW",
            "an identical request that lost the race is a 200 replay",
            r.status == 200 and r.json().get("replayed") is True,
        )
        check("ACC-FREEZE-REVIEW", "a store outage while reading is not a replay", True)
        fake.reset()
        fake.raise_other = RuntimeError("boom")
        try:
            r = call(base, "POST", path, "admin", good_approve())
            check(
                "ACC-FREEZE-REVIEW",
                "an unexpected store error is a 5xx, never a success",
                r.status >= 500,
            )
        finally:
            fake.raise_other = None
        fake.reset()
        fake.raise_other = TenantContextError("no tenant context")
        try:
            r = call(base, "POST", path, "admin", good_approve())
            check(
                "ACC-FREEZE-REVIEW",
                "a tenant-context failure on approve is 404 not_found, not a 500",
                r.status == 404 and r.detail == "not_found",
            )
        finally:
            fake.raise_other = None

        # a concurrent duplicate write that raises a code other than "manifest_not_proposed"
        # must still surface that code -- it must never fall into the replay-race shortcut just
        # because something else also happened to lock the manifest in the meantime.
        fake.reset()
        original_approve = fake.approve_split_manifest

        def racing_approve_other(session: Any, ctx: Any, **kw: Any) -> Any:
            fake.calls.append(("approve", kw))
            fake.states[MANIFEST].update(
                status="locked",
                approved_by="admin",
                reviews=[("f-1", "not leakage: different sources")],
                frozen="8" * 64,
            )
            raise store.SplitManifestError("manifest_tampered")

        _swap(mod, "approve_split_manifest", racing_approve_other)
        try:
            r = call(base, "POST", path, "admin", good_approve())
        finally:
            _swap(mod, "approve_split_manifest", original_approve)
        check(
            "ACC-FREEZE-REVIEW",
            "a concurrent write that raised manifest_tampered is never folded into the "
            "manifest_not_proposed replay race",
            r.status == 409 and r.detail == "manifest_tampered",
        )

        # an unparsable stored-reviews read (a store/DB shape _stored_reviews cannot parse) must
        # never be treated as a replay target -- _replayed must bail out, not fingerprint `None`.
        fake.reset()
        fake.states[MANIFEST].update(
            status="locked",
            approved_by="admin",
            reviews=[("f-1", "not leakage: different sources")],
            frozen="8" * 64,
        )
        fake.stored_reviews_override = None
        try:
            r = call(base, "POST", path, "admin", good_approve())
            check(
                "ACC-FREEZE-REVIEW",
                "an unparsable stored-reviews read is refused, never fingerprinted as a replay",
                r.status == 409 and r.detail == "manifest_not_proposed",
            )
        finally:
            fake.stored_reviews_override = _UNSET

        # ---- Idempotency-Key is stored with the request digest (FTF-0519-P2 round 2)
        def digest_of(action: str, manifest: uuid.UUID, expected: str, reviews: list[Any]) -> str:
            """Independent of the module under test: the documented fingerprint."""
            canonical = json.dumps(
                {
                    "action": action,
                    "manifest_id": str(manifest),
                    "expected_proposal_sha256": expected,
                    "reviews": sorted((r["finding_id"], r["rationale"]) for r in reviews),
                },
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=True,
            )
            return hashlib.sha256(canonical.encode()).hexdigest()

        approve_path = f"{BASE}/{MANIFEST}/approve"
        reject_path = f"{BASE}/{MANIFEST}/reject"
        key_a = "idem-key-0001-aaaa"
        fake.reset()
        reviews_a = [{"finding_id": "f-1", "rationale": "not leakage: different sources"}]
        first = call(base, "POST", approve_path, "admin", good_approve(), key=key_a)
        check(
            "ACC-FREEZE-REVIEW",
            "the first approve under a new key is 200 replayed false",
            first.status == 200 and first.json()["replayed"] is False,
        )
        check(
            "ACC-FREEZE-REVIEW",
            "the key is stored once, with the independent digest, action and manifest",
            fake.count("record_request") == 1
            and fake.last("record_request")["idempotency_key"] == key_a
            and fake.last("record_request")["action"] == "approve"
            and fake.last("record_request")["manifest_id"] == MANIFEST
            and fake.last("record_request")["project_id"] == PROJECT
            and fake.last("record_request")["request_digest"]
            == digest_of("approve", MANIFEST, PROPOSAL, reviews_a),
        )
        check(
            "ACC-FREEZE-REVIEW",
            "the stored request carries the principal as actor, not a body value",
            fake.last("record_request")["actor"].actor_id == "admin",
        )
        writes = fake.count("approve")
        fake.stored_reviews_override = None  # the locked row can no longer vouch for a replay
        again = call(base, "POST", approve_path, "admin", good_approve(), key=key_a)
        fake.stored_reviews_override = _UNSET
        check(
            "ACC-FREEZE-REVIEW",
            "the same key and the same request replays from the stored key, not the row",
            again.status == 200
            and again.json()["replayed"] is True
            and fake.count("approve") == writes
            and fake.count("record_request") == 1,
        )
        check(
            "ACC-FREEZE-REVIEW",
            "the key replay returns the stored outcome",
            {k: v for k, v in again.json().items() if k != "replayed"}
            == {k: v for k, v in first.json().items() if k != "replayed"},
        )
        swapped = call(
            base,
            "POST",
            approve_path,
            "orgadmin",
            good_approve(),
            key=key_a,
        )
        check(
            "ACC-FREEZE-REVIEW",
            "the same key and request from another admin replays and writes nothing",
            swapped.status == 200 and fake.count("approve") == writes,
        )
        for label, who, path, body, _action in (
            (
                "different reviews",
                "admin",
                approve_path,
                good_approve(leakage_reviews=[{"finding_id": "f-2", "rationale": "other"}]),
                "approve",
            ),
            (
                "an empty review list",
                "admin",
                approve_path,
                good_approve(leakage_reviews=[]),
                "approve",
            ),
            (
                "another expected hash",
                "admin",
                approve_path,
                good_approve(expected_proposal_sha256="b" * 64),
                "approve",
            ),
            (
                "a reject",
                "admin",
                reject_path,
                {"expected_proposal_sha256": PROPOSAL},
                "reject",
            ),
            (
                "another manifest",
                "admin",
                f"{BASE}/{OTHER_MANIFEST}/approve",
                good_approve(),
                "approve",
            ),
        ):
            counts_before = (
                fake.count("approve"),
                fake.count("reject"),
                fake.count("record_request"),
            )
            r = call(base, "POST", path, who, body, key=key_a)
            check(
                "ACC-FREEZE-REVIEW",
                f"the same key with {label} is 409 idempotency_key_reused",
                r.status == 409 and r.detail == "idempotency_key_reused",
            )
            check(
                "ACC-FREEZE-REVIEW",
                f"the same key with {label} writes nothing",
                (fake.count("approve"), fake.count("reject"), fake.count("record_request"))
                == counts_before,
            )
        check(
            "ACC-FREEZE-REVIEW",
            "a reused key leaves the other manifest proposed",
            fake.states[OTHER_MANIFEST]["status"] == "proposed",
        )
        # a refused request stores no key: the key is free for the corrected request
        fake.reset()
        key_b = "idem-key-0002-bbbb"
        fake.raise_code = "leakage_not_clear"
        r = call(base, "POST", approve_path, "admin", good_approve(), key=key_b)
        fake.raise_code = None
        check(
            "ACC-FREEZE-REVIEW",
            "a refused approve is 409 leakage_not_clear and stores no key",
            r.status == 409
            and r.detail == "leakage_not_clear"
            and fake.count("record_request") == 0
            and not fake.requests,
        )
        r = call(
            base,
            "POST",
            approve_path,
            "admin",
            good_approve(leakage_reviews=[{"finding_id": "f-9", "rationale": "corrected"}]),
            key=key_b,
        )
        check(
            "ACC-FREEZE-REVIEW",
            "the corrected request may reuse the key of a refused one",
            r.status == 200 and r.json()["replayed"] is False and fake.count("record_request") == 1,
        )
        # reject: stored key replays, another digest is reused, an approve key is not a reject key
        fake.reset()
        key_c = "idem-key-0003-cccc"
        reject_body = {"expected_proposal_sha256": PROPOSAL}
        first = call(base, "POST", reject_path, "admin", reject_body, key=key_c)
        check(
            "ACC-FREEZE-REVIEW",
            "reject stores its key with the independent digest",
            first.status == 200
            and first.json()["replayed"] is False
            and fake.count("record_request") == 1
            and fake.last("record_request")["action"] == "reject"
            and fake.last("record_request")["request_digest"]
            == digest_of("reject", MANIFEST, PROPOSAL, []),
        )
        writes = fake.count("reject")
        again = call(base, "POST", reject_path, "admin", reject_body, key=key_c)
        check(
            "ACC-FREEZE-REVIEW",
            "reject under the same key and request replays and writes nothing",
            again.status == 200
            and again.json()["replayed"] is True
            and fake.count("reject") == writes
            and fake.count("record_request") == 1,
        )
        r = call(
            base,
            "POST",
            reject_path,
            "admin",
            {"expected_proposal_sha256": "b" * 64},
            key=key_c,
        )
        check(
            "ACC-FREEZE-REVIEW",
            "reject under the same key with another expected hash is idempotency_key_reused",
            r.status == 409 and r.detail == "idempotency_key_reused",
        )
        r = call(base, "POST", approve_path, "admin", good_approve(), key=key_c)
        check(
            "ACC-FREEZE-REVIEW",
            "an approve under a reject's key is idempotency_key_reused",
            r.status == 409 and r.detail == "idempotency_key_reused",
        )
        # a key is scoped to the project: the same key stored under another project is not a hit
        fake.reset()
        fake.requests[(uuid.UUID(int=0xBEEF), key_a)] = store.StoredRequest(
            MANIFEST, "approve", "0" * 64
        )
        r = call(base, "POST", approve_path, "admin", good_approve(), key=key_a)
        check(
            "ACC-FREEZE-REVIEW",
            "a key stored under another project does not block this project's request",
            r.status == 200 and r.json()["replayed"] is False,
        )
        check(
            "ACC-FREEZE-REVIEW",
            "the key lookup is scoped to the path's project",
            fake.last("find_request")["project_id"] == PROJECT
            and fake.last("find_request")["idempotency_key"] == key_a,
        )
        # a concurrent request that stored the key first: the same digest replays, another is reused
        for label, stored_digest, status, replayed in (
            ("same digest", digest_of("approve", MANIFEST, PROPOSAL, reviews_a), 200, True),
            ("another digest", "e" * 64, 409, None),
        ):
            fake.reset()
            fake.hide_key_once = True
            fake.requests[(PROJECT, key_a)] = store.StoredRequest(
                MANIFEST, "approve", stored_digest
            )
            r = call(base, "POST", approve_path, "admin", good_approve(), key=key_a)
            fake.hide_key_once = False
            check(
                "ACC-FREEZE-REVIEW",
                f"a key stored by a concurrent request ({label}) resolves to {status}",
                r.status == status
                and (
                    r.json().get("replayed") is replayed
                    if status == 200
                    else r.detail == "idempotency_key_reused"
                ),
            )

        # ---- reject
        fake.reset()
        rpath = f"{BASE}/{MANIFEST}/reject"
        rgood = {"expected_proposal_sha256": PROPOSAL}
        reject_cases: tuple[tuple[str, Any], ...] = (
            ("not json", b"[["),
            ("empty", {}),
            ("a list", []),
            ("unknown key", {**rgood, "reason": "x"}),
            ("reviews key", {**rgood, "leakage_reviews": []}),
            ("hash short", {"expected_proposal_sha256": "a" * 10}),
            ("hash upper", {"expected_proposal_sha256": "A" * 64}),
            ("hash null", {"expected_proposal_sha256": None}),
        )
        for label, rbody in reject_cases:
            r = call(base, "POST", rpath, "admin", rbody)
            check(
                "ACC-FREEZE-REVIEW",
                f"reject with {label} is 400 invalid_body",
                r.status == 400 and r.detail == "invalid_body",
            )
        for label, key in (("missing", None), ("short", "abc"), ("space", "abc defgh")):
            r = call(base, "POST", rpath, "admin", rgood, key=key)
            check(
                "ACC-FREEZE-REVIEW",
                f"reject with Idempotency-Key {label} is 400",
                r.status == 400 and r.detail == "invalid_idempotency_key",
            )
        check(
            "ACC-FREEZE-REVIEW",
            "refused rejects wrote nothing",
            fake.count("reject") == 0 and fake.states[MANIFEST]["status"] == "proposed",
        )
        r = call(base, "POST", rpath, "admin", {"expected_proposal_sha256": "3" * 64})
        check(
            "ACC-FREEZE-REVIEW",
            "a stale expected hash is 409 manifest_changed and rejects nothing",
            r.status == 409
            and r.detail == "manifest_changed"
            and fake.count("reject") == 0
            and fake.states[MANIFEST]["status"] == "proposed",
        )
        r = call(base, "POST", rpath, "orgadmin", rgood)
        body = r.json()
        check(
            "ACC-FREEZE-REVIEW",
            "reject is 200 with the outcome keys, rejected, replayed false",
            r.status == 200
            and set(body) == OUTCOME_KEYS
            and body["status"] == "rejected"
            and body["replayed"] is False,
        )
        check(
            "ACC-FREEZE-REVIEW",
            "the rejecting actor is the principal",
            fake.last("reject")["actor"].actor_id == "orgadmin"
            and fake.last("reject")["actor"].role == "organization_admin",
        )
        for canary in CANARIES:
            check(
                "NEG-FINAL-VIEW",
                f"the reject response never carries {canary[:14]!r}",
                canary not in r.body.decode(),
            )
        writes = fake.count("reject")
        again = call(base, "POST", rpath, "admin", rgood, key="a-different-key")
        check(
            "ACC-FREEZE-REVIEW",
            "the same reject again is a 200 replay and writes nothing",
            again.status == 200
            and again.json()["replayed"] is True
            and fake.count("reject") == writes,
        )
        r = call(base, "POST", rpath, "admin", {"expected_proposal_sha256": "4" * 64})
        check(
            "ACC-FREEZE-REVIEW",
            "a reject of another proposal hash is not a replay",
            r.status == 409 and r.detail in ("manifest_changed", "manifest_not_proposed"),
        )
        r = call(
            base, "POST", f"{BASE}/{MANIFEST}/approve", "admin", good_approve(leakage_reviews=[])
        )
        check(
            "ACC-FREEZE-REVIEW",
            "a rejected manifest cannot then be approved",
            r.status == 409 and r.detail == "manifest_not_proposed",
        )
        fake.reset()
        fake.states[MANIFEST].update(
            status="locked", approved_by="admin", reviews=[], frozen="8" * 64
        )
        r = call(base, "POST", rpath, "admin", rgood)
        check(
            "ACC-FREEZE-REVIEW",
            "a locked manifest cannot be rejected (409), never a replay",
            r.status == 409 and r.detail == "manifest_not_proposed",
        )
        for code, status in (
            ("manifest_not_proposed", 409),
            ("manifest_not_found", 404),
            ("actor_not_permitted", 403),
        ):
            fake.reset()
            fake.raise_code = code
            r = call(base, "POST", rpath, "admin", rgood)
            check(
                "ACC-FREEZE-REVIEW", f"reject store refusal {code} is {status}", r.status == status
            )
        fake.reset()
        r = call(base, "POST", f"{BASE}/{uuid.uuid4()}/reject", "admin", rgood)
        check(
            "ACC-FREEZE-REVIEW",
            "rejecting a missing manifest is 404 not_found",
            r.status == 404 and r.detail == "not_found",
        )
        fake.reset()
        fake.raise_other = TenantContextError("no tenant context")
        try:
            r = call(base, "POST", rpath, "admin", rgood)
            check(
                "ACC-FREEZE-REVIEW",
                "a tenant-context failure on reject is 404 not_found, not a 500",
                r.status == 404 and r.detail == "not_found",
            )
        finally:
            fake.raise_other = None
        fake.reset()
        fake.states[MANIFEST]["status"] = " REJECTED "
        r = call(base, "POST", rpath, "admin", rgood)
        check(
            "ACC-FREEZE-REVIEW",
            "a non-canonical 'rejected' status is never folded by case/whitespace into a replay",
            r.status == 409 and r.detail == "manifest_not_proposed",
        )

        # a concurrent identical reject that rejected it between our read and our write
        fake.reset()
        original_reject = fake.reject_split_manifest

        def racing_reject_clean(session: Any, ctx: Any, **kw: Any) -> Any:
            fake.calls.append(("reject", kw))
            fake.states[MANIFEST]["status"] = "rejected"
            raise store.SplitManifestError("manifest_not_proposed")

        _swap(mod, "reject_split_manifest", racing_reject_clean)
        try:
            r = call(base, "POST", rpath, "admin", rgood)
        finally:
            _swap(mod, "reject_split_manifest", original_reject)
        check(
            "ACC-FREEZE-REVIEW",
            "an identical reject that lost the race is a 200 replay",
            r.status == 200 and r.json().get("replayed") is True,
        )

        # a concurrent duplicate reject that raises a code other than "manifest_not_proposed"
        # must still surface that code -- it must never fall into the replay-race shortcut just
        # because something else also happened to reject the manifest in the meantime.
        fake.reset()
        original_reject = fake.reject_split_manifest

        def racing_reject_other(session: Any, ctx: Any, **kw: Any) -> Any:
            fake.calls.append(("reject", kw))
            fake.states[MANIFEST]["status"] = "rejected"
            raise store.SplitManifestError("manifest_tampered")

        _swap(mod, "reject_split_manifest", racing_reject_other)
        try:
            r = call(base, "POST", rpath, "admin", rgood)
        finally:
            _swap(mod, "reject_split_manifest", original_reject)
        check(
            "ACC-FREEZE-REVIEW",
            "a concurrent reject that raised manifest_tampered is never folded into the "
            "manifest_not_proposed replay race",
            r.status == 409 and r.detail == "manifest_tampered",
        )

        # a code that merely looks like the race codes (case / padding) is not one of them: only
        # the exact codes fall into the replay shortcut. The constructor asserts the code is
        # fixed, so the look-alike is built around it.
        for look_alike in (
            "MANIFEST_NOT_PROPOSED",
            " manifest_not_proposed ",
            "Idempotency_Key_Reused",
        ):
            fake.reset()

            def racing_reject_lookalike(
                session: Any, ctx: Any, _code: str = look_alike, **kw: Any
            ) -> Any:
                fake.calls.append(("reject", kw))
                fake.states[MANIFEST]["status"] = "rejected"
                err = store.SplitManifestError("manifest_not_proposed")
                err.code = _code
                raise err

            _swap(mod, "reject_split_manifest", racing_reject_lookalike)
            try:
                r = call(base, "POST", rpath, "admin", rgood)
            finally:
                _swap(mod, "reject_split_manifest", original_reject)
            check(
                "ACC-FREEZE-REVIEW",
                f"a reject refusal coded {look_alike!r} is not the replay race (409 with that code)",
                r.status == 409 and r.detail == look_alike,
            )


# ------------------------------------------------------------------ module copies for fault injection


def variant(src: str, name: str, workdir: Path) -> ModuleType:
    path = workdir / f"{name}.py"
    path.write_text(src)
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


FAULTS: tuple[tuple[str, str, str], ...] = (
    (
        "F01 the reviewer is a constant, not the principal",
        "actor_id=ctx.principal.subject,",
        'actor_id="route",',
    ),
    ("F02 the role is a constant", "role=ctx.role.value,", 'role="workspace_admin",'),
    (
        "F03 ml_engineer may approve and reject",
        "_WRITE_ROLES: frozenset[Role] = frozenset({Role.WORKSPACE_ADMIN, Role.ORGANIZATION_ADMIN})",
        "_WRITE_ROLES: frozenset[Role] = DATASET_SOURCE_ROLES",
    ),
    (
        "F04 a viewer may read",
        "DATASET_SOURCE_ROLES, principal_resolver=principal_resolver, role_lookup=role_lookup",
        "frozenset(Role), principal_resolver=principal_resolver, role_lookup=role_lookup",
    ),
    (
        "F05 the Idempotency-Key is not required",
        "    if key is None or _KEY_PATTERN.fullmatch(key) is None:",
        "    if False:",
    ),
    (
        "F06 the Idempotency-Key pattern accepts anything",
        '_KEY_PATTERN = re.compile(r"[A-Za-z0-9._:-]{8,200}")',
        '_KEY_PATTERN = re.compile(r".*", re.S)',
    ),
    ("F07 the Idempotency-Key may be one character", '{8,200}")', '{1,200}")'),
    ("F08 the list cap is 201", "_MAX_LIMIT = 200", "_MAX_LIMIT = 201"),
    ("F09 the review cap is unbounded", "_MAX_REVIEWS = 200", "_MAX_REVIEWS = 10**9"),
    ("F10 the rationale cap is 2001", "_MAX_RATIONALE = 2000", "_MAX_RATIONALE = 2001"),
    (
        "F11 unknown body keys are accepted",
        "or set(payload) != fields:",
        "or not set(payload) >= fields:",
    ),
    (
        "F12 a non-canonical expected hash is accepted",
        "if type(expected) is not str or _HEX64.fullmatch(expected) is None:",
        "if type(expected) is not str:",
    ),
    (
        "F13 replay ignores the approver",
        'if view.status != "locked" or view.approved_by != subject:',
        'if view.status != "locked":',
    ),
    ("F14 replay ignores the reviews", "if not hmac.compare_digest(wanted, have):", "if False:"),
    (
        "F16 reject skips the expected-hash check",
        "if not hmac.compare_digest(current.manifest.proposal_sha256, expected):",
        "if False:",
    ),
    (
        "F17 the ORM-like return value is serialized",
        "body = {name: _scalar(name, view.values[name]) for name in _VIEW_FIELDS}",
        "body = {name: _scalar(name, value) for name, value in view.values.items()}",
    ),
    (
        "F18 sources carry an object key",
        '"excluded_positions_sha256": s.excluded_positions_sha256,',
        '"excluded_positions_sha256": s.excluded_positions_sha256,\n            "object_key": s.object_key,',
    ),
    (
        "F19 role rows carry record ids",
        '"role_sha256": r.role_sha256,',
        '"role_sha256": r.role_sha256,\n            "record_ids": r.record_ids,',
    ),
    (
        "F21 invalid_review is a 409",
        '    if exc.code in ("invalid_review", "invalid_request"):',
        "    if False:",
    ),
    (
        "F22 a missing manifest is a 403",
        '    if exc.code == "manifest_not_found":\n        return _not_found()',
        '    if exc.code == "manifest_not_found":\n        return HTTPException(status_code=403, detail="not_found")',
    ),
    (
        "F23 an unlisted store code is a 500",
        "    return HTTPException(status_code=409, detail=exc.code)",
        "    return HTTPException(status_code=500, detail=exc.code)",
    ),
    (
        "F24 next_after is always set",
        'next_after = items[-1]["id"] if len(items) == limit else None',
        'next_after = items[-1]["id"] if items else None',
    ),
    (
        "F25 after is not validated",
        '    except ValueError as exc:\n        raise HTTPException(status_code=400, detail="invalid_after") from exc\n    if str(parsed) != raw:\n        raise HTTPException(status_code=400, detail="invalid_after")',
        "    except ValueError:\n        return None",
    ),
    (
        "F26 limit accepts non-ascii digits",
        "if not raw.isascii() or not raw.isdecimal():",
        "if not raw.isdecimal():",
    ),
    ("F27 the finding id is unbounded", "_MAX_FINDING_ID = 400", "_MAX_FINDING_ID = 10**9"),
    ("F28 the oversize body is not refused", "if len(raw) > _MAX_BODY_BYTES:", "if False:"),
    (
        "F29 an empty review list passes with a non-list",
        "if not isinstance(raw, list) or len(raw) > _MAX_REVIEWS:",
        "if not isinstance(raw, list | dict | str) or len(raw) > _MAX_REVIEWS:",
    ),
    (
        "F31 the stored key is never looked up",
        "        if outcome is not None:\n            return outcome\n",
        "        if False:\n            return outcome\n",
    ),
    (
        "F32 a reused key with another digest replays",
        "            if not hmac.compare_digest(stored.request_digest, digest):",
        "            if False:",
    ),
    (
        "F34 approve stores the key under a different name",
        '                        action="approve",\n                        idempotency_key=key,',
        '                        action="approve",\n                        idempotency_key=key + "-x",',
    ),
    (
        "F35 reject stores a constant digest",
        '                        action="reject",\n                        idempotency_key=key,\n                        request_digest=digest,',
        '                        action="reject",\n                        idempotency_key=key,\n                        request_digest="0" * 64,',
    ),
    (
        "F36 a held-out source is shown to every role",
        "        if include_held_out or s.dataset_purpose != _HELD_OUT_PURPOSE",
        "        if True",
    ),
    (
        "F37 the final role keeps group_count for everyone",
        "    if not include_held_out and r.role == _FINAL_ROLE:",
        "    if False:",
    ),
    (
        "F38 every role may see held-out data",
        "    return ctx.role is not Role.ML_ENGINEER",
        "    return True",
    ),
    (
        "F39 the held-out purpose is misspelled",
        '_HELD_OUT_PURPOSE = "held_out_evaluation"',
        '_HELD_OUT_PURPOSE = "held_out"',
    ),
    (
        "F40 only ml_engineer may see held-out data",
        "    return ctx.role is not Role.ML_ENGINEER",
        "    return ctx.role is Role.ML_ENGINEER",
    ),
    (
        "F41 the key replay of a race is dropped on approve",
        '                if exc.code not in ("manifest_not_proposed", "idempotency_key_reused"):\n                    raise\n                # A concurrent identical request may have locked it first.',
        '                if exc.code not in ("manifest_not_proposed",):\n                    raise\n                # A concurrent identical request may have locked it first.',
    ),
    (
        "F42 the key is not recorded on reject",
        '                    record_split_request(\n                        session,\n                        tenant_context,\n                        project_id=project_id,\n                        manifest_id=manifest_id,\n                        action="reject",',
        '                    (lambda *a, **k: None)(\n                        session,\n                        tenant_context,\n                        project_id=project_id,\n                        manifest_id=manifest_id,\n                        action="reject",',
    ),
    (
        "F30 the leakage summary is dropped",
        'body["leakage_summary"] = json.loads(json.dumps(dict(view.leakage_summary)))',
        'body["leakage_summary"] = {}',
    ),
)


def test_r_routes_pass_on_the_real_module() -> None:
    check = Checker()
    route_suite(real_routes, check)
    FAILURES.extend(check.fails)


def test_f_each_fault_is_red() -> None:
    src = ROUTES_PATH.read_text()
    with tempfile.TemporaryDirectory() as tmp:
        workdir = Path(tmp)
        for label, old, new in FAULTS:
            if src.count(old) != 1:
                FAILURES.append(
                    f"FAIL [{CID}] fault {label}: the target text occurs {src.count(old)} times"
                )
                continue
            name = "faulted_" + label.split()[0].lower()
            try:
                module = variant(src.replace(old, new, 1), name, workdir)
            except Exception as exc:  # noqa: BLE001 -- a fault that cannot even load is not a behavioural kill
                FAILURES.append(f"FAIL [{CID}] fault {label} did not load: {exc}")
                continue
            check = Checker()
            try:
                route_suite(module, check)
            except Exception:  # noqa: BLE001 -- a crash in a faulted copy is a kill too
                check.fails.append("crashed")
            if not check.fails:
                FAILURES.append(f"FAIL [{CID}] fault stays GREEN: {label}")


# ------------------------------------------------------------------ stored requests (SQLite)


def test_s_stored_requests_store_behaviour() -> None:
    """`find_split_request` / `record_split_request` against a real table (SQLite: the model carries
    the same unique key as migration 0032; the live Postgres evidence is the integration suite)."""
    from sqlalchemy import create_engine

    engine = create_engine("sqlite://")
    store.DatasetSplitManifestRequest.__table__.create(engine)  # type: ignore[attr-defined]
    saved = store.execute_tenant_scoped

    def passthrough(
        session: Session, organization_id: Any, statement: Any, params: Any = None
    ) -> Any:
        del organization_id, params
        return session.execute(statement)

    store.execute_tenant_scoped = passthrough
    try:
        org, other_org, other_project = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        ctx, other_ctx = (
            TenantContext(organization_id=org),
            TenantContext(organization_id=other_org),
        )
        actor = store.SplitActor("user", "admin-1", "workspace_admin", "req-1")
        digest = "ab" * 32
        with Session(engine) as session:

            def find(project: uuid.UUID, key: str, context: Any = ctx) -> Any:
                return store.find_split_request(
                    session, context, project_id=project, idempotency_key=key
                )

            def record(**over: Any) -> None:
                kw: dict[str, Any] = {
                    "project_id": PROJECT,
                    "manifest_id": MANIFEST,
                    "action": "approve",
                    "idempotency_key": "key-0001-abcd",
                    "request_digest": digest,
                    "actor": actor,
                    "now": NOW,
                }
                kw.update(over)
                store.record_split_request(session, ctx, **kw)
                session.commit()

            def rows() -> list[Any]:
                return list(
                    session.execute(store.select(store.DatasetSplitManifestRequest)).scalars()
                )

            def refusal(fn: Callable[[], None]) -> str | None:
                try:
                    fn()
                except store.SplitManifestError as exc:
                    session.rollback()
                    return exc.code
                return None

            if find(PROJECT, "key-0001-abcd") is not None:
                FAILURES.append(f"FAIL [{CID}] an unused key finds nothing")
            record()
            stored = find(PROJECT, "key-0001-abcd")
            if stored != store.StoredRequest(MANIFEST, "approve", digest):
                FAILURES.append(f"FAIL [{CID}] the stored request round-trips: {stored!r}")
            row = rows()[0]
            if not (
                row.organization_id == org
                and row.project_id == PROJECT
                and row.manifest_id == MANIFEST
                and row.action == "approve"
                and row.idempotency_key == "key-0001-abcd"
                and row.request_digest == digest
                and row.created_by == "admin-1"
                and row.created_at.replace(tzinfo=UTC) == NOW
            ):
                FAILURES.append(
                    f"FAIL [{CID}] the stored row carries the actor, tenant and instant"
                )
            if find(other_project, "key-0001-abcd") is not None:
                FAILURES.append(f"FAIL [{CID}] a key is scoped to its project")
            if find(PROJECT, "key-0002-abcd") is not None:
                FAILURES.append(f"FAIL [{CID}] a different key finds nothing")
            record(idempotency_key="Key-MIXED-0009-AbCd")
            if find(PROJECT, "Key-MIXED-0009-AbCd") is None:
                FAILURES.append(f"FAIL [{CID}] a mixed-case key is found by its exact text")
            for variant_key in (
                "key-mixed-0009-abcd",
                " Key-MIXED-0009-AbCd",
                "Key-MIXED-0009-AbCd ",
            ):
                if find(PROJECT, variant_key) is not None:
                    FAILURES.append(f"FAIL [{CID}] a key is matched exactly, not {variant_key!r}")
            code = refusal(lambda: record(request_digest="cd" * 32))
            if code != "idempotency_key_reused" or len(rows()) != 2:
                FAILURES.append(f"FAIL [{CID}] a stored key is not overwritten ({code!r})")
            kept = find(PROJECT, "key-0001-abcd")
            if kept is None or kept.request_digest != digest:
                FAILURES.append(f"FAIL [{CID}] a refused second write leaves the first digest")
            record(project_id=other_project)  # same key, another project: a different slot
            if len(rows()) != 3:
                FAILURES.append(f"FAIL [{CID}] the same key in another project is its own row")
            record(idempotency_key="key-0003-abcd", action="reject", manifest_id=OTHER_MANIFEST)
            third = find(PROJECT, "key-0003-abcd")
            if third != store.StoredRequest(OTHER_MANIFEST, "reject", digest):
                FAILURES.append(f"FAIL [{CID}] a reject request is stored as a reject: {third!r}")
            for bad in ("approved", "", "APPROVE", "approve "):
                before = len(rows())

                def bad_action(bad: str = bad) -> None:
                    record(idempotency_key="key-bad-0001", action=bad)

                if refusal(bad_action) != "invalid_request" or len(rows()) != before:
                    FAILURES.append(
                        f"FAIL [{CID}] action {bad!r} is refused invalid_request, no write"
                    )
            before = len(rows())
            code = refusal(lambda: record(idempotency_key="key-bad-0002", actor=None))
            if code != "invalid_request" or len(rows()) != before:
                FAILURES.append(f"FAIL [{CID}] a request without an actor is refused ({code!r})")
            viewer = store.SplitActor("user", "viewer-1", "viewer", "req-2")
            code = refusal(lambda: record(idempotency_key="key-bad-0003", actor=viewer))
            if code != "actor_not_permitted" or len(rows()) != before:
                FAILURES.append(f"FAIL [{CID}] a non-admin actor is refused ({code!r})")
            del other_ctx
    finally:
        store.execute_tenant_scoped = saved
        engine.dispose()


# ------------------------------------------------------------------ migration 0032 shape (offline SQL)


def _offline_sql(action: str, revision_range: str) -> str:
    import subprocess

    result = subprocess.run(
        [sys.executable, "-m", "alembic", action, revision_range, "--sql"],
        cwd=str(ROOT / "apps" / "api"),
        capture_output=True,
        text=True,
        env={**os.environ, "SOVRA_DATABASE_URL": "postgresql+psycopg://x:y@localhost:5432/z"},
    )
    if result.returncode != 0:
        FAILURES.append(
            f"FAIL [{CID}] alembic {action} {revision_range} --sql: {result.stderr[-300:]}"
        )
    return result.stdout


def test_m_migration_0032_shape() -> None:
    """Law 18: the new table ships with FORCE row-level security, a tenant policy and no UPDATE /
    DELETE grant. Rendered offline; the live proof is the Postgres integration suite."""
    up = _offline_sql("upgrade", "0031_model_import_validation:0032_split_manifest_requests")
    down = _offline_sql("downgrade", "0032_split_manifest_requests:0031_model_import_validation")
    table = "dataset_split_manifest_requests"
    for needle in (
        f"CREATE TABLE {table}",
        "CONSTRAINT uq_dataset_split_manifest_requests_key UNIQUE (organization_id, project_id, idempotency_key)",
        "CONSTRAINT fk_dataset_split_manifest_requests_manifest FOREIGN KEY(organization_id, manifest_id) "
        "REFERENCES dataset_split_manifests (organization_id, id) ON DELETE RESTRICT",
        "CHECK (action IN ('approve', 'reject'))",
        "request_digest ~ '^[0-9a-f]{64}$'",
        "char_length(idempotency_key) BETWEEN 8 AND 200",
        f'GRANT SELECT, INSERT ON {table} TO "sovra_app"',
        f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY",
        f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY",
        f"CREATE POLICY tenant_isolation ON {table} FOR ALL",
        "current_setting('app.current_organization_id', true)",
        f"CREATE INDEX ix_{table}_organization_id ON {table} (organization_id)",
        f"CREATE INDEX ix_{table}_manifest ON {table} (manifest_id)",
        "0032_split_manifest_requests",
    ):
        if needle not in up:
            FAILURES.append(f"FAIL [{CID}] migration 0032 upgrade SQL lacks {needle!r}")
    if "DELETE ON" in up or "UPDATE ON" in up or ", UPDATE" in up or "ALL ON" in up:
        FAILURES.append(f"FAIL [{CID}] migration 0032 grants more than SELECT, INSERT")
    for needle in (
        f"DROP POLICY IF EXISTS tenant_isolation ON {table}",
        f'REVOKE ALL ON {table} FROM "sovra_app"',
        f"DROP INDEX ix_{table}_manifest",
        f"DROP INDEX ix_{table}_organization_id",
        f"DROP TABLE {table}",
        "0031_model_import_validation",
    ):
        if needle not in down:
            FAILURES.append(f"FAIL [{CID}] migration 0032 downgrade SQL lacks {needle!r}")
    sys_path_src = (
        ROOT / "apps" / "api" / "alembic" / "versions" / "0032_dataset_split_manifest_requests.py"
    ).read_text()
    if (
        'revision = "0032_split_manifest_requests"' not in sys_path_src
        or len("0032_split_manifest_requests") > 32
    ):
        FAILURES.append(f"FAIL [{CID}] migration 0032 revision id fits alembic_version")


# ------------------------------------------------------------------ contract and wiring


def _spec() -> dict[str, Any]:
    return yaml.safe_load(INTERFACE_PATH.read_text())


def test_c1_interface_contract() -> None:
    spec = _spec()
    paths = spec["paths"]
    prefix = "/api/v1/organizations/{organization_id}/projects/{project_id}/dataset-splits"
    want = {
        prefix: {"get"},
        prefix + "/{manifest_id}": {"get"},
        prefix + "/{manifest_id}/approve": {"post"},
        prefix + "/{manifest_id}/reject": {"post"},
    }
    for path, methods in want.items():
        entry = paths.get(path)
        check = entry is not None
        if not check:
            FAILURES.append(f"FAIL [{CID}] 05-interface.yaml declares {path}")
            continue
        found = {m for m in entry if m in ("get", "post", "put", "patch", "delete")}
        if found != methods:
            FAILURES.append(f"FAIL [{CID}] {path} declares exactly {methods}, found {found}")
    if any(m in paths[prefix] for m in ("post", "put", "delete")):
        FAILURES.append(f"FAIL [{CID}] the collection declares a create route")
    for action in ("approve", "reject"):
        op = paths[prefix + f"/{{manifest_id}}/{action}"]["post"]
        header = [p for p in op["parameters"] if p.get("in") == "header"]
        if not (
            len(header) == 1
            and header[0]["name"] == "Idempotency-Key"
            and header[0]["required"] is True
        ):
            FAILURES.append(f"FAIL [{CID}] {action} requires the Idempotency-Key header")
        for status in ("400", "401", "403", "404", "409", "200"):
            if status not in op["responses"]:
                FAILURES.append(f"FAIL [{CID}] {action} documents {status}")
    schemas = spec["components"]["schemas"]
    for name in (
        "DatasetSplitManifestApproveRequest",
        "DatasetSplitManifestRejectRequest",
        "DatasetSplitLeakageReviewInput",
        "DatasetSplitManifest",
        "DatasetSplitManifestRoleRow",
        "DatasetSplitManifestSourceRow",
        "DatasetSplitLeakageSummary",
        "DatasetSplitManifestPage",
    ):
        if schemas.get(name, {}).get("additionalProperties") is not False:
            FAILURES.append(f"FAIL [{CID}] {name} is closed (additionalProperties false)")
    props = set(schemas["DatasetSplitManifest"]["properties"])
    if props != VIEW_KEYS:
        FAILURES.append(
            f"FAIL [{CID}] DatasetSplitManifest properties differ from the response: {props ^ VIEW_KEYS}"
        )
    if set(schemas["DatasetSplitManifestRoleRow"]["properties"]) != ROLE_KEYS:
        FAILURES.append(f"FAIL [{CID}] role row properties")
    if set(schemas["DatasetSplitManifestSourceRow"]["properties"]) != SOURCE_KEYS:
        FAILURES.append(f"FAIL [{CID}] source row properties")
    approve = schemas["DatasetSplitManifestApproveRequest"]
    if set(approve["properties"]) != {"expected_proposal_sha256", "leakage_reviews"} or set(
        approve["required"]
    ) != set(approve["properties"]):
        FAILURES.append(f"FAIL [{CID}] approve request fields")
    if approve["properties"]["leakage_reviews"]["maxItems"] != 200:
        FAILURES.append(f"FAIL [{CID}] approve request caps reviews at 200")
    review = schemas["DatasetSplitLeakageReviewInput"]["properties"]
    if review["rationale"]["maxLength"] != 2000 or review["rationale"]["minLength"] != 1:
        FAILURES.append(f"FAIL [{CID}] rationale bounds")
    flat = json.dumps(
        {
            k: v
            for k, v in schemas.items()
            if k.startswith("DatasetSplit") and not k.endswith("Request")
        }
    )
    for banned in (
        "record_id",
        "group_id",
        "assignment",
        "object_key",
        "storage_key",
        "bucket",
        'leakage_report"',
        "leakage_reviews",
    ):
        if banned in flat:
            FAILURES.append(f"FAIL [{CID}] the split schemas must not name {banned!r}")
    codes = set(
        re.findall(
            r"`([a-z_]+)`",
            json.dumps(paths[prefix + "/{manifest_id}/approve"]["post"]["responses"]),
        )
    )
    for code in (
        "manifest_changed",
        "manifest_not_proposed",
        "leakage_not_clear",
        "manifest_insufficient",
        "manifest_tampered",
        "invalid_body",
        "invalid_idempotency_key",
        "invalid_review",
        "idempotency_key_reused",
    ):
        if code not in codes:
            FAILURES.append(f"FAIL [{CID}] approve documents {code}")
    reject_codes = set(
        re.findall(
            r"`([a-z_]+)`", json.dumps(paths[prefix + "/{manifest_id}/reject"]["post"]["responses"])
        )
    )
    for code in (
        "manifest_changed",
        "manifest_not_proposed",
        "invalid_body",
        "invalid_idempotency_key",
        "idempotency_key_reused",
    ):
        if code not in reject_codes:
            FAILURES.append(f"FAIL [{CID}] reject documents {code}")
    role_row = schemas["DatasetSplitManifestRoleRow"]
    if set(role_row["required"]) != {"role", "purpose", "record_count", "role_sha256"}:
        FAILURES.append(
            f"FAIL [{CID}] group_count is optional (omitted on the final row for ml_engineer)"
        )
    if "held_out_evaluation" not in json.dumps(
        schemas["DatasetSplitManifestDetail"]["allOf"][1]["properties"]["sources"]
    ):
        FAILURES.append(
            f"FAIL [{CID}] the contract says held-out sources are hidden from ml_engineer"
        )
    for action in ("approve", "reject"):
        if "idempotency_key_reused" not in json.dumps(
            paths[prefix + "/{manifest_id}/" + action]["post"]["summary"]
        ):
            FAILURES.append(f"FAIL [{CID}] {action} summary documents the key digest rule")


def _full_app_env() -> dict[str, str]:
    return {
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


def test_c2_router_is_wired_into_the_app() -> None:
    main = MAIN_PATH.read_text()
    if (
        "from app.datasets.dataset_split_manifest_routes import build_dataset_split_manifest_router"
        not in main
    ):
        FAILURES.append(f"FAIL [{CID}] main.py imports build_dataset_split_manifest_router")
    if (
        re.search(
            r"app\.include_router\(\s*build_dataset_split_manifest_router\(\s*session_factory, principal_resolver=principal_resolver, role_lookup=role_lookup\s*\)\s*\)",
            main,
        )
        is None
    ):
        FAILURES.append(
            f"FAIL [{CID}] main.py includes the router with the shared resolver and lookup"
        )
    env = _full_app_env()
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
    with running_app_for(app) as base:
        for method, suffix in (
            ("GET", ""),
            ("GET", f"/{MANIFEST}"),
            ("POST", f"/{MANIFEST}/approve"),
            ("POST", f"/{MANIFEST}/reject"),
        ):
            status = call(
                base, method, BASE + suffix, None, {} if method == "POST" else None
            ).status
            if status != 401:
                FAILURES.append(
                    f"FAIL [{CID}] {method} {suffix or '/'} is mounted on the real app (401, not {status})"
                )


def test_c3_proving_symbol_is_the_wired_builder() -> None:
    matrix = (ROOT / "docs" / "spec-matrix.yaml").read_text()
    if "FTF-0519" not in matrix:
        FAILURES.append(f"FAIL [{CID}] docs/spec-matrix.yaml has an FTF-0519 entry")
    if not hasattr(real_routes, "build_dataset_split_manifest_router"):
        FAILURES.append(f"FAIL [{CID}] the proving symbol exists")


def main() -> None:
    tests: list[tuple[str, Callable[[], None]]] = [
        (n, f) for n, f in sorted(globals().items()) if n.startswith("test_") and callable(f)
    ]
    for name, fn in tests:
        try:
            fn()
        except Exception as exc:  # noqa: BLE001 - a crash is a failed test, reported by name
            FAILURES.append(f"FAIL [{CID}] {name} raised {type(exc).__name__}: {exc}")

    if FAILURES:
        print(f"FTF-0519-P2 split manifest route tests: {len(FAILURES)} failed")
        for failure in FAILURES:
            print(f"  {failure}")
        sys.exit(1)

    print("FTF-0519-P2 split manifest route tests: all checks passed")
    sys.exit(0)


if __name__ == "__main__":
    main()
