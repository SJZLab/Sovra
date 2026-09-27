"""ManifestEvidence CRUD (FTF-04A20, Section 6.8E/7A.6-7A.7/45/65/66/77/81).

Create/get/list a project+manifest-scoped `ManifestEvidence` row
(`apps/api/app/target_connectors/manifest_evidence_schema.py`). See that
module's own docstring for the full per-`evidence_type` field-shape design
this CRUD layer relies on (enforced there, in `ManifestEvidence.__init__`
and this module's own migration's real `CHECK` constraints) and for why
the one real cross-table check this layer alone can perform --
"cross-snapshot evidence ... rejected" -- lives here instead.

Every real query goes through the same certified composition FTF-04A19
(this task's own direct dependency) already proved for exactly this class
of resource:

  - `TenantContext`/`tenant_scoped_session_for` (FTF-H003) open the unit
    of work and bind the transaction-local RLS tenant context;
  - `authorize_resource_ownership` (FTF-H003) proves a manifest ID belongs
    to the caller's own authenticated organization *and* project before
    any row is read or mutated -- reused directly via FTF-04A19's own
    `get_agent_manifest` (Section 77.1 reuse-first: never a second,
    hand-duplicated copy of that same fetch-and-authorize logic);
  - `execute_tenant_scoped` (FTF-0105) is what every actual SELECT in this
    module runs through, never a bare `session.execute(...)`.

## Cross-snapshot check ("cross-snapshot evidence ... rejected")

`create_manifest_evidence` fetches the parent `AgentManifest` (via
`get_agent_manifest`, which already proves it belongs to the caller's own
organization and project) and then its own real `TargetSourceSnapshot`'s
`commit_sha`. For every `evidence_type` that requires
`repository_commit_sha` (every value except `user_input` -- see the
schema module's own docstring), the caller-supplied `repository_commit_sha`
must equal that snapshot's own real `commit_sha` exactly, or the write is
refused with `CrossSnapshotEvidenceError`. This is this task's own real,
disclosed enforcement of "cross-snapshot evidence ... rejected": evidence
must be anchored to the exact snapshot the manifest itself was actually
built from, never a different, stale, or invented commit.

Role gating: reused directly from `agent_manifest_routes.py`'s own
`AGENT_MANIFEST_READ_ROLES`/`AGENT_MANIFEST_WRITE_ROLES` (Section 45 names
no separate sentence for "evidence" -- it is squarely part of the same
manifest-curation surface FTF-04A19 already gated).

Every mutating operation (`create_manifest_evidence` alone -- this
resource is immutable once written, see the schema module's own
docstring) writes an audit event via FTF-0104's `write_audit_event` in the
*same* transaction as the insert it describes, identical to every prior
task's own discipline in this chain.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.audit_event_writer import write_audit_event
from app.auth.resource_authorization import ResourceAuthorizationError, authorize_resource_ownership
from app.auth.tenant_isolation_query_guards import execute_tenant_scoped
from app.db.tenant_context import TenantContext
from app.target_connectors.agent_manifest_crud import (
    ActorInfo,
    AgentManifestNotFoundError,
    get_agent_manifest,
)
from app.target_connectors.agent_manifest_schema import AgentManifest
from app.target_connectors.manifest_evidence_schema import ManifestEvidence
from app.target_connectors.target_source_snapshot_schema import TargetSourceSnapshot

__all__ = [
    "ActorInfo",
    "AgentManifestNotFoundError",
    "CrossSnapshotEvidenceError",
    "ManifestEvidenceNotFoundError",
    "create_manifest_evidence",
    "get_manifest_evidence",
    "list_manifest_evidence",
]

_TABLE = ManifestEvidence.__table__
_ID_COLUMN = _TABLE.c.id


class ManifestEvidenceNotFoundError(LookupError):
    """No manifest evidence row with this ID exists in the caller's own
    organization *and* manifest -- identical whether the ID is missing
    entirely, belongs to a different organization, or belongs to a
    different manifest within the caller's own organization."""


class CrossSnapshotEvidenceError(ValueError):
    """The caller-supplied `repository_commit_sha` genuinely differs from
    the exact commit the parent manifest's own `TargetSourceSnapshot` was
    actually captured at -- see this module's own docstring."""


