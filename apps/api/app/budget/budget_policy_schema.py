"""Budget policy domain schema (FTF-0403, Section 6.8/7A/22/32/65-66/70.3/77/81).

Refined from the task packet's own suggested single-file path
(`apps/api/app/services/budget_policy.py`) to a dedicated
`apps/api/app/budget/` package split across schema/crud/routes -- the same
refinement precedent FTF-0305 already established for its own suggested
path (see that task's own module docstring). `services/` is this
repository's own convention for a pure, DB-free enforcement module with no
table of its own (`project_security_profile.py`, `side_effect_classification.py`,
`critical_behavior_requirements.py`); this task's own acceptance clause 1
("Persist decimal budget authorization, cost currency, scopes and
approval history") genuinely needs a real table, so the closer structural
analog is FTF-0404's own `apps/api/app/tools/` package -- a new
project-scoped resource with its own schema/CRUD/routes files -- not a
`services/` module. This refinement is recorded here, not silently taken.

A project-scoped `BudgetPolicy`. Section 22's own Phase 4 DoD line
("project records project type, initial security/budget policy, and
user-entered objective") is what this task closes: FTF-0401's own
`Project.budget_policy_id` column has existed since that task's own
migration, deliberately left unwritten -- FTF-0401's own
`project_routes.py` module docstring says so explicitly: "`budget_policy_id`
stays a schema column this task's own routes never accept, for a later
task to write through." This is that later task.

`total_amount`/`currency` are Section 6's own general rule made real for
this specific entity ("money uses Decimal + currency"): `total_amount` is
a genuine `decimal.Decimal`, never a float, and `currency` is checked
against a closed, real ISO-4217 allowlist -- this task's own
negative_acceptance ("Negative/unknown rates ... cannot be accepted") read
as: a negative/zero/non-finite amount, or a currency code outside that
allowlist, is refused before any row is ever persisted.

`scopes_json` is a non-empty, deduplicated subset of `BUDGET_SCOPES` --
grounded directly in Section 41's own named Temporal workflow list
(`TrainingWorkflow`, `EvaluationWorkflow`, `DeploymentWorkflow`/
`CanaryPromotionWorkflow`), the only three cost-bearing workflow
categories this specification names by title anywhere -- not an invented
enumeration. No upstream section fixes a richer vocabulary than that.

`reserved_amount` is this task's own real, DB-backed reservation ledger
primitive -- the part of this task's own negative_acceptance clause
("... and concurrent allocation beyond available reservation cannot be
accepted") that a pure, DB-free check alone cannot prove. Scope
disclosed, not hidden: this is the *policy* layer Section 70.3 itself
still leaves for a later task -- "Budget reservation is transactional
before external submission" is FTF-H018's own later, separately
dependency-gated "Atomic cost reservations and bounded stop-lag controls"
(see docs/TASK_INDEX.md, which records FTF-H018 as depending on this
task, not the reverse) -- individual, per-job reservation records with
release-by-ID, stop-lag headroom and settlement reconciliation are that
later task's job. This task proves the one primitive every later caller
will need to build on: `reserve_budget_allocation`/
`release_budget_allocation` (`budget_policy_crud.py`) apply a single
atomic, conditional SQL `UPDATE` against this row's own aggregate
`reserved_amount` -- never a read-then-write race -- so "concurrent
allocation beyond available reservation" is refused by the database
itself, not by an application-level check a second concurrent request
could still race past.

"approval history" (acceptance clause 1) is carried by FTF-0104's own
already-CERTIFIED, append-only `audit_events` table -- every mutation
this task performs (create/revise/reserve/release) writes one audit event
in the same transaction, the same discipline FTF-0201/FTF-0301/FTF-0401/
FTF-0404 already established -- rather than a second, independent
"approval" table this task's own `database_changes` charter ("Own forward
Alembic migration for this resource") does not call for. See
`budget_policy_crud.py`'s own `list_budget_policy_approval_history`.

Real forward Alembic migration
(`apps/api/alembic/versions/0007_budget_policies.py`) FORCEs row-level
security on `budget_policies` scoped to the same
`app.current_organization_id` tenant context FTF-0102/FTF-0104/FTF-0201/
FTF-0301/FTF-0401/FTF-0404's migrations already established, and
additionally adds the real, DB-enforced composite foreign key
`projects(organization_id, budget_policy_id)` ->
`budget_policies(organization_id, id)` -- FTF-0401's own disclosed "for a
later task to write through" column, made real.
"""

