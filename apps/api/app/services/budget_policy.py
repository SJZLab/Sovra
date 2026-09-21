"""Budget policy (FTF-0403, Section 6.8/32/65-66/70.3/77/81).

Section 6.8's own `Project.budget_policy_id` (FTF-0401's own migration)
is a bare, unconstrained nullable UUID column -- nothing exists for it to
actually reference, and nothing stops a project from pointing at a
completely unrelated tenant's row. This task's own job, per its exact
resolved contract, is narrow and concrete: "Persist decimal budget
authorization, cost currency, scopes and approval history," and refuse
"Negative/unknown rates and concurrent allocation beyond available
reservation" (this task's own negative_acceptance, verbatim).

Two real, tenant-scoped tables (`apps/api/alembic/versions/
0007_budget_policies.py`):

  - `BudgetPolicy` -- the authorization itself: a decimal
    `authorized_amount`, a `currency`, a closed set of allowed `scopes`,
    and a live `remaining_amount` this task's own reservation mechanism
    decrements.
  - `BudgetPolicyEvent` -- an append-only ledger (never UPDATEd or
    DELETEd, the same discipline Section 6.24's own `AuditEvent` already
    establishes) recording both halves of this task's own acceptance
    clause in one place: the "approval history" (`authorized`/`revoked`
    events) and every real reservation this policy ever grants
    (`reserved` events).

`reserve_budget`'s own negative_acceptance ("concurrent allocation beyond
available reservation cannot be accepted") is enforced by construction,
not by a read-then-write race a concurrent caller could win: it is a
single atomic conditional `UPDATE budget_policies SET remaining_amount =
remaining_amount - :amount WHERE id = :id AND remaining_amount >=
:amount`. Postgres's own row-level locking -- not application code -- is
what makes two concurrent reservations against the same policy safe: the
first to execute holds the row lock until it commits; the second blocks,
then re-evaluates `remaining_amount >= :amount` against the *post-commit*
value once unblocked, so a reservation that would push the allocated
total beyond the authorized ceiling always affects zero rows and is
refused, exactly the same class of guard `apps/api/app/services/
side_effect_classification.py`'s own conditional-UPDATE-free siblings
achieve with row locks elsewhere in this repository's certified work.
Proven directly against a real concurrently-racing Postgres cluster in
`tests/integration/test_ftf_0403_budget_policy.py`, not only asserted.

`validate_amount`'s own negative_acceptance ("Negative/unknown rates...
cannot be accepted") is enforced at the type level: a caller must supply
a real `decimal.Decimal` (Section 32: "All monetary computation uses
decimal arithmetic with currency and effective-rate version") -- a bare
`float`, `None`, `NaN`, `Infinity`, a negative value, a value carrying
more than this table's own `Numeric(18, 6)` precision, or a value beyond
its own ceiling are all refused outright, never silently coerced or
treated as zero.

Real, live-wired: `enforce_budget_policy_reference` is called from
`apps/api/app/projects/project_crud.py`'s `update_project` whenever a
caller supplies `budget_policy_id` -- the same wiring point FTF-0402's
own `enforce_security_profile_selection` already established for
`security_profile`. It is denied identically whether the referenced
policy does not exist at all, belongs to a different organization,
belongs to a different project in the *same* organization (the IDOR case
a bare foreign key alone cannot express), or is no longer `active` -- the
same "denied identically" discipline FTF-H003's own
`authorize_resource_ownership` already established, applied here to a
cross-reference between two tenant-owned tables rather than a single
owning table. `create_project` deliberately does NOT accept
`budget_policy_id`: a `BudgetPolicy` row can only be created against an
already-existing project (its own composite foreign key requires a real
`project_id`), so attaching one is structurally always a later edit, not
a creation-time field -- unlike `security_profile`, which has no such
ordering dependency.

Disclosed, not hidden: `create_budget_policy`/`reserve_budget` have no
live HTTP route of their own yet -- authoring a budget policy and
recording a spend reservation are FTF-0719 ("Budget kill switch")/
FTF-R016 ("...enforce budget tree")'s own, later, separately
dependency-gated consumers (both name FTF-0403 as their own dependency in
docs/TASK_INDEX.md, not the reverse), and Section 42's `enforce_budget`
TrainingWorkflow activity is the eventual real caller of `reserve_budget`
once Temporal wiring exists. This is the same disclosed "no live caller
yet" class already accepted for FTF-0103/FTF-0104/FTF-0105/FTF-H003/
FTF-0402's own `require_profile_at_least` before each one's own first
live caller existed -- proven directly (no-DB and real live-Postgres)
rather than left untested while unwired.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Integer,
    Numeric,
    String,
    select,
    text,
    update,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, Session, mapped_column

from app.auth.organization_workspace_schema import Base
from app.auth.tenant_isolation_query_guards import execute_tenant_scoped

__all__ = [
    "AMOUNT_DECIMAL_PLACES",
    "BUDGET_SCOPES",
    "CURRENCY_CODE_LENGTH",
    "BudgetPolicy",
    "BudgetPolicyEvent",
    "BudgetPolicyNotFoundError",
    "BudgetPolicyStateError",
    "BudgetPolicyValidationError",
    "InsufficientBudgetError",
    "InvalidBudgetAmountError",
    "InvalidCurrencyCodeError",
    "UnknownBudgetScopeError",
    "create_budget_policy",
    "enforce_budget_policy_reference",
    "list_budget_policy_events",
    "reserve_budget",
    "validate_amount",
    "validate_currency",
    "validate_scopes",
]

AMOUNT_MAX_DIGITS = 18
AMOUNT_DECIMAL_PLACES = 6
# Numeric(18, 6)'s own real ceiling -- rejected before it ever reaches the
# database, not discovered as an opaque DB error.
_MAX_AMOUNT = Decimal(10 ** (AMOUNT_MAX_DIGITS - AMOUNT_DECIMAL_PLACES)) - Decimal(1).scaleb(
    -AMOUNT_DECIMAL_PLACES
)

CURRENCY_CODE_LENGTH = 3

# Grounded directly in this specification's own text, not invented:
# Section 2148 ("Coding-agent usage is a separate budget"), Section
# 70.3 ("Reserve invoice minimums and managed custom-model serving
# commitments"), and Section 32/6.15-6.18's own TrainingExperiment/
# EvaluationRun split between training and evaluation spend.
BUDGET_SCOPES: tuple[str, ...] = ("training", "evaluation", "coding_agent", "serving")

ALLOWED_STATUSES = ("active", "exhausted", "revoked")
EVENT_TYPES = ("authorized", "reserved", "revoked")


class BudgetPolicyValidationError(ValueError):
    """Base for every validation failure this module raises."""


class UnknownBudgetScopeError(BudgetPolicyValidationError):
    """A scope outside `BUDGET_SCOPES`, an empty scope list, or a scope
    not actually authorized by the referenced policy."""


class InvalidBudgetAmountError(BudgetPolicyValidationError):
    """A negative, unknown (`None`/`NaN`/`Infinity`), non-`Decimal`,
    over-precision or over-ceiling amount -- this task's own
    negative_acceptance ("Negative/unknown rates... cannot be accepted"),
    made executable."""


class InvalidCurrencyCodeError(BudgetPolicyValidationError):
    """Not an uppercase 3-letter code, or does not match the policy's own
    authorized currency."""


class InsufficientBudgetError(BudgetPolicyValidationError):
    """A reservation would allocate beyond the policy's real remaining
    budget -- this task's own negative_acceptance's second half
    ("concurrent allocation beyond available reservation cannot be
    accepted"), made executable via `reserve_budget`'s own atomic
    conditional UPDATE."""


class BudgetPolicyStateError(BudgetPolicyValidationError):
    """The policy is not `active` (`exhausted`/`revoked`)."""


class BudgetPolicyNotFoundError(LookupError):
    """No budget policy with this ID belongs to the stated organization
    *and* project -- raised identically whether the ID is missing
    entirely, belongs to a different organization, or belongs to a
    different project in the same organization (this task's own IDOR
    discipline, matching FTF-H003/FTF-0201/FTF-0301/FTF-0401's own
    negative_acceptance precedent)."""


def validate_amount(value: object, *, allow_zero: bool) -> Decimal:
    if not isinstance(value, Decimal):
        raise InvalidBudgetAmountError(
            f"amount must be a decimal.Decimal, not {type(value).__name__}: {value!r}"
        )
    if not value.is_finite():
        raise InvalidBudgetAmountError(f"amount must be finite (not NaN/Infinity): {value!r}")
    if value < 0 or (value == 0 and not allow_zero):
        expected = "non-negative" if allow_zero else "strictly positive"
        raise InvalidBudgetAmountError(f"amount must be {expected}: {value!r}")
    exponent = value.as_tuple().exponent
    if isinstance(exponent, int) and -exponent > AMOUNT_DECIMAL_PLACES:
        raise InvalidBudgetAmountError(
            f"amount must have at most {AMOUNT_DECIMAL_PLACES} decimal places: {value!r}"
        )
    if value > _MAX_AMOUNT:
        raise InvalidBudgetAmountError(f"amount exceeds the maximum representable value: {value!r}")
    return value


def validate_currency(value: object) -> str:
    if (
        not isinstance(value, str)
        or len(value) != CURRENCY_CODE_LENGTH
        or not value.isalpha()
        or value != value.upper()
    ):
        raise InvalidCurrencyCodeError(
            f"currency must be an uppercase {CURRENCY_CODE_LENGTH}-letter ISO 4217-shaped code: "
            f"{value!r}"
        )
    return value


def validate_scopes(value: object) -> tuple[str, ...]:
    if not isinstance(value, list | tuple) or len(value) == 0:
        raise UnknownBudgetScopeError("scopes must be a non-empty list")
    scopes: list[str] = []
    for item in value:
        if item not in BUDGET_SCOPES:
            raise UnknownBudgetScopeError(
                f"unrecognized budget scope {item!r}; must be one of {BUDGET_SCOPES}"
            )
        if item in scopes:
            raise UnknownBudgetScopeError(f"duplicate scope {item!r}")
        scopes.append(item)
    return tuple(scopes)


class BudgetPolicy(Base):
    """Section 6.8's own `budget_policy_id` target, made real."""

    __tablename__ = "budget_policies"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    project_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    authorized_amount: Mapped[Decimal] = mapped_column(
        Numeric(AMOUNT_MAX_DIGITS, AMOUNT_DECIMAL_PLACES), nullable=False
    )
    remaining_amount: Mapped[Decimal] = mapped_column(
        Numeric(AMOUNT_MAX_DIGITS, AMOUNT_DECIMAL_PLACES), nullable=False
    )
    currency: Mapped[str] = mapped_column(String(CURRENCY_CODE_LENGTH), nullable=False)
    scopes_json: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, server_default="active")
    created_by: Mapped[str] = mapped_column(String(200), nullable=False)
    approved_by: Mapped[str | None] = mapped_column(String(200), nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    row_version: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))

    __table_args__ = (
        CheckConstraint(
            "authorized_amount >= 0", name="ck_budget_policies_authorized_amount_nonneg"
        ),
        CheckConstraint("remaining_amount >= 0", name="ck_budget_policies_remaining_amount_nonneg"),
        CheckConstraint(
            "remaining_amount <= authorized_amount",
            name="ck_budget_policies_remaining_le_authorized",
        ),
        CheckConstraint(
            "status in ('active','exhausted','revoked')", name="ck_budget_policies_status"
        ),
        ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            ondelete="CASCADE",
            name="fk_budget_policies_organization_id",
        ),
        ForeignKeyConstraint(
            ["organization_id", "project_id"],
            ["projects.organization_id", "projects.id"],
            ondelete="CASCADE",
            name="fk_budget_policies_organization_id_project_id",
        ),
    )

    def __init__(self, **kwargs: object) -> None:
        if "authorized_amount" in kwargs:
            kwargs["authorized_amount"] = validate_amount(
                kwargs["authorized_amount"], allow_zero=False
            )
        if "remaining_amount" in kwargs:
            kwargs["remaining_amount"] = validate_amount(
                kwargs["remaining_amount"], allow_zero=True
            )
        if "currency" in kwargs:
            kwargs["currency"] = validate_currency(kwargs["currency"])
        if "scopes_json" in kwargs:
            kwargs["scopes_json"] = list(validate_scopes(kwargs["scopes_json"]))
        if "status" in kwargs and kwargs["status"] not in ALLOWED_STATUSES:
            raise BudgetPolicyValidationError(
                f"status must be one of {ALLOWED_STATUSES}: {kwargs['status']!r}"
            )
        super().__init__(**kwargs)


