"""Immutable split manifest store (FTF-0519-P1; Section 0 items 13-14, 6.10-6.12, 7, 7A.10, 8.1
steps 13/16-18, 8.2, 9, 10, 65, 67.2/67.4, 77, 81).

FTF-0517 allocates a four-role split, FTF-0518 reports its leakage and FTF-0516-P1 persists the
quality ledger. This module freezes the result. The tables, constraints, grants and triggers are in
`0030_dataset_split_manifests.py`; `workers/data/immutable_split_manifest.py` is the pure half
(role map, role hashes, frozen hash, legacy migration, training view guard).

Rules enforced here, on top of the database's own:

- `propose_split_manifest` is the only way a proposal is built. Per source dataset it reads the
  dataset (`get_dataset`), the resolved quality report (`load_resolved_quality_report`) and runs
  FTF-0515 `assert_clear_for_split` (`quality_not_clear`), drops `excluded_batch_positions`, pins a
  `held_out_evaluation` / `candidate_development` source to its evaluation role from
  `datasets.purpose` (never from the caller), and refuses `baseline_only`. It then runs
  `allocate_splits`, checks every placed role's purpose against the source connection
  (`authorize_dataset_source_purpose`, `purpose_not_authorized`) and runs `detect_split_leakage`
  itself on that exact manifest: it accepts no caller-supplied report. Prior campaigns must be
  supplied for every locked manifest of the project and must match the stored record ids and
  hashes exactly (`prior_sources_incomplete`). A manifest above `MAX_MANIFEST_RECORDS` is refused
  (`manifest_too_large`), never truncated. A three-split input is refused
  (`legacy_manifest_requires_migration`): `migrate_legacy_split_manifest` is the explicit path.
- A change is a new version: an unchanged re-proposal returns the latest row and writes nothing,
  anything else is version + 1 with `parent_version` = the previous version. No row is edited.
- `approve_split_manifest` takes the reviewer from the authenticated actor, never from input,
  rebuilds the leakage report from its stored content-free body (a body that does not hash to the
  recorded hash is refused), applies the reviews, requires `assert_leakage_clear(report,
  "final_freeze")` and locks. The frozen hash covers the proposal, the reviewed report, the reviews,
  the approver, all four role rows and every source grant. A stale `expected_proposal_sha256` is
  `manifest_changed`.
- `load_training_split` is the training access path. It filters `train` / `validation` in SQL and
  asserts the role of every row again, re-verifies the frozen hash and the served rows against the
  frozen role hashes, re-asserts the leakage report for `training`, FTF-0515
  `assert_clear_for_training` on each source's latest report (a different report or exclusion set
  is `quality_report_changed`) and re-authorizes each frozen grant live (a revoked or narrowed grant
  is `grant_changed`, Section 67.4). There is NO reader of final-qualification records here: the
  sealed reader is a later task (FTF-H020). `load_development_split` serves only `development_eval`.

Every refusal is a `SplitManifestError` with a stable `code`, raised before anything is written; the
whole proposal is one transaction. Audit rows (`dataset_split_manifest.proposed` / `locked` /
`rejected` / `legacy_migrated`) carry ids, counts and hashes, never record content or ids.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    DateTime,
    Integer,
    String,
    and_,
    func,
    select,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Mapped, Session, mapped_column
from workers.data.group_aware_split_engine import (
    SplitEngineError,
    SplitManifest,
    SplitRecord,
    SplitSpec,
    allocate_splits,
    verify_manifest,
)
from workers.data.immutable_split_manifest import (
    MAX_MANIFEST_RECORDS,
    ROLE_PURPOSE,
    TRAINING_ROLES,
    AssignmentRow,
    LegacyThreeSplitManifest,
    ManifestContent,
    ManifestDomainError,
    RoleRow,
    SourceGrant,
    assert_development_rows,
    assert_training_rows,
    build_assignment_rows,
    build_role_rows,
    compute_frozen_manifest_sha256,
    compute_proposal_sha256,
    excluded_positions_sha256,
    is_legacy_three_split,
    migrate_legacy_three_split,
    pg_safe,
    pinned_role_for_purpose,
    report_from_body,
    role_sha256,
    same,
    verify_frozen_content,
)
from workers.data.quality_issue_engine import (
    QualityBlockedError,
    assert_clear_for_split,
    assert_clear_for_training,
)
from workers.data.split_leakage_detector import (
    PRIOR_ROLES,
    EmbeddingRoute,
    LeakageBlockedError,
    LeakageEngineError,
    LeakageRecord,
    LeakageReview,
    PriorQualificationSource,
    apply_leakage_reviews,
    assert_leakage_clear,
    compute_report_sha256,
    detect_split_leakage,
    report_body,
)

from app.auth.audit_event_writer import write_audit_event
from app.auth.organization_workspace_schema import Base
from app.auth.tenant_isolation_query_guards import execute_tenant_scoped
from app.datasets.dataset_quality_store import (
    ADMIN_ROLES,
    QualityReportNotFoundError,
    excluded_batch_positions,
    get_latest_report,
    load_resolved_quality_report,
)
from app.datasets.dataset_source_connection_crud import (
    DatasetSourceAuthorizationError,
    DatasetSourceConnectionNotFoundError,
    authorize_dataset_source_purpose,
)
from app.datasets.dataset_upload_store import (
    Dataset,
    DatasetNotFoundError,
    DatasetUploadSession,
    get_dataset,
)
from app.db.tenant_context import TenantContext

__all__ = [
    "CODES",
    "DatasetSplitAssignment",
    "DatasetSplitManifest",
    "DatasetSplitManifestRole",
    "DatasetSplitManifestSource",
    "ManifestSummary",
    "ReviewInput",
    "SourceRecord",
    "SplitActor",
    "SplitManifestError",
    "SplitSourceInput",
    "TrainingSplitView",
    "approve_split_manifest",
    "get_split_manifest",
    "list_split_manifests",
    "load_development_split",
    "load_training_split",
    "migrate_legacy_split_manifest",
    "propose_split_manifest",
    "reject_split_manifest",
]

CODES = (
    "actor_not_permitted",
    "dataset_not_found",
    "grant_changed",
    "invalid_request",
    "invalid_review",
    "leakage_not_clear",
    "legacy_manifest_requires_migration",
    "manifest_changed",
    "manifest_insufficient",
    "manifest_not_found",
    "manifest_not_locked",
    "manifest_not_proposed",
    "manifest_tampered",
    "manifest_too_large",
    "no_records",
    "prior_sources_incomplete",
    "purpose_not_authorized",
    "quality_not_clear",
    "quality_report_changed",
    "source_not_splittable",
    "source_role_pinned",
    "version_conflict",
)
_RESOURCE = "dataset_split_manifest"
_MAX_PAGE_SIZE = 200
_MAX_KEY = 400
_CHUNK = 5000

_INT_ARRAY = ARRAY(Integer).with_variant(JSON(), "sqlite")
_TEXT_ARRAY = ARRAY(String).with_variant(JSON(), "sqlite")
_JSON = JSONB().with_variant(JSON(), "sqlite")


class SplitManifestError(Exception):
    """A refusal with a stable `code`. Nothing was written when it was raised."""

    def __init__(self, code: str) -> None:
        assert code in CODES, code
        super().__init__(code)
        self.code = code


# ------------------------------------------------------------------ rows


class DatasetSplitManifest(Base):
    """One manifest version. Immutable except the single proposed -> locked | rejected step."""

    __tablename__ = "dataset_split_manifests"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    organization_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    project_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    parent_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    strategy: Mapped[str] = mapped_column(String(64), nullable=False)
    seed: Mapped[str] = mapped_column(String(128), nullable=False)
    ratios: Mapped[list[int]] = mapped_column(_INT_ARRAY, nullable=False)
    cutoff: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    normalization_version: Mapped[str] = mapped_column(String(64), nullable=False)
    tokenizer_identity: Mapped[str | None] = mapped_column(String(200), nullable=True)
    split_manifest_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    proposal_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    leakage_report: Mapped[dict[str, Any]] = mapped_column(_JSON, nullable=False)
    leakage_report_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    leakage_status: Mapped[str] = mapped_column(String(16), nullable=False)
    leakage_reviews: Mapped[list[dict[str, Any]]] = mapped_column(_JSON, nullable=False)
    reviewed_leakage_report_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    migrated_from_legacy_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    frozen_manifest_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    approved_by: Mapped[str | None] = mapped_column(String(200), nullable=True)
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_by: Mapped[str] = mapped_column(String(200), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class DatasetSplitManifestRole(Base):
    __tablename__ = "dataset_split_manifest_roles"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    organization_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    manifest_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    role: Mapped[str] = mapped_column(String(24), nullable=False)
    purpose: Mapped[str] = mapped_column(String(32), nullable=False)
    record_count: Mapped[int] = mapped_column(Integer, nullable=False)
    group_count: Mapped[int] = mapped_column(Integer, nullable=False)
    role_sha256: Mapped[str] = mapped_column(String(64), nullable=False)


class DatasetSplitManifestSource(Base):
    __tablename__ = "dataset_split_manifest_sources"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    organization_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    manifest_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    project_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    dataset_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    dataset_name: Mapped[str] = mapped_column(String(200), nullable=False)
    dataset_purpose: Mapped[str] = mapped_column(String(32), nullable=False)
    dataset_version: Mapped[int] = mapped_column(Integer, nullable=False)
    dataset_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    dataset_source_connection_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False
    )
    allowed_purposes_snapshot: Mapped[list[str]] = mapped_column(_TEXT_ARRAY, nullable=False)
    quality_report_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    quality_report_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    excluded_positions_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class DatasetSplitAssignment(Base):
    __tablename__ = "dataset_split_assignments"

    manifest_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    record_id: Mapped[str] = mapped_column(String(400), primary_key=True)
    organization_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    source_dataset_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    batch_position: Mapped[int] = mapped_column(Integer, nullable=False)
    role: Mapped[str] = mapped_column(String(24), nullable=False)
    group_id: Mapped[str] = mapped_column(String(400), nullable=False)
    record_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    normalized_sha256: Mapped[str] = mapped_column(String(64), nullable=False)


# ------------------------------------------------------------------ inputs


@dataclass(frozen=True)
class SplitActor:
    """`actor_id` and `role` come from the authenticated principal, never from a request body.
    Only a role that may see held-out data may propose, approve or reject."""

    actor_type: str
    actor_id: str
    role: str
    request_id: str


@dataclass(frozen=True)
class SourceRecord:
    """One record of a source dataset as the data worker read it: its batch position, its FTF-0506
    canonical `content`, the lineage hashes and any linkage keys. No role, ever."""

    batch_position: int
    content: Mapping[str, object]
    record_sha256: str
    normalized_sha256: str
    group_keys: tuple[str, ...] = ()
    event_time: int | None = None


@dataclass(frozen=True)
class SplitSourceInput:
    dataset_id: uuid.UUID
    records: tuple[SourceRecord, ...]


@dataclass(frozen=True)
class ReviewInput:
    """A reviewer's decision on one finding. The reviewer is the authenticated actor."""

    finding_id: str
    rationale: str


