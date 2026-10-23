"""Model role assignments and the facts behind a version's allowed actions (FTF-H010-P4;
Section 0.12, Section 17 paragraphs 3-4, Section 27, Section 58A, Section 68.1 paragraph 4).

`assign_role` / `revoke_role` / `list_roles` go through the same certified composition FTF-1001 and
FTF-H010-P1 use: `TenantContext` + `tenant_scoped_session_for` open the unit of work,
`authorize_resource_ownership` (via `get_model_version`) proves the version belongs to the caller's
organization and project before any row is read or written -- identical for a missing and a foreign
id -- and every query runs through `execute_tenant_scoped`.

The database is the authority for every conflict (Section 65.1: the route never trusts a pre-check
it could race): an assignment is inserted inside a savepoint and the violated index or trigger
names the outcome -- the active-role index `role_already_assigned`, the incumbent index
`incumbent_exists`, the self-judging trigger `self_judging_role_conflict`, the validation trigger
`import_not_validated`. A retried request carries the same idempotency key and the same request
digest and replays the stored row with no new row and no audit event; the same key with another
digest is `idempotency_key_reused`.

`build_action_facts` reads the version, its succeeded import job, its artifact, the license gate,
the FTF-1002 lineage manifest (for an adapter, against the exact parent base) and the active roles
from the database, never from request text, and returns the `ModelActionFacts`
`workers/training/continuation.py` decides on. A fact that cannot be established is fail closed
(a missing artifact is an unaccepted license, a missing or unvalidated base is an incompatible one).
"""

from __future__ import annotations

import hmac
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import Session
from workers.training.continuation import (
    ORIGIN_EXTERNAL_ENDPOINT,
    ORIGIN_FACTORY_TRAINED,
    ORIGIN_IMPORTED_ADAPTER,
    ORIGIN_IMPORTED_WEIGHTS,
    ContinuationFactsError,
    ModelActionFacts,
    canonical_origin,
)

from app.artifacts.artifact_lineage_manifest import (
    ArtifactLineageError,
    build_artifact_lineage_manifest,
)
from app.artifacts.artifact_schema import TrainableArtifact
from app.artifacts.license_acceptance_workflow import evaluate_license_gate
from app.auth.audit_event_writer import write_audit_event
from app.auth.tenant_isolation_query_guards import execute_tenant_scoped
from app.db.tenant_context import TenantContext
from app.model_versions.model_import_schema import ModelImportJob
from app.model_versions.model_role_schema import (
    REVOKE_REASON_BY_ACTOR,
    ModelRoleAssignment,
    request_sha256,
)
from app.model_versions.model_version_crud import ActorInfo, get_model_version
from app.model_versions.model_version_schema import ModelVersion

__all__ = [
    "RoleConflictError",
    "assign_role",
    "build_action_facts",
    "is_validated",
    "list_roles",
    "revoke_role",
]

# violated index / trigger CONSTRAINT -> the stable conflict code (the DB is the authority)
_CONFLICT_BY_NAME = {
    "uq_model_role_assignments_active": "role_already_assigned",
    "uq_model_role_assignments_incumbent": "incumbent_exists",
    "tr_model_role_assignments_self_judging": "self_judging_role_conflict",
    "tr_model_role_assignments_insert_guard_validated": "import_not_validated",
}
_IDEMPOTENCY_INDEX = "uq_model_role_assignments_idempotency"


