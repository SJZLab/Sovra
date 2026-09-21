"""Live `/api/v1/organizations/{organization_id}/projects/{project_id}/requirements`
route (FTF-0407, Section 6.9/7A.6/22/45/65-66/77/81) -- the sixth
live-wired product-runtime path in this repository, after FTF-0101's
`/auth/*`, FTF-0201's `/connections/litellm`, FTF-0301's `/artifacts`,
FTF-0401's `/projects` and FTF-0404's own `/tools`.

`packages/contracts/openapi/04-interface.yaml`'s own module docstring
deliberately left `/requirements` undeclared, naming FTF-0407/FTF-0408 as
the tasks that would add it for real -- this is that route. It is
read-only and derived, never a separate stored claim: every current
`ToolDefinition` a project has already passed
`enforce_critical_behavior_requirement`
(`app.services.critical_behavior_requirements`) at write time, so this
view only *reports* that already-enforced state, it does not re-decide
it.

Scope (disclosed explicitly, not hidden -- see this task's own
`docs/certifications/queue.md` submission entry): this is a read-only
projection over `ToolDefinition` rows, not the full mandate-authoring
Screen D/F experience Section 22 describes, and not `FTF-0408`'s own
later "Requirements UI" page that assembles this alongside project
privacy/budget policy. `AgentManifest`'s own `mandate_json`/
`forbidden_actions_json` (Section 6.8D) do not exist as real storage
anywhere in this repository yet -- see
`app.services.critical_behavior_requirements`'s own module docstring for
the full reasoning.

Role gating matches `tool_definition_routes.py`'s own `TOOL_READ_ROLES`
exactly -- this is a read view over the same underlying resource, with no
dedicated restrictive Section 45 sentence of its own.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.auth.rbac_middleware import (
    AuthorizedContext,
    PrincipalResolver,
    RoleLookup,
    default_principal_resolver,
    default_role_lookup,
    require_role,
)
from app.db.tenant_context import TenantContext, tenant_scoped_session_for
from app.services.critical_behavior_requirements import (
    CriticalBehaviorRequirementView,
    derive_critical_behavior_requirement_view,
)
from app.tools.tool_definition_crud import list_tool_definitions
from app.tools.tool_definition_routes import TOOL_READ_ROLES

__all__ = ["build_critical_behavior_requirement_router"]

_COLLECTION_PATH = "/api/v1/organizations/{organization_id}/projects/{project_id}/requirements"


def _serialize(view: CriticalBehaviorRequirementView) -> dict[str, object]:
    return {
        "tool_definition_id": view.tool_definition_id,
        "name": view.name,
        "side_effect_class": view.side_effect_class,
        "is_critical": view.is_critical,
        "requires_confirmation": view.requires_confirmation,
        "measurable": view.measurable,
        "runtime_enforced": view.runtime_enforced,
    }


def build_critical_behavior_requirement_router(
    session_factory: Callable[[], Session],
    *,
    principal_resolver: PrincipalResolver = default_principal_resolver,
    role_lookup: RoleLookup = default_role_lookup,
) -> APIRouter:
    """Real FastAPI router for FTF-0407's own read-only `/requirements`
    surface. `session_factory`/`principal_resolver`/`role_lookup` follow
    the exact same injection convention FTF-0201/FTF-0301/FTF-0401/
    FTF-0404's own routers established."""
    router = APIRouter()
    read_dependency = Depends(
        require_role(
            TOOL_READ_ROLES, principal_resolver=principal_resolver, role_lookup=role_lookup
        )
    )

    @router.get(_COLLECTION_PATH)
    async def list_requirements(
        organization_id: uuid.UUID,
        project_id: uuid.UUID,
        ctx: AuthorizedContext = read_dependency,
    ) -> JSONResponse:
        del organization_id
        tenant_context = TenantContext.from_authorized_context(ctx)
        with tenant_scoped_session_for(session_factory, tenant_context) as session:
            tools = list_tool_definitions(session, tenant_context, project_id)
            body = [
                _serialize(
                    derive_critical_behavior_requirement_view(
                        tool_definition_id=str(tool.id),
                        name=tool.name,
                        side_effect_class=tool.side_effect_class,
                        is_critical=tool.is_critical,
                        requires_confirmation=tool.requires_confirmation,
                    )
                )
                for tool in tools
            ]
        return JSONResponse(body, status_code=200)

    return router
