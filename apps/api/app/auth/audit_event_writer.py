"""Audit event writer (FTF-0104, Section 6.24/45-47/65).

An append-only, tenant-scoped record of every committed sensitive
mutation. `write_audit_event` only `session.add()`s the row -- it never
calls `session.commit()` itself, so a caller that writes a domain mutation
and an audit event in the *same* transaction gets both-or-neither: a
rollback (a failed transaction) discards the audit row along with the
mutation it would have described, so a failed transaction can never leave
behind a misleading "it succeeded" audit trail (this task's own
negative_acceptance clause). The real forward Alembic migration
(`apps/api/alembic/versions/0002_audit_event_writer.py`) FORCEs row-level
security scoped to the same `app.current_organization_id` tenant context
FTF-0102 established, and grants the restricted `sovra_app` role SELECT
and INSERT only -- never UPDATE or DELETE -- so "audit events are
append-only" (Section 6.24) is enforced by the database itself, not only
by this module's own API surface.
"""

from __future__ import annotations

import re
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, String, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, Session, mapped_column

from app.auth.organization_workspace_schema import Base

# Same short, safe-identifier shape FTF-0006's observability_foundation.py
# enforces on its own event/identity fields -- structurally incapable of
# carrying a raw token, cookie or signed-URL querystring (Section 46/47).
_SAFE_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")

ALLOWED_ACTOR_TYPES = ("user", "system", "worker")

# Section 46: "Logging sanitizer must redact: Authorization headers, API
# keys, secret refs..., signed URLs, cookies, tokens." Key names are
# matched case-insensitively; values are also pattern-matched below so a
# secret stored under an unexpected key name is still caught.
_REDACTED_KEYS = frozenset(
    {
        "authorization",
        "api_key",
        "apikey",
        "x-api-key",
        "secret",
        "secret_ref",
        "password",
        "token",
        "access_token",
        "refresh_token",
        "id_token",
        "cookie",
        "set-cookie",
        "signed_url",
    }
)
_REDACTED_VALUE_PATTERNS = (
    re.compile(r"^bearer\s+\S+$", re.IGNORECASE),
    # A JWT-shaped compact serialization (three base64url segments).
    re.compile(r"^[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{5,}$"),
)
REDACTED_PLACEHOLDER = "[REDACTED]"


class AuditEventValidationError(ValueError):
    """A field value violates the domain schema's own shape/invariant."""


def _validate_identifier(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not _SAFE_IDENTIFIER.match(value):
        raise AuditEventValidationError(
            f"{field_name} must be a short, safe identifier matching "
            f"{_SAFE_IDENTIFIER.pattern!r} (never raw prompt/token/URL content): {value!r}"
        )
    return value


def redact_details(payload: dict[str, Any]) -> dict[str, Any]:
    """Recursively redacts secret-shaped keys/values from a details payload.

    Redacts by key name (case-insensitive, against `_REDACTED_KEYS`) and,
    independently, by value shape (`_REDACTED_VALUE_PATTERNS`) so a secret
    stored under an unexpected key is still caught. Never mutates the
    input; returns a new structure.
    """

    def _redact(node: Any) -> Any:
        if isinstance(node, dict):
            out: dict[str, Any] = {}
            for key, value in node.items():
                if isinstance(key, str) and key.lower() in _REDACTED_KEYS:
                    out[key] = REDACTED_PLACEHOLDER
                else:
                    out[key] = _redact(value)
            return out
        if isinstance(node, list):
            return [_redact(item) for item in node]
        if isinstance(node, str):
            for pattern in _REDACTED_VALUE_PATTERNS:
                if pattern.match(node):
                    return REDACTED_PLACEHOLDER
            return node
        return node

    if not isinstance(payload, dict):
        raise AuditEventValidationError(
            f"details must be a JSON object, got {type(payload).__name__}"
        )
    return _redact(payload)


class AuditEvent(Base):
    """Section 6.24 — append-only, tenant-scoped audit trail."""

    __tablename__ = "audit_events"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
    )
    actor_type: Mapped[str] = mapped_column(String(32), nullable=False)
    actor_id: Mapped[str] = mapped_column(String(128), nullable=False)
    action: Mapped[str] = mapped_column(String(128), nullable=False)
    resource_type: Mapped[str] = mapped_column(String(128), nullable=False)
    resource_id: Mapped[str] = mapped_column(String(128), nullable=False)
    request_id: Mapped[str] = mapped_column(String(128), nullable=False)
    ip_hash: Mapped[str | None] = mapped_column(String(128), nullable=True)
    details_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )

    __table_args__ = (
        CheckConstraint(
            "actor_type in ('user','system','worker')", name="ck_audit_events_actor_type"
        ),
    )

    def __init__(self, **kwargs: object) -> None:
        for field_name in (
            "actor_id",
            "action",
            "resource_type",
            "resource_id",
            "request_id",
        ):
            if field_name in kwargs:
                kwargs[field_name] = _validate_identifier(kwargs[field_name], field_name)
        if "actor_type" in kwargs:
            actor_type = kwargs["actor_type"]
            if actor_type not in ALLOWED_ACTOR_TYPES:
                raise AuditEventValidationError(
                    f"actor_type must be one of {ALLOWED_ACTOR_TYPES}: {actor_type!r}"
                )
        if "ip_hash" in kwargs and kwargs["ip_hash"] is not None:
            kwargs["ip_hash"] = _validate_identifier(kwargs["ip_hash"], "ip_hash")
        if "details_json" in kwargs and kwargs["details_json"] is not None:
            kwargs["details_json"] = redact_details(kwargs["details_json"])  # type: ignore[arg-type]
        super().__init__(**kwargs)


def write_audit_event(
    session: Session,
    *,
    organization_id: uuid.UUID,
    actor_type: str,
    actor_id: str,
    action: str,
    resource_type: str,
    resource_id: str,
    request_id: str,
    ip_hash: str | None = None,
    details: dict[str, Any] | None = None,
) -> AuditEvent:
    """Adds (never commits) one append-only audit row to `session`.

    The caller must write this in the *same* transaction as the mutation
    it describes and commit both together — this function deliberately
    never calls `session.commit()`/`session.flush()` itself, so a rollback
    of the enclosing transaction (a failed mutation) discards the audit
    row too, rather than leaving a misleading "it succeeded" record behind.
    """
    event = AuditEvent(
        organization_id=organization_id,
        actor_type=actor_type,
        actor_id=actor_id,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        request_id=request_id,
        ip_hash=ip_hash,
        details_json=details if details is not None else {},
    )
    session.add(event)
    return event
