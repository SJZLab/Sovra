#!/usr/bin/env python3
# mypy: disable-error-code="arg-type,call-arg,list-item,unused-ignore,func-returns-value,attr-defined,misc,assignment,index,var-annotated"
# ruff: noqa: E501
"""FTF-0518 -- Split leakage detector.

Runnable without any database or network: `workers.data.split_leakage_detector` is pure and
synchronous (no independent schema change). Every record here is built from FTF-0506
`normalize_conversation` (through the FTF-0502-P1 mapping helpers) and every manifest from FTF-0517
`allocate_splits`; the only hand-edited manifests are the tamper cases.

Acceptance ("Check exact/normalized/group/semantic cross-purpose leakage including previous
campaigns"): test_each_method_detects_its_seeded_pair, test_findings_name_both_records_and_reason,
test_recall_over_ten_thousand_records, test_previous_campaigns, test_consumed_final_reuse,
test_group_keys_across_roles.

Negative acceptance ("Privacy-forbidden embedding provider is denied; unresolved leakage blocks
final freeze and training"): test_route_profile_matrix, test_semantic_saturation_is_not_clear,
test_gate_blocks_unresolved_leakage, test_reviews_bind_only_review_required_findings,
test_tampered_inputs_fail_closed.

Run directly: python3 tests/tasks/test_ftf_0518.py
"""

from __future__ import annotations

import hashlib
import json
import os
import random
import subprocess
import sys
import time
from dataclasses import replace
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests" / "tasks"))
sys.path.insert(0, str(ROOT / "apps" / "api"))

import import_test_support as support  # noqa: E402
from packages.security.security_profile_enforcement import (  # noqa: E402
    Destination,
    RouteAuthorization,
)
from workers.data import import_common as ic  # noqa: E402
from workers.data import split_leakage_detector as sl  # noqa: E402
from workers.data.group_aware_split_engine import (  # noqa: E402
    SplitRecord,
    SplitSpec,
    allocate_splits,
)
from workers.data.near_duplicate_detector import NearDuplicateThresholds  # noqa: E402
from workers.data.normalized_duplicate_detector import compute_normalized_text_hash  # noqa: E402

FAILURES: list[str] = []
SPEC = SplitSpec("sha256_group_hash_v1", "seed-0518", (70, 10, 10, 10))
INTERNAL = Destination(kind="internal")
ROUTE = sl.EmbeddingRoute(INTERNAL, "standard", "standard")
BLOCKED_ROUTE = sl.EmbeddingRoute(
    Destination(kind="external", provider="acme-embed"), "zero_egress", "standard"
)
ROLES = ("train", "validation", "development_eval", "final_qualification")
PC = '{"shape":"prompt_completion","prompt":{"key":"q"},"completion":{"key":"a"}}'
MAPPING = ic.parse_mapping(PC)

LONG_A = (
    "please summarise the quarterly maintenance schedule for the northern warehouse including "
    "every inspection window the assigned technician and the escalation contact for each bay"
)
LONG_B = LONG_A.replace("northern", "southern")
SEM_A = (
    "please list every open ticket assigned to the platform team that mentions the billing "
    "export failure together with the owner and the last update time for each one"
)
SEM_B = SEM_A.replace("billing", "invoice")


def check(criterion_id: str, description: str, condition: bool) -> None:
    if not condition:
        FAILURES.append(f"FAIL [{criterion_id}] {description}")


# --- builders -------------------------------------------------------------------------


def raw_hash(label: str) -> str:
    return hashlib.sha256(label.encode()).hexdigest()


def record(
    record_id: str,
    q: str,
    a: str = "ok",
    *,
    keys: tuple[str, ...] = (),
    index: int = 0,
) -> sl.LeakageRecord:
    """A LeakageRecord whose content and hashes come from FTF-0506 via the P1 mapping."""
    mapped = ic.apply_mapping(MAPPING, {"q": q, "a": a})
    normalized = ic.normalize_mapped_record(
        MAPPING, mapped, source_record_index=index, dataset_sha256="0" * 64, source_type="upload"
    )
    candidate = ic.to_dataset_record_candidate(
        normalized, record_index=index, tool_schema_status="not_applicable"
    )
    return sl.LeakageRecord(
        record_id=record_id,
        content=candidate["content"],  # type: ignore[arg-type]
        record_sha256=str(candidate["record_sha256"]),
        normalized_sha256=str(candidate["normalized_sha256"]),
        group_keys=keys,
    )


def split(
    placed: list[tuple[sl.LeakageRecord, str, tuple[str, ...]]],
) -> tuple[Any, list[sl.LeakageRecord]]:
    """A real FTF-0517 manifest that puts each record in the role it was given.
    `placed` is (record, role, split-group keys)."""
    split_records = [
        SplitRecord(r.record_id, keys, preassigned_role=role) for r, role, keys in placed
    ]
    manifest = allocate_splits(SPEC, split_records)
    return manifest, [r for r, _role, _keys in placed]


def scan(
    placed: list[tuple[sl.LeakageRecord, str, tuple[str, ...]]],
    *,
    prior: tuple[sl.PriorQualificationSource, ...] = (),
    route: sl.EmbeddingRoute = ROUTE,
    **kwargs: Any,
) -> sl.LeakageReport:
    manifest, records = split(placed)
    return sl.detect_split_leakage(manifest, records, prior_sources=prior, route=route, **kwargs)


def solo(prefix: str, role: str, n: int = 4) -> list[tuple[sl.LeakageRecord, str, tuple[str, ...]]]:
    """`n` unrelated records in one role."""
    out: list[tuple[sl.LeakageRecord, str, tuple[str, ...]]] = []
    for i in range(n):
        words = " ".join(f"{prefix}{i}w{j}x{(i * 7 + j * 13) % 97}" for j in range(12))
        out.append(
            (
                record(f"{prefix}-{i}", words, f"answer {prefix} {i}", index=i),
                role,
                (f"{prefix}-g{i}",),
            )
        )
    return out


def methods_of(report: sl.LeakageReport) -> set[str]:
    return {f.method for f in report.findings}


def pair_of(f: sl.LeakageFinding) -> frozenset[str]:
    return frozenset({f.record_id_a, f.record_id_b})


def prior_source(
    label: str, role: str, records: list[sl.LeakageRecord], seed: str = "prior"
) -> sl.PriorQualificationSource:
    return sl.PriorQualificationSource(
        label=label,
        role=role,
        manifest_sha256=raw_hash(seed + label),
        records=tuple(records),
    )


# --- each method ----------------------------------------------------------------------


def test_each_method_detects_its_seeded_pair() -> None:
    cid = "LK-METHODS"
    base = solo("base", "train", 3) + solo("val", "validation", 2)
    original = record("t-orig", LONG_A, "the schedule is attached", keys=())
    # exact copy: same raw and canonical hashes, same content
    exact = record("f-exact", LONG_A, "the schedule is attached")
    check(
        cid,
        "fixture: an exact copy has the same record_sha256",
        exact.record_sha256 == original.record_sha256,
    )
    report = scan(
        [*base, (original, "train", ("g-orig",)), (exact, "final_qualification", ("g-exact",))]
    )
    found = [f for f in report.findings if pair_of(f) == {"t-orig", "f-exact"}]
    check(
        cid,
        f"exact copy: raw + canonical + text methods ({sorted(f.method for f in found)})",
        {f.method for f in found} >= {"exact_raw", "canonical_normalized", "text_normalized"},
    )
    check(
        cid,
        "exact copy is blocking in each of them",
        all(
            f.disposition == "blocking"
            for f in found
            if f.method in {"exact_raw", "canonical_normalized", "text_normalized"}
        ),
    )
    check(cid, "report is leakage_found, not clear", report.status == "leakage_found")

    # whitespace / case / timestamp variant: text-normalized only
    a = record("t-ws", "Please call the DEPOT at 2025-01-02T03:04:05Z about order 77", "Fine.")
    b = record("f-ws", "please   call the depot at 2026-05-06T07:08:09Z about order 77", "fine")
    check(
        cid,
        "fixture: the variants differ raw and canonical",
        a.record_sha256 != b.record_sha256 and a.normalized_sha256 != b.normalized_sha256,
    )
    check(
        cid,
        "fixture: the variants share the text-normalized hash",
        compute_normalized_text_hash(a.content) == compute_normalized_text_hash(b.content),
    )
    report = scan([*base, (a, "train", ("g-ws-a",)), (b, "development_eval", ("g-ws-b",))])
    found = [f for f in report.findings if pair_of(f) == {"t-ws", "f-ws"}]
    check(
        cid,
        f"variant: text_normalized ({sorted(f.method for f in found)})",
        "text_normalized" in {f.method for f in found},
    )
    check(
        cid,
        "variant: not raw and not canonical",
        not {f.method for f in found} & {"exact_raw", "canonical_normalized"},
    )

    # group: same customer-case key in two roles
    g1 = record("t-g", "alpha beta gamma delta epsilon one", "reply one", keys=("case-77",))
    g2 = record("v-g", "zeta eta theta iota kappa two two", "reply two", keys=("case-77",))
    report = scan([*base, (g1, "train", ("sg-a",)), (g2, "validation", ("sg-b",))])
    found = [f for f in report.findings if pair_of(f) == {"t-g", "v-g"}]
    check(
        cid,
        f"group: one blocking group finding ({[(f.method, f.disposition) for f in found]})",
        [(f.method, f.disposition) for f in found] == [("group", "blocking")],
    )

    # paraphrase over 0.95: semantic
    p1 = record("t-par", LONG_A, "the schedule is attached for review")
    p2 = record("f-par", LONG_B, "the schedule is attached for review")
    report = scan([*base, (p1, "train", ("g-p1",)), (p2, "final_qualification", ("g-p2",))])
    found = [f for f in report.findings if pair_of(f) == {"t-par", "f-par"}]
    check(
        cid,
        f"paraphrase: semantic methods fire ({sorted(f.method for f in found)})",
        {"char_ngram", "token_jaccard", "embedding"} <= {f.method for f in found},
    )
    check(
        cid,
        "paraphrase: review_required, never blocking",
        all(f.disposition == "review_required" for f in found),
    )
    check(
        cid,
        "paraphrase: carries its scores",
        all(f.scores and all(0.0 <= v <= 1.0 for _k, v in f.scores) for f in found),
    )
    check(cid, "paraphrase: report is leakage_found", report.status == "leakage_found")

    # same tool sequence + goal + template, different words
    s1 = record("t-str", "where is my order", "Your order #1234 has shipped to depot A.")
    s2 = record("v-str", "where is my order", "Your order #9 has shipped to depot B.")
    report = scan([*base, (s1, "train", ("g-s1",)), (s2, "validation", ("g-s2",))])
    found = [f for f in report.findings if pair_of(f) == {"t-str", "v-str"}]
    check(
        cid,
        f"structural: agent_trace_structural ({sorted(f.method for f in found)})",
        "agent_trace_structural" in {f.method for f in found},
    )
    check(
        cid,
        "structural is review_required",
        all(
            f.disposition == "review_required"
            for f in found
            if f.method == "agent_trace_structural"
        ),
    )

    clean = scan(
        [
            *solo("a", "train", 5),
            *solo("b", "validation", 3),
            *solo("c", "development_eval", 3),
            *solo("d", "final_qualification", 3),
        ]
    )
    check(
        cid,
        f"unrelated records: clear ({clean.status} {clean.findings[:1]})",
        clean.status == "clear" and clean.findings == () and clean.issues == (),
    )
    check(
        cid,
        "clear means every method ran",
        clean.methods_not_run == () and set(clean.methods_run) == set(sl.METHODS),
    )


