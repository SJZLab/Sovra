"""ModelVersion CRUD (FTF-1001, Section 34/35/36/45/65/68/77/81).

Create/get/list a project-scoped `ModelVersion`
(`apps/api/app/model_versions/model_version_schema.py`). There is no edit
or delete/archive surface at all -- see that module's own docstring for
why every model version is immutable by construction, unlike every prior
versioned resource in this chain.

Every real query goes through the same certified composition FTF-0201/
FTF-0301/FTF-0401/FTF-0404 already proved for exactly this class of
resource:

  - `TenantContext`/`tenant_scoped_session_for` (FTF-H003) open the unit
    of work and bind the transaction-local RLS tenant context;
  - `authorize_resource_ownership` (FTF-H003) proves a project ID (at
    creation) and a model-version ID (at get) belong to the caller's own
    authenticated organization *before* any row is read or mutated --
    denied identically whether the ID does not exist at all or belongs to
    a different organization;
  - `execute_tenant_scoped` (FTF-0105) is what every actual SELECT in this
    module runs through, never a bare `session.execute(...)`/`session.get(...)`.

No new authorization primitive is introduced for the parent
`TrainableArtifact` reference either: `create_model_version` reuses
FTF-0301's own already-certified `app.artifacts.artifact_crud.get_artifact`
directly (built on the identical `authorize_resource_ownership` guard)
rather than re-deriving that IDOR check a second time -- this is what
`model_version_schema.py`'s own docstring point 3 ("cross-tenant parent
... denied") actually enforces.

Role gating (Section 45's own role table): no sentence names a role for
model-version import specifically. This module follows FTF-0301's own
precedent exactly, since importing a trained/adapter artifact into a real
model version is the direct continuation of the same "datasets/training,
no production approval" mandate Section 45 assigns `ml_engineer`:
`MODEL_VERSION_WRITE_ROLES` is `{ml_engineer, organization_admin}`.
`MODEL_VERSION_READ_ROLES` is every role Section 45 names, matching every
prior resource's own wide-read baseline for a resource with no dedicated
restrictive read sentence.

Every mutating operation (creation only -- there is no other mutation)
writes an audit event via FTF-0104's `write_audit_event` in the *same*
transaction as the insert it describes, identical to every prior task's
own discipline in this chain.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.artifacts.artifact_crud import ArtifactNotFoundError, get_artifact
from app.artifacts.artifact_schema import (
    TrainableArtifactValidationError,
    validate_weights_digest,
)
from app.auth.audit_event_writer import write_audit_event
from app.auth.resource_authorization import ResourceAuthorizationError, authorize_resource_ownership
from app.auth.tenant_isolation_query_guards import execute_tenant_scoped
from app.db.tenant_context import TenantContext
from app.model_versions.model_version_schema import ModelVersion
from app.projects.project_schema import Project

__all__ = [
    "ActorInfo",
    "ArtifactNotFoundError",
    "ModelVersionArtifactStateError",
    "ModelVersionNotFoundError",
    "ProjectNotFoundError",
    "create_model_version",
    "get_model_version",
    "list_model_versions",
]

_TABLE = ModelVersion.__table__
_ID_COLUMN = _TABLE.c.id
_ORG_COLUMN = _TABLE.c.organization_id

_PROJECT_TABLE = Project.__table__
_PROJECT_ID_COLUMN = _PROJECT_TABLE.c.id
_PROJECT_ORG_COLUMN = _PROJECT_TABLE.c.organization_id


class ProjectNotFoundError(LookupError):
    """No project with this ID exists in the caller's own organization --
    raised identically whether the ID is missing entirely or belongs to a
    different organization (the same IDOR discipline FTF-0401/FTF-0404's
    own `ProjectNotFoundError` already established)."""


class ModelVersionNotFoundError(LookupError):
    """No model version with this ID exists in the caller's own
    organization *and* project -- identical whether the ID is missing
    entirely, belongs to a different organization, or belongs to a
    different project within the caller's own organization."""


class ModelVersionArtifactStateError(ValueError):
    """The referenced `TrainableArtifact` cannot back a real
    `full_weight`/`adapter` import -- this task's own negative_acceptance
    made real: a `registered` (still-mutable) or `archived` (closed)
    artifact is refused, and a `ready` artifact whose `weights_digest` is
    missing or not a valid `sha256:` digest (the shared
    `validate_weights_digest`; should be structurally impossible, but checked
    directly as defense in depth -- the same "re-validate, never merely
    trust an earlier check" discipline `artifact_crud.py`'s own
    `apply_mark_ready` already applies to `source_uri`) is refused."""


@dataclass(frozen=True)
class ActorInfo:
    """Who is performing this mutation, for the audit event it writes.
    `actor_id` is always the authenticated principal's own `subject`,
    never a client-supplied string."""

    actor_type: str
    actor_id: str
    request_id: str


