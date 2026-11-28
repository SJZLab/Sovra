"""FTF-R004-P4 -- Inspect log normalization, complete accounting and the sealed-data guard.

Each DoD fault of the QUEUE.md P4 entry is one test below, run against the real pinned Inspect
release and the real FTF-04A26 isolated tool-backend adapter (no mocks):

* one score is edited in the log            -> test_edited_score_is_tampering_or_mismatch
* a sample is deleted                       -> test_deleted_sample_is_not_run_and_blocks
* a case is cancelled                       -> test_cancelled_or_limited_case
* infra_error relabel on a critical effect  -> test_critical_effect_is_never_infra_error
* Inspect score vs backend state            -> test_score_disagreeing_with_backend_state
* the judge is unapproved                   -> test_model_graded_scores_need_an_approved_judge
* a canary in the summary                   -> test_summary_is_content_free
* a final-purpose run without a freeze      -> test_final_suite_and_foreign_inputs_are_refused

Run directly: python3 tests/tasks/test_ftf_r004_p4.py
"""

from __future__ import annotations

import dataclasses
import hashlib
import importlib.util
import socket
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "apps" / "api"))

import workers.evaluation.adapters.inspect_adapter.case_adapter as case_adapter  # noqa: E402
import workers.evaluation.adapters.inspect_adapter.result_reader as reader  # noqa: E402
from inspect_ai.log import EvalSampleLimit, read_eval_log, write_eval_log  # noqa: E402
from inspect_ai.scorer import CORRECT, INCORRECT, Score  # noqa: E402
from workers.evaluation.adapters.inspect_adapter.result_reader import (  # noqa: E402
    CATEGORIES,
    ResultReaderError,
    normalize_bridge_run,
)
from workers.evaluation.adapters.inspect_adapter.runtime_bridge import (  # noqa: E402
    BridgeError,
)

_spec = importlib.util.spec_from_file_location(
    "ftf_r004_p3_fixtures", ROOT / "tests" / "tasks" / "test_ftf_r004_p3.py"
)
assert _spec and _spec.loader
P3 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(P3)  # the P3 fixtures (real adapter, loaded suites); its tests do not run
_Spy: Any = P3._Spy

from app.target_connectors.isolated_real_tool_backend_adapter_contract_tests import (  # noqa: E402
    build_isolated_tool_backend_adapter,
)

FAILURES: list[str] = []
CHECKS = [0]


def check(criterion_id: str, description: str, condition: bool) -> None:
    CHECKS[0] += 1
    if not condition:
        FAILURES.append(f"FAIL [{criterion_id}] {description}")


def _code(fn: Any, *args: Any, **kwargs: Any) -> str:
    try:
        fn(*args, **kwargs)
    except (ResultReaderError, BridgeError) as exc:
        return exc.code
    except BaseException as exc:  # noqa: BLE001
        return f"OTHER:{type(exc).__name__}"
    return "NO_RAISE"


def _real_run(records: list[Any] | None = None, adapter: Any = None) -> tuple[Any, Any, Any]:
    adapter = adapter if adapter is not None else build_isolated_tool_backend_adapter()
    case_set = P3._case_set(records)
    result = P3._run(adapter, case_set)
    return result, case_set, adapter


def _rewrite(result: Any, edit: Any) -> Any:
    """Writes an edited copy of the run's log with Inspect's own writer and re-records its hash,
    as if P3 had written that log: only the semantic checks can catch the edit."""
    log = read_eval_log(result.log_path)
    edit(log)
    path = Path(result.log_dir) / f"edited-{len(list(Path(result.log_dir).iterdir()))}.eval"
    write_eval_log(log, str(path))
    return dataclasses.replace(
        result, log_path=str(path), log_sha256=hashlib.sha256(path.read_bytes()).hexdigest()
    )


def _category(run: Any, key: str) -> str:
    return next(c.category for c in run.cases if c.case_key == key)


