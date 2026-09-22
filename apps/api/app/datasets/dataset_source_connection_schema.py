"""DatasetSourceConnection domain schema (FTF-0500, Section 6.8F/7A.10/9/45/
65/67/77/81).

A project-scoped `DatasetSourceConnection` -- Section 6.8F's own entity,
"separates source authorization from imported dataset artifacts." This
module owns exactly the storage/validation shape of that record: an
explicit, purpose-scoped authorization to pull data from one external or
internal source, before any import happens. It deliberately does not
implement the Dataset/DatasetRecord import pipeline itself (Section 8 --
parsing, normalization, PII/duplicate detection), the full `DataUseGrant`
entity (Section 67.4 -- approver identity, processing boundary, retention,
expiry, underlying-rights evidence; not one of Section 6's own listed
entities, and no dependency task certifies it yet), or any actual source
connector call (validating S3/Postgres/REST credentials against the real
external service) -- those are FTF-0501-and-later, separately
dependency-gated tasks (see `docs/TASK_INDEX.md`). Declaring any of that as
a real, enforced capability here without a real backing implementation
would be exactly the "placeholder endpoint" Law 11 forbids.

Path refined from the task packet's own generic suggestion
(`services/training-worker/data/datasetsourceconnection_schema_api_rbac.py`)
to `apps/api/app/datasets/dataset_source_connection_schema.py`, matching
every other CERTIFIED task's own real `apps/api/app/<domain>/*_schema.py`
layout (FTF-0201/FTF-0301/FTF-0401/FTF-0404) rather than the packet's
initial owner-path placeholder -- the packet's own front matter (Section
84.3) explicitly allows this kind of path refinement to reuse the coherent
existing module layout.

This task's own acceptance clause 1 ("Create purpose-scoped source grants
and authorization before import") is read narrowly and made real, not
deferred: `allowed_purposes_json` (Section 6.8F's own field) *is* the
purpose-scoped grant this task creates -- declared once, at connection
creation, from Section 6.8F's own literal purpose vocabulary ("training",
"validation", "held_out_evaluation", "candidate_development",
"baseline_only") -- and `dataset_source_connection_crud.py`'s own
`authorize_purpose_use`/`authorize_dataset_source_purpose` is the real,
callable authorization check "before import" a later Dataset-import task
(FTF-0501+) will call, proven now against this task's own negative_
acceptance directly rather than left as an unenforced field. The broader,
later `DataUseGrant` (approver workflow, retention policy, rights
evidence) is a materially larger, separately dependency-gated concept this
task does not build.

Real forward Alembic migration
(`apps/api/alembic/versions/0010_dataset_source_connections.py`) FORCEs
row-level security on `dataset_source_connections` scoped to the same
`app.current_organization_id` tenant context FTF-0102/FTF-0104/FTF-0201/
FTF-0301/FTF-0401/FTF-0404's migrations already established, and adds a
real composite foreign key `(organization_id, project_id)` ->
`projects(organization_id, id)` -- reusing the already-real
`uq_projects_organization_id_id` constraint FTF-0404's own migration
already added (no second additive constraint needed).

Credential/config handling (Section 46 "Secret values never returned to
browser after creation... Store only secret manager path/reference"):
`credential_secret_ref`/`connection_config_encrypted_ref` only ever store
an `openbao://<mount>/<path>/<field>` reference, validated to that literal
shape below -- structurally incapable of accepting a raw credential value.
Unlike FTF-0201 (which predates FTF-0202 and only ever validated a generic
`vault://`-style reference shape, never resolving it), this task has
FTF-0202 as a certified dependency and is this repository's first real
caller of `store_secret_ref` (`dataset_source_connection_crud.py`'s own
`create_dataset_source_connection`) -- a raw credential/config value
supplied at creation is written for real to a real OpenBao KV v2 path, and
only the resulting reference is ever persisted in this table. There is no
separate encrypted-config-blob primitive anywhere in this repository;
`connection_config_encrypted_ref` reuses the exact same already-certified
OpenBao store, at a second field of the same KV v2 path, rather than this
task inventing a second storage mechanism for a materially identical
"sensitive value at rest" problem.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.auth.organization_workspace_schema import Base, validate_name

__all__ = [
    "ALLOWED_CONNECTION_STATUSES",
    "ALLOWED_PURPOSES",
    "ALLOWED_SECURITY_CLASSIFICATIONS",
    "ALLOWED_SOURCE_TYPES",
    "DISPLAY_NAME_MAX_LENGTH",
    "SECRET_REF_MAX_LENGTH",
    "DatasetSourceConnection",
    "DatasetSourceConnectionValidationError",
    "validate_allowed_purposes",
    "validate_secret_ref_or_none",
    "validate_security_classification",
    "validate_source_type",
]

DISPLAY_NAME_MAX_LENGTH = 200
SECRET_REF_MAX_LENGTH = 512

# Section 6.8F's own literal enumeration.
ALLOWED_SOURCE_TYPES = (
    "upload",
    "s3",
    "minio",
    "database",
    "agent_traces",
    "production_conversations",
    "api",
)

# Section 6.8F's own literal example purposes ("e.g. `training`,
# `validation`, `held_out_evaluation`, `candidate_development`,
# `baseline_only`"), made the closed, real vocabulary this task enforces --
# Section 6.8F's own next sentence ("The Factory must never expand these
# permissions automatically") requires a closed, checkable set, not an
# open string.
ALLOWED_PURPOSES = (
    "training",
    "validation",
    "held_out_evaluation",
    "candidate_development",
    "baseline_only",
)

# Section 6.8F names `security_classification` as a required field but does
# not itself enumerate its values anywhere in this plan (confirmed by
# direct search -- the only other two hits are prose, not a table). This is
# a disclosed, narrow choice (a standard four-tier data-classification
# scheme), not a value picked from an upstream section; a later task may
# tighten or replace it if the architecture ever pins a specific set, which
# would be a real spec-matrix.yaml note update at that time, not a silent
# widening now.
ALLOWED_SECURITY_CLASSIFICATIONS = ("public", "internal", "confidential", "restricted")

# No literal enumeration exists for this field either; reuses the exact
# active/revoked, soft-transition convention FTF-0201's own
# `LiteLLMConnection.status` already established for this same shape of
# credential-bearing connection resource (a revoked connection's history
# stays visible, never deleted).
ALLOWED_CONNECTION_STATUSES = ("active", "revoked")

# Deliberately the same closed shape FTF-0202's own already-certified
# `_REF_PATTERN` accepts (`openbao://<mount>/<path>/<field>`) -- this
# module never invents a second, wider secret-reference grammar.
_SECRET_REF_PATTERN = "openbao://"


class DatasetSourceConnectionValidationError(ValueError):
    """A field value violates this domain schema's own shape/invariant."""


