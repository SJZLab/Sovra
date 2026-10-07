#!/usr/bin/env python3
# mypy: disable-error-code="arg-type,call-arg,var-annotated"
# ruff: noqa: E501
"""FTF-0607 -- Tool-argument grader.

Runnable without any database or network: `grade_tool_arguments` is a pure,
synchronous, no-I/O function (no independent schema change).

Acceptance ("Check typed arguments and required identities/tolerances against
expected contract"): test_valid_call_passes, test_required_parameter_missing,
test_optional_parameter_checked_only_when_present, test_one_of,
test_of_type_is_strict, test_tolerance_boundaries, test_exact_nested_values,
test_extra_parameters_policy, test_wrong_tool_name, test_multiple_issues.

Negative acceptance ("Numeric/string coercion or swapped customer/order IDs
cannot pass on superficial schema validity alone"):
test_string_for_number_never_passes, test_number_for_string_never_passes,
test_bool_and_number_never_conflated, test_int_and_float_not_coerced,
test_swapped_identities_fail_even_though_both_are_valid_strings,
test_swap_is_named_in_the_issue, test_numeric_string_never_inside_tolerance.

GRADER CHECKLIST: malformed records/specs fail closed, case/whitespace
exactness of names and values, duplicates preserved/rejected, empty-arguments
semantics, multi-constraint coverage.

Run directly: python3 tests/tasks/test_ftf_0607.py
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from workers.evaluation.tool_argument_grader import (  # noqa: E402
    ArgumentContract,
    Exact,
    Identity,
    NumberWithin,
    OfType,
    OneOf,
    ToolArgumentGraderError,
    ToolCallArguments,
    grade_tool_arguments,
)

FAILURES: list[str] = []
CID = "FTF-0607"


def check(criterion_id: str, description: str, condition: bool) -> None:
    if not condition:
        FAILURES.append(f"FAIL [{criterion_id}] {description}")


def raises(call: Any) -> bool:
    try:
        call()
    except ToolArgumentGraderError:
        return True
    except Exception:  # noqa: BLE001 -- the wrong exception type is a failed check
        return False
    return False


def raises_type_error(fn: Any) -> bool:
    try:
        fn()
    except TypeError:
        return True
    except Exception:  # noqa: BLE001
        return False
    return False


def call(arguments_json: str, name: str = "refund_order") -> ToolCallArguments:
    return ToolCallArguments(tool_call_id="c1", name=name, arguments_json=arguments_json)


REFUND = ArgumentContract(
    tool_name="refund_order",
    required={
        "customer_id": Identity("C-100"),
        "order_id": Identity("O-200"),
        "amount": NumberWithin(12.5, 0.01),
    },
    optional={"reason": OfType("string")},
)


def grade(arguments_json: str, contract: ArgumentContract = REFUND, *, extra: bool = False) -> Any:
    return grade_tool_arguments(call(arguments_json), contract, allow_extra=extra)


GOOD = '{"customer_id": "C-100", "order_id": "O-200", "amount": 12.5}'


def keywords(result: Any) -> list[tuple[str, str]]:
    return [(i.parameter, i.keyword) for i in result.issues]


def test_valid_call_passes() -> None:
    result = grade(GOOD)
    check(CID, "a conforming call passes with no issues", result.passed and result.issues == ())
    check(CID, "the result carries the call identity", result.tool_call_id == "c1")
    check(CID, "an in-tolerance amount passes", grade(GOOD.replace("12.5", "12.505")).passed)
    check(
        CID,
        "optional parameter present and valid passes",
        grade(GOOD[:-1] + ', "reason": "dup"}').passed,
    )


def test_required_parameter_missing() -> None:
    result = grade('{"customer_id": "C-100", "amount": 12.5}')
    check(CID, "a missing required parameter fails", keywords(result) == [("order_id", "missing")])
    check(
        CID,
        "null does not satisfy a required identity",
        not grade(GOOD.replace('"O-200"', "null")).passed,
    )
    check(
        CID,
        "an empty string does not satisfy an identity",
        not grade(GOOD.replace('"O-200"', '""')).passed,
    )


def test_optional_parameter_checked_only_when_present() -> None:
    check(CID, "an absent optional parameter is fine", grade(GOOD).passed)
    check(
        CID,
        "a present optional parameter of the wrong type fails",
        keywords(grade(GOOD[:-1] + ', "reason": 5}')) == [("reason", "mismatch")],
    )


def test_one_of() -> None:
    contract = ArgumentContract("t", {"mode": OneOf(("fast", "safe", 3))}, {})
    for raw, expected in (
        ('{"mode": "fast"}', True),
        ('{"mode": 3}', True),
        ('{"mode": "3"}', False),
        ('{"mode": "Fast"}', False),
        ('{"mode": "fast "}', False),
        ('{"mode": 3.0}', False),
    ):
        check(
            CID,
            f"OneOf {raw} -> {expected}",
            grade_tool_arguments(call(raw, "t"), contract, allow_extra=False).passed is expected,
        )


def test_of_type_is_strict() -> None:
    cases = [
        ("string", '"a"', True), ("string", "1", False), ("string", "null", False),
        ("integer", "3", True), ("integer", "3.0", False), ("integer", '"3"', False), ("integer", "true", False),
        ("number", "3", True), ("number", "3.5", True), ("number", '"3"', False), ("number", "true", False),
        ("boolean", "true", True), ("boolean", "1", False), ("boolean", '"true"', False),
        ("object", "{}", True), ("object", "[]", False), ("array", "[]", True), ("array", "{}", False),
        ("null", "null", True), ("null", '""', False), ("null", "0", False),
    ]  # fmt: skip
    for type_name, raw, expected in cases:
        contract = ArgumentContract("t", {"v": OfType(type_name)}, {})
        result = grade_tool_arguments(call('{"v": ' + raw + "}", "t"), contract, allow_extra=False)
        check(CID, f"OfType({type_name}) accepts {raw}: {expected}", result.passed is expected)


def test_tolerance_boundaries() -> None:
    contract = ArgumentContract("t", {"x": NumberWithin(10, 0.5)}, {})

    def ok(raw: str) -> bool:
        return grade_tool_arguments(
            call('{"x": ' + raw + "}", "t"), contract, allow_extra=False
        ).passed

    check(CID, "exactly at the upper tolerance passes", ok("10.5"))
    check(CID, "exactly at the lower tolerance passes", ok("9.5"))
    check(CID, "just above the tolerance fails", not ok("10.51"))
    check(CID, "just below the tolerance fails", not ok("9.49"))
    check(CID, "the expected value itself passes", ok("10"))
    exact = ArgumentContract("t", {"x": NumberWithin(7, 0)}, {})
    check(
        CID,
        "tolerance 0 means exact",
        grade_tool_arguments(call('{"x": 7}', "t"), exact, allow_extra=False).passed,
    )
    check(
        CID,
        "tolerance 0 rejects any difference",
        not grade_tool_arguments(call('{"x": 7.0001}', "t"), exact, allow_extra=False).passed,
    )
    check(CID, "a huge integer fails instead of crashing", not ok(str(10**400)))
    check(CID, "a huge negative integer fails instead of crashing", not ok("-" + str(10**400)))
    check(CID, "an out-of-range float literal is a failed call", not ok("1e999"))
    check(CID, "NaN is a failed call", not ok("NaN"))
    float_contract = ArgumentContract("t", {"x": NumberWithin(0.5, 0.25)}, {})
    check(
        CID,
        "a huge integer against a float expectation fails without crashing",
        not grade_tool_arguments(
            call('{"x": ' + str(10**400) + "}", "t"), float_contract, allow_extra=False
        ).passed,
    )


def test_string_for_number_never_passes() -> None:
    for raw in ('"12.5"', '"12.50"', '" 12.5"', '"1.25e1"', '"twelve"'):
        check(
            CID,
            f"amount {raw} (a string) fails the numeric contract",
            not grade(GOOD.replace("12.5", raw)).passed,
        )


def test_number_for_string_never_passes() -> None:
    check(
        CID,
        "an integer ID where a string ID is expected fails",
        not grade(GOOD.replace('"O-200"', "200")).passed,
    )
    contract = ArgumentContract("t", {"id": Identity("123")}, {})
    for raw in ("123", "123.0", "true"):
        check(
            CID,
            f"{raw} is not the string identity '123'",
            not grade_tool_arguments(
                call('{"id": ' + raw + "}", "t"), contract, allow_extra=False
            ).passed,
        )
    check(
        CID,
        "the string identity '123' passes",
        grade_tool_arguments(call('{"id": "123"}', "t"), contract, allow_extra=False).passed,
    )
    int_contract = ArgumentContract("t", {"id": Identity(123)}, {})
    check(
        CID,
        "the string '123' is not the integer identity 123",
        not grade_tool_arguments(
            call('{"id": "123"}', "t"), int_contract, allow_extra=False
        ).passed,
    )
    check(
        CID,
        "the integer identity 123 passes",
        grade_tool_arguments(call('{"id": 123}', "t"), int_contract, allow_extra=False).passed,
    )


def test_bool_and_number_never_conflated() -> None:
    for expected, raw in ((True, "1"), (False, "0"), (1, "true"), (0, "false")):
        contract = ArgumentContract("t", {"v": Exact(expected)}, {})
        check(
            CID,
            f"Exact({expected!r}) rejects {raw}",
            not grade_tool_arguments(
                call('{"v": ' + raw + "}", "t"), contract, allow_extra=False
            ).passed,
        )
        same = "true" if expected is True else "false" if expected is False else str(expected)
        check(
            CID,
            f"Exact({expected!r}) accepts {same}",
            grade_tool_arguments(
                call('{"v": ' + same + "}", "t"), contract, allow_extra=False
            ).passed,
        )


def test_int_and_float_not_coerced() -> None:
    contract = ArgumentContract("t", {"v": Exact(5)}, {})
    check(
        CID,
        "Exact(5) rejects 5.0",
        not grade_tool_arguments(call('{"v": 5.0}', "t"), contract, allow_extra=False).passed,
    )
    check(
        CID,
        "Exact(5.0) rejects 5",
        not grade_tool_arguments(
            call('{"v": 5}', "t"), ArgumentContract("t", {"v": Exact(5.0)}, {}), allow_extra=False
        ).passed,
    )


def test_numeric_string_never_inside_tolerance() -> None:
    contract = ArgumentContract("t", {"x": NumberWithin(10, 100)}, {})
    check(
        CID,
        "a numeric string never passes even with a wide tolerance",
        not grade_tool_arguments(call('{"x": "10"}', "t"), contract, allow_extra=False).passed,
    )
    check(
        CID,
        "a boolean is never a number within tolerance",
        not grade_tool_arguments(
            call('{"x": true}', "t"),
            ArgumentContract("t", {"x": NumberWithin(1, 5)}, {}),
            allow_extra=False,
        ).passed,
    )
    check(
        CID,
        "null is not a number within tolerance",
        not grade_tool_arguments(call('{"x": null}', "t"), contract, allow_extra=False).passed,
    )


def test_swapped_identities_fail_even_though_both_are_valid_strings() -> None:
    result = grade('{"customer_id": "O-200", "order_id": "C-100", "amount": 12.5}')
    check(CID, "swapped customer/order IDs fail", not result.passed)
    check(
        CID,
        "both swapped parameters are reported",
        sorted(i.parameter for i in result.issues if i.keyword == "mismatch")
        == ["customer_id", "order_id"],
    )
    same = grade('{"customer_id": "C-100", "order_id": "C-100", "amount": 12.5}')
    check(
        CID,
        "a duplicated identity in both slots fails",
        not same.passed and ("order_id", "swapped_identity") in keywords(same),
    )


def test_swap_is_named_in_the_issue() -> None:
    result = grade('{"customer_id": "O-200", "order_id": "C-100", "amount": 12.5}')
    found = {(i.parameter, i.message) for i in result.issues if i.keyword == "swapped_identity"}
    check(
        CID,
        "the swap names the other parameter",
        found
        == {
            ("customer_id", "value is the expected identity of parameter 'order_id'"),
            ("order_id", "value is the expected identity of parameter 'customer_id'"),
        },
    )
    plain = grade('{"customer_id": "X", "order_id": "O-200", "amount": 12.5}')
    check(
        CID,
        "a wrong identity that is nobody's value is a plain mismatch",
        keywords(plain) == [("customer_id", "mismatch")],
    )
    optional_swap = ArgumentContract("t", {"a": Identity("A")}, {"b": Identity("B")})
    swapped = grade_tool_arguments(
        call('{"a": "B", "b": "A"}', "t"), optional_swap, allow_extra=False
    )
    check(
        CID,
        "an optional identity is part of swap detection",
        ("a", "swapped_identity") in keywords(swapped)
        and ("b", "swapped_identity") in keywords(swapped),
    )
    non_identity = ArgumentContract("t", {"a": Identity("A"), "b": Exact("B")}, {})
    mixed = grade_tool_arguments(call('{"a": "B", "b": "B"}', "t"), non_identity, allow_extra=False)
    check(
        CID,
        "only declared Identity specs count for swap detection",
        keywords(mixed) == [("a", "mismatch")],
    )


def test_issue_messages_never_echo_values() -> None:
    result = grade('{"customer_id": "SECRET-TOKEN-XYZ", "order_id": "O-200", "amount": 99999}')
    check(
        CID,
        "issues are content-free",
        all("SECRET" not in i.message and "99999" not in i.message for i in result.issues),
    )


def test_extra_parameters_policy() -> None:
    with_extra = GOOD[:-1] + ', "role": "admin"}'
    result = grade(with_extra)
    check(
        CID,
        "an undeclared parameter fails when extras are not allowed",
        keywords(result) == [("role", "extra")],
    )
    check(
        CID,
        "an undeclared parameter passes when extras are allowed",
        grade(with_extra, extra=True).passed,
    )
    check(
        CID,
        "extras allowed does not relax declared parameters",
        not grade(with_extra.replace("12.5", "13"), extra=True).passed,
    )
    check(
        CID,
        "allow_extra is required (no default)",
        raises_type_error(lambda: grade_tool_arguments(call(GOOD), REFUND)),
    )


def test_wrong_tool_name() -> None:
    for name in (
        "Refund_Order",
        "refund_order ",
        " refund_order",
        "refund_order_v2",
        "refund",
        "cancel_order",
    ):
        result = grade_tool_arguments(call(GOOD, name), REFUND, allow_extra=False)
        check(CID, f"tool name {name!r} fails", keywords(result) == [("<tool>", "tool_name")])
    check(
        CID,
        "the exact name with bad arguments reports only the argument issues",
        ("<tool>", "tool_name") not in keywords(grade('{"customer_id": "x"}')),
    )


def test_parameter_names_are_case_and_whitespace_exact() -> None:
    for raw in (
        '{"Customer_ID": "C-100", "order_id": "O-200", "amount": 12.5}',
        '{" customer_id": "C-100", "order_id": "O-200", "amount": 12.5}',
        '{"customer_id ": "C-100", "order_id": "O-200", "amount": 12.5}',
    ):
        result = grade(raw)
        check(
            CID,
            f"a near-miss name fails: {raw[:22]}",
            ("customer_id", "missing") in keywords(result) and not result.passed,
        )
    check(CID, "values are case-exact", not grade(GOOD.replace("C-100", "c-100")).passed)
    check(CID, "values are whitespace-exact", not grade(GOOD.replace("C-100", "C-100 ")).passed)
    check(
        CID,
        "a whitespace-only parameter name is a legal exact name",
        ArgumentContract("t", {" ": Exact(1)}, {}) is not None
        and grade_tool_arguments(
            call('{" ": 1}', "t"), ArgumentContract("t", {" ": Exact(1)}, {}), allow_extra=False
        ).passed,
    )


def test_exact_nested_values() -> None:
    contract = ArgumentContract("t", {"v": Exact({"a": [1, {"b": True}], "c": None})}, {})
    check(
        CID,
        "an identical nested value passes",
        grade_tool_arguments(
            call('{"v": {"c": null, "a": [1, {"b": true}]}}', "t"), contract, allow_extra=False
        ).passed,
    )
    for raw in (
        '{"a": [1.0, {"b": true}], "c": null}',
        '{"a": [true, {"b": true}], "c": null}',
        '{"a": [1, {"b": 1}], "c": null}',
        '{"a": [1, {"b": true}]}',
        '{"a": [1, {"b": true}], "c": null, "d": 1}',
        '{"a": [{"b": true}, 1], "c": null}',
        '{"a": [1], "c": null}',
    ):
        check(
            CID,
            f"a nested near-miss fails: {raw[:30]}",
            not grade_tool_arguments(
                call('{"v": ' + raw + "}", "t"), contract, allow_extra=False
            ).passed,
        )
    check(
        CID,
        "an empty list is not an empty object",
        not grade_tool_arguments(
            call('{"v": []}', "t"), ArgumentContract("t", {"v": Exact({})}, {}), allow_extra=False
        ).passed,
    )


def test_malformed_arguments_text_is_a_failed_call_never_repaired() -> None:
    attacks = [
        "", "   ", "not json", "I will call refund_order for C-100", '```json\n' + GOOD + "\n```", GOOD + " trailing",
        "[" + GOOD + "]", "null", '"str"', "5", "true",
        '{"customer_id": "C-100", "customer_id": "X", "order_id": "O-200", "amount": 12.5}',
        '{"customer_id": "C-100", "order_id": "O-200", "amount": NaN}',
        "{'customer_id': 'C-100'}",
    ]  # fmt: skip
    for raw in attacks:
        result = grade(raw)
        check(
            CID,
            f"malformed arguments {raw[:28]!r} fail with an arguments_json issue",
            not result.passed and any(i.keyword == "arguments_json" for i in result.issues),
        )


def test_empty_arguments_semantics() -> None:
    nothing = ArgumentContract("ping", {}, {})
    check(
        CID,
        "empty object passes a contract with no parameters",
        grade_tool_arguments(call("{}", "ping"), nothing, allow_extra=False).passed,
    )
    check(CID, "empty object fails a contract with a required parameter", not grade("{}").passed)
    check(
        CID,
        "empty text never passes, even for a no-parameter contract",
        not grade_tool_arguments(call("", "ping"), nothing, allow_extra=False).passed,
    )
    check(
        CID,
        "an extra parameter fails a no-parameter contract",
        not grade_tool_arguments(call('{"a": 1}', "ping"), nothing, allow_extra=False).passed,
    )


def test_multiple_issues_all_reported_sorted() -> None:
    result = grade(
        '{"order_id": "X", "role": "admin", "amount": "12.5"}',
        ArgumentContract("refund_order", dict(REFUND.required), dict(REFUND.optional)),
    )
    got = keywords(result)
    check(
        CID,
        "every violated constraint is reported",
        got == sorted(got)
        and ("customer_id", "missing") in got
        and ("order_id", "mismatch") in got
        and ("amount", "mismatch") in got
        and ("role", "extra") in got,
    )
    again = grade('{"order_id": "X", "role": "admin", "amount": "12.5"}')
    check(CID, "the result is deterministic", again == result)
    wrong_both = grade_tool_arguments(
        call('{"customer_id": "x"}', "other"), REFUND, allow_extra=False
    )
    check(
        CID,
        "a wrong tool and wrong arguments are both reported",
        ("<tool>", "tool_name") in keywords(wrong_both)
        and ("order_id", "missing") in keywords(wrong_both),
    )


def test_duplicate_calls_and_keys_are_not_merged() -> None:
    result = grade(
        '{"customer_id": "X", "customer_id": "C-100", "order_id": "O-200", "amount": 12.5}'
    )
    check(
        CID,
        "a duplicate key is not last-wins",
        not result.passed and result.issues[0].keyword == "arguments_json",
    )


def test_contract_construction_fails_closed() -> None:
    bad_specs: list[Any] = [None, 5, "x", [], {}, ("a",)]
    for spec in bad_specs:
        check(
            CID,
            f"required spec {spec!r} is refused",
            raises(lambda spec=spec: ArgumentContract("t", {"a": spec}, {})),
        )
        check(
            CID,
            f"optional spec {spec!r} is refused",
            raises(lambda spec=spec: ArgumentContract("t", {}, {"a": spec})),
        )
    for bad_name in ("", None, 5):
        check(
            CID,
            f"parameter name {bad_name!r} is refused",
            raises(lambda bad_name=bad_name: ArgumentContract("t", {bad_name: Exact(1)}, {})),
        )
        check(
            CID,
            f"tool name {bad_name!r} is refused",
            raises(lambda bad_name=bad_name: ArgumentContract(bad_name, {}, {})),
        )
    check(
        CID,
        "required as a list is refused",
        raises(lambda: ArgumentContract("t", [("a", Exact(1))], {})),
    )
    check(CID, "optional as None is refused", raises(lambda: ArgumentContract("t", {}, None)))
    check(
        CID,
        "a parameter both required and optional is refused",
        raises(lambda: ArgumentContract("t", {"a": Exact(1)}, {"a": Exact(2)})),
    )
    check(CID, "identity bool is refused", raises(lambda: Identity(True)))
    for bad in ("", None, 1.5, [], {}):
        check(CID, f"identity {bad!r} is refused", raises(lambda bad=bad: Identity(bad)))
    check(CID, "Exact with NaN is refused", raises(lambda: Exact(float("nan"))))
    check(CID, "Exact with a non-JSON object is refused", raises(lambda: Exact(object())))
    check(CID, "Exact with a non-string dict key is refused", raises(lambda: Exact({1: 2})))
    check(
        CID, "Exact with a nested non-JSON value is refused", raises(lambda: Exact([1, [object()]]))
    )
    check(CID, "Exact null is legal", Exact(None).matches(None))
    check(CID, "OneOf empty is refused", raises(lambda: OneOf(())))
    check(CID, "OneOf as a list is refused", raises(lambda: OneOf([1])))
    check(CID, "OneOf with a non-JSON member is refused", raises(lambda: OneOf((1, object()))))
    check(CID, "OfType unknown name is refused", raises(lambda: OfType("str")))
    for name in ("String", " string", "INTEGER", "", None):
        check(
            CID,
            f"OfType name {name!r} is refused (case/whitespace exact)",
            raises(lambda name=name: OfType(name)),
        )
    for bad in ("1", True, None, float("nan"), float("inf")):
        check(
            CID,
            f"NumberWithin expected {bad!r} is refused",
            raises(lambda bad=bad: NumberWithin(bad, 0)),
        )
        check(
            CID,
            f"NumberWithin abs_tol {bad!r} is refused",
            raises(lambda bad=bad: NumberWithin(1, bad)),
        )
    check(CID, "NumberWithin negative abs_tol is refused", raises(lambda: NumberWithin(1, -0.1)))
    check(CID, "NumberWithin zero abs_tol is legal", NumberWithin(1, 0).matches(1))
    check(CID, "NumberWithin huge int expected is legal", NumberWithin(10**400, 0).matches(10**400))


def test_call_record_and_arguments_fail_closed() -> None:
    check(CID, "empty tool_call_id is refused", raises(lambda: ToolCallArguments("", "t", "{}")))
    check(CID, "empty name is refused", raises(lambda: ToolCallArguments("c", "", "{}")))
    for bad in (None, 5, {}, b"{}"):
        check(
            CID,
            f"arguments_json {bad!r} is refused",
            raises(lambda bad=bad: ToolCallArguments("c", "t", bad)),
        )
    check(
        CID, "a non-string tool_call_id is refused", raises(lambda: ToolCallArguments(5, "t", "{}"))
    )
    for bad_call in (None, "{}", {"name": "t"}):
        check(
            CID,
            f"call {bad_call!r} is refused",
            raises(
                lambda bad_call=bad_call: grade_tool_arguments(bad_call, REFUND, allow_extra=False)
            ),
        )
    for bad_contract in (None, {}, "refund_order"):
        check(
            CID,
            f"contract {bad_contract!r} is refused",
            raises(
                lambda bad_contract=bad_contract: grade_tool_arguments(
                    call(GOOD), bad_contract, allow_extra=False
                )
            ),
        )
    for bad_flag in (None, 0, 1, "yes"):
        check(
            CID,
            f"allow_extra {bad_flag!r} is refused",
            raises(
                lambda bad_flag=bad_flag: grade_tool_arguments(
                    call(GOOD), REFUND, allow_extra=bad_flag
                )
            ),
        )


def test_a_mention_in_prose_is_not_a_call() -> None:
    prose = "I'll call refund_order with customer_id C-100, order_id O-200, amount 12.5"
    check(CID, "prose describing the right arguments never passes", not grade(prose).passed)


def test_whitespace_and_case_in_names_are_exact_everywhere() -> None:
    check(
        CID,
        "a whitespace-only tool_call_id is a legal exact id",
        ToolCallArguments(" ", "t", "{}").tool_call_id == " ",
    )
    check(
        CID,
        "a whitespace-only call name is a legal exact name",
        ToolCallArguments("c", " ", "{}").name == " ",
    )
    spaced = ArgumentContract(" ", {}, {})
    check(
        CID,
        "a whitespace-only contract tool name is legal and exact",
        grade_tool_arguments(call("{}", " "), spaced, allow_extra=False).passed,
    )
    check(
        CID,
        "...and does not match a different name",
        not grade_tool_arguments(call("{}", "t"), spaced, allow_extra=False).passed,
    )
    upper = ArgumentContract("Refund_Order", {}, {})
    check(
        CID,
        "a mixed-case contract name rejects the lower-case call",
        not grade_tool_arguments(call("{}", "refund_order"), upper, allow_extra=False).passed,
    )
    padded = ArgumentContract(" t", {}, {})
    check(
        CID,
        "a padded contract name rejects the unpadded call",
        not grade_tool_arguments(call("{}", "t"), padded, allow_extra=False).passed,
    )
    optional = ArgumentContract("t", {}, {"Reason": OfType("string")})
    check(
        CID,
        "an optional parameter name is case-exact when present",
        keywords(grade_tool_arguments(call('{"Reason": 5}', "t"), optional, allow_extra=False))
        == [("Reason", "mismatch")],
    )
    check(
        CID,
        "a different-case key is an extra, not the optional parameter",
        keywords(grade_tool_arguments(call('{"reason": 5}', "t"), optional, allow_extra=False))
        == [("reason", "extra")],
    )
    pair = ArgumentContract("t", {"id": Identity("X"), "ID": Identity("Y")}, {})
    swapped = grade_tool_arguments(call('{"id": "Y", "ID": "X"}', "t"), pair, allow_extra=False)
    check(
        CID,
        "swap detection treats id and ID as different parameters",
        {("id", "swapped_identity"), ("ID", "swapped_identity")} <= set(keywords(swapped)),
    )


def test_arguments_failure_reasons_are_distinguished() -> None:
    parse_failure = grade("not json")
    check(
        CID,
        "unparseable text reports the parse reason",
        parse_failure.issues[0].message != "arguments are not an object",
    )
    root_failure = grade("[1]")
    check(
        CID,
        "a non-object root reports that it is not an object",
        root_failure.issues[0].message == "arguments are not an object",
    )
    check(
        CID,
        "both stop before parameter checks",
        all(i.keyword == "arguments_json" for i in parse_failure.issues + root_failure.issues),
    )


def main() -> int:
    tests = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_") and callable(f)]
    for name, fn in tests:
        try:
            fn()
        except Exception as exc:  # noqa: BLE001 -- a crash is a failed test, reported by name
            FAILURES.append(f"FAIL [FTF-0607] {name} raised {type(exc).__name__}: {exc}")
    if FAILURES:
        for failure in FAILURES:
            print(failure)
        print(f"{len(FAILURES)} check(s) failed")
        return 1
    print("all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
