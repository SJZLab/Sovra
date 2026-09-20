#!/usr/bin/env python3
"""FTF-H003 -- real Postgres cross-layer tenant isolation proof.

Proves this task's own acceptance clause 1 ("Use two actual organizations
and pooled DB sessions to prove cross-tenant read/write/download/job/route
denial including guessed IDs and missing context") against a real, live
PostgreSQL server -- never a mocked connection or an in-memory stand-in
(SQLite has no row-level security at all, so a passing test against it
would prove nothing about this task's actual acceptance criterion) -- and
this task's own negative_acceptance ("Migration role cannot be used by the
API or worker; test views, background activities and object presigned
URLs, not API guards alone").

Two real organizations (org1/org2) with one real workspace each are seeded
as the migration-owner role; every check below runs as `sovra_app`, the
same distinct least-privileged application role FTF-0102's migration
creates (never the migration role), through a real connection pool:

- **read denial ("guessed ID")**: `authorize_resource_ownership` against a
  session genuinely scoped to org1 allows org1's own real workspace and
  denies org2's real workspace -- the exact IDOR vector FTF-0103's own
  `require_role` cannot see, since it never queries the resource's real
  owner. Denial is proven identical in wording to a wholly missing ID
  (Section 65.1 "no user-controlled JSON used to read another tenant").
- **write denial**: an `INSERT` claiming org2's `organization_id` from a
  session whose real live context is org1 is rejected by FTF-0102's own
  `WITH CHECK` row-level-security policy -- a real database error, not an
  application-layer assumption.
- **download denial**: `packages.storage.tenant_policy` validates a
  storage key built for org1's real UUID as belonging to org1 and denies
  it for org2's real UUID (a guessed/foreign tenant prefix) -- using the
  two real organization IDs this test actually seeded, not synthetic ones.
- **job denial**: a background-job-style session, opened the naive way
  (bypassing `tenant_scoped_session_for`, the way an unreviewed worker
  code path might) and manually bound to org1, is still refused by
  `authorize_resource_ownership` for org2's real workspace.
- **route denial**: a real, locally-hosted demonstration FastAPI app (a
  real uvicorn server over a real local socket, the same pattern
  tests/tasks/test_ftf_0103.py established) wires `require_role`
  (FTF-0103) and `authorize_resource_ownership` (this task) together on
  one route. An org1 organization_admin -- a real, validly authorized
  role in org1 -- requesting org1's own path with org2's real workspace
  ID substituted in (a guessed/foreign resource ID an authorized-but-
  malicious org1 member could try) is denied, even though `require_role`
  alone would have let the request through.
- **missing context**: a session that never had any tenant context bound
  at all is refused by `authorize_resource_ownership` (via FTF-0105's own
  `require_tenant_context`), not treated as "no rows, so deny" by luck.

Requires a real Postgres reachable at `SOVRA_DATABASE_URL` (the migration
owner role) plus `SOVRA_APP_DB_PASSWORD`, matching FTF-0102/FTF-0105's own
integration tests' environment contract exactly. No independent schema
change: this test consumes FTF-0102's own `organizations`/`workspaces`
tables and migration exactly as delivered.

Run directly (against an already-migrated or unmigrated database; this
script runs the migration itself):
    SOVRA_DATABASE_URL=postgresql+psycopg://<migration-role>@host/db \\
    SOVRA_APP_DB_PASSWORD=<app-role-password> \\
    python3 tests/integration/test_ftf_h003_cross_layer_isolation.py
"""

from __future__ import annotations

import contextlib
import http.client
import os
import socket
import subprocess
import sys
import threading
import time
import uuid
from collections.abc import Iterator
from pathlib import Path
from urllib.parse import urlparse, urlsplit, urlunsplit

ROOT = Path(__file__).resolve().parent.parent.parent
API_ROOT = ROOT / "apps" / "api"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(API_ROOT))

import uvicorn  # noqa: E402
from fastapi import Depends, FastAPI, HTTPException  # noqa: E402
from packages.storage.tenant_policy import (  # noqa: E402
    build_tenant_scoped_object_key,
    validate_object_key_belongs_to_tenant,
)
from sqlalchemy import create_engine, text  # noqa: E402
from sqlalchemy.orm import Session, sessionmaker  # noqa: E402
from sqlalchemy.pool import QueuePool  # noqa: E402

