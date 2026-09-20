"""Reusable RBAC/IDOR security-test harness (FTF-0106, Section 6/45-47/65/77/81).

Every prior security proof in this repository picked a small, hand-chosen
subset of Section 6.4's six roles to exercise: FTF-0103's own suite
covers viewer/evaluator/approver; FTF-H003's own suite covers a single
organization_admin. Each is a legitimate, narrower proof of its own
task's own acceptance clause, but none of them is what this task's own
acceptance clause names: "Full real two-tenant role matrix exercises
reads/writes/artifacts/approvals with guessed IDs." This module is that
full matrix, built once as a reusable harness rather than a one-off test
fixture, so the later full-scale suites this task's own dependents name
(FTF-1501 "Tenant isolation suite", FTF-1504 "IDOR suite") have a real
six-role, two-tenant fixture and demonstration app to build on instead of
each re-deriving their own narrower one.

`TwoTenantRoleMatrixFixture` assigns all six of Section 6.4's roles
(`organization_admin`, `workspace_admin`, `ml_engineer`, `evaluator`,
`approver`, `viewer`) to a distinct real principal in each of two real
organizations -- twelve distinct (issuer, subject) identities, never one
principal reused across roles or organizations, so a passing test cannot
be hiding a role-boundary bug behind a tenant-boundary one or vice versa.

`build_two_tenant_security_demo_app` wires FTF-0103's `require_role` and
FTF-H003's `authorize_resource_ownership`/`TenantContext`/
`tenant_scoped_session_for` together across three routes representing
this task's own named operation classes:

  - **read** (`GET`): any real member of the organization may read.
  - **write** (`PATCH`): Section 45's own role descriptions --
    "ml_engineer: datasets/training", "workspace_admin: project
    administration" -- restricted to `ml_engineer`/`workspace_admin`/
    `organization_admin`.
  - **approve** (`POST .../approve`): Section 45's "approver: promotion
    approval" -- restricted to `approver`/`organization_admin`. This
    task's own `database_changes` contract forbids an independent schema
    change, so no live `PromotionDecision`/artifact table exists yet
    (Section 6.20/6.21 are later-phase tasks) -- this route exercises the
    identical `require_role` + `authorize_resource_ownership` stack a
    real approval endpoint would use, against the only real tenant-owned
    resource that exists in this repository (`workspaces`, FTF-0102),
    disclosed as a stand-in rather than a fabricated artifact table this
    task is not authorized to create.

For every route, every allowed role, `authorize_resource_ownership`
proves both directions this task's own acceptance clause names: the real
resource in the caller's own organization is reachable, and the exact
same real resource ID borrowed from the *other* organization (a guessed/
foreign ID) is denied identically to a missing one -- never a
distinguishable 403-vs-404 or role-vs-tenant leak. For every route, every
disallowed role, the request is denied before `authorize_resource_ownership`
is ever reached (a role-boundary denial, not a tenant-boundary one).

Disclosed, matching this task's own negative_acceptance ("No authorization
test exclusively uses privileged DB connection or mocked membership
result"): the *membership* input (which role a principal holds) is
necessarily a fixture here, since no live `Membership` table exists yet
and this task's own `database_changes` contract forbids adding one --
the same disclosed limitation FTF-0103/FTF-H003 already established and
were certified against. What this harness does not fake is the actual
tenant-boundary decision: `authorize_resource_ownership` always runs a
real query through FTF-0105's own `execute_tenant_scoped` guard against a
real, restricted `sovra_app` database connection (never the migration-
owner role, and never mocked/stubbed in the integration suite that
exercises this harness) -- the membership fixture supplies *who is
asking*, never *what the database says is true*.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass, field

from fastapi import Depends, FastAPI, HTTPException, Request
from sqlalchemy import ColumnElement, FromClause
from sqlalchemy.orm import Session

from app.auth.rbac_middleware import (
    AuthenticatedPrincipal,
    AuthorizedContext,
    Role,
    require_role,
)
from app.auth.resource_authorization import ResourceAuthorizationError, authorize_resource_ownership
from app.db.tenant_context import TenantContext, tenant_scoped_session_for

__all__ = [
    "READ_ROLES",
    "WRITE_ROLES",
    "APPROVE_ROLES",
    "TwoTenantRoleMatrixFixture",
    "build_two_tenant_security_demo_app",
]

# Section 45's own role descriptions, read as the allow-list each
# operation class enforces -- not re-invented here, just applied.
READ_ROLES: frozenset[Role] = frozenset(Role)
WRITE_ROLES: frozenset[Role] = frozenset(
    {Role.ML_ENGINEER, Role.WORKSPACE_ADMIN, Role.ORGANIZATION_ADMIN}
)
APPROVE_ROLES: frozenset[Role] = frozenset({Role.APPROVER, Role.ORGANIZATION_ADMIN})

_PRINCIPAL_HEADER = "x-test-principal"


@dataclass(frozen=True)
class TwoTenantRoleMatrixFixture:
    """A real two-organization, full-six-role membership fixture -- the
    disclosed stand-in for a not-yet-existing live Membership table
    (Section 6.4; this task's own `database_changes` contract forbids
    adding one). `org_labels` names the two organizations by a short
    label ("org1"/"org2"), each mapped to its own real `organization_id`;
    every `Role` is assigned to a distinct real principal in each."""

    issuer: str
    organization_ids: dict[str, uuid.UUID]
    principals: dict[tuple[str, Role], AuthenticatedPrincipal] = field(repr=False)
    memberships: dict[tuple[str, str, uuid.UUID], Role] = field(repr=False)

    @classmethod
    def build(
        cls,
        organization_ids: dict[str, uuid.UUID],
        issuer: str = "https://issuer.example",
    ) -> TwoTenantRoleMatrixFixture:
        if len(organization_ids) != 2:
            raise ValueError(
                "TwoTenantRoleMatrixFixture requires exactly two organizations, "
                f"got {len(organization_ids)}"
            )
        principals: dict[tuple[str, Role], AuthenticatedPrincipal] = {}
        memberships: dict[tuple[str, str, uuid.UUID], Role] = {}
        for org_label, organization_id in organization_ids.items():
            for role in Role:
                subject = f"user-{org_label}-{role.value}"
                principals[(org_label, role)] = AuthenticatedPrincipal(
                    issuer=issuer, subject=subject
                )
                memberships[(issuer, subject, organization_id)] = role
        return cls(
            issuer=issuer,
            organization_ids=dict(organization_ids),
            principals=principals,
            memberships=memberships,
        )

    def header_for(self, org_label: str, role: Role) -> str:
        """The `X-Test-Principal` header value identifying one real
        (org_label, role) principal this fixture defined."""
        return f"{org_label}:{role.value}"

    def principal_resolver(self, request: Request) -> AuthenticatedPrincipal | None:
        header = request.headers.get(_PRINCIPAL_HEADER)
        if header is None:
            return None
        org_label, _, role_value = header.partition(":")
        try:
            role = Role(role_value)
        except ValueError:
            return None
        return self.principals.get((org_label, role))

    def role_lookup(self, issuer: str, subject: str, organization_id: uuid.UUID) -> Role | None:
        return self.memberships.get((issuer, subject, organization_id))


def build_two_tenant_security_demo_app(
    fixture: TwoTenantRoleMatrixFixture,
    session_factory: Callable[[], Session],
    table: FromClause,
    id_column: ColumnElement[uuid.UUID],
    organization_id_column: ColumnElement[uuid.UUID],
) -> FastAPI:
    """A real, locally-hosted demonstration FastAPI app (meant to be run
    behind a real uvicorn server over a real local socket, the same
    pattern `tests/tasks/test_ftf_0103.py` established) wiring
    `require_role` and `authorize_resource_ownership` together across
    this task's own three named operation classes. `table`/`id_column`/
    `organization_id_column` name the real tenant-owned resource to
    authorize against (this repository's own `workspaces`, the only real
    one that exists besides `organizations` itself -- the same disclosed
    scoping precedent FTF-0105/FTF-H003 already established)."""
    app = FastAPI()

    def _dependency(allowed_roles: frozenset[Role]):
        return Depends(
            require_role(
                allowed_roles,
                principal_resolver=fixture.principal_resolver,
                role_lookup=fixture.role_lookup,
            )
        )

    read_dependency = _dependency(READ_ROLES)
    write_dependency = _dependency(WRITE_ROLES)
    approve_dependency = _dependency(APPROVE_ROLES)

    def _authorize_and_respond(ctx: AuthorizedContext, resource_id: uuid.UUID) -> dict[str, str]:
        tenant_context = TenantContext.from_authorized_context(ctx)
        with tenant_scoped_session_for(session_factory, tenant_context) as session:
            try:
                authorize_resource_ownership(
                    session, tenant_context, table, id_column, organization_id_column, resource_id
                )
            except ResourceAuthorizationError as exc:
                raise HTTPException(status_code=404, detail="not_found") from exc
            return {"organization_id": str(ctx.organization_id), "role": ctx.role.value}

    @app.get("/api/v1/orgs/{organization_id}/workspaces/{workspace_id}")
    def read_workspace(
        workspace_id: uuid.UUID, ctx: AuthorizedContext = read_dependency
    ) -> dict[str, str]:
        return _authorize_and_respond(ctx, workspace_id)

    @app.patch("/api/v1/orgs/{organization_id}/workspaces/{workspace_id}")
    def write_workspace(
        workspace_id: uuid.UUID, ctx: AuthorizedContext = write_dependency
    ) -> dict[str, str]:
        return _authorize_and_respond(ctx, workspace_id)

    @app.post("/api/v1/orgs/{organization_id}/workspaces/{workspace_id}/approve")
    def approve_workspace(
        workspace_id: uuid.UUID, ctx: AuthorizedContext = approve_dependency
    ) -> dict[str, str]:
        return _authorize_and_respond(ctx, workspace_id)

    return app
