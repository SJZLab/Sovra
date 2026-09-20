"""TrainableArtifact domain schema (FTF-0301, Section 6.7/13/34/68/77/81).

An organization-scoped record of one candidate training artifact --
Section 6.7's `TrainableArtifact` entity. This module owns exactly the
storage/validation shape of that record -- registering a versioned
artifact URI/revision/license identity -- not resolving it against a real
Hugging Face Hub, S3 bucket, local filesystem or private registry (that is
FTF-0302/FTF-0303/FTF-0304's own, later, separately dependency-gated
tasks) and not computing/verifying `weights_digest` from real bytes (that
is FTF-0306 "Base model revision/digest resolver"'s job). Declaring those
as real resolved-and-verified operations here without a real backing call
would be exactly the "placeholder endpoint" Law 11 forbids; this task
instead owns the field this task's own domain model requires
(`weights_digest`) as a value a later resolver task writes through this
same schema's own edit path, never fabricated here.

This task's own negative_acceptance ("Unsafe URI, unknown owner or
mutable unresolved revision cannot enter training as ready") is enforced
in two layers: `validate_source_uri` below rejects an unsafe URI at
construction/edit time (the same creation-time-400 precedent FTF-0201's
own `validate_base_url` established), while "unknown owner" (no real
authenticated actor has accepted the license) and "mutable unresolved
revision" (no pinned revision + resolved digest) are pure, DB-free
ready-transition gates in `apps/api/app/artifacts/artifact_crud.py`'s own
`apply_mark_ready` -- status can reach `ready` only once all three hold.

Real forward Alembic migration
(`apps/api/alembic/versions/0004_trainable_artifacts.py`) FORCEs row-level
security on `trainable_artifacts` scoped to the same
`app.current_organization_id` tenant context FTF-0102/FTF-0104/FTF-0201
already established, and grants the restricted `sovra_app` role SELECT/
INSERT/UPDATE only -- never DELETE, since "archive" is a soft state
transition (`status='archived'`), not row deletion, the same
grant-surface-matches-actual-DML-need discipline FTF-0201 already applied
to a different resource for a structurally identical reason.
"""

from __future__ import annotations

import re
import uuid
from datetime import datetime
from urllib.parse import urlsplit

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.auth.organization_workspace_schema import Base, validate_name

__all__ = [
    "ALLOWED_SOURCE_TYPES",
    "ALLOWED_STATUSES",
    "BASE_MODEL_REVISION_MAX_LENGTH",
    "DISPLAY_NAME_MAX_LENGTH",
    "LICENSE_ID_MAX_LENGTH",
    "MODEL_FAMILY_MAX_LENGTH",
    "MUTABLE_REVISION_ALIASES",
    "SOURCE_URI_MAX_LENGTH",
    "WEIGHTS_DIGEST_PATTERN_SOURCE",
    "TrainableArtifact",
    "TrainableArtifactValidationError",
    "is_pinned_revision",
    "validate_base_model_revision",
    "validate_license_id",
    "validate_minimum_gpu_profile",
    "validate_required_python_packages",
    "validate_source_type",
    "validate_source_uri",
    "validate_weights_digest",
]

DISPLAY_NAME_MAX_LENGTH = 200
SOURCE_URI_MAX_LENGTH = 2048
MODEL_FAMILY_MAX_LENGTH = 200
BASE_MODEL_REVISION_MAX_LENGTH = 200
LICENSE_ID_MAX_LENGTH = 200
WEIGHTS_DIGEST_MAX_LENGTH = 128
_MAX_REQUIRED_PACKAGES = 50
_MAX_PACKAGE_SPEC_LENGTH = 200

ALLOWED_STATUSES = ("registered", "ready", "archived")

# Section 6.7's own literal enumeration.
ALLOWED_SOURCE_TYPES = ("huggingface", "private_s3", "local_filesystem", "private_registry")

