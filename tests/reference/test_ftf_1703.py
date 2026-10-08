#!/usr/bin/env python3
"""FTF-1703 -- Training dataset (no-DB unit/fault tests).

Criteria (ids used in failure output):
  FTF-1703-A1  >=1,000 traces, one independent group each, synthetic provenance,
               every tool result replays against the real backend.
  FTF-1703-A2  "approved" requires an independent review; with no reviews the
               floor is NOT met (BLOCKED_EXTERNAL review), at exactly 1,000
               approved it is met, at 999 it is not.
  FTF-1703-N1  no harvested/final/validation purpose, no secrets, no overlap
               with the sealed final suite's groups/templates/entities/answers.
  FTF-1703-D1  deterministic dataset hash (also across PYTHONHASHSEED).

Run directly: python3 tests/reference/test_ftf_1703.py
"""

from __future__ import annotations

import dataclasses
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, cast

ROOT = Path(__file__).resolve().parent.parent.parent
API_ROOT = ROOT / "apps" / "api"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(API_ROOT))

from evals.reference.training_dataset import (  # noqa: E402
    GENERATOR_ID,
    MIN_APPROVED_TRACES,
    MIN_INDEPENDENT_GROUPS,
    HeldOutLeakageError,
    ReservedFinalSuite,
    ReviewDecision,
    ReviewIndependenceError,
    SecretInTraceError,
    TraceVerificationError,
    TrainingDatasetError,
    build_training_dataset,
    generate_training_traces,
    verify_trace_provenance,
    verify_trace_replay,
)

FAILURES: list[str] = []


def check(criterion_id: str, description: str, condition: bool) -> None:
    if not condition:
        FAILURES.append(f"FAIL [{criterion_id}] {description}")


def _raises(exc_type: type[BaseException], fn, *args: object, **kwargs: object) -> bool:
    try:
        fn(*args, **kwargs)
        return False
    except exc_type:
        return True


TRACES = generate_training_traces()


def _approve(traces, reviewer: str = "reviewer:alice"):
    return [ReviewDecision(t.trace_id, reviewer, "approved") for t in traces]


def _replace_step(trace, index: int, step: dict):
    steps = list(trace.steps)
    steps[index] = step
    return dataclasses.replace(trace, steps=tuple(steps))


def test_generation_shape() -> None:
    check("FTF-1703-A1", "1200 traces generated", len(TRACES) == 1200)
    check("FTF-1703-A1", "1200 independent groups", len({t.group_id for t in TRACES}) == 1200)
    check("FTF-1703-A1", "unique entity titles", len({t.entity_title for t in TRACES}) == 1200)
    check(
        "FTF-1703-A1",
        "all synthetic generator provenance",
        all(
            t.provenance["origin"] == "synthetic_generator" and t.provenance["label"] == "synthetic"
            for t in TRACES
        ),
    )
    kinds = {t.kind for t in TRACES}
    check("FTF-1703-A1", "all seven scenario kinds present", len(kinds) == 7)
    crit = sum(1 for t in TRACES if t.critical)
    check(
        "FTF-1703-A1",
        "critical traces exactly 3 of 7 kinds",
        crit == sum(1 for i in range(1200) if i % 7 in (4, 5, 6)),
    )
    import re

    uuid_re = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
    check(
        "FTF-1703-A1",
        "server uuids replaced by symbolic refs",
        not any(uuid_re.search(str(t.to_dict())) for t in TRACES),
    )


def test_real_backend_results() -> None:
    by_kind = {t.kind: t for t in TRACES[:7]}
    approved = by_kind["approval_approved"]
    executed = [
        s
        for s in approved.steps
        if s["type"] == "tool_result" and s["tool"] == "execute_approved_action"
    ]
    check(
        "FTF-1703-A1",
        "approved flow really executed",
        len(executed) == 1 and executed[0]["result"]["status"] == "executed",
    )
    denied = by_kind["approval_denied"]
    check(
        "FTF-1703-A1",
        "denied flow: real ReferenceDenialError, no execution result",
        any(
            s["type"] == "tool_error" and s["error_type"] == "ReferenceDenialError"
            for s in denied.steps
        )
        and not any(
            s["type"] == "tool_result" and s["tool"] == "execute_approved_action"
            for s in denied.steps
        ),
    )
    cross = by_kind["cross_subject"]
    check(
        "FTF-1703-A1",
        "cross-subject: real ReferenceAuthorizationError",
        any(
            s["type"] == "tool_error" and s["error_type"] == "ReferenceAuthorizationError"
            for s in cross.steps
        ),
    )
    retry = by_kind["note_retry"]
    ids = [
        s["result"]["note_id"]
        for s in retry.steps
        if s["type"] == "tool_result" and s["tool"] == "create_note"
    ]
    check(
        "FTF-1703-A1",
        "retry returns the same note id (idempotency)",
        len(ids) == 2 and ids[0] == ids[1],
    )


def test_replay_rejects_tampering() -> None:
    t = TRACES[3]  # approval_approved
    i = next(
        k
        for k, s in enumerate(t.steps)
        if s["type"] == "tool_result" and s["tool"] == "execute_approved_action"
    )
    forged = _replace_step(
        t,
        i,
        {
            "type": "tool_result",
            "tool": "execute_approved_action",
            "result": {"approval_id": "appr-0", "record_id": "rec-0", "status": "failed"},
        },
    )
    check(
        "FTF-1703-A1",
        "forged result refused by replay",
        _raises(TraceVerificationError, verify_trace_replay, forged),
    )
    denied = TRACES[4]
    j = next(k for k, s in enumerate(denied.steps) if s["type"] == "tool_error")
    fake_ok = _replace_step(
        denied,
        j,
        {
            "type": "tool_result",
            "tool": "execute_approved_action",
            "result": {"approval_id": "appr-0", "record_id": "rec-0", "status": "executed"},
        },
    )
    check(
        "FTF-1703-A1",
        "fabricated grant on denied trace refused",
        _raises(TraceVerificationError, verify_trace_replay, fake_ok),
    )
    check(
        "FTF-1703-A1",
        "builder itself refuses a forged trace (replay is on the admission path)",
        _raises(TraceVerificationError, build_training_dataset, [forged], _approve([forged])),
    )
    wrong_outcome = dataclasses.replace(TRACES[0], expected_outcome="action_executed")
    check(
        "FTF-1703-A1",
        "wrong declared outcome refused",
        _raises(TraceVerificationError, verify_trace_replay, wrong_outcome),
    )
    lie = _replace_step(
        denied,
        len(denied.steps) - 1,
        {"type": "message", "role": "assistant", "content": "Done, archived."},
    )
    check(
        "FTF-1703-A1",
        "final answer contradicting the verified outcome refused",
        _raises(TraceVerificationError, verify_trace_replay, lie),
    )
    check(
        "FTF-1703-A1",
        "non-trace refused",
        _raises(TraceVerificationError, verify_trace_replay, {"x": 1}),
    )
    check(
        "FTF-1703-A1",
        "unhashable outcome-free step type refused",
        _raises(
            TraceVerificationError,
            verify_trace_replay,
            dataclasses.replace(TRACES[0], steps=({"type": []},)),
        ),
    )


def test_floor_requires_independent_review() -> None:
    none = build_training_dataset(TRACES)
    check(
        "FTF-1703-A2",
        "no reviews => zero approved",
        none.approved_count == 0 and none.pending_count == 1200,
    )
    check("FTF-1703-A2", "no reviews => floor NOT met", not none.meets_floor)
    check(
        "FTF-1703-A2",
        "no reviews => blocked status",
        none.review_status == "BLOCKED_EXTERNAL_independent_review",
    )
    full = build_training_dataset(TRACES, _approve(TRACES))
    check(
        "FTF-1703-A2",
        "all 1200 approved, but only 21 template families => group floor NOT met",
        full.approved_count == 1200
        and full.group_count == 21
        and not full.meets_floor
        and full.review_status == "BLOCKED_independent_group_floor",
    )
    check(
        "FTF-1703-A2",
        "critical count carried",
        full.critical_count == sum(1 for t in TRACES if t.critical),
    )
    exact = build_training_dataset(TRACES[:1000], _approve(TRACES[:1000]))
    check(
        "FTF-1703-A2",
        "exactly 1000 approved traces still do not meet the independent-group floor",
        exact.approved_count == 1000
        and not exact.meets_floor
        and MIN_APPROVED_TRACES == 1000
        and MIN_INDEPENDENT_GROUPS == 1000,
    )
    short = build_training_dataset(TRACES[:1000], _approve(TRACES[:999]))
    check(
        "FTF-1703-A2",
        "999 approved does not meet floor",
        not short.meets_floor and short.pending_count == 1,
    )
    rej = build_training_dataset(
        TRACES[:1000],
        _approve(TRACES[:999]) + [ReviewDecision(TRACES[999].trace_id, "reviewer:bob", "rejected")],
    )
    check(
        "FTF-1703-A2",
        "rejected trace excluded and counted",
        rej.rejected_count == 1 and not rej.meets_floor,
    )


