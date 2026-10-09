"""ModelImportJob CRUD (FTF-H010-P1, Section 6.17/58A/68.1).

Enqueue/get/list only -- the same "no edit path through this module"
discipline `model_version_crud.py` applies to `model_versions` itself,
for the same reason: nothing in this slice's own acceptance implements
the worker that would ever move a job out of `queued` (disclosed in
`model_import_routes.py`'s own module docstring), so there is no
legitimate caller-facing mutation surface here at all yet. The migration's
own column-limited `UPDATE (state, failure_code, updated_at)` grant
exists for that future worker, not for this module.

No new authorization primitive: project ownership reuses
`model_version_crud.py`'s own already-tested `_authorize_project_or_raise`
directly (the identical `ProjectNotFoundError`/`authorize_resource_ownership`
guard FTF-1001 already proved for this exact resource shape -- ``.../
projects/{project_id}/...``), a referenced parent `ModelVersion` reuses
`get_model_version`, and a referenced `TrainableArtifact` reuses
`app.artifacts.artifact_crud.get_artifact` -- the same "one organization
IDOR guard, never re-derived" discipline every prior CRUD module in this
chain already follows.

This module validates only the request's own *shape* at enqueue time
(Law 17: "POST validates the shape and enqueues; it never claims success
synchronously") -- whether a referenced id exists and belongs to the
caller's own organization/project, and this task's own structural
negative_acceptance rules (round 2): `requested_origin='factory_trained'`
is refused outright -- Section 68.1 names exactly three import forms
(endpoint/full-weight/adapter), never Factory's own direct training
path; an `external_endpoint` request may not carry
`trainable_artifact_id`, and its `external_provider_model_ref` must be a
real, non-blank string (never merely non-empty -- a whitespace-only
value is refused too); an `imported_weights` or `imported_adapter`
request requires `trainable_artifact_id`, and `imported_adapter` also
requires `parent_model_version_id` (the base). It never checks a
referenced `TrainableArtifact`'s own readiness or a parent
`ModelVersion`'s own compatibility -- that deeper, real-provider/weights/
GPU-dependent check is exactly what the (not yet built) `validating`
state is for.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.artifacts.artifact_crud import ArtifactNotFoundError, get_artifact
from app.auth.audit_event_writer import write_audit_event
from app.auth.tenant_isolation_query_guards import execute_tenant_scoped
from app.db.tenant_context import TenantContext
from app.model_versions.model_import_schema import (
    ORIGIN_EXTERNAL_ENDPOINT,
    ORIGIN_IMPORTED_ADAPTER,
    ORIGIN_IMPORTED_WEIGHTS,
    ModelImportJob,
    ModelImportValidationError,
    validate_external_provider_model_ref,
    validate_origin,
)
from app.model_versions.model_version_crud import (
    ModelVersionNotFoundError,
    ProjectNotFoundError,
    _authorize_project_or_raise,
)
from app.model_versions.model_version_crud import (
    _fetch_or_raise as _fetch_model_version_or_raise,
)

__all__ = [
    "ArtifactNotFoundError",
    "ModelImportJobNotFoundError",
    "ModelVersionNotFoundError",
    "ProjectNotFoundError",
    "create_model_import_job",
    "get_model_import_job",
    "list_model_import_jobs",
]


class ModelImportJobNotFoundError(LookupError):
    """No import job with this id exists in the caller's own organization
    *and* project -- identical whether the id is missing entirely,
    belongs to a different organization, or belongs to a different
    project within the caller's own organization."""


@dataclass(frozen=True)
class ActorInfo:
    actor_type: str
    actor_id: str
    request_id: str


