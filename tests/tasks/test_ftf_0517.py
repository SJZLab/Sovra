#!/usr/bin/env python3
# mypy: disable-error-code="arg-type,call-arg,var-annotated,list-item,attr-defined"
# ruff: noqa: E501
"""FTF-0517 -- Group-aware split engine.

Runnable without any database or network: `allocate_splits` / `verify_manifest` are pure,
synchronous, no-I/O functions (no independent schema change).

Acceptance ("Allocate train/validation/development/sealed-final by source groups and
immutable seed/strategy"): test_four_roles_by_seed_and_strategy, test_deterministic_and_order_independent,
test_seed_changes_the_split, test_ratios_roughly_honoured, test_manifest_is_hash_bound.

Negative acceptance ("Same conversation/template/customer case family cannot cross split
boundaries; no final-driven reshuffling"): test_shared_key_groups_never_cross,
test_transitive_linkage_groups, test_group_role_independent_of_other_groups,
test_verify_rejects_crossing_group, test_verify_rejects_tampering, test_preassigned_eval_never_becomes_train,
test_no_outcome_input_exists.

GRADER-style checklist: malformed input fails closed, case/whitespace exact keys, duplicate ids
refused, empty input refused, multi-constraint (temporal) coverage.

FTF-0517 round 2 additions: pinned golden, PYTHONHASHSEED, group counts, cutoff binding.

FTF-0517 round 3 additions (returned: V3/V42 union-truncation faults GREEN because no test
record carried 3+ linkage keys; exact-type keys/roles/assignments): test_bridge_keys_beyond_the_
second_are_still_unioned, test_generated_key_level_property_no_key_crosses_roles,
test_keys_roles_and_seed_require_exact_str_type, test_verify_manifest_rejects_malformed_
assignment_entries.

Run directly: python3 tests/tasks/test_ftf_0517.py
"""

from __future__ import annotations

import inspect
import os
import random
import subprocess
import sys
from dataclasses import FrozenInstanceError, replace
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from workers.data.group_aware_split_engine import (  # noqa: E402
    ROLES,
    STRATEGY,
    SplitEngineError,
    SplitManifest,
    SplitRecord,
    SplitSpec,
    allocate_splits,
    compute_manifest_sha256,
    verify_manifest,
)

FAILURES: list[str] = []
CID = "FTF-0517"


def check(criterion_id: str, description: str, condition: bool) -> None:
    if not condition:
        FAILURES.append(f"FAIL [{criterion_id}] {description}")


def raises(fn: Any) -> bool:
    try:
        fn()
    except SplitEngineError:
        return True
    except Exception:  # noqa: BLE001 -- the wrong exception type is a failed check
        return False
    return False


def spec(**over: Any) -> SplitSpec:
    base: dict[str, Any] = {
        "strategy": STRATEGY,
        "seed": "seed-1",
        "ratios": (70, 10, 10, 10),
        "cutoff": None,
    }
    base.update(over)
    return SplitSpec(**base)


def rec(rid: str, *keys: str, **over: Any) -> SplitRecord:
    return SplitRecord(record_id=rid, group_keys=keys or (f"g-{rid}",), **over)


def many(n: int, prefix: str = "r") -> list[SplitRecord]:
    return [rec(f"{prefix}{i}", f"{prefix}g{i}") for i in range(n)]


def roles_of(manifest: SplitManifest) -> dict[str, str]:
    return {rid: role for rid, role, _ in manifest.assignments}


def test_four_roles_by_seed_and_strategy() -> None:
    m = allocate_splits(spec(), many(400))
    check(CID, "all four roles are used", {role for _, role, _ in m.assignments} == set(ROLES))
    check(
        CID, "manifest carries the strategy and seed", m.strategy == STRATEGY and m.seed == "seed-1"
    )
    check(
        CID,
        "manifest carries the ratios and cutoff",
        m.ratios == (70, 10, 10, 10) and m.cutoff is None,
    )
    check(CID, "counts sum to the records", sum(c for _, c, _ in m.counts) == 400)
    check(CID, "counts are in ROLES order", tuple(r for r, _, _ in m.counts) == ROLES)
    check(CID, "a full split is ready", m.status == "ready")
    check(CID, "an unknown strategy is refused", raises(lambda: spec(strategy="random")))
    check(CID, "a strategy with a space is refused", raises(lambda: spec(strategy=STRATEGY + " ")))


def test_deterministic_and_order_independent() -> None:
    records = many(200)
    a = allocate_splits(spec(), records)
    b = allocate_splits(spec(), list(reversed(records)))
    check(
        CID,
        "same input gives the same manifest hash",
        a.manifest_sha256 == allocate_splits(spec(), records).manifest_sha256,
    )
    check(CID, "record order does not change the manifest", a == b)
    t = allocate_splits(spec(), tuple(records))
    check(CID, "a tuple of records gives the same manifest", a == t)


