"""Quality issue engine (FTF-0515, Section 8.1 / 10 / 67.4 / 77 / 81).

FTF-0508..FTF-0514 each ship a pure detector whose report says "this batch
position is wrong". Nothing yet turns those separate reports into ONE ledger
that says, per issue, whether it blocks, why, where it came from, and whether
a reviewer has resolved it. This module is that ledger, and the single place
`assert_clear_for_split` / `assert_clear_for_training` ask "may this dataset
move on".

Like its sibling detectors it is a pure, synchronous, no-I/O function set
(this task's `database_changes`: "No independent schema change").

Acceptance: "Issue engine records blocking/warning reason, provenance and
deterministic resolution state."

  - **reason + severity**: every `QualityIssue` carries `severity`
    (`blocking`/`warning`), `reason_code` and `message`, taken from the
    originating detector's own code, never reworded into a success.
  - **provenance**: `detector`, `batch_positions`, `record_indices` and an
    `evidence_digest` (a content hash a detector already computed, never
    content) name exactly which detector flagged which rows.
  - **deterministic resolution state**: `issue_id` is a SHA-256 of the
    issue's canonical identity (detector, category, reason, sorted
    positions, digest), so the same findings yield the same ids and the same
    order in any process or `PYTHONHASHSEED`. A resolution is bound to an
    `issue_id`; it never matches by position in a list.

Negative acceptance: "Broad exception cannot mark clean; unresolved critical
data/poisoning issue blocks split/training."

  - A required detector with no report, a recorded failure
    (`DetectorRun.error`), or a report of the wrong type yields a BLOCKING
    `detector_*` issue. `run_detector` captures an exception from a detector
    as a failure; it never converts it into an empty report. These issues
    cannot be resolved or waived by a reviewer; only a clean re-run clears them.
  - Every blocking issue that is not resolved blocks
    `assert_clear_for_split` and `assert_clear_for_training`. `poisoning`
    issues (Section 67.4) are always blocking and can be neither downgraded
    nor waived. No detector for poisoning exists yet (disclosed): a later one
    reports through `QualityIssueInput`, and this module enforces the block.
  - A warning may be `waived`; a blocking issue may only be `resolved`, and
    both need a non-blank reviewer id and reason.

A clean report is NOT proof the data is free of problems; it says only that
the required detectors ran and found nothing they can detect
(`RESIDUAL_RISK_DISCLOSURE`).
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace

from workers.data.exact_duplicate_detector import ExactDuplicateReport
from workers.data.near_duplicate_detector import NearDuplicateReport
from workers.data.normalized_duplicate_detector import NormalizedDuplicateReport
from workers.data.pii_secrets_detector import PiiSecretsReport
from workers.data.record_schema_validator import RecordSchemaValidationReport
from workers.data.tokenization_analysis import TokenizationAnalysisReport
from workers.data.tool_argument_validator import ToolCallValidationReport

__all__ = [
    "CATEGORIES",
    "DECISIONS",
    "REQUIRED_DETECTORS",
    "RESIDUAL_RISK_DISCLOSURE",
    "SEVERITIES",
    "DetectorRun",
    "IssueResolution",
    "QualityBlockedError",
    "QualityEngineError",
    "QualityIssue",
    "QualityIssueInput",
    "QualityReport",
    "apply_resolutions",
    "assert_clear_for_split",
    "assert_clear_for_training",
    "evaluate_quality",
    "run_detector",
]

SEVERITIES = frozenset({"blocking", "warning"})
# decision -> severities it may be applied to (a blocking issue is resolved,
# never waived).
_DECISION_SEVERITIES: Mapping[str, frozenset[str]] = {
    "resolved": frozenset({"blocking", "warning"}),
    "waived": frozenset({"warning"}),
}
# Categories whose severity the reporter cannot choose.
_FORCED_SEVERITY: Mapping[str, str] = {"poisoning": "blocking"}
DECISIONS = frozenset(_DECISION_SEVERITIES)
_RESOLVED_STATES = DECISIONS

# detector name -> the real report type it must produce.
_EXPECTED_REPORT_TYPES: Mapping[str, type] = {
    "record_schema": RecordSchemaValidationReport,
    "tool_arguments": ToolCallValidationReport,
    "tokenization": TokenizationAnalysisReport,
    "exact_duplicate": ExactDuplicateReport,
    "normalized_duplicate": NormalizedDuplicateReport,
    "near_duplicate": NearDuplicateReport,
    "pii_secrets": PiiSecretsReport,
}
REQUIRED_DETECTORS = frozenset(_EXPECTED_REPORT_TYPES)

_DETECTOR_FAILURE_CATEGORY = "detector_failure"
CATEGORIES = frozenset({*REQUIRED_DETECTORS, "poisoning", _DETECTOR_FAILURE_CATEGORY})
# What a detector this module does not wrap may report. `detector_failure` is
# produced only by this module itself.
_EXTRA_CATEGORIES = CATEGORIES - {_DETECTOR_FAILURE_CATEGORY}

RESIDUAL_RISK_DISCLOSURE = (
    "A clean quality report means only that every required detector ran and "
    "found nothing it is able to detect. It is not proof that the data has no "
    "personal data, secrets, poisoned or leaked records."
)


class QualityEngineError(ValueError):
    """The call itself is malformed (unknown detector, unknown issue id,
    blank reviewer, a waiver of a blocking issue, ...). Raised, never
    swallowed into a clean report."""


class QualityBlockedError(RuntimeError):
    """Raised by `assert_clear_for_*` when unresolved blocking issues
    remain. `issue_ids` names every one of them."""

    def __init__(self, stage: str, issue_ids: tuple[str, ...]) -> None:
        super().__init__(
            f"{stage} blocked by {len(issue_ids)} unresolved blocking quality issue(s)"
        )
        self.stage = stage
        self.issue_ids = issue_ids


@dataclass(frozen=True)
class QualityIssue:
    issue_id: str
    detector: str
    category: str
    severity: str
    reason_code: str
    message: str
    batch_positions: tuple[int, ...]
    record_indices: tuple[int | None, ...]
    evidence_digest: str | None
    resolution_state: str = "open"  # open | resolved | waived
    resolved_by: str | None = None
    resolution_reason: str | None = None
    is_detector_failure: bool = False

    @property
    def is_unresolved_blocking(self) -> bool:
        # Fail closed: anything that is not exactly a warning is blocking, and
        # anything that is not exactly a recorded resolution is still open.
        return self.severity != "warning" and self.resolution_state not in _RESOLVED_STATES


@dataclass(frozen=True)
class DetectorRun:
    """One detector's outcome: its report, or the error it failed with."""

    detector: str
    report: object | None = None
    error: str | None = None  # exception type name only, content-free


