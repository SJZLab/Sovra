#!/usr/bin/env python3
# mypy: disable-error-code="arg-type,call-arg,var-annotated,list-item"
# ruff: noqa: E501
"""FTF-0612 -- Latency grader.

Runnable without any database or network: `grade_latency` is a pure, synchronous,
no-I/O function (no independent schema change).

Acceptance ("Measure request/queue/TTFT/whole-agent latencies using declared
clock/workload and timeout treatment"): test_all_four_metrics_measured,
test_nearest_rank_percentiles, test_limit_boundary_is_inclusive,
test_clock_and_workload_declared, test_timeout_treatment_declared.

Negative acceptance ("Dropped/timeout cases cannot be removed to improve p95;
cold/warm profiles are not mixed silently"): test_timeout_dropped_error_counted_not_removed,
test_missing_case_counted_not_removed, test_empty_observations_fail,
test_dropping_slow_cases_cannot_improve_p95, test_profiles_not_mixed.

GRADER CHECKLIST: malformed records/specs fail closed, case/whitespace exactness,
duplicates preserved, empty-trace semantics, multi-constraint coverage.

Run directly: python3 tests/tasks/test_ftf_0612.py
"""

from __future__ import annotations

import copy
import sys
from dataclasses import FrozenInstanceError, replace
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from workers.evaluation.latency_grader import (  # noqa: E402
    LatencyGraderError,
    LatencyLimit,
    LatencyObservation,
    LatencyResult,
    LatencySpec,
    grade_latency,
)

FAILURES: list[str] = []
CID = "FTF-0612"
TIMEOUT = 10_000


def check(criterion_id: str, description: str, condition: bool) -> None:
    if not condition:
        FAILURES.append(f"FAIL [{criterion_id}] {description}")


def raises(fn: Any) -> bool:
    try:
        fn()
    except LatencyGraderError:
        return True
    except Exception:  # noqa: BLE001 -- the wrong exception type is a failed check
        return False
    return False


def case_ids(n: int) -> tuple[str, ...]:
    return tuple(f"c{i}" for i in range(1, n + 1))


def spec(n: int = 4, **over: Any) -> LatencySpec:
    base: dict[str, Any] = {
        "clock": "monotonic",
        "workload_id": "w1",
        "profile": "warm",
        "timeout_ms": TIMEOUT,
        "expected_case_ids": case_ids(n),
        "limits": (LatencyLimit("agent_ms", 95, 5_000),),
    }
    base.update(over)
    return LatencySpec(**base)


def obs(case_id: str, agent: int = 100, **over: Any) -> LatencyObservation:
    base: dict[str, Any] = {
        "case_id": case_id,
        "clock": "monotonic",
        "workload_id": "w1",
        "profile": "warm",
        "outcome": "ok",
        "request_ms": 10,
        "queue_ms": 5,
        "ttft_ms": 20,
        "agent_ms": agent,
    }
    base.update(over)
    return LatencyObservation(**base)


def good(n: int = 4, agent: int = 100) -> list[LatencyObservation]:
    return [obs(c, agent) for c in case_ids(n)]


def pct(result: LatencyResult, metric: str, percentile: int) -> int:
    for m, p, v in result.percentiles:
        if (m, p) == (metric, percentile):
            return v
    return -1


def test_all_four_metrics_measured() -> None:
    limits = tuple(
        LatencyLimit(m, 50, 10_000) for m in ("request_ms", "queue_ms", "ttft_ms", "agent_ms")
    )
    observations = [
        obs("c1", 100, request_ms=11, queue_ms=3, ttft_ms=21),
        obs("c2", 200, request_ms=12, queue_ms=4, ttft_ms=22),
    ]
    result = grade_latency(spec(2, limits=limits), observations)
    check(CID, "clean run passes", result.passed and result.issues == ())
    check(CID, "request p50 measured", pct(result, "request_ms", 50) == 11)
    check(CID, "queue p50 measured", pct(result, "queue_ms", 50) == 3)
    check(CID, "ttft p50 measured", pct(result, "ttft_ms", 50) == 21)
    check(CID, "agent p50 measured", pct(result, "agent_ms", 50) == 100)


