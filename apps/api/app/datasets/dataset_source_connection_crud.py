"""DatasetSourceConnection CRUD + purpose authorization (FTF-0500, Section
6.8F/7A.10/9/45/65-67/77/81).

Create/get/list/edit/revoke a project-scoped `DatasetSourceConnection`
(`apps/api/app/datasets/dataset_source_connection_schema.py`), plus this
task's own central primitive: `authorize_purpose_use`/
`authorize_dataset_source_purpose` -- the real, callable "authorization
before import" check a later Dataset-import task (FTF-0501+) will call.
Every real query goes through the same certified composition FTF-0201/
FTF-0301/FTF-0401/FTF-0404 already proved for exactly this class of
resource:

  - `TenantContext`/`tenant_scoped_session_for` (FTF-H003) open the unit
    of work and bind the transaction-local RLS tenant context;
  - `authorize_resource_ownership` (FTF-H003) proves a project ID (at
    creation) and a connection ID (at get/edit/revoke/authorize) belong to
    the caller's own authenticated organization *before* any row is read
    or mutated -- denied identically whether the ID does not exist at all
    or belongs to a different organization;
  - `execute_tenant_scoped` (FTF-0105) is what every actual SELECT in this
    module runs through, never a bare `session.execute(...)`/
    `session.get(...)`.

Role gating (Section 45's own literal role table): "ml_engineer:
datasets/training" is the only sentence Section 45 names for dataset work
-- unlike FTF-0401/FTF-0404's own project/tool-schema precedent (which
open reads to every role, since neither carries a credential), this
resource carries `credential_secret_ref`/`connection_config_encrypted_ref`
(Section 46: "Secret values never returned to browser after creation"),
the same class of secret-bearing row FTF-0201's own `LiteLLMConnection`
already established a narrower read/write split for. This task therefore
gates every operation, including reads, to `{ml_engineer, workspace_admin,
organization_admin}` -- `ml_engineer` because Section 45 names it
specifically for datasets (unlike connections, which name only
`organization_admin`), `workspace_admin`/`organization_admin` as the same
standing project-administration superset every other project-scoped
resource in this repository already grants.

Every mutating operation (create/update/revoke) writes an audit event via
FTF-0104's `write_audit_event` in the *same* transaction as the mutation
it describes, identical to FTF-0201/FTF-0301/FTF-0401/FTF-0404's own
discipline. `authorize_dataset_source_purpose` is a read-only check, not a
mutation -- no audit event, matching `get_connection`/`get_project`'s own
established "reads are not audited" scope in this repository.
"""

from __future__ import annotations

import json
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
from app.datasets.dataset_source_connection_schema import (  # noqa: E402
    DatasetSourceConnection,
    DatasetSourceConnectionValidationError,
    validate_allowed_purposes,
    validate_security_classification,
)
from app.db.tenant_context import TenantContext  # noqa: E402
from app.projects.project_schema import Project  # noqa: E402

__all__ = [
    "ActorInfo",
    "DatasetSourceAuthorizationError",
    "DatasetSourceConnectionConfigurationError",
    "DatasetSourceConnectionNotFoundError",
    "DatasetSourceConnectionStateError",
    "ProjectNotFoundError",
    "apply_edits",
    "apply_revoke",
    "authorize_dataset_source_purpose",
    "authorize_purpose_use",
    "create_dataset_source_connection",
    "get_dataset_source_connection",
    "list_dataset_source_connections",
    "revoke_dataset_source_connection",
    "update_dataset_source_connection",
]

_TABLE = DatasetSourceConnection.__table__
_ID_COLUMN = _TABLE.c.id
_ORG_COLUMN = _TABLE.c.organization_id

_PROJECT_TABLE = Project.__table__
_PROJECT_ID_COLUMN = _PROJECT_TABLE.c.id
_PROJECT_ORG_COLUMN = _PROJECT_TABLE.c.organization_id

