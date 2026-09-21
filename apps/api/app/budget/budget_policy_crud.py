"""Budget policy CRUD + reservation ledger (FTF-0403, Section 6.8/7A/22/32/
65-66/70.3/77/81).

Create/get/revise a project-scoped `BudgetPolicy`
(`apps/api/app/budget/budget_policy_schema.py`), plus the real
`reserve_budget_allocation`/`release_budget_allocation` reservation
primitive this task's own negative_acceptance clause depends on. Every
real query goes through the same certified composition FTF-0201/FTF-0301/
FTF-0401/FTF-0404 already proved for exactly this class of resource:

  - `TenantContext`/`tenant_scoped_session_for` (FTF-H003) open the unit
    of work and bind the transaction-local RLS tenant context;
  - `authorize_resource_ownership` (FTF-H003) proves a project ID (at
    create/get/revise/reserve/release) belongs to the caller's own
    authenticated organization *before* any row is read or mutated;
  - `execute_tenant_scoped` (FTF-0105) is what every actual SELECT/UPDATE
    in this module runs through, never a bare `session.execute(...)`.

Role gating (Section 45's own role table, no dedicated sentence for
budget authorization specifically): follows FTF-0401/FTF-0404's own
precedent exactly -- "workspace_admin: project administration" is the
nearest, and only, sentence naming a role for this class of per-project
configuration/authorization work, so `BUDGET_WRITE_ROLES` is
`{workspace_admin, organization_admin}`. `BUDGET_READ_ROLES` is every
role Section 45 names.

Every mutating operation writes an audit event via FTF-0104's
`write_audit_event` in the *same* transaction as the mutation it
describes -- this is this task's own "approval history" (see
`budget_policy_schema.py`'s own module docstring for why no second,
independent approval table exists).

`reserve_budget_allocation`/`release_budget_allocation` are this task's
own central job: a single, conditional SQL `UPDATE` against the policy
row's own aggregate `reserved_amount`, guarded in the same statement by
`reserved_amount + amount <= total_amount` (reserve) or
`reserved_amount - amount >= 0` (release) -- never a
read-then-compare-then-write sequence, which two concurrent requests
could both pass before either commits. Two concurrent reservations that
would together exceed the policy's own total authorization can never both
succeed: whichever `UPDATE` commits first shrinks the remaining headroom
the real database itself checks before allowing the second to proceed,
and the real `CHECK (reserved_amount <= total_amount)` constraint in
`0007_budget_policies.py` is the backstop behind even that.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.auth.audit_event_writer import AuditEvent, write_audit_event
from app.auth.resource_authorization import ResourceAuthorizationError, authorize_resource_ownership
from app.auth.tenant_isolation_query_guards import execute_tenant_scoped
from app.budget.budget_policy_schema import (
    BUDGET_SCOPES,
    BudgetPolicy,
    BudgetPolicyValidationError,
    validate_scopes,
    validate_total_amount,
)
from app.db.tenant_context import TenantContext
from app.projects.project_schema import Project

__all__ = [
    "ActorInfo",
    "ProjectNotFoundError",
    "BudgetPolicyNotFoundError",
    "BudgetPolicyStateError",
    "BudgetPolicyConcurrencyError",
    "BudgetPolicyScopeError",
    "BudgetPolicyInsufficientReservationError",
    "validate_allocation_amount",
    "apply_revise_budget_policy",
    "create_budget_policy",
    "get_budget_policy",
    "revise_budget_policy",
    "reserve_budget_allocation",
    "release_budget_allocation",
    "list_budget_policy_approval_history",
]

_TABLE = BudgetPolicy.__table__
_ID_COLUMN = _TABLE.c.id
_ORG_COLUMN = _TABLE.c.organization_id

_PROJECT_TABLE = Project.__table__
_PROJECT_ID_COLUMN = _PROJECT_TABLE.c.id
_PROJECT_ORG_COLUMN = _PROJECT_TABLE.c.organization_id


class ProjectNotFoundError(LookupError):
    """No project with this ID exists in the caller's own organization --
    raised identically whether the ID is missing entirely or belongs to a
    different organization (the same IDOR discipline FTF-0401/FTF-0404's
    own `ProjectNotFoundError` already established)."""


class BudgetPolicyNotFoundError(LookupError):
    """No budget policy exists for this project in the caller's own
    organization -- identical whether the project itself does not exist,
    belongs to a different organization, or simply has no budget policy
    attached yet."""


class BudgetPolicyStateError(ValueError):
    """The requested operation is not valid for this policy/project's
    current state (e.g. the project already has a budget policy)."""


class BudgetPolicyConcurrencyError(ValueError):
    """`expected_row_version` did not match the policy's own current
    `row_version` -- the same optimistic-concurrency refusal
    `project_crud.py`'s own `ProjectConcurrencyError` established."""