class RoleConflictError(Exception):
    """A 409-class outcome. `code` is one of `role_already_assigned`, `incumbent_exists`,
    `self_judging_role_conflict`, `role_not_assigned`, `idempotency_key_reused`,
    `import_not_validated`; the text is the code only."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class AssignmentResult:
    assignment: ModelRoleAssignment
    replayed: bool


def _violated_name(exc: DBAPIError) -> str | None:
    diag = getattr(exc.orig, "diag", None)
    if diag is None:
        return None
    name = diag.constraint_name
    return name if isinstance(name, str) else None


def _origin_of(version: ModelVersion) -> str | None:
    """The module's own singleton for the version's origin, or None when it is not exactly one of
    the four (a row the database CHECK would have refused). Callers compare with `is`."""
    try:
        return canonical_origin(version.origin)
    except ContinuationFactsError:
        return None


def is_validated(session: Session, context: TenantContext, version: ModelVersion) -> bool:
    """A version is validated when the Factory trained it, or a succeeded import job names it as
    its result. An FTF-1001 synchronous create has neither."""
    if _origin_of(version) is ORIGIN_FACTORY_TRAINED:
        return True
    statement = select(ModelImportJob.id).where(
        ModelImportJob.organization_id == context.organization_id,
        ModelImportJob.project_id == version.project_id,
        ModelImportJob.result_model_version_id == version.id,
        ModelImportJob.state == "succeeded",
    )
    return execute_tenant_scoped(session, context.organization_id, statement).first() is not None


def _find_by_idempotency_key(
    session: Session, context: TenantContext, project_id: uuid.UUID, key: str
) -> ModelRoleAssignment | None:
    statement = select(ModelRoleAssignment).where(
        ModelRoleAssignment.organization_id == context.organization_id,
        ModelRoleAssignment.project_id == project_id,
        ModelRoleAssignment.idempotency_key == key,
    )
    return execute_tenant_scoped(session, context.organization_id, statement).scalars().first()


def _replay_or_reuse(existing: ModelRoleAssignment, digest: str) -> AssignmentResult:
    if not hmac.compare_digest(existing.request_sha256.encode(), digest.encode()):
        raise RoleConflictError("idempotency_key_reused")
    return AssignmentResult(assignment=existing, replayed=True)


def assign_role(
    session: Session,
    context: TenantContext,
    *,
    project_id: uuid.UUID,
    model_version_id: uuid.UUID,
    role: str,
    idempotency_key: str,
    actor: ActorInfo,
) -> AssignmentResult:
    version = get_model_version(session, context, project_id, model_version_id)
    digest = request_sha256(project_id, model_version_id, role)
    existing = _find_by_idempotency_key(session, context, project_id, idempotency_key)
    if existing is not None:
        return _replay_or_reuse(existing, digest)
    if not is_validated(session, context, version):
        raise RoleConflictError("import_not_validated")
    assignment = ModelRoleAssignment(
        organization_id=context.organization_id,
        project_id=project_id,
        model_version_id=model_version_id,
        role=role,
        assigned_by=actor.actor_id,
        request_id=actor.request_id,
        idempotency_key=idempotency_key,
        request_sha256=digest,
    )
    try:
        with session.begin_nested():
            session.add(assignment)
            session.flush()
    except (IntegrityError, DBAPIError) as exc:
        name = _violated_name(exc)
        if name == _IDEMPOTENCY_INDEX:
            raced = _find_by_idempotency_key(session, context, project_id, idempotency_key)
            if raced is None:
                raise
            return _replay_or_reuse(raced, digest)
        code = _CONFLICT_BY_NAME.get(name) if name is not None else None
        if code is None:
            raise
        raise RoleConflictError(code) from None
    write_audit_event(
        session,
        organization_id=context.organization_id,
        actor_type=actor.actor_type,
        actor_id=actor.actor_id,
        action="model_role.assign",
        resource_type="model_role_assignment",
        resource_id=str(assignment.id),
        request_id=actor.request_id,
    )
    return AssignmentResult(assignment=assignment, replayed=False)


def revoke_role(
    session: Session,
    context: TenantContext,
    *,
    project_id: uuid.UUID,
    model_version_id: uuid.UUID,
    role: str,
    actor: ActorInfo,
) -> ModelRoleAssignment:
    get_model_version(session, context, project_id, model_version_id)
    statement = (
        select(ModelRoleAssignment)
        .where(
            ModelRoleAssignment.organization_id == context.organization_id,
            ModelRoleAssignment.project_id == project_id,
            ModelRoleAssignment.model_version_id == model_version_id,
            ModelRoleAssignment.role == role,
            ModelRoleAssignment.revoked_at.is_(None),
        )
        .with_for_update()
    )
    assignment = (
        execute_tenant_scoped(session, context.organization_id, statement).scalars().first()
    )
    if assignment is None:
        raise RoleConflictError("role_not_assigned")
    assignment.revoked_at = datetime.now(UTC)
    assignment.revoked_by = actor.actor_id
    assignment.revoke_reason_code = REVOKE_REASON_BY_ACTOR
    session.flush()
    write_audit_event(
        session,
        organization_id=context.organization_id,
        actor_type=actor.actor_type,
        actor_id=actor.actor_id,
        action="model_role.revoke",
        resource_type="model_role_assignment",
        resource_id=str(assignment.id),
        request_id=actor.request_id,
    )
    return assignment


def list_roles(
    session: Session, context: TenantContext, project_id: uuid.UUID, model_version_id: uuid.UUID
) -> list[ModelRoleAssignment]:
    get_model_version(session, context, project_id, model_version_id)
    statement = (
        select(ModelRoleAssignment)
        .where(
            ModelRoleAssignment.organization_id == context.organization_id,
            ModelRoleAssignment.project_id == project_id,
            ModelRoleAssignment.model_version_id == model_version_id,
        )
        .order_by(ModelRoleAssignment.assigned_at, ModelRoleAssignment.id)
    )
    return list(execute_tenant_scoped(session, context.organization_id, statement).scalars().all())


def _active_roles(
    session: Session, context: TenantContext, version: ModelVersion
) -> frozenset[str]:
    statement = select(ModelRoleAssignment.role).where(
        ModelRoleAssignment.organization_id == context.organization_id,
        ModelRoleAssignment.project_id == version.project_id,
        ModelRoleAssignment.model_version_id == version.id,
        ModelRoleAssignment.revoked_at.is_(None),
    )
    return frozenset(execute_tenant_scoped(session, context.organization_id, statement).scalars())


def _artifact_of(
    session: Session, context: TenantContext, artifact_id: uuid.UUID | None
) -> TrainableArtifact | None:
    if artifact_id is None:
        return None
    statement = select(TrainableArtifact).where(
        TrainableArtifact.id == artifact_id,
        TrainableArtifact.organization_id == context.organization_id,
    )
    return execute_tenant_scoped(session, context.organization_id, statement).scalars().first()


def _license_accepted(artifact: TrainableArtifact | None) -> bool:
    return artifact is not None and evaluate_license_gate(artifact).accepted


def _base_compatible(
    session: Session,
    context: TenantContext,
    version: ModelVersion,
    artifact: TrainableArtifact | None,
) -> bool:
    """An adapter is compatible only while its exact parent base is an imported-weights version
    with a succeeded import, a ready artifact with an accepted license, and the FTF-1002 lineage
    manifest still binds the pair. Anything missing is incompatible (fail closed)."""
    if version.parent_model_version_id is None or artifact is None:
        return False
    statement = select(ModelVersion).where(
        ModelVersion.id == version.parent_model_version_id,
        ModelVersion.organization_id == context.organization_id,
        ModelVersion.project_id == version.project_id,
    )
    parent = execute_tenant_scoped(session, context.organization_id, statement).scalars().first()
    if parent is None or _origin_of(parent) is not ORIGIN_IMPORTED_WEIGHTS:
        return False
    if not is_validated(session, context, parent):
        return False
    base_artifact = _artifact_of(session, context, parent.trainable_artifact_id)
    if not _license_accepted(base_artifact):
        return False
    try:
        build_artifact_lineage_manifest(version, artifact, resolved_base_artifact=base_artifact)
    except ArtifactLineageError:
        return False
    return True


def build_action_facts(
    session: Session, context: TenantContext, version: ModelVersion
) -> ModelActionFacts:
    """The server-side facts for `evaluate_model_actions`. No checkpoint fact and no available
    prerequisite is ever assumed: FTF-0713's checkpoint manifest, the qualification lifecycle,
    deployment routing and the status lifecycle do not exist yet."""
    origin = canonical_origin(version.origin)
    is_endpoint = origin is ORIGIN_EXTERNAL_ENDPOINT
    artifact = (
        None if is_endpoint else _artifact_of(session, context, version.trainable_artifact_id)
    )
    license_accepted = None if is_endpoint else _license_accepted(artifact)
    base_compatible = (
        _base_compatible(session, context, version, artifact)
        if origin is ORIGIN_IMPORTED_ADAPTER
        else None
    )
    return ModelActionFacts(
        model_version_id=str(version.id),
        origin=origin,
        validated=is_validated(session, context, version),
        license_accepted=license_accepted,
        base_compatible=base_compatible,
        roles=_active_roles(session, context, version),
        new_run_id=str(uuid.uuid4()),
        model_ref=version.external_provider_model_ref if is_endpoint else None,
    )