# Section 13/68.4: "mutable, unresolved revision" -- a floating branch/tag
# alias that can silently point at different bytes over time, not a pinned
# identity. Compared case-insensitively, stripped.
MUTABLE_REVISION_ALIASES = frozenset(
    {"", "head", "latest", "main", "master", "stable", "nightly", "trunk", "default"}
)

WEIGHTS_DIGEST_PATTERN_SOURCE = r"^sha256:[0-9a-f]{64}$"
_WEIGHTS_DIGEST_PATTERN = re.compile(WEIGHTS_DIGEST_PATTERN_SOURCE)

# org/repo only -- no scheme, no leading/trailing separators, no ".."
# traversal, no whitespace. Deliberately does not accept a full
# "https://huggingface.co/..." URL: a bare repo id is this task's own
# storage shape (resolving it to real files is FTF-0302's job).
_HUGGINGFACE_REPO_PATTERN = re.compile(
    r"^[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?/[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?$"
)

# AWS bucket-naming shape (RFC-1123-like, lowercase, 3-63 chars).
_S3_BUCKET_PATTERN = re.compile(r"^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$")


class TrainableArtifactValidationError(ValueError):
    """A field value violates this domain schema's own shape/invariant."""


def validate_source_type(value: object) -> str:
    if not isinstance(value, str) or value not in ALLOWED_SOURCE_TYPES:
        raise TrainableArtifactValidationError(
            f"source_type must be one of {ALLOWED_SOURCE_TYPES}: {value!r}"
        )
    return value


def _validate_huggingface_uri(value: str) -> str:
    if "://" in value or value.startswith("/") or ".." in value or any(c.isspace() for c in value):
        raise TrainableArtifactValidationError(
            "huggingface source_uri must be a bare 'org/repo' identifier -- no scheme, "
            f"leading slash, '..' traversal or whitespace: {value!r}"
        )
    if not _HUGGINGFACE_REPO_PATTERN.fullmatch(value):
        raise TrainableArtifactValidationError(
            f"huggingface source_uri must match 'org/repo': {value!r}"
        )
    return value


def _validate_private_s3_uri(value: str) -> str:
    parsed = urlsplit(value)
    if parsed.scheme != "s3":
        raise TrainableArtifactValidationError(
            f"private_s3 source_uri must use the s3:// scheme, got {parsed.scheme!r}"
        )
    if parsed.username or parsed.password:
        raise TrainableArtifactValidationError(
            "private_s3 source_uri may not carry userinfo (Section 65.4)"
        )
    if parsed.fragment:
        raise TrainableArtifactValidationError("private_s3 source_uri may not carry a fragment")
    bucket = parsed.hostname or ""
    if not _S3_BUCKET_PATTERN.fullmatch(bucket):
        raise TrainableArtifactValidationError(
            f"private_s3 source_uri must have a valid bucket name: {value!r}"
        )
    key = parsed.path.lstrip("/")
    if not key or ".." in key.split("/"):
        raise TrainableArtifactValidationError(
            f"private_s3 source_uri must have a non-empty key with no '..' segments: {value!r}"
        )
    return value


def _validate_local_filesystem_uri(value: str) -> str:
    if "://" in value:
        raise TrainableArtifactValidationError(
            "local_filesystem source_uri must be a bare relative path, not a URL"
        )
    if value.startswith("/") or value.startswith("~"):
        raise TrainableArtifactValidationError(
            "local_filesystem source_uri must be relative -- an absolute path or home-relative "
            f"path could escape the approved artifact root: {value!r}"
        )
    if "\x00" in value or ".." in value.split("/"):
        raise TrainableArtifactValidationError(
            f"local_filesystem source_uri must not contain a '..' traversal segment: {value!r}"
        )
    if not value.strip():
        raise TrainableArtifactValidationError("local_filesystem source_uri must be non-empty")
    return value


