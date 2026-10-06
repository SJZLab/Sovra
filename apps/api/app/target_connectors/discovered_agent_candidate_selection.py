"""Candidate selection + persisted-audit action (FTF-04A18, Section 6.8C/
6.24/7A.5/45/65-67/77/81).

Section 7A.5's own closing sentence ("The system must never silently
select the first/highest-confidence candidate") is this task's own real
job: FTF-04A17's own `DiscoveredAgentCandidate` row is immutable by
construction (`discovered_agent_candidate_schema.py`'s own docstring;
its real Alembic migration, `0014_discovered_agent_candidates.py`, grants
the `sovra_app` role SELECT/INSERT only on that table -- never UPDATE --
so there is no code path, in this module or any other, that could durably
flip a candidate's own `discovery_status` to a "selected" value even if
it wanted to). This task's own acceptance clause ("UI lists multiple
detected agents and requires explicit selected ID with persisted audit")
reads literally, not narrowed: the persistence this task owns is the
*audit record of the selection act itself*, via FTF-0104's own already-
migrated, already-certified `write_audit_event` -- not a new mutable
"current selection" column on any existing table, which this task's own
`database_changes` clause ("No independent schema change. Consume frozen
contract...") forbids adding. `audit_events` already grants `sovra_app`
INSERT (see `0002_audit_event_writer.py`), so recording a selection needs
no migration at all -- this is "consuming the frozen contract" read
literally, the same resolution the default-resolution rule in CLAUDE.md
directs when the architecture already fixes the shape of an answer.

Path refined from the task packet's own frontend-only `files` list
(`apps/web/components/target-application/
candidate_selection_ui_no_auto_selection.tsx` plus its own `.spec.ts`) to
also include this real backend module plus
`discovered_agent_candidate_selection_routes.py` -- the same "packet
`files` is an initial owner-path suggestion, not a cap on what a real
acceptance clause needs" precedent FTF-04A04's own real
`github_branch_ref_resolver.py`/`_crud.py`/`_routes.py` triad already
established for an identically-shaped "packet names only a `.tsx`, but
the clause needs a real backend write" task.

`select_discovered_agent_candidate` is the only function here. It:
1. Reuses FTF-04A07's own already-CERTIFIED `get_target_source_snapshot`
   to prove `(connection_id, snapshot_id)` is a real pair in the caller's
   own organization (never re-derived).
2. Reuses FTF-04A17's own already-CERTIFIED `get_discovered_agent_candidate`
   to prove `candidate_id` is a real row under that exact snapshot (never
   a different snapshot/organization -- the same IDOR discipline every
   prior task in this chain already established).
3. Refuses with `StaleSnapshotCandidateError` when `snapshot_id` is not the
   connection's latest snapshot, defined server-side against the real
   database: some *other* snapshot of the same connection either names
   `snapshot_id` as the one it supersedes (`supersedes_snapshot_id`, set
   once at INSERT time on the *new* row) or was captured strictly after
   it (`captured_at`). The second clause covers a newer snapshot created
   without the optional supersedes link -- this task's own real "stale
   candidate from prior snapshot is rejected" negative_acceptance half,
   checked against the real database, never a client-supplied flag.
4. Writes one real, append-only `AuditEvent` row
   (`action="discovered_agent_candidate.selected"`) in the *same*
   transaction as the check above, identical to every prior task's own
   write-audit-event-in-the-same-transaction discipline in this chain --
   a rollback can never leave behind a misleading "it was selected" audit
   trail. `selected_at` in the returned `CandidateSelectionRecord` is the
   real audit row's own `created_at`, read back from the ORM-managed
   object after `session.flush()`, never a value computed in Python
   before the write actually happens.

"Highest score/first result cannot be preselected" (this task's own other
negative_acceptance half) is met structurally, not just by this module:
`candidate_id` is always a caller-supplied, explicit argument -- there is
no code path here, or in the UI component this module backs, that reads
`confidence_score` at all, let alone sorts/filters by it to pick a
default. See `candidate_selection_ui_no_auto_selection.tsx`'s own
docstring for the UI-side half of this guarantee (no pre-checked/
default-selected option on initial render).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.auth.audit_event_writer import write_audit_event
from app.auth.tenant_isolation_query_guards import execute_tenant_scoped
from app.db.tenant_context import TenantContext
from app.target_connectors.discovered_agent_candidate_crud import (
    ActorInfo,
    DiscoveredAgentCandidateNotFoundError,
    TargetApplicationConnectionNotFoundError,
    TargetSourceSnapshotNotFoundError,
    get_discovered_agent_candidate,
)
from app.target_connectors.target_source_snapshot_crud import get_target_source_snapshot
from app.target_connectors.target_source_snapshot_schema import TargetSourceSnapshot

__all__ = [
    "CANDIDATE_SELECTED_ACTION",
    "ActorInfo",
    "CandidateSelectionRecord",
    "DiscoveredAgentCandidateNotFoundError",
    "StaleSnapshotCandidateError",
    "TargetApplicationConnectionNotFoundError",
    "TargetSourceSnapshotNotFoundError",
    "select_discovered_agent_candidate",
]

CANDIDATE_SELECTED_ACTION = "discovered_agent_candidate.selected"
_CANDIDATE_RESOURCE_TYPE = "discovered_agent_candidate"


class StaleSnapshotCandidateError(ValueError):
    """The named snapshot has already been superseded by a newer real
    snapshot of the same connection (see this module's own docstring) --
    a selection against it would durably record a decision about source
    code that is no longer the connection's real current state. This
    task's own real "stale candidate from prior snapshot is rejected"
    negative_acceptance half."""


@dataclass(frozen=True)
class CandidateSelectionRecord:
    """The real, durable result of a selection action. `selected_at` is
    the real `AuditEvent` row's own `created_at`, read back after it is
    actually written -- never a value computed before the write, so a
    caller can never report a selection as persisted before it is."""

    candidate_id: uuid.UUID
    target_source_snapshot_id: uuid.UUID
    actor_id: str
    selected_at: datetime


def _is_snapshot_stale(
    session: Session, organization_id: uuid.UUID, snapshot: TargetSourceSnapshot
) -> bool:
    """True iff `snapshot` is no longer the connection's latest snapshot,
    decided server-side against the real database: some *other* snapshot
    of the same connection either names it as the one it supersedes, or
    was captured strictly after it. The second clause matters because
    `supersedes_snapshot_id` is an optional, client-supplied link -- a
    newer snapshot created without it must still make this one stale.
    RLS on `target_source_snapshots` already restricts every row this can
    see to the caller's own organization; the explicit `organization_id`
    filter is kept for defense-in-depth and readability."""
    statement = select(TargetSourceSnapshot.id).where(
        TargetSourceSnapshot.organization_id == organization_id,
        TargetSourceSnapshot.target_application_connection_id
        == snapshot.target_application_connection_id,
        TargetSourceSnapshot.id != snapshot.id,
        or_(
            TargetSourceSnapshot.supersedes_snapshot_id == snapshot.id,
            TargetSourceSnapshot.captured_at > snapshot.captured_at,
        ),
    )
    return execute_tenant_scoped(session, organization_id, statement).first() is not None


def select_discovered_agent_candidate(
    session: Session,
    context: TenantContext,
    connection_id: uuid.UUID,
    snapshot_id: uuid.UUID,
    candidate_id: uuid.UUID,
    *,
    actor: ActorInfo,
) -> CandidateSelectionRecord:
    """Validates and records one explicit candidate-selection act.

    Raises `TargetApplicationConnectionNotFoundError`/
    `TargetSourceSnapshotNotFoundError`/
    `DiscoveredAgentCandidateNotFoundError` for a missing/cross-tenant/
    cross-parent id (identical IDOR handling to every prior task in this
    chain), `StaleSnapshotCandidateError` for a superseded snapshot. Never
    silently falls back to a "best guess" candidate on any failure.
    """
    # Proves (connection_id, snapshot_id) is real and in the caller's own
    # organization -- reused directly from FTF-04A07, never re-derived.
    snapshot = get_target_source_snapshot(session, context, connection_id, snapshot_id)

    if _is_snapshot_stale(session, context.organization_id, snapshot):
        raise StaleSnapshotCandidateError(
            f"target source snapshot {snapshot_id} is not the connection's latest snapshot (a "
            "newer snapshot of the same connection exists); selection must be made "
            "against the connection's current snapshot"
        )

    # Proves candidate_id is a real row under exactly this snapshot --
    # reused directly from FTF-04A17, never re-derived.
    candidate = get_discovered_agent_candidate(
        session, context, connection_id, snapshot_id, candidate_id
    )

    audit_event = write_audit_event(
        session,
        organization_id=context.organization_id,
        actor_type=actor.actor_type,
        actor_id=actor.actor_id,
        action=CANDIDATE_SELECTED_ACTION,
        resource_type=_CANDIDATE_RESOURCE_TYPE,
        resource_id=str(candidate.id),
        request_id=actor.request_id,
        details={
            "target_source_snapshot_id": str(snapshot_id),
            "target_application_connection_id": str(connection_id),
        },
    )
    session.flush()
    return CandidateSelectionRecord(
        candidate_id=candidate.id,
        target_source_snapshot_id=candidate.target_source_snapshot_id,
        actor_id=actor.actor_id,
        selected_at=audit_event.created_at,
    )
