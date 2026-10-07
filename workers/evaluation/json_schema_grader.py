"""JSON-schema grader (FTF-0605, Section 23/24/26/29/69).

Section 23's grader-type list names `json_schema` as one of the deterministic
grader types; this module implements exactly that one grader, standing alone
from the sibling grader-type tasks (`exact_match`/`tool_name`/
`tool_sequence`/...), each its own separately dependency-gated FTF-06xx task.

This task's own acceptance clause ("Validate actual output against pinned JSON
Schema with deterministic result/errors") shapes the design:

  - **pinned schema**: `expected_schema_sha256` is a required keyword-only
    argument with no default. The grader hashes the canonical form of the
    schema it is handed (`schema_fingerprint`) and refuses to grade when the
    hash differs, so a schema that drifted after it was pinned can never
    silently grade a candidate.

  - **deterministic result/errors**: the result carries the sorted tuple of
    `(path, keyword, message)` errors. Nothing depends on dict ordering, hash
    seeds, time or I/O, and messages never echo candidate values (content-free
    events, Section 66/observability clause).

This task's own negative_acceptance clause ("Malformed JSON/prose, forbidden
extras and schema reference attacks fail; no silent repair to pass") shapes
the rest:

  - **no repair**: the candidate is parsed with `json.loads` as-is. Prose,
    a fenced code block, trailing text, a bare empty string, `NaN`/`Infinity`,
    out-of-range floats and duplicate object keys are all a failed grade; the
    grader never strips fences, extracts the first object, or de-duplicates.

  - **forbidden extras**: every object-typed subschema must state
    `additionalProperties` explicitly (a `SchemaError` otherwise), because
    JSON Schema's own default silently allows extras. `false` rejects them.

  - **reference attacks**: the only `$ref` form accepted is a local
    `#/$defs/<name>` whose target exists in the root schema's `$defs`. Remote
    (`http://`, `file:`), root (`#`), JSON-pointer-into-keyword,
    `$dynamicRef`/`$anchor`/`$id` and any other reference form is a
    `SchemaError`, as is a reference loop (a recursion budget is enforced).

  - **closed keyword set, fail closed**: only the keywords in
    `SUPPORTED_KEYWORDS` are interpreted. Any other keyword (`pattern`,
    `format`, `oneOf`, `not`, `if`, ...) is a `SchemaError`; this grader never
    ignores a constraint and then reports a pass.

A `SchemaError`/`JsonSchemaGraderError` means the grader was called with a
malformed schema/pin/candidate type -- never a normal graded failure. A
candidate that merely fails the schema is a normal `passed=False` result.

`workers/evaluation` is this repository's real evaluation-worker root
(FTF-0001); the packet's generic `services/evaluation-worker/evaluators/`
suggestion names a tree that does not exist here (same refinement as
FTF-0604/FTF-0606/FTF-0608).

Scope decision, same disclosed "no live caller yet" precedent as the sibling
graders: Section 25's evaluation runtime loop that would call a grader is not
owned by any dependency-ready task yet, so `proving_symbol` stays blank. This
task's `database_changes` clause means no schema/table/bucket surface is added.
No third-party dependency is added: the validator is stdlib-only.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import math
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

__all__ = [
    "MAX_OUTPUT_BYTES",
    "MAX_INSTANCE_DEPTH",
    "SUPPORTED_DIALECT",
    "SUPPORTED_KEYWORDS",
    "JsonSchemaGraderError",
    "JsonSchemaResult",
    "SchemaError",
    "ValidationIssue",
    "grade_json_schema",
    "parse_strict_json",
    "schema_fingerprint",
]

SUPPORTED_DIALECT = "https://json-schema.org/draft/2020-12/schema"
_DIALECTS = (SUPPORTED_DIALECT,)
MAX_OUTPUT_BYTES = 1_000_000
MAX_INSTANCE_DEPTH = 32
_MAX_VALIDATION_DEPTH = 256

_ANNOTATIONS = frozenset({"title", "description", "$comment", "examples", "default"})
SUPPORTED_KEYWORDS = frozenset(
    {
        "$schema",
        "$defs",
        "$ref",
        "type",
        "enum",
        "const",
        "properties",
        "required",
        "additionalProperties",
        "items",
        "minItems",
        "maxItems",
        "uniqueItems",
        "minLength",
        "maxLength",
        "minimum",
        "maximum",
        "exclusiveMinimum",
        "exclusiveMaximum",
        "anyOf",
        "allOf",
    }
    | _ANNOTATIONS
)
_REF_PREFIX = "#/$defs/"


class JsonSchemaGraderError(ValueError):
    """The grader was called with a malformed candidate type or schema pin.
    Never raised for a candidate that merely fails the schema."""


class SchemaError(JsonSchemaGraderError):
    """The schema itself is malformed, unsupported, or unsafe to evaluate."""


@dataclass(frozen=True, order=True)
class ValidationIssue:
    path: str
    keyword: str
    message: str


@dataclass(frozen=True)
class JsonSchemaResult:
    passed: bool
    issues: tuple[ValidationIssue, ...]
    schema_sha256: str


def schema_fingerprint(schema: Mapping[str, Any]) -> str:
    """SHA-256 of the canonical JSON form of `schema` (sorted keys, compact
    separators, ASCII-escaped, NaN/Infinity refused)."""
    try:
        canonical = json.dumps(
            schema, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False
        )
    except (TypeError, ValueError, RecursionError) as exc:
        raise SchemaError(f"schema is not canonical JSON: {exc}") from exc
    return hashlib.sha256(canonical.encode("ascii")).hexdigest()


def _is_number(value: Any) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool)


def _json_equal(left: Any, right: Any) -> bool:
    """JSON-value equality: `true` is never `1`, `1` equals `1.0`, no coercion."""
    if _is_number(left):
        return _is_number(right) and bool(left == right)
    if isinstance(left, list):
        return (
            isinstance(right, list)
            and len(left) == len(right)
            and all(_json_equal(a, b) for a, b in zip(left, right, strict=True))
        )
    if isinstance(left, dict):
        return (
            isinstance(right, dict)
            and left.keys() == right.keys()
            and all(_json_equal(left[key], right[key]) for key in left)
        )
    # str, None and bool: same exact type and equal (`True` is never `1`: the
    # number branch above excludes bool and `type(True) is not type(1)`).
    return type(left) is type(right) and bool(left == right)


# One closed dispatch table decides every type name (no per-name comparisons).
_TYPE_CHECKERS: dict[str, Callable[[Any], bool]] = {
    "null": lambda value: value is None,
    "boolean": lambda value: isinstance(value, bool),
    "object": lambda value: isinstance(value, dict),
    "array": lambda value: isinstance(value, list),
    "string": lambda value: isinstance(value, str),
    "number": _is_number,
    # JSON Schema 2020-12 treats 1.0 as an integer.
    "integer": lambda value: (isinstance(value, int) and not isinstance(value, bool))
    or (isinstance(value, float) and math.isfinite(value) and value.is_integer()),
}


def _expect_nonneg_int(schema: Mapping[str, Any], keyword: str, where: str) -> None:
    value = schema[keyword]
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise SchemaError(f"{where}: {keyword} must be a non-negative integer")


def _expect_number(schema: Mapping[str, Any], keyword: str, where: str) -> None:
    # Non-finite floats never reach here: `schema_fingerprint` (called before
    # the schema is checked) refuses NaN/Infinity. Deliberately no
    # `math.isfinite` call: it raises OverflowError on an int beyond float range.
    if not _is_number(schema[keyword]):
        raise SchemaError(f"{where}: {keyword} must be a number")


def _check_schema(schema: Any, where: str, defs: Mapping[str, Any], depth: int) -> None:
    """Rejects a malformed/unsupported schema up front, independent of any
    candidate, so a schema defect can never be masked by a lucky input."""
    if depth > _MAX_VALIDATION_DEPTH:
        raise SchemaError(f"{where}: schema nesting exceeds {_MAX_VALIDATION_DEPTH}")
    if not isinstance(schema, dict):
        raise SchemaError(f"{where}: a schema must be an object, not {type(schema).__name__}")
    for keyword in schema:
        if keyword not in SUPPORTED_KEYWORDS:
            raise SchemaError(f"{where}: unsupported keyword {keyword!r} (fail closed)")
    if "$schema" in schema and schema["$schema"] not in _DIALECTS:
        raise SchemaError(f"{where}: $schema must be {SUPPORTED_DIALECT!r}")
    if "$ref" in schema:
        ref = schema["$ref"]
        if not isinstance(ref, str) or not ref.startswith(_REF_PREFIX):
            raise SchemaError(f"{where}: only local '#/$defs/<name>' references are allowed")
        name = ref[len(_REF_PREFIX) :]
        if name not in defs:
            raise SchemaError(f"{where}: $ref target {ref!r} is not defined in $defs")
    if "$defs" in schema:
        if not isinstance(schema["$defs"], dict):
            raise SchemaError(f"{where}: $defs must be an object")
        if depth != 0:
            raise SchemaError(f"{where}: $defs is only allowed at the schema root")
        for name, sub in schema["$defs"].items():
            _check_schema(sub, f"{where}/$defs/{name}", defs, depth + 1)
    if "type" in schema:
        declared = schema["type"]
        names = [declared] if isinstance(declared, str) else declared
        if (
            not isinstance(names, list)
            or not names
            or any(not isinstance(n, str) or n not in _TYPE_CHECKERS for n in names)
        ):
            raise SchemaError(
                f"{where}: type must be one of {tuple(_TYPE_CHECKERS)} or a non-empty list of them"
            )
    if "enum" in schema and (not isinstance(schema["enum"], list) or not schema["enum"]):
        raise SchemaError(f"{where}: enum must be a non-empty array")
    if "required" in schema:
        required = schema["required"]
        if not isinstance(required, list) or any(
            not isinstance(name, str) or name == "" for name in required
        ):
            raise SchemaError(f"{where}: required must be an array of non-empty strings")
    if "properties" in schema:
        if not isinstance(schema["properties"], dict):
            raise SchemaError(f"{where}: properties must be an object")
        for name, sub in schema["properties"].items():
            _check_schema(sub, f"{where}/properties/{name}", defs, depth + 1)
    if "additionalProperties" in schema:
        extra = schema["additionalProperties"]
        if not isinstance(extra, bool):
            _check_schema(extra, f"{where}/additionalProperties", defs, depth + 1)
    if "items" in schema:
        _check_schema(schema["items"], f"{where}/items", defs, depth + 1)
    for keyword in ("anyOf", "allOf"):
        if keyword in schema:
            branches = schema[keyword]
            if not isinstance(branches, list) or not branches:
                raise SchemaError(f"{where}: {keyword} must be a non-empty array")
            for index, sub in enumerate(branches):
                _check_schema(sub, f"{where}/{keyword}/{index}", defs, depth + 1)
    for keyword in ("minItems", "maxItems", "minLength", "maxLength"):
        if keyword in schema:
            _expect_nonneg_int(schema, keyword, where)
    for keyword in ("minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum"):
        if keyword in schema:
            _expect_number(schema, keyword, where)
    if "uniqueItems" in schema and not isinstance(schema["uniqueItems"], bool):
        raise SchemaError(f"{where}: uniqueItems must be a boolean")
    declares_object = "properties" in schema or (
        "type" in schema
        and "object" in ([schema["type"]] if isinstance(schema["type"], str) else schema["type"])
    )
    if declares_object and "additionalProperties" not in schema:
        raise SchemaError(
            f"{where}: an object schema must state additionalProperties explicitly "
            "(the JSON Schema default silently allows extras)"
        )


def _pointer(path: str, token: str | int) -> str:
    escaped = str(token).replace("~", "~0").replace("/", "~1")
    return f"{path}/{escaped}"


def _validate(
    instance: Any,
    schema: Mapping[str, Any],
    path: str,
    defs: Mapping[str, Any],
    depth: int,
    issues: list[ValidationIssue],
) -> None:
    if depth > _MAX_VALIDATION_DEPTH:
        raise SchemaError(f"schema reference/nesting depth exceeds {_MAX_VALIDATION_DEPTH}")

    def fail(keyword: str, message: str) -> None:
        issues.append(ValidationIssue(path or "/", keyword, message))

    if "$ref" in schema:
        target = defs[schema["$ref"][len(_REF_PREFIX) :]]
        _validate(instance, target, path, defs, depth + 1, issues)
    if "type" in schema:
        declared = schema["type"]
        names = [declared] if isinstance(declared, str) else declared
        if not any(_TYPE_CHECKERS[name](instance) for name in names):
            fail("type", f"value is not of type {sorted(names)}")
    if "enum" in schema and not any(_json_equal(instance, option) for option in schema["enum"]):
        fail("enum", "value is not one of the allowed values")
    if "const" in schema and not _json_equal(instance, schema["const"]):
        fail("const", "value does not equal the required constant")
    if isinstance(instance, str):
        if "minLength" in schema and len(instance) < schema["minLength"]:
            fail("minLength", f"string is shorter than {schema['minLength']}")
        if "maxLength" in schema and len(instance) > schema["maxLength"]:
            fail("maxLength", f"string is longer than {schema['maxLength']}")
    if _is_number(instance):
        if "minimum" in schema and instance < schema["minimum"]:
            fail("minimum", f"number is below {schema['minimum']}")
        if "maximum" in schema and instance > schema["maximum"]:
            fail("maximum", f"number is above {schema['maximum']}")
        if "exclusiveMinimum" in schema and instance <= schema["exclusiveMinimum"]:
            fail("exclusiveMinimum", f"number is not above {schema['exclusiveMinimum']}")
        if "exclusiveMaximum" in schema and instance >= schema["exclusiveMaximum"]:
            fail("exclusiveMaximum", f"number is not below {schema['exclusiveMaximum']}")
    if isinstance(instance, list):
        if "minItems" in schema and len(instance) < schema["minItems"]:
            fail("minItems", f"array has fewer than {schema['minItems']} items")
        if "maxItems" in schema and len(instance) > schema["maxItems"]:
            fail("maxItems", f"array has more than {schema['maxItems']} items")
        if schema.get("uniqueItems") is True:
            for index, item in enumerate(instance):
                if any(_json_equal(item, earlier) for earlier in instance[:index]):
                    fail("uniqueItems", f"item {index} duplicates an earlier item")
        if "items" in schema:
            for index, item in enumerate(instance):
                _validate(item, schema["items"], _pointer(path, index), defs, depth + 1, issues)
    if isinstance(instance, dict):
        for name in schema.get("required", []):
            if name not in instance:
                fail("required", f"missing required property {name!r}")
        declared_properties = schema.get("properties", {})
        for name, value in instance.items():
            if name in declared_properties:
                _validate(
                    value, declared_properties[name], _pointer(path, name), defs, depth + 1, issues
                )
            elif "additionalProperties" in schema:
                extra = schema["additionalProperties"]
                if extra is False:
                    issues.append(
                        ValidationIssue(
                            _pointer(path, name),
                            "additionalProperties",
                            "additional property is not allowed",
                        )
                    )
                elif extra is not True:
                    _validate(value, extra, _pointer(path, name), defs, depth + 1, issues)
    if "allOf" in schema:
        for branch in schema["allOf"]:
            _validate(instance, branch, path, defs, depth + 1, issues)
    if "anyOf" in schema:
        satisfied = False
        for branch in schema["anyOf"]:
            branch_issues: list[ValidationIssue] = []
            _validate(instance, branch, path, defs, depth + 1, branch_issues)
            if not branch_issues:
                satisfied = True
                break
        if not satisfied:
            fail("anyOf", "value matches none of the allowed alternatives")


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    seen: dict[str, Any] = {}
    for key, value in pairs:
        if key in seen:
            raise ValueError("duplicate object key")
        seen[key] = value
    return seen


def _reject_constant(_name: str) -> Any:
    raise ValueError("NaN/Infinity are not JSON")


def _finite_float(text: str) -> float:
    value = float(text)
    if not math.isfinite(value):
        raise ValueError("float out of range")
    return value


def _depth_exceeds(value: Any, limit: int) -> bool:
    stack: list[tuple[Any, int]] = [(value, 1)]
    while stack:
        current, depth = stack.pop()
        if isinstance(current, dict | list):
            if depth > limit:
                return True
            children = current.values() if isinstance(current, dict) else current
            stack.extend((child, depth + 1) for child in children)
    return False


def parse_strict_json(raw_output: str) -> tuple[bool, Any, str]:
    """Returns (ok, value, reason). Never repairs: the text is parsed as-is."""
    if len(raw_output.encode("utf-8", errors="surrogatepass")) > MAX_OUTPUT_BYTES:
        return False, None, "output exceeds the size limit"
    try:
        value = json.loads(
            raw_output,
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_constant,
            parse_float=_finite_float,
        )
    except RecursionError:
        return False, None, "output nesting is too deep"
    except ValueError:
        return False, None, "output is not a single valid JSON document"
    if _depth_exceeds(value, MAX_INSTANCE_DEPTH):
        return False, None, f"output nesting exceeds {MAX_INSTANCE_DEPTH}"
    return True, value, ""


def grade_json_schema(
    raw_output: str,
    schema: Mapping[str, Any],
    *,
    expected_schema_sha256: str,
) -> JsonSchemaResult:
    """Grades `raw_output` (the model's actual, unmodified text) against the
    pinned `schema`. Returns a result for any candidate (a failing candidate is
    `passed=False`, never an exception); raises `JsonSchemaGraderError` /
    `SchemaError` only for a malformed call, schema, or pin."""
    if not isinstance(raw_output, str):
        raise JsonSchemaGraderError(f"raw_output must be a string, not {type(raw_output).__name__}")
    if not isinstance(schema, dict):
        raise SchemaError(f"schema must be an object, not {type(schema).__name__}")
    actual_sha256 = schema_fingerprint(schema)
    if not isinstance(expected_schema_sha256, str) or not hmac.compare_digest(
        actual_sha256.encode("ascii"),
        expected_schema_sha256.encode("utf-8", errors="surrogatepass"),
    ):
        raise JsonSchemaGraderError(
            "schema does not match the pinned digest "
            f"(expected {expected_schema_sha256!r}, got {actual_sha256})"
        )
    defs = schema.get("$defs", {})
    _check_schema(schema, "#", defs if isinstance(defs, dict) else {}, 0)

    ok, value, reason = parse_strict_json(raw_output)
    if not ok:
        return JsonSchemaResult(
            passed=False,
            issues=(ValidationIssue("/", "json", reason),),
            schema_sha256=actual_sha256,
        )
    issues: list[ValidationIssue] = []
    _validate(value, schema, "", defs, 0, issues)
    return JsonSchemaResult(
        passed=not issues, issues=tuple(sorted(issues)), schema_sha256=actual_sha256
    )
