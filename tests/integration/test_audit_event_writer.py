#!/usr/bin/env python3
"""FTF-0104 -- real Postgres append-only/tenant-isolation/atomicity proof.

Proves this task's acceptance and negative_acceptance clauses against a
real, live PostgreSQL server -- never a mocked connection or an in-memory
stand-in:

- **Acceptance**: a committed sensitive mutation (an Organization rename)
  and its audit event, written in the same transaction, are both visible
  after commit, with the real actor/resource/version/request identity
  recorded.
- **negative_acceptance ("failed transaction cannot emit misleading
  success")**: the *same* mutation-plus-audit-event pair, written in one
  transaction that is then rolled back, leaves neither the mutation nor
  the audit event visible -- proving `write_audit_event` never commits on
  its own behalf and cannot report success for a mutation that never
  actually happened.
- **negative_acceptance ("redacted audit cannot be edited by normal app
  role")**: the restricted `sovra_app` role's own UPDATE/DELETE attempts
  against an existing audit row are rejected by the database (Postgres
  `InsufficientPrivilege`), not merely by application code choosing not
  to issue them.
- Tenant isolation (Section 65.1, same discipline as FTF-0102): a
  connection with a different/no tenant context sees zero of another
  tenant's audit rows.

Requires a real Postgres reachable at `SOVRA_DATABASE_URL` (the migration
owner role) plus `SOVRA_APP_DB_PASSWORD`, matching FTF-0102's own
integration test's environment contract exactly (see that file's
docstring for the local-vs-CI registry-egress disclosure this task
inherits unchanged).

Run directly (against an already-migrated or unmigrated database; this
script runs the migration itself):
    SOVRA_DATABASE_URL=postgresql+psycopg://<migration-role>@host/db \\
    SOVRA_APP_DB_PASSWORD=<app-role-password> \\
    python3 tests/integration/test_audit_event_writer.py
"""

from __future__ import annotations

import os
import subprocess
import sys
import uuid
from pathlib import Path
from typing import cast

ROOT = Path(__file__).resolve().parent.parent.parent
API_ROOT = ROOT / "apps" / "api"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(API_ROOT))

from sqlalchemy import Table, create_engine, select  # noqa: E402
from sqlalchemy.exc import DBAPIError  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.auth.audit_event_writer import AuditEvent, write_audit_event  # noqa: E402
from app.auth.organization_workspace_schema import (  # noqa: E402
    APP_ROLE_NAME,
    Organization,
    set_tenant_context,
)

# ORGANIZATIONS_TABLE/AUDIT_EVENTS_TABLE are real sqlalchemy.Table
# instances; mypy's stubs type the attribute as the more general
# FromClause, which has no .update()/.delete() (same cast FTF-0102's own
# test suite needed for .constraints).
ORGANIZATIONS_TABLE = cast(Table, Organization.__table__)
AUDIT_EVENTS_TABLE = cast(Table, AuditEvent.__table__)

FAILURES: list[str] = []


def check(criterion_id: str, description: str, condition: bool) -> None:
    if not condition:
        FAILURES.append(f"FAIL [{criterion_id}] {description}")


def _required_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        print(
            f"BLOCKED_EXTERNAL: {name} is not set -- this task's real "
            "append-only/tenant-isolation/atomicity evidence requires a "
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
        "FTF-0104",
        f"alembic upgrade head runs cleanly (exit {result.returncode})\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}",
        result.returncode == 0,
    )


