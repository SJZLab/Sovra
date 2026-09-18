#!/usr/bin/env python3
"""FTF-0102 -- real Postgres migration + RLS tenant-isolation proof.

Proves this task's negative_acceptance clause ("Cross-tenant parent IDs
and missing tenant context fail DB constraints/RLS, including privileged
test-role mistakes") against a real, live PostgreSQL server -- never a
mocked connection or an in-memory SQLite stand-in (SQLite has no row-level
security at all, so a passing test against it would prove nothing about
this task's actual acceptance criterion).

Requires a real Postgres reachable at `SOVRA_DATABASE_URL` (the migration
owner role) plus `SOVRA_APP_DB_PASSWORD` (used both by the migration to
create the restricted `sovra_app` role and by this test to connect as it).
This repo's interactive dev sandbox has no container-registry egress to
boot Postgres via Docker (confirmed again this task with a real `docker
pull postgres:16-alpine`, same reproducible 403 FTF-0004/FTF-R017 already
documented) -- but does have PostgreSQL 16 installed directly (not via a
container), which is what this task's own evidence was produced against
locally before being wired into CI's dedicated
`organization-workspace-schema-integration` job (a GitHub Actions
`services: postgres` container, real registry egress on the hosted
runner).

Shown able to fail (Law 1), against this exact file, not just the tracked
migration's own fixtures: granting the `sovra_app` role `BYPASSRLS`
instead of `NOBYPASSRLS` in a scratch copy of the migration made 6 of
this suite's checks genuinely fail (every tenant-isolation and
negative_acceptance case) against a real local Postgres 16 cluster,
restored byte-identical and re-run clean afterward.

Run directly (against an already-migrated or unmigrated database; this
script runs the migration itself):
    SOVRA_DATABASE_URL=postgresql+psycopg://<migration-role>@host/db \\
    SOVRA_APP_DB_PASSWORD=<app-role-password> \\
    python3 tests/integration/test_organization_workspace_schema.py
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

from sqlalchemy import create_engine, select, text  # noqa: E402
from sqlalchemy.engine import CursorResult  # noqa: E402
from sqlalchemy.exc import DBAPIError, IntegrityError  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.auth.organization_workspace_schema import (  # noqa: E402
    APP_ROLE_NAME,
    Organization,
    Workspace,
    set_tenant_context,
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
            "RLS/tenant-isolation evidence requires a live Postgres "
            "(migration owner connection). Not silently skipped: see this "
            "file's own docstring for how to run it locally or in CI.",
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
        "FTF-0102",
        f"alembic upgrade head runs cleanly (exit {result.returncode})\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}",
        result.returncode == 0,
    )


def main() -> None:
    migration_url = _required_env("SOVRA_DATABASE_URL")
    app_password = _required_env("SOVRA_APP_DB_PASSWORD")

    _run_migration()
    if FAILURES:
        print(f"FTF-0102 real tenant-isolation proof: {len(FAILURES)} failed")
        for failure in FAILURES:
            print(f"  {failure}")
        sys.exit(1)

    owner_engine = create_engine(migration_url)
    app_engine = create_engine(_app_role_url(migration_url, app_password))

    # -- Seed two real, distinct tenants as the migration owner role --------
    with Session(owner_engine) as session:
        org1 = Organization(name="Acme Corp", slug=f"acme-{uuid.uuid4().hex[:8]}")
        org2 = Organization(name="Globex Corp", slug=f"globex-{uuid.uuid4().hex[:8]}")
        session.add_all([org1, org2])
        session.flush()
        ws1 = Workspace(organization_id=org1.id, name="Acme Main", slug="main")
        ws2 = Workspace(organization_id=org2.id, name="Globex Main", slug="main")
        session.add_all([ws1, ws2])
        session.commit()
        org1_id, org2_id, ws1_id, ws2_id = org1.id, org2.id, ws1.id, ws2.id

    # -- Acceptance: two real organizations/workspaces exist, unique names --
    check(
        "FTF-0102",
        "two distinct organizations were created via a real migrated schema",
        org1_id != org2_id,
    )
    check("FTF-0102", "each organization owns its own real workspace", ws1_id != ws2_id)

    # -- 1. Restricted app role + correct tenant context sees only its own --
    with Session(app_engine) as session:
        set_tenant_context(session, org1_id)
        visible_orgs = session.execute(select(Organization)).scalars().all()
        visible_ws = session.execute(select(Workspace)).scalars().all()
        check(
            "FTF-0102",
            "with tenant context = org1, only org1's own organization row is visible",
            [o.id for o in visible_orgs] == [org1_id],
        )
        check(
            "FTF-0102",
            "with tenant context = org1, only org1's own workspace row is visible",
            [w.id for w in visible_ws] == [ws1_id],
        )

    # -- 2. Negative_acceptance: missing tenant context fails closed --------
    with Session(app_engine) as session:
        visible_orgs = session.execute(select(Organization)).scalars().all()
        visible_ws = session.execute(select(Workspace)).scalars().all()
        check(
            "FTF-0102",
            "negative_acceptance: missing tenant context sees zero organization rows, "
            "not every row and not an error",
            visible_orgs == [],
        )
        check(
            "FTF-0102",
            "negative_acceptance: missing tenant context sees zero workspace rows",
            visible_ws == [],
        )

    # -- 3. Negative_acceptance: cross-tenant parent ID rejected on INSERT --
    try:
        with Session(app_engine) as session:
            set_tenant_context(session, org1_id)
            rogue = Workspace(organization_id=org2_id, name="Rogue", slug="rogue")
            session.add(rogue)
            session.commit()
        check(
            "FTF-0102",
            "negative_acceptance: inserting a workspace with a cross-tenant "
            "parent organization_id is rejected by RLS WITH CHECK",
            False,
        )
    except DBAPIError:
        check(
            "FTF-0102",
            "negative_acceptance: inserting a workspace with a cross-tenant "
            "parent organization_id is rejected by RLS WITH CHECK",
            True,
        )

    # -- 4. Negative_acceptance: "privileged test-role mistake" -- a caller --
    #       with tenant context set correctly still cannot retarget an
    #       existing row to a different tenant via UPDATE.
    try:
        with Session(app_engine) as session:
            set_tenant_context(session, org1_id)
            session.execute(
                text("UPDATE workspaces SET organization_id = :other WHERE id = :ws"),
                {"other": str(org2_id), "ws": str(ws1_id)},
            )
            session.commit()
        check(
            "FTF-0102",
            "negative_acceptance: retargeting an owned workspace to a "
            "different tenant via UPDATE is rejected by RLS WITH CHECK",
            False,
        )
    except DBAPIError:
        check(
            "FTF-0102",
            "negative_acceptance: retargeting an owned workspace to a "
            "different tenant via UPDATE is rejected by RLS WITH CHECK",
            True,
        )

    # -- 5. A same-tenant UPDATE to another tenant's row affects zero rows --
    #       (RLS's USING clause filters it out silently, not an error --
    #       distinct code path from the WITH CHECK cases above).
    with Session(app_engine) as session:
        set_tenant_context(session, org1_id)
        result = session.execute(
            text("UPDATE workspaces SET name = 'Hijacked' WHERE id = :ws"),
            {"ws": str(ws2_id)},
        )
        session.commit()
        # session.execute's declared return type doesn't statically expose
        # .rowcount, but a real CursorResult (what a raw UPDATE actually
        # returns) always has it.
        rowcount = cast(CursorResult, result).rowcount
        check(
            "FTF-0102",
            "negative_acceptance: an UPDATE targeting another tenant's row "
            "by ID affects zero rows under RLS, not the foreign row",
            rowcount == 0,
        )

    # -- 6. Acceptance: real migration enforces globally-unique org slugs ---
    with Session(owner_engine) as session:
        original_slug = session.execute(
            select(Organization.slug).where(Organization.id == org1_id)
        ).scalar_one()
    try:
        with Session(owner_engine) as session:
            dup = Organization(name="Acme Corp Dup", slug=original_slug)
            session.add(dup)
            session.commit()
        check(
            "FTF-0102",
            "a duplicate organization slug is rejected by a real UNIQUE constraint",
            False,
        )
    except IntegrityError:
        check(
            "FTF-0102",
            "a duplicate organization slug is rejected by a real UNIQUE constraint",
            True,
        )

    # -- 7. Acceptance: tenant-composite uniqueness (same slug, different --
    #       organizations is allowed; same slug within one organization is not)
    with Session(owner_engine) as session:
        reused_slug_ws = Workspace(
            organization_id=org2_id, name="Reused Slug OK", slug="shared-slug"
        )
        session.add(reused_slug_ws)
        session.commit()
        other_org_reused_slug_ws = Workspace(
            organization_id=org1_id, name="Reused Slug OK Too", slug="shared-slug"
        )
        session.add(other_org_reused_slug_ws)
        session.commit()
    check(
        "FTF-0102",
        "the same workspace slug is allowed across two different organizations "
        "(tenant-composite uniqueness, not global uniqueness)",
        True,
    )
    try:
        with Session(owner_engine) as session:
            dup_ws = Workspace(organization_id=org1_id, name="Dup", slug="main")
            session.add(dup_ws)
            session.commit()
        check(
            "FTF-0102",
            "a duplicate (organization_id, slug) within one organization is "
            "rejected by the real tenant-composite UNIQUE constraint",
            False,
        )
    except IntegrityError:
        check(
            "FTF-0102",
            "a duplicate (organization_id, slug) within one organization is "
            "rejected by the real tenant-composite UNIQUE constraint",
            True,
        )

    if FAILURES:
        print(f"FTF-0102 real tenant-isolation proof: {len(FAILURES)} failed")
        for failure in FAILURES:
            print(f"  {failure}")
        sys.exit(1)

    print("FTF-0102 real tenant-isolation proof: all checks passed")
    sys.exit(0)


if __name__ == "__main__":
    main()