@dataclass(frozen=True)
class QualityIssueInput:
    """An issue reported by a detector this module does not wrap (e.g. a
    future poisoning detector). Severity is stated by the reporter except
    for `poisoning`, which is always blocking."""

    detector: str
    category: str
    severity: str
    reason_code: str
    message: str
    batch_positions: tuple[int, ...] = ()
    record_indices: tuple[int | None, ...] = ()
    evidence_digest: str | None = None


@dataclass(frozen=True)
class IssueResolution:
    issue_id: str
    decision: str  # "resolved" | "waived"
    reviewer_id: str
    reason: str


@dataclass(frozen=True)
class QualityReport:
    issues: tuple[QualityIssue, ...]
    detectors_reported: frozenset[str]
    residual_risk_disclosure: str = RESIDUAL_RISK_DISCLOSURE

    @property
    def unresolved_blocking(self) -> tuple[QualityIssue, ...]:
        return tuple(i for i in self.issues if i.is_unresolved_blocking)

    @property
    def is_clean(self) -> bool:
        # Clean = every required detector reported AND nothing blocking is
        # left open. (Missing detectors already appear as blocking issues.)
        return REQUIRED_DETECTORS <= self.detectors_reported and not self.unresolved_blocking

    @property
    def blocks_split(self) -> bool:
        return bool(self.unresolved_blocking)

    @property
    def blocks_training(self) -> bool:
        return bool(self.unresolved_blocking)


def run_detector(name: str, detector: Callable[[], object]) -> DetectorRun:
    """Run one detector thunk. Any exception is recorded as a failure (type
    name only); it is never turned into an empty/clean report."""
    if name not in REQUIRED_DETECTORS:
        raise QualityEngineError(f"unknown detector {name!r}")
    try:
        return DetectorRun(detector=name, report=detector())
    except Exception as exc:  # noqa: BLE001 - recorded as a blocking failure, never swallowed
        return DetectorRun(detector=name, error=type(exc).__name__)


