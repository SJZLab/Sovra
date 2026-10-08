"""Dataset quality report / issue / resolution store (FTF-0516-P1, Section
6.10/6.12/6.24/7A.10/8.1 steps 16-18/8.2/9/67.2/67.4/69.1/77/78.4).

FTF-0515's `quality_issue_engine` is pure. This module persists its ledger and
the reviewer decisions bound to it; the tables, constraints and grants are in
`0026_dataset_quality_issues.py`, and the database itself refuses a wrong
evidence hash, a resolution of a detector failure, a waiver of a blocking
issue or by a role that may not waive, and every UPDATE/DELETE of the ledger.

Rules this module enforces on top of them:

- `record_quality_report` runs FTF-0515's `evaluate_quality` itself. No caller
  supplies issue rows, so a stored issue is always one the engine produced
  (Law 11). The detector message is never stored, only its SHA-256, because a
  detector message can echo record values. Recording the same findings again
  for the dataset's latest report returns that report and writes nothing.
  Reports form a hash chain (each hash covers the previous report's hash), so
  a later report never collides with an older one holding the same findings.
- `submit_resolutions` locks the report row, replays a repeated idempotency
  key, refuses a report that is not the dataset's latest, refuses a stale
  `expected_resolution_version`, and hands every decision to the engine's own
  `apply_resolutions` (exclude / correct / new_version => `resolved`, waive =>
  `waived`). The engine owns the rules about detector failures and blocking
  waivers; this module only maps the engine's refusals to stable codes. The
  whole request is all-or-nothing: any raise leaves nothing written.
- `exclude` creates no dataset version: it records an exclusion set that split
  generation reads through `excluded_batch_positions`. `correct` and
  `new_version` bind a successor dataset (same project, name and purpose,
  later version) created by FTF-0501-P1; its latest report must carry every
  required detector, else the successor is not revalidated (Section 78.4).
  No `datasets` row is ever modified.
- Reads hide a `held_out_evaluation` dataset from callers that may not see it:
  every read is the same not-found as a missing id.
- `load_resolved_quality_report` rebuilds the engine's `QualityReport` from the
  rows, so `assert_clear_for_split` / `assert_clear_for_training` keep working
  unchanged for FTF-0517/0518/0519.

This module never calls S3 and never returns record content, messages or
object keys.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import uuid
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import NamedTuple

from sqlalchemy import JSON, DateTime, Integer, String, Text, and_, select
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Mapped, Session, mapped_column
from workers.data.quality_issue_engine import (
    CATEGORIES,
    REQUIRED_DETECTORS,
    DetectorRun,
    IssueResolution,
    QualityEngineError,
    QualityIssue,
    QualityIssueInput,
    QualityReport,
    apply_resolutions,
    evaluate_quality,
)

from app.auth.audit_event_writer import write_audit_event
from app.auth.organization_workspace_schema import Base
from app.auth.tenant_isolation_query_guards import execute_tenant_scoped
from app.datasets.dataset_upload_store import Dataset, DatasetNotFoundError, get_dataset
from app.db.tenant_context import TenantContext

__all__ = [
    "ADMIN_ROLES",
    "ALL_ACTIONS",
    "MAX_PAGE_SIZE",
    "MAX_RESOLUTIONS_PER_REQUEST",
    "DatasetIssueResolution",
    "DatasetNotFoundError",
    "DatasetQualityIssue",
    "DatasetQualityReport",
    "DetectorFailureRequiresRerunError",
    "EvidenceMismatchError",
    "IdempotencyKeyReusedError",
    "InvalidResolutionRequestError",
    "IssueAlreadyDecidedError",
    "IssueNotFoundError",
    "IssueView",
    "PositionsNotInIssueError",
    "QualityActor",
    "QualityReportNotFoundError",
    "QualityStoreError",
    "ReportSupersededError",
    "ReportVersionConflictError",
    "ResolutionRequest",
    "ResolveOutcome",
    "SuccessorNotLaterVersionError",
    "SuccessorNotRevalidatedError",
    "WaiveNotPermittedError",
    "WaiverNotAllowedForBlockingError",
    "evidence_sha256",
    "exclude_after_evidence",
    "excluded_batch_positions",
    "get_issue",
    "get_latest_report",
    "list_issues",
    "list_resolutions",
    "load_resolved_quality_report",
    "record_quality_report",
    "request_digest",
    "submit_resolutions",
]

MAX_PAGE_SIZE = 200
MAX_RESOLUTIONS_PER_REQUEST = 100
ADMIN_ROLES = frozenset({"workspace_admin", "organization_admin"})
ALL_ACTIONS = ("exclude", "correct", "new_version", "waive")
_STATES = frozenset({"open", "resolved", "waived"})
_SEVERITIES = frozenset({"blocking", "warning"})
_REASON_MAX_LENGTH = 2000
_KEY_MAX_LENGTH = 200
_VERSION_MAX_LENGTH = 64

_HEX64 = re.compile(r"[0-9a-f]{64}")
_REASON_CODE = re.compile(r"[a-z][a-z0-9_]*")

_RESOURCE_REPORT = "dataset_quality_report"
_RESOURCE_RESOLUTION = "dataset_issue_resolution"
_ACTION_REPORT_RECORD = "dataset_quality_report.record"
_ACTION_ISSUE_RESOLVE = "dataset_quality_issue.resolve"


# ----------------------------------------------------------------- errors


class QualityStoreError(Exception):
    """A refusal with a stable code and HTTP status. Nothing was written."""

    code = "invalid_body"
    status = 400


class InvalidResolutionRequestError(QualityStoreError):
    code = "invalid_body"
    status = 400


class PositionsNotInIssueError(QualityStoreError):
    code = "positions_not_in_issue"
    status = 400


class WaiveNotPermittedError(QualityStoreError):
    code = "waive_not_permitted"
    status = 403


class QualityReportNotFoundError(QualityStoreError):
    code = "quality_report_not_found"
    status = 404


class IssueNotFoundError(QualityStoreError):
    code = "not_found"
    status = 404


class ReportVersionConflictError(QualityStoreError):
    code = "report_version_conflict"
    status = 409


class ReportSupersededError(QualityStoreError):
    code = "report_superseded"
    status = 409


class IssueAlreadyDecidedError(QualityStoreError):
    code = "issue_already_decided"
    status = 409


class WaiverNotAllowedForBlockingError(QualityStoreError):
    code = "waiver_not_allowed_for_blocking"
    status = 409


class DetectorFailureRequiresRerunError(QualityStoreError):
    code = "detector_failure_requires_rerun"
    status = 409


class EvidenceMismatchError(QualityStoreError):
    code = "evidence_mismatch"
    status = 409


class SuccessorNotRevalidatedError(QualityStoreError):
    code = "successor_not_revalidated"
    status = 409


class SuccessorNotLaterVersionError(QualityStoreError):
    code = "successor_not_later_version"
    status = 409


class IdempotencyKeyReusedError(QualityStoreError):
    code = "idempotency_key_reused"
    status = 409


# FTF-0515's `apply_resolutions` refuses with a message only. The substring
# that identifies each refusal is pinned by a test against the real engine, so
# a reworded engine message reddens that test instead of silently mapping every
# refusal to the generic code.
_ENGINE_REFUSALS: tuple[tuple[str, type[QualityStoreError]], ...] = (
    ("cleared only by a clean re-run", DetectorFailureRequiresRerunError),
    ("only a warning may be waived", WaiverNotAllowedForBlockingError),
    ("unknown issue id", IssueNotFoundError),
)


def _map_engine_error(exc: QualityEngineError) -> QualityStoreError:
    for fragment, error_type in _ENGINE_REFUSALS:
        if fragment in str(exc):
            return error_type()
    return InvalidResolutionRequestError()


# ----------------------------------------------------------------- models

_INT_ARRAY = ARRAY(Integer).with_variant(JSON(), "sqlite")
_TEXT_ARRAY = ARRAY(Text).with_variant(JSON(), "sqlite")
_JSON_OBJECT = JSONB().with_variant(JSON(), "sqlite")


class DatasetQualityReport(Base):
    """One detector run over one dataset version. Immutable except
    `resolution_version` (the restricted role may UPDATE nothing else)."""

    __tablename__ = "dataset_quality_reports"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    organization_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    project_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    dataset_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    dataset_name: Mapped[str] = mapped_column(String(200), nullable=False)
    dataset_purpose: Mapped[str] = mapped_column(String(32), nullable=False)
    dataset_version: Mapped[int] = mapped_column(Integer, nullable=False)
    dataset_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    report_seq: Mapped[int] = mapped_column(Integer, nullable=False)
    report_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    detector_versions: Mapped[dict[str, str]] = mapped_column(_JSON_OBJECT, nullable=False)
    detectors_reported: Mapped[list[str]] = mapped_column(_TEXT_ARRAY, nullable=False)
    issue_count: Mapped[int] = mapped_column(Integer, nullable=False)
    blocking_count: Mapped[int] = mapped_column(Integer, nullable=False)
    warning_count: Mapped[int] = mapped_column(Integer, nullable=False)
    resolution_version: Mapped[int] = mapped_column(Integer, nullable=False)
    created_by: Mapped[str] = mapped_column(String(200), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class DatasetQualityIssue(Base):
    """An engine-produced issue. Append-only; the detector message is never
    stored, only its SHA-256."""

    __tablename__ = "dataset_quality_issues"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    organization_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    report_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    dataset_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    issue_id: Mapped[str] = mapped_column(String(64), nullable=False)
    detector: Mapped[str] = mapped_column(String(64), nullable=False)
    category: Mapped[str] = mapped_column(String(32), nullable=False)
    severity: Mapped[str] = mapped_column(String(16), nullable=False)
    reason_code: Mapped[str] = mapped_column(String(100), nullable=False)
    message_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    batch_positions: Mapped[list[int]] = mapped_column(_INT_ARRAY, nullable=False)
    record_indices: Mapped[list[int | None]] = mapped_column(_INT_ARRAY, nullable=False)
    evidence_digest: Mapped[str | None] = mapped_column(String(64), nullable=True)
    is_detector_failure: Mapped[bool] = mapped_column(nullable=False)
    has_positions: Mapped[bool] = mapped_column(nullable=False)
    evidence_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class DatasetIssueResolution(Base):
    """One reviewer decision on one issue. Append-only; one per issue."""

    __tablename__ = "dataset_issue_resolutions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    organization_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    quality_report_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    issue_id: Mapped[str] = mapped_column(String(64), nullable=False)
    project_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    dataset_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    dataset_name: Mapped[str] = mapped_column(String(200), nullable=False)
    dataset_purpose: Mapped[str] = mapped_column(String(32), nullable=False)
    dataset_version: Mapped[int] = mapped_column(Integer, nullable=False)
    dataset_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    severity: Mapped[str] = mapped_column(String(16), nullable=False)
    is_detector_failure: Mapped[bool] = mapped_column(nullable=False)
    has_positions: Mapped[bool] = mapped_column(nullable=False)
    before_evidence_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    action: Mapped[str] = mapped_column(String(16), nullable=False)
    engine_decision: Mapped[str] = mapped_column(String(16), nullable=False)
    excluded_batch_positions: Mapped[list[int]] = mapped_column(_INT_ARRAY, nullable=False)
    successor_dataset_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    successor_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    successor_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    after_evidence_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    actor_id: Mapped[str] = mapped_column(String(200), nullable=False)
    actor_role: Mapped[str] = mapped_column(String(32), nullable=False)
    request_id: Mapped[str] = mapped_column(String(100), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(200), nullable=False)
    request_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    resolution_version_after: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


# ----------------------------------------------------------------- inputs


@dataclass(frozen=True)
class QualityActor:
    """`actor_id` and `role` come from the authenticated principal, never from
    a client-supplied string."""

    actor_type: str
    actor_id: str
    role: str
    request_id: str


@dataclass(frozen=True)
class ResolutionRequest:
    issue_id: str
    action: str
    reason: str
    expected_before_evidence_sha256: str
    excluded_batch_positions: tuple[int, ...] = ()
    successor_dataset_id: uuid.UUID | None = None


@dataclass(frozen=True)
class IssueView:
    issue: DatasetQualityIssue
    state: str  # open | resolved | waived
    resolution: DatasetIssueResolution | None


@dataclass(frozen=True)
class ResolveOutcome:
    report: DatasetQualityReport
    resolutions: tuple[DatasetIssueResolution, ...]
    replayed: bool


# ----------------------------------------------------------------- pure helpers


def _now() -> datetime:
    return datetime.now(UTC)


def _require_now(now: datetime | None) -> datetime:
    if now is None:
        return _now()
    if not isinstance(now, datetime) or now.tzinfo is None:
        raise ValueError("now must be a timezone-aware datetime")
    return now


def _sha256_hex(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def evidence_sha256(issue_id: str, dataset_sha256: str) -> str:
    """The evidence an issue is judged against: sha256(issue_id:dataset_sha256).
    The database pins every stored issue to exactly this value."""
    return _sha256_hex(f"{issue_id}:{dataset_sha256}")


def exclude_after_evidence(dataset_sha256: str, positions: Sequence[int]) -> str:
    """The evidence an exclusion produces: sha256(dataset_sha256:exclude:<p1,p2,..>).
    The database pins every `exclude` resolution to exactly this value."""
    return _sha256_hex(f"{dataset_sha256}:exclude:{','.join(str(p) for p in positions)}")


def _canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def request_digest(
    quality_report_id: uuid.UUID,
    expected_resolution_version: int,
    resolutions: Sequence[ResolutionRequest],
) -> str:
    """A digest of the request body (not the idempotency key), order-insensitive
    across resolutions, so a replay with the same body is recognised."""
    items = sorted(
        (
            {
                "issue_id": r.issue_id,
                "action": r.action,
                "reason": r.reason,
                "expected_before_evidence_sha256": r.expected_before_evidence_sha256,
                "excluded_batch_positions": list(r.excluded_batch_positions),
                "successor_dataset_id": (
                    None if r.successor_dataset_id is None else str(r.successor_dataset_id)
                ),
            }
            for r in resolutions
        ),
        key=lambda item: str(item["issue_id"]),
    )
    return _sha256_hex(
        _canonical(
            {
                "quality_report_id": str(quality_report_id),
                "expected_resolution_version": expected_resolution_version,
                "resolutions": items,
            }
        )
    )


def _report_digest(
    dataset_sha256: str,
    previous_report_sha256: str,
    issues: Sequence[QualityIssue],
    detectors_reported: Sequence[str],
    detector_versions: Mapping[str, str],
) -> str:
    return _sha256_hex(
        _canonical(
            {
                "dataset_sha256": dataset_sha256,
                "previous_report_sha256": previous_report_sha256,
                "issues": sorted([i.issue_id, i.severity] for i in issues),
                "detectors_reported": sorted(detectors_reported),
                "detector_versions": dict(detector_versions),
            }
        )
    )


def _is_clean_text(value: object, max_length: int) -> bool:
    """A non-blank exact `str` with no leading or trailing whitespace."""
    if type(value) is not str:
        return False
    return bool(value) and value == value.strip() and len(value) <= max_length


def _require_detector_versions(value: object) -> dict[str, str]:
    if not isinstance(value, Mapping):
        raise ValueError("detector_versions must be a mapping")
    versions: dict[str, str] = {}
    for name, version in value.items():
        if type(name) is not str or not _is_clean_text(version, _VERSION_MAX_LENGTH):
            raise ValueError("detector_versions must map detector names to non-blank strings")
        versions[name] = str(version)
    if not REQUIRED_DETECTORS <= versions.keys():
        raise ValueError("detector_versions must name every required detector")
    return versions


def _is_hex64(value: object) -> bool:
    return type(value) is str and _HEX64.fullmatch(value) is not None


def _issue_state(resolution: DatasetIssueResolution | None) -> str:
    return "open" if resolution is None else resolution.engine_decision


def _rebuild_quality_report(
    report: DatasetQualityReport,
    issues: Sequence[DatasetQualityIssue],
    resolutions: Mapping[str, DatasetIssueResolution],
) -> QualityReport:
    """The engine's own `QualityReport` for the stored rows. The detector
    message is not stored, so `message` is empty; nothing in the engine reads
    it when deciding a block."""
    rebuilt: list[QualityIssue] = []
    for row in sorted(issues, key=lambda r: r.issue_id):
        resolution = resolutions.get(row.issue_id)
        rebuilt.append(
            QualityIssue(
                issue_id=row.issue_id,
                detector=row.detector,
                category=row.category,
                severity=row.severity,
                reason_code=row.reason_code,
                message="",
                batch_positions=tuple(row.batch_positions),
                record_indices=tuple(row.record_indices),
                evidence_digest=row.evidence_digest,
                resolution_state=_issue_state(resolution),
                resolved_by=None if resolution is None else resolution.actor_id,
                resolution_reason=None if resolution is None else resolution.reason,
                is_detector_failure=row.is_detector_failure,
            )
        )
    return QualityReport(
        issues=tuple(rebuilt), detectors_reported=frozenset(report.detectors_reported)
    )


# ----------------------------------------------------------------- reads


def _latest_report_or_none(
    session: Session, context: TenantContext, dataset: Dataset
) -> DatasetQualityReport | None:
    statement = (
        select(DatasetQualityReport)
        .where(DatasetQualityReport.dataset_id == dataset.id)
        .order_by(DatasetQualityReport.report_seq.desc())
        .limit(1)
    )
    return execute_tenant_scoped(session, context.organization_id, statement).scalars().first()


def _visible_dataset(
    session: Session,
    context: TenantContext,
    project_id: uuid.UUID,
    dataset_id: uuid.UUID,
    include_held_out: bool,
) -> Dataset:
    return get_dataset(
        session,
        context,
        project_id=project_id,
        dataset_id=dataset_id,
        include_held_out=include_held_out,
    )


def get_latest_report(
    session: Session,
    context: TenantContext,
    *,
    project_id: uuid.UUID,
    dataset_id: uuid.UUID,
    include_held_out: bool,
) -> DatasetQualityReport:
    """The dataset's latest report. `DatasetNotFoundError` for a missing,
    foreign or hidden held-out dataset, `QualityReportNotFoundError` when no
    report was recorded yet."""
    dataset = _visible_dataset(session, context, project_id, dataset_id, include_held_out)
    report = _latest_report_or_none(session, context, dataset)
    if report is None:
        raise QualityReportNotFoundError()
    return report


def _report_rows(
    session: Session, context: TenantContext, report: DatasetQualityReport
) -> tuple[list[DatasetQualityIssue], dict[str, DatasetIssueResolution]]:
    issues = list(
        execute_tenant_scoped(
            session,
            context.organization_id,
            select(DatasetQualityIssue).where(DatasetQualityIssue.report_id == report.id),
        ).scalars()
    )
    resolutions = {
        row.issue_id: row
        for row in execute_tenant_scoped(
            session,
            context.organization_id,
            select(DatasetIssueResolution).where(
                DatasetIssueResolution.quality_report_id == report.id
            ),
        ).scalars()
    }
    return issues, resolutions


def load_resolved_quality_report(
    session: Session,
    context: TenantContext,
    *,
    project_id: uuid.UUID,
    dataset_id: uuid.UUID,
    include_held_out: bool = True,
) -> QualityReport:
    """The engine's `QualityReport` for the dataset's latest report, resolutions
    applied. FTF-0517/0518/0519/R006 pass it to `assert_clear_for_split` /
    `assert_clear_for_training` unchanged."""
    report = get_latest_report(
        session,
        context,
        project_id=project_id,
        dataset_id=dataset_id,
        include_held_out=include_held_out,
    )
    issues, resolutions = _report_rows(session, context, report)
    return _rebuild_quality_report(report, issues, resolutions)


def excluded_batch_positions(
    session: Session,
    context: TenantContext,
    *,
    project_id: uuid.UUID,
    dataset_id: uuid.UUID,
    include_held_out: bool = True,
) -> tuple[int, ...]:
    """Sorted batch positions the dataset's latest report excludes. Split
    generation drops them (Section 8.1: only clean/accepted records enter it)."""
    report = get_latest_report(
        session,
        context,
        project_id=project_id,
        dataset_id=dataset_id,
        include_held_out=include_held_out,
    )
    rows = execute_tenant_scoped(
        session,
        context.organization_id,
        select(DatasetIssueResolution).where(
            DatasetIssueResolution.quality_report_id == report.id,
            DatasetIssueResolution.action == "exclude",
        ),
    ).scalars()
    return tuple(sorted({p for row in rows for p in row.excluded_batch_positions}))


def _check_limit(limit: object) -> int:
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= MAX_PAGE_SIZE:
        raise ValueError("limit must be an integer between 1 and 200")
    return limit


def list_issues(
    session: Session,
    context: TenantContext,
    *,
    project_id: uuid.UUID,
    dataset_id: uuid.UUID,
    include_held_out: bool,
    limit: int,
    after: str | None = None,
    severity: str | None = None,
    state: str | None = None,
    detector: str | None = None,
    category: str | None = None,
) -> list[IssueView]:
    """Issues of the dataset's latest report, ordered by `issue_id`; `after` is
    the last `issue_id` of the previous page."""
    _check_limit(limit)
    if after is not None and not _is_hex64(after):
        raise ValueError("after must be a 64-character lowercase hex issue id")
    if severity is not None and severity not in _SEVERITIES:
        raise ValueError("severity must be 'blocking' or 'warning'")
    if state is not None and state not in _STATES:
        raise ValueError("state must be 'open', 'resolved' or 'waived'")
    if category is not None and category not in CATEGORIES:
        raise ValueError("category is not a known quality category")
    if detector is not None and not _is_clean_text(detector, 64):
        raise ValueError("detector must be a non-blank string")
    report = get_latest_report(
        session,
        context,
        project_id=project_id,
        dataset_id=dataset_id,
        include_held_out=include_held_out,
    )
    statement = (
        select(DatasetQualityIssue, DatasetIssueResolution)
        .outerjoin(
            DatasetIssueResolution,
            and_(
                DatasetIssueResolution.quality_report_id == DatasetQualityIssue.report_id,
                DatasetIssueResolution.issue_id == DatasetQualityIssue.issue_id,
            ),
        )
        .where(DatasetQualityIssue.report_id == report.id)
    )
    if after is not None:
        statement = statement.where(DatasetQualityIssue.issue_id > after)
    if severity is not None:
        statement = statement.where(DatasetQualityIssue.severity.in_((severity,)))
    if detector is not None:
        statement = statement.where(DatasetQualityIssue.detector.in_((detector,)))
    if category is not None:
        statement = statement.where(DatasetQualityIssue.category.in_((category,)))
    if state is not None:
        state_filters = {
            "open": DatasetIssueResolution.id.is_(None),
            "resolved": DatasetIssueResolution.engine_decision.in_(("resolved",)),
            "waived": DatasetIssueResolution.engine_decision.in_(("waived",)),
        }
        statement = statement.where(state_filters[state])
    statement = statement.order_by(DatasetQualityIssue.issue_id).limit(limit)
    rows = execute_tenant_scoped(session, context.organization_id, statement).all()
    return [IssueView(issue=i, state=_issue_state(r), resolution=r) for i, r in rows]


def get_issue(
    session: Session,
    context: TenantContext,
    *,
    project_id: uuid.UUID,
    dataset_id: uuid.UUID,
    issue_id: str,
    include_held_out: bool,
) -> IssueView:
    """One issue of the latest report; `IssueNotFoundError` when it is not one."""
    if not _is_hex64(issue_id):
        raise IssueNotFoundError()
    report = get_latest_report(
        session,
        context,
        project_id=project_id,
        dataset_id=dataset_id,
        include_held_out=include_held_out,
    )
    issues, resolutions = _report_rows(session, context, report)
    row = {r.issue_id: r for r in issues}.get(issue_id)
    if row is None:
        raise IssueNotFoundError()
    resolution = resolutions.get(issue_id)
    return IssueView(issue=row, state=_issue_state(resolution), resolution=resolution)


def list_resolutions(
    session: Session,
    context: TenantContext,
    *,
    project_id: uuid.UUID,
    dataset_id: uuid.UUID,
    include_held_out: bool,
    limit: int,
) -> list[DatasetIssueResolution]:
    """The dataset's most recent resolutions across every report, newest first."""
    _check_limit(limit)
    dataset = _visible_dataset(session, context, project_id, dataset_id, include_held_out)
    statement = (
        select(DatasetIssueResolution)
        .where(DatasetIssueResolution.dataset_id == dataset.id)
        .order_by(DatasetIssueResolution.created_at.desc(), DatasetIssueResolution.id.desc())
        .limit(limit)
    )
    return list(execute_tenant_scoped(session, context.organization_id, statement).scalars())