def test_nearest_rank_percentiles() -> None:
    limits = tuple(LatencyLimit("agent_ms", p, 10_000) for p in (50, 95, 99))
    observations = [obs(f"c{i}", i * 10) for i in range(1, 101)]
    result = grade_latency(spec(100, limits=limits), observations)
    check(CID, "p50 of 10..1000 is 500", pct(result, "agent_ms", 50) == 500)
    check(CID, "p95 of 10..1000 is 950", pct(result, "agent_ms", 95) == 950)
    check(CID, "p99 of 10..1000 is 990", pct(result, "agent_ms", 99) == 990)
    four = grade_latency(spec(4, limits=limits), [obs(f"c{i}", i * 100) for i in range(1, 5)])
    check(CID, "p50 of 4 values is the 2nd", pct(four, "agent_ms", 50) == 200)
    check(CID, "p95 of 4 values is the max", pct(four, "agent_ms", 95) == 400)
    one = grade_latency(spec(1, limits=limits), [obs("c1", 7)])
    check(CID, "single value is every percentile", pct(one, "agent_ms", 99) == 7)


def test_limit_boundary_is_inclusive() -> None:
    at = grade_latency(spec(1, limits=(LatencyLimit("agent_ms", 95, 100),)), [obs("c1", 100)])
    over = grade_latency(spec(1, limits=(LatencyLimit("agent_ms", 95, 99),)), [obs("c1", 100)])
    check(CID, "exactly at the limit passes", at.passed)
    check(CID, "one ms over the limit fails", not over.passed and over.issues == ("over_limit",))
    zero = grade_latency(
        spec(1, limits=(LatencyLimit("agent_ms", 95, 0),)), [obs("c1", 0, request_ms=0)]
    )
    check(CID, "zero latency against a zero limit passes", zero.passed)


def test_clock_and_workload_declared() -> None:
    wall = grade_latency(spec(1), [obs("c1", clock="wall")])
    check(CID, "a different clock is clock_mismatch", wall.issues == ("clock_mismatch",))
    work = grade_latency(spec(1), [obs("c1", workload_id="w2")])
    check(CID, "a different workload is workload_mismatch", work.issues == ("workload_mismatch",))
    case = grade_latency(spec(1), [obs("c1", workload_id="W1")])
    check(CID, "workload compared case-exactly", case.issues == ("workload_mismatch",))
    space = grade_latency(spec(1), [obs("c1", workload_id="w1 ")])
    check(CID, "workload compared whitespace-exactly", space.issues == ("workload_mismatch",))
    check(CID, "spec rejects an unknown clock", raises(lambda: spec(clock="gps")))
    check(CID, "spec rejects an empty workload", raises(lambda: spec(workload_id="")))


def unvalidated(base: LatencySpec, **over: Any) -> LatencySpec:
    """A spec that skipped __post_init__ (copy/pickle path): grading must still compare exactly."""
    clone = copy.copy(base)
    for name, value in over.items():
        object.__setattr__(clone, name, value)
    return clone


def test_comparisons_exact_even_for_unvalidated_spec() -> None:
    check(
        CID,
        "spec workload W1 vs observation w1",
        grade_latency(spec(1, workload_id="W1"), [obs("c1")]).issues == ("workload_mismatch",),
    )
    check(
        CID,
        "spec workload with a trailing space vs observation",
        grade_latency(spec(1, workload_id="w1 "), [obs("c1")]).issues == ("workload_mismatch",),
    )
    check(
        CID,
        "observation clock WALL vs spec wall",
        grade_latency(spec(1, clock="wall"), [obs("c1", clock="WALL")]).issues
        == ("clock_mismatch",),
    )
    check(
        CID,
        "observation clock with a space",
        grade_latency(spec(1), [obs("c1", clock="monotonic ")]).issues == ("clock_mismatch",),
    )
    check(
        CID,
        "observation profile Warm vs warm",
        grade_latency(spec(1), [obs("c1", profile="Warm")]).issues == ("profile_mismatch",),
    )
    check(
        CID,
        "observation profile with a space",
        grade_latency(spec(1), [obs("c1", profile="warm ")]).issues == ("profile_mismatch",),
    )
    check(
        CID,
        "unvalidated spec profile Warm",
        grade_latency(unvalidated(spec(1), profile="Warm"), [obs("c1")]).issues
        == ("profile_mismatch",),
    )
    check(
        CID,
        "unvalidated spec profile with a space",
        grade_latency(unvalidated(spec(1), profile="warm "), [obs("c1")]).issues
        == ("profile_mismatch",),
    )
    check(
        CID,
        "unvalidated spec clock MONOTONIC",
        grade_latency(unvalidated(spec(1), clock="MONOTONIC"), [obs("c1")]).issues
        == ("clock_mismatch",),
    )
    check(
        CID,
        "unvalidated spec clock with a space",
        grade_latency(unvalidated(spec(1), clock="monotonic "), [obs("c1")]).issues
        == ("clock_mismatch",),
    )


