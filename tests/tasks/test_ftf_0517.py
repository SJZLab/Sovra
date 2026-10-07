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

Run directly: python3 tests/tasks/test_ftf_0517.py
"""

from __future__ import annotations

import inspect
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
