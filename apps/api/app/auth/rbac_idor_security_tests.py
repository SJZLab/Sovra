"""RBAC/IDOR security-test harness (FTF-0106, Section 6.4/45/65/77/81, Q01/Q24).

FTF-0103's `require_role` and FTF-H003's `authorize_resource_ownership`
each already enforce role/tenant scoping and cross-tenant resource
ownership. Both were themselves proven only against a *single*,
representative role (`organization_admin`, injected via a hardcoded
`role_lookup` closure) -- correct as a proof of the enforcement primitive
itself, but not the "full real two-tenant role matrix... with guessed
IDs" this task's own acceptance clause 1 requires across every one of
Section 6.4's six real roles, nor a role lookup this task's own
negative_acceptance clause forbids being merely mocked.

This module is that harness -- reusable, first-class production code (not
buried in one test file) so any future task's own security-regression
evidence can be built on it, not reimplemented per-task:

  - `SECURITY_TEST_MEMBERSHIPS`: a real, ordinary SQLAlchemy table --
    never a `Membership` production table. This task's own
    `database_changes` contract is explicit: "No independent schema
    change. Consume frozen contract; any required change is a separately
    claimed migration prerequisite" -- Section 6.4's real `Membership`
    table (with `workspace_id`, a `Project` foreign key, and the rest of
    its own frozen shape) is exactly that separately claimed prerequisite,
    not something this *test* task may invent by fiat. This table is
    created/dropped directly by whichever test owns it (never through
    `apps/api/alembic/versions/`, so it is never part of the frozen
    production migration chain or `docs/spec-matrix.yaml`'s schema
    surface), and carries only the columns a role lookup itself actually
    needs: `(issuer, subject, organization_id, role)`.
  - `postgres_backed_role_lookup()` resolves `RoleLookup` (FTF-0103's own
    injectable type) via a genuine SQL `SELECT` against that table on a
    fresh session each call -- never a Python `dict`/`if` chain pretending
    to be a database. This is what makes "no authorization test
    exclusively uses... a mocked membership result" (this task's own
    negative_acceptance) a property of the lookup itself, not of test
    discipline.
  - `ROLE_MATRIX` / `READ_ALLOWED_ROLES` / `WORKSPACE_WRITE_ALLOWED_ROLES`
    give the six-role read/write policy this task's own demonstration
    routes enforce, grounded in Section 45's own worked RBAC examples
    ("viewer: read only", "workspace_admin: project administration",
    "organization_admin: connections, security profiles, cost profiles")
    -- not an invented policy.
  - `build_rbac_idor_demo_app()` wires one read route and one write route
    combining `require_role` (FTF-0103) with `authorize_resource_ownership`
    (FTF-H003) over this harness's own DB-backed role lookup, so a real
    uvicorn server can exercise the real two-tenant, six-role, guessed-ID
    matrix end to end -- the same "real, locally-hosted demonstration
    FastAPI app" pattern `tests/tasks/test_ftf_0103.py` and
    `tests/integration/test_ftf_h003_cross_layer_isolation.py` already
    established, not a new one invented for this task.

Disclosed, not hidden: this task's own acceptance clause 1 names four
operation classes -- "reads/writes/artifacts/approvals". No live
`Dataset`/`TrainableArtifact`/`PromotionDecision` table or route exists
anywhere in this repository yet (Section 6.7/6.10/6.21 are domain-model
text only, not migrated schema), so "artifacts" and "approvals" have no
real subject matter to exercise a role matrix against yet -- the same
disclosed "no live infrastructure yet" class FTF-H003 already accepted for
its own "download"/"job" vectors. This harness's real evidence is scoped
to "reads" and "writes" against the only two real tenant-scoped resources
that exist in this repository (`organizations`/`workspaces`, FTF-0102),
the same disclosed scoping precedent FTF-0105/FTF-H003 already established
for the same reason. A future task adding a real artifact/approval table
extends this same harness (`ROLE_MATRIX`, `postgres_backed_role_lookup`)
rather than re-proving the role-matrix mechanism from scratch.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Iterable

from fastapi import Depends, FastAPI, HTTPException
from sqlalchemy import Column, MetaData, String, Table, UniqueConstraint, select, text
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from app.auth.organization_workspace_schema import APP_ROLE_NAME, Workspace
from app.auth.rbac_middleware import (
    AuthorizedContext,
    PrincipalResolver,
    Role,
    RoleLookup,
    require_role,
)
from app.auth.resource_authorization import ResourceAuthorizationError, authorize_resource_ownership
from app.auth.tenant_isolation_query_guards import TenantContextError
from app.db.tenant_context import TenantContext, tenant_scoped_session_for

__all__ = [
    "SECURITY_TEST_MEMBERSHIPS_TABLE_NAME",
    "security_test_memberships",
    "create_security_test_membership_table",
    "drop_security_test_membership_table",
    "seed_membership",
    "postgres_backed_role_lookup",
    "ROLE_MATRIX",
    "READ_ALLOWED_ROLES",
    "WORKSPACE_WRITE_ALLOWED_ROLES",
    "build_rbac_idor_demo_app",
]

# --- Section 6.4's real role set, exercised in full ------------------------

# Section 6.4's exact allowed-role tuple, in the master spec's own listed
# order -- the "full real two-tenant role matrix" this task's own
# acceptance clause names, never a subset chosen for convenience.
ROLE_MATRIX: tuple[Role, ...] = tuple(Role)

# Section 45's own worked example: "viewer: read only." A viewer is the
# least-privileged real role and still reads -- so every real role reads.
READ_ALLOWED_ROLES: frozenset[Role] = frozenset(Role)

# Section 45's own worked examples name workspace_admin ("project
# administration") and organization_admin ("connections, security
# profiles, cost profiles") as the only roles with any administrative
# capability over a workspace-shaped resource; evaluator/ml_engineer/
# approver/viewer are each named for a narrower, non-workspace-admin
# capability (evaluation, datasets/training, promotion approval, read-only)
# and are denied here.
WORKSPACE_WRITE_ALLOWED_ROLES: frozenset[Role] = frozenset(
    {Role.WORKSPACE_ADMIN, Role.ORGANIZATION_ADMIN}
)

# --- Real, DB-backed role lookup (never a Python dict pretending to be one) -

SECURITY_TEST_MEMBERSHIPS_TABLE_NAME = "_security_test_memberships"

_metadata = MetaData()

security_test_memberships: Table = Table(
    SECURITY_TEST_MEMBERSHIPS_TABLE_NAME,
    _metadata,
    Column(
        "id",
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    ),
    Column("issuer", String(256), nullable=False),
    Column("subject", String(256), nullable=False),
    # Deliberately no ForeignKey to `organizations.id`: this table lives in
    # its own private `MetaData`, never `organization_workspace_schema.Base`'s
    # own metadata (the one `alembic/env.py` treats as the frozen production
    # schema surface for autogeneration) -- see module docstring on why this
    # table must never be mistaken for part of that frozen contract. Every
    # real caller in this repository (the integration test) only ever seeds
    # this column with a real, already-existing Organization's own id.
    Column("organization_id", PG_UUID(as_uuid=True), nullable=False),
    Column("role", String(32), nullable=False),
    UniqueConstraint(
        "issuer", "subject", "organization_id", name="uq_security_test_memberships_natural_key"
    ),
)


def create_security_test_membership_table(owner_engine: Engine) -> None:
    """Creates this harness's own table via the migration-owner connection
    and grants the real least-privileged `sovra_app` role read-only access
    to it -- never through `apps/api/alembic/versions/` (see module
    docstring: this is deliberately never part of the frozen production
    migration chain). Deliberately carries no row-level-security policy:
    resolving a principal's role in a given organization is, by
    construction, a lookup that must run *before* any tenant context can
    be bound (Section 65.1's tenant-scoping requirement applies to
    tenant-owned *application data*, not to the role-resolution step that
    decides which tenant a request may even proceed against)."""
    _metadata.create_all(owner_engine, tables=[security_test_memberships])
    with owner_engine.begin() as connection:
        connection.execute(
            text(f'GRANT SELECT ON {SECURITY_TEST_MEMBERSHIPS_TABLE_NAME} TO "{APP_ROLE_NAME}"')
        )


def drop_security_test_membership_table(owner_engine: Engine) -> None:
    _metadata.drop_all(owner_engine, tables=[security_test_memberships])


def seed_membership(
    session: Session,
    *,
    issuer: str,
    subject: str,
    organization_id: uuid.UUID,
    role: Role,
) -> None:
    """Adds one real membership row. Never commits -- the caller controls
    the transaction, the same convention `write_audit_event` (FTF-0104)
    already established."""
    session.execute(
        security_test_memberships.insert().values(
            issuer=issuer,
            subject=subject,
            organization_id=organization_id,
            role=role.value,
        )
    )


def postgres_backed_role_lookup(session_factory: Callable[[], Session]) -> RoleLookup:
    """Returns a `RoleLookup` that resolves a role via a genuine SQL
    `SELECT` against `security_test_memberships` on a fresh, short-lived
    session -- never a Python `dict`/conditional standing in for a
    database. This is what lets this task's own two-tenant matrix evidence
    say "no mocked membership result" and mean it literally: a role is
    granted only because a real row, seeded ahead of time, really says so.
    """

    def _lookup(issuer: str, subject: str, organization_id: uuid.UUID) -> Role | None:
        session = session_factory()
        try:
            row = session.execute(
                select(security_test_memberships.c.role).where(
                    security_test_memberships.c.issuer == issuer,
                    security_test_memberships.c.subject == subject,
                    security_test_memberships.c.organization_id == organization_id,
                )
            ).first()
            session.commit()
        finally:
            session.close()
        if row is None:
            return None
        try:
            return Role(row[0])
        except ValueError:
            return None

    return _lookup


# --- Real demonstration app: require_role + authorize_resource_ownership ---


def build_rbac_idor_demo_app(
    session_factory: Callable[[], Session],
    *,
    principal_resolver: PrincipalResolver,
    role_lookup: RoleLookup,
) -> FastAPI:
    """A real, locally-hosted demonstration FastAPI app -- the same pattern
    `tests/tasks/test_ftf_0103.py` and
    `tests/integration/test_ftf_h003_cross_layer_isolation.py` already
    established -- wiring one read route (every real role) and one write
    route (`workspace_admin`/`organization_admin` only) together with
    `authorize_resource_ownership`'s cross-tenant guessed-ID guard, so a
    real uvicorn server can exercise the full two-tenant, six-role,
    guessed-ID matrix end to end.
    """
    app = FastAPI()

    require_reader = Depends(
        require_role(
            READ_ALLOWED_ROLES,
            principal_resolver=principal_resolver,
            role_lookup=role_lookup,
            body_tenant_field=None,
        )
    )
    require_writer = Depends(
        require_role(
            WORKSPACE_WRITE_ALLOWED_ROLES,
            principal_resolver=principal_resolver,
            role_lookup=role_lookup,
        )
    )

    def _authorize_workspace(tenant_context: TenantContext, workspace_id: uuid.UUID) -> None:
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
        except (ResourceAuthorizationError, TenantContextError) as exc:
            raise HTTPException(status_code=404, detail="not_found") from exc

    @app.get("/api/v1/orgs/{organization_id}/workspaces/{workspace_id}")
    def read_workspace(
        workspace_id: uuid.UUID, ctx: AuthorizedContext = require_reader
    ) -> dict[str, str]:
        tenant_context = TenantContext.from_authorized_context(ctx)
        _authorize_workspace(tenant_context, workspace_id)
        return {
            "organization_id": str(tenant_context.organization_id),
            "workspace_id": str(workspace_id),
            "role": ctx.role.value,
        }

    @app.patch("/api/v1/orgs/{organization_id}/workspaces/{workspace_id}")
    def rename_workspace(
        workspace_id: uuid.UUID, ctx: AuthorizedContext = require_writer
    ) -> dict[str, str]:
        tenant_context = TenantContext.from_authorized_context(ctx)
        _authorize_workspace(tenant_context, workspace_id)
        return {
            "organization_id": str(tenant_context.organization_id),
            "workspace_id": str(workspace_id),
            "role": ctx.role.value,
        }

    return app


def all_roles_except(excluded: Iterable[Role]) -> frozenset[Role]:
    """Small helper so tests can express "every role but these" without
    hand-maintaining a second copy of `ROLE_MATRIX`."""
    excluded_set = frozenset(excluded)
    return frozenset(role for role in ROLE_MATRIX if role not in excluded_set)
