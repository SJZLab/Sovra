"""Scoped review queue, concurrency-controlled responses and drift reconciliation
(FTF-R006-P2, Section 8-10, 65, 67, 77.3-77.6, 78.4, 80.1-80.4, 81).

Backed by `review_queues` / `review_items` / `review_responses`
(`apps/api/alembic/versions/0036_review_queues.py`) and the P1b scope / reviewer-identity tables
(`argilla_review_scopes`, `argilla_reviewer_identities`, `argilla_workspace_members`). Argilla is
reached only through `packages/integrations/argilla/review_mapping.py` (P1a's guarded transport);
this module never imports an HTTP library.

Three operations:

- `create_review_queue` -- one queue from one `datasets` version. In order, each refusal writing
  nothing, to the database or to Argilla: `dataset_not_found`; `sealed_purpose_refused` (source
  purpose `held_out_evaluation` / `baseline_only` -- the final suite is never queued for ordinary
  review); `grant_changed` (the source connection no longer authorizes the dataset's purpose,
  re-checked live through FTF-0500, never trusted from creation time); `dataset_already_split`
  (the dataset is a source of a locked FTF-0519 manifest, so review would mutate a frozen
  split); `quality_not_clear` (`assert_clear_for_split` on the resolved quality report); then the
  source bytes are read through FTF-0502-P1 `open_dataset_source` (sha verified end to end) and
  each accepted row is normalized by the dispatched importer (FTF-0506 underneath), the report's
  `excluded_batch_positions` are dropped, and `queue_too_large` (> 100,000, never truncated) /
  `record_too_large_for_review` / `no_records` refuse before anything is written. Only then are
  the queue and its items inserted (status `building`) and pushed. The push is idempotent: the
  Argilla dataset is found by its deterministic name, records already present are found by
  `external_id` / `record_id` metadata before any create, so replaying after a crash or an outage
  completes the same queue rather than duplicating it. Argilla down -> `argilla_unavailable`, the
  queue stays `building` (visible, Law 17), never `ready`.
- `submit_review_response` -- a reviewer's decision, written to Argilla only with the reviewer's
  own API key (resolved from OpenBao by the identity's `credential_secret_ref`), so the Argilla
  `user_id` of the stored response is the mapped annotator. `expected_version` is checked under
  `SELECT ... FOR UPDATE` on the item row (every write of that item's responses goes through
  it, so the response row needs no lock of its own): a stale version is
  `review_version_conflict` and nothing is sent to Argilla. After the write the record is read
  back with the provisioning key; the response must be the mapped user's, submitted, and equal to
  what was sent, and its sha256 is stored. Argilla down -> `argilla_unavailable` with the Factory
  row unchanged (Section 80.3). One content-free audit row per response, actor = the Factory
  principal, written in the same transaction.
- `reconcile_review_queue` -- authenticated polling (never webhooks): every record and response
  is listed with the provisioning key and compared with the Factory's rows. An Argilla-side edit
  of a record, a missing record, a response by a user that is not a mapped annotator (the owner
  and admins are never mapped), a response the Factory did not record, a changed or discarded
  response, or a Factory response missing in Argilla marks the item `drift`. Drift is sticky (a
  database trigger refuses any move out of it) and blocks export (FTF-R006-P3).

Roles are not decided here: the route layer (FTF-R006-P4) maps `DATASET_SOURCE_ROLES` to the
authenticated principal before calling in; `actor_id`, `issuer` and `subject` come from that
principal, never from a request body.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import uuid
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from packages.integrations.argilla.client import (
    ArgillaClientError,
    ArgillaConflictError,
    ArgillaNotFoundError,
    ArgillaRejectedError,
    ArgillaUnavailableError,
)
from packages.integrations.argilla.review_mapping import (
    DECISIONS,
    FIELD_NAME,
    MAX_RATIONALE_CHARS,
    MAX_RECORDS,
    SCHEMA_VERSION,
    ArgillaReviewApi,
    ArgillaReviewRecord,
    ReviewMappingError,
    dataset_name_for,
    fields_sha256,
    response_sha256,
    review_record_payload,
)
from packages.litellm_client.secret_manager_integration import OpenBaoConfig, resolve_secret_ref
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from workers.data.dataset_mapping_review import _EVENTS_BY_CONTENT_TYPE
from workers.data.immutable_split_manifest import excluded_positions_sha256
from workers.data.import_common import (
    DEFAULT_LIMITS,
    IMPORTER_RECORD_ID_NAMESPACE,
    AcceptedRecord,
    BlankLine,
    ChunkReader,
    ImportAborted,
    ImportMapping,
    RowMappingError,
    RowRejection,
    apply_mapping,
)
from workers.data.quality_issue_engine import QualityBlockedError, assert_clear_for_split

from app.auth.audit_event_writer import write_audit_event
from app.auth.organization_workspace_schema import set_tenant_context
from app.auth.tenant_isolation_query_guards import execute_tenant_scoped, tenant_scoped_session
from app.datasets.dataset_mapping_service import _live_tool_schemas
from app.datasets.dataset_object_source import (
    DatasetObjectStorage,
    DatasetSourceInconsistentError,
    open_dataset_source,
)
from app.datasets.dataset_quality_store import (
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
    DatasetNotFoundError,
    UploadSessionNotFoundError,
    get_dataset,
    get_upload_session,
)
from app.db.tenant_context import TenantContext
from app.services.json_schema_tool_validation import validate_instance

__all__ = [
    "CODES",
    "MAX_QUEUE_RECORDS",
    "QUEUE_PURPOSES",
    "SEALED_PURPOSES",
    "DriftFinding",
    "ReviewQueueError",
    "ReviewQueueRecord",
    "ReviewResponseRecord",
    "create_review_queue",
    "get_review_queue",
    "reconcile_review_queue",
    "submit_review_response",
]

MAX_QUEUE_RECORDS = MAX_RECORDS
# Total rendered conversation text one queue build may hold in memory while it is pushed.
MAX_QUEUE_TEXT_CHARS = 128 * 1024 * 1024
SEALED_PURPOSES = frozenset({"held_out_evaluation", "baseline_only"})
# Review is an allowlist, not a denylist: any purpose that is not one of these three (the two
# sealed ones, a misspelling, a case variant) is refused as sealed.
QUEUE_PURPOSES = frozenset({"training", "validation", "candidate_development"})
# ImportAborted failure code -> queue error code; every other abort is an unreadable source.
_ABORT_CODES = {"source_changed": "source_changed"}
_RESOURCE = "review_queue"
_RESPONSE_RESOURCE = "review_response"

CODES = frozenset(
    {
        "invalid_request",
        "dataset_not_found",
        "sealed_purpose_refused",
        "grant_changed",
        "dataset_already_split",
        "quality_not_clear",
        "queue_too_large",
        "record_too_large_for_review",
        "no_records",
        "source_unreadable",
        "source_changed",
        "record_not_renderable",
        "scope_not_found",
        "queue_source_changed",
        "queue_not_found",
        "queue_not_ready",
        "review_item_not_found",
        "review_item_drift",
        "reviewer_not_authorized",
        "review_version_conflict",
        "response_author_mismatch",
        "argilla_unavailable",
        "argilla_rejected",
    }
)


class ReviewQueueError(Exception):
    """A refusal or failure of this module. `code` is one of `CODES`; the message is the code
    alone (never record content, an API key or an Argilla body)."""

    def __init__(self, code: str) -> None:
        if code not in CODES:
            raise ValueError("unknown review queue error code")
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class ReviewQueueRecord:
    id: uuid.UUID
    organization_id: uuid.UUID
    project_id: uuid.UUID
    dataset_id: uuid.UUID
    scope_id: uuid.UUID
    argilla_dataset_id: str | None
    argilla_dataset_name: str
    quality_report_id: uuid.UUID
    quality_report_sha256: str
    excluded_positions_sha256: str
    queue_snapshot_sha256: str
    record_count: int
    schema_version: str
    status: str


@dataclass(frozen=True)
class ReviewResponseRecord:
    id: uuid.UUID
    queue_id: uuid.UUID
    item_id: uuid.UUID
    identity_id: uuid.UUID
    decision: str
    rationale: str
    response_sha256: str
    argilla_response_id: str
    row_version: int


@dataclass(frozen=True)
class DriftFinding:
    item_id: uuid.UUID | None
    record_id: str | None
    reason: str


@dataclass(frozen=True)
class _QueueItem:
    record_id: str
    batch_position: int
    group_id: str
    record_sha256: str
    fields_sha256: str
    payload: Mapping[str, object]


_QUEUE_COLUMNS = (
    "id, project_id, dataset_id, scope_id, argilla_dataset_id, argilla_dataset_name, "
    "quality_report_id, quality_report_sha256, excluded_positions_sha256, "
    "queue_snapshot_sha256, record_count, schema_version, status"
)


def _is_uuid(value: object) -> bool:
    return isinstance(value, uuid.UUID)


def _clean_text(value: object, limit: int) -> str:
    if (
        type(value) is not str
        or not value
        or len(value) > limit
        or "\x00" in value
        or value != value.strip()
    ):
        raise ReviewQueueError("invalid_request")
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        raise ReviewQueueError("invalid_request") from None
    return value


def _canonical_sha(body: object) -> str:
    raw = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(raw.encode("ascii")).hexdigest()


def _queue_of(organization_id: uuid.UUID, row: Any) -> ReviewQueueRecord:
    return ReviewQueueRecord(
        id=row[0],
        organization_id=organization_id,
        project_id=row[1],
        dataset_id=row[2],
        scope_id=row[3],
        argilla_dataset_id=row[4],
        argilla_dataset_name=row[5],
        quality_report_id=row[6],
        quality_report_sha256=row[7],
        excluded_positions_sha256=row[8],
        queue_snapshot_sha256=row[9],
        record_count=row[10],
        schema_version=row[11],
        status=row[12],
    )


def _argilla_error(exc: ArgillaClientError) -> ReviewQueueError:
    """Law 17: an outage stays an outage; anything the server understood and refused is its own
    code. Neither is ever turned into success, and no server text is carried."""
    if isinstance(exc, ArgillaUnavailableError):
        return ReviewQueueError("argilla_unavailable")
    return ReviewQueueError("argilla_rejected")


# ---------------------------------------------------------------------------------- reads


def get_review_queue(
    session_factory: Callable[[], Session], *, context: TenantContext, queue_id: uuid.UUID
) -> ReviewQueueRecord:
    if not _is_uuid(queue_id):
        raise ReviewQueueError("invalid_request")
    organization_id = context.organization_id
    with tenant_scoped_session(session_factory, organization_id) as session:
        row = execute_tenant_scoped(
            session,
            organization_id,
            text(f"SELECT {_QUEUE_COLUMNS} FROM review_queues WHERE id = :id"),
            {"id": queue_id},
        ).first()
    if row is None:
        raise ReviewQueueError("queue_not_found")
    return _queue_of(organization_id, row)


# ----------------------------------------------------------------------- queue creation


def _scope_for_project(
    session: Session, organization_id: uuid.UUID, project_id: uuid.UUID
) -> tuple[uuid.UUID, str]:
    row = execute_tenant_scoped(
        session,
        organization_id,
        text(
            "SELECT id, argilla_workspace_id FROM argilla_review_scopes "
            "WHERE organization_id = :org AND project_id = :project"
        ),
        {"org": organization_id, "project": project_id},
    ).first()
    if row is None:
        raise ReviewQueueError("scope_not_found")
    return row[0], row[1]


def _dataset_is_split_source(
    session: Session, organization_id: uuid.UUID, project_id: uuid.UUID, dataset_id: uuid.UUID
) -> bool:
    row = execute_tenant_scoped(
        session,
        organization_id,
        text(
            "SELECT 1 FROM dataset_split_manifest_sources s "
            "JOIN dataset_split_manifests m ON m.organization_id = s.organization_id "
            "AND m.id = s.manifest_id "
            "WHERE s.organization_id = :org AND s.project_id = :project "
            "AND s.dataset_id = :dataset AND m.status = 'locked' LIMIT 1"
        ),
        {"org": organization_id, "project": project_id, "dataset": dataset_id},
    ).first()
    return row is not None


def _group_id_for(mapping: ImportMapping, dataset_sha256: str, index: int, key: str | None) -> str:
    """The group id FTF-0506 gives the same row: explicit when the mapping names a group key,
    otherwise the singleton group of the importer's deterministic record id."""
    if key is not None:
        return hashlib.sha256(f"group:explicit:{key}".encode()).hexdigest()
    record_id = str(
        uuid.uuid5(
            IMPORTER_RECORD_ID_NAMESPACE,
            f"{dataset_sha256}:{mapping.mapping_sha256}:{index}",
        )
    )
    return hashlib.sha256(f"group:solo:{record_id}".encode()).hexdigest()


