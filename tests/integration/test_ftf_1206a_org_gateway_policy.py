#!/usr/bin/env python3
"""FTF-1206A -- real Postgres organization-gateway-policy persistence proof.

Proves this task's own acceptance clause ("Persist per-organization
gateway policy ... plus an append-only route-change record, tenant-scoped
under RLS, with every mutation audited through FTF-0104's writer in the
same transaction") and negative_acceptance ("Cross-tenant read or write is
denied ...; a project-level principal cannot widen the organization
policy; the app role cannot DELETE or UPDATE route-change history; no
allow-all RLS policy") against a real, live PostgreSQL server -- never a
mocked connection or an in-memory SQLite stand-in (SQLite has no row-level
security at all, so a passing test against it would prove nothing about
this task's actual acceptance criterion).

Two real organizations (org1 with a real workspace/project, org2 for the
cross-tenant IDOR case) are seeded as the migration-owner role (FTF-0102/
FTF-0104/FTF-0401, already-CERTIFIED). Every store call below runs as the
real, distinct, least-privileged `sovra_app` role FTF-0102's migration
creates (never the migration-owner role), exactly like every prior
integration test in this chain. No uvicorn server is started here -- this
task's own `api_contract` is explicit: "Internal store consumed by
FTF-1206's organization_policy_control; no new public route in this
task."

Also proves the real upgrade/downgrade round-trip Sections 6/65/71 and
this task's own `database_changes` clause require.

Requires a real Postgres reachable at `SOVRA_DATABASE_URL` (the migration
owner role) plus `SOVRA_APP_DB_PASSWORD`, matching every prior integration
test's own environment contract exactly. This repo's interactive dev
sandbox has no container-registry egress (same finding as every prior
integration-tested task) -- this evidence is wired into CI's own dedicated
integration job (a GitHub Actions `services: postgres` container, real
registry egress on the hosted runner).

Run directly (against an already-migrated or unmigrated database; this
script runs the migration itself):
    SOVRA_DATABASE_URL=postgresql+psycopg://<migration-role>@host/db \\
    SOVRA_APP_DB_PASSWORD=<app-role-password> \\
    python3 tests/integration/test_ftf_1206a_org_gateway_policy.py
"""

from __future__ import annotations

import os
import subprocess
import sys
import uuid
from pathlib import Path
from typing import cast
from urllib.parse import urlsplit, urlunsplit

ROOT = Path(__file__).resolve().parent.parent.parent
API_ROOT = ROOT / "apps" / "api"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(API_ROOT))

from packages.litellm_client.organization_policy_control import (  # noqa: E402
    OrganizationGatewayPolicy,
)
from sqlalchemy import Table, create_engine, select, text  # noqa: E402
from sqlalchemy.exc import DBAPIError  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.auth.audit_event_writer import AuditEvent  # noqa: E402
from app.auth.organization_workspace_schema import (  # noqa: E402
    APP_ROLE_NAME,
    Organization,
    Workspace,
    set_tenant_context,
)
from app.db.tenant_context import TenantContext  # noqa: E402
from app.litellm.organization_gateway_policy_store import (  # noqa: E402
    ActorInfo,
    OrganizationGatewayPolicyRecord,
    OrganizationGatewayRouteChangeRecord,
    ProjectNotFoundError,
    get_organization_gateway_policy,
    list_route_changes,
    record_route_change,
    upsert_organization_gateway_policy,
)
from app.projects.project_schema import Project, initial_status_for  # noqa: E402

POLICIES_TABLE = cast(Table, OrganizationGatewayPolicyRecord.__table__)
ROUTE_CHANGES_TABLE = cast(Table, OrganizationGatewayRouteChangeRecord.__table__)

FAILURES: list[str] = []


def check(criterion_id: str, description: str, condition: bool) -> None:
    if not condition:
        FAILURES.append(f"FAIL [{criterion_id}] {description}")


