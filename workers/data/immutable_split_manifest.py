"""Immutable split manifest, pure domain (FTF-0519-P1; FINE_TUNING_FACTORY_RELEASE_1.md
Sections 0 items 13-14, 6.10-6.12, 7, 7A.10, 8.1 steps 13/16-18, 8.2, 9, 10, 65, 67.2/67.4, 77, 81).

Acceptance ("Freeze hashes/counts/roles/grants for all four dataset purposes and version on
change"): this module is the no-I/O half. FTF-0517 allocates a four-role `SplitManifest` and
FTF-0518 reports its leakage; here the result is turned into the data that is frozen:

  - the fixed role <-> purpose map (Section 9): train<->training, validation<->validation,
    development_eval<->candidate_development, final_qualification<->held_out_evaluation. A source
    dataset whose purpose is `held_out_evaluation` or `candidate_development` is pinned to its
    evaluation role by `pinned_role_for_purpose`; `training` / `validation` sources are free;
    `baseline_only` and anything else is not a split purpose and is refused;
  - `role_sha256`: SHA-256 over the role's sorted (record_id, record_sha256, normalized_sha256,
    group_id) rows, so it does not depend on record order or `PYTHONHASHSEED`;
  - `ManifestContent`: every field that is frozen (header, four role rows, every source with its
    grant snapshot, the content-free leakage report, its reviews). `compute_proposal_sha256` is the
    hash an approver confirms; `compute_frozen_manifest_sha256` additionally covers the reviewed
    leakage report hash, the reviews and the approver, so editing any frozen field, a role hash, a
    grant or the reviewed report changes it;
  - `report_from_body`: rebuilds FTF-0518's `LeakageReport` from the stored content-free body and
    refuses a body whose hash is not the stored one (a database edit of the report);
  - legacy migration: a three-split manifest (`train` / `validation` / `heldout_test`, Section
    6.10/6.12 shape) never reads implicitly. `migrate_legacy_three_split` builds a four-role
    manifest explicitly: `heldout_test` -> `final_qualification` only (Section 9: never
    reclassified into training), train / validation keep their roles, and a development role exists
    only from explicitly supplied `candidate_development` records, otherwise the manifest is
    `insufficient` and cannot be locked;
  - training view: `assert_training_rows` refuses any row outside train / validation.

Nothing here accepts an outcome, score or metric (Law 12), reads a record's content, or touches a
database. The store (`apps/api/app/datasets/dataset_split_manifest_store.py`) owns persistence.
No live caller yet (the dataset import runtime is not dependency-ready), so `proving_symbol` stays
blank, the same precedent as FTF-0517 / FTF-0518. No new dependency.
"""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Final

from workers.data.group_aware_split_engine import (
    ROLES,
    SplitEngineError,
    SplitManifest,
    compute_manifest_sha256,
    verify_manifest,
)
from workers.data.split_leakage_detector import (
    STATUSES,
    LeakageEngineError,
    LeakageFinding,
    LeakageIssue,
    LeakageReport,
    LeakageReview,
    compute_report_sha256,
    report_body,
    verify_report,
)

__all__ = [
    "DEVELOPMENT_ROLES",
    "LEGACY_ROLES",
    "LEGACY_STRATEGY",
    "MAX_MANIFEST_RECORDS",
    "PINNED_PURPOSE_ROLE",
    "ROLE_PURPOSE",
    "TRAINING_ROLES",
    "AssignmentRow",
    "LegacyThreeSplitManifest",
    "ManifestContent",
    "ManifestDomainError",
    "RoleRow",
    "SourceGrant",
    "assert_development_rows",
    "assert_training_rows",
    "build_assignment_rows",
    "build_role_rows",
    "compute_frozen_manifest_sha256",
    "compute_legacy_sha256",
    "compute_proposal_sha256",
    "excluded_positions_sha256",
    "is_legacy_three_split",
    "migrate_legacy_three_split",
    "pinned_role_for_purpose",
    "report_from_body",
    "role_sha256",
    "verify_frozen_content",
]

