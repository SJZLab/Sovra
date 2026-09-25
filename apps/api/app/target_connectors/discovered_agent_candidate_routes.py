"""Live `/api/v1/organizations/{organization_id}/projects/{project_id}/
target-connections/{connection_id}/snapshots/{snapshot_id}/candidates`
routes (FTF-04A17, Section 6.8C/7A.4/9/45/65-67/77/81).

Scope (disclosed explicitly, not hidden -- see this task's own
docs/certifications/queue.md submission entry): this router implements
create/get/list (paginated) for the `DiscoveredAgentCandidate` record
itself. It deliberately does not implement `/manifests`, `/evidence` or
`/target-drift` (the task packet's own abbreviated `api_contract` line)
-- no `AgentManifest`/`ManifestEvidence` entity exists anywhere in this
repository yet (FTF-04A19, a separate, dependency-gated later task), and
this router never itself runs any part of the Section 7A.4 source-analysis
pipeline (FTF-04A08-FTF-04A16, none built yet); it only stores/serves the
record of a candidate a real analysis run already produced. It also
implements no selection/confirmation endpoint at all -- FTF-04A18
("Candidate-selection UI -- no auto-selection") is a separate,
dependency-gated later task, and this task's own negative_acceptance
("not ... automatic in-scope selection") means this module must not
invent that transition itself. Declaring any of that as a real, enforced
capability here without a real backing implementation would be exactly
the "placeholder endpoint" Law 11 forbids.

Path shape: nested one level deeper than FTF-04A07's own
`/target-connections/{connection_id}/snapshots/{snapshot_id}` (a
`DiscoveredAgentCandidate` belongs to exactly one snapshot for its entire
lifetime, Section 6.8C's own `target_source_snapshot_id` field), the same
nesting-under-the-real-owning-parent precedent FTF-04A07's own
`/snapshots` applied under `/target-connections/{connection_id}`.

Role gating: Section 45 names no dedicated role sentence for this
specific resource class either -- this module follows FTF-04A01/FTF-04A07's
own `{organization_admin}`-only precedent exactly (see
`discovered_agent_candidate_crud.py`'s own module docstring for the full
reasoning: a candidate's own evidence columns are real extracted content
from a target repository, tied to the same credential-bearing access
chain as the snapshot/connection it descends from).

There is no PATCH/DELETE at all -- Section 6.8C's own candidate record is
a fact about what analysis found, never mutated or removed after being
recorded (see the schema module's own docstring).
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
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
from app.target_connectors.discovered_agent_candidate_crud import (
    DEFAULT_PAGE_LIMIT,
    ActorInfo,
    DiscoveredAgentCandidateDuplicateKeyError,
    DiscoveredAgentCandidateNotFoundError,
    Page,
    TargetApplicationConnectionNotFoundError,
    TargetSourceSnapshotNotFoundError,
    create_discovered_agent_candidate,
    get_discovered_agent_candidate,
    list_discovered_agent_candidates,
)
from app.target_connectors.discovered_agent_candidate_schema import (
    DiscoveredAgentCandidate,
    DiscoveredAgentCandidateValidationError,
)

__all__ = ["DISCOVERED_CANDIDATE_ROLES", "build_discovered_agent_candidate_router"]

# Section 45 names no dedicated role sentence for this resource class --
# the same {organization_admin}-only precedent FTF-04A01/FTF-04A07 already
# applied to this credential-bearing-access-adjacent resource family.
DISCOVERED_CANDIDATE_ROLES: frozenset[Role] = frozenset({Role.ORGANIZATION_ADMIN})

_COLLECTION_PATH = (
    "/api/v1/organizations/{organization_id}/projects/{project_id}"
    "/target-connections/{connection_id}/snapshots/{snapshot_id}/candidates"
)
_ITEM_PATH = _COLLECTION_PATH + "/{candidate_id}"

_EVIDENCE_FIELDS = (
    "entrypoint_evidence_json",
    "model_evidence_json",
    "prompt_evidence_json",
    "tool_evidence_json",
    "test_evidence_json",
    "trace_hook_evidence_json",
    "deployment_evidence_json",
)


def _serialize(candidate: DiscoveredAgentCandidate) -> dict[str, object]:
    body: dict[str, object] = {
        "id": str(candidate.id),
        "organization_id": str(candidate.organization_id),
        "target_source_snapshot_id": str(candidate.target_source_snapshot_id),
        "candidate_key": candidate.candidate_key,
        "display_name": candidate.display_name,
        "framework_or_runtime": candidate.framework_or_runtime,
        "confidence_score": float(candidate.confidence_score),
        "discovery_status": candidate.discovery_status,
        "created_at": candidate.created_at.isoformat(),
    }
    for field_name in _EVIDENCE_FIELDS:
        body[field_name] = getattr(candidate, field_name)
    return body


def _serialize_page(page: Page) -> dict[str, object]:
    return {
        "items": [_serialize(item) for item in page.items],
        "limit": page.limit,
        "offset": page.offset,
        "total": page.total,
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


def _optional_evidence(payload: dict[str, Any], key: str) -> dict[str, object] | None:
    if key not in payload:
        return None
    value = payload[key]
    if value is not None and not isinstance(value, dict):
        raise HTTPException(status_code=400, detail=f"{key} must be a JSON object or null")
    return value


def _require_confidence_score(payload: dict[str, Any]) -> float:
    value = payload.get("confidence_score")
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise HTTPException(
            status_code=400, detail="confidence_score is required and must be a number"
        )
    return float(value)


def build_discovered_agent_candidate_router(
    session_factory: Callable[[], Session],
    *,
    principal_resolver: PrincipalResolver = default_principal_resolver,
    role_lookup: RoleLookup = default_role_lookup,
) -> APIRouter:
    """Real FastAPI router for FTF-04A17's create/get/list(paginated)
    surface.

    `session_factory`/`principal_resolver`/`role_lookup` follow the exact
    same injection convention every prior router in this chain
    established -- fail-closed defaults until a real session-reader/
    DB-backed Membership lookup is wired in (same disclosed gap FTF-04A01/
    FTF-04A07 already carry).
    """
    router = APIRouter()
    role_dependency = Depends(
        require_role(
            DISCOVERED_CANDIDATE_ROLES,
            principal_resolver=principal_resolver,
            role_lookup=role_lookup,
        )
    )

    @router.post(_COLLECTION_PATH, status_code=201)
    async def create(
        request: Request,
        organization_id: uuid.UUID,
        project_id: uuid.UUID,
        connection_id: uuid.UUID,
        snapshot_id: uuid.UUID,
        ctx: AuthorizedContext = role_dependency,
    ) -> JSONResponse:
        del organization_id, project_id
        payload = await _read_json_body(request)
        tenant_context = TenantContext.from_authorized_context(ctx)
        try:
            evidence_kwargs = {
                field_name: _optional_evidence(payload, field_name)
                for field_name in _EVIDENCE_FIELDS
            }
            with tenant_scoped_session_for(session_factory, tenant_context) as session:
                candidate = create_discovered_agent_candidate(
                    session,
                    tenant_context,
                    connection_id=connection_id,
                    snapshot_id=snapshot_id,
                    candidate_key=_require_str(payload, "candidate_key"),
                    display_name=_require_str(payload, "display_name"),
                    confidence_score=_require_confidence_score(payload),
                    framework_or_runtime=_optional_str(payload, "framework_or_runtime"),
                    actor=_actor_from(ctx, request),
                    **evidence_kwargs,
                )
                body = _serialize(candidate)
        except (
            TargetApplicationConnectionNotFoundError,
            TargetSourceSnapshotNotFoundError,
            TenantContextError,
        ) as exc:
            raise HTTPException(status_code=404, detail="not_found") from exc
        except DiscoveredAgentCandidateDuplicateKeyError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except (ValueError, DiscoveredAgentCandidateValidationError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return JSONResponse(body, status_code=201)

    @router.get(_COLLECTION_PATH)
    async def list_all(
        organization_id: uuid.UUID,
        project_id: uuid.UUID,
        connection_id: uuid.UUID,
        snapshot_id: uuid.UUID,
        ctx: AuthorizedContext = role_dependency,
        limit: int = Query(default=DEFAULT_PAGE_LIMIT, ge=1),
        offset: int = Query(default=0, ge=0),
    ) -> JSONResponse:
        del organization_id, project_id
        tenant_context = TenantContext.from_authorized_context(ctx)
        try:
            with tenant_scoped_session_for(session_factory, tenant_context) as session:
                page = list_discovered_agent_candidates(
                    session, tenant_context, connection_id, snapshot_id, limit=limit, offset=offset
                )
                body = _serialize_page(page)
        except (
            TargetApplicationConnectionNotFoundError,
            TargetSourceSnapshotNotFoundError,
            TenantContextError,
        ) as exc:
            raise HTTPException(status_code=404, detail="not_found") from exc
        return JSONResponse(body, status_code=200)

    @router.get(_ITEM_PATH)
    async def get_one(
        organization_id: uuid.UUID,
        project_id: uuid.UUID,
        connection_id: uuid.UUID,
        snapshot_id: uuid.UUID,
        candidate_id: uuid.UUID,
        ctx: AuthorizedContext = role_dependency,
    ) -> JSONResponse:
        del organization_id, project_id
        tenant_context = TenantContext.from_authorized_context(ctx)
        try:
            with tenant_scoped_session_for(session_factory, tenant_context) as session:
                candidate = get_discovered_agent_candidate(
                    session, tenant_context, connection_id, snapshot_id, candidate_id
                )
                body = _serialize(candidate)
        except (
            TargetApplicationConnectionNotFoundError,
            TargetSourceSnapshotNotFoundError,
            DiscoveredAgentCandidateNotFoundError,
            TenantContextError,
        ) as exc:
            raise HTTPException(status_code=404, detail="not_found") from exc
        return JSONResponse(body, status_code=200)

    return router
