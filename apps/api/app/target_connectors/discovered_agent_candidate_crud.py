"""DiscoveredAgentCandidate CRUD (FTF-04A17, Section 6.8C/7A.4/9/45/65/66/
77/81).

Create/get/list (paginated) a snapshot-scoped `DiscoveredAgentCandidate`
(`apps/api/app/target_connectors/discovered_agent_candidate_schema.py`).
There is no edit or delete surface at all -- see that module's own
docstring for why every candidate is immutable by construction.

Parent-chain authorization reuses FTF-04A07's own already-CERTIFIED
`get_target_source_snapshot` directly (reuse-first, Section 77.1, the
same precedent FTF-0507 already established for composing a sibling
already-CERTIFIED module rather than re-deriving its own IDOR guard from
scratch): a `DiscoveredAgentCandidate` is reached only through a real
`(connection_id, snapshot_id)` pair that `get_target_source_snapshot`
itself proves belongs to the caller's own organization *and* the named
connection before any candidate row is ever read or written. This module
never re-implements FTF-04A01/FTF-04A07's own two-level parent-ownership
check.

`execute_tenant_scoped` (FTF-0105) is what every actual SELECT in this
module runs through, never a bare `session.execute(...)`/
`session.get(...)`, and `authorize_resource_ownership` (FTF-H003) is
what proves a *candidate* ID belongs to the caller's own organization at
`get`, the same composition every prior task in this chain already
established.

Role gating: this module follows FTF-04A01/FTF-04A07's own
`{organization_admin}`-only precedent exactly (see
`discovered_agent_candidate_routes.py`'s own module docstring for the
full reasoning) -- a discovered candidate's own evidence columns are real
extracted content from a target repository (entrypoints, prompts, tool
schemas), tied to the same credential-bearing access chain as the
snapshot and connection it descends from.

`create_discovered_agent_candidate` is the only mutating operation; it
writes an audit event via FTF-0104's `write_audit_event` in the *same*
transaction as the insert it describes, identical to every prior task's
own discipline in this chain. A duplicate `candidate_key` within the same
snapshot is refused before any row is inserted (this task's own real
deduplication identity -- see the schema module's own docstring), the
same "no silent duplicate for the same already-discovered entity"
invariant a raw INSERT could still only be caught by the real database
`UNIQUE` constraint, proven in the live-Postgres integration suite.

`list_discovered_agent_candidates` is this task's own real "paginated
project-scoped API" acceptance half: bounded `limit`/`offset` (Section
84.4's own "bounded collections" requirement for every card), never an
unbounded full-table return.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.auth.audit_event_writer import write_audit_event
from app.auth.resource_authorization import ResourceAuthorizationError, authorize_resource_ownership
from app.auth.tenant_isolation_query_guards import execute_tenant_scoped
from app.db.tenant_context import TenantContext
from app.target_connectors.discovered_agent_candidate_schema import DiscoveredAgentCandidate
from app.target_connectors.target_source_snapshot_crud import (
    TargetApplicationConnectionNotFoundError,
    TargetSourceSnapshotNotFoundError,
    get_target_source_snapshot,
)

__all__ = [
    "ActorInfo",
    "DEFAULT_PAGE_LIMIT",
    "MAX_PAGE_LIMIT",
    "DiscoveredAgentCandidateDuplicateKeyError",
    "DiscoveredAgentCandidateNotFoundError",
    "Page",
    "TargetApplicationConnectionNotFoundError",
    "TargetSourceSnapshotNotFoundError",
    "create_discovered_agent_candidate",
    "get_discovered_agent_candidate",
    "list_discovered_agent_candidates",
]

DEFAULT_PAGE_LIMIT = 50
MAX_PAGE_LIMIT = 200

_CANDIDATE_TABLE = DiscoveredAgentCandidate.__table__
_CANDIDATE_ID_COLUMN = _CANDIDATE_TABLE.c.id
_CANDIDATE_ORG_COLUMN = _CANDIDATE_TABLE.c.organization_id


class DiscoveredAgentCandidateNotFoundError(LookupError):
    """No discovered agent candidate with this ID exists in the caller's
    own organization *and* snapshot -- identical whether the ID is
    missing entirely, belongs to a different organization, or belongs to
    a different snapshot within the caller's own organization."""


