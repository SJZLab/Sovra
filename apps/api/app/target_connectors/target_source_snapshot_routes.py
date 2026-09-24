"""Live `/api/v1/organizations/{organization_id}/projects/{project_id}/
target-connections/{connection_id}/snapshots` routes (FTF-04A07, Section
6.8B/7A.1-7A.3/9/45/65-67/77/81).

Scope (disclosed explicitly, not hidden -- see this task's own
docs/certifications/queue.md submission entry): this router implements
create/get/list for the `TargetSourceSnapshot` record itself. It
deliberately does not implement `/candidates`, `/manifests`, `/evidence`
or `/target-drift` (the task packet's own abbreviated `api_contract`
line) -- no `DiscoveredAgentCandidate`/`AgentManifest`/`ManifestEvidence`
entity exists anywhere in this repository yet (FTF-04A17/FTF-04A19, each a
separate, dependency-gated later task), and this router never itself
contacts GitHub or a local filesystem to *produce* a snapshot (Section
7A.2/7A.3 -- FTF-04A02/FTF-04A06); it only stores/serves the record of one
a real connector already resolved. Declaring any of that as a real,
enforced capability here without a real backing implementation would be
exactly the "placeholder endpoint" Law 11 forbids.

Path shape: nested one level deeper than FTF-04A01's own
`/target-connections/{connection_id}` (a `TargetSourceSnapshot` belongs to
exactly one connection for its entire lifetime, Section 6.8B's own
`target_application_connection_id` field), the same nesting-under-the-
real-owning-parent precedent FTF-1001's own `/model-versions` applied
under `/projects/{project_id}`.

Role gating: Section 45's own literal "organization_admin: connections,
security profiles, cost profiles" sentence is the only sentence naming a
role for this class of resource. This module follows FTF-04A01's own
`TARGET_CONNECTION_ROLES` precedent exactly (`{organization_admin}` alone,
gating every operation including reads) -- see
`target_source_snapshot_crud.py`'s own module docstring for the full
reasoning (a snapshot is real, read-only access into a target repository's
own actual source content).

There is no PATCH/DELETE at all -- Section 6.8B's own "Immutable source
snapshot" rule means there is nothing to edit or remove.
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
from app.target_connectors.target_source_snapshot_crud import (
    ActorInfo,
    TargetApplicationConnectionNotFoundError,
    TargetSourceSnapshotNotFoundError,
    create_target_source_snapshot,
    get_target_source_snapshot,
    list_target_source_snapshots,
)
from app.target_connectors.target_source_snapshot_schema import (
    TargetSourceSnapshot,
    TargetSourceSnapshotValidationError,
)

__all__ = ["TARGET_SNAPSHOT_ROLES", "build_target_source_snapshot_router"]

# Section 45: "organization_admin: connections, security profiles, cost
# profiles" is the only sentence naming a role for this class of resource
# -- the same precedent FTF-04A01's own TARGET_CONNECTION_ROLES applied.
TARGET_SNAPSHOT_ROLES: frozenset[Role] = frozenset({Role.ORGANIZATION_ADMIN})

_COLLECTION_PATH = (
    "/api/v1/organizations/{organization_id}/projects/{project_id}"
    "/target-connections/{connection_id}/snapshots"
)
_ITEM_PATH = _COLLECTION_PATH + "/{snapshot_id}"


def _serialize(snapshot: TargetSourceSnapshot) -> dict[str, object]:
    return {
        "id": str(snapshot.id),
        "organization_id": str(snapshot.organization_id),
        "target_application_connection_id": str(snapshot.target_application_connection_id),
        "commit_sha": snapshot.commit_sha,
        "branch": snapshot.branch,
        "tree_hash": snapshot.tree_hash,
        "captured_at": snapshot.captured_at.isoformat(),
        "capture_method": snapshot.capture_method,
        "source_archive_object_key": snapshot.source_archive_object_key,
        "source_archive_sha256": snapshot.source_archive_sha256,
        "analysis_status": snapshot.analysis_status,
        "supersedes_snapshot_id": (
            str(snapshot.supersedes_snapshot_id) if snapshot.supersedes_snapshot_id else None
        ),
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


def build_target_source_snapshot_router(
    session_factory: Callable[[], Session],
    *,
    principal_resolver: PrincipalResolver = default_principal_resolver,
    role_lookup: RoleLookup = default_role_lookup,
) -> APIRouter:
    """Real FastAPI router for FTF-04A07's create/get/list surface.

    `session_factory`/`principal_resolver`/`role_lookup` follow the exact
    same injection convention every prior router in this chain
    established -- fail-closed defaults until a real session-reader/
    DB-backed Membership lookup is wired in (same disclosed gap FTF-04A01/
    FTF-1001 already carry).
    """
    router = APIRouter()
    role_dependency = Depends(
        require_role(
            TARGET_SNAPSHOT_ROLES, principal_resolver=principal_resolver, role_lookup=role_lookup
        )
    )

    @router.post(_COLLECTION_PATH, status_code=201)
    async def create(
        request: Request,
        organization_id: uuid.UUID,
        project_id: uuid.UUID,
        connection_id: uuid.UUID,
        ctx: AuthorizedContext = role_dependency,
    ) -> JSONResponse:
        del organization_id, project_id
        payload = await _read_json_body(request)
        tenant_context = TenantContext.from_authorized_context(ctx)
        try:
            with tenant_scoped_session_for(session_factory, tenant_context) as session:
                snapshot = create_target_source_snapshot(
                    session,
                    tenant_context,
                    connection_id=connection_id,
                    commit_sha=_require_str(payload, "commit_sha"),
                    tree_hash=_require_str(payload, "tree_hash"),
                    capture_method=_require_str(payload, "capture_method"),
                    branch=_optional_str(payload, "branch"),
                    source_archive_object_key=_optional_str(payload, "source_archive_object_key"),
                    source_archive_sha256=_optional_str(payload, "source_archive_sha256"),
                    supersedes_snapshot_id=_optional_uuid(payload, "supersedes_snapshot_id"),
                    actor=_actor_from(ctx, request),
                )
                body = _serialize(snapshot)
        except (
            TargetApplicationConnectionNotFoundError,
            TargetSourceSnapshotNotFoundError,
            TenantContextError,
        ) as exc:
            raise HTTPException(status_code=404, detail="not_found") from exc
        except (ValueError, TargetSourceSnapshotValidationError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return JSONResponse(body, status_code=201)

    @router.get(_COLLECTION_PATH)
    async def list_all(
        organization_id: uuid.UUID,
        project_id: uuid.UUID,
        connection_id: uuid.UUID,
        ctx: AuthorizedContext = role_dependency,
    ) -> JSONResponse:
        del organization_id, project_id
        tenant_context = TenantContext.from_authorized_context(ctx)
        try:
            with tenant_scoped_session_for(session_factory, tenant_context) as session:
                snapshots = list_target_source_snapshots(session, tenant_context, connection_id)
                body = [_serialize(s) for s in snapshots]
        except (TargetApplicationConnectionNotFoundError, TenantContextError) as exc:
            raise HTTPException(status_code=404, detail="not_found") from exc
        return JSONResponse(body, status_code=200)

    @router.get(_ITEM_PATH)
    async def get_one(
        organization_id: uuid.UUID,
        project_id: uuid.UUID,
        connection_id: uuid.UUID,
        snapshot_id: uuid.UUID,
        ctx: AuthorizedContext = role_dependency,
    ) -> JSONResponse:
        del organization_id, project_id
        tenant_context = TenantContext.from_authorized_context(ctx)
        try:
            with tenant_scoped_session_for(session_factory, tenant_context) as session:
                snapshot = get_target_source_snapshot(
                    session, tenant_context, connection_id, snapshot_id
                )
                body = _serialize(snapshot)
        except (TargetSourceSnapshotNotFoundError, TenantContextError) as exc:
            raise HTTPException(status_code=404, detail="not_found") from exc
        return JSONResponse(body, status_code=200)

    return router