@dataclass(frozen=True)
class ManifestSummary:
    manifest: DatasetSplitManifest
    roles: tuple[DatasetSplitManifestRole, ...]
    sources: tuple[DatasetSplitManifestSource, ...]


@dataclass(frozen=True)
class TrainingSplitView:
    """The rows a trainer may read: train / validation only, never a final or development record."""

    manifest_id: uuid.UUID
    version: int
    frozen_manifest_sha256: str
    rows: tuple[AssignmentRow, ...]


# ------------------------------------------------------------------ helpers


def _now() -> datetime:
    return datetime.now(UTC)


def _clean(value: object, limit: int) -> str:
    if (
        type(value) is not str
        or not value
        or not same(value, value.strip())
        or len(value) > limit
        or not pg_safe(value)
    ):
        raise SplitManifestError("invalid_request")
    return value


def _require_actor(actor: object) -> SplitActor:
    if not isinstance(actor, SplitActor):
        raise SplitManifestError("invalid_request")
    _clean(actor.actor_type, 64)
    _clean(actor.actor_id, 200)
    _clean(actor.role, 64)
    _clean(actor.request_id, 100)
    if actor.role not in ADMIN_ROLES:
        raise SplitManifestError("actor_not_permitted")
    return actor


def _fetch_manifest(
    session: Session,
    context: TenantContext,
    project_id: uuid.UUID,
    manifest_id: uuid.UUID,
    *,
    for_update: bool = False,
) -> DatasetSplitManifest:
    if not isinstance(project_id, uuid.UUID) or not isinstance(manifest_id, uuid.UUID):
        raise SplitManifestError("manifest_not_found")
    statement = select(DatasetSplitManifest).where(
        DatasetSplitManifest.id == manifest_id, DatasetSplitManifest.project_id == project_id
    )
    if for_update:
        statement = statement.with_for_update()
    row = execute_tenant_scoped(session, context.organization_id, statement).scalars().first()
    if row is None:
        raise SplitManifestError("manifest_not_found")
    return row


