"""Live routes for dataset field mapping (FTF-H011-P3; FINE_TUNING_FACTORY_RELEASE_1.md Section
7A.10, Section 8-8.2, Section 9, Section 67.1-67.4, Section 69.1, Section 77; AF-15):

    POST .../datasets/{dataset_id}/mapping-probe                   headers, raw rows
    POST .../datasets/{dataset_id}/mapping-preview                 raw beside canonical, read-only
    POST .../datasets/{dataset_id}/mappings                        propose the next version
    GET  .../datasets/{dataset_id}/mappings                        versions (limit, after)
    GET  .../datasets/{dataset_id}/mappings/{mapping_id}           one version
    POST .../datasets/{dataset_id}/mappings/{mapping_id}/approve   proposed -> approved
    POST .../datasets/{dataset_id}/mappings/{mapping_id}/reject    proposed -> rejected

Every route needs `ml_engineer`, `workspace_admin` or `organization_admin`; approve and reject
need `workspace_admin` or `organization_admin` (an ml_engineer is a 403). Errors are `{"detail":
"<code>"}`. The routes call the FTF-H011-P2 store and the FTF-H011-P1 preview service; the mapping
grammar is the certified FTF-0502-P1 `mapping_from_object`, the dataset bytes are read only
through `open_dataset_source`, and the importer comes from the server-verified content type only.

Masking (Section 67.2 "masked unless the user has preview permission"): values are shown to the
admin roles, and to an ml_engineer only when the dataset's connection is classified public or
internal. Otherwise every string leaf, header and key of a probe or preview is a `{"masked": true,
"chars": n}` marker and a stored mapping's document and rationale are withheld (`masked: true`).
Held-out datasets are visible to the admin roles only; to an ml_engineer they are a 404, identical
to a missing one.

`Idempotency-Key` is required on the three POST routes that write. The request digest binds the
action, the dataset, the mapping, the authenticated principal and the body. The key is stored with
that digest (and the rationale of an approval or a rejection) in the transaction that proposes,
approves or rejects (`dataset_mapping_requests`): the same key with the same digest replays the
stored outcome (200, `replayed: true`) and writes nothing; the same key with another digest is 409
`idempotency_key_reused`. A request that already took effect under another key is still recognised
from the mapping row and its stored request.

A response is built from an explicit allow-list of fields. It never carries an object key, a
bucket, a credential, the preview of a stored mapping or a detector message."""

from __future__ import annotations

import hmac
import re
import uuid
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from typing import Any, Final

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session
from workers.data.dataset_mapping_review import MappingPreviewResult, ProbeResult
from workers.data.import_common import StrictJsonError, strict_loads

from app.auth.audit_event_writer import write_audit_event
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
from app.datasets.dataset_mapping_request_store import (
    MappingRequestError,
    find_mapping_request,
    find_mapping_request_by_action,
    record_mapping_request,
    request_digest,
)
from app.datasets.dataset_mapping_store import (
    DatasetMapping,
    MappingActor,
    MappingStoreError,
    approve_dataset_mapping,
    dataset_visibility,
    get_dataset_mapping,
    list_dataset_mappings,
    propose_dataset_mapping,
    reject_dataset_mapping,
    review_preview,
    review_probe,
)
from app.datasets.dataset_object_source import DatasetObjectStorage
from app.datasets.dataset_source_connection_routes import DATASET_SOURCE_ROLES
from app.datasets.direct_object_storage_upload import UploadStorageSettings
from app.db.tenant_context import TenantContext, tenant_scoped_session_for

__all__ = ["build_dataset_mapping_router"]