def _read_queue_items(
    session: Session,
    context: TenantContext,
    *,
    project_id: uuid.UUID,
    dataset_id: uuid.UUID,
    mapping: ImportMapping,
    storage: DatasetObjectStorage,
    excluded: frozenset[int],
) -> list[_QueueItem]:
    try:
        source = open_dataset_source(
            session,
            context,
            project_id=project_id,
            dataset_id=dataset_id,
            include_held_out=True,
            storage=storage,
        )
    except (DatasetNotFoundError, DatasetSourceInconsistentError, UploadSessionNotFoundError):
        raise ReviewQueueError("source_unreadable") from None
    events_fn = _EVENTS_BY_CONTENT_TYPE.get(source.verified_content_type)
    if events_fn is None:
        raise ReviewQueueError("source_unreadable")
    group_keys: list[str | None] = []

    def observe(row: Any, headers: Any) -> None:
        # The importer calls this once per row reaching `apply_mapping`, before that row's
        # event; the group key is read the same way FTF-0506 reads it.
        try:
            group_keys.append(
                apply_mapping(
                    dataclasses.replace(mapping, row_observer=None), row, headers=headers
                ).group_key
            )
        except RowMappingError:
            group_keys.append(None)

    observed = dataclasses.replace(mapping, row_observer=observe)
    reader = ChunkReader(
        source.chunks,
        expected_size=source.expected_size,
        expected_sha256=source.expected_sha256,
        limits=DEFAULT_LIMITS,
    )
    stream = events_fn(
        reader,
        observed,
        verified_content_type=source.verified_content_type,
        dataset_sha256=source.expected_sha256,
        limits=DEFAULT_LIMITS,
        tool_schemas=_live_tool_schemas(session, context, project_id),
        tool_argument_validator=validate_instance,
    )
    items: list[_QueueItem] = []
    total_chars = 0
    accepted = 0
    try:
        for event in stream:
            if isinstance(event, BlankLine | RowRejection):
                group_keys.clear()
                continue
            assert isinstance(event, AcceptedRecord)  # the importers yield only these three
            key = group_keys.pop() if group_keys else None
            group_keys.clear()
            candidate = event.candidate
            # Batch position (FTF-0511/0516): the ordinal among the accepted candidates handed to
            # the detectors. `record_index` also counts rejected rows, so it is only the
            # importer's own record number (the group id and record id formulas use it).
            position = accepted
            accepted += 1
            source_index = candidate["record_index"]
            assert type(source_index) is int
            if position in excluded:
                continue
            content = candidate["content"]
            assert isinstance(content, Mapping)
            messages = content["messages"]
            record_id = f"{dataset_id}:{position}"
            normalized = candidate["normalized_sha256"]
            assert isinstance(normalized, str)
            group_id = _group_id_for(mapping, source.expected_sha256, source_index, key)
            payload = review_record_payload(
                record_id=record_id,
                group_id=group_id,
                record_sha256=normalized,
                messages=messages,
            )
            rendered = payload["fields"]
            assert isinstance(rendered, dict)
            text_value = rendered[FIELD_NAME]
            total_chars += len(text_value)
            if len(items) >= MAX_QUEUE_RECORDS or total_chars > MAX_QUEUE_TEXT_CHARS:
                raise ReviewQueueError("queue_too_large")
            items.append(
                _QueueItem(
                    record_id=record_id,
                    batch_position=position,
                    group_id=group_id,
                    record_sha256=normalized,
                    fields_sha256=fields_sha256(text_value),
                    payload=payload,
                )
            )
        for _ in reader:  # drain: size and sha256 are verified when the stream ends
            pass
    except ImportAborted as exc:
        raise ReviewQueueError(_ABORT_CODES.get(exc.failure_code, "source_unreadable")) from None
    except ReviewMappingError as exc:
        raise ReviewQueueError(exc.code) from None
    if not items:
        raise ReviewQueueError("no_records")
    return items


