"""Dataset mapping store (FTF-H011-P2; FINE_TUNING_FACTORY_RELEASE_1.md Section 0 items 13/27,
Section 7, Section 7A.10, Section 8-8.2, Section 9, Section 67.1-67.4, Section 69.1; AF-15).

FTF-H011-P1 previews a MappingSpec against a real dataset. This module persists the result as a
versioned, approvable record and is the input of a later import run. The table, constraints, grants
and triggers are in `0034_dataset_mappings.py`; the mapping grammar is the certified FTF-0502-P1
`mapping_from_object` (no new mapping language here) and the preview is FTF-H011-P1
`preview_dataset_mapping` (the only reader of the dataset bytes).

Rules enforced here, on top of the database's own:

- `propose_dataset_mapping` is the only way a proposal is built. It parses the document with
  `mapping_from_object` (`mapping_invalid` / `mapping_unsupported`), refuses a message shape whose
  `role_map` targets lack `user` or `assistant` (`critical_role_unmapped`) and a mapping that
  targets `tool` or maps `tool_calls_key` without both `tool_calls_key` and `tool_call_id_key`
  (`tool_id_unmapped`), re-authorizes the dataset's connection for the dataset's own purpose
  (`purpose_not_authorized`) and then runs the real preview itself: it accepts no caller-supplied
  preview. The importer comes from the server-verified content type only. A change is a new version
  (`parent_mapping_version` = the previous one); an unchanged re-proposal returns the latest row and
  writes nothing.
- `approve_dataset_mapping` takes the approver from the authenticated actor, never from input, and
  only for an admin role (`approval_not_permitted`). Authorization comes first: the requested
  purpose is re-authorized live (`purpose_not_authorized`) and must equal the dataset's own purpose
  (`purpose_mismatch`, Section 9: no reclassification). The preview is then re-run from the stored
  mapping against the live bytes and live tool definitions *before* the write lock is taken and must
  hash to the stored `preview_sha256` (`preview_changed`), also when the caller's
  `expected_preview_sha256` is stale. A preview that did not finish is `preview_incomplete`, a
  storage outage `source_unavailable` and a changed object `source_changed` (Law 17: never turned
  into success). `unmapped_role` / `unmapped_message_key` rows are `critical_rows_unmapped`;
  `on_unmapped_message_keys: "ignore"` needs `acknowledged_ignored_keys` exactly equal to the
  message keys the preview ignored (`ignored_keys_not_acknowledged`); only their sha256 is stored.
  A superseded version is never approved (`mapping_superseded`).
- `resolve_approved_mapping` is the input of a later import run: the dataset's own latest approved
  mapping, or an earlier dataset version's only when it is tabular, its header
  (`source_schema_sha256`) and the tool definitions are unchanged on a live re-probe, and no
  critical row is unmapped; anything else is `mapping_reapproval_required` (JSON / JSONL always need
  a fresh approval). The grant is re-authorized live on every call (Section 67.4), and a stored
  document that no longer hashes to its `mapping_sha256` is `mapping_tampered`.

Every refusal is a `MappingStoreError` with a stable `code`, raised before anything is written.
Audit rows (`dataset_mapping.proposed` / `approved` / `rejected`) carry ids, versions, hashes and
counts, never the preview, a raw row or a key name.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Final

from sqlalchemy import JSON, DateTime, Integer, String, UniqueConstraint, func, select
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Mapped, Session, mapped_column
from workers.data import csv_importer, json_importer, jsonl_importer
from workers.data.dataset_mapping_review import (
    JSONL_PREVIEW_CONTENT_TYPES,
    MappingPreviewResult,
    MappingPreviewUnavailableError,
)
from workers.data.import_common import (
    ImportMapping,
    MappingInvalidError,
    MappingUnsupportedError,
    mapping_from_object,
    resolve_pointer,
    strict_loads,
)

from app.auth.audit_event_writer import write_audit_event
from app.auth.organization_workspace_schema import Base
from app.auth.tenant_isolation_query_guards import execute_tenant_scoped
from app.datasets.dataset_mapping_service import preview_dataset_mapping
from app.datasets.dataset_object_source import DatasetObjectStorage, DatasetSourceInconsistentError
from app.datasets.dataset_quality_store import ADMIN_ROLES
from app.datasets.dataset_source_connection_crud import (
    DatasetSourceAuthorizationError,
    DatasetSourceConnectionNotFoundError,
    authorize_dataset_source_purpose,
)
from app.datasets.dataset_upload_store import (
    Dataset,
    DatasetNotFoundError,
    UploadSessionNotFoundError,
    get_dataset,
    get_upload_session,
)
from app.db.tenant_context import TenantContext

__all__ = [
    "CODES",
    "PROPOSER_ROLES",
    "TRANSFORM_VERSION",
    "DatasetMapping",
    "MappingActor",
    "MappingStoreError",
    "ResolvedMapping",
    "approve_dataset_mapping",
    "canonical_sha256",
    "check_critical_mapping",
    "get_dataset_mapping",
    "ignored_keys_sha256",
    "ignored_message_keys",
    "importer_for_content_type",
    "list_dataset_mappings",
    "propose_dataset_mapping",
    "reject_dataset_mapping",
    "resolve_approved_mapping",
]

TRANSFORM_VERSION: Final = "h011.1"
PROPOSER_ROLES: Final = ADMIN_ROLES | frozenset({"ml_engineer"})
MAX_LIST_LIMIT: Final = 200
_RESOURCE: Final = "dataset_mapping"
_HEX64: Final = re.compile(r"[0-9a-f]{64}")
_CRITICAL_ROW_CODES: Final = ("rejected:unmapped_role", "rejected:unmapped_message_key")
_IMPORTER_VERSIONS: Final = {
    csv_importer.IMPORTER_NAME: csv_importer.IMPORTER_VERSION,
    json_importer.IMPORTER_NAME: json_importer.IMPORTER_VERSION,
    jsonl_importer.IMPORTER_NAME: jsonl_importer.IMPORTER_VERSION,
}
_REUSABLE_PAIRS: Final = frozenset({(csv_importer.IMPORTER_NAME, csv_importer.IMPORTER_NAME)})

CODES: Final = frozenset(
    {
        "invalid_request",
        "actor_not_permitted",
        "approval_not_permitted",
        "dataset_not_found",
        "purpose_not_authorized",
        "purpose_mismatch",
        "mapping_invalid",
        "mapping_unsupported",
        "critical_role_unmapped",
        "tool_id_unmapped",
        "importer_unavailable",
        "source_unavailable",
        "source_changed",
        "preview_incomplete",
        "mapping_not_found",
        "mapping_not_proposed",
        "mapping_superseded",
        "mapping_conflict",
        "preview_changed",
        "critical_rows_unmapped",
        "ignored_keys_not_acknowledged",
        "approval_refused",
        "mapping_reapproval_required",
        "mapping_tampered",
    }
)

_JSON = JSONB().with_variant(JSON(), "sqlite")
_TEXT_ARRAY = ARRAY(String).with_variant(JSON(), "sqlite")


class MappingStoreError(Exception):
    """A refusal with a stable `code`. Nothing was written when it was raised."""

    def __init__(self, code: str) -> None:
        assert code in CODES, code
        super().__init__(code)
        self.code = code


class DatasetMapping(Base):
    """One mapping version of one dataset. Immutable except the single proposed -> approved |
    rejected step."""

    __tablename__ = "dataset_mappings"
    __table_args__ = (
        UniqueConstraint(
            "organization_id",
            "project_id",
            "dataset_id",
            "mapping_version",
            name="uq_dataset_mappings_dataset_version",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    organization_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    project_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    dataset_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    dataset_name: Mapped[str] = mapped_column(String(200), nullable=False)
    dataset_purpose: Mapped[str] = mapped_column(String(32), nullable=False)
    dataset_version: Mapped[int] = mapped_column(Integer, nullable=False)
    dataset_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    dataset_source_connection_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False
    )
    mapping_version: Mapped[int] = mapped_column(Integer, nullable=False)
    parent_mapping_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    mapping_document: Mapped[dict[str, Any]] = mapped_column(_JSON, nullable=False)
    mapping_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    importer: Mapped[str] = mapped_column(String(16), nullable=False)
    importer_version: Mapped[str] = mapped_column(String(16), nullable=False)
    transform_version: Mapped[str] = mapped_column(String(16), nullable=False)
    source_schema_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    tool_definitions_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    preview_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    preview_counts: Mapped[dict[str, int]] = mapped_column(_JSON, nullable=False)
    ignored_keys_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    allowed_purposes_snapshot: Mapped[list[str]] = mapped_column(_TEXT_ARRAY, nullable=False)
    approved_purpose: Mapped[str | None] = mapped_column(String(32), nullable=True)
    approved_by: Mapped[str | None] = mapped_column(String(200), nullable=True)
    approver_role: Mapped[str | None] = mapped_column(String(32), nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_by: Mapped[str] = mapped_column(String(200), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


@dataclass(frozen=True)
class MappingActor:
    """`actor_id` and `role` come from the authenticated principal, never from a request body."""

    actor_type: str
    actor_id: str
    role: str
    request_id: str


@dataclass(frozen=True)
class ResolvedMapping:
    """The input of a later import run: the parsed mapping and where it came from."""

    mapping: ImportMapping
    mapping_id: uuid.UUID
    mapping_version: int
    source_dataset_id: uuid.UUID
    approved_purpose: str
    reused: bool


# ------------------------------------------------------------------ pure helpers


def canonical_sha256(value: object) -> str:
    """sha256 of the canonical JSON form (sorted keys, no spaces, ASCII), the digest convention of
    FTF-0502-P1's `mapping_sha256`."""
    text = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(text.encode("ascii")).hexdigest()


