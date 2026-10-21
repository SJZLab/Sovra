"""Dataset version + upload-session store (FTF-0501-P1, Section 6.10/7A.10/
8.1/9/65.2/67.3/67.5/69.1/39).

The persistence half FTF-0501's presigned-multipart and finalize work needs
("cannot create accepted Dataset version" without a verified upload). Tables,
constraints and grants are in `0024_dataset_uploads.py`; the database itself
refuses a dataset on an open/rejected/expired session, a verified session
whose verified values differ from the declared ones, and any change to a
session a dataset references. This module adds the lifecycle around them.

Rules this module enforces:

- An upload session is only ever created from an *active* `upload` source
  connection that already authorizes the requested purpose
  (`authorize_dataset_source_purpose`, FTF-0500): a revoked connection, a
  non-upload connection or an ungranted purpose creates nothing (Law 16).
- The session's object key is derived from its own ids, never from a caller
  string, and its lifetime is bounded (<= 24 h, Section 67.5).
- `record_upload_verified_and_create_dataset` is the only way a Dataset
  version is created. It refuses, writing nothing, when the session is not
  open, is past `expires_at`, the verified size / sha256 / content type differ
  from the declared ones, or the source connection no longer authorizes the
  purpose (revocation propagates). On success the session is marked verified
  and the next immutable version of `(project, name)` is inserted in one
  transaction, with one audit event. On a mismatch the caller records the
  rejection with `record_upload_rejected` (a separate transaction, so a
  rolled-back finalize cannot erase the rejection).
- `record_upload_rejected` and `expire_open_sessions` only ever move an
  `open` session; a verified session is never rewritten.
- Reads hide `held_out_evaluation` datasets from callers that may not see
  them (Section 9/69.1): they are not-found, identical to a missing id.
  Nothing here ever returns the object key.

This module never calls S3, never presigns and never reads object bytes.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TypeGuard

from sqlalchemy import BigInteger, DateTime, Integer, String, func, select
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Mapped, Session, mapped_column

from app.auth.audit_event_writer import write_audit_event
from app.auth.organization_workspace_schema import Base
from app.auth.tenant_isolation_query_guards import execute_tenant_scoped
from app.datasets.dataset_source_connection_crud import (
    ProjectNotFoundError,
    _authorize_project_or_raise,
    authorize_dataset_source_purpose,
)
from app.datasets.dataset_source_connection_schema import ALLOWED_PURPOSES
from app.datasets.direct_object_storage_upload import encodings_for
from app.db.tenant_context import TenantContext

__all__ = [
    "DATASET_NAME_MAX_LENGTH",
    "HELD_OUT_PURPOSE",
    "MAX_PAGE_SIZE",
    "MAX_UPLOAD_BYTES",
    "MAX_UPLOAD_TTL",
    "Dataset",
    "DatasetNotFoundError",
    "DatasetUploadSession",
    "DatasetVersionConflictError",
    "NotAnUploadSourceError",
    "ProjectNotFoundError",
    "UploadActor",
    "UploadExpiredError",
    "UploadSessionNotFoundError",
    "UploadStateError",
    "UploadVerificationError",
    "create_upload_session",
    "expire_open_sessions",
    "get_dataset",
    "get_upload_session",
    "list_datasets",
    "record_upload_rejected",
    "record_upload_verified_and_create_dataset",
]

DATASET_NAME_MAX_LENGTH = 200
CONTENT_TYPE_MAX_LENGTH = 255
REJECTION_REASON_MAX_LENGTH = 64
MAX_UPLOAD_BYTES = 10 * 1024**3  # 10 GiB, Section 8.1.
MAX_UPLOAD_TTL = timedelta(hours=24)  # Section 67.5.
HELD_OUT_PURPOSE = "held_out_evaluation"
_HELD_OUT_PURPOSES = frozenset({HELD_OUT_PURPOSE})
MAX_PAGE_SIZE = 200

_SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")
_REASON_PATTERN = re.compile(r"[a-z][a-z0-9_]*")

_RESOURCE_SESSION = "dataset_upload_session"
_RESOURCE_DATASET = "dataset"
_ACTION_SESSION_CREATE = "dataset_upload_session.create"
_ACTION_SESSION_REJECT = "dataset_upload_session.reject"
_ACTION_SESSION_EXPIRE = "dataset_upload_session.expire"
_ACTION_DATASET_CREATE = "dataset.create"


class UploadSessionNotFoundError(LookupError):
    """No upload session with this id exists in the caller's own organization
    and project (identical for missing, foreign and other-project ids)."""


class DatasetNotFoundError(LookupError):
    """No dataset with this id is visible to the caller (identical for
    missing, foreign, other-project and a held-out dataset the caller may not
    see)."""


class NotAnUploadSourceError(ValueError):
    """The named source connection is not an `upload` connection."""


class UploadStateError(ValueError):
    """The session is not `open`, so this transition is not allowed."""


class UploadExpiredError(ValueError):
    """The session's `expires_at` has passed; nothing was written."""