class BudgetPolicyScopeError(ValueError):
    """A reservation/release named a scope the policy itself was never
    authorized for (not a member of its own `scopes_json`)."""


class BudgetPolicyInsufficientReservationError(ValueError):
    """This task's own negative_acceptance, made real: the atomic
    conditional `UPDATE` found no row to update -- either the requested
    reservation would exceed the policy's own remaining headroom, or a
    release would take `reserved_amount` below zero."""


@dataclass(frozen=True)
class ActorInfo:
    """Who is performing this mutation, for the audit event it writes and
    for `created_by` attribution. `actor_id` is always the authenticated
    principal's own `subject`, never a client-supplied string."""

    actor_type: str
    actor_id: str
    request_id: str


def _now() -> datetime:
    return datetime.now(UTC)


def validate_allocation_amount(value: object) -> Decimal:
    """Same shape discipline as `validate_total_amount` -- a real,
    finite, positive `decimal.Decimal`. This task's own
    negative_acceptance ("Negative/unknown rates ... cannot be accepted")
    applies identically to a reservation/release amount, not only to the
    policy's own `total_amount`."""
    if not isinstance(value, Decimal) or isinstance(value, bool):
        raise BudgetPolicyValidationError(
            f"amount must be a decimal.Decimal, not {type(value).__name__}: {value!r}"
        )
    if not value.is_finite():
        raise BudgetPolicyValidationError(f"amount must be finite, not {value!r}")
    if value <= 0:
        raise BudgetPolicyValidationError(f"amount must be a positive value: {value!r}")
    return value


def _fetch_project_or_raise(
    session: Session, context: TenantContext, project_id: uuid.UUID
) -> Project:
    if not isinstance(project_id, uuid.UUID):
        raise ProjectNotFoundError(f"project_id must be a uuid.UUID, got {project_id!r}")
    try:
        authorize_resource_ownership(
            session, context, _PROJECT_TABLE, _PROJECT_ID_COLUMN, _PROJECT_ORG_COLUMN, project_id
        )
    except ResourceAuthorizationError as exc:
        raise ProjectNotFoundError(str(exc)) from exc
    statement = select(Project).where(Project.id == project_id)
    result = execute_tenant_scoped(session, context.organization_id, statement)
    row = result.scalars().one_or_none()
    if row is None:
        raise ProjectNotFoundError(f"project {project_id} vanished mid-transaction")
    return row


def _fetch_policy_or_raise(
    session: Session, context: TenantContext, project_id: uuid.UUID
) -> BudgetPolicy:
    project = _fetch_project_or_raise(session, context, project_id)
    if project.budget_policy_id is None:
        raise BudgetPolicyNotFoundError(f"project {project_id} has no budget policy")
    try:
        authorize_resource_ownership(
            session, context, _TABLE, _ID_COLUMN, _ORG_COLUMN, project.budget_policy_id
        )
    except ResourceAuthorizationError as exc:
        raise BudgetPolicyNotFoundError(str(exc)) from exc
    statement = select(BudgetPolicy).where(BudgetPolicy.id == project.budget_policy_id)
    result = execute_tenant_scoped(session, context.organization_id, statement)
    row = result.scalars().one_or_none()
    if row is None or row.project_id != project_id:
        raise BudgetPolicyNotFoundError(
            f"budget policy for project {project_id} vanished or does not belong to it"
        )
    return row