def test_floor_counts_groups_not_traces() -> None:
    ds = build_training_dataset(TRACES[:1000], _approve(TRACES[:1000]))
    check(
        "FTF-1703-A2",
        "1000 approved traces are 21 template-family groups, not 1000 groups",
        ds.approved_count == 1000 and ds.group_count == 21 and not ds.meets_floor,
    )
    one_kind = [t for t in TRACES if t.template_id == "train-tmpl-lookup-v0"]
    only = build_training_dataset(one_kind, _approve(one_kind))
    check(
        "FTF-1703-A2",
        "many traces of one template family are one group",
        only.approved_count == len(one_kind) > 50 and only.group_count == 1,
    )
    check(
        "FTF-1703-A2",
        "no reviews => no approved traces => zero groups",
        build_training_dataset(TRACES[:30]).group_count == 0,
    )
    check(
        "FTF-1703-A2",
        "the generator's 1200 traces fall into exactly 21 template families",
        len({t.template_id for t in TRACES}) == 21,
    )


def test_floor_status_boundaries() -> None:
    import evals.reference.training_dataset as td

    fs = td._floor_status
    check(
        "FTF-1703-A2",
        "999 approved: review-blocked",
        fs(999, 5000) == (False, "BLOCKED_EXTERNAL_independent_review"),
    )
    check(
        "FTF-1703-A2",
        "1000 approved, 999 groups: group-blocked",
        fs(1000, 999) == (False, "BLOCKED_independent_group_floor"),
    )
    check(
        "FTF-1703-A2",
        "1000 approved, 1000 groups: floor met",
        fs(1000, 1000) == (True, "independently_approved"),
    )
    check(
        "FTF-1703-A2",
        "both short: the review blocker is named first",
        fs(0, 0) == (False, "BLOCKED_EXTERNAL_independent_review"),
    )


def test_review_independence() -> None:
    t = TRACES[0]
    for reviewer in (GENERATOR_ID, "generator:other", "  GENERATOR:X  "):
        check(
            "FTF-1703-A2",
            f"generator-identity reviewer {reviewer!r} refused",
            _raises(
                ReviewIndependenceError,
                build_training_dataset,
                [t],
                [ReviewDecision(t.trace_id, reviewer, "approved")],
            ),
        )
    check(
        "FTF-1703-A2",
        "case-variant producer id refused",
        _raises(
            ReviewIndependenceError,
            build_training_dataset,
            [t],
            [ReviewDecision(t.trace_id, GENERATOR_ID.upper(), "approved")],
        ),
    )
    check(
        "FTF-1703-A2",
        "unknown decision refused",
        _raises(
            ReviewIndependenceError,
            build_training_dataset,
            [t],
            [ReviewDecision(t.trace_id, "reviewer:a", "maybe")],
        ),
    )
    check(
        "FTF-1703-A2",
        "unhashable decision refused with module error, not TypeError",
        _raises(
            ReviewIndependenceError,
            build_training_dataset,
            [t],
            [ReviewDecision(t.trace_id, "reviewer:a", cast(Any, []))],
        ),
    )
    check(
        "FTF-1703-A2",
        "review of unknown trace refused",
        _raises(
            ReviewIndependenceError,
            build_training_dataset,
            [t],
            [ReviewDecision("nope", "reviewer:a", "approved")],
        ),
    )
    check(
        "FTF-1703-A2",
        "conflicting decisions refused",
        _raises(
            ReviewIndependenceError,
            build_training_dataset,
            [t],
            [
                ReviewDecision(t.trace_id, "reviewer:a", "approved"),
                ReviewDecision(t.trace_id, "reviewer:b", "rejected"),
            ],
        ),
    )
    check(
        "FTF-1703-A2",
        "non-ReviewDecision refused",
        _raises(ReviewIndependenceError, build_training_dataset, [t], [("x", "y", "approved")]),
    )
    check(
        "FTF-1703-A2",
        "empty reviewer refused",
        _raises(
            ReviewIndependenceError,
            build_training_dataset,
            [t],
            [ReviewDecision(t.trace_id, "", "approved")],
        ),
    )


def test_no_harvest_no_secrets() -> None:
    t = TRACES[0]
    for purpose in ("validation", "final", "evaluation", ""):
        bad = dataclasses.replace(t, purpose=purpose)
        check(
            "FTF-1703-N1",
            f"purpose {purpose!r} refused",
            _raises(TrainingDatasetError, build_training_dataset, [bad]),
        )
    for origin in ("repository", "production_log", "Synthetic_Generator"):
        bad = dataclasses.replace(t, provenance={**t.provenance, "origin": origin})
        check(
            "FTF-1703-N1",
            f"origin {origin!r} refused",
            _raises(TrainingDatasetError, build_training_dataset, [bad]),
        )
    bad = dataclasses.replace(t, provenance={k: v for k, v in t.provenance.items() if k != "label"})
    check(
        "FTF-1703-N1",
        "unlabelled provenance refused",
        _raises(TrainingDatasetError, build_training_dataset, [bad]),
    )
    out_ns = dataclasses.replace(t, group_id="final-grp-0001")
    check(
        "FTF-1703-N1",
        "group outside training namespace refused",
        _raises(TrainingDatasetError, build_training_dataset, [out_ns]),
    )
    dup = [t, t]
    check(
        "FTF-1703-N1",
        "duplicate trace id refused",
        _raises(TrainingDatasetError, build_training_dataset, dup),
    )
    for secret in (
        "sk-abcdefghijklmnopqrstuvwx",
        "AKIAABCDEFGHIJKLMNOP",
        "password: hunter2hunter2",
        "-----BEGIN RSA PRIVATE KEY-----",
        "Bearer abcdefghijklmnopqrstuv",
    ):
        leaked = _replace_step(
            t, 0, {"type": "message", "role": "user", "content": f"use {secret} please"}
        )
        check(
            "FTF-1703-N1",
            f"secret {secret[:8]!r} refused",
            _raises(SecretInTraceError, build_training_dataset, [leaked]),
        )
    clean = build_training_dataset(TRACES[:50], _approve(TRACES[:50]))
    check(
        "FTF-1703-N1",
        "default synthetic traces carry no secret-shaped text",
        clean.approved_count == 50,
    )


def test_final_suite_disjoint() -> None:
    t = TRACES[0]
    answer_hash = __import__("hashlib").sha256(t.final_answer().encode()).hexdigest()
    cases = {
        "group": ReservedFinalSuite(group_ids=frozenset({t.group_id})),
        "template": ReservedFinalSuite(template_ids=frozenset({t.template_id})),
        "entity": ReservedFinalSuite(entity_titles=frozenset({t.entity_title})),
        "answer": ReservedFinalSuite(answer_sha256s=frozenset({answer_hash})),
    }
    for name, reserved in cases.items():
        check(
            "FTF-1703-N1",
            f"overlap on {name} refused",
            _raises(HeldOutLeakageError, build_training_dataset, [t], [], reserved=reserved),
        )
    ok = ReservedFinalSuite(
        group_ids=frozenset({"final-grp-1"}),
        template_ids=frozenset({"final-tmpl-1"}),
        entity_titles=frozenset({"Final thing FIN-1"}),
    )
    ds = build_training_dataset(TRACES[:20], _approve(TRACES[:20]), reserved=ok)
    check(
        "FTF-1703-N1",
        "default traces share nothing with a final-namespace suite",
        ds.approved_count == 20,
    )
    check(
        "FTF-1703-N1",
        "every default group/template is in the train namespace",
        all(
            x.group_id.startswith("train-grp-") and x.template_id.startswith("train-tmpl-")
            for x in TRACES
        ),
    )


