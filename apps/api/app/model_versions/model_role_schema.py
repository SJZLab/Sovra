"""ModelRoleAssignment domain schema (FTF-H010-P4; Section 0.12 roles, Section 27).

A ModelVersion is immutable (FTF-1001), so the role a version plays in a project -- the
`current_production` incumbent, a `baseline`, a `candidate`, a `teacher` or a `judge` -- is a
separate, revocable row. Table, grants, triggers and indexes are in
`0036_model_role_assignments.py`: `sovra_app` gets `SELECT, INSERT` plus a column-limited
`UPDATE (revoked_at, revoked_by, revoke_reason_code)`; a trigger accepts only the one-time
NULL -> set revocation.

The role enum is exact: no case folding, trimming or Unicode normalisation is applied to either
side, so `Judge`, `judge ` and a full-width look-alike are refused (`invalid_role`).
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime

from sqlalchemy import DateTime, String, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.auth.organization_workspace_schema import Base

__all__ = [
    "ADMIN_ONLY_ROLES",
    "IDEMPOTENCY_KEY_MAX_LENGTH",
    "ROLES",
    "ModelRoleAssignment",
    "ModelRoleValidationError",
    "request_sha256",
    "validate_idempotency_key",
    "validate_role",
]

ROLE_CURRENT_PRODUCTION = "current_production"
ROLE_JUDGE = "judge"
ROLES = (ROLE_CURRENT_PRODUCTION, "baseline", "candidate", "teacher", ROLE_JUDGE)
# Section 0.12 / 45: the incumbent and the judge are governance decisions, not ML-engineer ones.
ADMIN_ONLY_ROLES = frozenset({ROLE_CURRENT_PRODUCTION, ROLE_JUDGE})
IDEMPOTENCY_KEY_MAX_LENGTH = 128
REVOKE_REASON_BY_ACTOR = "revoked_by_actor"


class ModelRoleValidationError(ValueError):
    """A request value violates this schema's own shape. `code` is `invalid_body` or
    `invalid_role`; the text carries the code only, never the offending value."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def validate_role(value: object) -> str:
    # `tuple.index` compares by equality inside the C implementation, so there is no `==`/`in`
    # node here for a normalisation-bypass mutation to land on; a non-str raises the same way.
    try:
        return ROLES[ROLES.index(value)]
    except ValueError:
        raise ModelRoleValidationError("invalid_role") from None


def validate_idempotency_key(value: object) -> str:
    if type(value) is not str or not 1 <= len(value) <= IDEMPOTENCY_KEY_MAX_LENGTH:
        raise ModelRoleValidationError("invalid_body")
    if any(not 0x20 <= ord(ch) <= 0x7E for ch in value):
        raise ModelRoleValidationError("invalid_body")
    return value


def request_sha256(project_id: uuid.UUID, model_version_id: uuid.UUID, role: str) -> str:
    """Canonical digest of what an assignment request asks for. The idempotency key is not part
    of it: the same key with a different digest is a reuse (409), the same digest a replay."""
    document = {
        "model_version_id": str(model_version_id),
        "project_id": str(project_id),
        "role": role,
    }
    canonical = json.dumps(document, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class ModelRoleAssignment(Base):
    """One assignment of a role to a model version (revoked, never edited or deleted)."""

    __tablename__ = "model_role_assignments"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    project_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    model_version_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    role: Mapped[str] = mapped_column(String(32), nullable=False)
    assigned_by: Mapped[str] = mapped_column(String(200), nullable=False)
    assigned_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    request_id: Mapped[str] = mapped_column(String(128), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    request_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_by: Mapped[str | None] = mapped_column(String(200), nullable=True)
    revoke_reason_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