ROLE_PURPOSE: Final[Mapping[str, str]] = {
    "train": "training",
    "validation": "validation",
    "development_eval": "candidate_development",
    "final_qualification": "held_out_evaluation",
}
PINNED_PURPOSE_ROLE: Final[Mapping[str, str]] = {
    "candidate_development": "development_eval",
    "held_out_evaluation": "final_qualification",
}
_FREE_PURPOSES: Final = ("training", "validation")
TRAINING_ROLES: Final = ("train", "validation")
DEVELOPMENT_ROLES: Final = ("development_eval",)
LEGACY_STRATEGY: Final = "legacy_three_split_v1"
LEGACY_ROLES: Final = ("train", "validation", "heldout_test")
_LEGACY_ROLE_MAP: Final = {
    "train": "train",
    "validation": "validation",
    "heldout_test": "final_qualification",
}
# A manifest above this size is refused by the store, never truncated (Law 17).
MAX_MANIFEST_RECORDS: Final = 1_000_000
_MAX_TEXT = 200
_MAX_SEED = 128
_SHA256 = re.compile(r"[0-9a-f]{64}")
_LEAKAGE_STATUSES: Final = STATUSES


class ManifestDomainError(ValueError):
    """Malformed input or an untrustworthy stored manifest. Never raised for a normal freeze."""


def _text(value: object, name: str, limit: int = _MAX_TEXT) -> str:
    # Exact type, not isinstance: a str subclass with a foreign hash / eq must never become an id.
    if type(value) is not str or not value or value != value.strip() or len(value) > limit:
        raise ManifestDomainError(f"{name} must be a clean non-empty string of at most {limit}")
    return value


def _sha(value: object, name: str) -> str:
    if type(value) is not str or not _SHA256.fullmatch(value):
        raise ManifestDomainError(f"{name} must be 64 lowercase hex characters")
    return value


def _opt_sha(value: object, name: str) -> str | None:
    return None if value is None else _sha(value, name)


def _int(value: object, name: str, low: int, high: int) -> int:
    if type(value) is not int or not low <= value <= high:
        raise ManifestDomainError(f"{name} must be an int in {low}..{high}")
    return value


def _uuid_text(value: object, name: str) -> str:
    if type(value) is not str:
        raise ManifestDomainError(f"{name} must be a canonical UUID string")
    try:
        canonical = str(uuid.UUID(value))
    except ValueError:
        raise ManifestDomainError(f"{name} must be a canonical UUID string") from None
    if canonical != value:
        raise ManifestDomainError(f"{name} must be a canonical UUID string")
    return value


def _role(value: object, name: str = "role") -> str:
    # Type before membership, so an unhashable value raises this module's error.
    if type(value) is not str or value not in ROLE_PURPOSE:
        raise ManifestDomainError(f"{name} must be one of {ROLES}")
    return value


def _canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _digest(value: object) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def pinned_role_for_purpose(purpose: object) -> str | None:
    """The evaluation role a source dataset of `purpose` is pinned to, or None for a free
    (`training` / `validation`) source. Anything else, `baseline_only` included, is not a split
    purpose."""
    if type(purpose) is not str:
        raise ManifestDomainError("purpose must be a string")
    if purpose in PINNED_PURPOSE_ROLE:
        return PINNED_PURPOSE_ROLE[purpose]
    if purpose in _FREE_PURPOSES:
        return None
    raise ManifestDomainError(f"purpose {purpose!r} is not a split purpose")


# ---------------------------------------------------------------------------
# Rows and role hashes
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AssignmentRow:
    """One record's frozen placement. `record_id` is `"{source_dataset_id}:{batch_position}"`."""

    record_id: str
    source_dataset_id: str
    batch_position: int
    role: str
    group_id: str
    record_sha256: str
    normalized_sha256: str

    def __post_init__(self) -> None:
        _text(self.record_id, "record_id", 400)
        _uuid_text(self.source_dataset_id, "source_dataset_id")
        _int(self.batch_position, "batch_position", 0, 2**31 - 1)
        _role(self.role)
        _text(self.group_id, "group_id", 400)
        _sha(self.record_sha256, "record_sha256")
        _sha(self.normalized_sha256, "normalized_sha256")
        if self.record_id != f"{self.source_dataset_id}:{self.batch_position}":
            raise ManifestDomainError("record_id must be '<source_dataset_id>:<batch_position>'")


def role_sha256(rows: Sequence[AssignmentRow]) -> str:
    """SHA-256 over the rows' sorted (record_id, record_sha256, normalized_sha256, group_id)."""
    entries = sorted((r.record_id, r.record_sha256, r.normalized_sha256, r.group_id) for r in rows)
    return _digest([list(entry) for entry in entries])