def test_determinism() -> None:
    a = build_training_dataset(TRACES, _approve(TRACES)).dataset_sha256
    b = build_training_dataset(generate_training_traces(), _approve(TRACES)).dataset_sha256
    check("FTF-1703-D1", "regenerated dataset hash identical", a == b)
    shuffled = build_training_dataset(list(reversed(TRACES)), _approve(TRACES)).dataset_sha256
    check("FTF-1703-D1", "hash independent of input order", a == shuffled)
    c = build_training_dataset([TRACES[1]], _approve([TRACES[1]])).dataset_sha256
    d = build_training_dataset([TRACES[0]], _approve([TRACES[0]])).dataset_sha256
    check("FTF-1703-D1", "different approved traces give a different dataset hash", c != d)
    changed = _replace_step(
        TRACES[0], 0, {"type": "message", "role": "user", "content": "different"}
    )
    check(
        "FTF-1703-D1",
        "a one-message user-text change is refused, so it can never change the hash",
        _raises(TraceVerificationError, build_training_dataset, [changed], _approve([changed])),
    )
    code = (
        f"import sys; sys.path.insert(0, {str(ROOT)!r}); sys.path.insert(0, {str(API_ROOT)!r});"
        "from evals.reference.training_dataset import *;"
        "t=generate_training_traces(count=60);"
        "r=[ReviewDecision(x.trace_id,'reviewer:a','approved') for x in t];"
        "print(build_training_dataset(t,r).dataset_sha256)"
    )
    hashes = set()
    for seed in ("0", "777"):
        out = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
            env={**os.environ, "PYTHONHASHSEED": seed},
            check=True,
        ).stdout.strip()
        hashes.add(out)
    check("FTF-1703-D1", "hash identical under PYTHONHASHSEED 0 and 777", len(hashes) == 1)
    check(
        "FTF-1703-D1",
        "bad count refused",
        all(
            _raises(TrainingDatasetError, generate_training_traces, count=c)
            for c in (0, -1, True, "5")
        ),
    )


def _mk_steps(*steps: dict):
    return list(steps)


def _res(tool: str, **result):
    return {"type": "tool_result", "tool": tool, "result": result}


def _err(tool: str, error_type: str):
    return {"type": "tool_error", "tool": tool, "error_type": error_type}


def test_outcome_predicates_directly() -> None:
    import evals.reference.training_dataset as td

    oh = td._outcome_holds
    note = lambda n: _res("create_note", note_id=n, record_id="rec-0")  # noqa: E731
    check(
        "FTF-1703-A1",
        "note_once: same id twice holds",
        oh("note_created_once", [note("n0"), note("n0")]),
    )
    check(
        "FTF-1703-A1",
        "note_once: differing ids fail",
        not oh("note_created_once", [note("n0"), note("n1")]),
    )
    check("FTF-1703-A1", "note_once: single note fails", not oh("note_created_once", [note("n0")]))
    check(
        "FTF-1703-A1",
        "note_created: needs a note",
        oh("note_created", [note("n0")]) and not oh("note_created", []),
    )
    ex = _res("execute_approved_action", approval_id="a", record_id="r", status="executed")
    den = _err("execute_approved_action", "ReferenceDenialError")
    check("FTF-1703-A1", "refused: denial only holds", oh("action_refused_denied", [den]))
    check(
        "FTF-1703-A1",
        "refused: denial plus execution fails",
        not oh("action_refused_denied", [den, ex]),
    )
    check(
        "FTF-1703-A1",
        "refused: neither denial nor execution fails",
        not oh("action_refused_pending", []),
    )
    check("FTF-1703-A1", "refused: execution only fails", not oh("action_refused_pending", [ex]))
    check(
        "FTF-1703-A1",
        "refused: wrong error type fails",
        not oh(
            "action_refused_pending",
            [_err("execute_approved_action", "ReferenceAuthorizationError")],
        ),
    )
    check(
        "FTF-1703-A1",
        "executed: needs executed status",
        oh("action_executed", [ex]) and not oh("action_executed", []),
    )
    auth = _err("get_record", "ReferenceAuthorizationError")
    other = _res("search_records", records=[])
    got = _res("get_record", record_id="r", title="t", status="open", notes=[])
    check(
        "FTF-1703-A1",
        "access: refusal holds even beside other tools' results",
        oh("access_refused", [other, auth]),
    )
    check(
        "FTF-1703-A1",
        "access: refusal plus a successful read fails",
        not oh("access_refused", [auth, got]),
    )
    check("FTF-1703-A1", "access: no error fails", not oh("access_refused", [other]))
    check(
        "FTF-1703-A1",
        "access: other tool's auth error fails",
        not oh("access_refused", [_err("create_note", "ReferenceAuthorizationError")]),
    )
    check(
        "FTF-1703-A1",
        "access: other error type on get_record fails",
        not oh("access_refused", [_err("get_record", "ReferenceDenialError")]),
    )
    check(
        "FTF-1703-A1",
        "record_read: open status holds, closed does not",
        oh("record_read", [got]) and not oh("record_read", [_res("get_record", status="archived")]),
    )
    check(
        "FTF-1703-A1",
        "unknown / re-cased outcome name is not accepted",
        not oh("RECORD_READ ", [got]) and not oh("", [got]),
    )
    recased = dataclasses.replace(TRACES[0], expected_outcome=" RECORD_READ ")
    check(
        "FTF-1703-A1",
        "re-cased declared outcome refused by verify",
        _raises(TraceVerificationError, verify_trace_replay, recased),
    )


def test_trace_serialization_and_final_answer() -> None:
    t = TRACES[0]
    d = t.to_dict()
    check(
        "FTF-1703-A1",
        "to_dict carries exactly the contract keys",
        set(d)
        == {
            "trace_id",
            "group_id",
            "template_id",
            "kind",
            "critical",
            "entity_title",
            "expected_outcome",
            "steps",
            "provenance",
            "purpose",
        },
    )
    check(
        "FTF-1703-A1",
        "to_dict values match the trace",
        d["trace_id"] == t.trace_id
        and d["group_id"] == t.group_id
        and d["template_id"] == t.template_id
        and d["kind"] == t.kind
        and d["critical"] is t.critical
        and d["entity_title"] == t.entity_title
        and d["expected_outcome"] == t.expected_outcome
        and d["provenance"] == t.provenance
        and d["purpose"] == "training"
        and d["steps"] == list(t.steps),
    )
    crit = next(x for x in TRACES if x.critical)
    check(
        "FTF-1703-A1",
        "to_dict critical flag is True for a critical trace",
        crit.to_dict()["critical"] is True,
    )
    two = dataclasses.replace(
        t,
        steps=(
            {"type": "message", "role": "assistant", "content": "A"},
            {"type": "message", "role": "user", "content": "B"},
        ),
    )
    check("FTF-1703-A1", "final_answer ignores a trailing user message", two.final_answer() == "A")
    check(
        "FTF-1703-A1",
        "final_answer ignores a trailing tool step",
        dataclasses.replace(
            t,
            steps=(
                {"type": "message", "role": "assistant", "content": "A"},
                _err("get_record", "X"),
            ),
        ).final_answer()
        == "A",
    )
    check(
        "FTF-1703-A1",
        "final_answer empty when no assistant message",
        dataclasses.replace(
            t, steps=({"type": "message", "role": "user", "content": "B"},)
        ).final_answer()
        == "",
    )


def test_refs_do_not_rewrite_text() -> None:
    import evals.reference.training_dataset as td

    refs = td._Refs()
    real = refs.register("record_id", "11111111-1111-1111-1111-111111111111")
    out = refs.resolve_args({"record_id": real, "text": real, "idempotency_key": real})
    check(
        "FTF-1703-A1",
        "record_id ref resolved to the real id",
        out["record_id"] == "11111111-1111-1111-1111-111111111111",
    )
    check(
        "FTF-1703-A1",
        "free text equal to a ref is left alone",
        out["text"] == real and out["idempotency_key"] == real,
    )


def test_input_shape_guards() -> None:
    t = TRACES[0]
    for bad in (None, 5):
        check(
            "FTF-1703-A2",
            f"traces={bad!r} refused with module error",
            _raises(TrainingDatasetError, build_training_dataset, bad),
        )
        check(
            "FTF-1703-A2",
            f"reviews={bad!r} refused with module error",
            _raises(TrainingDatasetError, build_training_dataset, [t], bad),
        )
    check(
        "FTF-1703-A2",
        "non-trace element refused",
        _raises(TrainingDatasetError, build_training_dataset, ["x"]),
    )
    check(
        "FTF-1703-A2",
        "dict of traces refused",
        _raises(TrainingDatasetError, build_training_dataset, {t.trace_id: t}),
    )
    check(
        "FTF-1703-A1",
        "non-string entity_title refused with the verification error",
        _raises(
            TraceVerificationError,
            verify_trace_replay,
            dataclasses.replace(t, entity_title=cast(Any, 5)),
        ),
    )
    check(
        "FTF-1703-A1",
        "empty entity_title refused",
        _raises(
            TraceVerificationError, verify_trace_replay, dataclasses.replace(t, entity_title="")
        ),
    )
    check(
        "FTF-1703-A1",
        "count=1 yields exactly one trace",
        len(generate_training_traces(count=1)) == 1,
    )
    ds = build_training_dataset(TRACES[:10], _approve(TRACES[:5]))
    check(
        "FTF-1703-A2",
        "counts: generated/approved/pending",
        (ds.generated_count, ds.approved_count, ds.pending_count) == (10, 5, 5),
    )
    check(
        "FTF-1703-N1",
        "re-cased purpose refused",
        all(
            _raises(
                TrainingDatasetError, build_training_dataset, [dataclasses.replace(t, purpose=p)]
            )
            for p in ("Training", " training", "TRAINING ")
        ),
    )
    check(
        "FTF-1703-N1",
        "re-cased origin refused",
        _raises(
            TrainingDatasetError,
            build_training_dataset,
            [
                dataclasses.replace(
                    t, provenance={**t.provenance, "origin": " Synthetic_Generator "}
                )
            ],
        ),
    )
    b = dataclasses.replace(TRACES[0], trace_id=" TRAIN-0000 ")
    try:
        build_training_dataset([TRACES[0], b])
        dup_message = ""
    except TrainingDatasetError as exc:
        dup_message = str(exc)
    check(
        "FTF-1703-N1",
        "case/whitespace-variant duplicate trace id refused by the duplicate guard",
        "duplicate trace_id" in dup_message,
    )