# --------------------------------------------------------------------------- baseline


def test_complete_run_normalizes_from_per_case_rows() -> None:
    result, case_set, adapter = _real_run()
    run = normalize_bridge_run(result, case_set, adapter)
    check(
        "FTF-R004-P4-AC1",
        f"a complete real run: every case pass, 2/2, not blocked ({run.counts})",
        [c.category for c in run.cases] == ["pass", "pass"]
        and (run.numerator, run.denominator) == (2, 2)
        and run.blocking_codes == ()
        and not run.blocked
        and dict(run.counts) == {**{name: 0 for name in CATEGORIES}, "pass": 2},
    )
    check(
        "FTF-R004-P4-AC1",
        "the summary carries the log by path and sha256 only, and the case ids",
        run.log_path == result.log_path
        and run.log_sha256 == result.log_sha256
        and [c.case_id for c in run.cases] == [c.case_id for c in case_set.cases]
        and [c.business_outcome_sha256 for c in run.cases]
        == [c.business_outcome_sha256 for c in result.cases]
        and [c.side_effect_count for c in run.cases] == [1, 1]
        and all(c.record_status == "completed" for c in run.cases),
    )

    def zero_metrics(log: Any) -> None:
        for score in log.results.scores:
            for metric in score.metrics.values():
                metric.value = 0.0

    ignored = normalize_bridge_run(_rewrite(result, zero_metrics), case_set, adapter)
    check(
        "FTF-R004-P4-AC1",
        "Inspect's own summary metrics are never trusted: zeroed metrics change nothing",
        (ignored.numerator, ignored.denominator) == (2, 2),
    )


# --------------------------------------------------------------------------- integrity


def test_edited_score_is_tampering_or_mismatch() -> None:
    result, case_set, adapter = _real_run()
    original = Path(result.log_path).read_bytes()
    log: Any = read_eval_log(result.log_path)
    log.samples[0].scores = {P3.bridge._SCORER_NAME: Score(value=INCORRECT)}
    write_eval_log(log, result.log_path)  # in place, hash NOT re-recorded
    check(
        "FTF-R004-P4-NEG1",
        "a score edited in the log P3 hashed -> score_log_tampered",
        _code(normalize_bridge_run, result, case_set, adapter) == "score_log_tampered",
    )
    Path(result.log_path).write_bytes(original)
    check(
        "FTF-R004-P4-NEG1",
        "the restored log normalizes again",
        _code(normalize_bridge_run, result, case_set, adapter) == "NO_RAISE",
    )

    def flip(log: Any) -> None:
        log.samples[0].scores[P3.bridge._SCORER_NAME] = Score(value=INCORRECT)

    check(
        "FTF-R004-P4-NEG1",
        "an edited score even with a matching hash -> outcome_mismatch (score vs business state)",
        _code(normalize_bridge_run, _rewrite(result, flip), case_set, adapter)
        == "outcome_mismatch",
    )

    def relabel(log: Any) -> None:
        log.samples[1].metadata["adapter_status"] = "error"

    check(
        "FTF-R004-P4-NEG1",
        "an edited adapter status in the log -> outcome_mismatch",
        _code(normalize_bridge_run, _rewrite(result, relabel), case_set, adapter)
        == "outcome_mismatch",
    )
    elsewhere = dataclasses.replace(result, log_dir=str(Path(result.log_dir).parent))
    check(
        "FTF-R004-P4-NEG1",
        "a log outside the run's private log dir -> score_log_tampered",
        _code(normalize_bridge_run, elsewhere, case_set, adapter) == "score_log_tampered",
    )
    missing = dataclasses.replace(result, log_path=str(Path(result.log_dir) / "gone.eval"))
    check(
        "FTF-R004-P4-NEG1",
        "a missing log -> score_log_tampered",
        _code(normalize_bridge_run, missing, case_set, adapter) == "score_log_tampered",
    )
    junk = Path(result.log_dir) / "junk.eval"
    junk.write_bytes(b"not a log")
    unparseable = dataclasses.replace(
        result, log_path=str(junk), log_sha256=hashlib.sha256(b"not a log").hexdigest()
    )
    check(
        "FTF-R004-P4-NEG1",
        "an unparseable log -> score_log_tampered",
        _code(normalize_bridge_run, unparseable, case_set, adapter) == "score_log_tampered",
    )

    def other_plan(log: Any) -> None:
        log.plan.steps[0].solver = "react"

    def other_model(log: Any) -> None:
        log.eval.model = "openai/gpt"

    def extra_sample(log: Any) -> None:
        extra = log.samples[0].model_copy(deep=True)
        extra.id = "not-scheduled"
        log.samples.append(extra)

    for label, edit in (
        ("a generic-agent plan", other_plan),
        ("a model-called log", other_model),
        ("an unscheduled sample", extra_sample),
    ):
        check(
            "FTF-R004-P4-NEG1",
            f"{label} -> score_log_tampered",
            _code(normalize_bridge_run, _rewrite(result, edit), case_set, adapter)
            == "score_log_tampered",
        )
    extra_record = dataclasses.replace(
        result,
        cases=(*result.cases, dataclasses.replace(result.cases[0], case_key="not-scheduled")),
    )
    duplicate_record = dataclasses.replace(result, cases=(*result.cases, result.cases[0]))
    other_id = dataclasses.replace(
        result, cases=(dataclasses.replace(result.cases[0], case_id="other"), result.cases[1])
    )
    for label, forged in (
        ("an unscheduled case record", extra_record),
        ("a duplicated case record", duplicate_record),
        ("a case record with another case id", other_id),
    ):
        check(
            "FTF-R004-P4-NEG1",
            f"{label} -> score_log_tampered",
            _code(normalize_bridge_run, forged, case_set, adapter) == "score_log_tampered",
        )


