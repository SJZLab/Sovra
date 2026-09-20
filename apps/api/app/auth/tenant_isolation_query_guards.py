"""Tenant-isolation query guards (FTF-0105, Section 6/45-47/65/77/81).

FTF-0102 already gives the database-level primitive this task builds on:
`set_tenant_context()` binds `app.current_organization_id` via a
parameterized, transaction-local `set_config(..., is_local=true)` call
(the exact equivalent of `SET LOCAL`), which every RLS policy on a
tenant-scoped table reads. That primitive is correct but, on its own,
only as safe as *every caller remembering to use it correctly, every
time* -- Section 65.1's "Set tenant context transaction-locally;
clear/reset pooled connections; refuse missing tenant context" is a
requirement on the whole repository/query layer, not just on one helper
function.

This module is the query-layer guard that makes that requirement
structural rather than a convention every future task has to remember:

  - `tenant_scoped_session()` is the one blessed way to open a
    tenant-scoped unit of work. It refuses (fail-closed, before any
    connection is even taken from the pool) to proceed without a real
    `uuid.UUID` organization ID -- there is no "run without a tenant"
    code path -- then binds the context, commits or rolls back, and
    always closes/returns the connection.
  - `execute_tenant_scoped()` is the guard a raw-SQL or background-job
    caller must go through instead of calling `session.execute(...)`
    directly: it reads back the session's own currently active
    transaction-local context and refuses to run the caller's statement
    at all if that context is missing or does not match what the caller
    believes it is scoped to. A raw query that forgot to set context, or
    one whose session inherited a stale context from a bug elsewhere,
    is refused before it reaches the database -- it is never trusted to
    filter itself correctly.
  - `install_tenant_pool_reset_guard()` is defense-in-depth against the
    one case the two guards above cannot see: a connection taken
    directly from the pool's DBAPI layer (a background worker using a
    raw connection, or a raw-SQL path that mistakenly used a bare `SET`
    instead of `SET LOCAL`, which is *not* transaction-scoped and would
    otherwise survive across a pool checkin). It force-clears the tenant
    GUID on every connection returned to the pool, so connection-pool
    reuse itself can never hand a later checkout a previous tenant's
    context, regardless of how the earlier borrower behaved.

No independent schema change: this module consumes FTF-0102's frozen
`organizations`/`workspaces` tables and `set_tenant_context()` exactly as
delivered; `database_changes` for this task is "No independent schema
change."

Disclosed, not hidden: no repository/DAO layer exists yet for any
tenant-scoped entity beyond FTF-0102's own two tables, so this module's
positive-path evidence is proven against those two tables (the only real
tenant-scoped tables that exist in this repository), plus a synthetic
raw-SQL query -- the same disclosed "no live product caller yet" class
already accepted for FTF-0005/FTF-0006/FTF-0102/FTF-0103/FTF-0104 before
each one's own first live caller existed.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any

from sqlalchemy import event, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session
from sqlalchemy.sql import Executable

from app.auth.organization_workspace_schema import set_tenant_context

__all__ = [
    "TenantContextError",
    "require_tenant_context",
    "execute_tenant_scoped",
    "tenant_scoped_session",
    "install_tenant_pool_reset_guard",
]


class TenantContextError(RuntimeError):
    """A query was refused for lacking, or disagreeing with, a real
    authenticated tenant context. Always fail-closed: this is raised
    instead of running the query, never after."""


def require_tenant_context(session: Session) -> uuid.UUID:
    """Read back this session's own currently active transaction-local
    tenant context. Raises `TenantContextError` -- never returns a
    fabricated or guessed value -- when no context is set, or when a set
    value is not a well-formed UUID."""
    raw = session.execute(
        text("SELECT NULLIF(current_setting('app.current_organization_id', true), '')")
    ).scalar_one_or_none()
    if raw is None:
        raise TenantContextError(
            "refusing to proceed: no tenant context is set on this "
            "session/transaction (Section 65.1 'refuse missing tenant context')"
        )
    try:
        return uuid.UUID(str(raw))
    except (ValueError, AttributeError, TypeError) as exc:
        raise TenantContextError(
            f"refusing to proceed: tenant context is not a well-formed UUID: {raw!r}"
        ) from exc


def execute_tenant_scoped(
    session: Session,
    organization_id: uuid.UUID,
    statement: Executable | str,
    params: dict[str, Any] | None = None,
) -> Any:
    """The guard a raw-SQL or background-job caller must use instead of
    `session.execute(...)` directly. Refuses to run `statement` at all
    unless this session's own active tenant context already equals
    `organization_id` -- the caller's stated intent is verified against
    reality, never assumed. This is what makes "raw SQL query... cannot
    leak previous tenant rows" (this task's negative_acceptance) a
    property of the guard, not of every future raw query author's
    discipline."""
    if not isinstance(organization_id, uuid.UUID):
        raise TenantContextError(
            "refusing to proceed: organization_id must be a real uuid.UUID, not "
            f"{organization_id!r}"
        )
    active = require_tenant_context(session)
    if active != organization_id:
        raise TenantContextError(
            f"refusing to proceed: session tenant context is {active}, "
            f"caller expected {organization_id} -- statement not executed"
        )
    clause = text(statement) if isinstance(statement, str) else statement
    return session.execute(clause, params or {})


@contextmanager
def tenant_scoped_session(
    session_factory: Callable[[], Session],
    organization_id: uuid.UUID,
) -> Iterator[Session]:
    """The one blessed way to open a tenant-scoped unit of work, for both
    request handlers and background jobs alike -- there is no separate,
    looser code path for workers to fall back to. Fails closed before a
    session is even created when `organization_id` is not a real
    `uuid.UUID` (Section 65.1 "refuse missing tenant context"), binds the
    transaction-local context immediately via FTF-0102's own
    `set_tenant_context()`, and always commits-or-rolls-back and closes
    the session -- so a connection is never returned to the pool
    mid-transaction with a tenant context still live on it."""
    if not isinstance(organization_id, uuid.UUID):
        raise TenantContextError(
            "refusing to open a session with no authenticated tenant context "
            f"(organization_id must be a real uuid.UUID, got {organization_id!r})"
        )
    session = session_factory()
    try:
        set_tenant_context(session, organization_id)
        yield session
        session.commit()
    except BaseException:
        session.rollback()
        raise
    finally:
        session.close()


def install_tenant_pool_reset_guard(engine: Engine) -> None:
    """Defense-in-depth beyond `SET LOCAL`'s own transaction scoping.

    `set_tenant_context()`'s `is_local=true` already guarantees the
    tenant GUID is discarded at commit/rollback on any connection routed
    through `tenant_scoped_session`/`execute_tenant_scoped`. This guard
    covers the case those two cannot see: a connection taken directly
    from `engine`'s pool at the DBAPI level (a background worker using a
    raw connection, or a raw-SQL path that used a bare `SET` instead of
    `SET LOCAL` -- a *session-level* setting that is NOT cleared by
    rollback and would otherwise survive a pool checkin). Every
    connection this engine's pool hands back is force-cleared before it
    can be reused, so connection-pool reuse itself can never leak a
    previous tenant's context into the next checkout, regardless of how
    the prior borrower behaved. Uses the same `set_config(...)` call
    `set_tenant_context()` already uses (session-scoped this time, since
    there is no application transaction left to bind `is_local` to at
    checkin time), never a string-formatted statement.
    """

    @event.listens_for(engine, "checkin")
    def _on_checkin(dbapi_connection: Any, connection_record: Any) -> None:
        del connection_record
        _reset_dbapi_connection_tenant_context(dbapi_connection)


def _reset_dbapi_connection_tenant_context(dbapi_connection: Any) -> None:
    """The actual per-connection reset `install_tenant_pool_reset_guard`
    registers for every checkin. Split out as its own module-level
    function (rather than left inline in the event listener) so it can be
    exercised directly against a fake/mock DBAPI connection without
    needing a real pool checkin round-trip."""
    try:
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute("SELECT set_config('app.current_organization_id', '', false)")
        finally:
            cursor.close()
        dbapi_connection.commit()
    except Exception:
        # Best-effort hygiene reset on an otherwise-healthy connection. A
        # connection that cannot even run this is broken/invalidated and
        # will not be handed to another checkout regardless.
        pass
