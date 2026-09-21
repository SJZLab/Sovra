"""CostProfile domain schema (FTF-1301, Section 28/31/32/45/64/69-70/77/81).

Refined from the task packet's own suggested files list (`apps/web/
components/cost-and-model-search/costprofile_schema_ui.tsx`, contracts
only -- no `apps/api` path is listed) to add a real backing
`apps/api/app/costs/` package, the same class of refinement FTF-0403's own
`budget_policy_schema.py` already disclosed for its own packet: this
task's own acceptance clause 1 ("Record dated provider/local hardware TCO
rates/currency/source") and `database_changes` charter ("Own forward
Alembic migration for this resource") genuinely need a real table, so an
`apps/web`-only file set cannot satisfy it. `costs/` (not `budget/`) is a
distinct resource: a `BudgetPolicy` (FTF-0403) is a project's own spending
*authorization*; a `CostProfile` is an organization-wide, dated *rate
card* entry -- how much a provider or a piece of local hardware actually
costs per unit -- consumed later by FTF-1302 ("Local compute costing"),
FTF-1303 ("Provider token costing") and FTF-1304 ("Cost-per-success
calculator"), all separately dependency-gated on this task per
`docs/TASK_INDEX.md`.

Scope (disclosed explicitly, not hidden -- see this task's own
docs/certifications/queue.md submission entry): this module owns exactly
the *rate-card* record -- one dated, sourced, currency-tagged TCO rate for
either a `provider` (e.g. a per-token LiteLLM model price) or a
`local_hardware` unit (e.g. a per-GPU-hour on-prem rate). It does NOT
compute an actual cost-per-success figure against real usage -- that full
Section-32 pipeline (`provider_call_cost`/`window_self_hosted_cost`/
`cost_per_success`) is FTF-1304's own, later, separately dependency-gated
acceptance criterion, which itself depends on this task plus FTF-1302/
FTF-1303. Declaring a real cost-per-success computation here, with no real
usage/billing data anywhere in this repository yet to compute it from,
would be exactly the "simulated progress, fake metrics" Law 11 forbids.

Each `CostProfile` row is an **immutable rate observation**, never edited
in place -- "Record dated ... rates" (plural, the acceptance clause's own
word) reads as a real price *history*, the same reasoning
`tool_definition_schema.py` (FTF-0404) already applied to
`ToolDefinition` ("editing creates a new version, never mutates the old
one"), not `BudgetPolicy`'s own single-mutable-row pattern (a budget
authorization is one live number; a rate observation is a dated fact that
must never silently change after the fact). The one field this task does
allow to change after creation is `verified_at` (`verify_cost_profile` in
`cost_profile_crud.py`) -- a human re-confirming a still-real, unchanged
rate is still current, never a rewrite of the rate itself.

This task's own negative_acceptance ("Token price zero is not local
compute free; unverified/expired rate cannot certify cheapest") is
Section 32's own general rule ("Do not assume self-hosted inference is
free"; "A $0 local token price does not imply $0 TCO") made real and
testable at the record level -- see `is_certifiable_cheapest` below, the
pure function `cost_profile_crud.py`'s own search endpoint calls to
"display unknowns honestly" rather than silently picking an unverified or
structurally-suspect rate as the answer.

Real forward Alembic migration
(`apps/api/alembic/versions/0008_cost_profiles.py`) FORCEs row-level
security on `cost_profiles` scoped to the same
`app.current_organization_id` tenant context FTF-0102/FTF-0104/FTF-0201/
FTF-0301/FTF-0401/FTF-0404/FTF-0403's migrations already established.
`currency` reuses FTF-0403's own already-CERTIFIED, real ISO-4217
allowlist (`app.budget.budget_policy_schema.ALLOWED_CURRENCIES`/
`validate_currency`) directly rather than a second, duplicate vocabulary
-- the same "reuse an existing compatible module instead of creating a
duplicate" instruction this repository's own front matter states.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Integer, Numeric, String, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.auth.organization_workspace_schema import Base
from app.budget.budget_policy_schema import validate_currency

__all__ = [
    "PROFILE_TYPES",
    "RATE_UNITS",
    "SOURCE_MAX_LENGTH",
    "LABEL_MAX_LENGTH",
    "RATE_AMOUNT_MAX_DIGITS",
    "RATE_AMOUNT_DECIMAL_PLACES",
    "CostProfile",
    "CostProfileValidationError",
    "validate_profile_type",
    "validate_label",
    "validate_rate_unit",
    "validate_rate_amount",
    "validate_source",
    "is_certifiable_cheapest",
]

# Section 32's own real, named cost dimensions ("provider_call_cost" is
# priced per input/output token; "window_self_hosted_cost" is priced per
# GPU/CPU/RAM-hour plus storage/network) -- the closed vocabulary this
# task's own two named categories ("provider/local hardware TCO rates")
# resolve to, not an invented taxonomy.
PROFILE_TYPES: tuple[str, ...] = ("provider", "local_hardware")

# A closed rate-unit vocabulary grounded directly in Section 32's own named
# cost formula terms (`input_tokens`, `output_tokens`,
# `total_billed_GPU_CPU_RAM_including_idle_and_warmup`) plus the two
# coarser units Section 32 also names directly ("per hour", "per request").
RATE_UNITS: tuple[str, ...] = (
    "per_input_token",
    "per_output_token",
    "per_gpu_hour",
    "per_cpu_hour",
    "per_ram_gb_hour",
    "per_hour",
    "per_request",
)

LABEL_MAX_LENGTH = 200
SOURCE_MAX_LENGTH = 2048
RATE_AMOUNT_MAX_DIGITS = 24
RATE_AMOUNT_DECIMAL_PLACES = 10
_MAX_INTEGER_DIGITS = RATE_AMOUNT_MAX_DIGITS - RATE_AMOUNT_DECIMAL_PLACES


class CostProfileValidationError(ValueError):
    """A field value violates this domain schema's own shape/invariant."""


