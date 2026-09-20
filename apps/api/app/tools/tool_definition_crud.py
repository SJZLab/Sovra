"""Tool schema CRUD (FTF-0404, Section 6.9/7A/22/45/65-66/77/81).

Create/get/list/edit a project-scoped `ToolDefinition`
(`apps/api/app/tools/tool_definition_schema.py`). Every real query goes
through the same certified composition FTF-0201/FTF-0301/FTF-0401 already
proved for exactly this class of resource:

  - `TenantContext`/`tenant_scoped_session_for` (FTF-H003) open the unit
    of work and bind the transaction-local RLS tenant context;
  - `authorize_resource_ownership` (FTF-H003) proves a project ID (at
    creation) and a tool-definition ID (at get/edit) belong to the
    caller's own authenticated organization *before* any row is read or
    mutated -- denied identically whether the ID does not exist at all or
    belongs to a different organization;
  - `execute_tenant_scoped` (FTF-0105) is what every actual SELECT in this
    module runs through, never a bare `session.execute(...)`/`session.get(...)`.

Role gating (Section 45's own role table): no sentence names a role for
tool-schema authoring specifically, so this module follows FTF-0401's own
precedent exactly -- "workspace_admin: project administration" is the
nearest, and only, sentence naming a role for this class of per-project
configuration work, so `TOOL_WRITE_ROLES` is `{workspace_admin,
organization_admin}`. `TOOL_READ_ROLES` is every role Section 45 names,
matching FTF-0301/FTF-0401's own wide-read baseline for a resource with no
dedicated restrictive read sentence.

Every mutating operation writes an audit event via FTF-0104's
`write_audit_event` in the *same* transaction as the mutation it
describes, identical to FTF-0201/FTF-0301/FTF-0401's own discipline.

This task's own negative_acceptance ("Editing used tool schema creates
new version and drift, never changes historical manifest in place") is
this module's own central job: `apply_new_version` is the one and only
pure, DB-free core that ever produces a new version, and it never mutates
the row it replaces except to set `superseded_at`/`updated_at` -- see its
own docstring below for the full reasoning.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.audit_event_writer import write_audit_event
from app.auth.resource_authorization import ResourceAuthorizationError, authorize_resource_ownership
from app.auth.tenant_isolation_query_guards import execute_tenant_scoped
from app.db.tenant_context import TenantContext
from app.projects.project_schema import Project
from app.tools.tool_definition_schema import (
    ToolDefinition,
    compute_schema_hash,
    validate_description,
    validate_schema_object,
    validate_side_effect_class,
)

__all__ = [
    "ActorInfo",
    "ProjectNotFoundError",
    "ToolDefinitionNotFoundError",
    "ToolDefinitionStateError",
    "apply_new_version",
    "create_tool_definition",
    "edit_tool_definition",
    "get_tool_definition",
    "list_tool_definitions",
]

_TABLE = ToolDefinition.__table__
_ID_COLUMN = _TABLE.c.id
_ORG_COLUMN = _TABLE.c.organization_id

_PROJECT_TABLE = Project.__table__
_PROJECT_ID_COLUMN = _PROJECT_TABLE.c.id
_PROJECT_ORG_COLUMN = _PROJECT_TABLE.c.organization_id


class ProjectNotFoundError(LookupError):
    """No project with this ID exists in the caller's own organization --
    raised identically whether the ID is missing entirely or belongs to a
    different organization (the same IDOR discipline FTF-0401's own
    `WorkspaceNotFoundError` established for its own parent reference)."""


class ToolDefinitionNotFoundError(LookupError):
    """No tool definition with this ID exists in the caller's own
    organization *and* project -- identical whether the ID is missing
    entirely, belongs to a different organization, or (a project-scoped
    resource's own deeper case) belongs to a different project within the
    caller's own organization."""


class ToolDefinitionStateError(ValueError):
    """The requested operation is not valid for this row's current state
    -- specifically, editing anything other than the one current
    (`superseded_at IS NULL`) version of a tool."""


@dataclass(frozen=True)
class ActorInfo:
    """Who is performing this mutation, for the audit event it writes and
    for `created_by` attribution. `actor_id` is always the authenticated
    principal's own `subject`, never a client-supplied string."""

    actor_type: str
    actor_id: str
    request_id: str


def _now() -> datetime:
    return datetime.now(UTC)


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
    tool_definition_id: uuid.UUID,
) -> ToolDefinition:
    if not isinstance(tool_definition_id, uuid.UUID):
        raise ToolDefinitionNotFoundError(
            f"tool_definition_id must be a uuid.UUID, got {tool_definition_id!r}"
        )
    try:
        authorize_resource_ownership(
            session, context, _TABLE, _ID_COLUMN, _ORG_COLUMN, tool_definition_id
        )
    except ResourceAuthorizationError as exc:
        raise ToolDefinitionNotFoundError(str(exc)) from exc
    statement = select(ToolDefinition).where(ToolDefinition.id == tool_definition_id)
    result = execute_tenant_scoped(session, context.organization_id, statement)
    row = result.scalars().one_or_none()
    if row is None:
        raise ToolDefinitionNotFoundError(
            f"tool definition {tool_definition_id} vanished mid-transaction"
        )
    if row.project_id != project_id:
        # A project-scoped resource's own deeper IDOR case: this row is a
        # real row in the caller's own organization, just under a
        # *different* project than the URL names -- refused identically
        # to a missing row, never leaked as "wrong project" (that would
        # confirm the row's existence to a caller who has no claim on it
        # under this path).
        raise ToolDefinitionNotFoundError(
            f"tool definition {tool_definition_id} does not belong to project {project_id}"
        )
    return row