# Reuses the same real KV v2 mount FTF-0202's own integration proof already
# uses ("secret") -- a fresh OpenBao server only auto-enables that one mount
# in `-dev` mode (confirmed directly: a second, not-yet-enabled mount name
# answers 404 on every request, indistinguishable from `SecretNotFoundError`
# -- caught during this task's own real-backend fault injection before this
# module ever attempted to invent one). Enabling a new secrets-engine mount
# is a real backend-provisioning operation this task's own `store_secret_ref`
# dependency (FTF-0202) does not expose, and is out of this task's own
# scope; `path` is namespaced under a `dataset-sources/` prefix plus per-
# organization/connection segments below so no two connections, even across
# organizations or other already-CERTIFIED callers of the same mount, ever
# collide at the same backend path.
_SECRET_MOUNT = "secret"
_SECRET_PATH_PREFIX = "dataset-sources"


class ProjectNotFoundError(LookupError):
    """No project with this ID exists in the caller's own organization --
    raised identically whether the ID is missing entirely or belongs to a
    different organization (the same IDOR discipline FTF-0401/FTF-0404's
    own `ProjectNotFoundError`/`WorkspaceNotFoundError` established)."""


class DatasetSourceConnectionNotFoundError(LookupError):
    """No dataset source connection with this ID exists in the caller's
    own organization *and* project -- identical whether the ID is missing
    entirely, belongs to a different organization, or belongs to a
    different project within the caller's own organization."""


class DatasetSourceConnectionStateError(ValueError):
    """The requested operation is not valid for this row's current state
    (e.g. editing an already-`revoked` connection)."""


class DatasetSourceConnectionConfigurationError(RuntimeError):
    """A credential/config value was supplied but no secret-manager
    backend configuration was given to store it -- fails closed rather
    than silently dropping the value or storing it in plaintext."""


class DatasetSourceAuthorizationError(PermissionError):
    """This task's own negative_acceptance in full: raised identically
    whether the connection is not `active` (covers "grant revocation
    propagates to future derivative use" -- a revoked connection
    authorizes no purpose at all, even one it previously allowed) or the
    requested purpose is not in the connection's own `allowed_purposes_json`
    (covers "Source allowed only evaluation cannot be trained")."""


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
) -> DatasetSourceConnection:
    if not isinstance(connection_id, uuid.UUID):
        raise DatasetSourceConnectionNotFoundError(
            f"connection_id must be a uuid.UUID, got {connection_id!r}"
        )
    try:
        authorize_resource_ownership(
            session, context, _TABLE, _ID_COLUMN, _ORG_COLUMN, connection_id
        )
    except ResourceAuthorizationError as exc:
        raise DatasetSourceConnectionNotFoundError(str(exc)) from exc
    statement = select(DatasetSourceConnection).where(DatasetSourceConnection.id == connection_id)
    result = execute_tenant_scoped(session, context.organization_id, statement)
    row = result.scalars().one_or_none()
    if row is None:
        raise DatasetSourceConnectionNotFoundError(
            f"dataset source connection {connection_id} vanished mid-transaction"
        )
    if row.project_id != project_id:
        # A project-scoped resource's own deeper IDOR case: this row is a
        # real row in the caller's own organization, just under a
        # *different* project than the URL names -- refused identically to
        # a missing row (FTF-0404's own established discipline).
        raise DatasetSourceConnectionNotFoundError(
            f"dataset source connection {connection_id} does not belong to project {project_id}"
        )
    return row


def _store_credential_and_config(
    *,
    secret_manager_config: OpenBaoConfig | None,
    organization_id: uuid.UUID,
    connection_id: uuid.UUID,
    credential: str | None,
    connection_config: dict[str, object] | None,
) -> tuple[str | None, str | None]:
    """The one place this task ever writes to the real secret backend.
    Returns `(credential_secret_ref, connection_config_encrypted_ref)`,
    each `None` if the caller supplied nothing for that field. Both fields
    share one real KV v2 path (`<organization_id>/<connection_id>`),
    namespaced under this task's own `_SECRET_MOUNT` -- `store_secret_ref`
    merges onto any fields already present at a path rather than
    overwriting it, so storing both in the same call does not clobber
    either."""
    if credential is None and connection_config is None:
        return None, None
    if secret_manager_config is None:
        raise DatasetSourceConnectionConfigurationError(
            "a credential/connection_config value was supplied but no secret-manager "
            "backend configuration is available -- refusing to store it in plaintext"
        )
    path = f"{_SECRET_PATH_PREFIX}/{organization_id}/{connection_id}"
    credential_ref: str | None = None
    config_ref: str | None = None
    if credential is not None:
        credential_ref = store_secret_ref(
            config=secret_manager_config,
            mount=_SECRET_MOUNT,
            path=path,
            field="credential",
            value=credential,
        )
    if connection_config is not None:
        config_ref = store_secret_ref(
            config=secret_manager_config,
            mount=_SECRET_MOUNT,
            path=path,
            field="config_json",
            value=json.dumps(connection_config, sort_keys=True),
        )
    return credential_ref, config_ref


