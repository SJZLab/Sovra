#!/usr/bin/env python3
"""FTF-1304 -- Cost-per-success calculator (no-DB unit/fault tests).

Runnable without Postgres or network egress -- this task adds no schema
(`database_changes`: "No independent schema change"; `contract_owner`:
FTF-1301). This suite proves:

1. `AttemptCost` construction guards: negative settled/pending cost, an
   outcome outside Section 69.2's closed vocabulary, `fallback_used=True`
   with no matching `fallback_cost`, and a `fallback_cost` declared
   without `fallback_used=True` are all refused at construction.
2. `cost_per_success`: a golden example mixing settled, pending
   (RECONCILIATION_REQUIRED) and fallback/judge/tool charges across five
   attempts, one of them a non-`pass` outcome that still costs money.
3. Dedup: two attempts sharing one `request_id` (a retry) count once in
   the successes denominator but both costs count in the total.
4. The negative_acceptance case: zero successes raises; pending and
   fallback charges are never dropped from the total even though they are
   `None`-able fields, proven by a real fault-injection comparison.
5. `amortized_cost_per_success`: golden value, and refusal when only one
   of the two amortization arguments is supplied.
6. Own fault injection (Law 1): a naive reimplementation of
   `cost_per_success` that sums only `settled_cost`, silently dropping
   pending and fallback charges -- exactly this task's own
   negative_acceptance failure mode ("pending charges and unobserved
   fallback use cannot yield misleading cheap passing metric") -- is
   shown to produce a strictly *cheaper*, wrong dollar figure than the
   real implementation on the same inputs.

Run directly: python3 tests/tasks/test_ftf_1304.py
"""

from __future__ import annotations

import sys
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
API_ROOT = ROOT / "apps" / "api"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(API_ROOT))

from app.services.cost_per_success_calculator import (  # noqa: E402
    AttemptCost,
    CostPerSuccessError,
    cost_per_success,
    unique_successful_request_ids,
)

FAILURES: list[str] = []


def check(criterion_id: str, description: str, condition: bool) -> None:
    if not condition:
        FAILURES.append(f"FAIL [{criterion_id}] {description}")


def _rejects(fn, *args: object, **kwargs: object) -> bool:
    try:
        fn(*args, **kwargs)
        return False
    except CostPerSuccessError:
        return True


def _attempt(**overrides):
    kwargs = {
        "request_id": "r1",
        "outcome": "pass",
        "settled_cost": Decimal("10.00"),
        "pending_cost": None,
        "fallback_used": False,
        "fallback_cost": None,
    }
    kwargs.update(overrides)
    return kwargs


# -- 1. Construction guards ---------------------------------------------------


def test_rejects_negative_settled_cost() -> None:
    check(
        "FTF-1304",
        "settled_cost must not be negative",
        _rejects(AttemptCost, **_attempt(settled_cost=Decimal("-1.00"))),
    )


def test_rejects_negative_pending_cost() -> None:
    check(
        "FTF-1304",
        "pending_cost, when declared, must not be negative",
        _rejects(AttemptCost, **_attempt(pending_cost=Decimal("-0.01"))),
    )


def test_rejects_non_decimal_settled_cost() -> None:
    check(
        "FTF-1304",
        "settled_cost must be a decimal.Decimal, not a float",
        _rejects(AttemptCost, **_attempt(settled_cost=10.0)),
    )


def test_rejects_outcome_outside_closed_vocabulary() -> None:
    check(
        "FTF-1304",
        "outcome must be one of Section 69.2's closed terminal-outcome vocabulary",
        _rejects(AttemptCost, **_attempt(outcome="success")),
    )


def test_rejects_blank_request_id() -> None:
    check(
        "FTF-1304",
        "request_id must be a non-empty string",
        _rejects(AttemptCost, **_attempt(request_id="  ")),
    )


def test_rejects_fallback_used_without_fallback_cost() -> None:
    check(
        "FTF-1304",
        "negative_acceptance: fallback_used=True with no fallback_cost raises, never a silent $0",
        _rejects(AttemptCost, **_attempt(fallback_used=True, fallback_cost=None)),
    )


def test_rejects_fallback_cost_without_fallback_used() -> None:
    check(
        "FTF-1304",
        "a declared fallback_cost cannot be attached to an attempt that did not declare "
        "fallback_used=True",
        _rejects(AttemptCost, **_attempt(fallback_used=False, fallback_cost=Decimal("1.00"))),
    )


