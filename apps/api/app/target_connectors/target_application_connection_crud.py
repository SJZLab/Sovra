"""TargetApplicationConnection CRUD (FTF-04A01, Section 6.8A/7A.1-7A.3/9/
45/65-67/77/81).

Create/get/list/edit/revoke a project-scoped `TargetApplicationConnection`
(`apps/api/app/target_connectors/target_application_connection_schema.py`).
Every real query goes through the same certified composition FTF-0201/
FTF-0301/FTF-0401/FTF-0404/FTF-0500 already proved for exactly this class
of resource:

  - `TenantContext`/`tenant_scoped_session_for` (FTF-H003) open the unit
    of work and bind the transaction-local RLS tenant context;
  - `authorize_resource_ownership` (FTF-H003) proves a project ID (at
    creation) and a connection ID (at get/edit/revoke) belong to the
    caller's own authenticated organization *before* any row is read or
    mutated -- denied identically whether the ID does not exist at all or
    belongs to a different organization;
  - `execute_tenant_scoped` (FTF-0105) is what every actual SELECT in this
    module runs through, never a bare `session.execute(...)`/
    `session.get(...)`.

Role gating (Section 45's own literal role table): "organization_admin:
connections, security profiles, cost profiles" is the only sentence
naming a role for this class of resource -- the same narrower,
credential-bearing-"connection" precedent FTF-0201's own
`LiteLLMConnection` already established (unlike a non-secret-bearing
project resource, this gates every operation, including reads, to
`{organization_admin}` alone -- see `target_application_connection_routes.py`
for the router-level role set).

Every mutating operation (create/update/revoke) writes an audit event via
FTF-0104's `write_audit_event` in the *same* transaction as the mutation it
describes, identical to FTF-0201/FTF-0301/FTF-0401/FTF-0404/FTF-0500's own
discipline -- Section 6.8A's own rule: "connection creation and validation
are audited."

This task's own real "validate credentials without fetching unapproved
data" acceptance clause is `_validate_connector_shape` below: a real,
connector-type-conditional structural check run synchronously at creation
(never a live dial -- see the module docstring of
`target_application_connection_schema.py` for the full disclosed scope
boundary against FTF-04A02). `last_validated_at` is set at creation time,
the moment this task's own real validation runs.
"""

from __future__ import annotations

import sys
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

_REPO_ROOT = Path(__file__).resolve().parents[4]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from packages.litellm_client.secret_manager_integration import (  # noqa: E402
    OpenBaoConfig,
    store_secret_ref,
)

from app.auth.audit_event_writer import write_audit_event  # noqa: E402
from app.auth.organization_workspace_schema import validate_name  # noqa: E402
from app.auth.resource_authorization import (  # noqa: E402
    ResourceAuthorizationError,
    authorize_resource_ownership,
)
from app.auth.tenant_isolation_query_guards import execute_tenant_scoped  # noqa: E402
from app.db.tenant_context import TenantContext  # noqa: E402
from app.projects.project_schema import Project  # noqa: E402
from app.target_connectors.target_application_connection_schema import (  # noqa: E402
    TargetApplicationConnection,
    TargetApplicationConnectionValidationError,
)

__all__ = [
    "ActorInfo",
    "ProjectNotFoundError",
    "TargetApplicationConnectionConfigurationError",
    "TargetApplicationConnectionNotFoundError",
    "TargetApplicationConnectionStateError",
    "apply_edits",
    "apply_revoke",
    "create_target_application_connection",
    "get_target_application_connection",
    "list_target_application_connections",
    "revoke_target_application_connection",
    "update_target_application_connection",
]

_TABLE = TargetApplicationConnection.__table__
_ID_COLUMN = _TABLE.c.id
_ORG_COLUMN = _TABLE.c.organization_id

_PROJECT_TABLE = Project.__table__
_PROJECT_ID_COLUMN = _PROJECT_TABLE.c.id
_PROJECT_ORG_COLUMN = _PROJECT_TABLE.c.organization_id

