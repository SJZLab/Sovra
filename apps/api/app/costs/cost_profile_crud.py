"""CostProfile CRUD + cheapest-rate search (FTF-1301, Section 28/31/32/45/
64/69-70/77/81).

Create/get/list/verify an organization-scoped, immutable `CostProfile`
rate observation (`apps/api/app/costs/cost_profile_schema.py`), plus
`search_cost_profiles` -- this task's own
`/costs/cost-per-success/search` acceptance-clause surface, scoped to a
caller-named project but reading the *project's own organization's* rate
card (a `CostProfile` is organization-wide, not project-scoped -- see
schema module docstring). Every real query goes through the same
certified composition FTF-0201/FTF-0301/FTF-0401/FTF-0403/FTF-0404 already
proved for exactly this class of resource:

  - `TenantContext`/`tenant_scoped_session_for` (FTF-H003) open the unit
    of work and bind the transaction-local RLS tenant context;
  - `authorize_resource_ownership` (FTF-H003) proves a resource ID (a
    `CostProfile` ID, or a `project_id` for the search endpoint) belongs
    to the caller's own authenticated organization *before* any row is
    read or mutated;
  - `execute_tenant_scoped` (FTF-0105) is what every actual SELECT in this
    module runs through, never a bare `session.execute(...)`.

Role gating: Section 45's own literal role table -- "organization_admin:
connections, security profiles, cost profiles" -- names cost profiles
explicitly, the same "admin-only, including reads" precedent FTF-0201's
own `connection_crud.py` already applied to the analogous "connections"
resource named in the same sentence. `COST_PROFILE_ROLES` in
`cost_profile_routes.py` is `{organization_admin}` alone, not a wider
read/write split.

Every mutating operation (`create_cost_profile`/`verify_cost_profile`)
writes an audit event via FTF-0104's `write_audit_event` in the *same*
transaction as the mutation it describes -- the same discipline every
other resource in this repository already established, and this task's
own real "approval history" equivalent (no second, independent history
table -- matching `budget_policy_crud.py`'s own precedent and reasoning).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.audit_event_writer import write_audit_event
from app.auth.resource_authorization import ResourceAuthorizationError, authorize_resource_ownership
from app.auth.tenant_isolation_query_guards import execute_tenant_scoped
from app.costs.cost_profile_schema import CostProfile, is_certifiable_cheapest
from app.db.tenant_context import TenantContext
from app.projects.project_schema import Project

__all__ = [
    "ActorInfo",
    "CostProfileNotFoundError",
    "ProjectNotFoundError",
    "SearchResult",
    "create_cost_profile",
    "get_cost_profile",
    "list_cost_profiles",
    "verify_cost_profile",
    "search_cost_profiles",
]

_TABLE = CostProfile.__table__
_ID_COLUMN = _TABLE.c.id
_ORG_COLUMN = _TABLE.c.organization_id

_PROJECT_TABLE = Project.__table__
_PROJECT_ID_COLUMN = _PROJECT_TABLE.c.id
_PROJECT_ORG_COLUMN = _PROJECT_TABLE.c.organization_id


class CostProfileNotFoundError(LookupError):
    """No cost profile with this ID exists in the caller's own
    organization -- raised identically whether the ID is missing entirely
    or belongs to a different organization (the same IDOR discipline every
    other resource in this repository already established)."""


class ProjectNotFoundError(LookupError):
    """No project with this ID exists in the caller's own organization --
    used by `search_cost_profiles` to resolve which organization's rate
    card to read."""


@dataclass(frozen=True)
class ActorInfo:
    """Who is performing this mutation, for the audit event it writes and
    for `created_by` attribution. `actor_id` is always the authenticated
    principal's own `subject`, never a client-supplied string."""

    actor_type: str
    actor_id: str
    request_id: str


@dataclass(frozen=True)
class SearchResult:
    """One cost profile as returned by `search_cost_profiles`, annotated
    with this task's own negative_acceptance verdict -- "display unknowns
    honestly" read as: every result carries its own certifiability, never
    silently filtered out."""

    profile: CostProfile
    certifiable_cheapest: bool
    disqualified_reason: str | None


def _now() -> datetime:
    return datetime.now(UTC)


def _fetch_or_raise(
    session: Session, context: TenantContext, cost_profile_id: uuid.UUID
) -> CostProfile:
    if not isinstance(cost_profile_id, uuid.UUID):
        raise CostProfileNotFoundError(
            f"cost_profile_id must be a uuid.UUID, got {cost_profile_id!r}"
        )
    try:
        authorize_resource_ownership(
            session, context, _TABLE, _ID_COLUMN, _ORG_COLUMN, cost_profile_id
        )
    except ResourceAuthorizationError as exc:
        raise CostProfileNotFoundError(str(exc)) from exc
    statement = select(CostProfile).where(CostProfile.id == cost_profile_id)
    result = execute_tenant_scoped(session, context.organization_id, statement)
    row = result.scalars().one_or_none()
    if row is None:
        raise CostProfileNotFoundError(f"cost profile {cost_profile_id} vanished mid-transaction")
    return row


