"""Live `/api/v1/organizations/{organization_id}/projects/{project_id}/
model-imports` routes (FTF-H010-P1, Section 6.17/58A/68.1).

    POST .../model-imports        validate the shape, enqueue a job (201)
    GET  .../model-imports         list the project's own jobs
    GET  .../model-imports/{id}    read one job

Disclosed scope (Law 11 -- no placeholder route): this slice implements
enqueue and read only. Nothing here runs a real provider/endpoint probe,
transfers weights, or touches a GPU budget -- FTF-H010's own real
endpoint fine-tune, weights, GPU and continuation clauses stay
`BLOCKED_EXTERNAL`. A job is created at `state='queued'` and never moves:
no worker that would advance it to `validating`/`failed`/`succeeded`/
`blocked_external` exists anywhere in this repository yet (a later,
separately dependency-gated FTF-H010 task's own job, the same "wiring
work, not yet done" disclosure `FTF-0516-P1`'s own `record_quality_report`
gap already carries). "Job states never jump to succeeded without
validation" (this task's own DoD) holds trivially and verifiably: there is
no code path anywhere in this module, `model_import_crud.py` or the
migration's own grants that can write `state='succeeded'` at all.

Role gating follows `model_version_routes.py`'s own precedent exactly
(Section 45 names no more specific role for this class of work): reads
`MODEL_VERSION_READ_ROLES` (every role), writes (enqueue)
`MODEL_VERSION_WRITE_ROLES` (`{ml_engineer, organization_admin}`), reused
directly rather than re-derived.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.artifacts.artifact_crud import ArtifactNotFoundError
from app.auth.rbac_middleware import (
    AuthorizedContext,
    PrincipalResolver,
    RoleLookup,
    default_principal_resolver,
    default_role_lookup,
    require_role,
)
from app.auth.tenant_isolation_query_guards import TenantContextError
from app.db.tenant_context import TenantContext, tenant_scoped_session_for
from app.model_versions.model_import_crud import (
    ActorInfo,
    ModelImportJobNotFoundError,
    ModelVersionNotFoundError,
    ProjectNotFoundError,
    create_model_import_job,
    get_model_import_job,
    list_model_import_jobs,
)
from app.model_versions.model_import_schema import ModelImportJob, ModelImportValidationError
from app.model_versions.model_version_routes import (
    MODEL_VERSION_READ_ROLES,
    MODEL_VERSION_WRITE_ROLES,
)

__all__ = ["build_model_import_router"]

_COLLECTION_PATH = "/api/v1/organizations/{organization_id}/projects/{project_id}/model-imports"
_ITEM_PATH = _COLLECTION_PATH + "/{model_import_job_id}"

_POST_BODY_KEYS = frozenset(
    {
        "requested_origin",
        "parent_model_version_id",
        "trainable_artifact_id",
        "external_provider_model_ref",
    }
)


def _serialize(job: ModelImportJob) -> dict[str, object]:
    return {
        "id": str(job.id),
        "organization_id": str(job.organization_id),
        "project_id": str(job.project_id),
        "requested_origin": job.requested_origin,
        "parent_model_version_id": (
            None if job.parent_model_version_id is None else str(job.parent_model_version_id)
        ),
        "trainable_artifact_id": (
            None if job.trainable_artifact_id is None else str(job.trainable_artifact_id)
        ),
        "external_provider_model_ref": job.external_provider_model_ref,
        "state": job.state,
        "failure_code": job.failure_code,
        "created_by": job.created_by,
        "created_at": job.created_at.isoformat(),
        "updated_at": job.updated_at.isoformat(),
    }


async def _read_post_body(request: Request) -> dict[str, Any]:
    try:
        payload = await request.json()
    except Exception as exc:  # noqa: BLE001 - any unparsable body is the same outcome
        raise HTTPException(status_code=400, detail="request body must be valid JSON") from exc
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="request body must be a JSON object")
    unknown = sorted(set(payload) - _POST_BODY_KEYS)
    if unknown:
        raise HTTPException(status_code=400, detail=f"unknown field: {unknown[0]}")
    if "requested_origin" not in payload or not isinstance(payload["requested_origin"], str):
        raise HTTPException(
            status_code=400, detail="requested_origin is required and must be a string"
        )
    if (
        "parent_model_version_id" in payload
        and payload["parent_model_version_id"] is not None
        and not isinstance(payload["parent_model_version_id"], str)
    ):
        raise HTTPException(
            status_code=400, detail="parent_model_version_id must be a uuid or null"
        )
    if (
        "trainable_artifact_id" in payload
        and payload["trainable_artifact_id"] is not None
        and not isinstance(payload["trainable_artifact_id"], str)
    ):
        raise HTTPException(status_code=400, detail="trainable_artifact_id must be a uuid or null")
    if (
        "external_provider_model_ref" in payload
        and payload["external_provider_model_ref"] is not None
        and not isinstance(payload["external_provider_model_ref"], str)
    ):
        raise HTTPException(
            status_code=400, detail="external_provider_model_ref must be a string or null"
        )
    return payload


def _parse_uuid_field(payload: dict[str, Any], key: str) -> uuid.UUID | None:
    raw = payload.get(key)
    if raw is None:
        return None
    try:
        return uuid.UUID(raw)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"{key} must be a valid UUID") from exc


def build_model_import_router(
    session_factory: Callable[[], Session],
    *,
    principal_resolver: PrincipalResolver = default_principal_resolver,
    role_lookup: RoleLookup = default_role_lookup,
) -> APIRouter:
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
    async def enqueue(
        request: Request,
        organization_id: uuid.UUID,
        project_id: uuid.UUID,
        ctx: AuthorizedContext = write_dependency,
    ) -> JSONResponse:
        del organization_id
        payload = await _read_post_body(request)
        parent_model_version_id = _parse_uuid_field(payload, "parent_model_version_id")
        trainable_artifact_id = _parse_uuid_field(payload, "trainable_artifact_id")
        external_provider_model_ref = payload.get("external_provider_model_ref")
        tenant_context = TenantContext.from_authorized_context(ctx)
        actor = ActorInfo(
            actor_type="user", actor_id=ctx.principal.subject, request_id=uuid.uuid4().hex
        )
        try:
            with tenant_scoped_session_for(session_factory, tenant_context) as session:
                job = create_model_import_job(
                    session,
                    tenant_context,
                    project_id=project_id,
                    requested_origin=payload["requested_origin"],
                    parent_model_version_id=parent_model_version_id,
                    trainable_artifact_id=trainable_artifact_id,
                    external_provider_model_ref=external_provider_model_ref,
                    actor=actor,
                )
                body = _serialize(job)
        except (
            ProjectNotFoundError,
            ModelVersionNotFoundError,
            ArtifactNotFoundError,
            TenantContextError,
        ) as exc:
            raise HTTPException(status_code=404, detail="not_found") from exc
        except ModelImportValidationError as exc:
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
            jobs = list_model_import_jobs(session, tenant_context, project_id)
            body = [_serialize(j) for j in jobs]
        return JSONResponse(body)

    @router.get(_ITEM_PATH)
    async def get_one(
        organization_id: uuid.UUID,
        project_id: uuid.UUID,
        model_import_job_id: uuid.UUID,
        ctx: AuthorizedContext = read_dependency,
    ) -> JSONResponse:
        del organization_id
        tenant_context = TenantContext.from_authorized_context(ctx)
        try:
            with tenant_scoped_session_for(session_factory, tenant_context) as session:
                job = get_model_import_job(session, tenant_context, project_id, model_import_job_id)
                body = _serialize(job)
        except (ModelImportJobNotFoundError, TenantContextError) as exc:
            raise HTTPException(status_code=404, detail="not_found") from exc
        return JSONResponse(body)

    return router