def create_tool_definition(
    session: Session,
    context: TenantContext,
    *,
    project_id: uuid.UUID,
    name: str,
    description: str | None,
    input_schema_json: dict[str, object],
    output_schema_json: dict[str, object],
    side_effect_class: str,
    requires_confirmation: bool,
    is_critical: bool,
    actor: ActorInfo,
) -> ToolDefinition:
    """Validates and inserts version 1 of a new tool definition. A second
    create with the same `(project_id, name)` while a current version
    already exists is refused by the real partial unique index in the
    migration (never only an application-level check) -- the same
    "trust the real database constraint" discipline FTF-0401's own
    duplicate-project-name case already established."""
    _authorize_project_or_raise(session, context, project_id)
    tool = ToolDefinition(
        organization_id=context.organization_id,
        project_id=project_id,
        name=name,
        description=description,
        input_schema_json=input_schema_json,
        output_schema_json=output_schema_json,
        side_effect_class=side_effect_class,
        requires_confirmation=requires_confirmation,
        is_critical=is_critical,
        version=1,
        schema_hash=compute_schema_hash(input_schema_json, output_schema_json),
        previous_version_id=None,
        superseded_at=None,
        created_by=actor.actor_id,
    )
    session.add(tool)
    session.flush()
    write_audit_event(
        session,
        organization_id=context.organization_id,
        actor_type=actor.actor_type,
        actor_id=actor.actor_id,
        action="tool_definition.create",
        resource_type="tool_definition",
        resource_id=str(tool.id),
        request_id=actor.request_id,
    )
    return tool


def get_tool_definition(
    session: Session, context: TenantContext, project_id: uuid.UUID, tool_definition_id: uuid.UUID
) -> ToolDefinition:
    return _fetch_or_raise(session, context, project_id, tool_definition_id)


def list_tool_definitions(
    session: Session, context: TenantContext, project_id: uuid.UUID
) -> list[ToolDefinition]:
    """Only the *current* version of each tool -- the historical lineage
    a superseded row carries is reachable by ID (`get_tool_definition`),
    not through this listing, the same "list shows current state, history
    is reachable by ID" shape a versioned resource needs."""
    statement = (
        select(ToolDefinition)
        .where(
            ToolDefinition.project_id == project_id,
            ToolDefinition.superseded_at.is_(None),
        )
        .order_by(ToolDefinition.name)
    )
    result = execute_tenant_scoped(session, context.organization_id, statement)
    return list(result.scalars().all())


