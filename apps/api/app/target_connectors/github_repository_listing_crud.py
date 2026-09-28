"""GitHub accessible-repository listing CRUD/audit layer (FTF-04A03,
Section 6.8A/7A.1-7A.2/9/45/65-67/77/81).

Authorizes the caller's `project_id` belongs to their own organization
(the same `authorize_resource_ownership`/`execute_tenant_scoped` (FTF-H003/
FTF-0105) composition FTF-04A01/FTF-0401/FTF-0404/FTF-0500 already proved),
writes a real audit event ("connection creation and validation are
audited", Section 6.8A) in the *same* transaction as the listing call, and
delegates the actual GitHub REST API v3 call to
`github_accessible_repository_listing.list_accessible_repositories`
(FTF-04A03's own new connector-level module). The audit event never
contains the raw credential (Section 46/47) -- `details` below carries only
`page`/`per_page`/the returned repository count, never the token.

This deliberately does not persist anything: browsing repositories is a
step *before* a `TargetApplicationConnection` row exists (the user has not
picked a `repository_identity` yet -- that is what this listing is for).
No `TargetApplicationConnection` row is read, created, or required to
exist for this call to succeed.
"""

from __future__ import annotations

import sys
import uuid
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy.orm import Session

_REPO_ROOT = Path(__file__).resolve().parents[4]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from packages.security.url_policy import ConnectionPolicy  # noqa: E402

from app.auth.audit_event_writer import write_audit_event  # noqa: E402
from app.auth.resource_authorization import (  # noqa: E402
    ResourceAuthorizationError,
    authorize_resource_ownership,
)
from app.db.tenant_context import TenantContext  # noqa: E402
from app.projects.project_schema import Project  # noqa: E402
from app.target_connectors.github_accessible_repository_listing import (  # noqa: E402
    DEFAULT_GITHUB_API_BASE_URL,
    GitHubConnectionError,
    GitHubRepositoryPage,
    list_accessible_repositories,
)

__all__ = ["ActorInfo", "ProjectNotFoundError", "list_project_accessible_github_repositories"]

_PROJECT_TABLE = Project.__table__
_PROJECT_ID_COLUMN = _PROJECT_TABLE.c.id
_PROJECT_ORG_COLUMN = _PROJECT_TABLE.c.organization_id


@dataclass(frozen=True)
class ActorInfo:
    """Same shape `target_application_connection_crud.ActorInfo` already
    established -- who is performing this real, credential-bearing read,
    for the audit event it writes."""

    actor_type: str
    actor_id: str
    request_id: str


class ProjectNotFoundError(LookupError):
    """No project with this ID exists in the caller's own organization --
    identical whether the ID is missing entirely or belongs to a different
    organization (the same IDOR discipline every prior project-scoped
    resource in this repository already established)."""


def _authorize_project_or_raise(
    session: Session, context: TenantContext, project_id: uuid.UUID
) -> None:
    if not isinstance(project_id, uuid.UUID):
        raise ProjectNotFoundError(f"project_id must be a uuid.UUID, got {project_id!r}")
    try:
        authorize_resource_ownership(
            session, context, _PROJECT_TABLE, _PROJECT_ID_COLUMN, _PROJECT_ORG_COLUMN, project_id
        )
    except ResourceAuthorizationError as exc:
        raise ProjectNotFoundError(str(exc)) from exc


def list_project_accessible_github_repositories(
    session: Session,
    context: TenantContext,
    project_id: uuid.UUID,
    *,
    credential: str,
    page: int,
    per_page: int,
    actor: ActorInfo,
    base_url: str = DEFAULT_GITHUB_API_BASE_URL,
    policy: ConnectionPolicy | None = None,
) -> GitHubRepositoryPage:
    """Authorizes `project_id`, lists the real page of repositories
    `credential` can see, and writes one real audit event describing the
    outcome (never the credential itself) in the same transaction the
    caller commits. Raises `ProjectNotFoundError` or one of
    `github_accessible_repository_listing`'s own `GitHubConnectionError`
    subclasses -- never silently returns a partial or fabricated page.

    `base_url`/`policy` default to the real GitHub API; a test/CI caller
    overrides them (via the router's own build-time factories) to point at
    a real local fixture server -- the same seam
    `target_application_connection_routes.py`'s own
    `secret_manager_config_factory` already established for a different
    external dependency.

    A denied/failed GitHub call is not a "failed mutation" in
    `write_audit_event`'s own both-or-neither sense (there is no domain row
    being created here to roll back) -- Section 6.8A requires *validation*
    itself to be audited, a denied attempt included, so this function
    commits that one audit row itself before re-raising, rather than
    letting the caller's enclosing `tenant_scoped_session_for` roll it back
    along with the propagated exception (a real defect this task's own
    integration test caught: without this explicit commit, a denied
    listing's own audit row silently never reached the database)."""
    _authorize_project_or_raise(session, context, project_id)

    try:
        page_result = list_accessible_repositories(
            token=credential, page=page, per_page=per_page, base_url=base_url, policy=policy
        )
    except GitHubConnectionError as exc:
        write_audit_event(
            session,
            organization_id=context.organization_id,
            actor_type=actor.actor_type,
            actor_id=actor.actor_id,
            action="target_application_connection.github_repositories_list_denied",
            resource_type="project",
            resource_id=str(project_id),
            request_id=actor.request_id,
            details={"page": page, "per_page": per_page, "reason": exc.__class__.__name__},
        )
        session.commit()
        raise

    write_audit_event(
        session,
        organization_id=context.organization_id,
        actor_type=actor.actor_type,
        actor_id=actor.actor_id,
        action="target_application_connection.github_repositories_listed",
        resource_type="project",
        resource_id=str(project_id),
        request_id=actor.request_id,
        details={
            "page": page_result.page,
            "per_page": page_result.per_page,
            "repository_count": len(page_result.repositories),
            "has_next_page": page_result.has_next_page,
        },
    )
    return page_result
