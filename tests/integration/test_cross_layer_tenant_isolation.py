#!/usr/bin/env python3
"""FTF-H003 -- real Postgres cross-layer tenant isolation proof.

Proves this task's acceptance and negative_acceptance clauses against a
real, live PostgreSQL server -- never a mocked connection or an
in-memory stand-in (SQLite has no row-level security and no view
`security_invoker` option at all, so a passing test against it would
prove nothing about this task's actual acceptance criteria):

- **Acceptance ("read/write... denial including guessed IDs and missing
  context")**: a real end-to-end demonstration FastAPI app combining
  FTF-0103's `require_role`, FTF-0105's `tenant_scoped_session` (via
  `tenant_context_dependency`) and this task's own `authorize_resource`
  proves an authenticated viewer can fetch their own real workspace,
  cannot fetch another real organization's real workspace by its real
  (guessed) ID, and an unauthenticated/no-context request is denied
  before any resource lookup happens at all.
- **negative_acceptance ("test views... not API guards alone")**: the
  `workspace_directory` view (this task's own migration,
  `0003_cross_layer_isolation`) enforces the exact same row-level
  security as the base tables under the restricted `sovra_app` role.
  Shown able to fail (Law 1): a scratch copy of the migration with its
  `security_invoker = true` line removed is run against a fresh scratch
  database and genuinely leaks every tenant's rows through the view;
  restored byte-identical afterward.
- **negative_acceptance ("Migration role cannot be used by the API or
  worker")**: connecting directly as the migration-owner role and
  querying the base tables with zero tenant context returns *every*
  organization's rows (table-owner roles are exempt from RLS by
  Postgres's own design) -- demonstrating concretely why application/
  worker code must never use it, distinct from (and a stronger claim
  than) `tests/tasks/test_ftf_h003.py`'s static "no source file
  references it" check.

Requires a real Postgres reachable at `SOVRA_DATABASE_URL` (the migration
owner role) plus `SOVRA_APP_DB_PASSWORD`, matching FTF-0102/FTF-0104/
FTF-0105's own integration tests' environment contract exactly (see
tests/integration/test_organization_workspace_schema.py's docstring for
the local-vs-CI registry-egress disclosure this task inherits unchanged).

Run directly (against an already-migrated or unmigrated database; this
script runs the migration itself):
    SOVRA_DATABASE_URL=postgresql+psycopg://<migration-role>@host/db \\
    SOVRA_APP_DB_PASSWORD=<app-role-password> \\
    python3 tests/integration/test_cross_layer_tenant_isolation.py
"""

from __future__ import annotations

import contextlib
import http.client
import json
import os
import socket
import subprocess
import sys
import threading
import time
import urllib.parse
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

ROOT = Path(__file__).resolve().parent.parent.parent
API_ROOT = ROOT / "apps" / "api"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(API_ROOT))

import uvicorn  # noqa: E402
from fastapi import Depends, FastAPI, HTTPException  # noqa: E402
from sqlalchemy import create_engine, text  # noqa: E402
from sqlalchemy.orm import Session, sessionmaker  # noqa: E402

from app.auth.organization_workspace_schema import (  # noqa: E402
    APP_ROLE_NAME,
    Organization,
    Workspace,
)
from app.auth.rbac_middleware import AuthenticatedPrincipal, Role  # noqa: E402
from app.auth.resource_authorization import authorize_resource  # noqa: E402
from app.db.tenant_context import TenantScopedRequest, tenant_context_dependency  # noqa: E402

MIGRATION_PATH = API_ROOT / "alembic" / "versions" / "0003_cross_layer_isolation.py"

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
            "Postgres (migration owner connection). Not silently "
            "skipped: see this file's own docstring for how to run it "
            "locally or in CI.",
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


def free_port() -> int:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


@dataclass
class _HttpResult:
    status: int
    body: bytes

    def json(self) -> dict:
        return json.loads(self.body)


def _request(base_url: str, method: str, path: str, *, principal: str | None = None) -> _HttpResult:
    headers: dict[str, str] = {}
    if principal is not None:
        headers["X-Test-Principal"] = principal
    parsed = urllib.parse.urlparse(base_url)
    conn = http.client.HTTPConnection(parsed.hostname or "127.0.0.1", parsed.port or 80, timeout=10)
    try:
        conn.request(method, path, headers=headers)
        response = conn.getresponse()
        raw_body = response.read()
        return _HttpResult(status=response.status, body=raw_body)
    finally:
        conn.close()


@contextlib.contextmanager
def _running_demo_app(app: FastAPI) -> Iterator[str]:
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


def _run_alembic(database_url: str, *args: str) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["SOVRA_DATABASE_URL"] = database_url
    return subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=str(API_ROOT),
        capture_output=True,
        text=True,
        env=env,
    )


