"""Record schema validator (FTF-0508, Section 6.11/7/8.1/8.2/67.3/77/81).

Section 8.1's import pipeline step 7 ("Validate schema") and step 17 ("UI
requires user resolution of blocking issues") both need one real,
load-bearing place where a *batch* of already-normalized candidate
records (FTF-0506's/FTF-0507's own `CanonicalConversationRecord` output,
plus the Section 6.11 `DatasetRecord` provenance/quality companion
fields a later, not-yet-built pipeline stage attaches to each one) is
checked against the canonical contract before any row is allowed into
split generation (step 18: "Only clean/accepted records enter split
generation") -- this module is that place.

This task's own `database_changes` clause ("No independent schema
change. Consume frozen contract; any required change is a separately
claimed migration prerequisite.") fixes this module's shape: it is a
pure, synchronous, no-I/O, no-DB function. It does not itself compute
quality metrics, run PII detection, or validate tool-call arguments --
those are separate, later pipeline stages (Section 8.1 steps 8/14/15).
This module validates that their *output fields* are present and hold a
recognized value on the canonical Section 6.11 `DatasetRecord` shape, not
that the values are correct.

This task's own acceptance clause ("Validate schema/dialect/content size
and required provenance/quality fields per canonical version") is met by
five real, independently testable checks per candidate record:

  - **schema version**: the candidate's own `schema_version` must be one
    of `CANONICAL_SCHEMA_VERSIONS` -- reused directly from FTF-0506's
    already-CERTIFIED `SCHEMA_VERSION` constant (reuse-first, Section
    77.1) rather than a second, independently invented version string.
    "per canonical version" is read literally: an unrecognized version is
    rejected outright, never guessed as compatible.
  - **dialect**: the candidate's own `source_type` must be one of Section
    6.8F's own frozen `DatasetSourceConnection.source_type` enum
    (`upload`, `s3`, `minio`, `database`, `agent_traces`,
    `production_conversations`, `api`) -- the same enum this repository's
    domain model already fixes for a dataset's *source*, reused here
    rather than inventing a second "dialect" vocabulary.
  - **content size**: the candidate's `content` (the FTF-0506 canonical
    body shape, `{"messages": [...], "metadata": {...}}`) is bound to
    Section 67.3's own "canonical single-record max 2MiB"
    (`MAX_CANONICAL_RECORD_BYTES`), measured the same way FTF-0506/FTF-0507
    hash it: canonical JSON serialization
    (`json.dumps(..., sort_keys=True, separators=(",", ":"))`).
  - **required provenance fields**: Section 6.11's own `record_sha256`
    and `normalized_sha256` fields -- the same two values FTF-0506's
    `RecordLineage.raw_sha256` and `CanonicalConversationRecord.canonical_hash`
    already produce for a caller to attach -- must both be present as
    non-empty strings.
  - **required quality fields**: Section 6.11's own `quality_status`,
    `pii_status` and `tool_schema_status` fields must each be present and
    hold one of this module's own explicit, disclosed enum values, grounded
    directly in Section 8.1's own pipeline stage language (`QUALITY_STATUS_VALUES`
    from steps 15/17/18, `PII_STATUS_VALUES` from step 14 and Section 67.4's
    "quarantined for reviewer resolution", `TOOL_SCHEMA_STATUS_VALUES` from
    step 8, with `not_applicable` for a record that carries no tool calls at
    all) -- never a silently-accepted arbitrary string.

This task's own negative_acceptance ("Unknown content or schema version
and invalid sequence produces blocking issues with row evidence") is met
by returning a `RecordSchemaValidationReport` -- never raising on the
first violation -- whose `issues` carry each row's own `record_index`
(Section 6.11's own field name) and `batch_position` (this batch call's
own 0-based position, used as row evidence whenever `record_index` itself
is the thing under dispute) alongside a stable machine-readable `code`:
`unknown_content` (content missing/non-mapping/not the canonical
`messages`+`metadata` shape/not JSON-serializable), `unknown_schema_version`,
and `invalid_sequence` (a missing/non-int/bool/negative `record_index`, or
one that duplicates another record's in the same batch -- Section 6.11's
own per-dataset row-ordering identity, the same "ambiguous ordering must
be refused, not guessed" discipline FTF-0507 already applies to its own
per-event `sequence` field, reapplied here at the record-batch level).
Only a batch that is not a real sequence of records at all (not a
sequence, or empty) raises `RecordSchemaValidationError` outright --
there is no row to attach that failure's evidence to.

Scope decision, same disclosed precedent as FTF-0506/FTF-0507/FTF-1302/
FTF-1303/FTF-1304 ("no live caller yet"): this task's own packet `files`
list names only this module and its test, not an importer/route. No
dataset-import pipeline route exists yet in this repository to call
`validate_record_batch` (Section 67.1's actual import pipeline routes are
separate, later, dependency-gated tasks). `proving_symbol` stays blank;
`product_truth_check.py`'s product-true count is deliberately unchanged.

Path refinement recorded before READY (Section 84.3), same precedent as
FTF-0506/FTF-0507: the packet names
`services/training-worker/data/record_schema_validator.py`, but this
repo's real `SOURCE_ROOTS` (FTF-0001, PR #10) name `workers/data` as the
real data/review worker root -- there is no `services/` tree in this
repository. Placed alongside FTF-0506's/FTF-0507's own modules instead of
creating a second, non-existent tree.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from workers.data.canonical_conversation_normalizer import SCHEMA_VERSION

__all__ = [
    "CANONICAL_SCHEMA_VERSIONS",
    "MAX_CANONICAL_RECORD_BYTES",
    "PII_STATUS_VALUES",
    "QUALITY_STATUS_VALUES",
    "REQUIRED_PROVENANCE_FIELDS",
    "SUPPORTED_SOURCE_TYPES",
    "TOOL_SCHEMA_STATUS_VALUES",
    "RecordSchemaValidationError",
    "RecordSchemaValidationReport",
    "RecordValidationIssue",
    "validate_record_batch",
]

# Reused directly from FTF-0506's already-CERTIFIED constant (reuse-first,
# Section 77.1) -- "1.0" is not re-declared independently here.
CANONICAL_SCHEMA_VERSIONS = frozenset({SCHEMA_VERSION})

# Section 67.3: "canonical single-record max 2MiB".
MAX_CANONICAL_RECORD_BYTES = 2 * 1024 * 1024

# Section 6.8F's own frozen `DatasetSourceConnection.source_type` enum,
# reused directly rather than a second "dialect" vocabulary.
SUPPORTED_SOURCE_TYPES = frozenset(
    {"upload", "s3", "minio", "database", "agent_traces", "production_conversations", "api"}
)

# Grounded in Section 8.1 steps 15 ("Calculate quality metrics"), 17 ("UI
# requires user resolution of blocking issues") and 18 ("Only
# clean/accepted records enter split generation").
QUALITY_STATUS_VALUES = frozenset({"pending", "passed", "flagged", "rejected"})

# Grounded in Section 8.1 step 14 ("Run PII/secrets policy") and Section
# 67.4 ("suspicious data is quarantined for reviewer resolution").
PII_STATUS_VALUES = frozenset({"unscanned", "clean", "detected", "quarantined"})

# Grounded in Section 8.1 step 8 ("Validate tool calls"); "not_applicable"
# for a record that carries no tool calls at all.
TOOL_SCHEMA_STATUS_VALUES = frozenset({"not_applicable", "valid", "invalid"})

# Section 6.11's own provenance field names, reused verbatim.
REQUIRED_PROVENANCE_FIELDS: tuple[str, ...] = ("record_sha256", "normalized_sha256")

_REQUIRED_CONTENT_KEYS = frozenset({"messages", "metadata"})


class RecordSchemaValidationError(ValueError):
    """The candidate record batch itself is not a real sequence of
    records -- not a sequence at all, or empty. There is no row to
    attach this failure's evidence to, so it is raised, not reported as
    a row issue."""


@dataclass(frozen=True)
class RecordValidationIssue:
    batch_position: int
    record_index: int | None
    code: str
    message: str


@dataclass(frozen=True)
class RecordSchemaValidationReport:
    issues: tuple[RecordValidationIssue, ...]
    accepted_batch_positions: tuple[int, ...]
    rejected_batch_positions: tuple[int, ...]

    @property
    def is_clean(self) -> bool:
        return not self.issues


def _canonical_byte_size(document: object) -> int:
    canonical = json.dumps(document, sort_keys=True, separators=(",", ":"))
    return len(canonical.encode("utf-8"))


def _validate_one(
    batch_position: int, candidate: object
) -> tuple[list[RecordValidationIssue], int | None]:
    issues: list[RecordValidationIssue] = []

    if not isinstance(candidate, Mapping):
        issues.append(
            RecordValidationIssue(
                batch_position=batch_position,
                record_index=None,
                code="invalid_candidate",
                message="candidate record must be an object",
            )
        )
        return issues, None

    raw_record_index = candidate.get("record_index")
    record_index: int | None
    if (
        not isinstance(raw_record_index, int)
        or isinstance(raw_record_index, bool)
        or raw_record_index < 0
    ):
        issues.append(
            RecordValidationIssue(
                batch_position=batch_position,
                record_index=None,
                code="invalid_sequence",
                message=(
                    "record_index must be a non-negative int, not "
                    f"{raw_record_index!r} -- refusing to guess row ordering"
                ),
            )
        )
        record_index = None
    else:
        record_index = raw_record_index

    schema_version = candidate.get("schema_version")
    if schema_version not in CANONICAL_SCHEMA_VERSIONS:
        issues.append(
            RecordValidationIssue(
                batch_position=batch_position,
                record_index=record_index,
                code="unknown_schema_version",
                message=f"unknown schema_version {schema_version!r}",
            )
        )

    source_type = candidate.get("source_type")
    if source_type not in SUPPORTED_SOURCE_TYPES:
        issues.append(
            RecordValidationIssue(
                batch_position=batch_position,
                record_index=record_index,
                code="unknown_dialect",
                message=f"unknown source_type (dialect) {source_type!r}",
            )
        )

    content = candidate.get("content")
    if not isinstance(content, Mapping) or set(content) != _REQUIRED_CONTENT_KEYS:
        content_shape = sorted(content) if isinstance(content, Mapping) else repr(content)
        issues.append(
            RecordValidationIssue(
                batch_position=batch_position,
                record_index=record_index,
                code="unknown_content",
                message=(
                    "content must be an object with exactly the canonical "
                    f"'messages'/'metadata' keys, not {content_shape}"
                ),
            )
        )
    else:
        try:
            content_size = _canonical_byte_size(dict(content))
        except (TypeError, ValueError) as exc:
            issues.append(
                RecordValidationIssue(
                    batch_position=batch_position,
                    record_index=record_index,
                    code="unknown_content",
                    message=f"content is not JSON-serializable: {exc}",
                )
            )
        else:
            if content_size > MAX_CANONICAL_RECORD_BYTES:
                issues.append(
                    RecordValidationIssue(
                        batch_position=batch_position,
                        record_index=record_index,
                        code="content_too_large",
                        message=(
                            f"content is {content_size} bytes, exceeding the Section 67.3 "
                            f"canonical single-record max of {MAX_CANONICAL_RECORD_BYTES} bytes"
                        ),
                    )
                )

    for field in REQUIRED_PROVENANCE_FIELDS:
        value = candidate.get(field)
        if not isinstance(value, str) or not value:
            issues.append(
                RecordValidationIssue(
                    batch_position=batch_position,
                    record_index=record_index,
                    code="missing_provenance_field",
                    message=f"required provenance field {field!r} is missing or empty",
                )
            )

    quality_status = candidate.get("quality_status")
    if quality_status not in QUALITY_STATUS_VALUES:
        issues.append(
            RecordValidationIssue(
                batch_position=batch_position,
                record_index=record_index,
                code="invalid_quality_status",
                message=f"quality_status {quality_status!r} is not a recognized value",
            )
        )

    pii_status = candidate.get("pii_status")
    if pii_status not in PII_STATUS_VALUES:
        issues.append(
            RecordValidationIssue(
                batch_position=batch_position,
                record_index=record_index,
                code="invalid_pii_status",
                message=f"pii_status {pii_status!r} is not a recognized value",
            )
        )

    tool_schema_status = candidate.get("tool_schema_status")
    if tool_schema_status not in TOOL_SCHEMA_STATUS_VALUES:
        issues.append(
            RecordValidationIssue(
                batch_position=batch_position,
                record_index=record_index,
                code="invalid_tool_schema_status",
                message=f"tool_schema_status {tool_schema_status!r} is not a recognized value",
            )
        )

    return issues, record_index


def validate_record_batch(
    records: Sequence[Mapping[str, object]],
) -> RecordSchemaValidationReport:
    """Validates a batch of already-normalized Section 6.11 `DatasetRecord`
    candidates. Never raises on a per-row violation -- returns a
    `RecordSchemaValidationReport` whose `issues` carry row evidence
    (`batch_position` always, `record_index` when it was itself valid).
    Raises `RecordSchemaValidationError` only when the batch itself is not
    a real sequence of records (not a sequence, or empty) -- there is no
    row to attach that failure's evidence to."""
    if not isinstance(records, Sequence) or isinstance(records, str | bytes):
        raise RecordSchemaValidationError("records must be a sequence of candidate records")
    if not records:
        raise RecordSchemaValidationError("records must be a non-empty sequence")

    all_issues: list[RecordValidationIssue] = []
    accepted: list[int] = []
    rejected: list[int] = []
    seen_record_indices: dict[int, int] = {}

    per_row: list[tuple[list[RecordValidationIssue], int | None]] = []
    for batch_position, candidate in enumerate(records):
        row_issues, record_index = _validate_one(batch_position, candidate)
        per_row.append((row_issues, record_index))
        if record_index is not None:
            if record_index in seen_record_indices:
                row_issues.append(
                    RecordValidationIssue(
                        batch_position=batch_position,
                        record_index=record_index,
                        code="invalid_sequence",
                        message=(
                            f"record_index {record_index} duplicates batch position "
                            f"{seen_record_indices[record_index]} -- ordering is ambiguous"
                        ),
                    )
                )
            else:
                seen_record_indices[record_index] = batch_position

    for batch_position, (row_issues, _record_index) in enumerate(per_row):
        all_issues.extend(row_issues)
        if row_issues:
            rejected.append(batch_position)
        else:
            accepted.append(batch_position)

    return RecordSchemaValidationReport(
        issues=tuple(all_issues),
        accepted_batch_positions=tuple(accepted),
        rejected_batch_positions=tuple(rejected),
    )