def validate_source_type(value: object) -> str:
    if not isinstance(value, str) or value not in ALLOWED_SOURCE_TYPES:
        raise DatasetSourceConnectionValidationError(
            f"source_type must be one of {ALLOWED_SOURCE_TYPES}: {value!r}"
        )
    return value


def validate_allowed_purposes(value: object) -> list[str]:
    """Section 6.8F: a non-empty, deduplicated subset of the closed purpose
    vocabulary -- "The Factory must never expand these permissions
    automatically" is read as "never silently accept an unrecognized
    purpose string," the only way that sentence is machine-checkable at
    this layer."""
    if not isinstance(value, list) or not value:
        raise DatasetSourceConnectionValidationError(
            "allowed_purposes must be a non-empty list of purpose strings"
        )
    seen: list[str] = []
    for item in value:
        if not isinstance(item, str) or item not in ALLOWED_PURPOSES:
            raise DatasetSourceConnectionValidationError(
                f"allowed_purposes entries must each be one of {ALLOWED_PURPOSES}: {item!r}"
            )
        if item not in seen:
            seen.append(item)
    return seen


def validate_security_classification(value: object) -> str:
    if not isinstance(value, str) or value not in ALLOWED_SECURITY_CLASSIFICATIONS:
        raise DatasetSourceConnectionValidationError(
            f"security_classification must be one of "
            f"{ALLOWED_SECURITY_CLASSIFICATIONS}: {value!r}"
        )
    return value


