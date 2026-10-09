#!/usr/bin/env python3
# mypy: disable-error-code="arg-type,call-arg,var-annotated,dict-item,index,union-attr,assignment,misc,attr-defined,no-untyped-def,no-untyped-call,return-value"
# ruff: noqa: E501
"""FTF-R004-P3 -- pinned Inspect worker + case adapter + actual-runtime bridge.

Runs the real pinned `inspect_ai` (0.3.269, model-less `none/none`) and the real FTF-04A26
`IsolatedToolBackendAdapter` over the real FTF-1702 reference tool backend. No database is needed
here (the case rows are ORM `EvaluationCaseRecord`-shaped objects and the loader's store calls are
replaced with fakes only to test the loader's own refusals); the real-Postgres case read is
tests/integration/test_inspect_target_bridge.py.

Acceptance ("Use qualified Agent Bridge or actual-runtime adapter; execute original orchestration
and real isolated dependencies with identity pinning."):
test_samples_carry_case_key_and_metadata (AC1),
test_loader_refuses_draft_final_and_unknown_purpose_before_reading_cases (AC2, fault FA/FB),
test_loader_pages_and_refuses_a_cursor_cycle (AC2b),
test_duplicate_case_ids_and_invalid_inputs_are_refused (AC2c),
test_run_goes_through_the_native_adapter_under_real_inspect (AC3),
test_attestation_is_pinned_and_rechecked_after_every_case (AC4, fault FC),
test_unsupported_binding_key_entry_kind_and_purpose_are_refused (AC5, fault FD),
test_hook_applies_p2_and_p1_to_model_traffic (AC6, fault FE/FF),
test_inspect_generation_forwarding_is_captured_not_assumed (AC7),
test_run_executes_under_network_denial (AC8, fault FG).

Negative acceptance ("A reconstructed generic agent ... cannot pass"):
test_reconstructed_adapter_without_all_parity_categories_cannot_pass (NEG1, fault FH),
test_a_generic_agent_solver_cannot_pass (NEG2, fault FI),
test_no_remote_destination_no_telemetry_and_private_log_dir (NEG3),
test_result_and_errors_are_content_free (NEG4).

Fault injection (Law 1): the table at the bottom rewrites the real modules in a fresh
subprocess; each behavioural probe key must change.

Run directly: python3 tests/tasks/test_ftf_r004_p3.py
The fault table re-runs the real modules mutated, one probe subprocess per row (slow); it is
opt-in so the mutation gate (which re-runs this file once per mutant) stays fast:
python3 tests/tasks/test_ftf_r004_p3.py --faults
"""

from __future__ import annotations

import json
import os
import re
import shutil
import socket
import stat
import subprocess
import sys
import tempfile
import threading
import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "apps" / "api"))

from app.target_connectors.isolated_real_tool_backend_adapter_contract_tests import (  # noqa: E402
    build_isolated_tool_backend_adapter,
)
from app.target_connectors.production_faithful_target_agent_harness_contract import (  # noqa: E402
    ModelBinding,
    ParityCaseCategory,
    ReconstructedAdapterDeclaration,
)

import workers.evaluation.adapters.inspect_adapter.case_adapter as case_adapter  # noqa: E402
import workers.evaluation.adapters.inspect_adapter.runtime_bridge as bridge  # noqa: E402
from workers.evaluation.adapters.inspect_adapter.case_adapter import (  # noqa: E402
    CaseAdapterError,
    load_locked_development_suite,
    samples_from_cases,
)
from workers.evaluation.adapters.inspect_adapter.provider_boundary import (  # noqa: E402
    ScreeningPolicy,
)
from workers.evaluation.adapters.inspect_adapter.runtime_bridge import (  # noqa: E402
    BridgeError,
    BridgeHook,
    observe_inspect_generation_forwarding,
    run_suite_through_runtime,
)

CHECKS = [0]
FAILURES: list[str] = []
CANARY = "CANARY-9f3a-do-not-leak"
MODULES = {
    "bridge": ROOT / "workers/evaluation/adapters/inspect_adapter/runtime_bridge.py",
    "cases": ROOT / "workers/evaluation/adapters/inspect_adapter/case_adapter.py",
}


def check(criterion_id: str, description: str, condition: bool) -> None:
    CHECKS[0] += 1
    if not condition:
        FAILURES.append(f"FAIL [{criterion_id}] {description}")


def _code(fn: Any, *args: Any, **kwargs: Any) -> str:
    try:
        fn(*args, **kwargs)
    except (BridgeError, CaseAdapterError) as exc:
        return exc.code
    except BaseException as exc:  # noqa: BLE001
        return f"OTHER:{type(exc).__name__}"
    return "NO_RAISE"


# --------------------------------------------------------------------------- fixtures


def _record(key: str, tool: str, subject: str, arguments: dict[str, Any], **extra: Any):
    values: dict[str, Any] = {
        "id": uuid.uuid4(),
        "case_key": key,
        "category": "retrieval",
        "severity": "high",
        "critical": False,
        "group_key": f"group-{key}",
        "language": "en",
        "grader_type": "exact_match",
        "input_json": {"tool_name": tool, "subject_id": subject, "arguments": arguments},
        "expected_json": {"expected": CANARY},
    }
    values.update(extra)
    return SimpleNamespace(**values)


def _records() -> list[Any]:
    return [
        _record("case-001", "search_records", "subject-a1", {"query": "quarterly"}),
        _record("case-002", "search_records", "subject-a2", {"query": "vendor"}),
    ]


SUITE_ID = str(uuid.uuid4())
PROJECT_ID = str(uuid.uuid4())
MANIFEST_ID = str(uuid.uuid4())


def _case_set(records: list[Any] | None = None, *, purpose: str = "development"):
    return samples_from_cases(
        suite_id=SUITE_ID,
        project_id=PROJECT_ID,
        agent_manifest_id=MANIFEST_ID,
        purpose=purpose,
        grading_logic_version=1,
        records=_records() if records is None else records,
    )


def _hook(approved: dict[str, Any] | None = None) -> BridgeHook:
    return BridgeHook(
        policy=ScreeningPolicy("standard", "standard", None, frozenset({"search_records"})),
        approved_settings=approved if approved is not None else {},
    )


def _run(adapter: Any = None, case_set: Any = None, **overrides: Any):
    kwargs: dict[str, Any] = {
        "hook": _hook(),
        "entry_kind": "signed_container",
        "is_reconstructed_adapter": False,
        "model_binding": ModelBinding("n/a", "n/a"),
        "tenant_namespace": "tenant-a",
        "deadline_seconds": 30.0,
        "run_id": "run-1",
    }
    kwargs.update(overrides)
    return run_suite_through_runtime(
        adapter if adapter is not None else build_isolated_tool_backend_adapter(),
        case_set if case_set is not None else _case_set(),
        **kwargs,
    )


class _Spy:
    """Delegates every operation to a real adapter; hooks let a test change what the adapter does."""

    def __init__(self, inner: Any, **hooks: Any) -> None:
        self.inner = inner
        self.hooks = hooks
        self.invokes: list[Any] = []
        self.preflights: list[Any] = []
        self.attests = 0
        self.bound: Any = None

    def describe(self):
        import dataclasses

        d = self.inner.describe()
        if self.hooks.get("describe_drift") and self.invokes:
            return dataclasses.replace(d, runtime_digest="drifted-runtime-digest")
        return d

    def preflight(self, request):
        self.preflights.append(request)
        return self.hooks.get("preflight", lambda r: self.inner.preflight(r))(request)

    def invoke(self, request):
        self.invokes.append(request)
        if "on_invoke" in self.hooks:
            self.hooks["on_invoke"](request)
        return self.inner.invoke(request)

    def cancel(self, case_id):
        return self.inner.cancel(case_id)

    def collect_outcome(self, case_id):
        if "collect" in self.hooks:
            return self.hooks["collect"](case_id, self.inner)
        return self.inner.collect_outcome(case_id)

    def reset(self, tenant_namespace):
        return self.inner.reset(tenant_namespace)

    def attest(self):
        import dataclasses

        self.attests += 1
        a = self.inner.attest()
        if self.hooks.get("attest_drift") and self.invokes:
            return dataclasses.replace(a, prompt_fingerprint="d" * 64)
        return a


class _BindingSpy(_Spy):
    def bind_bridge_hook(self, hook: Any) -> None:
        self.bound = hook


# --------------------------------------------------------------------------- AC1-AC2: case adapter


