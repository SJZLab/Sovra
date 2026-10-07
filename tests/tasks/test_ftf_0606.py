#!/usr/bin/env python3
"""FTF-0606 -- Tool-name grader.

Runnable without any database and without any external network egress:
`grade_tool_name` is a pure, synchronous, no-I/O function (this task's own
`database_changes` clause -- "No independent schema change. Consume frozen
contract."). Section 23's own grader-type list names `tool_name` as one of
the deterministic grader types, standing alone from the other sibling
grader types (exact_match/json_schema/tool_arguments/tool_sequence/
forbidden_action/required_refusal/numeric_tolerance/latency/cost/
llm_judge), each its own separately dependency-gated task on FTF-0601.

This task's own acceptance clause ("Tool-name grader verifies emitted tool
protocol and allowed/forbidden sets") is covered by
`test_required_call_satisfied_by_one_of_several_calls`,
`test_required_call_missing_fails`,
`test_no_required_call_specified_only_forbidden_checked`,
`test_forbidden_call_fails_even_when_required_call_also_made`,
`test_empty_emitted_calls_with_required_set_fails`,
`test_empty_emitted_calls_with_no_requirement_passes`,
`test_result_reports_called_names_and_forbidden_calls`,
`test_multiple_forbidden_calls_all_reported`, and
`test_policy_is_explicit_not_defaulted`.

This task's own negative_acceptance ("Mention of tool in natural language is
not an executed valid tool call") is covered by
`test_fuzzy_bypass_attack_natural_language_mention_never_counted_as_a_call`
(a real negative test against the shipped default code, not a
flipped-guard test), `test_rejects_mapping_in_emitted_tool_calls`,
`test_rejects_emitted_tool_calls_as_bare_string`,
`test_rejects_emitted_tool_calls_as_empty_string`,
`test_rejects_non_sequence_emitted_tool_calls`, plus the no-fuzzy-matching
guards: `test_name_with_version_suffix_does_not_satisfy_requirement`,
`test_forbidden_name_as_substring_of_called_name_does_not_trigger`,
`test_required_name_as_substring_of_called_name_does_not_satisfy`, and
the structural/input-validation guards:
`test_rejects_empty_name_tool_call`,
`test_rejects_non_string_in_must_call_one_of`,
`test_rejects_non_string_in_must_not_call`,
`test_rejects_empty_string_in_must_call_one_of`,
`test_rejects_non_sequence_must_call_one_of`,
`test_rejects_must_call_one_of_as_bare_string`.

Run directly: python3 tests/tasks/test_ftf_0606.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from workers.evaluation.tool_name_grader import (  # noqa: E402
    EmittedToolCall,
    ToolNameGraderError,
    grade_tool_name,
)

FAILURES: list[str] = []


def check(criterion_id: str, description: str, condition: bool) -> None:
    if not condition:
        FAILURES.append(f"FAIL [{criterion_id}] {description}")


def test_required_call_satisfied_by_one_of_several_calls() -> None:
    calls = [
        EmittedToolCall(tool_call_id="1", name="lookup_order"),
        EmittedToolCall(tool_call_id="2", name="request_approval"),
    ]
    result = grade_tool_name(
        calls,
        must_call_one_of=["request_approval", "ask_user"],
        must_not_call=["refund_order"],
    )
    check(
        "FTF-0606",
        "calling one of several required-one-of names, with no forbidden " "call, passes",
        result.passed,
    )


def test_required_call_missing_fails() -> None:
    calls = [EmittedToolCall(tool_call_id="1", name="lookup_order")]
    result = grade_tool_name(
        calls,
        must_call_one_of=["request_approval", "ask_user"],
        must_not_call=["refund_order"],
    )
    check(
        "FTF-0606",
        "not calling any of the required-one-of names fails, and reports "
        "satisfied_required_one_of=False",
        not result.passed and not result.satisfied_required_one_of,
    )


def test_no_required_call_specified_only_forbidden_checked() -> None:
    calls = [EmittedToolCall(tool_call_id="1", name="lookup_order")]
    result = grade_tool_name(
        calls,
        must_call_one_of=[],
        must_not_call=["refund_order"],
    )
    check(
        "FTF-0606",
        "an empty must_call_one_of means no required call -- any "
        "non-forbidden call sequence passes",
        result.passed and result.satisfied_required_one_of,
    )


def test_forbidden_call_fails_even_when_required_call_also_made() -> None:
    calls = [
        EmittedToolCall(tool_call_id="1", name="refund_order"),
        EmittedToolCall(tool_call_id="2", name="request_approval"),
    ]
    result = grade_tool_name(
        calls,
        must_call_one_of=["request_approval"],
        must_not_call=["refund_order"],
    )
    check(
        "FTF-0606",
        "a forbidden call fails the grade even when a required call was "
        "also made in the same sequence",
        not result.passed and result.forbidden_calls == ("refund_order",),
    )


def test_empty_emitted_calls_with_required_set_fails() -> None:
    result = grade_tool_name(
        [],
        must_call_one_of=["request_approval"],
        must_not_call=[],
    )
    check(
        "FTF-0606",
        "emitting no tool calls at all fails when a required call exists",
        not result.passed,
    )


def test_empty_emitted_calls_with_no_requirement_passes() -> None:
    result = grade_tool_name(
        [],
        must_call_one_of=[],
        must_not_call=["refund_order"],
    )
    check(
        "FTF-0606",
        "emitting no tool calls passes when there is no required call and "
        "nothing forbidden was called",
        result.passed,
    )


def test_result_reports_called_names_and_forbidden_calls() -> None:
    calls = [
        EmittedToolCall(tool_call_id="1", name="lookup_order"),
        EmittedToolCall(tool_call_id="2", name="refund_order"),
    ]
    result = grade_tool_name(
        calls,
        must_call_one_of=[],
        must_not_call=["refund_order"],
    )
    check(
        "FTF-0606",
        "the result echoes every called name and the real "
        "must_call_one_of/must_not_call sets used, not placeholders",
        result.called_names == ("lookup_order", "refund_order")
        and result.must_call_one_of == ()
        and result.must_not_call == ("refund_order",),
    )


def test_multiple_forbidden_calls_all_reported() -> None:
    calls = [
        EmittedToolCall(tool_call_id="1", name="refund_order"),
        EmittedToolCall(tool_call_id="2", name="delete_account"),
    ]
    result = grade_tool_name(
        calls,
        must_call_one_of=[],
        must_not_call=["refund_order", "delete_account"],
    )
    check(
        "FTF-0606",
        "every forbidden call in the sequence is reported, in order, not "
        "just the first one found",
        result.forbidden_calls == ("refund_order", "delete_account"),
    )


def test_policy_is_explicit_not_defaulted() -> None:
    try:
        grade_tool_name([])  # type: ignore[call-arg]
    except TypeError:
        pass
    else:
        check(
            "FTF-0606",
            "must_call_one_of/must_not_call are required keyword-only " "arguments with no default",
            False,
        )


def test_name_with_version_suffix_does_not_satisfy_requirement() -> None:
    calls = [EmittedToolCall(tool_call_id="1", name="request_approval_v2")]
    result = grade_tool_name(
        calls,
        must_call_one_of=["request_approval"],
        must_not_call=[],
    )
    check(
        "FTF-0606",
        "a longer tool name that merely extends a required name is never "
        "treated as satisfying it -- full-string equality only",
        not result.satisfied_required_one_of,
    )


def test_forbidden_name_as_substring_of_called_name_does_not_trigger() -> None:
    calls = [EmittedToolCall(tool_call_id="1", name="refund_order_preview")]
    result = grade_tool_name(
        calls,
        must_call_one_of=[],
        must_not_call=["refund_order"],
    )
    check(
        "FTF-0606",
        "a called name that merely contains a forbidden name as a "
        "substring is never flagged forbidden -- no fuzzy matching in "
        "either direction",
        result.passed and result.forbidden_calls == (),
    )


def test_required_name_as_substring_of_called_name_does_not_satisfy() -> None:
    calls = [EmittedToolCall(tool_call_id="1", name="ask_user_followup")]
    result = grade_tool_name(
        calls,
        must_call_one_of=["ask_user"],
        must_not_call=[],
    )
    check(
        "FTF-0606",
        "a called name that merely contains a required name as a "
        "substring does not satisfy the requirement",
        not result.satisfied_required_one_of,
    )


def test_fuzzy_bypass_attack_natural_language_mention_never_counted_as_a_call() -> None:
    """Real negative test against the shipped default code: simulate an
    adapter bug that hands this grader the assistant's own free-text
    content (which merely *mentions* the forbidden tool's exact name,
    e.g. "I will call refund_order next") instead of a real parsed tool
    call. The grader must refuse this outright, not silently accept a
    bare string and grade it as though a call happened -- in either
    direction (it must never become a false forbidden-call hit, and must
    never become a false required-call satisfaction either)."""
    try:
        grade_tool_name(
            ["refund_order"],  # type: ignore[list-item]
            must_call_one_of=["refund_order"],
            must_not_call=["refund_order"],
        )
    except ToolNameGraderError:
        pass
    else:
        check(
            "FTF-0606",
            "a bare string mention of a tool's exact name is never "
            "accepted as a real emitted tool call",
            False,
        )


def test_rejects_mapping_in_emitted_tool_calls() -> None:
    try:
        grade_tool_name(
            [{"tool_call_id": "1", "name": "request_approval"}],  # type: ignore[list-item]
            must_call_one_of=["request_approval"],
            must_not_call=[],
        )
    except ToolNameGraderError:
        pass
    else:
        check(
            "FTF-0606",
            "a dict shaped like a tool call is never accepted as a real "
            "EmittedToolCall -- only the real dataclass is",
            False,
        )


def test_rejects_emitted_tool_calls_as_bare_string() -> None:
    try:
        grade_tool_name(
            "request_approval",  # type: ignore[arg-type]
            must_call_one_of=["request_approval"],
            must_not_call=[],
        )
    except ToolNameGraderError:
        pass
    else:
        check(
            "FTF-0606",
            "passing a bare string as the whole emitted_tool_calls "
            "argument is refused, not iterated character-by-character",
            False,
        )


def test_rejects_emitted_tool_calls_as_empty_string() -> None:
    try:
        grade_tool_name(
            "",  # type: ignore[arg-type]
            must_call_one_of=[],
            must_not_call=[],
        )
    except ToolNameGraderError:
        pass
    else:
        check(
            "FTF-0606",
            "an empty string is still a string, not a sequence of calls -- "
            "refused even though iterating it would yield zero calls",
            False,
        )


def test_rejects_non_sequence_emitted_tool_calls() -> None:
    try:
        grade_tool_name(
            None,  # type: ignore[arg-type]
            must_call_one_of=[],
            must_not_call=[],
        )
    except ToolNameGraderError:
        pass
    else:
        check(
            "FTF-0606",
            "a non-sequence emitted_tool_calls value is refused",
            False,
        )


def test_rejects_empty_name_tool_call() -> None:
    try:
        grade_tool_name(
            [EmittedToolCall(tool_call_id="1", name="")],
            must_call_one_of=[],
            must_not_call=[],
        )
    except ToolNameGraderError:
        pass
    else:
        check(
            "FTF-0606",
            "an emitted tool call with an empty name is refused",
            False,
        )


def test_rejects_non_string_in_must_call_one_of() -> None:
    try:
        grade_tool_name(
            [],
            must_call_one_of=[123],  # type: ignore[list-item]
            must_not_call=[],
        )
    except ToolNameGraderError:
        pass
    else:
        check(
            "FTF-0606",
            "a non-string value inside must_call_one_of is refused",
            False,
        )


def test_rejects_non_string_in_must_not_call() -> None:
    try:
        grade_tool_name(
            [],
            must_call_one_of=[],
            must_not_call=[None],  # type: ignore[list-item]
        )
    except ToolNameGraderError:
        pass
    else:
        check(
            "FTF-0606",
            "a non-string value inside must_not_call is refused",
            False,
        )


def test_rejects_empty_string_in_must_call_one_of() -> None:
    try:
        grade_tool_name(
            [],
            must_call_one_of=[""],
            must_not_call=[],
        )
    except ToolNameGraderError:
        pass
    else:
        check(
            "FTF-0606",
            "an empty-string tool name inside must_call_one_of is refused",
            False,
        )


def test_rejects_non_sequence_must_call_one_of() -> None:
    try:
        grade_tool_name(
            [],
            must_call_one_of=None,  # type: ignore[arg-type]
            must_not_call=[],
        )
    except ToolNameGraderError:
        pass
    else:
        check(
            "FTF-0606",
            "a non-sequence must_call_one_of value is refused",
            False,
        )


def test_rejects_must_call_one_of_as_bare_string() -> None:
    try:
        grade_tool_name(
            [],
            must_call_one_of="request_approval",
            must_not_call=[],
        )
    except ToolNameGraderError:
        pass
    else:
        check(
            "FTF-0606",
            "a bare string must_call_one_of is refused, not iterated "
            "character-by-character into single-letter tool names",
            False,
        )


def main() -> None:
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
            except Exception as exc:  # noqa: BLE001 -- isolate one test's crash from the rest
                FAILURES.append(
                    f"FAIL [FTF-0606] {name} raised an unexpected " f"{type(exc).__name__}: {exc}"
                )

    if FAILURES:
        print(f"FTF-0606 tool-name grader: {len(FAILURES)} failed")
        for failure in FAILURES:
            print(f"  {failure}")
        sys.exit(1)

    print("FTF-0606 tool-name grader: all checks passed")
    sys.exit(0)


if __name__ == "__main__":
    main()