def _fetch_roles(
    session: Session, context: TenantContext, manifest_id: uuid.UUID
) -> tuple[DatasetSplitManifestRole, ...]:
    rows = execute_tenant_scoped(
        session,
        context.organization_id,
        select(DatasetSplitManifestRole).where(DatasetSplitManifestRole.manifest_id == manifest_id),
    ).scalars()
    order = list(ROLE_PURPOSE)
    return tuple(sorted(rows, key=lambda r: order.index(r.role)))


def _fetch_sources(
    session: Session, context: TenantContext, manifest_id: uuid.UUID
) -> tuple[DatasetSplitManifestSource, ...]:
    rows = execute_tenant_scoped(
        session,
        context.organization_id,
        select(DatasetSplitManifestSource).where(
            DatasetSplitManifestSource.manifest_id == manifest_id
        ),
    ).scalars()
    return tuple(sorted(rows, key=lambda r: str(r.dataset_id)))


def _content_from_rows(
    manifest: DatasetSplitManifest,
    roles: Sequence[DatasetSplitManifestRole],
    sources: Sequence[DatasetSplitManifestSource],
) -> ManifestContent:
    """The pure content for stored rows. Any malformed row is `manifest_tampered`."""
    try:
        return ManifestContent(
            project_id=str(manifest.project_id),
            name=manifest.name,
            version=manifest.version,
            parent_version=manifest.parent_version,
            strategy=manifest.strategy,
            seed=manifest.seed,
            ratios=tuple(manifest.ratios),  # type: ignore[arg-type]
            cutoff=manifest.cutoff,
            normalization_version=manifest.normalization_version,
            tokenizer_identity=manifest.tokenizer_identity,
            split_manifest_sha256=manifest.split_manifest_sha256,
            leakage_report=dict(manifest.leakage_report),
            leakage_report_sha256=manifest.leakage_report_sha256,
            leakage_status=manifest.leakage_status,
            roles=tuple(
                RoleRow(r.role, r.purpose, r.record_count, r.group_count, r.role_sha256)
                for r in roles
            ),
            sources=tuple(
                SourceGrant(
                    dataset_id=str(s.dataset_id),
                    dataset_version=s.dataset_version,
                    dataset_purpose=s.dataset_purpose,
                    dataset_sha256=s.dataset_sha256,
                    dataset_source_connection_id=str(s.dataset_source_connection_id),
                    allowed_purposes_snapshot=tuple(s.allowed_purposes_snapshot),
                    quality_report_id=str(s.quality_report_id),
                    quality_report_sha256=s.quality_report_sha256,
                    excluded_positions_sha256=s.excluded_positions_sha256,
                )
                for s in sources
            ),
            leakage_reviews=tuple(dict(r) for r in manifest.leakage_reviews),
            reviewed_leakage_report_sha256=manifest.reviewed_leakage_report_sha256,
            migrated_from_legacy_sha256=manifest.migrated_from_legacy_sha256,
            approved_by=manifest.approved_by,
        )
    except (ManifestDomainError, TypeError, ValueError):
        raise SplitManifestError("manifest_tampered") from None


def _audit(
    session: Session,
    context: TenantContext,
    actor: SplitActor,
    action: str,
    manifest: DatasetSplitManifest,
    extra: Mapping[str, object] | None = None,
) -> None:
    details: dict[str, Any] = {
        "project_id": str(manifest.project_id),
        "name": manifest.name,
        "version": manifest.version,
        "split_manifest_sha256": manifest.split_manifest_sha256,
        "proposal_sha256": manifest.proposal_sha256,
        "leakage_status": manifest.leakage_status,
    }
    if extra:
        details.update(extra)
    write_audit_event(
        session,
        organization_id=context.organization_id,
        actor_type=actor.actor_type,
        actor_id=actor.actor_id,
        action=action,
        resource_type=_RESOURCE,
        resource_id=str(manifest.id),
        request_id=actor.request_id,
        details=details,
    )


# ------------------------------------------------------------------ reads


def get_split_manifest(
    session: Session,
    context: TenantContext,
    *,
    project_id: uuid.UUID,
    manifest_id: uuid.UUID,
) -> ManifestSummary:
    """The manifest with its role rows (counts and role hashes only) and its sources. Never any
    assignment, record id, group id or storage key."""
    manifest = _fetch_manifest(session, context, project_id, manifest_id)
    return ManifestSummary(
        manifest,
        _fetch_roles(session, context, manifest.id),
        _fetch_sources(session, context, manifest.id),
    )


def list_split_manifests(
    session: Session,
    context: TenantContext,
    *,
    project_id: uuid.UUID,
    limit: int,
    after: uuid.UUID | None = None,
) -> list[DatasetSplitManifest]:
    if type(limit) is not int or not 1 <= limit <= _MAX_PAGE_SIZE:
        raise ValueError("limit must be an integer between 1 and 200")
    if after is not None and not isinstance(after, uuid.UUID):
        raise ValueError("after must be a uuid.UUID or None")
    statement = select(DatasetSplitManifest).where(DatasetSplitManifest.project_id == project_id)
    if after is not None:
        statement = statement.where(DatasetSplitManifest.id > after)
    statement = statement.order_by(DatasetSplitManifest.id).limit(limit)
    return list(execute_tenant_scoped(session, context.organization_id, statement).scalars())


# ------------------------------------------------------------------ propose


@dataclass(frozen=True)
class _PreparedSource:
    dataset: Dataset
    pinned_role: str | None
    connection_id: uuid.UUID
    quality_report_id: uuid.UUID
    quality_report_sha256: str
    excluded_positions: tuple[int, ...]
    records: tuple[SourceRecord, ...]