def ignored_keys_sha256(keys: Sequence[str]) -> str:
    """The only trace of an acknowledged ignored-key set that is stored."""
    return canonical_sha256(sorted(keys))


def importer_for_content_type(verified_content_type: object) -> str:
    """The importer the server-verified content type selects; never anything the caller says."""
    if type(verified_content_type) is not str:
        raise MappingStoreError("importer_unavailable")
    if verified_content_type in JSONL_PREVIEW_CONTENT_TYPES:
        return jsonl_importer.IMPORTER_NAME
    if verified_content_type == "application/json":
        return json_importer.IMPORTER_NAME
    if verified_content_type == "text/csv":
        return csv_importer.IMPORTER_NAME
    raise MappingStoreError("importer_unavailable")


def check_critical_mapping(mapping: ImportMapping) -> None:
    """The propose-time critical checks. A message shape needs `user` and `assistant` targets in its
    `role_map`; one that targets `tool`, or maps `tool_calls_key`, needs both `tool_calls_key` and
    `tool_call_id_key`. A `prompt_completion` mapping carries neither concern."""
    keys = mapping.message_keys
    if keys is None:
        return
    targets = set(mapping.role_map.values())
    if "user" not in targets or "assistant" not in targets:
        raise MappingStoreError("critical_role_unmapped")
    if ("tool" in targets or keys.tool_calls_key is not None) and not (
        keys.tool_calls_key is not None and keys.tool_call_id_key is not None
    ):
        raise MappingStoreError("tool_id_unmapped")


