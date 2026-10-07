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

# mypy: disable-error-code="arg-type,list-item,dict-item"
# (round-2 tests pass deliberately malformed input to prove the grader refuses it)
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
        refused(lambda: grade([TracedToolCall(5, "a")], must_not_call=["x"])),
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


# ---- round 2: exactness (no case / whitespace tolerance anywhere) -----------


def accepted(fn) -> bool:
    """True when `fn` runs without the grader refusing it."""
    try:
        fn()
    except ToolSequenceGraderError:
        return False
    return True


def test_forbidden_matching_is_exact_for_case_and_whitespace() -> None:
    for called in ("refund_order ", "Refund_Order", " refund_order", "REFUND_ORDER"):
        r = grade(trace("lookup", called), must_not_call=["refund_order"])
        check(
            "FTF-0608",
            f"{called!r} is not the forbidden tool 'refund_order'",
            r.passed and r.forbidden_calls == (),
        )
    r = grade(trace("refund_order"), must_not_call=["refund_order "])
    check("FTF-0608", "a forbidden name with trailing space does not match", r.passed)
    r = grade(trace("refund_order"), must_not_call=["refund_order"])
    check(
        "FTF-0608",
        "the exact forbidden name still fails",
        not r.passed and r.forbidden_calls == ("refund_order",),
    )


def test_allowed_order_forbidden_clash_check_is_exact() -> None:
    for order_name in ("refund_order ", "Refund_Order"):
        check(
            "FTF-0608",
            f"allowed order naming {order_name!r} does not clash with 'refund_order'",
            accepted(
                lambda n=order_name: grade(
                    trace("a"), allowed_orders=[["a", n]], must_not_call=["refund_order"]
                )
            ),
        )
    check(
        "FTF-0608",
        "an allowed order naming the exact forbidden tool is still refused",
        refused(
            lambda: grade(
                trace("a"), allowed_orders=[["a", "refund_order"]], must_not_call=["refund_order"]
            )
        ),
    )


def test_allowed_order_compare_is_exact() -> None:
    for called in ("Lookup", "lookup ", " lookup"):
        r = grade(trace(called, "refund"), allowed_orders=[["lookup", "refund"]])
        check(
            "FTF-0608",
            f"{called!r} does not match declared 'lookup' in an allowed order",
            not r.passed and not r.order_ok and r.matched_order_index is None,
        )


def test_precedence_names_are_exact() -> None:
    # `after` spelled differently from the trace: the pair is vacuous, never matched.
    r = grade(trace("Refund"), must_precede=[("approve", "refund")])
    check("FTF-0608", "'Refund' is not the after-name 'refund'", r.passed)
    r = grade(trace("refund "), must_precede=[("approve", "refund")])
    check("FTF-0608", "'refund ' is not the after-name 'refund'", r.passed)
    # `before` spelled differently: it does not satisfy the pair.
    r = grade(trace("Approve", "refund"), must_precede=[("approve", "refund")])
    check(
        "FTF-0608",
        "'Approve' does not satisfy before-name 'approve'",
        not r.passed and r.precedence_violations == (("approve", "refund"),),
    )
    r = grade(trace("approve ", "refund"), must_precede=[("approve", "refund")])
    check("FTF-0608", "'approve ' does not satisfy before-name 'approve'", not r.passed)
    # Declared names with different case/space than the trace.
    r = grade(trace("approve", "refund"), must_precede=[("Approve", "refund")])
    check("FTF-0608", "declared before 'Approve' is not satisfied by 'approve'", not r.passed)
    r = grade(trace("approve", "refund"), must_precede=[("approve", "Refund")])
    check("FTF-0608", "declared after 'Refund' never matches trace 'refund'", r.passed)


def test_state_keys_are_exact() -> None:
    for required_key, final_key in (
        ("Status", "status"),
        ("status ", "status"),
        ("status", "Status"),
    ):
        r = grade(
            trace("a"),
            required_state={required_key: "ok"},
            final_state={final_key: "ok"},
        )
        check(
            "FTF-0608",
            f"required key {required_key!r} is not satisfied by final key {final_key!r}",
            not r.passed and r.state_mismatches == (required_key,),
        )


def test_tool_call_id_identity_is_exact() -> None:
    calls = [TracedToolCall("c0", "a"), TracedToolCall("C0", "b"), TracedToolCall("c0 ", "c")]
    r = grade(calls, allowed_orders=[["a", "b", "c"]])
    check("FTF-0608", "'c0', 'C0' and 'c0 ' are three distinct call identities", r.passed)
    check(
        "FTF-0608",
        "the identical id twice is still refused",
        refused(
            lambda: grade(
                [TracedToolCall("c0", "a"), TracedToolCall("c0", "b")], must_not_call=["z"]
            )
        ),
    )


