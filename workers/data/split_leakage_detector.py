"""Split leakage detector (FTF-0518; FINE_TUNING_FACTORY_RELEASE_1.md Sections 7-10, 67, 77, 81).

Acceptance ("Check exact/normalized/group/semantic cross-purpose leakage including previous
campaigns"): `detect_split_leakage(manifest, records, ...)` takes a FTF-0517 `SplitManifest`, one
`LeakageRecord` per manifest record, optional `PriorQualificationSource`s (earlier campaigns'
development / final suites) and an `EmbeddingRoute`, and returns a hash-bound `LeakageReport`.

Eight Section 10 methods, in this order (`METHODS`):

  1. `exact_raw`             the caller's `record_sha256` (FTF-0511 `detect_exact_duplicates`);
  2. `canonical_normalized`  the caller's `normalized_sha256` (the same detector);
  3. `text_normalized`       FTF-0512 `detect_normalized_duplicates`;
  4. `group`                 a linkage key (the manifest's group id, or any key the record carries)
                             that appears in two pair-classes;
  5-8. `char_ngram`, `token_jaccard`, `embedding`, `agent_trace_structural`: FTF-0513
                             `detect_near_duplicates`, called with
                             `NearDuplicateThresholds(0, 0, 0)` so every LSH candidate pair comes
                             back with its scores; this module then applies the configured
                             thresholds with `>=`.

Nothing is re-implemented: the three detectors are called, not copied. Only the pair-class
rules, the dispositions, the saturation accounting, the route check and the review gate are here.

Pair classes checked (everything else is not, and is disclosed):

  - two current records in different roles                          reason `cross_role`;
  - a prior development / final record against a current train or
    validation record                                              reason `prior_campaign`;
  - a prior final record against a current final record             reason `consumed_final_reuse`
    (Section 9: a consumed final suite is not reused).
  Not checked: prior development against current development or final, current records in the
  same role, two prior sources against each other.

Dispositions (Section 10 table): methods 1-4 are `blocking` and cannot be reviewed away; they
clear only through a new manifest without the pair. Every `consumed_final_reuse` finding is
`blocking` whatever the method. Semantic / structural `cross_role` and `prior_campaign` findings are
`review_required`.

Embedding route (negative clause): before any semantic work, FTF-0901 `enforce_effect` is asked
whether the `data` surface may use `route.destination`. A coded refusal
(`security_profile_route_blocked`, `security_profile_route_not_authorized`,
`security_profile_invalid_request`) makes the report `blocked` with that code; an `internal`
destination runs FTF-0513's in-process hashing embedding; any other destination that policy
allows is `blocked` / `embedding_provider_unavailable` (no external embedding client ships;
Laws 11 and 17).
Methods 1-4 are always reported.

Saturation (Law 17): FTF-0513 silently stops adding partners at `max_candidates_per_record`.
Here a record that reaches `max_semantic_partners` (default 200) makes the semantic stage
incomplete: the report is `blocked` / `semantic_scan_saturated`, never "no semantic leakage".
Likewise more than `max_findings` findings is `blocked` / `finding_limit_exceeded`.

Report: `status` is `clear` only if every method ran on every pair class and nothing was found;
`leakage_found` for findings; `blocked` when a stage could not complete; `failed` for a tampered
manifest (`manifest_invalid`), a record set that differs from the manifest
(`record_set_mismatch`) or an unscannable record (`invalid_input`, with `issues`). A report names
both record ids, both roles and the method of every finding and carries no content, no text and no
content hash. `finding_id` and `report_sha256` are SHA-256 of canonical JSON, so they do not depend
on `PYTHONHASHSEED` or on the order the caller listed its records.

Gate: `apply_leakage_reviews` binds a `LeakageReview` only to a `review_required` finding;
`assert_leakage_clear(report, stage)` (`final_freeze` or `training`) raises `LeakageBlockedError`
unless the report is `clear` or every finding is reviewed `not_leakage`; `blocked` and `failed`
reports always raise, and a report whose hash does not cover its fields is refused.
Caller contract: FTF-0519 / the training workflow calls this AND FTF-0515
`assert_clear_for_training`; this module does not edit FTF-0515.

Scope (disclosed): the hashing embedding is a lexical proxy (FTF-0513 docstring), not a trained
model; no persistence of findings or reviews (needs a later migration); no live caller yet, so
`proving_symbol` stays blank. Pure and synchronous, no I/O, no new dependency.
"""