def test_seed_changes_the_split() -> None:
    records = many(200)
    a = allocate_splits(spec(seed="s1"), records)
    b = allocate_splits(spec(seed="s2"), records)
    check(CID, "a different seed gives a different assignment", roles_of(a) != roles_of(b))
    check(CID, "a different seed gives a different hash", a.manifest_sha256 != b.manifest_sha256)
    check(
        CID,
        "seed is case-exact",
        roles_of(allocate_splits(spec(seed="S1"), records)) != roles_of(a),
    )
    check(
        CID,
        "seed is whitespace-exact",
        roles_of(allocate_splits(spec(seed="s1 "), records)) != roles_of(a),
    )


def test_ratios_roughly_honoured() -> None:
    m = allocate_splits(spec(), many(800))
    share = {role: count / 800 for role, count, _ in m.counts}
    check(CID, "train near 70%", 0.60 <= share["train"] <= 0.80)
    for role in ROLES[1:]:
        check(CID, f"{role} near 10%", 0.04 <= share[role] <= 0.16)
    half = allocate_splits(spec(ratios=(50, 0, 0, 50)), many(400))
    check(
        CID,
        "a zero-ratio role gets nothing",
        dict((r, c) for r, c, _ in half.counts)["validation"] == 0,
    )
    check(CID, "an empty role makes the manifest insufficient", half.status == "insufficient")
    check(
        CID, "train ratio of exactly 1 accepted", not raises(lambda: spec(ratios=(1, 49, 25, 25)))
    )
    check(
        CID, "final ratio of exactly 1 accepted", not raises(lambda: spec(ratios=(25, 25, 49, 1)))
    )
    skew = allocate_splits(spec(ratios=(1, 0, 0, 99)), many(1000))
    skew_counts = {r: c for r, c, _ in skew.counts}
    check(
        CID,
        "a 1/99 split puts a small minority in train",
        0 < skew_counts["train"] < 60 and skew_counts["final_qualification"] > 900,
    )
    for bad in (
        (70, 10, 10, 9),
        (0, 50, 25, 25),
        (50, 25, 25, 0),
        (-10, 60, 25, 25),
        (70.0, 10, 10, 10),
        (70, 10, 10),
        [70, 10, 10, 10],
        (True, 9, 45, 45),
    ):
        check(CID, f"ratios {bad!r} refused", raises(lambda b=bad: spec(ratios=b)))


def test_manifest_is_hash_bound() -> None:
    m = allocate_splits(spec(), many(50))
    check(
        CID,
        "hash is 64 hex chars",
        len(m.manifest_sha256) == 64 and set(m.manifest_sha256) <= set("0123456789abcdef"),
    )
    check(CID, "hash matches the content", m.manifest_sha256 == compute_manifest_sha256(m))
    other = allocate_splits(spec(cutoff=None, ratios=(60, 20, 10, 10)), many(50))
    check(CID, "different ratios give a different hash", other.manifest_sha256 != m.manifest_sha256)
    verify_manifest(m)
    check(CID, "manifest is frozen", raises_frozen(lambda: setattr(m, "seed", "x")))


def raises_frozen(fn: Any) -> bool:
    try:
        fn()
    except FrozenInstanceError:
        return True
    return False


def test_shared_key_groups_never_cross() -> None:
    records = []
    for i in range(300):
        records.append(rec(f"a{i}", f"conv-{i}", f"tmpl-{i % 30}"))
    m = allocate_splits(spec(), records)
    role = roles_of(m)
    for t in range(30):
        family = {role[f"a{i}"] for i in range(300) if i % 30 == t}
        check(CID, f"template {t} stays in one role", len(family) == 1)
    check(CID, "families merged into 30 groups", len({g for _, _, g in m.assignments}) == 30)
    cust = [
        rec("x1", "conv-1", "cust-9"),
        rec("x2", "conv-2", "cust-9"),
        rec("x3", "conv-3", "cust-9"),
    ]
    mc = roles_of(allocate_splits(spec(), cust))
    check(CID, "records sharing a customer case stay together", len(set(mc.values())) == 1)


def test_transitive_linkage_groups() -> None:
    chain = [rec("c1", "k1", "k2"), rec("c2", "k2", "k3"), rec("c3", "k3", "k4"), rec("c4", "k4")]
    m = allocate_splits(spec(), chain)
    check(CID, "a chain of shared keys is one group", len({g for _, _, g in m.assignments}) == 1)
    check(CID, "group id is the smallest key", {g for _, _, g in m.assignments} == {"k1"})
    shuffled = allocate_splits(spec(), list(reversed(chain)))
    check(CID, "chain order does not matter", m == shuffled)
    for first, second in (("k1", "K1 "), ("K1 ", "k1"), ("a", "A"), ("A", "a")):
        bridged = allocate_splits(
            spec(), [rec("b1", first), rec("b2", first, second), rec("b3", second)]
        )
        check(
            CID,
            f"{first!r} and {second!r} are linked only through the bridge",
            len({g for _, _, g in bridged.assignments}) == 1,
        )
        apart = allocate_splits(spec(), [rec("b1", first), rec("b3", second)])
        check(
            CID,
            f"{first!r} and {second!r} are different keys without a bridge",
            len({g for _, _, g in apart.assignments}) == 2,
        )
    wide = [rec("w1", "z", "a"), rec("w2", "m", "b"), rec("w3", "a", "b")]
    check(
        CID,
        "two groups joined by a bridge record become one",
        len({g for _, _, g in allocate_splits(spec(), wide).assignments}) == 1,
    )
    apart = allocate_splits(spec(), wide[:2])
    check(
        CID,
        "without the bridge they stay two groups",
        len({g for _, _, g in apart.assignments}) == 2,
    )


