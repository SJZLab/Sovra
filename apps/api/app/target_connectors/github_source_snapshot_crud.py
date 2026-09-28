"""GitHub source snapshot capture + persistence CRUD/audit layer
(FTF-04A05, Section 6.8B/7A.1-7A.2/9/45/65-67/77/81).

Composes two already-CERTIFIED real pieces this task never re-implements:

  - `github_source_snapshot_resolver.capture_github_source_snapshot` (this
    task's own new connector) -- the real GitHub REST API v3 calls that
    resolve a branch/tag/ref to an immutable commit and its own real,
    complete, safety-checked tree;
  - `target_source_snapshot_crud.create_target_source_snapshot`
    (FTF-04A07, CERTIFIED) -- the real, immutable, tenant-authorized
    `TargetSourceSnapshot` INSERT.

Same shape `github_branch_ref_crud.py` (FTF-04A04) already established:
authorizes the caller's `project_id` belongs to their own organization,
writes a real audit event describing the outcome (never the credential)
in the same transaction, and never persists on a denied/incomplete/unsafe
capture -- the GitHub connector's own real errors
(`IncompleteSourceTreeError`/`UnsafeSourceTreeEntryError`/GitHub's own
401/403/404 outcomes) propagate to the caller and are logged, never
converted into a fabricated or partial snapshot (Law 1/Law 11).

Same "caller supplies the already-resolved credential in the request"
boundary `github_branch_ref_crud.py`/`github_repository_listing_crud.py`
already established -- this module never resolves a
`credential_secret_ref` itself.

`create_target_source_snapshot` (FTF-04A07) already authorizes
`connection_id` belongs to the caller's own organization before inserting
-- this module does not duplicate that check; it only additionally
authorizes `project_id`, the same two-level authorization shape
`github_branch_ref_crud.resolve_project_github_ref` already established
for the sibling resolve-then-persist flow.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from packages.security.url_policy import ConnectionPolicy
from sqlalchemy.orm import Session

from app.auth.audit_event_writer import write_audit_event
from app.auth.resource_authorization import ResourceAuthorizationError, authorize_resource_ownership
from app.db.tenant_context import TenantContext
from app.projects.project_schema import Project
from app.target_connectors.github_app_token_read_only_secret_integration import (
    DEFAULT_GITHUB_API_BASE_URL,
    GitHubConnectionError,
)
from app.target_connectors.github_source_snapshot_resolver import capture_github_source_snapshot
from app.target_connectors.target_source_snapshot_crud import (
    ActorInfo as _SnapshotActorInfo,
)
from app.target_connectors.target_source_snapshot_crud import (
    TargetApplicationConnectionNotFoundError,
    TargetSourceSnapshotNotFoundError,
    create_target_source_snapshot,
)
from app.target_connectors.target_source_snapshot_schema import TargetSourceSnapshot

__all__ = [
    "ActorInfo",
    "ProjectNotFoundError",
    "TargetApplicationConnectionNotFoundError",
    "TargetSourceSnapshotNotFoundError",
    "capture_and_create_github_source_snapshot",
]


_PROJECT_TABLE = Project.__table__
_PROJECT_ID_COLUMN = _PROJECT_TABLE.c.id
_PROJECT_ORG_COLUMN = _PROJECT_TABLE.c.organization_id


@dataclass(frozen=True)
class ActorInfo:
    """Same shape every sibling CRUD module's own `ActorInfo` already
    established -- who is performing this real, credential-bearing
    capture, for the audit events it writes."""

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


def capture_and_create_github_source_snapshot(
    session: Session,
    context: TenantContext,
    project_id: uuid.UUID,
    connection_id: uuid.UUID,
    *,
    credential: str,
    repository_identity: str,
    ref: str,
    actor: ActorInfo,
    supersedes_snapshot_id: uuid.UUID | None = None,
    base_url: str = DEFAULT_GITHUB_API_BASE_URL,
    policy: ConnectionPolicy | None = None,
) -> TargetSourceSnapshot:
    """Authorizes `project_id`, captures a real, complete, safety-checked
    GitHub source snapshot for `ref` in `repository_identity`
    (`github_source_snapshot_resolver.capture_github_source_snapshot`),
    and persists it as an immutable `TargetSourceSnapshot` row scoped to
    `connection_id` (`target_source_snapshot_crud.create_target_source_snapshot`,
    which independently authorizes `connection_id` itself). Writes one
    real audit event describing the outcome (never the credential) in the
    same transaction the caller commits. Raises `ProjectNotFoundError`,
    `TargetApplicationConnectionNotFoundError`,
    `TargetSourceSnapshotNotFoundError`,
    `TargetSourceSnapshotValidationError`, or one of the GitHub
    connector's own `GitHubConnectionError` subclasses -- never silently
    persists a partial or fabricated snapshot."""
    _authorize_project_or_raise(session, context, project_id)

    try:
        capture = capture_github_source_snapshot(
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
            action="target_source_snapshot.github_capture_denied",
            resource_type="project",
            resource_id=str(project_id),
            request_id=actor.request_id,
            details={
                "repository_identity": repository_identity,
                "ref": ref,
                "connection_id": str(connection_id),
                "reason": exc.__class__.__name__,
            },
        )
        session.commit()
        raise

    snapshot = create_target_source_snapshot(
        session,
        context,
        connection_id=connection_id,
        commit_sha=capture.commit_sha,
        tree_hash=capture.tree_hash,
        capture_method=capture.capture_method,
        branch=capture.branch,
        supersedes_snapshot_id=supersedes_snapshot_id,
        actor=_SnapshotActorInfo(
            actor_type=actor.actor_type, actor_id=actor.actor_id, request_id=actor.request_id
        ),
    )

    write_audit_event(
        session,
        organization_id=context.organization_id,
        actor_type=actor.actor_type,
        actor_id=actor.actor_id,
        action="target_source_snapshot.github_captured",
        resource_type="target_source_snapshot",
        resource_id=str(snapshot.id),
        request_id=actor.request_id,
        details={
            "repository_identity": repository_identity,
            "ref": ref,
            "connection_id": str(connection_id),
            "tree_entry_count": len(capture.tree_entries),
            "submodule_count": len(capture.submodule_paths),
            "lfs_tracked_path_count": len(capture.lfs_tracked_paths),
            "tree_listing_complete": capture.tree_listing_complete,
        },
    )
    return snapshot