def _prepare_source(
    session: Session,
    context: TenantContext,
    project_id: uuid.UUID,
    source: SplitSourceInput,
) -> _PreparedSource:
    if not isinstance(source, SplitSourceInput) or not isinstance(source.dataset_id, uuid.UUID):
        raise SplitManifestError("invalid_request")
    if type(source.records) is not tuple:
        raise SplitManifestError("invalid_request")
    try:
        dataset = get_dataset(
            session,
            context,
            project_id=project_id,
            dataset_id=source.dataset_id,
            include_held_out=True,
        )
    except DatasetNotFoundError:
        raise SplitManifestError("dataset_not_found") from None
    try:
        pinned = pinned_role_for_purpose(dataset.purpose)
    except ManifestDomainError:
        raise SplitManifestError("source_not_splittable") from None
    try:
        quality = load_resolved_quality_report(
            session, context, project_id=project_id, dataset_id=dataset.id, include_held_out=True
        )
        assert_clear_for_split(quality)
        latest = get_latest_report(
            session, context, project_id=project_id, dataset_id=dataset.id, include_held_out=True
        )
    except (QualityReportNotFoundError, QualityBlockedError):
        raise SplitManifestError("quality_not_clear") from None
    excluded = excluded_batch_positions(
        session, context, project_id=project_id, dataset_id=dataset.id, include_held_out=True
    )
    upload = (
        execute_tenant_scoped(
            session,
            context.organization_id,
            select(DatasetUploadSession).where(
                DatasetUploadSession.id == dataset.upload_session_id
            ),
        )
        .scalars()
        .first()
    )
    if upload is None:
        raise SplitManifestError("dataset_not_found")
    positions: set[int] = set()
    kept: list[SourceRecord] = []
    for record in source.records:
        if (
            not isinstance(record, SourceRecord)
            or type(record.batch_position) is not int
            or record.batch_position < 0
            or record.batch_position in positions
        ):
            raise SplitManifestError("invalid_request")
        if type(record.group_keys) is not tuple or not all(
            type(key) is str and len(key) <= _MAX_KEY and pg_safe(key) for key in record.group_keys
        ):
            raise SplitManifestError("invalid_request")
        positions.add(record.batch_position)
        if record.batch_position not in excluded:
            kept.append(record)
    if not kept:
        raise SplitManifestError("no_records")
    return _PreparedSource(
        dataset=dataset,
        pinned_role=pinned,
        connection_id=upload.dataset_source_connection_id,
        quality_report_id=latest.id,
        quality_report_sha256=latest.report_sha256,
        excluded_positions=excluded,
        records=tuple(sorted(kept, key=lambda r: r.batch_position)),
    )


def _authorize_roles(
    session: Session,
    context: TenantContext,
    project_id: uuid.UUID,
    prepared: _PreparedSource,
    roles: set[str],
) -> tuple[str, ...]:
    """Every role the source's records landed in must be a purpose its connection grants. Returns
    the connection's allowed purposes (the frozen snapshot)."""
    snapshot: tuple[str, ...] = ()
    for role in sorted(roles):
        try:
            connection = authorize_dataset_source_purpose(
                session, context, project_id, prepared.connection_id, ROLE_PURPOSE[role]
            )
        except (DatasetSourceAuthorizationError, DatasetSourceConnectionNotFoundError):
            raise SplitManifestError("purpose_not_authorized") from None
        snapshot = tuple(sorted(set(connection.allowed_purposes_json)))
    return snapshot


def _check_prior_sources(
    session: Session,
    context: TenantContext,
    project_id: uuid.UUID,
    prior_sources: Sequence[PriorQualificationSource],
) -> None:
    """The caller must supply every locked manifest's development / final suite, matching the
    stored record ids and hashes exactly, and nothing else."""
    if not isinstance(prior_sources, tuple | list) or not all(
        isinstance(p, PriorQualificationSource) for p in prior_sources
    ):
        raise SplitManifestError("invalid_request")
    locked = list(
        execute_tenant_scoped(
            session,
            context.organization_id,
            select(DatasetSplitManifest).where(
                DatasetSplitManifest.project_id == project_id,
                DatasetSplitManifest.status == "locked",
            ),
        ).scalars()
    )
    expected: dict[tuple[str, str], tuple[str, dict[str, tuple[str, str]]]] = {}
    for manifest in locked:
        suites: dict[str, dict[str, tuple[str, str]]] = {role: {} for role in PRIOR_ROLES}
        rows = execute_tenant_scoped(
            session,
            context.organization_id,
            select(DatasetSplitAssignment).where(
                DatasetSplitAssignment.manifest_id == manifest.id,
                DatasetSplitAssignment.role.in_(PRIOR_ROLES),
            ),
        ).scalars()
        for r in rows:
            suites[r.role][r.record_id] = (r.record_sha256, r.normalized_sha256)
        for role in PRIOR_ROLES:
            expected[(manifest.split_manifest_sha256, role)] = (
                f"{manifest.name}@v{manifest.version}:{role}",
                suites[role],
            )
    provided = {(p.manifest_sha256, p.role): p for p in prior_sources}
    if len(provided) != len(prior_sources) or set(provided) != set(expected):
        raise SplitManifestError("prior_sources_incomplete")
    for key, (label, stored) in expected.items():
        source = provided[key]
        supplied = {r.record_id: (r.record_sha256, r.normalized_sha256) for r in source.records}
        if not same(source.label, label) or supplied != stored:
            raise SplitManifestError("prior_sources_incomplete")


@dataclass(frozen=True)
class _Proposal:
    manifest: SplitManifest
    prepared: tuple[_PreparedSource, ...]
    origins: dict[str, tuple[str, int, str, str]]


def _record_id(dataset_id: uuid.UUID, position: int) -> str:
    return f"{dataset_id}:{position}"