def test_samples_carry_case_key_and_metadata() -> None:
    cs = _case_set()
    ids = [s.id for s in cs.samples]
    check("FTF-R004-P3-AC1", "Sample id is the case_key, in order", ids == ["case-001", "case-002"])
    meta = cs.samples[0].metadata
    check(
        "FTF-R004-P3-AC1",
        "metadata carries case_id, category, severity, critical, group_key, language, grader_type and suite/manifest ids",
        meta
        == {
            "case_id": str(_records()[0].id) if False else meta["case_id"],
            "category": "retrieval",
            "severity": "high",
            "critical": False,
            "group_key": "group-case-001",
            "language": "en",
            "grader_type": "exact_match",
            "suite_id": SUITE_ID,
            "agent_manifest_id": MANIFEST_ID,
        }
        and uuid.UUID(meta["case_id"]).version == 4,
    )
    check(
        "FTF-R004-P3-AC1",
        "declared_inputs is an immutable tuple of pairs (nested objects frozen too)",
        cs.cases[0].declared_inputs
        == (
            ("tool_name", "search_records"),
            ("subject_id", "subject-a1"),
            ("arguments", (("query", "quarterly"),)),
        ),
    )
    check(
        "FTF-R004-P3-AC1",
        "the bridge case never carries the expected value",
        CANARY not in repr(cs.cases),
    )
    check("FTF-R004-P3-AC1", "suite ids are carried", cs.suite_id == SUITE_ID)


class _FakeSuite:
    def __init__(self, purpose: str, status: str) -> None:
        self.id = uuid.UUID(SUITE_ID)
        self.project_id = uuid.UUID(PROJECT_ID)
        self.agent_manifest_id = uuid.UUID(MANIFEST_ID)
        self.purpose = purpose
        self.status = status
        self.grading_logic_version = 3


def _patch_store(suite: Any, pages: list[tuple[list[Any], str | None]], calls: list[Any]):
    originals = (case_adapter.get_evaluation_suite, case_adapter.list_cases)
    page_iter = iter(pages)

    def fake_get(session, context, project_id, suite_id):
        return suite

    def fake_list(session, context, **kwargs):
        calls.append(kwargs)
        return next(page_iter)

    case_adapter.get_evaluation_suite = fake_get
    case_adapter.list_cases = fake_list
    return originals


def _restore_store(originals: Any) -> None:
    case_adapter.get_evaluation_suite, case_adapter.list_cases = originals


def _load(suite: Any, pages: list[tuple[list[Any], str | None]], calls: list[Any]):
    originals = _patch_store(suite, pages, calls)
    try:
        return load_locked_development_suite(
            None, None, project_id=uuid.UUID(PROJECT_ID), suite_id=uuid.UUID(SUITE_ID), role="x"
        )
    finally:
        _restore_store(originals)


def test_loader_refuses_draft_final_and_unknown_purpose_before_reading_cases() -> None:
    for purpose, status, expected, label in (
        ("development", "draft", "suite_not_locked", "draft"),
        ("final", "locked", "final_suite_requires_freeze", "locked final"),
        ("final", "draft", "final_suite_requires_freeze", "draft final"),
        ("holdout", "locked", "unsupported_suite_purpose", "unknown purpose"),
    ):
        calls: list[Any] = []
        originals = _patch_store(_FakeSuite(purpose, status), [(_records(), None)], calls)
        try:
            code = _code(
                load_locked_development_suite,
                None,
                None,
                project_id=uuid.UUID(PROJECT_ID),
                suite_id=uuid.UUID(SUITE_ID),
                role="x",
            )
        finally:
            _restore_store(originals)
        check("FTF-R004-P3-AC2", f"{label} suite -> {expected} (got {code})", code == expected)
        check("FTF-R004-P3-AC2", f"{label} suite: no case row was read", calls == [])
    good = _load(_FakeSuite("development", "locked"), [(_records(), None)], [])
    check(
        "FTF-R004-P3-AC2",
        "a locked development suite loads with its ids and grading version",
        good.purpose == "development"
        and good.grading_logic_version == 3
        and len(good.cases) == 2
        and good.agent_manifest_id == MANIFEST_ID,
    )


def test_loader_pages_and_refuses_a_cursor_cycle() -> None:
    a, b = _records()
    calls: list[Any] = []
    loaded = _load(
        _FakeSuite("development", "locked"), [([a], "case-001"), ([b], None)], calls
    )
    check(
        "FTF-R004-P3-AC2",
        "pages are followed with the store's own cursor until it ends",
        [c.case_key for c in loaded.cases] == ["case-001", "case-002"]
        and [c["after"] for c in calls] == [None, "case-001"]
        and all(c["limit"] == 500 for c in calls),
    )
    cyc_calls: list[Any] = []
    originals = _patch_store(
        _FakeSuite("development", "locked"),
        [([a], "case-001"), ([b], "case-001"), ([a], "case-001")],
        cyc_calls,
    )
    try:
        code = _code(
            load_locked_development_suite,
            None,
            None,
            project_id=uuid.UUID(PROJECT_ID),
            suite_id=uuid.UUID(SUITE_ID),
            role="x",
        )
    finally:
        _restore_store(originals)
    check(
        "FTF-R004-P3-AC2",
        f"a cursor that does not advance is refused, not looped (got {code})",
        code == "invalid_case" and len(cyc_calls) == 2,
    )


def test_duplicate_case_ids_and_invalid_inputs_are_refused() -> None:
    one = _records()[0]
    dup_key = SimpleNamespace(**{**vars(one), "id": uuid.uuid4()})
    dup_id = SimpleNamespace(**{**vars(one), "case_key": "case-other"})
    for label, records, expected in (
        ("duplicate case_key", [one, dup_key], "duplicate_case_id"),
        ("duplicate case id", [one, dup_id], "duplicate_case_id"),
        ("empty suite", [], "empty_suite"),
        ("input not an object", [_record("c", "t", "s", {}, input_json=["x"])], "invalid_case"),
        (
            "non-string key",
            [_record("c", "t", "s", {}, input_json={1: "x"})],
            "invalid_case",
        ),
        (
            "unsupported value",
            [_record("c", "t", "s", {}, input_json={"a": {1, 2}})],
            "invalid_case",
        ),
        (
            "bytes value",
            [_record("c", "t", "s", {}, input_json={"a": b"x"})],
            "invalid_case",
        ),
        ("blank key", [_record("", "t", "s", {})], "invalid_case"),
        ("non-bool critical", [_record("c", "t", "s", {}, critical=1)], "invalid_case"),
    ):
        code = _code(_case_set, records)
        check("FTF-R004-P3-AC2", f"{label} -> {expected} (got {code})", code == expected)
    deep: Any = "x"
    for _ in range(80):
        deep = {"a": deep}
    check(
        "FTF-R004-P3-AC2",
        "an over-deep input is refused, not a RecursionError",
        _code(_case_set, [_record("c", "t", "s", {}, input_json=deep)]) == "invalid_case",
    )


# --------------------------------------------------------------------------- AC3-AC5: bridge