@dataclass(frozen=True)
class RoleRow:
    role: str
    purpose: str
    record_count: int
    group_count: int
    role_sha256: str

    def __post_init__(self) -> None:
        _role(self.role)
        if self.purpose != ROLE_PURPOSE[self.role]:
            raise ManifestDomainError("purpose does not match the fixed role <-> purpose map")
        _int(self.record_count, "record_count", 0, MAX_MANIFEST_RECORDS)
        _int(self.group_count, "group_count", 0, MAX_MANIFEST_RECORDS)
        _sha(self.role_sha256, "role_sha256")
        if self.group_count > self.record_count:
            raise ManifestDomainError("a role cannot have more groups than records")
        if (self.record_count == 0) != (self.group_count == 0):
            raise ManifestDomainError("record_count and group_count must both be zero or neither")


def build_role_rows(assignments: Sequence[AssignmentRow]) -> tuple[RoleRow, ...]:
    """Always four rows, in `ROLES` order, counted from the assignments alone."""
    if type(assignments) not in (tuple, list):
        raise ManifestDomainError("assignments must be a list or tuple")
    seen: set[str] = set()
    by_role: dict[str, list[AssignmentRow]] = {role: [] for role in ROLES}
    for row in assignments:
        if not isinstance(row, AssignmentRow):
            raise ManifestDomainError("assignments must contain AssignmentRow values")
        if row.record_id in seen:
            raise ManifestDomainError("duplicate record_id")
        seen.add(row.record_id)
        by_role[row.role].append(row)
    return tuple(
        RoleRow(
            role=role,
            purpose=ROLE_PURPOSE[role],
            record_count=len(by_role[role]),
            group_count=len({r.group_id for r in by_role[role]}),
            role_sha256=role_sha256(by_role[role]),
        )
        for role in ROLES
    )


def build_assignment_rows(
    manifest: SplitManifest, origins: Mapping[str, tuple[str, int, str, str]]
) -> tuple[AssignmentRow, ...]:
    """`manifest`'s assignments joined to `origins[record_id] = (source_dataset_id,
    batch_position, record_sha256, normalized_sha256)`. A record without an origin, or an origin
    without a record, is refused."""
    try:
        verify_manifest(manifest)
    except SplitEngineError as exc:
        raise ManifestDomainError(f"manifest is not a valid split manifest: {exc}") from None
    if set(origins) != {record_id for record_id, _, _ in manifest.assignments}:
        raise ManifestDomainError("origins do not cover exactly the manifest's records")
    rows = []
    for record_id, role, group_id in manifest.assignments:
        source, position, raw, normalized = origins[record_id]
        rows.append(AssignmentRow(record_id, source, position, role, group_id, raw, normalized))
    return tuple(rows)


def excluded_positions_sha256(positions: Sequence[int]) -> str:
    """SHA-256 of the sorted, strictly increasing excluded batch positions."""
    if type(positions) not in (tuple, list):
        raise ManifestDomainError("positions must be a list or tuple")
    for position in positions:
        _int(position, "position", 0, 2**31 - 1)
    if list(positions) != sorted(set(positions)):
        raise ManifestDomainError("positions must be sorted and unique")
    return _digest({"excluded_batch_positions": list(positions)})


# ---------------------------------------------------------------------------
# Grants and the frozen content
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SourceGrant:
    """One source dataset version with the grant snapshot taken when the manifest was proposed."""

    dataset_id: str
    dataset_version: int
    dataset_purpose: str
    dataset_sha256: str
    dataset_source_connection_id: str
    allowed_purposes_snapshot: tuple[str, ...]
    quality_report_id: str
    quality_report_sha256: str
    excluded_positions_sha256: str

    def __post_init__(self) -> None:
        _uuid_text(self.dataset_id, "dataset_id")
        _int(self.dataset_version, "dataset_version", 1, 2**31 - 1)
        pinned_role_for_purpose(self.dataset_purpose)
        _sha(self.dataset_sha256, "dataset_sha256")
        _uuid_text(self.dataset_source_connection_id, "dataset_source_connection_id")
        snapshot = self.allowed_purposes_snapshot
        if type(snapshot) is not tuple or not snapshot:
            raise ManifestDomainError("allowed_purposes_snapshot must be a non-empty tuple")
        for purpose in snapshot:
            _text(purpose, "allowed purpose", 64)
        if list(snapshot) != sorted(set(snapshot)):
            raise ManifestDomainError("allowed_purposes_snapshot must be sorted and unique")
        _uuid_text(self.quality_report_id, "quality_report_id")
        _sha(self.quality_report_sha256, "quality_report_sha256")
        _sha(self.excluded_positions_sha256, "excluded_positions_sha256")


