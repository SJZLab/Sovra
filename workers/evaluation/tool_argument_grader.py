"""Tool-argument grader (FTF-0607, Section 23/24/26/29/69).

Section 23's grader-type list names `tool_arguments`; this module implements
exactly that grader, standing alone from the sibling grader-type tasks. It
grades the arguments one real tool call carried against an explicit contract.

Acceptance ("Check typed arguments and required identities/tolerances against
expected contract"):

  - every parameter has one explicit spec from a closed set: `Identity` (a
    required ID, exact), `Exact` (any JSON value, exact), `NumberWithin` (a
    number inside an explicit absolute tolerance), `OneOf` (one of several
    exact values) and `OfType` (typed, any value of that JSON type);
  - `allow_extra` is a required keyword with no default, so a caller always
    states whether undeclared parameters are permitted; when `False` every
    extra parameter is a failure;
  - the call's tool name must equal the contract's, exactly.

Negative acceptance ("Numeric/string coercion or swapped customer/order IDs
cannot pass on superficial schema validity alone"):

  - comparison is strictly typed: `"5"` is not `5`, `5` is not `5.0`, `true`
    is not `1`, and none of these is repaired. A numeric tolerance only ever
    applies to a real JSON number, never to a numeric-looking string;
  - each identity is compared with its own expected value, so a customer ID
    placed in the order-ID parameter (both strings, both schema-valid) fails,
    and the result names the swap (`swapped_identity`) when the wrong value
    is another declared identity's expected value.

The arguments are the candidate's own raw JSON text and are parsed with
`json_schema_grader.parse_strict_json` (duplicate keys, NaN/Infinity, prose,
a non-object root are failed grades, never repaired). Issues are a sorted
tuple of `(parameter, keyword, message)` and never echo argument values.

A malformed contract or call record raises `ToolArgumentGraderError`; a
non-compliant call is a normal `passed=False` result. `workers/evaluation` is
this repository's real evaluation-worker root. No live caller yet (Section
25's runtime loop is not a dependency-ready task); `proving_symbol` stays
blank, same precedent as the sibling graders. No schema change, no new
dependency.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from workers.evaluation.json_schema_grader import parse_strict_json

__all__ = [
    "ArgumentContract",
    "ArgumentIssue",
    "Exact",
    "Identity",
    "NumberWithin",
    "OfType",
    "OneOf",
    "ToolArgumentGraderError",
    "ToolArgumentResult",
    "ToolCallArguments",
    "grade_tool_arguments",
]


class ToolArgumentGraderError(ValueError):
    """The grader was called with a malformed contract or call record. Never
    raised for a call that merely fails the contract."""


@dataclass(frozen=True)
class ToolCallArguments:
    """One real tool invocation: the runtime's call identity, the exact tool
    name and the candidate's raw JSON arguments text (never a free-text
    mention, never pre-parsed by something that could have deduplicated keys)."""

    tool_call_id: str
    name: str
    arguments_json: str

    def __post_init__(self) -> None:
        for field_name in ("tool_call_id", "name"):
            value = getattr(self, field_name)
            if not isinstance(value, str) or value == "":
                raise ToolArgumentGraderError(f"{field_name} must be a non-empty string")
        if not isinstance(self.arguments_json, str):
            raise ToolArgumentGraderError("arguments_json must be a string")


def _is_number(value: Any) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool)


def _is_finite_number(value: Any) -> bool:
    """A real JSON number that is finite. Ints are always finite (and
    `math.isfinite` raises OverflowError on an int beyond float range)."""
    return _is_number(value) and (isinstance(value, int) or math.isfinite(value))


def _is_json_value(value: Any) -> bool:
    if value is None or isinstance(value, bool | int | str):
        return True
    if isinstance(value, float):
        return math.isfinite(value)
    if isinstance(value, list):
        return all(_is_json_value(item) for item in value)
    if isinstance(value, dict):
        return all(isinstance(key, str) and _is_json_value(item) for key, item in value.items())
    return False


def _strict_equal(left: Any, right: Any) -> bool:
    """Typed JSON equality: no coercion between str/int/float/bool/None."""
    if type(left) is not type(right):
        return False
    if isinstance(left, dict):
        return left.keys() == right.keys() and all(_strict_equal(left[k], right[k]) for k in left)
    if isinstance(left, list):
        return len(left) == len(right) and all(
            _strict_equal(a, b) for a, b in zip(left, right, strict=True)
        )
    return bool(left == right)


# Strict JSON types: an integer is an int (not 1.0), a number is a finite
# int/float, and a bool is never a number.
_TYPE_CHECKERS: dict[str, Callable[[Any], bool]] = {
    "string": lambda value: isinstance(value, str),
    "integer": lambda value: isinstance(value, int) and not isinstance(value, bool),
    "number": _is_finite_number,
    "boolean": lambda value: isinstance(value, bool),
    "object": lambda value: isinstance(value, dict),
    "array": lambda value: isinstance(value, list),
    "null": lambda value: value is None,
}


@dataclass(frozen=True)
class Identity:
    """A required identity (customer/order/account ID): a non-empty string or
    an integer, compared exactly and typed."""

    value: str | int

    def __post_init__(self) -> None:
        if isinstance(self.value, bool) or not isinstance(self.value, str | int):
            raise ToolArgumentGraderError("Identity value must be a string or an integer")
        if self.value == "":
            raise ToolArgumentGraderError("Identity value must not be empty")

    def matches(self, actual: Any) -> bool:
        return _strict_equal(actual, self.value)


@dataclass(frozen=True)
class Exact:
    """Any JSON value, compared exactly and typed (nested values included)."""

    value: Any

    def __post_init__(self) -> None:
        if not _is_json_value(self.value):
            raise ToolArgumentGraderError("Exact value must be a finite JSON value")

    def matches(self, actual: Any) -> bool:
        return _strict_equal(actual, self.value)


@dataclass(frozen=True)
class OneOf:
    """One of several exact, typed values."""

    values: tuple[Any, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.values, tuple) or not self.values:
            raise ToolArgumentGraderError("OneOf values must be a non-empty tuple")
        if not all(_is_json_value(value) for value in self.values):
            raise ToolArgumentGraderError("OneOf values must be finite JSON values")

    def matches(self, actual: Any) -> bool:
        return any(_strict_equal(actual, value) for value in self.values)


@dataclass(frozen=True)
class NumberWithin:
    """A real JSON number within an explicit, required absolute tolerance
    (0 means exact). A numeric-looking string is never a number."""

    expected: int | float
    abs_tol: int | float

    def __post_init__(self) -> None:
        if not _is_finite_number(self.expected):
            raise ToolArgumentGraderError("NumberWithin expected must be a finite number")
        if not _is_finite_number(self.abs_tol) or self.abs_tol < 0:
            raise ToolArgumentGraderError("NumberWithin abs_tol must be a finite number >= 0")

    def matches(self, actual: Any) -> bool:
        if not _is_finite_number(actual):
            return False
        try:
            return bool(abs(actual - self.expected) <= self.abs_tol)
        except OverflowError:  # an int beyond float range against a float operand
            return False


@dataclass(frozen=True)
class OfType:
    """Any value of the named strict JSON type."""

    type_name: str

    def __post_init__(self) -> None:
        if not isinstance(self.type_name, str) or self.type_name not in _TYPE_CHECKERS:
            raise ToolArgumentGraderError(f"type_name must be one of {tuple(_TYPE_CHECKERS)}")

    def matches(self, actual: Any) -> bool:
        return _TYPE_CHECKERS[self.type_name](actual)


ArgumentSpec = Identity | Exact | OneOf | NumberWithin | OfType
_SPEC_TYPES = (Identity, Exact, OneOf, NumberWithin, OfType)


def _specs(value: Any, field_name: str) -> dict[str, ArgumentSpec]:
    if not isinstance(value, Mapping):
        raise ToolArgumentGraderError(f"{field_name} must be a mapping of parameter name to spec")
    result: dict[str, ArgumentSpec] = {}
    for name, spec in value.items():
        if not isinstance(name, str) or name == "":
            raise ToolArgumentGraderError(f"{field_name} parameter names must be non-empty strings")
        if not isinstance(spec, _SPEC_TYPES):
            raise ToolArgumentGraderError(f"{field_name}[{name!r}] is not a known argument spec")
        result[name] = spec
    return result


@dataclass(frozen=True)
class ArgumentContract:
    """The expected call: exact tool name, required parameters, optional
    parameters (checked only when present)."""

    tool_name: str
    required: Mapping[str, ArgumentSpec]
    optional: Mapping[str, ArgumentSpec]

    def __post_init__(self) -> None:
        if not isinstance(self.tool_name, str) or self.tool_name == "":
            raise ToolArgumentGraderError("tool_name must be a non-empty string")
        required = _specs(self.required, "required")
        optional = _specs(self.optional, "optional")
        overlap = sorted(set(required) & set(optional))
        if overlap:
            raise ToolArgumentGraderError(f"parameters are both required and optional: {overlap}")


@dataclass(frozen=True, order=True)
class ArgumentIssue:
    parameter: str
    keyword: str
    message: str


@dataclass(frozen=True)
class ToolArgumentResult:
    passed: bool
    issues: tuple[ArgumentIssue, ...]
    tool_call_id: str


def _identity_swaps(
    actual: Mapping[str, Any], specs: Mapping[str, ArgumentSpec], failed: set[str]
) -> list[ArgumentIssue]:
    """For every failed identity parameter whose actual value equals ANOTHER
    declared identity's expected value, name the swap."""
    issues: list[ArgumentIssue] = []
    identities = {name: spec for name, spec in specs.items() if isinstance(spec, Identity)}
    for name in sorted(failed & identities.keys()):
        for other_name, other in sorted(identities.items()):
            if other_name != name and other.matches(actual[name]):
                issues.append(
                    ArgumentIssue(
                        name,
                        "swapped_identity",
                        f"value is the expected identity of parameter {other_name!r}",
                    )
                )
    return issues