# ----------------------------------------------------------------- record
def _same_findings(
    session: Session,
    context: TenantContext,
    latest: DatasetQualityReport,
    quality: QualityReport,
    reported: Sequence[str],
    versions: Mapping[str, str],
) -> bool:
    """Whether recording would repeat the latest report's findings exactly."""
    if latest.detector_versions != dict(versions) or sorted(latest.detectors_reported) != list(
        reported
    ):
        return False
    stored = {
        (row.issue_id, row.severity)
        for row in execute_tenant_scoped(
            session,
            context.organization_id,
            select(DatasetQualityIssue).where(DatasetQualityIssue.report_id == latest.id),
        ).scalars()
    }
    return stored == {(i.issue_id, i.severity) for i in quality.issues}


def record_quality_report(
    session: Session,
    context: TenantContext,
    *,
    project_id: uuid.UUID,
    dataset_id: uuid.UUID,
    runs: Sequence[DetectorRun],
    extra_issues: Sequence[QualityIssueInput] = (),
    detector_versions: Mapping[str, str],
    actor: QualityActor,
    now: datetime | None = None,
) -> DatasetQualityReport:
    """Evaluates the detector runs with FTF-0515's engine and stores the ledger.
    Raises `DatasetNotFoundError`, `QualityEngineError` (malformed runs) or
    `ValueError`; on any raise nothing was written. Re-recording findings that
    equal the latest report's returns that report and writes nothing."""
    versions = _require_detector_versions(detector_versions)
    recorded_at = _require_now(now)
    dataset = _visible_dataset(session, context, project_id, dataset_id, True)
    quality = evaluate_quality(runs, extra_issues=extra_issues)
    for issue in quality.issues:
        if (
            not _is_clean_text(issue.detector, 64)
            or _REASON_CODE.fullmatch(issue.reason_code) is None
            or (issue.evidence_digest is not None and not _is_hex64(issue.evidence_digest))
            or any(type(p) is not int or p < 0 for p in issue.batch_positions)
        ):
            raise ValueError(
                "an issue carries a malformed detector, reason code, digest or position"
            )
    reported = sorted(quality.detectors_reported)

    latest = _latest_report_or_none(session, context, dataset)
    if latest is not None and _same_findings(session, context, latest, quality, reported, versions):
        return latest
    previous_sha = "" if latest is None else latest.report_sha256
    digest = _report_digest(
        dataset.verified_sha256, previous_sha, quality.issues, reported, versions
    )

    severities = Counter(i.severity for i in quality.issues)
    report = DatasetQualityReport(
        id=uuid.uuid4(),
        organization_id=context.organization_id,
        project_id=dataset.project_id,
        dataset_id=dataset.id,
        dataset_name=dataset.name,
        dataset_purpose=dataset.purpose,
        dataset_version=dataset.version,
        dataset_sha256=dataset.verified_sha256,
        report_seq=1 if latest is None else latest.report_seq + 1,
        report_sha256=digest,
        detector_versions=versions,
        detectors_reported=reported,
        issue_count=len(quality.issues),
        blocking_count=severities["blocking"],
        warning_count=severities["warning"],
        resolution_version=0,
        created_by=actor.actor_id,
        created_at=recorded_at,
        updated_at=recorded_at,
    )
    session.add(report)
    session.flush()
    for issue in quality.issues:
        session.add(
            DatasetQualityIssue(
                id=uuid.uuid4(),
                organization_id=context.organization_id,
                report_id=report.id,
                dataset_sha256=dataset.verified_sha256,
                issue_id=issue.issue_id,
                detector=issue.detector,
                category=issue.category,
                severity=issue.severity,
                reason_code=issue.reason_code,
                message_sha256=_sha256_hex(issue.message),
                batch_positions=list(issue.batch_positions),
                record_indices=list(issue.record_indices),
                evidence_digest=issue.evidence_digest,
                is_detector_failure=issue.is_detector_failure,
                has_positions=bool(issue.batch_positions),
                evidence_sha256=evidence_sha256(issue.issue_id, dataset.verified_sha256),
                created_at=recorded_at,
            )
        )
    write_audit_event(
        session,
        organization_id=context.organization_id,
        actor_type=actor.actor_type,
        actor_id=actor.actor_id,
        action=_ACTION_REPORT_RECORD,
        resource_type=_RESOURCE_REPORT,
        resource_id=str(report.id),
        request_id=actor.request_id,
        details={
            "project_id": str(dataset.project_id),
            "dataset_id": str(dataset.id),
            "report_seq": report.report_seq,
            "issue_count": report.issue_count,
            "blocking_count": report.blocking_count,
            "warning_count": report.warning_count,
            "detectors_reported": reported,
        },
    )
    session.flush()
    return report


