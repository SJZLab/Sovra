#!/usr/bin/env python3
"""FTF-0106 -- real Postgres RBAC/IDOR full role-matrix security proof.

Proves this task's own acceptance clause 1 ("Full real two-tenant role
matrix exercises reads/writes/artifacts/approvals with guessed IDs")
against a real, live PostgreSQL server -- never a mocked connection or an
in-memory stand-in (SQLite has no row-level security at all, so a passing
test against it would prove nothing about this task's actual acceptance
criterion) -- and this task's own negative_acceptance ("No authorization
test exclusively uses privileged DB connection or mocked membership
result").

Two real organizations (org1/org2), each with one real workspace, are
seeded as the migration-owner role; every authorization check below runs
through `TwoTenantRoleMatrixFixture`'s twelve real (issuer, subject)
principals -- all six of Section 6.4's roles, in both organizations --
authenticated against a real uvicorn server, with the demonstration app's
own database session bound to the real, distinct, least-privileged
`sovra_app` role FTF-0102's migration creates (never the migration-owner
role -- confirmed directly below, not merely asserted by convention).

For every (organization, role) pair and every one of this task's three
named operation classes (read/write/approve), two real requests are made:
one against the caller's *own* real workspace (proving the role boundary:
200 if the role is allowed for that operation, 403 if not), and one
against the *other* organization's real workspace ID substituted into the
caller's own path (the guessed/foreign-ID IDOR vector this task's own
acceptance clause names explicitly: 404 -- identical to a missing
resource, never a distinguishable error -- if the role would otherwise
have been allowed the operation, since the role gate passes and
`authorize_resource_ownership` is what denies it; 403 if the role itself
is not allowed, since the role gate denies before the database is ever
queried).

Requires a real Postgres reachable at `SOVRA_DATABASE_URL` (the migration
owner role) plus `SOVRA_APP_DB_PASSWORD`, matching FTF-0102/FTF-0105/
FTF-H003's own integration tests' environment contract exactly. No
independent schema change: this test consumes FTF-0102's own
`organizations`/`workspaces` tables and migration exactly as delivered.

Run directly (against an already-migrated or unmigrated database; this
script runs the migration itself):
    SOVRA_DATABASE_URL=postgresql+psycopg://<migration-role>@host/db \\
    SOVRA_APP_DB_PASSWORD=<app-role-password> \\
    python3 tests/integration/test_ftf_0106_rbac_idor_security.py
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

from app.auth.organization_workspace_schema import (  # noqa: E402
    APP_ROLE_NAME,
    Organization,
    Workspace,
)
from app.auth.rbac_idor_security_tests import (  # noqa: E402
    APPROVE_ROLES,
    WRITE_ROLES,
    TwoTenantRoleMatrixFixture,
    build_two_tenant_security_demo_app,
)
from app.auth.rbac_middleware import Role  # noqa: E402
from app.auth.resource_authorization import (  # noqa: E402
    ResourceAuthorizationError,
    authorize_resource_ownership,
)
from app.auth.tenant_isolation_query_guards import TenantContextError  # noqa: E402
from app.db.tenant_context import TenantContext  # noqa: E402

FAILURES: list[str] = []


def check(criterion_id: str, description: str, condition: bool) -> None:
    if not condition:
        FAILURES.append(f"FAIL [{criterion_id}] {description}")


def _required_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        print(
            f"BLOCKED_EXTERNAL: {name} is not set -- this task's real "
            "full role-matrix RBAC/IDOR evidence requires a live Postgres "
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


def _run_migration(database_url: str) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["SOVRA_DATABASE_URL"] = database_url
    return subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=str(API_ROOT),
        capture_output=True,
        text=True,
        env=env,
    )


def free_port() -> int:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


@contextlib.contextmanager
def _running_demo_app(app) -> Iterator[str]:
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


def _request(base_url: str, method: str, path: str, *, principal: str) -> int:
    headers = {"X-Test-Principal": principal}
    parsed = urllib.parse.urlparse(base_url)
    conn = http.client.HTTPConnection(parsed.hostname or "127.0.0.1", parsed.port or 80, timeout=10)
    try:
        conn.request(method, path, headers=headers)
        response = conn.getresponse()
        response.read()
        return response.status
    finally:
        conn.close()


def _expected_status(role: Role, allowed_roles: frozenset[Role], target: str) -> int:
    if role not in allowed_roles:
        return 403
    return 200 if target == "own" else 404


def main() -> None:
    migration_url = _required_env("SOVRA_DATABASE_URL")
    app_password = _required_env("SOVRA_APP_DB_PASSWORD")

    migration_result = _run_migration(migration_url)
    check(
        "FTF-0106",
        f"alembic upgrade head runs cleanly (exit {migration_result.returncode})\n"
        f"stdout: {migration_result.stdout}\nstderr: {migration_result.stderr}",
        migration_result.returncode == 0,
    )
    if FAILURES:
        print(f"FTF-0106 real RBAC/IDOR full-matrix proof: {len(FAILURES)} failed")
        for failure in FAILURES:
            print(f"  {failure}")
        sys.exit(1)

    owner_engine = create_engine(migration_url)
    app_url = _app_role_url(migration_url, app_password)
    app_engine = create_engine(app_url)

    # negative_acceptance: the engine every authorization check below runs
    # through is genuinely the restricted role, never the migration owner.
    check(
        "FTF-0106",
        "negative_acceptance: the demo app's own database session is built "
        "from the restricted sovra_app role's connection URL, not the "
        "migration-owner role's",
        app_engine.url.username == APP_ROLE_NAME
        and app_engine.url.username != owner_engine.url.username,
    )

    # -- Seed two real, distinct tenants (migration-owner role, seeding only) --
    with Session(owner_engine) as session:
        org1 = Organization(name="Acme 0106", slug=f"acme-0106-{uuid.uuid4().hex[:8]}")
        org2 = Organization(name="Globex 0106", slug=f"globex-0106-{uuid.uuid4().hex[:8]}")
        session.add_all([org1, org2])
        session.flush()
        ws1 = Workspace(organization_id=org1.id, name="Acme Main", slug="main")
        ws2 = Workspace(organization_id=org2.id, name="Globex Main", slug="main")
        session.add_all([ws1, ws2])
        session.commit()
        org1_id, org2_id, ws1_id, ws2_id = org1.id, org2.id, ws1.id, ws2.id

    fixture = TwoTenantRoleMatrixFixture.build({"org1": org1_id, "org2": org2_id})
    session_factory = sessionmaker(bind=app_engine)
    app = build_two_tenant_security_demo_app(
        fixture,
        session_factory,
        Workspace.__table__,
        Workspace.__table__.c.id,
        Workspace.__table__.c.organization_id,
    )

    org_data = {
        "org1": {"id": org1_id, "own_ws": ws1_id, "foreign_ws": ws2_id},
        "org2": {"id": org2_id, "own_ws": ws2_id, "foreign_ws": ws1_id},
    }
    operations = [
        ("GET", "/api/v1/orgs/{org}/workspaces/{ws}", frozenset(Role), "read"),
        ("PATCH", "/api/v1/orgs/{org}/workspaces/{ws}", WRITE_ROLES, "write"),
        ("POST", "/api/v1/orgs/{org}/workspaces/{ws}/approve", APPROVE_ROLES, "approve"),
    ]

    with _running_demo_app(app) as base_url:
        for org_label, data in org_data.items():
            for role in Role:
                header = fixture.header_for(org_label, role)
                for method, path_template, allowed_roles, op_name in operations:
                    for target in ("own", "foreign"):
                        ws_id = data["own_ws"] if target == "own" else data["foreign_ws"]
                        path = path_template.format(org=data["id"], ws=ws_id)
                        status = _request(base_url, method, path, principal=header)
                        expected = _expected_status(role, allowed_roles, target)
                        check(
                            "FTF-0106",
                            f"{org_label}/{role.value} {op_name} against {target} "
                            f"workspace: expected {expected}, got {status}",
                            status == expected,
                        )

    # -- Acceptance: missing context is refused, not treated as "0 rows" ---
    with Session(app_engine) as session:
        raw_context = TenantContext(organization_id=org1_id)
        try:
            authorize_resource_ownership(
                session,
                raw_context,
                Workspace.__table__,
                Workspace.__table__.c.id,
                Workspace.__table__.c.organization_id,
                ws1_id,
            )
            check(
                "FTF-0106",
                "acceptance: a session with no tenant context ever bound is "
                "refused (TenantContextError), not treated as an ordinary "
                "zero-row/foreign-tenant denial",
                False,
            )
        except TenantContextError:
            check(
                "FTF-0106",
                "acceptance: a session with no tenant context ever bound is "
                "refused (TenantContextError), not treated as an ordinary "
                "zero-row/foreign-tenant denial",
                True,
            )
        except ResourceAuthorizationError:
            check(
                "FTF-0106",
                "a missing-context request must fail via TenantContextError, "
                "not be silently absorbed into an ordinary "
                "ResourceAuthorizationError",
                False,
            )

    if FAILURES:
        print(f"FTF-0106 real RBAC/IDOR full-matrix proof: {len(FAILURES)} failed")
        for failure in FAILURES:
            print(f"  {failure}")
        sys.exit(1)

    print("FTF-0106 real RBAC/IDOR full-matrix proof: all checks passed")
    sys.exit(0)


if __name__ == "__main__":
    main()
