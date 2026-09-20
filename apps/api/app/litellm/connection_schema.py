"""LiteLLM connection domain schema (FTF-0201, Section 2-3/38/65/77/81).

An organization-scoped record of one gateway connection to the
organization's existing LiteLLM proxy (Section 77.3: "Existing LiteLLM --
Organizational inference access and routing... Factory remains responsible
for: Current access checks... route identity, candidate aliases and
actual product adoption"). This module owns exactly the storage/validation
shape of that record -- Section 3.1's "Normalize base URL once; do not
generate duplicate `/v1/v1` paths... Production `tls_verify=false` is
forbidden" and Section 65.4's secret-reference-only/no-userinfo-URL rules
-- not calling the gateway itself (that is FTF-0203 "Connection
validation"/FTF-0204 "`/v1/models` discovery", both later, separately
dependency-gated tasks; see this task's own docs/certifications/queue.md
submission entry for the full scope disclosure).

Real forward Alembic migration
(`apps/api/alembic/versions/0003_litellm_connections.py`) FORCEs row-level
security on `litellm_connections` scoped to the same
`app.current_organization_id` tenant context FTF-0102/FTF-0104 already
established, and grants the restricted `sovra_app` role SELECT/INSERT/
UPDATE only -- never DELETE -- since "revoke" is a soft state transition
(`status='revoked'`), not row deletion (Section 6.24-style disclosed
audit-trail-friendly design already used for `audit_events`, applied here
to keep a revoked connection's history visible rather than erasing it).

Secret handling (disclosed, same class of gap as FTF-0101/FTF-0005/
FTF-R017, per this task's own required-reading instructions): `secret_ref`
only ever stores a reference (`vault://...`), validated to that literal
shape below -- structurally incapable of accepting a raw secret value,
never a real secret manager lookup. Unlike FTF-0101, this task never
resolves that reference to a real value at all: FTF-0201's own acceptance
clause is CRUD only ("Create/edit/revoke an organization-scoped gateway
connection with secret refs and explicit TLS/URL policy"), not calling
the gateway -- resolving `secret_ref` to bytes is FTF-0202's job
("Secret-manager integration", a later, separately dependency-gated
task), and actually presenting an inference key to the gateway is
FTF-0203's. No secret-manager integration exists anywhere in this
repository yet.
"""

from __future__ import annotations

import re
import uuid
from datetime import datetime
from urllib.parse import urlsplit

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, String, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.auth.organization_workspace_schema import Base, validate_name

__all__ = [
    "ALLOWED_STATUSES",
    "BASE_URL_MAX_LENGTH",
    "SECRET_REF_MAX_LENGTH",
    "SECRET_REF_SCHEMES",
    "LiteLLMConnection",
    "LiteLLMConnectionValidationError",
    "validate_base_url",
    "validate_secret_ref",
]

BASE_URL_MAX_LENGTH = 2048
SECRET_REF_MAX_LENGTH = 512
ALLOWED_STATUSES = ("active", "revoked")

# Same reference-only convention `apps/api/app/auth/oidc_settings.py`
# already established for `client_secret_ref`/`session_signing_key_secret_ref`
# -- a real secret-manager scheme, never an inline value.
SECRET_REF_SCHEMES = ("vault", "openbao", "secret")
_SECRET_REF_PATTERN = re.compile(r"^(?P<scheme>[a-z]+)://[A-Za-z0-9_\-./]+$")

# A bearer token or JWT-compact-serialization shape pasted in by mistake --
# checked independently of the vault://-prefix requirement (which already
# structurally rejects it) so the rejection reason is explicit, matching
# `audit_event_writer.py`'s own independent value-shape redaction check.
_RAW_SECRET_SHAPE_PATTERNS = (
    re.compile(r"^bearer\s+\S+$", re.IGNORECASE),
    re.compile(r"^[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{5,}$"),
)


class LiteLLMConnectionValidationError(ValueError):
    """A field value violates this domain schema's own shape/invariant."""