def _validate_private_registry_uri(value: str) -> str:
    parsed = urlsplit(value)
    if parsed.scheme != "https":
        raise TrainableArtifactValidationError(
            f"private_registry source_uri must use https://, got scheme {parsed.scheme!r}"
        )
    if not parsed.hostname:
        raise TrainableArtifactValidationError("private_registry source_uri must have a host")
    if parsed.username or parsed.password:
        raise TrainableArtifactValidationError(
            "private_registry source_uri may not carry userinfo (Section 65.4)"
        )
    if parsed.fragment:
        raise TrainableArtifactValidationError(
            "private_registry source_uri may not carry a fragment"
        )
    return value


_URI_VALIDATORS = {
    "huggingface": _validate_huggingface_uri,
    "private_s3": _validate_private_s3_uri,
    "local_filesystem": _validate_local_filesystem_uri,
    "private_registry": _validate_private_registry_uri,
}


def validate_source_uri(source_type: object, value: object) -> str:
    """Section 65.4-style shape safety, dispatched per `source_type`
    (Section 6.7's own four allowed values) -- this task's own
    negative_acceptance "Unsafe URI ... cannot enter training as ready"
    is enforced here at construction/edit time (never merely at the later
    ready-transition), matching FTF-0201's own `validate_base_url`
    creation-time-400 precedent.
    """
    validate_source_type(source_type)
    if not isinstance(value, str) or not value:
        raise TrainableArtifactValidationError("source_uri must be a non-empty string")
    if len(value) > SOURCE_URI_MAX_LENGTH:
        raise TrainableArtifactValidationError(
            f"source_uri must be at most {SOURCE_URI_MAX_LENGTH} characters"
        )
    return _URI_VALIDATORS[source_type](value)  # type: ignore[index]


def is_pinned_revision(value: str) -> bool:
    """Section 13/68.4: a *pinned*, non-floating revision identifier --
    not one of the known mutable branch/tag aliases. This is a shape
    check only (this task never resolves the revision against a real
    upstream); FTF-0306's own resolver is what actually confirms the
    referenced commit/tag truly still points at `weights_digest`."""
    return value.strip().lower() not in MUTABLE_REVISION_ALIASES