def apply_new_version(
    current: ToolDefinition,
    *,
    description: str | None,
    input_schema_json: dict[str, object] | None,
    output_schema_json: dict[str, object] | None,
    side_effect_class: str | None,
    requires_confirmation: bool | None,
    is_critical: bool | None,
    created_by: str,
) -> ToolDefinition:
    """The pure, DB-free core of `edit_tool_definition` -- this task's own
    negative_acceptance made real. Refuses (`ToolDefinitionStateError`) to
    edit anything but the one current version. `None` for an optional
    field means "keep the current version's own value for it" -- a
    partial edit never has to restate every field, the same convention
    `apply_edits` established for `Project`.

    Never mutates `current`'s own content fields
    (`description`/`input_schema_json`/`output_schema_json`/
    `side_effect_class`/`requires_confirmation`/`is_critical`) -- only
    `superseded_at`/`updated_at`, so a caller holding a reference to
    `current` from before this call can still read its exact,
    byte-identical historical content afterward. The new version is a
    genuinely separate object, never `current` mutated in place and
    re-tagged."""
    if current.superseded_at is not None:
        raise ToolDefinitionStateError(
            f"tool definition {current.id} is not the current version -- "
            "only the current version can be edited"
        )

    new_description = (
        validate_description(description) if description is not None else current.description
    )
    new_input_schema = (
        input_schema_json if input_schema_json is not None else current.input_schema_json
    )
    new_output_schema = (
        output_schema_json if output_schema_json is not None else current.output_schema_json
    )
    new_side_effect_class = (
        validate_side_effect_class(side_effect_class)
        if side_effect_class is not None
        else current.side_effect_class
    )
    new_requires_confirmation = (
        requires_confirmation
        if requires_confirmation is not None
        else current.requires_confirmation
    )
    new_is_critical = is_critical if is_critical is not None else current.is_critical

    now = _now()
    new_version = ToolDefinition(
        organization_id=current.organization_id,
        project_id=current.project_id,
        name=current.name,
        description=new_description,
        input_schema_json=new_input_schema,
        output_schema_json=new_output_schema,
        side_effect_class=new_side_effect_class,
        requires_confirmation=new_requires_confirmation,
        is_critical=new_is_critical,
        version=current.version + 1,
        schema_hash=compute_schema_hash(new_input_schema, new_output_schema),
        previous_version_id=current.id,
        superseded_at=None,
        created_by=created_by,
        created_at=now,
        updated_at=now,
    )
    current.superseded_at = now
    current.updated_at = now
    return new_version


def edit_tool_definition(
    session: Session,
    context: TenantContext,
    project_id: uuid.UUID,
    tool_definition_id: uuid.UUID,
    *,
    description: str | None = None,
    input_schema_json: dict[str, object] | None = None,
    output_schema_json: dict[str, object] | None = None,
    side_effect_class: str | None = None,
    requires_confirmation: bool | None = None,
    is_critical: bool | None = None,
    actor: ActorInfo,
) -> ToolDefinition:
    if input_schema_json is not None:
        input_schema_json = validate_schema_object(input_schema_json, "input_schema_json")
    if output_schema_json is not None:
        output_schema_json = validate_schema_object(output_schema_json, "output_schema_json")

    current = _fetch_or_raise(session, context, project_id, tool_definition_id)
    new_version = apply_new_version(
        current,
        description=description,
        input_schema_json=input_schema_json,
        output_schema_json=output_schema_json,
        side_effect_class=side_effect_class,
        requires_confirmation=requires_confirmation,
        is_critical=is_critical,
        created_by=actor.actor_id,
    )
    session.add(new_version)
    session.flush()
    write_audit_event(
        session,
        organization_id=context.organization_id,
        actor_type=actor.actor_type,
        actor_id=actor.actor_id,
        action="tool_definition.edit",
        resource_type="tool_definition",
        resource_id=str(new_version.id),
        request_id=actor.request_id,
        details={"previous_version_id": str(current.id), "version": new_version.version},
    )
    return new_version
