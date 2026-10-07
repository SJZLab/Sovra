#!/usr/bin/env python3
# mypy: disable-error-code="arg-type,call-arg,var-annotated,list-item"
# ruff: noqa: E501
"""FTF-0613 -- Cost grader.

Runnable without any database or network: `grade_cost` is a pure, synchronous,
no-I/O function (no independent schema change).

Acceptance ("Grade total attempts/service-window cost against versioned policy and
actual measured prices"): test_total_includes_every_attempt, test_exact_ceilings,
test_cost_per_success_unique_by_request, test_policy_version_and_currency_declared,
test_measured_prices_only.

Negative acceptance ("Unknown cost or zero successful denominator is insufficient
evidence, not passing free usage"): test_unknown_cost_is_not_zero,
test_zero_successes_insufficient, test_missing_attempt_insufficient,
test_estimated_price_insufficient, test_empty_observations_never_pass.

GRADER CHECKLIST: malformed records/specs fail closed, case/whitespace exactness,
duplicates preserved, empty-trace semantics, multi-constraint coverage.

Run directly: python3 tests/tasks/test_ftf_0613.py
"""

from __future__ import annotations

import sys
from dataclasses import FrozenInstanceError
from fractions import Fraction
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from workers.evaluation.cost_grader import (  # noqa: E402
    CostGraderError,
    CostObservation,
    CostResult,
    CostSpec,
    grade_cost,
)

FAILURES: list[str] = []
CID = "FTF-0613"


def check(criterion_id: str, description: str, condition: bool) -> None:
    if not condition:
        FAILURES.append(f"FAIL [{criterion_id}] {description}")


def raises(fn: Any) -> bool:
    try:
        fn()
    except CostGraderError:
        return True
    except Exception:  # noqa: BLE001 -- the wrong exception type is a failed check
        return False
    return False


def spec(n: int = 2, **over: Any) -> CostSpec:
    base: dict[str, Any] = {
        "policy_id": "pol",
        "policy_version": 3,
        "currency": "USD",
        "expected_attempt_ids": tuple(f"a{i}" for i in range(1, n + 1)),
        "max_total_cost": "10",
        "max_cost_per_success": "5",
    }
    base.update(over)
    return CostSpec(**base)


def obs(attempt: str, cost: Any = "1", **over: Any) -> CostObservation:
    base: dict[str, Any] = {
        "attempt_id": attempt,
        "request_id": f"r-{attempt}",
        "outcome": "success",
        "source": "measured",
        "policy_version": 3,
        "currency": "USD",
        "cost": cost,
    }
    base.update(over)
    return CostObservation(**base)


def good(n: int = 2, cost: str = "1") -> list[CostObservation]:
    return [obs(f"a{i}", cost) for i in range(1, n + 1)]


def test_total_includes_every_attempt() -> None:
    result = grade_cost(
        spec(3), [obs("a1", "1"), obs("a2", "2", outcome="failure"), obs("a3", "3")]
    )
    check(
        CID, "clean run passes", result.passed and result.status == "pass" and result.issues == ()
    )
    check(CID, "failed attempts' cost is in the total", result.total_cost == Fraction(6))
    check(CID, "unique successes counted", result.unique_successes == 2)
    check(CID, "cost per success uses the full total", result.cost_per_success == Fraction(3))


def test_exact_ceilings() -> None:
    at = grade_cost(
        spec(1, max_total_cost="1.10", max_cost_per_success="1.10"), [obs("a1", "1.10")]
    )
    check(CID, "exactly at both ceilings passes", at.passed)
    over_total = grade_cost(
        spec(1, max_total_cost="1.09", max_cost_per_success="5"), [obs("a1", "1.10")]
    )
    check(
        CID,
        "one cent over the total ceiling fails",
        over_total.status == "fail" and over_total.issues == ("over_total_cost",),
    )
    over_ps = grade_cost(
        spec(1, max_total_cost="10", max_cost_per_success="1.09"), [obs("a1", "1.10")]
    )
    check(
        CID,
        "one cent over the per-success ceiling fails",
        over_ps.issues == ("over_cost_per_success",),
    )
    tenth = grade_cost(
        spec(1, max_total_cost="0.3", max_cost_per_success="0.3"), [obs("a1", "0.30")]
    )
    check(CID, "decimal 0.30 vs 0.3 has no binary rounding", tenth.passed)
    thirds = grade_cost(
        spec(3, max_total_cost="10", max_cost_per_success="0.33"),
        [obs("a1", "0.5", outcome="failure"), obs("a2", "0.25"), obs("a3", "0.25")],
    )
    check(
        CID,
        "per-success 0.5 is exact, not rounded down",
        thirds.issues == ("over_cost_per_success",),
    )


