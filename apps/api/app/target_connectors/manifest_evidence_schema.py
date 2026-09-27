"""ManifestEvidence domain schema (FTF-04A20, Section 6.8E/7A.6-7A.7/66/77/81).

Section 6.8E's own `ManifestEvidence` -- per-field source provenance for a
locked-or-unlocked `AgentManifest` (FTF-04A19, this task's own dependency,
CERTIFIED). This task's own job is exactly the storage/validation shape of
that record -- not the discovery scanners that actually produce evidence
content (FTF-04A09/FTF-04A10/FTF-04A11/FTF-04A12/FTF-04A13/FTF-04A14/
FTF-04A16, all already CERTIFIED, separate, earlier tasks), not the
completeness/validation engine that reads this table to decide whether a
manifest may lock (FTF-04A23, a separate, later, dependency-gated task),
and not the repository-drift-driven re-evidencing workflow (FTF-04A27).
Declaring any of that as a real, enforced capability here without a real
backing implementation would be exactly the "placeholder endpoint" Law 11
forbids.

Path refined from the task packet's own generic suggestion
(`packages/target-connectors/manifestevidence_schema_api.py`) to
`apps/api/app/target_connectors/manifest_evidence_schema.py`, matching
FTF-04A19's own real `apps/api/app/target_connectors/*_schema.py` layout
precedent exactly.

Field list scoped to Section 6.8E's own literal fields plus the two
universal additions every prior table in this chain already applies:
`organization_id` (Section 65.1, every tenant-scoped table) and
`created_at` (the same "no Section explicitly lists it, but every table in
this chain still carries one" precedent `discovered_agent_candidate_schema.py`
already established for a structurally identical "append-only evidence
row" resource -- see that module's own field list). No `created_by` column
is added: Section 6.8E does not list one (contrast with Section 6.8D's own
`AgentManifest.created_by`, which this schema does NOT reuse here), and
`discovered_agent_candidate_schema.py` set the exact same precedent for a
structurally identical evidence-row resource with no Section-listed
`created_by` -- the acting principal is instead captured in the real audit
event `manifest_evidence_crud.py` writes for every create (Section 45's
usual discipline, matching every prior mutating task in this chain).

## Immutability ("Store ... provenance" -- a permanent record, never edited)

Once written, a `ManifestEvidence` row is never mutated or removed -- it is
a permanent historical record of what was actually found (or who actually
overrode a field), the same "no legitimate mutation, ever" shape
FTF-04A07's own `TargetSourceSnapshot`/FTF-1001's own `ModelVersion`
already established for this codebase's other append-only evidence
resources (contrast with `AgentManifest` itself, which genuinely needs
`UPDATE` while draft/review_required). The real forward Alembic migration
(`apps/api/alembic/versions/0016_manifest_evidence.py`) grants the
restricted `sovra_app` role `SELECT`/`INSERT` only -- never `UPDATE`,
never `DELETE`.

## `field_path` ("Store per-field source ... provenance")

`field_path` names *which* `AgentManifest` content field this evidence row
backs -- `validate_field_path` requires its own leading dot-segment (or the
whole string, if it carries no dot) to be a real member of
`agent_manifest_schema.py`'s own already-CERTIFIED `CONTENT_FIELDS`
(Section 77.1 reuse-first: this task's own dependency's own real field
list, never a second, hand-duplicated copy of it). A deeper dotted path
(e.g. `current_model_binding_json.litellm_alias`) is permitted -- a JSON
content field's own real internal structure is not itself frozen by this
task -- but the top-level field name it claims to be evidence *for* must
be real, never invented. This is this module's own real enforcement of
this task's own negative_acceptance clause "... unknown values presented
as detected are rejected": an evidence row citing a manifest field that
does not exist is refused before it is ever written.

## Per-`evidence_type` field shape ("source commit/path/symbol/hash or
user override provenance and confidence method")

This task's own compressed acceptance sentence reads as two real,
mutually-exclusive shapes, split by `evidence_type`
(`ALLOWED_EVIDENCE_TYPES`, Section 6.8E's own literal six-value set):

1. **Detected** (`DETECTED_EVIDENCE_TYPES` -- every value except
   `user_input`): a machine actually found this, so it must disclose real
   provenance and a real confidence: `repository_commit_sha` and
   `confidence_score` are both required non-null (this module's own real
   "confidence method" reading -- `evidence_type` itself names *which*
   detection method: a static repo scan, a config/test file read, or a
   live runtime probe; Section 6.8E lists no separate `confidence_method`
   field, so inventing one beyond that literal list would itself be the
   kind of un-spec'd schema overreach this chain's own established
   discipline avoids). Within this bucket:
   - `REPO_CITED_EVIDENCE_TYPES` (`repo_file`/`repo_symbol`/`config`/
     `test` -- a real file was actually read) additionally require
     `file_path` and `content_hash` non-null; `runtime_probe` (evidence
     from probing a *running* system, not a static file) must instead
     carry `file_path`/`content_hash` as null -- there is no file to cite.
   - `LINE_REF_EVIDENCE_TYPES` (`repo_file`/`repo_symbol` alone, the two
     genuinely line-anchored types) additionally require `line_start`/
     `line_end` non-null, both `>= 1`, with `line_end >= line_start`.
     Every other evidence_type must carry `line_start`/`line_end` as null
     -- this module's own real, structural enforcement of this task's own
     negative_acceptance clause "Fabricated line refs ... are rejected":
     a line reference attached to evidence that is not genuinely a
     line-anchored file citation (a config file read as a whole, a live
     runtime probe, a direct user override) is definitionally fabricated,
     never merely decorative. (Deeper verification -- that the cited line
     range actually contains what is claimed -- is real-file-content
     verification, the responsibility of the discovery scanner that
     produced this evidence at scan time, FTF-04A09 through FTF-04A16,
     already CERTIFIED; this storage/API task's own real job is the
     structural, database-enforced half of that guarantee, disclosed here
     rather than silently narrowed.)
   - `symbol` is required non-null if and only if `evidence_type ==
     'repo_symbol'`; null for every other type (a symbol name attached to
     evidence that is not a real symbol citation is the same class of
     fabrication as an unanchored line ref).
2. **User override** (`evidence_type == 'user_input'`): a human directly
   entered or overrode this field's value, so there is no detection method
   and no repository citation at all -- `repository_commit_sha`,
   `file_path`, `line_start`, `line_end`, `symbol`, `content_hash` and
   `confidence_score` must ALL be null. The human is the source of truth
   by definition, not a probability (this module's own real reading of
   this task's own acceptance clause's own "or" -- detected-with-real-
   provenance and user-confirmed-with-no-provenance are the two real,
   mutually exclusive shapes this acceptance clause names, never blended).

Every one of the per-type field-shape rules above is enforced twice: once,
authoritatively, as a real database-level `CHECK` constraint (this
module's own migration) that a bypassing raw `INSERT` still cannot evade,
and again in `ManifestEvidence.__init__` itself, so a caller gets a real,
descriptive `ManifestEvidenceValidationError` before ever reaching the
database -- the same "never trust a caller-supplied shape, enforce it at
both layers" discipline `agent_manifest_schema.py`'s own `__init__`
already established for this exact class of invariant.

## `evidence_json`

Section 6.8E's own catch-all real finding-detail field. Required
non-empty (a real, substantive body of what was actually found or
overridden -- an empty `{}` claiming to *be* evidence is exactly the kind
of vacuous, fabricated-looking artifact this task's own negative_acceptance
is written against), enforced in `__init__` (a `NOT NULL` column alone
cannot express "non-empty").

## Cross-snapshot check ("cross-snapshot evidence ... rejected") --
deliberately NOT enforced here

This module's own `ManifestEvidence.__init__` has no access to the parent
`AgentManifest` row's own `target_source_snapshot_id` (an evidence row's
own `agent_manifest_id` is just a UUID at construction time, not a loaded
relationship) -- no `CHECK` constraint can express a cross-table
comparison against a sibling table either. The real enforcement of "a real
`repository_commit_sha` must match the exact snapshot commit the parent
manifest was actually built from, never a different/stale one" is
`manifest_evidence_crud.py`'s own `create_manifest_evidence`, which
genuinely fetches the parent manifest and its own snapshot's real
`commit_sha` before ever constructing this row -- see that module's own
docstring.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Integer,
    Numeric,
    String,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.auth.organization_workspace_schema import Base
from app.target_connectors.agent_manifest_schema import CONTENT_FIELDS

__all__ = [
    "ALLOWED_EVIDENCE_TYPES",
    "DETECTED_EVIDENCE_TYPES",
    "FIELD_PATH_MAX_LENGTH",
    "FILE_PATH_MAX_LENGTH",
    "LINE_REF_EVIDENCE_TYPES",
    "REPO_CITED_EVIDENCE_TYPES",
    "SYMBOL_MAX_LENGTH",
    "ManifestEvidence",
    "ManifestEvidenceValidationError",
    "validate_evidence_type",
    "validate_field_path",
]

FIELD_PATH_MAX_LENGTH = 500
FILE_PATH_MAX_LENGTH = 1000
SYMBOL_MAX_LENGTH = 500
COMMIT_SHA_MAX_LENGTH = 64
CONTENT_HASH_MAX_LENGTH = 64

# Section 6.8E's own literal six-value `evidence_type` set.
ALLOWED_EVIDENCE_TYPES = (
    "repo_file",
    "repo_symbol",
    "config",
    "test",
    "runtime_probe",
    "user_input",
)

# Every evidence_type except the one real "a human directly entered this"
# escape hatch -- see this module's own docstring point 1.
DETECTED_EVIDENCE_TYPES = frozenset(ALLOWED_EVIDENCE_TYPES) - {"user_input"}

# The four evidence_types that cite a real file inside the repository --
# see this module's own docstring point 1.
REPO_CITED_EVIDENCE_TYPES = frozenset({"repo_file", "repo_symbol", "config", "test"})

# The two evidence_types genuinely anchored to a specific line range --
# see this module's own docstring point 1.
LINE_REF_EVIDENCE_TYPES = frozenset({"repo_file", "repo_symbol"})


class ManifestEvidenceValidationError(ValueError):
    """A field value violates this domain schema's own shape/invariant --
    an unrecognized `evidence_type`, a `field_path` not naming a real
    `AgentManifest` content field, a per-type field-shape violation (this
    module's own docstring point 1/2), or empty `evidence_json`."""


def validate_evidence_type(value: object) -> str:
    if not isinstance(value, str) or value not in ALLOWED_EVIDENCE_TYPES:
        raise ManifestEvidenceValidationError(
            f"evidence_type must be one of {ALLOWED_EVIDENCE_TYPES}: {value!r}"
        )
    return value


def validate_field_path(value: object) -> str:
    if not isinstance(value, str) or not value:
        raise ManifestEvidenceValidationError("field_path must be a non-empty string")
    if len(value) > FIELD_PATH_MAX_LENGTH:
        raise ManifestEvidenceValidationError(
            f"field_path must be at most {FIELD_PATH_MAX_LENGTH} characters"
        )
    top_level = value.split(".", 1)[0]
    if top_level not in CONTENT_FIELDS:
        raise ManifestEvidenceValidationError(
            f"field_path {value!r} does not name a real AgentManifest content field -- "
            f"its own leading segment {top_level!r} must be one of {CONTENT_FIELDS} "
            "(this task's own negative_acceptance: unknown values presented as detected "
            "are rejected)"
        )
    return value


def _validate_evidence_json(value: object) -> dict[str, object]:
    if not isinstance(value, dict) or not value:
        raise ManifestEvidenceValidationError("evidence_json must be a non-empty JSON object")
    return value


def _require_field(kwargs: dict[str, object], field: str, *, required: bool) -> None:
    value = kwargs.get(field)
    if required and value is None:
        raise ManifestEvidenceValidationError(f"{field} is required for this evidence_type")
    if not required and value is not None:
        raise ManifestEvidenceValidationError(f"{field} must be null for this evidence_type")


class ManifestEvidence(Base):
    """Section 6.8E -- an `AgentManifest`'s own per-field source provenance
    row. Never mutated or deleted once written -- see this module's own
    docstring ("Immutability")."""

    __tablename__ = "manifest_evidence"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    agent_manifest_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    field_path: Mapped[str] = mapped_column(String(FIELD_PATH_MAX_LENGTH), nullable=False)
    evidence_type: Mapped[str] = mapped_column(String(32), nullable=False)
    repository_commit_sha: Mapped[str | None] = mapped_column(
        String(COMMIT_SHA_MAX_LENGTH), nullable=True
    )
    file_path: Mapped[str | None] = mapped_column(String(FILE_PATH_MAX_LENGTH), nullable=True)
    line_start: Mapped[int | None] = mapped_column(Integer, nullable=True)
    line_end: Mapped[int | None] = mapped_column(Integer, nullable=True)
    symbol: Mapped[str | None] = mapped_column(String(SYMBOL_MAX_LENGTH), nullable=True)
    content_hash: Mapped[str | None] = mapped_column(String(CONTENT_HASH_MAX_LENGTH), nullable=True)
    confidence_score: Mapped[Decimal | None] = mapped_column(Numeric(4, 3), nullable=True)
    evidence_json: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )

    __table_args__ = (
        CheckConstraint(
            "evidence_type in (" + ", ".join(f"'{v}'" for v in ALLOWED_EVIDENCE_TYPES) + ")",
            name="ck_manifest_evidence_evidence_type",
        ),
        CheckConstraint(
            "(confidence_score IS NULL) OR (confidence_score >= 0.0 AND confidence_score <= 1.0)",
            name="ck_manifest_evidence_confidence_score_range",
        ),
        # Section 6.8E's own six-value evidence_type set, split into the
        # two real acceptance shapes -- see this module's own docstring.
        CheckConstraint(
            "(evidence_type != 'user_input') = (repository_commit_sha IS NOT NULL)",
            name="ck_manifest_evidence_commit_sha_iff_detected",
        ),
        CheckConstraint(
            "(evidence_type != 'user_input') = (confidence_score IS NOT NULL)",
            name="ck_manifest_evidence_confidence_iff_detected",
        ),
        CheckConstraint(
            "(evidence_type in ('repo_file','repo_symbol','config','test')) "
            "= (file_path IS NOT NULL)",
            name="ck_manifest_evidence_file_path_iff_repo_cited",
        ),
        CheckConstraint(
            "(evidence_type in ('repo_file','repo_symbol','config','test')) "
            "= (content_hash IS NOT NULL)",
            name="ck_manifest_evidence_content_hash_iff_repo_cited",
        ),
        CheckConstraint(
            "(evidence_type = 'repo_symbol') = (symbol IS NOT NULL)",
            name="ck_manifest_evidence_symbol_iff_repo_symbol",
        ),
        CheckConstraint(
            "(line_start IS NULL) = (line_end IS NULL)",
            name="ck_manifest_evidence_line_refs_paired",
        ),
        CheckConstraint(
            "(line_start IS NULL) OR (line_start >= 1 AND line_end >= line_start)",
            name="ck_manifest_evidence_line_refs_valid_range",
        ),
        CheckConstraint(
            "(evidence_type in ('repo_file','repo_symbol')) = (line_start IS NOT NULL)",
            name="ck_manifest_evidence_line_refs_iff_line_anchored",
        ),
        ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            ondelete="CASCADE",
            name="fk_manifest_evidence_organization_id",
        ),
        ForeignKeyConstraint(
            ["organization_id", "agent_manifest_id"],
            ["agent_manifests.organization_id", "agent_manifests.id"],
            ondelete="CASCADE",
            name="fk_manifest_evidence_organization_id_manifest_id",
        ),
    )

    def __init__(self, **kwargs: object) -> None:
        if "evidence_type" in kwargs:
            kwargs["evidence_type"] = validate_evidence_type(kwargs["evidence_type"])
        if "field_path" in kwargs:
            kwargs["field_path"] = validate_field_path(kwargs["field_path"])
        if "evidence_json" in kwargs:
            kwargs["evidence_json"] = _validate_evidence_json(kwargs["evidence_json"])

        evidence_type = kwargs.get("evidence_type")
        if isinstance(evidence_type, str) and evidence_type in ALLOWED_EVIDENCE_TYPES:
            is_detected = evidence_type in DETECTED_EVIDENCE_TYPES
            is_repo_cited = evidence_type in REPO_CITED_EVIDENCE_TYPES
            is_line_anchored = evidence_type in LINE_REF_EVIDENCE_TYPES
            _require_field(kwargs, "repository_commit_sha", required=is_detected)
            _require_field(kwargs, "confidence_score", required=is_detected)
            _require_field(kwargs, "file_path", required=is_repo_cited)
            _require_field(kwargs, "content_hash", required=is_repo_cited)
            _require_field(kwargs, "symbol", required=evidence_type == "repo_symbol")
            _require_field(kwargs, "line_start", required=is_line_anchored)
            _require_field(kwargs, "line_end", required=is_line_anchored)

        line_start, line_end = kwargs.get("line_start"), kwargs.get("line_end")
        if (line_start is None) != (line_end is None):
            raise ManifestEvidenceValidationError(
                "line_start and line_end must be both set or both null"
            )
        if line_start is not None and line_end is not None:
            if not isinstance(line_start, int) or not isinstance(line_end, int):
                raise ManifestEvidenceValidationError("line_start/line_end must be integers")
            if line_start < 1 or line_end < line_start:
                raise ManifestEvidenceValidationError(
                    "negative_acceptance: fabricated line refs are rejected -- "
                    "line_start must be >= 1 and line_end >= line_start"
                )

        confidence_score = kwargs.get("confidence_score")
        if confidence_score is not None:
            score = Decimal(str(confidence_score))
            if score < 0 or score > 1:
                raise ManifestEvidenceValidationError("confidence_score must be between 0 and 1")

        super().__init__(**kwargs)