def test_case_ids_differing_only_by_case_or_space_are_distinct() -> None:
    ids = ("c1", "C1", "c1 ")
    result = grade_latency(spec(expected_case_ids=ids), [obs(i) for i in ids])
    check(CID, "c1, C1 and 'c1 ' are three distinct cases", result.passed and result.issues == ())
    for blank in ("\t", "\n", " \t "):
        check(
            CID,
            f"whitespace-only {blank!r} case id refused",
            raises(lambda b=blank: grade_latency(spec(1), [obs(b)])),
        )


def test_text_and_name_validation_exact() -> None:
    check(CID, "whitespace-only workload refused", raises(lambda: spec(workload_id=" ")))
    check(
        CID,
        "whitespace-only expected case id refused",
        raises(lambda: spec(expected_case_ids=("a", "  "))),
    )
    check(
        CID,
        "whitespace-only observation case id refused",
        raises(lambda: grade_latency(spec(1), [obs(" ")])),
    )
    check(CID, "clock Wall (case) refused", raises(lambda: spec(clock="Wall")))
    check(CID, "clock with a space refused", raises(lambda: spec(clock="wall ")))
    check(CID, "metric AGENT_MS refused", raises(lambda: LatencyLimit("AGENT_MS", 95, 1)))
    check(CID, "metric with a space refused", raises(lambda: LatencyLimit("agent_ms ", 95, 1)))
    check(
        CID, "outcome OK refused", raises(lambda: grade_latency(spec(1), [obs("c1", outcome="OK")]))
    )
    check(
        CID,
        "outcome with a space refused",
        raises(lambda: grade_latency(spec(1), [obs("c1", outcome="ok ")])),
    )
    check(CID, "1e12 ms accepted as a limit", LatencyLimit("agent_ms", 95, 10**12).max_ms == 10**12)
    check(
        CID,
        "1e12+1 ms refused as a limit",
        raises(lambda: LatencyLimit("agent_ms", 95, 10**12 + 1)),
    )


def test_timeout_treatment_declared() -> None:
    check(CID, "timeout_ms is required positive", raises(lambda: spec(timeout_ms=0)))
    check(CID, "timeout_ms refuses a float", raises(lambda: spec(timeout_ms=10.5)))
    check(CID, "timeout_ms refuses a bool", raises(lambda: spec(timeout_ms=True)))
    slow = grade_latency(spec(1), [obs("c1", TIMEOUT + 1)])
    check(CID, "an ok case over the timeout is flagged", "ok_exceeds_timeout" in slow.issues)
    exact = grade_latency(
        spec(1, limits=(LatencyLimit("agent_ms", 95, TIMEOUT),)), [obs("c1", TIMEOUT)]
    )
    check(CID, "an ok case exactly at the timeout is not flagged", exact.passed)