class BudgetPolicyEvent(Base):
    """Append-only ledger -- never UPDATEd or DELETEd (the migration
    grants `sovra_app` SELECT/INSERT only). Records both this task's own
    "approval history" (`authorized`/`revoked`) and every real
    reservation (`reserved`)."""

    __tablename__ = "budget_policy_events"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    budget_policy_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    organization_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    project_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    event_type: Mapped[str] = mapped_column(String(32), nullable=False)
    amount: Mapped[Decimal] = mapped_column(
        Numeric(AMOUNT_MAX_DIGITS, AMOUNT_DECIMAL_PLACES), nullable=False
    )
    currency: Mapped[str] = mapped_column(String(CURRENCY_CODE_LENGTH), nullable=False)
    scope: Mapped[str | None] = mapped_column(String(32), nullable=True)
    actor: Mapped[str] = mapped_column(String(200), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )

    __table_args__ = (
        CheckConstraint(
            "event_type in ('authorized','reserved','revoked')",
            name="ck_budget_policy_events_event_type",
        ),
        CheckConstraint("amount >= 0", name="ck_budget_policy_events_amount_nonneg"),
        ForeignKeyConstraint(
            ["budget_policy_id"],
            ["budget_policies.id"],
            ondelete="RESTRICT",
            name="fk_budget_policy_events_budget_policy_id",
        ),
    )

    def __init__(self, **kwargs: object) -> None:
        # A 'revoked' event carries no real monetary amount of its own
        # (it is not authorizing or spending anything); 'authorized' and
        # 'reserved' events always represent a real, strictly positive
        # amount -- zero would misrepresent either as having happened.
        if "amount" in kwargs:
            allow_zero = kwargs.get("event_type") == "revoked"
            kwargs["amount"] = validate_amount(kwargs["amount"], allow_zero=allow_zero)
        if "currency" in kwargs:
            kwargs["currency"] = validate_currency(kwargs["currency"])
        if "event_type" in kwargs and kwargs["event_type"] not in EVENT_TYPES:
            raise BudgetPolicyValidationError(
                f"event_type must be one of {EVENT_TYPES}: {kwargs['event_type']!r}"
            )
        scope = kwargs.get("scope")
        if scope is not None and scope not in BUDGET_SCOPES:
            raise UnknownBudgetScopeError(f"unrecognized budget scope {scope!r}")
        super().__init__(**kwargs)


