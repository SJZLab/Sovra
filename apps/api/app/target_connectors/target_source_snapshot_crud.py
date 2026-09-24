"""TargetSourceSnapshot CRUD (FTF-04A07, Section 6.8B/7A.1-7A.3/9/45/65/
66/77/81).

Create/get/list a connection-scoped `TargetSourceSnapshot`
(`apps/api/app/target_connectors/target_source_snapshot_schema.py`).
There is no edit or delete surface at all -- see that module's own
docstring for why every snapshot is immutable by construction.

Every real query goes through the same certified composition FTF-04A01/
FTF-1001 already proved for exactly this class of resource:

  - `TenantContext`/`tenant_scoped_session_for` (FTF-H003) open the unit
    of work and bind the transaction-local RLS tenant context;
  - `authorize_resource_ownership` (FTF-H003) proves a connection ID (at
    creation/list) and a snapshot ID (at get, and at create when
    `supersedes_snapshot_id` is given) belong to the caller's own
    authenticated organization *before* any row is read or mutated --
    denied identically whether the ID does not exist at all or belongs to
    a different organization;
  - `execute_tenant_scoped` (FTF-0105) is what every actual SELECT in this
    module runs through, never a bare `session.execute(...)`/
    `session.get(...)`.

No new authorization primitive is introduced for the parent
`TargetApplicationConnection` reference: `_authorize_connection_or_raise`
below is the exact same `authorize_resource_ownership` composition
`target_application_connection_crud.py`'s own `_authorize_project_or_raise`
already established for its own parent (`Project`) -- this module never
re-derives that IDOR guard from scratch.

This task's own negative_acceptance ("Existing snapshot cannot be mutated
or reassigned to another repository/project") splits into two real
enforcement layers, disclosed here, not hidden, the same way FTF-1001's
own docstring disclosed its own three-way split of a similarly compressed
sentence:

1. "cannot be mutated": there is no edit path in this module at all, and
   the migration's own database grant is SELECT/INSERT only (see
   `target_source_snapshot_schema.py`'s own docstring) -- a database-level
   guarantee, not only an omitted route.
2. "cannot be ... reassigned to another repository/project": the
   cross-*tenant* half is a real composite foreign key (self-referencing,
   see the schema module's own docstring); the cross-*connection* half
   (the same organization's own *different* target-connection cannot be
   claimed as the superseded snapshot's owner) cannot be expressed by any
   `CHECK` constraint (it compares against another row's own column), so
   `create_target_source_snapshot` below fetches the referenced
   `supersedes_snapshot_id` row through the exact same `_fetch_or_raise`
   used for a real `get`, scoped to `connection_id` -- a supersession
   target that exists but belongs to a *different* connection is refused
   with the same `TargetSourceSnapshotNotFoundError` as a genuinely
   missing one, the identical "cross-parent-within-the-same-organization
   is refused exactly like missing" precedent `model_version_crud.py`'s
   own `_fetch_or_raise` already established for its own cross-project
   case (never a second, differently-named error that would leak more
   about *why* the reference was refused).

Role gating: Section 45's own literal "organization_admin: connections,
security profiles, cost profiles" sentence is the only sentence naming a
role for this class of resource -- a snapshot is real, read-only access
into a target repository's own actual source content, tied directly to
the connection's own credential-bearing access. This module follows
FTF-04A01's own `TargetApplicationConnection` precedent exactly, not a
wider default: `TARGET_SNAPSHOT_ROLES` is `{organization_admin}` alone,
gating every operation including reads (see
`target_source_snapshot_routes.py`).

Every mutating operation (creation only -- there is no other mutation)
writes an audit event via FTF-0104's `write_audit_event` in the *same*
transaction as the insert it describes, identical to every prior task's
own discipline in this chain.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.audit_event_writer import write_audit_event
from app.auth.resource_authorization import ResourceAuthorizationError, authorize_resource_ownership
from app.auth.tenant_isolation_query_guards import execute_tenant_scoped
from app.db.tenant_context import TenantContext
from app.target_connectors.target_application_connection_schema import TargetApplicationConnection
from app.target_connectors.target_source_snapshot_schema import TargetSourceSnapshot

__all__ = [
    "ActorInfo",
    "TargetApplicationConnectionNotFoundError",
    "TargetSourceSnapshotNotFoundError",
    "create_target_source_snapshot",
    "get_target_source_snapshot",
    "list_target_source_snapshots",
]

_SNAPSHOT_TABLE = TargetSourceSnapshot.__table__
_SNAPSHOT_ID_COLUMN = _SNAPSHOT_TABLE.c.id
_SNAPSHOT_ORG_COLUMN = _SNAPSHOT_TABLE.c.organization_id

_CONNECTION_TABLE = TargetApplicationConnection.__table__
_CONNECTION_ID_COLUMN = _CONNECTION_TABLE.c.id
_CONNECTION_ORG_COLUMN = _CONNECTION_TABLE.c.organization_id


class TargetApplicationConnectionNotFoundError(LookupError):
    """No target application connection with this ID exists in the
    caller's own organization -- raised identically whether the ID is
    missing entirely or belongs to a different organization (the same
    IDOR discipline FTF-04A01's own error of the same name already
    established for its own parent reference)."""


class TargetSourceSnapshotNotFoundError(LookupError):
    """No target source snapshot with this ID exists in the caller's own
    organization *and* connection -- identical whether the ID is missing
    entirely, belongs to a different organization, or belongs to a
    different connection within the caller's own organization."""