# ----------------------------------------------------------------- resolve


def _validate_request(
    expected_resolution_version: object,
    idempotency_key: object,
    resolutions: object,
) -> tuple[int, str, tuple[ResolutionRequest, ...]]:
    if (
        isinstance(expected_resolution_version, bool)
        or not isinstance(expected_resolution_version, int)
        or expected_resolution_version < 0
    ):
        raise InvalidResolutionRequestError()
    if not _is_clean_text(idempotency_key, _KEY_MAX_LENGTH):
        raise InvalidResolutionRequestError()
    if (
        not isinstance(resolutions, Sequence)
        or isinstance(resolutions, str)
        or not 1 <= len(resolutions) <= MAX_RESOLUTIONS_PER_REQUEST
    ):
        raise InvalidResolutionRequestError()
    seen: set[str] = set()
    for item in resolutions:
        if (
            not isinstance(item, ResolutionRequest)
            or not _is_hex64(item.issue_id)
            or item.issue_id in seen
            or item.action not in ALL_ACTIONS
            or not _is_clean_text(item.reason, _REASON_MAX_LENGTH)
            or not _is_hex64(item.expected_before_evidence_sha256)
            or not isinstance(item.excluded_batch_positions, tuple)
            or any(type(p) is not int or p < 0 for p in item.excluded_batch_positions)
            or (
                item.successor_dataset_id is not None
                and not isinstance(item.successor_dataset_id, uuid.UUID)
            )
        ):
            raise InvalidResolutionRequestError()
        seen.add(item.issue_id)
    return expected_resolution_version, str(idempotency_key), tuple(resolutions)


