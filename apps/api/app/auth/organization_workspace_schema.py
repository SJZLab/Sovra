"""Organization/Workspace tenant schema (FTF-0102, Section 6.1/6.2).

The two tenant-root entities every other tenant-scoped table (Section 6)
ultimately references. Real forward Alembic migration in
`apps/api/alembic/versions/` creates these tables with row-level security
FORCEd on for a distinct, least-privileged application role — Section
65.1's "application runtime role is not the table owner, a superuser or
BYPASSRLS" — so tenant isolation is enforced by the database itself, not
only by this module's query filters.
"""

from __future__ import annotations

import re
import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, String, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column

# The distinct, least-privileged application role Section 65.1 requires.
# Created and owned by the migration (apps/api/alembic/versions/), never by
# application code; granted row-level (not table-owner) DML privileges only,
# and explicitly NOSUPERUSER/NOBYPASSRLS so FORCE ROW LEVEL SECURITY applies
# to every connection using it.
APP_ROLE_NAME = "sovra_app"

# Exposed (not underscore-prefixed) so the frozen contract files under
# packages/contracts/{jsonschema,openapi}/01-*.{json,yaml} can be tested
# against these exact values rather than an independently retyped copy
# that could silently drift from what this module actually enforces.
SLUG_PATTERN_SOURCE = r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$"
_SLUG_PATTERN = re.compile(SLUG_PATTERN_SOURCE)
NAME_MAX_LENGTH = 200
ALLOWED_STATUSES = ("active", "suspended", "archived")


class SchemaValidationError(ValueError):
    """A field value violates the domain schema's own shape/invariant."""


def validate_slug(value: str) -> str:
    """A URL-safe, lowercase, hyphen-internal identifier (RFC 1123 label shape)."""
    if not isinstance(value, str) or not _SLUG_PATTERN.fullmatch(value):
        raise SchemaValidationError(
            "slug must be 1-63 lowercase alphanumeric characters with "
            f"internal hyphens only (no leading/trailing hyphen): {value!r}"
        )
    return value


def validate_name(value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise SchemaValidationError("name must be a non-empty string")
    if len(value) > NAME_MAX_LENGTH:
        raise SchemaValidationError(f"name must be at most {NAME_MAX_LENGTH} characters")
    return value


class Base(DeclarativeBase):
    pass


class Organization(Base):
    """Section 6.1 — the tenant root. Every tenant-scoped row traces to one."""

    __tablename__ = "organizations"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    name: Mapped[str] = mapped_column(String(NAME_MAX_LENGTH), nullable=False)
    slug: Mapped[str] = mapped_column(String(63), nullable=False, unique=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, server_default="active")
    default_security_profile: Mapped[str] = mapped_column(
        String(64), nullable=False, server_default="standard"
    )
    default_currency: Mapped[str] = mapped_column(String(3), nullable=False, server_default="USD")
    default_timezone: Mapped[str] = mapped_column(String(64), nullable=False, server_default="UTC")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )

    __table_args__ = (
        CheckConstraint(
            "status in ('active','suspended','archived')", name="ck_organizations_status"
        ),
    )

    def __init__(self, **kwargs: object) -> None:
        if "name" in kwargs:
            kwargs["name"] = validate_name(kwargs["name"])  # type: ignore[arg-type]
        if "slug" in kwargs:
            kwargs["slug"] = validate_slug(kwargs["slug"])  # type: ignore[arg-type]
        if "status" in kwargs and kwargs["status"] not in ALLOWED_STATUSES:
            raise SchemaValidationError(
                f"status must be one of {ALLOWED_STATUSES}: {kwargs['status']!r}"
            )
        super().__init__(**kwargs)


class Workspace(Base):
    """Section 6.2 — tenant-scoped under its parent Organization.

    `(organization_id, slug)` is the real tenant-composite uniqueness
    constraint FTF-0102's acceptance clause requires — the same slug is
    reusable across different organizations, never within one.
    """

    __tablename__ = "workspaces"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(String(NAME_MAX_LENGTH), nullable=False)
    slug: Mapped[str] = mapped_column(String(63), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, server_default="active")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )

    __table_args__ = (
        UniqueConstraint("organization_id", "slug", name="uq_workspaces_organization_id_slug"),
        CheckConstraint("status in ('active','suspended','archived')", name="ck_workspaces_status"),
    )

    def __init__(self, **kwargs: object) -> None:
        if "name" in kwargs:
            kwargs["name"] = validate_name(kwargs["name"])  # type: ignore[arg-type]
        if "slug" in kwargs:
            kwargs["slug"] = validate_slug(kwargs["slug"])  # type: ignore[arg-type]
        if "status" in kwargs and kwargs["status"] not in ALLOWED_STATUSES:
            raise SchemaValidationError(
                f"status must be one of {ALLOWED_STATUSES}: {kwargs['status']!r}"
            )
        super().__init__(**kwargs)


def set_tenant_context(session: Session, organization_id: uuid.UUID | None) -> None:
    """Bind (or clear) the RLS tenant context for the *current transaction only*.

    Section 65.1: "Set tenant context transaction-locally; clear/reset
    pooled connections; refuse missing tenant context." Uses
    `set_config(..., is_local=true)` (the parameterized equivalent of
    `SET LOCAL`) rather than string-formatting a `SET` statement, so the
    organization ID is always passed as a real bind parameter, and the
    setting is automatically discarded at transaction end/rollback even on
    a pooled connection a later request reuses for a different tenant.
    `organization_id=None` clears the context, which the RLS policies
    below treat identically to a connection that never set it: zero rows
    visible, not an error and not "every row."
    """
    session.execute(
        text("SELECT set_config('app.current_organization_id', :org_id, true)"),
        {"org_id": "" if organization_id is None else str(organization_id)},
    )