from __future__ import annotations

import hashlib
import hmac
import itertools
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import Any, Final

from packages.security.security_profile_enforcement import (
    Destination,
    EffectRequest,
    RouteAuthorization,
    SecurityProfileEnforcementError,
    enforce_effect,
)

from workers.data.exact_duplicate_detector import detect_exact_duplicates
from workers.data.group_aware_split_engine import (
    ROLES,
    SplitEngineError,
    SplitManifest,
    verify_manifest,
)
from workers.data.near_duplicate_detector import (
    DEFAULT_THRESHOLDS,
    NearDuplicateThresholds,
    detect_near_duplicates,
)
from workers.data.normalized_duplicate_detector import detect_normalized_duplicates

__all__ = [
    "DEFAULT_MAX_FINDINGS",
    "DEFAULT_MAX_SEMANTIC_PARTNERS",
    "DEFAULT_THRESHOLDS",
    "METHODS",
    "SEMANTIC_METHODS",
    "STAGES",
    "STATUSES",
    "EmbeddingRoute",
    "LeakageBlockedError",
    "LeakageEngineError",
    "LeakageFinding",
    "LeakageIssue",
    "LeakageRecord",
    "LeakageReport",
    "LeakageReview",
    "PriorQualificationSource",
    "apply_leakage_reviews",
    "assert_leakage_clear",
    "compute_report_sha256",
    "detect_split_leakage",
    "report_body",
    "verify_report",
]

METHODS: Final = (
    "exact_raw",
    "canonical_normalized",
    "text_normalized",
    "group",
    "char_ngram",
    "token_jaccard",
    "embedding",
    "agent_trace_structural",
)
HASH_METHODS: Final = METHODS[:4]
SEMANTIC_METHODS: Final = METHODS[4:]
STAGES: Final = ("final_freeze", "training")
STATUSES: Final = ("clear", "leakage_found", "blocked", "failed")
DISPOSITIONS: Final = ("blocking", "review_required")
REASONS: Final = ("cross_role", "prior_campaign", "consumed_final_reuse")
PRIOR_ROLES: Final = ("development_eval", "final_qualification")
ISSUE_CODES: Final = ("invalid_record_hash", "invalid_content", "invalid_record")
FAILED_CODES: Final = ("manifest_invalid", "record_set_mismatch", "invalid_input")
BLOCKED_CODES: Final = (
    "security_profile_route_blocked",
    "security_profile_route_not_authorized",
    "security_profile_invalid_request",
    "embedding_provider_unavailable",
    "semantic_scan_saturated",
    "finding_limit_exceeded",
)
DEFAULT_MAX_SEMANTIC_PARTNERS: Final = 200
DEFAULT_MAX_FINDINGS: Final = 100_000
_MAX_FINDINGS_CEILING: Final = 1_000_000
_MAX_REVIEWER: Final = 200
_MAX_RATIONALE: Final = 2000
_SHA256: Final = re.compile(r"[0-9a-f]{64}")
_PURPOSE_BY_ROLE: Final = {
    "train": "training",
    "validation": "validation",
    "development_eval": "candidate_development",
    "final_qualification": "held_out_evaluation",
}
_ISSUE_BY_DETECTOR_CODE: Final = {
    "missing_hash": "invalid_record_hash",
    "unknown_content": "invalid_content",
}
_SEMANTIC_THRESHOLD_FIELDS: Final = (
    ("char_ngram", "char_ngram_similarity", "character_ngram_similarity"),
    ("token_jaccard", "token_jaccard_similarity", "token_jaccard_similarity"),
    ("embedding", "embedding_cosine_similarity", "embedding_cosine_similarity"),
)