_BASE_PATH: Final = (
    "/api/v1/organizations/{organization_id}/projects/{project_id}/datasets/{dataset_id}"
)
_DEFAULT_LIMIT: Final = 50
_MAX_LIMIT: Final = 200
_MAX_BODY_BYTES: Final = 1024 * 1024
_MAX_JSON_DEPTH: Final = 32
_MAX_RATIONALE: Final = 2000
_MAX_PURPOSE: Final = 32
_MAX_KEYS: Final = 200
_MAX_KEY_LENGTH: Final = 200
_KEY_PATTERN: Final = re.compile(r"[A-Za-z0-9._:-]{8,200}")
_HEX64: Final = re.compile(r"[0-9a-f]{64}")
_WRITE_ROLES: Final[frozenset[Role]] = frozenset({Role.WORKSPACE_ADMIN, Role.ORGANIZATION_ADMIN})
_PROBE_FIELDS: Final = frozenset({"csv", "json"})
_MAPPING_FIELDS: Final = frozenset({"mapping"})
_APPROVE_FIELDS: Final = frozenset(
    {"expected_preview_sha256", "purpose", "acknowledged_ignored_keys", "rationale"}
)
_REJECT_FIELDS: Final = frozenset({"rationale"})
_RESOURCE: Final = "dataset_mapping"

# Store refusals by HTTP status; any other code is a state or hash conflict (409).
_BAD_REQUEST: Final = frozenset(
    {
        "invalid_request",
        "mapping_invalid",
        "mapping_unsupported",
        "critical_role_unmapped",
        "tool_id_unmapped",
    }
)
_FORBIDDEN: Final = frozenset(
    {"actor_not_permitted", "approval_not_permitted", "purpose_not_authorized"}
)
_NOT_FOUND: Final = frozenset({"dataset_not_found", "mapping_not_found"})
# A refusal that may mean "an identical request already took effect": look for the stored outcome.
_REPLAYABLE: Final = frozenset({"mapping_not_proposed", "idempotency_key_reused"})
# The decision a stored status records: the action of the request that took the mapping there.
_DECISION_ACTION: Final = {"approved": "approve", "rejected": "reject"}

_MAPPING_FIELD_NAMES: Final = (
    "id",
    "dataset_id",
    "dataset_name",
    "dataset_purpose",
    "dataset_version",
    "dataset_sha256",
    "mapping_version",
    "parent_mapping_version",
    "mapping_sha256",
    "importer",
    "importer_version",
    "transform_version",
    "source_schema_sha256",
    "tool_definitions_sha256",
    "preview_sha256",
    "preview_counts",
    "status",
    "allowed_purposes_snapshot",
    "approved_purpose",
    "approved_by",
    "approver_role",
    "approved_at",
    "created_by",
    "created_at",
    "updated_at",
)
_UUID_FIELDS: Final = frozenset({"id", "dataset_id"})

StorageFactory = Callable[[], UploadStorageSettings | None]


def _default_storage_factory() -> UploadStorageSettings | None:
    try:
        return UploadStorageSettings.from_env()
    except ValueError as exc:
        raise HTTPException(status_code=503, detail="storage_not_configured") from exc


def _not_found() -> HTTPException:
    return HTTPException(status_code=404, detail="not_found")


def _invalid_body() -> HTTPException:
    return HTTPException(status_code=400, detail="invalid_body")


def _replayable(code: str) -> bool:
    return code in _REPLAYABLE


def _refusal(code: str) -> HTTPException:
    if code in _NOT_FOUND:
        return _not_found()
    if code in _BAD_REQUEST:
        return HTTPException(status_code=400, detail=code)
    if code in _FORBIDDEN:
        return HTTPException(status_code=403, detail=code)
    if code == "source_unavailable":
        return HTTPException(status_code=503, detail="storage_unavailable")
    return HTTPException(status_code=409, detail=code)


def _storage(factory: StorageFactory) -> DatasetObjectStorage:
    settings = factory()
    if settings is None:
        raise HTTPException(status_code=503, detail="storage_not_configured")
    return DatasetObjectStorage(client=settings.client(), bucket=settings.bucket)


def _idempotency_key(request: Request) -> str:
    key = request.headers.get("idempotency-key")
    if key is None or _KEY_PATTERN.fullmatch(key) is None:
        raise HTTPException(status_code=400, detail="invalid_idempotency_key")
    return key