def create_model_import_job(
    session: Session,
    context: TenantContext,
    *,
    project_id: uuid.UUID,
    requested_origin: str,
    parent_model_version_id: uuid.UUID | None,
    trainable_artifact_id: uuid.UUID | None,
    external_provider_model_ref: str | None,
    actor: ActorInfo,
) -> ModelImportJob:
    """Raises `ProjectNotFoundError`, `ModelVersionNotFoundError`,
    `ArtifactNotFoundError` or `ModelImportValidationError`; on any raise
    nothing was written."""
    _authorize_project_or_raise(session, context, project_id)

    # `ModelImportJob.__init__` already re-validates `requested_origin`
    # (defense in depth); this call surfaces the same error before any
    # referenced id is even looked up, so a bad origin never triggers an
    # extra, pointless lookup of a well-formed parent/artifact id.
    #
    # `validate_origin` always returns one of its own three named module-level
    # singletons, never the caller's own string object -- so the dispatch
    # below compares by identity (`is`), not value (`==`). A real caller's
    # value that is merely *equal* to one of these (any string a caller can
    # actually construct) was already turned into the singleton by
    # `validate_origin` on the line above; nothing downstream can reach this
    # dispatch holding some other, differently-cased or whitespace-padded
    # object that `is` would wrongly refuse.
    requested_origin = validate_origin(requested_origin)

    if requested_origin is ORIGIN_EXTERNAL_ENDPOINT:
        if trainable_artifact_id is not None:
            raise ModelImportValidationError(
                "an external_endpoint import must not carry trainable_artifact_id -- "
                "no local weight bytes back an endpoint import (this task's own "
                "negative_acceptance: no fictitious hash)"
            )
        # Raises ModelImportValidationError for None, empty or whitespace-only
        # (`not "  "` is False in Python -- a bare truthiness check would have
        # let a blank-looking ref through).
        validate_external_provider_model_ref(external_provider_model_ref)
    elif requested_origin is ORIGIN_IMPORTED_ADAPTER:
        if parent_model_version_id is None:
            raise ModelImportValidationError(
                "an imported_adapter import requires parent_model_version_id (the base)"
            )
        if trainable_artifact_id is None:
            raise ModelImportValidationError(
                "an imported_adapter import requires trainable_artifact_id (the adapter weights)"
            )
    elif requested_origin is ORIGIN_IMPORTED_WEIGHTS:
        if trainable_artifact_id is None:
            raise ModelImportValidationError(
                "an imported_weights import requires trainable_artifact_id"
            )

    if parent_model_version_id is not None:
        # ArtifactNotFoundError-style IDOR guard, reused directly: a
        # foreign/missing parent is the identical ModelVersionNotFoundError
        # a real model-version read already raises.
        _fetch_model_version_or_raise(session, context, project_id, parent_model_version_id)

    if trainable_artifact_id is not None:
        get_artifact(session, context, trainable_artifact_id)  # ArtifactNotFoundError if foreign

    now = datetime.now(UTC)
    job = ModelImportJob(
        id=uuid.uuid4(),
        organization_id=context.organization_id,
        project_id=project_id,
        requested_origin=requested_origin,
        parent_model_version_id=parent_model_version_id,
        trainable_artifact_id=trainable_artifact_id,
        external_provider_model_ref=external_provider_model_ref,
        state="queued",
        failure_code=None,
        created_by=actor.actor_id,
        created_at=now,
        updated_at=now,
    )
    session.add(job)
    session.flush()
    write_audit_event(
        session,
        organization_id=context.organization_id,
        actor_type=actor.actor_type,
        actor_id=actor.actor_id,
        action="model_import_job.create",
        resource_type="model_import_job",
        resource_id=str(job.id),
        request_id=actor.request_id,
        details={
            "project_id": str(project_id),
            "requested_origin": requested_origin,
            "parent_model_version_id": (
                None if parent_model_version_id is None else str(parent_model_version_id)
            ),
            "trainable_artifact_id": (
                None if trainable_artifact_id is None else str(trainable_artifact_id)
            ),
        },
    )
    return job


def _fetch_job_or_raise(
    session: Session, context: TenantContext, project_id: uuid.UUID, job_id: uuid.UUID
) -> ModelImportJob:
    if not isinstance(job_id, uuid.UUID):
        raise ModelImportJobNotFoundError(f"job_id must be a uuid.UUID, got {job_id!r}")
    statement = select(ModelImportJob).where(ModelImportJob.id == job_id)
    result = execute_tenant_scoped(session, context.organization_id, statement)
    job: ModelImportJob | None = result.scalars().one_or_none()
    if job is None:
        raise ModelImportJobNotFoundError(f"model import job {job_id} not found")
    if job.project_id != project_id:
        raise ModelImportJobNotFoundError(
            f"model import job {job_id} does not belong to project {project_id}"
        )
    return job


def get_model_import_job(
    session: Session, context: TenantContext, project_id: uuid.UUID, job_id: uuid.UUID
) -> ModelImportJob:
    return _fetch_job_or_raise(session, context, project_id, job_id)


def list_model_import_jobs(
    session: Session, context: TenantContext, project_id: uuid.UUID
) -> list[ModelImportJob]:
    statement = (
        select(ModelImportJob)
        .where(ModelImportJob.project_id == project_id)
        .order_by(ModelImportJob.created_at)
    )
    result = execute_tenant_scoped(session, context.organization_id, statement)
    return list(result.scalars().all())
