"""Live read routes for dataset versions (FTF-0501-P1, Section 6.10/7A.10/9/
39/69.1):

    GET .../projects/{project_id}/datasets                 list (limit, after)
    GET .../projects/{project_id}/datasets/{dataset_id}    one dataset version

Role gating is `DATASET_SOURCE_ROLES` (ml_engineer, workspace_admin,
organization_admin). A `held_out_evaluation` dataset is hidden from
`ml_engineer` (Section 9/69.1): it is absent from the list and a direct read
is the same 404 as a missing id. Errors are `{"detail": "<stable_code>"}`: 400
`invalid_limit` | `invalid_after`, 401, 403, 404 `not_found` (identical for a
missing, foreign or other-project id).

Responses never include the object key, bucket or any storage URI: those
stay inside the upload session, which has no read route. This module adds no
upload or finalize route; FTF-0501 owns those and the contracts:05 lock.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable

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
from app.datasets.dataset_source_connection_routes import DATASET_SOURCE_ROLES
from app.datasets.dataset_upload_store import (
    MAX_PAGE_SIZE,
    Dataset,
    DatasetNotFoundError,
    ProjectNotFoundError,
    get_dataset,
    list_datasets,
)
from app.db.tenant_context import TenantContext, tenant_scoped_session_for

__all__ = ["build_dataset_router"]

_COLLECTION_PATH = "/api/v1/organizations/{organization_id}/projects/{project_id}/datasets"
_ITEM_PATH = _COLLECTION_PATH + "/{dataset_id}"
_DEFAULT_LIMIT = 50

# Section 9/69.1: only ml_engineer, among the roles allowed on this surface,
# may not read held-out evaluation data.
_HELD_OUT_BLIND_ROLES: frozenset[Role] = frozenset({Role.ML_ENGINEER})


def _serialize(dataset: Dataset) -> dict[str, object]:
    return {
        "id": str(dataset.id),
        "organization_id": str(dataset.organization_id),
        "project_id": str(dataset.project_id),
        "name": dataset.name,
        "version": dataset.version,
        "purpose": dataset.purpose,
        "upload_session_id": str(dataset.upload_session_id),
        "verified_sha256": dataset.verified_sha256,
        "verified_size_bytes": dataset.verified_size_bytes,
        "created_by": dataset.created_by,
        "created_at": dataset.created_at.isoformat(),
    }


def _may_see_held_out(ctx: AuthorizedContext) -> bool:
    return ctx.role not in _HELD_OUT_BLIND_ROLES


def _parse_limit(request: Request) -> int:
    raw = request.query_params.get("limit")
    if raw is None:
        return _DEFAULT_LIMIT
    try:
        limit = int(raw)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="invalid_limit") from exc
    if not 1 <= limit <= MAX_PAGE_SIZE:
        raise HTTPException(status_code=400, detail="invalid_limit")
    return limit


def _parse_after(request: Request) -> uuid.UUID | None:
    raw = request.query_params.get("after")
    if raw is None:
        return None
    try:
        return uuid.UUID(raw)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="invalid_after") from exc


def build_dataset_router(
    session_factory: Callable[[], Session],
    *,
    principal_resolver: PrincipalResolver = default_principal_resolver,
    role_lookup: RoleLookup = default_role_lookup,
) -> APIRouter:
    router = APIRouter()
    dependency = Depends(
        require_role(
            DATASET_SOURCE_ROLES, principal_resolver=principal_resolver, role_lookup=role_lookup
        )
    )

    @router.get(_COLLECTION_PATH)
    async def list_all(
        request: Request,
        organization_id: uuid.UUID,
        project_id: uuid.UUID,
        ctx: AuthorizedContext = dependency,
    ) -> JSONResponse:
        del organization_id
        limit = _parse_limit(request)
        after = _parse_after(request)
        tenant_context = TenantContext.from_authorized_context(ctx)
        try:
            with tenant_scoped_session_for(session_factory, tenant_context) as session:
                rows = list_datasets(
                    session,
                    tenant_context,
                    project_id=project_id,
                    include_held_out=_may_see_held_out(ctx),
                    limit=limit,
                    after=after,
                )
                items = [_serialize(row) for row in rows]
        except (ProjectNotFoundError, TenantContextError) as exc:
            raise HTTPException(status_code=404, detail="not_found") from exc
        next_after = items[-1]["id"] if len(items) == limit else None
        return JSONResponse({"items": items, "next_after": next_after}, status_code=200)

    @router.get(_ITEM_PATH)
    async def get_one(
        organization_id: uuid.UUID,
        project_id: uuid.UUID,
        dataset_id: uuid.UUID,
        ctx: AuthorizedContext = dependency,
    ) -> JSONResponse:
        del organization_id
        tenant_context = TenantContext.from_authorized_context(ctx)
        try:
            with tenant_scoped_session_for(session_factory, tenant_context) as session:
                row = get_dataset(
                    session,
                    tenant_context,
                    project_id=project_id,
                    dataset_id=dataset_id,
                    include_held_out=_may_see_held_out(ctx),
                )
                body = _serialize(row)
        except (DatasetNotFoundError, TenantContextError) as exc:
            raise HTTPException(status_code=404, detail="not_found") from exc
        return JSONResponse(body, status_code=200)

    return router