async def _read_body(
    request: Request, *, required: frozenset[str], optional: frozenset[str] = frozenset()
) -> dict[str, Any]:
    raw = await request.body()
    if len(raw) > _MAX_BODY_BYTES:
        raise HTTPException(status_code=413, detail="request_too_large")
    try:
        payload = strict_loads(raw, max_depth=_MAX_JSON_DEPTH)
    except StrictJsonError as exc:
        raise _invalid_body() from exc
    if type(payload) is not dict:
        raise _invalid_body()
    fields = set(payload)
    if not required <= fields or not fields <= required | optional:
        raise _invalid_body()
    return payload


def _has_nul(value: object) -> bool:
    """U+0000 in a key or string of a parsed JSON value (PostgreSQL JSONB refuses it)."""
    if isinstance(value, str):
        return "\x00" in value
    if isinstance(value, dict):
        return any(_has_nul(key) or _has_nul(item) for key, item in value.items())
    if isinstance(value, list):
        return any(_has_nul(item) for item in value)
    return False


def _document(value: object) -> dict[str, Any]:
    if type(value) is not dict:
        raise _invalid_body()
    if _has_nul(value):
        raise HTTPException(status_code=400, detail="mapping_invalid")
    return value


def _rationale(value: object) -> str:
    if (
        type(value) is not str
        or not 1 <= len(value) <= _MAX_RATIONALE
        or value.isspace()
        or "\x00" in value
    ):
        raise _invalid_body()
    try:
        value.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise _invalid_body() from exc
    return value


def _approve_fields(payload: dict[str, Any]) -> dict[str, Any]:
    expected = payload["expected_preview_sha256"]
    purpose = payload["purpose"]
    acknowledged = payload["acknowledged_ignored_keys"]
    if type(expected) is not str or _HEX64.fullmatch(expected) is None:
        raise _invalid_body()
    if type(purpose) is not str or not 1 <= len(purpose) <= _MAX_PURPOSE:
        raise _invalid_body()
    if (
        type(acknowledged) is not list
        or len(acknowledged) > _MAX_KEYS
        or not all(type(key) is str and 1 <= len(key) <= _MAX_KEY_LENGTH for key in acknowledged)
    ):
        raise _invalid_body()
    return {
        "expected_preview_sha256": expected,
        "purpose": purpose,
        "acknowledged_ignored_keys": sorted(acknowledged),
        "rationale": _rationale(payload["rationale"]),
    }


def _parse_limit(request: Request) -> int:
    raw = request.query_params.get("limit")
    if raw is None:
        return _DEFAULT_LIMIT
    if not raw.isascii() or not raw.isdecimal() or len(raw) > 3:
        raise HTTPException(status_code=400, detail="invalid_limit")
    limit = int(raw)
    if not 1 <= limit <= _MAX_LIMIT:
        raise HTTPException(status_code=400, detail="invalid_limit")
    return limit


def _parse_after(request: Request) -> int:
    raw = request.query_params.get("after")
    if raw is None:
        return 0
    if not raw.isascii() or not raw.isdecimal() or len(raw) > 9:
        raise HTTPException(status_code=400, detail="invalid_after")
    return int(raw)


def _actor(ctx: AuthorizedContext) -> MappingActor:
    return MappingActor(
        actor_type="user",
        actor_id=ctx.principal.subject,
        role=ctx.role.value,
        request_id=uuid.uuid4().hex,
    )


# ------------------------------------------------------------------ serialization


def _jsonable(value: object) -> object:
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_jsonable(item) for item in value]
    return value