def test_called_names_preserved_verbatim() -> None:
    names = ("Lookup", "lookup ", " lookup", "LOOKUP", "lookup")
    r = grade(trace(*names), must_not_call=["zzz"])
    check(
        "FTF-0608",
        "called_names keeps case, whitespace and duplicates verbatim",
        r.called_names == names,
    )


# ---- round 2: malformed input fails closed ---------------------------------


def test_round2_malformed_trace_entries_are_refused() -> None:
    check(
        "FTF-0608",
        "int name refused",
        refused(lambda: grade([TracedToolCall("c0", 123)], must_not_call=["x"])),
    )
    check(
        "FTF-0608",
        "list name refused",
        refused(lambda: grade([TracedToolCall("c0", ["a"])], must_not_call=["x"])),
    )
    check(
        "FTF-0608",
        "None name refused",
        refused(lambda: grade([TracedToolCall("c0", None)], must_not_call=["x"])),
    )
    check(
        "FTF-0608",
        "empty tool_call_id refused",
        refused(lambda: grade([TracedToolCall("", "a")], must_not_call=["x"])),
    )
    check(
        "FTF-0608",
        "int tool_call_id refused",
        refused(lambda: grade([TracedToolCall(7, "a")], must_not_call=["x"])),
    )
    check(
        "FTF-0608",
        "empty-string trace refused",
        refused(
            lambda: grade_tool_sequence(
                "",
                allowed_orders=[],
                must_precede=[],
                must_not_call=["x"],
                required_state={},
                final_state={},
            )
        ),
    )
    check(
        "FTF-0608",
        "empty-bytes trace refused",
        refused(
            lambda: grade_tool_sequence(
                b"",
                allowed_orders=[],
                must_precede=[],
                must_not_call=["x"],
                required_state={},
                final_state={},
            )
        ),
    )
    check(
        "FTF-0608",
        "None trace refused",
        refused(
            lambda: grade_tool_sequence(
                None,
                allowed_orders=[],
                must_precede=[],
                must_not_call=["x"],
                required_state={},
                final_state={},
            )
        ),
    )


def test_round2_malformed_specification_items_are_refused() -> None:
    t = trace("a")
    check(
        "FTF-0608",
        "empty-string must_not_call item refused",
        refused(lambda: grade(t, must_not_call=[""])),
    )
    check(
        "FTF-0608",
        "non-str must_not_call item refused",
        refused(lambda: grade(t, must_not_call=[1])),
    )
    check(
        "FTF-0608",
        "bare-str must_not_call refused",
        refused(lambda: grade(t, must_not_call="refund")),
    )
    check(
        "FTF-0608",
        "empty-string item in an allowed order refused",
        refused(lambda: grade(t, allowed_orders=[[""]])),
    )
    check(
        "FTF-0608",
        "empty-string item beside a name refused",
        refused(lambda: grade(t, allowed_orders=[["a", ""]])),
    )
    check("FTF-0608", "empty allowed order refused", refused(lambda: grade(t, allowed_orders=[[]])))
    check(
        "FTF-0608",
        "bare-str allowed order refused",
        refused(lambda: grade(t, allowed_orders=["abc"])),
    )
    check(
        "FTF-0608",
        "empty-string before refused",
        refused(lambda: grade(t, must_precede=[("", "b")])),
    )
    check(
        "FTF-0608",
        "empty-string after refused",
        refused(lambda: grade(t, must_precede=[("a", "")])),
    )
    check("FTF-0608", "1-tuple pair refused", refused(lambda: grade(t, must_precede=[("a",)])))
    check(
        "FTF-0608",
        "3-tuple pair refused",
        refused(lambda: grade(t, must_precede=[("a", "b", "c")])),
    )
    check(
        "FTF-0608", "non-str pair names refused", refused(lambda: grade(t, must_precede=[(1, 2)]))
    )
    check("FTF-0608", "self-pair refused", refused(lambda: grade(t, must_precede=[("a", "a")])))
    check("FTF-0608", "bare-str pair refused", refused(lambda: grade(t, must_precede=["ab"])))
    check(
        "FTF-0608",
        "empty required_state key refused",
        refused(lambda: grade(t, required_state={"": 1}, final_state={"": 1})),
    )
    check(
        "FTF-0608",
        "non-str required_state key refused",
        refused(lambda: grade(t, required_state={1: 1}, final_state={1: 1})),
    )
    check(
        "FTF-0608",
        "non-mapping final_state refused",
        refused(lambda: grade(t, required_state={"a": 1}, final_state=[("a", 1)])),
    )


