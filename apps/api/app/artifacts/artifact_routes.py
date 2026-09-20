"""Live `/api/v1/organizations/{organization_id}/artifacts` routes
(FTF-0301, Section 6.7/13/34/45/65/68/77/81) -- the third live-wired
product-runtime path in this repository, after FTF-0101's `/auth/*` and
FTF-0201's `/connections/litellm`.

Scope (disclosed explicitly, not hidden -- see this task's own
docs/certifications/queue.md submission entry): this router implements
create/get/list/edit/accept-license/revoke-license/mark-ready/archive
(`revoke-license` added by FTF-0305, Section 6.7/13/34/68/77/81 --
`app.artifacts.license_acceptance_workflow`). It deliberately
does not implement any real resolution against Hugging Face/S3/local
filesystem/a private registry, nor computing/verifying `weights_digest`
from real bytes -- those are FTF-0302 ("Hugging Face artifact
resolver")/FTF-0303 ("Private S3 artifact resolver")/FTF-0304 ("Local
artifact resolver")/FTF-0306 ("Base model revision/digest resolver")'s
own, later, separately dependency-gated tasks (see docs/TASK_INDEX.md).
`weights_digest` is exposed only as a field this task's own PATCH accepts
and shape-validates, for a later resolver task to write through.

Path shape (refined from the task packet's own abbreviated
`api_contract: "/api/v1/models/{id}/training-artifact and /capabilities"`
to a real collection/item resource shape, matching FTF-0201's own
precedent of refining an abbreviated packet path to the concrete,
already-proven `/organizations/{organization_id}/<resource>` shape used
by every other tenant-scoped route in this repository): `TrainableArtifact`
is its own top-level tenant-scoped resource (Section 6.7), not a
sub-resource nested under a specific `ModelCatalogEntry` id -- the same
resource can, per Section 68.4, back more than one model-to-artifact
mapping (a later, separate task's own concern, FTF-0308).

Role gating: see `artifact_crud.py`'s own module docstring for the full
Section 45 reasoning -- reads are open to every role Section 45 names
(matching its own generic "viewer: read only" baseline), writes
(create/edit/accept-license/revoke-license/mark-ready/archive) are
restricted to `{ml_engineer, organization_admin}` (Section 45's
"ml_engineer: datasets/training, no production approval" is the only
sentence naming a role for this class of work).
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.artifacts.artifact_crud import (
    ActorInfo,
    ArtifactNotFoundError,
    ArtifactStateError,
    accept_license,
    archive_artifact,
    create_artifact,
    get_artifact,
    list_artifacts,
    mark_ready,
    revoke_license,
    update_artifact,
)
from app.artifacts.artifact_schema import TrainableArtifact
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

__all__ = ["ARTIFACT_READ_ROLES", "ARTIFACT_WRITE_ROLES", "build_trainable_artifact_router"]

# Section 45's own literal role table -- every role it names, applied to
# this resource's reads (no dedicated restrictive sentence exists for
# artifacts, unlike connections' single-role "organization_admin" line).
ARTIFACT_READ_ROLES: frozenset[Role] = frozenset(
    {
        Role.ORGANIZATION_ADMIN,
        Role.WORKSPACE_ADMIN,
        Role.ML_ENGINEER,
        Role.EVALUATOR,
        Role.APPROVER,
        Role.VIEWER,
    }
)
# Section 45: "ml_engineer: datasets/training, no production approval" is
# the only sentence naming a role for this class of work.
ARTIFACT_WRITE_ROLES: frozenset[Role] = frozenset({Role.ML_ENGINEER, Role.ORGANIZATION_ADMIN})

_COLLECTION_PATH = "/api/v1/organizations/{organization_id}/artifacts"
_ITEM_PATH = _COLLECTION_PATH + "/{artifact_id}"
_ACCEPT_LICENSE_PATH = _ITEM_PATH + "/accept-license"
_REVOKE_LICENSE_PATH = _ITEM_PATH + "/revoke-license"
_MARK_READY_PATH = _ITEM_PATH + "/mark-ready"


def _serialize(artifact: TrainableArtifact) -> dict[str, object]:
    return {
        "id": str(artifact.id),
        "organization_id": str(artifact.organization_id),
        "display_name": artifact.display_name,
        "source_type": artifact.source_type,
        "source_uri": artifact.source_uri,
        "model_family": artifact.model_family,
        "base_model_revision": artifact.base_model_revision,
        "weights_digest": artifact.weights_digest,
        "license_id": artifact.license_id,
        "license_accepted_by": artifact.license_accepted_by,
        "license_accepted_at": (
            artifact.license_accepted_at.isoformat() if artifact.license_accepted_at else None
        ),
        "trust_remote_code_allowed": artifact.trust_remote_code_allowed,
        "required_python_packages": list(artifact.required_python_packages_json or []),
        "minimum_gpu_profile": dict(artifact.minimum_gpu_profile_json or {}),
        "status": artifact.status,
        "created_at": artifact.created_at.isoformat(),
        "updated_at": artifact.updated_at.isoformat(),
        "archived_at": artifact.archived_at.isoformat() if artifact.archived_at else None,
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
    if not isinstance(value, str):
        raise HTTPException(status_code=400, detail=f"{key} must be a string")
    return value


def _optional_bool(payload: dict[str, Any], key: str) -> bool | None:
    if key not in payload:
        return None
    value = payload[key]
    if not isinstance(value, bool):
        raise HTTPException(status_code=400, detail=f"{key} must be a boolean")
    return value


def _optional_list(payload: dict[str, Any], key: str) -> list[str] | None:
    if key not in payload:
        return None
    value = payload[key]
    if not isinstance(value, list):
        raise HTTPException(status_code=400, detail=f"{key} must be a list")
    return value


def _optional_dict(payload: dict[str, Any], key: str) -> dict[str, object] | None:
    if key not in payload:
        return None
    value = payload[key]
    if not isinstance(value, dict):
        raise HTTPException(status_code=400, detail=f"{key} must be an object")
    return value


def build_trainable_artifact_router(
    session_factory: Callable[[], Session],
    *,
    principal_resolver: PrincipalResolver = default_principal_resolver,
    role_lookup: RoleLookup = default_role_lookup,
) -> APIRouter:
    """Real FastAPI router for FTF-0301's CRUD + ready-transition surface.

    `session_factory`/`principal_resolver`/`role_lookup` follow the exact
    same injection convention FTF-0201's own `build_litellm_connection_router`
    established -- fail-closed defaults until a real session-reader/
    DB-backed Membership lookup is wired in (same disclosed gap FTF-0103/
    FTF-0106/FTF-0201 already carry).
    """
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
                artifact = create_artifact(
                    session,
                    tenant_context,
                    display_name=_require_str(payload, "display_name"),
                    source_type=_require_str(payload, "source_type"),
                    source_uri=_require_str(payload, "source_uri"),
                    model_family=_require_str(payload, "model_family"),
                    base_model_revision=_require_str(payload, "base_model_revision"),
                    license_id=_require_str(payload, "license_id"),
                    trust_remote_code_allowed=_optional_bool(payload, "trust_remote_code_allowed")
                    or False,
                    required_python_packages=_optional_list(payload, "required_python_packages"),
                    minimum_gpu_profile=_optional_dict(payload, "minimum_gpu_profile"),
                    actor=_actor_from(ctx, request),
                )
                body = _serialize(artifact)
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
            artifacts = list_artifacts(session, tenant_context)
            body = [_serialize(a) for a in artifacts]
        return JSONResponse(body, status_code=200)

    @router.get(_ITEM_PATH)
    async def get_one(
        organization_id: uuid.UUID,
        artifact_id: uuid.UUID,
        ctx: AuthorizedContext = read_dependency,
    ) -> JSONResponse:
        del organization_id
        tenant_context = TenantContext.from_authorized_context(ctx)
        try:
            with tenant_scoped_session_for(session_factory, tenant_context) as session:
                artifact = get_artifact(session, tenant_context, artifact_id)
                body = _serialize(artifact)
        except (ArtifactNotFoundError, TenantContextError) as exc:
            raise HTTPException(status_code=404, detail="not_found") from exc
        return JSONResponse(body, status_code=200)

    @router.patch(_ITEM_PATH)
    async def update(
        request: Request,
        organization_id: uuid.UUID,
        artifact_id: uuid.UUID,
        ctx: AuthorizedContext = write_dependency,
    ) -> JSONResponse:
        del organization_id
        payload = await _read_json_body(request)
        tenant_context = TenantContext.from_authorized_context(ctx)
        try:
            with tenant_scoped_session_for(session_factory, tenant_context) as session:
                artifact = update_artifact(
                    session,
                    tenant_context,
                    artifact_id,
                    display_name=_optional_str(payload, "display_name"),
                    source_uri=_optional_str(payload, "source_uri"),
                    base_model_revision=_optional_str(payload, "base_model_revision"),
                    model_family=_optional_str(payload, "model_family"),
                    weights_digest=_optional_str(payload, "weights_digest"),
                    license_id=_optional_str(payload, "license_id"),
                    trust_remote_code_allowed=_optional_bool(payload, "trust_remote_code_allowed"),
                    required_python_packages=_optional_list(payload, "required_python_packages"),
                    minimum_gpu_profile=_optional_dict(payload, "minimum_gpu_profile"),
                    actor=_actor_from(ctx, request),
                )
                body = _serialize(artifact)
        except (ArtifactNotFoundError, TenantContextError) as exc:
            raise HTTPException(status_code=404, detail="not_found") from exc
        except ArtifactStateError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return JSONResponse(body, status_code=200)

    @router.post(_ACCEPT_LICENSE_PATH)
    async def accept(
        request: Request,
        organization_id: uuid.UUID,
        artifact_id: uuid.UUID,
        ctx: AuthorizedContext = write_dependency,
    ) -> JSONResponse:
        del organization_id
        tenant_context = TenantContext.from_authorized_context(ctx)
        try:
            with tenant_scoped_session_for(session_factory, tenant_context) as session:
                artifact = accept_license(
                    session, tenant_context, artifact_id, actor=_actor_from(ctx, request)
                )
                body = _serialize(artifact)
        except (ArtifactNotFoundError, TenantContextError) as exc:
            raise HTTPException(status_code=404, detail="not_found") from exc
        except ArtifactStateError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return JSONResponse(body, status_code=200)

    @router.post(_REVOKE_LICENSE_PATH)
    async def revoke(
        request: Request,
        organization_id: uuid.UUID,
        artifact_id: uuid.UUID,
        ctx: AuthorizedContext = write_dependency,
    ) -> JSONResponse:
        del organization_id
        tenant_context = TenantContext.from_authorized_context(ctx)
        try:
            with tenant_scoped_session_for(session_factory, tenant_context) as session:
                artifact = revoke_license(
                    session, tenant_context, artifact_id, actor=_actor_from(ctx, request)
                )
                body = _serialize(artifact)
        except (ArtifactNotFoundError, TenantContextError) as exc:
            raise HTTPException(status_code=404, detail="not_found") from exc
        except ArtifactStateError as exc:
            # FTF-0305's own negative_acceptance: "Unaccepted/revoked
            # license blocks training/serving" -- an archived artifact is
            # a closed record, so revoking its license is refused (409).
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return JSONResponse(body, status_code=200)

    @router.post(_MARK_READY_PATH)
    async def ready(
        request: Request,
        organization_id: uuid.UUID,
        artifact_id: uuid.UUID,
        ctx: AuthorizedContext = write_dependency,
    ) -> JSONResponse:
        del organization_id
        tenant_context = TenantContext.from_authorized_context(ctx)
        try:
            with tenant_scoped_session_for(session_factory, tenant_context) as session:
                artifact = mark_ready(
                    session, tenant_context, artifact_id, actor=_actor_from(ctx, request)
                )
                body = _serialize(artifact)
        except (ArtifactNotFoundError, TenantContextError) as exc:
            raise HTTPException(status_code=404, detail="not_found") from exc
        except ArtifactStateError as exc:
            # This task's own negative_acceptance: "Unsafe URI, unknown owner
            # or mutable unresolved revision cannot enter training as ready."
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return JSONResponse(body, status_code=200)

    @router.delete(_ITEM_PATH)
    async def archive(
        request: Request,
        organization_id: uuid.UUID,
        artifact_id: uuid.UUID,
        ctx: AuthorizedContext = write_dependency,
    ) -> JSONResponse:
        del organization_id
        tenant_context = TenantContext.from_authorized_context(ctx)
        try:
            with tenant_scoped_session_for(session_factory, tenant_context) as session:
                artifact = archive_artifact(
                    session, tenant_context, artifact_id, actor=_actor_from(ctx, request)
                )
                body = _serialize(artifact)
        except (ArtifactNotFoundError, TenantContextError) as exc:
            raise HTTPException(status_code=404, detail="not_found") from exc
        return JSONResponse(body, status_code=200)

    return router
