#!/usr/bin/env python3
# ruff: noqa: E501
# mypy: disable-error-code="misc"
"""FTF-0519-P2 -- real Postgres + real uvicorn proof for the dataset split manifest routes.

The route module is served by uvicorn over the restricted `sovra_app` role against a live
PostgreSQL 16 server (never SQLite: it has no row-level security). Sources are real datasets with
real quality reports, the split is FTF-0517's allocation and the leakage report is FTF-0518's, all
proposed through `dataset_split_manifest_store` exactly as the data worker does. Proved:

- migration 0032 upgrades, downgrades and re-upgrades with one head; `dataset_split_manifest_requests`
  has ENABLE + FORCE row-level security with a non-allow-all `tenant_isolation` policy and the app
  role may only SELECT and INSERT; raw `sovra_app` SQL cannot overwrite, delete, cross tenants,
  store a malformed digest / action / key or point at a manifest that does not exist;
- the role / 401 / 403 / 404 / 409 matrix on all four routes (no principal, viewer, ml_engineer on a
  write, a foreign organization, a foreign project, a manifest of another project, a missing id);
- paging over real rows (every manifest exactly once, ordered, `next_after` only on a full page) and
  the query validation table;
- approve and reject: a clear manifest locks with the authenticated approver and one audit row; an
  unreviewed / partially reviewed / blocking-finding / blocked-route / saturated-scan manifest is
  refused 409 (or 400 for a review of a finding that does not exist or cannot be reviewed away) and
  writes nothing; a tampered role hash is 409 manifest_tampered; an empty role is
  manifest_insufficient; a stale expected hash is manifest_changed;
- the Idempotency-Key is stored with the request digest: the same key and request replays (200,
  `replayed: true`, nothing written), the same key with another request is 409
  `idempotency_key_reused` and changes nothing, a refused request leaves its key free, and 12
  concurrent identical requests produce exactly one lock, one audit row and one stored key;
  approve-vs-reject on one manifest has exactly one winner;
- a route-locked manifest serves training rows until its connection is revoked, then
  `grant_changed`;
- a caller that may not read held-out data (ml_engineer) sees no held-out source (dataset id, name,
  hashes, connection id, quality report id and hash) and a final role row with only a count and a
  hash, while the same GET as an admin shows all four sources;
- a canary scan of every response body: no record id, group id, finding id, review text or storage
  key ever appears.

Requires SOVRA_DATABASE_URL (migration-owner role) and SOVRA_APP_DB_PASSWORD.
Run: python3 tests/integration/test_ftf_0519_routes_postgres.py
"""

from __future__ import annotations

import concurrent.futures
import contextlib
import hashlib
import http.client
import json
import socket
import sys
import threading
import time
import urllib.parse
import uuid
from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent.parent
API_ROOT = ROOT / "apps" / "api"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(API_ROOT))
sys.path.insert(0, str(ROOT / "tests" / "integration"))

import uvicorn  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from sqlalchemy import create_engine, text  # noqa: E402
from sqlalchemy.exc import DBAPIError  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402
from test_ftf_0519_p1_split_manifests import (  # noqa: E402
    BLOCKED_ROUTE,
    ROUTE,
    SPEC,
    Env,
    World,
    _alembic,
    _app_role_url,
    _long_record,
    _required_env,
    _sha,
    _variant,
    seed,
)
from workers.data import immutable_split_manifest as ism  # noqa: E402
from workers.data.group_aware_split_engine import SplitSpec  # noqa: E402

from app.auth.rbac_middleware import AuthenticatedPrincipal, Role  # noqa: E402
from app.datasets import dataset_split_manifest_store as store  # noqa: E402
from app.datasets.dataset_split_manifest_routes import (  # noqa: E402
    build_dataset_split_manifest_router,
)
from app.db.tenant_context import TenantContext, tenant_scoped_session_for  # noqa: E402

FAILURES: list[str] = []
CID = "FTF-0519-P2"
ISSUER = "https://issuer.example"
REQUEST_TABLE = "dataset_split_manifest_requests"
BODIES: list[tuple[str, str]] = []


def check(description: str, condition: bool) -> None:
    if not condition:
        FAILURES.append(f"FAIL [{CID}] {description}")


# ---------------------------------------------------------------------------------------- http


def _free_port() -> int:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


@contextlib.contextmanager
def running_app(app: FastAPI) -> Iterator[str]:
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
        thread.join(timeout=10)


class Resp:
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


class Client:
    def __init__(self, url: str) -> None:
        self.url = url

    def __call__(
        self,
        method: str,
        path: str,
        principal: str | None,
        body: Any = None,
        key: str | None = None,
    ) -> Resp:
        headers: dict[str, str] = {"X-Test-Principal": principal} if principal else {}
        if key is not None:
            headers["Idempotency-Key"] = key
        payload = None
        if body is not None:
            payload = body if isinstance(body, bytes) else json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
        parsed = urllib.parse.urlparse(self.url)
        conn = http.client.HTTPConnection(
            parsed.hostname or "127.0.0.1", parsed.port or 80, timeout=60
        )
        try:
            conn.request(method, path, body=payload, headers=headers)
            response = conn.getresponse()
            data = response.read()
        finally:
            conn.close()
        BODIES.append((f"{method} {path}", data.decode("utf-8", "replace")))
        return Resp(response.status, data)


class Fixture:
    """Principals and memberships: ml_engineer, workspace_admin, organization_admin and viewer in
    org1; ml_engineer and workspace_admin in org2."""

    def __init__(self, org1: uuid.UUID, org2: uuid.UUID) -> None:
        self.principals: dict[str, AuthenticatedPrincipal] = {}
        self.memberships: dict[tuple[str, str, uuid.UUID], Role] = {}
        for label, org, roles in (
            (
                "org1",
                org1,
                (Role.ML_ENGINEER, Role.WORKSPACE_ADMIN, Role.ORGANIZATION_ADMIN, Role.VIEWER),
            ),
            ("org2", org2, (Role.ML_ENGINEER, Role.WORKSPACE_ADMIN)),
        ):
            for role in roles:
                subject = f"user-{label}-{role.value}"
                header = f"{label}:{role.value}"
                self.principals[header] = AuthenticatedPrincipal(issuer=ISSUER, subject=subject)
                self.memberships[(ISSUER, subject, org)] = role

    @staticmethod
    def header(label: str, role: Role) -> str:
        return f"{label}:{role.value}"

    def subject(self, label: str, role: Role) -> str:
        return f"user-{label}-{role.value}"

    def principal_resolver(self, request: Any) -> AuthenticatedPrincipal | None:
        return self.principals.get(request.headers.get("x-test-principal", ""))

    def role_lookup(self, issuer: str, subject: str, organization_id: uuid.UUID) -> Role | None:
        return self.memberships.get((issuer, subject, organization_id))


# ---------------------------------------------------------------------------------------- digests