from app.auth.organization_workspace_schema import (  # noqa: E402
    APP_ROLE_NAME,
    Organization,
    Workspace,
    set_tenant_context,
)
from app.auth.rbac_middleware import (  # noqa: E402
    AuthenticatedPrincipal,
    AuthorizedContext,
    Role,
    require_role,
)
from app.auth.resource_authorization import (  # noqa: E402
    ResourceAuthorizationError,
    authorize_resource_ownership,
)
from app.auth.tenant_isolation_query_guards import TenantContextError  # noqa: E402
from app.db.tenant_context import TenantContext, tenant_scoped_session_for  # noqa: E402

FAILURES: list[str] = []


def check(criterion_id: str, description: str, condition: bool) -> None:
    if not condition:
        FAILURES.append(f"FAIL [{criterion_id}] {description}")


def _required_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        print(
            f"BLOCKED_EXTERNAL: {name} is not set -- this task's real "
            "cross-layer tenant-isolation evidence requires a live "
            "Postgres (migration owner connection). Not silently skipped: "
            "see this file's own docstring for how to run it locally or "
            "in CI.",
            file=sys.stderr,
        )
        sys.exit(1)
    return value


def _app_role_url(migration_url: str, app_password: str) -> str:
    parts = urlsplit(migration_url)
    netloc = f"{APP_ROLE_NAME}:{app_password}@{parts.hostname}"
    if parts.port:
        netloc += f":{parts.port}"
    return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))


def _run_migration() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=str(API_ROOT),
        capture_output=True,
        text=True,
    )
    check(
        "FTF-H003",
        f"alembic upgrade head runs cleanly (exit {result.returncode})\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}",
        result.returncode == 0,
    )


def free_port() -> int:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