def grade_tool_arguments(
    call: ToolCallArguments,
    contract: ArgumentContract,
    *,
    allow_extra: bool,
) -> ToolArgumentResult:
    """Grades one real tool call against `contract`. `allow_extra` is
    required: `False` fails every undeclared parameter."""
    if not isinstance(call, ToolCallArguments):
        raise ToolArgumentGraderError("call must be a ToolCallArguments record")
    if not isinstance(contract, ArgumentContract):
        raise ToolArgumentGraderError("contract must be an ArgumentContract")
    if not isinstance(allow_extra, bool):
        raise ToolArgumentGraderError("allow_extra must be a bool")

    issues: list[ArgumentIssue] = []
    if call.name != contract.tool_name:
        issues.append(ArgumentIssue("<tool>", "tool_name", "call is to a different tool"))
    ok, parsed, reason = parse_strict_json(call.arguments_json)
    if not ok:
        issues.append(ArgumentIssue("<arguments>", "arguments_json", reason))
        return ToolArgumentResult(False, tuple(sorted(issues)), call.tool_call_id)
    if not isinstance(parsed, dict):
        issues.append(ArgumentIssue("<arguments>", "arguments_json", "arguments are not an object"))
        return ToolArgumentResult(False, tuple(sorted(issues)), call.tool_call_id)

    failed: set[str] = set()
    for name, spec in contract.required.items():
        if name not in parsed:
            issues.append(ArgumentIssue(name, "missing", "required parameter is missing"))
        elif not spec.matches(parsed[name]):
            failed.add(name)
            issues.append(ArgumentIssue(name, "mismatch", "value does not satisfy the contract"))
    for name, spec in contract.optional.items():
        if name in parsed and not spec.matches(parsed[name]):
            failed.add(name)
            issues.append(ArgumentIssue(name, "mismatch", "value does not satisfy the contract"))
    if not allow_extra:
        declared = set(contract.required) | set(contract.optional)
        for name in parsed:
            if name not in declared:
                issues.append(ArgumentIssue(name, "extra", "parameter is not declared"))
    issues.extend(_identity_swaps(parsed, {**contract.required, **contract.optional}, failed))
    return ToolArgumentResult(not issues, tuple(sorted(issues)), call.tool_call_id)