# --------------------------------------------------------------------------- completeness


def test_deleted_sample_is_not_run_and_blocks() -> None:
    result, case_set, adapter = _real_run()

    def delete(log: Any) -> None:
        del log.samples[1]

    run = normalize_bridge_run(_rewrite(result, delete), case_set, adapter)
    check(
        "FTF-R004-P4-NEG2",
        f"a deleted sample -> not_run, incomplete_run, still listed ({run.counts})",
        _category(run, "case-002") == "not_run"
        and run.blocking_codes == ("incomplete_run",)
        and run.blocked
        and (run.numerator, run.denominator) == (1, 2)
        and [c.case_key for c in run.cases] == ["case-001", "case-002"],
    )
    no_record = dataclasses.replace(result, cases=result.cases[:1])
    run = normalize_bridge_run(no_record, case_set, adapter)
    check(
        "FTF-R004-P4-NEG2",
        "a scheduled case with no P3 record -> not_run, incomplete_run",
        _category(run, "case-002") == "not_run"
        and run.blocking_codes == ("incomplete_run",)
        and next(c for c in run.cases if c.case_key == "case-002").record_status == ""
        and next(c for c in run.cases if c.case_key == "case-002").business_outcome_sha256 is None,
    )

    def unscored(log: Any) -> None:
        log.samples[0].scores = {}

    run = normalize_bridge_run(_rewrite(result, unscored), case_set, adapter)
    check(
        "FTF-R004-P4-NEG2",
        "an unscored sample -> not_run, incomplete_run",
        _category(run, "case-001") == "not_run" and run.blocked,
    )
    refused = _real_run(
        P3._records()[:1]
        + [P3._record("case-009", "search_records", "subject-unseeded", {"query": "q"})]
    )
    run = normalize_bridge_run(*refused)
    check(
        "FTF-R004-P4-NEG2",
        "a preflight-refused case -> not_run, incomplete_run",
        _category(run, "case-009") == "not_run" and run.blocking_codes == ("incomplete_run",),
    )