def _role_dict(row: RoleRow) -> dict[str, object]:
    return {
        "role": row.role,
        "purpose": row.purpose,
        "record_count": row.record_count,
        "group_count": row.group_count,
        "role_sha256": row.role_sha256,
    }


def _source_dict(grant: SourceGrant) -> dict[str, object]:
    return {
        "dataset_id": grant.dataset_id,
        "dataset_version": grant.dataset_version,
        "dataset_purpose": grant.dataset_purpose,
        "dataset_sha256": grant.dataset_sha256,
        "dataset_source_connection_id": grant.dataset_source_connection_id,
        "allowed_purposes_snapshot": list(grant.allowed_purposes_snapshot),
        "quality_report_id": grant.quality_report_id,
        "quality_report_sha256": grant.quality_report_sha256,
        "excluded_positions_sha256": grant.excluded_positions_sha256,
    }


@dataclass(frozen=True)
class ManifestContent:
    """Everything a lock freezes. Validated on construction; the store builds one from the rows
    it just wrote (proposal) and again from the rows it reads back (training access)."""

    project_id: str
    name: str
    version: int
    parent_version: int | None
    strategy: str
    seed: str
    ratios: tuple[int, int, int, int]
    cutoff: int | None
    normalization_version: str
    tokenizer_identity: str | None
    split_manifest_sha256: str
    leakage_report: Mapping[str, object]
    leakage_report_sha256: str
    leakage_status: str
    roles: tuple[RoleRow, ...]
    sources: tuple[SourceGrant, ...]
    leakage_reviews: tuple[Mapping[str, object], ...] = ()
    reviewed_leakage_report_sha256: str | None = None
    migrated_from_legacy_sha256: str | None = None
    approved_by: str | None = None

    def __post_init__(self) -> None:
        _uuid_text(self.project_id, "project_id")
        _text(self.name, "name")
        _int(self.version, "version", 1, 2**31 - 1)
        if self.parent_version is not None:
            _int(self.parent_version, "parent_version", 1, 2**31 - 1)
        if (self.version == 1) != (self.parent_version is None):
            raise ManifestDomainError("parent_version must be None exactly for version 1")
        if self.parent_version is not None and self.parent_version != self.version - 1:
            raise ManifestDomainError("parent_version must be version - 1")
        _text(self.strategy, "strategy", 64)
        _text(self.seed, "seed", _MAX_SEED)
        if type(self.ratios) is not tuple or len(self.ratios) != len(ROLES):
            raise ManifestDomainError("ratios must be a tuple of four ints")
        for ratio in self.ratios:
            _int(ratio, "ratio", 0, 100)
        if self.cutoff is not None:
            _int(self.cutoff, "cutoff", 0, 10**13)
        _text(self.normalization_version, "normalization_version", 64)
        if self.tokenizer_identity is not None:
            _text(self.tokenizer_identity, "tokenizer_identity")
        _sha(self.split_manifest_sha256, "split_manifest_sha256")
        if not isinstance(self.leakage_report, Mapping):
            raise ManifestDomainError("leakage_report must be a mapping")
        _sha(self.leakage_report_sha256, "leakage_report_sha256")
        if type(self.leakage_status) is not str or self.leakage_status not in _LEAKAGE_STATUSES:
            raise ManifestDomainError(f"leakage_status must be one of {_LEAKAGE_STATUSES}")
        if type(self.roles) is not tuple or len(self.roles) != len(ROLES):
            raise ManifestDomainError("exactly four role rows are required")
        for row, role in zip(self.roles, ROLES, strict=True):
            if not isinstance(row, RoleRow) or row.role != role:
                raise ManifestDomainError("role rows must be RoleRow values in ROLES order")
        if type(self.sources) is not tuple or not self.sources:
            raise ManifestDomainError("sources must be a non-empty tuple")
        ids = [s.dataset_id for s in self.sources if isinstance(s, SourceGrant)]
        if len(ids) != len(self.sources) or len(set(ids)) != len(ids):
            raise ManifestDomainError("sources must be distinct SourceGrant values")
        if ids != sorted(ids):
            raise ManifestDomainError("sources must be ordered by dataset_id")
        if type(self.leakage_reviews) is not tuple:
            raise ManifestDomainError("leakage_reviews must be a tuple")
        for review in self.leakage_reviews:
            if not isinstance(review, Mapping):
                raise ManifestDomainError("each review must be a mapping")
        _opt_sha(self.reviewed_leakage_report_sha256, "reviewed_leakage_report_sha256")
        _opt_sha(self.migrated_from_legacy_sha256, "migrated_from_legacy_sha256")
        if (self.strategy == LEGACY_STRATEGY) != (self.migrated_from_legacy_sha256 is not None):
            raise ManifestDomainError("the legacy strategy and its lineage hash go together")
        if self.approved_by is not None:
            _text(self.approved_by, "approved_by")