def test_run_goes_through_the_native_adapter_under_real_inspect() -> None:
    spy = _Spy(build_isolated_tool_backend_adapter())
    result = _run(spy)
    check(
        "FTF-R004-P3-AC3",
        "both cases ran through preflight then invoke on the real adapter",
        len(spy.preflights) == 2 and len(spy.invokes) == 2,
    )
    req = spy.invokes[0]
    check(
        "FTF-R004-P3-AC3",
        "the request carries the case, tenant, manifest, immutable binding, inputs, deadline and key",
        req.case_id == "case-001"
        and req.tenant_namespace == "tenant-a"
        and req.agent_manifest_id == MANIFEST_ID
        and req.model_binding == ModelBinding("n/a", "n/a")
        and req.declared_inputs[0] == ("tool_name", "search_records")
        and req.deadline_seconds == 30.0
        and re.fullmatch(r"[0-9a-f]{64}", req.idempotency_key) is not None,
    )
    check(
        "FTF-R004-P3-AC3",
        "idempotency keys differ per case and per run, and repeat for the same run and case",
        spy.invokes[0].idempotency_key != spy.invokes[1].idempotency_key
        and _run(_Spy(build_isolated_tool_backend_adapter()), run_id="run-2").log_sha256 != ""
        and _key("run-1", "case-001") == spy.invokes[0].idempotency_key
        and _key("run-2", "case-001") != spy.invokes[0].idempotency_key,
    )
    statuses = {c.case_key: c.status for c in result.cases}
    check(
        "FTF-R004-P3-AC3",
        "case 1 completed with a business-outcome digest; case 2 (subject-a2 has no seeded access to the query) is recorded either way, never dropped",
        statuses["case-001"] == "completed"
        and result.cases[0].business_outcome_sha256 is not None
        and result.cases[0].collected_matches_invoke
        and len(result.cases) == 2,
    )
    import inspect_ai

    from inspect_ai.log import read_eval_log

    log = read_eval_log(result.log_path)
    check(
        "FTF-R004-P3-AC3",
        "the real Inspect log shows exactly one native solver, model none/none, two samples with scores",
        [s.solver for s in log.plan.steps] == ["native_runtime_adapter"]
        and log.eval.model == "none/none"
        and len(log.samples) == 2
        and all(s.scores for s in log.samples)
        and result.inspect_version == inspect_ai.__version__ == "0.3.269",
    )
    import hashlib

    check(
        "FTF-R004-P3-AC3",
        "log_sha256 is the sha256 of the log file as written",
        result.log_sha256 == hashlib.sha256(Path(result.log_path).read_bytes()).hexdigest(),
    )
    # preflight refusal: the case is recorded and invoke never runs for it
    refused = _Spy(build_isolated_tool_backend_adapter(), preflight=lambda r: False)
    res = _run(refused)
    check(
        "FTF-R004-P3-AC3",
        "a preflight refusal is recorded as preflight_refused and invoke is never called",
        refused.invokes == [] and {c.status for c in res.cases} == {"preflight_refused"},
    )
    # adapter exception: typed, content-free row
    boom = _Spy(
        build_isolated_tool_backend_adapter(),
        on_invoke=lambda r: (_ for _ in ()).throw(RuntimeError(CANARY)),
    )
    res = _run(boom)
    check(
        "FTF-R004-P3-AC3",
        "an adapter exception is a typed row naming the exception class only",
        all(
            c.status == "adapter_exception" and c.adapter_exception_type == "RuntimeError"
            for c in res.cases
        )
        and CANARY not in repr(res),
    )
    mismatched = _Spy(
        build_isolated_tool_backend_adapter(),
        collect=lambda cid, inner: _relabel(inner.collect_outcome(cid)),
    )
    check(
        "FTF-R004-P3-AC3",
        "an adapter answering for another case is refused",
        _code(_run, mismatched) == "adapter_case_mismatch",
    )


def _relabel(result: Any) -> Any:
    import dataclasses

    return dataclasses.replace(result, case_id="some-other-case")


def _key(run_id: str, case_key: str) -> str:
    import hashlib

    return hashlib.sha256(f"{run_id}\0{case_key}".encode()).hexdigest()


def test_attestation_is_pinned_and_rechecked_after_every_case() -> None:
    drift = _Spy(build_isolated_tool_backend_adapter(), attest_drift=True)
    check(
        "FTF-R004-P3-AC4",
        "an attestation that changes after the first case -> runtime_identity_changed",
        _code(_run, drift) == "runtime_identity_changed",
    )
    check(
        "FTF-R004-P3-AC4",
        "the run stopped checking cases as soon as identity changed (second invoke never ran)",
        len(drift.invokes) == 1,
    )
    describe_drift = _Spy(build_isolated_tool_backend_adapter(), describe_drift=True)
    check(
        "FTF-R004-P3-AC4",
        "a changed describe() digest -> runtime_identity_changed",
        _code(_run, describe_drift) == "runtime_identity_changed",
    )
    steady = _Spy(build_isolated_tool_backend_adapter())
    _run(steady)
    check(
        "FTF-R004-P3-AC4",
        "attest() is called before the run and around every case (1 pin + 2 per case + 1 final)",
        steady.attests == 1 + 2 * 2 + 1,
    )
    r = _run()
    check(
        "FTF-R004-P3-AC4",
        "the pinned attestation is in the result and observed time alone does not count as drift",
        r.pinned_attestation.source_fingerprint != "" and r.network_attempts == 0,
    )


def test_unsupported_binding_key_entry_kind_and_purpose_are_refused() -> None:
    check(
        "FTF-R004-P3-AC5",
        "a route_params key the adapter never declared -> unsupported_model_binding_key",
        _code(_run, model_binding=ModelBinding("n/a", "n/a", (("top_secret_knob", "1"),)))
        == "unsupported_model_binding_key",
    )
    ok = _run(model_binding=ModelBinding("n/a", "n/a", (("temperature", "0.1"),)))
    check("FTF-R004-P3-AC5", "a declared binding key runs", len(ok.cases) == 2)
    check(
        "FTF-R004-P3-AC5",
        "an unsupported runtime entry kind -> unsupported_entry_kind",
        _code(_run, entry_kind="ssh_shell") == "unsupported_entry_kind",
    )
    check(
        "FTF-R004-P3-AC5",
        "a final-purpose case set is refused by the bridge itself",
        _code(_run, case_set=_case_set(purpose="final")) == "bridge_invalid_request",
    )
    check(
        "FTF-R004-P3-AC5",
        "a hook is required (None is refused, nothing is bypassed by omission)",
        _code(_run, hook=None) == "bridge_invalid_request",
    )
    bound = _BindingSpy(build_isolated_tool_backend_adapter())
    hook = _hook()
    _run(bound, hook=hook)
    check(
        "FTF-R004-P3-AC5",
        "a model-calling adapter receives the run's hook through bind_bridge_hook",
        bound.bound is hook,
    )


# --------------------------------------------------------------------------- AC6-AC7: P1/P2 hook


def _chat(**extra: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "model": "m",
        "messages": [{"role": "user", "content": "hi"}],
        "tools": [
            {"type": "function", "function": {"name": "search_records", "parameters": {"type": "object"}}}
        ],
        "temperature": 0.2,
        "max_tokens": 64,
    }
    body.update(extra)
    return body


def _response(calls: list[tuple[str, str]]) -> dict[str, Any]:
    return {
        "id": "x",
        "object": "chat.completion",
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": "ok",
                    "tool_calls": [
                        {"id": cid, "type": "function", "function": {"name": name, "arguments": "{}"}}
                        for cid, name in calls
                    ],
                },
                "finish_reason": "tool_calls",
            }
        ],
    }


def test_hook_applies_p2_and_p1_to_model_traffic() -> None:
    hook = _hook({"temperature": 0.2, "max_tokens": 64})
    hook.screen_request(_chat())
    check("FTF-R004-P3-AC6", "an approved function tool request passes P2 screening", hook.requests_screened == 1)
    for label, body in (
        ("hosted web_search tool", _chat(tools=[{"type": "web_search_preview"}])),
        ("mcp tool", _chat(tools=[{"type": "mcp", "server_label": "x", "server_url": "https://x"}])),
        ("remote image", _chat(messages=[{"role": "user", "content": [{"type": "image_url", "image_url": {"url": "https://x.test/a.png"}}]}])),
        (
            "unapproved function tool",
            _chat(tools=[{"type": "function", "function": {"name": "delete_everything", "parameters": {"type": "object"}}}]),
        ),
    ):
        h = _hook()
        code = _code(h.screen_request, body)
        check("FTF-R004-P3-AC6", f"{label} -> provider_boundary_denied (got {code})", code == "provider_boundary_denied")
        check("FTF-R004-P3-AC6", f"{label}: the denial is counted", h.requests_denied == 1)
    check(
        "FTF-R004-P3-AC6",
        "a malformed body is refused with the boundary's own code, never a bare exception",
        _code(_hook().screen_request, ["not", "an", "object"]) == "provider_boundary_denied",
    )
    try:
        _hook().screen_request(_chat(tools=[{"type": "web_search_preview"}]))
    except BridgeError as exc:
        check("FTF-R004-P3-AC6", "the denial detail names codes and paths only", "web_search" not in str(exc) and "tools[0]" in str(exc))

    h2 = _hook()
    outcome = h2.capture_and_filter_response(_response([("call_1", "search_records"), ("call_2", "wire_money")]))
    check(
        "FTF-R004-P3-AC6",
        "every attempted call is captured before filtering; the unapproved one is removed from the output but kept in the evidence",
        h2.attempts_captured == 2
        and outcome.removed_call_ids == ("call_2",)
        and [a.name for a in h2.attempt_evidence()[0].attempts] == ["search_records", "wire_money"]
        and len(outcome.body["choices"][0]["message"]["tool_calls"]) == 1,
    )
    check(
        "FTF-R004-P3-AC6",
        "a malformed response is refused with the boundary's code",
        _code(_hook().capture_and_filter_response, "not-json-object") == "provider_boundary_denied",
    )

    # P1 through the hook, with the pinned Inspect release's real behaviour
    dropped = _hook({"temperature": 0.2, "max_tokens": 64})
    code = _code(dropped.check_generation_parity, _chat(), forward_generation_config=False)
    check(
        "FTF-R004-P3-AC6",
        f"Inspect's default (forward_generation_config=False) drops the client's settings -> blocked (got {code})",
        code == "generation_config_parity_blocked",
    )
    kept = _hook({"temperature": 0.2, "max_tokens": 64})
    result = kept.check_generation_parity(_chat(), forward_generation_config=True)
    check(
        "FTF-R004-P3-AC6",
        "forward_generation_config=True preserves them and the check is counted",
        result.verdict == "preserved" and kept.parity_checks == 1,
    )
    changed = _hook({"temperature": 0.7, "max_tokens": 64})
    check(
        "FTF-R004-P3-AC6",
        "a request whose setting differs from the approved baseline is blocked even when forwarded",
        _code(changed.check_generation_parity, _chat(), forward_generation_config=True)
        == "generation_config_parity_blocked",
    )