def digest_of(action: str, manifest_id: Any, expected: str, reviews: list[tuple[str, str]]) -> str:
    """The documented request fingerprint, computed here independently of the route module."""
    body = json.dumps(
        {
            "action": action,
            "manifest_id": str(manifest_id),
            "expected_proposal_sha256": expected,
            "reviews": sorted(reviews),
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )
    return hashlib.sha256(body.encode()).hexdigest()


def reviews_body(expected: str, reviews: list[tuple[str, str]]) -> dict[str, Any]:
    return {
        "expected_proposal_sha256": expected,
        "leakage_reviews": [{"finding_id": f, "rationale": r} for f, r in reviews],
    }


def new_key(label: str) -> str:
    return f"{label}-{uuid.uuid4().hex}"


# ---------------------------------------------------------------------------------------- db helpers


def request_rows(env: Env, manifest_id: uuid.UUID | None = None) -> list[Any]:
    if manifest_id is None:
        return env.rows(
            f"SELECT manifest_id, action, idempotency_key, request_digest, created_by FROM {REQUEST_TABLE}"
        )
    return env.rows(
        f"SELECT manifest_id, action, idempotency_key, request_digest, created_by FROM {REQUEST_TABLE} "
        "WHERE manifest_id=:m",
        m=manifest_id,
    )


def audit_count(env: Env, manifest_id: uuid.UUID, action: str) -> int:
    return int(
        env.scalar(
            "SELECT count(*) FROM audit_events WHERE resource_id=:r AND action=:a",
            r=str(manifest_id),
            a=action,
        )
        or 0
    )


def snapshot(env: Env, manifest_id: uuid.UUID) -> tuple[Any, ...]:
    return (
        env.fingerprint(manifest_id),
        len(request_rows(env)),
        audit_count(env, manifest_id, "dataset_split_manifest.locked"),
        audit_count(env, manifest_id, "dataset_split_manifest.rejected"),
    )


def raw_error(env: Env, ctx: TenantContext, sql: str, **params: Any) -> BaseException | None:
    try:
        with tenant_scoped_session_for(env.factory, ctx) as session:
            session.execute(text(sql), params)
        return None
    except DBAPIError as exc:
        return exc


def refused(error: BaseException | None, *needles: str) -> bool:
    return error is not None and all(n in str(error) for n in needles)


def base_path(org: uuid.UUID, project: uuid.UUID) -> str:
    return f"/api/v1/organizations/{org}/projects/{project}/dataset-splits"


# ---------------------------------------------------------------------------------------- catalog


def check_catalog(env: Env) -> None:
    with env.owner.connect() as conn:
        enabled, forced = conn.execute(
            text("SELECT relrowsecurity, relforcerowsecurity FROM pg_class WHERE relname=:t"),
            {"t": REQUEST_TABLE},
        ).one()
        check(f"{REQUEST_TABLE} has ENABLE and FORCE row-level security", bool(enabled and forced))
        policies = conn.execute(
            text(
                "SELECT polname, pg_get_expr(polqual, polrelid), pg_get_expr(polwithcheck, polrelid) "
                "FROM pg_policy WHERE polrelid = CAST(:t AS regclass)"
            ),
            {"t": REQUEST_TABLE},
        ).all()
        check(
            f"{REQUEST_TABLE} has one tenant_isolation policy, USING and WITH CHECK, not allow-all",
            len(policies) == 1
            and policies[0][0] == "tenant_isolation"
            and "app.current_organization_id" in str(policies[0][1])
            and "app.current_organization_id" in str(policies[0][2])
            and str(policies[0][1]).strip().lower() != "true",
        )
        for privilege, expected in (
            ("SELECT", True),
            ("INSERT", True),
            ("UPDATE", False),
            ("DELETE", False),
            ("TRUNCATE", False),
        ):
            granted = conn.execute(
                text("SELECT has_table_privilege('sovra_app', :t, :p)"),
                {"t": REQUEST_TABLE, "p": privilege},
            ).scalar()
            check(
                f"sovra_app {privilege} on {REQUEST_TABLE} is {expected}", bool(granted) is expected
            )
        check(
            f"sovra_app has no UPDATE on any column of {REQUEST_TABLE}",
            conn.execute(
                text(
                    "SELECT count(*) FROM information_schema.column_privileges "
                    "WHERE grantee='sovra_app' AND privilege_type='UPDATE' AND table_name=:t"
                ),
                {"t": REQUEST_TABLE},
            ).scalar()
            == 0,
        )


def section_raw_requests(env: Env, w: World, manifest_id: uuid.UUID) -> None:
    """Raw sovra_app SQL against the new table. `manifest_id` is a real manifest of org1."""
    good = {
        "o": env.org1,
        "p": w.project_id,
        "m": manifest_id,
        "k": new_key("raw-key"),
        "d": "ab" * 32,
    }
    insert = (
        f"INSERT INTO {REQUEST_TABLE} (organization_id, project_id, manifest_id, action, "
        "idempotency_key, request_digest, created_by) "
        "VALUES (:o, :p, :m, :a, :k, :d, 'raw')"
    )
    error = raw_error(env, w.ctx, insert, **good, a="approve")
    check(f"a well-formed own-tenant INSERT is accepted (control): {error!r}", error is None)
    error = raw_error(env, w.ctx, insert, **good, a="approve")
    check(f"the same key twice in a project is refused: {error!r}", refused(error, "uq_"))
    error = raw_error(env, w.ctx, insert, **{**good, "k": new_key("raw-key")}, a="rejectt")
    check(f"an unknown action is refused: {error!r}", refused(error, "ck_"))
    for label, bad in (
        ("an uppercase digest", "AB" * 32),
        ("a short digest", "ab" * 31),
        ("a non-hex digest", "zz" * 32),
    ):
        error = raw_error(
            env, w.ctx, insert, **{**good, "k": new_key("raw-key"), "d": bad}, a="approve"
        )
        check(f"{label} is refused: {error!r}", refused(error, "ck_"))
    error = raw_error(env, w.ctx, insert, **{**good, "k": "short"}, a="approve")
    check(f"a key shorter than 8 characters is refused: {error!r}", refused(error, "ck_"))
    error = raw_error(env, w.ctx, insert, **{**good, "k": "k" * 201}, a="approve")
    check(f"a key longer than 200 characters is refused: {error!r}", error is not None)
    error = raw_error(
        env, w.ctx, insert, **{**good, "k": new_key("raw-key"), "m": uuid.uuid4()}, a="approve"
    )
    check(
        f"a request for a manifest that does not exist is refused: {error!r}", refused(error, "fk_")
    )
    error = raw_error(
        env, env.ctx2, insert, **{**good, "k": new_key("raw-key"), "o": env.org2}, a="approve"
    )
    check(
        f"org2 cannot store a request against org1's manifest (composite FK): {error!r}",
        refused(error, "fk_"),
    )
    error = raw_error(env, env.ctx2, insert, **{**good, "k": new_key("raw-key")}, a="approve")
    check(
        f"a cross-tenant INSERT (org1 rows as org2) is refused: {error!r}",
        refused(error, "row-level"),
    )
    error = raw_error(env, w.ctx, f"UPDATE {REQUEST_TABLE} SET request_digest = :d", d="cd" * 32)
    check(
        f"an UPDATE of a stored digest is refused: {error!r}", refused(error, "permission denied")
    )
    error = raw_error(env, w.ctx, f"DELETE FROM {REQUEST_TABLE}")
    check(f"a DELETE is refused: {error!r}", refused(error, "permission denied"))
    seen = env.run(
        env.ctx2, lambda s: s.execute(text(f"SELECT count(*) FROM {REQUEST_TABLE}")).scalar()
    )
    check(f"org2 sees no stored request of org1: {seen!r}", seen == (0, None))
    own = env.run(
        w.ctx, lambda s: s.execute(text(f"SELECT count(*) FROM {REQUEST_TABLE}")).scalar()
    )
    check(f"org1 sees its stored requests: {own!r}", own[0] is not None and own[0] >= 1)
    no_context = env.run(
        TenantContext(organization_id=uuid.uuid4()),
        lambda s: s.execute(text(f"SELECT count(*) FROM {REQUEST_TABLE}")).scalar(),
    )
    check(f"a foreign tenant context reads zero rows: {no_context!r}", no_context == (0, None))


# ---------------------------------------------------------------------------------------- scenarios


def make_clear_world(env: Env, label: str) -> tuple[World, dict[str, Any], Any]:
    """One project, a held-out dataset on a connection of its own, and a clear proposal."""
    w = env.world(label)
    held_conn = uuid.uuid4()
    env.execute(
        "INSERT INTO dataset_source_connections (id, organization_id, project_id, source_type, "
        "display_name, allowed_purposes_json, security_classification, connection_status, created_by) "
        "VALUES (:id, :org, :project, 'upload', :name, CAST(:purposes AS jsonb), 'internal', 'active', "
        "'seed')",
        id=held_conn,
        org=env.org1,
        project=w.project_id,
        name=f"{label}-held-only",
        purposes=json.dumps(["held_out_evaluation"]),
    )
    w.conns["held"] = held_conn
    ds = {
        "train": env.dataset(w, f"{label}-train", "training", 200),
        "val": env.dataset(w, f"{label}-val", "validation", 40),
        "dev": env.dataset(w, f"{label}-dev", "candidate_development", 8),
        "held": env.dataset(w, f"{label}-held", "held_out_evaluation", 8, connection="held"),
    }
    manifest, error = env.propose(w, f"{label}-campaign", list(ds.values()))
    check(
        f"{label}: the clear proposal is stored: {error!r}", error is None and manifest is not None
    )
    if error is not None:
        raise error
    return w, ds, manifest


def fresh_world(env: Env, label: str) -> tuple[World, dict[str, Any]]:
    """A project of its own with four clear sources. A locked manifest becomes a required prior
    campaign of every later proposal in its project, so each scenario that locks one gets a world."""
    w = env.world(label)
    ds = {
        "train": env.dataset(w, f"{label}-train", "training", 60),
        "val": env.dataset(w, f"{label}-val", "validation", 20),
        "dev": env.dataset(w, f"{label}-dev", "candidate_development", 6),
        "held": env.dataset(w, f"{label}-held", "held_out_evaluation", 6),
    }
    return w, ds


def section_matrix(env: Env, fx: Fixture, http_: Client, w: World, manifest: Any) -> None:
    p = base_path(env.org1, w.project_id)
    one = f"{p}/{manifest.id}"
    ml = fx.header("org1", Role.ML_ENGINEER)
    admin = fx.header("org1", Role.WORKSPACE_ADMIN)
    orgadmin = fx.header("org1", Role.ORGANIZATION_ADMIN)
    viewer = fx.header("org1", Role.VIEWER)
    foreign_admin = fx.header("org2", Role.WORKSPACE_ADMIN)
    body = reviews_body(manifest.proposal_sha256, [])
    reject_body = {"expected_proposal_sha256": manifest.proposal_sha256}
    before = snapshot(env, manifest.id)

    for path in (p, one):
        check(f"unauthenticated GET {path[-14:]} is 401", http_("GET", path, None).status == 401)
        check(f"viewer GET {path[-14:]} is 403", http_("GET", path, viewer).status == 403)
        check(
            f"a foreign organization's admin GET {path[-14:]} is 403",
            http_("GET", path, foreign_admin).status == 403,
        )
        for who in (ml, admin, orgadmin):
            check(f"{who} GET {path[-14:]} is 200", http_("GET", path, who).status == 200)
    for action, payload in (("approve", body), ("reject", reject_body)):
        path = f"{one}/{action}"
        key = new_key("matrix")
        check(
            f"unauthenticated {action} is 401",
            http_("POST", path, None, payload, key).status == 401,
        )
        for who, label in (
            (viewer, "viewer"),
            (ml, "ml_engineer"),
            (foreign_admin, "foreign admin"),
        ):
            r = http_("POST", path, who, payload, key)
            check(
                f"{label} {action} is 403 role_not_authorized: {r.status} {r.detail}",
                r.status == 403,
            )
    check(
        "refused callers wrote nothing (no lock, no rejection, no stored key)",
        snapshot(env, manifest.id) == before and env.manifest_row(manifest.id)[0] == "proposed",
    )
    for method, path in (
        ("POST", p),
        ("PUT", p),
        ("DELETE", p),
        ("PATCH", one),
        ("DELETE", one),
        ("GET", f"{one}/approve"),
    ):
        status = http_(method, path, admin, {}, new_key("nr")).status
        check(f"{method} {path[-22:]} is not mounted (404/405), got {status}", status in (404, 405))

    # 404 identical for missing and foreign
    missing = http_("GET", f"{p}/{uuid.uuid4()}", admin)
    check(
        "a missing manifest is 404 not_found",
        missing.status == 404 and missing.detail == "not_found",
    )
    other_project = env.world("matrix-other")
    foreign_project_path = f"{base_path(env.org1, other_project.project_id)}/{manifest.id}"
    r = http_("GET", foreign_project_path, admin)
    check(
        "the manifest of another project in the same organization is 404 not_found",
        r.status == 404 and r.body == missing.body,
    )
    org2_world = env.world("matrix-org2", org=2)
    r = http_("GET", f"{base_path(env.org1, org2_world.project_id)}/{manifest.id}", admin)
    check("org1's admin on a project of org2 is 404", r.status == 404)
    r = http_("GET", f"{base_path(env.org2, org2_world.project_id)}/{manifest.id}", foreign_admin)
    check(
        "org2's admin asking for org1's manifest id is 404, identical to a missing id",
        r.status == 404 and r.detail == "not_found",
    )
    for action, payload in (("approve", body), ("reject", reject_body)):
        r = http_("POST", f"{p}/{uuid.uuid4()}/{action}", admin, payload, new_key("m404"))
        check(
            f"{action} of a missing id is 404 not_found",
            r.status == 404 and r.detail == "not_found",
        )
        r = http_("POST", f"{foreign_project_path}/{action}", admin, payload, new_key("m404"))
        check(f"{action} through another project is 404 not_found", r.status == 404)
    check("the matrix wrote nothing", snapshot(env, manifest.id) == before)

    # validation: nothing written, stable codes
    for label, headers_key, wire, status, detail in (
        ("no key", None, body, 400, "invalid_idempotency_key"),
        ("a short key", "short", body, 400, "invalid_idempotency_key"),
        ("a key with a space", "key with space 1234", body, 400, "invalid_idempotency_key"),
        ("an unknown body key", new_key("v"), {**body, "extra": 1}, 400, "invalid_body"),
        (
            "a missing reviews key",
            new_key("v"),
            {"expected_proposal_sha256": manifest.proposal_sha256},
            400,
            "invalid_body",
        ),
        (
            "a non-hex expected hash",
            new_key("v"),
            {**body, "expected_proposal_sha256": "Z" * 64},
            400,
            "invalid_body",
        ),
        (
            "an upper-case expected hash",
            new_key("v"),
            {**body, "expected_proposal_sha256": "A" * 64},
            400,
            "invalid_body",
        ),
        (
            "a non-list reviews value",
            new_key("v"),
            {**body, "leakage_reviews": {}},
            400,
            "invalid_body",
        ),
        (
            "a review with an extra key",
            new_key("v"),
            {**body, "leakage_reviews": [{"finding_id": "a", "rationale": "b", "x": 1}]},
            400,
            "invalid_body",
        ),
        (
            "an empty rationale",
            new_key("v"),
            {**body, "leakage_reviews": [{"finding_id": "a", "rationale": ""}]},
            400,
            "invalid_body",
        ),
        (
            "a 2001-character rationale",
            new_key("v"),
            {**body, "leakage_reviews": [{"finding_id": "a", "rationale": "r" * 2001}]},
            400,
            "invalid_body",
        ),
        (
            "201 reviews",
            new_key("v"),
            {
                **body,
                "leakage_reviews": [{"finding_id": f"f{i}", "rationale": "r"} for i in range(201)],
            },
            400,
            "invalid_body",
        ),
        ("a non-object body", new_key("v"), b"[]", 400, "invalid_body"),
        ("a body that is not JSON", new_key("v"), b"{not json", 400, "invalid_body"),
    ):
        r = http_("POST", f"{one}/approve", admin, wire, headers_key)
        check(
            f"approve with {label} is {status} {detail} (got {r.status} {r.detail})",
            r.status == status and r.detail == detail,
        )
    r = http_("POST", f"{one}/approve", admin, b"x" * (1024 * 1024 + 1), new_key("big"))
    check(f"an oversize body is 413 request_too_large (got {r.status})", r.status == 413)
    r = http_("POST", f"{one}/reject", admin, {**reject_body, "leakage_reviews": []}, new_key("v"))
    check("reject refuses a reviews key", r.status == 400 and r.detail == "invalid_body")
    r = http_("POST", f"{one}/reject", admin, reject_body, "short")
    check("reject refuses a short key", r.status == 400 and r.detail == "invalid_idempotency_key")
    check("validation refusals wrote nothing", snapshot(env, manifest.id) == before)


def section_paging(env: Env, fx: Fixture, http_: Client) -> None:
    w = env.world("paging")
    ds = [
        env.dataset(w, "pg-train", "training", 60),
        env.dataset(w, "pg-val", "validation", 12),
        env.dataset(w, "pg-dev", "candidate_development", 6),
        env.dataset(w, "pg-held", "held_out_evaluation", 6),
    ]
    ids: list[uuid.UUID] = []
    for index in range(5):
        spec = SplitSpec("sha256_group_hash_v1", f"seed-paging-{index}", (70, 10, 10, 10))
        manifest, error = env.propose(w, f"paging-{index}", ds, spec=spec)
        check(f"paging proposal {index} stored: {error!r}", error is None)
        if error is None:
            ids.append(manifest.id)
    p = base_path(env.org1, w.project_id)
    ml = fx.header("org1", Role.ML_ENGINEER)
    full = http_("GET", p, ml)
    check(
        "list without a limit is 200 with every manifest ordered by id",
        full.status == 200
        and [i["id"] for i in full.json()["items"]] == sorted(str(i) for i in ids)
        and full.json()["next_after"] is None,
    )
    seen: list[str] = []
    cursor: str | None = None
    pages = 0
    while True:
        query = "?limit=2" + (f"&after={cursor}" if cursor else "")
        page = http_("GET", p + query, ml)
        pages += 1
        check(f"page {pages} is 200", page.status == 200)
        seen.extend(i["id"] for i in page.json()["items"])
        cursor = page.json()["next_after"]
        if cursor is None or pages > 6:
            break
    check(
        f"paging with limit=2 visits every manifest exactly once, in order ({len(seen)} of {len(ids)})",
        seen == sorted(str(i) for i in ids) and pages == 3,
    )
    page = http_("GET", p + "?limit=5", ml)
    check(
        "a full page names its last id as next_after and the next page is empty",
        page.json()["next_after"] == page.json()["items"][-1]["id"]
        and http_("GET", p + f"?limit=5&after={page.json()['next_after']}", ml).json()
        == {"items": [], "next_after": None},
    )
    other = env.world("paging-other")
    check(
        "another project lists none of these manifests",
        http_("GET", base_path(env.org1, other.project_id), ml).json()["items"] == [],
    )
    for bad in ("0", "201", "-1", "abc", "1.0", "", "٣", "+5", "1e1"):
        r = http_("GET", p + f"?limit={urllib.parse.quote(bad)}", ml)
        check(
            f"limit={bad!r} is 400 invalid_limit", r.status == 400 and r.detail == "invalid_limit"
        )
    check("limit=200 is accepted", http_("GET", p + "?limit=200", ml).status == 200)
    for bad in ("nope", str(uuid.uuid4()).upper(), "{" + str(uuid.uuid4()) + "}", ""):
        r = http_("GET", p + f"?after={urllib.parse.quote(bad)}", ml)
        check(
            f"after={bad[:10]!r} is 400 invalid_after",
            r.status == 400 and r.detail == "invalid_after",
        )


def section_clear_lock(
    env: Env, fx: Fixture, http_: Client, w: World, ds: dict[str, Any], manifest: Any
) -> None:
    p = base_path(env.org1, w.project_id)
    one = f"{p}/{manifest.id}"
    admin = fx.header("org1", Role.WORKSPACE_ADMIN)
    ml = fx.header("org1", Role.ML_ENGINEER)
    admin_subject = fx.subject("org1", Role.WORKSPACE_ADMIN)
    expected = manifest.proposal_sha256

    # --- held-out visibility on a proposed manifest, over real rows
    held = ds["held"]
    held_conn = str(w.conns["held"])
    held_report = env.rows(
        "SELECT quality_report_id, quality_report_sha256, dataset_sha256, dataset_name "
        "FROM dataset_split_manifest_sources WHERE manifest_id=:m AND dataset_id=:d",
        m=manifest.id,
        d=held.id,
    )[0]
    held_tokens = {
        "dataset id": str(held.id),
        "connection id": held_conn,
        "quality report id": str(held_report[0]),
        "quality report hash": held_report[1],
        "dataset hash": held_report[2],
        "dataset name": held_report[3],
    }
    as_admin = http_("GET", one, admin)
    admin_text = as_admin.body.decode()
    check(
        "the admin sees all four sources and every held-out source token",
        as_admin.status == 200
        and len(as_admin.json()["sources"]) == 4
        and all(token in admin_text for token in held_tokens.values()),
    )
    as_ml = http_("GET", one, ml)
    ml_text = as_ml.body.decode()
    ml_body = as_ml.json()
    check(
        "ml_engineer sees the three non-held-out sources and no held-out dataset purpose",
        as_ml.status == 200
        and sorted(s["dataset_purpose"] for s in ml_body["sources"])
        == ["candidate_development", "training", "validation"],
    )
    for label, token in held_tokens.items():
        check(f"ml_engineer never sees the held-out {label}", token not in ml_text)
    roles = {r["role"]: r for r in ml_body["roles"]}
    final_db = env.rows(
        "SELECT record_count, role_sha256 FROM dataset_split_manifest_roles "
        "WHERE manifest_id=:m AND role='final_qualification'",
        m=manifest.id,
    )[0]
    check(
        "ml_engineer's final role row is exactly role, purpose, record count and role hash",
        set(roles["final_qualification"]) == {"role", "purpose", "record_count", "role_sha256"}
        and roles["final_qualification"]["record_count"] == final_db[0]
        and roles["final_qualification"]["role_sha256"] == final_db[1],
    )
    check(
        "the other roles keep their group counts for ml_engineer",
        all("group_count" in roles[r] for r in ("train", "validation", "development_eval")),
    )
    check(
        "the admin's final role row keeps its group count",
        "group_count" in {r["role"]: r for r in as_admin.json()["roles"]}["final_qualification"],
    )
    listed = http_("GET", p + "?limit=200", ml).json()["items"]
    check(
        "the list never carries a source or role rows",
        all("sources" not in i and "roles" not in i for i in listed),
    )

    # --- approve
    key = new_key("lock")
    body = reviews_body(expected, [])
    before_audit = audit_count(env, manifest.id, "dataset_split_manifest.locked")
    stale = http_("POST", f"{one}/approve", admin, reviews_body("0" * 64, []), new_key("stale"))
    check(
        f"a stale expected hash is 409 manifest_changed (got {stale.status} {stale.detail})",
        stale.status == 409 and stale.detail == "manifest_changed",
    )
    check("the stale request stored no key", request_rows(env, manifest.id) == [])
    first = http_("POST", f"{one}/approve", admin, body, key)
    check(
        f"approving a clear manifest is 200 (got {first.status} {first.detail})",
        first.status == 200,
    )
    if first.status != 200:
        return
    check("the first request is not a replay", first.json()["replayed"] is False)
    row = env.manifest_row(manifest.id)
    check(
        "locked with the authenticated approver, a frozen hash and a lock time",
        row[0] == "locked"
        and row[5] == admin_subject
        and row[6] is not None
        and row[7] is not None
        and row[11] == [],
    )
    check(
        "the response carries the frozen hash and the approver",
        first.json()["status"] == "locked"
        and first.json()["frozen_manifest_sha256"] == row[6]
        and first.json()["approved_by"] == admin_subject,
    )
    stored = request_rows(env, manifest.id)
    check(
        "the key is stored once with the independent digest, the action and the approver",
        len(stored) == 1
        and stored[0][1] == "approve"
        and stored[0][2] == key
        and stored[0][3] == digest_of("approve", manifest.id, expected, [])
        and stored[0][4] == admin_subject,
    )
    check(
        "one lock audit row",
        audit_count(env, manifest.id, "dataset_split_manifest.locked") == before_audit + 1,
    )
    after_lock = snapshot(env, manifest.id)

    # --- replay by key
    again = http_("POST", f"{one}/approve", admin, body, key)
    check(
        "the same key and request is 200 replayed true with the stored outcome",
        again.status == 200
        and again.json()["replayed"] is True
        and {k: v for k, v in again.json().items() if k != "replayed"}
        == {k: v for k, v in first.json().items() if k != "replayed"},
    )
    check("the key replay wrote nothing", snapshot(env, manifest.id) == after_lock)
    other_key = http_("POST", f"{one}/approve", admin, body, new_key("lock2"))
    check(
        "an identical request under a new key is recognised from the locked row (replay)",
        other_key.status == 200 and other_key.json()["replayed"] is True,
    )
    check("and writes nothing", snapshot(env, manifest.id) == after_lock)

    # --- the same key with another request
    for label, path, payload in (
        ("another expected hash", f"{one}/approve", reviews_body("b" * 64, [])),
        ("a review added", f"{one}/approve", reviews_body(expected, [("f" * 64, "extra")])),
        ("a reject", f"{one}/reject", {"expected_proposal_sha256": expected}),
    ):
        r = http_("POST", path, admin, payload, key)
        check(
            f"the same key with {label} is 409 idempotency_key_reused (got {r.status} {r.detail})",
            r.status == 409 and r.detail == "idempotency_key_reused",
        )
    r = http_(
        "POST", f"{base_path(env.org1, w.project_id)}/{uuid.uuid4()}/approve", admin, body, key
    )
    check(
        f"the same key on another manifest id is not applied (got {r.status} {r.detail})",
        r.status in (404, 409),
    )
    check("a reused key changed nothing", snapshot(env, manifest.id) == after_lock)
    r = http_(
        "POST", f"{one}/approve", admin, reviews_body(expected, [("f" * 64, "x")]), new_key("late")
    )
    check(
        f"approving a locked manifest with other reviews is 409 manifest_not_proposed (got {r.status} {r.detail})",
        r.status == 409 and r.detail == "manifest_not_proposed",
    )
    r = http_(
        "POST", f"{one}/reject", admin, {"expected_proposal_sha256": expected}, new_key("late")
    )
    check(
        "rejecting a locked manifest is 409 manifest_not_proposed",
        r.status == 409 and r.detail == "manifest_not_proposed",
    )
    check("those refusals stored no key", snapshot(env, manifest.id) == after_lock)

    # --- training access through the store on the route-locked manifest, then a revoked grant
    view, error = env.training(w, manifest.id)
    check(
        f"the route-locked manifest serves training rows: {error!r}",
        error is None and len(view.rows) > 0,
    )
    env.execute(
        "UPDATE dataset_source_connections SET connection_status='revoked' WHERE id=:c",
        c=w.conns["all"],
    )
    _, error = env.training(w, manifest.id)
    check(
        f"after the grant is revoked training access is grant_changed: {getattr(error, 'code', error)!r}",
        getattr(error, "code", None) == "grant_changed",
    )
    env.execute(
        "UPDATE dataset_source_connections SET connection_status='active' WHERE id=:c",
        c=w.conns["all"],
    )
    _, error = env.training(w, manifest.id)
    check(f"the restored grant serves again: {error!r}", error is None)
    r = http_("GET", one, admin)
    check(
        "the locked manifest reads back as locked with its frozen hash",
        r.status == 200
        and r.json()["status"] == "locked"
        and r.json()["frozen_manifest_sha256"] == row[6],
    )

    # --- concurrency on a fresh proposal: 12 identical requests, one lock
    cw_fresh, cds_fresh = fresh_world(env, "conc-identical")
    p = base_path(env.org1, cw_fresh.project_id)
    fresh, error = env.propose(cw_fresh, "concurrent-identical", list(cds_fresh.values()))
    check(f"a proposal for the concurrency test: {error!r}", error is None)
    if error is None:
        cpath = f"{p}/{fresh.id}/approve"
        ckey = new_key("conc")
        cbody = reviews_body(fresh.proposal_sha256, [])
        with concurrent.futures.ThreadPoolExecutor(max_workers=12) as pool:
            results = list(pool.map(lambda _: http_("POST", cpath, admin, cbody, ckey), range(12)))
        statuses = sorted(r.status for r in results)
        replays = sorted(r.json()["replayed"] for r in results if r.status == 200)
        check(f"12 identical concurrent requests are all 200: {statuses}", statuses == [200] * 12)
        check(f"exactly one is not a replay: {replays}", replays == [False] + [True] * 11)
        check(
            "one lock audit row, one stored key, one locked manifest",
            audit_count(env, fresh.id, "dataset_split_manifest.locked") == 1
            and len(request_rows(env, fresh.id)) == 1
            and env.manifest_row(fresh.id)[0] == "locked",
        )

    # --- concurrency, one key, two different requests: one winner, the loser is idempotency_key_reused
    cw_fresh2, cds_fresh2 = fresh_world(env, "conc-keyed")
    p = base_path(env.org1, cw_fresh2.project_id)
    fresh2, error = env.propose(cw_fresh2, "concurrent-keyed", list(cds_fresh2.values()))
    check(f"a second proposal for the concurrency test: {error!r}", error is None)
    if error is None:
        path_a, path_r = f"{p}/{fresh2.id}/approve", f"{p}/{fresh2.id}/reject"
        shared = new_key("conc-shared")
        a_body = reviews_body(fresh2.proposal_sha256, [])
        r_body = {"expected_proposal_sha256": fresh2.proposal_sha256}

        def fire(index: int) -> Resp:
            if index % 2 == 0:
                return http_("POST", path_a, admin, a_body, shared)
            return http_("POST", path_r, admin, r_body, shared)

        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(fire, range(8)))
        final = env.manifest_row(fresh2.id)[0]
        winners = [r for r in results if r.status == 200 and r.json()["replayed"] is False]
        check(f"exactly one request won: {[r.status for r in results]}", len(winners) == 1)
        check(f"the manifest ended locked or rejected: {final}", final in ("locked", "rejected"))
        check(
            "every other 200 is a replay of the winner's request and every loser is idempotency_key_reused",
            all(
                (r.status == 200 and r.json()["replayed"] is True)
                or (r.status == 409 and r.detail == "idempotency_key_reused")
                for r in results
                if r not in winners
            ),
        )
        check("one stored key for the manifest", len(request_rows(env, fresh2.id)) == 1)

    # --- approve vs reject (different keys): exactly one winner
    cw_fresh3, cds_fresh3 = fresh_world(env, "conc-avr")
    p = base_path(env.org1, cw_fresh3.project_id)
    fresh3, error = env.propose(cw_fresh3, "approve-vs-reject", list(cds_fresh3.values()))
    check(f"a third proposal for approve-vs-reject: {error!r}", error is None)
    if error is None:

        def race(index: int) -> Resp:
            if index % 2 == 0:
                return http_(
                    "POST",
                    f"{p}/{fresh3.id}/approve",
                    admin,
                    reviews_body(fresh3.proposal_sha256, []),
                    new_key("avr"),
                )
            return http_(
                "POST",
                f"{p}/{fresh3.id}/reject",
                admin,
                {"expected_proposal_sha256": fresh3.proposal_sha256},
                new_key("avr"),
            )

        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(race, range(8)))
        wins = [r for r in results if r.status == 200 and r.json()["replayed"] is False]
        losers = [r for r in results if r.status == 409]
        check(
            f"approve-vs-reject has exactly one winner: {[r.status for r in results]}",
            len(wins) == 1,
        )
        check(
            "the losers of the other action are 409 manifest_not_proposed",
            all(r.detail == "manifest_not_proposed" for r in losers),
        )
        check(
            "the manifest ended in the winner's state with one stored key",
            env.manifest_row(fresh3.id)[0] in ("locked", "rejected")
            and len(request_rows(env, fresh3.id)) == 1,
        )

    section_raw_requests(env, w, manifest.id)


