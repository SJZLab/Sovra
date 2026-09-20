#!/usr/bin/env python3
"""FTF-0106 -- real Postgres RBAC/IDOR two-tenant, six-role security matrix.

Proves this task's own acceptance clause 1 ("Full real two-tenant role
matrix exercises reads/writes... with guessed IDs") against a real, live
PostgreSQL server -- never a mocked connection or an in-memory stand-in
(SQLite has no row-level security at all, so a passing test against it
would prove nothing about FTF-0102's own FORCE-RLS enforcement this
matrix runs on top of) -- and this task's own negative_acceptance ("No
authorization test exclusively uses privileged DB connection or mocked
membership result").

Two real organizations (org1/org2) with one real workspace each are
seeded as the migration-owner role. Every one of Section 6.4's six real
roles is granted a real row in
`app.auth.rbac_idor_security_tests.security_test_memberships` for org1
(so `postgres_backed_role_lookup` genuinely resolves each one via a live
SQL `SELECT`, never a Python conditional standing in for one), and every
check below runs as `sovra_app` (never the migration-owner role) through
a real connection pool and a real, locally-hosted demonstration FastAPI
app (a real uvicorn server over a real local socket, the same pattern
`tests/tasks/test_ftf_0103.py` and
`tests/integration/test_ftf_h003_cross_layer_isolation.py` already
established):

- **reads, full role matrix**: every one of the six real roles, each
  genuinely resolved via a live database row, can read org1's own real
  workspace.
- **reads, guessed ID (IDOR)**: every one of the six real roles --
  including `organization_admin`, the most privileged real role -- is
  still denied (404) when the same request substitutes org2's real
  workspace ID, even though each role is genuinely, validly authorized in
  org1.
- **writes, role-scoped**: only `workspace_admin`/`organization_admin`
  can rename org1's own real workspace; the other four real roles are
  each denied (403), genuinely resolved via a live row, not merely absent
  (a role a caller does not hold at all would be indistinguishable from
  one explicitly denied -- this proves the *denial*, not just an absence).
- **writes, guessed ID (IDOR)**: an org1 `workspace_admin` -- a real,
  validly authorized elevated role in org1 -- substituting org2's real
  workspace ID is still denied (404), even though `require_role` alone
  would have let the request through on role/tenant grounds.
- **negative_acceptance (no mocked membership)**: role resolution never
  once uses a Python dict/conditional in this file -- it is always the
  real `postgres_backed_role_lookup`, querying real seeded rows. Proven
  further by an own fault injection: deleting one role's real row and
  confirming that role is denied by the *same* code path used for every
  other role, not a special-cased mock.
- **negative_acceptance (no privileged-connection-only evidence)**: the
  demonstration app's own database session is always opened through the
  pooled `sovra_app`-role engine; only the initial seed step uses the
  migration-owner connection, the same convention every prior
  integration test in this repository (FTF-0102/0104/0105/H003) already
  established.

Disclosed, not hidden: this task's own acceptance clause 1 also names
"artifacts" and "approvals". No live `TrainableArtifact`/`Dataset`/
`PromotionDecision` table or route exists anywhere in this repository yet
(Section 6.7/6.10/6.21 are domain-model text only) -- this suite's real
evidence is scoped to reads/writes against the only two real
tenant-scoped resources that exist (`organizations`/`workspaces`,
FTF-0102), the same disclosed scoping precedent FTF-0105/FTF-H003 already
established.

Requires a real Postgres reachable at `SOVRA_DATABASE_URL` (the migration
owner role) plus `SOVRA_APP_DB_PASSWORD`, matching FTF-0102/FTF-0105/
FTF-H003's own integration tests' environment contract exactly. No
independent schema change to the frozen production migration chain: this
test's own harness table is created/dropped directly by this file (see
`app.auth.rbac_idor_security_tests`'s own module docstring), never through
`apps/api/alembic/versions/`.

Run directly (against an already-migrated or unmigrated database; this
script runs the migration itself):
    SOVRA_DATABASE_URL=postgresql+psycopg://<migration-role>@host/db \\
    SOVRA_APP_DB_PASSWORD=<app-role-password> \\
    python3 tests/integration/test_ftf_0106_rbac_idor_security_matrix.py
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
import urllib.parse
import uuid
from collections.abc import Iterator
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

ROOT = Path(__file__).resolve().parent.parent.parent
API_ROOT = ROOT / "apps" / "api"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(API_ROOT))

import uvicorn  # noqa: E402
from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import Session, sessionmaker  # noqa: E402
from sqlalchemy.pool import QueuePool  # noqa: E402

from app.auth.organization_workspace_schema import (  # noqa: E402
    APP_ROLE_NAME,
    Organization,
    Workspace,
)
from app.auth.rbac_idor_security_tests import (  # noqa: E402
    ROLE_MATRIX,
    WORKSPACE_WRITE_ALLOWED_ROLES,
    all_roles_except,
    build_rbac_idor_demo_app,
    create_security_test_membership_table,
    drop_security_test_membership_table,
    postgres_backed_role_lookup,
    security_test_memberships,
)
from app.auth.rbac_middleware import AuthenticatedPrincipal, Role  # noqa: E402

FAILURES: list[str] = []


def check(criterion_id: str, description: str, condition: bool) -> None:
    if not condition:
        FAILURES.append(f"FAIL [{criterion_id}] {description}")


def _required_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        print(
            f"BLOCKED_EXTERNAL: {name} is not set -- this task's real "
            "two-tenant role-matrix evidence requires a live Postgres "
            "(migration owner connection). Not silently skipped: see this "
            "file's own docstring for how to run it locally or in CI.",
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
        "FTF-0106",
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


_ISSUER = "https://issuer.example"


def _principal_for(role: Role) -> AuthenticatedPrincipal:
    return AuthenticatedPrincipal(issuer=_ISSUER, subject=f"user-{role.value}")


def _resolver_for(role: Role):
    principal = _principal_for(role)

    def _resolve(request: object) -> AuthenticatedPrincipal | None:
        return principal

    return _resolve


@contextlib.contextmanager
def _running_app(app) -> Iterator[str]:  # noqa: ANN001
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


def _request(base_url: str, method: str, path: str) -> int:
    parsed = urllib.parse.urlparse(base_url)
    conn = http.client.HTTPConnection(parsed.hostname or "127.0.0.1", parsed.port or 80, timeout=10)
    try:
        conn.request(method, path)
        response = conn.getresponse()
        response.read()
        return response.status
    finally:
        conn.close()


def main() -> None:
    migration_url = _required_env("SOVRA_DATABASE_URL")
    app_password = _required_env("SOVRA_APP_DB_PASSWORD")

    _run_migration()
    if FAILURES:
        _report_and_exit()

    owner_engine = create_engine(migration_url)
    create_security_test_membership_table(owner_engine)

    app_url = _app_role_url(migration_url, app_password)
    pooled_engine = create_engine(app_url, poolclass=QueuePool, pool_size=4, max_overflow=0)
    session_factory = sessionmaker(bind=pooled_engine)

    try:
        # -- Seed two real, distinct tenants + one real workspace each, as
        # the migration-owner role. -----------------------------------------
        with Session(owner_engine) as session:
            org1 = Organization(name="Acme 0106", slug=f"acme-0106-{uuid.uuid4().hex[:8]}")
            org2 = Organization(name="Globex 0106", slug=f"globex-0106-{uuid.uuid4().hex[:8]}")
            session.add_all([org1, org2])
            session.flush()
            ws1 = Workspace(organization_id=org1.id, name="Acme Main", slug="main")
            ws2 = Workspace(organization_id=org2.id, name="Globex Main", slug="main")
            session.add_all([ws1, ws2])
            session.flush()
            # Every one of Section 6.4's six real roles gets a real,
            # seeded membership row in org1 -- this is the "full real
            # two-tenant role matrix", not a single representative role.
            for role in ROLE_MATRIX:
                session.execute(
                    security_test_memberships.insert().values(
                        issuer=_ISSUER,
                        subject=f"user-{role.value}",
                        organization_id=org1.id,
                        role=role.value,
                    )
                )
            session.commit()
            org1_id, ws1_id, ws2_id = org1.id, ws1.id, ws2.id

        role_lookup = postgres_backed_role_lookup(session_factory)

        # Section 84.4: "Count and verify expected tests; zero collected
        # tests is failure." A fault that collapses one of these role sets
        # (e.g. broadening WORKSPACE_WRITE_ALLOWED_ROLES to all six roles,
        # which empties all_roles_except's complement to zero) must not be
        # able to silently vanish the loop below it into a vacuous pass --
        # each expected count is asserted before its loop runs.
        write_allowed = list(WORKSPACE_WRITE_ALLOWED_ROLES)
        write_denied = list(all_roles_except(WORKSPACE_WRITE_ALLOWED_ROLES))
        check(
            "FTF-0106",
            f"exactly 6 real roles and a genuine 2/4 write-allowed/denied "
            f"split are exercised, not a fault-collapsed empty set "
            f"(matrix={len(ROLE_MATRIX)}, allowed={len(write_allowed)}, "
            f"denied={len(write_denied)})",
            len(ROLE_MATRIX) == 6 and len(write_allowed) == 2 and len(write_denied) == 4,
        )

        # == 1. reads: every real role can read org1's own real workspace ===
        for role in ROLE_MATRIX:
            app = build_rbac_idor_demo_app(
                session_factory, principal_resolver=_resolver_for(role), role_lookup=role_lookup
            )
            with _running_app(app) as base_url:
                status = _request(base_url, "GET", f"/api/v1/orgs/{org1_id}/workspaces/{ws1_id}")
            check(
                "FTF-0106",
                f"acceptance (read matrix): role {role.value!r}, genuinely "
                "resolved via a real seeded row, can read org1's own real "
                "workspace",
                status == 200,
            )

        # == 2. reads, guessed ID: every real role is denied org2's real ID =
        for role in ROLE_MATRIX:
            app = build_rbac_idor_demo_app(
                session_factory, principal_resolver=_resolver_for(role), role_lookup=role_lookup
            )
            with _running_app(app) as base_url:
                status = _request(base_url, "GET", f"/api/v1/orgs/{org1_id}/workspaces/{ws2_id}")
            check(
                "FTF-0106",
                f"negative_acceptance (guessed ID, read): role {role.value!r} "
                "-- a real, validly authorized role in org1 -- substituting "
                "org2's real workspace ID is denied (404) even though "
                "require_role alone would have let the request through",
                status == 404,
            )

        # == 3. writes: only workspace_admin/organization_admin succeed =====
        for role in write_allowed:
            app = build_rbac_idor_demo_app(
                session_factory, principal_resolver=_resolver_for(role), role_lookup=role_lookup
            )
            with _running_app(app) as base_url:
                status = _request(base_url, "PATCH", f"/api/v1/orgs/{org1_id}/workspaces/{ws1_id}")
            check(
                "FTF-0106",
                f"acceptance (write matrix): role {role.value!r} can write "
                "org1's own real workspace",
                status == 200,
            )

        # == 4. writes, role-scoped denial: the other four real roles =======
        for role in write_denied:
            app = build_rbac_idor_demo_app(
                session_factory, principal_resolver=_resolver_for(role), role_lookup=role_lookup
            )
            with _running_app(app) as base_url:
                status = _request(base_url, "PATCH", f"/api/v1/orgs/{org1_id}/workspaces/{ws1_id}")
            check(
                "FTF-0106",
                f"negative_acceptance (write, role matrix): role "
                f"{role.value!r} -- a real, validly authorized role in org1, "
                "genuinely resolved via a real seeded row, not merely "
                "absent -- is denied (403) writing org1's own real "
                "workspace",
                status == 403,
            )

        # == 5. writes, guessed ID: an authorized writer, foreign ID ========
        writer_role = Role.WORKSPACE_ADMIN
        app = build_rbac_idor_demo_app(
            session_factory,
            principal_resolver=_resolver_for(writer_role),
            role_lookup=role_lookup,
        )
        with _running_app(app) as base_url:
            status = _request(base_url, "PATCH", f"/api/v1/orgs/{org1_id}/workspaces/{ws2_id}")
        check(
            "FTF-0106",
            "negative_acceptance (guessed ID, write): an org1 "
            "workspace_admin -- a real, validly authorized elevated role in "
            "org1 -- substituting org2's real workspace ID is denied (404), "
            "even though require_role alone would have let the request "
            "through on role/tenant grounds",
            status == 404,
        )

        # == 6. negative_acceptance: a deleted role's row is denied the ====
        # same way as any other role that never had one -- proving the
        # lookup is a live query, not a cached/mocked decision.
        with Session(owner_engine) as session:
            session.execute(
                security_test_memberships.delete().where(
                    security_test_memberships.c.subject == f"user-{Role.EVALUATOR.value}"
                )
            )
            session.commit()
        app = build_rbac_idor_demo_app(
            session_factory,
            principal_resolver=_resolver_for(Role.EVALUATOR),
            role_lookup=role_lookup,
        )
        with _running_app(app) as base_url:
            status = _request(base_url, "GET", f"/api/v1/orgs/{org1_id}/workspaces/{ws1_id}")
        check(
            "FTF-0106",
            "fault injection: deleting a role's real row makes the exact "
            "same live lookup deny that principal for real (403) -- proving "
            "postgres_backed_role_lookup queries live state, never a cached "
            "or mocked membership result",
            status == 403,
        )
    finally:
        drop_security_test_membership_table(owner_engine)
        pooled_engine.dispose()

    _report_and_exit()


def _report_and_exit() -> None:
    if FAILURES:
        print(f"FTF-0106 real RBAC/IDOR security matrix: {len(FAILURES)} failed")
        for failure in FAILURES:
            print(f"  {failure}")
        sys.exit(1)
    print("FTF-0106 real RBAC/IDOR security matrix: all checks passed")
    sys.exit(0)


if __name__ == "__main__":
    main()