def test_inspect_generation_forwarding_is_captured_not_assumed() -> None:
    body = _chat(top_p=0.9, seed=7, stop=["END"], n=2, max_completion_tokens=99)
    del body["max_tokens"]
    off = observe_inspect_generation_forwarding(body, forward_generation_config=False)
    on = observe_inspect_generation_forwarding(body, forward_generation_config=True)
    check(
        "FTF-R004-P3-AC7",
        f"forward_generation_config=False keeps only what Inspect does not treat as a generation parameter (seed, stop): {dict(off)}",
        dict(off) == {"seed": 7, "stop": ["END"]},
    )
    check(
        "FTF-R004-P3-AC7",
        f"forward_generation_config=True keeps every one, under the request's own key names: {dict(on)}",
        dict(on)
        == {
            "temperature": 0.2,
            "top_p": 0.9,
            "seed": 7,
            "stop": ["END"],
            "n": 2,
            "max_completion_tokens": 99,
        },
    )
    check(
        "FTF-R004-P3-AC7",
        "a non-mapping request is refused with the bridge's code",
        _code(observe_inspect_generation_forwarding, "x", forward_generation_config=True)
        == "bridge_invalid_request",
    )


# --------------------------------------------------------------------------- AC8: network denial


def test_run_executes_under_network_denial() -> None:
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.bind(("127.0.0.1", 0))
    server.listen(5)
    server.settimeout(1.0)
    port = server.getsockname()[1]
    accepted: list[Any] = []

    def accept() -> None:
        try:
            accepted.append(server.accept())
        except OSError:
            pass

    thread = threading.Thread(target=accept)
    thread.start()

    def phone_home(_request: Any) -> None:
        try:
            socket.create_connection(("127.0.0.1", port), timeout=1).close()
        except BridgeError:
            raise
        except OSError:
            pass

    real_connect = socket.socket.connect
    try:
        spy = _Spy(build_isolated_tool_backend_adapter(), on_invoke=phone_home)
        code = _code(_run, spy)
    finally:
        thread.join(5)
        server.close()
    check(
        "FTF-R004-P3-AC8",
        f"an adapter that opens a connection during the run -> outbound_call_blocked (got {code})",
        code == "outbound_call_blocked",
    )
    check("FTF-R004-P3-AC8", "the listening server never saw the connection", accepted == [])
    check(
        "FTF-R004-P3-AC8",
        "the socket functions are restored after a refused run",
        socket.socket.connect is real_connect and socket.create_connection.__module__ == "socket",
    )
    swallowing = _Spy(
        build_isolated_tool_backend_adapter(),
        on_invoke=lambda r: _swallow(lambda: socket.getaddrinfo("example.invalid", 80)),
    )
    check(
        "FTF-R004-P3-AC8",
        "a name lookup is an outbound call even when the adapter swallows the error",
        _code(_run, swallowing) == "outbound_call_blocked",
    )
    unix = _Spy(build_isolated_tool_backend_adapter())
    ok = _run(unix)
    check(
        "FTF-R004-P3-AC8",
        "the default run needs no network at all and reports 0 attempts",
        ok.network_attempts == 0 and len(ok.cases) == 2,
    )


def _swallow(fn: Any) -> None:
    try:
        fn()
    except Exception:  # noqa: BLE001
        pass


# --------------------------------------------------------------------------- negatives


def test_reconstructed_adapter_without_all_parity_categories_cannot_pass() -> None:
    check(
        "FTF-R004-P3-NEG1",
        "a reconstructed adapter with no declaration -> integration_required",
        _code(_run, is_reconstructed_adapter=True) == "integration_required",
    )
    partial = ReconstructedAdapterDeclaration(
        "custom", frozenset({ParityCaseCategory.SUCCESS, ParityCaseCategory.TIMEOUT})
    )
    spy = _Spy(build_isolated_tool_backend_adapter())
    check(
        "FTF-R004-P3-NEG1",
        "missing parity categories -> integration_required and the adapter is never invoked",
        _code(_run, spy, is_reconstructed_adapter=True, reconstructed_declaration=partial)
        == "integration_required"
        and spy.invokes == [],
    )
    full = ReconstructedAdapterDeclaration("custom", frozenset(ParityCaseCategory))
    check(
        "FTF-R004-P3-NEG1",
        "all six parity categories declared -> the run is allowed",
        len(_run(is_reconstructed_adapter=True, reconstructed_declaration=full).cases) == 2,
    )


def test_a_generic_agent_solver_cannot_pass() -> None:
    from inspect_ai.agent import react
    from inspect_ai.solver import system_message

    original = bridge._build_solver
    try:
        bridge._build_solver = lambda state: react(prompt="be helpful")
        generic = _code(_run)
        bridge._build_solver = lambda state: [system_message("x"), original(state)]
        chained = _code(_run)
    finally:
        bridge._build_solver = original
    check(
        "FTF-R004-P3-NEG2",
        f"the solver swapped for react() -> generic_agent_solver_forbidden (got {generic})",
        generic == "generic_agent_solver_forbidden",
    )
    check(
        "FTF-R004-P3-NEG2",
        f"the native solver chained after another solver is refused too (got {chained})",
        chained == "generic_agent_solver_forbidden",
    )
    import ast

    tree = ast.parse(MODULES["bridge"].read_text(encoding="utf-8"))
    forbidden = {"react", "basic_agent", "mockllm", "MockLLM", "get_model", "generate"}
    used = {
        n.func.id if isinstance(n.func, ast.Name) else n.func.attr
        for n in ast.walk(tree)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name | ast.Attribute)
    }
    imported = {
        a.name.split(".")[-1]
        for n in ast.walk(tree)
        if isinstance(n, ast.Import | ast.ImportFrom)
        for a in n.names
    }
    check(
        "FTF-R004-P3-NEG2",
        "the bridge source never calls or imports a generic agent, a mock model or generate()",
        not (forbidden & used) and not (forbidden & imported),
    )


def test_no_remote_destination_no_telemetry_and_private_log_dir() -> None:
    saved = {k: os.environ.get(k) for k in ("INSPECT_TELEMETRY", "INSPECT_REQUIRED_HOOKS", "INSPECT_LOG_DIR", "INSPECT_EVAL_LOG_DIR")}
    try:
        for name in ("INSPECT_TELEMETRY", "INSPECT_REQUIRED_HOOKS"):
            os.environ[name] = "some_module"
            code = _code(_run)
            check("FTF-R004-P3-NEG3", f"{name} set -> telemetry_configured (got {code})", code == "telemetry_configured")
            del os.environ[name]
        os.environ["INSPECT_LOG_DIR"] = "s3://prod-logs/inspect"
        os.environ["INSPECT_EVAL_LOG_DIR"] = "s3://prod-logs/inspect"
        res = _run()
    finally:
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
    mode = stat.S_IMODE(os.stat(res.log_dir).st_mode)
    check(
        "FTF-R004-P3-NEG3",
        "an ambient remote INSPECT_LOG_DIR is ignored: the log is a local file in a private 0700 workspace",
        mode == 0o700
        and Path(res.log_path).parent == Path(res.log_dir)
        and Path(res.log_path).is_file()
        and "s3://" not in res.log_path,
    )
    parent = Path(tempfile.mkdtemp(prefix="p3-parent-"))
    try:
        res2 = _run(workspace_parent=parent)
        check(
            "FTF-R004-P3-NEG3",
            "an explicit workspace parent is honoured and each run gets its own directory",
            Path(res2.log_dir).parent == parent and res2.log_dir != res.log_dir,
        )
    finally:
        shutil.rmtree(parent, ignore_errors=True)


def test_result_and_errors_are_content_free() -> None:
    res = _run(case_set=_case_set([_record("case-x", "search_records", "subject-a1", {"query": CANARY})]))
    check(
        "FTF-R004-P3-NEG4",
        "the run result holds ids, statuses and digests only -- no input or expected content",
        CANARY not in repr(res) and CANARY not in repr(res.cases),
    )
    for exc in (
        _exc(_run, model_binding=ModelBinding("n/a", "n/a", ((CANARY, "1"),))),
        _exc(_run, hook=None),
    ):
        check("FTF-R004-P3-NEG4", "refusals never echo caller content", CANARY not in str(exc))
    check(
        "FTF-R004-P3-NEG4",
        "a case-adapter refusal never echoes case content",
        CANARY not in str(_exc(_case_set, [_record("c", "t", "s", {}, input_json={"a": {CANARY}})])),
    )


