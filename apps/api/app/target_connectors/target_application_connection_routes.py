"""Live `/api/v1/organizations/{organization_id}/projects/{project_id}/
target-connections` routes (FTF-04A01, Section 6.8A/7A.1-7A.3/9/45/65-67/
77/81).

Scope (disclosed explicitly, not hidden -- see this task's own
docs/certifications/queue.md submission entry): this router implements
create/get/list/edit/revoke for the `TargetApplicationConnection` record
itself. It deliberately does not implement the `TargetApplicationConnector`
Protocol (Section 7A.1 -- `validate_connection`/`resolve_revision`/
`create_snapshot`/`list_candidate_agents`/`read_evidence`/`detect_drift`),
a real GitHub App/token exchange (Section 7A.2 -- FTF-04A02), the local-
repository root-allowlist (Section 7A.3 -- FTF-04A06), or any
`/snapshot`/`/candidates`/`/manifest` route (no `TargetSourceSnapshot`/
`DiscoveredAgentCandidate`/`AgentManifest` entity exists anywhere in this
repository yet -- each is a separate, dependency-gated later task).
Declaring any of that as a real, enforced capability here without a real
backing implementation would be exactly the "placeholder endpoint" Law 11
forbids.

Path shape follows FTF-0404/FTF-0500's own established
`/api/v1/organizations/{organization_id}/projects/{project_id}/<resource>`
convention exactly (refined from the task packet's own abbreviated
`api_contract: "/api/v1/projects/{id}/target-connections; snapshots/
candidates/manifests/evidence/target-drift"` the same way every prior task
in this repository has refined its own abbreviated packet path -- only the
first segment, the connection record itself, is this task's own real
scope).

Role gating: Section 45's own literal "organization_admin: connections,
security profiles, cost profiles" sentence -- the same narrower,
credential-bearing-"connection" precedent FTF-0201's own
`CONNECTION_ADMIN_ROLES` already established (`{organization_admin}`
alone, gating every operation including reads -- this resource, tied to
real target source-code access, does not open reads wider than writes).
"""

from __future__ import annotations

import os
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

from packages.litellm_client.secret_manager_integration import (  # noqa: E402
    OpenBaoConfig,
    SecretManagerConfigurationError,
)

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
from app.db.tenant_context import TenantContext, tenant_scoped_session_for  # noqa: E402
from app.target_connectors.target_application_connection_crud import (  # noqa: E402
    ActorInfo,
    ProjectNotFoundError,
    TargetApplicationConnectionConfigurationError,
    TargetApplicationConnectionNotFoundError,
    TargetApplicationConnectionStateError,
    create_target_application_connection,
    get_target_application_connection,
    list_target_application_connections,
    revoke_target_application_connection,
    update_target_application_connection,
)
from app.target_connectors.target_application_connection_schema import (  # noqa: E402
    TargetApplicationConnection,
    TargetApplicationConnectionValidationError,
)

__all__ = ["TARGET_CONNECTION_ROLES", "build_target_application_connection_router"]

# Section 45: "organization_admin: connections, security profiles, cost
# profiles" is the only sentence naming a role for this class of resource.
TARGET_CONNECTION_ROLES: frozenset[Role] = frozenset({Role.ORGANIZATION_ADMIN})

_COLLECTION_PATH = (
    "/api/v1/organizations/{organization_id}/projects/{project_id}/target-connections"
)
_ITEM_PATH = _COLLECTION_PATH + "/{connection_id}"


def _default_secret_manager_config() -> OpenBaoConfig | None:
    """Lazily reads real backend configuration from the environment at
    call time (never at import/app-boot time) -- a deployment/test process
    that never exercises a credential-bearing request never needs
    `SOVRA_OPENBAO_ADDR`/`SOVRA_OPENBAO_TOKEN` set at all."""
    try:
        return OpenBaoConfig.from_env(os.environ)
    except SecretManagerConfigurationError:
        return None