def test_group_keys_are_exact() -> None:
    m = allocate_splits(spec(), [rec("e1", "Conv"), rec("e2", "conv"), rec("e3", "conv ")])
    check(
        CID, "case/space variants are distinct groups", len({g for _, _, g in m.assignments}) == 3
    )
    check(CID, "empty key refused", raises(lambda: allocate_splits(spec(), [rec("e1", "")])))
    check(
        CID, "whitespace key refused", raises(lambda: allocate_splits(spec(), [rec("e1", " \t")]))
    )
    check(CID, "None key refused", raises(lambda: allocate_splits(spec(), [rec("e1", None)])))


def test_group_role_independent_of_other_groups() -> None:
    base = many(200)
    a = roles_of(allocate_splits(spec(), base))
    more = base + many(300, prefix="extra")
    b = roles_of(allocate_splits(spec(), more))
    check(
        CID,
        "adding groups never moves an existing group",
        all(b[rid] == role for rid, role in a.items()),
    )
    fewer = roles_of(allocate_splits(spec(), base[:50]))
    check(
        CID,
        "removing groups never moves a remaining group",
        all(a[rid] == role for rid, role in fewer.items()),
    )


def test_no_outcome_input_exists() -> None:
    params = set(inspect.signature(allocate_splits).parameters)
    check(CID, "allocate_splits takes only a spec and records", params == {"spec", "records"})
    check(
        CID,
        "SplitSpec has no outcome field",
        set(SplitSpec.__dataclass_fields__) == {"strategy", "seed", "ratios", "cutoff"},
    )
    check(
        CID,
        "SplitRecord has no outcome field",
        set(SplitRecord.__dataclass_fields__)
        == {"record_id", "group_keys", "preassigned_role", "event_time"},
    )


def rebuilt(m: SplitManifest, assignments: tuple[tuple[str, str, str], ...]) -> SplitManifest:
    """A manifest whose counts, status and hash are all consistent with `assignments`
    (counted per distinct record id, so only the one defect under test can raise)."""
    by_record = {rid: (role, gid) for rid, role, gid in assignments}
    tally: dict[str, tuple[set[str], set[str]]] = {role: (set(), set()) for role in ROLES}
    for rid, (role, gid) in by_record.items():
        if role in tally:
            tally[role][0].add(rid)
            tally[role][1].add(gid)
    counts = tuple((role, len(tally[role][0]), len(tally[role][1])) for role in ROLES)
    status = "insufficient" if any(c == 0 for _, c, _ in counts) else "ready"
    out = replace(m, assignments=assignments, counts=counts, status=status)
    return replace(out, manifest_sha256=compute_manifest_sha256(out))


def test_verify_rejects_crossing_group() -> None:
    m = allocate_splits(spec(), [rec("v1", "g"), rec("v2", "g"), rec("v3", "h")])
    (rid_a, role_a, gid_a) = m.assignments[0]
    other = next(r for r in ROLES if r != role_a)
    tampered = rebuilt(m, ((rid_a, other, gid_a),) + m.assignments[1:])
    check(
        CID,
        "a group split across two roles is refused even with a valid hash",
        raises(lambda: verify_manifest(tampered)),
    )
    check(CID, "an untouched manifest verifies", not raises(lambda: verify_manifest(m)))