def _snapshot_sha256(items: Iterable[_QueueItem]) -> str:
    pairs = sorted((item.record_id, item.record_sha256) for item in items)
    return _canonical_sha({"schema_version": SCHEMA_VERSION, "records": pairs})


def create_review_queue(
    session_factory: Callable[[], Session],
    *,
    context: TenantContext,
    project_id: uuid.UUID,
    dataset_id: uuid.UUID,
    mapping: ImportMapping,
    storage: DatasetObjectStorage,
    owner_api: ArgillaReviewApi,
    actor_type: str,
    actor_id: str,
    request_id: str,
) -> ReviewQueueRecord:
    """Builds (or, on replay, completes) the review queue of one dataset version. See the module
    docstring for the ordered refusals. `owner_api` carries the provisioning key; the Argilla
    workspace comes from the project's FTF-R006-P1b scope row, never from the caller."""
    if (
        not _is_uuid(project_id)
        or not _is_uuid(dataset_id)
        or not isinstance(mapping, ImportMapping)
        or not isinstance(owner_api, ArgillaReviewApi)
    ):
        raise ReviewQueueError("invalid_request")
    actor_type = _clean_text(actor_type, 32)
    actor_id = _clean_text(actor_id, 200)
    request_id = _clean_text(request_id, 128)
    organization_id = context.organization_id

    # -- phase 1: every refusal, read-only ------------------------------------------------
    with tenant_scoped_session(session_factory, organization_id) as session:
        try:
            dataset = get_dataset(
                session,
                context,
                project_id=project_id,
                dataset_id=dataset_id,
                include_held_out=True,
            )
        except DatasetNotFoundError:
            raise ReviewQueueError("dataset_not_found") from None
        if dataset.purpose not in QUEUE_PURPOSES:
            raise ReviewQueueError("sealed_purpose_refused")
        try:
            upload = get_upload_session(session, context, project_id, dataset.upload_session_id)
            authorize_dataset_source_purpose(
                session, context, project_id, upload.dataset_source_connection_id, dataset.purpose
            )
        except (
            DatasetSourceAuthorizationError,
            DatasetSourceConnectionNotFoundError,
            UploadSessionNotFoundError,
        ):
            raise ReviewQueueError("grant_changed") from None
        if _dataset_is_split_source(session, organization_id, project_id, dataset_id):
            raise ReviewQueueError("dataset_already_split")
        try:
            quality = load_resolved_quality_report(
                session,
                context,
                project_id=project_id,
                dataset_id=dataset_id,
                include_held_out=True,
            )
            assert_clear_for_split(quality)
            latest = get_latest_report(
                session,
                context,
                project_id=project_id,
                dataset_id=dataset_id,
                include_held_out=True,
            )
        except (QualityReportNotFoundError, QualityBlockedError):
            raise ReviewQueueError("quality_not_clear") from None
        excluded = excluded_batch_positions(
            session, context, project_id=project_id, dataset_id=dataset_id, include_held_out=True
        )
        scope_id, workspace_id = _scope_for_project(session, organization_id, project_id)
        items = _read_queue_items(
            session,
            context,
            project_id=project_id,
            dataset_id=dataset_id,
            mapping=mapping,
            storage=storage,
            excluded=frozenset(excluded),
        )
        lineage = {
            "dataset_name": dataset.name,
            "dataset_purpose": dataset.purpose,
            "dataset_version": dataset.version,
            "dataset_sha256": dataset.verified_sha256,
            "quality_report_id": latest.id,
            "quality_report_sha256": latest.report_sha256,
            "excluded_positions_sha256": excluded_positions_sha256(excluded),
        }
    snapshot = _snapshot_sha256(items)
    argilla_name = dataset_name_for(organization_id, project_id, dataset_id)

    # -- phase 2: the queue and its items, one transaction --------------------------------
    queue = _insert_queue(
        session_factory,
        organization_id=organization_id,
        project_id=project_id,
        dataset_id=dataset_id,
        scope_id=scope_id,
        argilla_name=argilla_name,
        lineage=lineage,
        snapshot=snapshot,
        items=items,
        actor_type=actor_type,
        actor_id=actor_id,
        request_id=request_id,
    )

    # -- phase 3: push to Argilla (idempotent; outside any database transaction) ----------
    return _push_queue(
        session_factory,
        organization_id=organization_id,
        queue=queue,
        workspace_id=workspace_id,
        items=items,
        owner_api=owner_api,
    )