def _authorize_project_or_raise(
    session: Session, context: TenantContext, project_id: uuid.UUID
) -> None:
    if not isinstance(project_id, uuid.UUID):
        raise ProjectNotFoundError(f"project_id must be a uuid.UUID, got {project_id!r}")
    try:
        authorize_resource_ownership(
            session, context, _PROJECT_TABLE, _PROJECT_ID_COLUMN, _PROJECT_ORG_COLUMN, project_id
        )
    except ResourceAuthorizationError as exc:
        raise ProjectNotFoundError(str(exc)) from exc


def _fetch_or_raise(
    session: Session,
    context: TenantContext,
    project_id: uuid.UUID,
    model_version_id: uuid.UUID,
) -> ModelVersion:
    if not isinstance(model_version_id, uuid.UUID):
        raise ModelVersionNotFoundError(
            f"model_version_id must be a uuid.UUID, got {model_version_id!r}"
        )
    try:
        authorize_resource_ownership(
            session, context, _TABLE, _ID_COLUMN, _ORG_COLUMN, model_version_id
        )
    except ResourceAuthorizationError as exc:
        raise ModelVersionNotFoundError(str(exc)) from exc
    statement = select(ModelVersion).where(ModelVersion.id == model_version_id)
    result = execute_tenant_scoped(session, context.organization_id, statement)
    row = result.scalars().one_or_none()
    if row is None:
        raise ModelVersionNotFoundError(
            f"model version {model_version_id} vanished mid-transaction"
        )
    if row.project_id != project_id:
        # A project-scoped resource's own deeper IDOR case, the same
        # precedent `tool_definition_crud.py`'s own `_fetch_or_raise`
        # already established: a real row in the caller's own
        # organization, just under a *different* project than the URL
        # names, is refused identically to a missing row.
        raise ModelVersionNotFoundError(
            f"model version {model_version_id} does not belong to project {project_id}"
        )
    return row


def _next_version_number(session: Session, context: TenantContext, project_id: uuid.UUID) -> int:
    statement = select(func.coalesce(func.max(ModelVersion.version_number), 0)).where(
        ModelVersion.project_id == project_id
    )
    result = execute_tenant_scoped(session, context.organization_id, statement)
    return int(result.scalar_one()) + 1


def create_model_version(
    session: Session,
    context: TenantContext,
    *,
    project_id: uuid.UUID,
    import_kind: str,
    endpoint_reference: str | None = None,
    trainable_artifact_id: uuid.UUID | None = None,
    actor: ActorInfo,
) -> ModelVersion:
    """Validates and inserts a new, immutable `ModelVersion`.

    For `full_weight`/`adapter`, `weights_digest`/`base_model_revision`
    are never accepted from the caller -- they are always copied from the
    real, already-resolved parent `TrainableArtifact` row this function
    itself fetches (see this module's own docstring and
    `model_version_schema.py`'s own docstring point 3), so a client can
    never claim lineage to bytes the artifact itself does not actually
    carry.
    """
    _authorize_project_or_raise(session, context, project_id)

    weights_digest: str | None = None
    base_model_revision: str | None = None
    if trainable_artifact_id is not None:
        artifact = get_artifact(session, context, trainable_artifact_id)
        if artifact.status != "ready":
            raise ModelVersionArtifactStateError(
                f"trainable artifact {artifact.id} is {artifact.status!r} -- only a "
                "'ready' (resolved, immutable) artifact may back a real model-version "
                "import; a 'registered' artifact is still mutable and an 'archived' "
                "one is a closed record"
            )
        try:
            weights_digest = validate_weights_digest(artifact.weights_digest)
        except TrainableArtifactValidationError as exc:
            raise ModelVersionArtifactStateError(
                f"trainable artifact {artifact.id} has no valid resolved weights_digest -- "
                "a missing or malformed weights/hash cannot back a model-version import"
            ) from exc
        base_model_revision = artifact.base_model_revision

    version_number = _next_version_number(session, context, project_id)
    model_version = ModelVersion(
        organization_id=context.organization_id,
        project_id=project_id,
        version_number=version_number,
        import_kind=import_kind,
        trainable_artifact_id=trainable_artifact_id,
        weights_digest=weights_digest,
        endpoint_reference=endpoint_reference,
        base_model_revision=base_model_revision,
        created_by=actor.actor_id,
    )
    session.add(model_version)
    session.flush()
    write_audit_event(
        session,
        organization_id=context.organization_id,
        actor_type=actor.actor_type,
        actor_id=actor.actor_id,
        action="model_version.create",
        resource_type="model_version",
        resource_id=str(model_version.id),
        request_id=actor.request_id,
    )
    return model_version


def get_model_version(
    session: Session, context: TenantContext, project_id: uuid.UUID, model_version_id: uuid.UUID
) -> ModelVersion:
    return _fetch_or_raise(session, context, project_id, model_version_id)


def list_model_versions(
    session: Session, context: TenantContext, project_id: uuid.UUID
) -> list[ModelVersion]:
    statement = (
        select(ModelVersion)
        .where(ModelVersion.project_id == project_id)
        .order_by(ModelVersion.version_number)
    )
    result = execute_tenant_scoped(session, context.organization_id, statement)
    return list(result.scalars().all())
