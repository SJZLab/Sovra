"""Live `/api/v1/organizations/{organization_id}/projects/{project_id}/
target-connections/{connection_id}/snapshots/{snapshot_id}/candidates/
{candidate_id}/select` route (FTF-04A18, Section 6.8C/6.24/7A.5/45/
65-67/77/81).

Scope (disclosed explicitly, not hidden -- see this task's own
docs/certifications/queue.md submission entry): this router implements
exactly one real mutating action -- explicit candidate selection,
recorded as a real, persisted `AuditEvent` row
(`discovered_agent_candidate_selection.py`'s own docstring explains why
that is this task's own real "persisted audit", and why no new schema is
added). It deliberately does not implement `/manifests`, `/evidence` or
`/target-drift` (the task packet's own abbreviated `api_contract` line,
already disclaimed by FTF-04A17's own sibling router) and it never
mutates `DiscoveredAgentCandidate.discovery_status` -- that column's own
real database grant (SELECT/INSERT only, no UPDATE) makes any such
mutation structurally impossible from this process, not merely
undeclared.

Path shape: one level deeper than FTF-04A17's own `/candidates/
{candidate_id}` item path (an action on one specific, already-identified
candidate), the same "action verb nested under the real resource it
acts on" precedent this chain's sibling `/validate` (FTF-0203) route
already established for an action that is not itself a CRUD verb.

Role gating: follows FTF-04A01/FTF-04A07/FTF-04A17's own
`{organization_admin}`-only precedent exactly -- selecting a target agent
candidate is as credential-chain-adjacent as creating/reading one.
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
from app.db.tenant_context import TenantContext, tenant_scoped_session_for
from app.target_connectors.discovered_agent_candidate_selection import (
    ActorInfo,
    DiscoveredAgentCandidateNotFoundError,
    StaleSnapshotCandidateError,
    TargetApplicationConnectionNotFoundError,
    TargetSourceSnapshotNotFoundError,
    select_discovered_agent_candidate,
)

__all__ = ["CANDIDATE_SELECTION_ROLES", "build_discovered_agent_candidate_selection_router"]

# Section 45 names no dedicated role sentence for this resource class --
# the same {organization_admin}-only precedent FTF-04A01/FTF-04A07/
# FTF-04A17 already applied to this credential-bearing-access-adjacent
# resource family.
CANDIDATE_SELECTION_ROLES: frozenset[Role] = frozenset({Role.ORGANIZATION_ADMIN})

_SELECT_PATH = (
    "/api/v1/organizations/{organization_id}/projects/{project_id}"
    "/target-connections/{connection_id}/snapshots/{snapshot_id}"
    "/candidates/{candidate_id}/select"
)


def _actor_from(ctx: AuthorizedContext, request: Request) -> ActorInfo:
    del request
    return ActorInfo(
        actor_type="user",
        actor_id=ctx.principal.subject,
        request_id=uuid.uuid4().hex,
    )


def build_discovered_agent_candidate_selection_router(
    session_factory: Callable[[], Session],
    *,
    principal_resolver: PrincipalResolver = default_principal_resolver,
    role_lookup: RoleLookup = default_role_lookup,
) -> APIRouter:
    """Real FastAPI router for FTF-04A18's own explicit-selection surface.

    `session_factory`/`principal_resolver`/`role_lookup` follow the exact
    same injection convention every prior router in this chain
    established -- fail-closed defaults until a real session-reader/
    DB-backed Membership lookup is wired in (same disclosed gap FTF-04A01/
    FTF-04A07/FTF-04A17 already carry).
    """
    router = APIRouter()
    role_dependency = Depends(
        require_role(
            CANDIDATE_SELECTION_ROLES,
            principal_resolver=principal_resolver,
            role_lookup=role_lookup,
        )
    )

    @router.post(_SELECT_PATH, status_code=201)
    async def select(
        request: Request,
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
                record = select_discovered_agent_candidate(
                    session,
                    tenant_context,
                    connection_id,
                    snapshot_id,
                    candidate_id,
                    actor=_actor_from(ctx, request),
                )
                body = {
                    "candidate_id": str(record.candidate_id),
                    "target_source_snapshot_id": str(record.target_source_snapshot_id),
                    "selected_by": record.actor_id,
                    "selected_at": record.selected_at.isoformat(),
                }
        except (
            TargetApplicationConnectionNotFoundError,
            TargetSourceSnapshotNotFoundError,
            DiscoveredAgentCandidateNotFoundError,
            TenantContextError,
        ) as exc:
            raise HTTPException(status_code=404, detail="not_found") from exc
        except StaleSnapshotCandidateError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return JSONResponse(body, status_code=201)

    return router
