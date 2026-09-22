"""Live `/api/v1/organizations/{organization_id}/projects/{project_id}/model-versions`
routes (FTF-1001, Section 34/35/36/45/65-66/77/81) -- the seventh
live-wired product-runtime path in this repository, after FTF-0101's
`/auth/*`, FTF-0201's `/connections/litellm`, FTF-0301's `/artifacts`,
FTF-0401's `/projects`, FTF-0404's `/tools` and FTF-0407's
`/requirements`.

Path shape: nested under a specific project, matching FTF-0404's own
`/tools` precedent (a `ModelVersion` genuinely belongs to one project for
its entire lifetime, Section 6.8's own project-owns-its-models framing),
not FTF-0301's own top-level `/artifacts` shape (a `TrainableArtifact` can
back more than one project's own models, Section 68.4).

Scope (disclosed explicitly, not hidden -- see this task's own
docs/certifications/queue.md submission entry): this router implements
create/get/list only. There is no PATCH/DELETE -- Section 35's own "every
model version is immutable" rule means there is nothing to edit, and
nothing in this task's own acceptance/negative_acceptance calls for
removing one. It does not implement `/model-imports` or `/deployments` as
separate paths (the packet's own abbreviated `api_contract` listing) --
"import" *is* creation here (there is no separate asynchronous import job
to track, since resolving the referenced artifact/endpoint is already a
prerequisite this router enforces synchronously at create time), and
`/deployments` plus "owned health adapters and serving client" are
FTF-1003/FTF-1005/FTF-1008's own, later, separately dependency-gated
surface -- declaring a deployment/health endpoint with no real serving
backend behind it here would be exactly the "placeholder endpoint" Law 11
forbids.

Role gating: see `model_version_crud.py`'s own module docstring for the
full Section 45 reasoning -- reads are open to every role Section 45
names, writes (create) are restricted to `{ml_engineer,
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
from app.model_versions.model_version_crud import (
    ActorInfo,
    ArtifactNotFoundError,
    ModelVersionArtifactStateError,
    ModelVersionNotFoundError,
    ProjectNotFoundError,
    create_model_version,
    get_model_version,
    list_model_versions,
)
from app.model_versions.model_version_schema import ModelVersion, ModelVersionValidationError

__all__ = [
    "MODEL_VERSION_READ_ROLES",
    "MODEL_VERSION_WRITE_ROLES",
    "build_model_version_router",
]

# Section 45: "ml_engineer: datasets/training, no production approval" is
# the only sentence naming a role for this class of work -- the same
# precedent FTF-0301's own ARTIFACT_WRITE_ROLES applied.
MODEL_VERSION_WRITE_ROLES: frozenset[Role] = frozenset({Role.ML_ENGINEER, Role.ORGANIZATION_ADMIN})
MODEL_VERSION_READ_ROLES: frozenset[Role] = frozenset(
    {
        Role.ORGANIZATION_ADMIN,
        Role.WORKSPACE_ADMIN,
        Role.ML_ENGINEER,
        Role.EVALUATOR,
        Role.APPROVER,
        Role.VIEWER,
    }
)

_COLLECTION_PATH = "/api/v1/organizations/{organization_id}/projects/{project_id}/model-versions"
_ITEM_PATH = _COLLECTION_PATH + "/{model_version_id}"


def _serialize(model_version: ModelVersion) -> dict[str, object]:
    return {
        "id": str(model_version.id),
        "organization_id": str(model_version.organization_id),
        "project_id": str(model_version.project_id),
        "version_number": model_version.version_number,
        "import_kind": model_version.import_kind,
        "trainable_artifact_id": (
            str(model_version.trainable_artifact_id)
            if model_version.trainable_artifact_id
            else None
        ),
        "weights_digest": model_version.weights_digest,
        "endpoint_reference": model_version.endpoint_reference,
        "base_model_revision": model_version.base_model_revision,
        "created_by": model_version.created_by,
        "created_at": model_version.created_at.isoformat(),
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


def _optional_uuid(payload: dict[str, Any], key: str) -> uuid.UUID | None:
    value = _optional_str(payload, key)
    if value is None:
        return None
    try:
        return uuid.UUID(value)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"{key} must be a valid UUID") from exc


def build_model_version_router(
    session_factory: Callable[[], Session],
    *,
    principal_resolver: PrincipalResolver = default_principal_resolver,
    role_lookup: RoleLookup = default_role_lookup,
) -> APIRouter:
    """Real FastAPI router for FTF-1001's create/get/list surface.

    `session_factory`/`principal_resolver`/`role_lookup` follow the exact
    same injection convention every prior router in this chain
    established -- fail-closed defaults until a real session-reader/
    DB-backed Membership lookup is wired in (same disclosed gap FTF-0103/
    FTF-0106/FTF-0201/FTF-0301/FTF-0401/FTF-0404 already carry).
    """
    router = APIRouter()
    read_dependency = Depends(
        require_role(
            MODEL_VERSION_READ_ROLES, principal_resolver=principal_resolver, role_lookup=role_lookup
        )
    )
    write_dependency = Depends(
        require_role(
            MODEL_VERSION_WRITE_ROLES,
            principal_resolver=principal_resolver,
            role_lookup=role_lookup,
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
                model_version = create_model_version(
                    session,
                    tenant_context,
                    project_id=project_id,
                    import_kind=_require_str(payload, "import_kind"),
                    endpoint_reference=_optional_str(payload, "endpoint_reference"),
                    trainable_artifact_id=_optional_uuid(payload, "trainable_artifact_id"),
                    actor=_actor_from(ctx, request),
                )
                body = _serialize(model_version)
        except (ProjectNotFoundError, ArtifactNotFoundError, TenantContextError) as exc:
            raise HTTPException(status_code=404, detail="not_found") from exc
        except ModelVersionArtifactStateError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except (ValueError, ModelVersionValidationError) as exc:
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
            model_versions = list_model_versions(session, tenant_context, project_id)
            body = [_serialize(m) for m in model_versions]
        return JSONResponse(body, status_code=200)

    @router.get(_ITEM_PATH)
    async def get_one(
        organization_id: uuid.UUID,
        project_id: uuid.UUID,
        model_version_id: uuid.UUID,
        ctx: AuthorizedContext = read_dependency,
    ) -> JSONResponse:
        del organization_id
        tenant_context = TenantContext.from_authorized_context(ctx)
        try:
            with tenant_scoped_session_for(session_factory, tenant_context) as session:
                model_version = get_model_version(
                    session, tenant_context, project_id, model_version_id
                )
                body = _serialize(model_version)
        except (ModelVersionNotFoundError, TenantContextError) as exc:
            raise HTTPException(status_code=404, detail="not_found") from exc
        return JSONResponse(body, status_code=200)

    return router