def _same_lineage(
    record: ReviewQueueRecord, *, snapshot: str, lineage: Mapping[str, Any], scope_id: uuid.UUID
) -> ReviewQueueRecord:
    """A queue already exists for this dataset: it is the same queue only if every hash that
    defined it still holds; otherwise the source changed and a new queue needs a decision."""
    stored = (
        record.queue_snapshot_sha256,
        record.quality_report_sha256,
        record.excluded_positions_sha256,
    )
    wanted = (snapshot, lineage["quality_report_sha256"], lineage["excluded_positions_sha256"])
    if stored != wanted or record.scope_id != scope_id:
        raise ReviewQueueError("queue_source_changed")
    return record


def _existing_queue(
    session: Session, organization_id: uuid.UUID, project_id: uuid.UUID, dataset_id: uuid.UUID
) -> ReviewQueueRecord | None:
    row = execute_tenant_scoped(
        session,
        organization_id,
        text(
            f"SELECT {_QUEUE_COLUMNS} FROM review_queues "
            "WHERE organization_id = :org AND project_id = :project "
            "AND dataset_id = :dataset FOR UPDATE"
        ),
        {"org": organization_id, "project": project_id, "dataset": dataset_id},
    ).first()
    return None if row is None else _queue_of(organization_id, row)


