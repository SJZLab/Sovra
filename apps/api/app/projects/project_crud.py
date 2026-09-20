"""Project CRUD (FTF-0401, Section 6.8/7A/22/45/65-66/77/81).

Create/get/list/edit/save-ui-state/archive a workspace-scoped `Project`
(`apps/api/app/projects/project_schema.py`). Every real query goes through
the same certified composition FTF-0201/FTF-0301 already proved for
exactly this class of resource:

  - `TenantContext`/`tenant_scoped_session_for` (FTF-H003) open the unit
    of work and bind the transaction-local RLS tenant context;
  - `authorize_resource_ownership` (FTF-H003) proves a project ID (and,
    at creation, a workspace ID) belongs to the caller's own authenticated
    organization *before* any row is read or mutated -- denied identically
    whether the ID does not exist at all or belongs to a different
    organization;
  - `execute_tenant_scoped` (FTF-0105) is what every actual SELECT in this
    module runs through, never a bare `session.execute(...)`/`session.get(...)`.

Role gating (Section 45's own role table): "workspace_admin: project
administration" is the only sentence naming a role for this class of
work, so `PROJECT_WRITE_ROLES` is `{workspace_admin, organization_admin}`
(`organization_admin` retains its blanket admin capability, the same
precedent every prior write-role set in this repository already applies).
`PROJECT_READ_ROLES` is every role Section 45 names, matching FTF-0301's
own wide-read baseline for a resource with no dedicated restrictive read
sentence.

Every mutating operation writes an audit event via FTF-0104's
`write_audit_event` in the *same* transaction as the mutation it
describes, identical to FTF-0201/FTF-0301's own discipline.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.audit_event_writer import write_audit_event
from app.auth.organization_workspace_schema import Workspace, validate_name
from app.auth.resource_authorization import ResourceAuthorizationError, authorize_resource_ownership
from app.auth.tenant_isolation_query_guards import execute_tenant_scoped
from app.db.tenant_context import TenantContext
from app.projects.project_schema import (
    Project,
    ProjectValidationError,
    initial_status_for,
    validate_agent_objective,
    validate_description,
    validate_security_profile,
    validate_ui_state,
)

__all__ = [
    "ActorInfo",
    "ProjectConcurrencyError",
    "ProjectNotFoundError",
    "ProjectStateError",
    "WorkspaceNotFoundError",
    "apply_archive",
    "apply_edits",
    "apply_ui_state_save",
    "archive_project",
    "create_project",
    "get_project",
    "list_projects",
    "save_ui_state",
    "update_project",
]

_TABLE = Project.__table__
_ID_COLUMN = _TABLE.c.id
_ORG_COLUMN = _TABLE.c.organization_id

_WORKSPACE_TABLE = Workspace.__table__
_WORKSPACE_ID_COLUMN = _WORKSPACE_TABLE.c.id
_WORKSPACE_ORG_COLUMN = _WORKSPACE_TABLE.c.organization_id


class ProjectNotFoundError(LookupError):
    """No project with this ID exists in the caller's own organization --
    raised identically whether the ID is missing entirely or belongs to a
    different organization (this task's own IDOR discipline, matching
    FTF-0201/FTF-0301's negative_acceptance precedent)."""


class WorkspaceNotFoundError(LookupError):
    """No workspace with this ID exists in the caller's own organization --
    the same identical-to-missing discipline, applied to the parent
    reference a new project names at creation time."""


class ProjectStateError(ValueError):
    """The requested transition is not valid for the project's current
    state (e.g. editing an archived project)."""


class ProjectConcurrencyError(ValueError):
    """`expected_row_version` did not match the project's real current
    `row_version` -- Section 6's own "mutable rows carry row_version for
    concurrency checks" made into a real compare-and-set refusal, the same
    concept Section 65.3 already applies to approval transitions."""


@dataclass(frozen=True)
class ActorInfo:
    """Who is performing this mutation, for the audit event it writes and
    for `created_by` attribution. `actor_id` is always the authenticated
    principal's own `subject`, never a client-supplied string (Section
    65.1's "never trusting an arbitrary... value")."""

    actor_type: str
    actor_id: str
    request_id: str


def _now() -> datetime:
    return datetime.now(UTC)


def _fetch_or_raise(session: Session, context: TenantContext, project_id: uuid.UUID) -> Project:
    if not isinstance(project_id, uuid.UUID):
        raise ProjectNotFoundError(f"project_id must be a uuid.UUID, got {project_id!r}")
    try:
        authorize_resource_ownership(session, context, _TABLE, _ID_COLUMN, _ORG_COLUMN, project_id)
    except ResourceAuthorizationError as exc:
        raise ProjectNotFoundError(str(exc)) from exc
    statement = select(Project).where(Project.id == project_id)
    result = execute_tenant_scoped(session, context.organization_id, statement)
    row = result.scalars().one_or_none()
    if row is None:
        raise ProjectNotFoundError(f"project {project_id} vanished mid-transaction")
    return row


def _authorize_workspace_or_raise(
    session: Session, context: TenantContext, workspace_id: uuid.UUID
) -> None:
    if not isinstance(workspace_id, uuid.UUID):
        raise WorkspaceNotFoundError(f"workspace_id must be a uuid.UUID, got {workspace_id!r}")
    try:
        authorize_resource_ownership(
            session,
            context,
            _WORKSPACE_TABLE,
            _WORKSPACE_ID_COLUMN,
            _WORKSPACE_ORG_COLUMN,
            workspace_id,
        )
    except ResourceAuthorizationError as exc:
        raise WorkspaceNotFoundError(str(exc)) from exc


def create_project(
    session: Session,
    context: TenantContext,
    *,
    workspace_id: uuid.UUID,
    name: str,
    project_type: str,
    description: str | None = None,
    agent_objective: str | None = None,
    security_profile: str = "standard",
    actor: ActorInfo,
) -> Project:
    """Validates and inserts a new project. `status` is always derived
    from `project_type` (`initial_status_for`), never client-supplied --
    this task's own negative_acceptance ("no silent generic downgrade")
    would be meaningless if a caller could simply POST `status: active`
    for an agentic project."""
    _authorize_workspace_or_raise(session, context, workspace_id)
    project = Project(
        organization_id=context.organization_id,
        workspace_id=workspace_id,
        name=name,
        description=description,
        project_type=project_type,
        agent_objective=agent_objective,
        security_profile=security_profile,
        budget_policy_id=None,
        status=initial_status_for(project_type),
        ui_state_json={},
        row_version=1,
        created_by=actor.actor_id,
    )
    session.add(project)
    session.flush()
    write_audit_event(
        session,
        organization_id=context.organization_id,
        actor_type=actor.actor_type,
        actor_id=actor.actor_id,
        action="project.create",
        resource_type="project",
        resource_id=str(project.id),
        request_id=actor.request_id,
    )
    return project


def get_project(session: Session, context: TenantContext, project_id: uuid.UUID) -> Project:
    return _fetch_or_raise(session, context, project_id)


def list_projects(session: Session, context: TenantContext) -> list[Project]:
    statement = (
        select(Project)
        .where(Project.organization_id == context.organization_id)
        .order_by(Project.created_at)
    )
    result = execute_tenant_scoped(session, context.organization_id, statement)
    return list(result.scalars().all())


def apply_edits(
    project: Project,
    *,
    name: str | None,
    description: str | None,
    agent_objective: str | None,
    security_profile: str | None,
    expected_row_version: int,
) -> bool:
    """The pure, DB-free core of `update_project`. Refuses
    (`ProjectStateError`) to touch an archived project -- a closed record,
    the same precedent FTF-0201/FTF-0301 established for a revoked
    connection / a ready-or-archived artifact. `project_type` is
    deliberately never accepted here -- it is immutable after creation
    (see `project_schema.py`'s own module docstring)."""
    if project.status == "archived":
        raise ProjectStateError(f"project {project.id} is archived and cannot be edited")
    if expected_row_version != project.row_version:
        raise ProjectConcurrencyError(
            f"project {project.id} has row_version {project.row_version}, "
            f"not the expected {expected_row_version} -- reload and retry"
        )

    changed = False
    if name is not None:
        project.name = validate_name(name)
        changed = True
    if description is not None:
        project.description = validate_description(description)
        changed = True
    if agent_objective is not None:
        project.agent_objective = validate_agent_objective(project.project_type, agent_objective)
        changed = True
    if security_profile is not None:
        project.security_profile = validate_security_profile(security_profile)
        changed = True

    if not changed:
        raise ProjectValidationError("update_project requires at least one field")
    project.row_version += 1
    project.updated_at = _now()
    return changed


def apply_ui_state_save(project: Project, *, ui_state: dict[str, object]) -> bool:
    """The pure, DB-free core of `save_ui_state`. Deliberately does NOT
    bump `row_version` -- this is ephemeral client-draft state, not a
    versioned setting this task's own optimistic-concurrency contract
    covers (see `apply_edits`'s own `expected_row_version` check).
    Refuses (`ProjectStateError`) on an archived project."""
    if project.status == "archived":
        raise ProjectStateError(f"project {project.id} is archived and cannot save UI state")
    project.ui_state_json = validate_ui_state(ui_state)
    project.updated_at = _now()
    return True


def apply_archive(project: Project) -> bool:
    """The pure, DB-free core of `archive_project`: idempotent, mirrors
    FTF-0201/FTF-0301's own `apply_revoke`/`apply_archive`."""
    if project.status == "archived":
        return False
    project.status = "archived"
    project.archived_at = _now()
    project.updated_at = project.archived_at
    return True


def update_project(
    session: Session,
    context: TenantContext,
    project_id: uuid.UUID,
    *,
    name: str | None = None,
    description: str | None = None,
    agent_objective: str | None = None,
    security_profile: str | None = None,
    expected_row_version: int,
    actor: ActorInfo,
) -> Project:
    project = _fetch_or_raise(session, context, project_id)
    apply_edits(
        project,
        name=name,
        description=description,
        agent_objective=agent_objective,
        security_profile=security_profile,
        expected_row_version=expected_row_version,
    )
    write_audit_event(
        session,
        organization_id=context.organization_id,
        actor_type=actor.actor_type,
        actor_id=actor.actor_id,
        action="project.update",
        resource_type="project",
        resource_id=str(project.id),
        request_id=actor.request_id,
    )
    return project


def save_ui_state(
    session: Session,
    context: TenantContext,
    project_id: uuid.UUID,
    *,
    ui_state: dict[str, object],
    actor: ActorInfo,
) -> Project:
    project = _fetch_or_raise(session, context, project_id)
    apply_ui_state_save(project, ui_state=ui_state)
    write_audit_event(
        session,
        organization_id=context.organization_id,
        actor_type=actor.actor_type,
        actor_id=actor.actor_id,
        action="project.save_ui_state",
        resource_type="project",
        resource_id=str(project.id),
        request_id=actor.request_id,
    )
    return project


def archive_project(
    session: Session,
    context: TenantContext,
    project_id: uuid.UUID,
    *,
    actor: ActorInfo,
) -> Project:
    project = _fetch_or_raise(session, context, project_id)
    if not apply_archive(project):
        return project
    write_audit_event(
        session,
        organization_id=context.organization_id,
        actor_type=actor.actor_type,
        actor_id=actor.actor_id,
        action="project.archive",
        resource_type="project",
        resource_id=str(project.id),
        request_id=actor.request_id,
    )
    return project