# Closed tables instead of string comparisons: every key below is a validated enum member, so a
# lookup cannot be fooled by case or padding and has no "normalised" variant to test.
_DISPOSITION: Final = {
    (method, reason): (
        "blocking"
        if reason == "consumed_final_reuse" or method in HASH_METHODS
        else "review_required"
    )
    for method in METHODS
    for reason in REASONS
}
_IS_BLOCKING: Final = {"blocking": True, "review_required": False}
_REASON_STATUS: Final[dict[str | None, str | None]] = {
    None: None,
    **{code: "failed" for code in FAILED_CODES},
    **{code: "blocked" for code in BLOCKED_CODES},
}
_CROSS_ROLE: Final = {(a, b): "cross_role" for a in ROLES for b in ROLES if a != b}
# (current role, prior role) -> reason; prior development / final against current train or
# validation, and prior final against current final. Anything else is not checked.
_PRIOR_PAIR: Final = {
    (current, prior): (
        "consumed_final_reuse" if current == prior == "final_qualification" else "prior_campaign"
    )
    for current in ROLES
    for prior in PRIOR_ROLES
    if current in ("train", "validation") or current == prior == "final_qualification"
}


class LeakageEngineError(ValueError):
    """Malformed input or an untrustworthy report. Never raised for a normal scan."""


class LeakageBlockedError(Exception):
    """The report does not allow `stage` to proceed. Names the stage, the reason code and the
    ids of the findings that are still open; never any content."""

    def __init__(self, stage: str, finding_ids: tuple[str, ...], reason_code: str | None) -> None:
        super().__init__(f"{stage} blocked by split leakage ({len(finding_ids)} open findings)")
        self.stage = stage
        self.finding_ids = finding_ids
        self.reason_code = reason_code


def _text(value: object, name: str, limit: int | None = None) -> str:
    # Exact type, not isinstance: a str subclass with a foreign hash / eq must never become an
    # id or a key (the same rule FTF-0517 applies).
    if type(value) is not str or not value or value.isspace():
        raise LeakageEngineError(f"{name} must be a non-empty string")
    if limit is not None and len(value) > limit:
        raise LeakageEngineError(f"{name} must be at most {limit} characters")
    return value


def _member(value: object, allowed: tuple[str, ...], name: str) -> str:
    # Type before membership, so an unhashable value raises this module's error.
    if type(value) is not str or value not in allowed:
        raise LeakageEngineError(f"{name} must be one of {allowed}")
    return value


def _sha(value: object, name: str) -> str:
    if type(value) is not str or not _SHA256.fullmatch(value):
        raise LeakageEngineError(f"{name} must be 64 lowercase hex characters")
    return value


def _int(value: object, name: str, low: int, high: int) -> int:
    if type(value) is not int or not low <= value <= high:
        raise LeakageEngineError(f"{name} must be an int in {low}..{high}")
    return value


# ---------------------------------------------------------------------------
# Inputs
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LeakageRecord:
    """One record: its FTF-0517 id, its FTF-0506 canonical `content`, the FTF-0506 lineage
    `raw_sha256` as `record_sha256` and `canonical_hash` as `normalized_sha256`, and any extra
    linkage keys. A malformed hash is a per-record issue at scan time, not an error here."""

    record_id: str
    content: Mapping[str, object]
    record_sha256: str
    normalized_sha256: str
    group_keys: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _text(self.record_id, "record_id")
        if not isinstance(self.content, Mapping):
            raise LeakageEngineError("content must be a mapping")
        for name in ("record_sha256", "normalized_sha256"):
            if type(getattr(self, name)) is not str:
                raise LeakageEngineError(f"{name} must be a string")
        if type(self.group_keys) is not tuple:
            raise LeakageEngineError("group_keys must be a tuple")
        for key in self.group_keys:
            _text(key, "group key")


@dataclass(frozen=True)
class PriorQualificationSource:
    """An earlier campaign's development or final suite."""

    label: str
    role: str
    manifest_sha256: str
    records: tuple[LeakageRecord, ...]

    def __post_init__(self) -> None:
        _text(self.label, "label", _MAX_REVIEWER)
        _member(self.role, PRIOR_ROLES, "role")
        _sha(self.manifest_sha256, "manifest_sha256")
        if type(self.records) is not tuple or not self.records:
            raise LeakageEngineError("records must be a non-empty tuple")
        ids: set[str] = set()
        for item in self.records:
            if not isinstance(item, LeakageRecord):
                raise LeakageEngineError("records must contain LeakageRecord values")
            if item.record_id in ids:
                raise LeakageEngineError("duplicate record_id in a prior source")
            ids.add(item.record_id)


