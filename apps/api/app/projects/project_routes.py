"""Live `/api/v1/organizations/{organization_id}/projects` routes
(FTF-0401, Section 6.8/7A/22/45/65-66/77/81) -- the fourth live-wired
product-runtime path in this repository, after FTF-0101's `/auth/*`,
FTF-0201's `/connections/litellm` and FTF-0301's `/artifacts`.

Scope (disclosed explicitly, not hidden -- see this task's own
docs/certifications/queue.md submission entry): this router implements
create/get/list/edit/save-ui-state/archive for a workspace-scoped
`Project`. It deliberately does not implement `/requirements` or `/tools`
routes (the task packet's own abbreviated `api_contract` mentions both) --
those are FTF-0404 ("Tool schema CRUD")/FTF-0407 ("Critical behavior
requirements")/FTF-0408 ("Requirements UI")'s own, later, separately
dependency-gated tasks (see docs/TASK_INDEX.md). PATCH now accepts
`budget_policy_id` (FTF-0403): `project_crud.py`'s `update_project` runs
it through `app.services.budget_policy.enforce_budget_policy_reference`
before persisting it, the same real-lookup-before-write discipline
`security_profile` (FTF-0402) already established for this router. There
is still no route to *author* a `BudgetPolicy` itself -- FTF-0403's own
disclosed scope, see `apps/api/app/services/budget_policy.py`'s own
module docstring for exactly why.

Path shape (refined from the task packet's own abbreviated
`api_contract: "/api/v1/projects; /requirements; /tools; ..."` to the same
concrete `/api/v1/organizations/{organization_id}/<resource>` collection
shape FTF-0201/FTF-0301 already established for every other tenant-scoped
resource): `workspace_id` is a create-time body field, authorized the same
way any other foreign-key reference into a tenant-owned parent is
(`authorize_resource_ownership` against `workspaces`), rather than a
second path segment -- there is no existing precedent in this repository
for workspace-nested routes, and Section 6.8 never requires the URL itself
to be workspace-scoped.

Role gating: see `project_crud.py`'s own module docstring for the full
Section 45 reasoning -- reads are open to every role Section 45 names,
writes (create/edit/save-ui-state/archive) are restricted to
`{workspace_admin, organization_admin}` (Section 45's "workspace_admin:
project administration" is the only sentence naming a role for this class
of work).
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
from app.projects.project_crud import (
    ActorInfo,
    ProjectConcurrencyError,
    ProjectNotFoundError,
    ProjectStateError,
    WorkspaceNotFoundError,
    archive_project,
    create_project,
    get_project,
    list_projects,
    save_ui_state,
    update_project,
)
from app.projects.project_schema import Project
from app.services.budget_policy import BudgetPolicyNotFoundError, BudgetPolicyStateError

__all__ = ["PROJECT_READ_ROLES", "PROJECT_WRITE_ROLES", "build_project_router"]

# Section 45's own literal role table -- every role it names, applied to
# this resource's reads (no dedicated restrictive sentence exists for
# projects, matching FTF-0301's own precedent).
PROJECT_READ_ROLES: frozenset[Role] = frozenset(
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
# sentence naming a role for this class of work.
PROJECT_WRITE_ROLES: frozenset[Role] = frozenset({Role.WORKSPACE_ADMIN, Role.ORGANIZATION_ADMIN})

_COLLECTION_PATH = "/api/v1/organizations/{organization_id}/projects"
_ITEM_PATH = _COLLECTION_PATH + "/{project_id}"
_UI_STATE_PATH = _ITEM_PATH + "/ui-state"


def _serialize(project: Project) -> dict[str, object]:
    return {
        "id": str(project.id),
        "organization_id": str(project.organization_id),
        "workspace_id": str(project.workspace_id),
        "name": project.name,
        "description": project.description,
        "project_type": project.project_type,
        "agent_objective": project.agent_objective,
        "security_profile": project.security_profile,
        "budget_policy_id": str(project.budget_policy_id) if project.budget_policy_id else None,
        "status": project.status,
        "ui_state": dict(project.ui_state_json or {}),
        "row_version": project.row_version,
        "created_by": project.created_by,
        "created_at": project.created_at.isoformat(),
        "updated_at": project.updated_at.isoformat(),
        "archived_at": project.archived_at.isoformat() if project.archived_at else None,
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


def _require_uuid(payload: dict[str, Any], key: str) -> uuid.UUID:
    value = payload.get(key)
    if not isinstance(value, str):
        raise HTTPException(status_code=400, detail=f"{key} is required and must be a uuid string")
    try:
        return uuid.UUID(value)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"{key} must be a valid uuid") from exc


def _require_int(payload: dict[str, Any], key: str) -> int:
    value = payload.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        raise HTTPException(status_code=400, detail=f"{key} is required and must be an integer")
    return value


def _optional_str(payload: dict[str, Any], key: str) -> str | None:
    if key not in payload:
        return None
    value = payload[key]
    if value is not None and not isinstance(value, str):
        raise HTTPException(status_code=400, detail=f"{key} must be a string or null")
    return value


def _optional_uuid(payload: dict[str, Any], key: str) -> uuid.UUID | None:
    if key not in payload:
        return None
    value = payload[key]
    if value is None:
        return None
    if not isinstance(value, str):
        raise HTTPException(status_code=400, detail=f"{key} must be a uuid string or null")
    try:
        return uuid.UUID(value)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"{key} must be a valid uuid") from exc


def _optional_dict(payload: dict[str, Any], key: str) -> dict[str, object] | None:
    if key not in payload:
        return None
    value = payload[key]
    if not isinstance(value, dict):
        raise HTTPException(status_code=400, detail=f"{key} must be an object")
    return value


def build_project_router(
    session_factory: Callable[[], Session],
    *,
    principal_resolver: PrincipalResolver = default_principal_resolver,
    role_lookup: RoleLookup = default_role_lookup,
) -> APIRouter:
    """Real FastAPI router for FTF-0401's CRUD + UI-state surface.

    `session_factory`/`principal_resolver`/`role_lookup` follow the exact
    same injection convention FTF-0201/FTF-0301's own routers established
    -- fail-closed defaults until a real session-reader/DB-backed
    Membership lookup is wired in (same disclosed gap FTF-0103/FTF-0106/
    FTF-0201/FTF-0301 already carry).
    """
    router = APIRouter()
    read_dependency = Depends(
        require_role(
            PROJECT_READ_ROLES, principal_resolver=principal_resolver, role_lookup=role_lookup
        )
    )
    write_dependency = Depends(
        require_role(
            PROJECT_WRITE_ROLES, principal_resolver=principal_resolver, role_lookup=role_lookup
        )
    )

    @router.post(_COLLECTION_PATH, status_code=201)
    async def create(
        request: Request,
        organization_id: uuid.UUID,
        ctx: AuthorizedContext = write_dependency,
    ) -> JSONResponse:
        del organization_id
        payload = await _read_json_body(request)
        tenant_context = TenantContext.from_authorized_context(ctx)
        try:
            with tenant_scoped_session_for(session_factory, tenant_context) as session:
                project = create_project(
                    session,
                    tenant_context,
                    workspace_id=_require_uuid(payload, "workspace_id"),
                    name=_require_str(payload, "name"),
                    project_type=_require_str(payload, "project_type"),
                    description=_optional_str(payload, "description"),
                    agent_objective=_optional_str(payload, "agent_objective"),
                    security_profile=_optional_str(payload, "security_profile") or "standard",
                    actor=_actor_from(ctx, request),
                )
                body = _serialize(project)
        except (WorkspaceNotFoundError, TenantContextError) as exc:
            raise HTTPException(status_code=404, detail="workspace_not_found") from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return JSONResponse(body, status_code=201)

    @router.get(_COLLECTION_PATH)
    async def list_all(
        organization_id: uuid.UUID,
        ctx: AuthorizedContext = read_dependency,
    ) -> JSONResponse:
        del organization_id
        tenant_context = TenantContext.from_authorized_context(ctx)
        with tenant_scoped_session_for(session_factory, tenant_context) as session:
            projects = list_projects(session, tenant_context)
            body = [_serialize(p) for p in projects]
        return JSONResponse(body, status_code=200)

    @router.get(_ITEM_PATH)
    async def get_one(
        organization_id: uuid.UUID,
        project_id: uuid.UUID,
        ctx: AuthorizedContext = read_dependency,
    ) -> JSONResponse:
        del organization_id
        tenant_context = TenantContext.from_authorized_context(ctx)
        try:
            with tenant_scoped_session_for(session_factory, tenant_context) as session:
                project = get_project(session, tenant_context, project_id)
                body = _serialize(project)
        except (ProjectNotFoundError, TenantContextError) as exc:
            raise HTTPException(status_code=404, detail="not_found") from exc
        return JSONResponse(body, status_code=200)

    @router.patch(_ITEM_PATH)
    async def update(
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
                project = update_project(
                    session,
                    tenant_context,
                    project_id,
                    name=_optional_str(payload, "name"),
                    description=_optional_str(payload, "description"),
                    agent_objective=_optional_str(payload, "agent_objective"),
                    security_profile=_optional_str(payload, "security_profile"),
                    budget_policy_id=_optional_uuid(payload, "budget_policy_id"),
                    expected_row_version=_require_int(payload, "expected_row_version"),
                    actor=_actor_from(ctx, request),
                )
                body = _serialize(project)
        except (ProjectNotFoundError, BudgetPolicyNotFoundError, TenantContextError) as exc:
            raise HTTPException(status_code=404, detail="not_found") from exc
        except ProjectConcurrencyError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except (ProjectStateError, BudgetPolicyStateError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return JSONResponse(body, status_code=200)

    @router.post(_UI_STATE_PATH)
    async def ui_state(
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
                project = save_ui_state(
                    session,
                    tenant_context,
                    project_id,
                    ui_state=_optional_dict(payload, "ui_state") or {},
                    actor=_actor_from(ctx, request),
                )
                body = _serialize(project)
        except (ProjectNotFoundError, TenantContextError) as exc:
            raise HTTPException(status_code=404, detail="not_found") from exc
        except ProjectStateError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return JSONResponse(body, status_code=200)

    @router.delete(_ITEM_PATH)
    async def archive(
        request: Request,
        organization_id: uuid.UUID,
        project_id: uuid.UUID,
        ctx: AuthorizedContext = write_dependency,
    ) -> JSONResponse:
        del organization_id
        tenant_context = TenantContext.from_authorized_context(ctx)
        try:
            with tenant_scoped_session_for(session_factory, tenant_context) as session:
                project = archive_project(
                    session, tenant_context, project_id, actor=_actor_from(ctx, request)
                )
                body = _serialize(project)
        except (ProjectNotFoundError, TenantContextError) as exc:
            raise HTTPException(status_code=404, detail="not_found") from exc
        return JSONResponse(body, status_code=200)

    return router
