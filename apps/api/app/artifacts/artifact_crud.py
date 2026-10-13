"""TrainableArtifact CRUD (FTF-0301, Section 6.7/13/34/45/65/68/77/81).

Create/get/list/edit/accept-license/revoke-license/mark-ready/archive an
organization-scoped `TrainableArtifact`
(`apps/api/app/artifacts/artifact_schema.py`). Every real query against
`trainable_artifacts` goes through the same certified composition
FTF-0201 already proved for exactly this class of resource:

  - `TenantContext`/`tenant_scoped_session_for` (FTF-H003) open the unit
    of work and bind the transaction-local RLS tenant context;
  - `authorize_resource_ownership` (FTF-H003) proves an artifact ID
    belongs to the caller's own authenticated organization *before* any
    row is read or mutated -- denied identically (via
    `ResourceAuthorizationError`) whether the ID does not exist at all or
    belongs to a different organization;
  - `execute_tenant_scoped` (FTF-0105) is what every actual SELECT in this
    module runs through, never a bare `session.execute(...)`/`session.get(...)`.

No new authorization primitive is introduced by this module -- it is
composition only, applying the identical stack FTF-0201 already applied
to `litellm_connections` to this task's own new table.

Role gating (Section 45's own role table): unlike FTF-0201's connections
(where Section 45 names exactly one role, `organization_admin`, so every
route including reads was gated to it alone), Section 45 has no single
sentence naming a role for artifacts. This module follows FTF-0106's own
precedent for that situation (a wide read tier, a narrower write tier)
rather than FTF-0201's single-role lock: `ARTIFACT_WRITE_ROLES` (create/
edit/accept-license/mark-ready/archive) is `{ml_engineer,
organization_admin}` -- Section 45's own "ml_engineer: datasets/training,
no production approval" is the only sentence naming a role for this class
of work, and `organization_admin` retains its blanket admin capability.
`ARTIFACT_READ_ROLES` is every role Section 45 names, matching its own
generic "viewer: read only" baseline applied to a resource with no
dedicated restrictive sentence.

Every mutating operation writes an audit event via FTF-0104's
`write_audit_event` in the *same* transaction as the mutation it
describes, identical to FTF-0201's own discipline.

FTF-0305 (License acceptance workflow) adds `revoke_license` -- a purely
additive new operation, never touching `apply_edits`/`create_artifact`/
`accept_license`/`mark_ready`/`archive_artifact`'s own existing bodies --
see `app.artifacts.license_acceptance_workflow`'s own module docstring
for why this task's own real state-machine logic
(`apply_revoke_license`) lives there, pure and DB-free, rather than being
duplicated here. This module's own pre-existing create/get/list/edit/
accept-license/mark-ready/archive behavior is unchanged; independently
re-verified (this task's own docs/certifications/queue.md submission
entry) against FTF-0301's own no-DB and live-Postgres suites, both still
"all checks passed".
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.artifacts.artifact_schema import (
    TrainableArtifact,
    TrainableArtifactValidationError,
    is_pinned_revision,
    validate_base_model_revision,
    validate_license_id,
    validate_minimum_gpu_profile,
    validate_required_python_packages,
    validate_source_uri,
    validate_weights_digest,
)
from app.artifacts.license_acceptance_workflow import LicenseAcceptanceError
from app.artifacts.license_acceptance_workflow import (
    apply_revoke_license as _apply_revoke_license,
)
from app.auth.audit_event_writer import write_audit_event
from app.auth.organization_workspace_schema import validate_name
from app.auth.resource_authorization import ResourceAuthorizationError, authorize_resource_ownership
from app.auth.tenant_isolation_query_guards import execute_tenant_scoped
from app.db.tenant_context import TenantContext

__all__ = [
    "ArtifactNotFoundError",
    "ArtifactStateError",
    "ActorInfo",
    "apply_accept_license",
    "apply_archive",
    "apply_edits",
    "apply_mark_ready",
    "accept_license",
    "archive_artifact",
    "create_artifact",
    "get_artifact",
    "list_artifacts",
    "mark_ready",
    "revoke_license",
    "update_artifact",
]

_TABLE = TrainableArtifact.__table__
_ID_COLUMN = _TABLE.c.id
_ORG_COLUMN = _TABLE.c.organization_id


class ArtifactNotFoundError(LookupError):
    """No artifact with this ID exists in the caller's own organization --
    raised identically whether the ID is missing entirely or belongs to a
    different organization (this task's own IDOR discipline, matching
    FTF-0201's negative_acceptance precedent)."""


class ArtifactStateError(ValueError):
    """The requested transition is not valid for the artifact's current
    state (e.g. editing a ready/archived artifact, or marking ready
    before the ready-gate is satisfied)."""


@dataclass(frozen=True)
class ActorInfo:
    """Who is performing this mutation, for the audit event it writes and
    (for `accept_license`) for the license-acceptance attribution itself.
    `actor_id` is always the authenticated principal's own `subject`,
    never a client-supplied string -- Section 65.1's "never trusting an
    arbitrary... value" applied here to license-acceptance attribution,
    not only to tenant identity."""

    actor_type: str
    actor_id: str
    request_id: str


def _now() -> datetime:
    return datetime.now(UTC)


def _fetch_or_raise(
    session: Session,
    context: TenantContext,
    artifact_id: uuid.UUID,
    *,
    lock: bool = False,
) -> TrainableArtifact:
    """`lock=True` takes `SELECT ... FOR UPDATE` on the (tenant-scoped) row
    and refreshes any already-loaded copy, so concurrent transitions
    (`accept_license`/`revoke_license`/`mark_ready`/`archive_artifact`/
    edits) serialize and each pure transition sees the committed state
    (FTF-0305-F1)."""
    if not isinstance(artifact_id, uuid.UUID):
        raise ArtifactNotFoundError(f"artifact_id must be a uuid.UUID, got {artifact_id!r}")
    try:
        authorize_resource_ownership(session, context, _TABLE, _ID_COLUMN, _ORG_COLUMN, artifact_id)
    except ResourceAuthorizationError as exc:
        raise ArtifactNotFoundError(str(exc)) from exc
    statement = select(TrainableArtifact).where(TrainableArtifact.id == artifact_id)
    if lock:
        statement = statement.with_for_update().execution_options(populate_existing=True)
    result = execute_tenant_scoped(session, context.organization_id, statement)
    row = result.scalars().one_or_none()
    if row is None:
        raise ArtifactNotFoundError(f"artifact {artifact_id} vanished mid-transaction")
    return row


def create_artifact(
    session: Session,
    context: TenantContext,
    *,
    display_name: str,
    source_type: str,
    source_uri: str,
    model_family: str,
    base_model_revision: str,
    license_id: str,
    trust_remote_code_allowed: bool = False,
    required_python_packages: list[str] | None = None,
    minimum_gpu_profile: dict[str, object] | None = None,
    actor: ActorInfo,
) -> TrainableArtifact:
    """Validates and inserts a new `registered` artifact -- never `ready`
    at creation, since `weights_digest` is not yet resolved and no license
    acceptance has occurred (this task's own negative_acceptance)."""
    artifact = TrainableArtifact(
        organization_id=context.organization_id,
        display_name=display_name,
        source_type=source_type,
        source_uri=source_uri,
        model_family=model_family,
        base_model_revision=base_model_revision,
        weights_digest=None,
        license_id=license_id,
        license_accepted_by=None,
        license_accepted_at=None,
        trust_remote_code_allowed=trust_remote_code_allowed,
        required_python_packages_json=required_python_packages or [],
        minimum_gpu_profile_json=minimum_gpu_profile or {},
        status="registered",
    )
    session.add(artifact)
    session.flush()
    write_audit_event(
        session,
        organization_id=context.organization_id,
        actor_type=actor.actor_type,
        actor_id=actor.actor_id,
        action="trainable_artifact.create",
        resource_type="trainable_artifact",
        resource_id=str(artifact.id),
        request_id=actor.request_id,
    )
    return artifact


def get_artifact(
    session: Session, context: TenantContext, artifact_id: uuid.UUID
) -> TrainableArtifact:
    return _fetch_or_raise(session, context, artifact_id)


def list_artifacts(session: Session, context: TenantContext) -> list[TrainableArtifact]:
    statement = (
        select(TrainableArtifact)
        .where(TrainableArtifact.organization_id == context.organization_id)
        .order_by(TrainableArtifact.created_at)
    )
    result = execute_tenant_scoped(session, context.organization_id, statement)
    return list(result.scalars().all())


def apply_edits(
    artifact: TrainableArtifact,
    *,
    display_name: str | None,
    source_uri: str | None,
    base_model_revision: str | None,
    model_family: str | None,
    weights_digest: str | None,
    license_id: str | None,
    trust_remote_code_allowed: bool | None,
    required_python_packages: list[str] | None,
    minimum_gpu_profile: dict[str, object] | None,
) -> bool:
    """The pure, DB-free core of `update_artifact`. Refuses
    (`ArtifactStateError`) to touch an artifact that is not `registered`:
    this task's own acceptance clause ("immutable identity after
    resolution") means a `ready` artifact's identity is frozen, and an
    `archived` artifact is a closed record -- register a new artifact
    instead, the same "closed record" precedent FTF-0201 established for
    a revoked connection."""
    if artifact.status != "registered":
        raise ArtifactStateError(
            f"artifact {artifact.id} is {artifact.status!r} and cannot be edited -- "
            "only a 'registered' artifact's identity may still change; register a "
            "new artifact instead"
        )

    changed = False
    if display_name is not None:
        artifact.display_name = validate_name(display_name)
        changed = True
    if source_uri is not None:
        artifact.source_uri = validate_source_uri(artifact.source_type, source_uri)
        changed = True
    if base_model_revision is not None:
        artifact.base_model_revision = validate_base_model_revision(base_model_revision)
        changed = True
    if model_family is not None:
        if not isinstance(model_family, str) or not model_family.strip():
            raise TrainableArtifactValidationError("model_family must be a non-empty string")
        artifact.model_family = model_family
        changed = True
    if weights_digest is not None:
        artifact.weights_digest = validate_weights_digest(weights_digest)
        changed = True
    if license_id is not None:
        artifact.license_id = validate_license_id(license_id)
        changed = True
    if trust_remote_code_allowed is not None:
        artifact.trust_remote_code_allowed = bool(trust_remote_code_allowed)
        changed = True
    if required_python_packages is not None:
        artifact.required_python_packages_json = validate_required_python_packages(
            required_python_packages
        )
        changed = True
    if minimum_gpu_profile is not None:
        artifact.minimum_gpu_profile_json = validate_minimum_gpu_profile(minimum_gpu_profile)
        changed = True

    if not changed:
        raise TrainableArtifactValidationError("update_artifact requires at least one field")
    artifact.updated_at = _now()
    return changed


def apply_accept_license(artifact: TrainableArtifact, *, accepted_by: str) -> bool:
    """The pure, DB-free core of `accept_license`. Idempotent: a second
    acceptance (by the same or a different actor) is a no-op -- once
    accepted, the attribution is immutable, matching this task's own
    "immutable identity after resolution" theme applied to license
    attribution rather than only to the artifact's source identity.
    Refuses (`ArtifactStateError`) on an `archived` artifact -- a closed
    record accepts no further mutation."""
    if artifact.status == "archived":
        raise ArtifactStateError(f"artifact {artifact.id} is archived and cannot accept a license")
    if artifact.license_accepted_by is not None:
        return False
    if not accepted_by:
        raise TrainableArtifactValidationError("accepted_by must be a non-empty string")
    artifact.license_accepted_by = accepted_by
    artifact.license_accepted_at = _now()
    artifact.updated_at = artifact.license_accepted_at
    return True


def apply_mark_ready(artifact: TrainableArtifact) -> bool:
    """The pure, DB-free ready-transition gate -- this task's own
    negative_acceptance made executable: "Unsafe URI, unknown owner or
    mutable unresolved revision cannot enter training as ready."

    1. Unsafe URI: `source_uri` is re-validated against its own
       `source_type` (defense in depth -- it is already validated at
       construction/edit time, so this can only fail if a row was written
       by a path that bypassed this module entirely).
    2. Unknown owner: `license_accepted_by`/`license_accepted_at` must
       both be set (Section 65.1's "never trusting an arbitrary... value"
       -- these are only ever set by `apply_accept_license` from a real
       authenticated actor, never a client-supplied field).
    3. Mutable unresolved revision: `weights_digest` must be a valid
       `sha256:` + 64 lowercase hex digest, re-checked here with the shared
       `validate_weights_digest` (a row loaded from the database skips the
       write-time check) AND
       `base_model_revision` must be a pinned, non-floating identifier
       (`is_pinned_revision`).

    Idempotent: an already-`ready` artifact is a no-op (`False`).
    Refuses (`ArtifactStateError`) on an `archived` artifact, and on any
    status other than `registered` (including a non-str one)."""
    if artifact.status == "archived":
        raise ArtifactStateError(f"artifact {artifact.id} is archived and cannot become ready")
    if artifact.status == "ready":
        return False
    if type(artifact.status) is not str or artifact.status != "registered":
        # Only `registered` may transition. An unrecognised or non-str
        # status is refused, never overwritten with `ready`.
        raise ArtifactStateError(
            f"artifact {artifact.id} has unrecognised status {artifact.status!r} and "
            "cannot become ready"
        )

    reasons: list[str] = []
    try:
        validate_source_uri(artifact.source_type, artifact.source_uri)
    except TrainableArtifactValidationError:
        reasons.append("source_uri is unsafe")
    if artifact.license_accepted_by is None or artifact.license_accepted_at is None:
        reasons.append("license has not been accepted by a known, authenticated actor")
    try:
        validate_weights_digest(artifact.weights_digest)
    except TrainableArtifactValidationError:
        reasons.append("weights_digest has not been resolved to a valid sha256 digest")
    if not is_pinned_revision(artifact.base_model_revision):
        reasons.append("base_model_revision is a mutable, unresolved alias, not a pinned identity")

    if reasons:
        raise ArtifactStateError(
            f"artifact {artifact.id} cannot become ready: " + "; ".join(reasons)
        )

    artifact.status = "ready"
    artifact.updated_at = _now()
    return True


def apply_archive(artifact: TrainableArtifact) -> bool:
    """The pure, DB-free core of `archive_artifact`: idempotent, mirrors
    FTF-0201's own `apply_revoke`."""
    if artifact.status == "archived":
        return False
    artifact.status = "archived"
    artifact.archived_at = _now()
    artifact.updated_at = artifact.archived_at
    return True


def update_artifact(
    session: Session,
    context: TenantContext,
    artifact_id: uuid.UUID,
    *,
    display_name: str | None = None,
    source_uri: str | None = None,
    base_model_revision: str | None = None,
    model_family: str | None = None,
    weights_digest: str | None = None,
    license_id: str | None = None,
    trust_remote_code_allowed: bool | None = None,
    required_python_packages: list[str] | None = None,
    minimum_gpu_profile: dict[str, object] | None = None,
    actor: ActorInfo,
) -> TrainableArtifact:
    artifact = _fetch_or_raise(session, context, artifact_id, lock=True)
    apply_edits(
        artifact,
        display_name=display_name,
        source_uri=source_uri,
        base_model_revision=base_model_revision,
        model_family=model_family,
        weights_digest=weights_digest,
        license_id=license_id,
        trust_remote_code_allowed=trust_remote_code_allowed,
        required_python_packages=required_python_packages,
        minimum_gpu_profile=minimum_gpu_profile,
    )
    write_audit_event(
        session,
        organization_id=context.organization_id,
        actor_type=actor.actor_type,
        actor_id=actor.actor_id,
        action="trainable_artifact.update",
        resource_type="trainable_artifact",
        resource_id=str(artifact.id),
        request_id=actor.request_id,
    )
    return artifact


def accept_license(
    session: Session,
    context: TenantContext,
    artifact_id: uuid.UUID,
    *,
    actor: ActorInfo,
) -> TrainableArtifact:
    """`accepted_by` is always `actor.actor_id` -- the real authenticated
    principal's own subject -- never a client-supplied field (see this
    module's own docstring)."""
    artifact = _fetch_or_raise(session, context, artifact_id, lock=True)
    changed = apply_accept_license(artifact, accepted_by=actor.actor_id)
    if changed:
        write_audit_event(
            session,
            organization_id=context.organization_id,
            actor_type=actor.actor_type,
            actor_id=actor.actor_id,
            action="trainable_artifact.accept_license",
            resource_type="trainable_artifact",
            resource_id=str(artifact.id),
            request_id=actor.request_id,
        )
    return artifact


def revoke_license(
    session: Session,
    context: TenantContext,
    artifact_id: uuid.UUID,
    *,
    actor: ActorInfo,
) -> TrainableArtifact:
    """FTF-0305's own real, live-wired revocation surface. Delegates the
    pure state transition to `license_acceptance_workflow.apply_revoke_license`
    and translates its `LicenseAcceptanceError` (raised only on an
    archived artifact) into this module's own `ArtifactStateError`, so
    every mutating route in `artifact_routes.py` keeps catching exactly
    one state-conflict exception type for a 409, matching
    `accept_license`/`mark_ready`'s own existing convention."""
    artifact = _fetch_or_raise(session, context, artifact_id, lock=True)
    try:
        changed = _apply_revoke_license(artifact)
    except LicenseAcceptanceError as exc:
        raise ArtifactStateError(str(exc)) from exc
    if changed:
        write_audit_event(
            session,
            organization_id=context.organization_id,
            actor_type=actor.actor_type,
            actor_id=actor.actor_id,
            action="trainable_artifact.revoke_license",
            resource_type="trainable_artifact",
            resource_id=str(artifact.id),
            request_id=actor.request_id,
        )
    return artifact


def mark_ready(
    session: Session,
    context: TenantContext,
    artifact_id: uuid.UUID,
    *,
    actor: ActorInfo,
) -> TrainableArtifact:
    artifact = _fetch_or_raise(session, context, artifact_id, lock=True)
    changed = apply_mark_ready(artifact)
    if changed:
        write_audit_event(
            session,
            organization_id=context.organization_id,
            actor_type=actor.actor_type,
            actor_id=actor.actor_id,
            action="trainable_artifact.mark_ready",
            resource_type="trainable_artifact",
            resource_id=str(artifact.id),
            request_id=actor.request_id,
        )
    return artifact


def archive_artifact(
    session: Session,
    context: TenantContext,
    artifact_id: uuid.UUID,
    *,
    actor: ActorInfo,
) -> TrainableArtifact:
    artifact = _fetch_or_raise(session, context, artifact_id, lock=True)
    if not apply_archive(artifact):
        return artifact
    write_audit_event(
        session,
        organization_id=context.organization_id,
        actor_type=actor.actor_type,
        actor_id=actor.actor_id,
        action="trainable_artifact.archive",
        resource_type="trainable_artifact",
        resource_id=str(artifact.id),
        request_id=actor.request_id,
    )
    return artifact