def _exc(fn: Any, *args: Any, **kwargs: Any) -> BaseException:
    try:
        fn(*args, **kwargs)
    except BaseException as exc:  # noqa: BLE001
        return exc
    return RuntimeError("no raise")


# --------------------------------------------------------------------------- fault injection

_PROBE = r"""
import json, socket, sys, uuid, dataclasses
sys.path.insert(0, {root!r}); sys.path.insert(0, {api!r})
from types import SimpleNamespace as NS
import workers.evaluation.adapters.inspect_adapter.case_adapter as ca
from workers.evaluation.adapters.inspect_adapter.runtime_bridge import *
import workers.evaluation.adapters.inspect_adapter.runtime_bridge as rb
from workers.evaluation.adapters.inspect_adapter.provider_boundary import ScreeningPolicy
from app.target_connectors.isolated_real_tool_backend_adapter_contract_tests import build_isolated_tool_backend_adapter
from app.target_connectors.production_faithful_target_agent_harness_contract import ModelBinding, ParityCaseCategory, ReconstructedAdapterDeclaration

def code(fn, *a, **k):
    try:
        fn(*a, **k)
    except (BridgeError, ca.CaseAdapterError) as e:
        return e.code
    except BaseException as e:
        return "OTHER:" + type(e).__name__
    return "NO_RAISE"

def rec(key, **extra):
    v = dict(id=uuid.uuid4(), case_key=key, category="c", severity="low", critical=False, group_key="g", language="en",
             grader_type="exact_match", input_json={{"tool_name": "search_records", "subject_id": "subject-a1", "arguments": {{"query": "q"}}}},
             expected_json={{"x": 1}})
    v.update(extra)
    return NS(**v)

def cs(recs=None, purpose="development"):
    return ca.samples_from_cases(suite_id="s", project_id="p", agent_manifest_id="m", purpose=purpose, grading_logic_version=1,
                                 records=recs if recs is not None else [rec("k1"), rec("k2")])

def hook(approved=None):
    return BridgeHook(policy=ScreeningPolicy("standard","standard",None,frozenset({{"search_records"}})), approved_settings=approved or {{}})

class Spy:
    def __init__(self, inner, **h):
        self.inner = inner; self.h = h; self.invokes = []
    def describe(self):
        d = self.inner.describe()
        if self.h.get("describe_drift") and self.invokes:
            return dataclasses.replace(d, runtime_digest="x")
        return d
    def preflight(self, r): return self.h.get("preflight", self.inner.preflight)(r)
    def invoke(self, r):
        self.invokes.append(r)
        if "on_invoke" in self.h: self.h["on_invoke"](r)
        return self.inner.invoke(r)
    def cancel(self, c): return self.inner.cancel(c)
    def collect_outcome(self, c):
        if "collect" in self.h: return self.h["collect"](c, self.inner)
        return self.inner.collect_outcome(c)
    def reset(self, t): return self.inner.reset(t)
    def attest(self):
        a = self.inner.attest()
        if self.h.get("attest_drift") and self.invokes:
            return dataclasses.replace(a, prompt_fingerprint="d" * 64)
        return a

def run(adapter=None, cset=None, **o):
    kw = dict(hook=hook(), entry_kind="signed_container", is_reconstructed_adapter=False, model_binding=ModelBinding("n/a","n/a"),
              tenant_namespace="tenant-a", deadline_seconds=30.0, run_id="r1")
    kw.update(o)
    return run_suite_through_runtime(adapter or build_isolated_tool_backend_adapter(), cset or cs(), **kw)

def chat(**extra):
    b = {{"model":"m","messages":[{{"role":"user","content":"hi"}}],
          "tools":[{{"type":"function","function":{{"name":"search_records","parameters":{{"type":"object"}}}}}}],
          "temperature":0.2,"max_tokens":64}}
    b.update(extra); return b

def resp(calls):
    return {{"id":"x","object":"chat.completion","choices":[{{"index":0,"message":{{"role":"assistant","content":"ok",
      "tool_calls":[{{"id":c,"type":"function","function":{{"name":n,"arguments":"{{}}"}}}} for c,n in calls]}},"finish_reason":"tool_calls"}}]}}

out = {{}}
# case adapter
out["dup_key"] = code(cs, [rec("a"), rec("a", id=uuid.uuid4())])
out["dup_id"] = code(cs, [rec("a"), rec("b", id=None)]) if False else code(cs, [rec("a", id=uuid.UUID(int=1)), rec("b", id=uuid.UUID(int=1))])
out["empty"] = code(cs, [])
out["input_list"] = code(cs, [rec("a", input_json=["x"])])
out["input_set"] = code(cs, [rec("a", input_json={{"a": {{1}}}})])
out["input_intkey"] = code(cs, [rec("a", input_json={{1: "x"}})])
out["crit_int"] = code(cs, [rec("a", critical=1)])
out["blank_cat"] = code(cs, [rec("a", category="")])
deep = "x"
for _ in range(80): deep = {{"a": deep}}
out["deep"] = code(cs, [rec("a", input_json=deep)])
c0 = cs()
out["sample_ids"] = [s.id for s in c0.samples]
out["sample_meta_keys"] = sorted(c0.samples[0].metadata)
out["sample_meta_suite"] = c0.samples[0].metadata["suite_id"] + c0.samples[0].metadata["agent_manifest_id"]
out["sample_meta_crit"] = c0.samples[0].metadata["critical"]
out["frozen"] = repr(c0.cases[0].declared_inputs)
out["target_leak"] = "expected" in repr(c0.cases)
# loader
class Suite:
    def __init__(self, p, s):
        self.id = uuid.UUID(int=1); self.project_id = uuid.UUID(int=2); self.agent_manifest_id = uuid.UUID(int=3)
        self.purpose = p; self.status = s; self.grading_logic_version = 3
def load(p, s, pages):
    calls = []
    it = iter(pages)
    ca.get_evaluation_suite = lambda *a, **k: Suite(p, s)
    def fl(*a, **k):
        calls.append(k); return next(it)
    ca.list_cases = fl
    r = code(ca.load_locked_development_suite, None, None, project_id=uuid.UUID(int=2), suite_id=uuid.UUID(int=1), role="x")
    return r, len(calls)
out["load_draft"] = load("development", "draft", [([rec("a")], None)])
out["load_final_locked"] = load("final", "locked", [([rec("a")], None)])
out["load_final_draft"] = load("final", "draft", [([rec("a")], None)])
out["load_unknown"] = load("holdout", "locked", [([rec("a")], None)])
out["load_ok"] = load("development", "locked", [([rec("a")], None)])
out["load_cycle"] = load("development", "locked", [([rec("a")], "a"), ([rec("b")], "a"), ([rec("c")], "a"), ([rec("d")], "a")])
out["load_pages"] = load("development", "locked", [([rec("a")], "a"), ([rec("b")], None)])
# bridge
try:
    r = run()
    out["run_status"] = [c.status for c in r.cases]
    out["run_digest"] = [c.business_outcome_sha256 is not None for c in r.cases]
    out["run_match"] = [c.collected_matches_invoke for c in r.cases]
    out["run_sha"] = len(r.log_sha256)
    out["run_net"] = r.network_attempts
    import os, stat
    out["run_mode"] = oct(stat.S_IMODE(os.stat(r.log_dir).st_mode))
    out["run_fp"] = [bool(c.runtime_fingerprint) for c in r.cases]
except BaseException as e:
    out["run_status"] = "ERR:" + type(e).__name__ + str(e)[:80]
s = Spy(build_isolated_tool_backend_adapter())
run(s)
out["invokes"] = len(s.invokes)
out["idem"] = len({{i.idempotency_key for i in s.invokes}})
out["req_tenant"] = s.invokes[0].tenant_namespace + s.invokes[0].agent_manifest_id + s.invokes[0].case_id
out["req_deadline"] = s.invokes[0].deadline_seconds
out["req_binding"] = repr(s.invokes[0].model_binding)
s2 = Spy(build_isolated_tool_backend_adapter(), preflight=lambda r: False)
out["preflight_refused"] = [code(run, s2), len(s2.invokes)]
out["attest_drift"] = code(run, Spy(build_isolated_tool_backend_adapter(), attest_drift=True))
sd = Spy(build_isolated_tool_backend_adapter(), attest_drift=True)
code(run, sd)
out["attest_drift_stops"] = len(sd.invokes)
out["describe_drift"] = code(run, Spy(build_isolated_tool_backend_adapter(), describe_drift=True))
out["bind_key"] = code(run, model_binding=ModelBinding("n/a","n/a",(("zzz","1"),)))
out["bind_ok"] = code(run, model_binding=ModelBinding("n/a","n/a",(("temperature","1"),)))
out["entry_kind"] = code(run, entry_kind="ssh")
out["final_purpose"] = code(run, cset=cs(purpose="final"))
out["other_purpose"] = code(run, cset=cs(purpose="holdout"))
out["no_hook"] = code(run, hook=None)
out["recon_none"] = code(run, is_reconstructed_adapter=True)
out["recon_partial"] = code(run, is_reconstructed_adapter=True, reconstructed_declaration=ReconstructedAdapterDeclaration("f", frozenset({{ParityCaseCategory.SUCCESS}})))
out["recon_full"] = code(run, is_reconstructed_adapter=True, reconstructed_declaration=ReconstructedAdapterDeclaration("f", frozenset(ParityCaseCategory)))
out["adapter_mismatch"] = code(run, Spy(build_isolated_tool_backend_adapter(), collect=lambda c, i: i.collect_outcome("k2") if c == "k1" else i.collect_outcome(c)))
out["adapter_boom"] = [c.status for c in run(Spy(build_isolated_tool_backend_adapter(), on_invoke=lambda r: 1/0)).cases]
# network
def dial(r):
    try:
        socket.create_connection(("127.0.0.1", 9), timeout=0.2)
    except BridgeError:
        raise
    except OSError:
        pass
out["net_dial"] = code(run, Spy(build_isolated_tool_backend_adapter(), on_invoke=dial))
def lookup(r):
    try: socket.getaddrinfo("example.invalid", 80)
    except Exception: pass
out["net_lookup"] = code(run, Spy(build_isolated_tool_backend_adapter(), on_invoke=lookup))
def conn_ex(r):
    try: socket.socket().connect_ex(("127.0.0.1", 9))
    except Exception: pass
out["net_connect_ex"] = code(run, Spy(build_isolated_tool_backend_adapter(), on_invoke=conn_ex))
def host(r):
    try: socket.gethostbyname("example.invalid")
    except Exception: pass
out["net_gethost"] = code(run, Spy(build_isolated_tool_backend_adapter(), on_invoke=host))
real = socket.socket.connect
out["net_restored"] = socket.socket.connect is real and socket.getaddrinfo.__module__ == "socket"
# telemetry
import os
os.environ["INSPECT_TELEMETRY"] = "x"
out["telemetry"] = code(run)
del os.environ["INSPECT_TELEMETRY"]
os.environ["INSPECT_REQUIRED_HOOKS"] = "x"
out["req_hooks"] = code(run)
del os.environ["INSPECT_REQUIRED_HOOKS"]
# generic agent
from inspect_ai.agent import react
from inspect_ai.solver import system_message
orig = rb._build_solver
rb._build_solver = lambda st: react(prompt="x")
out["generic"] = code(run)
rb._build_solver = lambda st: [system_message("x"), orig(st)]
out["chained"] = code(run)
rb._build_solver = orig
# hook
h = hook({{"temperature": 0.2, "max_tokens": 64}})
out["hook_ok"] = code(h.screen_request, chat())
out["hook_hosted"] = code(hook().screen_request, chat(tools=[{{"type": "web_search_preview"}}]))
out["hook_mcp"] = code(hook().screen_request, chat(tools=[{{"type": "mcp", "server_label": "x", "server_url": "https://x"}}]))
out["hook_remote"] = code(hook().screen_request, chat(messages=[{{"role":"user","content":[{{"type":"image_url","image_url":{{"url":"https://x.test/a.png"}}}}]}}]))
out["hook_unapproved"] = code(hook().screen_request, chat(tools=[{{"type":"function","function":{{"name":"zzz","parameters":{{"type":"object"}}}}}}]))
out["hook_bad"] = code(hook().screen_request, ["x"])
hh = hook()
hh.screen_request(chat())
out["hook_count"] = hh.requests_screened
hd = hook()
code(hd.screen_request, chat(tools=[{{"type": "web_search_preview"}}]))
out["hook_denied_count"] = hd.requests_denied
h3 = hook()
o = h3.capture_and_filter_response(resp([("c1","search_records"),("c2","wire")]))
out["filter_removed"] = list(o.removed_call_ids)
out["filter_evidence"] = [a.name for a in h3.attempt_evidence()[0].attempts]
out["filter_captured"] = h3.attempts_captured
out["filter_body"] = len(o.body["choices"][0]["message"]["tool_calls"])
out["filter_bad"] = code(hook().capture_and_filter_response, "x")
out["parity_default"] = code(hook({{"temperature": 0.2, "max_tokens": 64}}).check_generation_parity, chat(), forward_generation_config=False)
hp = hook({{"temperature": 0.2, "max_tokens": 64}})
out["parity_forward"] = hp.check_generation_parity(chat(), forward_generation_config=True).verdict
out["parity_count"] = hp.parity_checks
out["parity_changed"] = code(hook({{"temperature": 0.7, "max_tokens": 64}}).check_generation_parity, chat(), forward_generation_config=True)
b2 = chat(top_p=0.9, seed=7, stop=["END"], n=2, max_completion_tokens=99); del b2["max_tokens"]
out["obs_off"] = sorted(observe_inspect_generation_forwarding(b2, forward_generation_config=False))
out["obs_on"] = sorted(observe_inspect_generation_forwarding(b2, forward_generation_config=True))
out["obs_bad"] = code(observe_inspect_generation_forwarding, "x", forward_generation_config=True)
b3 = chat(logprobs=True, top_logprobs=2, presence_penalty=0.1, frequency_penalty=0.2, parallel_tool_calls=False, reasoning_effort="low", logit_bias={{"1": 2}}, max_tokens=7)
out["obs_on_all"] = sorted(observe_inspect_generation_forwarding(b3, forward_generation_config=True))
print(json.dumps(out, sort_keys=True, default=str))
"""


