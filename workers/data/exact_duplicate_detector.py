"""Exact duplicate detector (FTF-0511, Section 8.1 step 10/9/10/67/77/81).

Section 8.1's import pipeline step 10 ("Detect exact duplicate records")
needs one real, load-bearing place where a batch of already-normalized
candidate records (FTF-0506's `CanonicalConversationRecord.lineage.raw_sha256`
and `canonical_hash`, the same two values FTF-0508's own
`REQUIRED_PROVENANCE_FIELDS` -- `record_sha256`/`normalized_sha256` -- already
require to be present on every Section 6.11 `DatasetRecord` candidate) is
checked for exact duplication before any row is allowed into split
generation -- this module is that place. It covers Section 10's own methods
1 ("Exact raw SHA-256") and 2 ("Canonical normalized hash") specifically;
near/semantic duplicate detection (methods 4-6) is a separate, later task.

This task's own `database_changes` clause ("No independent schema change.
Consume frozen contract.") fixes this module's shape, the same as
FTF-0506/FTF-0507/FTF-0508: a pure, synchronous, no-I/O, no-DB function.

This task's own acceptance clause ("Raw hash duplicate groups remain
deterministic and trace original record identity") is met by
`detect_exact_duplicates`: records sharing the same `record_sha256` (raw
hash, Section 10 method 1) or the same `normalized_sha256` (canonical
normalized hash, Section 10 method 2) are grouped into a `DuplicateGroup`
that carries every member's `batch_position` (this call's own 0-based
position) and `record_index` (Section 6.11's own row-ordering field, the
same field FTF-0508 already uses as row evidence) -- deterministic given
the same input, since groups are built by a single ordered pass and sorted
by digest, never by insertion-order-dependent set/dict iteration alone.

This task's own negative_acceptance ("Cross-purpose duplicate detection
cannot be disabled to fill sample minimum; hash collisions/errors
surfaced") is met by two real, independently testable design choices:

  - **cannot be disabled**: `detect_exact_duplicates` takes no parameter
    that skips, mutes or downgrades cross-purpose grouping -- there is no
    boolean flag to turn it off to hit Section 9's minimum production
    training count (200) or any other sample-size target. Every duplicate
    group's member `purpose` values (Section 6.8F's own closed
    `DatasetSourceConnection.allowed_purposes_json` vocabulary --
    `training`/`validation`/`held_out_evaluation`/`candidate_development`/
    `baseline_only`, mirrored here as `SUPPORTED_PURPOSES`, the same
    reuse-the-value-set-not-the-cross-boundary-import precedent FTF-0508
    already established for `SUPPORTED_SOURCE_TYPES`, since `workers/data`
    cannot import `apps/api` -- see FTF-0506's own module docstring) are
    always inspected and a group spanning more than one purpose is always
    marked `is_cross_purpose=True` and always included in
    `ExactDuplicateReport.blocking_groups`. Section 10's own literal
    severity table is applied exactly: raw-hash groups block when
    cross-purpose ("exact match: always block across train vs
    held-out"); normalized-hash groups block unconditionally, cross-purpose
    or not ("normalized match: always block").
  - **hash collisions/errors surfaced**: a record with a missing, empty, or
    malformed (`record_sha256`/`normalized_sha256` must each be exactly 64
    lowercase hex characters -- the real shape `hashlib.sha256(...).hexdigest()`
    produces) hash, or an unrecognized `purpose`, never silently drops out
    of grouping -- it is excluded from that specific hash's grouping (a
    hash this module cannot trust is not evidence of a duplicate or its
    absence) and reported as an `ExactDuplicateIssue` with row evidence, the
    same "never raise on a per-row violation, always report" discipline
    FTF-0508's own `RecordSchemaValidationReport` already established. Only
    a batch that is not a real sequence of records at all (not a sequence,
    or empty) raises `ExactDuplicateDetectionError` outright -- there is no
    row to attach that failure's evidence to.

Scope decision, same disclosed precedent as FTF-0506/FTF-0507/FTF-0508/
FTF-1302/FTF-1303/FTF-1304 ("no live caller yet"): this task's own packet
`files` list names only this module and its test, not an importer/route. No
dataset-import pipeline route exists yet in this repository to call
`detect_exact_duplicates` (Section 67.1's actual import pipeline routes are
separate, later, dependency-gated tasks). `proving_symbol` stays blank;
`product_truth_check.py`'s product-true count is deliberately unchanged.

Path refinement recorded before READY (Section 84.3), same precedent as
FTF-0506/FTF-0507/FTF-0508: the packet names
`services/training-worker/data/exact_duplicate_detector.py`, but this
repo's real `SOURCE_ROOTS` (FTF-0001, PR #10) name `workers/data` as the
real data/review worker root -- there is no `services/` tree in this
repository. Placed alongside FTF-0506's/FTF-0507's/FTF-0508's own modules
instead of creating a second, non-existent tree.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

__all__ = [
    "SUPPORTED_PURPOSES",
    "DuplicateGroup",
    "ExactDuplicateDetectionError",
    "ExactDuplicateIssue",
    "ExactDuplicateReport",
    "detect_exact_duplicates",
]

# Section 6.8F's own frozen `DatasetSourceConnection.allowed_purposes_json`
# vocabulary (`apps/api/app/datasets/dataset_source_connection_schema.py`'s
# own `ALLOWED_PURPOSES`), mirrored here rather than imported -- `workers/data`
# and `apps/api` are separate deployable units (FTF-0506's own module
# docstring), and nothing else in this repository imports across that
# boundary in either direction.
SUPPORTED_PURPOSES = frozenset(
    {"training", "validation", "held_out_evaluation", "candidate_development", "baseline_only"}
)

_HEX_DIGITS = frozenset("0123456789abcdef")
_SHA256_HEX_LENGTH = 64

_RAW_HASH_FIELD = "record_sha256"
_NORMALIZED_HASH_FIELD = "normalized_sha256"


class ExactDuplicateDetectionError(ValueError):
    """The candidate record batch itself is not a real sequence of
    records -- not a sequence at all, or empty. There is no row to attach
    this failure's evidence to, so it is raised, not reported as a row
    issue."""


@dataclass(frozen=True)
class ExactDuplicateIssue:
    batch_position: int
    record_index: int | None
    code: str
    message: str


@dataclass(frozen=True)
class DuplicateGroup:
    hash_kind: str  # "raw" or "normalized"
    digest: str
    batch_positions: tuple[int, ...]
    record_indices: tuple[int | None, ...]
    purposes: frozenset[str]
    is_cross_purpose: bool

    @property
    def is_blocking(self) -> bool:
        # Section 10's own literal severity table: normalized matches
        # always block; raw (exact) matches block specifically when they
        # cross purposes (the split-leakage case, "across train vs
        # held-out").
        return self.hash_kind == "normalized" or self.is_cross_purpose


@dataclass(frozen=True)
class ExactDuplicateReport:
    raw_duplicate_groups: tuple[DuplicateGroup, ...]
    normalized_duplicate_groups: tuple[DuplicateGroup, ...]
    issues: tuple[ExactDuplicateIssue, ...]

    @property
    def blocking_groups(self) -> tuple[DuplicateGroup, ...]:
        return tuple(
            group
            for group in (*self.raw_duplicate_groups, *self.normalized_duplicate_groups)
            if group.is_blocking
        )

    @property
    def is_clean(self) -> bool:
        return not self.issues and not self.blocking_groups


def _is_valid_sha256_hex(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == _SHA256_HEX_LENGTH
        and all(char in _HEX_DIGITS for char in value)
    )


@dataclass(frozen=True)
class _ValidCandidate:
    batch_position: int
    record_index: int | None
    purpose: str
    raw: Mapping[str, object]


def _validate_candidates(
    records: Sequence[Mapping[str, object]],
) -> tuple[tuple[_ValidCandidate, ...], tuple[ExactDuplicateIssue, ...]]:
    """One shared pass: candidate shape and `purpose` are checked exactly
    once per record (not once per hash field), so a single bad `purpose`
    never produces two redundant issues."""
    issues: list[ExactDuplicateIssue] = []
    valid: list[_ValidCandidate] = []

    for batch_position, candidate in enumerate(records):
        if not isinstance(candidate, Mapping):
            issues.append(
                ExactDuplicateIssue(
                    batch_position=batch_position,
                    record_index=None,
                    code="invalid_candidate",
                    message="candidate record must be an object",
                )
            )
            continue

        raw_record_index = candidate.get("record_index")
        record_index: int | None
        if isinstance(raw_record_index, int) and not isinstance(raw_record_index, bool):
            record_index = raw_record_index
        else:
            record_index = None

        purpose = candidate.get("purpose")
        if purpose not in SUPPORTED_PURPOSES:
            issues.append(
                ExactDuplicateIssue(
                    batch_position=batch_position,
                    record_index=record_index,
                    code="invalid_purpose",
                    message=f"purpose {purpose!r} is not a recognized value",
                )
            )
            continue

        assert isinstance(purpose, str)
        valid.append(
            _ValidCandidate(
                batch_position=batch_position,
                record_index=record_index,
                purpose=purpose,
                raw=candidate,
            )
        )

    return tuple(valid), tuple(issues)


def _build_groups(
    hash_kind: str,
    field: str,
    candidates: Sequence[_ValidCandidate],
) -> tuple[tuple[DuplicateGroup, ...], tuple[ExactDuplicateIssue, ...]]:
    issues: list[ExactDuplicateIssue] = []
    by_digest: dict[str, list[tuple[int, int | None, str]]] = {}

    for candidate in candidates:
        digest = candidate.raw.get(field)
        if not _is_valid_sha256_hex(digest):
            issues.append(
                ExactDuplicateIssue(
                    batch_position=candidate.batch_position,
                    record_index=candidate.record_index,
                    code="missing_hash" if digest in (None, "") else "invalid_hash",
                    message=(
                        f"{field} must be a 64-character lowercase hex SHA-256 digest, "
                        f"not {digest!r} -- refusing to guess a hash collision that "
                        "cannot be trusted"
                    ),
                )
            )
            continue

        assert isinstance(digest, str)
        by_digest.setdefault(digest, []).append(
            (candidate.batch_position, candidate.record_index, candidate.purpose)
        )

    groups: list[DuplicateGroup] = []
    for digest in sorted(by_digest):
        members = by_digest[digest]
        if len(members) < 2:
            continue
        purposes = frozenset(purpose for _, _, purpose in members)
        groups.append(
            DuplicateGroup(
                hash_kind=hash_kind,
                digest=digest,
                batch_positions=tuple(position for position, _, _ in members),
                record_indices=tuple(index for _, index, _ in members),
                purposes=purposes,
                is_cross_purpose=len(purposes) > 1,
            )
        )

    return tuple(groups), tuple(issues)


def detect_exact_duplicates(
    records: Sequence[Mapping[str, object]],
) -> ExactDuplicateReport:
    """Detects exact duplicate records in an already-normalized candidate
    batch, by raw hash (Section 10 method 1) and by canonical normalized
    hash (Section 10 method 2) independently. Every candidate must carry
    `record_sha256`, `normalized_sha256` (Section 6.11's own provenance
    field names, reused verbatim from FTF-0508) and `purpose` (Section
    6.8F's own closed vocabulary). Cross-purpose duplicate detection always
    runs -- there is no parameter to disable it. Never raises on a per-row
    violation -- returns an `ExactDuplicateReport` whose `issues` carry row
    evidence. Raises `ExactDuplicateDetectionError` only when the batch
    itself is not a real sequence of records (not a sequence, or empty)."""
    if not isinstance(records, Sequence) or isinstance(records, str | bytes):
        raise ExactDuplicateDetectionError("records must be a sequence of candidate records")
    if not records:
        raise ExactDuplicateDetectionError("records must be a non-empty sequence")

    valid_candidates, shared_issues = _validate_candidates(records)
    raw_groups, raw_issues = _build_groups("raw", _RAW_HASH_FIELD, valid_candidates)
    normalized_groups, normalized_issues = _build_groups(
        "normalized", _NORMALIZED_HASH_FIELD, valid_candidates
    )

    return ExactDuplicateReport(
        raw_duplicate_groups=raw_groups,
        normalized_duplicate_groups=normalized_groups,
        issues=shared_issues + raw_issues + normalized_issues,
    )