def _serialize_preview(result: MappingPreviewResult, reveal: bool) -> dict[str, Any]:
    return {
        "state": result.state,
        "failure_code": result.failure_code,
        "complete": result.complete,
        "masked": not reveal,
        "counts": {str(key): int(value) for key, value in result.counts.items()},
        "source_schema_sha256": result.source_schema_sha256,
        "tool_definitions_sha256": result.tool_definitions_sha256,
        "preview_sha256": result.preview_sha256,
        "observed_role_values": _jsonable(result.observed_role_values),
        "rows": [
            {
                "locator": row.locator,
                "outcome": row.outcome,
                "code": row.code,
                "slot": row.slot,
                "raw_row": _jsonable(row.raw_row),
                "not_mapped": _jsonable(row.not_mapped),
                "candidate": _jsonable(row.candidate),
                "comparison": [
                    {
                        "slot": cell.slot,
                        "raw_type": cell.raw_type,
                        "canonical_type": cell.canonical_type,
                        "identical": cell.identical,
                    }
                    for cell in row.comparison
                ],
            }
            for row in result.rows
        ],
    }


def _serialize_probe(result: ProbeResult, reveal: bool) -> dict[str, Any]:
    return {
        "state": result.state,
        "failure_code": result.failure_code,
        "complete": result.complete,
        "masked": not reveal,
        "headers": _jsonable(result.headers),
        "rows": [_jsonable(row) for row in result.raw_rows],
    }


def _scalar(name: str, value: Any) -> Any:
    if value is None:
        return None
    if name in _UUID_FIELDS:
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, list | tuple):
        return list(value)
    return value


def _serialize_mapping(
    row: DatasetMapping, *, reveal: bool, decision: Any | None
) -> dict[str, Any]:
    body = {name: _scalar(name, getattr(row, name)) for name in _MAPPING_FIELD_NAMES}
    body["preview_counts"] = {str(k): int(v) for k, v in row.preview_counts.items()}
    body["masked"] = not reveal
    body["mapping_document"] = _jsonable(row.mapping_document) if reveal else None
    body["decision"] = (
        None
        if decision is None
        else {
            "action": decision.action,
            "rationale": decision.rationale if reveal else None,
            "decided_by": decision.created_by,
        }
    )
    return body


def _audit(
    session: Session,
    tenant: TenantContext,
    actor: MappingActor,
    action: str,
    *,
    project_id: uuid.UUID,
    dataset_id: uuid.UUID,
    details: dict[str, Any],
) -> None:
    write_audit_event(
        session,
        organization_id=tenant.organization_id,
        actor_type=actor.actor_type,
        actor_id=actor.actor_id,
        action=action,
        resource_type=_RESOURCE,
        resource_id=str(dataset_id),
        request_id=actor.request_id,
        details={"project_id": str(project_id), "dataset_id": str(dataset_id), **details},
    )


def _decision(
    session: Session, tenant: TenantContext, project_id: uuid.UUID, row: DatasetMapping
) -> Any | None:
    """The stored request that approved or rejected `row`; None while it is proposed (or in a
    status no decision leads to)."""
    action = _DECISION_ACTION.get(row.status)
    if action is None:
        return None
    return find_mapping_request_by_action(
        session, tenant, project_id=project_id, mapping_id=row.id, action=action
    )


