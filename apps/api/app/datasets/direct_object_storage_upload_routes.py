"""Live upload routes for direct object-storage upload (FTF-0501, Section
8.1 steps 1-4, 65.2, 67.3, 67.5):

    POST .../projects/{project_id}/upload-sessions                      open a session
    GET  .../projects/{project_id}/upload-sessions/{id}                 read it
    GET  .../projects/{project_id}/upload-sessions/{id}/parts           parts stored so far (resume)
    POST .../projects/{project_id}/upload-sessions/{id}/part-urls       short-lived PUT URLs
    POST .../projects/{project_id}/upload-sessions/{id}/finalize        verify + create the Dataset

Role gating is `DATASET_SOURCE_ROLES`. `ml_engineer` may not open or read a
`held_out_evaluation` upload (Section 9/69.1): opening is 403 `purpose_forbidden`
and reading is the same 404 as a missing id.

Errors are `{"detail": "<stable_code>"}`. Finalize answers 201 with the Dataset,
or: 409 `upload_session_not_open` | `upload_incomplete` |
`dataset_version_conflict`, 410 `upload_expired`, 422 with the rejection reason
(`size_mismatch`, `sha256_mismatch`, `content_type_mismatch`, `part_size_mismatch`,
`unexpected_part`, `unsupported_content_type`, `source_revoked`; the session is
then `rejected` and the object deleted), 503 `storage_unavailable` |
`storage_not_configured` (the session stays open; an outage is never reported
as a rejection or a success).

Only the part-URL response carries a storage URL, and only for the caller's own
open session. No other response carries an object key, bucket or storage URI.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
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
from app.datasets.dataset_routes import _serialize as _serialize_dataset
from app.datasets.dataset_source_connection_crud import (
    DatasetSourceAuthorizationError,
    DatasetSourceConnectionNotFoundError,
)
from app.datasets.dataset_source_connection_routes import DATASET_SOURCE_ROLES
from app.datasets.dataset_upload_store import (
    DATASET_NAME_MAX_LENGTH,
    HELD_OUT_PURPOSE,
    MAX_UPLOAD_BYTES,
    DatasetUploadSession,
    DatasetVersionConflictError,
    NotAnUploadSourceError,
    ProjectNotFoundError,
    UploadActor,
    UploadExpiredError,
    UploadSessionNotFoundError,
    UploadStateError,
    UploadVerificationError,
    create_upload_session,
    expire_open_sessions,
    get_upload_session,
    record_upload_rejected,
    record_upload_verified_and_create_dataset,
)
from app.datasets.direct_object_storage_upload import (
    UploadExpired,
    UploadIncomplete,
    UploadRejection,
    UploadStorageSettings,
    UploadStorageUnavailableError,
    UploadTarget,
    abort_upload,
    find_upload_id,
    list_uploaded_parts,
    normalize_content_type,
    plan_parts,
    presign_part_urls,
    start_multipart,
    verify_and_complete,
)
from app.db.tenant_context import TenantContext, tenant_scoped_session_for

__all__ = ["build_direct_upload_router"]

_COLLECTION_PATH = "/api/v1/organizations/{organization_id}/projects/{project_id}/upload-sessions"
_ITEM_PATH = _COLLECTION_PATH + "/{upload_session_id}"
_PARTS_PATH = _ITEM_PATH + "/parts"
_PART_URLS_PATH = _ITEM_PATH + "/part-urls"
_FINALIZE_PATH = _ITEM_PATH + "/finalize"
_MAX_BODY_BYTES = 64 * 1024
_HELD_OUT_ONLY = frozenset({HELD_OUT_PURPOSE})


def _is_held_out(purpose: str) -> bool:
    """Fail closed: anything that reads as the held-out purpose, whatever its
    case or surrounding whitespace, is treated as held-out."""
    return purpose.strip().lower() in _HELD_OUT_ONLY


_CREATE_FIELDS = frozenset(
    {
        "dataset_source_connection_id",
        "purpose",
        "declared_size_bytes",
        "declared_sha256",
        "declared_content_type",
    }
)

StorageFactory = Callable[[], UploadStorageSettings | None]


def _default_storage_factory() -> UploadStorageSettings | None:
    try:
        return UploadStorageSettings.from_env()
    except ValueError as exc:
        raise HTTPException(status_code=503, detail="storage_not_configured") from exc


async def _read_object(request: Request, allowed: frozenset[str]) -> dict[str, Any]:
    raw = await request.body()
    if len(raw) > _MAX_BODY_BYTES:
        raise HTTPException(status_code=413, detail="request_too_large")
    try:
        payload = json.loads(raw)
    except (ValueError, UnicodeDecodeError) as exc:
        raise HTTPException(status_code=400, detail="invalid_body") from exc
    if not isinstance(payload, dict) or set(payload) != allowed:
        raise HTTPException(status_code=400, detail="invalid_body")
    return payload


def _actor(ctx: AuthorizedContext) -> UploadActor:
    return UploadActor(
        actor_type="user", actor_id=ctx.principal.subject, request_id=uuid.uuid4().hex
    )


def _target(record: DatasetUploadSession) -> UploadTarget:
    return UploadTarget(
        object_key=record.object_key,
        declared_size_bytes=record.declared_size_bytes,
        declared_sha256=record.declared_sha256,
        declared_content_type=record.declared_content_type,
        expires_at=record.expires_at,
    )


def _serialize(record: DatasetUploadSession) -> dict[str, object]:
    plan = plan_parts(record.declared_size_bytes)
    return {
        "id": str(record.id),
        "project_id": str(record.project_id),
        "dataset_source_connection_id": str(record.dataset_source_connection_id),
        "purpose": record.purpose,
        "status": record.status,
        "declared_size_bytes": record.declared_size_bytes,
        "declared_sha256": record.declared_sha256,
        "declared_content_type": record.declared_content_type,
        "rejection_reason": record.rejection_reason,
        "created_at": record.created_at.isoformat(),
        "expires_at": record.expires_at.isoformat(),
        "finalized_at": record.finalized_at.isoformat() if record.finalized_at else None,
        "part_size_bytes": plan.part_size_bytes,
        "part_count": plan.part_count,
    }


def _may_see_held_out(ctx: AuthorizedContext) -> bool:
    return ctx.role is not Role.ML_ENGINEER


def _not_found() -> HTTPException:
    return HTTPException(status_code=404, detail="not_found")


def _abort_best_effort(client: Any, settings: UploadStorageSettings, target: UploadTarget) -> None:
    """Cleanup after the session is already closed in the database. A storage
    outage here must not change the answer; the unfinalized multipart upload
    is aborted by `upload_sweeper` once it is 24 h old (Section 67.5); no bucket
    lifecycle rule is configured by this repository."""
    try:
        abort_upload(client, settings, target)
    except UploadStorageUnavailableError:
        pass


def _storage(factory: StorageFactory) -> UploadStorageSettings:
    settings = factory()
    if settings is None:
        raise HTTPException(status_code=503, detail="storage_not_configured")
    return settings


def build_direct_upload_router(
    session_factory: Callable[[], Session],
    *,
    principal_resolver: PrincipalResolver = default_principal_resolver,
    role_lookup: RoleLookup = default_role_lookup,
    storage_factory: StorageFactory = _default_storage_factory,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> APIRouter:
    router = APIRouter()
    dependency = Depends(
        require_role(
            DATASET_SOURCE_ROLES, principal_resolver=principal_resolver, role_lookup=role_lookup
        )
    )

    def load(
        ctx: AuthorizedContext, project_id: uuid.UUID, upload_session_id: uuid.UUID
    ) -> DatasetUploadSession:
        tenant_context = TenantContext.from_authorized_context(ctx)
        try:
            with tenant_scoped_session_for(session_factory, tenant_context) as session:
                record = get_upload_session(session, tenant_context, project_id, upload_session_id)
                session.expunge(record)
        except (UploadSessionNotFoundError, TenantContextError) as exc:
            raise _not_found() from exc
        if _is_held_out(record.purpose) and not _may_see_held_out(ctx):
            raise _not_found()
        return record

    def reject_in_new_transaction(
        ctx: AuthorizedContext, project_id: uuid.UUID, upload_session_id: uuid.UUID, reason: str
    ) -> bool:
        """Records the rejection in its own transaction; `False` when the session
        was no longer open (someone else closed it first)."""
        tenant_context = TenantContext.from_authorized_context(ctx)
        try:
            with tenant_scoped_session_for(session_factory, tenant_context) as session:
                record_upload_rejected(
                    session,
                    tenant_context,
                    project_id=project_id,
                    upload_session_id=upload_session_id,
                    reason=reason,
                    now=clock(),
                    actor=_actor(ctx),
                )
        except UploadStateError:
            return False
        return True

    def expire_in_new_transaction(ctx: AuthorizedContext) -> None:
        tenant_context = TenantContext.from_authorized_context(ctx)
        with tenant_scoped_session_for(session_factory, tenant_context) as session:
            expire_open_sessions(session, tenant_context, now=clock(), actor=_actor(ctx))

    @router.post(_COLLECTION_PATH, status_code=201)
    async def create(
        request: Request,
        organization_id: uuid.UUID,
        project_id: uuid.UUID,
        ctx: AuthorizedContext = dependency,
    ) -> JSONResponse:
        del organization_id
        payload = await _read_object(request, _CREATE_FIELDS)
        settings = _storage(storage_factory)
        try:
            connection_id = uuid.UUID(str(payload["dataset_source_connection_id"]))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="invalid_body") from exc
        purpose = payload["purpose"]
        size = payload["declared_size_bytes"]
        content_type = payload["declared_content_type"]
        if (
            not isinstance(purpose, str)
            or isinstance(size, bool)
            or not isinstance(size, int)
            or not isinstance(content_type, str)
            or not 0 < size <= MAX_UPLOAD_BYTES
        ):
            raise HTTPException(status_code=400, detail="invalid_body")
        if normalize_content_type(content_type) is None:
            raise HTTPException(status_code=400, detail="unsupported_content_type")
        if _is_held_out(purpose) and not _may_see_held_out(ctx):
            raise HTTPException(status_code=403, detail="purpose_forbidden")
        tenant_context = TenantContext.from_authorized_context(ctx)
        try:
            with tenant_scoped_session_for(session_factory, tenant_context) as session:
                record = create_upload_session(
                    session,
                    tenant_context,
                    project_id=project_id,
                    connection_id=connection_id,
                    purpose=purpose,
                    declared_size_bytes=size,
                    declared_sha256=payload["declared_sha256"],
                    declared_content_type=content_type,
                    max_bytes=size,
                    now=clock(),
                    actor=_actor(ctx),
                )
                # Opened inside the transaction: if storage refuses, the session
                # row rolls back with it and no orphan session remains.
                start_multipart(settings.client(), settings, _target(record))
                body = _serialize(record)
        except (
            ProjectNotFoundError,
            DatasetSourceConnectionNotFoundError,
            TenantContextError,
        ) as exc:
            raise _not_found() from exc
        except DatasetSourceAuthorizationError as exc:
            raise HTTPException(status_code=403, detail="purpose_not_granted") from exc
        except NotAnUploadSourceError as exc:
            raise HTTPException(status_code=409, detail="not_an_upload_source") from exc
        except UploadStorageUnavailableError as exc:
            raise HTTPException(status_code=503, detail="storage_unavailable") from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="invalid_body") from exc
        return JSONResponse(body, status_code=201)

    @router.get(_ITEM_PATH)
    async def read_one(
        organization_id: uuid.UUID,
        project_id: uuid.UUID,
        upload_session_id: uuid.UUID,
        ctx: AuthorizedContext = dependency,
    ) -> JSONResponse:
        del organization_id
        return JSONResponse(_serialize(load(ctx, project_id, upload_session_id)))

    @router.get(_PARTS_PATH)
    async def read_parts(
        organization_id: uuid.UUID,
        project_id: uuid.UUID,
        upload_session_id: uuid.UUID,
        ctx: AuthorizedContext = dependency,
    ) -> JSONResponse:
        del organization_id
        record = load(ctx, project_id, upload_session_id)
        if record.status != "open":
            raise HTTPException(status_code=409, detail="upload_session_not_open")
        settings = _storage(storage_factory)
        target = _target(record)
        try:
            client = settings.client()
            upload_id = find_upload_id(client, settings, target)
            parts = (
                []
                if upload_id is None
                else list_uploaded_parts(client, settings, target, upload_id)
            )
        except UploadStorageUnavailableError as exc:
            raise HTTPException(status_code=503, detail="storage_unavailable") from exc
        plan = plan_parts(record.declared_size_bytes)
        return JSONResponse(
            {
                "part_size_bytes": plan.part_size_bytes,
                "part_count": plan.part_count,
                "parts": [
                    {"part_number": part.part_number, "size_bytes": part.size_bytes}
                    for part in parts
                ],
            }
        )

    @router.post(_PART_URLS_PATH)
    async def part_urls(
        request: Request,
        organization_id: uuid.UUID,
        project_id: uuid.UUID,
        upload_session_id: uuid.UUID,
        ctx: AuthorizedContext = dependency,
    ) -> JSONResponse:
        del organization_id
        payload = await _read_object(request, frozenset({"part_numbers"}))
        record = load(ctx, project_id, upload_session_id)
        if record.status != "open":
            raise HTTPException(status_code=409, detail="upload_session_not_open")
        settings = _storage(storage_factory)
        target = _target(record)
        try:
            upload_id = find_upload_id(settings.client(), settings, target)
            if upload_id is None:
                raise HTTPException(status_code=409, detail="upload_session_not_open")
            grants = presign_part_urls(
                settings, target, upload_id, payload["part_numbers"], now=clock()
            )
        except UploadExpired as exc:
            expire_in_new_transaction(ctx)
            _abort_best_effort(settings.client(), settings, target)
            raise HTTPException(status_code=410, detail="upload_expired") from exc
        except UploadStorageUnavailableError as exc:
            raise HTTPException(status_code=503, detail="storage_unavailable") from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="invalid_part_numbers") from exc
        return JSONResponse(
            {
                "parts": [
                    {
                        "part_number": grant.part_number,
                        "method": grant.method,
                        "url": grant.url,
                        "content_length": grant.content_length,
                        "expires_at": grant.expires_at.isoformat(),
                    }
                    for grant in grants
                ]
            }
        )

    @router.post(_FINALIZE_PATH, status_code=201)
    async def finalize(
        request: Request,
        organization_id: uuid.UUID,
        project_id: uuid.UUID,
        upload_session_id: uuid.UUID,
        ctx: AuthorizedContext = dependency,
    ) -> JSONResponse:
        del organization_id
        payload = await _read_object(request, frozenset({"dataset_name"}))
        dataset_name = payload["dataset_name"]
        # Validated before any storage work: a name the store would refuse must
        # not leave a completed-but-unrecorded object behind.
        if not isinstance(dataset_name, str):
            raise HTTPException(status_code=400, detail="invalid_body")
        dataset_name = dataset_name.strip()
        if not dataset_name or len(dataset_name) > DATASET_NAME_MAX_LENGTH:
            raise HTTPException(status_code=400, detail="invalid_body")
        record = load(ctx, project_id, upload_session_id)
        if record.status != "open":
            raise HTTPException(status_code=409, detail="upload_session_not_open")
        settings = _storage(storage_factory)
        target = _target(record)
        client = settings.client()

        def expired() -> HTTPException:
            expire_in_new_transaction(ctx)
            _abort_best_effort(client, settings, target)
            return HTTPException(status_code=410, detail="upload_expired")

        # Storage work happens outside any database transaction: hashing a
        # 10 GiB object must not hold a row lock.
        try:
            verified = verify_and_complete(client, settings, target, now=clock())
        except UploadIncomplete as exc:
            raise HTTPException(status_code=409, detail="upload_incomplete") from exc
        except UploadExpired as exc:
            raise expired() from exc
        except UploadRejection as rejection:
            if not reject_in_new_transaction(ctx, project_id, upload_session_id, rejection.reason):
                raise HTTPException(status_code=409, detail="upload_session_not_open") from None
            raise HTTPException(status_code=422, detail=rejection.reason) from rejection
        except UploadStorageUnavailableError as exc:
            raise HTTPException(status_code=503, detail="storage_unavailable") from exc

        tenant_context = TenantContext.from_authorized_context(ctx)
        try:
            with tenant_scoped_session_for(session_factory, tenant_context) as session:
                dataset = record_upload_verified_and_create_dataset(
                    session,
                    tenant_context,
                    project_id=project_id,
                    upload_session_id=upload_session_id,
                    dataset_name=dataset_name,
                    verified_size_bytes=verified.size_bytes,
                    verified_sha256=verified.sha256,
                    verified_content_type=verified.content_type,
                    now=clock(),
                    actor=_actor(ctx),
                )
                body = _serialize_dataset(dataset)
        except UploadExpiredError as exc:
            raise expired() from exc
        except UploadVerificationError as exc:
            _abort_best_effort(client, settings, target)
            reject_in_new_transaction(ctx, project_id, upload_session_id, "verification_mismatch")
            raise HTTPException(status_code=422, detail="verification_mismatch") from exc
        except DatasetSourceAuthorizationError as exc:
            _abort_best_effort(client, settings, target)
            reject_in_new_transaction(ctx, project_id, upload_session_id, "source_revoked")
            raise HTTPException(status_code=422, detail="source_revoked") from exc
        except DatasetVersionConflictError as exc:
            raise HTTPException(status_code=409, detail="dataset_version_conflict") from exc
        except UploadStateError as exc:
            raise HTTPException(status_code=409, detail="upload_session_not_open") from exc
        except (UploadSessionNotFoundError, TenantContextError) as exc:
            raise _not_found() from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="invalid_body") from exc
        return JSONResponse(body, status_code=201)

    return router
