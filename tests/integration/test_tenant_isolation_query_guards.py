#!/usr/bin/env python3
"""FTF-0105 -- real Postgres tenant-isolation query-guard proof.

Proves this task's acceptance and negative_acceptance clauses against a
real, live PostgreSQL server -- never a mocked connection or an
in-memory stand-in (SQLite has no row-level security at all, so a
passing test against it would prove nothing about this task's actual
acceptance criterion):

- **Acceptance**: `tenant_scoped_session()` consistently applies the
  authenticated tenant's transaction-local context across independent
  units of work drawn from the *same* connection pool.
- **negative_acceptance ("connection-pool reuse... cannot leak previous
  tenant rows")**: with the pool forced to literally reuse one
  connection (`pool_size=1`), a session scoped to org2 sees none of
  org1's rows even though it inherited the same underlying DBAPI
  connection org1's session just used.
- **negative_acceptance ("background job... cannot leak previous tenant
  rows")**: a session opened the way a naive background worker would
  (direct `session_factory()`, not through `tenant_scoped_session`) is
  still refused by `execute_tenant_scoped` the moment its stated
  organization disagrees with the session's actual live context.
- **negative_acceptance ("raw SQL query... cannot leak previous tenant
  rows")**: this is demonstrated twice --
  1. `execute_tenant_scoped` refuses to run a raw statement at all when
     the caller's claimed tenant does not match the session's real one.
  2. The one raw-SQL misuse `execute_tenant_scoped`/`tenant_scoped_session`
     cannot see by construction -- a bare (session-level) `SET` instead of
     the required `SET LOCAL`/`set_config(..., is_local=true)` -- is shown
     to genuinely leak a stale tenant context across a pool checkout
     *without* `install_tenant_pool_reset_guard()` installed, and to no
     longer leak *with* it installed. Both runs are against the same real
     pool/connection reuse, not asserted from either side alone (Law 1:
     shown able to fail).

Requires a real Postgres reachable at `SOVRA_DATABASE_URL` (the migration
owner role) plus `SOVRA_APP_DB_PASSWORD`, matching FTF-0102/FTF-0104's own
integration tests' environment contract exactly (see
tests/integration/test_organization_workspace_schema.py's docstring for
the local-vs-CI registry-egress disclosure this task inherits unchanged).
No independent schema change: this test consumes FTF-0102's own
`organizations`/`workspaces` tables and migration exactly as delivered.

Run directly (against an already-migrated or unmigrated database; this
script runs the migration itself):
    SOVRA_DATABASE_URL=postgresql+psycopg://<migration-role>@host/db \\
    SOVRA_APP_DB_PASSWORD=<app-role-password> \\
    python3 tests/integration/test_tenant_isolation_query_guards.py
"""

from __future__ import annotations

import os
import subprocess
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
API_ROOT = ROOT / "apps" / "api"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(API_ROOT))

from sqlalchemy import create_engine, select, text  # noqa: E402
from sqlalchemy.orm import Session, sessionmaker  # noqa: E402
from sqlalchemy.pool import QueuePool  # noqa: E402

from app.auth.organization_workspace_schema import (  # noqa: E402
    APP_ROLE_NAME,
    Organization,
    Workspace,
    set_tenant_context,
)
from app.auth.tenant_isolation_query_guards import (  # noqa: E402
    TenantContextError,
    execute_tenant_scoped,
    install_tenant_pool_reset_guard,
    require_tenant_context,
    tenant_scoped_session,
)

FAILURES: list[str] = []


def check(criterion_id: str, description: str, condition: bool) -> None:
    if not condition:
        FAILURES.append(f"FAIL [{criterion_id}] {description}")


def _required_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        print(
            f"BLOCKED_EXTERNAL: {name} is not set -- this task's real "
            "connection-pool-reuse/tenant-isolation evidence requires a "
            "live Postgres (migration owner connection). Not silently "
            "skipped: see this file's own docstring for how to run it "
            "locally or in CI.",
            file=sys.stderr,
        )
        sys.exit(1)
    return value


def _app_role_url(migration_url: str, app_password: str) -> str:
    from urllib.parse import urlsplit, urlunsplit

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
        "FTF-0105",
        f"alembic upgrade head runs cleanly (exit {result.returncode})\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}",
        result.returncode == 0,
    )