def validate_base_model_revision(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise TrainableArtifactValidationError("base_model_revision must be a non-empty string")
    if len(value) > BASE_MODEL_REVISION_MAX_LENGTH:
        raise TrainableArtifactValidationError(
            f"base_model_revision must be at most {BASE_MODEL_REVISION_MAX_LENGTH} characters"
        )
    return value


def validate_weights_digest(value: object) -> str:
    if not isinstance(value, str) or not _WEIGHTS_DIGEST_PATTERN.fullmatch(value):
        raise TrainableArtifactValidationError(
            f"weights_digest must match {WEIGHTS_DIGEST_PATTERN_SOURCE!r} (e.g. "
            "'sha256:' followed by 64 hex characters), never a bare/short hash: {value!r}"
        )
    return value


def validate_license_id(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise TrainableArtifactValidationError("license_id must be a non-empty string")
    if len(value) > LICENSE_ID_MAX_LENGTH:
        raise TrainableArtifactValidationError(
            f"license_id must be at most {LICENSE_ID_MAX_LENGTH} characters"
        )
    return value


def validate_required_python_packages(value: object) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise TrainableArtifactValidationError("required_python_packages must be a list of strings")
    if len(value) > _MAX_REQUIRED_PACKAGES:
        raise TrainableArtifactValidationError(
            f"required_python_packages must have at most {_MAX_REQUIRED_PACKAGES} entries"
        )
    for item in value:
        if not item.strip() or len(item) > _MAX_PACKAGE_SPEC_LENGTH:
            raise TrainableArtifactValidationError(
                f"required_python_packages entries must be non-empty and at most "
                f"{_MAX_PACKAGE_SPEC_LENGTH} characters: {item!r}"
            )
    return list(value)


def validate_minimum_gpu_profile(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        raise TrainableArtifactValidationError("minimum_gpu_profile must be a JSON object")
    if "min_gpu_vram_gb" in value:
        vram = value["min_gpu_vram_gb"]
        if isinstance(vram, bool) or not isinstance(vram, int | float) or vram < 0:
            raise TrainableArtifactValidationError(
                f"minimum_gpu_profile.min_gpu_vram_gb must be a non-negative number: {vram!r}"
            )
    return dict(value)


class TrainableArtifact(Base):
    """Section 6.7 -- an organization's own candidate training artifact
    registration. Deliberately does not itself resolve `source_uri`
    against a real upstream or compute `weights_digest` from real bytes
    (FTF-0302/0303/0304/0306's own, later, separately dependency-gated
    jobs) -- this task's own acceptance scope is registration/storage/
    validation of the identity fields Section 6.7 names, plus the pure
    ready-transition gate in `artifact_crud.py`.
    """

    __tablename__ = "trainable_artifacts"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
    )
    display_name: Mapped[str] = mapped_column(String(DISPLAY_NAME_MAX_LENGTH), nullable=False)
    source_type: Mapped[str] = mapped_column(String(32), nullable=False)
    source_uri: Mapped[str] = mapped_column(String(SOURCE_URI_MAX_LENGTH), nullable=False)
    model_family: Mapped[str] = mapped_column(String(MODEL_FAMILY_MAX_LENGTH), nullable=False)
    base_model_revision: Mapped[str] = mapped_column(
        String(BASE_MODEL_REVISION_MAX_LENGTH), nullable=False
    )
    weights_digest: Mapped[str | None] = mapped_column(
        String(WEIGHTS_DIGEST_MAX_LENGTH), nullable=True
    )
    license_id: Mapped[str] = mapped_column(String(LICENSE_ID_MAX_LENGTH), nullable=False)
    license_accepted_by: Mapped[str | None] = mapped_column(String(200), nullable=True)
    license_accepted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    trust_remote_code_allowed: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    required_python_packages_json: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    minimum_gpu_profile_json: Mapped[dict[str, object]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False, server_default="registered")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        UniqueConstraint(
            "organization_id",
            "display_name",
            name="uq_trainable_artifacts_organization_id_display_name",
        ),
        CheckConstraint(
            "status in ('registered','ready','archived')", name="ck_trainable_artifacts_status"
        ),
        CheckConstraint(
            "source_type in ('huggingface','private_s3','local_filesystem','private_registry')",
            name="ck_trainable_artifacts_source_type",
        ),
    )

    def __init__(self, **kwargs: object) -> None:
        if "display_name" in kwargs:
            kwargs["display_name"] = validate_name(kwargs["display_name"])  # type: ignore[arg-type]
        if "source_type" in kwargs:
            kwargs["source_type"] = validate_source_type(kwargs["source_type"])
        if "source_uri" in kwargs:
            kwargs["source_uri"] = validate_source_uri(
                kwargs.get("source_type"), kwargs["source_uri"]
            )
        if "base_model_revision" in kwargs:
            kwargs["base_model_revision"] = validate_base_model_revision(
                kwargs["base_model_revision"]
            )
        if kwargs.get("weights_digest") is not None:
            kwargs["weights_digest"] = validate_weights_digest(kwargs["weights_digest"])
        if "license_id" in kwargs:
            kwargs["license_id"] = validate_license_id(kwargs["license_id"])
        if "required_python_packages_json" in kwargs:
            kwargs["required_python_packages_json"] = validate_required_python_packages(
                kwargs["required_python_packages_json"]
            )
        if "minimum_gpu_profile_json" in kwargs:
            kwargs["minimum_gpu_profile_json"] = validate_minimum_gpu_profile(
                kwargs["minimum_gpu_profile_json"]
            )
        if "status" in kwargs and kwargs["status"] not in ALLOWED_STATUSES:
            raise TrainableArtifactValidationError(
                f"status must be one of {ALLOWED_STATUSES}: {kwargs['status']!r}"
            )
        super().__init__(**kwargs)