def test_rejects_non_bool_fallback_used() -> None:
    check(
        "FTF-1304",
        "fallback_used must be a bool, not an int (even 0/1)",
        _rejects(AttemptCost, **_attempt(fallback_used=1, fallback_cost=Decimal("1.00"))),
    )


# -- 2. cost_per_success: golden example mixing all cost categories ----------


def _golden_attempts() -> list[AttemptCost]:
    return [
        AttemptCost(**_attempt(request_id="r1", outcome="pass", settled_cost=Decimal("10.00"))),
        AttemptCost(
            **_attempt(
                request_id="r2",
                outcome="pass",
                settled_cost=Decimal("6.00"),
                pending_cost=Decimal("2.00"),
            )
        ),
        AttemptCost(
            **_attempt(
                request_id="r3",
                outcome="pass",
                settled_cost=Decimal("5.00"),
                fallback_used=True,
                fallback_cost=Decimal("1.00"),
            )
        ),
        AttemptCost(**_attempt(request_id="r4", outcome="pass", settled_cost=Decimal("10.00"))),
        AttemptCost(
            **_attempt(request_id="r5", outcome="model_fail", settled_cost=Decimal("4.00"))
        ),
    ]


def test_cost_per_success_golden_example() -> None:
    result = cost_per_success(_golden_attempts())
    check(
        "FTF-1304",
        "total costs across all five attempts (10 + 8 + 6 + 10 + 4 = 38) divided by 4 "
        "independently successful tasks (r1, r2, r3, r4 -- r5's model_fail attempt still "
        "costs money but is not a success) = 9.50",
        result.cost_per_success == Decimal("9.50"),
    )
    check(
        "FTF-1304",
        "settled_cost_per_success excludes pending/fallback: (10+6+5+10+4)/4 = 8.75",
        result.settled_cost_per_success == Decimal("8.75"),
    )
    check(
        "FTF-1304",
        "has_pending_reconciliation is True: r2 declares a real pending_cost",
        result.has_pending_reconciliation is True,
    )
    check(
        "FTF-1304",
        "amortized_cost_per_success is None when no amortization was requested",
        result.amortized_cost_per_success is None,
    )


def test_unique_successful_request_ids_excludes_non_pass_outcomes() -> None:
    successes = unique_successful_request_ids(_golden_attempts())
    check(
        "FTF-1304",
        "unique_successful_request_ids is exactly {r1, r2, r3, r4} -- r5 (model_fail) excluded",
        successes == frozenset({"r1", "r2", "r3", "r4"}),
    )


# -- 3. Dedup: a retry of the same request_id counts once in the denominator -


def test_retry_of_same_request_id_counts_once_but_both_costs_included() -> None:
    attempts = [
        AttemptCost(**_attempt(request_id="r1", outcome="pass", settled_cost=Decimal("10.00"))),
        AttemptCost(**_attempt(request_id="r1", outcome="pass", settled_cost=Decimal("5.00"))),
    ]
    result = cost_per_success(attempts)
    check(
        "FTF-1304",
        "a retry of the same request_id is one independent task, not two -- Section 69.2: "
        "'Multiple turns from one conversation, repeated seeds ... are not independent tasks'",
        len(unique_successful_request_ids(attempts)) == 1,
    )
    check(
        "FTF-1304",
        "both attempts' own real cost is still included in the total: (10+5)/1 = 15.00, not "
        "just the first or last attempt's cost",
        result.cost_per_success == Decimal("15.00"),
    )


# -- 4. negative_acceptance: zero successes -----------------------------------


def test_zero_successes_raises() -> None:
    attempts = [
        AttemptCost(
            **_attempt(request_id="r1", outcome="model_fail", settled_cost=Decimal("4.00"))
        ),
        AttemptCost(**_attempt(request_id="r2", outcome="timeout", settled_cost=Decimal("3.00"))),
    ]
    check(
        "FTF-1304",
        "negative_acceptance: zero successes raises (Section 32: undefined/infinite), never "
        "a misleadingly cheap $0 or divide-by-zero crash",
        _rejects(cost_per_success, attempts),
    )


# -- 5. amortized_cost_per_success --------------------------------------------