def create_dataset_source_connection(
    session: Session,
    context: TenantContext,
    *,
    project_id: uuid.UUID,
    source_type: str,
    display_name: str,
    allowed_purposes: list[str],
    security_classification: str,
    credential: str | None,
    connection_config: dict[str, object] | None,
    secret_manager_config: OpenBaoConfig | None,
    actor: ActorInfo,
) -> DatasetSourceConnection:
    """Validates and inserts a new `active` connection -- this task's own
    "Create purpose-scoped source grants" acceptance clause made real:
    `allowed_purposes` is declared here, once, from the closed Section
    6.8F vocabulary, and is exactly what `authorize_dataset_source_purpose`
    checks against for the connection's entire lifetime (until a future
    edit re-grants it). A supplied `credential`/`connection_config` value
    is written to the real secret backend before this row is ever
    inserted -- if that write fails, no row is created (never a dangling
    connection whose declared `credential_secret_ref` points nowhere)."""
    _authorize_project_or_raise(session, context, project_id)
    connection_id = uuid.uuid4()
    credential_ref, config_ref = _store_credential_and_config(
        secret_manager_config=secret_manager_config,
        organization_id=context.organization_id,
        connection_id=connection_id,
        credential=credential,
        connection_config=connection_config,
    )
    connection = DatasetSourceConnection(
        id=connection_id,
        organization_id=context.organization_id,
        project_id=project_id,
        source_type=source_type,
        display_name=display_name,
        credential_secret_ref=credential_ref,
        connection_config_encrypted_ref=config_ref,
        allowed_purposes_json=allowed_purposes,
        security_classification=security_classification,
        connection_status="active",
        created_by=actor.actor_id,
    )
    session.add(connection)
    session.flush()
    write_audit_event(
        session,
        organization_id=context.organization_id,
        actor_type=actor.actor_type,
        actor_id=actor.actor_id,
        action="dataset_source_connection.create",
        resource_type="dataset_source_connection",
        resource_id=str(connection.id),
        request_id=actor.request_id,
    )
    return connection


def get_dataset_source_connection(
    session: Session, context: TenantContext, project_id: uuid.UUID, connection_id: uuid.UUID
) -> DatasetSourceConnection:
    return _fetch_or_raise(session, context, project_id, connection_id)


def list_dataset_source_connections(
    session: Session, context: TenantContext, project_id: uuid.UUID
) -> list[DatasetSourceConnection]:
    statement = (
        select(DatasetSourceConnection)
        .where(DatasetSourceConnection.project_id == project_id)
        .order_by(DatasetSourceConnection.created_at)
    )
    result = execute_tenant_scoped(session, context.organization_id, statement)
    return list(result.scalars().all())


def apply_edits(
    connection: DatasetSourceConnection,
    *,
    display_name: str | None,
    allowed_purposes: list[str] | None,
    security_classification: str | None,
) -> bool:
    """The pure, DB-free core of `update_dataset_source_connection`:
    refuses (`DatasetSourceConnectionStateError`) to touch an already-
    `revoked` connection -- a revoked source is a closed record, matching
    `apply_edits`'s own established precedent in
    `apps/api/app/litellm/connection_crud.py`. Re-granting
    `allowed_purposes` here (a narrower or wider closed-vocabulary subset)
    is this task's own real "re-authorization" path -- there is no
    separate grant object to edit."""
    if connection.connection_status == "revoked":
        raise DatasetSourceConnectionStateError(
            f"dataset source connection {connection.id} is revoked and cannot be edited "
            "-- create a new connection instead"
        )

    changed = False
    if display_name is not None:
        connection.display_name = validate_name(display_name)
        changed = True
    if allowed_purposes is not None:
        connection.allowed_purposes_json = validate_allowed_purposes(allowed_purposes)
        changed = True
    if security_classification is not None:
        connection.security_classification = validate_security_classification(
            security_classification
        )
        changed = True

    if not changed:
        raise DatasetSourceConnectionValidationError(
            "update_dataset_source_connection requires at least one of "
            "display_name/allowed_purposes/security_classification"
        )
    connection.updated_at = _now()
    return changed