def test_same_role_pairs_are_not_cross_role_leakage() -> None:
    cid = "LK-SAMEROLE"
    a = record("t1", LONG_A, "x")
    b = record("t2", LONG_A, "x")
    report = scan(
        [*solo("f", "final_qualification", 2), (a, "train", ("g1",)), (b, "train", ("g2",))]
    )
    check(
        cid,
        f"two identical train records are a duplicate problem, not split leakage ({report.status})",
        report.status == "clear" and report.findings == (),
    )


def test_findings_name_both_records_and_reason() -> None:
    cid = "LK-REPORT"
    marker = support.make_marker("leak")
    a = record("rec-train-1", f"{LONG_A} {marker}", "answer text")
    b = record("rec-final-1", f"{LONG_A} {marker}", "answer text")
    report = scan(
        [*solo("x", "train", 3), (a, "train", ("ga",)), (b, "final_qualification", ("gb",))]
    )
    findings = [f for f in report.findings if pair_of(f) == {"rec-train-1", "rec-final-1"}]
    check(cid, "the pair is reported", len(findings) >= 3)
    for f in findings:
        check(
            cid,
            f"{f.method}: both record ids and both roles",
            {f.record_id_a, f.record_id_b} == {"rec-train-1", "rec-final-1"}
            and {f.role_a, f.role_b} == {"train", "final_qualification"}
            and f.role_a != f.role_b,
        )
        check(cid, f"{f.method}: a reason", f.reason == "cross_role" and f.method in sl.METHODS)
        check(cid, f"{f.method}: no prior label", f.prior_source_label is None)
    text = json.dumps(sl.report_body(report), sort_keys=True, default=str)
    check(
        cid,
        "no content, text or marker anywhere in the report",
        marker not in text and LONG_A not in text,
    )
    for digest in (a.record_sha256, a.normalized_sha256, compute_normalized_text_hash(a.content)):
        check(cid, "no content hash in the report beyond record ids", digest not in text)
    check(cid, "no leaked marker in repr either", not support.leaked_markers(report, [marker]))
    ids = [f.finding_id for f in report.findings]
    check(
        cid,
        "finding ids are unique 64-hex digests",
        len(ids) == len(set(ids))
        and all(len(i) == 64 and set(i) <= set("0123456789abcdef") for i in ids),
    )
    check(cid, "findings are sorted by id (stable order)", ids == sorted(ids))
    check(cid, "report is bound to a sha256", len(report.report_sha256) == 64)


# --- recall ---------------------------------------------------------------------------


def synthetic_record(
    i: int, text: str | None = None, keys: tuple[str, ...] = ()
) -> sl.LeakageRecord:
    words = text or " ".join(f"w{i}x{j}y{(i * 31 + j * 17) % 997}" for j in range(8))
    content = {
        "messages": [
            {"role": "user", "content": [{"type": "text", "text": words}]},
            {"role": "assistant", "content": [{"type": "text", "text": f"reply {i}"}]},
        ],
        "metadata": {},
    }
    canonical = json.dumps(content, sort_keys=True, separators=(",", ":"))
    return sl.LeakageRecord(
        record_id=f"r{i:05d}",
        content=content,
        record_sha256=raw_hash(f"raw-{words}-{i}"),
        normalized_sha256=raw_hash(canonical),
        group_keys=keys,
    )


def test_recall_over_ten_thousand_records() -> None:
    cid = "LK-RECALL"
    total, seeded = 10_000, 200
    rng = random.Random(518)
    records = [synthetic_record(i) for i in range(total)]
    roles = {r.record_id: rng.choice(["train", "train", "train", "validation"]) for r in records}
    expected: set[frozenset[str]] = set()
    pool = list(range(total))
    rng.shuffle(pool)
    for n in range(seeded):
        a_i, b_i = pool[2 * n], pool[2 * n + 1]
        a, b = records[a_i], records[b_i]
        roles[b.record_id] = "final_qualification"
        kind = n % 3
        if kind == 0:  # exact copy: both hashes and content
            records[b_i] = replace(
                b,
                content=a.content,
                record_sha256=a.record_sha256,
                normalized_sha256=a.normalized_sha256,
            )
        elif kind == 1:  # normalized variant: text differs only in case / spacing
            upper = json.loads(json.dumps(a.content))
            upper["messages"][0]["content"][0]["text"] = (
                "  " + upper["messages"][0]["content"][0]["text"].upper()
            )
            records[b_i] = replace(b, content=upper)
        else:  # group link only
            records[a_i] = replace(a, group_keys=(f"case-{n}",))
            records[b_i] = replace(b, group_keys=(f"case-{n}",))
        expected.add(frozenset({a.record_id, b.record_id}))
    split_records = [
        SplitRecord(r.record_id, (f"g-{r.record_id}",), preassigned_role=roles[r.record_id])
        for r in records
    ]
    manifest = allocate_splits(SPEC, split_records)
    started = time.monotonic()
    report = sl.detect_split_leakage(manifest, records, route=BLOCKED_ROUTE)
    elapsed = time.monotonic() - started
    found = {pair_of(f) for f in report.findings if f.disposition == "blocking"}
    print(
        f"LK-RECALL: {total} records, {len(report.findings)} findings, {elapsed:.2f}s, "
        f"semantic skipped by route ({report.reason_code})"
    )
    check(
        cid,
        f"all {seeded} seeded violations found ({len(expected & found)}/{seeded})",
        expected <= found,
    )
    check(cid, f"nothing else is invented ({len(found - expected)} extra)", found <= expected)
    check(
        cid,
        "the blocked semantic stage is reported, never clear",
        report.status == "blocked" and report.reason_code == "security_profile_route_blocked",
    )
    check(cid, f"runs in bounded time ({elapsed:.1f}s)", elapsed < 60)


# --- previous campaigns ---------------------------------------------------------------


def test_previous_campaigns() -> None:
    cid = "LK-PRIOR"
    prior_final = record("old-final-1", LONG_A, "the schedule is attached")
    prior_dev = record("old-dev-1", "reset the gateway token for tenant seven", "done")
    prior_clean = record("old-dev-2", "an entirely unrelated old question about tires", "tyres")
    copy_of_final = record("n-train", LONG_A, "the schedule is attached")
    copy_of_dev = record("n-val", "reset the gateway token for tenant seven", "done")
    base = [
        *solo("tr", "train", 3),
        (copy_of_final, "train", ("g-nt",)),
        (copy_of_dev, "validation", ("g-nv",)),
        *solo("fi", "final_qualification", 2),
    ]
    sources = (
        prior_source("campaign-1-final", "final_qualification", [prior_final, prior_clean]),
        prior_source("campaign-1-dev", "development_eval", [prior_dev]),
    )
    report = scan(base, prior=sources)
    train_hits = [f for f in report.findings if f.prior_source_label == "campaign-1-final"]
    check(
        cid,
        f"a prior final record copied into current train is blocking "
        f"({[(f.method, f.disposition) for f in train_hits]})",
        train_hits != []
        and all(
            f.disposition == "blocking"
            for f in train_hits
            if f.method in {"exact_raw", "canonical_normalized", "text_normalized"}
        ),
    )
    check(
        cid,
        "naming the current record, the prior record and the prior label",
        all(
            {f.record_id_a, f.record_id_b} == {"n-train", "old-final-1"}
            and f.reason == "prior_campaign"
            for f in train_hits
            if f.method == "exact_raw"
        ),
    )
    check(
        cid,
        "the prior side carries its own role",
        all({f.role_a, f.role_b} == {"train", "final_qualification"} for f in train_hits),
    )
    dev_hits = [f for f in report.findings if f.prior_source_label == "campaign-1-dev"]
    check(
        cid,
        "a prior development record in current validation is found too",
        any(f.method == "exact_raw" and f.disposition == "blocking" for f in dev_hits),
    )
    check(
        cid,
        "the clean prior record is not reported",
        not any("old-dev-2" in {f.record_id_a, f.record_id_b} for f in report.findings),
    )
    check(cid, "status leakage_found", report.status == "leakage_found")
    # prior dev against current dev is not checked
    only_dev = scan(
        [
            *solo("tr", "train", 3),
            (
                record("c-dev", "reset the gateway token for tenant seven", "done"),
                "development_eval",
                ("g-cd",),
            ),
        ],
        prior=(prior_source("c1", "development_eval", [prior_dev]),),
    )
    check(
        cid,
        f"prior development vs current development is not checked ({only_dev.status})",
        only_dev.status == "clear",
    )
    # prior group keys
    keyed = record(
        "n-key", "something about rotating certificates quarterly", "ok", keys=("case-900",)
    )
    prior_keyed = record(
        "old-key", "a different old conversation about billing", "ok", keys=("case-900",)
    )
    report = scan(
        [*solo("tr", "train", 2), (keyed, "train", ("g-k",))],
        prior=(prior_source("c1-final", "final_qualification", [prior_keyed]),),
    )
    check(
        cid,
        f"a prior group key shared with current train is a blocking group finding "
        f"({[(f.method, f.reason) for f in report.findings]})",
        [(f.method, f.reason, f.disposition) for f in report.findings]
        == [("group", "prior_campaign", "blocking")],
    )


