"""Cost-per-success calculator (FTF-1304, Section 28/31-32/64/69-70/77/81).

Scope (disclosed explicitly, not hidden -- see this task's own
docs/certifications/queue.md submission entry): `database_changes`
charters "No independent schema change. Consume frozen contract" and
`contract_owner` is FTF-1301 -- this module adds no table, no route and no
new rate vocabulary. It composes its three already-CERTIFIED dependencies'
own real numbers (FTF-1301's declared `CostProfile` rates, FTF-1302's
`window_total_cost`/`ServiceWindow`, FTF-1303's `provider_call_cost`) into
Section 32's own top-level formula, exactly the same "declared, not
auto-derived" pattern both sibling modules already established -- this
module takes an already-priced, per-attempt total cost (the caller sums
whichever of local-compute/provider/fallback/judge/tool charges applied to
that attempt using FTF-1302/FTF-1303 first), it does not re-derive a price
from raw usage itself.

This task's own acceptance clause ("Total costs across all attempts
divided by independently successful tasks, plus optional training
amortization") is Section 32's own named formula, made real and callable:

    cost_per_success = all_window_costs_for_this_policy / unique_successful_tasks
    amortized_cost_per_success = cost_per_success
                              + training_and_qualification_cost
                                / approved_expected_lifetime_successes

"independently successful tasks" is Section 69.2's own terminal-outcome
classification, reusing `local_compute_costing.TASK_OUTCOMES` and its own
`request_id`-deduplication rule rather than a second copy of that
vocabulary ("Multiple turns from one conversation, repeated seeds ... are
not independent tasks" -- a request repeated under the same `request_id`
counts once, never once per attempt).

This task's own negative_acceptance ("Zero successes, pending charges and
unobserved fallback use cannot yield misleading cheap passing metric") is
enforced by construction, in three independent ways mirroring the two
sibling modules' own enforcement style:

1. **Zero successes** -- `cost_per_success` raises `CostPerSuccessError`
   exactly like FTF-1302's own `cost_per_success`, rather than returning
   `$0` or an uncaught `ZeroDivisionError`. Section 32: "undefined/
   infinite," never a passing metric.
2. **Pending charges** -- `AttemptCost.pending_cost` (Section 70.1's own
   `RECONCILIATION_REQUIRED` state: a real, reserved/estimated figure for
   a charge not yet settled, never simply absent) is summed into the same
   total as `settled_cost` by `AttemptCost.total_cost()` -- there is
   structurally no code path here capable of computing a total that drops
   a declared pending figure to reach a cheaper number. `has_pending_
   reconciliation` on the result surfaces that the figure is provisional
   (Section 32: "report estimated vs settled and variance, never promise
   an exact unbreachable remote bill") without ever excluding it from the
   number itself.
3. **Unobserved fallback use** -- `AttemptCost.fallback_used=True` with no
   matching `fallback_cost` raises at construction, the same "real,
   nonzero, declared usage with no matching price cannot be replaced by
   an invented or zero dollar value" refusal FTF-1303's own
   `provider_call_cost` already applies to cache/reasoning/request
   charges, extended here to Section 32's own named "fallback/judge/tool
   charges" category.

No live HTTP route is added by this task (`api_contract`/
`shared_lock_required` in the task packet both point at FTF-1301's own,
already-frozen contract slot 13); `proving_symbol` is deliberately left
empty in `docs/spec-matrix.yaml`, matching the FTF-1302/FTF-1303/FTF-1401/
FTF-1601/FTF-1701/FTF-1810 precedent for a computation module with no live
product-runtime caller in this repository yet -- a genuine, disclosed
0/0, not inflation.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from app.services.local_compute_costing import TASK_OUTCOMES

__all__ = [
    "CostPerSuccessError",
    "AttemptCost",
    "CostPerSuccessResult",
    "unique_successful_request_ids",
    "cost_per_success",
]


class CostPerSuccessError(ValueError):
    """A field value violates this domain's own shape/invariant, or a cost
    figure was requested that Section 32 declares undefined, or real
    declared fallback/judge/tool usage has no matching price -- never a
    silently invented or zero-filled figure."""


def _validate_non_negative_decimal(value: object, field_name: str) -> Decimal:
    if not isinstance(value, Decimal) or isinstance(value, bool):
        raise CostPerSuccessError(
            f"{field_name} must be a decimal.Decimal, not {type(value).__name__}: {value!r}"
        )
    if not value.is_finite():
        raise CostPerSuccessError(f"{field_name} must be finite, not {value!r}")
    if value < 0:
        raise CostPerSuccessError(f"{field_name} must not be negative: {value!r}")
    return value


def _validate_optional_non_negative_decimal(value: object, field_name: str) -> Decimal | None:
    if value is None:
        return None
    return _validate_non_negative_decimal(value, field_name)


@dataclass(frozen=True)
class AttemptCost:
    """One declared attempt's own real, already-priced cost contribution --
    Section 32's own "all costs for this policy" accounting unit, one entry
    per real attempt (an attempt may be `pass`, any other Section 69.2
    outcome, or a retry of a prior `request_id`).

    `settled_cost`: the fully reconciled portion of this attempt's own
    cost (already summed by the caller from FTF-1302/FTF-1303 as
    applicable -- never re-derived here).
    `pending_cost`: the real, reserved/estimated portion still awaiting
    settlement (Section 70.1 `RECONCILIATION_REQUIRED`). `None` only when
    this attempt genuinely has no outstanding charge -- see module
    docstring for why this can never be silently dropped from the total.
    `fallback_used`/`fallback_cost`: whether this attempt's own fallback/
    judge/tool routing occurred and, when it did, its real declared cost
    -- required together, see module docstring.
    """

    request_id: str
    outcome: str
    settled_cost: Decimal
    pending_cost: Decimal | None = None
    fallback_used: bool = False
    fallback_cost: Decimal | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.request_id, str) or not self.request_id.strip():
            raise CostPerSuccessError("request_id must be a non-empty string")
        if self.outcome not in TASK_OUTCOMES:
            raise CostPerSuccessError(f"outcome must be one of {TASK_OUTCOMES}: {self.outcome!r}")
        object.__setattr__(
            self, "settled_cost", _validate_non_negative_decimal(self.settled_cost, "settled_cost")
        )
        object.__setattr__(
            self,
            "pending_cost",
            _validate_optional_non_negative_decimal(self.pending_cost, "pending_cost"),
        )
        if not isinstance(self.fallback_used, bool):
            raise CostPerSuccessError(
                f"fallback_used must be a bool, not {type(self.fallback_used).__name__}: "
                f"{self.fallback_used!r}"
            )
        if self.fallback_used and self.fallback_cost is None:
            raise CostPerSuccessError(
                f"request_id {self.request_id!r} declares fallback_used=True but no "
                "fallback_cost was supplied -- negative_acceptance: unobserved fallback use "
                "cannot be replaced by an invented or zero dollar value"
            )
        if not self.fallback_used and self.fallback_cost is not None:
            raise CostPerSuccessError(
                f"request_id {self.request_id!r} declares fallback_cost without "
                "fallback_used=True -- a real charge cannot be attached to fallback/judge/tool "
                "usage that was not declared to have occurred"
            )
        object.__setattr__(
            self,
            "fallback_cost",
            _validate_optional_non_negative_decimal(self.fallback_cost, "fallback_cost"),
        )

    def total_cost(self) -> Decimal:
        """This attempt's own full real cost -- settled plus pending plus
        fallback/judge/tool, all three summed unconditionally. See module
        docstring for why no category here can ever be dropped to reach a
        cheaper figure."""
        total = self.settled_cost
        if self.pending_cost is not None:
            total += self.pending_cost
        if self.fallback_cost is not None:
            total += self.fallback_cost
        return total


@dataclass(frozen=True)
class CostPerSuccessResult:
    """Section 32's own `cost_per_success` (and, when requested,
    `amortized_cost_per_success`) plus the provisional-figure disclosure
    Section 32/69.3 require ("report estimated vs settled and variance").
    """

    cost_per_success: Decimal
    settled_cost_per_success: Decimal
    has_pending_reconciliation: bool
    amortized_cost_per_success: Decimal | None


def unique_successful_request_ids(attempts: list[AttemptCost]) -> frozenset[str]:
    """Section 69.2's own `unique_successful_tasks` denominator: distinct
    `request_id`s with a declared `pass` outcome, reusing
    `local_compute_costing`'s own rule that a request repeated under the
    same `request_id` (a retry of the same task) counts once, never once
    per attempt."""
    return frozenset(attempt.request_id for attempt in attempts if attempt.outcome == "pass")


def cost_per_success(
    attempts: list[AttemptCost],
    *,
    training_and_qualification_cost: Decimal | None = None,
    approved_expected_lifetime_successes: int | None = None,
) -> CostPerSuccessResult:
    """Section 32's own `cost_per_success` = total costs across all
    declared attempts / unique successful tasks, with an optional
    `amortized_cost_per_success` when both `training_and_qualification_cost`
    and `approved_expected_lifetime_successes` are supplied together.

    Raises `CostPerSuccessError` when there are zero successes (Section 32:
    undefined/infinite, never a passing metric) or when only one of the two
    amortization arguments is supplied (an incomplete amortization request
    is refused, not silently treated as "no amortization").
    """
    successes = unique_successful_request_ids(attempts)
    if not successes:
        raise CostPerSuccessError(
            "cost_per_success is undefined (Section 32: zero successes -> undefined/infinite, "
            "not a misleadingly cheap passing metric) -- no declared attempt has outcome 'pass'"
        )

    n = Decimal(len(successes))
    total_settled = sum((attempt.settled_cost for attempt in attempts), start=Decimal("0"))
    total_all = sum((attempt.total_cost() for attempt in attempts), start=Decimal("0"))
    has_pending = any(attempt.pending_cost is not None for attempt in attempts)

    base = total_all / n
    settled_only = total_settled / n

    amortized: Decimal | None = None
    if (
        training_and_qualification_cost is not None
        or approved_expected_lifetime_successes is not None
    ):
        if training_and_qualification_cost is None or approved_expected_lifetime_successes is None:
            raise CostPerSuccessError(
                "training_and_qualification_cost and approved_expected_lifetime_successes "
                "must be supplied together -- an incomplete amortization request is refused, "
                "never silently dropped"
            )
        _validate_non_negative_decimal(
            training_and_qualification_cost, "training_and_qualification_cost"
        )
        if (
            not isinstance(approved_expected_lifetime_successes, int)
            or isinstance(approved_expected_lifetime_successes, bool)
            or approved_expected_lifetime_successes < 1
        ):
            raise CostPerSuccessError(
                "approved_expected_lifetime_successes must be a positive int, not "
                f"{approved_expected_lifetime_successes!r}"
            )
        amortized = base + (
            training_and_qualification_cost / Decimal(approved_expected_lifetime_successes)
        )

    return CostPerSuccessResult(
        cost_per_success=base,
        settled_cost_per_success=settled_only,
        has_pending_reconciliation=has_pending,
        amortized_cost_per_success=amortized,
    )
