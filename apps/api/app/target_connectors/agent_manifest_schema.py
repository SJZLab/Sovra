"""AgentManifest domain schema (FTF-04A19, Section 6.8D/7A.6-7A.7/66/77/81).

A project-scoped `AgentManifest` -- Section 6.8D's own entity, "the
canonical contract used by training and evaluation." This task's own job
is exactly the storage/validation shape of that record plus its real
versioning/hash/status-transition mechanics -- not per-field evidence
provenance (Section 6.8E's own `ManifestEvidence`, FTF-04A20, a separate,
dependency-gated later task that names this one as *its own* dependency,
not the other way around), not the review/edit UI (FTF-04A21), not the
full completeness/tool-schema/security-profile validation engine Section
7A.7's own checklist describes (FTF-04A23, which itself lists FTF-04A21/
FTF-04A22/FTF-04A25/FTF-H008 as dependencies this task does not have),
and not the repository-drift-driven resupersession workflow (FTF-04A27
"Repository sync + manifest drift detector"). Declaring any of that as a
real, enforced capability here without a real backing implementation
would be exactly the "placeholder endpoint" Law 11 forbids.

Path refined from the task packet's own generic suggestion
(`packages/target-connectors/agentmanifest_schema_versioning_hash.py`) to
`apps/api/app/target_connectors/agent_manifest_schema.py`, matching
FTF-04A01/FTF-04A07's own real `apps/api/app/target_connectors/*_schema.py`
layout precedent exactly.

Field list scoped to Section 6.8D's own literal fields, minus the two
audit-timestamp-shaped fields that section does not actually list
(`updated_at` is not one of 6.8D's own fields -- a manifest's own
`status`/content changes are already fully described by
`created_at`/`approved_at`, and every mutation writes a real audit event,
Section 45's usual discipline), plus the one Section 65.1 "all
organization-scoped entities carry organization_id" inherited addition
every prior table in this chain already applies.

## Versioning without a `supersedes_manifest_id` field

Section 6.8D lists no `supersedes_manifest_id`/parent-link field at all
(contrast with FTF-04A07's own `TargetSourceSnapshot.supersedes_snapshot_id`,
a real field Section 6.8B does list). This task's own real reading, not
invented: at most one `AgentManifest` per project may be `status='locked'`
at any time (a real, database-level partial unique index enforces this --
see this module's own migration), and locking a new manifest for a
project that already has one locked transitions the *previous* one to
`superseded` in the same transaction (`agent_manifest_crud.py`'s own
`lock_agent_manifest`) -- version ordering (`version`, unique per project,
monotonically increasing) is what identifies the supersession chain, not
an invented extra column.

## Status transitions (this task's own acceptance: "reviewed/locked/
superseded transitions")

`ALLOWED_STATUSES` is exactly Section 6.8D's own four-value set, read in
its own literal listed order as the legal forward chain: `draft` ->
`review_required` -> `locked` -> `superseded`. `validate_status_transition`
enforces this as a strict, one-directional graph -- no skip, no reverse,
no self-loop. There is no `locked` -> `draft` "send back for edits" path
here; a real "review/edit UI" round-trip loop (if any) is FTF-04A21's own,
separate, later job.

## Hash ("canonical manifest schema, hash")

`compute_manifest_sha256` hashes exactly `CONTENT_FIELDS` below -- the
manifest's own real, substantive content (which snapshot it was built
from, plus every Section 6.8D content field) -- never `id`/`version`/
`status`/`created_by`/`approved_by`/`approved_at`/`created_at`, which are
envelope/audit metadata, not manifest *content*. Canonicalization is
`json.dumps(..., sort_keys=True, separators=(",", ":"), default=str)` --
deterministic regardless of caller-supplied key/field order, the same
"reproducible hash over a normalized representation" discipline
`artifact_schema.py`'s own `weights_digest` shape and
`tool_definition_schema.py`'s own `schema_hash` field already assume for
this codebase's other content-addressed fields (neither of which this
module reimplements; each hashes its own distinct content). Per Section
6.8D's own "locking computes manifest_sha256" rule, this module's own
`AgentManifest.__init__`/migration never accept a caller-supplied
`manifest_sha256` for a `draft`/`review_required` row (structurally
`None`) -- it exists only from the moment a row is `locked` onward (a real
`CHECK` constraint enforces this pairing -- see this module's own
migration).

## Negative acceptance ("Locked manifest mutation ... blocks activation")

This compressed sentence splits into two real, distinct enforcement
halves, disclosed here, not hidden, the same way every prior task in this
chain disclosed its own reading of a similarly compressed sentence:

1. "Locked manifest mutation ... blocks [it]": once `status` is `locked`
   or `superseded`, no content field may change at all. Unlike
   FTF-04A07/FTF-1001's own tables (`SELECT`/`INSERT`-only grants -- there
   is *no* legitimate mutation for those resources, ever), `agent_manifests`
   genuinely needs `UPDATE` while `draft`/`review_required` (content edits,
   and the `draft`->`review_required`->`locked` transitions themselves are
   updates) -- so this half cannot be a blanket DB grant restriction the
   way those two tasks used. It is enforced in `agent_manifest_crud.py`'s
   own `update_agent_manifest_content`/`submit_for_review`/`lock_agent_manifest`,
   which always re-fetch and check the row's *current* `status` first
   (never trust a caller-supplied "I know it's still draft" claim), the
   same "re-validate, never merely trust an earlier check" discipline
   `model_version_crud.py`'s own `apply_mark_ready`-adjacent checks already
   established for this codebase.
2. "missing critical evidence/model/runtime tuple blocks activation": this
   task's own literal three-item list maps directly onto three real
   Section 6.8D fields already on this row -- evidence:
   `agent_entrypoint_json` (what a real analysis run actually found), model:
   `current_model_binding_json`, runtime tuple: `runtime_dependencies_json`.
   `CRITICAL_LOCK_FIELDS` names exactly these three; `lock_agent_manifest`
   refuses to transition to `locked` ("activation") unless every one of
   them is non-null. The *fuller* Section 7A.7 checklist (tool-schema
   validity, security-profile compatibility, forbidden-action explicitness,
   etc.) is FTF-04A23's own, separate, later, dependency-gated validation
   engine -- this task's own negative_acceptance names only "evidence/
   model/runtime tuple," not that whole later checklist.

Real forward Alembic migration
(`apps/api/alembic/versions/0015_agent_manifests.py`) FORCEs row-level
security on `agent_manifests` scoped to the same
`app.current_organization_id` tenant context every prior migration in
this chain already established, grants `SELECT`/`INSERT`/`UPDATE` (never
`DELETE` -- a manifest is superseded, never removed), adds two real
composite foreign keys -- `(organization_id, project_id)` ->
`projects(organization_id, id)` (reusing the already-existing unique
constraint on `projects`, the same FTF-1001 precedent) and
`(organization_id, target_source_snapshot_id)` ->
`target_source_snapshots(organization_id, id)` (reusing FTF-04A07's own
already-added `UNIQUE (organization_id, id)` constraint on that table) --
and a real partial unique index enforcing "at most one locked manifest per
project," the real, database-level half of this task's own supersession
mechanic described above.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime
from hashlib import sha256

from sqlalchemy import CheckConstraint, DateTime, ForeignKeyConstraint, Integer, String, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.auth.organization_workspace_schema import Base

__all__ = [
    "ALLOWED_STATUSES",
    "ALLOWED_STATUS_TRANSITIONS",
    "AGENT_NAME_MAX_LENGTH",
    "CONTENT_FIELDS",
    "CRITICAL_LOCK_FIELDS",
    "AgentManifest",
    "AgentManifestValidationError",
    "compute_manifest_sha256",
    "validate_agent_name_or_none",
    "validate_status",
    "validate_status_transition",
]

AGENT_NAME_MAX_LENGTH = 200

# Section 6.8D's own four-value `status` set, in its own literal listed
# order -- see this module's own docstring for why that order is read as
# the legal forward chain.
ALLOWED_STATUSES = ("draft", "review_required", "locked", "superseded")

# The strict, one-directional transition graph this task's own acceptance
# ("reviewed/locked/superseded transitions") describes -- no skip, no
# reverse, no self-loop.
ALLOWED_STATUS_TRANSITIONS: dict[str, frozenset[str]] = {
    "draft": frozenset({"review_required"}),
    "review_required": frozenset({"locked"}),
    "locked": frozenset({"superseded"}),
    "superseded": frozenset(),
}

# Section 6.8D's own real content fields -- the manifest's own substantive
# meaning, hashed by `compute_manifest_sha256`. Deliberately excludes
# `id`/`project_id`/`version`/`status`/`manifest_sha256`/`created_by`/
# `approved_by`/`approved_at`/`created_at`: envelope/audit metadata, not
# content (see this module's own docstring).
CONTENT_FIELDS: tuple[str, ...] = (
    "target_source_snapshot_id",
    "agent_name",
    "agent_entrypoint_json",
    "current_model_binding_json",
    "system_prompt_refs_json",
    "tool_contracts_json",
    "input_contract_json",
    "output_contract_json",
    "mandate_json",
    "forbidden_actions_json",
    "permission_model_json",
    "required_context_json",
    "runtime_dependencies_json",
    "evaluation_harness_json",
    "trace_source_contract_json",
    "deployment_contract_json",
)

# This task's own literal "missing critical evidence/model/runtime tuple"
# negative_acceptance, mapped to exactly three real fields -- see this
# module's own docstring point 2 for the full mapping.
CRITICAL_LOCK_FIELDS: tuple[str, ...] = (
    "agent_entrypoint_json",
    "current_model_binding_json",
    "runtime_dependencies_json",
)

_JSON_CONTENT_FIELDS = frozenset(CONTENT_FIELDS) - {"target_source_snapshot_id", "agent_name"}


class AgentManifestValidationError(ValueError):
    """A field value violates this domain schema's own shape/invariant,
    including an illegal status transition or a caller attempting to
    supply `manifest_sha256` before locking."""


def validate_status(value: object) -> str:
    if not isinstance(value, str) or value not in ALLOWED_STATUSES:
        raise AgentManifestValidationError(f"status must be one of {ALLOWED_STATUSES}: {value!r}")
    return value


def validate_status_transition(current: str, new: str) -> None:
    """Raises unless `current` -> `new` is a real, legal edge in
    `ALLOWED_STATUS_TRANSITIONS` -- see this module's own docstring."""
    validate_status(current)
    validate_status(new)
    if new not in ALLOWED_STATUS_TRANSITIONS[current]:
        raise AgentManifestValidationError(
            f"illegal AgentManifest status transition {current!r} -> {new!r}; "
            f"only {sorted(ALLOWED_STATUS_TRANSITIONS[current])} is legal from {current!r}"
        )