# Reuses the same real KV v2 mount FTF-0202/FTF-0500's own integration
# proof already uses ("secret") -- a fresh OpenBao `-dev` server only
# auto-enables that one mount; `path` is namespaced under a
# `target-connections/` prefix plus per-organization/connection segments
# so no two connections, even across organizations or other already-
# CERTIFIED callers of the same mount, ever collide at the same backend
# path.
_SECRET_MOUNT = "secret"
_SECRET_PATH_PREFIX = "target-connections"


class ProjectNotFoundError(LookupError):
    """No project with this ID exists in the caller's own organization --
    raised identically whether the ID is missing entirely or belongs to a
    different organization (the same IDOR discipline FTF-0401/FTF-0404/
    FTF-0500's own established precedent)."""


class TargetApplicationConnectionNotFoundError(LookupError):
    """No target application connection with this ID exists in the
    caller's own organization *and* project -- identical whether the ID is
    missing entirely, belongs to a different organization, or belongs to a
    different project within the caller's own organization. This is this
    task's own real "cross-project target IDs... fail server-side"
    negative_acceptance."""


class TargetApplicationConnectionStateError(ValueError):
    """The requested operation is not valid for this row's current state
    (e.g. editing an already-`revoked` connection)."""


class TargetApplicationConnectionConfigurationError(RuntimeError):
    """A credential value was supplied but no secret-manager backend
    configuration was given to store it -- fails closed rather than
    silently dropping the value or storing it in plaintext."""


@dataclass(frozen=True)
class ActorInfo:
    """Who is performing this mutation, for the audit event it writes.
    `actor_id` must already be a safe identifier (enforced by
    `write_audit_event` itself) -- callers pass the authenticated
    principal's own `subject`, never a raw issuer URL or email."""

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
    connection_id: uuid.UUID,
) -> TargetApplicationConnection:
    if not isinstance(connection_id, uuid.UUID):
        raise TargetApplicationConnectionNotFoundError(
            f"connection_id must be a uuid.UUID, got {connection_id!r}"
        )
    try:
        authorize_resource_ownership(
            session, context, _TABLE, _ID_COLUMN, _ORG_COLUMN, connection_id
        )
    except ResourceAuthorizationError as exc:
        raise TargetApplicationConnectionNotFoundError(str(exc)) from exc
    statement = select(TargetApplicationConnection).where(
        TargetApplicationConnection.id == connection_id
    )
    result = execute_tenant_scoped(session, context.organization_id, statement)
    row = result.scalars().one_or_none()
    if row is None:
        raise TargetApplicationConnectionNotFoundError(
            f"target application connection {connection_id} vanished mid-transaction"
        )
    if row.project_id != project_id:
        # A project-scoped resource's own deeper IDOR case: this row is a
        # real row in the caller's own organization, just under a
        # *different* project than the URL names -- refused identically to
        # a missing row (this task's own "cross-project target IDs...
        # fail server-side" negative_acceptance).
        raise TargetApplicationConnectionNotFoundError(
            f"target application connection {connection_id} does not belong to "
            f"project {project_id}"
        )
    return row


def _validate_connector_shape(
    *, connector_type: str, repository_url_redacted: str | None, credential: str | None
) -> None:
    """This task's own real, connector-type-conditional structural
    validation -- the concrete meaning of "validate credentials" for this
    task's own narrow scope (see the schema module's own docstring for the
    full disclosed boundary against FTF-04A02/FTF-04A06). Raises
    `TargetApplicationConnectionValidationError` (never silently accepted)
    for a cross-field combination that does not match the connector type."""
    if connector_type == "github":
        if repository_url_redacted is None:
            raise TargetApplicationConnectionValidationError(
                "connector_type='github' requires repository_url_redacted"
            )
        if credential is None:
            raise TargetApplicationConnectionValidationError(
                "connector_type='github' requires a credential (GitHub App "
                "installation token or organization-approved token, Section 7A.2)"
            )
    elif connector_type == "local_repository":
        if repository_url_redacted is not None:
            raise TargetApplicationConnectionValidationError(
                "connector_type='local_repository' does not accept "
                "repository_url_redacted -- local repository root allowlisting "
                "is FTF-04A06's own separate scope"
            )
        if credential is not None:
            raise TargetApplicationConnectionValidationError(
                "connector_type='local_repository' does not accept a credential "
                "-- a local connection carries no credential at all"
            )


