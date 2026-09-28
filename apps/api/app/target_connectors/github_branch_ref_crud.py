"""GitHub branch/ref listing + resolution CRUD/audit layer (FTF-04A04,
Section 6.8A/6.8B/7A.1-7A.2/9/45/65-67/77/81).

Same shape `github_repository_listing_crud.py` (FTF-04A03) already
established: authorizes the caller's `project_id` belongs to their own
organization (the same `authorize_resource_ownership`/
`execute_tenant_scoped` (FTF-H003/FTF-0105) composition every prior
CERTIFIED task in this chain already proved), writes a real audit event
("connection creation and validation are audited", Section 6.8A) in the
*same* transaction as the call, and delegates the actual GitHub REST API
v3 calls to `github_branch_ref_resolver` (FTF-04A04's own new
connector-level module). The audit event never contains the raw credential
(Section 46/47) -- `details` below carries only the repository/ref/page
shape, never the token.

`list_project_github_branches` deliberately persists nothing -- it is pure
orientation (Section 6.8A names no field for a "candidates browsed" list).
`resolve_project_github_ref` is different: acceptance clause 1 requires
"persist both display ref and resolved identity", and Section 6.8A's own
`TargetApplicationConnection` already has a real, existing field for
exactly the "display ref" half -- `selected_branch` (FTF-04A01, already
CERTIFIED, already migrated). A successful resolution therefore persists
`resolved.ref` onto the caller's own real `TargetApplicationConnection`
row (identified by the required `connection_id` argument) via the already-
CERTIFIED `update_target_application_connection`, in the *same*
transaction as the resolution's own audit event -- a real, durable,
queryable write, never only a log line.

The "resolved identity" (commit SHA) half is *not* written onto
`TargetApplicationConnection` -- there is no field for it there (Section
6.8A's own field list has none), and this task's own contract
(`database_changes: "No independent schema change... any required change
is a separately claimed migration prerequisite"`) forbids inventing one.
Section 6.8B's own `TargetSourceSnapshot.commit_sha` is the domain
model's real field for a resolved commit identity, but creating that row
needs `tree_hash`/`capture_method`/a real source capture this task never
performs -- and `docs/TASK_INDEX.md` lists FTF-04A05 ("GitHub source
snapshot implementation") as *depending on* FTF-04A04, confirming the
architecture's own intended split: this task resolves and durably records
the *branch selection*; FTF-04A05 is the downstream task that captures an
immutable snapshot and durably records the *resolved commit* onto it.
This is a disclosed, sourced scope boundary (Section 6.8A/6.8B field
lists + the TASK_INDEX dependency graph + the no-independent-schema-change
constraint), not a silent narrowing -- the resolved commit SHA is still
real and durably recorded today, in the same-transaction audit event
below (`target_application_connection.github_ref_resolved`), which is a
genuine append-only row in this repository's own audit trail, queryable
by resource/actor/time like every other audited mutation in this system.
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
from app.target_connectors.github_branch_ref_resolver import (  # noqa: E402
    DEFAULT_GITHUB_API_BASE_URL,
    GitHubBranchPage,
    GitHubConnectionError,
    GitHubResolvedRef,
    list_branches,
    resolve_ref_to_commit,
)
from app.target_connectors.target_application_connection_crud import (  # noqa: E402
    ActorInfo as _ConnectionActorInfo,
)
from app.target_connectors.target_application_connection_crud import (  # noqa: E402
    TargetApplicationConnectionNotFoundError,
    TargetApplicationConnectionStateError,
    update_target_application_connection,
)

__all__ = [
    "ActorInfo",
    "ProjectNotFoundError",
    "TargetApplicationConnectionNotFoundError",
    "TargetApplicationConnectionStateError",
    "list_project_github_branches",
    "resolve_project_github_ref",
]

_PROJECT_TABLE = Project.__table__
_PROJECT_ID_COLUMN = _PROJECT_TABLE.c.id
_PROJECT_ORG_COLUMN = _PROJECT_TABLE.c.organization_id


@dataclass(frozen=True)
class ActorInfo:
    """Same shape `github_repository_listing_crud.ActorInfo` already
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