def validate_agent_name_or_none(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value:
        raise AgentManifestValidationError("agent_name must be a non-empty string or null")
    if len(value) > AGENT_NAME_MAX_LENGTH:
        raise AgentManifestValidationError(
            f"agent_name must be at most {AGENT_NAME_MAX_LENGTH} characters"
        )
    return value


def _validate_json_field_or_none(value: object, *, field_name: str) -> dict[str, object] | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise AgentManifestValidationError(f"{field_name} must be a JSON object or null")
    return value


def compute_manifest_sha256(content: dict[str, object]) -> str:
    """Deterministic content hash over exactly `CONTENT_FIELDS` -- see this
    module's own docstring ("Hash") for the canonicalization rule and why
    envelope/audit fields are excluded."""
    canonical = {field: content.get(field) for field in CONTENT_FIELDS}
    encoded = json.dumps(canonical, sort_keys=True, separators=(",", ":"), default=str)
    return sha256(encoded.encode("utf-8")).hexdigest()


class AgentManifest(Base):
    """Section 6.8D -- a project's own canonical Agent Manifest, versioned
    per project, with a real `draft` -> `review_required` -> `locked` ->
    `superseded` transition chain (see this module's own docstring)."""

    __tablename__ = "agent_manifests"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    project_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    target_source_snapshot_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, server_default="draft")
    agent_name: Mapped[str | None] = mapped_column(String(AGENT_NAME_MAX_LENGTH), nullable=True)
    agent_entrypoint_json: Mapped[dict[str, object] | None] = mapped_column(JSONB, nullable=True)
    current_model_binding_json: Mapped[dict[str, object] | None] = mapped_column(
        JSONB, nullable=True
    )
    system_prompt_refs_json: Mapped[dict[str, object] | None] = mapped_column(JSONB, nullable=True)
    tool_contracts_json: Mapped[dict[str, object] | None] = mapped_column(JSONB, nullable=True)
    input_contract_json: Mapped[dict[str, object] | None] = mapped_column(JSONB, nullable=True)
    output_contract_json: Mapped[dict[str, object] | None] = mapped_column(JSONB, nullable=True)
    mandate_json: Mapped[dict[str, object] | None] = mapped_column(JSONB, nullable=True)
    forbidden_actions_json: Mapped[dict[str, object] | None] = mapped_column(JSONB, nullable=True)
    permission_model_json: Mapped[dict[str, object] | None] = mapped_column(JSONB, nullable=True)
    required_context_json: Mapped[dict[str, object] | None] = mapped_column(JSONB, nullable=True)
    runtime_dependencies_json: Mapped[dict[str, object] | None] = mapped_column(
        JSONB, nullable=True
    )
    evaluation_harness_json: Mapped[dict[str, object] | None] = mapped_column(JSONB, nullable=True)
    trace_source_contract_json: Mapped[dict[str, object] | None] = mapped_column(
        JSONB, nullable=True
    )
    deployment_contract_json: Mapped[dict[str, object] | None] = mapped_column(JSONB, nullable=True)
    manifest_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_by: Mapped[str] = mapped_column(String(200), nullable=False)
    approved_by: Mapped[str | None] = mapped_column(String(200), nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )

    __table_args__ = (
        CheckConstraint(
            "status in (" + ", ".join(f"'{v}'" for v in ALLOWED_STATUSES) + ")",
            name="ck_agent_manifests_status",
        ),
        CheckConstraint("version >= 1", name="ck_agent_manifests_version_positive"),
        # Section 6.8D: "locking computes manifest_sha256" -- a real,
        # database-level guarantee that the hash exists if and only if the
        # row has actually been locked (or superseded, which can only be
        # reached via locked), never earlier, never missing afterward.
        CheckConstraint(
            "(status in ('locked','superseded')) = (manifest_sha256 IS NOT NULL)",
            name="ck_agent_manifests_hash_iff_locked",
        ),
        # Section 7A.6: "the user must approve" -- approval is real,
        # database-level paired with the same locked/superseded states.
        CheckConstraint(
            "(approved_by IS NULL) = (approved_at IS NULL)",
            name="ck_agent_manifests_approval_pair",
        ),
        CheckConstraint(
            "(status in ('locked','superseded')) = (approved_by IS NOT NULL)",
            name="ck_agent_manifests_approval_iff_locked",
        ),
        ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            ondelete="CASCADE",
            name="fk_agent_manifests_organization_id",
        ),
        ForeignKeyConstraint(
            ["organization_id", "project_id"],
            ["projects.organization_id", "projects.id"],
            ondelete="CASCADE",
            name="fk_agent_manifests_organization_id_project_id",
        ),
        ForeignKeyConstraint(
            ["organization_id", "target_source_snapshot_id"],
            ["target_source_snapshots.organization_id", "target_source_snapshots.id"],
            ondelete="RESTRICT",
            name="fk_agent_manifests_organization_id_snapshot_id",
        ),
    )

    def __init__(self, **kwargs: object) -> None:
        if "status" in kwargs:
            kwargs["status"] = validate_status(kwargs["status"])
        if "agent_name" in kwargs:
            kwargs["agent_name"] = validate_agent_name_or_none(kwargs["agent_name"])
        for field_name in _JSON_CONTENT_FIELDS:
            if field_name in kwargs:
                kwargs[field_name] = _validate_json_field_or_none(
                    kwargs[field_name], field_name=field_name
                )

        status = kwargs.get("status", "draft")
        has_hash = kwargs.get("manifest_sha256") is not None
        is_locked_or_superseded = status in ("locked", "superseded")
        if has_hash != is_locked_or_superseded:
            raise AgentManifestValidationError(
                "manifest_sha256 must be set if and only if status is "
                "'locked' or 'superseded' -- Section 6.8D: 'locking "
                "computes manifest_sha256', never earlier, never missing "
                "afterward"
            )
        has_approval = kwargs.get("approved_by") is not None
        if has_approval != is_locked_or_superseded:
            raise AgentManifestValidationError(
                "approved_by must be set if and only if status is " "'locked' or 'superseded'"
            )
        if (kwargs.get("approved_by") is None) != (kwargs.get("approved_at") is None):
            raise AgentManifestValidationError(
                "approved_by and approved_at must be both set or both null"
            )

        super().__init__(**kwargs)