def section_reject(env: Env, fx: Fixture, http_: Client) -> None:
    w, ds = fresh_world(env, "reject")
    p = base_path(env.org1, w.project_id)
    admin = fx.header("org1", Role.WORKSPACE_ADMIN)
    orgadmin = fx.header("org1", Role.ORGANIZATION_ADMIN)
    manifest, error = env.propose(
        w, "to-reject", list(ds.values()), spec=replace(SPEC, seed="seed-reject")
    )
    check(f"a proposal to reject: {error!r}", error is None)
    if error is not None:
        return
    one = f"{p}/{manifest.id}"
    expected = manifest.proposal_sha256
    r = http_(
        "POST", f"{one}/reject", admin, {"expected_proposal_sha256": "0" * 64}, new_key("rej")
    )
    check(
        "reject with a stale hash is 409 manifest_changed",
        r.status == 409 and r.detail == "manifest_changed",
    )
    check("and stores no key", request_rows(env, manifest.id) == [])
    key = new_key("rej")
    first = http_("POST", f"{one}/reject", orgadmin, {"expected_proposal_sha256": expected}, key)
    check(
        f"an organization_admin may reject: {first.status} {first.detail}",
        first.status == 200
        and first.json()["status"] == "rejected"
        and first.json()["replayed"] is False,
    )
    check(
        "the rejection is stored with the independent digest and audited once",
        [(x[1], x[2], x[3]) for x in request_rows(env, manifest.id)]
        == [("reject", key, digest_of("reject", manifest.id, expected, []))]
        and audit_count(env, manifest.id, "dataset_split_manifest.rejected") == 1
        and env.manifest_row(manifest.id)[0] == "rejected",
    )
    snap = snapshot(env, manifest.id)
    again = http_("POST", f"{one}/reject", orgadmin, {"expected_proposal_sha256": expected}, key)
    check(
        "the same reject again is a 200 replay",
        again.status == 200 and again.json()["replayed"] is True,
    )
    again = http_(
        "POST", f"{one}/reject", admin, {"expected_proposal_sha256": expected}, new_key("rej")
    )
    check(
        "another admin's identical reject under a new key is a replay",
        again.status == 200 and again.json()["replayed"] is True,
    )
    check("replays wrote nothing", snapshot(env, manifest.id) == snap)
    r = http_("POST", f"{one}/reject", orgadmin, {"expected_proposal_sha256": "b" * 64}, key)
    check(
        "the same key with another hash is 409 idempotency_key_reused",
        r.status == 409 and r.detail == "idempotency_key_reused",
    )
    r = http_("POST", f"{one}/approve", admin, reviews_body(expected, []), key)
    check(
        "an approve under a reject's key is 409 idempotency_key_reused",
        r.status == 409 and r.detail == "idempotency_key_reused",
    )
    r = http_("POST", f"{one}/approve", admin, reviews_body(expected, []), new_key("rej"))
    check(
        "a rejected manifest cannot be approved (409 manifest_not_proposed)",
        r.status == 409 and r.detail == "manifest_not_proposed",
    )
    r = http_(
        "POST", f"{one}/reject", admin, {"expected_proposal_sha256": "c" * 64}, new_key("rej")
    )
    check("a rejected manifest with another hash is not a replay (409)", r.status == 409)
    check("nothing else was written", snapshot(env, manifest.id) == snap)