# ---- round 2: semantics -----------------------------------------------------


def test_empty_trace_semantics() -> None:
    r = grade([], allowed_orders=[["a"]])
    check(
        "FTF-0608",
        "an empty trace fails a declared order",
        not r.passed and not r.order_ok and r.called_names == (),
    )
    r = grade([], must_not_call=["refund"])
    check("FTF-0608", "an empty trace passes must_not_call", r.passed and r.forbidden_calls == ())
    r = grade([], must_precede=[("a", "b")])
    check("FTF-0608", "an empty trace satisfies precedence vacuously", r.passed)
    r = grade([], required_state={"k": 1}, final_state={})
    check(
        "FTF-0608",
        "an empty trace still fails a missing required state",
        not r.passed and r.state_mismatches == ("k",),
    )


def test_duplicate_forbidden_calls_are_all_reported() -> None:
    r = grade(trace("x", "y", "x", "x"), must_not_call=["x", "y"])
    check(
        "FTF-0608",
        "every forbidden call is reported, duplicates included and in trace order",
        not r.passed and r.forbidden_calls == ("x", "y", "x", "x"),
    )


def test_every_precedence_pair_is_checked() -> None:
    pairs = [("a", "b"), ("c", "d")]
    r = grade(trace("a", "b", "d", "c"), must_precede=pairs)
    check(
        "FTF-0608",
        "first pair satisfied, second violated -> only second reported",
        not r.passed and r.precedence_violations == (("c", "d"),),
    )
    r = grade(trace("b", "a", "c", "d"), must_precede=pairs)
    check(
        "FTF-0608",
        "first pair violated, second satisfied -> only first reported",
        not r.passed and r.precedence_violations == (("a", "b"),),
    )
    r = grade(trace("d", "b"), must_precede=pairs)
    check(
        "FTF-0608",
        "both violated -> both reported in declared order",
        r.precedence_violations == (("a", "b"), ("c", "d")),
    )
    r = grade(trace("a", "c", "b", "d"), must_precede=pairs)
    check("FTF-0608", "both satisfied passes", r.passed and r.precedence_violations == ())


def test_nested_state_semantics() -> None:
    r = grade(trace("a"), required_state={"o": {"a": 1, "b": 2}}, final_state={"o": {"a": 1}})
    check(
        "FTF-0608",
        "a nested mapping missing a key fails",
        not r.passed and r.state_mismatches == ("o",),
    )
    r = grade(trace("a"), required_state={"o": {"a": 1}}, final_state={"o": {"a": 1, "b": 2}})
    check("FTF-0608", "a nested mapping with an extra key fails", not r.passed)
    r = grade(trace("a"), required_state={"o": {"a": 1}}, final_state={"o": {"A": 1}})
    check("FTF-0608", "nested keys are case-exact", not r.passed)
    r = grade(trace("a"), required_state={"o": [1, 2, 3]}, final_state={"o": [1, 2]})
    check("FTF-0608", "a nested list length mismatch fails", not r.passed)
    r = grade(trace("a"), required_state={"o": [1, 2]}, final_state={"o": [1, 2, 3]})
    check("FTF-0608", "a longer nested list fails", not r.passed)
    r = grade(
        trace("a"),
        required_state={"o": {"a": [1, {"b": 2}]}},
        final_state={"o": {"a": [1, {"b": 2}]}},
    )
    check("FTF-0608", "deeply nested identical state passes", r.passed)
    r = grade(
        trace("a"),
        required_state={"o": {"a": [1, {"b": 2}]}},
        final_state={"o": {"a": [1, {"b": 2.0}]}},
    )
    check("FTF-0608", "deeply nested int-vs-float fails", not r.passed)


# ---- round-3 follow-up FTF-0608-F1 ------------------------------------


def test_f1_strict_prefix_and_extension_traces_fail_the_declared_order() -> None:
    short = grade(trace("a", "b"), allowed_orders=[["a", "b", "c"]])
    check(
        "FTF-0608-F1",
        "a trace that is a strict prefix of the declared order fails",
        not short.passed and not short.order_ok and short.matched_order_index is None,
    )
    longer = grade(trace("a", "b", "c"), allowed_orders=[["a", "b"]])
    check(
        "FTF-0608-F1",
        "a trace that strictly extends the declared order fails",
        not longer.passed and not longer.order_ok and longer.matched_order_index is None,
    )
    suffix = grade(trace("b", "c"), allowed_orders=[["a", "b", "c"]])
    check("FTF-0608-F1", "a strict suffix of the declared order fails", not suffix.passed)