def _proposal_body(content: ManifestContent) -> dict[str, object]:
    return {
        "project_id": content.project_id,
        "name": content.name,
        "version": content.version,
        "parent_version": content.parent_version,
        "strategy": content.strategy,
        "seed": content.seed,
        "ratios": list(content.ratios),
        "cutoff": content.cutoff,
        "normalization_version": content.normalization_version,
        "tokenizer_identity": content.tokenizer_identity,
        "split_manifest_sha256": content.split_manifest_sha256,
        "leakage_report_sha256": content.leakage_report_sha256,
        "leakage_status": content.leakage_status,
        "migrated_from_legacy_sha256": content.migrated_from_legacy_sha256,
        "roles": [_role_dict(r) for r in content.roles],
        "sources": [_source_dict(s) for s in content.sources],
    }


def compute_proposal_sha256(content: ManifestContent) -> str:
    """The hash an approver confirms: the proposal as it was written, before any review."""
    return _digest(_proposal_body(content))


def compute_frozen_manifest_sha256(content: ManifestContent) -> str:
    """Canonical JSON over every frozen field: the proposal, the content-free leakage report, the
    reviews, the reviewed report hash, the approver, all four role rows and every source grant."""
    if content.reviewed_leakage_report_sha256 is None or content.approved_by is None:
        raise ManifestDomainError("only a reviewed and approved manifest has a frozen hash")
    return _digest(
        {
            "proposal": _proposal_body(content),
            "proposal_sha256": compute_proposal_sha256(content),
            "leakage_report": dict(content.leakage_report),
            "leakage_reviews": [dict(r) for r in content.leakage_reviews],
            "reviewed_leakage_report_sha256": content.reviewed_leakage_report_sha256,
            "approved_by": content.approved_by,
        }
    )


def verify_frozen_content(content: ManifestContent, frozen_manifest_sha256: str) -> None:
    """Raises `ManifestDomainError` unless `frozen_manifest_sha256` covers `content` exactly and
    the stored leakage report still hashes to what the manifest recorded."""
    _sha(frozen_manifest_sha256, "frozen_manifest_sha256")
    report = report_from_body(content.leakage_report)
    if compute_report_sha256(report) != content.leakage_report_sha256:
        raise ManifestDomainError("the stored leakage report does not hash to its recorded hash")
    if compute_frozen_manifest_sha256(content) != frozen_manifest_sha256:
        raise ManifestDomainError("the frozen manifest hash does not cover its content")


# ---------------------------------------------------------------------------
# Leakage report round trip
# ---------------------------------------------------------------------------

_BODY_KEYS = frozenset(
    {"status", "reason_code", "findings", "issues", "methods_run", "methods_not_run", "reviews"}
)
_FINDING_KEYS = frozenset(
    {
        "finding_id",
        "method",
        "reason",
        "disposition",
        "record_id_a",
        "role_a",
        "record_id_b",
        "role_b",
        "prior_source_label",
        "scores",
    }
)
_REVIEW_KEYS = frozenset({"finding_id", "reviewer_id", "decision", "rationale"})


def _keys(value: object, keys: frozenset[str], name: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping) or set(value) != keys:
        raise ManifestDomainError(f"{name} has the wrong shape")
    return value


def _list(value: object, name: str) -> list[object]:
    if type(value) is not list:
        raise ManifestDomainError(f"{name} must be a list")
    return value