def create_budget_policy(
    session: Session,
    context: TenantContext,
    *,
    project_id: uuid.UUID,
    total_amount: Decimal,
    currency: str,
    scopes: list[str],
    actor: ActorInfo,
) -> BudgetPolicy:
    """Validates and inserts this project's own budget policy, then
    writes it through FTF-0401's own disclosed `Project.budget_policy_id`
    column (see `budget_policy_schema.py`'s own module docstring).
    Refuses (`BudgetPolicyStateError`) if the project already has one --
    `revise_budget_policy` is the real path for changing an existing
    policy's authorization, never a second `create`. Deliberately does
    NOT bump `project.row_version`: that field is FTF-0401's own
    optimistic-concurrency contract over `name`/`description`/
    `agent_objective`/`security_profile` (`project_crud.py`'s own
    `apply_edits`) -- attaching a budget policy is additive to a
    different field this task owns, not a conflicting edit to those."""
    project = _fetch_project_or_raise(session, context, project_id)
    if project.budget_policy_id is not None:
        raise BudgetPolicyStateError(f"project {project_id} already has a budget policy")
    policy = BudgetPolicy(
        organization_id=context.organization_id,
        project_id=project_id,
        total_amount=total_amount,
        currency=currency,
        scopes_json=scopes,
        reserved_amount=Decimal("0"),
        created_by=actor.actor_id,
    )
    session.add(policy)
    session.flush()
    project.budget_policy_id = policy.id
    project.updated_at = _now()
    write_audit_event(
        session,
        organization_id=context.organization_id,
        actor_type=actor.actor_type,
        actor_id=actor.actor_id,
        action="budget_policy.create",
        resource_type="budget_policy",
        resource_id=str(policy.id),
        request_id=actor.request_id,
        details={"total_amount": str(policy.total_amount), "currency": policy.currency},
    )
    return policy


def get_budget_policy(
    session: Session, context: TenantContext, project_id: uuid.UUID
) -> BudgetPolicy:
    return _fetch_policy_or_raise(session, context, project_id)


def apply_revise_budget_policy(
    policy: BudgetPolicy,
    *,
    total_amount: Decimal | None,
    scopes: list[str] | None,
    expected_row_version: int,
) -> bool:
    """The pure, DB-free core of `revise_budget_policy`. `None` for an
    optional field means "keep the current value," the same convention
    `project_crud.py`'s own `apply_edits` established. Refuses
    (`BudgetPolicyConcurrencyError`) on a stale `expected_row_version`.
    Refuses (`BudgetPolicyStateError`) a revision that would authorize
    *less* than what is already reserved -- this task's own
    negative_acceptance read the other way around: you cannot retroactively
    make an already-committed reservation exceed its own policy's
    authorization by shrinking the authorization out from under it."""
    if expected_row_version != policy.row_version:
        raise BudgetPolicyConcurrencyError(
            f"budget policy {policy.id} has row_version {policy.row_version}, "
            f"not the expected {expected_row_version} -- reload and retry"
        )

    changed = False
    if total_amount is not None:
        total_amount = validate_total_amount(total_amount)
        if total_amount < policy.reserved_amount:
            raise BudgetPolicyStateError(
                f"cannot revise total_amount to {total_amount!r}: "
                f"{policy.reserved_amount!r} is already reserved against this policy"
            )
        policy.total_amount = total_amount
        changed = True
    if scopes is not None:
        policy.scopes_json = validate_scopes(scopes)
        changed = True

    if not changed:
        raise BudgetPolicyValidationError("revise_budget_policy requires at least one field")
    policy.row_version += 1
    policy.updated_at = _now()
    return changed


def revise_budget_policy(
    session: Session,
    context: TenantContext,
    project_id: uuid.UUID,
    *,
    total_amount: Decimal | None,
    scopes: list[str] | None,
    expected_row_version: int,
    actor: ActorInfo,
) -> BudgetPolicy:
    policy = _fetch_policy_or_raise(session, context, project_id)
    apply_revise_budget_policy(
        policy,
        total_amount=total_amount,
        scopes=scopes,
        expected_row_version=expected_row_version,
    )
    session.flush()
    write_audit_event(
        session,
        organization_id=context.organization_id,
        actor_type=actor.actor_type,
        actor_id=actor.actor_id,
        action="budget_policy.revise",
        resource_type="budget_policy",
        resource_id=str(policy.id),
        request_id=actor.request_id,
        details={"total_amount": str(policy.total_amount), "row_version": policy.row_version},
    )
    return policy


