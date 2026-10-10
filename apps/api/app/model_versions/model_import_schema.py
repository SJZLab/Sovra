"""ModelImportJob domain schema (FTF-H010-P1, Section 6.17/58A/68.1).

The asynchronous enqueue/track surface FTF-1001's own
`model_version_routes.py` deliberately declined to build ("'import' *is*
creation here ... there is no separate asynchronous import job to track"
-- true only for the three shapes that router itself resolves
synchronously). FTF-H010's own real endpoint/weights/GPU validation
cannot resolve inside one request, so a job is enqueued `queued` and a
later worker (not built by this slice -- disclosed in
`model_import_routes.py`'s own module docstring) moves it through
`validating` to `failed`/`succeeded`/`blocked_external`. This module owns
only the request shape and the job's own immutable identity columns, the
same split `model_version_schema.py` already uses between its own pure
shape module and `model_version_crud.py`'s own DB-touching operations.

Table and grants are in `0027_model_import.py`: `sovra_app` gets
`SELECT, INSERT` plus a column-limited `UPDATE (state, failure_code,
updated_at)` -- the job's own requested shape never changes after
insert, only its outcome does, mirroring `0024_dataset_uploads.py`'s own
`dataset_upload_sessions` split.
"""

from __future__ import annotations

import unicodedata
import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKeyConstraint, Integer, String, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.auth.organization_workspace_schema import Base

__all__ = [
    "ALLOWED_JOB_STATES",
    "ALLOWED_ORIGINS",
    "ModelImportJob",
    "ModelImportValidationError",
    "validate_external_provider_model_ref",
    "validate_origin",
]

# Section 68.1 names exactly three import forms for this route ("external
# endpoint/provider model reference, signed full-weight artifact, or
# signed adapter plus exact base artifact"); `factory_trained` is not one
# of them -- that is FTF-1001's own direct creation path
# (`model_version_crud.create_model_version`), never something *imported*
# through this job queue. `model_version_schema.py` keeps its own,
# separate four-value `ALLOWED_ORIGINS` (a `ModelVersion` row itself can
# legitimately be `factory_trained`); only the *requested_origin* of an
# import job excludes it, enforced here and, defense in depth, by
# `ck_model_import_jobs_origin_not_factory_trained` (0028).
#
# Named module-level singletons (not just tuple membership): `validate_origin`
# always returns one of these exact three objects, never the caller's own
# `value`, so `create_model_import_job`'s own dispatch can compare by identity
# (`is`) rather than value (`==`) -- a value-equal but non-identical string
# (anything a caller could actually construct) is already refused by
# `validate_origin` itself, so `is` never rejects a real caller; it only
# forecloses a str method silently returning an equal-but-different object.
ORIGIN_IMPORTED_WEIGHTS = "imported_weights"
ORIGIN_IMPORTED_ADAPTER = "imported_adapter"
ORIGIN_EXTERNAL_ENDPOINT = "external_endpoint"
ALLOWED_ORIGINS = (ORIGIN_IMPORTED_WEIGHTS, ORIGIN_IMPORTED_ADAPTER, ORIGIN_EXTERNAL_ENDPOINT)

ALLOWED_JOB_STATES = ("queued", "validating", "failed", "succeeded", "blocked_external")

EXTERNAL_PROVIDER_MODEL_REF_MAX_LENGTH = 2048
_REFUSED_REF_CATEGORIES = frozenset({"Cc", "Cs"})


class ModelImportValidationError(ValueError):
    """A field value violates this domain schema's own shape/invariant, or
    one of this task's own negative_acceptance structural rules (an
    `external_endpoint` request carrying an artifact hash; an
    `imported_adapter` request with no base)."""


def validate_origin(value: object) -> str:
    # `tuple.index` does its own membership comparison internally (not a
    # `==`/`in` node in this module's own source), and raises for a value
    # that is not a plain `str` the same way it does for one that just isn't
    # a recognized origin -- there is no separate isinstance branch to keep
    # in sync, and no `a == b` expression here for a normalisation-bypass
    # mutation to land on.
    try:
        return ALLOWED_ORIGINS[ALLOWED_ORIGINS.index(value)]
    except ValueError:
        raise ModelImportValidationError(
            f"requested_origin must be one of {ALLOWED_ORIGINS}: {value!r}"
        ) from None