class DiscoveredAgentCandidateDuplicateKeyError(ValueError):
    """A candidate with this `candidate_key` already exists for this
    snapshot -- this task's own real deduplication identity, refused
    before insert whenever the application layer can see the conflict
    (the real database `UNIQUE` constraint is the defense-in-depth
    backstop for a concurrent/bypassing write)."""


@dataclass(frozen=True)
class ActorInfo:
    """Who/what is performing this mutation, for the audit event it
    writes. `actor_id` is always the authenticated principal's own
    `subject` (or a real, identifiable automated-pipeline identity for a
    system-triggered discovery run), never a client-supplied free-text
    string."""

    actor_type: str
    actor_id: str
    request_id: str


@dataclass(frozen=True)
class Page:
    """A bounded page of candidates plus the real total row count for
    this snapshot, so a caller can tell "this page is short because the
    collection ended" apart from "this page is short because the limit
    was small"."""

    items: list[DiscoveredAgentCandidate]
    limit: int
    offset: int
    total: int


def _clamp_pagination(limit: int, offset: int) -> tuple[int, int]:
    if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1:
        raise ValueError(f"limit must be a positive integer: {limit!r}")
    if not isinstance(offset, int) or isinstance(offset, bool) or offset < 0:
        raise ValueError(f"offset must be a non-negative integer: {offset!r}")
    return min(limit, MAX_PAGE_LIMIT), offset


def _fetch_or_raise(
    session: Session,
    context: TenantContext,
    snapshot_id: uuid.UUID,
    candidate_id: uuid.UUID,
) -> DiscoveredAgentCandidate:
    if not isinstance(candidate_id, uuid.UUID):
        raise DiscoveredAgentCandidateNotFoundError(
            f"candidate_id must be a uuid.UUID, got {candidate_id!r}"
        )
    try:
        authorize_resource_ownership(
            session,
            context,
            _CANDIDATE_TABLE,
            _CANDIDATE_ID_COLUMN,
            _CANDIDATE_ORG_COLUMN,
            candidate_id,
        )
    except ResourceAuthorizationError as exc:
        raise DiscoveredAgentCandidateNotFoundError(str(exc)) from exc
    statement = select(DiscoveredAgentCandidate).where(DiscoveredAgentCandidate.id == candidate_id)
    result = execute_tenant_scoped(session, context.organization_id, statement)
    row = result.scalars().one_or_none()
    if row is None:
        raise DiscoveredAgentCandidateNotFoundError(
            f"discovered agent candidate {candidate_id} vanished mid-transaction"
        )
    if row.target_source_snapshot_id != snapshot_id:
        # A snapshot-scoped resource's own deeper IDOR case, the same
        # precedent `target_source_snapshot_crud.py`'s own `_fetch_or_raise`
        # already established for its own connection-scoped resource: a
        # real row in the caller's own organization, just under a
        # *different* snapshot than the URL names, is refused identically
        # to a missing row.
        raise DiscoveredAgentCandidateNotFoundError(
            f"discovered agent candidate {candidate_id} does not belong to snapshot {snapshot_id}"
        )
    return row