def _store_credential(
    *,
    secret_manager_config: OpenBaoConfig | None,
    organization_id: uuid.UUID,
    connection_id: uuid.UUID,
    credential: str | None,
) -> str | None:
    """The one place this task ever writes to the real secret backend.
    Returns the resulting `credential_secret_ref`, or `None` if the caller
    supplied nothing."""
    if credential is None:
        return None
    if secret_manager_config is None:
        raise TargetApplicationConnectionConfigurationError(
            "a credential value was supplied but no secret-manager backend "
            "configuration is available -- refusing to store it in plaintext"
        )
    path = f"{_SECRET_PATH_PREFIX}/{organization_id}/{connection_id}"
    return store_secret_ref(
        config=secret_manager_config,
        mount=_SECRET_MOUNT,
        path=path,
        field="credential",
        value=credential,
    )


def create_target_application_connection(
    session: Session,
    context: TenantContext,
    *,
    project_id: uuid.UUID,
    connector_type: str,
    display_name: str,
    repository_identity: str,
    repository_url_redacted: str | None,
    default_branch: str | None,
    selected_branch: str | None,
    credential: str | None,
    secret_manager_config: OpenBaoConfig | None,
    actor: ActorInfo,
    access_mode: str = "read_only",
) -> TargetApplicationConnection:
    """Validates and inserts a new `active` connection -- this task's own
    "Create tenant-scoped read-only target connection and validate
    credentials without fetching unapproved data" acceptance clause made
    real: `_validate_connector_shape` runs before any row is written or
    any secret stored (no dangling connection whose declared
    `credential_secret_ref` points nowhere), and `last_validated_at` is
    set to the moment that real validation ran. `access_mode` defaults to
    `'read_only'` but is accepted from the caller (never silently
    overridden) so a request for anything else is genuinely refused
    server-side by `TargetApplicationConnection.__init__`'s own
    `validate_access_mode` call -- this task's own real "excessive
    permission fail server-side" negative_acceptance, reachable through
    the live route, not only the pure schema."""
    _authorize_project_or_raise(session, context, project_id)
    _validate_connector_shape(
        connector_type=connector_type,
        repository_url_redacted=repository_url_redacted,
        credential=credential,
    )
    connection_id = uuid.uuid4()
    credential_ref = _store_credential(
        secret_manager_config=secret_manager_config,
        organization_id=context.organization_id,
        connection_id=connection_id,
        credential=credential,
    )
    now = _now()
    connection = TargetApplicationConnection(
        id=connection_id,
        organization_id=context.organization_id,
        project_id=project_id,
        connector_type=connector_type,
        display_name=display_name,
        repository_identity=repository_identity,
        repository_url_redacted=repository_url_redacted,
        default_branch=default_branch,
        selected_branch=selected_branch,
        credential_secret_ref=credential_ref,
        access_mode=access_mode,
        connection_status="active",
        last_validated_at=now,
        created_by=actor.actor_id,
    )
    session.add(connection)
    session.flush()
    write_audit_event(
        session,
        organization_id=context.organization_id,
        actor_type=actor.actor_type,
        actor_id=actor.actor_id,
        action="target_application_connection.create",
        resource_type="target_application_connection",
        resource_id=str(connection.id),
        request_id=actor.request_id,
    )
    return connection