def _row_cell(slot: Any, row: Any) -> tuple[bool, object]:
    if isinstance(row, Mapping):
        if slot.kind not in ("key", "header"):
            return False, None
        name = str(slot.value)
        return (name in row), row.get(name)
    if slot.kind == "index" and isinstance(row, tuple) and 0 <= int(slot.value) < len(row):
        return True, row[int(slot.value)]
    return False, None


def _row_messages(mapping: ImportMapping, row: Any) -> list[object]:
    """The message objects of one revealed raw row, resolved the way `apply_mapping` resolves them;
    anything that does not resolve to a list is no messages (such a row has its own rejection)."""
    array: object = None
    try:
        if mapping.messages_slot is None:
            if isinstance(row, Mapping):
                _found, array = resolve_pointer(dict(row), mapping.messages_pointer or ())
        else:
            found, cell = _row_cell(mapping.messages_slot, row)
            if found:
                array = strict_loads(cell) if type(cell) is str else cell
    except (ValueError, TypeError):
        return []
    return list(array) if type(array) is list else []


def ignored_message_keys(mapping: ImportMapping, preview: MappingPreviewResult) -> tuple[str, ...]:
    """The message keys an `on_unmapped_message_keys: "ignore"` mapping silently drops, as seen in
    the preview's *revealed* raw rows: every key of every message object outside the mapped
    `role_key` / `content_key` / `tool_calls_key` / `tool_call_id_key` / `name_key`. Empty for any
    other policy."""
    keys = mapping.message_keys
    if keys is None or mapping.on_unmapped_message_keys != "ignore":
        return ()
    known = {keys.role_key, keys.content_key}
    known.update(k for k in (keys.tool_calls_key, keys.tool_call_id_key, keys.name_key) if k)
    seen: set[str] = set()
    for row in preview.rows:
        for message in _row_messages(mapping, row.raw_row):
            if isinstance(message, dict):
                seen.update(str(key) for key in message if key not in known)
    return tuple(sorted(seen))


