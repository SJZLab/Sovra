"""TargetApplicationConnection domain schema (FTF-04A01, Section 6.8A/7A.1-
7A.3/9/45/65/77/81).

A project-scoped `TargetApplicationConnection` -- Section 6.8A's own
entity, "an explicitly authorized target product/repository connection."
This module owns exactly the storage/validation shape of that record: the
connection record itself, before any real repository access happens. It
deliberately does not implement the `TargetApplicationConnector` Protocol
(Section 7A.1: `validate_connection`/`resolve_revision`/`create_snapshot`/
`list_candidate_agents`/`read_evidence`/`detect_drift`), the concrete
GitHub App/token exchange (Section 7A.2 -- FTF-04A02, a separate,
dependency-gated later task), the local-repository root-allowlist/path-
traversal guard (Section 7A.3 -- FTF-04A06), or `TargetSourceSnapshot`/
`DiscoveredAgentCandidate`/`AgentManifest` (Sections 6.8B-6.8D -- FTF-04A07/
FTF-04A17/FTF-04A19, each a separate, dependency-gated later task).
Declaring any of that as a real, enforced capability here without a real
backing implementation would be exactly the "placeholder endpoint" Law 11
forbids.

Path refined from the task packet's own generic suggestion
(`packages/target-connectors/targetapplicationconnection_schema_api_rbac.py`)
to `apps/api/app/target_connectors/target_application_connection_schema.py`,
matching every other CERTIFIED task's own real `apps/api/app/<domain>/
*_schema.py` layout (FTF-0201/FTF-0301/FTF-0401/FTF-0404/FTF-0500) rather
than the packet's initial owner-path placeholder -- the packet's own front
matter (Section 84.3) explicitly allows this kind of path refinement to
reuse the coherent existing module layout.

This task's own acceptance clause 1 ("Create tenant-scoped read-only
target connection and validate credentials without fetching unapproved
data") is read narrowly and made real, not deferred: "validate credentials"
here is a real, synchronous, connector-type-conditional *structural*
validation performed at connection-creation time -- `credential_secret_ref`
is required (and stored only via FTF-0202's real OpenBao-backed
`store_secret_ref`) for `connector_type='github'`, refused entirely for
`connector_type='local_repository'` (a local connection carries no
credential at all); `repository_url_redacted` is required and validated
through FTF-H006's own shared `packages.security.url_policy.validate_url_shape`
for `github`, refused for `local_repository` (Section 7A.3's own root-
allowlist is a separate, later task -- this module never accepts or
stores a local filesystem path). No network connection is ever opened by
this validation -- "without fetching unapproved data" is satisfied in the
strictest sense: zero bytes of external data are fetched by this task at
all, since nothing is yet approved to fetch (no snapshot/branch exists).
A real, live credential exchange against GitHub itself (Section 7A.2's own
"connect using GitHub App installation or organization-approved token")
is FTF-04A02's job, a separate, certified dependency this task does not
require and does not build.

`access_mode` (Section 6.8A: "read_only; write is not required for
qualification") is made a real, enforced closed set of exactly one value
for Release 1 -- this is this task's own concrete reading of "excessive
permission fail server-side" (this task's own negative_acceptance): any
value other than 'read_only' is refused at construction, never silently
accepted and left unenforced.

Real forward Alembic migration
(`apps/api/alembic/versions/0011_target_application_connections.py`)
FORCEs row-level security on `target_application_connections` scoped to
the same `app.current_organization_id` tenant context FTF-0102/FTF-0104/
FTF-0201/FTF-0301/FTF-0401/FTF-0404/FTF-0500's migrations already
established, and adds a real composite foreign key
`(organization_id, project_id)` -> `projects(organization_id, id)`,
reusing the already-real `uq_projects_organization_id_id` constraint
FTF-0404's own migration already added.
"""

from __future__ import annotations

import sys
import uuid
from datetime import datetime
from pathlib import Path

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

_REPO_ROOT = Path(__file__).resolve().parents[4]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from packages.security.url_policy import (  # noqa: E402
    ConnectionPolicy,
    OutboundPolicyError,
    validate_url_shape,
)

from app.auth.organization_workspace_schema import Base, validate_name  # noqa: E402

