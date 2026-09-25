"""DiscoveredAgentCandidate domain schema (FTF-04A17, Section 6.8C/7A.4/9/
45/65/66/77/81).

A snapshot-scoped `DiscoveredAgentCandidate` -- Section 6.8C's own entity,
the real output row of the (separate, not-yet-built) Section 7A.4
source-analysis pipeline (FTF-04A08-FTF-04A16, none of which are
CERTIFIED or even implemented in this repository yet). This module owns
exactly the storage/validation shape of that record -- a real analysis
run has already produced a candidate with its own evidence -- exactly the
same division of responsibility FTF-04A07's own module docstring already
drew for `TargetSourceSnapshot` versus the connectors that populate it
(FTF-04A02/FTF-04A06). It deliberately does not implement any part of the
actual layered deterministic discovery (language/framework detection,
entrypoint/model/prompt/tool/test/trace-hook/deployment evidence
extraction) or the candidate-selection UI that lets a user confirm one
candidate as the target agent (FTF-04A18, also a separate, dependency-
gated later task). Declaring either of those as a real, enforced
capability here without a real backing implementation would be exactly
the "placeholder endpoint" Law 11 forbids.

Path refined from the task packet's own generic suggestion
(`packages/target-connectors/discoveredagentcandidate_schema_api.py`) to
`apps/api/app/target_connectors/discovered_agent_candidate_schema.py`,
matching FTF-04A01/FTF-04A07's own real `apps/api/app/<domain>/
*_schema.py` layout precedent exactly rather than the packet's initial
owner-path placeholder.

Field list scoped to Section 6.8C's own literal fields plus exactly one
inherited addition: `organization_id`, denormalized directly onto this
table (never derived only through a join), the same Section 65.1 "All
organization-scoped entities carry organization_id NOT NULL" precedent
FTF-04A07's own `TargetSourceSnapshot` already applied one level removed
from `organizations` itself.

This task's own acceptance clause 1 ("Store every candidate with
traceable evidence and unresolved fields; expose paginated project-scoped
API") maps directly onto Section 6.8C's own field list read literally:
`target_source_snapshot_id` is the traceability link back to the exact
immutable source this candidate was discovered in (never a floating
branch/connection reference); the seven `*_evidence_json` columns
(`entrypoint_evidence_json`/`model_evidence_json`/`prompt_evidence_json`/
`tool_evidence_json`/`test_evidence_json`/`trace_hook_evidence_json`/
`deployment_evidence_json`) are each independently nullable -- a real
layered pipeline (Section 7A.4's own numbered steps 3-11) can genuinely
resolve some evidence categories and not others for a given candidate,
and Section 7A.4's own closing sentence ("Unknown/custom frameworks are
not guessed. The UI shows unresolved fields and requires explicit user
input") is this task's own real "unresolved fields" half: a `null`
evidence column is how an honestly-unresolved category is represented,
never a fabricated empty-but-present evidence payload standing in for
"nothing was found here." `framework_or_runtime` is nullable for the
identical reason (Section 6.8C's own explicit `nullable` tag).

`confidence_score` and `discovery_status` are this task's own real
"advisory only" / negative_acceptance half ("Candidate priority/
confidence is not calibrated probability or automatic in-scope
selection"), enforced two ways, both disclosed here rather than only in
the CRUD/routes modules:

1. `confidence_score` is a bounded `NUMERIC` in `[0.0, 1.0]` (a real
   database `CHECK` constraint, not only an application-level one) --
   bounding its *shape* as a score is not the same claim as treating it
   as a calibrated probability; nothing in this module, the CRUD module,
   or the routes module ever sorts, filters, thresholds, or auto-selects
   a candidate by this column. Storing a bounded number is not itself an
   act of selection.
2. `discovery_status` is deliberately a closed *one*-value set for
   Release 1 (`'candidate'`) -- the same "single legal value now,
   expandable only through a real schema change" precedent FTF-04A07's
   own `ALLOWED_ANALYSIS_STATUSES = ('not_analyzed',)` already
   established for exactly this reason: FTF-04A18 (candidate-selection
   UI, "no auto-selection") is a separate, not-yet-built task, and this
   module provides no transition path to any confirmed/selected value at
   all. There is no PATCH/edit route anywhere in this chain -- the same
   "no edit path anywhere, SELECT/INSERT-only grant" discipline FTF-04A07
   already applied to `target_source_snapshots`, for the identical reason:
   a discovered candidate is a fact about what analysis found, never
   mutated after being recorded, and a real future user selection is
   FTF-04A18's own separate, explicit act -- never a silent status flip on
   this row.

`candidate_key` is this task's own real deduplication identity: a stable,
caller-supplied (i.e. pipeline-supplied, once FTF-04A08-16 exist) string
identifying *what* was detected (e.g. a real entrypoint file+symbol path),
scoped unique per snapshot via a real composite `UNIQUE
(organization_id, target_source_snapshot_id, candidate_key)` constraint --
never a second analysis run silently creating a duplicate row for the same
already-discovered entity.

Real forward Alembic migration
(`apps/api/alembic/versions/0014_discovered_agent_candidates.py`) FORCEs
row-level security on `discovered_agent_candidates` scoped to the same
`app.current_organization_id` tenant context every prior migration in
this chain already established, grants the restricted `sovra_app` role
SELECT/INSERT only (the same immutability discipline as
`target_source_snapshots`), and adds a real composite foreign key
`(organization_id, target_source_snapshot_id)` ->
`target_source_snapshots(organization_id, id)` using the `UNIQUE
(organization_id, id)` constraint FTF-04A07's own migration already added
to that table.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Numeric,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.auth.organization_workspace_schema import Base

__all__ = [
    "ALLOWED_DISCOVERY_STATUSES",
    "CANDIDATE_KEY_MAX_LENGTH",
    "DISPLAY_NAME_MAX_LENGTH",
    "FRAMEWORK_OR_RUNTIME_MAX_LENGTH",
    "DiscoveredAgentCandidate",
    "DiscoveredAgentCandidateValidationError",
    "validate_candidate_key",
    "validate_confidence_score",
    "validate_discovery_status",
    "validate_display_name",
    "validate_evidence_json_or_none",
    "validate_framework_or_runtime_or_none",
]

CANDIDATE_KEY_MAX_LENGTH = 512
DISPLAY_NAME_MAX_LENGTH = 200
FRAMEWORK_OR_RUNTIME_MAX_LENGTH = 100

# A closed *one*-value set for Release 1 -- see this module's own docstring.
ALLOWED_DISCOVERY_STATUSES = ("candidate",)

_EVIDENCE_COLUMNS = (
    "entrypoint_evidence_json",
    "model_evidence_json",
    "prompt_evidence_json",
    "tool_evidence_json",
    "test_evidence_json",
    "trace_hook_evidence_json",
    "deployment_evidence_json",
)


class DiscoveredAgentCandidateValidationError(ValueError):
    """A field value violates this domain schema's own shape/invariant."""