class _ActionRule(NamedTuple):
    takes_positions: bool
    takes_successor: bool
    needs_issue_positions: bool
    decision: str


# Per action: whether it carries exclusion positions, whether it carries a
# successor dataset, whether the issue must name batch positions, and the
# decision FTF-0515's engine applies. Table lookups, not string comparisons.
_ACTION_RULES: dict[str, _ActionRule] = {
    "exclude": _ActionRule(True, False, True, "resolved"),
    "correct": _ActionRule(False, True, True, "resolved"),
    "new_version": _ActionRule(False, True, False, "resolved"),
    "waive": _ActionRule(False, False, False, "waived"),
}


def _check_shape(item: ResolutionRequest, issue: DatasetQualityIssue) -> None:
    rule = _ACTION_RULES[item.action]
    positions = item.excluded_batch_positions
    if rule.takes_positions:
        if not positions or list(positions) != sorted(set(positions)):
            raise InvalidResolutionRequestError()
        if not set(positions) <= set(issue.batch_positions):
            raise PositionsNotInIssueError()
    elif positions:
        raise InvalidResolutionRequestError()
    if rule.needs_issue_positions and not issue.has_positions:
        raise PositionsNotInIssueError()
    if rule.takes_successor != (item.successor_dataset_id is not None):
        raise InvalidResolutionRequestError()


