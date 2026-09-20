"""Cross-layer tenant isolation composition (FTF-H003, Section 6/65/77/81).

FTF-0103's `require_role` proves *who* a request is authorized as and
*which organization* it is scoped to. FTF-0105's `tenant_scoped_session`
proves that once a database session exists, it is bound to exactly one
authenticated organization's transaction-local RLS context. Until this
module, nothing in this repository actually connected the two: a route
handler that called `require_role` and then opened its own database
session on the side could still get that composition wrong -- open an
unscoped session, scope it to the wrong field, or forget to scope it at
all. `tenant_context_dependency()` is the one place that composition
happens, so a route can no longer get it wrong by construction:
authorization runs first, and only on success is a session opened, always
scoped to exactly the organization `require_role` just authorized --
never a value re-derived from the request body or re-parsed independently
downstream.

Ordering matters and is deliberate: if authorization fails (unauthenticated,
malformed tenant, role not permitted, body-tenant mismatch), no database
session is ever created -- an unauthorized request never even takes a
connection from the pool, let alone one bound to a real tenant context.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass

from fastapi import Request
from sqlalchemy.orm import Session

from app.auth.rbac_middleware import (
    AuthorizedContext,
    PrincipalResolver,
    Role,
    RoleLookup,
    default_principal_resolver,
    default_role_lookup,
    require_role,
)
from app.auth.tenant_isolation_query_guards import tenant_scoped_session


@dataclass(frozen=True)
class TenantScopedRequest:
    """What a protected route handler actually receives: the authorized
    identity/role/organization (Section 45/65.1) alongside a database
    session already bound to that exact organization's transaction-local
    RLS context (Section 65.1) -- never a raw, unscoped `Session` a
    handler could accidentally query outside tenant isolation."""

    authorized: AuthorizedContext
    session: Session


def tenant_context_dependency(
    allowed_roles: frozenset[Role] | set[Role],
    *,
    session_factory: Callable[[], Session],
    organization_id_source: Callable[[Request], uuid.UUID] | None = None,
    principal_resolver: PrincipalResolver = default_principal_resolver,
    role_lookup: RoleLookup = default_role_lookup,
    body_tenant_field: str | None = "organization_id",
) -> Callable[[Request], AsyncIterator[TenantScopedRequest]]:
    """Returns a FastAPI dependency composing `require_role` (FTF-0103)
    and `tenant_scoped_session` (FTF-0105): authorizes the request exactly
    as `require_role` would (organization resolved strictly from the URL
    path, a disagreeing body tenant denied outright, role checked against
    `allowed_roles`), then -- only once that succeeds -- opens a real
    tenant-scoped session bound to the authorized organization and yields
    both to the route handler. The session commits on a successful
    response and rolls back on any exception raised while handling it,
    then always closes (`tenant_scoped_session`'s own guarantee)."""
    authorize = require_role(
        allowed_roles,
        organization_id_source=organization_id_source,
        principal_resolver=principal_resolver,
        role_lookup=role_lookup,
        body_tenant_field=body_tenant_field,
    )

    async def _dependency(request: Request) -> AsyncIterator[TenantScopedRequest]:
        authorized = await authorize(request)
        with tenant_scoped_session(session_factory, authorized.organization_id) as session:
            yield TenantScopedRequest(authorized=authorized, session=session)

    return _dependency
