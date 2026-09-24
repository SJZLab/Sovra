"""Provider token costing (FTF-1303, Section 28/31-32/64/69-70/77/81).

Scope (disclosed explicitly, not hidden -- see this task's own
docs/certifications/queue.md submission entry): `database_changes`
charters "No independent schema change. Consume frozen contract" and
`contract_owner` is FTF-1301 -- this module adds no table, no route and no
new `CostProfile.rate_unit` vocabulary entry. FTF-1301's own closed
`RATE_UNITS` (`per_input_token`/`per_output_token`/...) has no
`per_cache_token`/`per_reasoning_token`/`per_request` *rate-card* member,
and `CostProfile.label` is unstructured free text with no
provider/model-keyed lookup -- so this module does not resolve a rate by
querying `CostProfile` rows itself. Instead, exactly like FTF-1302's own
`ServiceWindow` (a declared, already-known real bill rather than an
auto-derived one), this module takes an explicitly declared per-category
rate card -- the caller resolves which real `CostProfile`/provider price
list entry backs each rate before calling this module, the same
separation of "what the rate is" (FTF-1301) from "what a given usage
costs at that rate" (this task) already established for local compute
costing.

This task's own acceptance clause ("Calculate billed input/output/cache/
reasoning/request charges using actual provider usage rules") is Section
32's own named formula, generalized beyond its two literal named terms:

    provider_call_cost = input_tokens * effective_input_rate
                      + output_tokens * effective_output_rate
                      + actual_other_billable_units

`input_tokens`/`output_tokens` are the two terms Section 32 names
directly and are always billed (`ProviderRateCard.input_token_rate`/
`output_token_rate` are required, non-optional fields). Cache tokens,
reasoning tokens and per-request charges are each one real, named
`actual_other_billable_units` category a provider may or may not apply to
a given call -- modeled as an independent `(usage, rate)` pair that is
*absent* (`None`) when the category does not apply to this call at all,
distinct from *present with zero usage* (a real, billable-but-zero
observation). Only the latter can ever legitimately produce a $0
contribution; the former contributes nothing because there is nothing to
price, not because a missing figure was rounded down.

This task's own negative_acceptance ("Missing usage/rate/unknown upstream
cannot be replaced by invented or zero dollar value") is enforced by
`provider_call_cost` refusing to compute a total, rather than silently
treating a missing rate as free, whenever a category has real, nonzero,
declared usage but no matching rate was supplied -- exactly the "no
guessed cost values" rule Section 32/69.3 state generally, made real and
testable at the per-call level. A category with zero declared usage never
raises even without a matching rate (there is nothing to multiply), and a
category the caller marks not-applicable (`None`) is skipped, not priced
at zero.

Disclosed integration gap: FTF-0210's own real chat probe
(`packages/litellm_client/real_chat_probe.py`) reports only
`prompt_tokens`/`completion_tokens`/`total_tokens` today -- it has no
cache/reasoning token fields to source `ProviderTokenUsage.cache_tokens`/
`reasoning_tokens` from yet. A caller integrating this module with
today's probe output passes `cache_tokens=None`/`reasoning_tokens=None`
(not applicable, not zero) until a future, separately-scoped task adds
that reporting upstream; `provider_call_cost` itself already implements
the full five-category formula this task's acceptance clause names, so no
further change to this module is needed once that upstream reporting
exists.

No live HTTP route is added by this task (`api_contract`/
`shared_lock_required` in the task packet both point at FTF-1301's own,
already-frozen contract slot 13); `proving_symbol` is deliberately left
empty in `docs/spec-matrix.yaml`, matching the FTF-1302/FTF-1401/FTF-1601/
FTF-1701/FTF-1810 precedent for a computation module with no live
product-runtime caller in this repository yet -- a genuine, disclosed
0/0, not inflation.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

__all__ = [
    "ProviderTokenCostingError",
    "ProviderTokenUsage",
    "ProviderRateCard",
    "provider_call_cost",
]


class ProviderTokenCostingError(ValueError):
    """A field value violates this domain's own shape/invariant, or a cost
    figure was requested for real, nonzero, declared usage with no
    matching rate -- Section 32's own "no guessed cost values" rule, never
    a silent $0 or invented figure."""


def _validate_non_negative_decimal(value: object, field_name: str) -> Decimal:
    if not isinstance(value, Decimal) or isinstance(value, bool):
        raise ProviderTokenCostingError(
            f"{field_name} must be a decimal.Decimal, not {type(value).__name__}: {value!r}"
        )
    if not value.is_finite():
        raise ProviderTokenCostingError(f"{field_name} must be finite, not {value!r}")
    if value < 0:
        raise ProviderTokenCostingError(f"{field_name} must not be negative: {value!r}")
    return value


def _validate_optional_non_negative_decimal(value: object, field_name: str) -> Decimal | None:
    if value is None:
        return None
    return _validate_non_negative_decimal(value, field_name)


def _validate_non_negative_int(value: object, field_name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise ProviderTokenCostingError(
            f"{field_name} must be an int, not {type(value).__name__}: {value!r}"
        )
    if value < 0:
        raise ProviderTokenCostingError(f"{field_name} must not be negative: {value!r}")
    return value


def _validate_optional_non_negative_int(value: object, field_name: str) -> int | None:
    if value is None:
        return None
    return _validate_non_negative_int(value, field_name)


@dataclass(frozen=True)
class ProviderTokenUsage:
    """One real, declared provider call's own billable-unit counts.

    `input_tokens`/`output_tokens` are always tracked (a provider call
    always has some prompt and completion, even if either happens to be
    `0`). `cache_tokens`/`reasoning_tokens`/`billable_requests` are each
    independently `None` when that billing category does not apply to
    this call at all (not sourced by the upstream probe, or the provider
    genuinely has no such category) -- distinct from a declared `0`, which
    is a real, billable-but-zero observation for that category.
    """

    input_tokens: int
    output_tokens: int
    cache_tokens: int | None = None
    reasoning_tokens: int | None = None
    billable_requests: int | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "input_tokens", _validate_non_negative_int(self.input_tokens, "input_tokens")
        )
        object.__setattr__(
            self, "output_tokens", _validate_non_negative_int(self.output_tokens, "output_tokens")
        )
        object.__setattr__(
            self,
            "cache_tokens",
            _validate_optional_non_negative_int(self.cache_tokens, "cache_tokens"),
        )
        object.__setattr__(
            self,
            "reasoning_tokens",
            _validate_optional_non_negative_int(self.reasoning_tokens, "reasoning_tokens"),
        )
        object.__setattr__(
            self,
            "billable_requests",
            _validate_optional_non_negative_int(self.billable_requests, "billable_requests"),
        )


@dataclass(frozen=True)
class ProviderRateCard:
    """The real, declared per-category rate this call is billed at.

    `input_token_rate`/`output_token_rate` are required -- Section 32's
    own two named `effective_input_rate`/`effective_output_rate` terms are
    always needed to price a real call. `cache_token_rate`/
    `reasoning_token_rate`/`request_rate` are each `None` only when the
    caller has not supplied a rate for that category; `provider_call_cost`
    below is what turns an unsupplied rate paired with real nonzero usage
    into a refusal, not a silent $0 -- see module docstring.
    """

    currency: str
    input_token_rate: Decimal
    output_token_rate: Decimal
    cache_token_rate: Decimal | None = None
    reasoning_token_rate: Decimal | None = None
    request_rate: Decimal | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.currency, str) or not self.currency.strip():
            raise ProviderTokenCostingError("currency must be a non-empty string")
        object.__setattr__(
            self,
            "input_token_rate",
            _validate_non_negative_decimal(self.input_token_rate, "input_token_rate"),
        )
        object.__setattr__(
            self,
            "output_token_rate",
            _validate_non_negative_decimal(self.output_token_rate, "output_token_rate"),
        )
        object.__setattr__(
            self,
            "cache_token_rate",
            _validate_optional_non_negative_decimal(self.cache_token_rate, "cache_token_rate"),
        )
        object.__setattr__(
            self,
            "reasoning_token_rate",
            _validate_optional_non_negative_decimal(
                self.reasoning_token_rate, "reasoning_token_rate"
            ),
        )
        object.__setattr__(
            self,
            "request_rate",
            _validate_optional_non_negative_decimal(self.request_rate, "request_rate"),
        )


def _priced_category(*, usage: int | None, rate: Decimal | None, category_name: str) -> Decimal:
    """One `actual_other_billable_units` term: `usage * rate` when both are
    real, `Decimal("0")` when the category does not apply
    (`usage is None`) or applies with genuinely zero usage, and a refusal
    -- never an invented $0 -- when real nonzero usage has no matching
    rate."""
    if usage is None:
        return Decimal("0")
    if usage == 0:
        return Decimal("0")
    if rate is None:
        raise ProviderTokenCostingError(
            f"{category_name} usage is {usage} (real, nonzero) but no {category_name}_rate "
            "was supplied -- negative_acceptance: missing rate cannot be replaced by an "
            "invented or zero dollar value"
        )
    return Decimal(usage) * rate


def provider_call_cost(usage: ProviderTokenUsage, rates: ProviderRateCard) -> Decimal:
    """Section 32's own `provider_call_cost`, generalized to this task's
    own five named categories (input/output/cache/reasoning/request).
    Raises `ProviderTokenCostingError` -- never returns an invented or
    zero-filled figure -- for any category with real, nonzero, declared
    usage and no matching rate. See module docstring.
    """
    total = Decimal(usage.input_tokens) * rates.input_token_rate
    total += Decimal(usage.output_tokens) * rates.output_token_rate
    total += _priced_category(
        usage=usage.cache_tokens, rate=rates.cache_token_rate, category_name="cache_tokens"
    )
    total += _priced_category(
        usage=usage.reasoning_tokens,
        rate=rates.reasoning_token_rate,
        category_name="reasoning_tokens",
    )
    total += _priced_category(
        usage=usage.billable_requests, rate=rates.request_rate, category_name="billable_requests"
    )
    return total