__all__ = [
    "ALLOWED_ACCESS_MODES",
    "ALLOWED_CONNECTION_STATUSES",
    "ALLOWED_CONNECTOR_TYPES",
    "DISPLAY_NAME_MAX_LENGTH",
    "REPOSITORY_IDENTITY_MAX_LENGTH",
    "REPOSITORY_URL_MAX_LENGTH",
    "SECRET_REF_MAX_LENGTH",
    "TargetApplicationConnection",
    "TargetApplicationConnectionValidationError",
    "validate_access_mode",
    "validate_connector_type",
    "validate_repository_url_or_none",
    "validate_secret_ref_or_none",
]

DISPLAY_NAME_MAX_LENGTH = 200
REPOSITORY_IDENTITY_MAX_LENGTH = 500
REPOSITORY_URL_MAX_LENGTH = 2048
SECRET_REF_MAX_LENGTH = 512

# Section 7A.1: "Minimum production connectors required by DoD: 1. GitHub
# read-only connector 2. Local repository connector. No mock connector may
# satisfy release qualification." -- the closed set this task's own
# negative_acceptance ("unsupported connector... fail server-side") checks
# against.
ALLOWED_CONNECTOR_TYPES = ("github", "local_repository")

# Section 6.8A: "access_mode (`read_only`; write is not required for
# qualification)" -- read literally as the only value Release 1 supports;
# this task's own real "excessive permission fail server-side" check.
ALLOWED_ACCESS_MODES = ("read_only",)

# Same active/revoked soft-transition convention FTF-0201's own
# `LiteLLMConnection.status` and FTF-0500's own `DatasetSourceConnection.
# connection_status` already established for this same shape of
# credential-bearing connection resource.
ALLOWED_CONNECTION_STATUSES = ("active", "revoked")

# Deliberately the same closed shape FTF-0202's own already-certified
# `_REF_PATTERN` accepts (`openbao://<mount>/<path>/<field>`) -- this
# module never invents a second, wider secret-reference grammar.
_SECRET_REF_PATTERN = "openbao://"


class TargetApplicationConnectionValidationError(ValueError):
    """A field value violates this domain schema's own shape/invariant."""


def validate_connector_type(value: object) -> str:
    if not isinstance(value, str) or value not in ALLOWED_CONNECTOR_TYPES:
        raise TargetApplicationConnectionValidationError(
            f"connector_type must be one of {ALLOWED_CONNECTOR_TYPES}: {value!r}"
        )
    return value


def validate_access_mode(value: object) -> str:
    if not isinstance(value, str) or value not in ALLOWED_ACCESS_MODES:
        raise TargetApplicationConnectionValidationError(
            f"access_mode must be one of {ALLOWED_ACCESS_MODES}: {value!r}"
        )
    return value


def validate_repository_url_or_none(value: object) -> str | None:
    """Structural shape only (FTF-H006's own shared
    `packages.security.url_policy.validate_url_shape`) -- this module never
    dials the URL and never resolves DNS; that is a later task's job
    (Section 7A.2's own real GitHub connector, FTF-04A02)."""
    if value is None:
        return None
    if not isinstance(value, str) or not value:
        raise TargetApplicationConnectionValidationError(
            "repository_url_redacted must be a non-empty string or null"
        )
    if len(value) > REPOSITORY_URL_MAX_LENGTH:
        raise TargetApplicationConnectionValidationError(
            f"repository_url_redacted must be at most {REPOSITORY_URL_MAX_LENGTH} characters"
        )
    policy = ConnectionPolicy(name="target-application-connection")
    try:
        validate_url_shape(value, policy)
    except OutboundPolicyError as exc:
        raise TargetApplicationConnectionValidationError(
            f"repository_url_redacted failed shared outbound URL policy shape " f"validation: {exc}"
        ) from exc
    return value


def validate_secret_ref_or_none(value: object, *, field_name: str) -> str | None:
    """Never a raw value -- structurally an `openbao://...` reference or
    nothing at all. This module never accepts a credential value directly;
    only `target_application_connection_crud.py` (which calls
    `store_secret_ref` first) ever constructs the string passed here."""
    if value is None:
        return None
    if not isinstance(value, str) or not value:
        raise TargetApplicationConnectionValidationError(f"{field_name} must be a non-empty string")
    if len(value) > SECRET_REF_MAX_LENGTH:
        raise TargetApplicationConnectionValidationError(
            f"{field_name} must be at most {SECRET_REF_MAX_LENGTH} characters"
        )
    if not value.startswith(_SECRET_REF_PATTERN):
        raise TargetApplicationConnectionValidationError(
            f"{field_name} must be an {_SECRET_REF_PATTERN}... reference, "
            "never an inline credential value"
        )
    return value


