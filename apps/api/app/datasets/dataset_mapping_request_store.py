"""Idempotent dataset mapping requests (FTF-H011-P3; FINE_TUNING_FACTORY_RELEASE_1.md Section 7A.10,
Section 8.1, Section 9, Section 67.1-67.4; AF-15).

The routes in `dataset_mapping_routes.py` need two things the P2 store (`dataset_mapping_store.py`)
has no column for: the `Idempotency-Key` of a propose / approve / reject request together with a
digest of that request, and the reviewer's rationale for an approval or a rejection. Both live in
`dataset_mapping_requests` (migration `0036_dataset_mapping_requests.py`), written in the same
transaction as the proposal, approval or rejection they record, so a key exists exactly when its
request took effect.

- `request_digest` is the digest of the request (action, dataset, mapping, principal, body), never
  of the key. The same key with the same digest replays the stored outcome; the same key with any
  other digest is `idempotency_key_reused`. A key that is already stored (a concurrent identical
  request got there first) raises `idempotency_key_reused` and writes nothing.
- The rationale is stored here and nowhere else; it is not copied into an audit row.
- `find_mapping_request` is the read side: by key (replay) or by mapping and action (a request that
  already took effect under another key).

Every refusal is a `MappingRequestError` with a stable `code`.
"""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Final

from sqlalchemy import DateTime, ForeignKeyConstraint, String, Text, UniqueConstraint, select
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Mapped, Session, mapped_column

from app.auth.organization_workspace_schema import Base
from app.auth.tenant_isolation_query_guards import execute_tenant_scoped
from app.db.tenant_context import TenantContext

__all__ = [
    "ACTIONS",
    "DatasetMappingRequest",
    "MappingRequestError",
    "StoredMappingRequest",
    "find_mapping_request",
    "find_mapping_request_by_action",
    "record_mapping_request",
    "request_digest",
]

# Whether the action carries a reviewer rationale: a proposal has none, a decision has one.
_RATIONALE_REQUIRED: Final = {"propose": False, "approve": True, "reject": True}
ACTIONS: Final = frozenset(_RATIONALE_REQUIRED)
MAX_RATIONALE: Final = 2000
_KEY_PATTERN: Final = re.compile(r"[A-Za-z0-9._:-]{8,200}")
_HEX64: Final = re.compile(r"[0-9a-f]{64}")
CODES: Final = frozenset({"invalid_request", "idempotency_key_reused"})


class MappingRequestError(Exception):
    """A refusal with a stable `code`. Nothing was written when it was raised."""

    def __init__(self, code: str) -> None:
        assert code in CODES, code
        super().__init__(code)
        self.code = code


class DatasetMappingRequest(Base):
    """One request that took effect. Insert-only: `sovra_app` has no UPDATE or DELETE grant."""

    __tablename__ = "dataset_mapping_requests"
    __table_args__ = (
        ForeignKeyConstraint(
            ["organization_id", "mapping_id"],
            ["dataset_mappings.organization_id", "dataset_mappings.id"],
            name="fk_dataset_mapping_requests_mapping",
        ),
        UniqueConstraint(
            "organization_id",
            "project_id",
            "idempotency_key",
            name="uq_dataset_mapping_requests_key",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    organization_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    project_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    mapping_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    action: Mapped[str] = mapped_column(String(16), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(200), nullable=False)
    request_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    rationale: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[str] = mapped_column(String(200), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


@dataclass(frozen=True)
class StoredMappingRequest:
    mapping_id: uuid.UUID
    action: str
    request_digest: str
    rationale: str | None
    created_by: str


def request_digest(
    action: str,
    *,
    dataset_id: uuid.UUID,
    mapping_id: uuid.UUID | None,
    subject: str,
    body: object,
) -> str:
    """The digest of a request: the action, the dataset, the mapping (None for a proposal), the
    authenticated principal and the body, in canonical JSON. The key is not part of it."""
    if action not in ACTIONS:
        raise MappingRequestError("invalid_request")
    text = json.dumps(
        {
            "action": action,
            "dataset_id": str(dataset_id),
            "mapping_id": None if mapping_id is None else str(mapping_id),
            "subject": subject,
            "body": body,
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )
    return hashlib.sha256(text.encode("ascii")).hexdigest()


def _stored(row: DatasetMappingRequest) -> StoredMappingRequest:
    return StoredMappingRequest(
        row.mapping_id, row.action, row.request_digest, row.rationale, row.created_by
    )


def find_mapping_request(
    session: Session,
    context: TenantContext,
    *,
    project_id: uuid.UUID,
    idempotency_key: str,
) -> StoredMappingRequest | None:
    """The request stored under this key in this project, or None. Read-only."""
    row = (
        execute_tenant_scoped(
            session,
            context.organization_id,
            select(DatasetMappingRequest).where(
                DatasetMappingRequest.project_id == project_id,
                DatasetMappingRequest.idempotency_key == idempotency_key,
            ),
        )
        .scalars()
        .first()
    )
    return None if row is None else _stored(row)


def find_mapping_request_by_action(
    session: Session,
    context: TenantContext,
    *,
    project_id: uuid.UUID,
    mapping_id: uuid.UUID,
    action: str,
) -> StoredMappingRequest | None:
    """The earliest request of this action that took effect on this mapping, or None. Read-only."""
    row = (
        execute_tenant_scoped(
            session,
            context.organization_id,
            select(DatasetMappingRequest)
            .where(
                DatasetMappingRequest.project_id == project_id,
                DatasetMappingRequest.mapping_id == mapping_id,
                DatasetMappingRequest.action == action,
            )
            .order_by(DatasetMappingRequest.created_at, DatasetMappingRequest.id),
        )
        .scalars()
        .first()
    )
    return None if row is None else _stored(row)


def record_mapping_request(
    session: Session,
    context: TenantContext,
    *,
    project_id: uuid.UUID,
    mapping_id: uuid.UUID,
    action: str,
    idempotency_key: str,
    digest: str,
    rationale: str | None,
    created_by: str,
    now: datetime | None = None,
) -> None:
    """Stores the key with the request digest (and the rationale of an approval or a rejection).
    Call it in the transaction that proposed, approved or rejected the mapping. A key already stored
    raises `idempotency_key_reused`; nothing is written."""
    if (
        action not in ACTIONS
        or _KEY_PATTERN.fullmatch(idempotency_key) is None
        or _HEX64.fullmatch(digest) is None
        or type(created_by) is not str
        or not created_by
    ):
        raise MappingRequestError("invalid_request")
    if not _RATIONALE_REQUIRED[action]:
        if rationale is not None:
            raise MappingRequestError("invalid_request")
    elif type(rationale) is not str or not 1 <= len(rationale) <= MAX_RATIONALE:
        raise MappingRequestError("invalid_request")
    session.add(
        DatasetMappingRequest(
            id=uuid.uuid4(),
            organization_id=context.organization_id,
            project_id=project_id,
            mapping_id=mapping_id,
            action=action,
            idempotency_key=idempotency_key,
            request_digest=digest,
            rationale=rationale,
            created_by=created_by,
            created_at=now or datetime.now(UTC),
        )
    )
    try:
        session.flush()
    except IntegrityError:
        raise MappingRequestError("idempotency_key_reused") from None