def validate_profile_type(value: object) -> str:
    if not isinstance(value, str) or value not in PROFILE_TYPES:
        raise CostProfileValidationError(f"profile_type must be one of {PROFILE_TYPES}: {value!r}")
    return value


def validate_label(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise CostProfileValidationError("label must be a non-empty string")
    if len(value) > LABEL_MAX_LENGTH:
        raise CostProfileValidationError(f"label must be at most {LABEL_MAX_LENGTH} characters")
    return value


def validate_rate_unit(value: object) -> str:
    if not isinstance(value, str) or value not in RATE_UNITS:
        raise CostProfileValidationError(f"rate_unit must be one of {RATE_UNITS}: {value!r}")
    return value


def validate_rate_amount(value: object) -> Decimal:
    """Section 6's own "money uses Decimal + currency" rule, enforced at
    construction -- the same shape discipline `budget_policy_schema.py`'s
    own `validate_total_amount` applies, except zero is a real, allowed
    value here (unlike a budget authorization): a rate observation of
    exactly zero is a legitimate fact to record (e.g. a provider's
    documented free tier, or a local hardware unit whose *marginal*
    per-token cost is genuinely near-zero) -- `is_certifiable_cheapest`
    below, not construction-time refusal, is where this task's own
    negative_acceptance about zero rates is actually enforced."""
    if not isinstance(value, Decimal) or isinstance(value, bool):
        raise CostProfileValidationError(
            f"rate_amount must be a decimal.Decimal, not {type(value).__name__}: {value!r}"
        )
    if not value.is_finite():
        raise CostProfileValidationError(f"rate_amount must be finite, not {value!r}")
    if value < 0:
        raise CostProfileValidationError(f"rate_amount must not be negative: {value!r}")
    sign, digits, exponent = value.as_tuple()
    del sign
    if isinstance(exponent, int) and exponent < -RATE_AMOUNT_DECIMAL_PLACES:
        raise CostProfileValidationError(
            f"rate_amount must have at most {RATE_AMOUNT_DECIMAL_PLACES} decimal "
            f"places: {value!r}"
        )
    integer_digits = len(digits) + (exponent if isinstance(exponent, int) and exponent > 0 else 0)
    if integer_digits > _MAX_INTEGER_DIGITS:
        raise CostProfileValidationError(
            f"rate_amount is too large for a NUMERIC({RATE_AMOUNT_MAX_DIGITS},"
            f"{RATE_AMOUNT_DECIMAL_PLACES}) column: {value!r}"
        )
    return value


def validate_source(value: object) -> str:
    """ "...currency/source" (acceptance clause 1) -- where this dated rate
    came from (a provider pricing-page URL, or a description of the local
    TCO worksheet it was computed from). Required and non-empty: an
    un-sourced rate is exactly the kind of unverifiable number Section
    32/69.3's own "no guessed performance or cost values" rule forbids."""
    if not isinstance(value, str) or not value.strip():
        raise CostProfileValidationError("source must be a non-empty string")
    if len(value) > SOURCE_MAX_LENGTH:
        raise CostProfileValidationError(f"source must be at most {SOURCE_MAX_LENGTH} characters")
    return value


class CostProfile(Base):
    """An organization-scoped, dated TCO rate observation. See module
    docstring for why this row is immutable once created (only
    `verified_at` may later change, via `cost_profile_crud.py`'s own
    `verify_cost_profile`)."""

    __tablename__ = "cost_profiles"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
    )
    profile_type: Mapped[str] = mapped_column(String(32), nullable=False)
    label: Mapped[str] = mapped_column(String(LABEL_MAX_LENGTH), nullable=False)
    rate_unit: Mapped[str] = mapped_column(String(32), nullable=False)
    rate_amount: Mapped[Decimal] = mapped_column(
        Numeric(RATE_AMOUNT_MAX_DIGITS, RATE_AMOUNT_DECIMAL_PLACES), nullable=False
    )
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    source: Mapped[str] = mapped_column(String(SOURCE_MAX_LENGTH), nullable=False)
    rate_recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    row_version: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))
    created_by: Mapped[str] = mapped_column(String(200), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )

    __table_args__ = (
        CheckConstraint(f"profile_type in {PROFILE_TYPES!r}", name="ck_cost_profiles_profile_type"),
        CheckConstraint(f"rate_unit in {RATE_UNITS!r}", name="ck_cost_profiles_rate_unit"),
        CheckConstraint("rate_amount >= 0", name="ck_cost_profiles_rate_amount_non_negative"),
        CheckConstraint("row_version >= 1", name="ck_cost_profiles_row_version_positive"),
    )

    def __init__(self, **kwargs: object) -> None:
        if "profile_type" in kwargs:
            kwargs["profile_type"] = validate_profile_type(kwargs["profile_type"])
        if "label" in kwargs:
            kwargs["label"] = validate_label(kwargs["label"])
        if "rate_unit" in kwargs:
            kwargs["rate_unit"] = validate_rate_unit(kwargs["rate_unit"])
        if "rate_amount" in kwargs:
            kwargs["rate_amount"] = validate_rate_amount(kwargs["rate_amount"])
        if "currency" in kwargs:
            kwargs["currency"] = validate_currency(kwargs["currency"])
        if "source" in kwargs:
            kwargs["source"] = validate_source(kwargs["source"])
        super().__init__(**kwargs)


