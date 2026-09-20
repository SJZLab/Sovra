"""Live `/api/v1/organizations/{organization_id}/connections/litellm` routes
(FTF-0201, Section 2-3/38/45/65/77/81) -- the second live-wired
product-runtime path in this repository, after FTF-0101's `/auth/*`.

Scope (disclosed explicitly, not hidden -- see this task's own
docs/certifications/queue.md submission entry): this router implements
**only** create/get/list/edit/revoke. It deliberately does **not**
declare `validate`/`sync-models`/`syncs`/`/models`/`/models/{id}/probe`
routes, even as a stub or a `501` -- those require a real call to the
organization's actual LiteLLM gateway and are FTF-0203 ("Connection
validation"), FTF-0204 ("`/v1/models` discovery") and FTF-0210 ("Real
chat probe")'s own, later, separately dependency-gated acceptance
criteria (see `docs/TASK_INDEX.md`). Declaring that surface here without
a real backing call would be exactly the "placeholder endpoint" Law 11
and this task's own acceptance clause ("no placeholder endpoints")
forbid.

Path shape (refined from the task packet's own abbreviated
`api_contract: "/api/v1/connections/litellm"` to include the real
`{organization_id}` path segment `require_role`'s own
`organization_id_from_path()` requires): matches Section 77.6's own
literal route shape for the analogous admin-only resource --
`/organizations/{id}/component-bindings` -- and every other tenant-scoped
route already proven in this repository (FTF-0106's own demo routes are
`/api/v1/orgs/{organization_id}/workspaces/{workspace_id}`). This is a
path refinement, not a new invented shape: the packet's own front matter
(Section 84.3) explicitly allows refining an initial owner path "to reuse
coherent existing modules under recorded ownership."

Role gating: every route (including reads) is `organization_admin`-only,
per Section 45's own literal role table ("organization_admin: connections,
security profiles, cost profiles" -- the *only* role Section 45 names for
connections) and Section 77.6's description of the analogous
`/organizations/{id}/component-bindings` route as "Admin-only
create/read/versioned update."
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
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
from app.auth.tenant_isolation_query_guards import TenantContextError
from app.db.tenant_context import TenantContext, tenant_scoped_session_for
from app.litellm.connection_crud import (
    ActorInfo,
    ConnectionNotFoundError,
    ConnectionStateError,
    create_connection,
    get_connection,
    list_connections,
    revoke_connection,
    update_connection,
)
from app.litellm.connection_schema import LiteLLMConnection

__all__ = ["CONNECTION_ADMIN_ROLES", "build_litellm_connection_router"]

# Section 45: "organization_admin: connections, security profiles, cost
# profiles" is the only role Section 45 names for connections.
CONNECTION_ADMIN_ROLES: frozenset[Role] = frozenset({Role.ORGANIZATION_ADMIN})

_COLLECTION_PATH = "/api/v1/organizations/{organization_id}/connections/litellm"
_ITEM_PATH = _COLLECTION_PATH + "/{connection_id}"


def _serialize(connection: LiteLLMConnection) -> dict[str, object]:
    """Never includes `secret_ref` -- this task's own negative_acceptance
    ("secret echo fails"): a connection's secret reference is write-only
    from the API's point of view, never echoed back in any response body."""
    return {
        "id": str(connection.id),
        "organization_id": str(connection.organization_id),
        "name": connection.name,
        "base_url": connection.base_url,
        "status": connection.status,
        "created_at": connection.created_at.isoformat(),
        "updated_at": connection.updated_at.isoformat(),
        "revoked_at": connection.revoked_at.isoformat() if connection.revoked_at else None,
    }


def _actor_from(ctx: AuthorizedContext, request: Request) -> ActorInfo:
    # `request_id` is always server-generated, never trusted from a client
    # header (Section 65.1's "never trusting an arbitrary client-supplied"
    # value, applied to request identity the same way it applies to tenant
    # identity elsewhere in this repository).
    del request
    return ActorInfo(
        actor_type="user",
        actor_id=ctx.principal.subject,
        request_id=uuid.uuid4().hex,
    )


async def _read_json_body(request: Request) -> dict[str, Any]:
    """Payload fields are validated for real by `connection_schema.py`'s
    own `validate_base_url`/`validate_secret_ref`/`validate_name` (which
    reject a non-`str` value outright) -- this only proves the body itself
    is a JSON object, so field values stay `Any` here rather than an
    unchecked `str` cast that would just move the same runtime check
    later without adding safety."""
    try:
        payload = await request.json()
    except Exception as exc:  # noqa: BLE001 - any unparsable body is the same outcome
        raise HTTPException(status_code=400, detail="request body must be valid JSON") from exc
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="request body must be a JSON object")
    return payload


def _require_str(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str):
        raise HTTPException(status_code=400, detail=f"{key} is required and must be a string")
    return value


def _optional_str(payload: dict[str, Any], key: str) -> str | None:
    if key not in payload:
        return None
    value = payload[key]
    if not isinstance(value, str):
        raise HTTPException(status_code=400, detail=f"{key} must be a string")
    return value


