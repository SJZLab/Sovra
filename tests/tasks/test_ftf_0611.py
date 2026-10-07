#!/usr/bin/env python3
# mypy: disable-error-code="arg-type,call-arg,var-annotated"
# ruff: noqa: E501
"""FTF-0611 -- Numeric grader.

Runnable without any database or network: `grade_numeric` is a pure, synchronous,
no-I/O function (no independent schema change).

Acceptance ("Use explicit absolute/relative tolerances, unit/currency and decimal
rules"): test_absolute_tolerance, test_relative_tolerance, test_either_tolerance_counts,
test_exact_decimal_arithmetic, test_decimal_places_rule, test_unit_and_currency_exact,
test_spec_is_fully_explicit.

Negative acceptance ("NaN/infinite/missing values and wrong units fail, not coerced
to zero or accepted by wide fallback tolerance"): test_nan_and_infinity_fail,
test_missing_value_fails, test_missing_or_wrong_unit_fails,
test_no_coercion_of_non_strings, test_no_wide_fallback_tolerance, test_zero_expected_is_not_coerced.

GRADER CHECKLIST: malformed records/specs fail closed, case/whitespace exactness,
multi-constraint coverage (all issues reported at once, deterministic order).

Run directly: python3 tests/tasks/test_ftf_0611.py
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from workers.evaluation.numeric_grader import (  # noqa: E402
    MAX_DECIMALS_LIMIT,
    NumericGraderError,
    NumericSpec,
    ObservedQuantity,
    grade_numeric,
)

FAILURES: list[str] = []
CID = "FTF-0611"


def check(criterion_id: str, description: str, condition: bool) -> None:
    if not condition:
        FAILURES.append(f"FAIL [{criterion_id}] {description}")


def raises(fn: Any) -> bool:
    try:
        fn()
    except NumericGraderError:
        return True
    except Exception:  # noqa: BLE001 -- the wrong exception type is a failed check
        return False
    return False


def spec(
    expected: str = "100", unit: str = "USD", abs_tol: str = "0", rel_tol: str = "0", dec: int = 2
) -> NumericSpec:
    return NumericSpec(
        expected=expected, unit=unit, abs_tol=abs_tol, rel_tol=rel_tol, max_decimals=dec
    )


def grade(value: Any, unit: Any = "USD", **kw: Any) -> Any:
    return grade_numeric(ObservedQuantity(value=value, unit=unit), spec(**kw))


def test_absolute_tolerance() -> None:
    check(CID, "the exact value passes with zero tolerance", grade("100").passed)
    check(CID, "a value just inside abs_tol passes", grade("100.50", abs_tol="0.5").passed)
    check(
        CID, "a value exactly at abs_tol passes (inclusive)", grade("99.50", abs_tol="0.5").passed
    )
    r = grade("100.51", abs_tol="0.5")
    check(
        CID,
        "a value just outside abs_tol fails out_of_tolerance",
        not r.passed and r.issues == ("out_of_tolerance",),
    )
    check(CID, "below the range fails too", not grade("99.49", abs_tol="0.5").passed)
    check(CID, "any deviation fails with zero tolerance", not grade("100.01").passed)


def test_relative_tolerance() -> None:
    check(CID, "exactly at rel_tol passes (inclusive)", grade("105", rel_tol="0.05", dec=0).passed)
    check(CID, "just outside rel_tol fails", not grade("105.01", rel_tol="0.05").passed)
    check(
        CID,
        "rel_tol scales with |expected| (negative expected)",
        grade("-105", expected="-100", rel_tol="0.05", dec=0).passed,
    )
    check(
        CID,
        "...and fails outside it",
        not grade("-106", expected="-100", rel_tol="0.05", dec=0).passed,
    )
    check(
        CID,
        "rel_tol is relative: the same 5 off fails at expected 1000 / 0.001",
        not grade("1005", expected="1000", rel_tol="0.001", dec=0).passed,
    )
    check(
        CID, "...and passes at 0.005", grade("1005", expected="1000", rel_tol="0.005", dec=0).passed
    )


def test_either_tolerance_counts() -> None:
    check(
        CID,
        "abs_tol alone can admit a value rel_tol would reject",
        grade("100.4", abs_tol="0.5", rel_tol="0.001").passed,
    )
    check(
        CID,
        "rel_tol alone can admit a value abs_tol would reject",
        grade("100.9", abs_tol="0.5", rel_tol="0.01").passed,
    )
    check(
        CID, "a value outside both fails", not grade("101.1", abs_tol="0.5", rel_tol="0.01").passed
    )


def test_zero_expected_is_not_coerced() -> None:
    check(
        CID,
        "expected 0 with only rel_tol admits only exactly 0",
        grade("0", expected="0", rel_tol="0.5").passed
        and not grade("0.01", expected="0", rel_tol="0.5").passed,
    )
    check(
        CID,
        "a missing value is never read as 0 against expected 0",
        not grade(None, expected="0").passed,
    )
    check(CID, "...nor an empty string", not grade("", expected="0").passed)
    check(CID, "negative zero is a plain number equal to 0", grade("-0", expected="0").passed)


def test_exact_decimal_arithmetic() -> None:
    check(
        CID,
        "0.30 against expected 0.3 passes with zero tolerance (no binary rounding)",
        grade("0.30", expected="0.3").passed,
    )
    check(
        CID,
        "0.31 against expected 0.3 fails with zero tolerance",
        not grade("0.31", expected="0.3").passed,
    )
    check(
        CID,
        "0.1 + 0.2 style boundary is exact: 0.3 at abs_tol 0.1 against 0.2 passes",
        grade("0.3", expected="0.2", abs_tol="0.1").passed,
    )
    check(CID, "...and 0.31 does not", not grade("0.31", expected="0.2", abs_tol="0.1").passed)
    big = "12345678901234567890123456789"
    check(CID, "a huge integer is compared exactly", grade(big, expected=big, dec=0).passed)
    check(
        CID,
        "...and a difference of 1 in the last digit fails",
        not grade("12345678901234567890123456788", expected=big, dec=0).passed,
    )


def test_decimal_places_rule() -> None:
    check(CID, "2 fractional digits pass max_decimals=2", grade("100.00").passed)
    r = grade("100.000")
    check(
        CID,
        "3 fractional digits fail max_decimals=2 even when equal in value",
        not r.passed and r.issues == ("too_many_decimals",),
    )
    check(CID, "an integer passes max_decimals=0", grade("100", dec=0).passed)
    check(CID, "one fractional digit fails max_decimals=0", not grade("100.0", dec=0).passed)
    check(
        CID,
        "max_decimals is counted on the written text, not the value",
        not grade("100.10", expected="100.1", dec=1).passed,
    )
    check(
        CID, "...and 1 digit passes max_decimals=1", grade("100.1", expected="100.1", dec=1).passed
    )


def test_unit_and_currency_exact() -> None:
    check(CID, "the exact unit passes", grade("100", unit="USD").passed)
    for bad in ("usd", "Usd", "USD ", " USD", "EUR", "US", "USDD", "US D"):
        r = grade("100", unit=bad)
        check(CID, f"unit {bad!r} fails wrong_unit", not r.passed and r.issues == ("wrong_unit",))
    check(
        CID,
        "a unit spec with a trailing space matches only itself",
        grade(
            "100",
            unit="kg ",
        ).passed
        is False
        and grade_numeric(ObservedQuantity("100", "kg "), spec(unit="kg ")).passed,
    )
    check(CID, "a non-string unit fails wrong_unit", grade("100", unit=5).issues == ("wrong_unit",))


def test_nan_and_infinity_fail() -> None:
    for text in (
        "NaN",
        "nan",
        "NAN",
        "Infinity",
        "infinity",
        "Inf",
        "inf",
        "-Infinity",
        "-inf",
        "+inf",
        "+Infinity",
    ):
        r = grade(text, abs_tol="0.99999", rel_tol="0.99999")
        check(
            CID,
            f"{text!r} fails not_finite even with the widest allowed tolerance",
            not r.passed and r.issues == ("not_finite",) or "not_finite" in r.issues,
        )
    for text in ("NaN", "Infinity", "-Infinity"):
        check(
            CID, f"{text!r} does not pass against expected 0", not grade(text, expected="0").passed
        )
    check(CID, "float nan is not accepted either", not grade(float("nan")).passed)
    check(CID, "float inf is not accepted either", not grade(float("inf")).passed)
    check(
        CID,
        "'nan' with surrounding space is malformed, not finite",
        grade(" nan").issues == ("malformed_number",),
    )


def test_missing_value_fails() -> None:
    r = grade(None)
    check(CID, "None fails missing_value", not r.passed and r.issues == ("missing_value",))
    check(
        CID,
        "an empty string is malformed, not missing and not 0",
        grade("").issues == ("malformed_number",),
    )
    check(
        CID, "a whitespace-only string is malformed", grade("   ").issues == ("malformed_number",)
    )


def test_missing_or_wrong_unit_fails() -> None:
    check(CID, "None unit fails missing_unit", grade("100", unit=None).issues == ("missing_unit",))
    check(CID, "empty unit fails missing_unit", grade("100", unit="").issues == ("missing_unit",))
    check(
        CID,
        "a missing unit does not pass because the number is right",
        not grade("100", unit=None, abs_tol="0.99999").passed,
    )
    check(
        CID,
        "a wrong unit does not pass because the number is right",
        not grade("100", unit="EUR").passed,
    )


def test_no_coercion_of_non_strings() -> None:
    for value in (100, 100.0, True, False, 1, Decimal_like(), [100], {"v": 100}, b"100", (100,)):
        r = grade(value)
        check(
            CID,
            f"non-string value {value!r} fails not_a_decimal_string",
            r.issues == ("not_a_decimal_string",),
        )
    check(
        CID,
        "a number inside a string with a unit suffix is malformed",
        grade("100 USD").issues == ("malformed_number",),
    )


class Decimal_like:  # noqa: N801 -- a stand-in for any non-str numeric object
    def __repr__(self) -> str:
        return "Decimal_like()"


def test_malformed_numbers_fail() -> None:
    for text in (
        "1e2",
        "1E2",
        "1,000",
        "1_000",
        "+5",
        "5.",
        ".5",
        "05",
        "00",
        "1.2.3",
        "--1",
        "1-",
        "0x10",
        "١٠٠",
        "１００",
        "1 000",
        "100\n",
        "\t100",
        "1e400",
        "-",
        ".",
    ):
        r = grade(text, abs_tol="0.99999", rel_tol="0.99999")
        check(
            CID, f"{text!r} fails malformed_number", "malformed_number" in r.issues and not r.passed
        )
    check(
        CID,
        "a very long digit string is refused, not parsed",
        grade("1" * 65).issues == ("malformed_number",),
    )
    check(
        CID,
        "...but exactly the maximum length is parsed",
        "malformed_number" not in grade("1" * 64, expected="1" * 64, dec=0).issues,
    )
    check(CID, "a negative value parses", grade("-5", expected="-5", dec=0).passed)
    check(CID, "zero with fraction parses", grade("0.50", expected="0.5").passed)


def test_no_wide_fallback_tolerance() -> None:
    check(CID, "rel_tol of 1 is refused", raises(lambda: spec(rel_tol="1")))
    check(CID, "rel_tol of 1.0 is refused", raises(lambda: spec(rel_tol="1.0")))
    check(CID, "rel_tol of 5 is refused", raises(lambda: spec(rel_tol="5")))
    check(CID, "rel_tol just below 1 is accepted", spec(rel_tol="0.999999").rel_tol == "0.999999")
    check(
        CID,
        "a wrong value does not pass under a tiny tolerance",
        not grade("200", abs_tol="0.01", rel_tol="0.001").passed,
    )
    check(CID, "the default (zero) tolerance admits nothing else", not grade("100.01").passed)


def test_spec_is_fully_explicit() -> None:
    check(
        CID,
        "the spec has no default for its tolerances or decimals",
        raises_type(lambda: NumericSpec(expected="1", unit="u")),
    )
    check(
        CID,
        "the spec has no default for max_decimals",
        raises_type(lambda: NumericSpec(expected="1", unit="u", abs_tol="0", rel_tol="0")),
    )


def raises_type(fn: Any) -> bool:
    try:
        fn()
    except TypeError:
        return True
    except Exception:  # noqa: BLE001
        return False
    return False


def test_spec_fails_closed() -> None:
    for bad in (
        "",
        " 1",
        "1 ",
        "NaN",
        "Infinity",
        "1e3",
        "abc",
        None,
        5,
        5.0,
        "+1",
        "1,0",
        ".5",
        "5.",
        "01",
    ):
        check(CID, f"expected {bad!r} is refused", raises(lambda b=bad: spec(expected=b)))
    for bad in ("", "-1", "-0.1", "NaN", "inf", "1e-3", None, 0, 0.5, " 0", "+0.1"):
        check(CID, f"abs_tol {bad!r} is refused", raises(lambda b=bad: spec(abs_tol=b)))
    for bad in ("", "-0.1", "NaN", "inf", "1e-3", None, 0, 0.01, " 0", "+0.1"):
        check(CID, f"rel_tol {bad!r} is refused", raises(lambda b=bad: spec(rel_tol=b)))
    unit_bads: list[Any] = ["", None, 5, 1.5, b"USD", ["USD"]]
    for bad in unit_bads:
        check(CID, f"unit {bad!r} is refused", raises(lambda b=bad: spec(unit=b)))
    dec_bads: list[Any] = [-1, MAX_DECIMALS_LIMIT + 1, 2.0, "2", None, True, bool]
    for bad in dec_bads:
        check(CID, f"max_decimals {bad!r} is refused", raises(lambda b=bad: spec(dec=b)))
    check(
        CID,
        "max_decimals 0 and the limit are accepted",
        spec(dec=0).max_decimals == 0
        and spec(dec=MAX_DECIMALS_LIMIT).max_decimals == MAX_DECIMALS_LIMIT,
    )
    check(CID, "a negative expected is accepted", spec(expected="-0.5").expected == "-0.5")
    check(CID, "a whitespace-only unit is accepted as an exact unit", spec(unit=" ").unit == " ")
    check(
        CID,
        "a 64-char expected is accepted and a 65-char one is refused",
        spec(expected="1" * 64).expected == "1" * 64 and raises(lambda: spec(expected="1" * 65)),
    )


def test_record_types_fail_closed() -> None:
    check(CID, "a non-ObservedQuantity is refused", raises(lambda: grade_numeric("100", spec())))
    check(
        CID,
        "a dict observation is refused",
        raises(lambda: grade_numeric({"value": "100", "unit": "USD"}, spec())),
    )
    check(CID, "None observation is refused", raises(lambda: grade_numeric(None, spec())))
    check(
        CID,
        "a non-NumericSpec is refused",
        raises(lambda: grade_numeric(ObservedQuantity("100", "USD"), {"expected": "100"})),
    )
    check(
        CID,
        "None spec is refused",
        raises(lambda: grade_numeric(ObservedQuantity("100", "USD"), None)),
    )


def test_multi_constraint_everything_reported() -> None:
    r = grade_numeric(ObservedQuantity("200.123", "EUR"), spec())
    check(
        CID,
        "wrong value, too many decimals and wrong unit are all reported",
        r.issues == ("out_of_tolerance", "too_many_decimals", "wrong_unit") and not r.passed,
    )
    r2 = grade_numeric(ObservedQuantity(None, None), spec())
    check(
        CID,
        "missing value and missing unit are both reported",
        r2.issues == ("missing_unit", "missing_value"),
    )
    r3 = grade_numeric(ObservedQuantity("NaN", "EUR"), spec())
    check(CID, "NaN with a wrong unit reports both", r3.issues == ("not_finite", "wrong_unit"))
    check(
        CID,
        "grading is deterministic and repeatable",
        grade_numeric(ObservedQuantity("200.123", "EUR"), spec()) == r,
    )
    clean = grade_numeric(ObservedQuantity("100.00", "USD"), spec())
    check(CID, "a fully correct answer has no issues", clean.passed and clean.issues == ())


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