def build_dataset_mapping_router(
    session_factory: Callable[[], Session],
    *,
    principal_resolver: PrincipalResolver = default_principal_resolver,
    role_lookup: RoleLookup = default_role_lookup,
    storage_factory: StorageFactory = _default_storage_factory,
) -> APIRouter:
    router = APIRouter()
    read_dependency = Depends(
        require_role(
            DATASET_SOURCE_ROLES, principal_resolver=principal_resolver, role_lookup=role_lookup
        )
    )
    write_dependency = Depends(
        require_role(_WRITE_ROLES, principal_resolver=principal_resolver, role_lookup=role_lookup)
    )

    def _view(
        session: Session,
        tenant: TenantContext,
        ctx: AuthorizedContext,
        project_id: uuid.UUID,
        dataset_id: uuid.UUID,
        mapping_id: uuid.UUID,
    ) -> dict[str, Any]:
        """One mapping version as `ctx` may see it. A mapping of another dataset, a dataset the role
        cannot see and a missing id are all `mapping_not_found` / `dataset_not_found`."""
        _held_out, reveal = dataset_visibility(
            session, tenant, project_id=project_id, dataset_id=dataset_id, role=ctx.role.value
        )
        row = get_dataset_mapping(session, tenant, project_id=project_id, mapping_id=mapping_id)
        if row.dataset_id != dataset_id:
            raise MappingStoreError("mapping_not_found")
        return _serialize_mapping(
            row, reveal=reveal, decision=_decision(session, tenant, project_id, row)
        )

    def _stored_outcome(
        tenant: TenantContext,
        ctx: AuthorizedContext,
        project_id: uuid.UUID,
        dataset_id: uuid.UUID,
        key: str,
        digest: str,
    ) -> dict[str, Any] | None:
        """The outcome stored under this Idempotency-Key: the mapping as it is now when the key
        was used by this very request, None when the key is unused, and 409
        `idempotency_key_reused` when it was used by any other request. Read-only."""
        with tenant_scoped_session_for(session_factory, tenant) as session:
            stored = find_mapping_request(
                session, tenant, project_id=project_id, idempotency_key=key
            )
            if stored is None:
                return None
            if not hmac.compare_digest(stored.request_digest, digest):
                raise MappingRequestError("idempotency_key_reused")
            return _view(session, tenant, ctx, project_id, dataset_id, stored.mapping_id)

    def _replayed_elsewhere(
        tenant: TenantContext,
        ctx: AuthorizedContext,
        project_id: uuid.UUID,
        dataset_id: uuid.UUID,
        mapping_id: uuid.UUID,
        action: str,
        digest: str,
    ) -> dict[str, Any] | None:
        """The mapping as it is now when this exact request already took effect under another key
        (a stored request of this action on this mapping with the same digest: the action, the
        dataset, the mapping, the principal and the body), None otherwise. Read-only."""
        with tenant_scoped_session_for(session_factory, tenant) as session:
            stored = find_mapping_request_by_action(
                session, tenant, project_id=project_id, mapping_id=mapping_id, action=action
            )
            if stored is None or not hmac.compare_digest(stored.request_digest, digest):
                return None
            return _view(session, tenant, ctx, project_id, dataset_id, mapping_id)

    def _replay(
        tenant: TenantContext,
        ctx: AuthorizedContext,
        project_id: uuid.UUID,
        dataset_id: uuid.UUID,
        mapping_id: uuid.UUID | None,
        action: str,
        key: str,
        digest: str,
    ) -> dict[str, Any] | None:
        outcome = _stored_outcome(tenant, ctx, project_id, dataset_id, key, digest)
        if outcome is not None or mapping_id is None:
            return outcome
        return _replayed_elsewhere(tenant, ctx, project_id, dataset_id, mapping_id, action, digest)

    # -------------------------------------------------------------- probe and preview

    @router.post(_BASE_PATH + "/mapping-probe")
    async def probe(
        request: Request,
        organization_id: uuid.UUID,
        project_id: uuid.UUID,
        dataset_id: uuid.UUID,
        ctx: AuthorizedContext = read_dependency,
    ) -> JSONResponse:
        del organization_id
        payload = await _read_body(request, required=frozenset(), optional=_PROBE_FIELDS)
        tenant = TenantContext.from_authorized_context(ctx)
        storage = _storage(storage_factory)
        try:
            with tenant_scoped_session_for(session_factory, tenant) as session:
                result, reveal = review_probe(
                    session,
                    tenant,
                    project_id=project_id,
                    dataset_id=dataset_id,
                    format_options=payload,
                    role=ctx.role.value,
                    storage=storage,
                )
                _audit(
                    session,
                    tenant,
                    _actor(ctx),
                    "dataset_mapping.probed",
                    project_id=project_id,
                    dataset_id=dataset_id,
                    details={
                        "state": result.state,
                        "rows": len(result.raw_rows),
                        "masked": not reveal,
                    },
                )
                body = _serialize_probe(result, reveal)
        except TenantContextError as exc:
            raise _not_found() from exc
        except MappingStoreError as exc:
            raise _refusal(exc.code) from exc
        return JSONResponse(body)

    @router.post(_BASE_PATH + "/mapping-preview")
    async def preview(
        request: Request,
        organization_id: uuid.UUID,
        project_id: uuid.UUID,
        dataset_id: uuid.UUID,
        ctx: AuthorizedContext = read_dependency,
    ) -> JSONResponse:
        del organization_id
        payload = await _read_body(request, required=_MAPPING_FIELDS)
        document = _document(payload["mapping"])
        tenant = TenantContext.from_authorized_context(ctx)
        storage = _storage(storage_factory)
        try:
            with tenant_scoped_session_for(session_factory, tenant) as session:
                result, reveal = review_preview(
                    session,
                    tenant,
                    project_id=project_id,
                    dataset_id=dataset_id,
                    mapping_document=document,
                    role=ctx.role.value,
                    storage=storage,
                )
                counts = result.counts
                _audit(
                    session,
                    tenant,
                    _actor(ctx),
                    "dataset_mapping.previewed",
                    project_id=project_id,
                    dataset_id=dataset_id,
                    details={
                        "state": result.state,
                        "preview_sha256": result.preview_sha256,
                        "seen": int(counts.get("seen", 0)),
                        "accepted": int(counts.get("accepted", 0)),
                        "rejected": int(counts.get("rejected", 0)),
                        "masked": not reveal,
                    },
                )
                body = _serialize_preview(result, reveal)
        except TenantContextError as exc:
            raise _not_found() from exc
        except MappingStoreError as exc:
            raise _refusal(exc.code) from exc
        return JSONResponse(body)

    # -------------------------------------------------------------- reads

    @router.get(_BASE_PATH + "/mappings")
    async def list_mappings(
        request: Request,
        organization_id: uuid.UUID,
        project_id: uuid.UUID,
        dataset_id: uuid.UUID,
        ctx: AuthorizedContext = read_dependency,
    ) -> JSONResponse:
        del organization_id
        limit = _parse_limit(request)
        after = _parse_after(request)
        tenant = TenantContext.from_authorized_context(ctx)
        try:
            with tenant_scoped_session_for(session_factory, tenant) as session:
                _held_out, reveal = dataset_visibility(
                    session,
                    tenant,
                    project_id=project_id,
                    dataset_id=dataset_id,
                    role=ctx.role.value,
                )
                rows = list_dataset_mappings(
                    session,
                    tenant,
                    project_id=project_id,
                    dataset_id=dataset_id,
                    limit=limit,
                    after_version=after,
                )
                items = [
                    _serialize_mapping(
                        row,
                        reveal=reveal,
                        decision=_decision(session, tenant, project_id, row),
                    )
                    for row in rows
                ]
        except TenantContextError as exc:
            raise _not_found() from exc
        except MappingStoreError as exc:
            raise _refusal(exc.code) from exc
        next_after = items[-1]["mapping_version"] if len(items) == limit else None
        return JSONResponse({"items": items, "next_after": next_after})

    @router.get(_BASE_PATH + "/mappings/{mapping_id}")
    async def read_mapping(
        organization_id: uuid.UUID,
        project_id: uuid.UUID,
        dataset_id: uuid.UUID,
        mapping_id: uuid.UUID,
        ctx: AuthorizedContext = read_dependency,
    ) -> JSONResponse:
        del organization_id
        tenant = TenantContext.from_authorized_context(ctx)
        try:
            with tenant_scoped_session_for(session_factory, tenant) as session:
                body = _view(session, tenant, ctx, project_id, dataset_id, mapping_id)
        except TenantContextError as exc:
            raise _not_found() from exc
        except MappingStoreError as exc:
            raise _refusal(exc.code) from exc
        return JSONResponse(body)

    # -------------------------------------------------------------- writes

    @router.post(_BASE_PATH + "/mappings")
    async def propose(
        request: Request,
        organization_id: uuid.UUID,
        project_id: uuid.UUID,
        dataset_id: uuid.UUID,
        ctx: AuthorizedContext = read_dependency,
    ) -> JSONResponse:
        del organization_id
        key = _idempotency_key(request)
        payload = await _read_body(request, required=_MAPPING_FIELDS)
        document = _document(payload["mapping"])
        digest = request_digest(
            "propose",
            dataset_id=dataset_id,
            mapping_id=None,
            subject=ctx.principal.subject,
            body={"mapping": document},
        )
        tenant = TenantContext.from_authorized_context(ctx)
        storage = _storage(storage_factory)
        try:
            replay = _replay(tenant, ctx, project_id, dataset_id, None, "propose", key, digest)
            if replay is not None:
                return JSONResponse({**replay, "replayed": True, "created": False})
            try:
                with tenant_scoped_session_for(session_factory, tenant) as session:
                    actor = _actor(ctx)
                    instant = datetime.now(UTC)
                    row = propose_dataset_mapping(
                        session,
                        tenant,
                        project_id=project_id,
                        dataset_id=dataset_id,
                        mapping_document=document,
                        storage=storage,
                        actor=actor,
                        now=instant,
                    )
                    # An unchanged re-proposal returns the existing row (older created_at).
                    created = row.created_at == instant
                    record_mapping_request(
                        session,
                        tenant,
                        project_id=project_id,
                        mapping_id=row.id,
                        action="propose",
                        idempotency_key=key,
                        digest=digest,
                        rationale=None,
                        created_by=actor.actor_id,
                    )
                    _held_out, reveal = dataset_visibility(
                        session,
                        tenant,
                        project_id=project_id,
                        dataset_id=dataset_id,
                        role=ctx.role.value,
                    )
                    body = _serialize_mapping(
                        row,
                        reveal=reveal,
                        decision=_decision(session, tenant, project_id, row),
                    )
            except MappingRequestError as exc:
                # A concurrent identical request may have stored the key first.
                replay = (
                    _replay(tenant, ctx, project_id, dataset_id, None, "propose", key, digest)
                    if _replayable(exc.code)
                    else None
                )
                if replay is None:
                    raise
                return JSONResponse({**replay, "replayed": True, "created": False})
        except TenantContextError as exc:
            raise _not_found() from exc
        except MappingStoreError as exc:
            raise _refusal(exc.code) from exc
        except MappingRequestError as exc:
            raise HTTPException(status_code=409, detail=exc.code) from exc
        return JSONResponse(
            {**body, "replayed": False, "created": created}, status_code=201 if created else 200
        )

    @router.post(_BASE_PATH + "/mappings/{mapping_id}/approve")
    async def approve(
        request: Request,
        organization_id: uuid.UUID,
        project_id: uuid.UUID,
        dataset_id: uuid.UUID,
        mapping_id: uuid.UUID,
        ctx: AuthorizedContext = write_dependency,
    ) -> JSONResponse:
        del organization_id
        key = _idempotency_key(request)
        fields = _approve_fields(await _read_body(request, required=_APPROVE_FIELDS))
        digest = request_digest(
            "approve",
            dataset_id=dataset_id,
            mapping_id=mapping_id,
            subject=ctx.principal.subject,
            body=fields,
        )
        tenant = TenantContext.from_authorized_context(ctx)
        storage = _storage(storage_factory)
        try:
            replay = _replay(
                tenant, ctx, project_id, dataset_id, mapping_id, "approve", key, digest
            )
            if replay is not None:
                return JSONResponse({**replay, "replayed": True})
            try:
                with tenant_scoped_session_for(session_factory, tenant) as session:
                    _owned_mapping(session, tenant, project_id, dataset_id, mapping_id)
                    actor = _actor(ctx)
                    approve_dataset_mapping(
                        session,
                        tenant,
                        project_id=project_id,
                        mapping_id=mapping_id,
                        expected_preview_sha256=fields["expected_preview_sha256"],
                        purpose=fields["purpose"],
                        acknowledged_ignored_keys=fields["acknowledged_ignored_keys"],
                        storage=storage,
                        actor=actor,
                    )
                    record_mapping_request(
                        session,
                        tenant,
                        project_id=project_id,
                        mapping_id=mapping_id,
                        action="approve",
                        idempotency_key=key,
                        digest=digest,
                        rationale=fields["rationale"],
                        created_by=actor.actor_id,
                    )
                    body = _view(session, tenant, ctx, project_id, dataset_id, mapping_id)
            except (MappingStoreError, MappingRequestError) as exc:
                # A concurrent identical request may have approved it first.
                replay = (
                    _replay(tenant, ctx, project_id, dataset_id, mapping_id, "approve", key, digest)
                    if _replayable(exc.code)
                    else None
                )
                if replay is None:
                    raise
                return JSONResponse({**replay, "replayed": True})
        except TenantContextError as exc:
            raise _not_found() from exc
        except MappingStoreError as exc:
            raise _refusal(exc.code) from exc
        except MappingRequestError as exc:
            raise HTTPException(status_code=409, detail=exc.code) from exc
        return JSONResponse({**body, "replayed": False})

    @router.post(_BASE_PATH + "/mappings/{mapping_id}/reject")
    async def reject(
        request: Request,
        organization_id: uuid.UUID,
        project_id: uuid.UUID,
        dataset_id: uuid.UUID,
        mapping_id: uuid.UUID,
        ctx: AuthorizedContext = write_dependency,
    ) -> JSONResponse:
        del organization_id
        key = _idempotency_key(request)
        payload = await _read_body(request, required=_REJECT_FIELDS)
        fields = {"rationale": _rationale(payload["rationale"])}
        digest = request_digest(
            "reject",
            dataset_id=dataset_id,
            mapping_id=mapping_id,
            subject=ctx.principal.subject,
            body=fields,
        )
        tenant = TenantContext.from_authorized_context(ctx)
        try:
            replay = _replay(tenant, ctx, project_id, dataset_id, mapping_id, "reject", key, digest)
            if replay is not None:
                return JSONResponse({**replay, "replayed": True})
            try:
                with tenant_scoped_session_for(session_factory, tenant) as session:
                    _owned_mapping(session, tenant, project_id, dataset_id, mapping_id)
                    actor = _actor(ctx)
                    reject_dataset_mapping(
                        session,
                        tenant,
                        project_id=project_id,
                        mapping_id=mapping_id,
                        actor=actor,
                    )
                    record_mapping_request(
                        session,
                        tenant,
                        project_id=project_id,
                        mapping_id=mapping_id,
                        action="reject",
                        idempotency_key=key,
                        digest=digest,
                        rationale=fields["rationale"],
                        created_by=actor.actor_id,
                    )
                    body = _view(session, tenant, ctx, project_id, dataset_id, mapping_id)
            except (MappingStoreError, MappingRequestError) as exc:
                replay = (
                    _replay(tenant, ctx, project_id, dataset_id, mapping_id, "reject", key, digest)
                    if _replayable(exc.code)
                    else None
                )
                if replay is None:
                    raise
                return JSONResponse({**replay, "replayed": True})
        except TenantContextError as exc:
            raise _not_found() from exc
        except MappingStoreError as exc:
            raise _refusal(exc.code) from exc
        except MappingRequestError as exc:
            raise HTTPException(status_code=409, detail=exc.code) from exc
        return JSONResponse({**body, "replayed": False})

    return router


def _owned_mapping(
    session: Session,
    tenant: TenantContext,
    project_id: uuid.UUID,
    dataset_id: uuid.UUID,
    mapping_id: uuid.UUID,
) -> None:
    """The mapping must belong to the dataset in the path: a mapping of another dataset is a 404."""
    row = get_dataset_mapping(session, tenant, project_id=project_id, mapping_id=mapping_id)
    if row.dataset_id != dataset_id:
        raise MappingStoreError("mapping_not_found")
