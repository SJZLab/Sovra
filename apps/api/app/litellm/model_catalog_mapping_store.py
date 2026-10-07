"""Model catalog entry + explicit training-artifact mapping store (FTF-0308-P1,
Section 2.2/3.3/6.6/6.24/39/65/77/81).

The persistence half FTF-0308's UI needs ("UI maps explicit accessible alias
to verified trainable artifact"; "changing mapping invalidates related
qualification; no guessed weight identity from LiteLLM alias"). Tables and
grants are in `0022_model_catalog_entries.py`.

Rules this module enforces:

- A mapping exists only because a caller explicitly named a trainable
  artifact for one (connection, public model name). Nothing here derives an
  artifact from the alias text.
- The target artifact must belong to the caller's organization (a foreign or
  missing id is the same `ArtifactNotFoundError`) and be `ready`.
- `catalog_version` is the optimistic-concurrency and invalidation token. A
  first mapping creates the entry at version 1 (previous version 0); every
  later change bumps it by exactly one, inserts one append-only change row
  and writes one audit event, all in the caller's transaction. Setting the
  artifact the entry already has is a no-op: no write, no version bump, no
  change row, no audit event.
- `require_current_mapping` is the hook a qualification calls with the
  version it observed; a later mapping change makes it raise
  `MappingChangedError`, so evidence bound to the old mapping is not reused.

This module never calls the gateway: whether the alias is currently
accessible is decided by the route from a fresh discovery.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import DateTime, Integer, String, select
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Mapped, Session, mapped_column

from app.artifacts.artifact_crud import get_artifact
from app.auth.audit_event_writer import write_audit_event
from app.auth.organization_workspace_schema import Base
from app.auth.tenant_isolation_query_guards import execute_tenant_scoped
from app.db.tenant_context import TenantContext

__all__ = [
    "PUBLIC_MODEL_NAME_MAX_LENGTH",
    "ArtifactNotReadyError",
    "CatalogVersionConflictError",
    "MappingActor",
    "MappingChangedError",
    "MappingOutcome",
    "ModelCatalogEntryNotFoundError",
    "ModelCatalogEntryRecord",
    "ModelCatalogMappingChangeRecord",
    "get_entry",
    "list_mapping_changes",
    "require_current_mapping",
    "set_training_artifact_mapping",
]

PUBLIC_MODEL_NAME_MAX_LENGTH = 256
MAPPING_CHANGE_AUDIT_ACTION = "model_catalog_entry.training_artifact_mapping_change"
_AUDIT_RESOURCE_TYPE = "model_catalog_entry"


class ModelCatalogEntryNotFoundError(LookupError):
    """No catalog entry with this id exists in the caller's own organization
    (identical for missing and foreign)."""


class MappingChangedError(RuntimeError):
    """The entry's mapping changed after the caller observed
    `observed_catalog_version`; anything bound to the old mapping is stale."""

    def __init__(self, entry_id: uuid.UUID, observed: int, current: int) -> None:
        super().__init__(
            f"model catalog entry {entry_id} mapping changed: observed version {observed}, "
            f"current version {current}"
        )
        self.entry_id = entry_id
        self.observed_catalog_version = observed
        self.current_catalog_version = current


class CatalogVersionConflictError(RuntimeError):
    """`expected_catalog_version` did not match the stored version (or a
    concurrent first mapping won the unique key)."""


class ArtifactNotReadyError(ValueError):
    """The named trainable artifact exists but is not `ready`."""


@dataclass(frozen=True)
class MappingActor:
    """`actor_id` is the authenticated principal's own `subject`, never a
    client-supplied string."""

    actor_type: str
    actor_id: str
    request_id: str


class ModelCatalogEntryRecord(Base):
    __tablename__ = "model_catalog_entries"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    organization_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    litellm_connection_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    public_model_name: Mapped[str] = mapped_column(
        String(PUBLIC_MODEL_NAME_MAX_LENGTH), nullable=False
    )
    trainable_artifact_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    catalog_version: Mapped[int] = mapped_column(Integer, nullable=False)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_verified_accessible_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    mapped_by: Mapped[str | None] = mapped_column(String(200), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ModelCatalogMappingChangeRecord(Base):
    """Append-only: the restricted role has SELECT/INSERT only."""

    __tablename__ = "model_catalog_mapping_changes"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    organization_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    model_catalog_entry_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    previous_trainable_artifact_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    new_trainable_artifact_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    previous_catalog_version: Mapped[int] = mapped_column(Integer, nullable=False)
    new_catalog_version: Mapped[int] = mapped_column(Integer, nullable=False)
    actor: Mapped[str] = mapped_column(String(200), nullable=False)
    request_id: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


@dataclass(frozen=True)
class MappingOutcome:
    entry: ModelCatalogEntryRecord | None
    changed: bool


def get_entry(
    session: Session,
    context: TenantContext,
    *,
    connection_id: uuid.UUID,
    public_model_name: str,
    for_update: bool = False,
) -> ModelCatalogEntryRecord | None:
    statement = select(ModelCatalogEntryRecord).where(
        ModelCatalogEntryRecord.litellm_connection_id == connection_id,
        ModelCatalogEntryRecord.public_model_name == public_model_name,
    )
    if for_update:
        statement = statement.with_for_update()
    result = execute_tenant_scoped(session, context.organization_id, statement)
    entry: ModelCatalogEntryRecord | None = result.scalars().one_or_none()
    return entry


def _require_ready_artifact(
    session: Session, context: TenantContext, artifact_id: uuid.UUID
) -> None:
    artifact = get_artifact(session, context, artifact_id)  # ArtifactNotFoundError if foreign
    if artifact.status != "ready":
        raise ArtifactNotReadyError(f"artifact {artifact_id} is {artifact.status}, not ready")


def set_training_artifact_mapping(
    session: Session,
    context: TenantContext,
    *,
    connection_id: uuid.UUID,
    public_model_name: str,
    trainable_artifact_id: uuid.UUID | None,
    expected_catalog_version: int | None,
    actor: MappingActor,
) -> MappingOutcome:
    """Explicitly maps (or, with `None`, unmaps) one alias. Raises
    `ArtifactNotFoundError`, `ArtifactNotReadyError` or
    `CatalogVersionConflictError`; on any raise nothing was written. The
    caller commits (the tenant-scoped session does) so the entry update,
    change row and audit event are one transaction."""
    if not isinstance(public_model_name, str) or not (
        0 < len(public_model_name) <= PUBLIC_MODEL_NAME_MAX_LENGTH
    ):
        raise ValueError("public_model_name must be 1..256 characters")
    if trainable_artifact_id is not None and not isinstance(trainable_artifact_id, uuid.UUID):
        raise ValueError("trainable_artifact_id must be a uuid.UUID or None")

    entry = get_entry(
        session,
        context,
        connection_id=connection_id,
        public_model_name=public_model_name,
        for_update=True,
    )
    stored_version = 0 if entry is None else entry.catalog_version
    if expected_catalog_version is not None and expected_catalog_version != stored_version:
        raise CatalogVersionConflictError(
            f"expected catalog_version {expected_catalog_version}, stored {stored_version}"
        )
    current_artifact = None if entry is None else entry.trainable_artifact_id
    if current_artifact == trainable_artifact_id:
        return MappingOutcome(entry=entry, changed=False)

    if trainable_artifact_id is not None:
        _require_ready_artifact(session, context, trainable_artifact_id)

    now = datetime.now(UTC)
    mapped_by = actor.actor_id if trainable_artifact_id is not None else None
    if entry is None:
        entry = ModelCatalogEntryRecord(
            id=uuid.uuid4(),
            organization_id=context.organization_id,
            litellm_connection_id=connection_id,
            public_model_name=public_model_name,
            trainable_artifact_id=trainable_artifact_id,
            catalog_version=1,
            first_seen_at=now,
            last_verified_accessible_at=now,
            mapped_by=mapped_by,
            created_at=now,
            updated_at=now,
        )
        session.add(entry)
    else:
        entry.trainable_artifact_id = trainable_artifact_id
        entry.catalog_version = stored_version + 1
        entry.last_verified_accessible_at = now
        entry.mapped_by = mapped_by
        entry.updated_at = now
    try:
        session.flush()
    except IntegrityError as exc:
        # Two first mappings of the same alias raced; the loser must not
        # surface a database error or write a second entry.
        raise CatalogVersionConflictError("concurrent first mapping of this alias") from exc

    session.add(
        ModelCatalogMappingChangeRecord(
            id=uuid.uuid4(),
            organization_id=context.organization_id,
            model_catalog_entry_id=entry.id,
            previous_trainable_artifact_id=current_artifact,
            new_trainable_artifact_id=trainable_artifact_id,
            previous_catalog_version=stored_version,
            new_catalog_version=entry.catalog_version,
            actor=actor.actor_id,
            request_id=actor.request_id,
            created_at=now,
        )
    )
    write_audit_event(
        session,
        organization_id=context.organization_id,
        actor_type=actor.actor_type,
        actor_id=actor.actor_id,
        action=MAPPING_CHANGE_AUDIT_ACTION,
        resource_type=_AUDIT_RESOURCE_TYPE,
        resource_id=str(entry.id),
        request_id=actor.request_id,
        details={
            "litellm_connection_id": str(connection_id),
            "previous_trainable_artifact_id": (
                None if current_artifact is None else str(current_artifact)
            ),
            "new_trainable_artifact_id": (
                None if trainable_artifact_id is None else str(trainable_artifact_id)
            ),
            "previous_catalog_version": stored_version,
            "new_catalog_version": entry.catalog_version,
        },
    )
    session.flush()
    return MappingOutcome(entry=entry, changed=True)


def require_current_mapping(
    session: Session, context: TenantContext, entry_id: uuid.UUID, catalog_version: int
) -> ModelCatalogEntryRecord:
    """Returns the entry only if its version still equals `catalog_version`;
    otherwise raises `MappingChangedError`. A foreign or missing entry is
    `ModelCatalogEntryNotFoundError`."""
    if not isinstance(entry_id, uuid.UUID):
        raise ModelCatalogEntryNotFoundError(f"entry_id must be a uuid.UUID, got {entry_id!r}")
    statement = select(ModelCatalogEntryRecord).where(ModelCatalogEntryRecord.id == entry_id)
    result = execute_tenant_scoped(session, context.organization_id, statement)
    entry: ModelCatalogEntryRecord | None = result.scalars().one_or_none()
    if entry is None:
        raise ModelCatalogEntryNotFoundError(f"model catalog entry {entry_id} not found")
    if entry.catalog_version != catalog_version:
        raise MappingChangedError(entry_id, catalog_version, entry.catalog_version)
    return entry


def list_mapping_changes(
    session: Session, context: TenantContext, entry_id: uuid.UUID, *, limit: int
) -> list[ModelCatalogMappingChangeRecord]:
    """Newest first. `limit` must be 1..100."""
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
        raise ValueError("limit must be an integer between 1 and 100")
    statement = (
        select(ModelCatalogMappingChangeRecord)
        .where(ModelCatalogMappingChangeRecord.model_catalog_entry_id == entry_id)
        .order_by(
            ModelCatalogMappingChangeRecord.new_catalog_version.desc(),
            ModelCatalogMappingChangeRecord.created_at.desc(),
        )
        .limit(limit)
    )
    result = execute_tenant_scoped(session, context.organization_id, statement)
    return list(result.scalars().all())