def _same_hash(left: str | None, right: str | None) -> bool:
    """Digest equality: both present and equal, byte for byte."""
    return left is not None and right is not None and hmac.compare_digest(left, right)


def _now() -> datetime:
    return datetime.now(UTC)


def _clean(value: object, limit: int) -> str:
    if (
        type(value) is not str
        or not value
        or value != value.strip()
        or len(value) > limit
        or "\x00" in value
    ):
        raise MappingStoreError("invalid_request")
    return value


def _require_actor(actor: object, *, roles: frozenset[str], code: str) -> MappingActor:
    if not isinstance(actor, MappingActor):
        raise MappingStoreError("invalid_request")
    _clean(actor.actor_type, 64)
    _clean(actor.actor_id, 200)
    _clean(actor.role, 64)
    _clean(actor.request_id, 100)
    if actor.role not in roles:
        raise MappingStoreError(code)
    return actor


def _require_hash(value: object) -> str:
    if type(value) is not str or _HEX64.fullmatch(value) is None:
        raise MappingStoreError("invalid_request")
    return value


def _require_ids(*values: object) -> None:
    if not all(isinstance(value, uuid.UUID) for value in values):
        raise MappingStoreError("invalid_request")


def _instant(value: datetime | None) -> datetime:
    return value if value is not None else _now()


# ------------------------------------------------------------------ reads


def _fetch(
    session: Session,
    context: TenantContext,
    project_id: uuid.UUID,
    mapping_id: uuid.UUID,
    *,
    for_update: bool = False,
) -> DatasetMapping:
    if not isinstance(project_id, uuid.UUID) or not isinstance(mapping_id, uuid.UUID):
        raise MappingStoreError("mapping_not_found")
    statement = select(DatasetMapping).where(
        DatasetMapping.id == mapping_id, DatasetMapping.project_id == project_id
    )
    if for_update:
        statement = statement.with_for_update()
    row = execute_tenant_scoped(session, context.organization_id, statement).scalars().first()
    if row is None:
        raise MappingStoreError("mapping_not_found")
    return row


def _latest_version(
    session: Session, context: TenantContext, project_id: uuid.UUID, dataset_id: uuid.UUID
) -> int:
    statement = select(func.max(DatasetMapping.mapping_version)).where(
        DatasetMapping.project_id == project_id, DatasetMapping.dataset_id == dataset_id
    )
    value = execute_tenant_scoped(session, context.organization_id, statement).scalar()
    return int(value) if value is not None else 0


def get_dataset_mapping(
    session: Session, context: TenantContext, *, project_id: uuid.UUID, mapping_id: uuid.UUID
) -> DatasetMapping:
    return _fetch(session, context, project_id, mapping_id)


def list_dataset_mappings(
    session: Session,
    context: TenantContext,
    *,
    project_id: uuid.UUID,
    dataset_id: uuid.UUID,
    limit: int = 50,
    after_version: int = 0,
) -> list[DatasetMapping]:
    """The dataset's mapping versions, oldest first, paged by `mapping_version`."""
    _require_ids(project_id, dataset_id)
    if (
        type(limit) is not int
        or not 1 <= limit <= MAX_LIST_LIMIT
        or type(after_version) is not int
        or after_version < 0
    ):
        raise MappingStoreError("invalid_request")
    statement = (
        select(DatasetMapping)
        .where(
            DatasetMapping.project_id == project_id,
            DatasetMapping.dataset_id == dataset_id,
            DatasetMapping.mapping_version > after_version,
        )
        .order_by(DatasetMapping.mapping_version)
        .limit(limit)
    )
    return list(execute_tenant_scoped(session, context.organization_id, statement).scalars())


