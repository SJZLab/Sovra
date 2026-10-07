"""Cost grader (FTF-0613, Sections 23, 24, 26, 29 Economics, 32, 69).

Acceptance ("Grade total attempts/service-window cost against versioned policy
and actual measured prices"): a `CostSpec` names the policy (`policy_id`,
`policy_version`), the one `currency`, the expected attempt ids (the
denominator) and two explicit ceilings, `max_total_cost` and
`max_cost_per_success`, all decimal strings. Every `CostObservation` is one
attempt with its price `source` ("measured" or "estimated"), its `cost` (a
decimal string, or None when unknown), the policy version and currency it was
priced under, and its outcome ("success" or "failure").

  - total cost sums EVERY attempt, failed ones included, exactly
    (`fractions.Fraction`, no binary rounding at a ceiling);
  - successes are unique by `request_id`: a request repeated under the same id
    counts once, never once per attempt (Section 69.2);
  - cost per success = total cost / unique successes.

Negative acceptance ("Unknown cost or zero successful denominator is
insufficient evidence, not passing free usage"): the result has three
statuses, never a bare boolean:

  - "pass" -- every expected attempt present with a measured price in the
    spec's currency and policy version, at least one unique success, and both
    ceilings respected;
  - "fail" -- a definite violation: `over_total_cost`,
    `over_cost_per_success`, `currency_mismatch` (never summed across
    currencies), `policy_version_mismatch`, `duplicate_attempt` (every copy
    still counted), `unexpected_attempt`;
  - "insufficient_evidence" -- the grade cannot be trusted either way:
    `unknown_cost` (None is NOT zero; the string "0" is a real measured zero),
    `unmeasured_price` (an estimate is never a measured price),
    `missing_attempt`, `zero_successes` (cost per success is undefined, never
    cheap). A definite fail takes precedence; costs already known are still
    summed, so a lower bound that already exceeds a ceiling fails.

All applicable issues are reported together, as a sorted tuple. A malformed
spec or record type raises `CostGraderError`; an expensive run is a normal
non-"pass" result. `workers/evaluation` is this repository's real
evaluation-worker root. No live caller yet (Section 25's runtime loop is not
dependency-ready), so `proving_symbol` stays blank, same precedent as the
sibling graders. No schema change, no new dependency.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from fractions import Fraction

__all__ = [
    "OUTCOMES",
    "PRICE_SOURCES",
    "CostGraderError",
    "CostObservation",
    "CostResult",
    "CostSpec",
    "grade_cost",
]

OUTCOMES = ("success", "failure")
PRICE_SOURCES = ("measured", "estimated")
_DECIMAL = re.compile(r"(0|[1-9][0-9]*)(\.[0-9]+)?")
_MAX_TEXT = 64
_FAIL_ISSUES = frozenset(
    {
        "over_total_cost",
        "over_cost_per_success",
        "currency_mismatch",
        "policy_version_mismatch",
        "duplicate_attempt",
        "unexpected_attempt",
    }
)


class CostGraderError(ValueError):
    """The grader was called with a malformed spec or record type. Never raised
    for an expensive run."""


def _text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value or value.isspace():
        raise CostGraderError(f"{name} must be a non-empty string")
    return value


def _decimal(value: object, name: str) -> Fraction:
    if not isinstance(value, str) or len(value) > _MAX_TEXT or _DECIMAL.fullmatch(value) is None:
        raise CostGraderError(f"{name} must be a non-negative plain decimal string")
    return Fraction(value)


def _version(value: object) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise CostGraderError("policy_version must be an int >= 1")
    return value


@dataclass(frozen=True)
class CostSpec:
    policy_id: str
    policy_version: int
    currency: str
    expected_attempt_ids: tuple[str, ...]
    max_total_cost: str
    max_cost_per_success: str

    def __post_init__(self) -> None:
        _text(self.policy_id, "policy_id")
        _version(self.policy_version)
        _text(self.currency, "currency")
        if not isinstance(self.expected_attempt_ids, tuple) or not self.expected_attempt_ids:
            raise CostGraderError("expected_attempt_ids must be a non-empty tuple")
        for attempt_id in self.expected_attempt_ids:
            _text(attempt_id, "expected attempt id")
        if len(set(self.expected_attempt_ids)) != len(self.expected_attempt_ids):
            raise CostGraderError("expected_attempt_ids must be unique")
        _decimal(self.max_total_cost, "max_total_cost")
        _decimal(self.max_cost_per_success, "max_cost_per_success")


@dataclass(frozen=True)
class CostObservation:
    """One priced attempt. `cost` is None when the price is unknown."""

    attempt_id: str
    request_id: str
    outcome: str
    source: str
    policy_version: int
    currency: str
    cost: object = None


@dataclass(frozen=True)
class CostResult:
    status: str
    issues: tuple[str, ...]
    total_cost: Fraction
    unique_successes: int
    cost_per_success: Fraction | None

    @property
    def passed(self) -> bool:
        return self.status == "pass"


def grade_cost(spec: CostSpec, observations: Sequence[CostObservation]) -> CostResult:
    if not isinstance(spec, CostSpec):
        raise CostGraderError("spec must be a CostSpec")
    if isinstance(observations, str | bytes) or not isinstance(observations, list | tuple):
        raise CostGraderError("observations must be a list or tuple")
    issues: set[str] = set()
    expected = set(spec.expected_attempt_ids)
    seen: set[str] = set()
    successes: set[str] = set()
    total = Fraction(0)
    for obs in observations:
        if not isinstance(obs, CostObservation):
            raise CostGraderError("observations must contain CostObservation values")
        _text(obs.attempt_id, "attempt_id")
        _text(obs.request_id, "request_id")
        _text(obs.currency, "currency")
        outcome, source = obs.outcome, obs.source
        if outcome not in OUTCOMES:
            raise CostGraderError(f"outcome must be one of {OUTCOMES}")
        if source not in PRICE_SOURCES:
            raise CostGraderError(f"source must be one of {PRICE_SOURCES}")
        _version(obs.policy_version)
        if obs.attempt_id not in expected:
            issues.add("unexpected_attempt")
        if obs.attempt_id in seen:
            issues.add("duplicate_attempt")
        seen.add(obs.attempt_id)
        if outcome == "success":
            successes.add(obs.request_id)
        if source == "estimated":
            issues.add("unmeasured_price")
        if obs.policy_version != spec.policy_version:
            issues.add("policy_version_mismatch")
        if obs.currency != spec.currency:
            issues.add("currency_mismatch")
            continue
        if obs.cost is None:
            issues.add("unknown_cost")
            continue
        total += _decimal(obs.cost, "cost")
    if expected - seen:
        issues.add("missing_attempt")
    per_success: Fraction | None = None
    if successes:
        per_success = total / len(successes)
        if per_success > Fraction(spec.max_cost_per_success):
            issues.add("over_cost_per_success")
    else:
        issues.add("zero_successes")
    if total > Fraction(spec.max_total_cost):
        issues.add("over_total_cost")
    if not issues:
        status = "pass"
    elif issues & _FAIL_ISSUES:
        status = "fail"
    else:
        status = "insufficient_evidence"
    return CostResult(
        status=status,
        issues=tuple(sorted(issues)),
        total_cost=total,
        unique_successes=len(successes),
        cost_per_success=per_success,
    )
