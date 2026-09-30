#!/usr/bin/env python3
"""FTF-1303 -- Provider token costing (no-DB unit/fault tests).

Runnable without Postgres or network egress -- this task adds no schema
(`database_changes`: "No independent schema change"; `contract_owner`:
FTF-1301). This suite proves:

1. `ProviderTokenUsage`/`ProviderRateCard` construction guards: negative
   counts/rates, non-int counts (including `bool`, an `int` subclass),
   non-Decimal rates, and a blank `currency` are all refused at
   construction, before any cost is computed.
2. `provider_call_cost`: a golden example covering all five named billing
   categories (input/output/cache/reasoning/request) computed against a
   declared rate card.
3. The negative_acceptance case: real, nonzero, declared usage in a
   category with no matching rate raises rather than silently pricing
   that category at $0 -- for each of the three optional categories
   independently.
4. Declared-zero usage in an optional category never raises even with no
   matching rate (there is nothing to multiply); a category marked
   not-applicable (`None`) is skipped entirely, distinct from zero.
5. Own fault injection (Law 1): a naive reimplementation of
   `provider_call_cost` that silently treats a missing rate as a $0
   charge -- exactly this task's own negative_acceptance failure mode
   ("missing usage/rate/unknown upstream cannot be replaced by invented
   or zero dollar value") -- is shown to produce a *different, wrong*
   dollar figure than the real implementation on the same inputs where
   the real implementation correctly refuses.

Run directly: python3 tests/tasks/test_ftf_1303.py
"""

from __future__ import annotations

import sys
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
API_ROOT = ROOT / "apps" / "api"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(API_ROOT))

from app.services.provider_token_costing import (  # noqa: E402
    ProviderRateCard,
    ProviderTokenCostingError,
    ProviderTokenUsage,
    provider_call_cost,
)

FAILURES: list[str] = []


def check(criterion_id: str, description: str, condition: bool) -> None:
    if not condition:
        FAILURES.append(f"FAIL [{criterion_id}] {description}")


def _rejects(fn, *args: object, **kwargs: object) -> bool:
    try:
        fn(*args, **kwargs)
        return False
    except ProviderTokenCostingError:
        return True


def _usage(**overrides):
    kwargs = {
        "input_tokens": 1000,
        "output_tokens": 500,
        "cache_tokens": None,
        "reasoning_tokens": None,
        "billable_requests": None,
    }
    kwargs.update(overrides)
    return kwargs


def _rates(**overrides):
    kwargs = {
        "currency": "USD",
        "input_token_rate": Decimal("0.01"),
        "output_token_rate": Decimal("0.02"),
        "cache_token_rate": None,
        "reasoning_token_rate": None,
        "request_rate": None,
    }
    kwargs.update(overrides)
    return kwargs


# -- 1. Construction guards --------------------------------------------------


def test_usage_rejects_negative_input_tokens() -> None:
    check(
        "FTF-1303",
        "input_tokens must not be negative",
        _rejects(ProviderTokenUsage, **_usage(input_tokens=-1)),
    )


def test_usage_rejects_bool_for_output_tokens() -> None:
    check(
        "FTF-1303",
        "output_tokens must be an int, not a bool (bool is an int subclass)",
        _rejects(ProviderTokenUsage, **_usage(output_tokens=True)),
    )


def test_usage_rejects_negative_optional_cache_tokens() -> None:
    check(
        "FTF-1303",
        "cache_tokens, when declared, must not be negative",
        _rejects(ProviderTokenUsage, **_usage(cache_tokens=-5)),
    )


def test_rates_rejects_blank_currency() -> None:
    check(
        "FTF-1303",
        "currency must be a non-empty string",
        _rejects(ProviderRateCard, **_rates(currency="")),
    )


def test_rates_rejects_non_decimal_input_token_rate() -> None:
    check(
        "FTF-1303",
        "input_token_rate must be a decimal.Decimal, not a float",
        _rejects(ProviderRateCard, **_rates(input_token_rate=0.01)),
    )


def test_rates_rejects_negative_optional_cache_token_rate() -> None:
    check(
        "FTF-1303",
        "cache_token_rate, when declared, must not be negative",
        _rejects(ProviderRateCard, **_rates(cache_token_rate=Decimal("-0.01"))),
    )


# -- 2. provider_call_cost: golden example across all five categories -------


def test_provider_call_cost_golden_example_all_five_categories() -> None:
    usage = ProviderTokenUsage(
        **_usage(
            input_tokens=1000,
            output_tokens=500,
            cache_tokens=200,
            reasoning_tokens=100,
            billable_requests=1,
        )
    )
    rates = ProviderRateCard(
        **_rates(
            input_token_rate=Decimal("0.01"),  # 1000 * 0.01 = 10.00
            output_token_rate=Decimal("0.02"),  # 500 * 0.02 = 10.00
            cache_token_rate=Decimal("0.005"),  # 200 * 0.005 = 1.00
            reasoning_token_rate=Decimal("0.02"),  # 100 * 0.02 = 2.00
            request_rate=Decimal("0.50"),  # 1 * 0.50 = 0.50
        )
    )
    check(
        "FTF-1303",
        "provider_call_cost sums all five named billing categories: "
        "10.00 + 10.00 + 1.00 + 2.00 + 0.50 = 23.50",
        provider_call_cost(usage, rates) == Decimal("23.50"),
    )