def _freeze_proposal(
    session: Session,
    context: TenantContext,
    *,
    project_id: uuid.UUID,
    name: str,
    proposal: _Proposal,
    prior_sources: Sequence[PriorQualificationSource],
    route: EmbeddingRoute,
    normalization_version: str,
    tokenizer_identity: str | None,
    actor: SplitActor,
    migrated_from_legacy_sha256: str | None,
    audit_action: str,
    now: datetime,
) -> DatasetSplitManifest:
    manifest = proposal.manifest
    role_of = {record_id: role for record_id, role, _ in manifest.assignments}
    snapshots: dict[uuid.UUID, tuple[str, ...]] = {}
    for prepared in proposal.prepared:
        roles = {
            role_of[_record_id(prepared.dataset.id, r.batch_position)] for r in prepared.records
        }
        if prepared.pinned_role is not None and roles - {prepared.pinned_role}:
            raise SplitManifestError("source_role_pinned")
        snapshots[prepared.dataset.id] = _authorize_roles(
            session, context, project_id, prepared, roles
        )
    _check_prior_sources(session, context, project_id, prior_sources)
    try:
        report = detect_split_leakage(
            manifest,
            [
                LeakageRecord(
                    record_id=_record_id(p.dataset.id, r.batch_position),
                    content=r.content,
                    record_sha256=r.record_sha256,
                    normalized_sha256=r.normalized_sha256,
                    group_keys=r.group_keys,
                )
                for p in proposal.prepared
                for r in p.records
            ],
            prior_sources=tuple(prior_sources),
            route=route,
        )
    except LeakageEngineError:
        raise SplitManifestError("invalid_request") from None
    try:
        assignment_rows = build_assignment_rows(manifest, proposal.origins)
        role_rows = build_role_rows(assignment_rows)
    except ManifestDomainError:
        raise SplitManifestError("invalid_request") from None
    body = report_body(report)

    latest = (
        execute_tenant_scoped(
            session,
            context.organization_id,
            select(DatasetSplitManifest)
            .where(DatasetSplitManifest.project_id == project_id, DatasetSplitManifest.name == name)
            .order_by(DatasetSplitManifest.version.desc())
            .limit(1)
            .with_for_update(),
        )
        .scalars()
        .first()
    )
    version = 1 if latest is None else latest.version + 1
    sources = tuple(
        sorted(
            (
                SourceGrant(
                    dataset_id=str(p.dataset.id),
                    dataset_version=p.dataset.version,
                    dataset_purpose=p.dataset.purpose,
                    dataset_sha256=p.dataset.verified_sha256,
                    dataset_source_connection_id=str(p.connection_id),
                    allowed_purposes_snapshot=snapshots[p.dataset.id],
                    quality_report_id=str(p.quality_report_id),
                    quality_report_sha256=p.quality_report_sha256,
                    excluded_positions_sha256=excluded_positions_sha256(p.excluded_positions),
                )
                for p in proposal.prepared
            ),
            key=lambda s: s.dataset_id,
        )
    )

    def content_for(number: int) -> ManifestContent:
        return ManifestContent(
            project_id=str(project_id),
            name=name,
            version=number,
            parent_version=None if number == 1 else number - 1,
            strategy=manifest.strategy,
            seed=manifest.seed,
            ratios=manifest.ratios,
            cutoff=manifest.cutoff,
            normalization_version=normalization_version,
            tokenizer_identity=tokenizer_identity,
            split_manifest_sha256=manifest.manifest_sha256,
            leakage_report=body,
            leakage_report_sha256=report.report_sha256,
            leakage_status=report.status,
            roles=role_rows,
            sources=sources,
            migrated_from_legacy_sha256=migrated_from_legacy_sha256,
        )

    if latest is not None and same(
        latest.proposal_sha256, compute_proposal_sha256(content_for(latest.version))
    ):
        return latest
    content = content_for(version)

    row = DatasetSplitManifest(
        id=uuid.uuid4(),
        organization_id=context.organization_id,
        project_id=project_id,
        name=name,
        version=version,
        parent_version=content.parent_version,
        strategy=manifest.strategy,
        seed=manifest.seed,
        ratios=list(manifest.ratios),
        cutoff=manifest.cutoff,
        normalization_version=normalization_version,
        tokenizer_identity=tokenizer_identity,
        split_manifest_sha256=manifest.manifest_sha256,
        proposal_sha256=compute_proposal_sha256(content),
        leakage_report=body,
        leakage_report_sha256=report.report_sha256,
        leakage_status=report.status,
        leakage_reviews=[],
        reviewed_leakage_report_sha256=None,
        migrated_from_legacy_sha256=migrated_from_legacy_sha256,
        status="proposed",
        frozen_manifest_sha256=None,
        approved_by=None,
        locked_at=None,
        created_by=actor.actor_id,
        created_at=now,
        updated_at=now,
    )
    try:
        with session.begin_nested():
            session.add(row)
            session.flush()
    except IntegrityError:
        raise SplitManifestError("version_conflict") from None
    for role_row in role_rows:
        session.add(
            DatasetSplitManifestRole(
                id=uuid.uuid4(),
                organization_id=context.organization_id,
                manifest_id=row.id,
                role=role_row.role,
                purpose=role_row.purpose,
                record_count=role_row.record_count,
                group_count=role_row.group_count,
                role_sha256=role_row.role_sha256,
            )
        )
    for grant, prepared in zip(
        sources,
        sorted(proposal.prepared, key=lambda p: str(p.dataset.id)),
        strict=True,
    ):
        session.add(
            DatasetSplitManifestSource(
                id=uuid.uuid4(),
                organization_id=context.organization_id,
                manifest_id=row.id,
                project_id=project_id,
                dataset_id=prepared.dataset.id,
                dataset_name=prepared.dataset.name,
                dataset_purpose=prepared.dataset.purpose,
                dataset_version=prepared.dataset.version,
                dataset_sha256=prepared.dataset.verified_sha256,
                dataset_source_connection_id=prepared.connection_id,
                allowed_purposes_snapshot=list(grant.allowed_purposes_snapshot),
                quality_report_id=prepared.quality_report_id,
                quality_report_sha256=prepared.quality_report_sha256,
                excluded_positions_sha256=grant.excluded_positions_sha256,
                created_at=now,
            )
        )
    session.flush()
    for start in range(0, len(assignment_rows), _CHUNK):
        for assignment in assignment_rows[start : start + _CHUNK]:
            session.add(
                DatasetSplitAssignment(
                    manifest_id=row.id,
                    record_id=assignment.record_id,
                    organization_id=context.organization_id,
                    source_dataset_id=uuid.UUID(assignment.source_dataset_id),
                    batch_position=assignment.batch_position,
                    role=assignment.role,
                    group_id=assignment.group_id,
                    record_sha256=assignment.record_sha256,
                    normalized_sha256=assignment.normalized_sha256,
                )
            )
        session.flush()
    _audit(
        session,
        context,
        actor,
        audit_action,
        row,
        {
            "source_count": len(sources),
            "role_counts": {r.role: r.record_count for r in role_rows},
            "finding_count": len(report.findings),
        },
    )
    session.flush()
    return row


def _validate_request(
    actor: object,
    project_id: object,
    name: object,
    normalization_version: object,
    tokenizer_identity: object,
    sources: object,
) -> None:
    """Every check that needs no database, in one place and before any query."""
    _require_actor(actor)
    if not isinstance(project_id, uuid.UUID):
        raise SplitManifestError("invalid_request")
    _clean(name, 200)
    _clean(normalization_version, 64)
    if tokenizer_identity is not None:
        _clean(tokenizer_identity, 200)
    if not isinstance(sources, tuple | list) or not sources:
        raise SplitManifestError("invalid_request")
    ids = [s.dataset_id for s in sources if isinstance(s, SplitSourceInput)]
    if len(ids) != len(sources) or len(set(ids)) != len(ids):
        raise SplitManifestError("invalid_request")
    if sum(len(s.records) for s in sources if type(s.records) is tuple) > MAX_MANIFEST_RECORDS:
        raise SplitManifestError("manifest_too_large")


