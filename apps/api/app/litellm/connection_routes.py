"""Live `/api/v1/organizations/{organization_id}/connections/litellm` routes
(FTF-0201/FTF-0203, Section 2-3/38/45/65/77/81) -- the second live-wired
product-runtime path in this repository, after FTF-0101's `/auth/*`.

Scope (disclosed explicitly, not hidden -- see this task's own
docs/certifications/queue.md submission entry): this router implements
create/get/list/edit/revoke (FTF-0201) plus, as of FTF-0203, a real
`/validate` route. It deliberately does **not** declare `sync-models`/
`syncs`/`/models`/`/models/{id}/probe` routes, even as a stub or a `501`
-- those require FTF-0204 ("`/v1/models` discovery") and FTF-0210 ("Real
chat probe")'s own, later, separately dependency-gated acceptance
criteria (see `docs/TASK_INDEX.md`). Declaring that surface here without
a real backing call would be exactly the "placeholder endpoint" Law 11
and this task's own acceptance clause ("no placeholder endpoints")
forbid.

FTF-0203's own `/validate` route is this repository's first real caller
of both `packages/litellm_client/secret_manager_integration.py`
(FTF-0202, resolves `secret_ref` to a real credential) and
`packages/security/outbound_guard.py` (FTF-H006, the only module allowed
to dial the resolved gateway) -- see
`packages/litellm_client/connection_validation.py`'s own module
docstring for the full design. No independent schema change: the
validation result is computed fresh on every call and returned directly
in the response, never persisted onto `LiteLLMConnection`.

FTF-0204 adds a real `GET .../connections/litellm/{connection_id}/models`
route, this repository's first live caller of
`packages/litellm_client/v1_models_discovery.py::discover_models`. Same
tenant-scoped read, same revoked-connection 409 and foreign-tenant 404 as
`/validate`; reuses the same injected `validation_policy_factory`/
`secret_manager_config_factory` rather than adding a redundant pair.

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

from packages.litellm_client.connection_validation import (  # noqa: E402
    ConnectionValidationResult,
    default_connection_validation_policy,
    validate_connection,
)
from packages.litellm_client.secret_manager_integration import (  # noqa: E402
    OpenBaoConfig,
    SecretManagerConfigurationError,
)
from packages.litellm_client.v1_models_discovery import (  # noqa: E402
    ModelDiscoveryResult,
    discover_models,
)
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
from app.db.tenant_context import TenantContext, tenant_scoped_session_for  # noqa: E402
from app.litellm.connection_crud import (  # noqa: E402
    ActorInfo,
    ConnectionNotFoundError,
    ConnectionStateError,
    create_connection,
    get_connection,
    list_connections,
    revoke_connection,
    update_connection,
)
from app.litellm.connection_schema import LiteLLMConnection  # noqa: E402

__all__ = ["CONNECTION_ADMIN_ROLES", "build_litellm_connection_router"]

# Section 45: "organization_admin: connections, security profiles, cost
# profiles" is the only role Section 45 names for connections.
CONNECTION_ADMIN_ROLES: frozenset[Role] = frozenset({Role.ORGANIZATION_ADMIN})

_COLLECTION_PATH = "/api/v1/organizations/{organization_id}/connections/litellm"
_ITEM_PATH = _COLLECTION_PATH + "/{connection_id}"
_VALIDATE_PATH = _ITEM_PATH + "/validate"
_MODELS_PATH = _ITEM_PATH + "/models"


def _default_secret_manager_config() -> OpenBaoConfig | None:
    """Same lazy, call-time-only convention `dataset_source_connection_routes.py`
    (FTF-0500) already established: `None` when unset, never an exception
    at import/app-boot time -- a process that never validates a connection
    never needs `SOVRA_OPENBAO_ADDR`/`SOVRA_OPENBAO_TOKEN` set at all."""
    try:
        return OpenBaoConfig.from_env(os.environ)
    except SecretManagerConfigurationError:
        return None


def _serialize_validation(result: ConnectionValidationResult) -> dict[str, object]:
    """Content-free by construction: `result.detail` is already sanitized
    by `connection_validation.py` (never a raw exception message, never a
    resolved credential, never a raw gateway response body)."""
    return {
        "inference_state": result.inference_state.value,
        "management_state": result.management_state.value,
        "model_count": result.model_count,
        "detail": result.detail,
        "checked_at": result.checked_at.isoformat(),
    }


def _serialize_discovery(result: ModelDiscoveryResult) -> dict[str, object]:
    """Content-free by construction, same convention as
    `_serialize_validation`: `result.detail` is already sanitized by
    `v1_models_discovery.py` (never a raw exception message, credential
    or gateway response body)."""
    return {
        "state": result.state.value,
        "models": [
            {"id": model.id, "metadata_status": model.metadata_status.value}
            for model in result.models
        ],
        "model_count": result.model_count,
        "content_hash": result.content_hash,
        "detail": result.detail,
        "checked_at": result.checked_at.isoformat(),
    }


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
    secret_manager_config_factory: Callable[
        [], OpenBaoConfig | None
    ] = _default_secret_manager_config,
    validation_policy_factory: Callable[
        [], ConnectionPolicy
    ] = default_connection_validation_policy,
) -> APIRouter:
    """Real FastAPI router for FTF-0201's CRUD surface plus FTF-0203's
    `/validate` route.

    `session_factory` is the already-configured `sessionmaker` bound to
    the restricted `sovra_app` role's engine -- this router never opens
    its own engine/connection, matching FTF-0106's own demo-app
    convention. `principal_resolver`/`role_lookup` are injected the same
    way FTF-0103's own `require_role` and FTF-0106's own demo app inject
    them -- fail-closed defaults until a real session-reader/Membership
    lookup is wired in (same disclosed "no live Membership table yet"
    gap already carried by FTF-0103/FTF-0106).
    `secret_manager_config_factory` follows the exact same call-time-only
    injection convention `dataset_source_connection_routes.py` (FTF-0500)
    established -- called fresh on every `/validate` request, never
    cached at router-build time, so a rotated `SOVRA_OPENBAO_TOKEN` takes
    effect on the next request without an app restart. `validation_policy_factory`
    defaults to `default_connection_validation_policy` (no private/loopback
    allowlist -- a real production LiteLLM gateway is never on a loopback
    address) and is injected the same way so a real live-Postgres proof of
    this route can point it at a real local test gateway without weakening
    the production default -- see `tests/integration/
    test_ftf_0203_connection_validation.py`'s own module docstring.
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

    @router.post(_VALIDATE_PATH)
    async def validate(
        organization_id: uuid.UUID,
        connection_id: uuid.UUID,
        ctx: AuthorizedContext = admin_dependency,
    ) -> JSONResponse:
        """FTF-0203: dials the connection's own real gateway. The stored
        `base_url`/`secret_ref` are read inside the same tenant-scoped
        session as every other route on this connection (never trusting a
        client-supplied value for either) -- the foreign-connection-ID
        IDOR case is denied identically to a missing connection, matching
        every other route here."""
        del organization_id
        tenant_context = TenantContext.from_authorized_context(ctx)
        try:
            with tenant_scoped_session_for(session_factory, tenant_context) as session:
                connection = get_connection(session, tenant_context, connection_id)
                if connection.status == "revoked":
                    raise ConnectionStateError("cannot validate a revoked connection")
                base_url = connection.base_url
                secret_ref = connection.secret_ref
        except (ConnectionNotFoundError, TenantContextError) as exc:
            raise HTTPException(status_code=404, detail="not_found") from exc
        except ConnectionStateError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

        result = validate_connection(
            base_url=base_url,
            secret_ref=secret_ref,
            secret_config=secret_manager_config_factory(),
            policy=validation_policy_factory(),
        )
        return JSONResponse(_serialize_validation(result), status_code=200)

    @router.get(_MODELS_PATH)
    async def models(
        organization_id: uuid.UUID,
        connection_id: uuid.UUID,
        ctx: AuthorizedContext = admin_dependency,
    ) -> JSONResponse:
        """FTF-0204: lists the connection's real credential-visible
        model IDs. Same tenant-scoped read, same revoked-connection 409
        and foreign-tenant 404 pattern as `/validate` -- the stored
        `base_url`/`secret_ref` are never trusted from the client."""
        del organization_id
        tenant_context = TenantContext.from_authorized_context(ctx)
        try:
            with tenant_scoped_session_for(session_factory, tenant_context) as session:
                connection = get_connection(session, tenant_context, connection_id)
                if connection.status == "revoked":
                    raise ConnectionStateError("cannot list models for a revoked connection")
                base_url = connection.base_url
                secret_ref = connection.secret_ref
        except (ConnectionNotFoundError, TenantContextError) as exc:
            raise HTTPException(status_code=404, detail="not_found") from exc
        except ConnectionStateError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

        result = discover_models(
            base_url=base_url,
            secret_ref=secret_ref,
            secret_config=secret_manager_config_factory(),
            policy=validation_policy_factory(),
        )
        return JSONResponse(_serialize_discovery(result), status_code=200)

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
