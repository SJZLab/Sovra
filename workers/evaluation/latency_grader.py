"""Latency grader (FTF-0612, Sections 23, 24, 26, 29 Performance, 69).

Acceptance ("Measure request/queue/TTFT/whole-agent latencies using declared
clock/workload and timeout treatment"): a `LatencySpec` declares everything
the measurement depends on, none of it defaulted:

  - `clock` ("monotonic" or "wall") and `workload_id`: every observation must
    carry the same clock and workload, so numbers from different clocks or
    workloads are never pooled;
  - `profile` ("cold" or "warm"): the one serving profile being graded;
  - `expected_case_ids`: the denominator, so a case that is not observed is
    visible, not silently absent;
  - `timeout_ms`: the timeout treatment. A case that timed out, was dropped or
    errored is COUNTED at `timeout_ms` for every metric (never removed, never
    counted as fast);
  - `limits`: `LatencyLimit(metric, percentile, max_ms)` for request, queue,
    TTFT and whole-agent latency at p50, p95 or p99 (nearest-rank, integer
    arithmetic, so no float rounding at a boundary).

Negative acceptance ("Dropped/timeout cases cannot be removed to improve p95;
cold/warm profiles are not mixed silently"):

  - a missing expected case is `missing_case` and is counted at `timeout_ms`
    in every percentile; an empty observation list therefore fails;
  - `timed_out_case` / `dropped_case` / `errored_case` each fail the grade
    (reported by count in the result) and are counted at `timeout_ms`;
  - an observation whose profile differs from the spec is `profile_mismatch`
    (never pooled), likewise `clock_mismatch` and `workload_mismatch`;
  - `duplicate_case` (every copy still counted) and `unexpected_case`.

An "ok" observation must report all four metrics as non-negative ints in
milliseconds (bool and float are refused, `null` is `missing_metric`), and none
may exceed `timeout_ms` (`ok_exceeds_timeout`). Whole-agent latency may not be
below request latency (`agent_below_request`), since the agent run contains
the request. All issues are reported together as a sorted tuple. A malformed
spec or record type raises `LatencyGraderError`; a slow run is a normal
`passed=False` result. `workers/evaluation` is this repository's real
evaluation-worker root. No live caller yet (Section 25's runtime loop is not
dependency-ready), so `proving_symbol` stays blank, same precedent as the
sibling graders. No schema change, no new dependency.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

__all__ = [
    "CLOCKS",
    "METRICS",
    "PERCENTILES",
    "PROFILES",
    "LatencyGraderError",
    "LatencyLimit",
    "LatencyObservation",
    "LatencyResult",
    "LatencySpec",
    "grade_latency",
]

METRICS = ("request_ms", "queue_ms", "ttft_ms", "agent_ms")
PERCENTILES = (50, 95, 99)
PROFILES = ("cold", "warm")
CLOCKS = ("monotonic", "wall")
# None for a measured ("ok") case; the issue code for a case that produced no valid measurement.
_OUTCOME_ISSUE: dict[str, str | None] = {
    "ok": None,
    "timeout": "timed_out_case",
    "dropped": "dropped_case",
    "error": "errored_case",
}
_MAX_MS = 10**12


class LatencyGraderError(ValueError):
    """The grader was called with a malformed spec or record type. Never raised
    for a slow run."""


def _is_ms(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= _MAX_MS


def _text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise LatencyGraderError(f"{name} must be a non-empty string")
    return value


@dataclass(frozen=True)
class LatencyLimit:
    metric: str
    percentile: int
    max_ms: int

    def __post_init__(self) -> None:
        if self.metric not in METRICS:
            raise LatencyGraderError(f"metric must be one of {METRICS}")
        if (
            not isinstance(self.percentile, int)
            or isinstance(self.percentile, bool)
            or self.percentile not in PERCENTILES
        ):
            raise LatencyGraderError(f"percentile must be one of {PERCENTILES}")
        if not _is_ms(self.max_ms):
            raise LatencyGraderError("max_ms must be a non-negative int of milliseconds")


@dataclass(frozen=True)
class LatencySpec:
    clock: str
    workload_id: str
    profile: str
    timeout_ms: int
    expected_case_ids: tuple[str, ...]
    limits: tuple[LatencyLimit, ...]

    def __post_init__(self) -> None:
        if self.clock not in CLOCKS:
            raise LatencyGraderError(f"clock must be one of {CLOCKS}")
        _text(self.workload_id, "workload_id")
        if self.profile not in PROFILES:
            raise LatencyGraderError(f"profile must be one of {PROFILES}")
        if not _is_ms(self.timeout_ms) or self.timeout_ms == 0:
            raise LatencyGraderError("timeout_ms must be a positive int of milliseconds")
        if not isinstance(self.expected_case_ids, tuple) or not self.expected_case_ids:
            raise LatencyGraderError("expected_case_ids must be a non-empty tuple")
        for case_id in self.expected_case_ids:
            _text(case_id, "expected case id")
        if len(set(self.expected_case_ids)) != len(self.expected_case_ids):
            raise LatencyGraderError("expected_case_ids must be unique")
        if not isinstance(self.limits, tuple) or not self.limits:
            raise LatencyGraderError("limits must be a non-empty tuple")
        for limit in self.limits:
            if not isinstance(limit, LatencyLimit):
                raise LatencyGraderError("limits must contain LatencyLimit values")
        pairs = [(limit.metric, limit.percentile) for limit in self.limits]
        if len(set(pairs)) != len(pairs):
            raise LatencyGraderError("each (metric, percentile) may be limited only once")


@dataclass(frozen=True)
class LatencyObservation:
    """One measured case. Metrics are ms; None when the run did not report one."""

    case_id: str
    clock: str
    workload_id: str
    profile: str
    outcome: str
    request_ms: object = None
    queue_ms: object = None
    ttft_ms: object = None
    agent_ms: object = None


@dataclass(frozen=True)
class LatencyResult:
    passed: bool
    issues: tuple[str, ...]
    # (metric, percentile, observed_ms) for every declared limit.
    percentiles: tuple[tuple[str, int, int], ...]
    counted_at_timeout: int


def _nearest_rank(sorted_values: list[int], percentile: int) -> int:
    rank = (percentile * len(sorted_values) + 99) // 100
    return sorted_values[max(rank, 1) - 1]


def grade_latency(spec: LatencySpec, observations: Sequence[LatencyObservation]) -> LatencyResult:
    if not isinstance(spec, LatencySpec):
        raise LatencyGraderError("spec must be a LatencySpec")
    if isinstance(observations, str | bytes) or not isinstance(observations, list | tuple):
        raise LatencyGraderError("observations must be a list or tuple")
    issues: set[str] = set()
    expected = set(spec.expected_case_ids)
    seen: set[str] = set()
    values: dict[str, list[int]] = {metric: [] for metric in METRICS}
    counted_at_timeout = 0
    for obs in observations:
        if not isinstance(obs, LatencyObservation):
            raise LatencyGraderError("observations must contain LatencyObservation values")
        _text(obs.case_id, "case_id")
        outcome = obs.outcome
        if outcome not in _OUTCOME_ISSUE:
            raise LatencyGraderError(f"outcome must be one of {tuple(_OUTCOME_ISSUE)}")
        if obs.case_id not in expected:
            issues.add("unexpected_case")
        if obs.case_id in seen:
            issues.add("duplicate_case")
        seen.add(obs.case_id)
        if obs.profile != spec.profile:
            issues.add("profile_mismatch")
        if obs.clock != spec.clock:
            issues.add("clock_mismatch")
        if obs.workload_id != spec.workload_id:
            issues.add("workload_mismatch")
        not_ok_issue = _OUTCOME_ISSUE[outcome]
        if not_ok_issue is not None:
            issues.add(not_ok_issue)
            counted_at_timeout += 1
            for metric in METRICS:
                values[metric].append(spec.timeout_ms)
            continue
        reported = {metric: getattr(obs, metric) for metric in METRICS}
        for metric, value in reported.items():
            if value is None:
                issues.add("missing_metric")
                values[metric].append(spec.timeout_ms)
            elif not _is_ms(value):
                raise LatencyGraderError(f"{metric} must be a non-negative int or None")
            elif value > spec.timeout_ms:
                issues.add("ok_exceeds_timeout")
                values[metric].append(value)
            else:
                values[metric].append(value)
        request, agent = reported["request_ms"], reported["agent_ms"]
        if request is not None and agent is not None and agent < request:
            issues.add("agent_below_request")
    for _ in expected - seen:
        issues.add("missing_case")
        counted_at_timeout += 1
        for metric in METRICS:
            values[metric].append(spec.timeout_ms)
    measured: list[tuple[str, int, int]] = []
    for limit in spec.limits:
        observed = _nearest_rank(sorted(values[limit.metric]), limit.percentile)
        measured.append((limit.metric, limit.percentile, observed))
        if observed > limit.max_ms:
            issues.add("over_limit")
    return LatencyResult(
        passed=not issues,
        issues=tuple(sorted(issues)),
        percentiles=tuple(measured),
        counted_at_timeout=counted_at_timeout,
    )
