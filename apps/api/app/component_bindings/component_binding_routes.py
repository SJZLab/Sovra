"""Live `/api/v1/organizations/{organization_id}/component-bindings` routes
(FTF-R002, Section 39/45/65/70/77.6).

Role gating: every route (including reads) is `organization_admin`-only,
matching Section 77.6's own literal description of this exact route
family ("Admin-only create/read/versioned update") -- the same
`CONNECTION_ADMIN_ROLES`/`TARGET_SNAPSHOT_ROLES` precedent
`connection_routes.py`/`target_source_snapshot_routes.py` already
established for the analogous class of resource.

Path shape: `/api/v1/organizations/{organization_id}/component-bindings`
matches Section 77.6's own literal route name exactly (no refinement
needed, unlike FTF-0201's own abbreviated packet path). The
`/projects/{id}/component-status` route from the same section is scoped
here to `/organizations/{organization_id}/component-status` instead: this
task's own `ComponentBinding` is organization-scoped only for Release 1
(see `component_binding_schema.py`'s own docstring for why project scope
is deferred), so a project-scoped status projection would either report
identical organization-level data twice under two different URLs or
silently claim a project dimension that does not exist in the data model
yet -- disclosed here rather than a placeholder route Law 11 forbids.
"""

from __future__ import annotations

import sys
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

_REPO_ROOT = Path(__file__).resolve().parents[4]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from packages.security.url_policy import ConnectionPolicy  # noqa: E402

from app.auth.rbac_middleware import (  # noqa: E402
    AuthorizedContext,
    PrincipalResolver,
    Role,
    RoleLookup,
    default_principal_resolver,
    default_role_lookup,
    require_role,
)
from app.auth.tenant_isolation_query_guards import TenantContextError  # noqa: E402
from app.component_bindings.component_binding_crud import (  # noqa: E402
    ActorInfo,
    ComponentBindingNotFoundError,
    ComponentBindingVersionConflictError,
    create_component_binding,
    default_component_health_policy,
    get_component_binding,
    get_component_status_projection,
    list_component_bindings,
    update_component_binding,
    validate_component_binding,
)
from app.component_bindings.component_binding_schema import (  # noqa: E402
    ComponentBinding,
    ComponentBindingValidationError,
)
from app.db.tenant_context import TenantContext, tenant_scoped_session_for  # noqa: E402

__all__ = ["COMPONENT_BINDING_ADMIN_ROLES", "build_component_binding_router"]

# Section 77.6: "'/organizations/{id}/component-bindings' -- Admin-only
# create/read/versioned update" is the only sentence naming access for
# this resource.
COMPONENT_BINDING_ADMIN_ROLES: frozenset[Role] = frozenset({Role.ORGANIZATION_ADMIN})

_COLLECTION_PATH = "/api/v1/organizations/{organization_id}/component-bindings"
_ITEM_PATH = _COLLECTION_PATH + "/{binding_id}"
_VALIDATE_PATH = _ITEM_PATH + "/validate"
_STATUS_PATH = "/api/v1/organizations/{organization_id}/component-status"


def _serialize(binding: ComponentBinding) -> dict[str, object]:
    """Never includes `secret_reference` -- write-only from the API's
    point of view, the same `LiteLLMConnection` precedent (Section 65.4:
    'never displaying token values')."""
    return {
        "id": str(binding.id),
        "organization_id": str(binding.organization_id),
        "component_key": binding.component_key,
        "security_zone_scope": binding.security_zone_scope,
        "project_scope": binding.project_scope,
        "deployment_mode": binding.deployment_mode,
        "approved_endpoint": binding.approved_endpoint,
        "network_policy_identity": binding.network_policy_identity,
        "health_result": binding.health_result,
        "health_checked_at": (
            binding.health_checked_at.isoformat() if binding.health_checked_at else None
        ),
        "health_detail": binding.health_detail,
        "compatibility_lock_reference": binding.compatibility_lock_reference,
        "row_version": binding.row_version,
        "created_at": binding.created_at.isoformat(),
        "updated_at": binding.updated_at.isoformat(),
    }


def _actor_from(ctx: AuthorizedContext) -> ActorInfo:
    return ActorInfo(
        actor_type="user",
        actor_id=ctx.principal.subject,
        request_id=uuid.uuid4().hex,
    )


async def _read_json_body(request: Request) -> dict[str, Any]:
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


