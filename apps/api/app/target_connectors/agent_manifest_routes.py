"""Live `/api/v1/organizations/{organization_id}/projects/{project_id}/
manifests` routes (FTF-04A19, Section 6.8D/7A.6-7A.7/45/65-66/77/81).

Scope (disclosed explicitly, not hidden -- see this task's own
docs/certifications/queue.md submission entry): this router implements
create/get/list/update-content/submit-for-review/lock for the
`AgentManifest` record itself. It deliberately does not implement
`/evidence` (Section 6.8E's own `ManifestEvidence`, FTF-04A20) or
`/target-drift` (FTF-04A27's own repository-sync/drift-detector job) --
neither entity/capability exists anywhere in this repository yet.
Declaring either as a real, enforced endpoint here without a real backing
implementation would be exactly the "placeholder endpoint" Law 11
forbids.

Path shape: directly under `/projects/{project_id}` (Section 6.8D's own
literal `project_id` field -- a manifest's real, primary parent is the
project, not the connection/snapshot it happens to reference as evidence;
see `agent_manifest_schema.py`'s own docstring), the same "resource's real
owning parent, read literally off its own field list" precedent
FTF-1001's own `/model-versions` and FTF-04A01's own `/target-connections`
both already applied -- not nested three levels deep under
`/target-connections/{connection_id}/snapshots/{snapshot_id}` the way
FTF-04A17's own `/candidates` is (a `DiscoveredAgentCandidate` has no
`project_id` field of its own at all, so nesting under its one real parent
chain was that task's own only choice).

Role gating: see `agent_manifest_crud.py`'s own module docstring for the
full Section 45 reasoning -- reads are open to every role Section 45
names, writes (create/update/submit-for-review/lock) are restricted to
`{workspace_admin, organization_admin}`.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from types import EllipsisType
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
from app.target_connectors.agent_manifest_crud import (
    ActorInfo,
    AgentManifestIncompleteError,
    AgentManifestLockedError,
    AgentManifestNotFoundError,
    ProjectNotFoundError,
    SnapshotNotFoundError,
    SnapshotProjectMismatchError,
    create_agent_manifest,
    get_agent_manifest,
    list_agent_manifests,
    lock_agent_manifest,
    submit_for_review,
    update_agent_manifest_content,
)
from app.target_connectors.agent_manifest_schema import (
    CONTENT_FIELDS,
    AgentManifest,
    AgentManifestValidationError,
)

__all__ = [
    "AGENT_MANIFEST_READ_ROLES",
    "AGENT_MANIFEST_WRITE_ROLES",
    "build_agent_manifest_router",
]

# Section 45: "workspace_admin: project administration" is the closest
# real sentence for this class of resource -- see `agent_manifest_crud.py`'s
# own module docstring for the full reasoning.
AGENT_MANIFEST_WRITE_ROLES: frozenset[Role] = frozenset(
    {Role.WORKSPACE_ADMIN, Role.ORGANIZATION_ADMIN}
)
AGENT_MANIFEST_READ_ROLES: frozenset[Role] = frozenset(
    {
        Role.ORGANIZATION_ADMIN,
        Role.WORKSPACE_ADMIN,
        Role.ML_ENGINEER,
        Role.EVALUATOR,
        Role.APPROVER,
        Role.VIEWER,
    }
)

# Matches agent_manifest_crud.py's own _MUTABLE_CONTENT_FIELDS exactly --
# excludes target_source_snapshot_id (fixed at creation) and agent_name (a
# plain string, always its own explicit param, never folded into this
# JSON-field set).
_MUTABLE_CONTENT_FIELDS = tuple(
    f for f in CONTENT_FIELDS if f not in ("target_source_snapshot_id", "agent_name")
)

_COLLECTION_PATH = "/api/v1/organizations/{organization_id}/projects/{project_id}/manifests"
_ITEM_PATH = _COLLECTION_PATH + "/{manifest_id}"
_SUBMIT_FOR_REVIEW_PATH = _ITEM_PATH + "/submit-for-review"
_LOCK_PATH = _ITEM_PATH + "/lock"


def _serialize(manifest: AgentManifest) -> dict[str, object]:
    body: dict[str, object] = {
        "id": str(manifest.id),
        "organization_id": str(manifest.organization_id),
        "project_id": str(manifest.project_id),
        "target_source_snapshot_id": str(manifest.target_source_snapshot_id),
        "version": manifest.version,
        "status": manifest.status,
        "agent_name": manifest.agent_name,
        "manifest_sha256": manifest.manifest_sha256,
        "created_by": manifest.created_by,
        "approved_by": manifest.approved_by,
        "approved_at": manifest.approved_at.isoformat() if manifest.approved_at else None,
        "created_at": manifest.created_at.isoformat(),
    }
    for field in _MUTABLE_CONTENT_FIELDS:
        body[field] = getattr(manifest, field)
    return body


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


async def _read_json_body_or_empty(request: Request) -> dict[str, Any]:
    body = await request.body()
    if not body:
        return {}
    return await _read_json_body(request)


def _require_uuid(payload: dict[str, Any], key: str) -> uuid.UUID:
    value = payload.get(key)
    if not isinstance(value, str):
        raise HTTPException(status_code=400, detail=f"{key} is required and must be a string")
    try:
        return uuid.UUID(value)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"{key} must be a valid UUID") from exc


def _optional_str(payload: dict[str, Any], key: str) -> str | None | EllipsisType:
    """Returns `...` (distinct from `None`) when `key` is absent entirely
    -- callers use this to tell "not supplied, leave unchanged" apart from
    "explicitly set to null" (see `update_agent_manifest_content`)."""
    if key not in payload:
        return ...
    value = payload[key]
    if value is not None and not isinstance(value, str):
        raise HTTPException(status_code=400, detail=f"{key} must be a string or null")
    return value


def _content_from(payload: dict[str, Any]) -> dict[str, object]:
    # agent_name and target_source_snapshot_id are real top-level fields
    # this router consumes separately (agent_name via _optional_str,
    # target_source_snapshot_id via _require_uuid at create time only) --
    # neither is an "unknown field" here even though neither is in
    # _MUTABLE_CONTENT_FIELDS.
    unknown = (
        set(payload)
        - set(_MUTABLE_CONTENT_FIELDS)
        - {
            "agent_name",
            "target_source_snapshot_id",
        }
    )
    if unknown:
        raise HTTPException(
            status_code=400, detail=f"unknown AgentManifest field(s): {sorted(unknown)}"
        )
    content: dict[str, object] = {}
    for field in _MUTABLE_CONTENT_FIELDS:
        if field in payload:
            value = payload[field]
            if value is not None and not isinstance(value, dict):
                raise HTTPException(
                    status_code=400, detail=f"{field} must be a JSON object or null"
                )
            content[field] = value
    return content


def build_agent_manifest_router(
    session_factory: Callable[[], Session],
    *,
    principal_resolver: PrincipalResolver = default_principal_resolver,
    role_lookup: RoleLookup = default_role_lookup,
) -> APIRouter:
    """Real FastAPI router for FTF-04A19's create/get/list/update-content/
    submit-for-review/lock surface.

    `session_factory`/`principal_resolver`/`role_lookup` follow the exact
    same injection convention every prior router in this chain
    established -- fail-closed defaults until a real session-reader/
    DB-backed Membership lookup is wired in (same disclosed gap FTF-0103/
    FTF-0106/FTF-0201/FTF-0301/FTF-0401/FTF-0404/FTF-1001/FTF-04A01/
    FTF-04A07 already carry).
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
        ctx: AuthorizedContext = write_dependency,
    ) -> JSONResponse:
        del organization_id
        payload = await _read_json_body(request)
        agent_name = _optional_str(payload, "agent_name")
        tenant_context = TenantContext.from_authorized_context(ctx)
        try:
            with tenant_scoped_session_for(session_factory, tenant_context) as session:
                manifest = create_agent_manifest(
                    session,
                    tenant_context,
                    project_id=project_id,
                    target_source_snapshot_id=_require_uuid(payload, "target_source_snapshot_id"),
                    agent_name=None if agent_name is ... else agent_name,
                    content=_content_from(payload),
                    actor=_actor_from(ctx, request),
                )
                body = _serialize(manifest)
        except (ProjectNotFoundError, SnapshotNotFoundError, TenantContextError) as exc:
            raise HTTPException(status_code=404, detail="not_found") from exc
        except SnapshotProjectMismatchError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except (ValueError, AgentManifestValidationError) as exc:
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
        try:
            with tenant_scoped_session_for(session_factory, tenant_context) as session:
                manifests = list_agent_manifests(session, tenant_context, project_id)
                body = [_serialize(m) for m in manifests]
        except (ProjectNotFoundError, TenantContextError) as exc:
            raise HTTPException(status_code=404, detail="not_found") from exc
        return JSONResponse(body, status_code=200)

    @router.get(_ITEM_PATH)
    async def get_one(
        organization_id: uuid.UUID,
        project_id: uuid.UUID,
        manifest_id: uuid.UUID,
        ctx: AuthorizedContext = read_dependency,
    ) -> JSONResponse:
        del organization_id
        tenant_context = TenantContext.from_authorized_context(ctx)
        try:
            with tenant_scoped_session_for(session_factory, tenant_context) as session:
                manifest = get_agent_manifest(session, tenant_context, project_id, manifest_id)
                body = _serialize(manifest)
        except (AgentManifestNotFoundError, TenantContextError) as exc:
            raise HTTPException(status_code=404, detail="not_found") from exc
        return JSONResponse(body, status_code=200)

    @router.patch(_ITEM_PATH)
    async def update_content(
        request: Request,
        organization_id: uuid.UUID,
        project_id: uuid.UUID,
        manifest_id: uuid.UUID,
        ctx: AuthorizedContext = write_dependency,
    ) -> JSONResponse:
        del organization_id
        payload = await _read_json_body(request)
        agent_name = _optional_str(payload, "agent_name")
        tenant_context = TenantContext.from_authorized_context(ctx)
        try:
            with tenant_scoped_session_for(session_factory, tenant_context) as session:
                manifest = update_agent_manifest_content(
                    session,
                    tenant_context,
                    project_id=project_id,
                    manifest_id=manifest_id,
                    agent_name=agent_name,
                    content=_content_from(payload),
                    actor=_actor_from(ctx, request),
                )
                body = _serialize(manifest)
        except (AgentManifestNotFoundError, TenantContextError) as exc:
            raise HTTPException(status_code=404, detail="not_found") from exc
        except AgentManifestLockedError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except (ValueError, AgentManifestValidationError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return JSONResponse(body, status_code=200)

    @router.post(_SUBMIT_FOR_REVIEW_PATH)
    async def submit_for_review_endpoint(
        request: Request,
        organization_id: uuid.UUID,
        project_id: uuid.UUID,
        manifest_id: uuid.UUID,
        ctx: AuthorizedContext = write_dependency,
    ) -> JSONResponse:
        del organization_id
        await _read_json_body_or_empty(request)
        tenant_context = TenantContext.from_authorized_context(ctx)
        try:
            with tenant_scoped_session_for(session_factory, tenant_context) as session:
                manifest = submit_for_review(
                    session,
                    tenant_context,
                    project_id=project_id,
                    manifest_id=manifest_id,
                    actor=_actor_from(ctx, request),
                )
                body = _serialize(manifest)
        except (AgentManifestNotFoundError, TenantContextError) as exc:
            raise HTTPException(status_code=404, detail="not_found") from exc
        except (ValueError, AgentManifestValidationError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return JSONResponse(body, status_code=200)

    @router.post(_LOCK_PATH)
    async def lock_endpoint(
        request: Request,
        organization_id: uuid.UUID,
        project_id: uuid.UUID,
        manifest_id: uuid.UUID,
        ctx: AuthorizedContext = write_dependency,
    ) -> JSONResponse:
        del organization_id
        await _read_json_body_or_empty(request)
        tenant_context = TenantContext.from_authorized_context(ctx)
        try:
            with tenant_scoped_session_for(session_factory, tenant_context) as session:
                manifest = lock_agent_manifest(
                    session,
                    tenant_context,
                    project_id=project_id,
                    manifest_id=manifest_id,
                    actor=_actor_from(ctx, request),
                )
                body = _serialize(manifest)
        except (AgentManifestNotFoundError, TenantContextError) as exc:
            raise HTTPException(status_code=404, detail="not_found") from exc
        except (AgentManifestIncompleteError, AgentManifestValidationError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return JSONResponse(body, status_code=200)

    return router