def create_discovered_agent_candidate(
    session: Session,
    context: TenantContext,
    *,
    connection_id: uuid.UUID,
    snapshot_id: uuid.UUID,
    candidate_key: str,
    display_name: str,
    confidence_score: float,
    framework_or_runtime: str | None = None,
    entrypoint_evidence_json: dict[str, object] | None = None,
    model_evidence_json: dict[str, object] | None = None,
    prompt_evidence_json: dict[str, object] | None = None,
    tool_evidence_json: dict[str, object] | None = None,
    test_evidence_json: dict[str, object] | None = None,
    trace_hook_evidence_json: dict[str, object] | None = None,
    deployment_evidence_json: dict[str, object] | None = None,
    actor: ActorInfo,
) -> DiscoveredAgentCandidate:
    """Validates and inserts a new, immutable `DiscoveredAgentCandidate`.

    `discovery_status` is never accepted from the caller -- it is always
    the single Release-1 starting value `'candidate'` (see the schema
    module's own docstring); there is no legitimate way to create a
    candidate with any other discovery state, and no route in this chain
    ever transitions it afterward.
    """
    # Proves (connection_id, snapshot_id) is a real pair belonging to the
    # caller's own organization -- reused directly from FTF-04A07, never
    # re-derived (see this module's own docstring).
    get_target_source_snapshot(session, context, connection_id, snapshot_id)

    existing = (
        execute_tenant_scoped(
            session,
            context.organization_id,
            select(DiscoveredAgentCandidate.id).where(
                DiscoveredAgentCandidate.target_source_snapshot_id == snapshot_id,
                DiscoveredAgentCandidate.candidate_key == candidate_key,
            ),
        )
        .scalars()
        .first()
    )
    if existing is not None:
        raise DiscoveredAgentCandidateDuplicateKeyError(
            f"a discovered agent candidate with candidate_key {candidate_key!r} "
            f"already exists for snapshot {snapshot_id}"
        )

    candidate = DiscoveredAgentCandidate(
        organization_id=context.organization_id,
        target_source_snapshot_id=snapshot_id,
        candidate_key=candidate_key,
        display_name=display_name,
        framework_or_runtime=framework_or_runtime,
        entrypoint_evidence_json=entrypoint_evidence_json,
        model_evidence_json=model_evidence_json,
        prompt_evidence_json=prompt_evidence_json,
        tool_evidence_json=tool_evidence_json,
        test_evidence_json=test_evidence_json,
        trace_hook_evidence_json=trace_hook_evidence_json,
        deployment_evidence_json=deployment_evidence_json,
        confidence_score=confidence_score,
        discovery_status="candidate",
    )
    session.add(candidate)
    try:
        session.flush()
    except IntegrityError as exc:
        # Defense-in-depth: a concurrent insert of the same
        # (snapshot, candidate_key) pair that raced past the
        # application-level check above is still caught by the real
        # database UNIQUE constraint, never a raw duplicate row.
        session.rollback()
        raise DiscoveredAgentCandidateDuplicateKeyError(
            f"a discovered agent candidate with candidate_key {candidate_key!r} "
            f"already exists for snapshot {snapshot_id}"
        ) from exc
    write_audit_event(
        session,
        organization_id=context.organization_id,
        actor_type=actor.actor_type,
        actor_id=actor.actor_id,
        action="discovered_agent_candidate.create",
        resource_type="discovered_agent_candidate",
        resource_id=str(candidate.id),
        request_id=actor.request_id,
    )
    return candidate


def get_discovered_agent_candidate(
    session: Session,
    context: TenantContext,
    connection_id: uuid.UUID,
    snapshot_id: uuid.UUID,
    candidate_id: uuid.UUID,
) -> DiscoveredAgentCandidate:
    get_target_source_snapshot(session, context, connection_id, snapshot_id)
    return _fetch_or_raise(session, context, snapshot_id, candidate_id)


def list_discovered_agent_candidates(
    session: Session,
    context: TenantContext,
    connection_id: uuid.UUID,
    snapshot_id: uuid.UUID,
    *,
    limit: int = DEFAULT_PAGE_LIMIT,
    offset: int = 0,
) -> Page:
    get_target_source_snapshot(session, context, connection_id, snapshot_id)
    clamped_limit, clamped_offset = _clamp_pagination(limit, offset)

    total = execute_tenant_scoped(
        session,
        context.organization_id,
        select(func.count())
        .select_from(DiscoveredAgentCandidate)
        .where(DiscoveredAgentCandidate.target_source_snapshot_id == snapshot_id),
    ).scalar_one()

    statement = (
        select(DiscoveredAgentCandidate)
        .where(DiscoveredAgentCandidate.target_source_snapshot_id == snapshot_id)
        .order_by(DiscoveredAgentCandidate.created_at, DiscoveredAgentCandidate.id)
        .limit(clamped_limit)
        .offset(clamped_offset)
    )
    result = execute_tenant_scoped(session, context.organization_id, statement)
    return Page(
        items=list(result.scalars().all()),
        limit=clamped_limit,
        offset=clamped_offset,
        total=int(total),
    )