def test_verify_rejects_tampering() -> None:
    m = allocate_splits(spec(), many(60))
    first = m.assignments[0]
    moved = replace(
        m,
        assignments=((first[0], next(r for r in ROLES if r != first[1]), first[2]),)
        + m.assignments[1:],
    )
    check(
        CID, "a moved record with the old hash is refused", raises(lambda: verify_manifest(moved))
    )
    check(
        CID,
        "a changed seed with the old hash is refused",
        raises(lambda: verify_manifest(replace(m, seed="other"))),
    )
    check(
        CID,
        "a changed cutoff with the old hash is refused",
        raises(lambda: verify_manifest(replace(m, cutoff=5))),
    )
    check(
        CID,
        "a changed ratio with the old hash is refused",
        raises(lambda: verify_manifest(replace(m, ratios=(10, 10, 10, 70)))),
    )
    for bad_hash in (
        m.manifest_sha256.upper(),
        m.manifest_sha256 + " ",
        " " + m.manifest_sha256,
        m.manifest_sha256[:-1],
    ):
        check(
            CID,
            f"hash variant of length {len(bad_hash)} is refused",
            raises(lambda b=bad_hash: verify_manifest(replace(m, manifest_sha256=b))),
        )
    check(
        CID,
        "an empty hash is refused",
        raises(lambda: verify_manifest(replace(m, manifest_sha256=""))),
    )
    badcounts = replace(m, counts=tuple((r, c + 1, g) for r, c, g in m.counts))
    check(CID, "wrong counts are refused", raises(lambda: verify_manifest(badcounts)))
    badstatus = allocate_splits(spec(ratios=(50, 0, 0, 50)), many(40))
    check(
        CID,
        "a falsely ready status is refused",
        raises(lambda: verify_manifest(replace(badstatus, status="ready"))),
    )
    for bad_status in ("Ready", "ready ", "READY", ""):
        check(
            CID,
            f"status {bad_status!r} is refused",
            raises(lambda b=bad_status: verify_manifest(replace(m, status=b))),
        )
    insufficient = allocate_splits(spec(ratios=(50, 0, 0, 50)), many(40))
    check(
        CID,
        "an insufficient manifest verifies as insufficient",
        not raises(lambda: verify_manifest(insufficient)),
    )
    for bad_status in ("Insufficient", "insufficient "):
        check(
            CID,
            f"insufficient variant {bad_status!r} is refused",
            raises(lambda b=bad_status: verify_manifest(replace(insufficient, status=b))),
        )
    for bad_role in ("holdout", "TRAIN", "train ", ""):
        unknown = rebuilt(m, (("zz", bad_role, "g"),) + m.assignments)
        check(
            CID,
            f"unknown role {bad_role!r} is refused",
            raises(lambda u=unknown: verify_manifest(u)),
        )
    dup = rebuilt(m, m.assignments + (m.assignments[0],))
    check(CID, "a duplicate record is refused", raises(lambda: verify_manifest(dup)))
    check(CID, "a non-manifest is refused", raises(lambda: verify_manifest({})))


def test_preassigned_eval_never_becomes_train() -> None:
    records = [rec(f"p{i}", f"pg{i}", preassigned_role="final_qualification") for i in range(100)]
    m = allocate_splits(spec(), records)
    check(
        CID,
        "preassigned final stays final",
        {role for _, role, _ in m.assignments} == {"final_qualification"},
    )
    mixed = [rec("m1", "mg", preassigned_role="development_eval"), rec("m2", "mg")]
    check(
        CID,
        "an unassigned sibling follows the pinned eval role",
        set(roles_of(allocate_splits(spec(), mixed)).values()) == {"development_eval"},
    )
    check(
        CID,
        "a group with conflicting preassignments is refused",
        raises(
            lambda: allocate_splits(
                spec(),
                [
                    rec("c1", "cg", preassigned_role="train"),
                    rec("c2", "cg", preassigned_role="validation"),
                ],
            )
        ),
    )
    for bad_role in ("TRAIN", "train ", " train", "Final_Qualification"):
        check(
            CID,
            f"preassigned role {bad_role!r} refused",
            raises(
                lambda b=bad_role: allocate_splits(spec(), [rec("u1", "ug", preassigned_role=b)])
            ),
        )
    check(
        CID,
        "an unknown preassigned role is refused",
        raises(lambda: allocate_splits(spec(), [rec("u1", "ug", preassigned_role="heldout_test")])),
    )
    check(
        CID,
        "preassigned train is honoured",
        set(roles_of(allocate_splits(spec(), [rec("t1", "tg", preassigned_role="train")])).values())
        == {"train"},
    )
    many_unpinned = many(300)
    base = roles_of(allocate_splits(spec(), many_unpinned + records[:20]))
    check(
        CID,
        "pinned groups never leave their role with unpinned neighbours",
        all(base[r.record_id] == "final_qualification" for r in records[:20]),
    )


