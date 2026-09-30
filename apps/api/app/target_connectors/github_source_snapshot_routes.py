"""Live `/api/v1/organizations/{organization_id}/projects/{project_id}/
target-connections/{connection_id}/github-source-snapshot` route
(FTF-04A05, Section 6.8B/7A.1-7A.2/9/45/65-67/77/81).

This is the real, GitHub-backed sibling of FTF-04A07's own
`target_source_snapshot_routes.py` `POST .../snapshots` route:
FTF-04A07's own route stores whatever `commit_sha`/`tree_hash` the
caller's own request body asserts, disclosed there as "it only
stores/serves the record of one a real connector already resolved." This
route *is* that real connector composition for the GitHub case -- the
server itself re-derives `commit_sha`/`tree_hash` from a real GitHub REST
API v3 call (`github_source_snapshot_crud.capture_and_create_github_source_snapshot`),
never trusting a client-supplied value for either field.

The credential is a request-body value, never a query parameter, and is
never persisted, echoed back, or written into the audit events this
route's own CRUD layer writes -- identical discipline to
`github_branch_ref_routes.py` (FTF-04A04).

Role gating: the same `TARGET_SNAPSHOT_ROLES`-shaped role set
`target_source_snapshot_routes.py` already established for this class of
resource (`{organization_admin}` alone, gating every operation).
"""

from __future__ import annotations

import uuid
from collections.abc import Callable

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from packages.security.url_policy import ConnectionPolicy
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
from app.target_connectors.github_app_token_read_only_secret_integration import (
    DEFAULT_GITHUB_API_BASE_URL,
    GitHubConnectionError,
    GitHubCredentialRevokedError,
    GitHubRateLimitExceededError,
    GitHubRepositoryAccessDeniedError,
)
from app.target_connectors.github_source_snapshot_crud import (
    ActorInfo,
    ProjectNotFoundError,
    TargetApplicationConnectionNotFoundError,
    TargetSourceSnapshotNotFoundError,
    capture_and_create_github_source_snapshot,
)
from app.target_connectors.github_source_snapshot_resolver import (
    IncompleteSourceTreeError,
    InvalidRefError,
    InvalidRepositoryIdentityError,
    UnsafeSourceTreeEntryError,
)
from app.target_connectors.target_source_snapshot_schema import (
    TargetSourceSnapshot,
    TargetSourceSnapshotValidationError,
)

__all__ = ["GITHUB_SOURCE_SNAPSHOT_ROLES", "build_github_source_snapshot_router"]

GITHUB_SOURCE_SNAPSHOT_ROLES: frozenset[Role] = frozenset({Role.ORGANIZATION_ADMIN})

_PATH = (
    "/api/v1/organizations/{organization_id}/projects/{project_id}"
    "/target-connections/{connection_id}/github-source-snapshot"
)


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


async def _read_json_body(request: Request) -> dict[str, object]:
    try:
        payload = await request.json()
    except Exception as exc:  # noqa: BLE001 - any unparsable body is the same outcome
        raise HTTPException(status_code=400, detail="request body must be valid JSON") from exc
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="request body must be a JSON object")
    return payload


def _require_str(payload: dict[str, object], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise HTTPException(status_code=400, detail=f"{key} is required and must be a string")
    return value


def _optional_uuid(payload: dict[str, object], key: str) -> uuid.UUID | None:
    value = payload.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise HTTPException(status_code=400, detail=f"{key} must be a string or null")
    try:
        return uuid.UUID(value)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"{key} must be a valid UUID") from exc


def _default_github_api_base_url() -> str:
    return DEFAULT_GITHUB_API_BASE_URL


def build_github_source_snapshot_router(
    session_factory: Callable[[], Session],
    *,
    principal_resolver: PrincipalResolver = default_principal_resolver,
    role_lookup: RoleLookup = default_role_lookup,
    github_api_base_url_factory: Callable[[], str] = _default_github_api_base_url,
    github_connection_policy_factory: Callable[[], ConnectionPolicy | None] = lambda: None,
) -> APIRouter:
    """Real FastAPI router for FTF-04A05's own GitHub-backed capture
    route. `github_api_base_url_factory`/`github_connection_policy_factory`
    default to the real GitHub API and are called fresh on every request;
    a test/CI caller overrides them to point at a real local fixture
    server -- the same seam `github_branch_ref_routes.py` already
    established."""
    router = APIRouter()
    dependency = Depends(
        require_role(
            GITHUB_SOURCE_SNAPSHOT_ROLES,
            principal_resolver=principal_resolver,
            role_lookup=role_lookup,
        )
    )

    @router.post(_PATH, status_code=201)
    async def capture(
        request: Request,
        organization_id: uuid.UUID,
        project_id: uuid.UUID,
        connection_id: uuid.UUID,
        ctx: AuthorizedContext = dependency,
    ) -> JSONResponse:
        del organization_id
        payload = await _read_json_body(request)
        credential = _require_str(payload, "credential")
        repository_identity = _require_str(payload, "repository_identity")
        ref = _require_str(payload, "ref")
        supersedes_snapshot_id = _optional_uuid(payload, "supersedes_snapshot_id")
        tenant_context = TenantContext.from_authorized_context(ctx)
        actor = _actor_from(ctx, request)
        try:
            with tenant_scoped_session_for(session_factory, tenant_context) as session:
                snapshot = capture_and_create_github_source_snapshot(
                    session,
                    tenant_context,
                    project_id,
                    connection_id,
                    credential=credential,
                    repository_identity=repository_identity,
                    ref=ref,
                    actor=actor,
                    supersedes_snapshot_id=supersedes_snapshot_id,
                    base_url=github_api_base_url_factory(),
                    policy=github_connection_policy_factory(),
                )
                body = _serialize(snapshot)
        except (
            ProjectNotFoundError,
            TargetApplicationConnectionNotFoundError,
            TargetSourceSnapshotNotFoundError,
            TenantContextError,
        ) as exc:
            raise HTTPException(status_code=404, detail="not_found") from exc
        except (InvalidRepositoryIdentityError, InvalidRefError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except TargetSourceSnapshotValidationError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except GitHubCredentialRevokedError as exc:
            raise HTTPException(status_code=401, detail=str(exc)) from exc
        except GitHubRateLimitExceededError as exc:
            raise HTTPException(status_code=429, detail=str(exc)) from exc
        except GitHubRepositoryAccessDeniedError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except (IncompleteSourceTreeError, UnsafeSourceTreeEntryError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except GitHubConnectionError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        return JSONResponse(body, status_code=201)

    return router
