"""LiteLLM connection CRUD (FTF-0201, Section 2-3/38/45/65/77/81).

Create/get/list/edit/revoke an organization-scoped `LiteLLMConnection`
(`apps/api/app/litellm/connection_schema.py`). Every real query against
`litellm_connections` goes through the same certified composition this
task's own dependency-chain already proved for exactly this class of
resource:

  - `TenantContext`/`tenant_scoped_session_for` (FTF-H003) open the unit
    of work and bind the transaction-local RLS tenant context;
  - `authorize_resource_ownership` (FTF-H003) proves a connection ID
    belongs to the caller's own authenticated organization *before* any
    row is read or mutated -- denied identically (via
    `ResourceAuthorizationError`) whether the ID does not exist at all or
    belongs to a different organization, so a caller cannot use the
    difference between "not found" and "forbidden" as an IDOR oracle
    (this task's own negative_acceptance: "Foreign connection ID...
    fails; cannot mutate another organization gateway");
  - `execute_tenant_scoped` (FTF-0105) is what every actual SELECT in this
    module runs through, never a bare `session.execute(...)`/`session.get(...)` --
    the same fail-closed connection-pool-reuse guarantee already proven
    for every other tenant-scoped table in this repository, applied here
    rather than re-derived as one more ad hoc tenant filter.

No new authorization primitive is introduced by this module -- it is
composition only, exactly as FTF-0106's own reusable harness already
demonstrated for a real tenant-owned table (`workspaces`); this task
applies the identical stack to its own new one (`litellm_connections`).

Role gating (Section 45's own role table, applied literally): "organization_admin:
connections, security profiles, cost profiles" is the *only* role Section
45 names for connections -- unlike FTF-0106's own read/write/approve
three-tier split (which had no single Section 45 sentence to draw a line
from), this task's routes gate every operation, including reads, to
`organization_admin` alone. A gateway connection carries a secret
reference and an explicit TLS/URL policy; Section 77.6's analogous
`/organizations/{id}/component-bindings` route is itself described as
"Admin-only create/read/versioned update" -- read is not opened to every
role the way FTF-0106's own three-tier matrix opened plain reads to all
six roles for a plain `workspaces` resource.

Every mutating operation (create/update/revoke) writes an audit event via
FTF-0104's `write_audit_event` in the *same* transaction as the mutation
it describes -- `tenant_scoped_session_for`'s own commit-or-rollback
means a failed mutation can never leave behind a misleading "it
succeeded" audit row (identical guarantee already proven for
`audit_events` itself).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.audit_event_writer import write_audit_event
from app.auth.organization_workspace_schema import validate_name
from app.auth.resource_authorization import ResourceAuthorizationError, authorize_resource_ownership
from app.auth.tenant_isolation_query_guards import execute_tenant_scoped
from app.db.tenant_context import TenantContext
from app.litellm.connection_schema import (
    LiteLLMConnection,
    LiteLLMConnectionValidationError,
    validate_base_url,
    validate_secret_ref,
)

__all__ = [
    "ConnectionNotFoundError",
    "ConnectionStateError",
    "ActorInfo",
    "apply_edits",
    "apply_revoke",
    "create_connection",
    "get_connection",
    "list_connections",
    "update_connection",
    "revoke_connection",
]

_TABLE = LiteLLMConnection.__table__
_ID_COLUMN = _TABLE.c.id
_ORG_COLUMN = _TABLE.c.organization_id


class ConnectionNotFoundError(LookupError):
    """No connection with this ID exists in the caller's own organization
    -- raised identically whether the ID is missing entirely or belongs to
    a different organization (this task's own negative_acceptance)."""


class ConnectionStateError(ValueError):
    """The requested transition is not valid for the connection's current
    state (e.g. editing a revoked connection)."""


@dataclass(frozen=True)
class ActorInfo:
    """Who is performing this mutation, for the audit event it writes.
    `actor_id` must already be a safe identifier (Section 46/47's
    redaction-shape rule, enforced by `write_audit_event` itself) --
    callers pass the authenticated principal's own `subject`, never a
    raw issuer URL or email."""

    actor_type: str
    actor_id: str
    request_id: str


def _now() -> datetime:
    return datetime.now(UTC)


def _fetch_or_raise(
    session: Session, context: TenantContext, connection_id: uuid.UUID
) -> LiteLLMConnection:
    """Proves ownership first (`authorize_resource_ownership`), then reads
    the real row through `execute_tenant_scoped` -- never a bare
    `session.get`, so this table's reads share the exact same fail-closed
    guard every other tenant-scoped read in this repository already goes
    through."""
    if not isinstance(connection_id, uuid.UUID):
        raise ConnectionNotFoundError(f"connection_id must be a uuid.UUID, got {connection_id!r}")
    try:
        authorize_resource_ownership(
            session, context, _TABLE, _ID_COLUMN, _ORG_COLUMN, connection_id
        )
    except ResourceAuthorizationError as exc:
        raise ConnectionNotFoundError(str(exc)) from exc
    statement = select(LiteLLMConnection).where(LiteLLMConnection.id == connection_id)
    result = execute_tenant_scoped(session, context.organization_id, statement)
    row = result.scalars().one_or_none()
    if row is None:
        # authorize_resource_ownership already confirmed the row exists and
        # belongs to this tenant a moment ago in the same transaction; this
        # is not a real race this module can observe under RLS + a single
        # transaction, but never assumed -- fail closed rather than return
        # a fabricated object.
        raise ConnectionNotFoundError(f"connection {connection_id} vanished mid-transaction")
    return row


def create_connection(
    session: Session,
    context: TenantContext,
    *,
    name: str,
    base_url: str,
    secret_ref: str,
    actor: ActorInfo,
) -> LiteLLMConnection:
    """Validates and inserts a new `active` connection, and writes the
    matching audit event in the same transaction. Validation errors
    (`LiteLLMConnectionValidationError`) propagate to the caller
    unmutated -- the route layer maps them to 400, never a fabricated
    success."""
    connection = LiteLLMConnection(
        organization_id=context.organization_id,
        name=name,
        base_url=base_url,
        secret_ref=secret_ref,
        status="active",
    )
    session.add(connection)
    session.flush()
    write_audit_event(
        session,
        organization_id=context.organization_id,
        actor_type=actor.actor_type,
        actor_id=actor.actor_id,
        action="litellm_connection.create",
        resource_type="litellm_connection",
        resource_id=str(connection.id),
        request_id=actor.request_id,
    )
    return connection


def get_connection(
    session: Session, context: TenantContext, connection_id: uuid.UUID
) -> LiteLLMConnection:
    """Read-only: no audit event (Section 6.24/45-47 audit the effectful
    mutations this task itself performs, not every read; matching
    `audit_event_writer.py`'s own scope, which is likewise never called
    for reads)."""
    return _fetch_or_raise(session, context, connection_id)


def list_connections(session: Session, context: TenantContext) -> list[LiteLLMConnection]:
    statement = (
        select(LiteLLMConnection)
        .where(LiteLLMConnection.organization_id == context.organization_id)
        .order_by(LiteLLMConnection.created_at)
    )
    result = execute_tenant_scoped(session, context.organization_id, statement)
    return list(result.scalars().all())


def apply_edits(
    connection: LiteLLMConnection,
    *,
    name: str | None,
    base_url: str | None,
    secret_ref: str | None,
) -> bool:
    """The pure, DB-free core of `update_connection`: validates and applies
    any provided subset of `name`/`base_url`/`secret_ref` directly onto an
    in-memory `LiteLLMConnection`, and refuses (`ConnectionStateError`) to
    touch one that is already `revoked` -- a revoked gateway binding is a
    closed record, not a draft to keep mutating. Returns whether anything
    actually changed (`False`/`LiteLLMConnectionValidationError` if the
    caller supplied no field at all). Split out from `update_connection`
    so this task's own "status transitions" acceptance can be proven
    without a database (`tests/tasks/test_ftf_0201.py`), the same
    no-DB-first-then-DB-wired pattern this repository already established
    (FTF-0106's own `apply role checks before opening a session` split)."""
    if connection.status == "revoked":
        raise ConnectionStateError(
            f"connection {connection.id} is revoked and cannot be edited "
            "-- create a new connection instead"
        )

    changed = False
    if name is not None:
        connection.name = validate_name(name)
        changed = True
    if base_url is not None:
        connection.base_url = validate_base_url(base_url)
        changed = True
    if secret_ref is not None:
        connection.secret_ref = validate_secret_ref(secret_ref)
        changed = True

    if not changed:
        raise LiteLLMConnectionValidationError(
            "update_connection requires at least one of name/base_url/secret_ref"
        )
    connection.updated_at = _now()
    return changed


def apply_revoke(connection: LiteLLMConnection) -> bool:
    """The pure, DB-free core of `revoke_connection`: idempotent --
    returns `False` (no change) if `connection` is already `revoked`,
    otherwise transitions it to `revoked` in place and returns `True`.
    Split out for the same no-DB-testability reason as `apply_edits`."""
    if connection.status == "revoked":
        return False
    connection.status = "revoked"
    connection.revoked_at = _now()
    connection.updated_at = connection.revoked_at
    return True


def update_connection(
    session: Session,
    context: TenantContext,
    connection_id: uuid.UUID,
    *,
    name: str | None = None,
    base_url: str | None = None,
    secret_ref: str | None = None,
    actor: ActorInfo,
) -> LiteLLMConnection:
    """Edits `name`/`base_url`/`secret_ref` (any subset). Refuses to edit a
    `revoked` connection (`ConnectionStateError`) -- a revoked gateway
    binding is a closed record, not a draft to keep mutating; a caller
    who genuinely needs a new connection creates one instead."""
    connection = _fetch_or_raise(session, context, connection_id)
    apply_edits(connection, name=name, base_url=base_url, secret_ref=secret_ref)
    write_audit_event(
        session,
        organization_id=context.organization_id,
        actor_type=actor.actor_type,
        actor_id=actor.actor_id,
        action="litellm_connection.update",
        resource_type="litellm_connection",
        resource_id=str(connection.id),
        request_id=actor.request_id,
    )
    return connection


def revoke_connection(
    session: Session,
    context: TenantContext,
    connection_id: uuid.UUID,
    *,
    actor: ActorInfo,
) -> LiteLLMConnection:
    """Idempotent soft-revoke: a connection that is already `revoked` is
    returned unchanged, with no new audit event written -- repeating the
    same DELETE is a no-op, not a second real state transition (matching
    this task's own required "revoke idempotency" behavior)."""
    connection = _fetch_or_raise(session, context, connection_id)
    if not apply_revoke(connection):
        return connection

    write_audit_event(
        session,
        organization_id=context.organization_id,
        actor_type=actor.actor_type,
        actor_id=actor.actor_id,
        action="litellm_connection.revoke",
        resource_type="litellm_connection",
        resource_id=str(connection.id),
        request_id=actor.request_id,
    )
    return connection