@dataclass(frozen=True)
class EmbeddingRoute:
    """Where the semantic stage would compute embeddings. Checked by FTF-0901 `enforce_effect`
    on the `data` surface before any semantic work."""

    destination: Destination
    organization_profile: str
    project_profile: str
    job_profile: str | None = None
    authorization: RouteAuthorization | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.destination, Destination):
            raise LeakageEngineError("destination must be a Destination")
        if self.authorization is not None and not isinstance(
            self.authorization, RouteAuthorization
        ):
            raise LeakageEngineError("authorization must be a RouteAuthorization")


# ---------------------------------------------------------------------------
# Outputs
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LeakageFinding:
    finding_id: str
    method: str
    reason: str
    disposition: str
    record_id_a: str
    role_a: str
    record_id_b: str
    role_b: str
    prior_source_label: str | None
    scores: tuple[tuple[str, float], ...]

    def __post_init__(self) -> None:
        _sha(self.finding_id, "finding_id")
        _member(self.method, METHODS, "method")
        _member(self.reason, REASONS, "reason")
        _member(self.disposition, DISPOSITIONS, "disposition")
        _text(self.record_id_a, "record_id_a")
        _text(self.record_id_b, "record_id_b")
        _member(self.role_a, ROLES, "role_a")
        _member(self.role_b, ROLES, "role_b")
        if self.prior_source_label is not None:
            _text(self.prior_source_label, "prior_source_label")
        if type(self.scores) is not tuple:
            raise LeakageEngineError("scores must be a tuple")
        for entry in self.scores:
            if (
                type(entry) is not tuple
                or len(entry) != 2
                or type(entry[0]) is not str
                or type(entry[1]) is not float
            ):
                raise LeakageEngineError("scores must be (name, float) pairs")


@dataclass(frozen=True)
class LeakageIssue:
    """A record that could not be scanned. A code and the caller's own ids, nothing else."""

    code: str
    record_id: str | None
    source_label: str | None

    def __post_init__(self) -> None:
        _member(self.code, ISSUE_CODES, "code")
        for name in ("record_id", "source_label"):
            value = getattr(self, name)
            if value is not None:
                _text(value, name)


@dataclass(frozen=True)
class LeakageReview:
    """A human decision that one `review_required` finding is not leakage. Validated when it is
    applied, so a bad one is a refusal there rather than a crash while it is being built."""

    finding_id: str
    reviewer_id: str
    decision: str
    rationale: str


@dataclass(frozen=True)
class LeakageReport:
    status: str
    reason_code: str | None
    findings: tuple[LeakageFinding, ...]
    issues: tuple[LeakageIssue, ...]
    methods_run: tuple[str, ...]
    methods_not_run: tuple[str, ...]
    reviews: tuple[LeakageReview, ...] = ()
    report_sha256: str = field(default="")

    def __post_init__(self) -> None:
        _member(self.status, STATUSES, "status")
        if self.reason_code is not None:
            _member(self.reason_code, (*FAILED_CODES, *BLOCKED_CODES), "reason_code")
        for name, items, kind in (
            ("findings", self.findings, LeakageFinding),
            ("issues", self.issues, LeakageIssue),
            ("reviews", self.reviews, LeakageReview),
        ):
            if type(items) is not tuple or not all(isinstance(i, kind) for i in items):
                raise LeakageEngineError(f"{name} must be a tuple of {kind.__name__}")
        for name in ("methods_run", "methods_not_run"):
            names = getattr(self, name)
            if type(names) is not tuple:
                raise LeakageEngineError(f"{name} must be a tuple")
            for method in names:
                _member(method, METHODS, name)
        if type(self.report_sha256) is not str:
            raise LeakageEngineError("report_sha256 must be a string")

    @property
    def unreviewed_findings(self) -> tuple[LeakageFinding, ...]:
        reviewed = {review.finding_id: review for review in self.reviews}
        return tuple(
            f
            for f in self.findings
            if _IS_BLOCKING[f.disposition] or reviewed.get(f.finding_id) is None
        )


