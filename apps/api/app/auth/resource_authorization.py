"""Resource-level IDOR guard (FTF-H003, Section 6/65.1, Q01/Q24).

FTF-0103's `require_role` proves the caller holds an allowed role *in the
organization named by the URL path*. By its own module docstring's
disclosed scope, it does not -- and structurally cannot, since it never
looks at the database -- prove that some *other* resource ID elsewhere in
that same path (a workspace ID, and eventually a dataset/model/evaluation
ID) actually belongs to that organization. A caller who is a genuinely,
validly authorized `organization_admin` of Org A, who also knows or
guesses a real resource ID that actually belongs to Org B, passes
`require_role`'s own checks outright (Org A in the path, a real role in
Org A) -- this is exactly Q01/Q24's cross-tenant-guessed-ID scenario, and
the reason this is a separate module from FTF-0103's own request-shaped
tenant/role check rather than one more field on it.

`authorize_resource_ownership` closes that gap once, generically, for any
tenant-owned SQLAlchemy table, instead of leaving every future route
handler to remember to re-derive it. It reads the resource's real owning
organization through FTF-0105's own `execute_tenant_scoped` guard -- so
it inherits that guard's fail-closed connection-pool-reuse safety and
FTF-0102's FORCE-RLS enforcement, rather than issuing a second,
independently-fallible raw query -- and denies identically whether the
row does not exist at all or exists under a different organization, so a
caller cannot use the distinction between "not found" and "forbidden" as
an oracle to enumerate real IDs belonging to another tenant.

Disclosed, not hidden: no live FastAPI route wires this in yet (the same
disclosed "no live caller yet" class already accepted for FTF-0005/
FTF-0006/FTF-0102/FTF-0103/FTF-0104/FTF-0105), and the only real
tenant-scoped tables that exist in this repository are FTF-0102's own
`organizations`/`workspaces` -- this module's positive-path evidence is
proven against `workspaces`, the same disclosed scoping precedent
FTF-0105 already established for the same reason.
"""

from __future__ import annotations

import uuid

from sqlalchemy import ColumnElement, FromClause
from sqlalchemy.orm import Session

from app.auth.tenant_isolation_query_guards import execute_tenant_scoped
from app.db.tenant_context import TenantContext

__all__ = ["ResourceAuthorizationError", "authorize_resource_ownership"]


class ResourceAuthorizationError(PermissionError):
    """A requested resource does not belong to the authenticated tenant
    context. Raised identically whether the resource does not exist at
    all or exists under a different organization -- a caller must never
    be able to tell the two apart from this error alone."""


def authorize_resource_ownership(
    session: Session,
    context: TenantContext,
    table: FromClause,
    id_column: ColumnElement[uuid.UUID],
    organization_id_column: ColumnElement[uuid.UUID],
    resource_id: uuid.UUID,
) -> None:
    """Raise `ResourceAuthorizationError` unless `resource_id` names a row
    of `table` that really belongs to `context.organization_id`.

    Queries through `execute_tenant_scoped`, so this also refuses outright
    (before ever reaching the database) if the session's own active
    tenant context does not already equal `context.organization_id` --
    the same fail-closed guarantee FTF-0105 gives every other caller of
    that guard, not a second, weaker path around it.
    """
    if not isinstance(resource_id, uuid.UUID):
        raise ResourceAuthorizationError(
            f"refusing to authorize a non-uuid.UUID resource id: {resource_id!r}"
        )
    statement = (
        table.select().with_only_columns(organization_id_column).where(id_column == resource_id)
    )
    row = execute_tenant_scoped(session, context.organization_id, statement).first()
    if row is None or row[0] != context.organization_id:
        raise ResourceAuthorizationError(
            "resource does not belong to the authenticated tenant context "
            "(denied identically whether missing or foreign-tenant, by design)"
        )