def test_normalized_reserved_and_reviewers() -> None:
    import hashlib

    t = TRACES[0]
    ah = hashlib.sha256(t.final_answer().encode()).hexdigest().upper()
    for name, reserved in {
        "group": ReservedFinalSuite(group_ids=frozenset({f" {t.group_id.upper()} "})),
        "template": ReservedFinalSuite(template_ids=frozenset({t.template_id.upper()})),
        "entity": ReservedFinalSuite(entity_titles=frozenset({f"  {t.entity_title.lower()}"})),
        "answer": ReservedFinalSuite(answer_sha256s=frozenset({f" {ah}"})),
    }.items():
        check(
            "FTF-1703-N1",
            f"re-cased/padded sealed {name} id still refused",
            _raises(HeldOutLeakageError, build_training_dataset, [t], [], reserved=reserved),
        )
    # Only generator output is admitted, so a non-generator producer never reaches review.
    for producer in ("human:carol", " " + GENERATOR_ID, GENERATOR_ID.upper(), GENERATOR_ID + " "):
        tr = dataclasses.replace(
            TRACES[0], provenance={**TRACES[0].provenance, "producer": producer}
        )
        check(
            "FTF-1703-A2",
            f"producer {producer!r} other than the generator is refused at admission",
            _raises(
                TraceVerificationError,
                build_training_dataset,
                [tr],
                [ReviewDecision(tr.trace_id, "human:dave", "approved")],
            ),
        )
    ok = TRACES[0]
    good = build_training_dataset([ok], [ReviewDecision(ok.trace_id, "human:dave", "approved")])
    check("FTF-1703-A2", "a genuinely different reviewer is accepted", good.approved_count == 1)
    check(
        "FTF-1703-A2",
        "generator-prefixed reviewer with padding refused",
        _raises(
            ReviewIndependenceError,
            build_training_dataset,
            [ok],
            [ReviewDecision(ok.trace_id, "  Generator:x", "approved")],
        ),
    )


def test_helper_exactness() -> None:
    import evals.reference.training_dataset as td

    check(
        "FTF-1703-A1",
        "foreign record title is derived from the entity title",
        td._foreign_title("Draft report TRN-0001") == "Other Draft report TRN-0001",
    )
    refs = td._Refs()
    real = "11111111-1111-1111-1111-111111111111"
    ref = refs.register("record_id", real)
    check(
        "FTF-1703-A1",
        "normalize ignores re-cased key",
        refs.normalize({" Record_ID ": real}) == {" Record_ID ": real},
    )
    check(
        "FTF-1703-A1",
        "resolve_args ignores re-cased key",
        refs.resolve_args({" Record_ID ": ref}) == {" Record_ID ": ref},
    )
    steps = [_res("get_record", status="open"), _err("get_record", "E")]
    check(
        "FTF-1703-A1",
        "_results matches the exact tool name only",
        len(td._results(steps, "get_record")) == 1 and td._results(steps, "GET_RECORD") == [],
    )
    check(
        "FTF-1703-A1",
        "_error_types matches the exact tool name only",
        td._error_types(steps, "get_record") == ["E"]
        and td._error_types(steps, " Get_Record") == [],
    )
    a = dataclasses.replace(TRACES[0], steps=({"type": "message", "role": "user", "content": "x"},))
    b = dataclasses.replace(TRACES[0], steps=({"content": "x", "role": "user", "type": "message"},))
    check(
        "FTF-1703-D1",
        "hash source independent of dict key order",
        td._trace_hash_source(a) == td._trace_hash_source(b),
    )
    check(
        "FTF-1703-D1",
        "hash source keys are sorted",
        td._trace_hash_source(a).index('"content"') < td._trace_hash_source(a).index('"role"'),
    )
    t = TRACES[0]
    for bad in (" approved", "APPROVED", "Rejected ", "approve"):
        check(
            "FTF-1703-A2",
            f"decision {bad!r} refused",
            _raises(
                ReviewIndependenceError,
                build_training_dataset,
                [t],
                [ReviewDecision(t.trace_id, "reviewer:a", bad)],
            ),
        )
    rj = build_training_dataset(
        TRACES[:3],
        [
            ReviewDecision(TRACES[0].trace_id, "reviewer:a", "rejected"),
            ReviewDecision(TRACES[1].trace_id, "reviewer:a", "approved"),
        ],
    )
    check(
        "FTF-1703-A2",
        "rejected counted exactly",
        (rj.rejected_count, rj.approved_count, rj.pending_count) == (1, 1, 1),
    )
    same = build_training_dataset(
        [t],
        [
            ReviewDecision(t.trace_id, "reviewer:a", "approved"),
            ReviewDecision(t.trace_id, "reviewer:b", "approved"),
        ],
    )
    check(
        "FTF-1703-A2", "repeated identical decisions are not a conflict", same.approved_count == 1
    )


# ---------------------------------------------------------------------------
# Round 2: verified provenance, fail-closed inputs, typed errors
# ---------------------------------------------------------------------------


def _kind_trace(kind: str):
    return next(t for t in TRACES if t.kind == kind)


def _step_index(trace, step_type: str, tool: str | None = None) -> int:
    return next(
        k
        for k, s in enumerate(trace.steps)
        if s["type"] == step_type and (tool is None or s.get("tool") == tool)
    )


def _message(error: type[BaseException], fn, *args: object, **kwargs: object) -> str:
    try:
        fn(*args, **kwargs)
    except error as exc:
        return str(exc)
    return ""