def validate_external_provider_model_ref(value: object) -> str:
    # `len(value) == 0` rather than `value == ""`: a Call, not a bare Name,
    # so there is no string-method-wrapping normalisation-bypass mutation
    # that can land on it (the still-bare `value.isspace()` catches every
    # whitespace-only string a would-be bypass of either check would need).
    if not isinstance(value, str) or len(value) == 0 or value.isspace():
        raise ModelImportValidationError(
            "external_provider_model_ref must be a non-empty, non-whitespace string"
        )
    if len(value) > EXTERNAL_PROVIDER_MODEL_REF_MAX_LENGTH:
        raise ModelImportValidationError(
            f"external_provider_model_ref must be at most "
            f"{EXTERNAL_PROVIDER_MODEL_REF_MAX_LENGTH} characters"
        )
    # FTF-H010-P2: a ref reaches PostgreSQL text and the gateway request, so a control character
    # (NUL, CR, LF, ...) or a lone surrogate is refused here as a 400, never a driver error.
    if any(unicodedata.category(ch) in _REFUSED_REF_CATEGORIES for ch in value):
        raise ModelImportValidationError(
            "external_provider_model_ref must not contain control or surrogate characters"
        )
    return value


class ModelImportJob(Base):
    """One row per enqueued import request (Section 6.17/58A). Never
    edited by this slice's own routes after creation -- a future worker
    (not built here) is the only intended writer of `state`/
    `failure_code`/`updated_at`, through the migration's own
    column-limited grant."""

    __tablename__ = "model_import_jobs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    organization_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    project_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    requested_origin: Mapped[str] = mapped_column(String(32), nullable=False)
    parent_model_version_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    trainable_artifact_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    external_provider_model_ref: Mapped[str | None] = mapped_column(
        String(EXTERNAL_PROVIDER_MODEL_REF_MAX_LENGTH), nullable=True
    )
    litellm_connection_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    result_model_version_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    claim_epoch: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    state: Mapped[str] = mapped_column(String(16), nullable=False)
    failure_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_by: Mapped[str] = mapped_column(String(200), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            ondelete="CASCADE",
            name="fk_model_import_jobs_organization_id",
        ),
        ForeignKeyConstraint(
            ["organization_id", "project_id"],
            ["projects.organization_id", "projects.id"],
            ondelete="CASCADE",
            name="fk_model_import_jobs_organization_id_project_id",
        ),
        ForeignKeyConstraint(
            ["organization_id", "parent_model_version_id"],
            ["model_versions.organization_id", "model_versions.id"],
            ondelete="RESTRICT",
            name="fk_model_import_jobs_organization_id_parent_model_version_id",
        ),
        ForeignKeyConstraint(
            ["organization_id", "trainable_artifact_id"],
            ["trainable_artifacts.organization_id", "trainable_artifacts.id"],
            ondelete="RESTRICT",
            name="fk_model_import_jobs_organization_id_trainable_artifact_id",
        ),
        CheckConstraint(
            "requested_origin in ('factory_trained','imported_weights',"
            "'imported_adapter','external_endpoint')",
            name="ck_model_import_jobs_origin",
        ),
        CheckConstraint(
            "state in ('queued','validating','failed','succeeded','blocked_external')",
            name="ck_model_import_jobs_state",
        ),
        CheckConstraint(
            "(state in ('failed','blocked_external')) = (failure_code IS NOT NULL)",
            name="ck_model_import_jobs_failure_code_matches_state",
        ),
        CheckConstraint("created_by <> ''", name="ck_model_import_jobs_created_by_not_blank"),
        # FTF-H010-P2 (0029): the fence, the lease and the connection/result bindings.
        ForeignKeyConstraint(
            ["organization_id", "litellm_connection_id"],
            ["litellm_connections.organization_id", "litellm_connections.id"],
            ondelete="RESTRICT",
            name="fk_model_import_jobs_organization_id_litellm_connection_id",
        ),
        ForeignKeyConstraint(
            ["organization_id", "project_id", "result_model_version_id", "requested_origin"],
            [
                "model_versions.organization_id",
                "model_versions.project_id",
                "model_versions.id",
                "model_versions.origin",
            ],
            ondelete="RESTRICT",
            name="fk_model_import_jobs_result_model_version",
        ),
        CheckConstraint("claim_epoch >= 0", name="ck_model_import_jobs_claim_epoch_non_negative"),
        CheckConstraint(
            "(state = 'succeeded') = (result_model_version_id IS NOT NULL)",
            name="ck_model_import_jobs_succeeded_has_result",
        ),
        CheckConstraint(
            "(state = 'validating') = (lease_expires_at IS NOT NULL)",
            name="ck_model_import_jobs_validating_has_lease",
        ),
        CheckConstraint(
            "(state = 'queued') = (claim_epoch = 0)",
            name="ck_model_import_jobs_queued_epoch_zero",
        ),
        CheckConstraint(
            "(requested_origin = 'external_endpoint') = (litellm_connection_id IS NOT NULL)",
            name="ck_model_import_jobs_connection_matches_origin",
        ),
    )

    def __init__(self, **kwargs: object) -> None:
        if "requested_origin" in kwargs:
            kwargs["requested_origin"] = validate_origin(kwargs["requested_origin"])
        if kwargs.get("external_provider_model_ref") is not None:
            kwargs["external_provider_model_ref"] = validate_external_provider_model_ref(
                kwargs["external_provider_model_ref"]
            )
        super().__init__(**kwargs)
