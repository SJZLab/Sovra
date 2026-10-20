"""Group-aware split engine (FTF-0517, Sections 9, 10, 67, 77, 81).

Acceptance ("Allocate train/validation/development/sealed-final by source
groups and immutable seed/strategy"): `allocate_splits(spec, records)` assigns
every record to one of the four Section 9 roles (`train`, `validation`,
`development_eval`, `final_qualification`) and returns an immutable,
hash-bound `SplitManifest` (strategy, seed, ratios, cutoff, every record's
role and group, per-role counts, SHA-256).

  - a record carries one or more linkage keys (conversation id, template or
    synthetic-seed family, customer case id, near-duplicate group...). Records
    that share ANY key, directly or through a chain, are one group
    (union-find), and a group is never split across roles;
  - the strategy `sha256_group_hash_v1` places each group with
    SHA-256(strategy NUL seed NUL smallest-group-key) mod the role weights.
    A group's role therefore depends only on the strategy, the seed and its
    own keys: never on other groups, record order, record counts or any
    evaluation outcome;
  - the default 70/10/10/10 proposal is a spec the caller states, not a
    default here; train and final_qualification must each be at least 1;
  - temporal tasks: with a `cutoff`, every record needs an `event_time`; a
    group with any record at or after the cutoff is placed ONLY in
    development_eval or final_qualification, every earlier group only in
    train or validation (prospective split, grouping still applies);
  - a record preassigned to an evaluation role pins its whole group to that
    role and can never be moved into train or validation; a group that mixes
    different preassigned roles, or a preassignment that breaks the cutoff,
    is refused.

Negative acceptance ("Same conversation/template/customer case family cannot
cross split boundaries; no final-driven reshuffling"): `verify_manifest`
recomputes the hash and independently re-checks that no group spans two roles
and that the counts match, so a tampered or hand-edited manifest is refused.
The engine accepts no outcome, score or metric input at all, so a final result
cannot drive a reshuffle; changing the seed or strategy is a new manifest, not
an edit. A role with no records makes the manifest `insufficient` (never
silently "ready"); sample-size adequacy is Section 69's job, not claimed here.

Caller contract: the caller must call `quality_issue_engine.assert_clear_for_split` before
`allocate_splits`; this engine does not call it, and the wiring task enforces the order.

Scope (disclosed): near-duplicate resolution happens before this engine and
reaches it as linkage keys; no augmentation, tokenization or token-packed
batches are handled here. Malformed input raises `SplitEngineError`. No live
caller yet (the dataset import runtime is not dependency-ready), so
`proving_symbol` stays blank, same precedent as the sibling modules. No schema
change, no new dependency.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from dataclasses import dataclass

__all__ = [
    "ROLES",
    "STRATEGY",
    "SplitEngineError",
    "SplitManifest",
    "SplitRecord",
    "SplitSpec",
    "allocate_splits",
    "compute_manifest_sha256",
    "verify_manifest",
]

STRATEGY = "sha256_group_hash_v1"
_SUPPORTED_STRATEGIES = (STRATEGY,)
ROLES = ("train", "validation", "development_eval", "final_qualification")
_EVAL_ROLES = ROLES[2:]
# role -> is an evaluation role (never used for fitting).
_IS_EVAL = {
    "train": False,
    "validation": False,
    "development_eval": True,
    "final_qualification": True,
}
_MAX_SEED = 128
_MAX_TIME = 10**13


class SplitEngineError(ValueError):
    """Malformed spec, record or manifest. Never raised for a normal split."""


def _text(value: object, name: str) -> str:
    # Exact type, not isinstance: a str subclass with the same text but foreign hash/eq
    # (or just a stray subclass) must never pass as a group key or record id -- the
    # union-find below keys a dict by this value, so a foreign hash/eq would silently
    # fail to merge with an identical-looking plain str and split one family across
    # two groups (and therefore two roles) instead of raising.
    if type(value) is not str or not value or value.isspace():
        raise SplitEngineError(f"{name} must be a non-empty string")
    return value


def _int(value: object, name: str, low: int, high: int) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or not low <= value <= high:
        raise SplitEngineError(f"{name} must be an int in {low}..{high}")
    return value


@dataclass(frozen=True)
class SplitSpec:
    strategy: str
    seed: str
    ratios: tuple[int, int, int, int]
    cutoff: int | None = None

    def __post_init__(self) -> None:
        if self.strategy not in _SUPPORTED_STRATEGIES:
            raise SplitEngineError(f"strategy must be {STRATEGY!r}")
        _text(self.seed, "seed")
        if len(self.seed) > _MAX_SEED:
            raise SplitEngineError(f"seed must be at most {_MAX_SEED} characters")
        if not isinstance(self.ratios, tuple) or len(self.ratios) != len(ROLES):
            raise SplitEngineError(
                "ratios must be a tuple of four ints (train, validation, dev, final)"
            )
        for role, ratio in zip(ROLES, self.ratios, strict=True):
            _int(ratio, f"ratio for {role}", 0, 100)
        if sum(self.ratios) != 100:
            raise SplitEngineError("ratios must sum to 100")
        if self.ratios[0] < 1 or self.ratios[3] < 1:
            raise SplitEngineError("train and final_qualification ratios must each be at least 1")
        if self.cutoff is not None:
            _int(self.cutoff, "cutoff", 0, _MAX_TIME)


@dataclass(frozen=True)
class SplitRecord:
    record_id: str
    group_keys: tuple[str, ...]
    preassigned_role: str | None = None
    event_time: int | None = None


@dataclass(frozen=True)
class SplitManifest:
    strategy: str
    seed: str
    ratios: tuple[int, int, int, int]
    cutoff: int | None
    # (record_id, role, group_id), sorted by record_id; group_id is the group's smallest key.
    assignments: tuple[tuple[str, str, str], ...]
    # (role, record_count, group_count) in ROLES order.
    counts: tuple[tuple[str, int, int], ...]
    status: str
    manifest_sha256: str


def compute_manifest_sha256(manifest: SplitManifest) -> str:
    body = {
        "strategy": manifest.strategy,
        "seed": manifest.seed,
        "ratios": list(manifest.ratios),
        "cutoff": manifest.cutoff,
        "assignments": [list(a) for a in manifest.assignments],
    }
    return hashlib.sha256(
        json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _bucket(spec: SplitSpec, group_id: str, weights: int) -> int:
    digest = hashlib.sha256(f"{spec.strategy}\0{spec.seed}\0{group_id}".encode()).digest()
    return int.from_bytes(digest, "big") % weights


def _pick(spec: SplitSpec, group_id: str, roles: tuple[str, ...]) -> str:
    # One slot per unit of weight, so the digest picks a slot and no boundary comparison exists.
    slots = [role for role in roles for _ in range(spec.ratios[ROLES.index(role)])]
    return slots[_bucket(spec, group_id, len(slots))]


def _find(parent: dict[str, str], key: str) -> str:
    while parent[key] != key:
        parent[key] = parent[parent[key]]
        key = parent[key]
    return key


def _status(counts: tuple[tuple[str, int, int], ...]) -> str:
    return "insufficient" if any(count == 0 for _, count, _ in counts) else "ready"


def _counts(
    roles_by_record: dict[str, str], group_by_record: dict[str, str]
) -> tuple[tuple[str, int, int], ...]:
    tally: dict[str, tuple[list[str], set[str]]] = {role: ([], set()) for role in ROLES}
    for record_id, role in roles_by_record.items():
        members, groups = tally[role]
        members.append(record_id)
        groups.add(group_by_record[record_id])
    return tuple((role, len(tally[role][0]), len(tally[role][1])) for role in ROLES)


def allocate_splits(spec: SplitSpec, records: Sequence[SplitRecord]) -> SplitManifest:
    if not isinstance(spec, SplitSpec):
        raise SplitEngineError("spec must be a SplitSpec")
    if isinstance(records, str | bytes) or not isinstance(records, list | tuple) or not records:
        raise SplitEngineError("records must be a non-empty list or tuple")
    seen_ids: set[str] = set()
    parent: dict[str, str] = {}
    for rec in records:
        if not isinstance(rec, SplitRecord):
            raise SplitEngineError("records must contain SplitRecord values")
        _text(rec.record_id, "record_id")
        if rec.record_id in seen_ids:
            raise SplitEngineError("duplicate record_id")
        seen_ids.add(rec.record_id)
        if not isinstance(rec.group_keys, tuple) or not rec.group_keys:
            raise SplitEngineError("group_keys must be a non-empty tuple")
        for key in rec.group_keys:
            _text(key, "group key")
            parent.setdefault(key, key)
        if rec.preassigned_role is not None and (
            # Type-checked before the membership test: an unhashable preassigned_role
            # ([] or {}) must raise this module's own error, never a raw TypeError.
            type(rec.preassigned_role) is not str or rec.preassigned_role not in _IS_EVAL
        ):
            raise SplitEngineError(f"preassigned_role must be None or one of {ROLES}")
        if rec.event_time is not None:
            _int(rec.event_time, "event_time", 0, _MAX_TIME)
        if spec.cutoff is not None and rec.event_time is None:
            raise SplitEngineError("every record needs an event_time when a cutoff is set")
    for rec in records:
        first = _find(parent, rec.group_keys[0])
        for key in rec.group_keys[1:]:
            # Re-pointing a root at itself is a no-op, so no "already joined" test is needed.
            parent[_find(parent, key)] = first
    members: dict[str, list[SplitRecord]] = {}
    for rec in records:
        members.setdefault(_find(parent, rec.group_keys[0]), []).append(rec)
    group_by_record: dict[str, str] = {}
    role_by_group: dict[str, str] = {}
    for recs in members.values():
        keys = {key for rec in recs for key in rec.group_keys}
        group_id = min(keys)
        pinned = {rec.preassigned_role for rec in recs if rec.preassigned_role is not None}
        if len(pinned) > 1:
            raise SplitEngineError("a group has records preassigned to different roles")
        post_cutoff = spec.cutoff is not None and any(
            rec.event_time is not None and rec.event_time >= spec.cutoff for rec in recs
        )
        if pinned:
            (role,) = pinned
            if post_cutoff and not _IS_EVAL[role]:
                raise SplitEngineError(
                    "a post-cutoff group cannot be preassigned to train or validation"
                )
        elif spec.cutoff is None:
            role = _pick(spec, group_id, ROLES)
        elif post_cutoff:
            role = _pick(spec, group_id, _EVAL_ROLES)
        else:
            role = _pick(spec, group_id, ROLES[:2])
        role_by_group[group_id] = role
        for rec in recs:
            group_by_record[rec.record_id] = group_id
    role_by_record = {rid: role_by_group[gid] for rid, gid in group_by_record.items()}
    counts = _counts(role_by_record, group_by_record)
    assignments = tuple(
        (rid, role_by_record[rid], group_by_record[rid]) for rid in sorted(role_by_record)
    )
    manifest = SplitManifest(
        strategy=spec.strategy,
        seed=spec.seed,
        ratios=spec.ratios,
        cutoff=spec.cutoff,
        assignments=assignments,
        counts=counts,
        status=_status(counts),
        manifest_sha256="",
    )
    return SplitManifest(
        **{**manifest.__dict__, "manifest_sha256": compute_manifest_sha256(manifest)}
    )


def _check_manifest_shape(manifest: SplitManifest) -> None:
    # Every field that compute_manifest_sha256 serialises is type-checked first, so a
    # malformed manifest raises this module's own error and never a raw TypeError out of
    # json.dumps (a set/bytes field) or iteration (None/int assignments).
    if type(manifest.strategy) is not str or type(manifest.seed) is not str:
        raise SplitEngineError("manifest strategy and seed must be strings")
    ratios = manifest.ratios
    if type(ratios) is not tuple or len(ratios) != len(ROLES):
        raise SplitEngineError("manifest ratios must be a tuple of four ints")
    for ratio in ratios:
        if type(ratio) is not int:
            raise SplitEngineError("manifest ratios must be a tuple of four ints")
    if manifest.cutoff is not None and type(manifest.cutoff) is not int:
        raise SplitEngineError("manifest cutoff must be None or an int")
    if type(manifest.assignments) is not tuple:
        raise SplitEngineError("manifest assignments must be a tuple")
    for entry in manifest.assignments:
        if type(entry) is not tuple or len(entry) != 3:
            raise SplitEngineError("manifest has a malformed assignment")
        if any(type(field) is not str for field in entry):
            raise SplitEngineError("manifest has a malformed assignment")


def verify_manifest(manifest: SplitManifest) -> None:
    """Raise SplitEngineError unless the manifest is untampered and group-isolated."""
    if not isinstance(manifest, SplitManifest):
        raise SplitEngineError("manifest must be a SplitManifest")
    _check_manifest_shape(manifest)
    if manifest.manifest_sha256 != compute_manifest_sha256(manifest):
        raise SplitEngineError("manifest hash does not match its content")
    roles_in_group: dict[str, set[str]] = {}
    role_by_record: dict[str, str] = {}
    group_by_record: dict[str, str] = {}
    for entry in manifest.assignments:
        # Entry structure and field types were validated by _check_manifest_shape.
        record_id, role, group_id = entry
        if role not in _IS_EVAL:
            raise SplitEngineError("manifest has an unknown role")
        roles_in_group.setdefault(group_id, set()).add(role)
        role_by_record[record_id] = role
        group_by_record[record_id] = group_id
    if any(len(roles) > 1 for roles in roles_in_group.values()):
        raise SplitEngineError("a group crosses a split boundary")
    if len(role_by_record) != len(manifest.assignments):
        raise SplitEngineError("manifest has a duplicate record_id")
    counts = _counts(role_by_record, group_by_record)
    if manifest.counts != counts:
        raise SplitEngineError("manifest counts do not match its assignments")
    if manifest.status != _status(counts):
        raise SplitEngineError("manifest status does not match its counts")
