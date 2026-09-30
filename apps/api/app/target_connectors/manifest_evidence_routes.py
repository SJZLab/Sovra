"""Live `/api/v1/organizations/{organization_id}/projects/{project_id}/
manifests/{manifest_id}/evidence` routes (FTF-04A20, Section 6.8E/
7A.6-7A.7/45/65-66/77/81).

Scope (disclosed explicitly, not hidden -- see this task's own
docs/certifications/queue.md submission entry): this router implements
create/get/list for `ManifestEvidence` alone. There is no update/delete
endpoint at all -- a `ManifestEvidence` row is immutable once written (see
`manifest_evidence_schema.py`'s own docstring, "Immutability").

Path shape: nested under the parent manifest's own real path
(`/manifests/{manifest_id}/evidence`) -- Section 6.8E's own literal
`agent_manifest_id` field names the manifest as this resource's one real
parent, the same "resource's real owning parent, read literally off its
own field list" precedent FTF-04A19's own `/manifests` and FTF-1001's own
`/model-versions` both already applied.

Role gating: reused directly from `agent_manifest_routes.py`'s own
`AGENT_MANIFEST_READ_ROLES`/`AGENT_MANIFEST_WRITE_ROLES` -- see
`manifest_evidence_crud.py`'s own module docstring for the full reasoning.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

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
from app.target_connectors.agent_manifest_crud import AgentManifestNotFoundError
from app.target_connectors.agent_manifest_routes import (
    AGENT_MANIFEST_READ_ROLES,
    AGENT_MANIFEST_WRITE_ROLES,
)
from app.target_connectors.manifest_evidence_crud import (
    ActorInfo,
    CrossSnapshotEvidenceError,
    ManifestEvidenceNotFoundError,
    create_manifest_evidence,
    get_manifest_evidence,
    list_manifest_evidence,
)
from app.target_connectors.manifest_evidence_schema import (
    ManifestEvidence,
    ManifestEvidenceValidationError,
)

__all__ = ["build_manifest_evidence_router"]

_COLLECTION_PATH = (
    "/api/v1/organizations/{organization_id}/projects/{project_id}/manifests/{manifest_id}/evidence"
)
_ITEM_PATH = _COLLECTION_PATH + "/{evidence_id}"

_REQUIRED_STRING_FIELDS = ("field_path", "evidence_type")
_OPTIONAL_STRING_FIELDS = ("repository_commit_sha", "file_path", "symbol", "content_hash")
_OPTIONAL_INT_FIELDS = ("line_start", "line_end")
_KNOWN_FIELDS = frozenset(
    _REQUIRED_STRING_FIELDS
    + _OPTIONAL_STRING_FIELDS
    + _OPTIONAL_INT_FIELDS
    + ("evidence_json", "confidence_score")
)


def _serialize(evidence: ManifestEvidence) -> dict[str, object]:
    return {
        "id": str(evidence.id),
        "organization_id": str(evidence.organization_id),
        "agent_manifest_id": str(evidence.agent_manifest_id),
        "field_path": evidence.field_path,
        "evidence_type": evidence.evidence_type,
        "repository_commit_sha": evidence.repository_commit_sha,
        "file_path": evidence.file_path,
        "line_start": evidence.line_start,
        "line_end": evidence.line_end,
        "symbol": evidence.symbol,
        "content_hash": evidence.content_hash,
        "confidence_score": float(evidence.confidence_score)
        if evidence.confidence_score is not None
        else None,
        "evidence_json": evidence.evidence_json,
        "created_at": evidence.created_at.isoformat(),
    }


def _actor_from(ctx: AuthorizedContext) -> ActorInfo:
    return ActorInfo(actor_type="user", actor_id=ctx.principal.subject, request_id=uuid.uuid4().hex)


async def _read_json_body(request: Request) -> dict[str, Any]:
    try:
        payload = await request.json()
    except Exception as exc:  # noqa: BLE001 - any unparsable body is the same outcome
        raise HTTPException(status_code=400, detail="request body must be valid JSON") from exc
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="request body must be a JSON object")
    return payload


@dataclass(frozen=True)
class _CreatePayload:
    field_path: str
    evidence_type: str
    evidence_json: dict[str, object]
    repository_commit_sha: str | None
    file_path: str | None
    line_start: int | None
    line_end: int | None
    symbol: str | None
    content_hash: str | None
    confidence_score: int | float | None


def _create_payload_from(payload: dict[str, Any]) -> _CreatePayload:
    unknown = set(payload) - _KNOWN_FIELDS
    if unknown:
        raise HTTPException(
            status_code=400, detail=f"unknown ManifestEvidence field(s): {sorted(unknown)}"
        )
    for key in _REQUIRED_STRING_FIELDS:
        if not isinstance(payload.get(key), str):
            raise HTTPException(status_code=400, detail=f"{key} is required and must be a string")
    if not isinstance(payload.get("evidence_json"), dict):
        raise HTTPException(
            status_code=400, detail="evidence_json is required and must be a JSON object"
        )
    for key in _OPTIONAL_STRING_FIELDS:
        if key in payload and payload[key] is not None and not isinstance(payload[key], str):
            raise HTTPException(status_code=400, detail=f"{key} must be a string or null")
    for key in _OPTIONAL_INT_FIELDS:
        if key in payload and payload[key] is not None and not isinstance(payload[key], int):
            raise HTTPException(status_code=400, detail=f"{key} must be an integer or null")
    confidence_score = payload.get("confidence_score")
    if confidence_score is not None and not isinstance(confidence_score, int | float):
        raise HTTPException(status_code=400, detail="confidence_score must be a number or null")

    return _CreatePayload(
        field_path=payload["field_path"],
        evidence_type=payload["evidence_type"],
        evidence_json=payload["evidence_json"],
        repository_commit_sha=payload.get("repository_commit_sha"),
        file_path=payload.get("file_path"),
        line_start=payload.get("line_start"),
        line_end=payload.get("line_end"),
        symbol=payload.get("symbol"),
        content_hash=payload.get("content_hash"),
        confidence_score=confidence_score,
    )


def build_manifest_evidence_router(
    session_factory: Callable[[], Session],
    *,
    principal_resolver: PrincipalResolver = default_principal_resolver,
    role_lookup: RoleLookup = default_role_lookup,
) -> APIRouter:
    """Real FastAPI router for FTF-04A20's create/get/list surface.

    `session_factory`/`principal_resolver`/`role_lookup` follow the exact
    same injection convention every prior router in this chain
    established -- fail-closed defaults until a real session-reader/
    DB-backed Membership lookup is wired in (same disclosed gap FTF-04A19
    and every prior router in this chain already carry).
    """
    router = APIRouter()
    read_dependency = Depends(
        require_role(
            AGENT_MANIFEST_READ_ROLES,
            principal_resolver=principal_resolver,
            role_lookup=role_lookup,
        )
    )
    write_dependency = Depends(
        require_role(
            AGENT_MANIFEST_WRITE_ROLES,
            principal_resolver=principal_resolver,
            role_lookup=role_lookup,
        )
    )

    @router.post(_COLLECTION_PATH, status_code=201)
    async def create(
        request: Request,
        organization_id: uuid.UUID,
        project_id: uuid.UUID,
        manifest_id: uuid.UUID,
        ctx: AuthorizedContext = write_dependency,
    ) -> JSONResponse:
        del organization_id
        payload = await _read_json_body(request)
        create_payload = _create_payload_from(payload)
        tenant_context = TenantContext.from_authorized_context(ctx)
        try:
            with tenant_scoped_session_for(session_factory, tenant_context) as session:
                evidence = create_manifest_evidence(
                    session,
                    tenant_context,
                    project_id=project_id,
                    manifest_id=manifest_id,
                    actor=_actor_from(ctx),
                    field_path=create_payload.field_path,
                    evidence_type=create_payload.evidence_type,
                    evidence_json=create_payload.evidence_json,
                    repository_commit_sha=create_payload.repository_commit_sha,
                    file_path=create_payload.file_path,
                    line_start=create_payload.line_start,
                    line_end=create_payload.line_end,
                    symbol=create_payload.symbol,
                    content_hash=create_payload.content_hash,
                    confidence_score=create_payload.confidence_score,
                )
                body = _serialize(evidence)
        except (AgentManifestNotFoundError, TenantContextError) as exc:
            raise HTTPException(status_code=404, detail="not_found") from exc
        except CrossSnapshotEvidenceError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except (ValueError, ManifestEvidenceValidationError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return JSONResponse(body, status_code=201)

    @router.get(_COLLECTION_PATH)
    async def list_all(
        organization_id: uuid.UUID,
        project_id: uuid.UUID,
        manifest_id: uuid.UUID,
        ctx: AuthorizedContext = read_dependency,
    ) -> JSONResponse:
        del organization_id
        tenant_context = TenantContext.from_authorized_context(ctx)
        try:
            with tenant_scoped_session_for(session_factory, tenant_context) as session:
                items = list_manifest_evidence(session, tenant_context, project_id, manifest_id)
                body = [_serialize(item) for item in items]
        except (AgentManifestNotFoundError, TenantContextError) as exc:
            raise HTTPException(status_code=404, detail="not_found") from exc
        return JSONResponse(body, status_code=200)

    @router.get(_ITEM_PATH)
    async def get_one(
        organization_id: uuid.UUID,
        project_id: uuid.UUID,
        manifest_id: uuid.UUID,
        evidence_id: uuid.UUID,
        ctx: AuthorizedContext = read_dependency,
    ) -> JSONResponse:
        del organization_id
        tenant_context = TenantContext.from_authorized_context(ctx)
        try:
            with tenant_scoped_session_for(session_factory, tenant_context) as session:
                evidence = get_manifest_evidence(
                    session, tenant_context, project_id, manifest_id, evidence_id
                )
                body = _serialize(evidence)
        except (
            AgentManifestNotFoundError,
            ManifestEvidenceNotFoundError,
            TenantContextError,
        ) as exc:
            raise HTTPException(status_code=404, detail="not_found") from exc
        return JSONResponse(body, status_code=200)

    return router