def is_certifiable_cheapest(profile: CostProfile, now: datetime) -> tuple[bool, str | None]:
    """This task's own negative_acceptance, made real: "Token price zero is
    not local compute free; unverified/expired rate cannot certify
    cheapest." Returns `(True, None)` only when none of the three
    disqualifying conditions below hold; otherwise `(False, reason)`, a
    stable machine-readable reason string `cost_profile_routes.py`'s own
    search endpoint surfaces verbatim -- "display unknowns honestly" read
    as: never silently drop a disqualified rate from a search result, name
    exactly why it cannot be certified cheapest instead.

    1. `expired` -- `expires_at` is set and has passed. A stale rate
       cannot certify cheapest even if it once was.
    2. `unverified` -- nobody has independently confirmed this rate is
       still accurate (`verified_at is None`). An unconfirmed entered
       number is not the same as a confirmed one.
    3. `zero_provider_rate_unconfirmed` -- a `provider` rate of exactly
       zero is never certified cheapest, verified or not: Section 32's own
       "do not assume self-hosted inference is free" principle, read the
       other direction -- a provider's *own* price of exactly zero must
       never be treated as equivalent to (or cheaper than) genuine local
       compute's own real, non-zero TCO. This is a structural rule, not a
       staleness one: no amount of `verified_at` re-confirmation lifts it,
       matching this task's own literal wording ("Token price zero is
       *not* local compute free" -- an absolute, not a "until verified"
       qualifier).
    """
    if profile.expires_at is not None and profile.expires_at <= now:
        return False, "expired"
    if profile.verified_at is None:
        return False, "unverified"
    if profile.profile_type == "provider" and profile.rate_amount == Decimal("0"):
        return False, "zero_provider_rate_unconfirmed"
    return True, None