def section_refusals(
    env: Env, fx: Fixture, http_: Client, label: str, scenarios: frozenset[str]
) -> None:
    """Every unclear manifest is refused over the wire and writes nothing. A scenario that locks a
    manifest runs in a project of its own (a locked manifest is a required prior campaign)."""
    w = env.world(label)
    p = base_path(env.org1, w.project_id)
    admin = fx.header("org1", Role.WORKSPACE_ADMIN)
    admin_subject = fx.subject("org1", Role.WORKSPACE_ADMIN)
    train = env.dataset(w, "rf-train", "training", 100)

    def approve(manifest: Any, reviews: list[tuple[str, str]], expected: str | None = None) -> Resp:
        return http_(
            "POST",
            f"{p}/{manifest.id}/approve",
            admin,
            reviews_body(expected or manifest.proposal_sha256, reviews),
            new_key("rf"),
        )

    def refusal(
        label: str, manifest: Any, reviews: list[tuple[str, str]], status: int, code: str
    ) -> None:
        before = snapshot(env, manifest.id)
        r = approve(manifest, reviews)
        check(
            f"{label}: {status} {code} (got {r.status} {r.detail})",
            r.status == status and r.detail == code,
        )
        check(
            f"{label}: nothing was written",
            snapshot(env, manifest.id) == before and env.manifest_row(manifest.id)[0] == "proposed",
        )

    if "block" in scenarios:
        # a blocking finding (the same raw hash in development and final)
        d_b = env.dataset(w, "rf-dev-block", "candidate_development", 6)
        h_b = env.dataset(w, "rf-held-block", "held_out_evaluation", 6)
        env.records[h_b.id] = (
            replace(env.records[d_b.id][0], batch_position=0),
            *env.records[h_b.id][1:],
        )
        blocking, error = env.propose(w, "rf-block", [train, d_b, h_b])
        check(f"a blocking proposal is stored: {error!r}", error is None)
        if error is None:
            found = blocking.leakage_report["findings"]
            blocking_id = next(
                (f["finding_id"] for f in found if f["disposition"] == "blocking"), None
            )
            check("the duplicate is a blocking finding", blocking_id is not None)
            refusal("unresolved blocking leakage", blocking, [], 409, "leakage_not_clear")
            if blocking_id is not None:
                refusal(
                    "a blocking finding cannot be reviewed away",
                    blocking,
                    [(blocking_id, "not the same record")],
                    400,
                    "invalid_review",
                )

    if "route" in scenarios:
        # a blocked scan: zero-egress project, external embedding route
        d_r = env.dataset(w, "rf-dev-route", "candidate_development", 6)
        h_r = env.dataset(w, "rf-held-route", "held_out_evaluation", 6)
        routed, error = env.propose(w, "rf-route", [train, d_r, h_r], route=BLOCKED_ROUTE)
        check(f"a blocked-route proposal is stored: {error!r}", error is None)
        if error is None:
            check("its leakage status is blocked", routed.leakage_status == "blocked")
            refusal("a blocked scan cannot be frozen", routed, [], 409, "leakage_not_clear")

    if "saturated" in scenarios:
        # a saturated semantic scan (215 near-duplicates reach the partner cap)
        base = "alpha bravo charlie delta echo foxtrot golf hotel india juliet kilo lima mike"
        sat_train = env.dataset(w, "rf-sat-train", "training", 215)
        env.records[sat_train.id] = tuple(
            replace(
                env.records[sat_train.id][i],
                content={
                    "messages": [
                        {
                            "role": "user",
                            "content": [{"type": "text", "text": f"{base} variant{i}"}],
                        }
                    ],
                    "metadata": {},
                },
                record_sha256=_sha(f"sat-raw-{i}"),
                normalized_sha256=_sha(f"sat-norm-{i}"),
            )
            for i in range(215)
        )
        sat_val = env.dataset(w, "rf-sat-val", "validation", 10)
        sat_dev = env.dataset(w, "rf-sat-dev", "candidate_development", 4)
        sat_held = env.dataset(w, "rf-sat-held", "held_out_evaluation", 4)
        saturated, error = env.propose(w, "rf-sat", [sat_train, sat_val, sat_dev, sat_held])
        check(f"a saturated proposal is stored: {error!r}", error is None)
        if error is None:
            check(
                f"its scan is blocked as semantic_scan_saturated: {saturated.leakage_status}",
                saturated.leakage_status == "blocked"
                and saturated.leakage_report["reason_code"] == "semantic_scan_saturated",
            )
            refusal("a saturated scan cannot be frozen", saturated, [], 409, "leakage_not_clear")

    if "semantic" in scenarios:
        # a review_required finding: reviewed, partially reviewed, unreviewed
        d_s = env.dataset(w, "rf-dev-sem", "candidate_development", 6)
        h_s = env.dataset(w, "rf-held-sem", "held_out_evaluation", 6)
        env.records[d_s.id] = (_long_record(str(d_s.id), 0), *env.records[d_s.id][1:])
        env.records[h_s.id] = (_variant(env.records[d_s.id][0], "zzzzzz"), *env.records[h_s.id][1:])
        semantic, error = env.propose(w, "rf-sem", [train, d_s, h_s])
        check(f"a review-required proposal is stored: {error!r}", error is None)
        if error is None:
            open_ids = [f["finding_id"] for f in semantic.leakage_report["findings"]]
            check("it has open review_required findings", len(open_ids) >= 1)
            refusal("unreviewed leakage", semantic, [], 409, "leakage_not_clear")
            refusal(
                "a review of a finding that does not exist",
                semantic,
                [("f" * 64, "no such finding")],
                400,
                "invalid_review",
            )
            if len(open_ids) > 1:
                refusal(
                    "a partial review",
                    semantic,
                    [(open_ids[0], "different tickets")],
                    409,
                    "leakage_not_clear",
                )
            reviews = [(fid, "different tickets, same template") for fid in open_ids]
            key = new_key("review")
            r = http_(
                "POST",
                f"{p}/{semantic.id}/approve",
                admin,
                reviews_body(semantic.proposal_sha256, reviews),
                key,
            )
            check(
                f"every finding reviewed locks the manifest ({r.status} {r.detail})",
                r.status == 200 and r.json()["replayed"] is False,
            )
            row = env.manifest_row(semantic.id)
            check(
                "the stored reviews name the authenticated approver, never an input",
                row[0] == "locked"
                and row[5] == admin_subject
                and len(row[11]) == len(open_ids)
                and all(
                    x["reviewer_id"] == admin_subject and x["decision"] == "not_leakage"
                    for x in row[11]
                ),
            )
            check(
                "the stored key's digest covers the reviews",
                [x[3] for x in request_rows(env, semantic.id)]
                == [digest_of("approve", semantic.id, semantic.proposal_sha256, reviews)],
            )
            snap = snapshot(env, semantic.id)
            r = http_(
                "POST",
                f"{p}/{semantic.id}/approve",
                admin,
                reviews_body(semantic.proposal_sha256, list(reversed(reviews))),
                key,
            )
            check(
                "the same key with the reviews in another order is the same request (replay)",
                r.status == 200 and r.json()["replayed"] is True,
            )
            r = http_(
                "POST",
                f"{p}/{semantic.id}/approve",
                admin,
                reviews_body(semantic.proposal_sha256, [(reviews[0][0], "another rationale")]),
                key,
            )
            check(
                "the same key with another rationale is idempotency_key_reused",
                r.status == 409 and r.detail == "idempotency_key_reused",
            )
            check("neither wrote anything", snapshot(env, semantic.id) == snap)

    if "tamper" in scenarios:
        # a tampered stored role hash is refused, then accepted once restored
        d_t = env.dataset(w, "rf-dev-tamper", "candidate_development", 6)
        h_t = env.dataset(w, "rf-held-tamper", "held_out_evaluation", 6)
        tamper, error = env.propose(w, "rf-tamper", [train, d_t, h_t])
        check(f"a proposal to tamper with is stored: {error!r}", error is None)
        if error is None:
            good = env.scalar(
                "SELECT role_sha256 FROM dataset_split_manifest_roles WHERE manifest_id=:m AND role='train'",
                m=tamper.id,
            )
            env.execute(
                "UPDATE dataset_split_manifest_roles SET role_sha256=:x WHERE manifest_id=:m AND role='train'",
                x="0" * 64,
                m=tamper.id,
            )
            refusal("a tampered role hash", tamper, [], 409, "manifest_tampered")
            env.execute(
                "UPDATE dataset_split_manifest_roles SET role_sha256=:x WHERE manifest_id=:m AND role='train'",
                x=good,
                m=tamper.id,
            )
            r = approve(tamper, [])
            check(f"the restored manifest locks (control): {r.status} {r.detail}", r.status == 200)

    if "insufficient" in scenarios:
        # an empty role: a legacy three-split manifest without a development dataset
        l_train = env.dataset(w, "rf-l-train", "training", 30)
        l_held = env.dataset(w, "rf-l-held", "held_out_evaluation", 6)

        def rid(dataset: Any, position: int) -> str:
            return f"{dataset.id}:{position}"

        assignments = tuple(
            sorted(
                [(rid(l_train, i), "train", f"g{i}") for i in range(14)]
                + [(rid(l_train, i), "validation", f"g{i}") for i in range(14, 20)]
                + [(rid(l_held, i), "heldout_test", f"h{i}") for i in range(6)]
            )
        )
        legacy = ism.LegacyThreeSplitManifest(
            "legacy-seed", assignments, ism.compute_legacy_sha256("legacy-seed", assignments)
        )
        insufficient, error = env.run(
            w.ctx,
            lambda s: store.migrate_legacy_split_manifest(
                s,
                w.ctx,
                project_id=w.project_id,
                name="rf-insufficient",
                legacy=legacy,
                sources=[env.source(x) for x in (l_train, l_held)],
                development_dataset_id=None,
                prior_sources=(),
                route=ROUTE,
                normalization_version="text-normalization-v1",
                tokenizer_identity=None,
                actor=env.admin,
            ),
        )
        check(f"a legacy proposal without development is stored: {error!r}", error is None)
        if error is None:
            refusal("an empty role", insufficient, [], 409, "manifest_insufficient")


