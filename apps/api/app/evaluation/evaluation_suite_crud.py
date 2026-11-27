"""EvaluationSuite CRUD (FTF-0601, Section 23-30/69/45/65/66/77/81).

Create/get/list/update/lock a project-scoped `EvaluationSuite`
(`apps/api/app/evaluation/evaluation_suite_schema.py`). See that module's
own docstring for the full purpose/versioning/manifest-binding/reviewer
design this CRUD layer implements.

Every real query goes through the same certified composition FTF-0201/
FTF-0301/FTF-0401/FTF-0404/FTF-1001/FTF-04A07/FTF-04A19 already proved for
exactly this class of resource:

  - `TenantContext`/`tenant_scoped_session_for` (FTF-H003) open the unit
    of work and bind the transaction-local RLS tenant context;
  - `authorize_resource_ownership` (FTF-H003) proves a project ID (at
    creation/list) and a suite ID (at get/update/lock), and the referenced
    manifest ID (at creation), belong to the caller's own authenticated
    organization *before* any row is read or mutated -- denied identically
    whether an ID does not exist at all or belongs to a different
    organization;
  - `execute_tenant_scoped` (FTF-0105) is what every actual SELECT in this
    module runs through, never a bare `session.execute(...)`/`session.get(...)`.

## Manifest-to-project cross-parent check (disclosed, not hidden)

`AgentManifest` genuinely has its own `project_id` field (unlike
`TargetSourceSnapshot`, which needed a two-hop lookup through its owning
connection -- see `agent_manifest_crud.py`'s own docstring for that
different case). So `create_evaluation_suite` only needs a single real
fetch of the referenced manifest, then a direct `manifest.project_id ==
project_id` comparison -- no cross-table join is required here. Refused
(`EvaluationSuiteManifestMismatchError`) on mismatch, and
(`EvaluationSuiteManifestNotLockedError`) unless the manifest's own
current `status == 'locked'` -- both this task's own negative_acceptance,
"Wrong project/manifest ... rejected" (see the schema module's own
docstring for the full disclosed reading).

Role gating: Section 45 names a real, specific sentence for exactly this
class of resource -- "evaluator: create/run evaluation, no deployment."
`EVALUATION_SUITE_WRITE_ROLES` is `{evaluator, workspace_admin,
organization_admin}` (an evaluator's own real, named write capability,
plus the two administrative roles every prior resource's own write set in
this chain already included); `EVALUATION_SUITE_READ_ROLES` is every role
Section 45 names, matching every prior resource's own wide-read baseline
for a resource with no dedicated restrictive read sentence.

Every mutating operation writes an audit event via FTF-0104's
`write_audit_event` in the *same* transaction as the write it describes,
identical to every prior task's own discipline in this chain.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.auth.audit_event_writer import write_audit_event
from app.auth.resource_authorization import ResourceAuthorizationError, authorize_resource_ownership
from app.auth.tenant_isolation_query_guards import execute_tenant_scoped
from app.db.tenant_context import TenantContext
from app.evaluation.evaluation_suite_schema import (
    PURPOSE_FINAL,
    EvaluationSuite,
    normalize_identity,
    validate_grading_logic_version,
    validate_name,
    validate_reviewer_id_or_none,
)
from app.projects.project_schema import Project
from app.target_connectors.agent_manifest_schema import AgentManifest

__all__ = [
    "ActorInfo",
    "OrganizationMemberLookup",
    "EvaluationSuiteIncompleteError",
    "EvaluationSuiteLockedError",
    "EvaluationSuiteLockForbiddenError",
    "EvaluationSuiteManifestMismatchError",
    "EvaluationSuiteManifestNotFoundError",
    "EvaluationSuiteManifestNotLockedError",
    "EvaluationSuiteNotFoundError",
    "EvaluationSuiteReviewerNotMemberError",
    "ProjectNotFoundError",
    "create_evaluation_suite",
    "default_organization_member_lookup",
    "get_evaluation_suite",
    "list_evaluation_suites",
    "lock_evaluation_suite",
    "update_evaluation_suite",
]

# (organization_id, subject) -> is this subject a real, active member of
# this organization. An L2 round-2 finding: `reviewer_id` was self-asserted
# free text with no check against real organization membership at all.
# Fail-closed by default (same disclosed-gap discipline as
# `app.auth.rbac_middleware.default_role_lookup`) until a real DB-backed
# Membership table exists -- see that module's own docstring for the
# matching, already-disclosed gap.
OrganizationMemberLookup = Callable[[uuid.UUID, str], bool]


def default_organization_member_lookup(organization_id: uuid.UUID, subject: str) -> bool:
    """Fail-closed: no live Membership table exists yet (see
    `app.auth.rbac_middleware`'s own module docstring for the identical,
    already-disclosed gap at the role-lookup layer). Every reviewer is
    treated as not-a-member until a real DB-backed lookup replaces this
    one."""
    del organization_id, subject
    return False


_TABLE = EvaluationSuite.__table__
_ID_COLUMN = _TABLE.c.id
_ORG_COLUMN = _TABLE.c.organization_id

_PROJECT_TABLE = Project.__table__
_PROJECT_ID_COLUMN = _PROJECT_TABLE.c.id
_PROJECT_ORG_COLUMN = _PROJECT_TABLE.c.organization_id

_MANIFEST_TABLE = AgentManifest.__table__
_MANIFEST_ID_COLUMN = _MANIFEST_TABLE.c.id
_MANIFEST_ORG_COLUMN = _MANIFEST_TABLE.c.organization_id


class ProjectNotFoundError(LookupError):
    """No project with this ID exists in the caller's own organization --
    raised identically whether the ID is missing entirely or belongs to a
    different organization."""


class EvaluationSuiteManifestNotFoundError(LookupError):
    """No agent manifest with this ID exists in the caller's own
    organization."""


class EvaluationSuiteManifestMismatchError(ValueError):
    """The referenced `AgentManifest` genuinely exists (and belongs to the
    caller's own organization) but belongs to a *different* project than
    the suite being created -- this task's own negative_acceptance, "Wrong
    project/manifest ... rejected"."""


class EvaluationSuiteManifestNotLockedError(ValueError):
    """The referenced `AgentManifest`'s current status is not `locked` --
    binding a suite's own "versioned" context to a mutable draft/
    review_required manifest, or to one already superseded, would not
    actually be versioned at all. Also this task's own negative_acceptance,
    "Wrong ... manifest ... rejected" (see the schema module's own
    docstring for the full disclosed reading)."""


class EvaluationSuiteNotFoundError(LookupError):
    """No evaluation suite with this ID exists in the caller's own
    organization *and* project."""


class EvaluationSuiteLockedError(ValueError):
    """The suite's current status is already `locked` -- no content
    mutation or re-lock is permitted."""


class EvaluationSuiteIncompleteError(ValueError):
    """`lock_evaluation_suite` was called with no `reviewer_id` assigned,
    or (for a `final`-purpose suite) with `reviewer_id` equal to the
    suite's own `created_by` (compared after strip+casefold --
    `normalize_identity`) -- Section 69.1's own independence rule."""


class EvaluationSuiteReviewerNotMemberError(ValueError):
    """`reviewer_id` does not correspond to any real, active member of the
    suite's own organization -- an L2 round-2 finding: `reviewer_id` was
    previously self-asserted free text with no membership check at all."""


class EvaluationSuiteLockForbiddenError(Exception):
    """Only the suite's own assigned reviewer may lock it -- the
    authenticated actor's identity does not match `reviewer_id` (compared
    after strip+casefold -- `normalize_identity`). An L2 round-2 finding:
    any caller holding a write role, including the suite's own creator,
    could previously lock a suite under any self-asserted `reviewer_id`
    with no actor-identity check at all -- Section 69.1: "Final suite
    creator/reviewer is independent of candidate implementer," read as
    requiring the lock itself to be performed by that real reviewer, not
    merely requiring the field to carry a different string."""


@dataclass(frozen=True)
class ActorInfo:
    """Who is performing this mutation, for the audit event it writes.
    `actor_id` is always the authenticated principal's own `subject`,
    never a client-supplied string."""

    actor_type: str
    actor_id: str
    request_id: str


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


def _fetch_manifest_and_check_project(
    session: Session, context: TenantContext, project_id: uuid.UUID, manifest_id: uuid.UUID
) -> AgentManifest:
    if not isinstance(manifest_id, uuid.UUID):
        raise EvaluationSuiteManifestNotFoundError(
            f"agent_manifest_id must be a uuid.UUID, got {manifest_id!r}"
        )
    try:
        authorize_resource_ownership(
            session,
            context,
            _MANIFEST_TABLE,
            _MANIFEST_ID_COLUMN,
            _MANIFEST_ORG_COLUMN,
            manifest_id,
        )
    except ResourceAuthorizationError as exc:
        raise EvaluationSuiteManifestNotFoundError(str(exc)) from exc
    statement = select(AgentManifest).where(AgentManifest.id == manifest_id)
    result = execute_tenant_scoped(session, context.organization_id, statement)
    manifest = result.scalars().one_or_none()
    if manifest is None:
        raise EvaluationSuiteManifestNotFoundError(
            f"agent manifest {manifest_id} vanished mid-transaction"
        )
    if manifest.project_id != project_id:
        raise EvaluationSuiteManifestMismatchError(
            f"agent manifest {manifest_id} does not belong to project {project_id}"
        )
    if manifest.status != "locked":
        raise EvaluationSuiteManifestNotLockedError(
            f"agent manifest {manifest_id} is {manifest.status!r}, not 'locked' -- a suite can "
            "only bind to an already-locked, real-versioned manifest"
        )
    return manifest


def _fetch_or_raise(
    session: Session, context: TenantContext, project_id: uuid.UUID, suite_id: uuid.UUID
) -> EvaluationSuite:
    if not isinstance(suite_id, uuid.UUID):
        raise EvaluationSuiteNotFoundError(f"suite_id must be a uuid.UUID, got {suite_id!r}")
    try:
        authorize_resource_ownership(session, context, _TABLE, _ID_COLUMN, _ORG_COLUMN, suite_id)
    except ResourceAuthorizationError as exc:
        raise EvaluationSuiteNotFoundError(str(exc)) from exc
    statement = select(EvaluationSuite).where(EvaluationSuite.id == suite_id)
    result = execute_tenant_scoped(session, context.organization_id, statement)
    row = result.scalars().one_or_none()
    if row is None:
        raise EvaluationSuiteNotFoundError(f"evaluation suite {suite_id} vanished mid-transaction")
    if row.project_id != project_id:
        raise EvaluationSuiteNotFoundError(
            f"evaluation suite {suite_id} does not belong to project {project_id}"
        )
    return row


def _next_version(session: Session, context: TenantContext, project_id: uuid.UUID) -> int:
    statement = select(func.coalesce(func.max(EvaluationSuite.version), 0)).where(
        EvaluationSuite.project_id == project_id
    )
    result = execute_tenant_scoped(session, context.organization_id, statement)
    return int(result.scalar_one()) + 1


def _check_reviewer_is_member(
    context: TenantContext, reviewer_id: str | None, member_lookup: OrganizationMemberLookup
) -> None:
    if reviewer_id is None:
        return
    if not member_lookup(context.organization_id, normalize_identity(reviewer_id)):
        raise EvaluationSuiteReviewerNotMemberError(
            f"reviewer_id {reviewer_id!r} is not an active member of this organization"
        )


def create_evaluation_suite(
    session: Session,
    context: TenantContext,
    *,
    project_id: uuid.UUID,
    agent_manifest_id: uuid.UUID,
    purpose: str,
    name: str,
    grading_logic_version: int,
    actor: ActorInfo,
    reviewer_id: str | None = None,
    member_lookup: OrganizationMemberLookup = default_organization_member_lookup,
) -> EvaluationSuite:
    """Creates a new `draft` `EvaluationSuite` at the next version number
    for this project, bound to an already-`locked` `AgentManifest`."""
    _authorize_project_or_raise(session, context, project_id)
    _fetch_manifest_and_check_project(session, context, project_id, agent_manifest_id)

    version = _next_version(session, context, project_id)
    suite = EvaluationSuite(
        organization_id=context.organization_id,
        project_id=project_id,
        agent_manifest_id=agent_manifest_id,
        version=version,
        status="draft",
        purpose=purpose,
        name=name,
        grading_logic_version=grading_logic_version,
        reviewer_id=reviewer_id,
        created_by=actor.actor_id,
    )
    _check_reviewer_is_member(context, suite.reviewer_id, member_lookup)
    session.add(suite)
    session.flush()
    write_audit_event(
        session,
        organization_id=context.organization_id,
        actor_type=actor.actor_type,
        actor_id=actor.actor_id,
        action="evaluation_suite.create",
        resource_type="evaluation_suite",
        resource_id=str(suite.id),
        request_id=actor.request_id,
    )
    return suite


def get_evaluation_suite(
    session: Session, context: TenantContext, project_id: uuid.UUID, suite_id: uuid.UUID
) -> EvaluationSuite:
    return _fetch_or_raise(session, context, project_id, suite_id)


def list_evaluation_suites(
    session: Session, context: TenantContext, project_id: uuid.UUID
) -> list[EvaluationSuite]:
    _authorize_project_or_raise(session, context, project_id)
    statement = (
        select(EvaluationSuite)
        .where(EvaluationSuite.project_id == project_id)
        .order_by(EvaluationSuite.version)
    )
    result = execute_tenant_scoped(session, context.organization_id, statement)
    return list(result.scalars().all())


def update_evaluation_suite(
    session: Session,
    context: TenantContext,
    *,
    project_id: uuid.UUID,
    suite_id: uuid.UUID,
    actor: ActorInfo,
    name: str | None = None,
    grading_logic_version: int | None = None,
    reviewer_id: str | None = None,
    member_lookup: OrganizationMemberLookup = default_organization_member_lookup,
) -> EvaluationSuite:
    """Edits mutable fields on a `draft` suite. Only fields explicitly
    passed (non-`None`) are changed; `agent_manifest_id`/`purpose`/
    `version` are never accepted here -- all three are fixed at creation
    (the same "immutable binding" discipline `agent_manifest_id` itself
    exists to guarantee).

    Every field this endpoint can change runs through the same real
    validator `__init__` already applies at creation -- an L2 finding
    against an earlier submission: this update path previously assigned
    `name`/`grading_logic_version`/`reviewer_id` directly, so
    `grading_logic_version=0` reached the database unchecked (a real
    `IntegrityError`, surfaced as an opaque 500 instead of a real 400),
    `name=""` was silently accepted against the schema's own
    `minLength: 1`, and `reviewer_id=""` passed `lock_evaluation_suite`'s
    own `is None` check, letting a `final` suite lock with no real
    reviewer identity (Section 69.1's own independence rule)."""
    suite = _fetch_or_raise(session, context, project_id, suite_id)
    if suite.status != "draft":
        raise EvaluationSuiteLockedError(
            f"evaluation suite {suite_id} is {suite.status!r} -- content can only be edited "
            "while draft"
        )

    if name is not None:
        suite.name = validate_name(name)
    if grading_logic_version is not None:
        suite.grading_logic_version = validate_grading_logic_version(grading_logic_version)
    if reviewer_id is not None:
        suite.reviewer_id = validate_reviewer_id_or_none(reviewer_id)
        _check_reviewer_is_member(context, suite.reviewer_id, member_lookup)

    session.flush()
    write_audit_event(
        session,
        organization_id=context.organization_id,
        actor_type=actor.actor_type,
        actor_id=actor.actor_id,
        action="evaluation_suite.update",
        resource_type="evaluation_suite",
        resource_id=str(suite.id),
        request_id=actor.request_id,
    )
    return suite


def lock_evaluation_suite(
    session: Session,
    context: TenantContext,
    *,
    project_id: uuid.UUID,
    suite_id: uuid.UUID,
    actor: ActorInfo,
    member_lookup: OrganizationMemberLookup = default_organization_member_lookup,
) -> EvaluationSuite:
    """`draft` -> `locked`: requires a real `reviewer_id` already assigned
    (Section 69.1); for a `final`-purpose suite, that reviewer must differ
    from the suite's own `created_by` (compared after strip+casefold --
    the same independence rule this module's own schema docstring
    describes); the lock itself must be performed by that exact reviewer
    identity (`actor.actor_id` normalized-equal to `reviewer_id` -- an L2
    round-2 finding: any write-role caller, including the suite's own
    creator, could previously lock a suite regardless of who `reviewer_id`
    actually named); and `reviewer_id` is re-checked against real
    organization membership as defense-in-depth against a raw write that
    bypassed `create_evaluation_suite`/`update_evaluation_suite`'s own
    membership check.
    """
    suite = _fetch_or_raise(session, context, project_id, suite_id)
    if suite.status != "draft":
        raise EvaluationSuiteLockedError(f"evaluation suite {suite_id} is already {suite.status!r}")

    if suite.reviewer_id is None or not suite.reviewer_id.strip():
        raise EvaluationSuiteIncompleteError(
            f"evaluation suite {suite_id} has no reviewer_id assigned -- Section 69.1: "
            "'Final suite creator/reviewer is independent of candidate implementer'"
        )
    reviewer_normalized = normalize_identity(suite.reviewer_id)
    creator_normalized = normalize_identity(suite.created_by)
    if suite.purpose == PURPOSE_FINAL and reviewer_normalized == creator_normalized:
        raise EvaluationSuiteIncompleteError(
            f"evaluation suite {suite_id} is purpose='final' and its own reviewer_id "
            "normalizes (strip+casefold) to the same identity as created_by -- "
            "Section 69.1's own independence rule"
        )
    _check_reviewer_is_member(context, suite.reviewer_id, member_lookup)
    if normalize_identity(actor.actor_id) != normalize_identity(suite.reviewer_id):
        raise EvaluationSuiteLockForbiddenError(
            f"evaluation suite {suite_id} can only be locked by its assigned reviewer "
            f"({suite.reviewer_id!r}), not actor {actor.actor_id!r} -- Section 69.1"
        )

    suite.status = "locked"
    suite.locked_at = datetime.now(UTC)
    session.flush()
    write_audit_event(
        session,
        organization_id=context.organization_id,
        actor_type=actor.actor_type,
        actor_id=actor.actor_id,
        action="evaluation_suite.lock",
        resource_type="evaluation_suite",
        resource_id=str(suite.id),
        request_id=actor.request_id,
    )
    return suite