def _base_two_success_attempts() -> list[AttemptCost]:
    return [
        AttemptCost(**_attempt(request_id="r1", outcome="pass", settled_cost=Decimal("10.00"))),
        AttemptCost(**_attempt(request_id="r2", outcome="pass", settled_cost=Decimal("10.00"))),
    ]


def test_amortized_cost_per_success_golden_example() -> None:
    result = cost_per_success(
        _base_two_success_attempts(),
        training_and_qualification_cost=Decimal("100.00"),
        approved_expected_lifetime_successes=50,
    )
    check(
        "FTF-1304",
        "amortized_cost_per_success = cost_per_success (10.00) + "
        "training_and_qualification_cost / approved_expected_lifetime_successes "
        "(100.00 / 50 = 2.00) = 12.00",
        result.amortized_cost_per_success == Decimal("12.00"),
    )
    check(
        "FTF-1304",
        "cost_per_success itself is unaffected by amortization: still 10.00",
        result.cost_per_success == Decimal("10.00"),
    )


def test_amortization_requires_both_arguments_together() -> None:
    check(
        "FTF-1304",
        "training_and_qualification_cost alone (no approved_expected_lifetime_successes) is "
        "refused, not silently treated as no amortization",
        _rejects(
            cost_per_success,
            _base_two_success_attempts(),
            training_and_qualification_cost=Decimal("100.00"),
        ),
    )
    check(
        "FTF-1304",
        "approved_expected_lifetime_successes alone (no training_and_qualification_cost) is "
        "refused, not silently treated as no amortization",
        _rejects(
            cost_per_success,
            _base_two_success_attempts(),
            approved_expected_lifetime_successes=50,
        ),
    )


def test_amortization_rejects_non_positive_lifetime_successes() -> None:
    check(
        "FTF-1304",
        "approved_expected_lifetime_successes=0 is refused (undefined amortization, not $0)",
        _rejects(
            cost_per_success,
            _base_two_success_attempts(),
            training_and_qualification_cost=Decimal("100.00"),
            approved_expected_lifetime_successes=0,
        ),
    )
    check(
        "FTF-1304",
        "approved_expected_lifetime_successes must be an int, not a bool (bool is an int subclass)",
        _rejects(
            cost_per_success,
            _base_two_success_attempts(),
            training_and_qualification_cost=Decimal("100.00"),
            approved_expected_lifetime_successes=True,
        ),
    )


# -- 6. Own fault injection: silently dropping pending/fallback charges ------


def _naive_incorrect_cost_per_success_drops_pending_and_fallback(
    attempts: list[AttemptCost],
) -> Decimal:
    """Exactly the mistake this task's own negative_acceptance forbids: sum
    only `settled_cost`, silently dropping pending (RECONCILIATION_
    REQUIRED) and fallback/judge/tool charges from the total. Used only
    here, as the failure mode this suite proves the real implementation
    does NOT exhibit."""
    successes = unique_successful_request_ids(attempts)
    total = sum((attempt.settled_cost for attempt in attempts), start=Decimal("0"))
    return total / Decimal(len(successes))


def test_fault_injection_naive_dropping_disagrees_with_real_total() -> None:
    attempts = _golden_attempts()
    real_result = cost_per_success(attempts)
    naive_result = _naive_incorrect_cost_per_success_drops_pending_and_fallback(attempts)
    check(
        "FTF-1304",
        "the real implementation's cost_per_success (9.50) includes r2's pending_cost and "
        "r3's fallback_cost",
        real_result.cost_per_success == Decimal("9.50"),
    )
    check(
        "FTF-1304",
        "fault injection: the naive settled-only reimplementation silently produces a "
        "cheaper, wrong figure (8.75) by dropping real declared pending/fallback charges -- "
        "exactly the misleading-cheap-metric failure mode this task's own negative_acceptance "
        "forbids",
        naive_result == Decimal("8.75") and naive_result != real_result.cost_per_success,
    )


def main() -> None:
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()

    if FAILURES:
        print(f"FTF-1304 Cost-per-success calculator tests: {len(FAILURES)} failed")
        for failure in FAILURES:
            print(f"  {failure}")
        sys.exit(1)

    print("FTF-1304 Cost-per-success calculator tests: all checks passed")
    sys.exit(0)


if __name__ == "__main__":
    main()