def _run_probe(path: Path | None = None, source: str | None = None) -> dict[str, Any]:
    originals = {name: p.read_bytes() for name, p in MODULES.items()}
    cache_dir = tempfile.mkdtemp(prefix="ftf-r004-p3-pyc-")
    try:
        if source is not None and path is not None:
            path.write_text(source, encoding="utf-8")
        result = subprocess.run(
            [
                sys.executable,
                "-X",
                "pycache_prefix=" + cache_dir,
                "-c",
                _PROBE.format(root=str(ROOT), api=str(ROOT / "apps" / "api")),
            ],
            capture_output=True,
            text=True,
            timeout=300,
            check=False,
        )
    finally:
        for name, p in MODULES.items():
            p.write_bytes(originals[name])
        shutil.rmtree(cache_dir, ignore_errors=True)
    if any(p.read_bytes() != originals[name] for name, p in MODULES.items()):
        raise RuntimeError("module not restored")
    if result.returncode != 0:
        return {"ERROR": result.stderr[-600:]}
    return cast(dict[str, Any], json.loads(result.stdout.strip().splitlines()[-1]))


def _keys(prefix: str, baseline: dict[str, Any]) -> tuple[str, ...]:
    return tuple(k for k in baseline if k.startswith(prefix))


# (label, module, regex, replacement, probe keys that must change)
_MUTATIONS: list[tuple[str, str, str, str, tuple[str, ...]]] = [
    # ---- case adapter
    ("final-suite-not-refused", "cases", r'    if suite\.purpose == "final":\n        raise CaseAdapterError\(CODE_FINAL_SUITE\)\n', "", ("load_final_locked", "load_final_draft")),
    ("unknown-purpose-allowed", "cases", r'    if suite\.purpose != "development":\n        raise CaseAdapterError\(CODE_UNSUPPORTED_PURPOSE\)\n', "", ("load_unknown",)),
    ("draft-suite-allowed", "cases", r'    if suite\.status != "locked":\n        raise CaseAdapterError\(CODE_SUITE_NOT_LOCKED\)\n', "", ("load_draft",)),
    ("cursor-cycle-not-detected", "cases", r"        if after is not None and next_after <= after:\n            raise CaseAdapterError\(CODE_INVALID_CASE\)\n", "", ("load_cycle",)),
    ("duplicate-key-allowed", "cases", r"        if case_key in seen_keys or case_id in seen_ids:\n            raise CaseAdapterError\(CODE_DUPLICATE_CASE_ID\)\n", "", ("dup_key", "dup_id")),
    ("duplicate-id-allowed", "cases", r"case_key in seen_keys or case_id in seen_ids", "case_key in seen_keys", ("dup_id",)),
    ("duplicate-key-only-by-id", "cases", r"case_key in seen_keys or case_id in seen_ids", "case_id in seen_ids", ("dup_key",)),
    ("empty-suite-allowed", "cases", r"    if not records:\n        raise CaseAdapterError\(CODE_EMPTY_SUITE\)\n", "", ("empty",)),
    ("non-object-input-allowed", "cases", r"    if not isinstance\(input_json, Mapping\):\n        raise CaseAdapterError\(CODE_INVALID_CASE\)\n", "", ("input_list",)),
    ("non-string-key-allowed", "cases", r"            if type\(key\) is not str:\n                raise CaseAdapterError\(CODE_INVALID_CASE\)\n", "", ("input_intkey",)),
    ("unsupported-value-allowed", "cases", r"    raise CaseAdapterError\(CODE_INVALID_CASE\)\n\n\ndef freeze_declared_inputs", "    return value\n\n\ndef freeze_declared_inputs", ("input_set",)),
    ("depth-unbounded", "cases", r"    if depth > _MAX_FREEZE_DEPTH:\n        raise CaseAdapterError\(CODE_INVALID_CASE\)\n", "", ("deep",)),
    ("critical-not-bool-checked", "cases", r"        if type\(record\.critical\) is not bool:\n            raise CaseAdapterError\(CODE_INVALID_CASE\)\n", "", ("crit_int",)),
    ("blank-text-allowed", "cases", r'    if type\(value\) is not str or not value:\n        raise CaseAdapterError\(CODE_INVALID_CASE\)\n', "    return value\n", ("blank_cat",)),
    ("sample-id-not-case-key", "cases", r"                id=case_key,", "                id=case_id,", ("sample_ids",)),
    ("sample-metadata-suite-dropped", "cases", r'                    "suite_id": suite_id,\n                    "agent_manifest_id": agent_manifest_id,\n', "", ("sample_meta_keys", "sample_meta_suite")),
    ("sample-metadata-critical-dropped", "cases", r'                    "critical": case\.critical,\n', "", ("sample_meta_keys",)),
    ("sample-metadata-group-dropped", "cases", r'                    "group_key": case\.group_key,\n', "", ("sample_meta_keys",)),
    ("sample-metadata-grader-dropped", "cases", r'                    "grader_type": case\.grader_type,\n', "", ("sample_meta_keys",)),
    ("inputs-not-frozen", "cases", r"    frozen = _freeze\(input_json\)\n    assert isinstance\(frozen, tuple\)\n    return frozen", "    return tuple(input_json.items())", ("frozen",)),
    # ---- bridge
    ("reconstructed-adapter-always-qualified", "bridge", r"    if status != QualificationStatus\.QUALIFIED:\n        raise BridgeError\(CODE_INTEGRATION_REQUIRED\)\n", "", ("recon_none", "recon_partial")),
    ("binding-not-validated", "bridge", r"        validate_model_binding_override\(describe, model_binding\)\n", "        pass\n", ("bind_key",)),
    ("entry-kind-not-validated", "bridge", r"        kind = validate_runtime_entry_kind\(entry_kind\)\n", "        kind = None\n", ("entry_kind",)),
    ("no-pre-run-attest", "bridge", r"    pinned = adapter\.attest\(\)\n", "    pinned = None\n", ("run_status",)),
    ("identity-not-rechecked-after-case", "bridge", r"                record = _run_case\(state, case\)\n                state\.check_identity\(\)\n", "                record = _run_case(state, case)\n", ("attest_drift_stops",)),
    ("identity-not-checked-before-case", "bridge", r"                state\.check_identity\(\)\n                record = _run_case\(state, case\)\n", "                record = _run_case(state, case)\n", ("attest_drift_stops",)),
    ("identity-not-rechecked-after-run", "bridge", r"    log_sha256, version = _verify_log\(log_path, frozenset\(cases\)\)\n    state\.check_identity\(\)\n", "    log_sha256, version = _verify_log(log_path, frozenset(cases))\n", ("attest_drift",)),
    ("attest-drift-ignored", "bridge", r"        if changed:\n            raise BridgeError\(CODE_IDENTITY_CHANGED, \",\"\.join\(sorted\(changed\)\)\)\n", "", ("attest_drift",)),
    ("describe-drift-ignored", "bridge", r"        if _describe_view\(self\.adapter\.describe\(\)\) != _describe_view\(self\.pinned_describe\):\n            raise BridgeError\(CODE_IDENTITY_CHANGED, \"describe\"\)\n", "", ("describe_drift",)),
    ("observed-time-counts-as-drift", "bridge", r'        if f\.name != "observed_at_epoch_seconds"\n', "", ("run_status",)),
    ("generic-solver-allowed", "bridge", r"    if steps != \[NATIVE_SOLVER_NAME\]:\n        raise BridgeError\(CODE_GENERIC_AGENT\)\n", "", ("generic", "chained")),
    ("chained-solver-allowed", "bridge", r"    if steps != \[NATIVE_SOLVER_NAME\]:", "    if NATIVE_SOLVER_NAME not in steps:", ("chained",)),
    ("model-call-allowed", "bridge", r"    if log\.eval\.model != _MODEL_LESS or \(log\.stats and log\.stats\.model_usage\):\n        raise BridgeError\(CODE_GENERIC_AGENT, \"model call\"\)\n", "", ("run_status",)),
    ("telemetry-allowed", "bridge", r"        if os\.environ\.get\(name\):\n            raise BridgeError\(CODE_TELEMETRY, name\)\n", "        pass\n", ("telemetry", "req_hooks")),
    ("required-hooks-not-refused", "bridge", r'_TELEMETRY_ENV = \("INSPECT_TELEMETRY", "INSPECT_REQUIRED_HOOKS"\)', '_TELEMETRY_ENV = ("INSPECT_TELEMETRY",)', ("req_hooks",)),
    ("network-guard-off", "bridge", r"    with _private_workspace\(workspace_parent\) as log_dir, _NetworkGuard\(\) as guard:", "    import contextlib\n    guard = type('G', (), {'attempts': 0})()\n    with _private_workspace(workspace_parent) as log_dir, contextlib.nullcontext():", ("net_dial", "net_lookup", "net_connect_ex", "net_gethost")),
    ("network-attempts-not-checked", "bridge", r"    if guard\.attempts:\n        raise BridgeError\(CODE_OUTBOUND\)\n", "", ("net_lookup", "net_gethost", "net_connect_ex")),
    ("connect-not-guarded", "bridge", r'        for name in \("connect", "connect_ex"\):', '        for name in ("connect_ex",):', ("net_dial",)),
    ("connect-ex-not-guarded", "bridge", r'        for name in \("connect", "connect_ex"\):', '        for name in ("connect",):', ("net_connect_ex",)),
    ("create-connection-not-guarded", "bridge", r'"create_connection", "getaddrinfo"', '"getaddrinfo"', ("net_dial",)),
    ("getaddrinfo-not-guarded", "bridge", r'"getaddrinfo", "gethostbyname", "gethostbyname_ex"', '"gethostbyname", "gethostbyname_ex"', ("net_lookup",)),
    ("gethostbyname-not-guarded", "bridge", r'"gethostbyname", "gethostbyname_ex"\)', '"gethostbyname_ex")', ("net_gethost",)),
    ("guard-not-restored", "bridge", r"        for owner, name, original in reversed\(self\._saved\):\n            setattr\(owner, name, original\)\n", "", ("net_restored",)),
    ("unix-sockets-blocked", "bridge", r"            if sock\.family == getattr\(socket, \"AF_UNIX\", None\):\n                return original\(sock, address, \*args, \*\*kwargs\)\n", "", ("run_status",)),
    ("preflight-ignored", "bridge", r"    if not state\.adapter\.preflight\(request\):", "    if False:", ("preflight_refused",)),
    ("case-id-mismatch-allowed", "bridge", r"    if invoked\.case_id != case\.case_key or collected\.case_id != case\.case_key:\n        raise BridgeError\(CODE_ADAPTER_MISMATCH\)\n", "", ("adapter_mismatch",)),
    ("collected-not-independent", "bridge", r"        collected = state\.adapter\.collect_outcome\(case\.case_key\)", "        collected = invoked", ("run_match",)),
    ("exception-class-not-recorded", "bridge", r"adapter_exception_type=type\(exc\)\.__name__,", "adapter_exception_type=None,", ("adapter_boom",)),
    ("adapter-exception-hidden-as-completed", "bridge", r'            status="adapter_exception",', '            status="completed",', ("adapter_boom",)),
    ("idempotency-key-constant", "bridge", r'        idempotency_key=_sha256\(f"\{state\.run_id\}\\0\{case\.case_key\}"\.encode\(\)\),', '        idempotency_key="k",', ("idem",)),
    ("deadline-not-passed", "bridge", r"        deadline_seconds=state\.deadline_seconds,\n        idempotency_key", "        deadline_seconds=1.0,\n        idempotency_key", ("req_deadline",)),
    ("tenant-not-passed", "bridge", r"        tenant_namespace=state\.tenant_namespace,", '        tenant_namespace="tenant-b",', ("req_tenant", "run_status")),
    ("binding-not-passed", "bridge", r"        model_binding=state\.binding,", '        model_binding=ModelBinding("other", "other"),', ("req_binding",)),
    ("manifest-not-passed", "bridge", r"        agent_manifest_id=state\.agent_manifest_id,", '        agent_manifest_id="other",', ("req_tenant",)),
    ("log-hash-not-real", "bridge", r"    return _sha256\(log_path\.read_bytes\(\)\), str\(inspect_ai\.__version__\)", '    return "0" * 64, str(inspect_ai.__version__)', ("run_sha",)),
    ("workspace-world-readable", "bridge", r"    os\.chmod\(path, 0o700\)\n", "    os.chmod(path, 0o755)\n", ("run_mode",)),
    ("workspace-not-restricted", "bridge", r"    os\.chmod\(path, 0o700\)\n", "    os.chmod(path, 0o777)\n", ("run_mode",)),
    ("final-purpose-bridge-allowed", "bridge", r'    if case_set\.purpose != "development":\n        raise BridgeError\(CODE_INVALID_REQUEST, "purpose"\)\n', "", ("final_purpose", "other_purpose")),
    ("hook-not-required", "bridge", r'    if not isinstance\(hook, BridgeHook\):\n        raise BridgeError\(CODE_INVALID_REQUEST, "hook"\)\n', "", ("no_hook",)),
    ("sample-set-not-checked", "bridge", r'    if sample_ids != case_keys:\n        raise BridgeError\(CODE_RUN_FAILED, "sample set"\)\n', "", ("run_status",)),
    # ---- hook (P2 / P1 applied)
    ("hook-skips-p2-screening", "bridge", r"            decision = screen_outbound_model_request\(body, self\.policy\)", "            decision = SimpleNamespace(allowed=True, denials=())", ("hook_hosted", "hook_mcp", "hook_remote", "hook_unapproved")),
    ("hook-ignores-denial", "bridge", r"        if not decision\.allowed:\n            detail = ", "        if False:\n            detail = ", ("hook_hosted", "hook_mcp", "hook_remote", "hook_unapproved")),
    ("hook-boundary-error-swallowed", "bridge", r"            raise BridgeError\(CODE_PROVIDER_DENIED, exc\.code\) from exc\n        with self\._lock:\n            self\.requests_screened", "            return\n        with self._lock:\n            self.requests_screened", ("hook_bad",)),
    ("hook-count-dropped", "bridge", r"            self\.requests_screened \+= 1\n", "", ("hook_count",)),
    ("hook-denied-count-dropped", "bridge", r"            if not decision\.allowed:\n                self\.requests_denied \+= 1\n", "", ("hook_denied_count",)),
    ("filter-before-capture", "bridge", r"            evidence = capture_response_attempts\(response_body\)\n            outcome = apply_approval_filter\(\n                response_body,\n                evidence,", "            outcome = apply_approval_filter(\n                response_body,\n                capture_response_attempts({}),", ("filter_removed", "filter_bad")),
    ("filter-evidence-dropped", "bridge", r"            self\._evidence\.append\(outcome\.evidence\)\n", "", ("filter_evidence",)),
    ("filter-attempt-count-dropped", "bridge", r"            self\.attempts_captured \+= len\(outcome\.evidence\.attempts\)\n", "", ("filter_captured",)),
    ("filter-approved-names-empty", "bridge", r"                approved_tool_names=self\.policy\.approved_tool_names,", "                approved_tool_names=frozenset({'search_records', 'wire'}),", ("filter_removed", "filter_body")),
    ("parity-skipped", "bridge", r"        result = compare_generation_parity\(\n            self\.approved_settings, before, after, self\.declared_change\n        \)", "        result = compare_generation_parity(self.approved_settings, before, before, self.declared_change)", ("parity_default",)),
    ("parity-block-ignored", "bridge", r'        if result\.verdict in \("blocked", "insufficient_evidence"\):\n            raise BridgeError\(CODE_PARITY_BLOCKED, ",".join\(result\.blocking_keys\) or result\.verdict\)\n', "", ("parity_default", "parity_changed")),
    ("parity-insufficient-evidence-passes", "bridge", r'if result\.verdict in \("blocked", "insufficient_evidence"\):', 'if result.verdict == "blocked":', ("parity_default",)),
    ("parity-count-dropped", "bridge", r"            self\.parity_checks \+= 1\n", "", ("parity_count",)),
    ("parity-declared-change-ignored", "bridge", r"self\.approved_settings, before, after, self\.declared_change", "self.approved_settings, before, after, None", ("parity_default",)),
    ("inspect-clearing-assumed-off", "bridge", r"    if not forward_generation_config:\n        clear_generation_params\(config\)\n", "", ("obs_off",)),
    ("inspect-clearing-assumed-on", "bridge", r"    if not forward_generation_config:", "    if True:", ("obs_on",)),
    ("observation-max-completion-key-lost", "bridge", r'        if config_field == "max_tokens" and "max_completion_tokens" in body:\n            key = "max_completion_tokens"\n', "", ("obs_on",)),
    ("observation-stop-key-lost", "bridge", r'    "stop_seqs": "stop",\n', "", ("obs_on", "obs_off")),
    ("observation-n-key-lost", "bridge", r'    "num_choices": "n",\n', "", ("obs_on",)),
    ("observation-seed-key-lost", "bridge", r'    "seed": "seed",\n', "", ("obs_on", "obs_off")),
    ("observation-top-p-key-lost", "bridge", r'    "top_p": "top_p",\n', "", ("obs_on",)),
    ("observation-logprobs-key-lost", "bridge", r'    "logprobs": "logprobs",\n', "", ("obs_on_all",)),
    ("observation-top-logprobs-key-lost", "bridge", r'    "top_logprobs": "top_logprobs",\n', "", ("obs_on_all",)),
    ("observation-penalty-keys-lost", "bridge", r'    "frequency_penalty": "frequency_penalty",\n    "presence_penalty": "presence_penalty",\n', "", ("obs_on_all",)),
    ("observation-logit-bias-key-lost", "bridge", r'    "logit_bias": "logit_bias",\n', "", ("obs_on_all",)),
    ("observation-parallel-key-lost", "bridge", r'    "parallel_tool_calls": "parallel_tool_calls",\n', "", ("obs_on_all",)),
    ("observation-reasoning-key-lost", "bridge", r'    "reasoning_effort": "reasoning_effort",\n', "", ("obs_on_all",)),
    ("observation-non-mapping-allowed", "bridge", r"    if not isinstance\(request_body, Mapping\):\n        raise BridgeError\(CODE_INVALID_REQUEST, \"request body must be a mapping\"\)\n", "", ("obs_bad",)),
]