def _serialize(connection: TargetApplicationConnection) -> dict[str, object]:
    """Never includes `credential_secret_ref` -- Section 46 ("Secret
    values never returned to browser after creation"), the same
    discipline `connection_routes.py::_serialize`/
    `dataset_source_connection_routes.py::_serialize` already established."""
    return {
        "id": str(connection.id),
        "organization_id": str(connection.organization_id),
        "project_id": str(connection.project_id),
        "connector_type": connection.connector_type,
        "display_name": connection.display_name,
        "repository_identity": connection.repository_identity,
        "repository_url_redacted": connection.repository_url_redacted,
        "default_branch": connection.default_branch,
        "selected_branch": connection.selected_branch,
        "has_credential": connection.credential_secret_ref is not None,
        "access_mode": connection.access_mode,
        "connection_status": connection.connection_status,
        "last_validated_at": (
            connection.last_validated_at.isoformat() if connection.last_validated_at else None
        ),
        "created_by": connection.created_by,
        "created_at": connection.created_at.isoformat(),
        "updated_at": connection.updated_at.isoformat(),
        "revoked_at": connection.revoked_at.isoformat() if connection.revoked_at else None,
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


def _optional_str(payload: dict[str, Any], key: str) -> str | None:
    if key not in payload:
        return None
    value = payload[key]
    if value is not None and not isinstance(value, str):
        raise HTTPException(status_code=400, detail=f"{key} must be a string or null")
    return value


def build_target_application_connection_router(
    session_factory: Callable[[], Session],
    *,
    principal_resolver: PrincipalResolver = default_principal_resolver,
    role_lookup: RoleLookup = default_role_lookup,
    secret_manager_config_factory: Callable[
        [], OpenBaoConfig | None
    ] = _default_secret_manager_config,
) -> APIRouter:
    """Real FastAPI router for FTF-04A01's CRUD surface.

    `session_factory`/`principal_resolver`/`role_lookup` follow the exact
    same injection convention FTF-0201/FTF-0301/FTF-0401/FTF-0404/FTF-0500's
    own routers established. `secret_manager_config_factory` is called
    fresh on every request that might need it (never cached at router-build
    time) so a rotated `SOVRA_OPENBAO_TOKEN` takes effect on the next
    request without an app restart.
    """
    router = APIRouter()
    dependency = Depends(
        require_role(
            TARGET_CONNECTION_ROLES, principal_resolver=principal_resolver, role_lookup=role_lookup
        )
    )

    @router.post(_COLLECTION_PATH, status_code=201)
    async def create(
        request: Request,
        organization_id: uuid.UUID,
        project_id: uuid.UUID,
        ctx: AuthorizedContext = dependency,
    ) -> JSONResponse:
        del organization_id
        payload = await _read_json_body(request)
        tenant_context = TenantContext.from_authorized_context(ctx)
        try:
            with tenant_scoped_session_for(session_factory, tenant_context) as session:
                connection = create_target_application_connection(
                    session,
                    tenant_context,
                    project_id=project_id,
                    connector_type=_require_str(payload, "connector_type"),
                    display_name=_require_str(payload, "display_name"),
                    repository_identity=_require_str(payload, "repository_identity"),
                    repository_url_redacted=_optional_str(payload, "repository_url_redacted"),
                    default_branch=_optional_str(payload, "default_branch"),
                    selected_branch=_optional_str(payload, "selected_branch"),
                    credential=_optional_str(payload, "credential"),
                    secret_manager_config=secret_manager_config_factory(),
                    actor=_actor_from(ctx, request),
                    access_mode=_optional_str(payload, "access_mode") or "read_only",
                )
                body = _serialize(connection)
        except (ProjectNotFoundError, TenantContextError) as exc:
            raise HTTPException(status_code=404, detail="project_not_found") from exc
        except TargetApplicationConnectionConfigurationError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        except (ValueError, TargetApplicationConnectionValidationError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return JSONResponse(body, status_code=201)

    @router.get(_COLLECTION_PATH)
    async def list_all(
        organization_id: uuid.UUID,
        project_id: uuid.UUID,
        ctx: AuthorizedContext = dependency,
    ) -> JSONResponse:
        del organization_id
        tenant_context = TenantContext.from_authorized_context(ctx)
        with tenant_scoped_session_for(session_factory, tenant_context) as session:
            connections = list_target_application_connections(session, tenant_context, project_id)
            body = [_serialize(c) for c in connections]
        return JSONResponse(body, status_code=200)

    @router.get(_ITEM_PATH)
    async def get_one(
        organization_id: uuid.UUID,
        project_id: uuid.UUID,
        connection_id: uuid.UUID,
        ctx: AuthorizedContext = dependency,
    ) -> JSONResponse:
        del organization_id
        tenant_context = TenantContext.from_authorized_context(ctx)
        try:
            with tenant_scoped_session_for(session_factory, tenant_context) as session:
                connection = get_target_application_connection(
                    session, tenant_context, project_id, connection_id
                )
                body = _serialize(connection)
        except (TargetApplicationConnectionNotFoundError, TenantContextError) as exc:
            raise HTTPException(status_code=404, detail="not_found") from exc
        return JSONResponse(body, status_code=200)

    @router.patch(_ITEM_PATH)
    async def update(
        request: Request,
        organization_id: uuid.UUID,
        project_id: uuid.UUID,
        connection_id: uuid.UUID,
        ctx: AuthorizedContext = dependency,
    ) -> JSONResponse:
        del organization_id
        payload = await _read_json_body(request)
        tenant_context = TenantContext.from_authorized_context(ctx)
        try:
            with tenant_scoped_session_for(session_factory, tenant_context) as session:
                connection = update_target_application_connection(
                    session,
                    tenant_context,
                    project_id,
                    connection_id,
                    display_name=_optional_str(payload, "display_name"),
                    selected_branch=_optional_str(payload, "selected_branch"),
                    actor=_actor_from(ctx, request),
                )
                body = _serialize(connection)
        except (TargetApplicationConnectionNotFoundError, TenantContextError) as exc:
            raise HTTPException(status_code=404, detail="not_found") from exc
        except TargetApplicationConnectionStateError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except (ValueError, TargetApplicationConnectionValidationError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return JSONResponse(body, status_code=200)

    @router.delete(_ITEM_PATH)
    async def revoke(
        request: Request,
        organization_id: uuid.UUID,
        project_id: uuid.UUID,
        connection_id: uuid.UUID,
        ctx: AuthorizedContext = dependency,
    ) -> JSONResponse:
        del organization_id
        tenant_context = TenantContext.from_authorized_context(ctx)
        try:
            with tenant_scoped_session_for(session_factory, tenant_context) as session:
                connection = revoke_target_application_connection(
                    session,
                    tenant_context,
                    project_id,
                    connection_id,
                    actor=_actor_from(ctx, request),
                )
                body = _serialize(connection)
        except (TargetApplicationConnectionNotFoundError, TenantContextError) as exc:
            raise HTTPException(status_code=404, detail="not_found") from exc
        return JSONResponse(body, status_code=200)

    return router
