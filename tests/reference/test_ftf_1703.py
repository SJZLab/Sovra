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