def test_temporal_cutoff_is_prospective() -> None:
    cutoff = 1000
    records = []
    for i in range(300):
        records.append(rec(f"t{i}", f"tg{i}", event_time=i * 5))
    m = allocate_splits(spec(cutoff=cutoff), records)
    role = roles_of(m)
    for i in range(300):
        before = i * 5 < cutoff
        if before:
            check(
                CID, f"t{i} before the cutoff stays in train/validation", role[f"t{i}"] in ROLES[:2]
            )
        else:
            check(
                CID,
                f"t{i} at or after the cutoff stays in an eval role",
                role[f"t{i}"] in ROLES[2:],
            )
    at = allocate_splits(spec(cutoff=cutoff), [rec("e1", "e", event_time=cutoff)])
    check(CID, "a record exactly at the cutoff is post-cutoff", roles_of(at)["e1"] in ROLES[2:])
    just = allocate_splits(spec(cutoff=cutoff), [rec("e1", "e", event_time=cutoff - 1)])
    check(CID, "a record one tick before is pre-cutoff", roles_of(just)["e1"] in ROLES[:2])
    straddle = allocate_splits(
        spec(cutoff=cutoff), [rec("s1", "sg", event_time=1), rec("s2", "sg", event_time=cutoff + 1)]
    )
    check(
        CID,
        "a group straddling the cutoff goes wholly to eval",
        {r for r in roles_of(straddle).values()} <= set(ROLES[2:])
        and len(set(roles_of(straddle).values())) == 1,
    )
    check(
        CID,
        "a missing event_time is refused under a cutoff",
        raises(lambda: allocate_splits(spec(cutoff=cutoff), [rec("n1", "ng")])),
    )
    check(
        CID,
        "post-cutoff preassigned train is refused",
        raises(
            lambda: allocate_splits(
                spec(cutoff=cutoff), [rec("b1", "bg", preassigned_role="train", event_time=cutoff)]
            )
        ),
    )
    check(
        CID,
        "pre-cutoff preassigned eval is honoured",
        roles_of(
            allocate_splits(
                spec(cutoff=cutoff),
                [rec("b1", "bg", preassigned_role="final_qualification", event_time=1)],
            )
        )["b1"]
        == "final_qualification",
    )
    check(CID, "cutoff of 1e13 accepted", not raises(lambda: spec(cutoff=10**13)))
    check(CID, "cutoff of 1e13+1 refused", raises(lambda: spec(cutoff=10**13 + 1)))
    check(CID, "cutoff of 0 accepted", not raises(lambda: spec(cutoff=0)))
    check(
        CID,
        "event_time of 1e13 accepted",
        not raises(lambda: allocate_splits(spec(), [rec("n1", "ng", event_time=10**13)])),
    )
    check(
        CID,
        "event_time of 1e13+1 refused",
        raises(lambda: allocate_splits(spec(), [rec("n1", "ng", event_time=10**13 + 1)])),
    )
    for bad in (-1, 1.5, True, "5"):
        check(CID, f"cutoff {bad!r} refused", raises(lambda b=bad: spec(cutoff=b)))
    check(
        CID,
        "negative event_time refused",
        raises(lambda: allocate_splits(spec(), [rec("n1", "ng", event_time=-1)])),
    )
    check(
        CID,
        "float event_time refused",
        raises(lambda: allocate_splits(spec(), [rec("n1", "ng", event_time=1.5)])),
    )


def test_malformed_inputs_fail_closed() -> None:
    check(CID, "spec of the wrong type", raises(lambda: allocate_splits({}, many(3))))
    check(CID, "records None", raises(lambda: allocate_splits(spec(), None)))
    check(CID, "records empty list", raises(lambda: allocate_splits(spec(), [])))
    check(CID, "records empty tuple", raises(lambda: allocate_splits(spec(), ())))
    check(CID, "records a string", raises(lambda: allocate_splits(spec(), "abc")))
    check(
        CID,
        "record of the wrong type",
        raises(lambda: allocate_splits(spec(), [{"record_id": "a"}])),
    )
    check(
        CID,
        "duplicate record id",
        raises(lambda: allocate_splits(spec(), [rec("d", "g1"), rec("d", "g2")])),
    )
    check(CID, "empty record id", raises(lambda: allocate_splits(spec(), [rec("")])))
    check(CID, "whitespace record id", raises(lambda: allocate_splits(spec(), [rec(" ")])))
    check(
        CID,
        "case-variant record ids are distinct",
        not raises(lambda: allocate_splits(spec(), [rec("d", "g1"), rec("D", "g2")])),
    )
    check(
        CID,
        "group_keys as a list",
        raises(lambda: allocate_splits(spec(), [SplitRecord("a", ["g"])])),
    )
    check(CID, "group_keys empty", raises(lambda: allocate_splits(spec(), [SplitRecord("a", ())])))
    check(CID, "empty seed", raises(lambda: spec(seed="")))
    check(CID, "whitespace seed", raises(lambda: spec(seed="  ")))
    check(CID, "128-character seed accepted", not raises(lambda: spec(seed="s" * 128)))
    check(CID, "129-character seed refused", raises(lambda: spec(seed="s" * 129)))
    check(CID, "SplitEngineError is a ValueError", issubclass(SplitEngineError, ValueError))
    check(CID, "SplitSpec frozen", raises_frozen(lambda: setattr(spec(), "seed", "x")))
    check(CID, "SplitRecord frozen", raises_frozen(lambda: setattr(rec("a"), "record_id", "x")))


GOLDEN_SHA = "ead1a66d97f34d46aca7263b7f0dc2d771dcbfb6651dfb0fccdf8481ce247b3c"
GOLDEN_COUNTS = (
    ("train", 146, 22),
    ("validation", 14, 2),
    ("development_eval", 20, 3),
    ("final_qualification", 20, 3),
)
GOLDEN_TRIPLES = (
    ("r000", "train", "conv-0"),
    ("r001", "final_qualification", "conv-1"),
    ("r007", "train", "conv-127"),
    ("r029", "train", "conv-119"),
    ("r030", "train", "conv-0"),
    ("r100", "train", "conv-10"),
    ("r199", "train", "conv-109"),
)