def test_provider_call_cost_only_input_and_output_when_others_not_applicable() -> None:
    usage = ProviderTokenUsage(**_usage(input_tokens=1000, output_tokens=500))
    rates = ProviderRateCard(**_rates())
    check(
        "FTF-1303",
        "cache/reasoning/request are None (not applicable) -- only input+output are billed: "
        "10.00 + 10.00 = 20.00",
        provider_call_cost(usage, rates) == Decimal("20.00"),
    )


# -- 3. negative_acceptance: real nonzero usage with no matching rate raises -


def test_raises_on_nonzero_cache_tokens_with_no_cache_rate() -> None:
    usage = ProviderTokenUsage(**_usage(cache_tokens=200))
    rates = ProviderRateCard(**_rates())
    check(
        "FTF-1303",
        "negative_acceptance: nonzero cache_tokens with no cache_token_rate raises, "
        "never a silent $0",
        _rejects(provider_call_cost, usage, rates),
    )


def test_raises_on_nonzero_reasoning_tokens_with_no_reasoning_rate() -> None:
    usage = ProviderTokenUsage(**_usage(reasoning_tokens=50))
    rates = ProviderRateCard(**_rates())
    check(
        "FTF-1303",
        "negative_acceptance: nonzero reasoning_tokens with no reasoning_token_rate raises, "
        "never a silent $0",
        _rejects(provider_call_cost, usage, rates),
    )


def test_raises_on_nonzero_billable_requests_with_no_request_rate() -> None:
    usage = ProviderTokenUsage(**_usage(billable_requests=1))
    rates = ProviderRateCard(**_rates())
    check(
        "FTF-1303",
        "negative_acceptance: nonzero billable_requests with no request_rate raises, "
        "never a silent $0",
        _rejects(provider_call_cost, usage, rates),
    )


# -- 4. declared-zero vs not-applicable ---------------------------------------


def test_declared_zero_cache_tokens_never_raises_even_with_no_rate() -> None:
    usage = ProviderTokenUsage(**_usage(cache_tokens=0))
    rates = ProviderRateCard(**_rates())
    result = provider_call_cost(usage, rates)
    check(
        "FTF-1303",
        "declared cache_tokens=0 (a real, billable-but-zero observation) never raises and "
        "contributes exactly $0, even with no cache_token_rate supplied",
        result == Decimal("20.00"),
    )


def test_not_applicable_none_is_indistinguishable_in_total_from_declared_zero() -> None:
    none_usage = ProviderTokenUsage(**_usage(cache_tokens=None))
    zero_usage = ProviderTokenUsage(**_usage(cache_tokens=0))
    rates = ProviderRateCard(**_rates())
    check(
        "FTF-1303",
        "both not-applicable (None) and declared-zero usage contribute exactly $0 to the "
        "total when no rate is supplied -- the distinction is about whether a raise is "
        "possible with a rate present, not about the resulting total here",
        provider_call_cost(none_usage, rates) == provider_call_cost(zero_usage, rates),
    )


# -- 5. Own fault injection: silently zero-filling a missing rate -----------


def _naive_incorrect_provider_call_cost_zero_fills_missing_rates(
    usage: ProviderTokenUsage, rates: ProviderRateCard
) -> Decimal:
    """Exactly the mistake this task's own negative_acceptance forbids:
    treat a missing rate as a $0 charge for that category instead of
    refusing to price it. Used only here, as the failure mode this suite
    proves the real implementation does NOT exhibit."""
    total = Decimal(usage.input_tokens) * rates.input_token_rate
    total += Decimal(usage.output_tokens) * rates.output_token_rate
    if usage.cache_tokens is not None and rates.cache_token_rate is not None:
        total += Decimal(usage.cache_tokens) * rates.cache_token_rate
    if usage.reasoning_tokens is not None and rates.reasoning_token_rate is not None:
        total += Decimal(usage.reasoning_tokens) * rates.reasoning_token_rate
    if usage.billable_requests is not None and rates.request_rate is not None:
        total += Decimal(usage.billable_requests) * rates.request_rate
    return total


def test_fault_injection_naive_zero_filling_disagrees_with_real_refusal() -> None:
    usage = ProviderTokenUsage(**_usage(cache_tokens=200))
    rates = ProviderRateCard(**_rates())
    check(
        "FTF-1303",
        "the real implementation refuses to price real, nonzero, unpriced cache_tokens usage",
        _rejects(provider_call_cost, usage, rates),
    )
    naive_result = _naive_incorrect_provider_call_cost_zero_fills_missing_rates(usage, rates)
    check(
        "FTF-1303",
        "fault injection: the naive zero-filling reimplementation silently returns a "
        "concrete dollar figure ($20.00, missing the real 200-token cache charge) instead "
        "of refusing -- exactly the invented/zero-dollar-value failure mode this task's own "
        "negative_acceptance forbids",
        naive_result == Decimal("20.00"),
    )


def main() -> None:
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()

    if FAILURES:
        print(f"FTF-1303 Provider token costing tests: {len(FAILURES)} failed")
        for failure in FAILURES:
            print(f"  {failure}")
        sys.exit(1)

    print("FTF-1303 Provider token costing tests: all checks passed")
    sys.exit(0)


if __name__ == "__main__":
    main()