def _audit(
    session: Session,
    context: TenantContext,
    actor: MappingActor,
    action: str,
    row: DatasetMapping,
) -> None:
    counts = row.preview_counts
    write_audit_event(
        session,
        organization_id=context.organization_id,
        actor_type=actor.actor_type,
        actor_id=actor.actor_id,
        action=action,
        resource_type=_RESOURCE,
        resource_id=str(row.id),
        request_id=actor.request_id,
        details={
            "project_id": str(row.project_id),
            "dataset_id": str(row.dataset_id),
            "mapping_version": row.mapping_version,
            "mapping_sha256": row.mapping_sha256,
            "preview_sha256": row.preview_sha256,
            "importer": row.importer,
            "status": row.status,
            "seen": counts.get("seen", 0),
            "accepted": counts.get("accepted", 0),
            "rejected": counts.get("rejected", 0),
        },
    )


# ------------------------------------------------------------------ preview plumbing


def _authorized_dataset(
    session: Session,
    context: TenantContext,
    *,
    project_id: uuid.UUID,
    dataset_id: uuid.UUID,
    include_held_out: bool,
    purpose: str | None = None,
) -> tuple[Dataset, uuid.UUID, list[str], str]:
    """The dataset, its connection id, that connection's allowed purposes now, and the importer.
    The connection is re-authorized for `purpose` (default: the dataset's own) before anything
    else is read from storage."""
    try:
        dataset = get_dataset(
            session,
            context,
            project_id=project_id,
            dataset_id=dataset_id,
            include_held_out=include_held_out,
        )
        upload = get_upload_session(session, context, project_id, dataset.upload_session_id)
        connection = authorize_dataset_source_purpose(
            session,
            context,
            project_id,
            upload.dataset_source_connection_id,
            dataset.purpose if purpose is None else purpose,
        )
    except (DatasetNotFoundError, UploadSessionNotFoundError):
        raise MappingStoreError("dataset_not_found") from None
    except (DatasetSourceAuthorizationError, DatasetSourceConnectionNotFoundError):
        raise MappingStoreError("purpose_not_authorized") from None
    importer = importer_for_content_type(upload.verified_content_type)
    return (
        dataset,
        upload.dataset_source_connection_id,
        [str(p) for p in connection.allowed_purposes_json],
        importer,
    )


def _finished_preview(
    session: Session,
    context: TenantContext,
    *,
    project_id: uuid.UUID,
    dataset_id: uuid.UUID,
    include_held_out: bool,
    storage: DatasetObjectStorage,
    mapping: ImportMapping,
    reveal: bool,
) -> MappingPreviewResult:
    """The real preview, or the code of why it is not one (Law 17: an outage, a changed object or
    an aborted run is never a result)."""
    try:
        result = preview_dataset_mapping(
            session,
            context,
            project_id=project_id,
            dataset_id=dataset_id,
            include_held_out=include_held_out,
            storage=storage,
            mapping=mapping,
            reveal=reveal,
        )
    except MappingPreviewUnavailableError:
        raise MappingStoreError("importer_unavailable") from None
    except DatasetNotFoundError:
        raise MappingStoreError("dataset_not_found") from None
    except (DatasetSourceAuthorizationError, DatasetSourceConnectionNotFoundError):
        raise MappingStoreError("purpose_not_authorized") from None
    except DatasetSourceInconsistentError:
        raise MappingStoreError("source_changed") from None
    if result.state == "blocked":
        raise MappingStoreError("source_unavailable")
    if result.failure_code == "source_changed":
        raise MappingStoreError("source_changed")
    if result.state != "completed":
        raise MappingStoreError("preview_incomplete")
    return result


def _counts(result: MappingPreviewResult) -> dict[str, int]:
    return {str(key): int(value) for key, value in result.counts.items()}


def _parse_document(document: object) -> ImportMapping:
    try:
        return mapping_from_object(document)
    except MappingUnsupportedError:
        raise MappingStoreError("mapping_unsupported") from None
    except MappingInvalidError:
        raise MappingStoreError("mapping_invalid") from None


# ------------------------------------------------------------------ propose