def test_cancelled_or_limited_case() -> None:
    result, case_set, adapter = _real_run()
    for limit_type, expected, blocks in (
        ("operator", "cancelled", True),
        ("custom", "cancelled", True),
        ("message", "cancelled", True),
        ("time", "timeout", False),
        ("working", "timeout", False),
    ):

        def limited(log: Any, t: str = limit_type) -> None:
            log.samples[0].limit = EvalSampleLimit(type=t, limit=1)  # type: ignore[arg-type]

        run = normalize_bridge_run(_rewrite(result, limited), case_set, adapter)
        check(
            "FTF-R004-P4-NEG2",
            f"a {limit_type} limit -> {expected} (blocks={blocks}, "
            f"got {_category(run, 'case-001')})",
            _category(run, "case-001") == expected and run.blocked is blocks,
        )


# --------------------------------------------------------------------------- categories


def test_record_statuses_map_to_exactly_one_category() -> None:
    record = P3.bridge.CaseRunRecord
    base = dict(
        case_key="k",
        case_id="i",
        error_classification="none",
        trace_ref="t",
        runtime_fingerprint="r",
        route_fingerprint="r",
        business_outcome_sha256=None,
        side_effect_count=0,
        collected_matches_invoke=True,
        deadline_exceeded=False,
        adapter_exception_type=None,
    )
    table = {
        ("completed", "none"): "pass",
        ("error", "refusal"): "model_fail",
        ("error", "malformed_output"): "model_fail",
        ("error", "fatal"): "tool_fail",
        ("error", "transient"): "infra_error",
        ("error", "timeout"): "timeout",
        ("error", "unknown-class"): "infra_error",
        ("cancelled", "none"): "cancelled",
        ("deadline_exceeded", "none"): "timeout",
        ("preflight_refused", "none"): "not_run",
        ("adapter_exception", "fatal"): "infra_error",
    }
    for (status, classification), expected in table.items():
        got = reader._base_category(
            record(**{**base, "status": status, "error_classification": classification})
        )
        check(
            "FTF-R004-P4-AC1",
            f"record {status}/{classification} -> {expected} (got {got})",
            got == expected and got in CATEGORIES,
        )
    check(
        "FTF-R004-P4-AC1",
        "an outcome_mismatch record (or any unknown status) is refused, never categorised",
        _code(reader._base_category, record(**{**base, "status": "outcome_mismatch"}))
        == "outcome_mismatch",
    )
    cross_tenant = _real_run(
        [P3._record("case-001", "get_record", "subject-a1", {"record_id": "rec-b1-0001"})],
    )
    run = normalize_bridge_run(*cross_tenant)
    check(
        "FTF-R004-P4-AC1",
        f"a real backend refusal end to end -> model_fail (got {run.cases[0].category})",
        run.cases[0].category == "model_fail" and run.cases[0].record_status == "error",
    )


def test_critical_effect_is_never_infra_error() -> None:
    records = [
        P3._record(
            "case-001", "search_records", "subject-a1", {"query": "quarterly"}, critical=True
        )
    ]
    result, case_set, adapter = _real_run(records)

    def errored(log: Any) -> None:
        log.samples[0].error = log.samples[0].error or _sample_error()

    run = normalize_bridge_run(_rewrite(result, errored), case_set, adapter)
    check(
        "FTF-R004-P4-NEG3",
        f"a critical case with a tool effect and a sample error -> tool_fail, never infra_error "
        f"(got {run.cases[0].category})",
        run.cases[0].category == "tool_fail",
    )
    plain, plain_set, plain_adapter = _real_run()
    run = normalize_bridge_run(_rewrite(plain, errored), plain_set, plain_adapter)
    check(
        "FTF-R004-P4-NEG3",
        "a non-critical case with the same sample error stays infra_error",
        run.cases[0].category == "infra_error",
    )
    no_effect = dataclasses.replace(
        result, cases=(dataclasses.replace(result.cases[0], side_effect_count=0),)
    )

    class _NoEffects(_Spy):
        def collect_outcome(self, case_id):
            return dataclasses.replace(super().collect_outcome(case_id), side_effects=())

    run = normalize_bridge_run(_rewrite(no_effect, errored), case_set, _NoEffects(adapter))
    check(
        "FTF-R004-P4-NEG3",
        "a critical case with no recorded or collected effect may be infra_error",
        run.cases[0].category == "infra_error",
    )
    collected_only = normalize_bridge_run(_rewrite(no_effect, errored), case_set, adapter)
    check(
        "FTF-R004-P4-NEG3",
        "a critical effect seen only in the adapter's independent state -> tool_fail",
        collected_only.cases[0].category == "tool_fail",
    )