def build_litellm_connection_router(
    session_factory: Callable[[], Session],
    *,
    principal_resolver: PrincipalResolver = default_principal_resolver,
    role_lookup: RoleLookup = default_role_lookup,
) -> APIRouter:
    """Real FastAPI router for FTF-0201's CRUD surface.

    `session_factory` is the already-configured `sessionmaker` bound to
    the restricted `sovra_app` role's engine -- this router never opens
    its own engine/connection, matching FTF-0106's own demo-app
    convention. `principal_resolver`/`role_lookup` are injected the same
    way FTF-0103's own `require_role` and FTF-0106's own demo app inject
    them -- fail-closed defaults until a real session-reader/Membership
    lookup is wired in (same disclosed "no live Membership table yet"
    gap already carried by FTF-0103/FTF-0106).
    """
    router = APIRouter()
    admin_dependency = Depends(
        require_role(
            CONNECTION_ADMIN_ROLES,
            principal_resolver=principal_resolver,
            role_lookup=role_lookup,
        )
    )

    @router.post(_COLLECTION_PATH, status_code=201)
    async def create(
        request: Request,
        organization_id: uuid.UUID,
        ctx: AuthorizedContext = admin_dependency,
    ) -> JSONResponse:
        del organization_id
        payload = await _read_json_body(request)
        tenant_context = TenantContext.from_authorized_context(ctx)
        try:
            with tenant_scoped_session_for(session_factory, tenant_context) as session:
                connection = create_connection(
                    session,
                    tenant_context,
                    name=_require_str(payload, "name"),
                    base_url=_require_str(payload, "base_url"),
                    secret_ref=_require_str(payload, "secret_ref"),
                    actor=_actor_from(ctx, request),
                )
                body = _serialize(connection)
        except ValueError as exc:
            # Covers both LiteLLMConnectionValidationError (base_url/
            # secret_ref/status shape) and organization_workspace_schema's
            # own SchemaValidationError (name shape, reused via
            # `validate_name`) -- both are real request-validation
            # failures, mapped to 400, never a fabricated success.
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return JSONResponse(body, status_code=201)

    @router.get(_COLLECTION_PATH)
    async def list_all(
        organization_id: uuid.UUID,
        ctx: AuthorizedContext = admin_dependency,
    ) -> JSONResponse:
        del organization_id
        tenant_context = TenantContext.from_authorized_context(ctx)
        with tenant_scoped_session_for(session_factory, tenant_context) as session:
            connections = list_connections(session, tenant_context)
            body = [_serialize(c) for c in connections]
        return JSONResponse(body, status_code=200)

    @router.get(_ITEM_PATH)
    async def get_one(
        organization_id: uuid.UUID,
        connection_id: uuid.UUID,
        ctx: AuthorizedContext = admin_dependency,
    ) -> JSONResponse:
        del organization_id
        tenant_context = TenantContext.from_authorized_context(ctx)
        try:
            with tenant_scoped_session_for(session_factory, tenant_context) as session:
                connection = get_connection(session, tenant_context, connection_id)
                body = _serialize(connection)
        except (ConnectionNotFoundError, TenantContextError) as exc:
            raise HTTPException(status_code=404, detail="not_found") from exc
        return JSONResponse(body, status_code=200)

    @router.patch(_ITEM_PATH)
    async def update(
        request: Request,
        organization_id: uuid.UUID,
        connection_id: uuid.UUID,
        ctx: AuthorizedContext = admin_dependency,
    ) -> JSONResponse:
        del organization_id
        payload = await _read_json_body(request)
        tenant_context = TenantContext.from_authorized_context(ctx)
        try:
            with tenant_scoped_session_for(session_factory, tenant_context) as session:
                connection = update_connection(
                    session,
                    tenant_context,
                    connection_id,
                    name=_optional_str(payload, "name"),
                    base_url=_optional_str(payload, "base_url"),
                    secret_ref=_optional_str(payload, "secret_ref"),
                    actor=_actor_from(ctx, request),
                )
                body = _serialize(connection)
        except (ConnectionNotFoundError, TenantContextError) as exc:
            raise HTTPException(status_code=404, detail="not_found") from exc
        except ConnectionStateError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return JSONResponse(body, status_code=200)

    @router.delete(_ITEM_PATH)
    async def revoke(
        request: Request,
        organization_id: uuid.UUID,
        connection_id: uuid.UUID,
        ctx: AuthorizedContext = admin_dependency,
    ) -> JSONResponse:
        del organization_id
        tenant_context = TenantContext.from_authorized_context(ctx)
        try:
            with tenant_scoped_session_for(session_factory, tenant_context) as session:
                connection = revoke_connection(
                    session, tenant_context, connection_id, actor=_actor_from(ctx, request)
                )
                body = _serialize(connection)
        except (ConnectionNotFoundError, TenantContextError) as exc:
            raise HTTPException(status_code=404, detail="not_found") from exc
        return JSONResponse(body, status_code=200)

    return router