from __future__ import annotations

import re
import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.auth.organization_workspace_schema import Base

__all__ = [
    "ALLOWED_CURRENCIES",
    "BUDGET_SCOPES",
    "TOTAL_AMOUNT_MAX_DIGITS",
    "TOTAL_AMOUNT_DECIMAL_PLACES",
    "BudgetPolicy",
    "BudgetPolicyValidationError",
    "validate_total_amount",
    "validate_currency",
    "validate_scopes",
]

# Real ISO-4217 alphabetic codes -- a genuine closed vocabulary (this
# task's own negative_acceptance "unknown rates ... cannot be accepted"
# read as "an unrecognized currency code is refused, not silently
# accepted"), not an exhaustive ISO-4217 mirror -- no upstream section
# fixes a richer list, and every code below is a real, live currency a
# Factory deployment could plausibly bill in. Matches
# `organization_workspace_schema.py`'s own `default_currency="USD"`
# default.
ALLOWED_CURRENCIES: tuple[str, ...] = (
    "USD",
    "EUR",
    "GBP",
    "SAR",
    "AED",
    "JPY",
    "CHF",
    "CAD",
    "AUD",
    "SGD",
)

# Section 41's own named Temporal workflow list -- the only three
# cost-bearing workflow categories this specification names by title
# anywhere (`TrainingWorkflow`, `EvaluationWorkflow`,
# `DeploymentWorkflow`/`CanaryPromotionWorkflow`). See module docstring.
BUDGET_SCOPES: tuple[str, ...] = ("training", "evaluation", "serving")

TOTAL_AMOUNT_MAX_DIGITS = 18
TOTAL_AMOUNT_DECIMAL_PLACES = 4
_MAX_INTEGER_DIGITS = TOTAL_AMOUNT_MAX_DIGITS - TOTAL_AMOUNT_DECIMAL_PLACES

_CURRENCY_SHAPE = re.compile(r"^[A-Z]{3}$")


class BudgetPolicyValidationError(ValueError):
    """A field value violates this domain schema's own shape/invariant."""


def validate_total_amount(value: object) -> Decimal:
    """Section 6's own "money uses Decimal + currency" rule, enforced at
    construction. Refuses anything that is not already a real
    `decimal.Decimal` (never a `float` -- a float-typed amount is a
    silent precision bug waiting to happen, not a valid input shape),
    non-finite (`NaN`/`Infinity`), non-positive, carrying more than
    `TOTAL_AMOUNT_DECIMAL_PLACES` decimal places, or too large for the
    real `NUMERIC(18,4)` column this maps to."""
    if not isinstance(value, Decimal) or isinstance(value, bool):
        raise BudgetPolicyValidationError(
            f"total_amount must be a decimal.Decimal, not {type(value).__name__}: {value!r}"
        )
    if not value.is_finite():
        raise BudgetPolicyValidationError(f"total_amount must be finite, not {value!r}")
    if value <= 0:
        raise BudgetPolicyValidationError(f"total_amount must be a positive amount: {value!r}")
    sign, digits, exponent = value.as_tuple()
    del sign
    if isinstance(exponent, int) and exponent < -TOTAL_AMOUNT_DECIMAL_PLACES:
        raise BudgetPolicyValidationError(
            f"total_amount must have at most {TOTAL_AMOUNT_DECIMAL_PLACES} decimal "
            f"places: {value!r}"
        )
    integer_digits = len(digits) + (exponent if isinstance(exponent, int) and exponent > 0 else 0)
    if integer_digits > _MAX_INTEGER_DIGITS:
        raise BudgetPolicyValidationError(
            f"total_amount is too large for a NUMERIC({TOTAL_AMOUNT_MAX_DIGITS},"
            f"{TOTAL_AMOUNT_DECIMAL_PLACES}) column: {value!r}"
        )
    return value


def validate_currency(value: object) -> str:
    if not isinstance(value, str) or not _CURRENCY_SHAPE.match(value):
        raise BudgetPolicyValidationError(
            f"currency must be a 3-letter uppercase ISO-4217-shaped code: {value!r}"
        )
    if value not in ALLOWED_CURRENCIES:
        raise BudgetPolicyValidationError(
            f"currency {value!r} is not one of the recognized currencies {ALLOWED_CURRENCIES} "
            "-- refusing an unknown rate rather than silently accepting it"
        )
    return value