def test_cost_per_success_unique_by_request() -> None:
    twice = [obs("a1", "2", request_id="r1"), obs("a2", "2", request_id="r1")]
    result = grade_cost(spec(2), twice)
    check(CID, "a request repeated under one id counts one success", result.unique_successes == 1)
    check(
        CID,
        "every attempt's cost still counts",
        result.total_cost == Fraction(4) and result.cost_per_success == Fraction(4),
    )
    cheap_if_double = grade_cost(spec(2, max_cost_per_success="3"), twice)
    check(
        CID,
        "double counting would pass; unique counting fails",
        cheap_if_double.issues == ("over_cost_per_success",),
    )
    case = grade_cost(spec(2), [obs("a1", request_id="r1"), obs("a2", request_id="R1")])
    check(CID, "request ids are case-exact", case.unique_successes == 2)
    space = grade_cost(spec(2), [obs("a1", request_id="r1"), obs("a2", request_id="r1 ")])
    check(CID, "request ids are whitespace-exact", space.unique_successes == 2)
    fail_only = grade_cost(
        spec(2), [obs("a1", request_id="r1", outcome="failure"), obs("a2", request_id="r1")]
    )
    check(CID, "one success among failed retries counts once", fail_only.unique_successes == 1)


def test_policy_version_and_currency_declared() -> None:
    old = grade_cost(spec(2), [obs("a1"), obs("a2", policy_version=2)])
    check(
        CID,
        "an observation under another policy version fails",
        old.status == "fail" and old.issues == ("policy_version_mismatch",),
    )
    eur = grade_cost(spec(2), [obs("a1"), obs("a2", currency="EUR")])
    check(CID, "another currency fails", eur.status == "fail" and "currency_mismatch" in eur.issues)
    check(CID, "another currency is never summed", eur.total_cost == Fraction(1))
    lower = grade_cost(spec(1), [obs("a1", currency="usd")])
    check(CID, "currency is case-exact", "currency_mismatch" in lower.issues)
    spaced = grade_cost(spec(1), [obs("a1", currency="USD ")])
    check(CID, "currency is whitespace-exact", "currency_mismatch" in spaced.issues)
    check(CID, "policy_version must be >= 1", raises(lambda: spec(policy_version=0)))
    check(CID, "policy_version refuses bool", raises(lambda: spec(policy_version=True)))
    check(CID, "policy_version refuses float", raises(lambda: spec(policy_version=1.0)))


def test_measured_prices_only() -> None:
    result = grade_cost(spec(2), [obs("a1"), obs("a2", source="estimated")])
    check(
        CID,
        "an estimated price is insufficient evidence",
        result.status == "insufficient_evidence" and result.issues == ("unmeasured_price",),
    )
    check(
        CID, "an estimate is still summed as a lower-bound figure", result.total_cost == Fraction(2)
    )
    check(
        CID,
        "source Measured is refused (case-exact)",
        raises(lambda: grade_cost(spec(1), [obs("a1", source="Measured")])),
    )
    check(
        CID,
        "source with a space is refused",
        raises(lambda: grade_cost(spec(1), [obs("a1", source="measured ")])),
    )