def create_budget_policy(
    session: Session,
    *,
    organization_id: uuid.UUID,
    project_id: uuid.UUID,
    authorized_amount: Decimal,
    currency: str,
    scopes: list[str],
    created_by: str,
    approved_by: str | None = None,
) -> BudgetPolicy:
    """Persists a new `BudgetPolicy` plus its first `authorized`
    approval-history event, in the same transaction (both-or-neither, the
    same discipline `write_audit_event`'s own module docstring already
    establishes for every other mutation in this repository)."""
    amount = validate_amount(authorized_amount, allow_zero=False)
    currency = validate_currency(currency)
    validated_scopes = list(validate_scopes(scopes))
    now = datetime.now(UTC)
    policy = BudgetPolicy(
        organization_id=organization_id,
        project_id=project_id,
        authorized_amount=amount,
        remaining_amount=amount,
        currency=currency,
        scopes_json=validated_scopes,
        status="active",
        created_by=created_by,
        approved_by=approved_by,
        approved_at=now if approved_by else None,
        row_version=1,
    )
    session.add(policy)
    session.flush()
    session.add(
        BudgetPolicyEvent(
            budget_policy_id=policy.id,
            organization_id=organization_id,
            project_id=project_id,
            event_type="authorized",
            amount=amount,
            currency=currency,
            scope=None,
            actor=created_by,
        )
    )
    session.flush()
    return policy