def reserve_budget_allocation(
    session: Session,
    context: TenantContext,
    project_id: uuid.UUID,
    *,
    amount: Decimal,
    scope: str,
    actor: ActorInfo,
) -> BudgetPolicy:
    """Atomically reserves `amount` against this project's own budget
    policy for `scope`. See module docstring for why this is a single
    conditional `UPDATE`, never read-then-write."""
    amount = validate_allocation_amount(amount)
    if scope not in BUDGET_SCOPES:
        raise BudgetPolicyValidationError(f"scope must be one of {BUDGET_SCOPES}, got {scope!r}")
    policy = _fetch_policy_or_raise(session, context, project_id)
    if scope not in policy.scopes_json:
        raise BudgetPolicyScopeError(
            f"budget policy {policy.id} is not authorized for scope {scope!r} "
            f"(authorized scopes: {policy.scopes_json})"
        )
    statement = (
        update(BudgetPolicy)
        .where(BudgetPolicy.id == policy.id)
        .where(BudgetPolicy.reserved_amount + amount <= BudgetPolicy.total_amount)
        .values(reserved_amount=BudgetPolicy.reserved_amount + amount, updated_at=_now())
        .returning(BudgetPolicy.reserved_amount)
    )
    result = execute_tenant_scoped(session, context.organization_id, statement)
    row = result.first()
    if row is None:
        raise BudgetPolicyInsufficientReservationError(
            f"budget policy {policy.id} has insufficient remaining reservation headroom "
            f"for {amount!r}"
        )
    policy.reserved_amount = row[0]
    write_audit_event(
        session,
        organization_id=context.organization_id,
        actor_type=actor.actor_type,
        actor_id=actor.actor_id,
        action="budget_policy.reserve",
        resource_type="budget_policy",
        resource_id=str(policy.id),
        request_id=actor.request_id,
        details={"amount": str(amount), "scope": scope},
    )
    return policy


def release_budget_allocation(
    session: Session,
    context: TenantContext,
    project_id: uuid.UUID,
    *,
    amount: Decimal,
    scope: str,
    actor: ActorInfo,
) -> BudgetPolicy:
    """Atomically releases a previously reserved `amount`. Symmetric to
    `reserve_budget_allocation`: a single conditional `UPDATE` guarded by
    `reserved_amount - amount >= 0`, so a double-release (or a release
    larger than what remains reserved) is refused by the database itself,
    never left to silently drive `reserved_amount` negative."""
    amount = validate_allocation_amount(amount)
    if scope not in BUDGET_SCOPES:
        raise BudgetPolicyValidationError(f"scope must be one of {BUDGET_SCOPES}, got {scope!r}")
    policy = _fetch_policy_or_raise(session, context, project_id)
    statement = (
        update(BudgetPolicy)
        .where(BudgetPolicy.id == policy.id)
        .where(BudgetPolicy.reserved_amount - amount >= 0)
        .values(reserved_amount=BudgetPolicy.reserved_amount - amount, updated_at=_now())
        .returning(BudgetPolicy.reserved_amount)
    )
    result = execute_tenant_scoped(session, context.organization_id, statement)
    row = result.first()
    if row is None:
        raise BudgetPolicyInsufficientReservationError(
            f"budget policy {policy.id} does not have {amount!r} reserved to release"
        )
    policy.reserved_amount = row[0]
    write_audit_event(
        session,
        organization_id=context.organization_id,
        actor_type=actor.actor_type,
        actor_id=actor.actor_id,
        action="budget_policy.release",
        resource_type="budget_policy",
        resource_id=str(policy.id),
        request_id=actor.request_id,
        details={"amount": str(amount), "scope": scope},
    )
    return policy


def list_budget_policy_approval_history(
    session: Session, context: TenantContext, project_id: uuid.UUID
) -> list[AuditEvent]:
    """This task's own "approval history" (acceptance clause 1) -- every
    `budget_policy.create`/`revise`/`reserve`/`release` audit event
    FTF-0104's own append-only `audit_events` table already carries for
    this policy, oldest first. See `budget_policy_schema.py`'s own module
    docstring for why this task adds no second, independent history
    table."""
    policy = _fetch_policy_or_raise(session, context, project_id)
    statement = (
        select(AuditEvent)
        .where(
            AuditEvent.resource_type == "budget_policy",
            AuditEvent.resource_id == str(policy.id),
        )
        .order_by(AuditEvent.created_at)
    )
    result = execute_tenant_scoped(session, context.organization_id, statement)
    return list(result.scalars().all())