def _issue_id(
    detector: str,
    category: str,
    reason_code: str,
    pairs: Sequence[tuple[int, int | None]],
    digest: str | None,
    message: str,
    qualifier: str,
) -> str:
    canonical = json.dumps(
        [detector, category, reason_code, [list(p) for p in pairs], digest, message, qualifier],
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _make(
    detector: str,
    category: str,
    severity: str,
    reason_code: str,
    message: str,
    positions: Sequence[int] = (),
    indices: Sequence[int | None] = (),
    digest: str | None = None,
    is_detector_failure: bool = False,
    qualifier: str = "",
) -> QualityIssue:
    # positions and indices are sorted as PAIRS so provenance never decouples
    if indices and len(indices) != len(positions):
        raise QualityEngineError("record_indices must pair one-to-one with batch_positions")
    if indices:
        pairs = sorted(
            zip(positions, indices, strict=True), key=lambda p: (p[0], p[1] is None, p[1] or 0)
        )
    else:
        pairs = sorted((pos, None) for pos in positions)
    sorted_positions = tuple(p[0] for p in pairs)
    sorted_indices = tuple(p[1] for p in pairs) if indices else ()
    return QualityIssue(
        issue_id=_issue_id(detector, category, reason_code, pairs, digest, message, qualifier),
        detector=detector,
        category=category,
        severity=severity,
        reason_code=reason_code,
        message=message,
        batch_positions=sorted_positions,
        record_indices=sorted_indices,
        evidence_digest=digest,
        is_detector_failure=is_detector_failure,
    )


def _row_issues(detector: str, report: object) -> list[QualityIssue]:
    out: list[QualityIssue] = []
    for issue in getattr(report, "issues", ()):
        position = getattr(issue, "batch_position", None)
        if position is None:
            position = getattr(issue, "call_position", None)
        positions = () if position is None else (position,)
        indices = (getattr(issue, "record_index", None),) if positions else ()
        out.append(
            _make(detector, detector, "blocking", issue.code, issue.message, positions, indices)
        )
    return out


def _from_report(detector: str, report: object) -> list[QualityIssue]:
    out = _row_issues(detector, report)
    if isinstance(report, ExactDuplicateReport):
        for group in (*report.raw_duplicate_groups, *report.normalized_duplicate_groups):
            out.append(
                _make(
                    detector,
                    detector,
                    "blocking" if group.is_blocking else "warning",
                    f"{group.hash_kind}_duplicate_group",
                    f"{group.hash_kind} hash shared by {len(group.batch_positions)} records",
                    group.batch_positions,
                    group.record_indices,
                    group.digest,
                )
            )
    elif isinstance(report, NormalizedDuplicateReport):
        for ngroup in report.duplicate_groups:
            out.append(
                _make(
                    detector,
                    detector,
                    "blocking" if ngroup.is_blocking else "warning",
                    "normalized_duplicate_group",
                    f"normalized hash shared by {len(ngroup.batch_positions)} records",
                    ngroup.batch_positions,
                    ngroup.record_indices,
                    ngroup.digest,
                )
            )
    elif isinstance(report, NearDuplicateReport):
        for cand in report.flagged_candidates:
            out.append(
                _make(
                    detector,
                    detector,
                    "blocking" if cand.is_cross_purpose else "warning",
                    "near_duplicate_pair",
                    "near-duplicate pair flagged by " + ",".join(sorted(cand.flagged_methods)),
                    (cand.batch_position_a, cand.batch_position_b),
                    (cand.record_index_a, cand.record_index_b),
                )
            )
    elif isinstance(report, PiiSecretsReport):
        for finding in report.findings:
            out.append(
                _make(
                    detector,
                    detector,
                    "blocking" if finding.requires_quarantine else "warning",
                    f"pii_secret_{finding.category}",
                    f"{finding.category} found at {finding.content_path}",
                    (finding.batch_position,),
                    (finding.record_index,),
                    qualifier=f"{finding.message_index}|{finding.content_path}",
                )
            )
    elif isinstance(report, TokenizationAnalysisReport):
        for conv in report.conversations:
            if conv.truncated_critical_turn:
                out.append(
                    _make(
                        detector,
                        detector,
                        "blocking",
                        "truncated_critical_turn",
                        "a critical turn is truncated by the token limit",
                        (conv.batch_position,),
                        (conv.record_index,),
                    )
                )
            elif conv.requires_explicit_truncation_policy:
                out.append(
                    _make(
                        detector,
                        detector,
                        "warning",
                        "requires_explicit_truncation_policy",
                        "conversation needs an explicit truncation policy",
                        (conv.batch_position,),
                        (conv.record_index,),
                    )
                )
    return out


def evaluate_quality(
    runs: Sequence[DetectorRun],
    *,
    extra_issues: Sequence[QualityIssueInput] = (),
) -> QualityReport:
    """Merge every detector's outcome into one deterministic ledger."""
    issues: dict[str, QualityIssue] = {}
    reported: set[str] = set()
    names = [run.detector for run in runs]
    seen = set(names)
    if len(seen) != len(names):
        raise QualityEngineError("more than one run for the same detector")

    def add(issue: QualityIssue) -> None:
        issues[issue.issue_id] = issue

    for run in runs:
        if run.detector not in REQUIRED_DETECTORS:
            raise QualityEngineError(f"unknown detector {run.detector!r}")
        if run.error is not None:
            add(
                _make(
                    run.detector,
                    _DETECTOR_FAILURE_CATEGORY,
                    "blocking",
                    "detector_failed",
                    f"detector raised {run.error}",
                    is_detector_failure=True,
                )
            )
        elif not isinstance(run.report, _EXPECTED_REPORT_TYPES[run.detector]):
            add(
                _make(
                    run.detector,
                    _DETECTOR_FAILURE_CATEGORY,
                    "blocking",
                    "detector_report_invalid",
                    "detector returned no report of the expected type",
                    is_detector_failure=True,
                )
            )
        else:
            reported.add(run.detector)
            for issue in _from_report(run.detector, run.report):
                add(issue)

    for missing in sorted(REQUIRED_DETECTORS - seen):
        add(
            _make(
                missing,
                _DETECTOR_FAILURE_CATEGORY,
                "blocking",
                "detector_not_run",
                "required detector produced no report",
                is_detector_failure=True,
            )
        )

    for item in extra_issues:
        if item.category not in _EXTRA_CATEGORIES or item.severity not in SEVERITIES:
            raise QualityEngineError("unknown category or severity on extra issue")
        severity = _FORCED_SEVERITY.get(item.category, item.severity)
        add(
            _make(
                item.detector,
                item.category,
                severity,
                item.reason_code,
                item.message,
                item.batch_positions,
                item.record_indices,
                item.evidence_digest,
            )
        )

    ordered = tuple(sorted(issues.values(), key=lambda i: i.issue_id))
    return QualityReport(issues=ordered, detectors_reported=frozenset(reported))


def apply_resolutions(
    report: QualityReport, resolutions: Sequence[IssueResolution]
) -> QualityReport:
    """Bind reviewer decisions to issue ids. Unknown ids, repeated ids,
    blank reviewer/reason, a waiver of a blocking issue, and any resolution
    of a detector-failure issue are all refused (raised)."""
    by_id = {i.issue_id: i for i in report.issues}
    wanted = [res.issue_id for res in resolutions]
    if len(set(wanted)) != len(wanted):
        raise QualityEngineError("more than one resolution for the same issue")
    decided: dict[str, QualityIssue] = {}
    for res in resolutions:
        issue = by_id.get(res.issue_id)
        if issue is None:
            raise QualityEngineError("resolution names an unknown issue id")
        allowed_severities = _DECISION_SEVERITIES.get(res.decision)
        if allowed_severities is None:
            raise QualityEngineError("decision must be 'resolved' or 'waived'")
        reviewer = res.reviewer_id.strip()
        reason = res.reason.strip()
        if not reviewer or not reason:
            raise QualityEngineError("a resolution needs a reviewer id and a reason")
        if issue.is_detector_failure:
            raise QualityEngineError("a detector failure is cleared only by a clean re-run")
        if issue.severity not in allowed_severities:
            raise QualityEngineError("only a warning may be waived")
        decided[res.issue_id] = replace(
            issue,
            resolution_state=res.decision,
            resolved_by=reviewer,
            resolution_reason=reason,
        )
    merged = tuple(decided.get(i.issue_id, i) for i in report.issues)
    return replace(report, issues=merged)


def assert_clear_for_split(report: QualityReport) -> None:
    if not REQUIRED_DETECTORS <= report.detectors_reported or report.blocks_split:
        raise QualityBlockedError("split", _blocking_ids(report))


def assert_clear_for_training(report: QualityReport) -> None:
    if not REQUIRED_DETECTORS <= report.detectors_reported or report.blocks_training:
        raise QualityBlockedError("training", _blocking_ids(report))


def _blocking_ids(report: QualityReport) -> tuple[str, ...]:
    return tuple(i.issue_id for i in report.unresolved_blocking)