def main() -> None:
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
    if "--faults" not in sys.argv:
        _finish()
        return
    baseline = _run_probe()
    check("FTF-R004-P3", "the behavioural probe runs on the unmutated modules", "ERROR" not in baseline)
    if "ERROR" in baseline:
        FAILURES.append(f"probe error: {baseline['ERROR']}")
    else:
        sources = {name: p.read_text(encoding="utf-8") for name, p in MODULES.items()}
        for label, module, pattern, replacement, keys in _MUTATIONS:
            mutated, count = re.subn(
                pattern, lambda _m, r=replacement: r, sources[module], count=1, flags=re.S
            )
            check("FTF-R004-P3", f"mutation {label!r} applies exactly once", count == 1)
            if count != 1:
                continue
            result = _run_probe(MODULES[module], mutated)
            if "ERROR" in result:
                continue  # a crash at import or in the run is also a RED
            changed = [k for k in keys if result.get(k) != baseline.get(k)]
            check(
                "FTF-R004-P3-MUT",
                f"mutation {label!r} turns {keys} RED (changed: {changed})",
                bool(changed),
            )
    _finish()


def _finish() -> None:
    if FAILURES:
        print(f"FTF-R004-P3 inspect runtime bridge: {len(FAILURES)} failed")
        for failure in FAILURES:
            print(f"  {failure}")
        sys.exit(1)
    print(f"FTF-R004-P3 inspect runtime bridge: all {CHECKS[0]} checks passed")


if __name__ == "__main__":
    main()