def _score(entry: object) -> tuple[str, float]:
    if type(entry) is not list or len(entry) != 2 or type(entry[0]) is not str:
        raise ManifestDomainError("scores must be [name, number] pairs")
    value = entry[1]
    if type(value) not in (int, float):
        raise ManifestDomainError("scores must be [name, number] pairs")
    return entry[0], float(value)


def report_from_body(body: Mapping[str, object]) -> LeakageReport:
    """The `LeakageReport` whose `report_body` is `body`, sealed with its recomputed hash. The
    caller compares that hash with the recorded one. Reviews are accepted (a reviewed body) and
    bound unchanged. Any malformed body raises `ManifestDomainError`."""
    data = _keys(body, _BODY_KEYS, "leakage report body")
    try:
        findings = tuple(
            LeakageFinding(
                finding_id=f["finding_id"],  # type: ignore[arg-type]
                method=f["method"],  # type: ignore[arg-type]
                reason=f["reason"],  # type: ignore[arg-type]
                disposition=f["disposition"],  # type: ignore[arg-type]
                record_id_a=f["record_id_a"],  # type: ignore[arg-type]
                role_a=f["role_a"],  # type: ignore[arg-type]
                record_id_b=f["record_id_b"],  # type: ignore[arg-type]
                role_b=f["role_b"],  # type: ignore[arg-type]
                prior_source_label=f["prior_source_label"],  # type: ignore[arg-type]
                scores=tuple(_score(s) for s in _list(f["scores"], "scores")),
            )
            for f in (
                _keys(item, _FINDING_KEYS, "finding")
                for item in _list(data["findings"], "findings")
            )
        )
        issues = tuple(
            LeakageIssue(
                code=i["code"],  # type: ignore[arg-type]
                record_id=i["record_id"],  # type: ignore[arg-type]
                source_label=i["source_label"],  # type: ignore[arg-type]
            )
            for i in (
                _keys(item, frozenset({"code", "record_id", "source_label"}), "issue")
                for item in _list(data["issues"], "issues")
            )
        )
        reviews = tuple(
            LeakageReview(
                finding_id=r["finding_id"],  # type: ignore[arg-type]
                reviewer_id=r["reviewer_id"],  # type: ignore[arg-type]
                decision=r["decision"],  # type: ignore[arg-type]
                rationale=r["rationale"],  # type: ignore[arg-type]
            )
            for r in (
                _keys(item, _REVIEW_KEYS, "review") for item in _list(data["reviews"], "reviews")
            )
        )
        report = LeakageReport(
            status=data["status"],  # type: ignore[arg-type]
            reason_code=data["reason_code"],  # type: ignore[arg-type]
            findings=findings,
            issues=issues,
            methods_run=tuple(_list(data["methods_run"], "methods_run")),  # type: ignore[arg-type]
            methods_not_run=tuple(_list(data["methods_not_run"], "methods_not_run")),  # type: ignore[arg-type]
            reviews=reviews,
        )
    except LeakageEngineError as exc:
        raise ManifestDomainError(f"leakage report body is malformed: {exc}") from None
    sealed = replace(report, report_sha256=compute_report_sha256(report))
    if report_body(sealed) != _normalised_body(data):
        raise ManifestDomainError("leakage report body does not round-trip")
    try:
        verify_report(sealed)
    except LeakageEngineError as exc:
        raise ManifestDomainError(f"leakage report is inconsistent: {exc}") from None
    return sealed


def _normalised_body(data: Mapping[str, object]) -> dict[str, object]:
    # Scores are floats in a rebuilt report; an integer-valued JSON number must compare equal.
    loaded: dict[str, object] = json.loads(_canonical(data))
    return loaded


# ---------------------------------------------------------------------------
# Legacy three-split migration
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LegacyThreeSplitManifest:
    """The Section 6.10/6.12 shape: `train` / `validation` / `heldout_test`, no development role.
    `assignments` are (record_id, role, group_id); `legacy_sha256` must cover them and the seed."""

    seed: str
    assignments: tuple[tuple[str, str, str], ...]
    legacy_sha256: str

    def __post_init__(self) -> None:
        _text(self.seed, "seed", _MAX_SEED)
        if type(self.assignments) is not tuple or not self.assignments:
            raise ManifestDomainError("assignments must be a non-empty tuple")
        for entry in self.assignments:
            if type(entry) is not tuple or len(entry) != 3:
                raise ManifestDomainError("each assignment must be (record_id, role, group_id)")
            _text(entry[0], "record_id", 400)
            if type(entry[1]) is not str or entry[1] not in LEGACY_ROLES:
                raise ManifestDomainError(f"a legacy role must be one of {LEGACY_ROLES}")
            _text(entry[2], "group_id", 400)
        _sha(self.legacy_sha256, "legacy_sha256")
        if self.legacy_sha256 != compute_legacy_sha256(self.seed, self.assignments):
            raise ManifestDomainError("the legacy manifest hash does not cover its content")