def propose_dataset_mapping(
    session: Session,
    context: TenantContext,
    *,
    project_id: uuid.UUID,
    dataset_id: uuid.UUID,
    mapping_document: object,
    storage: DatasetObjectStorage,
    actor: MappingActor,
    now: datetime | None = None,
) -> DatasetMapping:
    """Parses, checks and previews `mapping_document` against the dataset and stores it as the next
    version. Held-out datasets are visible only to admin roles. An unchanged re-proposal returns the
    latest row and writes nothing. On any raise nothing was written."""
    _require_actor(actor, roles=PROPOSER_ROLES, code="actor_not_permitted")
    _require_ids(project_id, dataset_id)
    mapping = _parse_document(mapping_document)
    check_critical_mapping(mapping)
    include_held_out = actor.role in ADMIN_ROLES
    dataset, connection_id, snapshot, importer = _authorized_dataset(
        session,
        context,
        project_id=project_id,
        dataset_id=dataset_id,
        include_held_out=include_held_out,
    )
    preview = _finished_preview(
        session,
        context,
        project_id=project_id,
        dataset_id=dataset_id,
        include_held_out=include_held_out,
        storage=storage,
        mapping=mapping,
        reveal=False,
    )
    latest = _latest_version(session, context, project_id, dataset_id)
    if latest:
        previous = _fetch_version(session, context, project_id, dataset_id, latest)
        if previous.status != "rejected" and _same_proposal(
            previous, mapping, preview, connection_id, importer, snapshot
        ):
            return previous
    instant = _instant(now)
    row = DatasetMapping(
        id=uuid.uuid4(),
        organization_id=context.organization_id,
        project_id=project_id,
        dataset_id=dataset.id,
        dataset_name=dataset.name,
        dataset_purpose=dataset.purpose,
        dataset_version=dataset.version,
        dataset_sha256=dataset.verified_sha256,
        dataset_source_connection_id=connection_id,
        mapping_version=latest + 1,
        parent_mapping_version=latest or None,
        mapping_document=json.loads(json.dumps(dict(mapping.document))),
        mapping_sha256=mapping.mapping_sha256,
        importer=importer,
        importer_version=_IMPORTER_VERSIONS[importer],
        transform_version=TRANSFORM_VERSION,
        source_schema_sha256=preview.source_schema_sha256,
        tool_definitions_sha256=preview.tool_definitions_sha256,
        preview_sha256=preview.preview_sha256,
        preview_counts=_counts(preview),
        ignored_keys_sha256=None,
        status="proposed",
        allowed_purposes_snapshot=sorted(snapshot),
        approved_purpose=None,
        approved_by=None,
        approver_role=None,
        approved_at=None,
        created_by=actor.actor_id,
        created_at=instant,
        updated_at=instant,
    )
    session.add(row)
    try:
        session.flush()
    except IntegrityError:
        raise MappingStoreError("mapping_conflict") from None
    _audit(session, context, actor, "dataset_mapping.proposed", row)
    session.flush()
    return row


def _fetch_version(
    session: Session,
    context: TenantContext,
    project_id: uuid.UUID,
    dataset_id: uuid.UUID,
    version: int,
) -> DatasetMapping:
    statement = select(DatasetMapping).where(
        DatasetMapping.project_id == project_id,
        DatasetMapping.dataset_id == dataset_id,
        DatasetMapping.mapping_version == version,
    )
    return execute_tenant_scoped(session, context.organization_id, statement).scalars().one()


def _same_proposal(
    previous: DatasetMapping,
    mapping: ImportMapping,
    preview: MappingPreviewResult,
    connection_id: uuid.UUID,
    importer: str,
    snapshot: Sequence[str],
) -> bool:
    return (
        previous.mapping_sha256,
        previous.preview_sha256,
        previous.source_schema_sha256,
        previous.tool_definitions_sha256,
        previous.dataset_source_connection_id,
        previous.importer,
        previous.importer_version,
        previous.transform_version,
        sorted(previous.allowed_purposes_snapshot),
    ) == (
        mapping.mapping_sha256,
        preview.preview_sha256,
        preview.source_schema_sha256,
        preview.tool_definitions_sha256,
        connection_id,
        importer,
        _IMPORTER_VERSIONS[importer],
        TRANSFORM_VERSION,
        sorted(snapshot),
    )


