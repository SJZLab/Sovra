"""Live `/api/v1/organizations/{organization_id}/projects/{project_id}/tools`
routes (FTF-0404, Section 6.9/7A/22/45/65-66/77/81) -- the fifth
live-wired product-runtime path in this repository, after FTF-0101's
`/auth/*`, FTF-0201's `/connections/litellm`, FTF-0301's `/artifacts` and
FTF-0401's `/projects`.

Path shape: nested under a specific project, unlike FTF-0401's own
`workspace_id`-as-body-field choice for `Project` -- a `ToolDefinition`
genuinely belongs to one project for its entire lifetime (Section 6.9's
own `project_id` field), so the URL itself names that project, matching
Section 22 Screen D's own framing of "tools and schemas" as part of one
project's own manifest review.

Scope (disclosed explicitly, not hidden -- see this task's own
docs/certifications/queue.md submission entry): this router implements
create/get/list/edit for a project-scoped `ToolDefinition`. It does not
implement delete/archive -- Section 6.9 names no status field, and
nothing in this task's own acceptance/negative_acceptance calls for
removing a tool schema, only for versioning an edit to one. It also does
not implement the Agent Manifest `tool_contracts_json` consumer
(FTF-0408 and later, separately dependency-gated).

Role gating: see `tool_definition_crud.py`'s own module docstring for the
full Section 45 reasoning -- reads are open to every role Section 45
names, writes (create/edit) are restricted to `{workspace_admin,
organization_admin}`.
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
from app.tools.tool_definition_crud import (
    ActorInfo,
    ProjectNotFoundError,
    ToolDefinitionNotFoundError,
    ToolDefinitionStateError,
    create_tool_definition,
    edit_tool_definition,
    get_tool_definition,
    list_tool_definitions,
)
from app.tools.tool_definition_schema import ToolDefinition, ToolDefinitionValidationError

__all__ = ["TOOL_READ_ROLES", "TOOL_WRITE_ROLES", "build_tool_definition_router"]

# Section 45's own literal role table -- every role it names, applied to
# this resource's reads (no dedicated restrictive sentence exists for
# tool schemas, matching FTF-0301/FTF-0401's own precedent).
TOOL_READ_ROLES: frozenset[Role] = frozenset(
    {
        Role.ORGANIZATION_ADMIN,
        Role.WORKSPACE_ADMIN,
        Role.ML_ENGINEER,
        Role.EVALUATOR,
        Role.APPROVER,
        Role.VIEWER,
    }
)
# Section 45: "workspace_admin: project administration" is the only
# sentence naming a role for this class of per-project configuration
# work -- the same precedent FTF-0401's own PROJECT_WRITE_ROLES applied.
TOOL_WRITE_ROLES: frozenset[Role] = frozenset({Role.WORKSPACE_ADMIN, Role.ORGANIZATION_ADMIN})

_COLLECTION_PATH = "/api/v1/organizations/{organization_id}/projects/{project_id}/tools"
_ITEM_PATH = _COLLECTION_PATH + "/{tool_definition_id}"


def _serialize(tool: ToolDefinition) -> dict[str, object]:
    return {
        "id": str(tool.id),
        "organization_id": str(tool.organization_id),
        "project_id": str(tool.project_id),
        "name": tool.name,
        "description": tool.description,
        "input_schema": dict(tool.input_schema_json or {}),
        "output_schema": dict(tool.output_schema_json or {}),
        "side_effect_class": tool.side_effect_class,
        "requires_confirmation": tool.requires_confirmation,
        "is_critical": tool.is_critical,
        "version": tool.version,
        "schema_hash": tool.schema_hash,
        "previous_version_id": str(tool.previous_version_id) if tool.previous_version_id else None,
        "superseded_at": tool.superseded_at.isoformat() if tool.superseded_at else None,
        "created_by": tool.created_by,
        "created_at": tool.created_at.isoformat(),
        "updated_at": tool.updated_at.isoformat(),
    }


def _actor_from(ctx: AuthorizedContext, request: Request) -> ActorInfo:
    del request
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


def _require_dict(payload: dict[str, Any], key: str) -> dict[str, object]:
    value = payload.get(key)
    if not isinstance(value, dict):
        raise HTTPException(status_code=400, detail=f"{key} is required and must be an object")
    return value


def _optional_str(payload: dict[str, Any], key: str) -> str | None:
    if key not in payload:
        return None
    value = payload[key]
    if value is not None and not isinstance(value, str):
        raise HTTPException(status_code=400, detail=f"{key} must be a string or null")
    return value


def _optional_dict(payload: dict[str, Any], key: str) -> dict[str, object] | None:
    if key not in payload:
        return None
    value = payload[key]
    if not isinstance(value, dict):
        raise HTTPException(status_code=400, detail=f"{key} must be an object")
    return value


def _optional_bool(payload: dict[str, Any], key: str) -> bool | None:
    if key not in payload:
        return None
    value = payload[key]
    if not isinstance(value, bool):
        raise HTTPException(status_code=400, detail=f"{key} must be a boolean")
    return value


def build_tool_definition_router(
    session_factory: Callable[[], Session],
    *,
    principal_resolver: PrincipalResolver = default_principal_resolver,
    role_lookup: RoleLookup = default_role_lookup,
) -> APIRouter:
    """Real FastAPI router for FTF-0404's create/get/list/edit surface.

    `session_factory`/`principal_resolver`/`role_lookup` follow the exact
    same injection convention FTF-0201/FTF-0301/FTF-0401's own routers
    established -- fail-closed defaults until a real session-reader/
    DB-backed Membership lookup is wired in (same disclosed gap FTF-0103/
    FTF-0106/FTF-0201/FTF-0301/FTF-0401 already carry).
    """
    router = APIRouter()
    read_dependency = Depends(
        require_role(
            TOOL_READ_ROLES, principal_resolver=principal_resolver, role_lookup=role_lookup
        )
    )
    write_dependency = Depends(
        require_role(
            TOOL_WRITE_ROLES, principal_resolver=principal_resolver, role_lookup=role_lookup
        )
    )

    @router.post(_COLLECTION_PATH, status_code=201)
    async def create(
        request: Request,
        organization_id: uuid.UUID,
        project_id: uuid.UUID,
        ctx: AuthorizedContext = write_dependency,
    ) -> JSONResponse:
        del organization_id
        payload = await _read_json_body(request)
        tenant_context = TenantContext.from_authorized_context(ctx)
        try:
            with tenant_scoped_session_for(session_factory, tenant_context) as session:
                tool = create_tool_definition(
                    session,
                    tenant_context,
                    project_id=project_id,
                    name=_require_str(payload, "name"),
                    description=_optional_str(payload, "description"),
                    input_schema_json=_require_dict(payload, "input_schema"),
                    output_schema_json=_require_dict(payload, "output_schema"),
                    side_effect_class=_require_str(payload, "side_effect_class"),
                    requires_confirmation=_optional_bool(payload, "requires_confirmation") or False,
                    is_critical=_optional_bool(payload, "is_critical") or False,
                    actor=_actor_from(ctx, request),
                )
                body = _serialize(tool)
        except (ProjectNotFoundError, TenantContextError) as exc:
            raise HTTPException(status_code=404, detail="project_not_found") from exc
        except (ValueError, ToolDefinitionValidationError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return JSONResponse(body, status_code=201)

    @router.get(_COLLECTION_PATH)
    async def list_all(
        organization_id: uuid.UUID,
        project_id: uuid.UUID,
        ctx: AuthorizedContext = read_dependency,
    ) -> JSONResponse:
        del organization_id
        tenant_context = TenantContext.from_authorized_context(ctx)
        with tenant_scoped_session_for(session_factory, tenant_context) as session:
            tools = list_tool_definitions(session, tenant_context, project_id)
            body = [_serialize(t) for t in tools]
        return JSONResponse(body, status_code=200)

    @router.get(_ITEM_PATH)
    async def get_one(
        organization_id: uuid.UUID,
        project_id: uuid.UUID,
        tool_definition_id: uuid.UUID,
        ctx: AuthorizedContext = read_dependency,
    ) -> JSONResponse:
        del organization_id
        tenant_context = TenantContext.from_authorized_context(ctx)
        try:
            with tenant_scoped_session_for(session_factory, tenant_context) as session:
                tool = get_tool_definition(session, tenant_context, project_id, tool_definition_id)
                body = _serialize(tool)
        except (ToolDefinitionNotFoundError, TenantContextError) as exc:
            raise HTTPException(status_code=404, detail="not_found") from exc
        return JSONResponse(body, status_code=200)

    @router.patch(_ITEM_PATH)
    async def edit(
        request: Request,
        organization_id: uuid.UUID,
        project_id: uuid.UUID,
        tool_definition_id: uuid.UUID,
        ctx: AuthorizedContext = write_dependency,
    ) -> JSONResponse:
        del organization_id
        payload = await _read_json_body(request)
        tenant_context = TenantContext.from_authorized_context(ctx)
        try:
            with tenant_scoped_session_for(session_factory, tenant_context) as session:
                tool = edit_tool_definition(
                    session,
                    tenant_context,
                    project_id,
                    tool_definition_id,
                    description=_optional_str(payload, "description"),
                    input_schema_json=_optional_dict(payload, "input_schema"),
                    output_schema_json=_optional_dict(payload, "output_schema"),
                    side_effect_class=_optional_str(payload, "side_effect_class"),
                    requires_confirmation=_optional_bool(payload, "requires_confirmation"),
                    is_critical=_optional_bool(payload, "is_critical"),
                    actor=_actor_from(ctx, request),
                )
                body = _serialize(tool)
        except (ToolDefinitionNotFoundError, TenantContextError) as exc:
            raise HTTPException(status_code=404, detail="not_found") from exc
        except ToolDefinitionStateError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except (ValueError, ToolDefinitionValidationError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return JSONResponse(body, status_code=200)

    return router