def _sample_error() -> Any:
    from inspect_ai._util.error import EvalError

    return EvalError(message="x", traceback="", traceback_ansi="")


# --------------------------------------------------------------------------- business state


def test_score_disagreeing_with_backend_state() -> None:
    result, case_set, adapter = _real_run()

    class _Drifted(_Spy):
        def __init__(self, inner: Any, change: dict[str, Any]) -> None:
            super().__init__(inner)
            self.change = change

        def collect_outcome(self, case_id):
            return dataclasses.replace(super().collect_outcome(case_id), **self.change)

    from app.target_connectors.production_faithful_target_agent_harness_contract import (
        ErrorClassification,
    )

    for label, change in (
        ("another business outcome", {"business_outcome": (("forged", True),)}),
        (
            "an error status",
            {
                "status": "error",
                "business_outcome": None,
                "error_classification": ErrorClassification.FATAL,
            },
        ),
        ("another case id", {"case_id": "other"}),
    ):
        check(
            "FTF-R004-P4-NEG4",
            f"the adapter's independent state shows {label} -> outcome_mismatch",
            _code(normalize_bridge_run, result, case_set, _Drifted(adapter, change))
            == "outcome_mismatch",
        )

    class _NotAResult(_Spy):
        def collect_outcome(self, case_id):
            return {"case_id": case_id}

    check(
        "FTF-R004-P4-NEG4",
        "a business state that is not a contract AdapterInvokeResult -> outcome_mismatch",
        _code(normalize_bridge_run, result, case_set, _NotAResult(adapter)) == "outcome_mismatch",
    )
    calls: list[str] = []

    class _Counting(_Spy):
        def collect_outcome(self, case_id):
            calls.append(case_id)
            return super().collect_outcome(case_id)

    normalize_bridge_run(result, case_set, _Counting(adapter))
    check(
        "FTF-R004-P4-NEG4",
        "every invoked case is re-checked against the adapter",
        calls == ["case-001", "case-002"],
    )

    def dial(_case: Any, inner: Any) -> Any:
        try:
            socket.create_connection(("127.0.0.1", 9), timeout=1).close()
        except Exception:  # noqa: BLE001
            pass
        return inner.collect_outcome(_case)

    check(
        "FTF-R004-P4-NEG4",
        "the re-check runs under the network guard -> outbound_call_blocked",
        _code(normalize_bridge_run, result, case_set, P3._Spy(adapter, collect=dial))
        == "outbound_call_blocked",
    )


# --------------------------------------------------------------------------- judge