# ---------------------------------------------------------------------------------------- canary


def canary_scan(env: Env) -> None:
    tokens: dict[str, str] = {}
    for (record_id,) in env.rows("SELECT record_id FROM dataset_split_assignments"):
        tokens[record_id] = "record id"
    for (group_id,) in env.rows("SELECT DISTINCT group_id FROM dataset_split_assignments"):
        tokens[group_id] = "group id"
    for (key,) in env.rows("SELECT object_key FROM dataset_upload_sessions"):
        tokens[key] = "storage key"
    for (report,) in env.rows("SELECT leakage_report::text FROM dataset_split_manifests"):
        for finding in json.loads(report).get("findings", []):
            tokens[finding["finding_id"]] = "finding id"
    for (reviews,) in env.rows("SELECT leakage_reviews::text FROM dataset_split_manifests"):
        for review in json.loads(reviews):
            tokens[review["rationale"]] = "review rationale"
    tokens["different tickets, same template"] = "review rationale"
    check(f"the canary set is not empty ({len(tokens)} tokens)", len(tokens) > 1000)
    check(f"every route call was recorded ({len(BODIES)} bodies)", len(BODIES) > 120)

    def strings_of(node: Any) -> set[str]:
        if isinstance(node, str):
            return {node}
        if isinstance(node, dict):
            return {x for k, v in node.items() for x in (strings_of(k) | strings_of(v))}
        if isinstance(node, list):
            return {x for v in node for x in strings_of(v)}
        return set()

    def find_leaks(blobs: list[tuple[str, str]]) -> list[tuple[str, str, str]]:
        found = []
        for where, blob in blobs:
            try:
                values = strings_of(json.loads(blob))
            except ValueError:
                values = set()
            for token, why in tokens.items():
                # a short token (a group id like "g1") is matched as a whole JSON string; a long
                # one anywhere in the body
                if token in values or (len(token) >= 16 and token in blob):
                    found.append((where, why, token))
        return found

    leaked = find_leaks(BODIES)
    check(
        f"no response body ever carries a record id, group id, finding id, review text or storage key: {leaked[:3]}",
        not leaked,
    )
    # positive controls: the scan really finds each kind of canary
    for kind in ("record id", "group id", "storage key", "finding id"):
        sample = next(t for t, why in tokens.items() if why == kind)
        check(
            f"canary control: the scan finds a planted {kind}",
            bool(
                find_leaks(
                    [
                        (
                            "planted",
                            json.dumps({"v": f"x {sample} y" if len(sample) >= 16 else sample}),
                        )
                    ]
                )
            ),
        )