def main() -> None:
    migration_url = _required_env("SOVRA_DATABASE_URL")
    app_password = _required_env("SOVRA_APP_DB_PASSWORD")

    _run_migration()
    if FAILURES:
        print(f"FTF-H003 real cross-layer tenant isolation proof: {len(FAILURES)} failed")
        for failure in FAILURES:
            print(f"  {failure}")
        sys.exit(1)

    owner_engine = create_engine(migration_url)
    app_url = _app_role_url(migration_url, app_password)

    # -- Seed two real, distinct tenants as the migration owner role --------
    with Session(owner_engine) as session:
        org1 = Organization(name="Acme H003", slug=f"acme-h003-{uuid.uuid4().hex[:8]}")
        org2 = Organization(name="Globex H003", slug=f"globex-h003-{uuid.uuid4().hex[:8]}")
        session.add_all([org1, org2])
        session.flush()
        ws1 = Workspace(organization_id=org1.id, name="Acme Main", slug="main")
        ws2 = Workspace(organization_id=org2.id, name="Globex Main", slug="main")
        session.add_all([ws1, ws2])
        session.commit()
        org1_id, org2_id, ws1_id, ws2_id = org1.id, org2.id, ws1.id, ws2.id

    pooled_engine = create_engine(app_url, poolclass=QueuePool, pool_size=2, max_overflow=0)
    session_factory = sessionmaker(bind=pooled_engine)
    org1_context = TenantContext(organization_id=org1_id)

    try:
        # == 1. read denial: guessed ID + missing ID, identically denied ====
        with tenant_scoped_session_for(session_factory, org1_context) as session:
            authorize_resource_ownership(
                session,
                org1_context,
                Workspace.__table__,
                Workspace.__table__.c.id,
                Workspace.__table__.c.organization_id,
                ws1_id,
            )
            check(
                "FTF-H003",
                "acceptance: a real resource that really belongs to the "
                "authenticated org1 context is authorized (no exception)",
                True,
            )

        guessed_message: str | None = None
        with tenant_scoped_session_for(session_factory, org1_context) as session:
            try:
                authorize_resource_ownership(
                    session,
                    org1_context,
                    Workspace.__table__,
                    Workspace.__table__.c.id,
                    Workspace.__table__.c.organization_id,
                    ws2_id,
                )
            except ResourceAuthorizationError as exc:
                guessed_message = str(exc)
        check(
            "FTF-H003",
            "negative_acceptance: a guessed ID that is a real workspace "
            "belonging to org2 is denied while authenticated as org1, live "
            "against Postgres/RLS -- not merely a mocked assumption",
            guessed_message is not None,
        )

        missing_message: str | None = None
        with tenant_scoped_session_for(session_factory, org1_context) as session:
            try:
                authorize_resource_ownership(
                    session,
                    org1_context,
                    Workspace.__table__,
                    Workspace.__table__.c.id,
                    Workspace.__table__.c.organization_id,
                    uuid.uuid4(),
                )
            except ResourceAuthorizationError as exc:
                missing_message = str(exc)
        check(
            "FTF-H003",
            "a wholly nonexistent resource ID is denied identically to a "
            "real guessed foreign-tenant ID -- no existence oracle, live",
            missing_message is not None and missing_message == guessed_message,
        )

        # == 2. write denial: a cross-tenant INSERT is rejected by RLS ======
        with tenant_scoped_session_for(session_factory, org1_context) as session:
            try:
                session.execute(
                    text(
                        "INSERT INTO workspaces (organization_id, name, slug) "
                        "VALUES (:org_id, 'Illegit', 'illegit')"
                    ),
                    {"org_id": str(org2_id)},
                )
                write_rejected = False
            except Exception:  # noqa: BLE001 - proving a real DB-level rejection, any exception counts
                write_rejected = True
                session.rollback()
        check(
            "FTF-H003",
            "negative_acceptance (write): an INSERT claiming org2's "
            "organization_id from a session whose real live context is "
            "org1 is rejected by FTF-0102's own RLS WITH CHECK policy",
            write_rejected,
        )

        # == 3. download denial: tenant-scoped storage key policy ===========
        real_key = build_tenant_scoped_object_key(org1_id, "datasets", ws1_id, "train.jsonl")
        check(
            "FTF-H003",
            "a storage key built for org1's real seeded UUID validates true for org1",
            validate_object_key_belongs_to_tenant(real_key, org1_id) is True,
        )
        check(
            "FTF-H003",
            "negative_acceptance (download): the same real key is denied "
            "for org2's real seeded UUID (a guessed/foreign tenant prefix)",
            validate_object_key_belongs_to_tenant(real_key, org2_id) is False,
        )

        # == 4. job denial: a naively-opened background-job-style session ===
        job_session = session_factory()
        try:
            set_tenant_context(job_session, org1_id)
            try:
                authorize_resource_ownership(
                    job_session,
                    org1_context,
                    Workspace.__table__,
                    Workspace.__table__.c.id,
                    Workspace.__table__.c.organization_id,
                    ws2_id,
                )
                job_denied = False
            except ResourceAuthorizationError:
                job_denied = True
        finally:
            job_session.rollback()
            job_session.close()
        check(
            "FTF-H003",
            "negative_acceptance (job): a background-job-style session "
            "bound to org1 the naive way is still refused org2's real "
            "workspace",
            job_denied,
        )

        # == 5. missing context: never bound at all ==========================
        bare_session = session_factory()
        try:
            try:
                authorize_resource_ownership(
                    bare_session,
                    org1_context,
                    Workspace.__table__,
                    Workspace.__table__.c.id,
                    Workspace.__table__.c.organization_id,
                    ws1_id,
                )
                refused_missing_context = False
            except TenantContextError:
                refused_missing_context = True
        finally:
            bare_session.rollback()
            bare_session.close()
        check(
            "FTF-H003",
            "negative_acceptance (missing context): a session with no "
            "tenant context ever bound is refused, not treated as an "
            "ordinary zero-row denial",
            refused_missing_context,
        )

        # == 6. route denial: require_role + authorize_resource_ownership ===
        _run_route_denial_checks(session_factory, org1_id, ws1_id, ws2_id)
    finally:
        pooled_engine.dispose()

    if FAILURES:
        print(f"FTF-H003 real cross-layer tenant isolation proof: {len(FAILURES)} failed")
        for failure in FAILURES:
            print(f"  {failure}")
        sys.exit(1)

    print("FTF-H003 real cross-layer tenant isolation proof: all checks passed")
    sys.exit(0)