class UploadVerificationError(ValueError):
    """The verified size / sha256 / content type differ from the declared
    ones; nothing was written. `mismatched` names the differing fields."""

    def __init__(self, mismatched: tuple[str, ...]) -> None:
        super().__init__(f"upload verification mismatch: {', '.join(mismatched)}")
        self.mismatched = mismatched


class DatasetVersionConflictError(RuntimeError):
    """A concurrent finalize created the same `(project, name, version)`."""


@dataclass(frozen=True)
class UploadActor:
    """`actor_id` is the authenticated principal's own `subject` (or a fixed
    system identifier), never a client-supplied string."""

    actor_type: str
    actor_id: str
    request_id: str


class DatasetUploadSession(Base):
    __tablename__ = "dataset_upload_sessions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    organization_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    project_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    dataset_source_connection_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False
    )
    purpose: Mapped[str] = mapped_column(String(32), nullable=False)
    object_key: Mapped[str] = mapped_column(String(512), nullable=False)
    declared_size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    max_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    declared_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    declared_content_type: Mapped[str] = mapped_column(String(255), nullable=False)
    declared_encoding: Mapped[str] = mapped_column(
        String(16), nullable=False, default="utf-8", server_default="utf-8"
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    verified_size_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    verified_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    verified_content_type: Mapped[str | None] = mapped_column(String(255), nullable=True)
    rejection_reason: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_by: Mapped[str] = mapped_column(String(200), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finalized_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class Dataset(Base):
    """One immutable dataset version (Section 7A.10). The restricted role has
    SELECT/INSERT only."""

    __tablename__ = "datasets"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    organization_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    project_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    name: Mapped[str] = mapped_column(String(DATASET_NAME_MAX_LENGTH), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    purpose: Mapped[str] = mapped_column(String(32), nullable=False)
    upload_session_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    upload_status: Mapped[str] = mapped_column(String(16), nullable=False)
    verified_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    verified_size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    created_by: Mapped[str] = mapped_column(String(200), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


def _now() -> datetime:
    return datetime.now(UTC)


def _require_int(value: object, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{name} must be an integer")
    return value


def _require_sha256(value: object, name: str) -> str:
    if not isinstance(value, str) or _SHA256_PATTERN.fullmatch(value) is None:
        raise ValueError(f"{name} must be 64 lowercase hex characters")
    return value


def _is_clean_text(value: object, max_length: int) -> TypeGuard[str]:
    """A non-blank string with no leading or trailing whitespace."""
    if not isinstance(value, str):
        return False
    stripped = value.strip()
    return bool(stripped) and stripped == value and len(value) <= max_length


def _require_content_type(value: object, name: str) -> str:
    if not _is_clean_text(value, CONTENT_TYPE_MAX_LENGTH):
        raise ValueError(f"{name} must be a non-blank string of at most 255 characters")
    return value


def _require_now(now: datetime | None) -> datetime:
    if now is None:
        return _now()
    if not isinstance(now, datetime) or now.tzinfo is None:
        raise ValueError("now must be a timezone-aware datetime")
    return now


def _fetch_session(
    session: Session,
    context: TenantContext,
    project_id: uuid.UUID,
    upload_session_id: uuid.UUID,
    *,
    for_update: bool,
) -> DatasetUploadSession:
    if not isinstance(upload_session_id, uuid.UUID) or not isinstance(project_id, uuid.UUID):
        raise UploadSessionNotFoundError("project_id and upload_session_id must be uuid.UUID")
    statement = select(DatasetUploadSession).where(
        DatasetUploadSession.id == upload_session_id,
        DatasetUploadSession.project_id == project_id,
    )
    if for_update:
        statement = statement.with_for_update()
    row = execute_tenant_scoped(session, context.organization_id, statement).scalars().one_or_none()
    if row is None:
        raise UploadSessionNotFoundError(f"upload session {upload_session_id} not found")
    return row


def get_upload_session(
    session: Session,
    context: TenantContext,
    project_id: uuid.UUID,
    upload_session_id: uuid.UUID,
) -> DatasetUploadSession:
    return _fetch_session(session, context, project_id, upload_session_id, for_update=False)


def create_upload_session(
    session: Session,
    context: TenantContext,
    *,
    project_id: uuid.UUID,
    connection_id: uuid.UUID,
    purpose: str,
    declared_size_bytes: int,
    declared_sha256: str,
    declared_content_type: str,
    max_bytes: int,
    declared_encoding: str = "utf-8",
    ttl: timedelta = MAX_UPLOAD_TTL,
    now: datetime | None = None,
    actor: UploadActor,
) -> DatasetUploadSession:
    """Opens one upload session. Raises `ValueError` for malformed values,
    `DatasetSourceConnectionNotFoundError`/`DatasetSourceAuthorizationError`
    (FTF-0500) for a missing, foreign, revoked or not-granted connection, or
    `NotAnUploadSourceError`; on any raise nothing was written."""
    declared = _require_int(declared_size_bytes, "declared_size_bytes")
    maximum = _require_int(max_bytes, "max_bytes")
    if not 0 < declared <= maximum <= MAX_UPLOAD_BYTES:
        raise ValueError("require 0 < declared_size_bytes <= max_bytes <= 10 GiB")
    sha = _require_sha256(declared_sha256, "declared_sha256")
    content_type = _require_content_type(declared_content_type, "declared_content_type")
    if declared_encoding not in encodings_for(content_type):
        raise ValueError(f"declared_encoding must be one of {encodings_for(content_type)}")
    if not isinstance(ttl, timedelta) or not timedelta(0) < ttl <= MAX_UPLOAD_TTL:
        raise ValueError("ttl must be positive and at most 24 hours")
    if purpose not in ALLOWED_PURPOSES:
        raise ValueError(f"purpose must be one of {ALLOWED_PURPOSES}")
    created = _require_now(now)

    connection = authorize_dataset_source_purpose(
        session, context, project_id, connection_id, purpose
    )
    if connection.source_type != "upload":
        raise NotAnUploadSourceError(
            f"dataset source connection {connection_id} is a {connection.source_type!r} "
            "connection, not an upload connection"
        )

    session_id = uuid.uuid4()
    record = DatasetUploadSession(
        id=session_id,
        organization_id=context.organization_id,
        project_id=project_id,
        dataset_source_connection_id=connection_id,
        purpose=purpose,
        object_key=(
            f"tenants/{context.organization_id}/projects/{project_id}/uploads/{session_id}/source"
        ),
        declared_size_bytes=declared,
        max_bytes=maximum,
        declared_sha256=sha,
        declared_content_type=content_type,
        declared_encoding=declared_encoding,
        status="open",
        created_by=actor.actor_id,
        created_at=created,
        expires_at=created + ttl,
        updated_at=created,
    )
    session.add(record)
    write_audit_event(
        session,
        organization_id=context.organization_id,
        actor_type=actor.actor_type,
        actor_id=actor.actor_id,
        action=_ACTION_SESSION_CREATE,
        resource_type=_RESOURCE_SESSION,
        resource_id=str(session_id),
        request_id=actor.request_id,
        details={
            "project_id": str(project_id),
            "dataset_source_connection_id": str(connection_id),
            "purpose": purpose,
            "declared_size_bytes": declared,
        },
    )
    session.flush()
    return record


def record_upload_rejected(
    session: Session,
    context: TenantContext,
    *,
    project_id: uuid.UUID,
    upload_session_id: uuid.UUID,
    reason: str,
    now: datetime | None = None,
    actor: UploadActor,
) -> DatasetUploadSession:
    """Moves an `open` session to `rejected` with a stable reason code (never
    object content). Any other state raises `UploadStateError`."""
    if (
        not isinstance(reason, str)
        or len(reason) > REJECTION_REASON_MAX_LENGTH
        or _REASON_PATTERN.fullmatch(reason) is None
    ):
        raise ValueError("reason must be a stable lowercase code of at most 64 characters")
    closed = _require_now(now)
    record = _fetch_session(session, context, project_id, upload_session_id, for_update=True)
    if record.status != "open":
        raise UploadStateError(f"upload session {upload_session_id} is {record.status}, not open")
    record.status = "rejected"
    record.rejection_reason = reason
    record.finalized_at = closed
    record.updated_at = closed
    write_audit_event(
        session,
        organization_id=context.organization_id,
        actor_type=actor.actor_type,
        actor_id=actor.actor_id,
        action=_ACTION_SESSION_REJECT,
        resource_type=_RESOURCE_SESSION,
        resource_id=str(record.id),
        request_id=actor.request_id,
        details={"project_id": str(project_id), "reason": reason},
    )
    session.flush()
    return record


def record_upload_verified_and_create_dataset(
    session: Session,
    context: TenantContext,
    *,
    project_id: uuid.UUID,
    upload_session_id: uuid.UUID,
    dataset_name: str,
    verified_size_bytes: int,
    verified_sha256: str,
    verified_content_type: str,
    now: datetime | None = None,
    actor: UploadActor,
) -> Dataset:
    """The only way a Dataset version comes to exist. Raises
    `UploadSessionNotFoundError`, `UploadStateError` (not open),
    `UploadExpiredError`, `UploadVerificationError` (verified values differ
    from the declared ones), `DatasetSourceAuthorizationError` (the
    connection is revoked or no longer grants the purpose) or
    `DatasetVersionConflictError`; on any raise nothing was written."""
    if not _is_clean_text(dataset_name, DATASET_NAME_MAX_LENGTH):
        raise ValueError("dataset_name must be a non-blank string of at most 200 characters")
    size = _require_int(verified_size_bytes, "verified_size_bytes")
    sha = _require_sha256(verified_sha256, "verified_sha256")
    content_type = _require_content_type(verified_content_type, "verified_content_type")
    finalized = _require_now(now)

    record = _fetch_session(session, context, project_id, upload_session_id, for_update=True)
    if record.status != "open":
        raise UploadStateError(f"upload session {upload_session_id} is {record.status}, not open")
    if finalized > record.expires_at:
        raise UploadExpiredError(f"upload session {upload_session_id} expired")
    declared = {
        "size_bytes": record.declared_size_bytes,
        "sha256": record.declared_sha256,
        "content_type": record.declared_content_type,
    }
    verified = {"size_bytes": size, "sha256": sha, "content_type": content_type}
    mismatched = tuple(field for field in declared if verified[field] != declared[field])
    if mismatched:
        raise UploadVerificationError(mismatched)
    # Revocation propagates: a source revoked or narrowed after the session
    # opened authorizes nothing, so no version is created from it.
    authorize_dataset_source_purpose(
        session, context, project_id, record.dataset_source_connection_id, record.purpose
    )

    latest = execute_tenant_scoped(
        session,
        context.organization_id,
        select(func.max(Dataset.version)).where(
            Dataset.project_id == project_id, Dataset.name == dataset_name
        ),
    ).scalar_one()
    version = (latest or 0) + 1

    record.status = "verified"
    record.verified_size_bytes = size
    record.verified_sha256 = sha
    record.verified_content_type = content_type
    record.finalized_at = finalized
    record.updated_at = finalized
    # The session UPDATE must reach the database before the dataset INSERT: the
    # dataset's composite foreign key targets the session's `verified` status
    # and values, and SQLAlchemy's unit of work would otherwise INSERT first.
    session.flush()
    dataset = Dataset(
        id=uuid.uuid4(),
        organization_id=context.organization_id,
        project_id=project_id,
        name=dataset_name,
        version=version,
        purpose=record.purpose,
        upload_session_id=record.id,
        upload_status="verified",
        verified_sha256=sha,
        verified_size_bytes=size,
        created_by=actor.actor_id,
        created_at=finalized,
    )
    session.add(dataset)
    try:
        session.flush()
    except IntegrityError as exc:
        if "uq_datasets_project_name_version" not in str(exc.orig):
            raise
        raise DatasetVersionConflictError(
            f"dataset {dataset_name!r} version {version} already exists"
        ) from exc
    write_audit_event(
        session,
        organization_id=context.organization_id,
        actor_type=actor.actor_type,
        actor_id=actor.actor_id,
        action=_ACTION_DATASET_CREATE,
        resource_type=_RESOURCE_DATASET,
        resource_id=str(dataset.id),
        request_id=actor.request_id,
        details={
            "project_id": str(project_id),
            "upload_session_id": str(record.id),
            "version": version,
            "purpose": record.purpose,
        },
    )
    session.flush()
    return dataset


def expire_open_sessions(
    session: Session,
    context: TenantContext,
    *,
    now: datetime | None = None,
    actor: UploadActor,
) -> int:
    """Moves every `open` session of the caller's organization whose
    `expires_at <= now` to `expired`; returns how many. Verified and rejected
    sessions are never touched."""
    cutoff = _require_now(now)
    statement = (
        select(DatasetUploadSession)
        .where(DatasetUploadSession.status == "open", DatasetUploadSession.expires_at <= cutoff)
        .order_by(DatasetUploadSession.id)
        .with_for_update()
    )
    rows = list(execute_tenant_scoped(session, context.organization_id, statement).scalars())
    for row in rows:
        row.status = "expired"
        row.finalized_at = cutoff
        row.updated_at = cutoff
        write_audit_event(
            session,
            organization_id=context.organization_id,
            actor_type=actor.actor_type,
            actor_id=actor.actor_id,
            action=_ACTION_SESSION_EXPIRE,
            resource_type=_RESOURCE_SESSION,
            resource_id=str(row.id),
            request_id=actor.request_id,
            details={"project_id": str(row.project_id)},
        )
    session.flush()
    return len(rows)


def list_datasets(
    session: Session,
    context: TenantContext,
    *,
    project_id: uuid.UUID,
    include_held_out: bool,
    limit: int,
    after: uuid.UUID | None = None,
) -> list[Dataset]:
    """Ordered by id; `after` is the last id of the previous page. A caller
    with `include_held_out=False` never sees a `held_out_evaluation`
    dataset. `limit` must be 1..200. A foreign or missing project raises
    `ProjectNotFoundError`."""
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= MAX_PAGE_SIZE:
        raise ValueError("limit must be an integer between 1 and 200")
    if after is not None and not isinstance(after, uuid.UUID):
        raise ValueError("after must be a uuid.UUID or None")
    _authorize_project_or_raise(session, context, project_id)
    statement = select(Dataset).where(Dataset.project_id == project_id)
    if not include_held_out:
        statement = statement.where(Dataset.purpose.not_in(_HELD_OUT_PURPOSES))
    if after is not None:
        statement = statement.where(Dataset.id > after)
    statement = statement.order_by(Dataset.id).limit(limit)
    return list(execute_tenant_scoped(session, context.organization_id, statement).scalars())


def get_dataset(
    session: Session,
    context: TenantContext,
    *,
    project_id: uuid.UUID,
    dataset_id: uuid.UUID,
    include_held_out: bool,
) -> Dataset:
    if not isinstance(dataset_id, uuid.UUID) or not isinstance(project_id, uuid.UUID):
        raise DatasetNotFoundError("project_id and dataset_id must be uuid.UUID")
    statement = select(Dataset).where(Dataset.id == dataset_id, Dataset.project_id == project_id)
    row = execute_tenant_scoped(session, context.organization_id, statement).scalars().one_or_none()
    if row is None or (not include_held_out and row.purpose in _HELD_OUT_PURPOSES):
        raise DatasetNotFoundError(f"dataset {dataset_id} not found")
    return row