def test_unknown_cost_is_not_zero() -> None:
    unknown = grade_cost(spec(2), [obs("a1"), obs("a2", None)])
    check(
        CID,
        "None cost is unknown_cost / insufficient",
        unknown.status == "insufficient_evidence" and unknown.issues == ("unknown_cost",),
    )
    free = grade_cost(spec(2), [obs("a1"), obs("a2", "0")])
    check(
        CID,
        "the string 0 is a real measured zero and passes",
        free.passed and free.total_cost == Fraction(1),
    )
    all_unknown = grade_cost(spec(2), [obs("a1", None), obs("a2", None)])
    check(
        CID,
        "all-unknown cost never passes as free",
        all_unknown.status == "insufficient_evidence" and all_unknown.total_cost == 0,
    )


def test_zero_successes_insufficient() -> None:
    result = grade_cost(spec(2), [obs("a1", outcome="failure"), obs("a2", outcome="failure")])
    check(
        CID,
        "zero successes is insufficient_evidence",
        result.status == "insufficient_evidence" and result.issues == ("zero_successes",),
    )
    check(CID, "cost per success is undefined, not zero", result.cost_per_success is None)
    check(CID, "a cheap all-failure run does not pass", not result.passed)


def test_missing_attempt_insufficient() -> None:
    result = grade_cost(spec(3), good(2))
    check(
        CID,
        "a missing attempt is insufficient_evidence",
        result.status == "insufficient_evidence" and result.issues == ("missing_attempt",),
    )
    wrong = grade_cost(spec(1), [obs("A1")])
    check(
        CID,
        "attempt ids are case-exact",
        {"missing_attempt", "unexpected_attempt"} <= set(wrong.issues),
    )
    spaced = grade_cost(spec(1), [obs("a1 ")])
    check(
        CID,
        "attempt ids are whitespace-exact",
        {"missing_attempt", "unexpected_attempt"} <= set(spaced.issues),
    )


def test_empty_observations_never_pass() -> None:
    for empty in ([], ()):
        result = grade_cost(spec(2), empty)
        check(CID, "no observations never passes", not result.passed)
        check(
            CID,
            "no observations is insufficient",
            result.status == "insufficient_evidence"
            and {"missing_attempt", "zero_successes"} <= set(result.issues),
        )
    check(CID, "empty expected ids refused at spec", raises(lambda: spec(0)))


def test_duplicates_and_unexpected_preserved() -> None:
    dup = grade_cost(spec(1), [obs("a1", "2"), obs("a1", "3", request_id="r2")])
    check(
        CID,
        "a repeated attempt id fails",
        dup.status == "fail" and "duplicate_attempt" in dup.issues,
    )
    check(CID, "every duplicate copy is summed", dup.total_cost == Fraction(5))
    extra = grade_cost(spec(1), [obs("a1"), obs("zz")])
    check(
        CID,
        "an unrequested attempt fails and is summed",
        extra.status == "fail"
        and extra.issues == ("unexpected_attempt",)
        and extra.total_cost == Fraction(2),
    )


def test_fail_takes_precedence_over_insufficient() -> None:
    result = grade_cost(spec(2, max_total_cost="1"), [obs("a1", "5")])
    check(
        CID,
        "a known lower bound over the ceiling fails despite a missing attempt",
        result.status == "fail" and {"over_total_cost", "missing_attempt"} <= set(result.issues),
    )


def test_multi_constraint_all_issues_reported_sorted() -> None:
    observations = [
        obs("a1", "100", policy_version=1),
        obs("a1", "1", currency="EUR"),
        obs("zz", None, source="estimated", outcome="failure"),
    ]
    result = grade_cost(spec(2, max_total_cost="1", max_cost_per_success="1"), observations)
    expected = (
        "currency_mismatch",
        "duplicate_attempt",
        "missing_attempt",
        "over_cost_per_success",
        "over_total_cost",
        "policy_version_mismatch",
        "unexpected_attempt",
        "unknown_cost",
        "unmeasured_price",
    )
    check(CID, "every applicable issue reported once in sorted order", result.issues == expected)
    check(CID, "status is fail", result.status == "fail")