def test_consumed_final_reuse() -> None:
    cid = "LK-CONSUMED"
    prior_final = record("old-final", LONG_A, "the schedule is attached")
    reused = record("new-final", LONG_A, "the schedule is attached")
    report = scan(
        [*solo("tr", "train", 3), (reused, "final_qualification", ("g-nf",))],
        prior=(prior_source("campaign-1-final", "final_qualification", [prior_final]),),
    )
    hits = [f for f in report.findings if f.reason == "consumed_final_reuse"]
    check(
        cid,
        f"a prior final record reused in the current final role ({[(f.method) for f in hits]})",
        {"exact_raw", "canonical_normalized", "text_normalized"} <= {f.method for f in hits},
    )
    check(cid, "consumed reuse is blocking", all(f.disposition == "blocking" for f in hits))
    check(
        cid,
        "and names both ids and the prior label",
        all(
            {f.record_id_a, f.record_id_b} == {"new-final", "old-final"}
            and f.prior_source_label == "campaign-1-final"
            for f in hits
        ),
    )
    paraphrase = record("new-final-2", LONG_B, "the schedule is attached for review")
    old = record("old-final-2", LONG_A, "the schedule is attached for review")
    report = scan(
        [*solo("tr", "train", 3), (paraphrase, "final_qualification", ("g-n2",))],
        prior=(prior_source("c1", "final_qualification", [old]),),
    )
    check(
        cid,
        "a near copy of a consumed final suite is blocking as well",
        report.findings != ()
        and all(
            f.reason == "consumed_final_reuse" and f.disposition == "blocking"
            for f in report.findings
        ),
    )
    other = scan(
        [*solo("tr", "train", 3), (record("nf", LONG_A, "z"), "final_qualification", ("g1",))],
        prior=(prior_source("c1", "development_eval", [record("od", LONG_A, "z")]),),
    )
    check(
        cid,
        "a prior development record is not a consumed final",
        not any(f.reason == "consumed_final_reuse" for f in other.findings),
    )


def test_group_keys_across_roles() -> None:
    cid = "LK-GROUP"
    a = record("a", "first unrelated sentence about turbines", "x", keys=("shared-1", "only-a"))
    b = record("b", "second unrelated sentence about harbours", "y", keys=("only-b", "shared-1"))
    c = record("c", "third unrelated sentence about orchards", "z", keys=("only-c",))
    report = scan(
        [
            (a, "train", ("ga",)),
            (b, "final_qualification", ("gb",)),
            (c, "validation", ("gc",)),
            *solo("pad", "development_eval", 2),
        ]
    )
    groups = [f for f in report.findings if f.method == "group"]
    check(
        cid,
        f"one group finding for the shared key ({[(f.record_id_a, f.record_id_b) for f in groups]})",
        [pair_of(f) for f in groups] == [frozenset({"a", "b"})],
    )
    d = record("d", "fourth unrelated sentence about quarries", "q", keys=("same",))
    e = record("e", "fifth unrelated sentence about glaciers", "q", keys=("same",))
    report = scan(
        [(d, "train", ("gd",)), (e, "train", ("ge",)), *solo("pad", "final_qualification", 2)]
    )
    check(cid, "a shared key inside one role is not leakage", report.status == "clear")
    # the manifest's own group id counts as a key
    h1 = record("h1", "sixth unrelated sentence about lighthouses", "q", keys=("gid-x",))
    h2 = record("h2", "seventh unrelated sentence about meadows", "q")
    manifest, records = split(
        [
            (h1, "train", ("k1",)),
            (h2, "validation", ("gid-x",)),
            *solo("pad", "final_qualification", 2),
        ]
    )
    report = sl.detect_split_leakage(manifest, records, route=ROUTE)
    check(
        cid,
        "a record key equal to another role's manifest group id is found",
        [pair_of(f) for f in report.findings if f.method == "group"] == [frozenset({"h1", "h2"})],
    )


# --- route / profile ------------------------------------------------------------------


def test_route_profile_matrix() -> None:
    cid = "LK-ROUTE"
    exact_a = record("t", LONG_A, "z")
    exact_b = record("f", LONG_A, "z")
    placed = [
        *solo("tr", "train", 3),
        (exact_a, "train", ("g1",)),
        (exact_b, "final_qualification", ("g2",)),
    ]
    ext = Destination(kind="external", provider="acme-embed")
    managed = Destination(kind="managed_provider", provider="acme-embed")
    auth = RouteAuthorization(
        provider="acme-embed", surfaces=frozenset({"data"}), approved_by="owner-1"
    )
    managed_auth = RouteAuthorization(
        provider="acme-embed",
        surfaces=frozenset({"data"}),
        approved_by="owner-1",
        account="acct",
        region="eu-1",
        retention_terms="none",
    )
    cases = [
        (
            "zero_egress external",
            sl.EmbeddingRoute(ext, "zero_egress", "standard"),
            "security_profile_route_blocked",
        ),
        (
            "air_gapped managed",
            sl.EmbeddingRoute(managed, "air_gapped", "standard"),
            "security_profile_route_blocked",
        ),
        (
            "project zero_egress",
            sl.EmbeddingRoute(ext, "standard", "zero_egress"),
            "security_profile_route_blocked",
        ),
        (
            "job zero_egress",
            sl.EmbeddingRoute(ext, "standard", "standard", "zero_egress"),
            "security_profile_route_blocked",
        ),
        (
            "standard, no authorization",
            sl.EmbeddingRoute(ext, "standard", "standard"),
            "security_profile_route_not_authorized",
        ),
        (
            "standard, wrong provider",
            sl.EmbeddingRoute(
                ext,
                "standard",
                "standard",
                None,
                RouteAuthorization(provider="other", surfaces=frozenset({"data"}), approved_by="o"),
            ),
            "security_profile_route_not_authorized",
        ),
        (
            "standard, wrong surface",
            sl.EmbeddingRoute(
                ext,
                "standard",
                "standard",
                None,
                RouteAuthorization(
                    provider="acme-embed", surfaces=frozenset({"judge"}), approved_by="o"
                ),
            ),
            "security_profile_route_not_authorized",
        ),
        (
            "standard, authorized external",
            sl.EmbeddingRoute(ext, "standard", "standard", None, auth),
            "embedding_provider_unavailable",
        ),
        (
            "standard, authorized managed",
            sl.EmbeddingRoute(managed, "standard", "standard", None, managed_auth),
            "embedding_provider_unavailable",
        ),
        (
            "invalid profile",
            sl.EmbeddingRoute(ext, "bogus", "standard"),
            "security_profile_invalid_request",
        ),
    ]
    for label, route, code in cases:
        report = scan(placed, route=route)
        check(
            cid,
            f"{label}: blocked/{code} ({report.status}/{report.reason_code})",
            report.status == "blocked" and report.reason_code == code,
        )
        check(
            cid,
            f"{label}: exact/normalized/text/group still reported",
            {"exact_raw", "canonical_normalized", "text_normalized"} <= methods_of(report),
        )
        check(
            cid,
            f"{label}: no semantic finding and the semantic methods listed as not run",
            not methods_of(report) & set(sl.SEMANTIC_METHODS)
            and set(report.methods_not_run) == set(sl.SEMANTIC_METHODS)
            and not set(report.methods_run) & set(sl.SEMANTIC_METHODS),
        )
    for kind_route in (
        sl.EmbeddingRoute(INTERNAL, "zero_egress", "air_gapped"),
        sl.EmbeddingRoute(INTERNAL, "standard", "standard"),
    ):
        report = scan(placed, route=kind_route)
        check(
            cid,
            f"an internal destination runs the semantic stage under {kind_route.organization_profile}",
            report.reason_code is None and set(sl.SEMANTIC_METHODS) <= set(report.methods_run),
        )
    blocked_clean = scan(
        [*solo("a", "train", 3), *solo("b", "final_qualification", 3)], route=BLOCKED_ROUTE
    )
    check(
        cid,
        "a blocked route with nothing found is blocked, never clear",
        blocked_clean.status == "blocked" and blocked_clean.findings == (),
    )
    for bad in (None, "standard", INTERNAL):
        try:
            sl.detect_split_leakage(*split(placed), route=bad)  # type: ignore[arg-type]
            raised = False
        except sl.LeakageEngineError:
            raised = True
        check(cid, f"route {bad!r} is refused", raised)


def test_semantic_detector_is_never_called_before_the_route_clears() -> None:
    """The route check decides whether `detect_near_duplicates` is called at all, not only
    whether its results are kept: a report listing the semantic methods as not run is not, by
    itself, proof the (comparatively expensive, and for an external route, policy-sensitive)
    detector call never happened."""
    cid = "LK-ROUTE-F13"
    exact_a = record("t", LONG_A, "z")
    exact_b = record("f", LONG_A, "z")
    placed = [
        *solo("tr", "train", 3),
        (exact_a, "train", ("g1",)),
        (exact_b, "final_qualification", ("g2",)),
    ]
    ext = Destination(kind="external", provider="acme-embed")
    auth = RouteAuthorization(
        provider="acme-embed", surfaces=frozenset({"data"}), approved_by="owner-1"
    )
    cases = [
        ("internal", ROUTE, 1),
        (
            "zero_egress external (route_blocked)",
            sl.EmbeddingRoute(ext, "zero_egress", "standard"),
            0,
        ),
        (
            "standard, no authorization (route_not_authorized)",
            sl.EmbeddingRoute(ext, "standard", "standard"),
            0,
        ),
        (
            "standard, authorized external (embedding_provider_unavailable)",
            sl.EmbeddingRoute(ext, "standard", "standard", None, auth),
            0,
        ),
        ("invalid profile (invalid_request)", sl.EmbeddingRoute(ext, "bogus", "standard"), 0),
    ]
    original = sl.detect_near_duplicates
    calls = {"n": 0}

    def counting(*args: Any, **kwargs: Any) -> Any:
        calls["n"] += 1
        return original(*args, **kwargs)

    sl.detect_near_duplicates = counting  # type: ignore[assignment]
    try:
        for label, route, expected in cases:
            calls["n"] = 0
            scan(placed, route=route)
            check(
                cid,
                f"{label}: detect_near_duplicates called {expected} time(s), got {calls['n']}",
                calls["n"] == expected,
            )
    finally:
        sl.detect_near_duplicates = original
    check(
        cid,
        "fixture: the patch is removed (the real detector is back)",
        sl.detect_near_duplicates is original,
    )