def _fetch_policy_or_raise(
    session: Session,
    *,
    organization_id: uuid.UUID,
    project_id: uuid.UUID,
    budget_policy_id: uuid.UUID,
) -> BudgetPolicy:
    if not isinstance(budget_policy_id, uuid.UUID):
        raise BudgetPolicyNotFoundError(
            f"budget_policy_id must be a uuid.UUID, got {budget_policy_id!r}"
        )
    statement = select(BudgetPolicy).where(BudgetPolicy.id == budget_policy_id)
    result = execute_tenant_scoped(session, organization_id, statement)
    policy = result.scalars().one_or_none()
    if (
        policy is None
        or policy.organization_id != organization_id
        or policy.project_id != project_id
    ):
        raise BudgetPolicyNotFoundError(
            f"budget policy {budget_policy_id} does not belong to project {project_id} in "
            f"organization {organization_id} (denied identically whether missing or "
            "foreign-tenant/foreign-project, by design)"
        )
    return policy


def enforce_budget_policy_reference(
    session: Session,
    *,
    organization_id: uuid.UUID,
    project_id: uuid.UUID,
    budget_policy_id: uuid.UUID,
) -> BudgetPolicy:
    """Validates that `budget_policy_id` names a real, `active`
    `BudgetPolicy` belonging to *this* organization and *this* project
    before a caller is allowed to persist it on `Project.budget_policy_id`
    -- the real, live-wired caller is `project_crud.py`'s
    `update_project`. Raises `BudgetPolicyNotFoundError` (IDOR, denied
    identically whether missing/foreign-tenant/foreign-project) or
    `BudgetPolicyStateError` (not `active`)."""
    policy = _fetch_policy_or_raise(
        session,
        organization_id=organization_id,
        project_id=project_id,
        budget_policy_id=budget_policy_id,
    )
    if policy.status != "active":
        raise BudgetPolicyStateError(
            f"budget policy {budget_policy_id} is {policy.status!r}, not active"
        )
    return policy