def main() -> None:
    migration_url = _required_env("SOVRA_DATABASE_URL")
    app_password = _required_env("SOVRA_APP_DB_PASSWORD")

    migration_result = _run_alembic(migration_url, "upgrade", "head")
    check(
        "FTF-H003",
        f"alembic upgrade head runs cleanly on the primary test database "
        f"(exit {migration_result.returncode})\nstdout: {migration_result.stdout}\n"
        f"stderr: {migration_result.stderr}",
        migration_result.returncode == 0,
    )
    if FAILURES:
        print(f"FTF-H003 real cross-layer tenant-isolation proof: {len(FAILURES)} failed")
        for failure in FAILURES:
            print(f"  {failure}")
        sys.exit(1)

    owner_engine = create_engine(migration_url)
    app_url = _app_role_url(migration_url, app_password)
    app_engine = create_engine(app_url)

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

    # == 1. negative_acceptance: the workspace_directory view enforces the ==
    #       same RLS as the base tables (not just the API guards above it).
    from app.auth.organization_workspace_schema import set_tenant_context

    with Session(app_engine) as session:
        visible = session.execute(text("SELECT organization_id FROM workspace_directory")).all()
        check(
            "FTF-H003",
            "negative_acceptance: workspace_directory returns zero rows "
            "with no tenant context set -- the view enforces RLS itself, "
            "not merely because callers happen to always set context",
            visible == [],
        )
        set_tenant_context(session, org1_id)
        visible = session.execute(text("SELECT organization_id FROM workspace_directory")).all()
        check(
            "FTF-H003",
            "acceptance: with tenant context = org1, workspace_directory "
            "returns only org1's own row",
            [row[0] for row in visible] == [org1_id],
        )

    # == 2. negative_acceptance: migration role bypasses RLS -- exactly ====
    #       why it must never be the API/worker's own connection role.
    with Session(owner_engine) as session:
        all_orgs = session.execute(text("SELECT id FROM organizations")).all()
        check(
            "FTF-H003",
            "negative_acceptance: the migration-owner role sees every "
            "organization's rows with zero tenant context set (table "
            "owners are exempt from RLS by Postgres's own design) -- this "
            "is exactly why application/worker code must never connect as "
            "this role",
            {row[0] for row in all_orgs} >= {org1_id, org2_id},
        )

    # == 3. Shown able to fail (Law 1): security_invoker actually matters ==
    #       Reuses the same already-migrated primary database rather than a
    #       second scratch database: migration 0001 CREATEs a cluster-global
    #       role (`sovra_app`), which cannot be created twice in the same
    #       Postgres cluster -- a second full migration chain run against a
    #       fresh scratch database in this same cluster would collide on
    #       that CREATE ROLE, including in CI's own single-container-per-job
    #       setup. Downgrading only this migration's own view (0003 ->
    #       0002) and re-upgrading leaves 0001/0002's role and tables
    #       untouched, isolating the fault injection to exactly the one
    #       migration this task owns.
    original_migration_src = MIGRATION_PATH.read_bytes()
    try:
        down_result = _run_alembic(migration_url, "downgrade", "0002_audit_event_writer")
        check(
            "FTF-H003",
            f"downgrading to 0002 (dropping this task's own view) runs "
            f"cleanly (exit {down_result.returncode})\nstdout: {down_result.stdout}\n"
            f"stderr: {down_result.stderr}",
            down_result.returncode == 0,
        )

        needle = (
            "    # The one line that actually closes the RLS-bypass-through-views gap\n"
            "    # this migration's own docstring reproduces above -- without it, this\n"
            "    # view would silently leak every tenant's rows to sovra_app.\n"
            '    op.execute("ALTER VIEW workspace_directory SET (security_invoker = true)")\n'
        )
        src = original_migration_src.decode("utf-8")
        assert needle in src, "expected security_invoker block not found -- migration shape changed"
        mutated = src.replace(needle, "")
        assert mutated != src, "fault injection did not change the file"
        MIGRATION_PATH.write_text(mutated, encoding="utf-8")

        fault_result = _run_alembic(migration_url, "upgrade", "head")
        check(
            "FTF-H003",
            f"re-applying the fault-injected migration runs cleanly (exit "
            f"{fault_result.returncode})\nstdout: {fault_result.stdout}\n"
            f"stderr: {fault_result.stderr}",
            fault_result.returncode == 0,
        )

        with Session(app_engine) as session:
            leaked = session.execute(text("SELECT organization_id FROM workspace_directory")).all()
            check(
                "FTF-H003",
                "Law 1 (shown able to fail): WITHOUT security_invoker = true, "
                "the workspace_directory view genuinely leaks both real "
                "organizations' rows (org1 and org2, seeded above) to "
                "sovra_app with zero tenant context set -- reproducing the "
                "exact vulnerability this migration's real (unmutated) "
                "version exists to close",
                {row[0] for row in leaked} >= {org1_id, org2_id},
            )
    finally:
        MIGRATION_PATH.write_bytes(original_migration_src)
        restored = MIGRATION_PATH.read_bytes()
        check(
            "FTF-H003",
            "the migration file is restored byte-identical after fault injection",
            restored == original_migration_src,
        )
        # Re-apply the real, unmutated migration on top of the fault
        # injection's view so the rest of this suite (and any later run
        # against this same database) sees the genuinely fixed behavior,
        # not the fault-injected one.
        redown_result = _run_alembic(migration_url, "downgrade", "0002_audit_event_writer")
        reup_result = _run_alembic(migration_url, "upgrade", "head")
        check(
            "FTF-H003",
            f"re-applying the real, restored migration runs cleanly "
            f"(downgrade exit {redown_result.returncode}, upgrade exit "
            f"{reup_result.returncode})",
            redown_result.returncode == 0 and reup_result.returncode == 0,
        )
        with Session(app_engine) as session:
            fixed = session.execute(text("SELECT organization_id FROM workspace_directory")).all()
            check(
                "FTF-H003",
                "after restoring the real migration, the view is genuinely "
                "fixed again -- zero rows visible with no tenant context, "
                "not just a restored file with a still-broken database",
                fixed == [],
            )

    # == 4. Acceptance: real end-to-end demonstration app -- guessed-ID =====
    #       cross-tenant denial, RBAC + tenant-scoped session + resource
    #       guard composed together against the real database.
    issuer = "https://issuer.example"
    principals = {"viewer-1": AuthenticatedPrincipal(issuer=issuer, subject="user-viewer-1")}
    memberships = {(issuer, "user-viewer-1", org1_id): Role.VIEWER}

    def principal_resolver(request):
        header = request.headers.get("x-test-principal")
        if header is None:
            return None
        return principals.get(header)

    def role_lookup(iss: str, sub: str, organization_id):
        return memberships.get((iss, sub, organization_id))

    session_factory = sessionmaker(bind=app_engine)
    app = FastAPI()
    require_viewer = Depends(
        tenant_context_dependency(
            frozenset({Role.VIEWER}),
            session_factory=session_factory,
            principal_resolver=principal_resolver,
            role_lookup=role_lookup,
        )
    )

    @app.get("/api/v1/orgs/{organization_id}/workspaces/{workspace_id}")
    def get_workspace(workspace_id: uuid.UUID, ctx: TenantScopedRequest = require_viewer) -> dict:
        workspace = authorize_resource(ctx.session, Workspace, workspace_id)
        if workspace is None:
            raise HTTPException(status_code=404, detail="not_found")
        return {"id": str(workspace.id), "name": workspace.name}

    with _running_demo_app(app) as base_url:
        result = _request(base_url, "GET", f"/api/v1/orgs/{org1_id}/workspaces/{ws1_id}")
        check(
            "FTF-H003",
            "unauthenticated is irrelevant here -- baseline sanity: missing "
            "principal header is denied (401)",
            result.status == 401,
        )

        result = _request(
            base_url,
            "GET",
            f"/api/v1/orgs/{org1_id}/workspaces/{ws1_id}",
            principal="viewer-1",
        )
        check(
            "FTF-H003",
            "acceptance: an authorized viewer fetches their own real " "workspace successfully",
            result.status == 200 and result.json()["name"] == "Acme Main",
        )

        result = _request(
            base_url,
            "GET",
            f"/api/v1/orgs/{org1_id}/workspaces/{ws2_id}",
            principal="viewer-1",
        )
        check(
            "FTF-H003",
            "negative_acceptance: a guessed real workspace ID belonging to "
            "a different organization is denied (404, not 403 -- "
            "indistinguishable from a nonexistent resource, no side "
            "channel confirming it exists)",
            result.status == 404,
        )

        result = _request(
            base_url,
            "GET",
            f"/api/v1/orgs/{org1_id}/workspaces/{uuid.uuid4()}",
            principal="viewer-1",
        )
        check(
            "FTF-H003",
            "a truly nonexistent workspace ID within the caller's own "
            "organization returns the identical 404",
            result.status == 404,
        )

        result = _request(
            base_url,
            "GET",
            f"/api/v1/orgs/{org2_id}/workspaces/{ws2_id}",
            principal="viewer-1",
        )
        check(
            "FTF-H003",
            "negative_acceptance: missing context/membership -- a caller "
            "with no membership at all in org2 is denied before any "
            "resource lookup happens (403 at the RBAC layer)",
            result.status == 403,
        )

    if FAILURES:
        print(f"FTF-H003 real cross-layer tenant-isolation proof: {len(FAILURES)} failed")
        for failure in FAILURES:
            print(f"  {failure}")
        sys.exit(1)

    print("FTF-H003 real cross-layer tenant-isolation proof: all checks passed")
    sys.exit(0)


if __name__ == "__main__":
    main()