def propose_split_manifest(
    session: Session,
    context: TenantContext,
    *,
    project_id: uuid.UUID,
    name: str,
    spec: SplitSpec,
    sources: Sequence[SplitSourceInput],
    prior_sources: Sequence[PriorQualificationSource],
    route: EmbeddingRoute,
    normalization_version: str,
    tokenizer_identity: str | None,
    actor: SplitActor,
    now: datetime | None = None,
) -> DatasetSplitManifest:
    """Builds and stores a `proposed` manifest. Raises `SplitManifestError`; on any raise nothing
    was written. Re-proposing an unchanged manifest returns the latest version untouched."""
    if is_legacy_three_split(spec):
        raise SplitManifestError("legacy_manifest_requires_migration")
    if not isinstance(spec, SplitSpec):
        raise SplitManifestError("invalid_request")
    _clean(spec.seed, 128)
    _validate_request(actor, project_id, name, normalization_version, tokenizer_identity, sources)
    prepared = tuple(
        _prepare_source(session, context, project_id, s)
        for s in sorted(sources, key=lambda s: str(s.dataset_id))
    )
    split_records: list[SplitRecord] = []
    origins: dict[str, tuple[str, int, str, str]] = {}
    for source in prepared:
        for record in source.records:
            record_id = _record_id(source.dataset.id, record.batch_position)
            split_records.append(
                SplitRecord(
                    record_id=record_id,
                    group_keys=record.group_keys or (record_id,),
                    preassigned_role=source.pinned_role,
                    event_time=record.event_time,
                )
            )
            origins[record_id] = (
                str(source.dataset.id),
                record.batch_position,
                record.record_sha256,
                record.normalized_sha256,
            )
    try:
        manifest = allocate_splits(spec, split_records)
    except SplitEngineError:
        raise SplitManifestError("invalid_request") from None
    return _freeze_proposal(
        session,
        context,
        project_id=project_id,
        name=name,
        proposal=_Proposal(manifest, prepared, origins),
        prior_sources=prior_sources,
        route=route,
        normalization_version=normalization_version,
        tokenizer_identity=tokenizer_identity,
        actor=actor,
        migrated_from_legacy_sha256=None,
        audit_action="dataset_split_manifest.proposed",
        now=now or _now(),
    )


def migrate_legacy_split_manifest(
    session: Session,
    context: TenantContext,
    *,
    project_id: uuid.UUID,
    name: str,
    legacy: LegacyThreeSplitManifest,
    sources: Sequence[SplitSourceInput],
    development_dataset_id: uuid.UUID | None,
    prior_sources: Sequence[PriorQualificationSource],
    route: EmbeddingRoute,
    normalization_version: str,
    tokenizer_identity: str | None,
    actor: SplitActor,
    now: datetime | None = None,
) -> DatasetSplitManifest:
    """The explicit path for a three-split manifest. `heldout_test` becomes
    `final_qualification` only. A development role exists only from `development_dataset_id`
    (an explicit `candidate_development` dataset among `sources`); without one the stored manifest
    is `insufficient` and the database refuses to lock it."""
    _validate_request(actor, project_id, name, normalization_version, tokenizer_identity, sources)
    if development_dataset_id is not None and development_dataset_id not in [
        s.dataset_id for s in sources
    ]:
        raise SplitManifestError("invalid_request")
    prepared = tuple(
        _prepare_source(session, context, project_id, s)
        for s in sorted(sources, key=lambda s: str(s.dataset_id))
    )
    origins: dict[str, tuple[str, int, str, str]] = {}
    development: list[tuple[str, str]] = []
    for source in prepared:
        is_development = source.dataset.id == development_dataset_id
        if is_development and not same(source.pinned_role, "development_eval"):
            raise SplitManifestError("invalid_request")
        for record in source.records:
            record_id = _record_id(source.dataset.id, record.batch_position)
            origins[record_id] = (
                str(source.dataset.id),
                record.batch_position,
                record.record_sha256,
                record.normalized_sha256,
            )
            if is_development:
                development.append((record_id, min(record.group_keys or (record_id,))))
    try:
        manifest = migrate_legacy_three_split(legacy, development)
    except ManifestDomainError:
        raise SplitManifestError("invalid_request") from None
    placed = {record_id for record_id, _, _ in manifest.assignments}
    for record_id in set(origins) - placed:
        origins.pop(record_id)
    # A source none of whose records the legacy manifest places contributes no grant.
    used = tuple(
        replace(
            p,
            records=tuple(
                r for r in p.records if _record_id(p.dataset.id, r.batch_position) in placed
            ),
        )
        for p in prepared
        if any(_record_id(p.dataset.id, r.batch_position) in placed for r in p.records)
    )
    return _freeze_proposal(
        session,
        context,
        project_id=project_id,
        name=name,
        proposal=_Proposal(manifest, used, origins),
        prior_sources=prior_sources,
        route=route,
        normalization_version=normalization_version,
        tokenizer_identity=tokenizer_identity,
        actor=actor,
        migrated_from_legacy_sha256=legacy.legacy_sha256,
        audit_action="dataset_split_manifest.legacy_migrated",
        now=now or _now(),
    )


# ------------------------------------------------------------------ approve / reject


def _assignments_match_manifest(
    session: Session, context: TenantContext, manifest: DatasetSplitManifest
) -> None:
    """Rebuilds the FTF-0517 manifest from the stored assignments and verifies it."""
    rows = execute_tenant_scoped(
        session,
        context.organization_id,
        select(
            DatasetSplitAssignment.record_id,
            DatasetSplitAssignment.role,
            DatasetSplitAssignment.group_id,
        )
        .where(DatasetSplitAssignment.manifest_id == manifest.id)
        .order_by(DatasetSplitAssignment.record_id),
    ).all()
    assignments = tuple((r.record_id, r.role, r.group_id) for r in rows)
    ordered = tuple(sorted(assignments))
    tally: dict[str, tuple[int, set[str]]] = {role: (0, set()) for role in ROLE_PURPOSE}
    for _, role, group_id in ordered:
        count, groups = tally[role]
        tally[role] = (count + 1, groups | {group_id})
    counts = tuple((role, tally[role][0], len(tally[role][1])) for role in ROLE_PURPOSE)
    rebuilt = SplitManifest(
        strategy=manifest.strategy,
        seed=manifest.seed,
        ratios=tuple(manifest.ratios),  # type: ignore[arg-type]
        cutoff=manifest.cutoff,
        assignments=ordered,
        counts=counts,
        status="insufficient" if any(c == 0 for _, c, _ in counts) else "ready",
        manifest_sha256=manifest.split_manifest_sha256,
    )
    try:
        verify_manifest(rebuilt)
    except SplitEngineError:
        raise SplitManifestError("manifest_tampered") from None


