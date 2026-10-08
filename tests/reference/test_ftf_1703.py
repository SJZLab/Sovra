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
        "FTF-1703-A2", "all approved => floor met", full.meets_floor and full.approved_count == 1200
    )
    check(
        "FTF-1703-A2",
        "critical count carried",
        full.critical_count == sum(1 for t in TRACES if t.critical),
    )
    exact = build_training_dataset(TRACES[:1000], _approve(TRACES[:1000]))
    check(
        "FTF-1703-A2",
        "exactly 1000 approved meets floor",
        exact.meets_floor and MIN_APPROVED_TRACES == 1000,
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
    dup_group = [
        dataclasses.replace(t, group_id="train-grp-0000") if t.trace_id != "train-0000" else t
        for t in TRACES[:1000]
    ]
    ds = build_training_dataset(dup_group, _approve(dup_group))
    check(
        "FTF-1703-A2",
        "1000 traces in one group do not meet the independent-group floor",
        ds.approved_count == 1000 and ds.group_count == 1 and not ds.meets_floor,
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
    changed = _replace_step(
        TRACES[0], 0, {"type": "message", "role": "user", "content": "different"}
    )
    c = build_training_dataset([changed], _approve([changed])).dataset_sha256
    d = build_training_dataset([TRACES[0]], _approve([TRACES[0]])).dataset_sha256
    check("FTF-1703-D1", "a one-message user-text change changes the dataset hash", c != d)
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