def _insert_queue(
    session_factory: Callable[[], Session],
    *,
    organization_id: uuid.UUID,
    project_id: uuid.UUID,
    dataset_id: uuid.UUID,
    scope_id: uuid.UUID,
    argilla_name: str,
    lineage: Mapping[str, Any],
    snapshot: str,
    items: list[_QueueItem],
    actor_type: str,
    actor_id: str,
    request_id: str,
) -> ReviewQueueRecord:
    with tenant_scoped_session(session_factory, organization_id) as session:
        existing = _existing_queue(session, organization_id, project_id, dataset_id)
        if existing is not None:
            return _same_lineage(existing, snapshot=snapshot, lineage=lineage, scope_id=scope_id)
        try:
            row = execute_tenant_scoped(
                session,
                organization_id,
                text(
                    "INSERT INTO review_queues (organization_id, project_id, dataset_id, "
                    "dataset_name, dataset_purpose, dataset_version, dataset_sha256, "
                    "scope_id, argilla_dataset_name, quality_report_id, "
                    "quality_report_sha256, excluded_positions_sha256, "
                    "queue_snapshot_sha256, record_count, schema_version, created_by) "
                    "VALUES (:org, :project, :dataset, :dataset_name, :dataset_purpose, "
                    ":dataset_version, :dataset_sha256, :scope, :argilla_name, "
                    ":quality_report_id, :quality_report_sha256, "
                    ":excluded_positions_sha256, :snapshot, :count, :schema, :actor) "
                    f"RETURNING {_QUEUE_COLUMNS}"
                ),
                {
                    "org": organization_id,
                    "project": project_id,
                    "dataset": dataset_id,
                    "scope": scope_id,
                    "argilla_name": argilla_name,
                    "snapshot": snapshot,
                    "count": len(items),
                    "schema": SCHEMA_VERSION,
                    "actor": actor_id,
                    **lineage,
                },
            ).one()
            record = _queue_of(organization_id, row)
            execute_tenant_scoped(
                session,
                organization_id,
                text(
                    "INSERT INTO review_items (organization_id, queue_id, record_id, "
                    "batch_position, group_id, record_sha256, fields_sha256) "
                    "VALUES (:org, :queue, :record_id, :position, :group_id, "
                    ":record_sha256, :fields_sha256)"
                ),
                [  # type: ignore[arg-type]  # executemany rows: Session.execute takes a list
                    {
                        "org": organization_id,
                        "queue": record.id,
                        "record_id": item.record_id,
                        "position": item.batch_position,
                        "group_id": item.group_id,
                        "record_sha256": item.record_sha256,
                        "fields_sha256": item.fields_sha256,
                    }
                    for item in items
                ],
            )
            write_audit_event(
                session,
                organization_id=organization_id,
                actor_type=actor_type,
                actor_id=actor_id,
                action="review_queue.created",
                resource_type=_RESOURCE,
                resource_id=str(record.id),
                request_id=request_id,
                details={
                    "project_id": str(project_id),
                    "dataset_id": str(dataset_id),
                    "record_count": len(items),
                    "queue_snapshot_sha256": snapshot,
                },
            )
            session.flush()
        except IntegrityError:
            # A concurrent creator won the unique (org, project, dataset) race: take its queue.
            session.rollback()
            set_tenant_context(session, organization_id)
            winner = _existing_queue(session, organization_id, project_id, dataset_id)
            if winner is None:
                raise
            return _same_lineage(winner, snapshot=snapshot, lineage=lineage, scope_id=scope_id)
        return record


_PUSH_ATTEMPTS = 3


def _push_queue(
    session_factory: Callable[[], Session],
    *,
    organization_id: uuid.UUID,
    queue: ReviewQueueRecord,
    workspace_id: str,
    items: list[_QueueItem],
    owner_api: ArgillaReviewApi,
) -> ReviewQueueRecord:
    by_id = {item.record_id: item for item in items}
    wanted = by_id.keys()
    ids: dict[str | None, str] = {}
    try:
        dataset = owner_api.ensure_dataset(workspace_id, queue.argilla_dataset_name)
        for attempt in range(_PUSH_ATTEMPTS):
            existing = {r.external_id: r for r in owner_api.list_records(dataset.id)}
            if existing.keys() - wanted:
                # Records the Factory never queued: refuse to adopt the dataset.
                raise ReviewQueueError("argilla_rejected")
            for external_id, found in existing.items():
                # A record is adopted only if it is exactly what the Factory would have pushed.
                assert external_id is not None  # `existing` holds only queued ids here
                if _record_reason(_item_view(by_id[external_id]), found) is not None:
                    raise ReviewQueueError("argilla_rejected")
            missing = [by_id[record_id] for record_id in sorted(by_id.keys() - existing.keys())]
            try:
                created = owner_api.create_records(dataset.id, [i.payload for i in missing])
            except ArgillaRejectedError:
                # A concurrent push created some of them first (the pinned server answers a
                # duplicate external_id with 422): list again, verify, create what is left.
                if attempt + 1 == _PUSH_ATTEMPTS:
                    raise
                continue
            ids = {record.external_id: record.id for record in existing.values()}
            ids.update({record.external_id: record.id for record in created})
            break
    except ArgillaClientError as exc:
        raise _argilla_error(exc) from None
    with tenant_scoped_session(session_factory, organization_id) as session:
        if queue.argilla_dataset_id is None:
            execute_tenant_scoped(
                session,
                organization_id,
                text(
                    "UPDATE review_queues SET argilla_dataset_id = :dataset_id, "
                    "updated_at = now() WHERE id = :id AND argilla_dataset_id IS NULL"
                ),
                {"dataset_id": dataset.id, "id": queue.id},
            )
        execute_tenant_scoped(
            session,
            organization_id,
            text(
                "UPDATE review_items SET argilla_record_id = :record, updated_at = now() "
                "WHERE queue_id = :queue AND record_id = :record_id "
                "AND argilla_record_id IS NULL"
            ),
            [  # type: ignore[arg-type]  # executemany rows: Session.execute takes a list
                {"record": ids[item.record_id], "queue": queue.id, "record_id": item.record_id}
                for item in items
            ],
        )
        # `ready` only when no item of the queue is left unlinked (the guard is in the statement).
        row = execute_tenant_scoped(
            session,
            organization_id,
            text(
                "UPDATE review_queues SET status = 'ready', updated_at = now() "
                "WHERE id = :id AND NOT EXISTS (SELECT 1 FROM review_items "
                "WHERE queue_id = :id AND argilla_record_id IS NULL) "
                f"RETURNING {_QUEUE_COLUMNS}"
            ),
            {"id": queue.id},
        ).first()
        if row is None:
            raise ReviewQueueError("argilla_rejected")
        return _queue_of(organization_id, row)