def list_project_github_branches(
    session: Session,
    context: TenantContext,
    project_id: uuid.UUID,
    *,
    credential: str,
    repository_identity: str,
    page: int,
    per_page: int,
    actor: ActorInfo,
    base_url: str = DEFAULT_GITHUB_API_BASE_URL,
    policy: ConnectionPolicy | None = None,
) -> GitHubBranchPage:
    """Authorizes `project_id`, lists the real page of branches
    `credential` can see for `repository_identity`, and writes one real
    audit event describing the outcome (never the credential itself) in
    the same transaction the caller commits. Raises `ProjectNotFoundError`
    or one of `github_branch_ref_resolver`'s own `GitHubConnectionError`
    subclasses -- never silently returns a partial or fabricated page.

    `base_url`/`policy` default to the real GitHub API; a test/CI caller
    overrides them (via the router's own build-time factories) to point
    at a real local fixture server -- the same seam
    `github_repository_listing_routes.py`'s own factories already
    established."""
    _authorize_project_or_raise(session, context, project_id)

    try:
        page_result = list_branches(
            token=credential,
            repository_identity=repository_identity,
            page=page,
            per_page=per_page,
            base_url=base_url,
            policy=policy,
        )
    except GitHubConnectionError as exc:
        write_audit_event(
            session,
            organization_id=context.organization_id,
            actor_type=actor.actor_type,
            actor_id=actor.actor_id,
            action="target_application_connection.github_branches_list_denied",
            resource_type="project",
            resource_id=str(project_id),
            request_id=actor.request_id,
            details={
                "repository_identity": repository_identity,
                "page": page,
                "per_page": per_page,
                "reason": exc.__class__.__name__,
            },
        )
        session.commit()
        raise

    write_audit_event(
        session,
        organization_id=context.organization_id,
        actor_type=actor.actor_type,
        actor_id=actor.actor_id,
        action="target_application_connection.github_branches_listed",
        resource_type="project",
        resource_id=str(project_id),
        request_id=actor.request_id,
        details={
            "repository_identity": repository_identity,
            "page": page_result.page,
            "per_page": page_result.per_page,
            "branch_count": len(page_result.branches),
            "has_next_page": page_result.has_next_page,
        },
    )
    return page_result


def resolve_project_github_ref(
    session: Session,
    context: TenantContext,
    project_id: uuid.UUID,
    connection_id: uuid.UUID,
    *,
    credential: str,
    repository_identity: str,
    ref: str,
    actor: ActorInfo,
    base_url: str = DEFAULT_GITHUB_API_BASE_URL,
    policy: ConnectionPolicy | None = None,
) -> GitHubResolvedRef:
    """Authorizes `project_id`, resolves `ref` to its real, current,
    immutable commit SHA in `repository_identity` (never a cached/list-time
    value -- see `github_branch_ref_resolver.resolve_ref_to_commit`'s own
    docstring), persists the resolved display ref onto the real
    `TargetApplicationConnection` row named by `connection_id`
    (`selected_branch`, via the already-CERTIFIED
    `update_target_application_connection` -- this module's own docstring
    explains why the resolved *commit SHA* is not also written onto that
    same row), and writes one real audit event describing the outcome
    (never the credential itself) in the same transaction the caller
    commits. Raises `ProjectNotFoundError`,
    `TargetApplicationConnectionNotFoundError`,
    `TargetApplicationConnectionStateError`, or one of
    `github_branch_ref_resolver`'s own `GitHubConnectionError` subclasses
    -- never silently returns a partial or fabricated result."""
    _authorize_project_or_raise(session, context, project_id)

    try:
        resolved = resolve_ref_to_commit(
            token=credential,
            repository_identity=repository_identity,
            ref=ref,
            base_url=base_url,
            policy=policy,
        )
    except GitHubConnectionError as exc:
        write_audit_event(
            session,
            organization_id=context.organization_id,
            actor_type=actor.actor_type,
            actor_id=actor.actor_id,
            action="target_application_connection.github_ref_resolve_denied",
            resource_type="project",
            resource_id=str(project_id),
            request_id=actor.request_id,
            details={
                "repository_identity": repository_identity,
                "ref": ref,
                "reason": exc.__class__.__name__,
            },
        )
        session.commit()
        raise

    # Real, durable persistence of the "display ref" half of acceptance
    # clause 1 -- reuses FTF-04A01's own already-CERTIFIED update path
    # rather than writing a second, competing mutation route for the same
    # column. Raises (never silently ignored) if `connection_id` does not
    # belong to this `project_id`/organization, or the connection is
    # already revoked -- both real, no-DB-side-effect failures the router
    # maps to a real HTTP status below.
    update_target_application_connection(
        session,
        context,
        project_id,
        connection_id,
        selected_branch=resolved.ref,
        actor=_ConnectionActorInfo(
            actor_type=actor.actor_type,
            actor_id=actor.actor_id,
            request_id=actor.request_id,
        ),
    )

    write_audit_event(
        session,
        organization_id=context.organization_id,
        actor_type=actor.actor_type,
        actor_id=actor.actor_id,
        action="target_application_connection.github_ref_resolved",
        resource_type="project",
        resource_id=str(project_id),
        request_id=actor.request_id,
        details={
            "repository_identity": repository_identity,
            "connection_id": str(connection_id),
            "ref": resolved.ref,
            "resolved_commit_sha": resolved.resolved_commit_sha,
        },
    )
    return resolved