def golden_records() -> list[SplitRecord]:
    return [rec(f"r{i:03d}", f"conv-{i}", f"tmpl-{i % 30}") for i in range(200)]


def golden_spec() -> SplitSpec:
    return spec(seed="golden-seed")


def test_pinned_golden_manifest() -> None:
    m = allocate_splits(golden_spec(), golden_records())
    check(CID, "golden manifest hash is pinned", m.manifest_sha256 == GOLDEN_SHA)
    check(CID, "golden per-role record and group counts are pinned", m.counts == GOLDEN_COUNTS)
    check(CID, "golden status is ready", m.status == "ready")
    by_id = {a[0]: a for a in m.assignments}
    for triple in GOLDEN_TRIPLES:
        check(CID, f"golden triple {triple[0]} is pinned", by_id[triple[0]] == triple)
    check(
        CID,
        "template families collapse 200 records into exactly 30 groups",
        sum(g for _, _, g in m.counts) == 30,
    )


def test_hash_is_independent_of_pythonhashseed() -> None:
    code = (
        "import sys; sys.path.insert(0, sys.argv[1])\n"
        "from tests.tasks.test_ftf_0517 import golden_records, golden_spec\n"
        "from workers.data.group_aware_split_engine import allocate_splits\n"
        "print(allocate_splits(golden_spec(), golden_records()).manifest_sha256)\n"
    )
    for hash_seed in ("0", "777"):
        env = {**os.environ, "PYTHONHASHSEED": hash_seed}
        out = subprocess.run(  # noqa: S603 -- fixed argv, no shell
            [sys.executable, "-c", code, str(ROOT)],
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        check(
            CID,
            f"hash under PYTHONHASHSEED={hash_seed} equals the golden SHA-256 ({out.stderr[-200:]})",
            out.stdout.strip() == GOLDEN_SHA,
        )


def test_group_counts_are_group_counts_not_record_counts() -> None:
    m = allocate_splits(spec(), [rec(f"x{i}", "one-family") for i in range(5)])
    check(CID, "five records in one family are one group", sum(g for _, _, g in m.counts) == 1)
    check(CID, "and five records", sum(c for _, c, _ in m.counts) == 5)
    two = allocate_splits(spec(), [rec("a1", "ga"), rec("a2", "ga"), rec("b1", "gb")])
    check(CID, "two groups, three records", sum(g for _, _, g in two.counts) == 2)


def test_cutoff_is_recorded_and_tamper_refused() -> None:
    m = allocate_splits(spec(cutoff=1000), [rec("c1", "cg", event_time=1)])
    check(CID, "manifest.cutoff equals the spec cutoff", m.cutoff == 1000)
    check(CID, "no-cutoff run records None", allocate_splits(spec(), many(3)).cutoff is None)
    for forged in (999, 1001, None):
        tampered = replace(m, cutoff=forged)
        check(
            CID,
            f"cutoff edited to {forged!r} is refused",
            raises(lambda t=tampered: verify_manifest(t)),
        )
    resealed = replace(m, cutoff=999)
    resealed = replace(resealed, manifest_sha256=compute_manifest_sha256(resealed))
    check(
        CID,
        "a cutoff change changes the manifest hash",
        resealed.manifest_sha256 != m.manifest_sha256,
    )


def test_preassigned_validation_follows_the_cutoff() -> None:
    check(
        CID,
        "a post-cutoff group preassigned validation is refused",
        raises(
            lambda: allocate_splits(
                spec(cutoff=1000), [rec("p1", "pg", preassigned_role="validation", event_time=1000)]
            )
        ),
    )
    ok = allocate_splits(
        spec(cutoff=1000), [rec("p1", "pg", preassigned_role="validation", event_time=999)]
    )
    check(
        CID,
        "a pre-cutoff group preassigned validation is honoured",
        roles_of(ok)["p1"] == "validation",
    )


def test_manifest_hash_binds_group_ids_and_strategy() -> None:
    m = allocate_splits(spec(), [rec("h1", "ga"), rec("h2", "gb")])
    first = m.assignments[0]
    regrouped = rebuilt(m, ((first[0], first[1], "renamed-group"),) + m.assignments[1:])
    check(
        CID,
        "changing only a group_id changes the hash",
        regrouped.manifest_sha256 != m.manifest_sha256,
    )
    other = replace(m, strategy="other_strategy")
    check(
        CID,
        "changing only the strategy changes the hash",
        compute_manifest_sha256(other) != m.manifest_sha256,
    )
    check(
        CID, "a strategy edit with the old hash is refused", raises(lambda: verify_manifest(other))
    )


def test_negative_ratio_in_any_slot_refused() -> None:
    for ratios in ((70, -10, 20, 20), (70, 10, -10, 30), (110, -5, -5, 0), (70, 20, 20, -10)):
        check(CID, f"ratios {ratios} refused", raises(lambda r=ratios: spec(ratios=r)))


def test_bridge_keys_beyond_the_second_are_still_unioned() -> None:
    """Round-3 return: the union step folds every one of a record's own extra keys into
    its group (`group_keys[1:]`). Two verifier mutations passed every pre-round-3 test here
    because no record in this file carried 3+ keys: V3 truncates the union to `[1:2]`
    (only the second key), V42 to `[-1:]` (only the last key). A 3-key record whose THIRD
    key is the only bridge to a pinned group catches V3; a 3-key record whose MIDDLE key
    is the only bridge catches V42."""
    third_key_bridge = [
        rec("v3a", "ka"),
        rec("v3b", "kb", preassigned_role="final_qualification"),
        rec("v3bridge", "ka", "kx", "kb"),
    ]
    m3 = allocate_splits(spec(), third_key_bridge)
    roles3 = roles_of(m3)
    check(
        CID,
        "a 3-key bridge record (third key is the only link) merges ka/kx/kb into one group",
        len({g for _, _, g in m3.assignments}) == 1,
    )
    check(
        CID,
        "the merged family inherits its pinned member's role (catches V3's [1:2] truncation)",
        set(roles3.values()) == {"final_qualification"},
    )

    fourth_key_bridge = [
        rec("v3c", "kc"),
        rec("v3d", "kd", preassigned_role="development_eval"),
        rec("v3bridge2", "kc", "ky", "kd", "kz"),
    ]
    m4 = allocate_splits(spec(), fourth_key_bridge)
    roles4 = roles_of(m4)
    check(
        CID,
        "a 4-key bridge record (third key is the only link; a 4th key is filler) merges"
        " every key into one group",
        len({g for _, _, g in m4.assignments}) == 1,
    )
    check(
        CID,
        "the merged 4-key family inherits its pinned member's role (catches V3's [1:2]"
        " truncation on a 4-key record too)",
        set(roles4.values()) == {"development_eval"},
    )

    middle_key_bridge = [
        rec("v42a", "wa"),
        rec("v42b", "wmid", preassigned_role="validation"),
        rec("v42bridge", "wa", "wmid", "wtail"),
    ]
    mm = allocate_splits(spec(), middle_key_bridge)
    rolesm = roles_of(mm)
    check(
        CID,
        "a 3-key bridge record whose MIDDLE key is the only link merges wa/wmid/wtail"
        " into one group",
        len({g for _, _, g in mm.assignments}) == 1,
    )
    check(
        CID,
        "the merged family inherits its pinned member's role (catches V42's [-1:] truncation)",
        set(rolesm.values()) == {"validation"},
    )


def test_generated_key_level_property_no_key_crosses_roles() -> None:
    """Class-closing property test (Control-plane round-3 ruling): over many fixed-seed,
    randomly-overlapping 1-5-key records, across several ratio sets and with/without a
    cutoff, no single linkage key value is ever attributed to two different roles in the
    output, and every resulting manifest independently verifies."""
    ratio_sets = ((70, 10, 10, 10), (25, 25, 25, 25), (1, 1, 1, 97), (40, 40, 10, 10))
    pool = [f"pk-{i}" for i in range(60)]
    for ratio_set in ratio_sets:
        for cutoff in (None, 500):
            rng = random.Random(f"ftf-0517-r3|{ratio_set}|{cutoff}")
            records = []
            for i in range(400):
                n_keys = rng.randint(1, 5)
                keys = tuple(rng.sample(pool, n_keys))
                event_time = rng.randint(0, 1000) if cutoff is not None else None
                records.append(rec(f"gen{i}", *keys, event_time=event_time))
            m = allocate_splits(spec(ratios=ratio_set, cutoff=cutoff), records)
            role_by_record = roles_of(m)
            role_by_key: dict[str, set[str]] = {}
            for r in records:
                role = role_by_record[r.record_id]
                for key in r.group_keys:
                    role_by_key.setdefault(key, set()).add(role)
            crossing = {k: v for k, v in role_by_key.items() if len(v) > 1}
            check(
                CID,
                f"no linkage key crosses roles (ratios={ratio_set}, cutoff={cutoff})",
                not crossing,
            )
            check(
                CID,
                f"the generated manifest independently verifies (ratios={ratio_set},"
                f" cutoff={cutoff})",
                not raises(lambda mm=m: verify_manifest(mm)),
            )


class _StrSubclass(str):
    """Same text, ordinary inherited hash/eq -- type(x) is not str must still refuse it."""


class _ForeignHashStr(str):
    """Same text but a hash/eq that never matches a plain str with the same text. This is
    the concrete mechanism by which an isinstance-only type check would let one family's
    records silently split across two groups (and therefore two roles) instead of raising:
    the union-find dict hashes this key differently from an identical-looking plain str."""

    def __hash__(self) -> int:
        return hash(("foreign", str(self)))

    def __eq__(self, other: object) -> bool:
        return False


def test_keys_roles_and_seed_require_exact_str_type() -> None:
    check(
        CID,
        "a str-subclass group key (ordinary hash/eq) is refused, not silently accepted",
        raises(lambda: allocate_splits(spec(), [rec("s1", _StrSubclass("conv-1"))])),
    )
    foreign = _ForeignHashStr("conv-1")
    check(
        CID,
        "a str-subclass group key with foreign hash/eq is refused rather than silently"
        " splitting the plain-str family it claims to share a key with",
        raises(
            lambda: allocate_splits(spec(), [rec("s1", "conv-1"), rec("s2", foreign, "other-key")])
        ),
    )
    for bad_key in (123, 1.5, True, [], {}, b"conv-1"):
        check(
            CID,
            f"group key {bad_key!r} ({type(bad_key).__name__}) refused, not a raw TypeError",
            raises(lambda k=bad_key: allocate_splits(spec(), [SplitRecord("s1", (k,))])),
        )
    check(
        CID,
        "a str-subclass record_id is refused",
        raises(lambda: allocate_splits(spec(), [SplitRecord(_StrSubclass("r1"), ("g",))])),
    )
    check(CID, "a str-subclass seed is refused", raises(lambda: spec(seed=_StrSubclass("seed-1"))))
    for bad_role in ([], {}, 123, True, _StrSubclass("train")):
        check(
            CID,
            f"preassigned_role {bad_role!r} ({type(bad_role).__name__}) refused, not a raw"
            " TypeError",
            raises(
                lambda b=bad_role: allocate_splits(spec(), [rec("u1", "ug", preassigned_role=b)])
            ),
        )


_ALL_ZERO_COUNTS = (
    ("train", 0, 0),
    ("validation", 0, 0),
    ("development_eval", 0, 0),
    ("final_qualification", 0, 0),
)


def manifest_with_raw_assignments(
    assignments: tuple[Any, ...],
    counts: tuple[tuple[str, int, int], ...] = _ALL_ZERO_COUNTS,
    status: str = "insufficient",
) -> SplitManifest:
    base = SplitManifest(
        strategy=STRATEGY,
        seed="s",
        ratios=(70, 10, 10, 10),
        cutoff=None,
        assignments=assignments,
        counts=counts,
        status=status,
        manifest_sha256="",
    )
    return replace(base, manifest_sha256=compute_manifest_sha256(base))


def _one_record_counts(role: str) -> tuple[tuple[str, int, int], ...]:
    """Counts for a single-entry manifest whose one record has role `role` -- so the
    counts/status are self-consistent with the assignment, and only the ONE guard a
    test targets can still raise (mirrors the `rebuilt()` helper above)."""
    return tuple((r, 1, 1) if r == role else (r, 0, 0) for r in ROLES)


def test_verify_manifest_rejects_malformed_assignment_entries() -> None:
    for bad_assignments, label in (
        ((("a", "train", "c", "d"),), "a 4-tuple assignment"),
        ((("a",),), "a 1-tuple assignment"),
        ((["a", "train", "g"],), "a list instead of a tuple"),
        ((("a", [], "g"),), "an unhashable (list) role"),
        ((("a", {}, "g"),), "an unhashable (dict) role"),
        ((("a", 1, "g"),), "a non-str (int) role"),
    ):
        bad = manifest_with_raw_assignments(bad_assignments)
        check(
            CID,
            f"verify_manifest refuses {label}, not a raw TypeError/ValueError",
            raises(lambda b=bad: verify_manifest(b)),
        )
    # These two give counts/status self-consistent with a "train" acceptance, so the
    # record_id/group_id type check is the ONLY thing that can still raise -- otherwise
    # a coincidental counts mismatch against the placeholder all-zero counts above would
    # raise for the wrong reason and hide a dropped or bypassed type check.
    consistent_cases: tuple[tuple[tuple[Any, ...], str], ...] = (
        (((1, "train", "g"),), "a non-str (int) record_id"),
        ((("a", "train", 5),), "a non-str (int) group_id"),
    )
    for bad_assignments, label in consistent_cases:
        bad = manifest_with_raw_assignments(
            bad_assignments, counts=_one_record_counts("train"), status="insufficient"
        )
        check(
            CID,
            f"verify_manifest refuses {label}, not a raw TypeError/ValueError, even with"
            " otherwise-consistent counts",
            raises(lambda b=bad: verify_manifest(b)),
        )


def main() -> int:
    tests = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_") and callable(f)]
    for name, fn in tests:
        try:
            fn()
        except Exception as exc:  # noqa: BLE001 -- a crash is a failed test, reported by name
            FAILURES.append(f"FAIL [{CID}] {name} raised {type(exc).__name__}: {exc}")
    if FAILURES:
        for failure in FAILURES:
            print(failure)
        print(f"{len(FAILURES)} check(s) failed")
        return 1
    print("all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