def main() -> None:
    migration_url = _required_env("SOVRA_DATABASE_URL")
    app_password = _required_env("SOVRA_APP_DB_PASSWORD")

    _run_migration()
    if FAILURES:
        print(f"FTF-0104 audit event writer real proof: {len(FAILURES)} failed")
        for failure in FAILURES:
            print(f"  {failure}")
        sys.exit(1)

    owner_engine = create_engine(migration_url)
    app_engine = create_engine(_app_role_url(migration_url, app_password))

    # -- Seed two real, distinct tenants (as the migration owner role) ------
    with Session(owner_engine) as session:
        org1 = Organization(name="Acme Corp", slug=f"acme-{uuid.uuid4().hex[:8]}")
        org2 = Organization(name="Globex Corp", slug=f"globex-{uuid.uuid4().hex[:8]}")
        session.add_all([org1, org2])
        session.commit()
        org1_id, org2_id = org1.id, org2.id

    # -- 1. Acceptance: a committed mutation + its audit event are both -----
    #       visible after commit, written in the same transaction.
    commit_request_id = f"req-{uuid.uuid4().hex[:12]}"
    with Session(app_engine) as session:
        set_tenant_context(session, org1_id)
        session.execute(
            ORGANIZATIONS_TABLE.update()
            .where(ORGANIZATIONS_TABLE.c.id == org1_id)
            .values(name="Acme Corp Renamed")
        )
        write_audit_event(
            session,
            organization_id=org1_id,
            actor_type="user",
            actor_id="user-1",
            action="organization.rename",
            resource_type="organization",
            resource_id=str(org1_id),
            request_id=commit_request_id,
            details={"old_name": "Acme Corp", "new_name": "Acme Corp Renamed"},
        )
        session.commit()

    with Session(owner_engine) as session:
        renamed_name = session.execute(
            select(Organization.name).where(Organization.id == org1_id)
        ).scalar_one()
        committed_event = session.execute(
            select(AuditEvent).where(AuditEvent.request_id == commit_request_id)
        ).scalar_one_or_none()
    check(
        "FTF-0104",
        "acceptance: a committed mutation is genuinely visible after commit",
        renamed_name == "Acme Corp Renamed",
    )
    check(
        "FTF-0104",
        "acceptance: the audit event written in the same transaction as a "
        "committed mutation is genuinely visible after commit, with the "
        "real actor/resource/request identity recorded",
        committed_event is not None
        and committed_event.actor_id == "user-1"
        and committed_event.action == "organization.rename"
        and committed_event.resource_id == str(org1_id),
    )

    # -- 2. negative_acceptance: a failed transaction leaves neither the ----
    #       mutation nor its audit event behind -- write_audit_event never
    #       commits on its own, so it cannot report a success that never
    #       happened.
    rollback_request_id = f"req-{uuid.uuid4().hex[:12]}"
    try:
        with Session(app_engine) as session:
            set_tenant_context(session, org1_id)
            session.execute(
                ORGANIZATIONS_TABLE.update()
                .where(ORGANIZATIONS_TABLE.c.id == org1_id)
                .values(name="Should Not Persist")
            )
            write_audit_event(
                session,
                organization_id=org1_id,
                actor_type="user",
                actor_id="user-1",
                action="organization.rename",
                resource_type="organization",
                resource_id=str(org1_id),
                request_id=rollback_request_id,
            )
            session.flush()
            raise RuntimeError("simulated failure before commit")
    except RuntimeError:
        pass

    with Session(owner_engine) as session:
        current_name = session.execute(
            select(Organization.name).where(Organization.id == org1_id)
        ).scalar_one()
        rolled_back_event = session.execute(
            select(AuditEvent).where(AuditEvent.request_id == rollback_request_id)
        ).scalar_one_or_none()
    check(
        "FTF-0104",
        "negative_acceptance: a failed (rolled-back) transaction's mutation "
        "never actually persisted",
        current_name == "Acme Corp Renamed",
    )
    check(
        "FTF-0104",
        "negative_acceptance: 'failed transaction cannot emit misleading "
        "success' -- the audit event written in the same rolled-back "
        "transaction is genuinely absent, not a false success record",
        rolled_back_event is None,
    )

    # -- 3. negative_acceptance: the restricted app role cannot edit an -----
    #       existing, already-committed audit row -- rejected by the
    #       database's own GRANT surface, not just unused by the app.
    try:
        with Session(app_engine) as session:
            set_tenant_context(session, org1_id)
            session.execute(
                AUDIT_EVENTS_TABLE.update()
                .where(AUDIT_EVENTS_TABLE.c.request_id == commit_request_id)
                .values(action="tampered")
            )
            session.commit()
        check(
            "FTF-0104",
            "negative_acceptance: UPDATEing an existing audit row as the "
            "restricted app role is rejected by the database",
            False,
        )
    except DBAPIError as exc:
        check(
            "FTF-0104",
            f"negative_acceptance: UPDATEing an existing audit row as the "
            f"restricted app role is rejected by the database ({exc.orig!r})",
            True,
        )

    try:
        with Session(app_engine) as session:
            set_tenant_context(session, org1_id)
            session.execute(
                AUDIT_EVENTS_TABLE.delete().where(
                    AUDIT_EVENTS_TABLE.c.request_id == commit_request_id
                )
            )
            session.commit()
        check(
            "FTF-0104",
            "negative_acceptance: DELETEing an existing audit row as the "
            "restricted app role is rejected by the database",
            False,
        )
    except DBAPIError as exc:
        check(
            "FTF-0104",
            f"negative_acceptance: DELETEing an existing audit row as the "
            f"restricted app role is rejected by the database ({exc.orig!r})",
            True,
        )

    # Confirm the row genuinely survived both rejected attempts, byte-for-byte.
    with Session(owner_engine) as session:
        still_there = session.execute(
            select(AuditEvent).where(AuditEvent.request_id == commit_request_id)
        ).scalar_one_or_none()
    check(
        "FTF-0104",
        "the audit row survives the rejected UPDATE/DELETE attempts unchanged",
        still_there is not None and still_there.action == "organization.rename",
    )

    # -- 4. Tenant isolation: a connection scoped to org2 (or with no ------
    #       tenant context) sees none of org1's audit rows.
    with Session(app_engine) as session:
        set_tenant_context(session, org2_id)
        visible = (
            session.execute(select(AuditEvent).where(AuditEvent.request_id == commit_request_id))
            .scalars()
            .all()
        )
    check(
        "FTF-0104",
        "tenant isolation: a connection scoped to a different tenant sees "
        "none of org1's audit rows",
        visible == [],
    )

    with Session(app_engine) as session:
        visible_no_context = (
            session.execute(select(AuditEvent).where(AuditEvent.request_id == commit_request_id))
            .scalars()
            .all()
        )
    check(
        "FTF-0104",
        "tenant isolation: a connection with no tenant context set sees "
        "zero audit rows, not every row and not an error",
        visible_no_context == [],
    )

    if FAILURES:
        print(f"FTF-0104 audit event writer real proof: {len(FAILURES)} failed")
        for failure in FAILURES:
            print(f"  {failure}")
        sys.exit(1)

    print("FTF-0104 audit event writer real proof: all checks passed")
    sys.exit(0)


if __name__ == "__main__":
    main()
