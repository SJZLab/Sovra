"""Live `.../connections/litellm/{connection_id}/models/{model_name}/
training-artifact` routes (FTF-0308-P1, Section 2.2/3.3/6.6/6.24/39/65/77/81).

    GET   .../training-artifact          read the current explicit mapping
    POST  .../training-artifact          map / unmap (a fresh discovery first)
    GET   .../training-artifact/changes  the append-only change history

Role gating follows Section 39/45 for artifacts: reads `ARTIFACT_READ_ROLES`,
the POST `ARTIFACT_WRITE_ROLES`. Errors are `{"detail": "<stable_code>"}`:
400, 401, 403, 404 `not_found` (identical for a missing or foreign
connection/artifact), 409 `connection_revoked` | `model_not_accessible` |
`artifact_not_ready` | `catalog_version_conflict`, 503 `gateway_unavailable`.

The POST runs `discover_models` (FTF-0204) on every call, before any write:
an alias the gateway does not currently list is `model_not_accessible`, and
any discovery state other than `discovered` is `gateway_unavailable` with no
write (Law 17: an unknown or failed provider state is never read as
success). The alias text never selects an artifact; the caller names the
artifact id. This module does not call the LiteLLM management API and does
not touch `model_catalog_routes.py` or the training capability classifier.
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

from packages.litellm_client.connection_validation import (  # noqa: E402
    default_connection_validation_policy,
)
from packages.litellm_client.secret_manager_integration import OpenBaoConfig  # noqa: E402
from packages.litellm_client.v1_models_discovery import (  # noqa: E402
    DiscoveryState,
    ModelDiscoveryResult,
    discover_models,
)
from packages.security.url_policy import ConnectionPolicy  # noqa: E402

from app.artifacts.artifact_crud import ArtifactNotFoundError, get_artifact  # noqa: E402
from app.artifacts.artifact_routes import (  # noqa: E402
    ARTIFACT_READ_ROLES,
    ARTIFACT_WRITE_ROLES,
)
from app.artifacts.artifact_routes import _serialize as _serialize_artifact  # noqa: E402
from app.auth.rbac_middleware import (  # noqa: E402
    AuthorizedContext,
    PrincipalResolver,
    RoleLookup,
    default_principal_resolver,
    default_role_lookup,
    require_role,
)
from app.auth.tenant_isolation_query_guards import TenantContextError  # noqa: E402
from app.db.tenant_context import TenantContext, tenant_scoped_session_for  # noqa: E402
from app.litellm.connection_crud import ConnectionNotFoundError, get_connection  # noqa: E402
from app.litellm.connection_routes import _default_secret_manager_config  # noqa: E402
from app.litellm.model_catalog_mapping_store import (  # noqa: E402
    PUBLIC_MODEL_NAME_MAX_LENGTH,
    ArtifactNotReadyError,
    CatalogVersionConflictError,
    MappingActor,
    ModelCatalogEntryRecord,
    ModelCatalogMappingChangeRecord,
    get_entry,
    list_mapping_changes,
    set_training_artifact_mapping,
)

__all__ = ["build_model_catalog_mapping_router"]

_ITEM_PATH = "/api/v1/organizations/{organization_id}/connections/litellm/{connection_id}"
_MAPPING_PATH = _ITEM_PATH + "/models/{model_name:path}/training-artifact"
_CHANGES_PATH = _MAPPING_PATH + "/changes"

_POST_BODY_KEYS = frozenset({"trainable_artifact_id", "expected_catalog_version"})
_DEFAULT_CHANGES_LIMIT = 50


def _not_found() -> HTTPException:
    return HTTPException(status_code=404, detail="not_found")


def _check_model_name(model_name: str) -> None:
    if not model_name or len(model_name) > PUBLIC_MODEL_NAME_MAX_LENGTH:
        raise HTTPException(status_code=400, detail="invalid_model_name")


def _iso(value: Any) -> str | None:
    return None if value is None else str(value.isoformat())


def _mapping_body(
    *,
    connection_id: uuid.UUID,
    model_name: str,
    entry: ModelCatalogEntryRecord | None,
    artifact: dict[str, object] | None,
    last_changed_at: Any,
) -> dict[str, object]:
    mapped = entry is not None and entry.trainable_artifact_id is not None
    return {
        "mapping_state": "mapped" if mapped else "unmapped",
        "model_catalog_entry_id": None if entry is None else str(entry.id),
        "litellm_connection_id": str(connection_id),
        "public_model_name": model_name,
        "trainable_artifact_id": (
            None
            if entry is None or entry.trainable_artifact_id is None
            else str(entry.trainable_artifact_id)
        ),
        "catalog_version": 0 if entry is None else entry.catalog_version,
        "last_verified_accessible_at": (
            None if entry is None else _iso(entry.last_verified_accessible_at)
        ),
        "last_changed_at": _iso(last_changed_at),
        "artifact": artifact,
    }


def _serialize_change(change: ModelCatalogMappingChangeRecord) -> dict[str, object]:
    return {
        "id": str(change.id),
        "model_catalog_entry_id": str(change.model_catalog_entry_id),
        "previous_trainable_artifact_id": (
            None
            if change.previous_trainable_artifact_id is None
            else str(change.previous_trainable_artifact_id)
        ),
        "new_trainable_artifact_id": (
            None
            if change.new_trainable_artifact_id is None
            else str(change.new_trainable_artifact_id)
        ),
        "previous_catalog_version": change.previous_catalog_version,
        "new_catalog_version": change.new_catalog_version,
        "actor": change.actor,
        "request_id": change.request_id,
        "created_at": _iso(change.created_at),
    }


async def _read_post_body(request: Request) -> tuple[uuid.UUID | None, int | None]:
    try:
        payload = await request.json()
    except Exception as exc:  # noqa: BLE001 - any unparsable body is the same outcome
        raise HTTPException(status_code=400, detail="request body must be valid JSON") from exc
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="request body must be a JSON object")
    unknown = sorted(set(payload) - _POST_BODY_KEYS)
    if unknown:
        raise HTTPException(status_code=400, detail=f"unknown field: {unknown[0]}")
    if "trainable_artifact_id" not in payload:
        raise HTTPException(status_code=400, detail="trainable_artifact_id is required")
    raw_artifact = payload["trainable_artifact_id"]
    artifact_id: uuid.UUID | None = None
    if raw_artifact is not None:
        if not isinstance(raw_artifact, str):
            raise HTTPException(status_code=400, detail="trainable_artifact_id must be a uuid")
        try:
            artifact_id = uuid.UUID(raw_artifact)
        except ValueError as exc:
            raise HTTPException(
                status_code=400, detail="trainable_artifact_id must be a uuid"
            ) from exc
    expected = payload.get("expected_catalog_version")
    if expected is not None and (
        isinstance(expected, bool) or not isinstance(expected, int) or expected < 0
    ):
        raise HTTPException(
            status_code=400, detail="expected_catalog_version must be a non-negative integer"
        )
    return artifact_id, expected


def build_model_catalog_mapping_router(
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
    discovery_function: Callable[..., ModelDiscoveryResult] = discover_models,
) -> APIRouter:
    """`discovery_function` defaults to the real FTF-0204 `discover_models`;
    it is a seam only so a test can supply a `ModelDiscoveryResult` without a
    live gateway. Injection conventions otherwise match
    `build_model_catalog_router`."""
    router = APIRouter()
    read_dependency = Depends(
        require_role(
            ARTIFACT_READ_ROLES, principal_resolver=principal_resolver, role_lookup=role_lookup
        )
    )
    write_dependency = Depends(
        require_role(
            ARTIFACT_WRITE_ROLES, principal_resolver=principal_resolver, role_lookup=role_lookup
        )
    )

    @router.get(_MAPPING_PATH)
    async def read_mapping(
        organization_id: uuid.UUID,
        connection_id: uuid.UUID,
        model_name: str,
        ctx: AuthorizedContext = read_dependency,
    ) -> JSONResponse:
        del organization_id
        _check_model_name(model_name)
        tenant_context = TenantContext.from_authorized_context(ctx)
        try:
            with tenant_scoped_session_for(session_factory, tenant_context) as session:
                get_connection(session, tenant_context, connection_id)
                entry = get_entry(
                    session,
                    tenant_context,
                    connection_id=connection_id,
                    public_model_name=model_name,
                )
                artifact_body: dict[str, object] | None = None
                last_changed_at = None
                if entry is not None:
                    changes = list_mapping_changes(session, tenant_context, entry.id, limit=1)
                    last_changed_at = changes[0].created_at if changes else None
                    if entry.trainable_artifact_id is not None:
                        artifact_body = _serialize_artifact(
                            get_artifact(session, tenant_context, entry.trainable_artifact_id)
                        )
                body = _mapping_body(
                    connection_id=connection_id,
                    model_name=model_name,
                    entry=entry,
                    artifact=artifact_body,
                    last_changed_at=last_changed_at,
                )
        except (ConnectionNotFoundError, TenantContextError) as exc:
            raise _not_found() from exc
        return JSONResponse(body)

    @router.post(_MAPPING_PATH)
    async def write_mapping(
        request: Request,
        organization_id: uuid.UUID,
        connection_id: uuid.UUID,
        model_name: str,
        ctx: AuthorizedContext = write_dependency,
    ) -> JSONResponse:
        del organization_id
        _check_model_name(model_name)
        artifact_id, expected_version = await _read_post_body(request)
        tenant_context = TenantContext.from_authorized_context(ctx)

        try:
            with tenant_scoped_session_for(session_factory, tenant_context) as session:
                connection = get_connection(session, tenant_context, connection_id)
                if connection.status == "revoked":
                    raise HTTPException(status_code=409, detail="connection_revoked")
                base_url = connection.base_url
                secret_ref = connection.secret_ref
        except (ConnectionNotFoundError, TenantContextError) as exc:
            raise _not_found() from exc

        # A fresh discovery on every call, before any write (Law 17).
        discovery = discovery_function(
            base_url=base_url,
            secret_ref=secret_ref,
            secret_config=secret_manager_config_factory(),
            policy=validation_policy_factory(),
        )
        if discovery.state is not DiscoveryState.DISCOVERED:
            raise HTTPException(status_code=503, detail="gateway_unavailable")
        if model_name not in {model.id for model in discovery.models}:
            raise HTTPException(status_code=409, detail="model_not_accessible")

        actor = MappingActor(
            actor_type="user", actor_id=ctx.principal.subject, request_id=uuid.uuid4().hex
        )
        try:
            with tenant_scoped_session_for(session_factory, tenant_context) as session:
                # Re-read inside the write transaction: a revoke that landed
                # during discovery must not be mapped through.
                if get_connection(session, tenant_context, connection_id).status == "revoked":
                    raise HTTPException(status_code=409, detail="connection_revoked")
                outcome = set_training_artifact_mapping(
                    session,
                    tenant_context,
                    connection_id=connection_id,
                    public_model_name=model_name,
                    trainable_artifact_id=artifact_id,
                    expected_catalog_version=expected_version,
                    actor=actor,
                )
                artifact_body: dict[str, object] | None = None
                if outcome.entry is not None and outcome.entry.trainable_artifact_id is not None:
                    artifact_body = _serialize_artifact(
                        get_artifact(session, tenant_context, outcome.entry.trainable_artifact_id)
                    )
                changes = (
                    list_mapping_changes(session, tenant_context, outcome.entry.id, limit=1)
                    if outcome.entry is not None
                    else []
                )
                body = _mapping_body(
                    connection_id=connection_id,
                    model_name=model_name,
                    entry=outcome.entry,
                    artifact=artifact_body,
                    last_changed_at=changes[0].created_at if changes else None,
                )
                body["changed"] = outcome.changed
        except (ConnectionNotFoundError, ArtifactNotFoundError, TenantContextError) as exc:
            raise _not_found() from exc
        except ArtifactNotReadyError as exc:
            raise HTTPException(status_code=409, detail="artifact_not_ready") from exc
        except CatalogVersionConflictError as exc:
            raise HTTPException(status_code=409, detail="catalog_version_conflict") from exc
        return JSONResponse(body, status_code=200)

    @router.get(_CHANGES_PATH)
    async def read_changes(
        request: Request,
        organization_id: uuid.UUID,
        connection_id: uuid.UUID,
        model_name: str,
        ctx: AuthorizedContext = read_dependency,
    ) -> JSONResponse:
        del organization_id
        _check_model_name(model_name)
        limit = _DEFAULT_CHANGES_LIMIT
        raw_limit = request.query_params.get("limit")
        if raw_limit is not None:
            try:
                limit = int(raw_limit)
            except ValueError as exc:
                raise HTTPException(status_code=400, detail="limit must be an integer") from exc
            if not 1 <= limit <= 100:
                raise HTTPException(status_code=400, detail="limit must be between 1 and 100")
        tenant_context = TenantContext.from_authorized_context(ctx)
        try:
            with tenant_scoped_session_for(session_factory, tenant_context) as session:
                get_connection(session, tenant_context, connection_id)
                entry = get_entry(
                    session,
                    tenant_context,
                    connection_id=connection_id,
                    public_model_name=model_name,
                )
                records = (
                    []
                    if entry is None
                    else list_mapping_changes(session, tenant_context, entry.id, limit=limit)
                )
                changes = [_serialize_change(record) for record in records]
        except (ConnectionNotFoundError, TenantContextError) as exc:
            raise _not_found() from exc
        return JSONResponse({"changes": changes})

    return router