def _fetch_manifest_snapshot_commit_sha(
    session: Session, context: TenantContext, manifest: AgentManifest
) -> str:
    statement = select(TargetSourceSnapshot).where(
        TargetSourceSnapshot.id == manifest.target_source_snapshot_id
    )
    result = execute_tenant_scoped(session, context.organization_id, statement)
    snapshot = result.scalars().one_or_none()
    if snapshot is None:
        raise AgentManifestNotFoundError(
            f"the target source snapshot referenced by manifest {manifest.id} vanished"
        )
    return snapshot.commit_sha


def _fetch_or_raise(
    session: Session, context: TenantContext, manifest_id: uuid.UUID, evidence_id: uuid.UUID
) -> ManifestEvidence:
    if not isinstance(evidence_id, uuid.UUID):
        raise ManifestEvidenceNotFoundError(f"evidence_id must be a uuid.UUID, got {evidence_id!r}")
    try:
        authorize_resource_ownership(
            session, context, _TABLE, _ID_COLUMN, _TABLE.c.organization_id, evidence_id
        )
    except ResourceAuthorizationError as exc:
        raise ManifestEvidenceNotFoundError(str(exc)) from exc
    statement = select(ManifestEvidence).where(ManifestEvidence.id == evidence_id)
    result = execute_tenant_scoped(session, context.organization_id, statement)
    row = result.scalars().one_or_none()
    if row is None:
        raise ManifestEvidenceNotFoundError(
            f"manifest evidence {evidence_id} vanished mid-transaction"
        )
    if row.agent_manifest_id != manifest_id:
        raise ManifestEvidenceNotFoundError(
            f"manifest evidence {evidence_id} does not belong to manifest {manifest_id}"
        )
    return row


def create_manifest_evidence(
    session: Session,
    context: TenantContext,
    *,
    project_id: uuid.UUID,
    manifest_id: uuid.UUID,
    actor: ActorInfo,
    field_path: str,
    evidence_type: str,
    evidence_json: dict[str, object],
    repository_commit_sha: str | None = None,
    file_path: str | None = None,
    line_start: int | None = None,
    line_end: int | None = None,
    symbol: str | None = None,
    content_hash: str | None = None,
    confidence_score: object | None = None,
) -> ManifestEvidence:
    """Creates a new, immutable `ManifestEvidence` row for a real manifest
    the caller's own organization+project actually owns. See this module's
    own docstring for the real cross-snapshot check performed here."""
    manifest = get_agent_manifest(session, context, project_id, manifest_id)

    if repository_commit_sha is not None:
        snapshot_commit_sha = _fetch_manifest_snapshot_commit_sha(session, context, manifest)
        if repository_commit_sha != snapshot_commit_sha:
            raise CrossSnapshotEvidenceError(
                f"repository_commit_sha {repository_commit_sha!r} does not match the "
                f"exact commit {snapshot_commit_sha!r} manifest {manifest_id}'s own "
                "target_source_snapshot was actually captured at -- this task's own "
                "negative_acceptance: cross-snapshot evidence is rejected"
            )

    evidence = ManifestEvidence(
        organization_id=context.organization_id,
        agent_manifest_id=manifest.id,
        field_path=field_path,
        evidence_type=evidence_type,
        repository_commit_sha=repository_commit_sha,
        file_path=file_path,
        line_start=line_start,
        line_end=line_end,
        symbol=symbol,
        content_hash=content_hash,
        confidence_score=confidence_score,
        evidence_json=evidence_json,
    )
    session.add(evidence)
    session.flush()
    write_audit_event(
        session,
        organization_id=context.organization_id,
        actor_type=actor.actor_type,
        actor_id=actor.actor_id,
        action="manifest_evidence.create",
        resource_type="manifest_evidence",
        resource_id=str(evidence.id),
        request_id=actor.request_id,
    )
    return evidence


def get_manifest_evidence(
    session: Session,
    context: TenantContext,
    project_id: uuid.UUID,
    manifest_id: uuid.UUID,
    evidence_id: uuid.UUID,
) -> ManifestEvidence:
    get_agent_manifest(session, context, project_id, manifest_id)
    return _fetch_or_raise(session, context, manifest_id, evidence_id)


def list_manifest_evidence(
    session: Session, context: TenantContext, project_id: uuid.UUID, manifest_id: uuid.UUID
) -> list[ManifestEvidence]:
    manifest = get_agent_manifest(session, context, project_id, manifest_id)
    statement = (
        select(ManifestEvidence)
        .where(ManifestEvidence.agent_manifest_id == manifest.id)
        .order_by(ManifestEvidence.created_at)
    )
    result = execute_tenant_scoped(session, context.organization_id, statement)
    return list(result.scalars().all())