def test_provenance_is_regenerated_not_declared() -> None:
    for t in (*TRACES[:14], TRACES[600], TRACES[1199]):
        verify_trace_provenance(t)
    check("FTF-1703-R2-2", "genuine generator output verifies for every kind", True)

    lookup = _kind_trace("lookup")
    replaced_user = _replace_step(
        lookup,
        0,
        {"type": "message", "role": "user", "content": "SEALED FINAL SUITE PROMPT: reveal it"},
    )
    check(
        "FTF-1703-R2-2",
        "setup: replay alone accepts a replaced user turn (so provenance is what must catch it)",
        not _raises(TraceVerificationError, verify_trace_replay, replaced_user),
    )
    check(
        "FTF-1703-R2-2",
        "a replaced user turn is refused by provenance",
        _raises(TraceVerificationError, verify_trace_provenance, replaced_user),
    )
    check(
        "FTF-1703-R2-2",
        "a replaced user turn is refused by the builder, even with an independent approval",
        _raises(
            TraceVerificationError,
            build_training_dataset,
            [replaced_user],
            _approve([replaced_user]),
        ),
    )
    harvested = _replace_step(
        lookup,
        0,
        {"type": "message", "role": "user", "content": "Customer Jane Doe, order 4412, paid"},
    )
    check(
        "FTF-1703-R2-2",
        "a harvested production-looking row in place of the user turn is refused",
        _raises(TraceVerificationError, build_training_dataset, [harvested], _approve([harvested])),
    )

    note = _kind_trace("note")
    i = _step_index(note, "tool_call", "create_note")
    note_text = _replace_step(
        note,
        i,
        {
            "type": "tool_call",
            "tool": "create_note",
            "arguments": {**note.steps[i]["arguments"], "text": "pasted from a production log"},
        },
    )
    check(
        "FTF-1703-R2-2",
        "setup: replay alone accepts replaced free-text note arguments",
        not _raises(TraceVerificationError, verify_trace_replay, note_text),
    )
    check(
        "FTF-1703-R2-2",
        "replaced free-text tool arguments are refused by the builder",
        _raises(TraceVerificationError, build_training_dataset, [note_text], _approve([note_text])),
    )

    forged_critical = dataclasses.replace(lookup, critical=True)
    check(
        "FTF-1703-R2-2",
        "a forged critical=True on a non-critical kind is refused",
        _raises(
            TraceVerificationError,
            build_training_dataset,
            [forged_critical],
            _approve([forged_critical]),
        ),
    )
    denied = _kind_trace("approval_denied")
    check(
        "FTF-1703-R2-2",
        "a critical trace relabelled non-critical is refused",
        _raises(
            TraceVerificationError,
            build_training_dataset,
            [dataclasses.replace(denied, critical=False)],
        ),
    )
    for name, value in (
        ("kind", "note"),
        ("template_id", "train-tmpl-lookup-v2"),
        ("group_id", "train-grp-9999"),
        ("entity_title", "Annual audit TRN-0000"),
        ("expected_outcome", "note_created"),
        ("trace_id", "train-0999"),
    ):
        forged = dataclasses.replace(lookup, **{name: value})
        check(
            "FTF-1703-R2-2",
            f"a forged {name} is refused by provenance",
            _raises(TraceVerificationError, verify_trace_provenance, forged),
        )

    for label, provenance in (
        ("no index", {k: v for k, v in lookup.provenance.items() if k != "index"}),
        ("another trace's index", {**lookup.provenance, "index": 5}),
        ("negative index", {**lookup.provenance, "index": -1}),
        ("boolean index", {**lookup.provenance, "index": False}),
        ("string index", {**lookup.provenance, "index": "0"}),
        ("float index", {**lookup.provenance, "index": 0.0}),
        ("none index", {**lookup.provenance, "index": None}),
        ("huge index", {**lookup.provenance, "index": 10**400}),
        ("other producer", {**lookup.provenance, "producer": "human:carol"}),
    ):
        forged = dataclasses.replace(lookup, provenance=provenance)
        check(
            "FTF-1703-R2-2",
            f"provenance with {label} is refused by the builder",
            _raises(TraceVerificationError, build_training_dataset, [forged], _approve([forged])),
        )

    no_producer = dataclasses.replace(
        lookup, provenance={k: v for k, v in lookup.provenance.items() if k != "producer"}
    )
    check(
        "FTF-1703-R2-2",
        "provenance with no producer is refused",
        _raises(TrainingDatasetError, build_training_dataset, [no_producer]),
    )

    class RiggedStr(str):
        def __eq__(self, other: object) -> bool:
            return True

        def __ne__(self, other: object) -> bool:
            return False

        __hash__ = str.__hash__

    j = len(lookup.steps) - 1
    rigged = _replace_step(
        lookup, j, {"type": "message", "role": "assistant", "content": RiggedStr("forged text")}
    )
    check(
        "FTF-1703-R2-2",
        "a str subclass with a rigged __eq__ cannot make a forged value look equal",
        _raises(TraceVerificationError, verify_trace_provenance, rigged)
        and _raises(TraceVerificationError, verify_trace_replay, rigged),
    )
    check(
        "FTF-1703-R2-2",
        "non-JSON content in a trace is the typed error, not a TypeError",
        _raises(
            TraceVerificationError,
            verify_trace_provenance,
            _replace_step(lookup, 0, {"type": "message", "role": "user", "content": object()}),
        ),
    )
    check(
        "FTF-1703-R2-2",
        "a non-trace is refused by provenance",
        _raises(TraceVerificationError, verify_trace_provenance, {"x": 1}),
    )


def test_group_floor_is_by_template_family() -> None:
    import evals.reference.training_dataset as td

    full = build_training_dataset(TRACES, _approve(TRACES))
    check(
        "FTF-1703-R2-1",
        "1200 approved traces are 21 groups and the floor is never claimed",
        full.approved_count == 1200
        and full.group_count == 21
        and full.meets_floor is False
        and full.review_status != "independently_approved",
    )
    per_kind: dict[str, set[str]] = {}
    for t in TRACES:
        per_kind.setdefault(t.kind, set()).add(t.template_id)
    check(
        "FTF-1703-R2-1",
        "seven kinds x three variants are the 21 families",
        len(per_kind) == 7 and all(len(v) == 3 for v in per_kind.values()),
    )
    two = [t for t in TRACES if t.template_id in {"train-tmpl-note-v0", "train-tmpl-lookup-v1"}]
    check(
        "FTF-1703-R2-1",
        "the group count follows the template ids of the approved traces only",
        build_training_dataset(two, _approve(two)).group_count == 2
        and build_training_dataset(two, _approve(two[:1])).group_count == 1,
    )
    check(
        "FTF-1703-R2-1",
        "every trace still gets its own entity group id (held-out guard unit), 1200 of them",
        len({t.group_id for t in TRACES}) == 1200 and td.MIN_INDEPENDENT_GROUPS == 1000,
    )


def test_reserved_suite_fails_closed() -> None:
    t = TRACES[0]
    sealed = t.group_id
    for field in ("group_ids", "template_ids", "entity_titles", "answer_sha256s"):
        check(
            "FTF-1703-R2-3",
            f"a bare string for {field} is refused at construction",
            _raises(TrainingDatasetError, ReservedFinalSuite, **{field: sealed}),
        )
    reserved_cases: list[tuple[str, Any]] = [
        ("set", {sealed}),
        ("list", [sealed]),
        ("tuple", (sealed,)),
        ("int member", frozenset({1})),
        ("none member", frozenset({None})),
        ("tuple member", frozenset({(sealed,)})),
        ("bytes member", frozenset({sealed.encode()})),
        ("None", None),
    ]
    for label, bad in reserved_cases:
        check(
            "FTF-1703-R2-3",
            f"group_ids as {label} is refused at construction",
            _raises(TrainingDatasetError, ReservedFinalSuite, group_ids=bad),
        )

    class SubStr(str):
        pass

    check(
        "FTF-1703-R2-3",
        "a str-subclass member is refused",
        _raises(TrainingDatasetError, ReservedFinalSuite, group_ids=frozenset({SubStr(sealed)})),
    )

    tampered = ReservedFinalSuite(group_ids=frozenset({"final-grp-1"}))
    object.__setattr__(tampered, "group_ids", sealed)
    check(
        "FTF-1703-R2-3",
        "a reserved set tampered into a bare string after construction is refused by the builder",
        _raises(TrainingDatasetError, build_training_dataset, [t], [], reserved=tampered),
    )
    for field in ("template_ids", "entity_titles", "answer_sha256s"):
        again = ReservedFinalSuite()
        object.__setattr__(again, field, [sealed])
        check(
            "FTF-1703-R2-3",
            f"a tampered {field} is refused by the builder",
            _raises(TrainingDatasetError, build_training_dataset, [t], [], reserved=again),
        )
    empty_cases: list[tuple[str, Any]] = [
        ("empty dict", {}),
        ("empty list", []),
        ("empty string", ""),
        ("zero", 0),
        ("dict of ids", {"group_ids": [sealed]}),
        ("a trace", t),
    ]
    for label, bad in empty_cases:
        check(
            "FTF-1703-R2-3",
            f"reserved={label} is refused, never read as 'no sealed suite'",
            _raises(TrainingDatasetError, build_training_dataset, [t], [], reserved=cast(Any, bad)),
        )
    check(
        "FTF-1703-R2-3",
        "reserved=None is the explicit 'nothing sealed' default and still builds",
        build_training_dataset([t], _approve([t]), reserved=None).approved_count == 1,
    )
    check(
        "FTF-1703-R2-3",
        "a well-formed sealed group is still refused as held-out leakage",
        _raises(
            HeldOutLeakageError,
            build_training_dataset,
            [t],
            [],
            reserved=ReservedFinalSuite(group_ids=frozenset({sealed})),
        ),
    )