def validate_candidate_key(value: object) -> str:
    if not isinstance(value, str) or not value:
        raise DiscoveredAgentCandidateValidationError(
            "candidate_key must be a real, non-empty string identifying "
            f"what was detected: {value!r}"
        )
    if len(value) > CANDIDATE_KEY_MAX_LENGTH:
        raise DiscoveredAgentCandidateValidationError(
            f"candidate_key must be at most {CANDIDATE_KEY_MAX_LENGTH} characters"
        )
    return value


def validate_display_name(value: object) -> str:
    if not isinstance(value, str) or not value:
        raise DiscoveredAgentCandidateValidationError(
            f"display_name must be a real, non-empty string: {value!r}"
        )
    if len(value) > DISPLAY_NAME_MAX_LENGTH:
        raise DiscoveredAgentCandidateValidationError(
            f"display_name must be at most {DISPLAY_NAME_MAX_LENGTH} characters"
        )
    return value


def validate_framework_or_runtime_or_none(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value:
        raise DiscoveredAgentCandidateValidationError(
            "framework_or_runtime must be a non-empty string or null"
        )
    if len(value) > FRAMEWORK_OR_RUNTIME_MAX_LENGTH:
        raise DiscoveredAgentCandidateValidationError(
            f"framework_or_runtime must be at most {FRAMEWORK_OR_RUNTIME_MAX_LENGTH} characters"
        )
    return value


def validate_evidence_json_or_none(value: object, *, field_name: str) -> dict[str, object] | None:
    """Section 7A.4's own closing sentence: an honestly-unresolved evidence
    category is `null`, never a fabricated non-empty payload standing in
    for "nothing was found here." When evidence *is* present, it must be a
    real JSON object (a bare scalar/array is not "evidence", it is a
    value with no place to attach further provenance)."""
    if value is None:
        return None
    if not isinstance(value, dict):
        raise DiscoveredAgentCandidateValidationError(
            f"{field_name} must be a JSON object or null, never a bare scalar/array: {value!r}"
        )
    return value


def validate_confidence_score(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise DiscoveredAgentCandidateValidationError(
            f"confidence_score must be a real number: {value!r}"
        )
    score = float(value)
    if not (0.0 <= score <= 1.0):
        raise DiscoveredAgentCandidateValidationError(
            f"confidence_score must be within [0.0, 1.0]: {score!r}"
        )
    return score


def validate_discovery_status(value: object) -> str:
    if not isinstance(value, str) or value not in ALLOWED_DISCOVERY_STATUSES:
        raise DiscoveredAgentCandidateValidationError(
            f"discovery_status must be one of {ALLOWED_DISCOVERY_STATUSES}: {value!r}"
        )
    return value


class DiscoveredAgentCandidate(Base):
    """Section 6.8C -- a snapshot-scoped discovered agent candidate, never
    edited after creation (see this module's own docstring for why no
    edit path exists at all)."""

    __tablename__ = "discovered_agent_candidates"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    target_source_snapshot_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    candidate_key: Mapped[str] = mapped_column(String(CANDIDATE_KEY_MAX_LENGTH), nullable=False)
    display_name: Mapped[str] = mapped_column(String(DISPLAY_NAME_MAX_LENGTH), nullable=False)
    framework_or_runtime: Mapped[str | None] = mapped_column(
        String(FRAMEWORK_OR_RUNTIME_MAX_LENGTH), nullable=True
    )
    entrypoint_evidence_json: Mapped[dict[str, object] | None] = mapped_column(JSONB, nullable=True)
    model_evidence_json: Mapped[dict[str, object] | None] = mapped_column(JSONB, nullable=True)
    prompt_evidence_json: Mapped[dict[str, object] | None] = mapped_column(JSONB, nullable=True)
    tool_evidence_json: Mapped[dict[str, object] | None] = mapped_column(JSONB, nullable=True)
    test_evidence_json: Mapped[dict[str, object] | None] = mapped_column(JSONB, nullable=True)
    trace_hook_evidence_json: Mapped[dict[str, object] | None] = mapped_column(JSONB, nullable=True)
    deployment_evidence_json: Mapped[dict[str, object] | None] = mapped_column(JSONB, nullable=True)
    confidence_score: Mapped[float] = mapped_column(Numeric(4, 3), nullable=False)
    discovery_status: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default="candidate"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )

    __table_args__ = (
        UniqueConstraint(
            "organization_id",
            "target_source_snapshot_id",
            "candidate_key",
            name="uq_discovered_agent_candidates_snapshot_id_candidate_key",
        ),
        CheckConstraint(
            "confidence_score >= 0.0 AND confidence_score <= 1.0",
            name="ck_discovered_agent_candidates_confidence_score_range",
        ),
        CheckConstraint(
            "discovery_status in (" + ", ".join(f"'{v}'" for v in ALLOWED_DISCOVERY_STATUSES) + ")",
            name="ck_discovered_agent_candidates_discovery_status",
        ),
        ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            ondelete="CASCADE",
            name="fk_discovered_agent_candidates_organization_id",
        ),
        ForeignKeyConstraint(
            ["organization_id", "target_source_snapshot_id"],
            ["target_source_snapshots.organization_id", "target_source_snapshots.id"],
            ondelete="RESTRICT",
            name="fk_discovered_agent_candidates_organization_id_snapshot_id",
        ),
    )

    def __init__(self, **kwargs: object) -> None:
        if "candidate_key" in kwargs:
            kwargs["candidate_key"] = validate_candidate_key(kwargs["candidate_key"])
        if "display_name" in kwargs:
            kwargs["display_name"] = validate_display_name(kwargs["display_name"])
        if "framework_or_runtime" in kwargs:
            kwargs["framework_or_runtime"] = validate_framework_or_runtime_or_none(
                kwargs["framework_or_runtime"]
            )
        for field_name in _EVIDENCE_COLUMNS:
            if field_name in kwargs:
                kwargs[field_name] = validate_evidence_json_or_none(
                    kwargs[field_name], field_name=field_name
                )
        if "confidence_score" in kwargs:
            kwargs["confidence_score"] = validate_confidence_score(kwargs["confidence_score"])
        if "discovery_status" in kwargs:
            kwargs["discovery_status"] = validate_discovery_status(kwargs["discovery_status"])

        super().__init__(**kwargs)