def compute_legacy_sha256(seed: str, assignments: Sequence[tuple[str, str, str]]) -> str:
    return _digest(
        {"strategy": LEGACY_STRATEGY, "seed": seed, "assignments": [list(a) for a in assignments]}
    )


def is_legacy_three_split(value: object) -> bool:
    """True for a `LegacyThreeSplitManifest`, the legacy strategy name, a three-ratio tuple, or any
    role list that holds `heldout_test`. `propose` refuses all of these: no implicit mapping."""
    if isinstance(value, LegacyThreeSplitManifest):
        return True
    if type(value) is str:
        return value in (LEGACY_STRATEGY, "heldout_test")
    if isinstance(value, tuple | list):
        if len(value) == 3 and all(type(v) is int for v in value):
            return True
        return any(type(v) is str and v == "heldout_test" for v in value)
    return False


def migrate_legacy_three_split(
    legacy: LegacyThreeSplitManifest,
    development: Sequence[tuple[str, str]] = (),
) -> SplitManifest:
    """An explicit, verified four-role manifest. `heldout_test` becomes `final_qualification`
    and nothing else; `development` is (record_id, group_id) pairs from an explicit
    `candidate_development` dataset. With none, the result is `insufficient` and cannot lock."""
    if not isinstance(legacy, LegacyThreeSplitManifest):
        raise ManifestDomainError("legacy must be a LegacyThreeSplitManifest")
    if type(development) not in (tuple, list):
        raise ManifestDomainError("development must be a list or tuple")
    placed: list[tuple[str, str, str]] = [
        (record_id, _LEGACY_ROLE_MAP[role], group_id)
        for record_id, role, group_id in legacy.assignments
    ]
    for entry in development:
        if type(entry) is not tuple or len(entry) != 2:
            raise ManifestDomainError("development entries must be (record_id, group_id)")
        placed.append(
            (
                _text(entry[0], "record_id", 400),
                "development_eval",
                _text(entry[1], "group_id", 400),
            )
        )
    assignments = tuple(sorted(placed))
    if len({a[0] for a in assignments}) != len(assignments):
        raise ManifestDomainError("duplicate record_id")
    tally: dict[str, tuple[int, set[str]]] = {role: (0, set()) for role in ROLES}
    for _, role, group_id in assignments:
        count, groups = tally[role]
        tally[role] = (count + 1, groups | {group_id})
    counts = tuple((role, tally[role][0], len(tally[role][1])) for role in ROLES)
    manifest = SplitManifest(
        strategy=LEGACY_STRATEGY,
        seed=legacy.seed,
        ratios=(0, 0, 0, 0),
        cutoff=None,
        assignments=assignments,
        counts=counts,
        status="insufficient" if any(count == 0 for _, count, _ in counts) else "ready",
        manifest_sha256="",
    )
    sealed = replace(manifest, manifest_sha256=compute_manifest_sha256(manifest))
    try:
        verify_manifest(sealed)
    except SplitEngineError as exc:
        raise ManifestDomainError(f"the migrated manifest is not valid: {exc}") from None
    return sealed


# ---------------------------------------------------------------------------
# Training / development views
# ---------------------------------------------------------------------------


def assert_training_rows(rows: Sequence[AssignmentRow]) -> tuple[AssignmentRow, ...]:
    """The rows if every one is train / validation; any other role raises. The store filters in
    SQL first, so this is the second, independent check (a final record is never served)."""
    for row in rows:
        if not isinstance(row, AssignmentRow) or row.role not in TRAINING_ROLES:
            raise ManifestDomainError("a non-training record reached a training view")
    return tuple(rows)


def assert_development_rows(rows: Sequence[AssignmentRow]) -> tuple[AssignmentRow, ...]:
    for row in rows:
        if not isinstance(row, AssignmentRow) or row.role not in DEVELOPMENT_ROLES:
            raise ManifestDomainError("a non-development record reached a development view")
    return tuple(rows)