# ------------------------------------------------------------------ approve / reject


def _is_superseded(session: Session, context: TenantContext, row: DatasetMapping) -> bool:
    return _latest_version(session, context, row.project_id, row.dataset_id) > row.mapping_version


def approve_dataset_mapping(
    session: Session,
    context: TenantContext,
    *,
    project_id: uuid.UUID,
    mapping_id: uuid.UUID,
    expected_preview_sha256: str,
    purpose: str,
    acknowledged_ignored_keys: Sequence[str],
    storage: DatasetObjectStorage,
    actor: MappingActor,
    now: datetime | None = None,
) -> DatasetMapping:
    """proposed -> approved. The approver is `actor`, never input. The preview is re-run from the
    stored mapping against the live bytes before the write lock is taken. On any raise nothing was
    written."""
    _require_actor(actor, roles=ADMIN_ROLES, code="approval_not_permitted")
    _require_hash(expected_preview_sha256)
    _clean(purpose, 32)
    if not isinstance(acknowledged_ignored_keys, list | tuple) or not all(
        type(key) is str for key in acknowledged_ignored_keys
    ):
        raise MappingStoreError("invalid_request")
    row = _fetch(session, context, project_id, mapping_id)
    if row.status != "proposed":
        raise MappingStoreError("mapping_not_proposed")
    if _is_superseded(session, context, row):
        raise MappingStoreError("mapping_superseded")
    _dataset, _connection_id, _snapshot, _importer = _authorized_dataset(
        session,
        context,
        project_id=project_id,
        dataset_id=row.dataset_id,
        include_held_out=True,
        purpose=purpose,
    )
    if purpose != row.dataset_purpose:
        raise MappingStoreError("purpose_mismatch")
    if not _same_hash(expected_preview_sha256, row.preview_sha256):
        raise MappingStoreError("preview_changed")
    mapping = _parse_document(row.mapping_document)
    if not _same_hash(mapping.mapping_sha256, row.mapping_sha256):
        raise MappingStoreError("mapping_tampered")
    preview = _finished_preview(
        session,
        context,
        project_id=project_id,
        dataset_id=row.dataset_id,
        include_held_out=True,
        storage=storage,
        mapping=mapping,
        reveal=True,
    )
    if not _same_hash(preview.preview_sha256, row.preview_sha256):
        raise MappingStoreError("preview_changed")
    counts = _counts(preview)
    if any(counts.get(code, 0) > 0 for code in _CRITICAL_ROW_CODES):
        raise MappingStoreError("critical_rows_unmapped")
    seen_keys = ignored_message_keys(mapping, preview)
    if sorted(acknowledged_ignored_keys) != list(seen_keys):
        raise MappingStoreError("ignored_keys_not_acknowledged")
    row = _fetch(session, context, project_id, mapping_id, for_update=True)
    if row.status != "proposed":
        raise MappingStoreError("mapping_not_proposed")
    if _is_superseded(session, context, row):
        raise MappingStoreError("mapping_superseded")
    instant = _instant(now)
    row.status = "approved"
    row.approved_purpose = purpose
    row.approved_by = actor.actor_id
    row.approver_role = actor.role
    row.approved_at = instant
    row.updated_at = instant
    row.ignored_keys_sha256 = (
        ignored_keys_sha256(seen_keys) if mapping.on_unmapped_message_keys == "ignore" else None
    )
    try:
        session.flush()
    except IntegrityError:
        raise MappingStoreError("approval_refused") from None
    _audit(session, context, actor, "dataset_mapping.approved", row)
    session.flush()
    return row


def reject_dataset_mapping(
    session: Session,
    context: TenantContext,
    *,
    project_id: uuid.UUID,
    mapping_id: uuid.UUID,
    actor: MappingActor,
    now: datetime | None = None,
) -> DatasetMapping:
    """proposed -> rejected. Nothing else changes; the next proposal is a new version."""
    _require_actor(actor, roles=ADMIN_ROLES, code="approval_not_permitted")
    row = _fetch(session, context, project_id, mapping_id, for_update=True)
    if row.status != "proposed":
        raise MappingStoreError("mapping_not_proposed")
    row.status = "rejected"
    row.updated_at = _instant(now)
    session.flush()
    _audit(session, context, actor, "dataset_mapping.rejected", row)
    session.flush()
    return row