def validate_secret_ref_or_none(value: object, *, field_name: str) -> str | None:
    """Never a raw value -- structurally an `openbao://...` reference or
    nothing at all. This module never accepts a credential/config value
    directly; only `dataset_source_connection_crud.py` (which calls
    `store_secret_ref` first) ever constructs the string passed here."""
    if value is None:
        return None
    if not isinstance(value, str) or not value:
        raise DatasetSourceConnectionValidationError(f"{field_name} must be a non-empty string")
    if len(value) > SECRET_REF_MAX_LENGTH:
        raise DatasetSourceConnectionValidationError(
            f"{field_name} must be at most {SECRET_REF_MAX_LENGTH} characters"
        )
    if not value.startswith(_SECRET_REF_PATTERN):
        raise DatasetSourceConnectionValidationError(
            f"{field_name} must be an {_SECRET_REF_PATTERN}... reference, "
            "never an inline credential/config value"
        )
    return value


class DatasetSourceConnection(Base):
    """Section 6.8F -- a project's own explicit, purpose-scoped
    authorization to pull data from one external or internal source.

    `organization_id` is not one of Section 6.8F's own literal fields but
    is required by Section 65.1's own "All organization-scoped entities
    carry organization_id NOT NULL" rule -- the same refinement FTF-0201/
    FTF-0301/FTF-0401/FTF-0404 already applied to their own new tables,
    needed here so RLS can enforce isolation directly on this table rather
    than only via a join through `projects`.
    """

    __tablename__ = "dataset_source_connections"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    project_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    source_type: Mapped[str] = mapped_column(String(32), nullable=False)
    display_name: Mapped[str] = mapped_column(String(DISPLAY_NAME_MAX_LENGTH), nullable=False)
    credential_secret_ref: Mapped[str | None] = mapped_column(
        String(SECRET_REF_MAX_LENGTH), nullable=True
    )
    connection_config_encrypted_ref: Mapped[str | None] = mapped_column(
        String(SECRET_REF_MAX_LENGTH), nullable=True
    )
    allowed_purposes_json: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    security_classification: Mapped[str] = mapped_column(String(32), nullable=False)
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
            name="uq_dataset_source_connections_project_id_display_name",
        ),
        CheckConstraint(
            "source_type in ('upload','s3','minio','database','agent_traces',"
            "'production_conversations','api')",
            name="ck_dataset_source_connections_source_type",
        ),
        CheckConstraint(
            "security_classification in ('public','internal','confidential','restricted')",
            name="ck_dataset_source_connections_security_classification",
        ),
        CheckConstraint(
            "connection_status in ('active','revoked')",
            name="ck_dataset_source_connections_connection_status",
        ),
        ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            ondelete="CASCADE",
            name="fk_dataset_source_connections_organization_id",
        ),
        ForeignKeyConstraint(
            ["organization_id", "project_id"],
            ["projects.organization_id", "projects.id"],
            ondelete="CASCADE",
            name="fk_dataset_source_connections_organization_id_project_id",
        ),
    )

    def __init__(self, **kwargs: object) -> None:
        if "display_name" in kwargs:
            kwargs["display_name"] = validate_name(kwargs["display_name"])  # type: ignore[arg-type]
        if "source_type" in kwargs:
            kwargs["source_type"] = validate_source_type(kwargs["source_type"])
        if "allowed_purposes_json" in kwargs:
            kwargs["allowed_purposes_json"] = validate_allowed_purposes(
                kwargs["allowed_purposes_json"]
            )
        if "security_classification" in kwargs:
            kwargs["security_classification"] = validate_security_classification(
                kwargs["security_classification"]
            )
        if "credential_secret_ref" in kwargs:
            kwargs["credential_secret_ref"] = validate_secret_ref_or_none(
                kwargs["credential_secret_ref"], field_name="credential_secret_ref"
            )
        if "connection_config_encrypted_ref" in kwargs:
            kwargs["connection_config_encrypted_ref"] = validate_secret_ref_or_none(
                kwargs["connection_config_encrypted_ref"],
                field_name="connection_config_encrypted_ref",
            )
        if (
            "connection_status" in kwargs
            and kwargs["connection_status"] not in ALLOWED_CONNECTION_STATUSES
        ):
            raise DatasetSourceConnectionValidationError(
                f"connection_status must be one of {ALLOWED_CONNECTION_STATUSES}: "
                f"{kwargs['connection_status']!r}"
            )
        super().__init__(**kwargs)