def report_body(report: LeakageReport) -> dict[str, object]:
    """Every field except `report_sha256`, as canonical-JSON-ready data."""
    return {
        "status": report.status,
        "reason_code": report.reason_code,
        "findings": [
            {
                "finding_id": f.finding_id,
                "method": f.method,
                "reason": f.reason,
                "disposition": f.disposition,
                "record_id_a": f.record_id_a,
                "role_a": f.role_a,
                "record_id_b": f.record_id_b,
                "role_b": f.role_b,
                "prior_source_label": f.prior_source_label,
                "scores": [[name, value] for name, value in f.scores],
            }
            for f in report.findings
        ],
        "issues": [
            {"code": i.code, "record_id": i.record_id, "source_label": i.source_label}
            for i in report.issues
        ],
        "methods_run": list(report.methods_run),
        "methods_not_run": list(report.methods_not_run),
        "reviews": [
            {
                "finding_id": r.finding_id,
                "reviewer_id": r.reviewer_id,
                "decision": r.decision,
                "rationale": r.rationale,
            }
            for r in report.reviews
        ],
    }


def compute_report_sha256(report: LeakageReport) -> str:
    text = json.dumps(report_body(report), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _sealed(report: LeakageReport) -> LeakageReport:
    return replace(report, report_sha256=compute_report_sha256(report))


def _finding_id(
    method: str,
    reason: str,
    a: tuple[str, str],
    b: tuple[str, str],
    label: str | None,
) -> str:
    text = json.dumps(
        {"m": method, "r": reason, "a": list(a), "b": list(b), "l": label},
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _derived_status(report: LeakageReport) -> str:
    return _REASON_STATUS[report.reason_code] or ("leakage_found" if report.findings else "clear")


def _canonical_report(report: LeakageReport) -> LeakageReport:
    """`report` as this module would have sealed it: issues imply `invalid_input`, and the status
    follows from the reason and the findings. Its hash is what `report_sha256` must be."""
    interim = replace(report, reason_code="invalid_input" if report.issues else report.reason_code)
    return replace(interim, status=_derived_status(interim))


def _canonical_finding(finding: LeakageFinding) -> LeakageFinding:
    return replace(
        finding,
        finding_id=_finding_id(
            finding.method,
            finding.reason,
            (finding.record_id_a, finding.role_a),
            (finding.record_id_b, finding.role_b),
            finding.prior_source_label,
        ),
        disposition=_DISPOSITION[(finding.method, finding.reason)],
    )


def _check_review(review: object) -> LeakageReview:
    if not isinstance(review, LeakageReview):
        raise LeakageEngineError("reviews must contain LeakageReview values")
    _sha(review.finding_id, "finding_id")
    _text(review.reviewer_id, "reviewer_id", _MAX_REVIEWER)
    _member(review.decision, ("not_leakage",), "decision")
    _text(review.rationale, "rationale", _MAX_RATIONALE)
    return review


def verify_report(report: LeakageReport) -> None:
    """Raises `LeakageEngineError` unless `report` is exactly what this module sealed: its hash
    covers every field and agrees with the status its reason and findings imply, every finding
    id and disposition follow from the finding itself, and every review binds a distinct
    `review_required` finding."""
    if not isinstance(report, LeakageReport):
        raise LeakageEngineError("report must be a LeakageReport")
    stored = report.report_sha256.encode()
    # The hash must cover the report as it stands AND agree with the status and reason it implies.
    for candidate in (report, _canonical_report(report)):
        if not hmac.compare_digest(stored, compute_report_sha256(candidate).encode()):
            raise LeakageEngineError("report hash does not match its content")
    # `reason_code is None` is the only case `_derived_status` can resolve to `clear` or
    # `leakage_found` (every FAILED_CODES/BLOCKED_CODES member maps to `failed`/`blocked`), and
    # `detect_split_leakage` only reaches that case by running every method. A self-sealed report
    # is internally consistent by construction (its own hash covers whatever it claims), so an
    # unkeyed hash alone cannot tell a real scan from one that fabricates `clear` while disclosing
    # it skipped the semantic methods -- this cross-check is the only thing that can.
    if report.reason_code is None and (
        report.methods_run != METHODS or report.methods_not_run != ()
    ):
        raise LeakageEngineError(
            "a clear or leakage_found report must have every method run and none skipped"
        )
    for finding in report.findings:
        if finding != _canonical_finding(finding):
            raise LeakageEngineError("a finding id or disposition does not follow from its fields")
    by_id = {finding.finding_id: finding for finding in report.findings}
    if len(by_id) != len(report.findings):
        raise LeakageEngineError("a finding id is repeated")
    for review in report.reviews:
        _check_review(review)
        target = by_id.get(review.finding_id)
        if target is None or _IS_BLOCKING[target.disposition]:
            raise LeakageEngineError("a review does not bind a review_required finding")
    if len({review.finding_id for review in report.reviews}) != len(report.reviews):
        raise LeakageEngineError("a finding is reviewed twice")


def apply_leakage_reviews(report: LeakageReport, reviews: Sequence[LeakageReview]) -> LeakageReport:
    """A new report with `reviews` bound to its `review_required` findings. An unknown finding,
    a second review of one finding, a review of a `blocking` finding or a blank reviewer /
    rationale raises `LeakageEngineError`; the original report is not touched."""
    verify_report(report)
    if not isinstance(reviews, tuple | list):
        raise LeakageEngineError("reviews must be a tuple or list")
    by_id = {f.finding_id: f for f in report.findings}
    reviewed = {review.finding_id: True for review in report.reviews}
    added: list[LeakageReview] = []
    for review in reviews:
        _check_review(review)
        target = by_id.get(review.finding_id)
        if target is None:
            raise LeakageEngineError("a review names an unknown finding")
        if _IS_BLOCKING[target.disposition]:
            raise LeakageEngineError("a blocking finding cannot be reviewed away")
        if reviewed.get(review.finding_id, False):
            raise LeakageEngineError("a finding can be reviewed only once")
        reviewed[review.finding_id] = True
        added.append(review)
    merged = tuple(sorted((*report.reviews, *added), key=lambda r: r.finding_id))
    return _sealed(replace(report, reviews=merged))


def assert_leakage_clear(report: LeakageReport, stage: str) -> None:
    """Raises `LeakageBlockedError` unless `stage` may proceed. `blocked` and `failed` reports
    never clear; a `leakage_found` report clears only when every finding is `review_required`
    and reviewed `not_leakage`. A report this module did not seal raises `LeakageEngineError`."""
    _member(stage, STAGES, "stage")
    verify_report(report)
    open_ids = tuple(sorted(f.finding_id for f in report.unreviewed_findings))
    if report.reason_code is not None or open_ids:
        raise LeakageBlockedError(stage, open_ids, report.reason_code)


# ---------------------------------------------------------------------------
# The scan
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _Entry:
    position: int
    record_id: str
    role: str
    label: str | None
    record: LeakageRecord
    keys: frozenset[str]


def _pair_reason(first: _Entry, second: _Entry) -> str | None:
    """The pair-class reason for two entries already in `_order`: current records come before
    prior ones, so a prior `first` means two prior records, which are never compared."""
    if first.label is not None:
        return None
    if second.label is None:
        return _CROSS_ROLE.get((first.role, second.role))
    return _PRIOR_PAIR.get((first.role, second.role))


def _order(entry: _Entry) -> tuple[bool, int]:
    """Current records before prior ones, then by position."""
    return entry.label is not None, entry.position


class _Collector:
    """Findings by id, in a deterministic generation order, up to `limit`."""

    def __init__(self, limit: int) -> None:
        self.limit = limit
        self.items: dict[str, LeakageFinding] = {}
        self.full = False

    def add(
        self,
        method: str,
        a: _Entry,
        b: _Entry,
        scores: tuple[tuple[str, float], ...] = (),
    ) -> bool:
        """Records one finding for the pair `(a, b)` if that pair class is checked. Returns
        False once the limit is hit."""
        first, second = sorted((a, b), key=_order)
        reason = _pair_reason(first, second)
        if reason is None:
            return True
        label = first.label or second.label
        finding_id = _finding_id(
            method,
            reason,
            (first.record_id, first.role),
            (second.record_id, second.role),
            label,
        )
        if self.items.get(finding_id) is not None:
            return True
        if len(self.items) >= self.limit:
            self.full = True
            return False
        self.items[finding_id] = LeakageFinding(
            finding_id=finding_id,
            method=method,
            reason=reason,
            disposition=_DISPOSITION[(method, reason)],
            record_id_a=first.record_id,
            role_a=first.role,
            record_id_b=second.record_id,
            role_b=second.role,
            prior_source_label=label,
            scores=scores,
        )
        return True


def _bucket_pairs(members: Sequence[_Entry]) -> Sequence[tuple[_Entry, _Entry]]:
    """Pairs of `members` that belong to different pair-classes (source and role), without ever
    walking pairs that cannot be findings: a huge same-role bucket costs one pass, not a quadratic
    one. `members` arrive in position order, so the classes and the pairs do too."""
    classes: dict[tuple[str | None, str], list[_Entry]] = {}
    for entry in members:
        classes.setdefault((entry.label, entry.role), []).append(entry)
    return [
        pair
        for left, right in itertools.combinations(classes.values(), 2)
        for pair in itertools.product(left, right)
    ]


def _failed(reason_code: str, issues: tuple[LeakageIssue, ...] = ()) -> LeakageReport:
    return _sealed(
        LeakageReport(
            status="failed",
            reason_code=reason_code,
            findings=(),
            issues=issues,
            methods_run=(),
            methods_not_run=METHODS,
        )
    )


def _validate_arguments(
    records: Sequence[LeakageRecord],
    prior_sources: Sequence[PriorQualificationSource],
    route: object,
    thresholds: object,
    max_semantic_partners: object,
    max_findings: object,
) -> None:
    if not isinstance(route, EmbeddingRoute):
        raise LeakageEngineError("route must be an EmbeddingRoute")
    if (
        type(records) not in (list, tuple)
        or not records
        or not all(isinstance(r, LeakageRecord) for r in records)
    ):
        raise LeakageEngineError("records must be a non-empty list or tuple of LeakageRecord")
    if type(prior_sources) not in (list, tuple) or not all(
        isinstance(s, PriorQualificationSource) for s in prior_sources
    ):
        raise LeakageEngineError(
            "prior_sources must be a list or tuple of PriorQualificationSource"
        )
    labels = [s.label for s in prior_sources]
    if len(set(labels)) != len(labels):
        raise LeakageEngineError("prior source labels must be unique")
    if not isinstance(thresholds, NearDuplicateThresholds):
        raise LeakageEngineError("thresholds must be NearDuplicateThresholds")
    _int(max_semantic_partners, "max_semantic_partners", 1, 1_000_000)
    _int(max_findings, "max_findings", 1, _MAX_FINDINGS_CEILING)


def _route_code(route: EmbeddingRoute) -> str | None:
    """None when the semantic stage may run; otherwise the coded reason it may not."""
    request = EffectRequest(
        surface="data",
        destination=route.destination,
        organization_profile=route.organization_profile,
        project_profile=route.project_profile,
        job_profile=route.job_profile,
    )
    try:
        enforce_effect(request, route.authorization)
    except SecurityProfileEnforcementError as exc:
        return exc.code
    if route.destination.provider is not None:  # only an internal destination has no provider
        return "embedding_provider_unavailable"
    return None


def _issues_from(
    detector_issues: Sequence[Any], entries: Sequence[_Entry], into: set[LeakageIssue]
) -> None:
    for issue in detector_issues:
        entry = entries[issue.batch_position]
        into.add(
            LeakageIssue(
                code=_ISSUE_BY_DETECTOR_CODE.get(issue.code, "invalid_record"),
                record_id=entry.record_id,
                source_label=entry.label,
            )
        )


def detect_split_leakage(
    manifest: SplitManifest,
    records: Sequence[LeakageRecord],
    *,
    prior_sources: Sequence[PriorQualificationSource] = (),
    route: EmbeddingRoute,
    thresholds: NearDuplicateThresholds = DEFAULT_THRESHOLDS,
    max_semantic_partners: int = DEFAULT_MAX_SEMANTIC_PARTNERS,
    max_findings: int = DEFAULT_MAX_FINDINGS,
) -> LeakageReport:
    """Scans a FTF-0517 split for cross-role and previous-campaign leakage. Never raises for a
    tampered manifest, a record set that differs from it or an unscannable record: those are
    `failed` reports. Raises `LeakageEngineError` only for malformed arguments."""
    _validate_arguments(
        records, prior_sources, route, thresholds, max_semantic_partners, max_findings
    )
    try:
        verify_manifest(manifest)
    except SplitEngineError:
        return _failed("manifest_invalid")
    assigned = {rid: (role, group) for rid, role, group in manifest.assignments}
    ids = [r.record_id for r in records]
    if len(set(ids)) != len(ids) or set(ids) != set(assigned):
        return _failed("record_set_mismatch")

    entries: list[_Entry] = []
    for item in sorted(records, key=lambda r: (ROLES.index(assigned[r.record_id][0]), r.record_id)):
        role, group = assigned[item.record_id]
        entries.append(
            _Entry(
                len(entries),
                item.record_id,
                role,
                None,
                item,
                frozenset({group, *item.group_keys}),
            )
        )
    for source in sorted(prior_sources, key=lambda s: s.label):
        for item in sorted(source.records, key=lambda r: r.record_id):
            entries.append(
                _Entry(
                    len(entries),
                    item.record_id,
                    source.role,
                    source.label,
                    item,
                    frozenset(item.group_keys),
                )
            )
    batch = [
        {
            "record_index": entry.position,
            "record_sha256": entry.record.record_sha256,
            "normalized_sha256": entry.record.normalized_sha256,
            "content": entry.record.content,
            "purpose": _PURPOSE_BY_ROLE[entry.role],
        }
        for entry in entries
    ]
    collector = _Collector(max_findings)
    issues: set[LeakageIssue] = set()

    exact = detect_exact_duplicates(batch)
    _issues_from(exact.issues, entries, issues)
    normalized = detect_normalized_duplicates(batch)
    _issues_from(normalized.issues, entries, issues)
    hash_groups = (
        ("exact_raw", [g for g in exact.raw_duplicate_groups]),
        ("canonical_normalized", [g for g in exact.normalized_duplicate_groups]),
        ("text_normalized", list(normalized.duplicate_groups)),
    )
    for method, groups in hash_groups:
        for duplicate_group in groups:
            members = [entries[p] for p in duplicate_group.batch_positions]
            for a, b in _bucket_pairs(members):
                if not collector.add(method, a, b):
                    break

    by_key: dict[str, list[_Entry]] = {}
    for entry in entries:
        for key in entry.keys:
            by_key.setdefault(key, []).append(entry)
    for key in sorted(by_key):
        for a, b in _bucket_pairs(by_key[key]):
            if not collector.add("group", a, b):
                break

    route_code = _route_code(route)
    saturated = False
    if route_code is None:
        near = detect_near_duplicates(
            batch,
            thresholds=NearDuplicateThresholds(0.0, 0.0, 0.0),
            max_candidates_per_record=max_semantic_partners,
        )
        _issues_from(near.issues, entries, issues)
        partners: dict[int, int] = {}
        for candidate in near.candidates:
            for position in (candidate.batch_position_a, candidate.batch_position_b):
                partners[position] = partners.get(position, 0) + 1
        saturated = any(count >= max_semantic_partners for count in partners.values())
        for candidate in sorted(
            near.candidates, key=lambda c: (c.batch_position_a, c.batch_position_b)
        ):
            a, b = entries[candidate.batch_position_a], entries[candidate.batch_position_b]
            scores = tuple(
                sorted(
                    (
                        ("char_ngram_similarity", float(candidate.character_ngram_similarity)),
                        (
                            "embedding_cosine_similarity",
                            float(candidate.embedding_cosine_similarity),
                        ),
                        ("structural_match", 1.0 if candidate.structural_match else 0.0),
                        ("token_jaccard_similarity", float(candidate.token_jaccard_similarity)),
                    )
                )
            )
            for method, score_name, threshold_name in _SEMANTIC_THRESHOLD_FIELDS:
                if dict(scores)[score_name] >= getattr(thresholds, threshold_name):
                    collector.add(method, a, b, scores)
            if candidate.structural_match:
                collector.add("agent_trace_structural", a, b, scores)

    findings = tuple(sorted(collector.items.values(), key=lambda f: f.finding_id))
    issue_list = tuple(sorted(issues, key=repr))
    semantic_ran = route_code is None and not saturated
    reason: str | None = None
    if issue_list:
        reason = "invalid_input"
    elif route_code is not None:
        reason = route_code
    elif saturated:
        reason = "semantic_scan_saturated"
    elif collector.full:
        reason = "finding_limit_exceeded"
    run = (*HASH_METHODS, *(SEMANTIC_METHODS if semantic_ran else ()))
    not_run = () if semantic_ran else SEMANTIC_METHODS
    report = LeakageReport(
        status="clear",
        reason_code=reason,
        findings=findings,
        issues=issue_list,
        methods_run=run,
        methods_not_run=not_run,
    )
    return _sealed(replace(report, status=_derived_status(report)))