def test_f1_bare_str_specs_are_refused() -> None:
    check(
        "FTF-0608-F1",
        "allowed_orders given as a bare str is refused",
        refused(lambda: grade(trace("ab"), allowed_orders="ab")),
    )
    check(
        "FTF-0608-F1",
        "an allowed order given as a bare str is refused, never split into characters",
        refused(lambda: grade(trace("a", "b"), allowed_orders=["ab"])),
    )
    check(
        "FTF-0608-F1",
        "must_precede given as a bare str is refused",
        refused(lambda: grade(trace("a", "b"), must_precede="ab")),
    )
    check(
        "FTF-0608-F1",
        "a must_precede pair given as a bare str is refused, never split into characters",
        refused(lambda: grade(trace("a", "b"), must_precede=["ab"])),
    )
    check(
        "FTF-0608-F1",
        "must_not_call given as a bare str is refused",
        refused(lambda: grade(trace("a"), must_not_call="ab")),
    )


def test_f1_duplicate_tool_call_id_detection_is_case_exact_in_both_directions() -> None:
    for first, second in (("C0", "c0"), ("c0", "C0")):
        calls = [
            TracedToolCall(tool_call_id=first, name="a"),
            TracedToolCall(tool_call_id=second, name="b"),
        ]
        r = grade(calls, allowed_orders=[["a", "b"]])
        check("FTF-0608-F1", f"ids {first!r},{second!r} are distinct and accepted", r.passed)
    same = [
        TracedToolCall(tool_call_id="c0", name="a"),
        TracedToolCall(tool_call_id="c0", name="b"),
    ]
    check(
        "FTF-0608-F1",
        "an identical repeated id is still refused",
        refused(lambda: grade(same, allowed_orders=[["a", "b"]])),
    )


def test_f1_pair_names_differing_only_by_case_are_distinct() -> None:
    ok = grade(trace("a", "A"), must_precede=[("a", "A")])
    check("FTF-0608-F1", "pair ('a','A') is accepted as two distinct names and passes", ok.passed)
    bad = grade(trace("A", "a"), must_precede=[("a", "A")])
    check(
        "FTF-0608-F1",
        "pair ('a','A') fails when 'A' is called before 'a'",
        not bad.passed and bad.precedence_violations == (("a", "A"),),
    )
    check(
        "FTF-0608-F1",
        "an identical-name pair is still refused",
        refused(lambda: grade(trace("a"), must_precede=[("a", "a")])),
    )


def test_f1_first_matching_order_index_wins() -> None:
    r = grade(trace("a", "b"), allowed_orders=[["a", "b"], ["a", "b"]])
    check("FTF-0608-F1", "duplicate matching orders report index 0", r.matched_order_index == 0)
    r = grade(trace("a", "b"), allowed_orders=[["b", "a"], ["a", "b"], ["a", "b"]])
    check("FTF-0608-F1", "the first matching order (index 1) wins", r.matched_order_index == 1)


def test_f1_violations_and_mismatches_keep_declared_and_call_order() -> None:
    pairs = [("x", "y"), ("p", "q")]
    r = grade(trace("y", "q"), must_precede=pairs)
    check(
        "FTF-0608-F1",
        "precedence violations are reported in declared pair order",
        r.precedence_violations == (("x", "y"), ("p", "q")),
    )
    r = grade(trace("y", "q"), must_precede=list(reversed(pairs)))
    check(
        "FTF-0608-F1",
        "reversing the declared pairs reverses the reported violations",
        r.precedence_violations == (("p", "q"), ("x", "y")),
    )
    r = grade(trace("c", "a", "b", "c"), must_not_call=["a", "c"])
    check(
        "FTF-0608-F1",
        "forbidden calls are reported in call order, duplicates preserved",
        r.forbidden_calls == ("c", "a", "c"),
    )
    r = grade(
        trace("a"),
        required_state={"k2": 1, "k1": 1, "k3": 1},
        final_state={"k1": 2, "k3": 1},
    )
    check(
        "FTF-0608-F1",
        "state mismatches are reported in required_state order (missing key included)",
        r.state_mismatches == ("k2", "k1"),
    )
    r = grade(
        trace("a"),
        required_state={"k1": 1, "k2": 1},
        final_state={"k1": 2, "k2": 2},
    )
    check(
        "FTF-0608-F1",
        "mismatch order follows required_state order",
        r.state_mismatches == ("k1", "k2"),
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