@dataclass(frozen=True)
class ActorInfo:
    """Who/what is performing this mutation, for the audit event it
    writes. `actor_id` is always the authenticated principal's own
    `subject` (or a real, identifiable automated-workflow identity for a
    system-triggered capture), never a client-supplied free-text string."""

    actor_type: str
    actor_id: str
    request_id: str


def _authorize_connection_or_raise(
    session: Session, context: TenantContext, connection_id: uuid.UUID
) -> None:
    if not isinstance(connection_id, uuid.UUID):
        raise TargetApplicationConnectionNotFoundError(
            f"connection_id must be a uuid.UUID, got {connection_id!r}"
        )
    try:
        authorize_resource_ownership(
            session,
            context,
            _CONNECTION_TABLE,
            _CONNECTION_ID_COLUMN,
            _CONNECTION_ORG_COLUMN,
            connection_id,
        )
    except ResourceAuthorizationError as exc:
        raise TargetApplicationConnectionNotFoundError(str(exc)) from exc


def _fetch_or_raise(
    session: Session,
    context: TenantContext,
    connection_id: uuid.UUID,
    snapshot_id: uuid.UUID,
) -> TargetSourceSnapshot:
    if not isinstance(snapshot_id, uuid.UUID):
        raise TargetSourceSnapshotNotFoundError(
            f"snapshot_id must be a uuid.UUID, got {snapshot_id!r}"
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
        raise TargetSourceSnapshotNotFoundError(str(exc)) from exc
    statement = select(TargetSourceSnapshot).where(TargetSourceSnapshot.id == snapshot_id)
    result = execute_tenant_scoped(session, context.organization_id, statement)
    row = result.scalars().one_or_none()
    if row is None:
        raise TargetSourceSnapshotNotFoundError(
            f"target source snapshot {snapshot_id} vanished mid-transaction"
        )
    if row.target_application_connection_id != connection_id:
        # A connection-scoped resource's own deeper IDOR case, the same
        # precedent `model_version_crud.py`'s own `_fetch_or_raise` already
        # established for its own project-scoped resource: a real row in
        # the caller's own organization, just under a *different*
        # connection than the URL names, is refused identically to a
        # missing row.
        raise TargetSourceSnapshotNotFoundError(
            f"target source snapshot {snapshot_id} does not belong to connection {connection_id}"
        )
    return row


def create_target_source_snapshot(
    session: Session,
    context: TenantContext,
    *,
    connection_id: uuid.UUID,
    commit_sha: str,
    tree_hash: str,
    capture_method: str,
    branch: str | None = None,
    source_archive_object_key: str | None = None,
    source_archive_sha256: str | None = None,
    supersedes_snapshot_id: uuid.UUID | None = None,
    actor: ActorInfo,
) -> TargetSourceSnapshot:
    """Validates and inserts a new, immutable `TargetSourceSnapshot`.

    `analysis_status` is never accepted from the caller -- it is always
    the single Release-1 starting value `'not_analyzed'` (see the schema
    module's own docstring); there is no legitimate way to create a
    snapshot with any other analysis state.
    """
    _authorize_connection_or_raise(session, context, connection_id)

    if supersedes_snapshot_id is not None:
        superseded = _fetch_or_raise(session, context, connection_id, supersedes_snapshot_id)
        # `_fetch_or_raise` already proved `superseded` is a real row in
        # the caller's own organization *and* belongs to `connection_id`
        # (its own IDOR check above is exactly this task's own real
        # "reassigned to another repository/project" negative_acceptance
        # -- a snapshot cannot claim to supersede another connection's own
        # snapshot). Nothing further to check here; kept as an explicit,
        # named local so this invariant is not silently implicit.
        del superseded

    snapshot = TargetSourceSnapshot(
        organization_id=context.organization_id,
        target_application_connection_id=connection_id,
        commit_sha=commit_sha,
        branch=branch,
        tree_hash=tree_hash,
        capture_method=capture_method,
        source_archive_object_key=source_archive_object_key,
        source_archive_sha256=source_archive_sha256,
        analysis_status="not_analyzed",
        supersedes_snapshot_id=supersedes_snapshot_id,
    )
    session.add(snapshot)
    session.flush()
    write_audit_event(
        session,
        organization_id=context.organization_id,
        actor_type=actor.actor_type,
        actor_id=actor.actor_id,
        action="target_source_snapshot.create",
        resource_type="target_source_snapshot",
        resource_id=str(snapshot.id),
        request_id=actor.request_id,
    )
    return snapshot


def get_target_source_snapshot(
    session: Session, context: TenantContext, connection_id: uuid.UUID, snapshot_id: uuid.UUID
) -> TargetSourceSnapshot:
    return _fetch_or_raise(session, context, connection_id, snapshot_id)


def list_target_source_snapshots(
    session: Session, context: TenantContext, connection_id: uuid.UUID
) -> list[TargetSourceSnapshot]:
    _authorize_connection_or_raise(session, context, connection_id)
    statement = (
        select(TargetSourceSnapshot)
        .where(TargetSourceSnapshot.target_application_connection_id == connection_id)
        .order_by(TargetSourceSnapshot.captured_at)
    )
    result = execute_tenant_scoped(session, context.organization_id, statement)
    return list(result.scalars().all())