def test_internal_route_scores_use_the_configured_thresholds() -> None:
    cid = "LK-THRESH"
    p1 = record("a", LONG_A, "reply")
    p2 = record("b", LONG_B, "reply")
    placed = [*solo("tr", "train", 3), (p1, "train", ("g1",)), (p2, "final_qualification", ("g2",))]
    strict = scan(placed, thresholds=NearDuplicateThresholds(1.0, 1.0, 1.0))
    check(
        cid,
        f"1.0 thresholds: a near copy is not a semantic finding ({methods_of(strict)})",
        not methods_of(strict) & {"char_ngram", "token_jaccard", "embedding"},
    )
    loose = scan(placed, thresholds=NearDuplicateThresholds(0.5, 0.5, 0.5))
    check(
        cid,
        "0.5 thresholds: all three fire",
        {"char_ngram", "token_jaccard", "embedding"} <= methods_of(loose),
    )
    exact = scan(placed, thresholds=NearDuplicateThresholds(0.0, 0.0, 0.0))
    check(
        cid,
        "0.0 thresholds: every candidate pair fires",
        {"char_ngram", "token_jaccard"} <= methods_of(exact),
    )
    for f in loose.findings:
        for name, value in f.scores:
            check(
                cid,
                f"{f.method}: score {name}={value} is at or above its 0.5 threshold when it fired",
                f.method == "agent_trace_structural"
                or value >= 0.5
                or name
                not in {
                    "char_ngram_similarity",
                    "token_jaccard_similarity",
                    "embedding_cosine_similarity",
                },
            )
    boundary = scan(placed, thresholds=NearDuplicateThresholds(0.95, 0.95, 0.95))
    exact_score = {f.method: dict(f.scores) for f in loose.findings if pair_of(f) == {"a", "b"}}
    token = exact_score.get("token_jaccard", {}).get("token_jaccard_similarity")
    if token is not None:
        at = scan(placed, thresholds=NearDuplicateThresholds(1.0, token, 1.0))
        above = scan(placed, thresholds=NearDuplicateThresholds(1.0, min(1.0, token + 0.0001), 1.0))
        check(
            cid,
            "the comparison is >=: a threshold equal to the score fires",
            "token_jaccard" in methods_of(at),
        )
        check(
            cid,
            "and a threshold above the score does not",
            "token_jaccard" not in methods_of(above),
        )
    check(
        cid,
        "default thresholds keep 0.95 for embeddings",
        boundary is not None and sl.DEFAULT_THRESHOLDS.embedding_cosine_similarity == 0.95,
    )


# --- saturation -----------------------------------------------------------------------


def test_semantic_saturation_is_not_clear() -> None:
    cid = "LK-SATURATION"
    siblings = []
    for i in range(300):
        siblings.append(
            (
                record(
                    f"sib-{i}",
                    f"quarterly report template number {i} for the warehouse team",
                    "template reply",
                    index=i,
                ),
                "train",
                (f"sg-{i}",),
            )
        )
    t = record("t-para", LONG_A, "the schedule is attached for review")
    f = record("f-para", LONG_B, "the schedule is attached for review")
    placed = [*siblings, (t, "train", ("gt",)), (f, "final_qualification", ("gf",))]
    report = scan(placed)
    check(
        cid,
        f"300 same-role template siblings: blocked/semantic_scan_saturated "
        f"({report.status}/{report.reason_code})",
        report.status == "blocked" and report.reason_code == "semantic_scan_saturated",
    )
    check(
        cid,
        "never reported clear, even though the paraphrase pair is invisible to the capped scan",
        report.status != "clear",
    )
    check(
        cid,
        "the semantic methods are listed as not run",
        set(sl.SEMANTIC_METHODS) <= set(report.methods_not_run),
    )
    roomy = scan(placed, max_semantic_partners=400)
    check(
        cid,
        f"a cap above the sibling count completes the scan ({roomy.status}/{roomy.reason_code})",
        roomy.reason_code != "semantic_scan_saturated",
    )
    for value in (0, -1, True, 1.5, "200", None):
        try:
            scan(placed, max_semantic_partners=value)
            raised = False
        except sl.LeakageEngineError:
            raised = True
        check(cid, f"max_semantic_partners={value!r} is refused", raised)
    one = scan(placed[:3] + placed[-2:], max_semantic_partners=1)
    check(
        cid,
        f"a cap of 1 is valid, and t/f's own near-duplicate match alone saturates it "
        f"({one.status}/{one.reason_code})",
        one.reason_code == "semantic_scan_saturated",
    )
    exact_hit = [
        *solo("a", "train", 2),
        (record("e1", LONG_A, "z"), "train", ("ge1",)),
        (record("e2", LONG_A, "z"), "final_qualification", ("ge2",)),
    ]
    report = scan(exact_hit, max_semantic_partners=1)
    check(
        cid,
        "exact findings survive a saturated semantic stage",
        {"exact_raw", "canonical_normalized"} <= methods_of(report),
    )