# --------------------------------------------------------------------------- responses


def _identity(
    session: Session, organization_id: uuid.UUID, issuer: str, subject: str
) -> tuple[uuid.UUID, str, str, str] | None:
    row = execute_tenant_scoped(
        session,
        organization_id,
        text(
            "SELECT id, argilla_user_id, credential_secret_ref, status "
            "FROM argilla_reviewer_identities "
            "WHERE organization_id = :org AND issuer = :issuer AND subject = :subject"
        ),
        {"org": organization_id, "issuer": issuer, "subject": subject},
    ).first()
    return None if row is None else (row[0], row[1], row[2], row[3])


def submit_review_response(
    session_factory: Callable[[], Session],
    *,
    context: TenantContext,
    queue_id: uuid.UUID,
    record_id: str,
    issuer: str,
    subject: str,
    decision: str,
    rationale: str,
    expected_version: int,
    owner_api: ArgillaReviewApi,
    reviewer_api_factory: Callable[[str], ArgillaReviewApi],
    openbao_config: OpenBaoConfig,
    request_id: str,
) -> ReviewResponseRecord:
    """Records one reviewer's decision. `expected_version` is 0 for a first response and the
    stored `row_version` for a change; anything else is `review_version_conflict`."""
    if (
        not _is_uuid(queue_id)
        or type(expected_version) is not int
        or expected_version < 0
        or type(decision) is not str
        or decision not in DECISIONS
        or type(rationale) is not str
        or len(rationale) > MAX_RATIONALE_CHARS
        or "\x00" in rationale
        or not isinstance(owner_api, ArgillaReviewApi)
    ):
        raise ReviewQueueError("invalid_request")
    try:
        rationale.encode("utf-8")
    except UnicodeEncodeError:
        raise ReviewQueueError("invalid_request") from None
    record_id = _clean_text(record_id, 300)
    issuer = _clean_text(issuer, 512)
    subject = _clean_text(subject, 255)
    request_id = _clean_text(request_id, 128)
    organization_id = context.organization_id

    # -- authorize and resolve the reviewer's own key ---------------------------------------
    with tenant_scoped_session(session_factory, organization_id) as session:
        queue_row = execute_tenant_scoped(
            session,
            organization_id,
            text(f"SELECT {_QUEUE_COLUMNS} FROM review_queues WHERE id = :id"),
            {"id": queue_id},
        ).first()
        if queue_row is None:
            raise ReviewQueueError("queue_not_found")
        queue = _queue_of(organization_id, queue_row)
        if queue.status != "ready":
            raise ReviewQueueError("queue_not_ready")
        identity = _identity(session, organization_id, issuer, subject)
        if identity is None or identity[3] != "active":
            raise ReviewQueueError("reviewer_not_authorized")
        member = execute_tenant_scoped(
            session,
            organization_id,
            text(
                "SELECT 1 FROM argilla_workspace_members WHERE scope_id = :scope "
                "AND identity_id = :identity AND status = 'active'"
            ),
            {"scope": queue.scope_id, "identity": identity[0]},
        ).first()
        if member is None:
            raise ReviewQueueError("reviewer_not_authorized")
        item_check = execute_tenant_scoped(
            session,
            organization_id,
            text("SELECT 1 FROM review_items WHERE queue_id = :queue AND record_id = :record_id"),
            {"queue": queue_id, "record_id": record_id},
        ).first()
        if item_check is None:
            raise ReviewQueueError("review_item_not_found")
    identity_id, argilla_user_id, secret_ref, _status = identity
    reviewer_api = reviewer_api_factory(resolve_secret_ref(secret_ref, config=openbao_config))

    # -- the write: row locks, version check, Argilla write, read-back, row, audit ----------
    with tenant_scoped_session(session_factory, organization_id) as session:
        item = execute_tenant_scoped(
            session,
            organization_id,
            text(
                "SELECT id, argilla_record_id, status FROM review_items "
                "WHERE queue_id = :queue AND record_id = :record_id FOR UPDATE"
            ),
            {"queue": queue_id, "record_id": record_id},
        ).one()  # existence was proven above; items are never deleted
        item_id, argilla_record_id, item_status = item
        if item_status != "pending":
            raise ReviewQueueError("review_item_drift")
        if argilla_record_id is None:
            raise ReviewQueueError("queue_not_ready")
        # Authorization again, under the item lock: a revoke or a narrowed role that committed
        # after the first check must not let this write through.
        still_authorized = execute_tenant_scoped(
            session,
            organization_id,
            text(
                "SELECT 1 FROM argilla_workspace_members m JOIN argilla_reviewer_identities i "
                "ON i.organization_id = m.organization_id AND i.id = m.identity_id "
                "WHERE m.scope_id = :scope AND m.identity_id = :identity "
                "AND m.status = 'active' AND i.status = 'active'"
            ),
            {"scope": queue.scope_id, "identity": identity_id},
        ).first()
        if still_authorized is None:
            raise ReviewQueueError("reviewer_not_authorized")
        current = execute_tenant_scoped(
            session,
            organization_id,
            text(
                "SELECT id, argilla_response_id, row_version FROM review_responses "
                "WHERE item_id = :item AND identity_id = :identity"
            ),
            {"item": item_id, "identity": identity_id},
        ).first()
        current_version = 0 if current is None else current[2]
        if expected_version != current_version:
            raise ReviewQueueError("review_version_conflict")
        try:
            written = _write_argilla_response(
                reviewer_api,
                owner_api,
                argilla_record_id=argilla_record_id,
                argilla_user_id=argilla_user_id,
                known_response_id=None if current is None else current[1],
                decision=decision,
                rationale=rationale,
            )
        except ArgillaClientError as exc:
            raise _argilla_error(exc) from None
        digest = response_sha256(
            record_id=record_id,
            user_id=argilla_user_id,
            decision=decision,
            rationale=rationale,
        )
        if current is None:
            row = execute_tenant_scoped(
                session,
                organization_id,
                text(
                    "INSERT INTO review_responses (organization_id, queue_id, item_id, "
                    "identity_id, decision, rationale, response_sha256, argilla_response_id) "
                    "VALUES (:org, :queue, :item, :identity, :decision, :rationale, :digest, "
                    ":argilla_response_id) RETURNING id, row_version"
                ),
                {
                    "org": organization_id,
                    "queue": queue_id,
                    "item": item_id,
                    "identity": identity_id,
                    "decision": decision,
                    "rationale": rationale,
                    "digest": digest,
                    "argilla_response_id": written,
                },
            ).first()
        else:
            row = execute_tenant_scoped(
                session,
                organization_id,
                text(
                    "UPDATE review_responses SET decision = :decision, rationale = :rationale, "
                    "response_sha256 = :digest, row_version = row_version + 1, "
                    "updated_at = now() WHERE id = :id RETURNING id, row_version"
                ),
                {
                    "decision": decision,
                    "rationale": rationale,
                    "digest": digest,
                    "id": current[0],
                },
            ).first()
        assert row is not None
        write_audit_event(
            session,
            organization_id=organization_id,
            actor_type="user",
            actor_id=subject,
            action="review_response.submitted" if current is None else "review_response.changed",
            resource_type=_RESPONSE_RESOURCE,
            resource_id=str(row[0]),
            request_id=request_id,
            details={
                "issuer": issuer,
                "queue_id": str(queue_id),
                "item_id": str(item_id),
                "row_version": row[1],
                "response_sha256": digest,
            },
        )
        session.flush()
        return ReviewResponseRecord(
            id=row[0],
            queue_id=queue_id,
            item_id=item_id,
            identity_id=identity_id,
            decision=decision,
            rationale=rationale,
            response_sha256=digest,
            argilla_response_id=written,
            row_version=row[1],
        )