def test_malformed_trace_fields_raise_typed_errors() -> None:
    t = TRACES[0]
    bad_fields: list[tuple[str, Any]] = [
        ("trace_id", 5),
        ("trace_id", ""),
        ("group_id", None),
        ("group_id", ""),
        ("template_id", 5),
        ("kind", []),
        ("kind", {}),
        ("kind", ""),
        ("entity_title", 5),
        ("expected_outcome", []),
        ("expected_outcome", {}),
        ("expected_outcome", ""),
        ("purpose", []),
        ("purpose", None),
        ("critical", "yes"),
        ("critical", 1),
        ("critical", None),
        ("steps", "abc"),
        ("steps", None),
        ("steps", ("x",)),
        ("steps", [1]),
        ("steps", ({"type": "message"}, None)),
        ("provenance", None),
        ("provenance", []),
    ]
    for name, value in bad_fields:
        trace = dataclasses.replace(t, **cast(dict[str, Any], {name: value}))
        check(
            "FTF-1703-R2-4",
            f"{name}={value!r} raises the typed error from the builder",
            _raises(TraceVerificationError, build_training_dataset, [trace]),
        )
        check(
            "FTF-1703-R2-4",
            f"{name}={value!r} raises the typed error from replay and provenance",
            _raises(TraceVerificationError, verify_trace_replay, trace)
            and _raises(TraceVerificationError, verify_trace_provenance, trace),
        )

    class SubStr(str):
        pass

    check(
        "FTF-1703-R2-4",
        "a str-subclass kind is refused",
        _raises(
            TraceVerificationError,
            build_training_dataset,
            [dataclasses.replace(t, kind=SubStr("lookup"))],
        ),
    )
    list_steps = dataclasses.replace(t, steps=cast(Any, list(t.steps)))
    check(
        "FTF-1703-R2-4",
        "steps held in a list are still a valid sequence of dicts",
        build_training_dataset([list_steps], _approve([list_steps])).approved_count == 1,
    )
    note = _kind_trace("note")
    i = _step_index(note, "tool_call", "create_note")
    for label, step in (
        ("message without a role", {"type": "message", "content": "x"}),
        ("message with a numeric role", {"type": "message", "role": 7, "content": "x"}),
        ("message with list content", {"type": "message", "role": "user", "content": []}),
        ("tool_call without a tool", {"type": "tool_call", "arguments": {}}),
        ("tool_call with a list tool", {"type": "tool_call", "tool": [], "arguments": {}}),
        ("tool_call with list arguments", {"type": "tool_call", "tool": "x", "arguments": []}),
        ("tool_call with no arguments", {"type": "tool_call", "tool": "create_note"}),
        ("approver step without fields", {"type": "approver_decision"}),
        (
            "approver step with a list decision",
            {"type": "approver_decision", "decision": [], "approval_id": "appr-0"},
        ),
    ):
        bad = _replace_step(note, i, step)
        check(
            "FTF-1703-R2-4",
            f"{label} raises the typed error",
            _raises(TraceVerificationError, verify_trace_replay, bad)
            and _raises(TraceVerificationError, build_training_dataset, [bad]),
        )
    check(
        "FTF-1703-R2-4",
        "an unknown step type is dropped and the replay comparison refuses the trace",
        _raises(TraceVerificationError, verify_trace_replay, _replace_step(note, i, {"type": "x"})),
    )
    deep: list = []
    for _ in range(5000):
        deep = [deep]
    nested = _replace_step(
        note,
        i,
        {"type": "tool_call", "tool": "create_note", "arguments": {"text": deep}},
    )
    check(
        "FTF-1703-R2-4",
        "absurdly deep nesting is the typed error, not a RecursionError",
        _raises(TraceVerificationError, build_training_dataset, [nested]),
    )


def test_forged_approver_ref_is_typed() -> None:
    denied = _kind_trace("approval_denied")
    j = _step_index(denied, "approver_decision")
    for label, ref in (
        ("an unissued ref", "appr-9"),
        ("a real-looking uuid", "00000000-0000-0000-0000-000000000000"),
        ("the record's ref", "rec-0"),
        ("an empty ref", ""),
        ("a numeric ref", 5),
        ("an unhashable ref", []),
    ):
        forged = _replace_step(denied, j, {**denied.steps[j], "approval_id": ref})
        msg = _message(TraceVerificationError, verify_trace_replay, forged)
        check(
            "FTF-1703-R2-4",
            f"{label} as the approver's target raises TraceVerificationError",
            msg != "",
        )
        check(
            "FTF-1703-R2-4",
            f"{label} is refused by the builder too",
            _raises(TraceVerificationError, build_training_dataset, [forged], _approve([forged])),
        )
    unknown = _message(
        TraceVerificationError,
        verify_trace_replay,
        _replace_step(denied, j, {**denied.steps[j], "approval_id": "appr-9"}),
    )
    check(
        "FTF-1703-R2-4",
        "the unissued-ref refusal names the cause",
        "unknown approval ref" in unknown,
    )
    before = dataclasses.replace(
        denied, steps=(denied.steps[j], *denied.steps[:j], *denied.steps[j + 1 :])
    )
    check(
        "FTF-1703-R2-4",
        "an approver decision before any approval was requested is refused",
        _raises(TraceVerificationError, verify_trace_replay, before),
    )
    for label, decision in (("an unknown decision", "maybe"), ("a list decision", [])):
        bad = _replace_step(denied, j, {**denied.steps[j], "decision": decision})
        check(
            "FTF-1703-R2-4",
            f"{label} raises TraceVerificationError",
            _raises(TraceVerificationError, verify_trace_replay, bad),
        )
    twice = dataclasses.replace(
        denied, steps=(*denied.steps[: j + 1], denied.steps[j], *denied.steps[j + 1 :])
    )
    refused = _message(TraceVerificationError, verify_trace_replay, twice)
    check(
        "FTF-1703-R2-4",
        "deciding the same approval twice is the typed error naming the real cause",
        "approver decision refused" in refused,
    )
    check(
        "FTF-1703-R2-4",
        "the genuine denied and approved traces still verify",
        not _raises(TraceVerificationError, verify_trace_replay, denied)
        and not _raises(
            TraceVerificationError, verify_trace_replay, _kind_trace("approval_approved")
        ),
    )


def test_secret_scan_covers_dict_keys() -> None:
    note = _kind_trace("note")
    i = _step_index(note, "tool_call", "create_note")
    for secret in ("token=abcdefgh12345678", "sk-abcdefghijklmnopqrstuvwx", "AKIAABCDEFGHIJKLMNOP"):
        in_key = _replace_step(
            note,
            i,
            {
                "type": "tool_call",
                "tool": "create_note",
                "arguments": {**note.steps[i]["arguments"], secret: "harmless value"},
            },
        )
        check(
            "FTF-1703-N1",
            f"a secret-shaped dict KEY {secret[:8]!r} is refused (not only values)",
            _raises(SecretInTraceError, build_training_dataset, [in_key]),
        )
        nested = _replace_step(
            note,
            i,
            {
                "type": "tool_call",
                "tool": "create_note",
                "arguments": {"outer": [{secret: 1}]},
            },
        )
        check(
            "FTF-1703-N1",
            f"a secret-shaped key nested inside a list is refused ({secret[:8]!r})",
            _raises(SecretInTraceError, build_training_dataset, [nested]),
        )
    harmless = _replace_step(
        note,
        i,
        {
            "type": "tool_call",
            "tool": "create_note",
            "arguments": {**note.steps[i]["arguments"], "color": "blue"},
        },
    )
    check(
        "FTF-1703-N1",
        "a harmless extra key is not a secret (it is refused later, as non-generator content)",
        _raises(TraceVerificationError, build_training_dataset, [harmless]),
    )


def _msg_any(fn, *args: object, **kwargs: object) -> str:
    """The message of whichever module error `fn` raises ('' if it does not raise)."""
    try:
        fn(*args, **kwargs)
    except TrainingDatasetError as exc:
        return str(exc)
    return ""