def _check_successor(
    session: Session,
    context: TenantContext,
    dataset: Dataset,
    successor_id: uuid.UUID,
    include_held_out: bool,
) -> Dataset:
    try:
        successor = get_dataset(
            session,
            context,
            project_id=dataset.project_id,
            dataset_id=successor_id,
            include_held_out=include_held_out,
        )
    except DatasetNotFoundError as exc:
        raise IssueNotFoundError() from exc
    if successor.name != dataset.name or successor.purpose != dataset.purpose:
        raise InvalidResolutionRequestError()
    if successor.version <= dataset.version:
        raise SuccessorNotLaterVersionError()
    latest = _latest_report_or_none(session, context, successor)
    if latest is None or not REQUIRED_DETECTORS <= set(latest.detectors_reported):
        raise SuccessorNotRevalidatedError()
    return successor


def _replay(
    session: Session,
    context: TenantContext,
    report: DatasetQualityReport,
    idempotency_key: str,
    digest: str,
) -> ResolveOutcome | None:
    rows = list(
        execute_tenant_scoped(
            session,
            context.organization_id,
            select(DatasetIssueResolution)
            .where(DatasetIssueResolution.idempotency_key == idempotency_key)
            .order_by(DatasetIssueResolution.issue_id),
        ).scalars()
    )
    if not rows:
        return None
    if any(r.request_sha256 != digest or r.quality_report_id != report.id for r in rows):
        raise IdempotencyKeyReusedError()
    return ResolveOutcome(report=report, resolutions=tuple(rows), replayed=True)