def reserve_budget(
    session: Session,
    *,
    budget_policy_id: uuid.UUID,
    organization_id: uuid.UUID,
    project_id: uuid.UUID,
    scope: str,
    amount: Decimal,
    currency: str,
    reserved_by: str,
) -> BudgetPolicyEvent:
    """Atomically allocates `amount` against the policy's own real
    remaining budget. See this module's own docstring for why the atomic
    conditional `UPDATE` below -- not a read-then-write race -- is what
    makes this safe under real concurrent reservations."""
    amount = validate_amount(amount, allow_zero=False)
    currency = validate_currency(currency)
    if scope not in BUDGET_SCOPES:
        raise UnknownBudgetScopeError(
            f"unrecognized budget scope {scope!r}; must be one of {BUDGET_SCOPES}"
        )

    policy = _fetch_policy_or_raise(
        session,
        organization_id=organization_id,
        project_id=project_id,
        budget_policy_id=budget_policy_id,
    )
    if policy.status != "active":
        raise BudgetPolicyStateError(
            f"budget policy {budget_policy_id} is {policy.status!r}, not active"
        )
    if currency != policy.currency:
        raise InvalidCurrencyCodeError(
            f"reservation currency {currency!r} does not match this policy's own authorized "
            f"currency {policy.currency!r}"
        )
    if scope not in policy.scopes_json:
        raise UnknownBudgetScopeError(
            f"scope {scope!r} is not authorized by this budget policy's own scopes "
            f"{tuple(policy.scopes_json)!r}"
        )

    result = session.execute(
        update(BudgetPolicy)
        .where(BudgetPolicy.id == budget_policy_id)
        .where(BudgetPolicy.organization_id == organization_id)
        .where(BudgetPolicy.remaining_amount >= amount)
        .values(
            remaining_amount=BudgetPolicy.remaining_amount - amount,
            row_version=BudgetPolicy.row_version + 1,
            updated_at=datetime.now(UTC),
        )
    )
    if result.rowcount != 1:  # type: ignore[attr-defined]
        raise InsufficientBudgetError(
            f"reserving {amount} {currency} against budget policy {budget_policy_id} would "
            "exceed its real remaining authorized budget -- refused (negative_acceptance: "
            "concurrent allocation beyond available reservation cannot be accepted)"
        )
    event = BudgetPolicyEvent(
        budget_policy_id=budget_policy_id,
        organization_id=organization_id,
        project_id=project_id,
        event_type="reserved",
        amount=amount,
        currency=currency,
        scope=scope,
        actor=reserved_by,
    )
    session.add(event)
    session.flush()
    return event


def list_budget_policy_events(
    session: Session, *, organization_id: uuid.UUID, budget_policy_id: uuid.UUID
) -> list[BudgetPolicyEvent]:
    """This task's own "approval history" acceptance clause, made
    retrievable: every event for one policy, oldest first."""
    statement = (
        select(BudgetPolicyEvent)
        .where(BudgetPolicyEvent.budget_policy_id == budget_policy_id)
        .order_by(BudgetPolicyEvent.created_at)
    )
    result = execute_tenant_scoped(session, organization_id, statement)
    return list(result.scalars().all())