def apply_revoke(connection: DatasetSourceConnection) -> bool:
    """The pure, DB-free core of `revoke_dataset_source_connection`:
    idempotent -- returns `False` if already `revoked`, otherwise
    transitions it and returns `True`. Once revoked,
    `authorize_purpose_use` denies every purpose for this connection, not
    only new ones -- this task's own "grant revocation propagates to
    future derivative use" negative_acceptance."""
    if connection.connection_status == "revoked":
        return False
    connection.connection_status = "revoked"
    connection.revoked_at = _now()
    connection.updated_at = connection.revoked_at
    return True


def update_dataset_source_connection(
    session: Session,
    context: TenantContext,
    project_id: uuid.UUID,
    connection_id: uuid.UUID,
    *,
    display_name: str | None = None,
    allowed_purposes: list[str] | None = None,
    security_classification: str | None = None,
    actor: ActorInfo,
) -> DatasetSourceConnection:
    connection = _fetch_or_raise(session, context, project_id, connection_id)
    apply_edits(
        connection,
        display_name=display_name,
        allowed_purposes=allowed_purposes,
        security_classification=security_classification,
    )
    write_audit_event(
        session,
        organization_id=context.organization_id,
        actor_type=actor.actor_type,
        actor_id=actor.actor_id,
        action="dataset_source_connection.update",
        resource_type="dataset_source_connection",
        resource_id=str(connection.id),
        request_id=actor.request_id,
    )
    return connection


def revoke_dataset_source_connection(
    session: Session,
    context: TenantContext,
    project_id: uuid.UUID,
    connection_id: uuid.UUID,
    *,
    actor: ActorInfo,
) -> DatasetSourceConnection:
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
        action="dataset_source_connection.revoke",
        resource_type="dataset_source_connection",
        resource_id=str(connection.id),
        request_id=actor.request_id,
    )
    return connection


def authorize_purpose_use(connection: DatasetSourceConnection, purpose: str) -> None:
    """The pure, DB-free core of this task's own central primitive. Raises
    `DatasetSourceAuthorizationError` -- never returns a bare `False` --
    so a caller cannot accidentally ignore a denial by forgetting to check
    a return value; every negative_acceptance case is a real exception
    reaching the caller."""
    if connection.connection_status != "active":
        raise DatasetSourceAuthorizationError(
            f"dataset source connection {connection.id} is not active "
            f"(connection_status={connection.connection_status!r}) -- a revoked or "
            "otherwise inactive source authorizes no purpose"
        )
    if purpose not in connection.allowed_purposes_json:
        raise DatasetSourceAuthorizationError(
            f"dataset source connection {connection.id} is not granted for purpose "
            f"{purpose!r} (allowed: {connection.allowed_purposes_json})"
        )


def authorize_dataset_source_purpose(
    session: Session,
    context: TenantContext,
    project_id: uuid.UUID,
    connection_id: uuid.UUID,
    purpose: str,
) -> DatasetSourceConnection:
    """DB-wired wrapper: proves ownership/project match first (the same
    IDOR-safe fetch every other operation in this module uses), then
    applies `authorize_purpose_use`. Returns the connection on success (a
    later import-pipeline task can read whatever else it needs from it in
    the same call) or raises `DatasetSourceAuthorizationError`/
    `DatasetSourceConnectionNotFoundError` on denial."""
    connection = _fetch_or_raise(session, context, project_id, connection_id)
    authorize_purpose_use(connection, purpose)
    return connection