def _write_argilla_response(
    reviewer_api: ArgillaReviewApi,
    owner_api: ArgillaReviewApi,
    *,
    argilla_record_id: str,
    argilla_user_id: str,
    known_response_id: str | None,
    decision: str,
    rationale: str,
) -> str:
    """Writes with the reviewer's own key, then proves with the provisioning key that the stored
    response is that user's, submitted and equal to what was sent. Returns the Argilla response
    id."""
    if known_response_id is not None:
        reviewer_api.update_response(known_response_id, decision, rationale)
    else:
        try:
            reviewer_api.submit_response(argilla_record_id, decision, rationale)
        except ArgillaConflictError:
            # An earlier attempt wrote to Argilla but never reached the Factory's commit: adopt
            # that response (found below by author) and overwrite it with this decision.
            adopted = _response_by(owner_api.get_record(argilla_record_id), argilla_user_id)
            if adopted is None:
                raise
            reviewer_api.update_response(adopted.id, decision, rationale)
    stored = _response_by(owner_api.get_record(argilla_record_id), argilla_user_id)
    if (
        stored is None
        or not stored.submitted
        or (stored.decision, stored.rationale or "") != (decision, rationale)
    ):
        raise ReviewQueueError("response_author_mismatch")
    return stored.id


def _response_by(record: ArgillaReviewRecord, user_id: str) -> Any:
    mine = [response for response in record.responses if response.user_id == user_id]
    return mine[0] if len(mine) == 1 else None


# -------------------------------------------------------------------------- reconcile


def _item_view(item: _QueueItem) -> dict[str, str]:
    return {
        "record_id": item.record_id,
        "group_id": item.group_id,
        "record_sha256": item.record_sha256,
        "fields_sha256": item.fields_sha256,
    }


def _record_reason(item: Mapping[str, Any], record: ArgillaReviewRecord) -> str | None:
    """Why an Argilla record is not the one the Factory queued for `item` (metadata, then text)."""
    expected_metadata = {
        "record_id": item["record_id"],
        "group_id": item["group_id"],
        "record_sha256": item["record_sha256"],
        "schema_version": SCHEMA_VERSION,
    }
    if dict(record.metadata) != expected_metadata:
        return "record_metadata_changed"
    rendered = record.fields.get(FIELD_NAME)
    if not isinstance(rendered, str) or fields_sha256(rendered) != item["fields_sha256"]:
        return "record_fields_changed"
    return None


def _drift_reason(
    item: Mapping[str, Any],
    record: ArgillaReviewRecord | None,
    stored: Mapping[str, Mapping[str, Any]],
    mapped_users: Mapping[str, str],
) -> str | None:
    """`stored` maps identity id text -> the Factory's response row for this item;
    `mapped_users` maps Argilla user id -> identity id text."""
    if record is None:
        return "record_missing"
    reason = _record_reason(item, record)
    if reason is not None:
        return reason
    seen: set[str] = set()
    for response in record.responses:
        identity = mapped_users.get(response.user_id)
        if identity is None:
            return "response_by_unmapped_user"
        row = stored.get(identity)
        if row is None:
            return "response_untracked"
        seen.add(identity)
        if (
            not response.submitted
            or response.decision is None
            or response.id != row["argilla_response_id"]
            or response_sha256(
                record_id=item["record_id"],
                user_id=response.user_id,
                decision=response.decision,
                rationale=response.rationale or "",
            )
            != row["response_sha256"]
        ):
            return "response_drift"
    if set(stored) - seen:
        return "response_missing"
    return None