def approve_split_manifest(
    session: Session,
    context: TenantContext,
    *,
    project_id: uuid.UUID,
    manifest_id: uuid.UUID,
    expected_proposal_sha256: str,
    reviews: Sequence[ReviewInput],
    actor: SplitActor,
    now: datetime | None = None,
) -> DatasetSplitManifest:
    """Locks a proposed manifest. The reviewer is `actor`, never input. Raises
    `manifest_changed` for a stale expected hash, `leakage_not_clear` while any finding is open,
    `manifest_insufficient` when a role is empty. On any raise nothing was written."""
    _require_actor(actor)
    manifest = _fetch_manifest(session, context, project_id, manifest_id, for_update=True)
    if not same(manifest.status, "proposed"):
        raise SplitManifestError("manifest_not_proposed")
    if not same(expected_proposal_sha256, manifest.proposal_sha256):
        raise SplitManifestError("manifest_changed")
    roles = _fetch_roles(session, context, manifest.id)
    sources = _fetch_sources(session, context, manifest.id)
    content = _content_from_rows(manifest, roles, sources)
    try:
        if not same(compute_proposal_sha256(content), manifest.proposal_sha256):
            raise SplitManifestError("manifest_tampered")
        base = report_from_body(content.leakage_report)
        if not same(compute_report_sha256(base), manifest.leakage_report_sha256):
            raise SplitManifestError("manifest_tampered")
    except ManifestDomainError:
        raise SplitManifestError("manifest_tampered") from None
    if not isinstance(reviews, tuple | list):
        raise SplitManifestError("invalid_review")
    built: list[LeakageReview] = []
    for review in reviews:
        if (
            not isinstance(review, ReviewInput)
            or type(review.rationale) is not str
            or not pg_safe(review.rationale)
        ):
            raise SplitManifestError("invalid_review")
        built.append(
            LeakageReview(
                finding_id=review.finding_id,
                reviewer_id=actor.actor_id,
                decision="not_leakage",
                rationale=review.rationale,
            )
        )
    try:
        reviewed = apply_leakage_reviews(base, built)
    except LeakageEngineError:
        raise SplitManifestError("invalid_review") from None
    try:
        assert_leakage_clear(reviewed, "final_freeze")
    except LeakageBlockedError:
        raise SplitManifestError("leakage_not_clear") from None
    if any(r.record_count < 1 for r in content.roles):
        raise SplitManifestError("manifest_insufficient")
    _assignments_match_manifest(session, context, manifest)
    locked_at = now or _now()
    locked_content = ManifestContent(
        **{
            **content.__dict__,
            "leakage_reviews": tuple(
                {
                    "finding_id": r.finding_id,
                    "reviewer_id": r.reviewer_id,
                    "decision": r.decision,
                    "rationale": r.rationale,
                }
                for r in reviewed.reviews
            ),
            "reviewed_leakage_report_sha256": reviewed.report_sha256,
            "approved_by": actor.actor_id,
        }
    )
    manifest.leakage_reviews = [dict(r) for r in locked_content.leakage_reviews]
    manifest.reviewed_leakage_report_sha256 = reviewed.report_sha256
    manifest.approved_by = actor.actor_id
    manifest.frozen_manifest_sha256 = compute_frozen_manifest_sha256(locked_content)
    manifest.locked_at = locked_at
    manifest.updated_at = locked_at
    manifest.status = "locked"
    try:
        session.flush()
    except IntegrityError:
        raise SplitManifestError("manifest_insufficient") from None
    _audit(
        session,
        context,
        actor,
        "dataset_split_manifest.locked",
        manifest,
        {
            "frozen_manifest_sha256": manifest.frozen_manifest_sha256,
            "review_count": len(reviewed.reviews),
        },
    )
    session.flush()
    return manifest


def reject_split_manifest(
    session: Session,
    context: TenantContext,
    *,
    project_id: uuid.UUID,
    manifest_id: uuid.UUID,
    actor: SplitActor,
    now: datetime | None = None,
) -> DatasetSplitManifest:
    """proposed -> rejected. Nothing else changes; the next proposal is a new version."""
    _require_actor(actor)
    manifest = _fetch_manifest(session, context, project_id, manifest_id, for_update=True)
    if not same(manifest.status, "proposed"):
        raise SplitManifestError("manifest_not_proposed")
    manifest.status = "rejected"
    manifest.updated_at = now or _now()
    session.flush()
    _audit(session, context, actor, "dataset_split_manifest.rejected", manifest)
    session.flush()
    return manifest


# ------------------------------------------------------------------ training access


def _verified_locked(
    session: Session,
    context: TenantContext,
    project_id: uuid.UUID,
    manifest_id: uuid.UUID,
    *,
    stage: str,
) -> tuple[DatasetSplitManifest, ManifestContent, tuple[DatasetSplitManifestSource, ...]]:
    manifest = _fetch_manifest(session, context, project_id, manifest_id)
    if not same(manifest.status, "locked") or manifest.frozen_manifest_sha256 is None:
        raise SplitManifestError("manifest_not_locked")
    roles = _fetch_roles(session, context, manifest.id)
    sources = _fetch_sources(session, context, manifest.id)
    content = _content_from_rows(manifest, roles, sources)
    try:
        verify_frozen_content(content, manifest.frozen_manifest_sha256)
        base = report_from_body(content.leakage_report)
        reviews = [
            LeakageReview(
                finding_id=str(r.get("finding_id")),
                reviewer_id=str(r.get("reviewer_id")),
                decision=str(r.get("decision")),
                rationale=str(r.get("rationale")),
            )
            for r in content.leakage_reviews
        ]
        reviewed = apply_leakage_reviews(base, reviews)
    except (ManifestDomainError, LeakageEngineError):
        raise SplitManifestError("manifest_tampered") from None
    if not same(reviewed.report_sha256, content.reviewed_leakage_report_sha256):
        raise SplitManifestError("manifest_tampered")
    try:
        assert_leakage_clear(reviewed, stage)
    except LeakageBlockedError:
        raise SplitManifestError("leakage_not_clear") from None
    # The stored assignments must still be exactly what was frozen, role by role.
    counts = dict(
        execute_tenant_scoped(
            session,
            context.organization_id,
            select(DatasetSplitAssignment.role, func.count())
            .where(DatasetSplitAssignment.manifest_id == manifest.id)
            .group_by(DatasetSplitAssignment.role),
        ).all()
    )
    for row in content.roles:
        if counts.get(row.role, 0) != row.record_count:
            raise SplitManifestError("manifest_tampered")
    return manifest, content, sources