def test_timeout_dropped_error_counted_not_removed() -> None:
    for outcome, code in (
        ("timeout", "timed_out_case"),
        ("dropped", "dropped_case"),
        ("error", "errored_case"),
    ):
        observations = good(3) + [
            obs("c4", 1, outcome=outcome, request_ms=1, queue_ms=1, ttft_ms=1)
        ]
        result = grade_latency(
            spec(4, limits=(LatencyLimit("agent_ms", 99, TIMEOUT),)), observations
        )
        check(CID, f"{outcome} fails with {code}", not result.passed and code in result.issues)
        check(
            CID,
            f"{outcome} counted at timeout_ms, not its reported 1ms",
            pct(result, "agent_ms", 99) == TIMEOUT,
        )
        check(CID, f"{outcome} counted once", result.counted_at_timeout == 1)
    for metric in ("request_ms", "queue_ms", "ttft_ms"):
        lim = (LatencyLimit(metric, 99, TIMEOUT),)
        result = grade_latency(
            spec(2, limits=lim),
            [
                obs("c1"),
                obs("c2", outcome="timeout", request_ms=0, queue_ms=0, ttft_ms=0, agent_ms=0),
            ],
        )
        check(
            CID,
            f"a timeout is counted at timeout_ms in {metric}",
            pct(result, metric, 99) == TIMEOUT,
        )


def test_missing_case_counted_not_removed() -> None:
    result = grade_latency(spec(4, limits=(LatencyLimit("agent_ms", 99, TIMEOUT),)), good(3))
    check(CID, "a missing expected case is missing_case", result.issues == ("missing_case",))
    check(CID, "a missing case is counted at timeout_ms", pct(result, "agent_ms", 99) == TIMEOUT)
    check(CID, "a missing case is counted once", result.counted_at_timeout == 1)
    wrong_case = grade_latency(spec(1), [obs("C1")])
    check(
        CID,
        "case ids compared case-exactly",
        {"missing_case", "unexpected_case"} <= set(wrong_case.issues),
    )
    spaced = grade_latency(spec(1), [obs("c1 ")])
    check(
        CID,
        "case ids compared whitespace-exactly",
        {"missing_case", "unexpected_case"} <= set(spaced.issues),
    )


def test_empty_observations_fail() -> None:
    for empty in ([], ()):
        result = grade_latency(spec(2), empty)
        check(CID, "no observations is never a vacuous pass", not result.passed)
        check(CID, "no observations reports every case missing", "missing_case" in result.issues)
        check(CID, "no observations counts all cases at timeout", result.counted_at_timeout == 2)
    check(CID, "empty expected ids refused at spec", raises(lambda: spec(0)))


def test_dropping_slow_cases_cannot_improve_p95() -> None:
    lim = (LatencyLimit("agent_ms", 95, 5_000),)
    full = good(20, 100)[:19] + [obs("c20", 9_000)]
    honest = grade_latency(spec(20, limits=lim), full)
    check(CID, "baseline p95 is the 19th value", pct(honest, "agent_ms", 95) == 100)
    dropped = grade_latency(spec(20, limits=lim), good(20, 100)[:19])
    check(
        CID,
        "dropping the slowest case fails the grade",
        not dropped.passed and "missing_case" in dropped.issues,
    )
    many = grade_latency(spec(20, limits=lim), good(20, 100)[:15])
    check(
        CID,
        "dropping many cases pushes p95 to timeout_ms",
        pct(many, "agent_ms", 95) == TIMEOUT and not many.passed,
    )
    timed = good(20, 100)[:15] + [obs(f"c{i}", 50, outcome="timeout") for i in range(16, 21)]
    result = grade_latency(spec(20, limits=lim), timed)
    check(CID, "timeouts cannot be hidden as fast", pct(result, "agent_ms", 95) == TIMEOUT)


def test_profiles_not_mixed() -> None:
    mixed = good(3) + [obs("c4", profile="cold")]
    result = grade_latency(spec(4), mixed)
    check(
        CID,
        "a cold case in a warm run is profile_mismatch",
        "profile_mismatch" in result.issues and not result.passed,
    )
    cold = grade_latency(spec(2, profile="cold"), good(2))
    check(CID, "warm observations against a cold spec fail", cold.issues == ("profile_mismatch",))
    check(CID, "spec rejects an unknown profile", raises(lambda: spec(profile="hot")))
    check(CID, "spec rejects Warm (case-exact)", raises(lambda: spec(profile="Warm")))