def _random_phrase(seed: int, n_chars: int = 200) -> str:
    """`n_chars` characters of random lowercase words, seeded so the near-duplicate shingle
    overlap between two different seeds stays at zero (confirmed empirically, not assumed)."""
    rng = random.Random(seed)
    alphabet = "abcdefghijklmnopqrstuvwxyz"
    words = ("".join(rng.choice(alphabet) for _ in range(6)) for _ in range(n_chars // 7))
    return " ".join(words)


def test_saturation_counts_both_sides_of_a_pair() -> None:
    """A record counted only on one side of each near-duplicate pair it appears in (e.g. only
    `batch_position_a`) can hide real saturation: a "hub" that near-duplicate-matches several
    otherwise-unrelated partners accumulates its count only across pairs where IT is the other
    side. Four disjoint-content partners, each sharing nothing with the others but each sharing
    a block with one prior "hub" record (so the hub, placed last, is always the larger position
    in its own pairs) isolates this: each partner's own count stays at 1 (well under the cap),
    while the hub alone reaches it."""
    cid = "LK-SATURATION-F14"
    n_partners = 4
    phrases = [_random_phrase(seed) for seed in range(n_partners)]
    hub_text = " ".join(phrases)
    partners = [
        (record(f"partner-{i}", phrase, "ok", keys=(f"pg-{i}",)), "train", (f"pg-{i}",))
        for i, phrase in enumerate(phrases)
    ]
    hub_record = record("hub", hub_text, "ok", keys=("hub-g",))
    hub = prior_source("prior-hub", "final_qualification", [hub_record])
    report = scan(partners, prior=(hub,), max_semantic_partners=n_partners)
    check(
        cid,
        f"the hub alone reaches the cap: blocked/semantic_scan_saturated "
        f"({report.status}/{report.reason_code})",
        report.status == "blocked" and report.reason_code == "semantic_scan_saturated",
    )
    # fixture check, run directly against the detector this module calls, confirming the
    # scenario is really "hub matches every partner once, no partner matches another partner" --
    # if this fixture check ever fails, the test above would no longer isolate F14 and must be
    # rebuilt, not loosened.
    batch = [
        {
            "record_index": i,
            "record_sha256": "a" * 64,
            "normalized_sha256": "b" * 64,
            "content": {
                "messages": [{"role": "user", "content": [{"type": "text", "text": phrase}]}],
                "metadata": {},
            },
            "purpose": "training",
        }
        for i, phrase in enumerate(phrases)
    ]
    hub_position = len(batch)
    batch.append(
        {
            "record_index": hub_position,
            "record_sha256": "a" * 64,
            "normalized_sha256": "b" * 64,
            "content": {
                "messages": [{"role": "user", "content": [{"type": "text", "text": hub_text}]}],
                "metadata": {},
            },
            "purpose": "held_out_evaluation",
        }
    )
    near = sl.detect_near_duplicates(
        batch,
        thresholds=NearDuplicateThresholds(0.0, 0.0, 0.0),
        max_candidates_per_record=n_partners,
    )
    hub_pairs = [
        c for c in near.candidates if hub_position in (c.batch_position_a, c.batch_position_b)
    ]
    partner_pairs = [
        c for c in near.candidates if hub_position not in (c.batch_position_a, c.batch_position_b)
    ]
    check(
        cid,
        f"fixture: the hub pairs with every one of the {n_partners} partners "
        f"({len(hub_pairs)} hub pairs)",
        len(hub_pairs) == n_partners,
    )
    check(cid, "fixture: no partner pairs with another partner", len(partner_pairs) == 0)
    check(
        cid,
        "fixture: the hub is always the larger (later) batch position in its own pairs",
        all(c.batch_position_b == hub_position for c in hub_pairs),
    )
    # a lower cap, below the sibling-free partner count: the hub still alone saturates it
    under = scan(partners, prior=(hub,), max_semantic_partners=n_partners - 1)
    check(
        cid,
        f"a cap one below the hub's true count still saturates "
        f"({under.status}/{under.reason_code})",
        under.status == "blocked" and under.reason_code == "semantic_scan_saturated",
    )
    # a cap above the hub's true count: nothing saturates, a clean scan completes
    over = scan(partners, prior=(hub,), max_semantic_partners=n_partners + 1)
    check(
        cid,
        f"a cap above the hub's true count does not saturate ({over.status}/{over.reason_code})",
        over.reason_code != "semantic_scan_saturated",
    )


# --- the gate ---------------------------------------------------------------------------


def seeded_report() -> sl.LeakageReport:
    t = record("t", LONG_A, "z")
    f = record("f", LONG_A, "z")
    p1 = record("pt", SEM_A, "ok")
    p2 = record("pf", SEM_B, "ok")
    return scan(
        [
            *solo("tr", "train", 2),
            (t, "train", ("g1",)),
            (f, "final_qualification", ("g2",)),
            (p1, "validation", ("g3",)),
            (p2, "development_eval", ("g4",)),
        ]
    )


def semantic_only_report() -> sl.LeakageReport:
    p1 = record("pt", SEM_A, "ok")
    p2 = record("pf", SEM_B, "ok")
    return scan(
        [
            *solo("tr", "train", 2),
            (p1, "validation", ("g3",)),
            (p2, "development_eval", ("g4",)),
            *solo("fi", "final_qualification", 2),
        ]
    )


def test_gate_blocks_unresolved_leakage() -> None:
    cid = "LK-GATE"
    clean = scan([*solo("a", "train", 4), *solo("b", "final_qualification", 2)])
    for stage in ("final_freeze", "training"):
        sl.assert_leakage_clear(clean, stage)
        check(cid, f"{stage}: a clear report passes", True)
        report = seeded_report()
        raised = None
        try:
            sl.assert_leakage_clear(report, stage)
        except sl.LeakageBlockedError as exc:
            raised = exc
        check(cid, f"{stage}: unresolved leakage raises", raised is not None)
        if raised is not None:
            check(
                cid,
                f"{stage}: the error names the stage and the open finding ids",
                raised.stage == stage
                and set(raised.finding_ids) == {f.finding_id for f in report.findings},
            )
    sem = semantic_only_report()
    check(
        cid,
        "fixture: the semantic-only report has only review_required findings",
        sem.findings != () and all(f.disposition == "review_required" for f in sem.findings),
    )
    for stage in ("final_freeze", "training"):
        try:
            sl.assert_leakage_clear(sem, stage)
            blocked = False
        except sl.LeakageBlockedError:
            blocked = True
        check(cid, f"{stage}: an unreviewed semantic finding blocks", blocked)
    reviews = tuple(
        sl.LeakageReview(
            f.finding_id, "reviewer-1", "not_leakage", "different customers, same checklist"
        )
        for f in sem.findings
    )
    reviewed = sl.apply_leakage_reviews(sem, reviews)
    for stage in ("final_freeze", "training"):
        sl.assert_leakage_clear(reviewed, stage)
        check(cid, f"{stage}: a fully reviewed not_leakage report clears", True)
    check(
        cid,
        "applying reviews returns a new report with its own hash",
        reviewed is not sem
        and reviewed.report_sha256 != sem.report_sha256
        and reviewed.reviews == reviews,
    )
    check(cid, "the original is untouched", sem.reviews == ())
    partial = sl.apply_leakage_reviews(sem, reviews[:-1]) if len(reviews) > 1 else sem
    if len(reviews) > 1:
        try:
            sl.assert_leakage_clear(partial, "training")
            still_blocked = False
        except sl.LeakageBlockedError:
            still_blocked = True
        check(cid, "one unreviewed finding left still blocks", still_blocked)
    blocked_reports = (
        scan([*solo("a", "train", 3), *solo("b", "final_qualification", 3)], route=BLOCKED_ROUTE),
        scan(
            [
                *solo("a", "train", 3),
                (record("e1", LONG_A, "q"), "train", ("x1",)),
                (record("e2", LONG_A, "q"), "final_qualification", ("x2",)),
            ],
            max_findings=1,
        ),
    )
    for status_report in blocked_reports:
        check(cid, "fixture: blocked", status_report.status == "blocked")
        for stage in ("final_freeze", "training"):
            try:
                sl.assert_leakage_clear(status_report, stage)
                cleared = True
            except sl.LeakageBlockedError as exc:
                cleared = False
                check(
                    cid,
                    "the blocked error carries the reason code",
                    exc.reason_code == status_report.reason_code,
                )
            check(cid, f"{stage}: a blocked report never clears", not cleared)
    failed = sl.detect_split_leakage(
        replace(
            allocate_splits(
                SPEC,
                [
                    SplitRecord("x", ("gx",), preassigned_role="train"),
                    SplitRecord("y", ("gy",), preassigned_role="final_qualification"),
                ],
            ),
            manifest_sha256="0" * 64,
        ),
        [record("x", "alpha", "1"), record("y", "beta", "2")],
        route=ROUTE,
    )
    for stage in ("final_freeze", "training"):
        try:
            sl.assert_leakage_clear(failed, stage)
            cleared = True
        except sl.LeakageBlockedError:
            cleared = False
        check(cid, f"{stage}: a failed report never clears", not cleared)
    for bad_stage in ("freeze", "", "TRAINING", None, "training "):
        try:
            sl.assert_leakage_clear(clean, bad_stage)  # type: ignore[arg-type]
            accepted = True
        except sl.LeakageEngineError:
            accepted = False
        check(cid, f"stage {bad_stage!r} is refused", not accepted)
    for bad in (None, "clear", {"status": "clear"}):
        try:
            sl.assert_leakage_clear(bad, "training")  # type: ignore[arg-type]
            accepted = True
        except sl.LeakageEngineError:
            accepted = False
        check(cid, f"report {bad!r} is refused", not accepted)


def test_reviews_bind_only_review_required_findings() -> None:
    cid = "LK-REVIEW"
    report = seeded_report()
    blocking = next(f for f in report.findings if f.disposition == "blocking")
    review_required = next(f for f in report.findings if f.disposition == "review_required")

    def refused(*reviews: Any, base: sl.LeakageReport = report) -> bool:
        try:
            sl.apply_leakage_reviews(base, reviews)
            return False
        except sl.LeakageEngineError:
            return True

    good = sl.LeakageReview(
        review_required.finding_id, "reviewer-1", "not_leakage", "same template only"
    )
    check(cid, "a good review is accepted", not refused(good))
    check(
        cid,
        "a review of a blocking finding is refused",
        refused(sl.LeakageReview(blocking.finding_id, "r", "not_leakage", "why")),
    )
    check(
        cid,
        "an unknown finding id is refused",
        refused(sl.LeakageReview("f" * 64, "r", "not_leakage", "why")),
    )
    check(cid, "a duplicate review in one call is refused", refused(good, good))
    once = sl.apply_leakage_reviews(report, (good,))
    check(
        cid, "a second review of an already reviewed finding is refused", refused(good, base=once)
    )
    for label, review in (
        (
            "blank reviewer",
            sl.LeakageReview(review_required.finding_id, "  ", "not_leakage", "why"),
        ),
        ("empty reviewer", sl.LeakageReview(review_required.finding_id, "", "not_leakage", "why")),
        (
            "blank rationale",
            sl.LeakageReview(review_required.finding_id, "r", "not_leakage", " \n\t"),
        ),
        ("empty rationale", sl.LeakageReview(review_required.finding_id, "r", "not_leakage", "")),
        (
            "rationale over 2000",
            sl.LeakageReview(review_required.finding_id, "r", "not_leakage", "x" * 2001),
        ),
        ("decision leakage", sl.LeakageReview(review_required.finding_id, "r", "leakage", "why")),
        ("decision blank", sl.LeakageReview(review_required.finding_id, "r", "", "why")),
        (
            "decision padded",
            sl.LeakageReview(review_required.finding_id, "r", " not_leakage", "why"),
        ),
        ("decision upper", sl.LeakageReview(review_required.finding_id, "r", "NOT_LEAKAGE", "why")),
        ("non-str reviewer", sl.LeakageReview(review_required.finding_id, 7, "not_leakage", "why")),  # type: ignore[arg-type]
        (
            "str-subclass rationale",
            sl.LeakageReview(review_required.finding_id, "r", "not_leakage", support.StrSub("why")),
        ),
        (
            "reviewer over 200",
            sl.LeakageReview(review_required.finding_id, "r" * 201, "not_leakage", "why"),
        ),
    ):
        check(cid, f"{label} is refused", refused(review))
    max_ok = sl.LeakageReview(review_required.finding_id, "r" * 200, "not_leakage", "x" * 2000)
    check(cid, "200-char reviewer and 2000-char rationale are accepted", not refused(max_ok))
    check(cid, "a non-review is refused", refused("review"))
    tampered = replace(report, report_sha256="0" * 64)
    check(
        cid,
        "reviews cannot be attached to a report whose hash does not cover it",
        refused(good, base=tampered),
    )
    check(cid, "an unknown stage word in reviews is refused", refused(object()))
    check(cid, "reviews must be a tuple/list", _refuses_collection(report, good))


def _refuses_collection(report: sl.LeakageReport, good: sl.LeakageReview) -> bool:
    try:
        sl.apply_leakage_reviews(report, "abc")  # type: ignore[arg-type]
    except sl.LeakageEngineError:
        return True
    return False


# --- input validation -------------------------------------------------------------------


def test_tampered_inputs_fail_closed() -> None:
    cid = "LK-INPUT"
    t = record("t", "alpha one", "1")
    f = record("f", "beta two", "2")
    manifest, records = split([(t, "train", ("g1",)), (f, "final_qualification", ("g2",))])
    ok = sl.detect_split_leakage(manifest, records, route=ROUTE)
    check(cid, "fixture scans", ok.status == "clear")
    tampered = replace(manifest, manifest_sha256="1" * 64)
    report = sl.detect_split_leakage(tampered, records, route=ROUTE)
    check(
        cid,
        f"a tampered manifest is failed/manifest_invalid ({report.status}/{report.reason_code})",
        report.status == "failed"
        and report.reason_code == "manifest_invalid"
        and report.findings == (),
    )
    check(
        cid,
        "and nothing was scanned",
        set(report.methods_run) == set() and set(report.methods_not_run) == set(sl.METHODS),
    )
    crossing = replace(
        manifest, assignments=(("t", "train", "g"), ("f", "final_qualification", "g"))
    )
    crossing = replace(
        crossing,
        manifest_sha256=__import__(
            "workers.data.group_aware_split_engine", fromlist=["x"]
        ).compute_manifest_sha256(crossing),
    )
    report = sl.detect_split_leakage(crossing, records, route=ROUTE)
    check(
        cid,
        "a manifest whose group crosses roles is failed/manifest_invalid",
        report.status == "failed" and report.reason_code == "manifest_invalid",
    )
    report = sl.detect_split_leakage("manifest", records, route=ROUTE)  # type: ignore[arg-type]
    check(
        cid,
        "a non-manifest is failed/manifest_invalid",
        report.status == "failed" and report.reason_code == "manifest_invalid",
    )
    extra = record("z", "gamma three", "3")
    report = sl.detect_split_leakage(manifest, [*records, extra], route=ROUTE)
    check(
        cid,
        "an extra record is failed/record_set_mismatch",
        report.status == "failed" and report.reason_code == "record_set_mismatch",
    )
    report = sl.detect_split_leakage(manifest, records[:1], route=ROUTE)
    check(
        cid,
        "a missing record is failed/record_set_mismatch",
        report.status == "failed" and report.reason_code == "record_set_mismatch",
    )
    report = sl.detect_split_leakage(
        manifest, [records[0], replace(records[1], record_id="other")], route=ROUTE
    )
    check(
        cid,
        "a renamed record is failed/record_set_mismatch",
        report.status == "failed" and report.reason_code == "record_set_mismatch",
    )
    report = sl.detect_split_leakage(manifest, [records[0], records[0]], route=ROUTE)
    check(
        cid,
        "a duplicated record id is failed/record_set_mismatch",
        report.status == "failed" and report.reason_code == "record_set_mismatch",
    )
    # the case above also drops records[1], so the set-vs-assigned mismatch alone would catch
    # it; isolate the duplicate-id check itself with the FULL record set plus one duplicate --
    # the id set still equals assigned's exactly, so only len(set(ids)) != len(ids) can trigger.
    report = sl.detect_split_leakage(manifest, [*records, records[0]], route=ROUTE)
    check(
        cid,
        "the full record set plus one duplicate is failed/record_set_mismatch "
        f"({report.status}/{report.reason_code})",
        report.status == "failed" and report.reason_code == "record_set_mismatch",
    )
    for hash_field in ("record_sha256", "normalized_sha256"):
        for bad in ("", "abc", "G" * 64, "A" * 64, "a" * 63, "a" * 65):
            broken = replace(records[0], **{hash_field: bad})
            report = sl.detect_split_leakage(manifest, [broken, records[1]], route=ROUTE)
            check(
                cid,
                f"{hash_field}={bad[:8]!r}: an issue, never silently skipped, never clear",
                report.status == "failed"
                and report.reason_code == "invalid_input"
                and any(i.record_id == "t" for i in report.issues),
            )
    broken = replace(records[0], content={"messages": []})
    report = sl.detect_split_leakage(manifest, [broken, records[1]], route=ROUTE)
    check(
        cid,
        "malformed content is an issue and the report is failed",
        report.status == "failed" and any(i.record_id == "t" for i in report.issues),
    )
    check(
        cid,
        "issues name only a code and record id",
        all(set(vars(i)) <= {"code", "record_id", "source_label"} for i in report.issues),
    )
    # typed constructors
    for label, make in (
        (
            "record_id str subclass",
            lambda: sl.LeakageRecord(
                support.StrSub("x"), records[0].content, raw_hash("a"), raw_hash("b")
            ),
        ),
        (
            "record_id empty",
            lambda: sl.LeakageRecord("", records[0].content, raw_hash("a"), raw_hash("b")),
        ),
        (
            "record_id blank",
            lambda: sl.LeakageRecord("  ", records[0].content, raw_hash("a"), raw_hash("b")),
        ),
        ("content not a mapping", lambda: sl.LeakageRecord("x", [1], raw_hash("a"), raw_hash("b"))),  # type: ignore[arg-type]
        (
            "group_keys list",
            lambda: sl.LeakageRecord("x", records[0].content, raw_hash("a"), raw_hash("b"), ["k"]),
        ),  # type: ignore[arg-type]
        (
            "group key blank",
            lambda: sl.LeakageRecord("x", records[0].content, raw_hash("a"), raw_hash("b"), (" ",)),
        ),
        (
            "group key subclass",
            lambda: sl.LeakageRecord(
                "x", records[0].content, raw_hash("a"), raw_hash("b"), (support.StrSub("k"),)
            ),
        ),
        ("hash type", lambda: sl.LeakageRecord("x", records[0].content, 5, raw_hash("b"))),  # type: ignore[arg-type]
        ("prior bad role", lambda: prior_source("c", "train", [records[0]])),
        (
            "prior role subclass",
            lambda: prior_source("c", support.StrSub("final_qualification"), [records[0]]),
        ),
        ("prior role unhashable", lambda: prior_source("c", [], [records[0]])),  # type: ignore[arg-type]
        ("prior blank label", lambda: prior_source(" ", "final_qualification", [records[0]])),
        ("prior empty records", lambda: prior_source("c", "final_qualification", [])),
        (
            "prior bad manifest hash",
            lambda: sl.PriorQualificationSource("c", "final_qualification", "zz", (records[0],)),
        ),
        (
            "prior record type",
            lambda: sl.PriorQualificationSource("c", "final_qualification", raw_hash("m"), ("x",)),
        ),  # type: ignore[arg-type]
        ("route destination", lambda: sl.EmbeddingRoute("internal", "standard", "standard")),  # type: ignore[arg-type]
        (
            "route authorization",
            lambda: sl.EmbeddingRoute(INTERNAL, "standard", "standard", None, "yes"),
        ),  # type: ignore[arg-type]
    ):
        try:
            make()
            raised = False
        except sl.LeakageEngineError:
            raised = True
        check(cid, f"{label} is refused with LeakageEngineError", raised)
    for label, call in (
        (
            "records tuple of non-records",
            lambda: sl.detect_split_leakage(manifest, ["x"], route=ROUTE),
        ),  # type: ignore[list-item]
        ("records str", lambda: sl.detect_split_leakage(manifest, "abc", route=ROUTE)),  # type: ignore[arg-type]
        ("records empty", lambda: sl.detect_split_leakage(manifest, [], route=ROUTE)),
        (
            "prior not a source",
            lambda: sl.detect_split_leakage(manifest, records, prior_sources=("x",), route=ROUTE),
        ),  # type: ignore[arg-type]
        (
            "prior duplicate label",
            lambda: sl.detect_split_leakage(
                manifest,
                records,
                prior_sources=(
                    prior_source("c", "final_qualification", [record("p", "a b", "c")]),
                    prior_source("c", "development_eval", [record("q", "d e", "f")]),
                ),
                route=ROUTE,
            ),
        ),
        (
            "thresholds type",
            lambda: sl.detect_split_leakage(manifest, records, route=ROUTE, thresholds=0.9),
        ),  # type: ignore[arg-type]
        (
            "max_findings 0",
            lambda: sl.detect_split_leakage(manifest, records, route=ROUTE, max_findings=0),
        ),
        (
            "max_findings bool",
            lambda: sl.detect_split_leakage(manifest, records, route=ROUTE, max_findings=True),
        ),  # type: ignore[arg-type]
    ):
        try:
            call()
            raised = False
        except sl.LeakageEngineError:
            raised = True
        check(cid, f"{label}: LeakageEngineError", raised)


def test_finding_limit_is_blocked_not_truncated_silently() -> None:
    cid = "LK-LIMIT"
    copies = [(record(f"t{i}", LONG_A, "z", index=i), "train", (f"gt{i}",)) for i in range(6)]
    finals = [
        (record(f"f{i}", LONG_A, "z", index=i), "final_qualification", (f"gf{i}",))
        for i in range(6)
    ]
    report = scan([*copies, *finals], max_findings=10)
    check(
        cid,
        f"6x6 identical records exceed 10 findings: blocked/finding_limit_exceeded "
        f"({report.status}/{report.reason_code}, {len(report.findings)} findings)",
        report.status == "blocked"
        and report.reason_code == "finding_limit_exceeded"
        and len(report.findings) <= 10,
    )
    full = scan([*copies, *finals], max_findings=1000)
    check(
        cid,
        "with room, every pair is reported (36 pairs x 3 hash methods)",
        len([f for f in full.findings if f.method == "exact_raw"]) == 36,
    )
    exactly = scan(
        [
            *solo("a", "train", 2),
            (record("e1", LONG_A, "q"), "train", ("x1",)),
            (record("e2", LONG_A, "q"), "final_qualification", ("x2",)),
        ],
        max_findings=len(
            scan(
                [
                    *solo("a", "train", 2),
                    (record("e1", LONG_A, "q"), "train", ("x1",)),
                    (record("e2", LONG_A, "q"), "final_qualification", ("x2",)),
                ]
            ).findings
        ),
    )
    check(
        cid,
        "a limit equal to the finding count is not exceeded",
        exactly.reason_code != "finding_limit_exceeded",
    )


# --- determinism -------------------------------------------------------------------------


def test_determinism_order_and_hash_seed() -> None:
    cid = "LK-DETERMINISM"
    t = record("t", LONG_A, "z")
    f = record("f", LONG_A, "z")
    p = record("p", LONG_B, "z")
    placed = [
        *solo("tr", "train", 4),
        (t, "train", ("g1",)),
        (f, "final_qualification", ("g2",)),
        (p, "validation", ("g3",)),
        *solo("dv", "development_eval", 2),
    ]
    base = scan(placed)
    rng = random.Random(7)
    for _ in range(4):
        shuffled = list(placed)
        rng.shuffle(shuffled)
        again = scan(shuffled)
        check(
            cid,
            "shuffled input order gives the same report hash",
            again.report_sha256 == base.report_sha256,
        )
    manifest, records = split(placed)
    reversed_records = list(reversed(records))
    check(
        cid,
        "records handed over in reverse order give the same report",
        sl.detect_split_leakage(manifest, reversed_records, route=ROUTE).report_sha256
        == base.report_sha256,
    )
    script = (
        f"import sys; sys.path.insert(0, {str(ROOT)!r}); "
        f"sys.path.insert(0, {str(ROOT / 'tests' / 'tasks')!r}); "
        "import test_ftf_0518 as t; "
        "t.FAILURES.clear(); r = t.scan(t.determinism_fixture()); print(r.report_sha256)"
    )
    digests = set()
    for seed in ("0", "1", "424242"):
        env = dict(os.environ, PYTHONHASHSEED=seed)
        out = subprocess.run(
            [sys.executable, "-c", script], env=env, capture_output=True, text=True, timeout=300
        )
        digests.add(out.stdout.strip())
        check(
            cid,
            f"PYTHONHASHSEED={seed}: the subprocess ran ({out.stderr[-200:]})",
            out.returncode == 0,
        )
    check(
        cid,
        f"the report sha256 is identical under three hash seeds ({digests})",
        len(digests) == 1 and "" not in digests,
    )
    check(
        cid,
        "and equals the in-process digest",
        digests == {scan(determinism_fixture()).report_sha256},
    )


def determinism_fixture() -> list[tuple[sl.LeakageRecord, str, tuple[str, ...]]]:
    t = record("t", LONG_A, "z")
    f = record("f", LONG_A, "z")
    p = record("p", LONG_B, "z")
    return [
        *solo("tr", "train", 4),
        (t, "train", ("g1",)),
        (f, "final_qualification", ("g2",)),
        (p, "validation", ("g3",)),
        *solo("dv", "development_eval", 2),
    ]


def test_report_integrity() -> None:
    cid = "LK-INTEGRITY"
    report = seeded_report()
    check(cid, "verify_report accepts an untouched report", sl.verify_report(report) is None)
    first = report.findings[0]
    mutations = {
        "a finding changed": replace(
            report, findings=(replace(first, record_id_b="zzz"), *report.findings[1:])
        ),
        "a finding dropped": replace(report, findings=report.findings[1:]),
        "status changed": replace(report, status="clear"),
        "reason changed": replace(report, reason_code="manifest_invalid"),
        "methods_run changed": replace(report, methods_run=()),
        "methods_not_run changed": replace(report, methods_not_run=("embedding",)),
        "issues added": replace(
            report, issues=(sl.LeakageIssue("invalid_record_hash", "x", None),)
        ),
        "reviews added": replace(
            report, reviews=(sl.LeakageReview(first.finding_id, "r", "not_leakage", "why"),)
        ),
        "score changed": replace(
            report,
            findings=(
                replace(first, scores=(("char_ngram_similarity", 0.0),)),
                *report.findings[1:],
            ),
        ),
        "disposition changed": replace(
            report, findings=(replace(first, disposition="review_required"), *report.findings[1:])
        ),
    }
    for label, broken in mutations.items():
        try:
            sl.verify_report(broken)
            accepted = True
        except sl.LeakageEngineError:
            accepted = False
        check(cid, f"{label}: the report hash no longer covers it", not accepted)
        try:
            sl.assert_leakage_clear(broken, "training")
            cleared = True
        except (sl.LeakageEngineError, sl.LeakageBlockedError):
            cleared = False
        check(cid, f"{label}: the gate refuses it", not cleared)
    forged = replace(report, status="clear", findings=())
    forged = replace(forged, report_sha256=sl.compute_report_sha256(forged))
    check(
        cid,
        "even a self-consistent forged report differs from the real one",
        forged.report_sha256 != report.report_sha256,
    )
    try:
        replace(report, status="bogus")
        status_ok = True
    except sl.LeakageEngineError:
        status_ok = False
    check(cid, "an unknown status cannot be constructed", not status_ok)
    for bad_method in ("exact", "", None, support.StrSub("group")):
        try:
            replace(first, method=bad_method)
            built = True
        except sl.LeakageEngineError:
            built = False
        check(cid, f"finding method {bad_method!r} is refused", not built)
    for field, bad in (
        ("disposition", "blocking "),
        ("reason", "other"),
        ("role_a", "test"),
        ("role_b", "x"),
        ("finding_id", "A" * 64),
        ("finding_id", "a" * 63),
    ):
        try:
            replace(first, **{field: bad})
            built = True
        except sl.LeakageEngineError:
            built = False
        check(cid, f"finding {field}={bad!r} is refused", not built)


def forged(report: sl.LeakageReport, **changes: Any) -> sl.LeakageReport:
    """`report` with fields changed and the hash recomputed over the new fields: a report a
    forger who can run this module's own hash function would produce."""
    changed = replace(report, **changes)
    return replace(changed, report_sha256=sl.compute_report_sha256(changed))


def refused(report: sl.LeakageReport) -> bool:
    try:
        sl.verify_report(report)
    except sl.LeakageEngineError:
        return True
    return False


def test_self_sealed_forgeries_are_refused() -> None:
    cid = "LK-FORGERY"
    report = seeded_report()
    blocking = next(f for f in report.findings if f.disposition == "blocking")
    soft = next(f for f in report.findings if f.disposition == "review_required")
    check(cid, "fixture: the genuine report verifies", not refused(report))
    check(
        cid,
        "fixture: the forger's hash function reproduces the genuine hash",
        forged(report).report_sha256 == report.report_sha256,
    )
    review = sl.LeakageReview(soft.finding_id, "r", "not_leakage", "why")
    clean = scan([*solo("a", "train", 4), *solo("b", "final_qualification", 2)])
    check(cid, "fixture: the clean scan verifies and has run every method", not refused(clean))
    cases = {
        "clear with findings": forged(report, status="clear"),
        "leakage_found claiming the semantic methods were skipped": forged(
            report, methods_run=sl.HASH_METHODS, methods_not_run=sl.SEMANTIC_METHODS
        ),
        "clear claiming the semantic methods were skipped": forged(
            clean, methods_run=sl.HASH_METHODS, methods_not_run=sl.SEMANTIC_METHODS
        ),
        "clear claiming nothing ran at all": forged(
            clean, methods_run=(), methods_not_run=sl.METHODS
        ),
        "leakage_found with a blocked reason": forged(
            report, reason_code="semantic_scan_saturated"
        ),
        "blocked status with no reason": forged(report, status="blocked"),
        "issues without invalid_input": forged(
            report, issues=(sl.LeakageIssue("invalid_record", "x", None),)
        ),
        "a finding with another id": forged(
            report,
            findings=(
                replace(blocking, record_id_b="someone-else"),
                *[f for f in report.findings if f is not blocking],
            ),
        ),
        "a blocking finding downgraded": forged(
            report,
            findings=(
                replace(blocking, disposition="review_required"),
                *[f for f in report.findings if f is not blocking],
            ),
        ),
        "a review_required finding upgraded": forged(
            report,
            findings=(
                replace(soft, disposition="blocking"),
                *[f for f in report.findings if f is not soft],
            ),
        ),
        "a finding repeated": forged(report, findings=(*report.findings, report.findings[0])),
        "a review of a blocking finding": forged(
            report, reviews=(sl.LeakageReview(blocking.finding_id, "r", "not_leakage", "why"),)
        ),
        "a review of an unknown finding": forged(
            report, reviews=(sl.LeakageReview("e" * 64, "r", "not_leakage", "why"),)
        ),
        "a finding reviewed twice": forged(report, reviews=(review, review)),
        "a blank rationale": forged(
            report, reviews=(sl.LeakageReview(soft.finding_id, "r", "not_leakage", " "),)
        ),
        "a review decision of leakage": forged(
            report, reviews=(sl.LeakageReview(soft.finding_id, "r", "leakage", "why"),)
        ),
    }
    for label, bad in cases.items():
        check(cid, f"{label}: refused even though the hash covers it", refused(bad))
        try:
            sl.assert_leakage_clear(bad, "training")
            cleared = True
        except (sl.LeakageEngineError, sl.LeakageBlockedError):
            cleared = False
        check(cid, f"{label}: the gate refuses it", not cleared)
    # the property itself never trusts a review of a blocking finding
    loose = replace(
        report, reviews=(sl.LeakageReview(blocking.finding_id, "r", "not_leakage", "why"),)
    )
    check(
        cid,
        "unreviewed_findings keeps a blocking finding even if a review names it",
        blocking in loose.unreviewed_findings and soft in loose.unreviewed_findings,
    )
    check(
        cid,
        "and drops a review_required finding that was reviewed",
        soft not in replace(report, reviews=(review,)).unreviewed_findings
        and blocking in replace(report, reviews=(review,)).unreviewed_findings,
    )
    # unsealed edits of fields that only the hash binds
    blocked = scan(
        [*solo("a", "train", 3), *solo("b", "final_qualification", 3)], route=BLOCKED_ROUTE
    )
    check(cid, "fixture: a blocked report verifies", not refused(blocked))
    check(
        cid,
        "a blocked report's reason cannot change to another blocked reason",
        refused(replace(blocked, reason_code="security_profile_route_not_authorized")),
    )
    check(cid, "nor to a failed one", refused(replace(blocked, reason_code="manifest_invalid")))
    check(cid, "nor to none", refused(replace(blocked, reason_code=None)))
    bad_hash = replace(records_for_issue(), report_sha256="0" * 64)
    invalid = records_for_issue()
    check(cid, "fixture: a failed/invalid_input report verifies", not refused(invalid))
    check(
        cid,
        "its issues cannot change",
        refused(replace(invalid, issues=(sl.LeakageIssue("invalid_content", "t", None),))),
    )
    check(cid, "nor vanish", refused(replace(invalid, issues=())))
    check(cid, "and a wrong hash is refused", refused(bad_hash))
    check(
        cid,
        "a hash with non-ASCII characters is refused, not a crash",
        refused(replace(report, report_sha256="\u00e9" * 64)),
    )
    check(cid, "an empty hash is refused", refused(replace(report, report_sha256="")))
    check(
        cid,
        "an uppercase copy of the right hash is refused",
        refused(replace(report, report_sha256=report.report_sha256.upper())),
    )


def records_for_issue() -> sl.LeakageReport:
    t = record("t", "alpha one", "1")
    f = record("f", "beta two", "2")
    manifest, recs = split([(t, "train", ("g1",)), (f, "final_qualification", ("g2",))])
    return sl.detect_split_leakage(
        manifest, [replace(recs[0], record_sha256="nope"), recs[1]], route=ROUTE
    )


def test_constructors_validate_their_fields() -> None:
    cid = "LK-CONSTRUCT"
    report = seeded_report()
    first = report.findings[0]
    soft = next(f for f in report.findings if f.scores)

    def built(make: Any) -> bool:
        try:
            make()
        except sl.LeakageEngineError:
            return False
        return True

    for label, scores in (
        ("scores a list", [("char_ngram_similarity", 0.5)]),
        ("an entry a list", (["char_ngram_similarity", 0.5],)),
        ("an entry of three", (("char_ngram_similarity", 0.5, 1),)),
        ("an entry of one", (("char_ngram_similarity",),)),
        ("a name that is not text", ((7, 0.5),)),
        ("a value that is an int", (("char_ngram_similarity", 1),)),
        ("a value that is a bool", (("char_ngram_similarity", True),)),
        ("a value that is text", (("char_ngram_similarity", "0.5"),)),
    ):
        check(
            cid,
            f"finding {label} is refused",
            not built(lambda scores=scores: replace(soft, scores=scores)),
        )
    check(
        cid,
        "a finding with proper float scores is built",
        built(lambda: replace(soft, scores=(("a", 0.5),))),
    )
    check(cid, "and with none", built(lambda: replace(first, scores=())))
    for field, bad in (
        ("record_id_a", ""),
        ("record_id_b", " "),
        ("record_id_a", support.StrSub("x")),
        ("prior_source_label", ""),
        ("prior_source_label", 7),
        ("finding_id", 7),
    ):
        check(
            cid,
            f"finding {field}={bad!r} is refused",
            not built(lambda f=field, b=bad: replace(first, **{f: b})),
        )
    check(
        cid,
        "a finding's own label may be None or text",
        built(lambda: replace(first, prior_source_label=None))
        and built(lambda: replace(first, prior_source_label="c1")),
    )
    for label, make in (
        ("issue code", lambda: sl.LeakageIssue("other", "x", None)),
        (
            "issue code subclass",
            lambda: sl.LeakageIssue(support.StrSub("invalid_record"), "x", None),
        ),
        ("issue code unhashable", lambda: sl.LeakageIssue([], "x", None)),
        ("issue record_id int", lambda: sl.LeakageIssue("invalid_record", 5, None)),
        ("issue record_id blank", lambda: sl.LeakageIssue("invalid_record", " ", None)),
        ("issue label int", lambda: sl.LeakageIssue("invalid_record", "x", 5)),
        ("issue label blank", lambda: sl.LeakageIssue("invalid_record", "x", "")),
    ):
        check(cid, f"{label} is refused", not built(make))
    check(
        cid,
        "an issue may carry no ids",
        built(lambda: sl.LeakageIssue("invalid_record", None, None)),
    )
    for label, changes in (
        ("findings a list", {"findings": list(report.findings)}),
        ("findings with a non-finding", {"findings": ("x",)}),
        ("issues a list", {"issues": [sl.LeakageIssue("invalid_record", None, None)]}),
        ("issues with a non-issue", {"issues": ("x",)}),
        ("reviews a list", {"reviews": []}),
        ("reviews with a non-review", {"reviews": ("x",)}),
        ("methods_run a list", {"methods_run": list(sl.METHODS)}),
        ("methods_run unknown", {"methods_run": ("bogus",)}),
        ("methods_not_run a string", {"methods_not_run": "embedding"}),
        ("methods_not_run unknown", {"methods_not_run": ("bogus",)}),
        ("report_sha256 an int", {"report_sha256": 5}),
        ("reason_code unknown", {"reason_code": "bogus"}),
        ("status subclass", {"status": support.StrSub("clear")}),
    ):
        check(cid, f"report {label} is refused", not built(lambda c=changes: replace(report, **c)))
    for label, make in (
        (
            "a source with two records of one id",
            lambda: prior_source(
                "c", "final_qualification", [record("dup", "a b", "c"), record("dup", "d e", "f")]
            ),
        ),
    ):
        check(cid, f"{label} is refused", not built(make))
    check(
        cid,
        "a source with distinct ids is built",
        built(
            lambda: prior_source(
                "c", "final_qualification", [record("p1", "a b", "c"), record("p2", "d e", "f")]
            )
        ),
    )
    t = record("t", "alpha one", "1")
    f = record("f", "beta two", "2")
    manifest, recs = split([(t, "train", ("g1",)), (f, "final_qualification", ("g2",))])
    edge = 1_000_000
    check(
        cid,
        "max_findings and max_semantic_partners accept 1,000,000",
        built(
            lambda: sl.detect_split_leakage(
                manifest, recs, route=ROUTE, max_findings=edge, max_semantic_partners=edge
            )
        ),
    )
    check(
        cid,
        "max_findings refuses 1,000,001",
        not built(
            lambda: sl.detect_split_leakage(manifest, recs, route=ROUTE, max_findings=edge + 1)
        ),
    )
    check(
        cid,
        "max_semantic_partners refuses 1,000,001",
        not built(
            lambda: sl.detect_split_leakage(
                manifest, recs, route=ROUTE, max_semantic_partners=edge + 1
            )
        ),
    )
    check(
        cid,
        "max_findings accepts 1",
        built(lambda: sl.detect_split_leakage(manifest, recs, route=ROUTE, max_findings=1)),
    )


def test_prior_sources_are_only_compared_with_current_records() -> None:
    cid = "LK-PRIOR-PAIRS"
    same_a = record("p", LONG_A, "z")
    same_b = record("q", LONG_A, "z")
    report = scan(
        [*solo("tr", "train", 3), *solo("fi", "final_qualification", 2)],
        prior=(
            prior_source("c1", "final_qualification", [same_a]),
            prior_source("c2", "development_eval", [same_b]),
        ),
    )
    check(
        cid,
        f"identical records in two prior sources are not leakage ({report.status})",
        report.status == "clear" and report.findings == (),
    )
    keyed_a = record("pa", "alpha prior one", "z", keys=("shared-prior",))
    keyed_b = record("pb", "beta prior two", "z", keys=("shared-prior",))
    report = scan(
        solo("tr", "train", 3),
        prior=(
            prior_source("c1", "final_qualification", [keyed_a]),
            prior_source("c2", "final_qualification", [keyed_b]),
        ),
    )
    check(cid, "nor are keys shared by two prior sources", report.status == "clear")
    duplicate = prior_source(
        "c1",
        "final_qualification",
        [record("Dup", "a b", "c"), record("dup", "d e", "f"), record(" dup", "g h", "i")],
    )
    check(
        cid,
        "record ids that differ by case or padding are distinct in a prior source",
        len(duplicate.records) == 3,
    )
    both = scan(
        [*solo("tr", "train", 2), (record("cur", LONG_A, "z"), "train", ("gc",))],
        prior=(
            prior_source("c1", "final_qualification", [record("old", LONG_A, "z")]),
            prior_source("c2", "development_eval", [record("old", LONG_A, "z")]),
        ),
    )
    check(
        cid,
        "the same prior id in two sources gives separate findings per source",
        {f.prior_source_label for f in both.findings} == {"c1", "c2"},
    )
    check(
        cid,
        "and the current record is always side a",
        all(f.record_id_a == "cur" and f.record_id_b == "old" for f in both.findings),
    )


def test_reviews_must_be_a_real_sequence() -> None:
    cid = "LK-REVIEW-SEQ"
    report = semantic_only_report()
    reviews = [sl.LeakageReview(f.finding_id, "r", "not_leakage", "why") for f in report.findings]

    def refused_call(arg: Any) -> bool:
        try:
            sl.apply_leakage_reviews(report, arg)
            return False
        except sl.LeakageEngineError:
            return True

    check(cid, "fixture: there is something to review", len(reviews) >= 1)
    check(cid, "a list is accepted", not refused_call(reviews))
    check(cid, "a tuple is accepted", not refused_call(tuple(reviews)))
    check(cid, "a generator of valid reviews is refused", refused_call(r for r in reviews))
    check(
        cid,
        "a set of valid reviews is refused",
        refused_call({r.finding_id: r for r in reviews}.values()),
    )
    check(cid, "a string is refused", refused_call("abc"))
    check(cid, "None is refused", refused_call(None))
    for decision in (" not_leakage", "not_leakage ", "NOT_LEAKAGE", "Not_Leakage", "not leakage"):
        check(
            cid,
            f"decision {decision!r} is refused",
            refused_call([sl.LeakageReview(report.findings[0].finding_id, "r", decision, "why")]),
        )


def test_group_findings_are_bounded_and_deduplicated() -> None:
    cid = "LK-GROUP-LIMIT"
    left = [
        (
            record(
                f"l{i}", f"left unrelated words number {i} aaa{i}", "x", keys=("shared",), index=i
            ),
            "train",
            (f"gl{i}",),
        )
        for i in range(6)
    ]
    right = [
        (
            record(
                f"r{i}", f"right other phrases count {i} bbb{i}", "y", keys=("shared",), index=i
            ),
            "final_qualification",
            (f"gr{i}",),
        )
        for i in range(6)
    ]
    full = scan([*left, *right], max_findings=1000)
    groups = [f for f in full.findings if f.method == "group"]
    check(
        cid,
        f"every cross-role pair of one shared key is found ({len(groups)}/36)",
        len(groups) == 36,
    )
    check(
        cid,
        "and none is invented between records of one role",
        all({f.role_a, f.role_b} == {"train", "final_qualification"} for f in groups),
    )
    limited = scan([*left, *right], max_findings=5)
    check(
        cid,
        f"over the limit: blocked/finding_limit_exceeded with at most 5 findings "
        f"({limited.status}/{limited.reason_code}/{len(limited.findings)})",
        limited.reason_code == "finding_limit_exceeded" and 0 < len(limited.findings) <= 5,
    )
    check(
        cid,
        "the group stage stopped at the limit rather than running on",
        len([f for f in limited.findings if f.method == "group"]) == len(limited.findings),
    )
    a = record("a", "first unrelated sentence about turbines", "x", keys=("k1", "k2"))
    b = record("b", "second unrelated sentence about harbours", "y", keys=("k1", "k2"))
    twice = scan(
        [(a, "train", ("ga",)), (b, "final_qualification", ("gb",)), *solo("pad", "validation", 2)],
        max_findings=1,
    )
    check(
        cid,
        f"two shared keys are one finding, and one finding fits a limit of 1 "
        f"({twice.status}/{twice.reason_code}, {len(twice.findings)})",
        twice.reason_code is None and [f.method for f in twice.findings] == ["group"],
    )
    same_key_twice = scan(
        [(a, "train", ("ga",)), (b, "final_qualification", ("gb",)), *solo("pad", "validation", 2)]
    )
    check(
        cid,
        "the same pair is never reported twice for a method",
        len([f for f in same_key_twice.findings if f.method == "group"]) == 1,
    )
    over = scan(
        [(a, "train", ("ga",)), (b, "final_qualification", ("gb",)), *solo("pad", "validation", 2)],
        max_findings=1,
    )
    check(
        cid,
        "no false limit when an existing finding is met again at the limit",
        over.reason_code is None,
    )


def test_methods_catalog() -> None:
    cid = "LK-CATALOG"
    check(
        cid,
        "eight methods in the §10 order",
        sl.METHODS
        == (
            "exact_raw",
            "canonical_normalized",
            "text_normalized",
            "group",
            "char_ngram",
            "token_jaccard",
            "embedding",
            "agent_trace_structural",
        ),
    )
    check(cid, "four semantic methods", sl.SEMANTIC_METHODS == sl.METHODS[4:])
    check(cid, "two gate stages", sl.STAGES == ("final_freeze", "training"))
    check(cid, "four statuses", sl.STATUSES == ("clear", "leakage_found", "blocked", "failed"))


def test_wiring_ci_runs_this_test() -> None:
    ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    check(
        "LK-WIRING",
        "ci.yml runs tests/tasks/test_ftf_0518.py",
        "tests/tasks/test_ftf_0518.py" in ci,
    )


def main() -> int:
    tests = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_") and callable(f)]
    for name, fn in tests:
        try:
            fn()
        except Exception as exc:  # noqa: BLE001 -- a crash is a failed test, reported by name
            FAILURES.append(f"FAIL [FTF-0518] {name} raised {type(exc).__name__}: {exc}")
    if FAILURES:
        for failure in FAILURES:
            print(failure)
        print(f"{len(FAILURES)} check(s) failed")
        return 1
    print("all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