def _recheck_sources(
    session: Session,
    context: TenantContext,
    project_id: uuid.UUID,
    manifest: DatasetSplitManifest,
    sources: Sequence[DatasetSplitManifestSource],
    served_roles: tuple[str, ...],
    *,
    training: bool,
) -> None:
    served = execute_tenant_scoped(
        session,
        context.organization_id,
        select(DatasetSplitAssignment.source_dataset_id, DatasetSplitAssignment.role)
        .where(
            DatasetSplitAssignment.manifest_id == manifest.id,
            DatasetSplitAssignment.role.in_(served_roles),
        )
        .distinct(),
    ).all()
    roles_by_source: dict[uuid.UUID, set[str]] = {}
    for dataset_id, role in served:
        roles_by_source.setdefault(dataset_id, set()).add(role)
    for source in sources:
        try:
            latest = get_latest_report(
                session,
                context,
                project_id=project_id,
                dataset_id=source.dataset_id,
                include_held_out=True,
            )
            excluded = excluded_batch_positions(
                session,
                context,
                project_id=project_id,
                dataset_id=source.dataset_id,
                include_held_out=True,
            )
        except (QualityReportNotFoundError, DatasetNotFoundError):
            raise SplitManifestError("quality_report_changed") from None
        if training:
            try:
                assert_clear_for_training(
                    load_resolved_quality_report(
                        session,
                        context,
                        project_id=project_id,
                        dataset_id=source.dataset_id,
                        include_held_out=True,
                    )
                )
            except QualityBlockedError:
                raise SplitManifestError("quality_not_clear") from None
        if (
            latest.id != source.quality_report_id
            or not same(latest.report_sha256, source.quality_report_sha256)
            or not same(excluded_positions_sha256(excluded), source.excluded_positions_sha256)
        ):
            raise SplitManifestError("quality_report_changed")
        for role in sorted(roles_by_source.get(source.dataset_id, ())):
            try:
                authorize_dataset_source_purpose(
                    session,
                    context,
                    project_id,
                    source.dataset_source_connection_id,
                    ROLE_PURPOSE[role],
                )
            except (DatasetSourceAuthorizationError, DatasetSourceConnectionNotFoundError):
                raise SplitManifestError("grant_changed") from None


def _read_rows(
    session: Session,
    context: TenantContext,
    manifest: DatasetSplitManifest,
    roles: tuple[str, ...],
) -> tuple[AssignmentRow, ...]:
    rows = execute_tenant_scoped(
        session,
        context.organization_id,
        select(DatasetSplitAssignment)
        .where(
            and_(DatasetSplitAssignment.manifest_id == manifest.id),
            DatasetSplitAssignment.role.in_(roles),
        )
        .order_by(DatasetSplitAssignment.record_id),
    ).scalars()
    try:
        return tuple(
            AssignmentRow(
                record_id=r.record_id,
                source_dataset_id=str(r.source_dataset_id),
                batch_position=r.batch_position,
                role=r.role,
                group_id=r.group_id,
                record_sha256=r.record_sha256,
                normalized_sha256=r.normalized_sha256,
            )
            for r in rows
        )
    except ManifestDomainError:
        raise SplitManifestError("manifest_tampered") from None


def _check_served_hashes(
    content: ManifestContent, rows: Sequence[AssignmentRow], roles: tuple[str, ...]
) -> None:
    frozen_by_role = {r.role: r for r in content.roles}
    by_role: dict[str, list[AssignmentRow]] = {role: [] for role in roles}
    for served_row in rows:
        by_role[served_row.role].append(served_row)
    for role in roles:
        frozen = frozen_by_role[role]
        served = by_role[role]
        if len(served) != frozen.record_count or not same(role_sha256(served), frozen.role_sha256):
            raise SplitManifestError("manifest_tampered")


def load_training_split(
    session: Session,
    context: TenantContext,
    *,
    project_id: uuid.UUID,
    manifest_id: uuid.UUID,
) -> TrainingSplitView:
    """The training rows of a locked manifest: `train` and `validation` only. Re-verifies the
    frozen hash, the leakage report for `training`, each source's latest quality report and every
    frozen grant live before serving anything."""
    manifest, content, sources = _verified_locked(
        session, context, project_id, manifest_id, stage="training"
    )
    _recheck_sources(session, context, project_id, manifest, sources, TRAINING_ROLES, training=True)
    try:
        rows = assert_training_rows(_read_rows(session, context, manifest, TRAINING_ROLES))
    except ManifestDomainError:
        raise SplitManifestError("manifest_tampered") from None
    _check_served_hashes(content, rows, TRAINING_ROLES)
    return TrainingSplitView(
        manifest_id=manifest.id,
        version=manifest.version,
        frozen_manifest_sha256=str(manifest.frozen_manifest_sha256),
        rows=rows,
    )


def load_development_split(
    session: Session,
    context: TenantContext,
    *,
    project_id: uuid.UUID,
    manifest_id: uuid.UUID,
) -> TrainingSplitView:
    """The `development_eval` rows of a locked manifest, under the same re-verification. Final
    qualification records are never served here."""
    roles = ("development_eval",)
    manifest, content, sources = _verified_locked(
        session, context, project_id, manifest_id, stage="final_freeze"
    )
    _recheck_sources(session, context, project_id, manifest, sources, roles, training=False)
    try:
        rows = assert_development_rows(_read_rows(session, context, manifest, roles))
    except ManifestDomainError:
        raise SplitManifestError("manifest_tampered") from None
    _check_served_hashes(content, rows, roles)
    return TrainingSplitView(
        manifest_id=manifest.id,
        version=manifest.version,
        frozen_manifest_sha256=str(manifest.frozen_manifest_sha256),
        rows=rows,
    )
