"""AgentManifest CRUD (FTF-04A19, Section 6.8D/7A.6-7A.7/45/65/66/77/81).

Create/get/list/update-content/submit-for-review/lock a project-scoped
`AgentManifest` (`apps/api/app/target_connectors/agent_manifest_schema.py`).
See that module's own docstring for the full versioning/hash/status-
transition design this CRUD layer implements.

Every real query goes through the same certified composition FTF-0201/
FTF-0301/FTF-0401/FTF-0404/FTF-1001/FTF-04A07 already proved for exactly
this class of resource:

  - `TenantContext`/`tenant_scoped_session_for` (FTF-H003) open the unit
    of work and bind the transaction-local RLS tenant context;
  - `authorize_resource_ownership` (FTF-H003) proves a project ID (at
    creation/list) and a manifest ID (at get/update/transition), and the
    referenced snapshot/connection IDs (at creation), belong to the
    caller's own authenticated organization *before* any row is read or
    mutated -- denied identically whether an ID does not exist at all or
    belongs to a different organization;
  - `execute_tenant_scoped` (FTF-0105) is what every actual SELECT in this
    module runs through, never a bare `session.execute(...)`/`session.get(...)`.

No new authorization primitive is introduced for the parent `Project`
reference: `_authorize_project_or_raise` below is the exact same
`authorize_resource_ownership` composition `model_version_crud.py`'s own
function of the same name already established.

## Snapshot-to-project cross-parent check (disclosed, not hidden)

Section 6.8D's own `target_source_snapshot_id` field names *which*
snapshot a manifest was built from; `TargetSourceSnapshot` itself has no
`project_id` at all (Section 6.8B -- it belongs to a
`TargetApplicationConnection`, which in turn belongs to a project). A
snapshot genuinely belonging to a *different* project's own connection
cannot be caught by any `CHECK` constraint (it would have to compare
across three tables), so `create_agent_manifest` fetches the snapshot,
then its owning connection, and refuses
(`SnapshotProjectMismatchError`) unless `connection.project_id` equals the
manifest's own `project_id` -- the same "cross-parent invariant enforced
in the CRUD layer, tenant safety enforced by composite foreign keys and
`authorize_resource_ownership`" division of responsibility FTF-04A07's own
`create_target_source_snapshot` already established for a structurally
identical reason.

## "Locked manifest mutation ... blocks [it]" -- re-validated, never trusted

`update_agent_manifest_content`/`submit_for_review`/`lock_agent_manifest`
each fetch the manifest's *current* row and status first and refuse (with
`AgentManifestLockedError`/`AgentManifestValidationError`, via
`validate_status_transition`) before writing anything, exactly the
discipline this task's own schema module docstring describes -- never
trusting a caller-supplied "it's still draft" assumption.

Role gating: Section 45 names no sentence for "manifest" specifically.
This module follows the closest real sentence, "workspace_admin: project
administration" -- a manifest is squarely project-level configuration,
distinct from FTF-04A01's own "organization_admin: connections" mapping
(a manifest is not itself a credential-bearing connection).
`AGENT_MANIFEST_WRITE_ROLES` is `{workspace_admin, organization_admin}`;
`AGENT_MANIFEST_READ_ROLES` is every role Section 45 names, matching every
prior resource's own wide-read baseline for a resource with no dedicated
restrictive read sentence (see `agent_manifest_routes.py`).

Every mutating operation writes an audit event via FTF-0104's
`write_audit_event` in the *same* transaction as the write it describes,
identical to every prior task's own discipline in this chain.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from types import EllipsisType

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.auth.audit_event_writer import write_audit_event
from app.auth.resource_authorization import ResourceAuthorizationError, authorize_resource_ownership
from app.auth.tenant_isolation_query_guards import execute_tenant_scoped
from app.db.tenant_context import TenantContext
from app.projects.project_schema import Project
from app.target_connectors.agent_manifest_schema import (
    CONTENT_FIELDS,
    CRITICAL_LOCK_FIELDS,
    AgentManifest,
    compute_manifest_sha256,
    validate_status_transition,
)
from app.target_connectors.target_application_connection_schema import TargetApplicationConnection
from app.target_connectors.target_source_snapshot_schema import TargetSourceSnapshot

__all__ = [
    "ActorInfo",
    "AgentManifestIncompleteError",
    "AgentManifestLockedError",
    "AgentManifestNotFoundError",
    "ProjectNotFoundError",
    "SnapshotNotFoundError",
    "SnapshotProjectMismatchError",
    "create_agent_manifest",
    "get_agent_manifest",
    "list_agent_manifests",
    "lock_agent_manifest",
    "submit_for_review",
    "update_agent_manifest_content",
]

# Content fields callers may set/edit; excludes target_source_snapshot_id
# (fixed at creation -- the manifest's own real evidence anchor, never
# silently re-pointed) and agent_name (a plain string, always passed as
# its own explicit keyword, never folded into this JSON-field dict --
# doing both would collide as a duplicate keyword argument).
_MUTABLE_CONTENT_FIELDS = tuple(
    f for f in CONTENT_FIELDS if f not in ("target_source_snapshot_id", "agent_name")
)

_TABLE = AgentManifest.__table__
_ID_COLUMN = _TABLE.c.id
_ORG_COLUMN = _TABLE.c.organization_id

_PROJECT_TABLE = Project.__table__
_PROJECT_ID_COLUMN = _PROJECT_TABLE.c.id
_PROJECT_ORG_COLUMN = _PROJECT_TABLE.c.organization_id

_SNAPSHOT_TABLE = TargetSourceSnapshot.__table__
_SNAPSHOT_ID_COLUMN = _SNAPSHOT_TABLE.c.id
_SNAPSHOT_ORG_COLUMN = _SNAPSHOT_TABLE.c.organization_id


class ProjectNotFoundError(LookupError):
    """No project with this ID exists in the caller's own organization --
    raised identically whether the ID is missing entirely or belongs to a
    different organization (the same IDOR discipline every prior task in
    this chain already established)."""


class SnapshotNotFoundError(LookupError):
    """No target source snapshot with this ID exists in the caller's own
    organization."""


class SnapshotProjectMismatchError(ValueError):
    """The referenced `TargetSourceSnapshot` genuinely exists (and belongs
    to the caller's own organization) but its owning connection belongs to
    a *different* project than the manifest being created -- see this
    module's own docstring for why no `CHECK` constraint can express this."""


class AgentManifestNotFoundError(LookupError):
    """No agent manifest with this ID exists in the caller's own
    organization *and* project -- identical whether the ID is missing
    entirely, belongs to a different organization, or belongs to a
    different project within the caller's own organization."""


class AgentManifestLockedError(ValueError):
    """The manifest's current status is `locked`/`superseded` -- no
    content mutation or (for `lock_agent_manifest`) re-lock is permitted
    (this task's own negative_acceptance, "Locked manifest mutation ...
    blocks [it]")."""


class AgentManifestIncompleteError(ValueError):
    """`lock_agent_manifest` was called while one or more
    `CRITICAL_LOCK_FIELDS` is still null -- this task's own
    negative_acceptance, "missing critical evidence/model/runtime tuple
    blocks activation."""


@dataclass(frozen=True)
class ActorInfo:
    """Who is performing this mutation, for the audit event it writes and
    (for `lock_agent_manifest`) the real `approved_by` attribution.
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


def _fetch_snapshot_and_check_project(
    session: Session, context: TenantContext, project_id: uuid.UUID, snapshot_id: uuid.UUID
) -> TargetSourceSnapshot:
    if not isinstance(snapshot_id, uuid.UUID):
        raise SnapshotNotFoundError(
            f"target_source_snapshot_id must be a uuid.UUID, got {snapshot_id!r}"
        )
    try:
        authorize_resource_ownership(
            session,
            context,
            _SNAPSHOT_TABLE,
            _SNAPSHOT_ID_COLUMN,
            _SNAPSHOT_ORG_COLUMN,
            snapshot_id,
        )
    except ResourceAuthorizationError as exc:
        raise SnapshotNotFoundError(str(exc)) from exc
    statement = select(TargetSourceSnapshot).where(TargetSourceSnapshot.id == snapshot_id)
    result = execute_tenant_scoped(session, context.organization_id, statement)
    snapshot = result.scalars().one_or_none()
    if snapshot is None:
        raise SnapshotNotFoundError(
            f"target source snapshot {snapshot_id} vanished mid-transaction"
        )

    connection_statement = select(TargetApplicationConnection).where(
        TargetApplicationConnection.id == snapshot.target_application_connection_id
    )
    connection_result = execute_tenant_scoped(
        session, context.organization_id, connection_statement
    )
    connection = connection_result.scalars().one_or_none()
    if connection is None or connection.project_id != project_id:
        raise SnapshotProjectMismatchError(
            f"target source snapshot {snapshot_id} does not belong (via its own "
            f"connection) to project {project_id}"
        )
    return snapshot


def _fetch_or_raise(
    session: Session, context: TenantContext, project_id: uuid.UUID, manifest_id: uuid.UUID
) -> AgentManifest:
    if not isinstance(manifest_id, uuid.UUID):
        raise AgentManifestNotFoundError(f"manifest_id must be a uuid.UUID, got {manifest_id!r}")
    try:
        authorize_resource_ownership(session, context, _TABLE, _ID_COLUMN, _ORG_COLUMN, manifest_id)
    except ResourceAuthorizationError as exc:
        raise AgentManifestNotFoundError(str(exc)) from exc
    statement = select(AgentManifest).where(AgentManifest.id == manifest_id)
    result = execute_tenant_scoped(session, context.organization_id, statement)
    row = result.scalars().one_or_none()
    if row is None:
        raise AgentManifestNotFoundError(f"agent manifest {manifest_id} vanished mid-transaction")
    if row.project_id != project_id:
        raise AgentManifestNotFoundError(
            f"agent manifest {manifest_id} does not belong to project {project_id}"
        )
    return row


def _next_version(session: Session, context: TenantContext, project_id: uuid.UUID) -> int:
    statement = select(func.coalesce(func.max(AgentManifest.version), 0)).where(
        AgentManifest.project_id == project_id
    )
    result = execute_tenant_scoped(session, context.organization_id, statement)
    return int(result.scalar_one()) + 1


def create_agent_manifest(
    session: Session,
    context: TenantContext,
    *,
    project_id: uuid.UUID,
    target_source_snapshot_id: uuid.UUID,
    actor: ActorInfo,
    agent_name: str | None = None,
    content: dict[str, object] | None = None,
) -> AgentManifest:
    """Creates a new `draft` `AgentManifest` at the next version number for
    this project. `content` may set any of `_MUTABLE_CONTENT_FIELDS`;
    unrecognized keys are refused, never silently ignored."""
    _authorize_project_or_raise(session, context, project_id)
    _fetch_snapshot_and_check_project(session, context, project_id, target_source_snapshot_id)

    content = content or {}
    unknown = set(content) - set(_MUTABLE_CONTENT_FIELDS)
    if unknown:
        raise ValueError(f"unknown AgentManifest content field(s): {sorted(unknown)}")

    version = _next_version(session, context, project_id)
    manifest = AgentManifest(
        organization_id=context.organization_id,
        project_id=project_id,
        target_source_snapshot_id=target_source_snapshot_id,
        version=version,
        status="draft",
        agent_name=agent_name,
        created_by=actor.actor_id,
        **{field: content.get(field) for field in _MUTABLE_CONTENT_FIELDS},
    )
    session.add(manifest)
    session.flush()
    write_audit_event(
        session,
        organization_id=context.organization_id,
        actor_type=actor.actor_type,
        actor_id=actor.actor_id,
        action="agent_manifest.create",
        resource_type="agent_manifest",
        resource_id=str(manifest.id),
        request_id=actor.request_id,
    )
    return manifest


def get_agent_manifest(
    session: Session, context: TenantContext, project_id: uuid.UUID, manifest_id: uuid.UUID
) -> AgentManifest:
    return _fetch_or_raise(session, context, project_id, manifest_id)


def list_agent_manifests(
    session: Session, context: TenantContext, project_id: uuid.UUID
) -> list[AgentManifest]:
    _authorize_project_or_raise(session, context, project_id)
    statement = (
        select(AgentManifest)
        .where(AgentManifest.project_id == project_id)
        .order_by(AgentManifest.version)
    )
    result = execute_tenant_scoped(session, context.organization_id, statement)
    return list(result.scalars().all())


def update_agent_manifest_content(
    session: Session,
    context: TenantContext,
    *,
    project_id: uuid.UUID,
    manifest_id: uuid.UUID,
    actor: ActorInfo,
    agent_name: str | None | EllipsisType = ...,
    content: dict[str, object] | None = None,
) -> AgentManifest:
    """Edits content fields on a `draft`/`review_required` manifest.
    `agent_name` uses the sentinel default `...` so "not supplied" (leave
    unchanged) is distinguishable from "explicitly set to null."
    """
    manifest = _fetch_or_raise(session, context, project_id, manifest_id)
    if manifest.status not in ("draft", "review_required"):
        raise AgentManifestLockedError(
            f"agent manifest {manifest_id} is {manifest.status!r} -- content can only be "
            "edited while draft or review_required (this task's own negative_acceptance)"
        )

    content = content or {}
    unknown = set(content) - set(_MUTABLE_CONTENT_FIELDS)
    if unknown:
        raise ValueError(f"unknown AgentManifest content field(s): {sorted(unknown)}")

    for field, value in content.items():
        setattr(manifest, field, value)
    if agent_name is not ...:
        manifest.agent_name = agent_name

    session.flush()
    write_audit_event(
        session,
        organization_id=context.organization_id,
        actor_type=actor.actor_type,
        actor_id=actor.actor_id,
        action="agent_manifest.update_content",
        resource_type="agent_manifest",
        resource_id=str(manifest.id),
        request_id=actor.request_id,
    )
    return manifest


def submit_for_review(
    session: Session,
    context: TenantContext,
    *,
    project_id: uuid.UUID,
    manifest_id: uuid.UUID,
    actor: ActorInfo,
) -> AgentManifest:
    """`draft` -> `review_required`."""
    manifest = _fetch_or_raise(session, context, project_id, manifest_id)
    validate_status_transition(manifest.status, "review_required")
    manifest.status = "review_required"
    session.flush()
    write_audit_event(
        session,
        organization_id=context.organization_id,
        actor_type=actor.actor_type,
        actor_id=actor.actor_id,
        action="agent_manifest.submit_for_review",
        resource_type="agent_manifest",
        resource_id=str(manifest.id),
        request_id=actor.request_id,
    )
    return manifest


def lock_agent_manifest(
    session: Session,
    context: TenantContext,
    *,
    project_id: uuid.UUID,
    manifest_id: uuid.UUID,
    actor: ActorInfo,
) -> AgentManifest:
    """`review_required` -> `locked`: checks `CRITICAL_LOCK_FIELDS`,
    computes `manifest_sha256`, sets `approved_by`/`approved_at`, and --
    the real supersession mechanic this module's own schema docstring
    describes -- transitions any other already-`locked` manifest for the
    *same* project to `superseded`, all in this same transaction.
    """
    manifest = _fetch_or_raise(session, context, project_id, manifest_id)
    validate_status_transition(manifest.status, "locked")

    missing = [field for field in CRITICAL_LOCK_FIELDS if getattr(manifest, field) is None]
    if missing:
        raise AgentManifestIncompleteError(
            f"agent manifest {manifest_id} is missing required field(s) {missing} -- "
            "this task's own negative_acceptance: missing critical evidence/model/"
            "runtime tuple blocks activation"
        )

    content = {field: getattr(manifest, field) for field in CONTENT_FIELDS}
    now = datetime.now(UTC)

    previously_locked_statement = select(AgentManifest).where(
        AgentManifest.project_id == project_id, AgentManifest.status == "locked"
    )
    previously_locked_result = execute_tenant_scoped(
        session, context.organization_id, previously_locked_statement
    )
    for previous in previously_locked_result.scalars().all():
        previous.status = "superseded"
        write_audit_event(
            session,
            organization_id=context.organization_id,
            actor_type=actor.actor_type,
            actor_id=actor.actor_id,
            action="agent_manifest.superseded",
            resource_type="agent_manifest",
            resource_id=str(previous.id),
            request_id=actor.request_id,
        )
    # `ix_agent_manifests_project_id_locked` is a partial UNIQUE INDEX, not
    # a deferrable CONSTRAINT (Postgres has no deferrable partial unique
    # constraint) -- it is checked immediately per statement, not at
    # transaction end. Flushing the "supersede the old row" UPDATE here,
    # before the new row's own UPDATE below, forces the old row's status
    # to leave 'locked' first, so the two real UPDATE statements this
    # flush emits are never both live against that index at once.
    session.flush()

    manifest.status = "locked"
    manifest.manifest_sha256 = compute_manifest_sha256(content)
    manifest.approved_by = actor.actor_id
    manifest.approved_at = now
    session.flush()
    write_audit_event(
        session,
        organization_id=context.organization_id,
        actor_type=actor.actor_type,
        actor_id=actor.actor_id,
        action="agent_manifest.lock",
        resource_type="agent_manifest",
        resource_id=str(manifest.id),
        request_id=actor.request_id,
    )
    return manifest