def test_malformed_inputs_fail_closed() -> None:
    check(CID, "spec of the wrong type", raises(lambda: grade_cost({}, good(1))))
    check(CID, "observations None", raises(lambda: grade_cost(spec(1), None)))
    check(CID, "observations a string", raises(lambda: grade_cost(spec(1), "a1")))
    check(
        CID,
        "observation of the wrong type",
        raises(lambda: grade_cost(spec(1), [{"attempt_id": "a1"}])),
    )
    check(CID, "empty attempt id", raises(lambda: grade_cost(spec(1), [obs("")])))
    check(CID, "None attempt id", raises(lambda: grade_cost(spec(1), [obs(None)])))
    check(CID, "whitespace attempt id", raises(lambda: grade_cost(spec(1), [obs(" ")])))
    check(
        CID,
        "whitespace tab request id",
        raises(lambda: grade_cost(spec(1), [obs("a1", request_id="\t")])),
    )
    check(CID, "empty currency", raises(lambda: grade_cost(spec(1), [obs("a1", currency="")])))
    check(
        CID, "unknown outcome", raises(lambda: grade_cost(spec(1), [obs("a1", outcome="Success")]))
    )
    check(CID, "None outcome", raises(lambda: grade_cost(spec(1), [obs("a1", outcome=None)])))
    check(
        CID,
        "observation policy_version 0",
        raises(lambda: grade_cost(spec(1), [obs("a1", policy_version=0)])),
    )
    for bad in (
        1,
        1.5,
        True,
        -1,
        "-1",
        "1e3",
        "1,5",
        " 1",
        "1 ",
        "+1",
        "01",
        "NaN",
        "inf",
        "",
        "1.",
        ".5",
        "1" * 65,
    ):
        check(
            CID,
            f"cost {bad!r} fails closed",
            raises(lambda b=bad: grade_cost(spec(1), [obs("a1", b)])),
        )
    for bad_max in ("-1", "1e3", "", "NaN", 5, None, "01"):
        check(
            CID,
            f"max_total_cost {bad_max!r} refused",
            raises(lambda b=bad_max: spec(max_total_cost=b)),
        )
        check(
            CID,
            f"max_cost_per_success {bad_max!r} refused",
            raises(lambda b=bad_max: spec(max_cost_per_success=b)),
        )
    check(CID, "duplicate expected ids", raises(lambda: spec(expected_attempt_ids=("a", "a"))))
    check(CID, "expected ids as a list", raises(lambda: spec(expected_attempt_ids=["a"])))
    check(CID, "whitespace expected id", raises(lambda: spec(expected_attempt_ids=("a", " "))))
    check(CID, "empty policy_id", raises(lambda: spec(policy_id="")))
    check(CID, "whitespace policy_id", raises(lambda: spec(policy_id=" ")))
    check(CID, "empty currency in spec", raises(lambda: spec(currency=" ")))


def test_models_frozen_and_error_type() -> None:
    def mutate(obj: Any, name: str, value: Any) -> None:
        setattr(obj, name, value)

    def frozen(fn: Any) -> bool:
        try:
            fn()
        except FrozenInstanceError:
            return True
        return False

    check(CID, "CostSpec frozen", frozen(lambda: mutate(spec(), "policy_version", 9)))
    check(CID, "CostObservation frozen", frozen(lambda: mutate(obs("a1"), "cost", "0")))
    check(
        CID,
        "CostResult frozen",
        frozen(lambda: mutate(grade_cost(spec(1), good(1)), "status", "fail")),
    )
    check(CID, "CostGraderError is a ValueError", issubclass(CostGraderError, ValueError))
    check(
        CID,
        "CostResult.passed only for pass",
        CostResult("fail", (), Fraction(0), 0, None).passed is False,
    )


def main() -> int:
    tests = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_") and callable(f)]
    for name, fn in tests:
        try:
            fn()
        except Exception as exc:  # noqa: BLE001 -- a crash is a failed test, reported by name
            FAILURES.append(f"FAIL [{CID}] {name} raised {type(exc).__name__}: {exc}")
    if FAILURES:
        for failure in FAILURES:
            print(failure)
        print(f"{len(FAILURES)} check(s) failed")
        return 1
    print("all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
