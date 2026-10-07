"""Exact-match grader (FTF-0604, Section 23/26/29/69).

Section 23's own grader-type list ("Grader types") names `exact_match`
first, as the simplest deterministic grader type; this module implements
exactly that one grader, standing alone from the other grader-type tasks
(`json_schema`/`tool_name`/`tool_arguments`/`tool_sequence`/
`forbidden_action`/`required_refusal`/`numeric_tolerance`/`latency`/`cost`/
`llm_judge`), each its own separately dependency-gated FTF-06xx task on
FTF-0601.

This task's own acceptance clause ("Exact matcher compares canonical
approved values with explicit whitespace/case policy") and
negative_acceptance clause ("No fuzzy or partial matching secretly
replaces exact critical assertions") shape this module's design:

  - **explicit whitespace/case policy**: `case_policy` and
    `whitespace_policy` are required keyword-only arguments with no
    default -- a caller must always state which policy it means; there is
    no silently-assumed normalization. Each is a real closed, validated
    enum (`ALLOWED_CASE_POLICIES`/`ALLOWED_WHITESPACE_POLICIES`), refused
    outright, never ignored, when the caller passes an unknown value.

  - **canonical approved values**: `approved_values` is a non-empty
    sequence of canonical acceptable strings, not a single value -- Section
    26's own evaluation-categories list and Section 24's tool-call example
    both treat an evaluation case's own "correct answer" as potentially
    one of several equally-canonical phrasings (e.g. "USA"/"United
    States"/"U.S.A."). Any one exact match (after the explicit policy is
    applied to both sides) passes; the matched canonical value is reported
    so a caller/report can show which one.

  - **no fuzzy or partial matching**: the only comparison this module ever
    performs, after normalization, is Python string `==` -- full-string
    equality. There is no substring/`in`, `startswith`/`endswith`,
    edit-distance, or token-overlap comparison anywhere in this module. A
    candidate that merely contains, starts with, or is contained by an
    approved value is never accepted; a longer candidate with extra
    trailing/leading content attached to an otherwise-correct approved
    value fails exactly like any other non-match. This is a deliberate,
    narrow design choice, not an omission: critical assertions (Section
    30's own "zero forbidden effects"/"critical scenarios" gates) must
    never be satisfied by a model response that merely echoes the approved
    answer somewhere inside a longer, uncontrolled string.

`workers/evaluation` is this repository's real, already-scaffolded
evaluation-worker root (FTF-0001; `pyproject.toml`'s own
`sovra-worker-evaluation` package, previously empty apart from its own
README/pyproject). Placed here rather than the task packet's own generic
suggestion (`services/evaluation-worker/evaluators/exact_match_grader.py`,
a `services/` tree that does not exist in this repository -- the real
`SOURCE_ROOTS`, FTF-0001/PR #10, name `workers/evaluation` as the
evaluation-worker root).

Scope decision, same disclosed "no live caller yet" precedent as
FTF-0506/FTF-0507/FTF-0508/FTF-0509/FTF-0510/FTF-0511/FTF-0512 /FTF-0514:
this task's own packet `files` list names only this module and its test,
not an importer/route. Section 25's own evaluation runtime loop (the
engine that would call a grader after a candidate turn) is not owned by
any dependency-ready task yet. `proving_symbol` stays blank;
`product_truth_check.py`'s product-true count is deliberately unchanged.
This task's own `database_changes` clause ("No independent schema
change... any required change is a separately claimed migration
prerequisite") means this module adds no new production database surface.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass

__all__ = [
    "ALLOWED_CASE_POLICIES",
    "ALLOWED_WHITESPACE_POLICIES",
    "CASE_INSENSITIVE",
    "CASE_SENSITIVE",
    "WHITESPACE_COLLAPSE",
    "WHITESPACE_EXACT",
    "WHITESPACE_STRIP",
    "ExactMatchGraderError",
    "ExactMatchResult",
    "grade_exact_match",
]

CASE_SENSITIVE = "case_sensitive"
CASE_INSENSITIVE = "case_insensitive"
ALLOWED_CASE_POLICIES = (CASE_SENSITIVE, CASE_INSENSITIVE)

# "exact": no whitespace normalization at all.
# "strip": only leading/trailing whitespace is removed (internal runs of
#   whitespace must still match byte-for-byte).
# "collapse": every run of whitespace (leading, trailing or internal) is
#   collapsed to a single space, after stripping the ends.
WHITESPACE_EXACT = "exact"
WHITESPACE_STRIP = "strip"
WHITESPACE_COLLAPSE = "collapse"
ALLOWED_WHITESPACE_POLICIES = (WHITESPACE_EXACT, WHITESPACE_STRIP, WHITESPACE_COLLAPSE)


class ExactMatchGraderError(ValueError):
    """The grader itself was called with a malformed candidate, policy, or
    approved-value set. Never raised for a legitimate non-matching answer --
    that is a normal graded `passed=False` result, not an error."""


@dataclass(frozen=True)
class ExactMatchResult:
    passed: bool
    matched_value: str | None
    normalized_candidate: str
    case_policy: str
    whitespace_policy: str


_WHITESPACE_NORMALIZERS: dict[str, Callable[[str], str]] = {
    WHITESPACE_EXACT: lambda value: value,
    WHITESPACE_STRIP: str.strip,
    WHITESPACE_COLLAPSE: lambda value: " ".join(value.split()),
}
_CASE_NORMALIZERS: dict[str, Callable[[str], str]] = {
    CASE_SENSITIVE: lambda value: value,
    CASE_INSENSITIVE: str.casefold,
}


def _normalize(value: str, *, case_policy: str, whitespace_policy: str) -> str:
    """Applies an already-validated policy pair to `value` through the two
    closed dispatch tables above. `grade_exact_match` is this module's only
    caller and validates both policies against their closed enums before
    ever calling this (private) helper -- there is deliberately no second
    validation/raise here, so exactly one place decides whether a policy
    value is legal."""
    return _CASE_NORMALIZERS[case_policy](_WHITESPACE_NORMALIZERS[whitespace_policy](value))


def grade_exact_match(
    candidate_value: str,
    approved_values: Sequence[str],
    *,
    case_policy: str,
    whitespace_policy: str,
) -> ExactMatchResult:
    """Grades `candidate_value` against `approved_values` by exact,
    whole-string comparison only, after applying the caller's explicit
    `case_policy`/`whitespace_policy` to both sides. Any one approved value
    matching passes; never partial/substring/fuzzy. Raises
    `ExactMatchGraderError` for a malformed call; otherwise always returns a
    result (a genuine non-match is `passed=False`, not an error)."""
    if not isinstance(candidate_value, str):
        raise ExactMatchGraderError(
            f"candidate_value must be a string, not {type(candidate_value).__name__}"
        )
    if (
        not isinstance(approved_values, Sequence)
        or isinstance(approved_values, str | bytes)
        or not approved_values
    ):
        raise ExactMatchGraderError(
            "approved_values must be a non-empty sequence of canonical approved strings"
        )
    for value in approved_values:
        if not isinstance(value, str):
            raise ExactMatchGraderError(
                f"every approved value must be a string, not {type(value).__name__}"
            )
    if case_policy not in ALLOWED_CASE_POLICIES:
        raise ExactMatchGraderError(
            f"case_policy must be one of {ALLOWED_CASE_POLICIES}: {case_policy!r}"
        )
    if whitespace_policy not in ALLOWED_WHITESPACE_POLICIES:
        raise ExactMatchGraderError(
            f"whitespace_policy must be one of {ALLOWED_WHITESPACE_POLICIES}: {whitespace_policy!r}"
        )

    normalized_candidate = _normalize(
        candidate_value, case_policy=case_policy, whitespace_policy=whitespace_policy
    )
    for approved in approved_values:
        normalized_approved = _normalize(
            approved, case_policy=case_policy, whitespace_policy=whitespace_policy
        )
        if normalized_candidate == normalized_approved:
            return ExactMatchResult(
                passed=True,
                matched_value=approved,
                normalized_candidate=normalized_candidate,
                case_policy=case_policy,
                whitespace_policy=whitespace_policy,
            )

    return ExactMatchResult(
        passed=False,
        matched_value=None,
        normalized_candidate=normalized_candidate,
        case_policy=case_policy,
        whitespace_policy=whitespace_policy,
    )