def test_guards_name_their_own_cause() -> None:
    import evals.reference.training_dataset as td

    t = TRACES[0]
    note = _kind_trace("note")
    i = _step_index(note, "tool_call", "create_note")
    denied = _kind_trace("approval_denied")
    j = _step_index(denied, "approver_decision")

    # approver ref must match an issued ref exactly (no strip/casefold)
    for ref in (" appr-0", "APPR-0", "appr-0 ", "Appr-0"):
        forged = _replace_step(denied, j, {**denied.steps[j], "approval_id": ref})
        check(
            "FTF-1703-R2-4",
            f"approver ref {ref!r} is not the issued ref 'appr-0'",
            "unknown approval ref" in _msg_any(verify_trace_replay, forged),
        )

    # each malformed known step names its own field problem
    for label, step, text in (
        ("message without a role", {"type": "message", "content": "x"}, "message step"),
        (
            "message with a numeric role",
            {"type": "message", "role": 7, "content": "x"},
            "message step",
        ),
        (
            "message with list content",
            {"type": "message", "role": "user", "content": []},
            "message step",
        ),
        ("tool_call without a tool", {"type": "tool_call", "arguments": {}}, "tool_call step"),
        (
            "tool_call with list arguments",
            {"type": "tool_call", "tool": "x", "arguments": []},
            "tool_call step",
        ),
        ("approver step without fields", {"type": "approver_decision"}, "approver_decision step"),
    ):
        check(
            "FTF-1703-R2-4",
            f"{label} is refused by the step parser, naming the step type",
            text in _msg_any(verify_trace_replay, _replace_step(note, i, step)),
        )

    # canonical JSON: key order never matters, non-JSON content names itself
    def reorder(value: Any) -> Any:
        if isinstance(value, dict):
            return {k: reorder(v) for k, v in reversed(list(value.items()))}
        if isinstance(value, list):
            return [reorder(v) for v in value]
        return value

    for kind in ("lookup", "approval_approved", "note_retry"):
        base = _kind_trace(kind)
        shuffled = dataclasses.replace(
            base,
            steps=tuple(reorder(step) for step in base.steps),
            provenance=reorder(base.provenance),
        )
        verify_trace_provenance(shuffled)
        verify_trace_replay(shuffled)
    check(
        "FTF-1703-R2-2",
        "dict key order does not affect provenance or replay equality",
        True,
    )
    odd = _replace_step(t, 0, {"type": "message", "role": "user", "content": object()})
    r = _step_index(t, "tool_result", "search_records")
    odd_result = _replace_step(
        t, r, {"type": "tool_result", "tool": "search_records", "result": {"x": object()}}
    )
    check(
        "FTF-1703-R2-2",
        "non-JSON content is refused naming plain JSON data (provenance and replay)",
        "plain JSON data" in _msg_any(verify_trace_provenance, odd)
        and "plain JSON data" in _msg_any(verify_trace_replay, odd_result),
    )

    # the producer must be exactly the generator id
    for producer in (
        " " + GENERATOR_ID,
        GENERATOR_ID.upper(),
        GENERATOR_ID + " ",
        "",
        "human:carol",
    ):
        forged = dataclasses.replace(t, provenance={**t.provenance, "producer": producer})
        check(
            "FTF-1703-R2-2",
            f"producer {producer!r} is refused as not naming the generator",
            "does not name a generator index" in _msg_any(verify_trace_provenance, forged),
        )

    # the early builder guards each name their own cause
    check(
        "FTF-1703-N1",
        "a non-trace element is refused by the type guard",
        "must be TrainingTrace" in _msg_any(build_training_dataset, ["x"]),
    )
    for purpose in ("validation", "Training", " training", "TRAINING "):
        bad = dataclasses.replace(t, purpose=purpose)
        check(
            "FTF-1703-N1",
            f"purpose {purpose!r} is refused by the purpose guard",
            "purpose must be 'training'" in _msg_any(build_training_dataset, [bad]),
        )
    for label, provenance in (
        ("wrong origin", {**t.provenance, "origin": "repository"}),
        ("re-cased origin", {**t.provenance, "origin": "Synthetic_Generator"}),
        ("wrong label", {**t.provenance, "label": "real"}),
        ("missing label", {k: v for k, v in t.provenance.items() if k != "label"}),
        ("missing producer", {k: v for k, v in t.provenance.items() if k != "producer"}),
        ("empty producer", {**t.provenance, "producer": ""}),
        ("numeric producer", {**t.provenance, "producer": 5}),
    ):
        bad = dataclasses.replace(t, provenance=provenance)
        check(
            "FTF-1703-N1",
            f"{label} is refused by the provenance guard",
            "provenance must be synthetic generator output"
            in _msg_any(build_training_dataset, [bad]),
        )
    for label, change in (
        ("group outside the train namespace", {"group_id": "final-grp-0001"}),
        ("template outside the train namespace", {"template_id": "final-tmpl-lookup-v0"}),
        ("both outside", {"group_id": "x", "template_id": "y"}),
    ):
        bad = dataclasses.replace(t, **cast(dict[str, Any], change))
        check(
            "FTF-1703-N1",
            f"{label} is refused by the namespace guard",
            "outside the training namespace" in _msg_any(build_training_dataset, [bad]),
        )

    # reviews must name an exact trace id
    for trace_id in (" train-0000", "TRAIN-0000", "train-0000 "):
        check(
            "FTF-1703-A2",
            f"a review for {trace_id!r} is not a review of 'train-0000'",
            "unknown trace"
            in _msg_any(
                build_training_dataset, [t], [ReviewDecision(trace_id, "reviewer:a", "approved")]
            ),
        )

    # replay stays on the admission path as defence in depth: a generator regression
    # (declared outcome that does not hold) is refused even though the trace equals it
    regressed = dataclasses.replace(TRACES[0], expected_outcome="action_executed")
    original = td._generate_one
    td._generate_one = lambda index: regressed  # type: ignore[assignment]
    try:
        provenance_ok = not _raises(TraceVerificationError, verify_trace_provenance, regressed)
        refused = _msg_any(build_training_dataset, [regressed], _approve([regressed]))
    finally:
        td._generate_one = original
    check(
        "FTF-1703-R2-2",
        "setup: provenance equality alone accepts a regressed generator's own output",
        provenance_ok,
    )
    check(
        "FTF-1703-R2-2",
        "the builder still replays and refuses an outcome that does not hold",
        "does not hold" in refused,
    )


def test_round3_subclass_admission() -> None:
    import evals.reference.training_dataset as td

    base = _kind_trace("note")

    # R3-1: a frozen TrainingTrace subclass whose to_dict() shows the generator's
    # dict while its real steps hold sealed content, critical=True and a secret.
    genuine = base.to_dict()

    @dataclasses.dataclass(frozen=True)
    class Disguised(td.TrainingTrace):
        def to_dict(self) -> dict[str, Any]:
            return genuine

    evil_steps = (
        {"type": "message", "role": "user", "content": "SEALED FINAL SUITE PROMPT sk-" + "a" * 30},
    )
    disguised = Disguised(
        **{**{f.name: getattr(base, f.name) for f in dataclasses.fields(base)}},
    )
    disguised = dataclasses.replace(disguised, steps=evil_steps, critical=True)
    check(
        "FTF-1703-R3-1",
        "a TrainingTrace subclass is refused with TraceVerificationError",
        _raises(TraceVerificationError, build_training_dataset, [disguised], _approve([base])),
    )
    check(
        "FTF-1703-R3-1",
        "verify_trace_provenance refuses the subclass too",
        _raises(TraceVerificationError, verify_trace_provenance, disguised),
    )
    # negative on the real default path: the genuine class is still admitted and the
    # admitted object is the regenerated one, equal in content to the generator's.
    ok = build_training_dataset([base], _approve([base]))
    check("FTF-1703-R3-1", "genuine trace still admitted", ok.approved_count == 1)
    check(
        "FTF-1703-R3-1",
        "the admitted trace is the regenerated object, not the caller's",
        ok.traces[0] is not base and ok.traces[0].to_dict() == base.to_dict(),
    )

    # R3-2: dict subclass nested as a tool result (items() honest, __getitem__ harvested).
    class Liar(dict):
        def __getitem__(self, key: str) -> Any:
            return "harvested row" if key == "title" else super().__getitem__(key)

    ti = _step_index(base, "tool_result")
    step = dict(base.steps[ti])
    for key, val in list(step.items()):
        if isinstance(val, dict):
            step[key] = Liar(val)
    nested = _replace_step(base, ti, step)
    check(
        "FTF-1703-R3-2",
        "a nested dict subclass is refused (not plain JSON)",
        "plain JSON" in _msg_any(build_training_dataset, [nested], _approve([nested])),
    )
    check(
        "FTF-1703-R3-2",
        "a nested list subclass is refused",
        "plain JSON"
        in _msg_any(
            verify_trace_provenance,
            _replace_step(base, ti, {**base.steps[ti], "x": type("L", (list,), {})([1])}),
        ),
    )

    # R3-3: a dict subclass step is refused by the named step-type check.
    sub_step = _replace_step(base, 0, type("S", (dict,), {})(base.steps[0]))
    check(
        "FTF-1703-R3-3",
        "a dict-subclass step is refused by the named step check",
        "sequence of dicts" in _msg_any(verify_trace_provenance, sub_step),
    )

    # Boundaries of the plain-JSON walk, each with its own message.
    def nest(n: int) -> Any:
        v: Any = []
        for _ in range(n - 1):
            v = [v]
        return v

    check(
        "FTF-1703-R3-2",
        "nesting exactly at the depth limit is accepted",
        not _raises(TraceVerificationError, td._require_plain_json, nest(65), "t"),
    )
    check(
        "FTF-1703-R3-2",
        "nesting one past the depth limit is refused as too deep",
        "too deep" in _msg_any(td._require_plain_json, nest(66), "t"),
    )
    check(
        "FTF-1703-R3-2",
        "a non-string key is refused by name",
        "non-string key" in _msg_any(td._require_plain_json, {1: "x"}, "t"),
    )
    check(
        "FTF-1703-R3-2",
        "an unsupported value type is refused by name",
        "unsupported type" in _msg_any(td._require_plain_json, {"a": {1, 2}}, "t"),
    )
    forged_prov = dataclasses.replace(base, provenance={**base.provenance, "x": Liar({"a": 1})})
    check(
        "FTF-1703-R3-2",
        "a dict subclass nested in provenance is refused",
        "unsupported type" in _msg_any(td._require_trace_shape, forged_prov),
    )