def validate_scopes(value: object) -> list[str]:
    if not isinstance(value, list) or not value:
        raise BudgetPolicyValidationError("scopes must be a non-empty list of strings")
    deduplicated: list[str] = []
    for item in value:
        if not isinstance(item, str) or item not in BUDGET_SCOPES:
            raise BudgetPolicyValidationError(
                f"every scope must be one of {BUDGET_SCOPES}, got {item!r}"
            )
        if item not in deduplicated:
            deduplicated.append(item)
    return sorted(deduplicated)


def validate_reserved_amount(total_amount: Decimal, value: object) -> Decimal:
    """Same shape check as `validate_total_amount` (a `Decimal`, finite,
    never negative -- zero is allowed, an unreserved policy is the normal
    starting state), plus the real bound this task's own
    negative_acceptance names directly: reserved amount can never exceed
    the policy's own total authorization. The real database-level
    `CHECK (reserved_amount <= total_amount)` in
    `0007_budget_policies.py` is this same rule's own backstop -- this
    check exists so a bad in-memory construction fails immediately,
    before ever reaching the database."""
    if not isinstance(value, Decimal) or isinstance(value, bool):
        raise BudgetPolicyValidationError(
            f"reserved_amount must be a decimal.Decimal, not {type(value).__name__}: {value!r}"
        )
    if not value.is_finite():
        raise BudgetPolicyValidationError(f"reserved_amount must be finite, not {value!r}")
    if value < 0:
        raise BudgetPolicyValidationError(f"reserved_amount must not be negative: {value!r}")
    if value > total_amount:
        raise BudgetPolicyValidationError(
            f"reserved_amount {value!r} exceeds total_amount {total_amount!r}"
        )
    return value


class BudgetPolicy(Base):
    """Section 22's own Phase 4 DoD -- a project's own budget
    authorization. `organization_id` is required by Section 65.1's own
    "All organization-scoped entities carry organization_id NOT NULL"
    rule, the same refinement FTF-0201/FTF-0301/FTF-0401/FTF-0404 already
    applied to their own new tables."""

    __tablename__ = "budget_policies"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    project_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    total_amount: Mapped[Decimal] = mapped_column(
        Numeric(TOTAL_AMOUNT_MAX_DIGITS, TOTAL_AMOUNT_DECIMAL_PLACES), nullable=False
    )
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    scopes_json: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    reserved_amount: Mapped[Decimal] = mapped_column(
        Numeric(TOTAL_AMOUNT_MAX_DIGITS, TOTAL_AMOUNT_DECIMAL_PLACES),
        nullable=False,
        server_default=text("0"),
    )
    row_version: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))
    created_by: Mapped[str] = mapped_column(String(200), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )

    __table_args__ = (
        UniqueConstraint("project_id", name="uq_budget_policies_project_id"),
        ForeignKeyConstraint(
            ["organization_id", "project_id"],
            ["projects.organization_id", "projects.id"],
            ondelete="CASCADE",
            name="fk_budget_policies_organization_id_project_id",
        ),
        CheckConstraint("total_amount > 0", name="ck_budget_policies_total_amount_positive"),
        CheckConstraint(
            "reserved_amount >= 0", name="ck_budget_policies_reserved_amount_non_negative"
        ),
        CheckConstraint(
            "reserved_amount <= total_amount", name="ck_budget_policies_reserved_amount_bounded"
        ),
    )

    def __init__(self, **kwargs: object) -> None:
        if "total_amount" in kwargs:
            kwargs["total_amount"] = validate_total_amount(kwargs["total_amount"])
        if "currency" in kwargs:
            kwargs["currency"] = validate_currency(kwargs["currency"])
        if "scopes_json" in kwargs:
            kwargs["scopes_json"] = validate_scopes(kwargs["scopes_json"])
        if "reserved_amount" in kwargs:
            total = kwargs.get("total_amount")
            if not isinstance(total, Decimal):
                raise BudgetPolicyValidationError(
                    "reserved_amount cannot be validated without a real total_amount"
                )
            kwargs["reserved_amount"] = validate_reserved_amount(total, kwargs["reserved_amount"])
        super().__init__(**kwargs)
