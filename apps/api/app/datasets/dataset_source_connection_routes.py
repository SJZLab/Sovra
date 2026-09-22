"""Live `/api/v1/organizations/{organization_id}/projects/{project_id}/
dataset-sources` routes (FTF-0500, Section 6.8F/7A.10/9/45/65-67/77/81).

Scope (disclosed explicitly, not hidden -- see this task's own
docs/certifications/queue.md submission entry): this router implements
create/get/list/edit/revoke plus one real authorization check
(`POST .../authorize`). It deliberately does not implement any actual
source-connector call (validating S3/Postgres/REST credentials against
the real external service -- FTF-0501-and-later's own job), a `/preview`
or `/import` route (Section 8's dataset import pipeline -- no `Dataset`
entity exists anywhere in this repository yet to import into), or the
broader `DataUseGrant` workflow (Section 67.4 -- approver identity,
retention, expiry; not one of Section 6's own listed entities). Declaring
any of that as a real, enforced capability here without a real backing
implementation would be exactly the "placeholder endpoint" Law 11 forbids.

Path shape follows FTF-0404's own established
`/api/v1/organizations/{organization_id}/projects/{project_id}/<resource>`
convention exactly (refined from the task packet's own abbreviated
`api_contract: "/api/v1/projects/{id}/dataset-sources; ..."` the same way
every prior task in this repository has refined its own abbreviated
packet path).

Role gating: see `dataset_source_connection_crud.py`'s own module
docstring for the full Section 45 reasoning -- every operation, including
reads, is gated to `{ml_engineer, workspace_admin, organization_admin}`.
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
from app.datasets.dataset_source_connection_crud import (  # noqa: E402
    ActorInfo,
    DatasetSourceAuthorizationError,
    DatasetSourceConnectionConfigurationError,
    DatasetSourceConnectionNotFoundError,
    DatasetSourceConnectionStateError,
    ProjectNotFoundError,
    authorize_dataset_source_purpose,
    create_dataset_source_connection,
    get_dataset_source_connection,
    list_dataset_source_connections,
    revoke_dataset_source_connection,
    update_dataset_source_connection,
)
from app.datasets.dataset_source_connection_schema import DatasetSourceConnection  # noqa: E402
from app.db.tenant_context import TenantContext, tenant_scoped_session_for  # noqa: E402

__all__ = ["DATASET_SOURCE_ROLES", "build_dataset_source_connection_router"]

# Section 45: "ml_engineer: datasets/training" is the only sentence naming
# a role for this class of work; workspace_admin/organization_admin are
# the same standing project-administration superset every other
# project-scoped resource in this repository already grants.
DATASET_SOURCE_ROLES: frozenset[Role] = frozenset(
    {Role.ML_ENGINEER, Role.WORKSPACE_ADMIN, Role.ORGANIZATION_ADMIN}
)

_COLLECTION_PATH = "/api/v1/organizations/{organization_id}/projects/{project_id}/dataset-sources"
_ITEM_PATH = _COLLECTION_PATH + "/{connection_id}"
_AUTHORIZE_PATH = _ITEM_PATH + "/authorize"


def _default_secret_manager_config() -> OpenBaoConfig | None:
    """Lazily reads real backend configuration from the environment at
    call time (never at import/app-boot time) -- a deployment/test process
    that never exercises a credential-bearing request never needs
    `SOVRA_OPENBAO_ADDR`/`SOVRA_OPENBAO_TOKEN` set at all. Returns `None`
    (not an exception) when unset, so a caller who supplies no
    credential/config value is completely unaffected;
    `create_dataset_source_connection` only raises
    `DatasetSourceConnectionConfigurationError` if a credential/config
    value was actually supplied with no backend available to store it."""
    try:
        return OpenBaoConfig.from_env(os.environ)
    except SecretManagerConfigurationError:
        return None


def _serialize(connection: DatasetSourceConnection) -> dict[str, object]:
    """Never includes `credential_secret_ref`/`connection_config_encrypted_ref`
    -- Section 46 ("Secret values never returned to browser after
    creation"), the same discipline `connection_routes.py::_serialize`
    already established for `LiteLLMConnection.secret_ref`."""
    return {
        "id": str(connection.id),
        "organization_id": str(connection.organization_id),
        "project_id": str(connection.project_id),
        "source_type": connection.source_type,
        "display_name": connection.display_name,
        "has_credential": connection.credential_secret_ref is not None,
        "has_connection_config": connection.connection_config_encrypted_ref is not None,
        "allowed_purposes": list(connection.allowed_purposes_json or []),
        "security_classification": connection.security_classification,
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


def _require_str_list(payload: dict[str, Any], key: str) -> list[str]:
    value = payload.get(key)
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise HTTPException(
            status_code=400, detail=f"{key} is required and must be a list of strings"
        )
    return value


def _optional_str_list(payload: dict[str, Any], key: str) -> list[str] | None:
    if key not in payload:
        return None
    return _require_str_list(payload, key)


def _optional_dict(payload: dict[str, Any], key: str) -> dict[str, object] | None:
    if key not in payload:
        return None
    value = payload[key]
    if not isinstance(value, dict):
        raise HTTPException(status_code=400, detail=f"{key} must be an object")
    return value


def build_dataset_source_connection_router(
    session_factory: Callable[[], Session],
    *,
    principal_resolver: PrincipalResolver = default_principal_resolver,
    role_lookup: RoleLookup = default_role_lookup,
    secret_manager_config_factory: Callable[
        [], OpenBaoConfig | None
    ] = _default_secret_manager_config,
) -> APIRouter:
    """Real FastAPI router for FTF-0500's CRUD + purpose-authorization
    surface.

    `session_factory`/`principal_resolver`/`role_lookup` follow the exact
    same injection convention FTF-0201/FTF-0301/FTF-0401/FTF-0404's own
    routers established. `secret_manager_config_factory` is called fresh
    on every request that might need it (never cached at router-build
    time) so a rotated `SOVRA_OPENBAO_TOKEN` takes effect on the next
    request without an app restart.
    """
    router = APIRouter()
    dependency = Depends(
        require_role(
            DATASET_SOURCE_ROLES, principal_resolver=principal_resolver, role_lookup=role_lookup
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
                connection = create_dataset_source_connection(
                    session,
                    tenant_context,
                    project_id=project_id,
                    source_type=_require_str(payload, "source_type"),
                    display_name=_require_str(payload, "display_name"),
                    allowed_purposes=_require_str_list(payload, "allowed_purposes"),
                    security_classification=_require_str(payload, "security_classification"),
                    credential=_optional_str(payload, "credential"),
                    connection_config=_optional_dict(payload, "connection_config"),
                    secret_manager_config=secret_manager_config_factory(),
                    actor=_actor_from(ctx, request),
                )
                body = _serialize(connection)
        except (ProjectNotFoundError, TenantContextError) as exc:
            raise HTTPException(status_code=404, detail="project_not_found") from exc
        except DatasetSourceConnectionConfigurationError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        except ValueError as exc:
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
            connections = list_dataset_source_connections(session, tenant_context, project_id)
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
                connection = get_dataset_source_connection(
                    session, tenant_context, project_id, connection_id
                )
                body = _serialize(connection)
        except (DatasetSourceConnectionNotFoundError, TenantContextError) as exc:
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
                connection = update_dataset_source_connection(
                    session,
                    tenant_context,
                    project_id,
                    connection_id,
                    display_name=_optional_str(payload, "display_name"),
                    allowed_purposes=_optional_str_list(payload, "allowed_purposes"),
                    security_classification=_optional_str(payload, "security_classification"),
                    actor=_actor_from(ctx, request),
                )
                body = _serialize(connection)
        except (DatasetSourceConnectionNotFoundError, TenantContextError) as exc:
            raise HTTPException(status_code=404, detail="not_found") from exc
        except DatasetSourceConnectionStateError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ValueError as exc:
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
                connection = revoke_dataset_source_connection(
                    session,
                    tenant_context,
                    project_id,
                    connection_id,
                    actor=_actor_from(ctx, request),
                )
                body = _serialize(connection)
        except (DatasetSourceConnectionNotFoundError, TenantContextError) as exc:
            raise HTTPException(status_code=404, detail="not_found") from exc
        return JSONResponse(body, status_code=200)

    @router.post(_AUTHORIZE_PATH)
    async def authorize(
        request: Request,
        organization_id: uuid.UUID,
        project_id: uuid.UUID,
        connection_id: uuid.UUID,
        ctx: AuthorizedContext = dependency,
    ) -> JSONResponse:
        """This task's own "authorization before import" acceptance clause
        made real and callable -- a later Dataset-import task (FTF-0501+)
        calls this exact real check (via `authorize_dataset_source_purpose`
        directly, or this route) before creating any Dataset from this
        connection. Returns 200 `{"authorized": true, "purpose": ...}` on
        success or 403 with a content-free reason on denial -- never a
        silent 200 for a denied purpose."""
        del organization_id
        payload = await _read_json_body(request)
        purpose = _require_str(payload, "purpose")
        tenant_context = TenantContext.from_authorized_context(ctx)
        try:
            with tenant_scoped_session_for(session_factory, tenant_context) as session:
                authorize_dataset_source_purpose(
                    session, tenant_context, project_id, connection_id, purpose
                )
        except (DatasetSourceConnectionNotFoundError, TenantContextError) as exc:
            raise HTTPException(status_code=404, detail="not_found") from exc
        except DatasetSourceAuthorizationError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        return JSONResponse({"authorized": True, "purpose": purpose}, status_code=200)

    return router