def _required_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        print(
            f"BLOCKED_EXTERNAL: {name} is not set -- this task's real "
            "persistence/tenant-isolation/append-only evidence requires a "
            "live Postgres (migration owner connection). Not silently "
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


def _run(args: list[str], database_url: str) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["SOVRA_DATABASE_URL"] = database_url
    return subprocess.run(args, cwd=str(API_ROOT), capture_output=True, text=True, env=env)


def main() -> None:
    migration_url = _required_env("SOVRA_DATABASE_URL")
    app_password = _required_env("SOVRA_APP_DB_PASSWORD")

    upgrade = _run([sys.executable, "-m", "alembic", "upgrade", "head"], migration_url)
    check(
        "FTF-1206A",
        f"alembic upgrade head runs cleanly (exit {upgrade.returncode})\n"
        f"stdout: {upgrade.stdout}\nstderr: {upgrade.stderr}",
        upgrade.returncode == 0,
    )
    if FAILURES:
        print(f"FTF-1206A organization gateway policy real proof: {len(FAILURES)} failed")
        for failure in FAILURES:
            print(f"  {failure}")
        sys.exit(1)

    # -- database_changes: Sections 6/65/71's own upgrade/downgrade --------
    #    round-trip requirement, proven against this task's own real
    #    migration specifically (not merely trusted from a prior task).
    downgrade = _run([sys.executable, "-m", "alembic", "downgrade", "-1"], migration_url)
    check(
        "FTF-1206A",
        f"alembic downgrade -1 runs cleanly (exit {downgrade.returncode})\n"
        f"stdout: {downgrade.stdout}\nstderr: {downgrade.stderr}",
        downgrade.returncode == 0,
    )
    reupgrade = _run([sys.executable, "-m", "alembic", "upgrade", "head"], migration_url)
    check(
        "FTF-1206A",
        f"alembic upgrade head re-applies cleanly after downgrade -1 "
        f"(exit {reupgrade.returncode})\nstdout: {reupgrade.stdout}\nstderr: {reupgrade.stderr}",
        reupgrade.returncode == 0,
    )

    owner_engine = create_engine(migration_url)
    app_engine = create_engine(_app_role_url(migration_url, app_password))

    # -- Seed two real tenants, each with a real workspace + project -------
    with Session(owner_engine) as session:
        org1 = Organization(name="Acme 1206A", slug=f"acme-1206a-{uuid.uuid4().hex[:8]}")
        org2 = Organization(name="Globex 1206A", slug=f"globex-1206a-{uuid.uuid4().hex[:8]}")
        session.add_all([org1, org2])
        session.flush()
        ws1 = Workspace(organization_id=org1.id, name="Acme Main", slug="main")
        ws2 = Workspace(organization_id=org2.id, name="Globex Main", slug="main")
        session.add_all([ws1, ws2])
        session.flush()
        project1 = Project(
            organization_id=org1.id,
            workspace_id=ws1.id,
            name="org1-project",
            project_type="generic_model_tuning",
            status=initial_status_for("generic_model_tuning"),
            created_by="seed-harness",
        )
        project2 = Project(
            organization_id=org2.id,
            workspace_id=ws2.id,
            name="org2-project",
            project_type="generic_model_tuning",
            status=initial_status_for("generic_model_tuning"),
            created_by="seed-harness",
        )
        session.add_all([project1, project2])
        session.commit()
        org1_id, org2_id = org1.id, org2.id
        project1_id, project2_id = project1.id, project2.id

    ctx1 = TenantContext(organization_id=org1_id)
    ctx2 = TenantContext(organization_id=org2_id)

    # -- 1. Acceptance: no policy persisted yet reads back as None, not an -
    #       error or a fabricated default.
    with Session(app_engine) as session:
        set_tenant_context(session, org1_id)
        policy_before = get_organization_gateway_policy(session, ctx1)
    check(
        "FTF-1206A",
        "acceptance: an organization with no persisted policy yet reads back as None",
        policy_before is None,
    )

    # -- 2. Acceptance: upsert creates the row, visible after commit, with -
    #       a real audit event in the same transaction.
    policy_v1 = OrganizationGatewayPolicy(
        allowed_registration_namespaces=frozenset({"factory-candidates"}),
        allowed_deployment_namespaces=frozenset({"factory-production"}),
        approvers=frozenset({"user:jane", "user:ahmed"}),
    )
    create_request_id = f"req-{uuid.uuid4().hex[:12]}"
    with Session(app_engine) as session:
        set_tenant_context(session, org1_id)
        upsert_organization_gateway_policy(
            session,
            ctx1,
            policy_v1,
            actor=ActorInfo(actor_type="user", actor_id="user-1206a", request_id=create_request_id),
        )
        session.commit()

    with Session(owner_engine) as session:
        row_count = session.execute(
            select(POLICIES_TABLE.c.id).where(POLICIES_TABLE.c.organization_id == org1_id)
        ).all()
        audit_row = session.execute(
            select(AuditEvent).where(AuditEvent.request_id == create_request_id)
        ).scalar_one_or_none()
    check(
        "FTF-1206A", "acceptance: exactly one policy row is persisted for org1", len(row_count) == 1
    )
    check(
        "FTF-1206A",
        "acceptance: the create is audited via FTF-0104's writer, in the same "
        "transaction, with the real action/resource identity recorded",
        audit_row is not None
        and audit_row.action == "organization_gateway_policy.create"
        and audit_row.resource_type == "organization_gateway_policy",
    )

    with Session(app_engine) as session:
        set_tenant_context(session, org1_id)
        read_back = get_organization_gateway_policy(session, ctx1)
    check(
        "FTF-1206A",
        "acceptance: the persisted policy round-trips exactly as written",
        read_back is not None
        and read_back.allowed_registration_namespaces == frozenset({"factory-candidates"})
        and read_back.allowed_deployment_namespaces == frozenset({"factory-production"})
        and read_back.approvers == frozenset({"user:jane", "user:ahmed"})
        and read_back.route_change_requires_approval is True,
    )

    # -- 3. Acceptance: a second upsert updates the same row in place -------
    #       (Law 2 -- an organization's policy is one atomically-updated
    #       statement, never a growing history of competing rows).
    policy_v2 = OrganizationGatewayPolicy(
        allowed_registration_namespaces=frozenset({"factory-candidates", "factory-staging"}),
        allowed_deployment_namespaces=frozenset({"factory-production"}),
        approvers=frozenset({"user:jane"}),
        route_change_requires_approval=False,
    )
    update_request_id = f"req-{uuid.uuid4().hex[:12]}"
    with Session(app_engine) as session:
        set_tenant_context(session, org1_id)
        upsert_organization_gateway_policy(
            session,
            ctx1,
            policy_v2,
            actor=ActorInfo(actor_type="user", actor_id="user-1206a", request_id=update_request_id),
        )
        session.commit()

    with Session(owner_engine) as session:
        row_count_after_update = session.execute(
            select(POLICIES_TABLE.c.id).where(POLICIES_TABLE.c.organization_id == org1_id)
        ).all()
        update_audit_row = session.execute(
            select(AuditEvent).where(AuditEvent.request_id == update_request_id)
        ).scalar_one_or_none()
    check(
        "FTF-1206A",
        "acceptance: updating an existing policy still leaves exactly one row "
        "(an UPDATE in place, never a second competing row)",
        len(row_count_after_update) == 1,
    )
    check(
        "FTF-1206A",
        "acceptance: the update is audited as organization_gateway_policy.update",
        update_audit_row is not None
        and update_audit_row.action == "organization_gateway_policy.update",
    )

    with Session(app_engine) as session:
        set_tenant_context(session, org1_id)
        policy_v2_read = get_organization_gateway_policy(session, ctx1)
    check(
        "FTF-1206A",
        "acceptance: the updated policy round-trips exactly as written, "
        "including route_change_requires_approval=False",
        policy_v2_read is not None
        and policy_v2_read.allowed_registration_namespaces
        == frozenset({"factory-candidates", "factory-staging"})
        and policy_v2_read.approvers == frozenset({"user:jane"})
        and policy_v2_read.route_change_requires_approval is False,
    )

    # -- 4. negative_acceptance: cross-tenant read is denied, identically to -
    #       missing -- org2 sees no policy for org1's row.
    with Session(app_engine) as session:
        set_tenant_context(session, org2_id)
        org2_view_of_org1_policy = get_organization_gateway_policy(session, ctx2)
    check(
        "FTF-1206A",
        "negative_acceptance: a connection scoped to a different tenant sees "
        "no policy where org1's real one exists (RLS, not an app-layer filter)",
        org2_view_of_org1_policy is None,
    )

    # -- 5. Acceptance: record_route_change appends a real row, audited, ----
    #       for a project that genuinely belongs to the caller's own org.
    change_request_id = f"req-{uuid.uuid4().hex[:12]}"
    with Session(app_engine) as session:
        set_tenant_context(session, org1_id)
        record_route_change(
            session,
            ctx1,
            project_id=project1_id,
            actor="user:jane",
            target_deployment_namespace="factory-production",
            approved=True,
            actor_info=ActorInfo(
                actor_type="user", actor_id="user-1206a", request_id=change_request_id
            ),
        )
        session.commit()

    denied_request_id = f"req-{uuid.uuid4().hex[:12]}"
    with Session(app_engine) as session:
        set_tenant_context(session, org1_id)
        record_route_change(
            session,
            ctx1,
            project_id=project1_id,
            actor="user:outsider",
            target_deployment_namespace="factory-production",
            approved=False,
            actor_info=ActorInfo(
                actor_type="user", actor_id="user-1206a", request_id=denied_request_id
            ),
        )
        session.commit()

    with Session(app_engine) as session:
        set_tenant_context(session, org1_id)
        history = list_route_changes(session, ctx1, project1_id)
    check(
        "FTF-1206A",
        "acceptance: the append-only route-change history is real and "
        "retrievable, oldest first, with the real approved/denied outcomes",
        [r.approved for r in history] == [True, False]
        and history[0].actor == "user:jane"
        and history[1].actor == "user:outsider",
    )

    with Session(owner_engine) as session:
        change_audit_rows = (
            session.execute(
                select(AuditEvent).where(
                    AuditEvent.request_id.in_([change_request_id, denied_request_id])
                )
            )
            .scalars()
            .all()
        )
    check(
        "FTF-1206A",
        "acceptance: both route-change records are audited via FTF-0104's writer",
        len(change_audit_rows) == 2
        and all(
            row.action == "organization_gateway_route_change.record" for row in change_audit_rows
        ),
    )

    # -- 6. negative_acceptance: cross-tenant write/read is denied, --------
    #       identically to a missing project -- org1's context against
    #       org2's real project, and a genuinely missing project, both
    #       raise the identical ProjectNotFoundError.
    with Session(app_engine) as session:
        set_tenant_context(session, org1_id)
        try:
            record_route_change(
                session,
                ctx1,
                project_id=project2_id,
                actor="user:jane",
                target_deployment_namespace="factory-production",
                approved=True,
                actor_info=ActorInfo(
                    actor_type="user", actor_id="user-1206a", request_id="should-not-write"
                ),
            )
            check(
                "FTF-1206A",
                "negative_acceptance: recording a route change against another "
                "organization's real project is denied",
                False,
            )
        except ProjectNotFoundError as cross_tenant_exc:
            check(
                "FTF-1206A",
                "negative_acceptance: recording a route change against another "
                "organization's real project is denied",
                True,
            )
            cross_tenant_exc_type = type(cross_tenant_exc)
            cross_tenant_exc_message = str(cross_tenant_exc)
        session.rollback()

    with Session(app_engine) as session:
        set_tenant_context(session, org1_id)
        try:
            record_route_change(
                session,
                ctx1,
                project_id=uuid.uuid4(),
                actor="user:jane",
                target_deployment_namespace="factory-production",
                approved=True,
                actor_info=ActorInfo(
                    actor_type="user", actor_id="user-1206a", request_id="should-not-write-2"
                ),
            )
            check(
                "FTF-1206A",
                "a genuinely missing project_id is denied the same way",
                False,
            )
        except ProjectNotFoundError as missing_exc:
            check(
                "FTF-1206A",
                "negative_acceptance: a foreign-tenant project ID behaves "
                "identically to a genuinely missing one (same exception type, "
                "same message shape)",
                cross_tenant_exc_type is type(missing_exc)
                and cross_tenant_exc_message == str(missing_exc),
            )
        session.rollback()

    with Session(owner_engine) as session:
        leaked_rows = session.execute(
            select(ROUTE_CHANGES_TABLE.c.id).where(
                ROUTE_CHANGES_TABLE.c.request_id.in_(["should-not-write", "should-not-write-2"])
            )
        ).all()
    check(
        "FTF-1206A",
        "neither denied cross-tenant/missing-project attempt left a row behind",
        leaked_rows == [],
    )

    # -- 7. negative_acceptance: the app role cannot UPDATE or DELETE ------
    #       existing route-change history (append-only, enforced by the
    #       database, not merely by this module choosing not to issue it).
    try:
        with Session(app_engine) as session:
            set_tenant_context(session, org1_id)
            session.execute(
                ROUTE_CHANGES_TABLE.update()
                .where(ROUTE_CHANGES_TABLE.c.request_id == change_request_id)
                .values(approved=False)
            )
            session.commit()
        check(
            "FTF-1206A",
            "negative_acceptance: UPDATEing an existing route-change row as "
            "the restricted app role is rejected by the database",
            False,
        )
    except DBAPIError as exc:
        check(
            "FTF-1206A",
            f"negative_acceptance: UPDATEing an existing route-change row as "
            f"the restricted app role is rejected by the database ({exc.orig!r})",
            True,
        )

    try:
        with Session(app_engine) as session:
            set_tenant_context(session, org1_id)
            session.execute(
                ROUTE_CHANGES_TABLE.delete().where(
                    ROUTE_CHANGES_TABLE.c.request_id == change_request_id
                )
            )
            session.commit()
        check(
            "FTF-1206A",
            "negative_acceptance: DELETEing an existing route-change row as "
            "the restricted app role is rejected by the database",
            False,
        )
    except DBAPIError as exc:
        check(
            "FTF-1206A",
            f"negative_acceptance: DELETEing an existing route-change row as "
            f"the restricted app role is rejected by the database ({exc.orig!r})",
            True,
        )

    # -- 8. The policy table genuinely permits UPDATE (its own design) but --
    #       never DELETE -- a policy is reset via UPDATE, never removed.
    try:
        with Session(app_engine) as session:
            set_tenant_context(session, org1_id)
            session.execute(
                POLICIES_TABLE.delete().where(POLICIES_TABLE.c.organization_id == org1_id)
            )
            session.commit()
        check(
            "FTF-1206A",
            "DELETEing an organization's own policy row as the restricted "
            "app role is rejected by the database (no legitimate deletion path)",
            False,
        )
    except DBAPIError as exc:
        check(
            "FTF-1206A",
            f"DELETEing an organization's own policy row as the restricted "
            f"app role is rejected by the database ({exc.orig!r})",
            True,
        )

    # -- 9. negative_acceptance: no allow-all policy -- a raw INSERT of a ---
    #       non-array JSON value into a namespaces column is rejected by
    #       the real database CHECK constraint, not only by this module's
    #       own application-level validation.
    try:
        with Session(app_engine) as session:
            set_tenant_context(session, org1_id)
            session.execute(
                text(
                    "INSERT INTO organization_gateway_policies "
                    "(id, organization_id, allowed_registration_namespaces, "
                    "allowed_deployment_namespaces, approvers, "
                    "route_change_requires_approval) "
                    "VALUES (gen_random_uuid(), :org_id, "
                    "'\"not-an-array\"'::jsonb, '[]'::jsonb, '[]'::jsonb, true)"
                ),
                {"org_id": str(org2_id)},
            )
            session.commit()
        check(
            "FTF-1206A",
            "a raw INSERT with a non-array JSON value in a namespaces column "
            "is rejected by the real database CHECK constraint",
            False,
        )
    except DBAPIError as exc:
        check(
            "FTF-1206A",
            f"a raw INSERT with a non-array JSON value in a namespaces column "
            f"is rejected by the real database CHECK constraint ({exc.orig!r})",
            True,
        )

    if FAILURES:
        print(f"FTF-1206A organization gateway policy real proof: {len(FAILURES)} failed")
        for failure in FAILURES:
            print(f"  {failure}")
        sys.exit(1)

    print("FTF-1206A organization gateway policy real proof: all checks passed")
    sys.exit(0)


if __name__ == "__main__":
    main()