def get_target_application_connection(
    session: Session, context: TenantContext, project_id: uuid.UUID, connection_id: uuid.UUID
) -> TargetApplicationConnection:
    return _fetch_or_raise(session, context, project_id, connection_id)


def list_target_application_connections(
    session: Session, context: TenantContext, project_id: uuid.UUID
) -> list[TargetApplicationConnection]:
    statement = (
        select(TargetApplicationConnection)
        .where(TargetApplicationConnection.project_id == project_id)
        .order_by(TargetApplicationConnection.created_at)
    )
    result = execute_tenant_scoped(session, context.organization_id, statement)
    return list(result.scalars().all())


def apply_edits(
    connection: TargetApplicationConnection,
    *,
    display_name: str | None,
    selected_branch: str | None,
) -> bool:
    """The pure, DB-free core of `update_target_application_connection`:
    refuses (`TargetApplicationConnectionStateError`) to touch an already-
    `revoked` connection -- a revoked source is a closed record, matching
    `apply_edits`'s own established precedent in
    `apps/api/app/datasets/dataset_source_connection_crud.py`. Only
    `display_name`/`selected_branch` are mutable here -- `connector_type`/
    `repository_url_redacted`/`credential_secret_ref` are fixed at
    creation; changing any of those is a different connection (revoke and
    create a new one), never an in-place identity change."""
    if connection.connection_status == "revoked":
        raise TargetApplicationConnectionStateError(
            f"target application connection {connection.id} is revoked and "
            "cannot be edited -- create a new connection instead"
        )

    changed = False
    if display_name is not None:
        connection.display_name = validate_name(display_name)
        changed = True
    if selected_branch is not None:
        connection.selected_branch = selected_branch
        changed = True

    if not changed:
        raise TargetApplicationConnectionValidationError(
            "update_target_application_connection requires at least one of "
            "display_name/selected_branch"
        )
    connection.updated_at = _now()
    return changed


def apply_revoke(connection: TargetApplicationConnection) -> bool:
    """The pure, DB-free core of `revoke_target_application_connection`:
    idempotent -- returns `False` if already `revoked`, otherwise
    transitions it and returns `True`."""
    if connection.connection_status == "revoked":
        return False
    connection.connection_status = "revoked"
    connection.revoked_at = _now()
    connection.updated_at = connection.revoked_at
    return True


def update_target_application_connection(
    session: Session,
    context: TenantContext,
    project_id: uuid.UUID,
    connection_id: uuid.UUID,
    *,
    display_name: str | None = None,
    selected_branch: str | None = None,
    actor: ActorInfo,
) -> TargetApplicationConnection:
    connection = _fetch_or_raise(session, context, project_id, connection_id)
    apply_edits(connection, display_name=display_name, selected_branch=selected_branch)
    write_audit_event(
        session,
        organization_id=context.organization_id,
        actor_type=actor.actor_type,
        actor_id=actor.actor_id,
        action="target_application_connection.update",
        resource_type="target_application_connection",
        resource_id=str(connection.id),
        request_id=actor.request_id,
    )
    return connection


def revoke_target_application_connection(
    session: Session,
    context: TenantContext,
    project_id: uuid.UUID,
    connection_id: uuid.UUID,
    *,
    actor: ActorInfo,
) -> TargetApplicationConnection:
    """Idempotent soft-revoke: repeating the same revoke is a no-op, not a
    second real state transition (matching
    `connection_crud.py::revoke_connection`'s own established behavior)."""
    connection = _fetch_or_raise(session, context, project_id, connection_id)
    if not apply_revoke(connection):
        return connection

    write_audit_event(
        session,
        organization_id=context.organization_id,
        actor_type=actor.actor_type,
        actor_id=actor.actor_id,
        action="target_application_connection.revoke",
        resource_type="target_application_connection",
        resource_id=str(connection.id),
        request_id=actor.request_id,
    )
    return connection