def test_duplicates_and_unexpected_preserved() -> None:
    result = grade_latency(
        spec(2, limits=(LatencyLimit("agent_ms", 99, TIMEOUT),)),
        [obs("c1", 100), obs("c1", 9_000), obs("c2", 100)],
    )
    check(
        CID,
        "a repeated case is duplicate_case",
        "duplicate_case" in result.issues and not result.passed,
    )
    check(CID, "every duplicate copy is counted", pct(result, "agent_ms", 99) == 9_000)
    extra = grade_latency(spec(1), [obs("c1"), obs("zz")])
    check(CID, "an unrequested case is unexpected_case", extra.issues == ("unexpected_case",))


def test_ok_observation_rules() -> None:
    missing = grade_latency(spec(1), [obs("c1", ttft_ms=None)])
    check(CID, "a missing metric is missing_metric", missing.issues == ("missing_metric",))
    check(
        CID,
        "a missing metric is counted at timeout_ms",
        pct(
            grade_latency(
                spec(1, limits=(LatencyLimit("ttft_ms", 50, TIMEOUT),)), [obs("c1", ttft_ms=None)]
            ),
            "ttft_ms",
            50,
        )
        == TIMEOUT,
    )
    below = grade_latency(spec(1), [obs("c1", 5, request_ms=50)])
    check(
        CID,
        "agent latency below request latency is flagged",
        below.issues == ("agent_below_request",),
    )
    equal = grade_latency(spec(1), [obs("c1", 50, request_ms=50)])
    check(CID, "agent equal to request is fine", equal.passed)
    for bad in (-1, 1.5, True, "10", float("nan")):
        check(
            CID,
            f"metric {bad!r} fails closed",
            raises(lambda b=bad: grade_latency(spec(1), [obs("c1", agent_ms=b)])),
        )


def test_multi_constraint_all_issues_reported_sorted() -> None:
    observations = [
        obs("c1", 9_000, profile="cold", clock="wall"),
        obs("c1", 100, workload_id="x"),
        obs("zz", 100, outcome="dropped"),
    ]
    result = grade_latency(spec(2, limits=(LatencyLimit("agent_ms", 50, 100),)), observations)
    expected = (
        "clock_mismatch",
        "dropped_case",
        "duplicate_case",
        "missing_case",
        "over_limit",
        "profile_mismatch",
        "unexpected_case",
        "workload_mismatch",
    )
    check(CID, "every applicable issue reported once in sorted order", result.issues == expected)


def test_multiple_limits_each_enforced() -> None:
    limits = (LatencyLimit("agent_ms", 95, 5_000), LatencyLimit("ttft_ms", 95, 15))
    result = grade_latency(spec(2, limits=limits), [obs("c1"), obs("c2")])
    check(
        CID, "ttft over its own limit fails though agent is fine", result.issues == ("over_limit",)
    )
    check(
        CID,
        "both limits are reported",
        {(m, p) for m, p, _ in result.percentiles} == {("agent_ms", 95), ("ttft_ms", 95)},
    )


def test_malformed_inputs_fail_closed() -> None:
    check(CID, "spec of the wrong type", raises(lambda: grade_latency({}, good(1))))
    check(CID, "observations None", raises(lambda: grade_latency(spec(1), None)))
    check(CID, "observations a string", raises(lambda: grade_latency(spec(1), "c1")))
    check(
        CID,
        "observation of the wrong type",
        raises(lambda: grade_latency(spec(1), [{"case_id": "c1"}])),
    )
    check(CID, "empty case id", raises(lambda: grade_latency(spec(1), [obs("")])))
    check(CID, "None case id", raises(lambda: grade_latency(spec(1), [obs(None)])))
    check(CID, "unknown outcome", raises(lambda: grade_latency(spec(1), [obs("c1", outcome="OK")])))
    check(CID, "None outcome", raises(lambda: grade_latency(spec(1), [obs("c1", outcome=None)])))
    check(CID, "duplicate expected ids", raises(lambda: spec(expected_case_ids=("a", "a"))))
    check(CID, "expected ids as a list", raises(lambda: spec(expected_case_ids=["a"])))
    check(CID, "empty expected id", raises(lambda: spec(expected_case_ids=("a", ""))))
    check(CID, "empty limits", raises(lambda: spec(limits=())))
    check(CID, "limits of the wrong type", raises(lambda: spec(limits=("p95",))))
    check(
        CID,
        "the same limit twice",
        raises(
            lambda: spec(limits=(LatencyLimit("agent_ms", 95, 1), LatencyLimit("agent_ms", 95, 2)))
        ),
    )
    check(CID, "unknown metric", raises(lambda: LatencyLimit("cost", 95, 1)))
    check(CID, "unknown percentile", raises(lambda: LatencyLimit("agent_ms", 90, 1)))
    check(CID, "percentile bool", raises(lambda: LatencyLimit("agent_ms", True, 1)))
    check(CID, "negative max_ms", raises(lambda: LatencyLimit("agent_ms", 95, -1)))
    check(CID, "float max_ms", raises(lambda: LatencyLimit("agent_ms", 95, 1.0)))


