"""Live `/api/v1/organizations/{organization_id}/projects/{project_id}/
target-connections/github-repositories` route (FTF-04A03, Section 6.8A/
7A.1-7A.2/9/45/65-67/77/81).

Backs the GitHub accessible-repository selector UI's own real backend call
(`github_accessible_repository_selector_ui.tsx`) -- this is the step
*before* a `TargetApplicationConnection` row exists: the user pastes an
already-resolved GitHub App installation token or organization-approved
token, and this route returns the real, credential-scoped, paginated list
of repositories GitHub itself says that credential can see (`GET
/installation/repositories`, via `github_accessible_repository_listing.py`,
FTF-04A03's own new connector-level module -- see that module's own
docstring for why this is a new sibling module rather than a change to
the already-CERTIFIED `github_app_token_read_only_secret_integration.py`,
FTF-04A02).

The credential is a request-body value, never a query parameter (so it
never lands in access logs or browser history) and is never persisted,
echoed back, or written into the audit event this route's own CRUD layer
writes (`github_repository_listing_crud.py`).

Role gating: Section 45's own "organization_admin: connections..."
sentence -- identical `TARGET_CONNECTION_ROLES` set
`target_application_connection_routes.py` already established for this
exact class of credential-bearing target-connection resource.
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
from app.target_connectors.github_accessible_repository_listing import (
    DEFAULT_GITHUB_API_BASE_URL,
    DEFAULT_PER_PAGE,
    GitHubConnectionError,
    GitHubCredentialRevokedError,
    GitHubRateLimitExceededError,
    GitHubRepositoryPage,
    InvalidPaginationError,
)
from app.target_connectors.github_repository_listing_crud import (
    ActorInfo,
    ProjectNotFoundError,
    list_project_accessible_github_repositories,
)

__all__ = ["GITHUB_REPOSITORY_LISTING_ROLES", "build_github_repository_listing_router"]

# Same role set `target_application_connection_routes.TARGET_CONNECTION_ROLES`
# already established for this class of credential-bearing resource.
GITHUB_REPOSITORY_LISTING_ROLES: frozenset[Role] = frozenset({Role.ORGANIZATION_ADMIN})

_PATH = (
    "/api/v1/organizations/{organization_id}/projects/{project_id}"
    "/target-connections/github-repositories"
)


def _serialize(page: GitHubRepositoryPage) -> dict[str, object]:
    return {
        "repositories": [
            {
                "repository_identity": repo.repository_identity,
                "private": repo.private,
                "default_branch": repo.default_branch,
            }
            for repo in page.repositories
        ],
        "page": page.page,
        "per_page": page.per_page,
        "has_next_page": page.has_next_page,
    }


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


def _optional_int(payload: dict[str, object], key: str, default: int) -> int:
    if key not in payload or payload[key] is None:
        return default
    value = payload[key]
    if isinstance(value, bool) or not isinstance(value, int):
        raise HTTPException(status_code=400, detail=f"{key} must be an integer")
    return value


def _default_github_api_base_url() -> str:
    return DEFAULT_GITHUB_API_BASE_URL


def build_github_repository_listing_router(
    session_factory: Callable[[], Session],
    *,
    principal_resolver: PrincipalResolver = default_principal_resolver,
    role_lookup: RoleLookup = default_role_lookup,
    github_api_base_url_factory: Callable[[], str] = _default_github_api_base_url,
    github_connection_policy_factory: Callable[[], ConnectionPolicy | None] = lambda: None,
) -> APIRouter:
    """Real FastAPI router for FTF-04A03's own repository-listing call.
    `session_factory`/`principal_resolver`/`role_lookup` follow the exact
    same injection convention every prior router in this repository
    established. `github_api_base_url_factory`/`github_connection_policy_factory`
    default to the real GitHub API and are called fresh on every request
    (never cached at router-build time); a test/CI caller overrides them to
    point at a real local fixture server, the same seam
    `target_application_connection_routes.py`'s own
    `secret_manager_config_factory` already established."""
    router = APIRouter()
    dependency = Depends(
        require_role(
            GITHUB_REPOSITORY_LISTING_ROLES,
            principal_resolver=principal_resolver,
            role_lookup=role_lookup,
        )
    )

    @router.post(_PATH)
    async def list_repositories(
        request: Request,
        organization_id: uuid.UUID,
        project_id: uuid.UUID,
        ctx: AuthorizedContext = dependency,
    ) -> JSONResponse:
        del organization_id
        payload = await _read_json_body(request)
        credential = _require_credential(payload)
        page = _optional_int(payload, "page", 1)
        per_page = _optional_int(payload, "per_page", DEFAULT_PER_PAGE)
        tenant_context = TenantContext.from_authorized_context(ctx)
        actor = ActorInfo(
            actor_type="user", actor_id=ctx.principal.subject, request_id=uuid.uuid4().hex
        )
        try:
            with tenant_scoped_session_for(session_factory, tenant_context) as session:
                page_result = list_project_accessible_github_repositories(
                    session,
                    tenant_context,
                    project_id,
                    credential=credential,
                    page=page,
                    per_page=per_page,
                    actor=actor,
                    base_url=github_api_base_url_factory(),
                    policy=github_connection_policy_factory(),
                )
                body = _serialize(page_result)
        except (ProjectNotFoundError, TenantContextError) as exc:
            raise HTTPException(status_code=404, detail="project_not_found") from exc
        except InvalidPaginationError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except GitHubCredentialRevokedError as exc:
            raise HTTPException(status_code=401, detail=str(exc)) from exc
        except GitHubRateLimitExceededError as exc:
            raise HTTPException(status_code=429, detail=str(exc)) from exc
        except GitHubConnectionError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        return JSONResponse(body, status_code=200)

    return router