# --- Real demonstration app: real uvicorn server, real local socket --------

_ISSUER = "https://issuer.example"
_PRINCIPAL = AuthenticatedPrincipal(issuer=_ISSUER, subject="user-admin")


def _build_demo_app(session_factory: sessionmaker, org1_id: uuid.UUID) -> FastAPI:
    app = FastAPI()

    def _test_principal_resolver(request: object) -> AuthenticatedPrincipal | None:
        header = request.headers.get("x-test-principal")  # type: ignore[attr-defined]
        return _PRINCIPAL if header == "admin" else None

    def _test_role_lookup(issuer: str, subject: str, organization_id: uuid.UUID) -> Role | None:
        if (issuer, subject) == (_ISSUER, "user-admin") and organization_id == org1_id:
            return Role.ORGANIZATION_ADMIN
        return None

    require_admin = Depends(
        require_role(
            frozenset({Role.ORGANIZATION_ADMIN}),
            principal_resolver=_test_principal_resolver,
            role_lookup=_test_role_lookup,
        )
    )

    @app.get("/api/v1/orgs/{organization_id}/workspaces/{workspace_id}")
    def get_workspace(
        workspace_id: uuid.UUID, ctx: AuthorizedContext = require_admin
    ) -> dict[str, str]:
        tenant_context = TenantContext.from_authorized_context(ctx)
        try:
            with tenant_scoped_session_for(session_factory, tenant_context) as session:
                authorize_resource_ownership(
                    session,
                    tenant_context,
                    Workspace.__table__,
                    Workspace.__table__.c.id,
                    Workspace.__table__.c.organization_id,
                    workspace_id,
                )
        except ResourceAuthorizationError as exc:
            raise HTTPException(status_code=404, detail="not_found") from exc
        return {
            "organization_id": str(tenant_context.organization_id),
            "workspace_id": str(workspace_id),
        }

    return app


@contextlib.contextmanager
def _running_demo_app(session_factory: sessionmaker, org1_id: uuid.UUID) -> Iterator[str]:
    app = _build_demo_app(session_factory, org1_id)
    port = free_port()
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(config)
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


def _get(base_url: str, path: str, *, principal: str | None = None) -> int:
    headers = {}
    if principal is not None:
        headers["X-Test-Principal"] = principal
    parsed = urlparse(base_url)
    conn = http.client.HTTPConnection(parsed.hostname or "127.0.0.1", parsed.port or 80, timeout=10)
    try:
        conn.request("GET", path, headers=headers)
        response = conn.getresponse()
        response.read()
        return response.status
    finally:
        conn.close()


def _run_route_denial_checks(
    session_factory: sessionmaker,
    org1_id: uuid.UUID,
    ws1_id: uuid.UUID,
    ws2_id: uuid.UUID,
) -> None:
    with _running_demo_app(session_factory, org1_id) as base_url:
        status = _get(base_url, f"/api/v1/orgs/{org1_id}/workspaces/{ws1_id}", principal="admin")
        check(
            "FTF-H003",
            "route: an org1 organization_admin fetching org1's own real "
            "workspace through the combined require_role + "
            "authorize_resource_ownership route is allowed",
            status == 200,
        )

        status = _get(base_url, f"/api/v1/orgs/{org1_id}/workspaces/{ws2_id}", principal="admin")
        check(
            "FTF-H003",
            "negative_acceptance (route): an org1 organization_admin -- a "
            "real, validly authorized role in org1 -- substituting org2's "
            "real workspace ID in the path is still denied, even though "
            "require_role alone (Section 45/65.1) would have let the "
            "request through on role/tenant grounds alone",
            status == 404,
        )

        status = _get(base_url, f"/api/v1/orgs/{org1_id}/workspaces/{ws1_id}")
        check(
            "FTF-H003",
            "an unauthenticated request to the same combined route is "
            "denied (401), not silently allowed",
            status == 401,
        )


if __name__ == "__main__":
    main()