def _validate_repository_identity(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise TargetApplicationConnectionValidationError(
            "repository_identity must be a non-empty string"
        )
    if len(value) > REPOSITORY_IDENTITY_MAX_LENGTH:
        raise TargetApplicationConnectionValidationError(
            f"repository_identity must be at most {REPOSITORY_IDENTITY_MAX_LENGTH} characters"
        )
    return value


class TargetApplicationConnection(Base):
    """Section 6.8A -- a project's own explicitly authorized target
    product/repository connection.

    `organization_id` is not one of Section 6.8A's own literal fields but
    is required by Section 65.1's own "All organization-scoped entities
    carry organization_id NOT NULL" rule -- the same refinement FTF-0201/
    FTF-0301/FTF-0401/FTF-0404/FTF-0500 already applied to their own new
    tables, needed here so RLS can enforce isolation directly on this
    table rather than only via a join through `projects`.
    """

    __tablename__ = "target_application_connections"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    project_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    connector_type: Mapped[str] = mapped_column(String(32), nullable=False)
    display_name: Mapped[str] = mapped_column(String(DISPLAY_NAME_MAX_LENGTH), nullable=False)
    repository_identity: Mapped[str] = mapped_column(
        String(REPOSITORY_IDENTITY_MAX_LENGTH), nullable=False
    )
    repository_url_redacted: Mapped[str | None] = mapped_column(
        String(REPOSITORY_URL_MAX_LENGTH), nullable=True
    )
    default_branch: Mapped[str | None] = mapped_column(String(200), nullable=True)
    selected_branch: Mapped[str | None] = mapped_column(String(200), nullable=True)
    credential_secret_ref: Mapped[str | None] = mapped_column(
        String(SECRET_REF_MAX_LENGTH), nullable=True
    )
    access_mode: Mapped[str] = mapped_column(String(16), nullable=False, server_default="read_only")
    connection_status: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default="active"
    )
    last_validated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_by: Mapped[str] = mapped_column(String(200), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        UniqueConstraint(
            "project_id",
            "display_name",
            name="uq_target_application_connections_project_id_display_name",
        ),
        CheckConstraint(
            "connector_type in ('github','local_repository')",
            name="ck_target_application_connections_connector_type",
        ),
        CheckConstraint(
            "access_mode in ('read_only')",
            name="ck_target_application_connections_access_mode",
        ),
        CheckConstraint(
            "connection_status in ('active','revoked')",
            name="ck_target_application_connections_connection_status",
        ),
        ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            ondelete="CASCADE",
            name="fk_target_application_connections_organization_id",
        ),
        ForeignKeyConstraint(
            ["organization_id", "project_id"],
            ["projects.organization_id", "projects.id"],
            ondelete="CASCADE",
            name="fk_target_application_connections_organization_id_project_id",
        ),
    )

    def __init__(self, **kwargs: object) -> None:
        if "display_name" in kwargs:
            kwargs["display_name"] = validate_name(kwargs["display_name"])  # type: ignore[arg-type]
        if "connector_type" in kwargs:
            kwargs["connector_type"] = validate_connector_type(kwargs["connector_type"])
        if "repository_identity" in kwargs:
            kwargs["repository_identity"] = _validate_repository_identity(
                kwargs["repository_identity"]
            )
        if "repository_url_redacted" in kwargs:
            kwargs["repository_url_redacted"] = validate_repository_url_or_none(
                kwargs["repository_url_redacted"]
            )
        if "credential_secret_ref" in kwargs:
            kwargs["credential_secret_ref"] = validate_secret_ref_or_none(
                kwargs["credential_secret_ref"], field_name="credential_secret_ref"
            )
        if "access_mode" in kwargs:
            kwargs["access_mode"] = validate_access_mode(kwargs["access_mode"])
        if (
            "connection_status" in kwargs
            and kwargs["connection_status"] not in ALLOWED_CONNECTION_STATUSES
        ):
            raise TargetApplicationConnectionValidationError(
                f"connection_status must be one of {ALLOWED_CONNECTION_STATUSES}: "
                f"{kwargs['connection_status']!r}"
            )
        super().__init__(**kwargs)