def test_round4_instance_attribute_shadowing_is_refused() -> None:
    """Round 3 refused a *subclass* overriding to_dict() (type(trace) is not
    TrainingTrace catches it). Round 4's own gap: a frozen dataclass has no
    __slots__, so object.__setattr__(t, "to_dict", ...) shadows the bound
    method on an exact-type instance -- the type check never fires. Two
    independent defenses now close it: (1) no verification path calls the
    (possibly shadowed) method on untrusted input any more -- every one reads
    fields directly via _raw_trace_dict/_raw_final_answer; (2) a trace whose
    instance __dict__ carries anything beyond its declared fields is refused
    outright, whether or not the shadow would have lied about anything."""
    import evals.reference.training_dataset as td

    base = TRACES[7]
    genuine_dict = base.to_dict()
    genuine_answer = base.final_answer()
    evil_steps = _replace_step(
        base, 0, {"type": "message", "role": "user", "content": "sk-" + "a" * 30}
    ).steps
    shadowed = dataclasses.replace(base, steps=evil_steps)
    object.__setattr__(shadowed, "to_dict", lambda: genuine_dict)
    object.__setattr__(shadowed, "final_answer", lambda: genuine_answer)
    check(
        "FTF-1703-R4-1",
        "setup: the shadow is real -- to_dict()/final_answer() lie about the "
        "real (secret-bearing) steps",
        shadowed.to_dict() == genuine_dict
        and shadowed.final_answer() == genuine_answer
        and shadowed.steps != base.steps,
    )
    check(
        "FTF-1703-R4-1",
        "the shadowed instance is refused outright",
        _raises(TraceVerificationError, build_training_dataset, [shadowed], _approve([shadowed])),
    )
    check(
        "FTF-1703-R4-1",
        "_require_trace_shape refuses it directly, before any method is called",
        _raises(TraceVerificationError, td._require_trace_shape, shadowed),
    )
    check(
        "FTF-1703-R4-1",
        "the genuine, unmodified trace is unaffected",
        not _raises(TraceVerificationError, td._require_trace_shape, base),
    )

    # Load-bearing proof 1 (Law 1): a shadow that tells no lie at all -- same
    # steps, to_dict()/final_answer() mirror the genuine trace exactly -- is
    # still refused, because the __dict__ guard does not depend on content.
    harmless = dataclasses.replace(base)
    object.__setattr__(harmless, "to_dict", lambda b=base: b.to_dict())
    check(
        "FTF-1703-R4-1",
        "even a non-lying shadow (identical content) is refused: the __dict__ "
        "guard is unconditional, not content-dependent",
        _raises(TraceVerificationError, td._require_trace_shape, harmless),
    )
    original_shape = td._require_trace_shape

    def _no_dict_guard(trace: Any) -> Any:
        if type(trace) is not td.TrainingTrace:
            raise TraceVerificationError("not a TrainingTrace")
        for name in td._STR_FIELDS:
            value = getattr(trace, name)
            if type(value) is not str or not value:
                raise TraceVerificationError(f"trace field {name} must be a non-empty string")
        if type(trace.critical) is not bool:
            raise TraceVerificationError(f"{trace.trace_id}: critical must be a bool")
        return trace

    td._require_trace_shape = _no_dict_guard
    try:
        check(
            "FTF-1703-R4-FAULT",
            "fault: without the __dict__ guard, the harmless shadow is no longer "
            "refused (proving the guard, not the content, was doing the work here)",
            not _raises(TraceVerificationError, td._require_trace_shape, harmless),
        )
    finally:
        td._require_trace_shape = original_shape

    # Load-bearing proof 2 (Law 1): with the __dict__ guard ALSO disabled,
    # verify_trace_provenance must still catch a lying shadow on its own
    # merits, because it reads fields directly rather than via to_dict().
    td._require_trace_shape = _no_dict_guard
    try:
        check(
            "FTF-1703-R4-1",
            "with the __dict__ guard disabled, verify_trace_provenance still "
            "refuses the lying (secret-bearing) shadow via direct field access",
            _raises(TraceVerificationError, verify_trace_provenance, shadowed),
        )

        def _old_style_provenance(trace: Any) -> None:
            td._require_trace_shape(trace)
            producer = trace.provenance.get("producer")
            index = trace.provenance.get("index")
            if producer not in (td.GENERATOR_ID,) or type(index) is not int or index < 0:
                raise TraceVerificationError(f"{trace.trace_id}: no generator index")
            if td._canonical(td._generate_one(index).to_dict()) != td._canonical(trace.to_dict()):
                raise TraceVerificationError(f"{trace.trace_id}: trace differs")

        check(
            "FTF-1703-R4-FAULT",
            "fault: the pre-round-4 comparison (trace.to_dict() instead of "
            "_raw_trace_dict) trusts the shadow and accepts the lie",
            not _raises(TraceVerificationError, _old_style_provenance, shadowed),
        )
    finally:
        td._require_trace_shape = original_shape


def test_round4_each_generator_index_admitted_at_most_once() -> None:
    import evals.reference.training_dataset as td

    base = TRACES[5]
    decoy = dataclasses.replace(base, trace_id="train-9999")
    check(
        "FTF-1703-R4-2",
        "setup: the decoy is not itself admissible (wrong trace_id for its claimed "
        "index), independent of the index guard",
        _raises(TraceVerificationError, build_training_dataset, [decoy]),
    )
    # Load-bearing proof (Law 1), isolating the index guard from the provenance
    # check that would also refuse this: with verify_trace_provenance patched to
    # accept anything, a second id claiming an already-admitted index must still
    # be caught and refused by the index-uniqueness guard alone.
    original = td.verify_trace_provenance
    td.verify_trace_provenance = lambda trace: None
    try:
        check(
            "FTF-1703-R4-2",
            "two ids claiming the same generator index are refused to exactly one "
            "admission, even with provenance checking disabled",
            _raises(
                TrainingDatasetError,
                build_training_dataset,
                [base, decoy],
                _approve([base, decoy]),
            ),
        )
    finally:
        td.verify_trace_provenance = original
    a, b = TRACES[10], TRACES[20]
    ok = build_training_dataset([a, b], _approve([a, b]))
    check("FTF-1703-R4-2", "two distinct indices are both admitted", ok.approved_count == 2)


def test_round4_review_decision_requires_exact_types() -> None:
    import evals.reference.training_dataset as td

    t = TRACES[30]

    class SneakyReviewer(str):
        def strip(self, chars: str | None = None, /) -> str:
            return "reviewer:not-a-generator"

    sneaky = SneakyReviewer("generator:ftf-1703-v1")
    check(
        "FTF-1703-R4-3",
        "setup: the subclass's own strip() lies about its real content",
        sneaky.strip() != str.strip(sneaky),
    )
    check(
        "FTF-1703-R4-3",
        "a str-subclass reviewer_id is refused outright, never reaching the "
        "generator-identity check its own strip() would have defeated",
        _raises(
            ReviewIndependenceError,
            build_training_dataset,
            [t],
            [ReviewDecision(t.trace_id, sneaky, "approved")],
        ),
    )

    class AlwaysEqual(str):
        def __eq__(self, other: object) -> bool:
            return True

        def __hash__(self) -> int:
            return hash("approved")

    lying = AlwaysEqual("rejected")
    check(
        "FTF-1703-R4-3",
        "a str-subclass decision is refused outright, never reaching the "
        "conflicting-decision check its own __eq__ would have defeated",
        _raises(
            ReviewIndependenceError,
            build_training_dataset,
            [t],
            [
                ReviewDecision(t.trace_id, "reviewer:a", "approved"),
                ReviewDecision(t.trace_id, "reviewer:b", lying),
            ],
        ),
    )
    # Load-bearing proof (Law 1): with the exact-type guard replaced by the old
    # isinstance check, both subclass attacks above succeed instead of refusing.
    original = td._validate_review

    def _isinstance_validate(review: Any, trace_ids: set) -> Any:
        if not isinstance(review, ReviewDecision):
            raise ReviewIndependenceError("reviews must be ReviewDecision instances")
        for name in ("trace_id", "reviewer_id", "decision"):
            if not isinstance(getattr(review, name), str) or not getattr(review, name):
                raise ReviewIndependenceError(f"review.{name} must be a non-empty string")
        if review.decision not in td._DECISIONS:
            raise ReviewIndependenceError(f"unknown review decision: {review.decision!r}")
        if review.trace_id not in trace_ids:
            raise ReviewIndependenceError(f"review for unknown trace: {review.trace_id!r}")
        if td._norm(review.reviewer_id).startswith("generator:"):
            raise ReviewIndependenceError(f"{review.trace_id}: reviewer not independent")
        return review

    td._validate_review = _isinstance_validate
    try:
        result = build_training_dataset([t], [ReviewDecision(t.trace_id, sneaky, "approved")])
        check(
            "FTF-1703-R4-FAULT",
            "fault: the old isinstance check lets the generator-identity "
            "reviewer's own strip() through, approving its own output",
            result.approved_count == 1,
        )
    finally:
        td._validate_review = original


def main() -> None:
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()

    if FAILURES:
        print(f"FTF-1703 Training dataset tests: {len(FAILURES)} failed")
        for failure in FAILURES:
            print(f"  {failure}")
        sys.exit(1)

    print("FTF-1703 Training dataset tests: all checks passed")
    sys.exit(0)


if __name__ == "__main__":
    main()