def test_models_frozen_and_error_type() -> None:
    def mutate(obj: Any, name: str, value: Any) -> None:
        setattr(obj, name, value)

    def frozen(fn: Any) -> bool:
        try:
            fn()
        except FrozenInstanceError:
            return True
        return False

    check(CID, "LatencySpec frozen", frozen(lambda: mutate(spec(), "timeout_ms", 1)))
    check(
        CID,
        "LatencyLimit frozen",
        frozen(lambda: mutate(LatencyLimit("agent_ms", 95, 1), "max_ms", 2)),
    )
    check(CID, "LatencyObservation frozen", frozen(lambda: mutate(obs("c1"), "agent_ms", 1)))
    check(
        CID,
        "LatencyResult frozen",
        frozen(lambda: mutate(grade_latency(spec(1), good(1)), "passed", False)),
    )
    check(CID, "LatencyGraderError is a ValueError", issubclass(LatencyGraderError, ValueError))
    check(CID, "replace() on a spec re-validates", raises(lambda: replace(spec(), timeout_ms=0)))


def test_round2_unhashable_outcome_fails_closed() -> None:
    for bad in ([], {}, ["ok"], {"ok": 1}, 1, b"ok"):
        check(
            CID,
            f"outcome {bad!r} raises the grader's own error",
            raises(lambda b=bad: grade_latency(spec(1), [obs("c1", outcome=b)])),
        )


def test_round2_agent_below_request_boundary() -> None:
    one_below = grade_latency(spec(1), [obs("c1", 49, request_ms=50)])
    check(
        CID,
        "agent one ms below request is flagged",
        one_below.issues == ("agent_below_request",),
    )
    check(
        CID,
        "agent one ms above request is fine",
        grade_latency(spec(1), [obs("c1", 51, request_ms=50)]).passed,
    )
    check(
        CID,
        "agent far below request is flagged",
        grade_latency(spec(1), [obs("c1", 10, request_ms=50)]).issues == ("agent_below_request",),
    )


def test_round2_ok_over_timeout_reported_unclamped() -> None:
    limits = (LatencyLimit("agent_ms", 95, TIMEOUT * 2),)
    result = grade_latency(spec(1, limits=limits), [obs("c1", TIMEOUT + 1)])
    check(
        CID,
        "an ok case over the timeout is flagged",
        result.issues == ("ok_exceeds_timeout",),
    )
    check(
        CID,
        "its percentile is the raw value, not clamped to the timeout",
        pct(result, "agent_ms", 95) == TIMEOUT + 1,
    )
    well_over = grade_latency(spec(1, limits=limits), [obs("c1", TIMEOUT * 2)])
    check(CID, "far over the timeout stays raw", pct(well_over, "agent_ms", 95) == TIMEOUT * 2)


def test_round2_percentile_independent_of_input_order() -> None:
    limits = tuple(LatencyLimit("agent_ms", p, 10_000) for p in (50, 95, 99))
    values = [900, 100, 500, 300, 700, 200, 800, 400, 600, 1000]
    forward = [obs(f"c{i}", v) for i, v in enumerate(values, 1)]
    result = grade_latency(spec(10, limits=limits), forward)
    check(CID, "shuffled p50 is the 5th smallest", pct(result, "agent_ms", 50) == 500)
    check(CID, "shuffled p95 is the max", pct(result, "agent_ms", 95) == 1000)
    reverse = grade_latency(spec(10, limits=limits), forward[::-1])
    check(
        CID, "reversed order gives the same percentiles", reverse.percentiles == result.percentiles
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
