"""Numeric grader (FTF-0611, Section 23 `numeric_tolerance`, 24, 26, 69).

Acceptance ("Use explicit absolute/relative tolerances, unit/currency and
decimal rules"): every `NumericSpec` states all of its rules, none defaulted:

  - `expected`: the exact decimal value;
  - `unit`: the one accepted unit or currency code (compared exactly, so
    "USD" is not "usd" or "USD ");
  - `abs_tol` and `rel_tol`: explicit tolerances. A value passes when its
    deviation is at most `max(abs_tol, rel_tol * |expected|)` (the same
    "either tolerance" meaning as `math.isclose`). `rel_tol` must be below 1,
    so a spec cannot declare a 100% "tolerance"; use "0" for an unused one;
  - `max_decimals`: the most fractional digits the answer may be written with
    (2 for a currency amount, say).

The candidate's value is a decimal string, never a float. All arithmetic is
exact (`fractions.Fraction`), so there is no binary rounding at the boundary:
"0.30" against expected "0.3" with abs_tol "0" passes, "0.31" does not.

Negative acceptance ("NaN/infinite/missing values and wrong units fail, not
coerced to zero or accepted by wide fallback tolerance"): each is a failed
grade with its own issue code and is never repaired --

  - `missing_value` (None), `not_finite` (NaN / Infinity in any spelling),
    `malformed_number` (anything outside `-?digits[.digits]`: exponents,
    separators, whitespace, a leading "+", leading zeros, an empty string),
    `not_a_decimal_string` (a float, int, bool or other non-string, which
    could already have lost precision);
  - `missing_unit` (None or empty) and `wrong_unit` (any other unit);
  - `too_many_decimals` and `out_of_tolerance`.

All applicable issues are reported together, as a sorted tuple of codes. A
malformed spec or record type raises `NumericGraderError`; a wrong answer is a
normal `passed=False` result. `workers/evaluation` is this repository's real
evaluation-worker root. No live caller yet (Section 25's runtime loop is not
dependency-ready), so `proving_symbol` stays blank, same precedent as the
sibling graders. No schema change, no new dependency.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from fractions import Fraction

__all__ = [
    "MAX_DECIMALS_LIMIT",
    "NumericGraderError",
    "NumericResult",
    "NumericSpec",
    "ObservedQuantity",
    "grade_numeric",
]

MAX_DECIMALS_LIMIT = 12
_MAX_TEXT = 64
_NUMBER = re.compile(r"-?(0|[1-9][0-9]*)(\.[0-9]+)?")
# Spellings of the non-finite values, matched whole and case-insensitively, so
# they get their own issue code instead of the generic "malformed".
_NON_FINITE = frozenset({"nan", "inf", "infinity", "+inf", "-inf", "+infinity", "-infinity"})
_ONE = Fraction(1)


class NumericGraderError(ValueError):
    """The grader was called with a malformed spec or record type. Never raised
    for a wrong answer."""


def _parse(text: str, *, allow_negative: bool) -> Fraction | None:
    if len(text) > _MAX_TEXT or _NUMBER.fullmatch(text) is None:
        return None
    if not allow_negative and text.startswith("-"):
        return None
    return Fraction(text)


def _fraction_digits(text: str) -> int:
    return len(text.partition(".")[2])


@dataclass(frozen=True)
class NumericSpec:
    expected: str
    unit: str
    abs_tol: str
    rel_tol: str
    max_decimals: int

    def __post_init__(self) -> None:
        for name in ("expected", "unit", "abs_tol", "rel_tol"):
            value = getattr(self, name)
            if not isinstance(value, str) or value == "":
                raise NumericGraderError(f"{name} must be a non-empty string")
        if _parse(self.expected, allow_negative=True) is None:
            raise NumericGraderError("expected must be a plain decimal string")
        if _parse(self.abs_tol, allow_negative=False) is None:
            raise NumericGraderError("abs_tol must be a non-negative plain decimal string")
        rel = _parse(self.rel_tol, allow_negative=False)
        if rel is None or rel >= _ONE:
            raise NumericGraderError("rel_tol must be a non-negative decimal string below 1")
        if (
            not isinstance(self.max_decimals, int)
            or isinstance(self.max_decimals, bool)
            or not 0 <= self.max_decimals <= MAX_DECIMALS_LIMIT
        ):
            raise NumericGraderError(f"max_decimals must be an int in 0..{MAX_DECIMALS_LIMIT}")


@dataclass(frozen=True)
class ObservedQuantity:
    """What the candidate answered. `value` and `unit` are None when absent."""

    value: object
    unit: object


@dataclass(frozen=True)
class NumericResult:
    passed: bool
    issues: tuple[str, ...]


def _value_issues(value: object, spec: NumericSpec) -> list[str]:
    if value is None:
        return ["missing_value"]
    if not isinstance(value, str):
        return ["not_a_decimal_string"]
    if value.lower() in _NON_FINITE:
        return ["not_finite"]
    observed = _parse(value, allow_negative=True)
    if observed is None:
        return ["malformed_number"]
    issues: list[str] = []
    if _fraction_digits(value) > spec.max_decimals:
        issues.append("too_many_decimals")
    expected = Fraction(spec.expected)
    allowed = max(Fraction(spec.abs_tol), Fraction(spec.rel_tol) * abs(expected))
    if abs(observed - expected) > allowed:
        issues.append("out_of_tolerance")
    return issues


def _unit_issues(unit: object, spec: NumericSpec) -> list[str]:
    if unit is None or unit == "":
        return ["missing_unit"]
    if unit != spec.unit:
        return ["wrong_unit"]
    return []


def grade_numeric(observed: ObservedQuantity, spec: NumericSpec) -> NumericResult:
    """Grades one answered quantity against its spec."""
    if not isinstance(observed, ObservedQuantity):
        raise NumericGraderError("observed must be an ObservedQuantity")
    if not isinstance(spec, NumericSpec):
        raise NumericGraderError("spec must be a NumericSpec")
    issues = sorted({*_value_issues(observed.value, spec), *_unit_issues(observed.unit, spec)})
    return NumericResult(passed=not issues, issues=tuple(issues))