def submit_resolutions(
    session: Session,
    context: TenantContext,
    *,
    project_id: uuid.UUID,
    dataset_id: uuid.UUID,
    quality_report_id: uuid.UUID,
    expected_resolution_version: int,
    idempotency_key: str,
    resolutions: Sequence[ResolutionRequest],
    include_held_out: bool,
    actor: QualityActor,
    now: datetime | None = None,
) -> ResolveOutcome:
    """Records every decision of one request, or none. Raises a
    `QualityStoreError` subclass (stable `code` and `status`) for a refusal and
    `DatasetNotFoundError` for a missing or hidden dataset. The same
    `idempotency_key` with the same request returns the stored decisions
    (`replayed=True`) and writes nothing."""
    version, key, requests = _validate_request(
        expected_resolution_version, idempotency_key, resolutions
    )
    if not isinstance(quality_report_id, uuid.UUID):
        raise QualityReportNotFoundError()
    decided_at = _require_now(now)
    if actor.role not in {"ml_engineer", *ADMIN_ROLES}:
        raise InvalidResolutionRequestError()
    if any(r.action == "waive" for r in requests) and actor.role not in ADMIN_ROLES:
        raise WaiveNotPermittedError()

    dataset = _visible_dataset(session, context, project_id, dataset_id, include_held_out)
    report = (
        execute_tenant_scoped(
            session,
            context.organization_id,
            select(DatasetQualityReport)
            .where(
                DatasetQualityReport.id == quality_report_id,
                DatasetQualityReport.dataset_id == dataset.id,
            )
            .with_for_update(),
        )
        .scalars()
        .one_or_none()
    )
    if report is None:
        raise QualityReportNotFoundError()

    digest = request_digest(quality_report_id, version, requests)
    replayed = _replay(session, context, report, key, digest)
    if replayed is not None:
        return replayed
    latest = _latest_report_or_none(session, context, dataset)
    if latest is None or latest.id != report.id:
        raise ReportSupersededError()
    if report.resolution_version != version:
        raise ReportVersionConflictError()

    issues, decided = _report_rows(session, context, report)
    by_id = {row.issue_id: row for row in issues}
    successors: dict[uuid.UUID, Dataset] = {}
    for item in requests:
        issue = by_id.get(item.issue_id)
        if issue is None:
            raise IssueNotFoundError()
        if item.issue_id in decided:
            raise IssueAlreadyDecidedError()
        if not hmac.compare_digest(item.expected_before_evidence_sha256, issue.evidence_sha256):
            raise EvidenceMismatchError()

    engine_report = _rebuild_quality_report(report, issues, decided)
    try:
        apply_resolutions(
            engine_report,
            [
                IssueResolution(
                    issue_id=item.issue_id,
                    decision=_ACTION_RULES[item.action].decision,
                    reviewer_id=actor.actor_id,
                    reason=item.reason,
                )
                for item in requests
            ],
        )
    except QualityEngineError as exc:
        raise _map_engine_error(exc) from exc
    # After the engine, so a detector failure or a blocking waiver is refused
    # with its own code whatever positions the request carries.
    for item in requests:
        _check_shape(item, by_id[item.issue_id])

    for item in requests:
        if item.successor_dataset_id is not None and item.successor_dataset_id not in successors:
            successors[item.successor_dataset_id] = _check_successor(
                session, context, dataset, item.successor_dataset_id, include_held_out
            )

    new_version = report.resolution_version + 1
    rows: list[DatasetIssueResolution] = []
    for item in requests:
        issue = by_id[item.issue_id]
        successor = (
            None if item.successor_dataset_id is None else successors[item.successor_dataset_id]
        )
        if _ACTION_RULES[item.action].takes_positions:
            after: str | None = exclude_after_evidence(
                dataset.verified_sha256, item.excluded_batch_positions
            )
        elif successor is not None:
            after = successor.verified_sha256
        else:
            after = None
        rows.append(
            DatasetIssueResolution(
                id=uuid.uuid4(),
                organization_id=context.organization_id,
                quality_report_id=report.id,
                issue_id=item.issue_id,
                project_id=dataset.project_id,
                dataset_id=dataset.id,
                dataset_name=dataset.name,
                dataset_purpose=dataset.purpose,
                dataset_version=dataset.version,
                dataset_sha256=dataset.verified_sha256,
                severity=issue.severity,
                is_detector_failure=issue.is_detector_failure,
                has_positions=issue.has_positions,
                before_evidence_sha256=issue.evidence_sha256,
                action=item.action,
                engine_decision=_ACTION_RULES[item.action].decision,
                excluded_batch_positions=list(item.excluded_batch_positions),
                successor_dataset_id=None if successor is None else successor.id,
                successor_version=None if successor is None else successor.version,
                successor_sha256=None if successor is None else successor.verified_sha256,
                after_evidence_sha256=after,
                reason=item.reason,
                actor_id=actor.actor_id,
                actor_role=actor.role,
                request_id=actor.request_id,
                idempotency_key=key,
                request_sha256=digest,
                resolution_version_after=new_version,
                created_at=decided_at,
            )
        )
    session.add_all(rows)
    session.flush()
    report.resolution_version = new_version
    report.updated_at = decided_at
    for row in rows:
        write_audit_event(
            session,
            organization_id=context.organization_id,
            actor_type=actor.actor_type,
            actor_id=actor.actor_id,
            action=_ACTION_ISSUE_RESOLVE,
            resource_type=_RESOURCE_RESOLUTION,
            resource_id=str(row.id),
            request_id=actor.request_id,
            details={
                "issue_id": row.issue_id,
                "project_id": str(dataset.project_id),
                "dataset_id": str(dataset.id),
                "quality_report_id": str(report.id),
                "action": row.action,
                "before_evidence_sha256": row.before_evidence_sha256,
                "after_evidence_sha256": row.after_evidence_sha256,
                "successor_dataset_id": (
                    None if row.successor_dataset_id is None else str(row.successor_dataset_id)
                ),
                "reason_length": len(row.reason),
                "resolution_version_after": new_version,
            },
        )
    try:
        session.flush()
    except IntegrityError as exc:
        if "uq_dataset_issue_resolutions_one_decision" in str(exc.orig):
            raise IssueAlreadyDecidedError() from exc
        raise
    return ResolveOutcome(report=report, resolutions=tuple(rows), replayed=False)