def reconcile_review_queue(
    session_factory: Callable[[], Session],
    *,
    context: TenantContext,
    queue_id: uuid.UUID,
    owner_api: ArgillaReviewApi,
    actor_type: str,
    actor_id: str,
    request_id: str,
) -> tuple[DriftFinding, ...]:
    """Polls Argilla with the provisioning key and marks every drifted item. Returns every item
    now in `drift` (earlier findings included: drift is sticky) plus `foreign_record` findings
    for Argilla records the Factory never queued. Argilla down -> `argilla_unavailable` with
    nothing changed."""
    if not _is_uuid(queue_id) or not isinstance(owner_api, ArgillaReviewApi):
        raise ReviewQueueError("invalid_request")
    actor_type = _clean_text(actor_type, 32)
    actor_id = _clean_text(actor_id, 200)
    request_id = _clean_text(request_id, 128)
    organization_id = context.organization_id
    with tenant_scoped_session(session_factory, organization_id) as session:
        queue_row = execute_tenant_scoped(
            session,
            organization_id,
            text(f"SELECT {_QUEUE_COLUMNS} FROM review_queues WHERE id = :id"),
            {"id": queue_id},
        ).first()
        if queue_row is None:
            raise ReviewQueueError("queue_not_found")
        queue = _queue_of(organization_id, queue_row)
        if queue.status != "ready" or queue.argilla_dataset_id is None:
            raise ReviewQueueError("queue_not_ready")
    try:
        records = owner_api.list_records(queue.argilla_dataset_id)
    except ArgillaClientError as exc:
        raise _argilla_error(exc) from None
    by_external = {record.external_id: record for record in records}

    # -- phase 1: candidates, no locks. A poll that overlaps a legitimate submit sees a state
    # that is already stale, so nothing is marked from this phase alone.
    with tenant_scoped_session(session_factory, organization_id) as session:
        items, responses, mapped_users = _factory_state(session, organization_id, queue_id)
    findings: list[DriftFinding] = []
    candidates: list[dict[str, Any]] = []
    for item in items:
        if item["status"] == "drift":
            findings.append(DriftFinding(item["id"], item["record_id"], item["drift_reason"]))
        elif (
            _drift_reason(
                item,
                by_external.get(item["record_id"]),
                responses.get(item["id"], {}),
                mapped_users,
            )
            is not None
        ):
            candidates.append(item)
    known = {item["record_id"] for item in items}
    for external_id in sorted(by_external.keys() - known, key=lambda e: e or ""):
        findings.append(DriftFinding(None, external_id, "foreign_record"))

    # -- phase 2: each candidate is re-verified under its item lock. Every response write holds
    # that same lock until it commits, so after the lock is taken the Factory rows and a fresh
    # read of the record agree; only a drift that survives that is marked.
    newly = 0
    with tenant_scoped_session(session_factory, organization_id) as session:
        for item in sorted(candidates, key=lambda c: str(c["id"])):
            locked = execute_tenant_scoped(
                session,
                organization_id,
                text(
                    "SELECT status, argilla_record_id FROM review_items "
                    "WHERE id = :id FOR UPDATE"
                ),
                {"id": item["id"]},
            ).one()
            if locked[0] != "pending" or locked[1] is None:
                continue
            _, responses_now, mapped_now = _factory_state(session, organization_id, queue_id)
            try:
                fresh: ArgillaReviewRecord | None = owner_api.get_record(locked[1])
            except ArgillaNotFoundError:
                fresh = None
            except ArgillaClientError as exc:
                raise _argilla_error(exc) from None
            reason = _drift_reason(item, fresh, responses_now.get(item["id"], {}), mapped_now)
            if reason is None:
                continue
            execute_tenant_scoped(
                session,
                organization_id,
                text(
                    "UPDATE review_items SET status = 'drift', drift_reason = :reason, "
                    "updated_at = now() WHERE id = :id AND status = 'pending'"
                ),
                {"id": item["id"], "reason": reason},
            )
            findings.append(DriftFinding(item["id"], item["record_id"], reason))
            newly += 1
        write_audit_event(
            session,
            organization_id=organization_id,
            actor_type=actor_type,
            actor_id=actor_id,
            action="review_queue.reconciled",
            resource_type=_RESOURCE,
            resource_id=str(queue_id),
            request_id=request_id,
            details={
                "newly_drifted": newly,
                "drifted_total": len(findings),
                "queue_snapshot_sha256": queue.queue_snapshot_sha256,
            },
        )
        session.flush()
    return tuple(findings)


def _factory_state(
    session: Session, organization_id: uuid.UUID, queue_id: uuid.UUID
) -> tuple[list[dict[str, Any]], dict[uuid.UUID, dict[str, Mapping[str, Any]]], dict[str, str]]:
    """The queue's items, their recorded responses (by identity id text) and the Argilla user id
    -> identity id text map, as the database holds them now."""
    items = [
        dict(row._mapping)
        for row in execute_tenant_scoped(
            session,
            organization_id,
            text(
                "SELECT id, record_id, group_id, record_sha256, fields_sha256, status, "
                "drift_reason FROM review_items WHERE queue_id = :queue ORDER BY batch_position"
            ),
            {"queue": queue_id},
        )
    ]
    responses: dict[uuid.UUID, dict[str, Mapping[str, Any]]] = {}
    for row in execute_tenant_scoped(
        session,
        organization_id,
        text(
            "SELECT item_id, identity_id, argilla_response_id, response_sha256 "
            "FROM review_responses WHERE queue_id = :queue"
        ),
        {"queue": queue_id},
    ):
        responses.setdefault(row[0], {})[str(row[1])] = {
            "argilla_response_id": row[2],
            "response_sha256": row[3],
        }
    mapped_users = {
        row[0]: str(row[1])
        for row in execute_tenant_scoped(
            session,
            organization_id,
            text(
                "SELECT argilla_user_id, id FROM argilla_reviewer_identities "
                "WHERE organization_id = :org"
            ),
            {"org": organization_id},
        )
    }
    return items, responses, mapped_users