def validate_base_url(value: object) -> str:
    """Section 3.1/65.4: normalized, TLS-only, no userinfo/fragment, no
    duplicate `/v1/v1` path segment, no `tls_verify=false` in the query.

    Only validates *shape* -- this module never dials the URL (that is
    FTF-0203's job). `https` is the only allowed scheme: this structurally
    forbids `http`, `file`, `gopher`, `ftp` and any other scheme Section
    65.4 names as disallowed, not merely `tls_verify=false` in isolation.
    """
    if not isinstance(value, str) or not value:
        raise LiteLLMConnectionValidationError("base_url must be a non-empty string")
    if len(value) > BASE_URL_MAX_LENGTH:
        raise LiteLLMConnectionValidationError(
            f"base_url must be at most {BASE_URL_MAX_LENGTH} characters"
        )
    try:
        parsed = urlsplit(value)
    except ValueError as exc:
        raise LiteLLMConnectionValidationError(f"base_url is not a valid URL: {value!r}") from exc

    if parsed.scheme != "https":
        raise LiteLLMConnectionValidationError(
            f"base_url must use https:// -- production tls_verify=false is forbidden "
            f"(Section 3.1/65.4), got scheme {parsed.scheme!r}"
        )
    if not parsed.hostname:
        raise LiteLLMConnectionValidationError("base_url must have a host")
    if parsed.username or parsed.password:
        raise LiteLLMConnectionValidationError(
            "base_url may not carry userinfo (Section 65.4 forbids userinfo URLs)"
        )
    if parsed.fragment:
        raise LiteLLMConnectionValidationError("base_url may not carry a fragment")
    normalized_query = parsed.query.lower().replace(" ", "")
    if "tls_verify=false" in normalized_query:
        raise LiteLLMConnectionValidationError(
            "base_url may not set tls_verify=false -- forbidden in production (Section 3.1)"
        )
    if "/v1/v1" in parsed.path.rstrip("/"):
        raise LiteLLMConnectionValidationError(
            "base_url must not contain a duplicate /v1/v1 path segment (Section 3.1 "
            "'Normalize base URL once; do not generate duplicate /v1/v1 paths')"
        )
    return value


def validate_secret_ref(value: object) -> str:
    """A `<scheme>://...` reference only -- never a raw secret value.

    The `vault://`/`openbao://`/`secret://` scheme requirement is itself
    structurally incompatible with a bearer token or JWT value (neither
    contains `://`), and `_RAW_SECRET_SHAPE_PATTERNS` is checked
    independently as an explicit, named rejection reason rather than
    relying solely on the scheme check's side effect.
    """
    if not isinstance(value, str) or not value:
        raise LiteLLMConnectionValidationError("secret_ref must be a non-empty string")
    if len(value) > SECRET_REF_MAX_LENGTH:
        raise LiteLLMConnectionValidationError(
            f"secret_ref must be at most {SECRET_REF_MAX_LENGTH} characters"
        )
    for pattern in _RAW_SECRET_SHAPE_PATTERNS:
        if pattern.match(value):
            raise LiteLLMConnectionValidationError(
                "secret_ref must not be a raw secret/bearer-token/JWT value -- "
                "store a vault://-style reference only"
            )
    match = _SECRET_REF_PATTERN.match(value)
    if not match or match.group("scheme") not in SECRET_REF_SCHEMES:
        raise LiteLLMConnectionValidationError(
            f"secret_ref must be a reference URI with scheme in {SECRET_REF_SCHEMES} "
            f"(e.g. 'vault://path/to/secret'), never an inline secret value"
        )
    return value


class LiteLLMConnection(Base):
    """Section 77.3/77.5 -- an organization's own connection binding to its
    existing LiteLLM gateway. Deliberately does not store `ComponentBinding`'s
    full shape (Section 77.5 is a later, broader architecture item) --
    just this task's own acceptance-scoped fields: name, base_url,
    secret_ref, status, timestamps.
    """

    __tablename__ = "litellm_connections"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    base_url: Mapped[str] = mapped_column(String(BASE_URL_MAX_LENGTH), nullable=False)
    secret_ref: Mapped[str] = mapped_column(String(SECRET_REF_MAX_LENGTH), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, server_default="active")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        UniqueConstraint(
            "organization_id", "name", name="uq_litellm_connections_organization_id_name"
        ),
        CheckConstraint("status in ('active','revoked')", name="ck_litellm_connections_status"),
    )

    def __init__(self, **kwargs: object) -> None:
        if "name" in kwargs:
            kwargs["name"] = validate_name(kwargs["name"])  # type: ignore[arg-type]
        if "base_url" in kwargs:
            kwargs["base_url"] = validate_base_url(kwargs["base_url"])
        if "secret_ref" in kwargs:
            kwargs["secret_ref"] = validate_secret_ref(kwargs["secret_ref"])
        if "status" in kwargs and kwargs["status"] not in ALLOWED_STATUSES:
            raise LiteLLMConnectionValidationError(
                f"status must be one of {ALLOWED_STATUSES}: {kwargs['status']!r}"
            )
        super().__init__(**kwargs)