def create_cost_profile(
    session: Session,
    context: TenantContext,
    *,
    profile_type: str,
    label: str,
    rate_unit: str,
    rate_amount: Decimal,
    currency: str,
    source: str,
    rate_recorded_at: datetime,
    expires_at: datetime | None,
    actor: ActorInfo,
) -> CostProfile:
    """Inserts one new, immutable rate observation. Never edits an
    existing row -- see schema module docstring for why a new dated
    observation is always a new row, not an update."""
    profile = CostProfile(
        organization_id=context.organization_id,
        profile_type=profile_type,
        label=label,
        rate_unit=rate_unit,
        rate_amount=rate_amount,
        currency=currency,
        source=source,
        rate_recorded_at=rate_recorded_at,
        verified_at=None,
        expires_at=expires_at,
        created_by=actor.actor_id,
    )
    session.add(profile)
    session.flush()
    write_audit_event(
        session,
        organization_id=context.organization_id,
        actor_type=actor.actor_type,
        actor_id=actor.actor_id,
        action="cost_profile.create",
        resource_type="cost_profile",
        resource_id=str(profile.id),
        request_id=actor.request_id,
        details={
            "profile_type": profile.profile_type,
            "rate_unit": profile.rate_unit,
            "rate_amount": str(profile.rate_amount),
            "currency": profile.currency,
        },
    )
    return profile


def get_cost_profile(
    session: Session, context: TenantContext, cost_profile_id: uuid.UUID
) -> CostProfile:
    return _fetch_or_raise(session, context, cost_profile_id)


def list_cost_profiles(
    session: Session,
    context: TenantContext,
    *,
    profile_type: str | None = None,
    rate_unit: str | None = None,
) -> list[CostProfile]:
    statement = select(CostProfile).where(CostProfile.organization_id == context.organization_id)
    if profile_type is not None:
        statement = statement.where(CostProfile.profile_type == profile_type)
    if rate_unit is not None:
        statement = statement.where(CostProfile.rate_unit == rate_unit)
    statement = statement.order_by(CostProfile.rate_recorded_at.desc())
    result = execute_tenant_scoped(session, context.organization_id, statement)
    return list(result.scalars().all())


def verify_cost_profile(
    session: Session,
    context: TenantContext,
    cost_profile_id: uuid.UUID,
    *,
    actor: ActorInfo,
) -> CostProfile:
    """A human independently re-confirms this still-unchanged rate is
    still accurate -- the only field this task ever changes on an existing
    row (see schema module docstring). Always advances `verified_at` to
    now and bumps `row_version`, even if already verified -- each
    verification is itself a new, real confirmation event worth its own
    audit trail entry, not a no-op."""
    profile = _fetch_or_raise(session, context, cost_profile_id)
    profile.verified_at = _now()
    profile.updated_at = profile.verified_at
    profile.row_version += 1
    write_audit_event(
        session,
        organization_id=context.organization_id,
        actor_type=actor.actor_type,
        actor_id=actor.actor_id,
        action="cost_profile.verify",
        resource_type="cost_profile",
        resource_id=str(profile.id),
        request_id=actor.request_id,
        details={"verified_at": profile.verified_at.isoformat()},
    )
    return profile


def _fetch_project_organization_or_raise(
    session: Session, context: TenantContext, project_id: uuid.UUID
) -> uuid.UUID:
    if not isinstance(project_id, uuid.UUID):
        raise ProjectNotFoundError(f"project_id must be a uuid.UUID, got {project_id!r}")
    try:
        authorize_resource_ownership(
            session, context, _PROJECT_TABLE, _PROJECT_ID_COLUMN, _PROJECT_ORG_COLUMN, project_id
        )
    except ResourceAuthorizationError as exc:
        raise ProjectNotFoundError(str(exc)) from exc
    statement = select(Project.organization_id).where(Project.id == project_id)
    result = execute_tenant_scoped(session, context.organization_id, statement)
    organization_id = result.scalars().one_or_none()
    if organization_id is None:
        raise ProjectNotFoundError(f"project {project_id} vanished mid-transaction")
    return organization_id


def search_cost_profiles(
    session: Session,
    context: TenantContext,
    project_id: uuid.UUID,
    *,
    profile_type: str | None = None,
    rate_unit: str | None = None,
) -> list[SearchResult]:
    """This task's own `/costs/cost-per-success/search` acceptance-clause
    surface. Resolves `project_id` to its own organization (IDOR-checked
    exactly like every other project-scoped lookup in this repository),
    then returns every matching `CostProfile` in that organization's rate
    card, each annotated with `is_certifiable_cheapest`'s own verdict --
    "display unknowns honestly" read as: a disqualified rate is still
    returned, with its own disqualification reason, never silently
    dropped from the result set."""
    _fetch_project_organization_or_raise(session, context, project_id)
    profiles = list_cost_profiles(session, context, profile_type=profile_type, rate_unit=rate_unit)
    now = _now()
    results: list[SearchResult] = []
    for profile in profiles:
        certifiable, reason = is_certifiable_cheapest(profile, now)
        results.append(
            SearchResult(
                profile=profile, certifiable_cheapest=certifiable, disqualified_reason=reason
            )
        )
    return results