def _dbapi_identity(session: Session) -> int:
    """A stable identity for the real underlying DBAPI connection a
    session is currently bound to -- used to prove two sessions genuinely
    share one pooled connection, not merely that they behave as if they
    might."""
    return id(session.connection().connection.dbapi_connection)


def main() -> None:
    migration_url = _required_env("SOVRA_DATABASE_URL")
    app_password = _required_env("SOVRA_APP_DB_PASSWORD")

    _run_migration()
    if FAILURES:
        print(f"FTF-0105 real tenant-isolation query-guard proof: {len(FAILURES)} failed")
        for failure in FAILURES:
            print(f"  {failure}")
        sys.exit(1)

    owner_engine = create_engine(migration_url)
    app_url = _app_role_url(migration_url, app_password)

    # -- Seed two real, distinct tenants as the migration owner role --------
    with Session(owner_engine) as session:
        org1 = Organization(name="Acme QG", slug=f"acme-qg-{uuid.uuid4().hex[:8]}")
        org2 = Organization(name="Globex QG", slug=f"globex-qg-{uuid.uuid4().hex[:8]}")
        session.add_all([org1, org2])
        session.flush()
        ws1 = Workspace(organization_id=org1.id, name="Acme Main", slug="main")
        ws2 = Workspace(organization_id=org2.id, name="Globex Main", slug="main")
        session.add_all([ws1, ws2])
        session.commit()
        org1_id, org2_id, ws1_id, ws2_id = org1.id, org2.id, ws1.id, ws2.id

    # == 1. Acceptance: tenant_scoped_session consistently applies context ==
    #       across independent units of work drawn from one pooled engine,
    #       and negative_acceptance: connection-pool reuse cannot leak.
    pooled_engine = create_engine(app_url, poolclass=QueuePool, pool_size=1, max_overflow=0)
    session_factory = sessionmaker(bind=pooled_engine)
    try:
        with tenant_scoped_session(session_factory, org1_id) as session:
            conn_identity_1 = _dbapi_identity(session)
            visible = session.execute(select(Workspace)).scalars().all()
            check(
                "FTF-0105",
                "acceptance: tenant_scoped_session(org1) sees only org1's own workspace row",
                [w.id for w in visible] == [ws1_id],
            )

        with tenant_scoped_session(session_factory, org2_id) as session:
            conn_identity_2 = _dbapi_identity(session)
            visible = session.execute(select(Workspace)).scalars().all()
            check(
                "FTF-0105",
                "negative_acceptance: connection-pool reuse -- a session "
                "scoped to org2, reusing the exact same pooled DBAPI "
                "connection org1's session just used, sees only org2's own "
                "workspace row, never a leaked org1 row",
                [w.id for w in visible] == [ws2_id],
            )
        check(
            "FTF-0105",
            "the pool literally reused the same underlying DBAPI connection "
            "across the two sessions above (pool_size=1 forces reuse -- "
            "this is a real proof of pool reuse, not merely asserted)",
            conn_identity_1 == conn_identity_2,
        )
    finally:
        pooled_engine.dispose()

    # == 2. negative_acceptance: background job / raw SQL cannot leak =======
    #       A session opened the naive way (bypassing tenant_scoped_session,
    #       the way an unreviewed background-worker code path might) is
    #       still refused by execute_tenant_scoped the moment its claimed
    #       tenant disagrees with the session's real live context.
    raw_engine = create_engine(app_url, poolclass=QueuePool, pool_size=1, max_overflow=0)
    try:
        session = sessionmaker(bind=raw_engine)()
        try:
            set_tenant_context(session, org1_id)
            result = execute_tenant_scoped(session, org1_id, text("SELECT name FROM workspaces"))
            names = [row[0] for row in result]
            check(
                "FTF-0105",
                "a background-job-style session, correctly scoped, can run "
                "its own raw SQL through execute_tenant_scoped and see only "
                "its real tenant's row",
                names == ["Acme Main"],
            )
            try:
                execute_tenant_scoped(session, org2_id, text("SELECT name FROM workspaces"))
                check(
                    "FTF-0105",
                    "negative_acceptance: a raw SQL query whose caller claims "
                    "a different tenant than the session's real live context "
                    "is refused outright, never silently run against the "
                    "session's actual (org1) tenant while claiming org2",
                    False,
                )
            except TenantContextError:
                check(
                    "FTF-0105",
                    "negative_acceptance: a raw SQL query whose caller claims "
                    "a different tenant than the session's real live context "
                    "is refused outright, never silently run against the "
                    "session's actual (org1) tenant while claiming org2",
                    True,
                )
            session.commit()
        finally:
            session.close()

        # A brand-new session on the reused connection, with no context set
        # at all, must see zero rows -- SET LOCAL's own transaction scoping
        # already discarded org1's context at the prior session's commit.
        session2 = sessionmaker(bind=raw_engine)()
        try:
            try:
                require_tenant_context(session2)
                check(
                    "FTF-0105",
                    "negative_acceptance: a fresh session on a reused "
                    "connection with no tenant context set is refused by "
                    "require_tenant_context, not treated as 'every row' or "
                    "silently passed through",
                    False,
                )
            except TenantContextError:
                check(
                    "FTF-0105",
                    "negative_acceptance: a fresh session on a reused "
                    "connection with no tenant context set is refused by "
                    "require_tenant_context, not treated as 'every row' or "
                    "silently passed through",
                    True,
                )
            visible = session2.execute(select(Workspace)).scalars().all()
            check(
                "FTF-0105",
                "the same fresh, unscoped session's own RLS-governed query "
                "genuinely sees zero rows (fail-closed at the database too, "
                "not merely at this guard)",
                visible == [],
            )
        finally:
            session2.close()
    finally:
        raw_engine.dispose()

    # == 3. Shown able to fail (Law 1): the pool-checkin reset guard is =====
    #       what actually prevents a bare (session-level) `SET` misuse --
    #       the one raw-SQL error class execute_tenant_scoped/
    #       tenant_scoped_session cannot see by construction, since it never
    #       goes through either -- from leaking across a pool checkout.
    def _leaks_without_guard(install_guard: bool) -> bool:
        engine = create_engine(app_url, poolclass=QueuePool, pool_size=1, max_overflow=0)
        if install_guard:
            install_tenant_pool_reset_guard(engine)
        try:
            # A raw SQL misuse: a bare SET (session-level, not transaction-
            # local) instead of the required SET LOCAL/set_config(is_local).
            conn1 = engine.connect()
            conn1.execute(text(f"SET app.current_organization_id = '{org1_id}'"))
            conn1.commit()
            conn1.close()  # returns the connection to the pool (checkin)

            # A brand-new caller checks out a connection from the same
            # pool (pool_size=1 guarantees it is the exact same one) and
            # runs an ordinary RLS-governed query with no context of its
            # own -- proving whether the prior bare SET's session-level
            # state survived the checkout.
            conn2 = engine.connect()
            try:
                rows = conn2.execute(text("SELECT name FROM workspaces")).fetchall()
                return len(rows) > 0
            finally:
                conn2.close()
        finally:
            engine.dispose()

    leaked_without_guard = _leaks_without_guard(install_guard=False)
    check(
        "FTF-0105",
        "Law 1 (shown able to fail): WITHOUT install_tenant_pool_reset_guard, "
        "a bare (session-level) SET misuse genuinely does leak the prior "
        "tenant's context across a real pool checkout -- reproducing the "
        "exact vulnerability this guard exists to close, not a hypothetical",
        leaked_without_guard is True,
    )

    leaked_with_guard = _leaks_without_guard(install_guard=True)
    check(
        "FTF-0105",
        "negative_acceptance: WITH install_tenant_pool_reset_guard installed "
        "on the same engine/pool configuration, the identical bare-SET "
        "misuse no longer leaks across the reused connection -- the "
        "checkin reset genuinely closes the gap Law 1 just proved was real",
        leaked_with_guard is False,
    )

    if FAILURES:
        print(f"FTF-0105 real tenant-isolation query-guard proof: {len(FAILURES)} failed")
        for failure in FAILURES:
            print(f"  {failure}")
        sys.exit(1)

    print("FTF-0105 real tenant-isolation query-guard proof: all checks passed")
    sys.exit(0)


if __name__ == "__main__":
    main()
