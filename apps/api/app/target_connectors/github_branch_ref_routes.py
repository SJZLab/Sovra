"""Live `/api/v1/organizations/{organization_id}/projects/{project_id}/
target-connections/github-branches` and `.../github-resolve-ref` routes
(FTF-04A04, Section 6.8A/6.8B/7A.1-7A.2/9/45/65-67/77/81).

Backs the GitHub branch/ref selector + immutable SHA resolver UI's own
real backend calls
(`github_branch_ref_selector_immutable_sha_resolver.tsx`) -- these are the
steps *between* FTF-04A03's own repository selection and FTF-04A05's own
`TargetSourceSnapshot` creation: the user has already picked a
`repository_identity`, and these routes let them browse its real branches
and resolve a chosen branch/tag/ref to its real, current, immutable commit
SHA.

The credential is a request-body value, never a query parameter (so it
never lands in access logs or browser history) and is never persisted,
echoed back, or written into the audit event this route's own CRUD layer
writes (`github_branch_ref_crud.py`) -- identical discipline to
`github_repository_listing_routes.py` (FTF-04A03).

Role gating: Section 45's own "organization_admin: connections..."
sentence -- identical `TARGET_CONNECTION_ROLES`-shaped role set every
prior credential-bearing target-connection route in this repository
already established.
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
from app.target_connectors.github_branch_ref_crud import (
    ActorInfo,
    ProjectNotFoundError,
    list_project_github_branches,
    resolve_project_github_ref,
)
from app.target_connectors.github_branch_ref_resolver import (
    DEFAULT_GITHUB_API_BASE_URL,
    DEFAULT_PER_PAGE,
    GitHubBranchPage,
    GitHubConnectionError,
    GitHubCredentialRevokedError,
    GitHubRateLimitExceededError,
    GitHubRepositoryAccessDeniedError,
    GitHubResolvedRef,
    InvalidPaginationError,
    InvalidRefError,
    InvalidRepositoryIdentityError,
)

__all__ = ["GITHUB_BRANCH_REF_ROLES", "build_github_branch_ref_router"]

# Same role set `github_repository_listing_routes.GITHUB_REPOSITORY_LISTING_ROLES`
# already established for this class of credential-bearing resource.
GITHUB_BRANCH_REF_ROLES: frozenset[Role] = frozenset({Role.ORGANIZATION_ADMIN})

_PREFIX = "/api/v1/organizations/{organization_id}/projects/{project_id}/target-connections"
_BRANCHES_PATH = _PREFIX + "/github-branches"
_RESOLVE_REF_PATH = _PREFIX + "/github-resolve-ref"


def _serialize_branches(page: GitHubBranchPage) -> dict[str, object]:
    return {
        "branches": [
            {"name": branch.name, "list_time_commit_sha": branch.list_time_commit_sha}
            for branch in page.branches
        ],
        "page": page.page,
        "per_page": page.per_page,
        "has_next_page": page.has_next_page,
    }


def _serialize_resolved_ref(resolved: GitHubResolvedRef) -> dict[str, object]:
    return {"ref": resolved.ref, "resolved_commit_sha": resolved.resolved_commit_sha}


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


def _require_credential(payload: dict[str, object]) -> str:
    value = payload.get("credential")
    if not isinstance(value, str) or not value.strip():
        raise HTTPException(status_code=400, detail="credential is required and must be a string")
    return value


def _require_repository_identity(payload: dict[str, object]) -> str:
    value = payload.get("repository_identity")
    if not isinstance(value, str) or not value.strip():
        raise HTTPException(
            status_code=400, detail="repository_identity is required and must be a string"
        )
    return value


def _require_ref(payload: dict[str, object]) -> str:
    value = payload.get("ref")
    if not isinstance(value, str) or not value.strip():
        raise HTTPException(status_code=400, detail="ref is required and must be a string")
    return value


def _optional_int(payload: dict[str, object], key: str, default: int) -> int:
    if key not in payload or payload[key] is None:
        return default
    value = payload[key]
    if isinstance(value, bool) or not isinstance(value, int):
        raise HTTPException(status_code=400, detail=f"{key} must be an integer")
    return value


def _default_github_api_base_url() -> str:
    return DEFAULT_GITHUB_API_BASE_URL


def build_github_branch_ref_router(
    session_factory: Callable[[], Session],
    *,
    principal_resolver: PrincipalResolver = default_principal_resolver,
    role_lookup: RoleLookup = default_role_lookup,
    github_api_base_url_factory: Callable[[], str] = _default_github_api_base_url,
    github_connection_policy_factory: Callable[[], ConnectionPolicy | None] = lambda: None,
) -> APIRouter:
    """Real FastAPI router for FTF-04A04's own branch-listing and
    ref-resolution calls. `session_factory`/`principal_resolver`/
    `role_lookup` follow the exact same injection convention every prior
    router in this repository established.
    `github_api_base_url_factory`/`github_connection_policy_factory`
    default to the real GitHub API and are called fresh on every request
    (never cached at router-build time); a test/CI caller overrides them
    to point at a real local fixture server -- the same seam
    `github_repository_listing_routes.py`'s own factories already
    established."""
    router = APIRouter()
    dependency = Depends(
        require_role(
            GITHUB_BRANCH_REF_ROLES, principal_resolver=principal_resolver, role_lookup=role_lookup
        )
    )

    @router.post(_BRANCHES_PATH)
    async def list_branches_endpoint(
        request: Request,
        organization_id: uuid.UUID,
        project_id: uuid.UUID,
        ctx: AuthorizedContext = dependency,
    ) -> JSONResponse:
        del organization_id
        payload = await _read_json_body(request)
        credential = _require_credential(payload)
        repository_identity = _require_repository_identity(payload)
        page = _optional_int(payload, "page", 1)
        per_page = _optional_int(payload, "per_page", DEFAULT_PER_PAGE)
        tenant_context = TenantContext.from_authorized_context(ctx)
        actor = _actor_from(ctx, request)
        try:
            with tenant_scoped_session_for(session_factory, tenant_context) as session:
                page_result = list_project_github_branches(
                    session,
                    tenant_context,
                    project_id,
                    credential=credential,
                    repository_identity=repository_identity,
                    page=page,
                    per_page=per_page,
                    actor=actor,
                    base_url=github_api_base_url_factory(),
                    policy=github_connection_policy_factory(),
                )
                body = _serialize_branches(page_result)
        except (ProjectNotFoundError, TenantContextError) as exc:
            raise HTTPException(status_code=404, detail="project_not_found") from exc
        except (InvalidPaginationError, InvalidRepositoryIdentityError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except GitHubCredentialRevokedError as exc:
            raise HTTPException(status_code=401, detail=str(exc)) from exc
        except GitHubRateLimitExceededError as exc:
            raise HTTPException(status_code=429, detail=str(exc)) from exc
        except GitHubRepositoryAccessDeniedError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except GitHubConnectionError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        return JSONResponse(body, status_code=200)

    @router.post(_RESOLVE_REF_PATH)
    async def resolve_ref_endpoint(
        request: Request,
        organization_id: uuid.UUID,
        project_id: uuid.UUID,
        ctx: AuthorizedContext = dependency,
    ) -> JSONResponse:
        del organization_id
        payload = await _read_json_body(request)
        credential = _require_credential(payload)
        repository_identity = _require_repository_identity(payload)
        ref = _require_ref(payload)
        tenant_context = TenantContext.from_authorized_context(ctx)
        actor = _actor_from(ctx, request)
        try:
            with tenant_scoped_session_for(session_factory, tenant_context) as session:
                resolved = resolve_project_github_ref(
                    session,
                    tenant_context,
                    project_id,
                    credential=credential,
                    repository_identity=repository_identity,
                    ref=ref,
                    actor=actor,
                    base_url=github_api_base_url_factory(),
                    policy=github_connection_policy_factory(),
                )
                body = _serialize_resolved_ref(resolved)
        except (ProjectNotFoundError, TenantContextError) as exc:
            raise HTTPException(status_code=404, detail="project_not_found") from exc
        except (InvalidRefError, InvalidRepositoryIdentityError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except GitHubCredentialRevokedError as exc:
            raise HTTPException(status_code=401, detail=str(exc)) from exc
        except GitHubRateLimitExceededError as exc:
            raise HTTPException(status_code=429, detail=str(exc)) from exc
        except GitHubRepositoryAccessDeniedError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except GitHubConnectionError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        return JSONResponse(body, status_code=200)

    return router