def test_model_graded_scores_need_an_approved_judge() -> None:
    result, case_set, adapter = _real_run()
    route = "f" * 64

    def judged(meta: dict[str, Any] | None) -> Any:
        def edit(log: Any) -> None:
            log.samples[0].scores["llm_judge"] = Score(value=CORRECT, metadata=meta)

        return _rewrite(result, edit)

    for label, meta, approved in (
        ("with no route", None, ()),
        ("with no route, approvals given", None, (route,)),
        ("with an unapproved route", {"judge_route_fingerprint": "e" * 64}, (route,)),
        ("with a non-str route", {"judge_route_fingerprint": 7}, (route,)),
        (
            "with a route and nothing approved (default deny)",
            {"judge_route_fingerprint": route},
            (),
        ),
    ):
        check(
            "FTF-R004-P4-NEG5",
            f"a model-graded score {label} -> unapproved_judge",
            _code(
                normalize_bridge_run,
                judged(meta),
                case_set,
                adapter,
                approved_judge_routes=approved,
            )
            == "unapproved_judge",
        )
    run = normalize_bridge_run(
        judged({"judge_route_fingerprint": route}),
        case_set,
        adapter,
        approved_judge_routes=[route],
    )
    check(
        "FTF-R004-P4-NEG5",
        "a model-graded score from an approved judge route is accepted and counted",
        run.approved_judge_scores == 1 and run.numerator == 2,
    )
    for bad in ([""], [7], ["x", None]):
        check(
            "FTF-R004-P4-NEG5",
            f"malformed approved judge routes {bad!r} -> result_reader_invalid_request",
            _code(normalize_bridge_run, result, case_set, adapter, approved_judge_routes=bad)
            == "result_reader_invalid_request",
        )


# --------------------------------------------------------------------------- sealed data / inputs


def test_final_suite_and_foreign_inputs_are_refused() -> None:
    result, case_set, adapter = _real_run()
    final = P3._raw_case_set(purpose="final")
    case_adapter._register_loaded(final)
    check(
        "FTF-R004-P4-NEG6",
        "a final-purpose suite without a freeze -> final_suite_requires_freeze",
        _code(normalize_bridge_run, result, final, adapter) == "final_suite_requires_freeze",
    )
    unregistered_final = P3._raw_case_set(purpose="final")
    check(
        "FTF-R004-P4-NEG6",
        "an unregistered final suite -> final_suite_requires_freeze too",
        _code(normalize_bridge_run, result, unregistered_final, adapter)
        == "final_suite_requires_freeze",
    )
    other = P3._raw_case_set(purpose="calibration")
    case_adapter._register_loaded(other)
    for label, args in (
        ("a set the loader did not return", (result, P3._raw_case_set(), adapter)),
        ("a loaded set of another purpose", (result, other, adapter)),
        ("a result that is not a BridgeRunResult", (object(), case_set, adapter)),
        (
            "a result for another suite",
            (dataclasses.replace(result, suite_id="other"), case_set, adapter),
        ),
        (
            "a result for another manifest",
            (dataclasses.replace(result, agent_manifest_id="other"), case_set, adapter),
        ),
    ):
        check(
            "FTF-R004-P4-NEG6",
            f"{label} -> result_reader_invalid_request",
            _code(normalize_bridge_run, *args) == "result_reader_invalid_request",
        )


def test_summary_is_content_free() -> None:
    result, case_set, adapter = _real_run()
    run = normalize_bridge_run(result, case_set, adapter)
    text = repr(run)
    check(
        "FTF-R004-P4-AC1",
        "neither the canary expected value nor any input text reaches the summary",
        P3.CANARY not in text and "quarterly" not in text and "vendor" not in text,
    )
    try:
        normalize_bridge_run(dataclasses.replace(result, log_sha256="0" * 64), case_set, adapter)
        message = ""
    except ResultReaderError as exc:
        message = str(exc)
    check(
        "FTF-R004-P4-AC1",
        "error messages hold codes only",
        message == "score_log_tampered: sha256" and P3.CANARY not in message,
    )


def main() -> None:
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
    if FAILURES:
        print(f"FTF-R004-P4 inspect result reader: {len(FAILURES)} failed")
        for failure in FAILURES:
            print(f"  {failure}")
        sys.exit(1)
    print(f"FTF-R004-P4 inspect result reader: all {CHECKS[0]} checks passed")


if __name__ == "__main__":
    main()