# ---------------------------------------------------------------------------------------- main


def main() -> None:
    migration_url = _required_env("SOVRA_DATABASE_URL")
    app_password = _required_env("SOVRA_APP_DB_PASSWORD")

    for args, label in (
        (["upgrade", "head"], "alembic upgrade head"),
        (["downgrade", "-1"], "alembic downgrade -1"),
        (["upgrade", "head"], "alembic upgrade head after downgrade -1"),
    ):
        migrated = _alembic(args, migration_url)
        check(
            f"{label} runs cleanly (exit {migrated.returncode})\n{migrated.stdout}\n{migrated.stderr}",
            migrated.returncode == 0,
        )
        if migrated.returncode != 0:
            finish()
    heads = _alembic(["heads"], migration_url)
    check(f"exactly one Alembic head: {heads.stdout!r}", heads.stdout.count("(head)") == 1)
    history = _alembic(["history"], migration_url)
    check(
        "migration 0032 is on the single head's chain",
        "0032_split_manifest_requests" in history.stdout,
    )

    env = Env()
    env.owner = create_engine(migration_url)
    app_engine = create_engine(_app_role_url(migration_url, app_password))
    check("the app engine is the restricted sovra_app role", app_engine.url.username == "sovra_app")
    env.factory = sessionmaker(bind=app_engine, expire_on_commit=False)
    check_catalog(env)
    seed(env)
    fx = Fixture(env.org1, env.org2)
    app = FastAPI()
    app.include_router(
        build_dataset_split_manifest_router(
            env.factory, principal_resolver=fx.principal_resolver, role_lookup=fx.role_lookup
        )
    )
    w, ds, manifest = make_clear_world(env, "routes")
    with running_app(app) as url:
        http_ = Client(url)
        section_matrix(env, fx, http_, w, manifest)
        section_paging(env, fx, http_)
        section_clear_lock(env, fx, http_, w, ds, manifest)
        section_reject(env, fx, http_)
        section_refusals(
            env, fx, http_, "refusals", frozenset({"block", "route", "saturated", "insufficient"})
        )
        section_refusals(env, fx, http_, "refusals-review", frozenset({"semantic"}))
        section_refusals(env, fx, http_, "refusals-tamper", frozenset({"tamper"}))
    canary_scan(env)
    finish()


def finish() -> None:
    if FAILURES:
        print(f"FTF-0519-P2 split manifest routes real proof: {len(FAILURES)} failed")
        for failure in FAILURES:
            print(f"  {failure}")
        sys.exit(1)
    print("FTF-0519-P2 split manifest routes real proof: all checks passed")
    sys.exit(0)


if __name__ == "__main__":
    main()
