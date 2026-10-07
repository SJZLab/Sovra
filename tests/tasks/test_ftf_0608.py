#!/usr/bin/env python3
"""FTF-0608 -- Tool-sequence grader.

Runnable without any database or network: `grade_tool_sequence` is a pure,
synchronous function (this task's `database_changes`: no schema change).

Acceptance ("Support declared sequence/partial-order/state constraints;
inspect actual trace and outcome"):
  sequence      -- test_exact_declared_order_passes, test_alternative_declared_order_passes,
                   test_missing_call_fails, test_reordered_call_fails
  partial order -- test_precedence_satisfied_passes, test_precedence_violated_fails,
                   test_precedence_vacuous_when_after_never_called,
                   test_precedence_uses_first_call_of_after
  state/outcome -- test_state_match_passes, test_state_missing_key_fails,
                   test_state_value_mismatch_fails, test_state_is_type_strict,
                   test_state_nested_is_type_strict
  actual trace  -- test_rejects_mapping_in_trace, test_rejects_string_trace,
                   test_rejects_empty_name, test_rejects_duplicate_call_id,
                   test_result_reports_actual_trace

Negative acceptance ("Alternative valid orders only accepted when predefined;
extra forbidden tool call always fails"):
  test_undeclared_alternative_order_fails (real default code, attack input),
  test_extra_call_beyond_declared_order_fails,
  test_forbidden_call_fails_even_when_order_matches_other_constraints,
  test_forbidden_call_fails_with_only_forbidden_constraint,
  test_forbidden_call_fails_alongside_precedence_and_state_ok,
  test_allowed_order_containing_forbidden_name_is_refused,
  test_no_substring_matching, test_empty_specification_is_refused,
  test_malformed_specification_is_refused.

Run directly: python3 tests/tasks/test_ftf_0608.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from workers.evaluation.tool_sequence_grader import (  # noqa: E402
    ToolSequenceGraderError,
    TracedToolCall,
    grade_tool_sequence,
)

FAILURES: list[str] = []


def check(criterion_id: str, description: str, condition: bool) -> None:
    if not condition:
        FAILURES.append(f"FAIL [{criterion_id}] {description}")


def trace(*names: str) -> list[TracedToolCall]:
    return [TracedToolCall(tool_call_id=f"c{i}", name=n) for i, n in enumerate(names)]


def grade(calls, **overrides):
    spec = {
        "allowed_orders": [],
        "must_precede": [],
        "must_not_call": [],
        "required_state": {},
        "final_state": {},
    }
    spec.update(overrides)
    return grade_tool_sequence(calls, **spec)


def refused(fn) -> bool:
    try:
        fn()
    except ToolSequenceGraderError:
        return True
    return False


# ---- sequence ---------------------------------------------------------


def test_exact_declared_order_passes() -> None:
    r = grade(
        trace("lookup", "approve", "refund"), allowed_orders=[["lookup", "approve", "refund"]]
    )
    check("FTF-0608", "the exact declared order passes", r.passed and r.matched_order_index == 0)


def test_alternative_declared_order_passes() -> None:
    orders = [["lookup", "approve", "refund"], ["approve", "lookup", "refund"]]
    r = grade(trace("approve", "lookup", "refund"), allowed_orders=orders)
    check(
        "FTF-0608",
        "a predefined alternative order passes and reports which one matched",
        r.passed and r.matched_order_index == 1,
    )


def test_missing_call_fails() -> None:
    r = grade(trace("lookup", "refund"), allowed_orders=[["lookup", "approve", "refund"]])
    check("FTF-0608", "a missing call fails the declared order", not r.passed and not r.order_ok)


def test_reordered_call_fails() -> None:
    r = grade(
        trace("approve", "lookup", "refund"), allowed_orders=[["lookup", "approve", "refund"]]
    )
    check("FTF-0608", "a reordered trace fails", not r.passed)


# ---- partial order ----------------------------------------------------


def test_precedence_satisfied_passes() -> None:
    r = grade(trace("x", "approve", "y", "refund"), must_precede=[("approve", "refund")])
    check("FTF-0608", "before-call earlier than after-call passes", r.passed)


def test_precedence_violated_fails() -> None:
    r = grade(trace("refund", "approve"), must_precede=[("approve", "refund")])
    check(
        "FTF-0608",
        "after-call preceding the before-call fails and reports the pair",
        not r.passed and r.precedence_violations == (("approve", "refund"),),
    )
    r2 = grade(trace("refund"), must_precede=[("approve", "refund")])
    check("FTF-0608", "after-call with no before-call at all fails", not r2.passed)


def test_precedence_vacuous_when_after_never_called() -> None:
    r = grade(trace("lookup"), must_precede=[("approve", "refund")])
    check("FTF-0608", "a pair whose after-call never happens is satisfied", r.passed)


def test_precedence_uses_first_call_of_after() -> None:
    r = grade(trace("refund", "approve", "refund"), must_precede=[("approve", "refund")])
    check(
        "FTF-0608", "the FIRST after-call must be preceded, a later one is not enough", not r.passed
    )


# ---- state / outcome --------------------------------------------------


def test_state_match_passes() -> None:
    r = grade(
        trace("a"),
        required_state={"refunded": True, "amount": 5},
        final_state={"refunded": True, "amount": 5, "extra": "ignored"},
    )
    check("FTF-0608", "matching outcome state passes", r.passed)


def test_state_missing_key_fails() -> None:
    r = grade(trace("a"), required_state={"refunded": True}, final_state={})
    check(
        "FTF-0608",
        "a required key absent from the outcome fails",
        r.state_mismatches == ("refunded",),
    )


def test_state_value_mismatch_fails() -> None:
    r = grade(trace("a"), required_state={"refunded": True}, final_state={"refunded": False})
    check("FTF-0608", "a wrong outcome value fails", not r.passed)


def test_state_is_type_strict() -> None:
    for expected, actual in ((True, 1), (1, 1.0), (1, True), ("1", 1), (0, False)):
        r = grade(trace("a"), required_state={"k": expected}, final_state={"k": actual})
        check("FTF-0608", f"{expected!r} must not equal {actual!r} in outcome state", not r.passed)


def test_state_nested_is_type_strict() -> None:
    r = grade(trace("a"), required_state={"k": {"n": [1, True]}}, final_state={"k": {"n": [1, 1]}})
    check("FTF-0608", "nested bool/int confusion fails", not r.passed)
    r2 = grade(
        trace("a"), required_state={"k": {"n": [1, True]}}, final_state={"k": {"n": [1, True]}}
    )
    check("FTF-0608", "an identical nested value passes", r2.passed)
    r3 = grade(trace("a"), required_state={"k": {"n": 1}}, final_state={"k": {"n": 1, "m": 2}})
    check("FTF-0608", "a nested mapping with extra keys is not equal", not r3.passed)


# ---- actual trace -----------------------------------------------------


def test_rejects_mapping_in_trace() -> None:
    check(
        "FTF-0608",
        "a dict masquerading as a call is refused",
        refused(lambda: grade([{"name": "lookup"}], must_not_call=["x"])),
    )


def test_rejects_string_trace() -> None:
    check(
        "FTF-0608",
        "a bare string trace is refused",
        refused(lambda: grade("lookup", must_not_call=["x"])),
    )
    check(
        "FTF-0608",
        "a non-sequence trace is refused",
        refused(lambda: grade(None, must_not_call=["x"])),
    )


def test_rejects_empty_name() -> None:
    check(
        "FTF-0608",
        "a call with an empty name is refused",
        refused(lambda: grade([TracedToolCall("c0", "")], must_not_call=["x"])),
    )


def test_rejects_duplicate_call_id() -> None:
    dup = [TracedToolCall("c0", "a"), TracedToolCall("c0", "b")]
    check(
        "FTF-0608",
        "a repeated tool_call_id is refused",
        refused(lambda: grade(dup, must_not_call=["x"])),
    )


def test_result_reports_actual_trace() -> None:
    r = grade(trace("a", "b", "a"), must_not_call=["z"])
    check(
        "FTF-0608",
        "the result reports the real call names in order",
        r.called_names == ("a", "b", "a"),
    )


# ---- negative acceptance ----------------------------------------------


def test_undeclared_alternative_order_fails() -> None:
    # Attack: a plausible equivalent order that nobody declared.
    r = grade(
        trace("approve", "lookup", "refund"), allowed_orders=[["lookup", "approve", "refund"]]
    )
    check("FTF-0608", "an undeclared alternative order is never accepted", not r.passed)
    r2 = grade(
        trace("lookup", "lookup", "approve", "refund"),
        allowed_orders=[["lookup", "approve", "refund"]],
    )
    check("FTF-0608", "a repeated call is not an alternative order", not r2.passed)


def test_extra_call_beyond_declared_order_fails() -> None:
    r = grade(
        trace("lookup", "approve", "refund", "notify"),
        allowed_orders=[["lookup", "approve", "refund"]],
    )
    check("FTF-0608", "an extra trailing call fails the declared order", not r.passed)


def test_forbidden_call_fails_even_when_order_matches_other_constraints() -> None:
    r = grade(
        trace("lookup", "wipe", "approve", "refund"),
        must_precede=[("approve", "refund")],
        must_not_call=["wipe"],
        required_state={"refunded": True},
        final_state={"refunded": True},
    )
    check(
        "FTF-0608",
        "a forbidden call fails even though precedence and state both hold",
        not r.passed and r.forbidden_calls == ("wipe",) and not r.precedence_violations,
    )


def test_forbidden_call_fails_with_only_forbidden_constraint() -> None:
    r = grade(trace("a", "wipe"), must_not_call=["wipe"])
    check("FTF-0608", "a forbidden call fails with no other constraint", not r.passed)
    check("FTF-0608", "no forbidden call passes", grade(trace("a"), must_not_call=["wipe"]).passed)


def test_forbidden_call_fails_alongside_precedence_and_state_ok() -> None:
    r = grade(
        trace("approve", "refund", "wipe"),
        allowed_orders=[["approve", "refund"]],
        must_not_call=["wipe"],
    )
    check(
        "FTF-0608",
        "forbidden extra call fails on both order and forbidden axes",
        r.forbidden_calls == ("wipe",) and not r.passed,
    )


def test_allowed_order_containing_forbidden_name_is_refused() -> None:
    check(
        "FTF-0608",
        "a contradictory contract is refused, never graded",
        refused(lambda: grade(trace("wipe"), allowed_orders=[["wipe"]], must_not_call=["wipe"])),
    )


def test_no_substring_matching() -> None:
    r = grade(trace("refund_order_v2"), must_not_call=["refund_order"])
    check("FTF-0608", "a forbidden name is not substring-matched", r.passed)
    r2 = grade(trace("approve_v2", "refund"), must_precede=[("approve", "refund")])
    check("FTF-0608", "a before-name is not substring-matched", not r2.passed)
    r3 = grade(trace("Refund"), allowed_orders=[["refund"]])
    check("FTF-0608", "names are case-sensitive", not r3.passed)


def test_empty_specification_is_refused() -> None:
    check(
        "FTF-0608",
        "a spec with no constraint is refused, not vacuously passed",
        refused(lambda: grade(trace("a"))),
    )


def test_malformed_specification_is_refused() -> None:
    check(
        "FTF-0608",
        "bare-string allowed_orders refused",
        refused(lambda: grade(trace("a"), allowed_orders="abc")),
    )
    check(
        "FTF-0608",
        "bare-string order refused",
        refused(lambda: grade(trace("a"), allowed_orders=["abc"])),
    )
    check(
        "FTF-0608", "empty order refused", refused(lambda: grade(trace("a"), allowed_orders=[[]]))
    )
    check(
        "FTF-0608", "non-string name refused", refused(lambda: grade(trace("a"), must_not_call=[1]))
    )
    check(
        "FTF-0608",
        "bare-string must_not_call refused",
        refused(lambda: grade(trace("a"), must_not_call="wipe")),
    )
    check(
        "FTF-0608",
        "self-pair refused",
        refused(lambda: grade(trace("a"), must_precede=[("a", "a")])),
    )
    check(
        "FTF-0608",
        "3-tuple pair refused",
        refused(lambda: grade(trace("a"), must_precede=[("a", "b", "c")])),
    )
    check(
        "FTF-0608",
        "bare-string pair list refused",
        refused(lambda: grade(trace("a"), must_precede="ab")),
    )
    check(
        "FTF-0608",
        "non-mapping state refused",
        refused(lambda: grade(trace("a"), required_state=[("k", 1)])),
    )
    check(
        "FTF-0608",
        "non-mapping final_state refused",
        refused(lambda: grade(trace("a"), must_not_call=["x"], final_state=None)),
    )
    check(
        "FTF-0608",
        "empty state key refused",
        refused(lambda: grade(trace("a"), required_state={"": 1})),
    )


def test_rejects_non_string_call_id_and_non_sequence_specs() -> None:
    check(
        "FTF-0608",
        "a non-string tool_call_id is refused",
        refused(lambda: grade([TracedToolCall(5, "a")], must_not_call=["x"])),  # type: ignore[arg-type]
    )
    check(
        "FTF-0608",
        "None allowed_orders refused",
        refused(lambda: grade(trace("a"), allowed_orders=None)),
    )
    check(
        "FTF-0608",
        "int allowed_orders refused",
        refused(lambda: grade(trace("a"), allowed_orders=5)),
    )
    check(
        "FTF-0608",
        "None must_precede refused",
        refused(lambda: grade(trace("a"), must_precede=None)),
    )
    check(
        "FTF-0608", "int must_precede refused", refused(lambda: grade(trace("a"), must_precede=5))
    )


def main() -> None:
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
            except Exception as exc:  # noqa: BLE001 -- isolate one test's crash from the rest
                FAILURES.append(
                    f"FAIL [FTF-0608] {name} raised an unexpected {type(exc).__name__}: {exc}"
                )

    if FAILURES:
        print(f"FTF-0608 tool-sequence grader: {len(FAILURES)} failed")
        for failure in FAILURES:
            print(f"  {failure}")
        sys.exit(1)

    print("FTF-0608 tool-sequence grader: all checks passed")
    sys.exit(0)


if __name__ == "__main__":
    main()
