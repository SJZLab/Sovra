#!/usr/bin/env python3
"""FTF-0605 -- JSON-schema grader.

Runnable without any database or network: `grade_json_schema` is a pure,
synchronous, no-I/O function (this task's `database_changes` clause -- no
independent schema change).

Acceptance ("Validate actual output against pinned JSON Schema with
deterministic result/errors"):
  test_valid_output_passes, test_invalid_output_reports_sorted_issues,
  test_result_is_deterministic_across_runs, test_pin_mismatch_is_refused,
  test_pin_must_be_lowercase_hex_digest, test_fingerprint_ignores_key_order,
  test_all_supported_keywords_enforced (multi-constraint coverage).

Negative acceptance ("Malformed JSON/prose, forbidden extras and schema
reference attacks fail; no silent repair to pass"):
  test_prose_fails, test_fenced_json_is_not_repaired,
  test_json_followed_by_prose_fails, test_empty_and_whitespace_output_fail,
  test_duplicate_keys_fail, test_nan_infinity_and_overflow_fail,
  test_extra_property_fails, test_extra_property_in_nested_object_fails,
  test_object_schema_without_additional_properties_is_refused,
  test_remote_and_file_refs_are_refused, test_ref_loop_is_refused,
  test_unsupported_keywords_fail_closed, test_unresolved_ref_is_refused.

GRADER CHECKLIST: malformed records/specs fail closed (wrong types, empty
strings, None), case/whitespace exactness of names, duplicates preserved,
empty-output semantics, multi-constraint coverage.

Run directly: python3 tests/tasks/test_ftf_0605.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from workers.evaluation.json_schema_grader import (  # noqa: E402
    MAX_INSTANCE_DEPTH,
    MAX_OUTPUT_BYTES,
    JsonSchemaGraderError,
    SchemaError,
    grade_json_schema,
    schema_fingerprint,
)

FAILURES: list[str] = []
CID = "FTF-0605"


def check(criterion_id: str, description: str, condition: bool) -> None:
    if not condition:
        FAILURES.append(f"FAIL [{criterion_id}] {description}")


def grade(raw: Any, schema: dict[str, Any]) -> Any:
    return grade_json_schema(raw, schema, expected_schema_sha256=schema_fingerprint(schema))


def raises(exc: type[Exception], call: Any) -> bool:
    try:
        call()
    except exc:
        return True
    except Exception:  # noqa: BLE001 -- wrong exception type is a failed check
        return False
    return False


ORDER_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "properties": {
        "Name": {"type": "string", "minLength": 1},
        "qty": {"type": "integer", "minimum": 1, "maximum": 10},
    },
    "required": ["Name", "qty"],
    "additionalProperties": False,
}


def test_valid_output_passes() -> None:
    result = grade('{"Name": "a", "qty": 3}', ORDER_SCHEMA)
    check(CID, "conforming output passes with no issues", result.passed and result.issues == ())
    check(
        CID,
        "result carries the schema digest",
        result.schema_sha256 == schema_fingerprint(ORDER_SCHEMA),
    )


def test_invalid_output_reports_sorted_issues() -> None:
    result = grade('{"qty": 99, "zzz": 1}', ORDER_SCHEMA)
    keywords = [(i.path, i.keyword) for i in result.issues]
    check(CID, "invalid output fails", not result.passed)
    check(
        CID,
        "issues are sorted and name path+keyword",
        keywords == sorted(keywords)
        and ("/", "required") in keywords
        and ("/qty", "maximum") in keywords
        and ("/zzz", "additionalProperties") in keywords,
    )
    check(
        CID,
        "messages never echo candidate values",
        all("99" not in i.message for i in result.issues),
    )


def test_result_is_deterministic_across_runs() -> None:
    first = grade('{"qty": 99, "zzz": 1, "Name": ""}', ORDER_SCHEMA)
    second = grade('{"qty": 99, "zzz": 1, "Name": ""}', ORDER_SCHEMA)
    check(CID, "identical input gives identical result", first == second)


def test_pin_mismatch_is_refused() -> None:
    drifted = {**ORDER_SCHEMA, "required": ["Name"]}
    check(
        CID,
        "a schema that differs from its pinned digest is refused, not graded",
        raises(
            JsonSchemaGraderError,
            lambda: grade_json_schema(
                '{"Name": "a"}', drifted, expected_schema_sha256=schema_fingerprint(ORDER_SCHEMA)
            ),
        ),
    )


def test_pin_must_be_lowercase_hex_digest() -> None:
    good = schema_fingerprint(ORDER_SCHEMA)
    for bad in ("", "abc", good.upper(), good[:-1] + "g", good + "0", None, 5):
        check(
            CID,
            f"pin {bad!r} is refused",
            raises(
                JsonSchemaGraderError,
                lambda bad=bad: grade_json_schema("{}", ORDER_SCHEMA, expected_schema_sha256=bad),
            ),
        )


def test_fingerprint_ignores_key_order() -> None:
    reordered = dict(reversed(list(ORDER_SCHEMA.items())))
    check(
        CID,
        "fingerprint is canonical",
        schema_fingerprint(reordered) == schema_fingerprint(ORDER_SCHEMA),
    )
    check(
        CID,
        "fingerprint changes when the schema changes",
        schema_fingerprint({**ORDER_SCHEMA, "required": []}) != schema_fingerprint(ORDER_SCHEMA),
    )
    check(
        CID,
        "non-JSON schema content is refused",
        raises(SchemaError, lambda: schema_fingerprint({"type": object()})),
    )
    check(
        CID,
        "NaN in a schema is refused",
        raises(SchemaError, lambda: schema_fingerprint({"minimum": float("nan")})),
    )


def test_prose_fails() -> None:
    for raw in ("Sure! Here is the order.", "null is fine", "{'Name': 'a', 'qty': 1}", "{"):
        result = grade(raw, ORDER_SCHEMA)
        check(
            CID,
            f"prose/malformed {raw!r} fails with a json issue",
            not result.passed and result.issues[0].keyword == "json",
        )


def test_fenced_json_is_not_repaired() -> None:
    result = grade('```json\n{"Name": "a", "qty": 1}\n```', ORDER_SCHEMA)
    check(CID, "a fenced block is not repaired into a pass", not result.passed)


def test_json_followed_by_prose_fails() -> None:
    for raw in (
        '{"Name": "a", "qty": 1} thanks!',
        '{"Name": "a", "qty": 1}{"Name": "b", "qty": 2}',
    ):
        check(CID, f"trailing content {raw[-12:]!r} fails", not grade(raw, ORDER_SCHEMA).passed)


def test_empty_and_whitespace_output_fail() -> None:
    for raw in ("", "   ", "\n\t"):
        check(CID, f"empty output {raw!r} fails", not grade(raw, {"type": "null"}).passed)
    check(
        CID,
        "surrounding whitespace around valid JSON is legal JSON",
        grade(' \n{"Name":"a","qty":1}\n', ORDER_SCHEMA).passed,
    )


def test_duplicate_keys_fail() -> None:
    result = grade('{"Name": "a", "Name": "b", "qty": 1}', ORDER_SCHEMA)
    check(
        CID,
        "duplicate keys are not silently last-wins",
        not result.passed and result.issues[0].keyword == "json",
    )
    nested = grade(
        '{"Name": "a", "qty": 1, "x": {"a": 1, "a": 2}}',
        {**ORDER_SCHEMA, "additionalProperties": True},
    )
    check(CID, "nested duplicate keys fail", not nested.passed)


def test_nan_infinity_and_overflow_fail() -> None:
    schema = {"type": "number"}
    for raw in ("NaN", "Infinity", "-Infinity", "1e999", "-1e999"):
        check(CID, f"{raw} fails", not grade(raw, schema).passed)
    check(CID, "a large finite number passes", grade("1e300", schema).passed)


def test_extra_property_fails() -> None:
    result = grade('{"Name": "a", "qty": 1, "role": "admin"}', ORDER_SCHEMA)
    check(CID, "an undeclared property fails", not result.passed)
    check(
        CID,
        "...reported as additionalProperties",
        any(i.keyword == "additionalProperties" and i.path == "/role" for i in result.issues),
    )


def test_extra_property_in_nested_object_fails() -> None:
    schema = {
        "type": "object",
        "properties": {
            "items": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {"id": {"type": "integer"}},
                    "required": ["id"],
                    "additionalProperties": False,
                },
            }
        },
        "required": ["items"],
        "additionalProperties": False,
    }
    result = grade('{"items": [{"id": 1}, {"id": 2, "evil": true}]}', schema)
    check(
        CID,
        "extras nested in an array item fail",
        not result.passed and result.issues[0].path == "/items/1/evil",
    )


def test_additional_properties_schema_is_applied() -> None:
    schema = {"type": "object", "additionalProperties": {"type": "integer"}}
    check(
        CID,
        "typed additionalProperties accepts conforming extras",
        grade('{"a": 1}', schema).passed,
    )
    check(
        CID,
        "typed additionalProperties rejects non-conforming extras",
        not grade('{"a": "x"}', schema).passed,
    )
    check(
        CID,
        "additionalProperties true accepts any extras",
        grade('{"a": "x"}', {"type": "object", "additionalProperties": True}).passed,
    )


def test_object_schema_without_additional_properties_is_refused() -> None:
    for schema in (
        {"type": "object"},
        {"properties": {"a": {"type": "string"}}},
        {"type": ["object", "null"]},
    ):
        check(
            CID,
            f"schema {schema} must state additionalProperties",
            raises(SchemaError, lambda schema=schema: grade("{}", schema)),
        )
    nested = {
        "type": "object",
        "properties": {"a": {"type": "object", "properties": {}}},
        "additionalProperties": False,
    }
    check(
        CID,
        "a nested object schema is held to the same rule",
        raises(SchemaError, lambda: grade("{}", nested)),
    )


def test_remote_and_file_refs_are_refused() -> None:
    attacks = [
        "http://evil.example/schema.json",
        "https://evil.example/schema.json#/$defs/a",
        "file:///etc/passwd",
        "#",
        "#/properties/a",
        "#/$defs/",
        "other.json#/$defs/a",
        "",
        None,
        5,
    ]
    for ref in attacks:
        schema = {"$defs": {"a": {"type": "string"}}, "$ref": ref}
        check(
            CID,
            f"$ref {ref!r} is refused",
            raises(SchemaError, lambda schema=schema: grade('"x"', schema)),
        )
    for keyword in ("$dynamicRef", "$anchor", "$id", "$recursiveRef", "definitions"):
        schema = {keyword: "#/$defs/a", "$defs": {"a": {"type": "string"}}}
        check(
            CID,
            f"{keyword} is refused",
            raises(SchemaError, lambda schema=schema: grade('"x"', schema)),
        )


def test_ref_loop_is_refused() -> None:
    loop = {"$defs": {"a": {"$ref": "#/$defs/b"}, "b": {"$ref": "#/$defs/a"}}, "$ref": "#/$defs/a"}
    check(
        CID,
        "a pure reference loop is refused, not hung",
        raises(SchemaError, lambda: grade("1", loop)),
    )
    self_ref = {"$defs": {"a": {"allOf": [{"$ref": "#/$defs/a"}]}}, "$ref": "#/$defs/a"}
    check(
        CID,
        "a self-referencing allOf is refused",
        raises(SchemaError, lambda: grade("1", self_ref)),
    )


def test_valid_local_ref_and_recursion_work() -> None:
    schema = {
        "$defs": {
            "node": {
                "type": "object",
                "properties": {"value": {"type": "integer"}, "next": {"$ref": "#/$defs/node"}},
                "required": ["value"],
                "additionalProperties": False,
            }
        },
        "$ref": "#/$defs/node",
    }
    check(
        CID,
        "a recursive local $ref validates conforming data",
        grade('{"value": 1, "next": {"value": 2}}', schema).passed,
    )
    bad = grade('{"value": 1, "next": {"value": "x"}}', schema)
    check(
        CID,
        "...and rejects non-conforming nested data at its path",
        not bad.passed and bad.issues[0].path == "/next/value",
    )
    deep = '{"value": 1}'
    for _ in range(MAX_INSTANCE_DEPTH // 2):
        deep = '{"value": 1, "next": ' + deep + "}"
    check(
        CID,
        "a legitimately deep recursive instance within the limit passes",
        grade(deep, schema).passed,
    )


def test_unresolved_ref_is_refused() -> None:
    check(
        CID,
        "a $ref to a missing $defs entry is refused",
        raises(SchemaError, lambda: grade("1", {"$ref": "#/$defs/missing"})),
    )
    nested_defs = {"$defs": {"a": {"$defs": {"b": {"type": "string"}}}}, "$ref": "#/$defs/a"}
    check(
        CID, "$defs below the root is refused", raises(SchemaError, lambda: grade("1", nested_defs))
    )


def test_unsupported_keywords_fail_closed() -> None:
    for keyword, value in (
        ("pattern", "^a$"),
        ("format", "email"),
        ("oneOf", [{"type": "string"}]),
        ("not", {"type": "string"}),
        ("if", {"type": "string"}),
        ("patternProperties", {}),
        ("propertyNames", {}),
        ("contains", {}),
        ("prefixItems", []),
        ("dependentRequired", {}),
        ("unevaluatedProperties", False),
        ("multipleOf", 2),
        ("minProperties", 1),
    ):
        schema = {"type": "string", keyword: value}
        check(
            CID,
            f"unsupported keyword {keyword} is refused, never ignored",
            raises(SchemaError, lambda schema=schema: grade('"a"', schema)),
        )
    check(
        CID,
        "annotation keywords are accepted and ignored",
        grade(
            '"a"',
            {
                "type": "string",
                "title": "t",
                "description": "d",
                "$comment": "c",
                "examples": ["a"],
                "default": "a",
            },
        ).passed,
    )
    check(
        CID,
        "a different dialect is refused",
        raises(
            SchemaError,
            lambda: grade('"a"', {"$schema": "http://json-schema.org/draft-07/schema#"}),
        ),
    )


def test_malformed_schema_keywords_fail_closed() -> None:
    bad_schemas: list[dict[str, Any]] = [
        {"type": "str"},
        {"type": ""},
        {"type": []},
        {"type": [5]},
        {"type": None},
        {"enum": []},
        {"enum": "a"},
        {"required": "a"},
        {"required": [""]},
        {"required": [5]},
        {"properties": []},
        {"properties": {"a": []}, "additionalProperties": False},
        {"items": "string"},
        {"anyOf": []},
        {"allOf": {}},
        {"anyOf": [5]},
        {"minLength": -1},
        {"minLength": True},
        {"maxLength": "1"},
        {"minItems": 1.5},
        {"maxItems": None},
        {"minimum": "1"},
        {"maximum": True},
        {"exclusiveMinimum": float("inf")},
        {"uniqueItems": "yes"},
        {"$defs": []},
    ]
    for schema in bad_schemas:
        try:
            pin = schema_fingerprint(schema)
        except SchemaError:
            continue
        check(
            CID,
            f"malformed schema {schema} is refused",
            raises(
                SchemaError,
                lambda schema=schema, pin=pin: grade_json_schema(
                    "1", schema, expected_schema_sha256=pin
                ),
            ),
        )
    wrong_types: list[Any] = [None, [], "{}", 5]
    for wrong in wrong_types:
        check(
            CID,
            f"a {type(wrong).__name__} schema is refused",
            raises(
                SchemaError,
                lambda wrong=wrong: grade_json_schema("1", wrong, expected_schema_sha256="0" * 64),
            ),
        )


def test_candidate_type_is_checked() -> None:
    for raw in (None, 5, b'{"Name": "a", "qty": 1}', ["a"], {"Name": "a"}):
        check(
            CID,
            f"non-string candidate {raw!r} is refused, not coerced",
            raises(JsonSchemaGraderError, lambda raw=raw: grade(raw, ORDER_SCHEMA)),
        )


def test_schema_defect_is_not_masked_by_unparseable_candidate() -> None:
    check(
        CID,
        "a malformed schema is refused even when the candidate is prose",
        raises(SchemaError, lambda: grade("not json", {"type": "object"})),
    )


def test_type_exactness() -> None:
    check(CID, "true is not an integer", not grade("true", {"type": "integer"}).passed)
    check(CID, "true is not a number", not grade("true", {"type": "number"}).passed)
    check(CID, "1 is not a boolean", not grade("1", {"type": "boolean"}).passed)
    check(CID, "1.0 is an integer (2020-12)", grade("1.0", {"type": "integer"}).passed)
    check(CID, "1.5 is not an integer", not grade("1.5", {"type": "integer"}).passed)
    check(CID, "an integer is a number", grade("3", {"type": "number"}).passed)
    check(CID, "null is not an empty string", not grade("null", {"type": "string"}).passed)
    check(CID, "a string '1' is not an integer", not grade('"1"', {"type": "integer"}).passed)
    check(CID, "a type list accepts any member", grade("null", {"type": ["string", "null"]}).passed)
    check(
        CID, "a type list rejects non-members", not grade("1", {"type": ["string", "null"]}).passed
    )
    check(
        CID,
        "an array is not an object",
        not grade("[]", {"type": "object", "additionalProperties": True}).passed,
    )
    check(CID, "an object is not an array", not grade("{}", {"type": "array"}).passed)


def test_key_and_value_case_and_whitespace_exactness() -> None:
    check(
        CID,
        "property names are case-exact",
        not grade('{"name": "a", "qty": 1}', ORDER_SCHEMA).passed,
    )
    check(
        CID,
        "property names are whitespace-exact",
        not grade('{" Name": "a", "qty": 1}', ORDER_SCHEMA).passed,
    )
    check(
        CID,
        "a trailing-space key is an extra, not Name",
        not grade('{"Name ": "a", "Name": "a", "qty": 1}', ORDER_SCHEMA).passed,
    )
    enum_schema = {"enum": ["Yes", "no"]}
    check(
        CID,
        "enum is case-exact",
        not grade('"yes"', enum_schema).passed and grade('"Yes"', enum_schema).passed,
    )
    check(CID, "enum is whitespace-exact", not grade('"no "', enum_schema).passed)
    check(CID, "const is case-exact", not grade('"A"', {"const": "a"}).passed)


def test_enum_and_const_never_coerce_bool_and_numbers() -> None:
    check(CID, "enum [1] rejects true", not grade("true", {"enum": [1]}).passed)
    check(CID, "enum [true] rejects 1", not grade("1", {"enum": [True]}).passed)
    check(CID, "enum [0] rejects false", not grade("false", {"enum": [0]}).passed)
    check(CID, "const 1 accepts 1.0", grade("1.0", {"const": 1}).passed)
    check(CID, "const rejects false for 0", not grade("false", {"const": 0}).passed)
    check(
        CID,
        "enum compares arrays elementwise without coercion",
        not grade("[true]", {"enum": [[1]]}).passed,
    )
    check(
        CID,
        "enum compares objects without coercion",
        not grade('{"a": true}', {"enum": [{"a": 1}]}).passed,
    )
    check(
        CID,
        "enum accepts a structurally equal object",
        grade('{"a": [1, 2]}', {"enum": [{"a": [1, 2]}]}).passed,
    )
    check(
        CID,
        "const null matches only null",
        grade("null", {"const": None}).passed and not grade("0", {"const": None}).passed,
    )
    check(CID, "const rejects a longer array", not grade("[1, 2]", {"const": [1]}).passed)
    check(
        CID,
        "const rejects an object with an extra key",
        not grade('{"a": 1, "b": 2}', {"const": {"a": 1}}).passed,
    )
    check(
        CID,
        "const rejects an object with a different key",
        not grade('{"b": 1}', {"const": {"a": 1}}).passed,
    )


def test_duplicates_preserved_in_arrays() -> None:
    schema = {"type": "array", "items": {"type": "integer"}, "uniqueItems": True}
    check(CID, "uniqueItems rejects an exact duplicate", not grade("[1, 2, 1]", schema).passed)
    check(CID, "uniqueItems treats 1 and 1.0 as duplicates", not grade("[1, 1.0]", schema).passed)
    check(CID, "uniqueItems accepts distinct items", grade("[1, 2, 3]", schema).passed)
    loose = {"type": "array", "items": {"type": "integer"}}
    check(CID, "without uniqueItems duplicates are kept and pass", grade("[1, 1]", loose).passed)
    mixed = {"type": "array", "uniqueItems": True}
    check(CID, "uniqueItems does not conflate true and 1", grade("[true, 1]", mixed).passed)
    check(CID, "uniqueItems does not conflate [] and {}", grade("[[], {}]", mixed).passed)
    check(
        CID,
        "uniqueItems detects duplicate objects regardless of key order",
        not grade('[{"a": 1, "b": 2}, {"b": 2, "a": 1}]', mixed).passed,
    )
    many = grade("[1, 1, 1]", schema)
    check(
        CID,
        "every duplicate occurrence is reported",
        [i.message for i in many.issues if i.keyword == "uniqueItems"].__len__() == 2,
    )
    errors = grade(
        '{"items": [1, "a", "b"]}',
        {
            "type": "object",
            "properties": {"items": {"type": "array", "items": {"type": "integer"}}},
            "additionalProperties": False,
        },
    )
    check(
        CID,
        "sibling failures at distinct paths are all kept",
        [i.path for i in errors.issues] == ["/items/1", "/items/2"],
    )


def test_all_supported_keywords_enforced() -> None:
    cases: list[tuple[str, dict[str, Any], str, bool]] = [
        ("minLength", {"minLength": 2}, '"a"', False),
        ("minLength ok", {"minLength": 2}, '"ab"', True),
        ("maxLength", {"maxLength": 2}, '"abc"', False),
        ("maxLength ok", {"maxLength": 2}, '"ab"', True),
        ("minLength counts code points", {"minLength": 2}, '"\\ud83d\\ude00"', False),
        ("minimum", {"minimum": 5}, "4", False),
        ("minimum boundary", {"minimum": 5}, "5", True),
        ("maximum", {"maximum": 5}, "6", False),
        ("maximum boundary", {"maximum": 5}, "5", True),
        ("exclusiveMinimum", {"exclusiveMinimum": 5}, "5", False),
        ("exclusiveMinimum ok", {"exclusiveMinimum": 5}, "5.1", True),
        ("exclusiveMaximum", {"exclusiveMaximum": 5}, "5", False),
        ("exclusiveMaximum ok", {"exclusiveMaximum": 5}, "4.9", True),
        ("minItems", {"minItems": 2}, "[1]", False),
        ("minItems ok", {"minItems": 2}, "[1, 2]", True),
        ("maxItems", {"maxItems": 1}, "[1, 2]", False),
        ("maxItems ok", {"maxItems": 1}, "[1]", True),
        (
            "required",
            {"type": "object", "required": ["a"], "additionalProperties": True},
            "{}",
            False,
        ),
        ("anyOf none", {"anyOf": [{"type": "string"}, {"type": "null"}]}, "1", False),
        ("anyOf one", {"anyOf": [{"type": "string"}, {"type": "null"}]}, "null", True),
        ("allOf one fails", {"allOf": [{"type": "integer"}, {"minimum": 5}]}, "3", False),
        ("allOf all pass", {"allOf": [{"type": "integer"}, {"minimum": 5}]}, "7", True),
        ("items", {"items": {"type": "string"}}, '["a", 1]', False),
        ("string keywords ignore numbers", {"minLength": 5}, "1", True),
        ("number keywords ignore strings", {"minimum": 5}, '"a"', True),
        ("array keywords ignore objects", {"minItems": 5}, "{}", True),
        ("number keywords ignore booleans", {"minimum": 5}, "false", True),
    ]
    for label, schema, raw, expected in cases:
        check(CID, f"keyword case '{label}' -> {expected}", grade(raw, schema).passed is expected)


def test_multi_constraint_all_issues_reported() -> None:
    schema = {"type": "string", "minLength": 5, "maxLength": 2, "enum": ["zzz"]}
    keywords = sorted(i.keyword for i in grade('"abc"', schema).issues)
    check(
        CID,
        "every violated constraint is reported, none short-circuited",
        keywords == ["enum", "maxLength", "minLength"],
    )
    check(
        CID,
        "anyOf branches do not leak their issues",
        [i.keyword for i in grade("1", {"anyOf": [{"type": "string"}, {"type": "null"}]}).issues]
        == ["anyOf"],
    )


def test_size_and_depth_limits_fail_closed() -> None:
    big = '"' + "a" * MAX_OUTPUT_BYTES + '"'
    check(CID, "oversized output fails", not grade(big, {"type": "string"}).passed)
    exact = '"' + "a" * (MAX_OUTPUT_BYTES - 2) + '"'
    check(
        CID,
        "output exactly at the limit is still gradable",
        grade(exact, {"type": "string"}).passed,
    )
    deep = "[" * (MAX_INSTANCE_DEPTH + 1) + "]" * (MAX_INSTANCE_DEPTH + 1)
    check(CID, "over-deep nesting fails", not grade(deep, {"type": "array"}).passed)
    ok_deep = "[" * MAX_INSTANCE_DEPTH + "]" * MAX_INSTANCE_DEPTH
    check(CID, "nesting at the limit is allowed", grade(ok_deep, {"type": "array"}).passed)
    huge = "[" * 100_000 + "]" * 100_000
    check(
        CID,
        "pathological nesting fails without crashing",
        not grade(huge, {"type": "array"}).passed,
    )
    multibyte = '"' + "é" * (MAX_OUTPUT_BYTES // 2) + '"'
    check(
        CID,
        "the size limit is measured in bytes, not characters",
        not grade(multibyte, {"type": "string"}).passed,
    )


def test_json_pointer_escaping_in_paths() -> None:
    schema = {
        "type": "object",
        "properties": {"a/b": {"type": "string"}, "c~d": {"type": "string"}},
        "additionalProperties": False,
    }
    paths = sorted(i.path for i in grade('{"a/b": 1, "c~d": 2}', schema).issues)
    check(CID, "paths use RFC 6901 escaping", paths == ["/a~1b", "/c~0d"])


def test_json_roundtrip_sanity() -> None:
    check(
        CID,
        "test schema is itself canonical JSON",
        json.loads(json.dumps(ORDER_SCHEMA)) == ORDER_SCHEMA,
    )


def test_bool_and_null_equality_both_ways() -> None:
    check(CID, "enum [true] accepts true", grade("true", {"enum": [True]}).passed)
    check(CID, "const false accepts false", grade("false", {"const": False}).passed)
    check(CID, "const true rejects false", not grade("false", {"const": True}).passed)
    check(CID, "const 1 rejects 2", not grade("2", {"const": 1}).passed)
    check(CID, "const 1.5 accepts 1.5", grade("1.5", {"const": 1.5}).passed)
    check(CID, "const string rejects a number", not grade("1", {"const": "1"}).passed)
    check(CID, "const string accepts an equal string", grade('"1"', {"const": "1"}).passed)
    check(CID, "const [true] accepts [true]", grade("[true]", {"const": [True]}).passed)
    check(CID, "const {} accepts {}", grade("{}", {"const": {}}).passed)


def test_zero_bounds_are_legal_and_enforced() -> None:
    check(CID, "minItems 0 is legal", grade("[]", {"minItems": 0}).passed)
    check(CID, "minLength 0 is legal", grade('""', {"minLength": 0}).passed)
    check(
        CID,
        "maxItems 0 admits only the empty array",
        grade("[]", {"maxItems": 0}).passed and not grade("[1]", {"maxItems": 0}).passed,
    )
    check(
        CID,
        "maxLength 0 admits only the empty string",
        grade('""', {"maxLength": 0}).passed and not grade('"a"', {"maxLength": 0}).passed,
    )


def _nested_all_of(levels: int) -> dict[str, Any]:
    schema: dict[str, Any] = {"type": "string"}
    for _ in range(levels):
        schema = {"allOf": [schema]}
    return schema


def test_schema_depth_limit_is_independent_of_the_candidate() -> None:
    # The deep branch sits behind an anyOf whose first branch the candidate
    # satisfies, so only the up-front schema check can ever see it.
    def wrapped(levels: int) -> dict[str, Any]:
        return {"anyOf": [{"type": "string"}, _nested_all_of(levels)]}

    check(CID, "a schema at exactly the depth limit is accepted", grade('"a"', wrapped(255)).passed)
    check(
        CID,
        "a schema one level past the limit is refused",
        raises(SchemaError, lambda: grade('"a"', wrapped(256))),
    )
    check(
        CID,
        "an absurdly deep schema is refused, not a crash",
        raises(SchemaError, lambda: grade('"a"', _nested_all_of(5000))),
    )


def test_non_string_schema_keys_are_refused() -> None:
    schema: dict[Any, Any] = {1: "x", "type": "string"}
    check(
        CID,
        "a non-string key in a schema is refused",
        raises(SchemaError, lambda: grade('"a"', schema)),
    )


def test_dialect_is_exact() -> None:
    base = "https://json-schema.org/draft/2020-12/schema"
    check(
        CID,
        "the pinned dialect is accepted",
        grade('"a"', {"$schema": base, "type": "string"}).passed,
    )
    for variant in (" " + base, base + " ", base.upper(), base + "#", "", None, 7):
        schema = {"$schema": variant, "type": "string"}
        check(
            CID,
            f"$schema {variant!r} is refused",
            raises(SchemaError, lambda schema=schema: grade('"a"', schema)),
        )


def test_type_names_are_case_and_whitespace_exact() -> None:
    for name in ("String", "STRING", " string", "string ", "Null", "INTEGER", "Object"):
        schema = {"type": name}
        check(
            CID,
            f"type {name!r} is refused, never normalised",
            raises(SchemaError, lambda schema=schema: grade('"a"', schema)),
        )
    check(
        CID,
        "type list members are held to the same rule",
        raises(SchemaError, lambda: grade('"a"', {"type": ["string", "Null"]})),
    )


def test_property_names_with_whitespace_are_legal_and_exact() -> None:
    schema = {
        "type": "object",
        "properties": {" ": {"type": "string"}},
        "required": [" "],
        "additionalProperties": False,
    }
    check(
        CID,
        "a whitespace-only property name is a legal required name",
        grade('{" ": "a"}', schema).passed,
    )
    check(CID, "...and is not satisfied by a different key", not grade('{"": "a"}', schema).passed)


def test_additional_properties_value_is_validated() -> None:
    extras: list[Any] = [{"type": "str"}, 5, "x", None, []]
    for extra in extras:
        schema = {"type": "object", "additionalProperties": extra}
        try:
            pin = schema_fingerprint(schema)
        except SchemaError:
            continue
        check(
            CID,
            f"additionalProperties {extra!r} is refused",
            raises(
                SchemaError,
                lambda schema=schema, pin=pin: grade_json_schema(
                    "{}", schema, expected_schema_sha256=pin
                ),
            ),
        )


def test_numeric_bounds_values_are_validated() -> None:
    for keyword in ("minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum"):
        for bad in ("1", True, None, [1]):
            schema = {keyword: bad}
            check(
                CID,
                f"{keyword}={bad!r} is refused",
                raises(SchemaError, lambda schema=schema: grade("1", schema)),
            )


def test_nan_infinity_rejected_even_when_the_schema_would_accept_anything() -> None:
    for raw in ("NaN", "Infinity", "-Infinity", "[NaN]", '{"a": Infinity}'):
        result = grade(raw, {})
        check(
            CID,
            f"{raw} fails under a permissive schema",
            not result.passed and result.issues[0].keyword == "json",
        )
    check(CID, "a permissive schema accepts ordinary JSON", grade('{"a": [1, null]}', {}).passed)


def test_ref_target_names_are_case_and_whitespace_exact() -> None:
    defs = {"Node": {"type": "string"}, " pad ": {"type": "integer"}}
    check(
        CID,
        "a mixed-case def is referenced exactly",
        grade('"a"', {"$defs": defs, "$ref": "#/$defs/Node"}).passed,
    )
    check(
        CID,
        "a padded def is referenced exactly",
        grade("1", {"$defs": defs, "$ref": "#/$defs/ pad "}).passed,
    )
    check(
        CID,
        "a different case is a different (undefined) def",
        raises(SchemaError, lambda: grade('"a"', {"$defs": defs, "$ref": "#/$defs/node"})),
    )
    check(
        CID,
        "different padding is a different (undefined) def",
        raises(SchemaError, lambda: grade("1", {"$defs": defs, "$ref": "#/$defs/pad"})),
    )


def test_validation_depth_boundary_when_the_candidate_walks_the_schema() -> None:
    check(
        CID,
        "a schema walked to exactly the depth limit passes",
        grade('"a"', _nested_all_of(256)).passed,
    )
    check(
        CID,
        "one level past the limit is refused",
        raises(SchemaError, lambda: grade('"a"', _nested_all_of(257))),
    )


def main() -> int:
    tests = [
        (name, fn)
        for name, fn in sorted(globals().items())
        if name.startswith("test_") and callable(fn)
    ]
    for _name, fn in tests:
        fn()
    if FAILURES:
        for failure in FAILURES:
            print(failure)
        print(f"{len(FAILURES)} check(s) failed")
        return 1
    print("all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