# ------------------------------------------------------------------ resolve


def _approved_for(
    session: Session, context: TenantContext, dataset: Dataset, *, earlier: bool
) -> DatasetMapping | None:
    statement = select(DatasetMapping).where(
        DatasetMapping.project_id == dataset.project_id, DatasetMapping.status == "approved"
    )
    if earlier:
        statement = statement.where(
            DatasetMapping.dataset_name == dataset.name,
            DatasetMapping.dataset_purpose == dataset.purpose,
            DatasetMapping.dataset_version < dataset.version,
        ).order_by(DatasetMapping.dataset_version.desc(), DatasetMapping.mapping_version.desc())
    else:
        statement = statement.where(DatasetMapping.dataset_id == dataset.id).order_by(
            DatasetMapping.mapping_version.desc()
        )
    return execute_tenant_scoped(session, context.organization_id, statement).scalars().first()


def _verified_mapping(row: DatasetMapping) -> ImportMapping:
    mapping = _parse_document(row.mapping_document)
    if not _same_hash(mapping.mapping_sha256, row.mapping_sha256):
        raise MappingStoreError("mapping_tampered")
    return mapping


def resolve_approved_mapping(
    session: Session,
    context: TenantContext,
    *,
    project_id: uuid.UUID,
    dataset_id: uuid.UUID,
    purpose: str,
    storage: DatasetObjectStorage,
    include_held_out: bool,
) -> ResolvedMapping:
    """The mapping a later import run of `dataset_id` for `purpose` may use. The grant is
    re-authorized now (Section 67.4). The dataset's own latest approved mapping wins; otherwise an
    earlier version's is reused only when it is tabular and, on a live re-probe, its header hash and
    the tool definitions are unchanged and no critical row is unmapped; else
    `mapping_reapproval_required`. No approved mapping at all is `mapping_not_found`."""
    _require_ids(project_id, dataset_id)
    _clean(purpose, 32)
    if type(include_held_out) is not bool:
        raise MappingStoreError("invalid_request")
    dataset, _connection_id, _snapshot, importer = _authorized_dataset(
        session,
        context,
        project_id=project_id,
        dataset_id=dataset_id,
        include_held_out=include_held_out,
        purpose=purpose,
    )
    if purpose != dataset.purpose:
        raise MappingStoreError("purpose_mismatch")
    own = _approved_for(session, context, dataset, earlier=False)
    if own is not None:
        return ResolvedMapping(
            _verified_mapping(own),
            own.id,
            own.mapping_version,
            own.dataset_id,
            str(own.approved_purpose),
            reused=False,
        )
    prior = _approved_for(session, context, dataset, earlier=True)
    if prior is None:
        raise MappingStoreError("mapping_not_found")
    mapping = _verified_mapping(prior)
    if (prior.importer, importer) not in _REUSABLE_PAIRS:
        raise MappingStoreError("mapping_reapproval_required")
    if prior.approved_purpose != purpose:
        raise MappingStoreError("mapping_reapproval_required")
    try:
        preview = _finished_preview(
            session,
            context,
            project_id=project_id,
            dataset_id=dataset_id,
            include_held_out=include_held_out,
            storage=storage,
            mapping=mapping,
            reveal=False,
        )
    except MappingStoreError as exc:
        # A prior mapping that cannot even finish a preview over the new bytes (a header it
        # names is gone) needs a fresh approval; an outage or a changed object stays visible.
        if exc.code == "preview_incomplete":
            raise MappingStoreError("mapping_reapproval_required") from None
        raise
    counts = _counts(preview)
    if (
        not _same_hash(preview.source_schema_sha256, prior.source_schema_sha256)
        or not _same_hash(preview.tool_definitions_sha256, prior.tool_definitions_sha256)
        or any(counts.get(code, 0) > 0 for code in _CRITICAL_ROW_CODES)
    ):
        raise MappingStoreError("mapping_reapproval_required")
    return ResolvedMapping(
        mapping,
        prior.id,
        prior.mapping_version,
        prior.dataset_id,
        str(prior.approved_purpose),
        reused=True,
    )
