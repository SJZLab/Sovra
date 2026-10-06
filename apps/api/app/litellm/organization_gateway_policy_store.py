"""Organization gateway policy persistence store (FTF-1206A, Section 3/6/38/
65/66/71/77/81).

The real storage layer `packages/litellm_client/organization_policy_control.py`'s
own `database_changes` clause deferred to "a separately claimed migration
prerequisite" (see `docs/adr/0002-task-addendum-ftf-1206a.md`): persists an
organization's own `OrganizationGatewayPolicy` (allowed registration/
deployment namespaces, approvers, whether a route change needs approval) and
an append-only record of every real route-change decision (Section
38.5-38.6), both tenant-scoped under the real forward migration
(`apps/api/alembic/versions/0019_organization_gateway_policies.py`).

This module is a pure persistence/query layer -- it never re-implements
`organization_policy_control.py`'s own decision procedures
(`enforce_project_gateway_permission_request`/`enforce_route_change_authority`),
and `record_route_change` does not itself decide whether a route change is
authorized; a caller proves that first (against the policy this store
returns) and passes the real outcome in as `approved`/`bypass_requested` --
a weaker, additive-only version of the "decision happens elsewhere, this
layer only durably records it" shape FTF-04A18's own
`discovered_agent_candidate_selection.py` already established (that task
recorded a selection via an existing audit table alone, with no new
mutable column or history table of its own; this task's own acceptance
clause instead requires a real, queryable history table, since a route
change -- unlike a one-time candidate selection -- is a recurring decision
an organization needs to review over time).

Every real query goes through the same certified composition every prior
task in this chain already proved for exactly this class of resource:
`TenantContext`/`tenant_scoped_session_for` (FTF-H003) bind the
transaction-local RLS tenant context; `authorize_resource_ownership`
(FTF-H003) proves a referenced project ID belongs to the caller's own
authenticated organization *before* any row referencing it is written --
denied identically whether the project ID does not exist at all or belongs
to a different organization (this task's own negative_acceptance, "Cross-
tenant read or write is denied... a foreign-tenant ID behaves identically to
a missing one"); `execute_tenant_scoped` (FTF-0105) is what every actual
SELECT in this module runs through, never a bare `session.execute(...)`.
Every mutating operation writes an audit event via FTF-0104's
`write_audit_event` in the *same* transaction as the write it describes.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from packages.litellm_client.organization_policy_control import (
    OrganizationGatewayPolicy,
    validate_approver_identity,
    validate_namespace,
)
from sqlalchemy import Boolean, DateTime, String, select
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, Session, mapped_column

from app.auth.audit_event_writer import write_audit_event
from app.auth.organization_workspace_schema import Base
from app.auth.resource_authorization import ResourceAuthorizationError, authorize_resource_ownership
from app.auth.tenant_isolation_query_guards import execute_tenant_scoped
from app.db.tenant_context import TenantContext
from app.projects.project_schema import Project

__all__ = [
    "ActorInfo",
    "ProjectNotFoundError",
    "OrganizationGatewayPolicyRecord",
    "OrganizationGatewayRouteChangeRecord",
    "get_organization_gateway_policy",
    "upsert_organization_gateway_policy",
    "record_route_change",
    "list_route_changes",
]

_PROJECT_TABLE = Project.__table__
_PROJECT_ID_COLUMN = _PROJECT_TABLE.c.id
_PROJECT_ORG_COLUMN = _PROJECT_TABLE.c.organization_id


class ProjectNotFoundError(LookupError):
    """No project with this ID exists in the caller's own organization --
    raised identically whether the ID is missing entirely or belongs to a
    different organization (this task's own negative_acceptance)."""


@dataclass(frozen=True)
class ActorInfo:
    """Who is performing this mutation, for the audit event it writes.
    `actor_id` is always the authenticated principal's own `subject`, never
    a client-supplied string."""

    actor_type: str
    actor_id: str
    request_id: str


class OrganizationGatewayPolicyRecord(Base):
    """One real row per organization -- the organization's own current,
    mutable gateway policy (Section 38.1/38.5-38.6). See this module's own
    docstring and the tracked migration's own docstring for the full
    disclosed reading."""

    __tablename__ = "organization_gateway_policies"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    organization_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    allowed_registration_namespaces: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    allowed_deployment_namespaces: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    approvers: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    route_change_requires_approval: Mapped[bool] = mapped_column(Boolean, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class OrganizationGatewayRouteChangeRecord(Base):
    """One real, append-only row per real route-change decision attempt
    (Section 38.5-38.6) -- never updated or deleted once written (this
    task's own negative_acceptance, enforced at the database level by the
    tracked migration's own restricted GRANT)."""

    __tablename__ = "organization_gateway_route_changes"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    organization_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    project_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    actor: Mapped[str] = mapped_column(String(200), nullable=False)
    target_deployment_namespace: Mapped[str] = mapped_column(String(128), nullable=False)
    approved: Mapped[bool] = mapped_column(Boolean, nullable=False)
    bypass_requested: Mapped[bool] = mapped_column(Boolean, nullable=False)
    request_id: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


def _authorize_project_or_raise(
    session: Session, context: TenantContext, project_id: uuid.UUID
) -> None:
    if not isinstance(project_id, uuid.UUID):
        raise ProjectNotFoundError(f"project_id must be a uuid.UUID, got {project_id!r}")
    try:
        authorize_resource_ownership(
            session, context, _PROJECT_TABLE, _PROJECT_ID_COLUMN, _PROJECT_ORG_COLUMN, project_id
        )
    except ResourceAuthorizationError as exc:
        raise ProjectNotFoundError(str(exc)) from exc


def _to_policy(record: OrganizationGatewayPolicyRecord) -> OrganizationGatewayPolicy:
    return OrganizationGatewayPolicy(
        allowed_registration_namespaces=frozenset(record.allowed_registration_namespaces),
        allowed_deployment_namespaces=frozenset(record.allowed_deployment_namespaces),
        approvers=frozenset(record.approvers),
        route_change_requires_approval=record.route_change_requires_approval,
    )


def get_organization_gateway_policy(
    session: Session, context: TenantContext
) -> OrganizationGatewayPolicy | None:
    """Returns the caller's own organization's persisted gateway policy, or
    `None` if it has never set one. Scoped by `execute_tenant_scoped`, so a
    row belonging to a different organization is never returned -- RLS
    filters it out before the application layer ever compares an ID."""
    statement = select(OrganizationGatewayPolicyRecord).where(
        OrganizationGatewayPolicyRecord.organization_id == context.organization_id
    )
    result = execute_tenant_scoped(session, context.organization_id, statement)
    record = result.scalars().one_or_none()
    return None if record is None else _to_policy(record)


def upsert_organization_gateway_policy(
    session: Session,
    context: TenantContext,
    policy: OrganizationGatewayPolicy,
    *,
    actor: ActorInfo,
) -> OrganizationGatewayPolicyRecord:
    """Persists `policy` as the caller's own organization's current gateway
    policy -- creating the row on first use, updating it in place
    thereafter (Law 2: an organization's policy is a single, atomically-
    updated statement, never a growing history of competing rows). `policy`
    is already a validated `OrganizationGatewayPolicy` (every namespace/
    approver value real and safe by construction, per that type's own
    `__post_init__`); this function only ever persists what that type
    already proved well-formed."""
    statement = select(OrganizationGatewayPolicyRecord).where(
        OrganizationGatewayPolicyRecord.organization_id == context.organization_id
    )
    result = execute_tenant_scoped(session, context.organization_id, statement)
    record = result.scalars().one_or_none()

    now = datetime.now(UTC)
    if record is None:
        record = OrganizationGatewayPolicyRecord(
            id=uuid.uuid4(),
            organization_id=context.organization_id,
            allowed_registration_namespaces=sorted(policy.allowed_registration_namespaces),
            allowed_deployment_namespaces=sorted(policy.allowed_deployment_namespaces),
            approvers=sorted(policy.approvers),
            route_change_requires_approval=policy.route_change_requires_approval,
            created_at=now,
            updated_at=now,
        )
        session.add(record)
        action = "organization_gateway_policy.create"
    else:
        record.allowed_registration_namespaces = sorted(policy.allowed_registration_namespaces)
        record.allowed_deployment_namespaces = sorted(policy.allowed_deployment_namespaces)
        record.approvers = sorted(policy.approvers)
        record.route_change_requires_approval = policy.route_change_requires_approval
        record.updated_at = now
        action = "organization_gateway_policy.update"

    session.flush()
    write_audit_event(
        session,
        organization_id=context.organization_id,
        actor_type=actor.actor_type,
        actor_id=actor.actor_id,
        action=action,
        resource_type="organization_gateway_policy",
        resource_id=str(record.id),
        request_id=actor.request_id,
    )
    return record


def record_route_change(
    session: Session,
    context: TenantContext,
    *,
    project_id: uuid.UUID,
    actor: str,
    target_deployment_namespace: str,
    approved: bool,
    actor_info: ActorInfo,
    bypass_requested: bool = False,
) -> OrganizationGatewayRouteChangeRecord:
    """Appends one real route-change decision record (Section 38.5-38.6).
    Never decides authorization itself -- the caller must already have
    proven `approved`/`bypass_requested` against the organization's own
    policy (e.g. via `organization_policy_control.enforce_route_change_
    authority`) before calling this; this function's only job is to
    durably, append-only record what was actually decided. `project_id`
    must genuinely belong to the caller's own authenticated organization
    (checked first, before any row is written) -- this task's own
    negative_acceptance, "Cross-tenant read or write is denied"."""
    _authorize_project_or_raise(session, context, project_id)
    validate_namespace(target_deployment_namespace)
    validate_approver_identity(actor)

    record = OrganizationGatewayRouteChangeRecord(
        id=uuid.uuid4(),
        organization_id=context.organization_id,
        project_id=project_id,
        actor=actor,
        target_deployment_namespace=target_deployment_namespace,
        approved=approved,
        bypass_requested=bypass_requested,
        request_id=actor_info.request_id,
        created_at=datetime.now(UTC),
    )
    session.add(record)
    session.flush()
    write_audit_event(
        session,
        organization_id=context.organization_id,
        actor_type=actor_info.actor_type,
        actor_id=actor_info.actor_id,
        action="organization_gateway_route_change.record",
        resource_type="organization_gateway_route_change",
        resource_id=str(record.id),
        request_id=actor_info.request_id,
    )
    return record


def list_route_changes(
    session: Session, context: TenantContext, project_id: uuid.UUID
) -> list[OrganizationGatewayRouteChangeRecord]:
    """Returns every real route-change record for one project, oldest
    first -- the append-only history this task's own acceptance clause
    requires to actually be retrievable, not only written."""
    _authorize_project_or_raise(session, context, project_id)
    statement = (
        select(OrganizationGatewayRouteChangeRecord)
        .where(OrganizationGatewayRouteChangeRecord.project_id == project_id)
        .order_by(OrganizationGatewayRouteChangeRecord.created_at)
    )
    result = execute_tenant_scoped(session, context.organization_id, statement)
    return list(result.scalars().all())