def build_component_binding_router(
    session_factory: Callable[[], Session],
    *,
    principal_resolver: PrincipalResolver = default_principal_resolver,
    role_lookup: RoleLookup = default_role_lookup,
    health_policy_factory: Callable[[], ConnectionPolicy] = default_component_health_policy,
) -> APIRouter:
    """`health_policy_factory` follows the same call-time-only injection
    convention `connection_routes.py`'s own `validation_policy_factory`
    established -- a real integration test can point it at a real local
    test server's own reachable address without weakening the production
    default."""
    router = APIRouter()
    admin_dependency = Depends(
        require_role(
            COMPONENT_BINDING_ADMIN_ROLES,
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
                binding = create_component_binding(
                    session,
                    tenant_context,
                    component_key=_require_str(payload, "component_key"),
                    deployment_mode=_require_str(payload, "deployment_mode"),
                    approved_endpoint=_optional_str(payload, "approved_endpoint"),
                    secret_reference=_optional_str(payload, "secret_reference"),
                    actor=_actor_from(ctx),
                )
                body = _serialize(binding)
        except (ComponentBindingValidationError, ValueError) as exc:
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
            bindings = list_component_bindings(session, tenant_context)
            body = [_serialize(b) for b in bindings]
        return JSONResponse(body, status_code=200)

    @router.get(_ITEM_PATH)
    async def get_one(
        organization_id: uuid.UUID,
        binding_id: uuid.UUID,
        ctx: AuthorizedContext = admin_dependency,
    ) -> JSONResponse:
        del organization_id
        tenant_context = TenantContext.from_authorized_context(ctx)
        try:
            with tenant_scoped_session_for(session_factory, tenant_context) as session:
                binding = get_component_binding(session, tenant_context, binding_id)
                body = _serialize(binding)
        except (ComponentBindingNotFoundError, TenantContextError) as exc:
            raise HTTPException(status_code=404, detail="not_found") from exc
        return JSONResponse(body, status_code=200)

    @router.patch(_ITEM_PATH)
    async def update(
        request: Request,
        organization_id: uuid.UUID,
        binding_id: uuid.UUID,
        ctx: AuthorizedContext = admin_dependency,
    ) -> JSONResponse:
        del organization_id
        payload = await _read_json_body(request)
        row_version = payload.get("expected_row_version")
        if not isinstance(row_version, int) or isinstance(row_version, bool):
            raise HTTPException(
                status_code=400, detail="expected_row_version is required and must be an integer"
            )
        tenant_context = TenantContext.from_authorized_context(ctx)
        try:
            with tenant_scoped_session_for(session_factory, tenant_context) as session:
                binding = update_component_binding(
                    session,
                    tenant_context,
                    binding_id,
                    expected_row_version=row_version,
                    approved_endpoint=_optional_str(payload, "approved_endpoint"),
                    secret_reference=_optional_str(payload, "secret_reference"),
                    actor=_actor_from(ctx),
                )
                body = _serialize(binding)
        except (ComponentBindingNotFoundError, TenantContextError) as exc:
            raise HTTPException(status_code=404, detail="not_found") from exc
        except ComponentBindingVersionConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except (ComponentBindingValidationError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return JSONResponse(body, status_code=200)

    @router.post(_VALIDATE_PATH)
    async def validate(
        organization_id: uuid.UUID,
        binding_id: uuid.UUID,
        ctx: AuthorizedContext = admin_dependency,
    ) -> JSONResponse:
        del organization_id
        tenant_context = TenantContext.from_authorized_context(ctx)
        try:
            with tenant_scoped_session_for(session_factory, tenant_context) as session:
                binding = validate_component_binding(
                    session,
                    tenant_context,
                    binding_id,
                    policy=health_policy_factory(),
                    actor=_actor_from(ctx),
                )
                body = _serialize(binding)
        except (ComponentBindingNotFoundError, TenantContextError) as exc:
            raise HTTPException(status_code=404, detail="not_found") from exc
        return JSONResponse(body, status_code=200)

    @router.get(_STATUS_PATH)
    async def component_status(
        organization_id: uuid.UUID,
        ctx: AuthorizedContext = admin_dependency,
    ) -> JSONResponse:
        del organization_id
        tenant_context = TenantContext.from_authorized_context(ctx)
        with tenant_scoped_session_for(session_factory, tenant_context) as session:
            entries = get_component_status_projection(session, tenant_context)
            body = [
                {
                    "component_key": e.component_key,
                    "configured": e.configured,
                    "health_result": e.health_result,
                    "health_checked_at": (
                        e.health_checked_at.isoformat() if e.health_checked_at else None
                    ),
                    "pending_outbox_count": e.pending_outbox_count,
                    "failed_outbox_count": e.failed_outbox_count,
                    "blocked_reason": e.blocked_reason,
                }
                for e in entries
            ]
        return JSONResponse(body, status_code=200)

    return router
