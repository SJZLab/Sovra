#!/usr/bin/env python3
"""FTF-0515 -- Quality issue engine.

Runnable without a database or network: `evaluate_quality` is a pure function
over the real FTF-0508..FTF-0514 detector reports (no stubs of the detectors).

Acceptance ("records blocking/warning reason, provenance and deterministic
resolution state") -> the reason/severity, provenance, determinism and
resolution tests. Negative acceptance ("Broad exception cannot mark clean;
unresolved critical data/poisoning issue blocks split/training") -> the
detector-failure, missing-detector, wrong-type, poisoning and assert_clear_*
tests.

Run directly: python3 tests/tasks/test_ftf_0515.py
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from workers.data.exact_duplicate_detector import detect_exact_duplicates  # noqa: E402
from workers.data.near_duplicate_detector import detect_near_duplicates  # noqa: E402
from workers.data.normalized_duplicate_detector import (  # noqa: E402
    detect_normalized_duplicates,
)
from workers.data.pii_secrets_detector import detect_pii_and_secrets  # noqa: E402
from workers.data.quality_issue_engine import (  # noqa: E402
    REQUIRED_DETECTORS,
    DetectorRun,
    IssueResolution,
    QualityBlockedError,
    QualityEngineError,
    QualityIssueInput,
    QualityReport,
    apply_resolutions,
    assert_clear_for_split,
    assert_clear_for_training,
    evaluate_quality,
    run_detector,
)
from workers.data.record_schema_validator import RecordSchemaValidationReport  # noqa: E402
from workers.data.tokenization_analysis import (  # noqa: E402
    ConversationTokenization,
    TokenizationAnalysisReport,
)
from workers.data.tool_argument_validator import ToolCallValidationReport  # noqa: E402

FAILURES: list[str] = []


def check(criterion_id: str, description: str, condition: bool) -> None:
    if not condition:
        FAILURES.append(f"FAIL [{criterion_id}] {description}")


def _hash(seed: str) -> str:
    import hashlib

    return hashlib.sha256(seed.encode()).hexdigest()


def _record(
    index: int,
    text: str,
    purpose: str = "training",
    raw: str | None = None,
    norm: str | None = None,
) -> dict[str, object]:
    return {
        "record_index": index,
        "purpose": purpose,
        "record_sha256": _hash(raw if raw is not None else f"raw-{index}-{text}"),
        "normalized_sha256": _hash(norm if norm is not None else f"norm-{index}-{text}"),
        "content": {
            "messages": [{"role": "user", "content": [{"type": "text", "text": text}]}],
            "metadata": {},
        },
    }


CLEAN_BATCH = [
    _record(0, "How do I reset my router password?"),
    _record(1, "Summarise the quarterly budget meeting notes."),
]


def _clean_reports(batch: list[dict[str, object]] | None = None) -> dict[str, object]:
    batch = batch if batch is not None else CLEAN_BATCH
    return {
        "record_schema": RecordSchemaValidationReport(
            issues=(), accepted_batch_positions=(0, 1), rejected_batch_positions=()
        ),
        "tool_arguments": ToolCallValidationReport(issues=(), tool_schema_status="not_applicable"),
        "tokenization": TokenizationAnalysisReport(
            conversations=(), distribution=None, issues=(), certifies_training_fit=True
        ),
        "exact_duplicate": detect_exact_duplicates(batch),
        "normalized_duplicate": detect_normalized_duplicates(batch),
        "near_duplicate": detect_near_duplicates(batch),
        "pii_secrets": detect_pii_and_secrets(batch),
    }


def _runs(
    overrides: dict[str, object] | None = None, drop: tuple[str, ...] = ()
) -> list[DetectorRun]:
    reports = _clean_reports()
    reports.update(overrides or {})
    return [DetectorRun(detector=n, report=r) for n, r in reports.items() if n not in drop]


def _rules_of(report: QualityReport) -> set[str]:
    return {i.reason_code for i in report.issues}


# ---------------------------------------------------------------- acceptance


def test_clean_run_is_clean_and_blocks_nothing() -> None:
    report = evaluate_quality(_runs())
    check("FTF-0515", "all seven detectors clean => is_clean", report.is_clean)
    check("FTF-0515", "clean => no issues", report.issues == ())
    check(
        "FTF-0515",
        "clean => does not block split/training",
        not report.blocks_split and not report.blocks_training,
    )
    assert_clear_for_split(report)
    assert_clear_for_training(report)
    check(
        "FTF-0515",
        "residual-risk disclosure present",
        "not proof" in report.residual_risk_disclosure,
    )


def test_cross_purpose_exact_duplicate_blocks_with_provenance() -> None:
    batch = [
        _record(0, "same", purpose="training", raw="dup"),
        _record(1, "other", purpose="held_out_evaluation", raw="dup"),
    ]
    report = evaluate_quality(_runs({"exact_duplicate": detect_exact_duplicates(batch)}))
    found = [i for i in report.issues if i.reason_code == "raw_duplicate_group"]
    check("FTF-0515", "cross-purpose raw duplicate yields one issue", len(found) == 1)
    if found:
        issue = found[0]
        check("FTF-0515", "cross-purpose duplicate is blocking", issue.severity == "blocking")
        check("FTF-0515", "provenance names detector", issue.detector == "exact_duplicate")
        check("FTF-0515", "provenance names batch positions", issue.batch_positions == (0, 1))
        check("FTF-0515", "provenance carries record indices", issue.record_indices == (0, 1))
        check(
            "FTF-0515",
            "provenance carries the detector's digest",
            issue.evidence_digest == _hash("dup"),
        )
        check("FTF-0515", "new issue starts open", issue.resolution_state == "open")
    check("FTF-0515", "report blocks split", report.blocks_split)


def test_same_purpose_raw_duplicate_is_warning_not_blocking() -> None:
    batch = [_record(0, "a", raw="dup"), _record(1, "b", raw="dup")]
    report = evaluate_quality(_runs({"exact_duplicate": detect_exact_duplicates(batch)}))
    found = [i for i in report.issues if i.reason_code == "raw_duplicate_group"]
    check(
        "FTF-0515",
        "same-purpose raw duplicate is a warning",
        len(found) == 1 and found[0].severity == "warning",
    )
    check("FTF-0515", "a warning alone does not block", not report.blocks_split)


def test_normalized_duplicate_always_blocks() -> None:
    batch = [_record(0, "Hello  World!"), _record(1, "hello world")]
    report = evaluate_quality(_runs({"normalized_duplicate": detect_normalized_duplicates(batch)}))
    found = [
        i
        for i in report.issues
        if i.reason_code == "normalized_duplicate_group" and i.detector == "normalized_duplicate"
    ]
    check(
        "FTF-0515",
        "normalized duplicate is blocking",
        len(found) == 1 and found[0].severity == "blocking",
    )


def test_pii_finding_blocks_and_never_carries_the_value() -> None:
    secret_email = "jane.doe@example.com"
    batch = [_record(0, f"contact me at {secret_email}"), _record(1, "nothing here")]
    report = evaluate_quality(_runs({"pii_secrets": detect_pii_and_secrets(batch)}))
    found = [i for i in report.issues if i.detector == "pii_secrets"]
    check("FTF-0515", "PII finding becomes an issue", len(found) >= 1)
    check("FTF-0515", "PII issue is blocking", all(i.severity == "blocking" for i in found))
    check("FTF-0515", "PII issue names the row", all(i.batch_positions == (0,) for i in found))
    blob = repr(report)
    check("FTF-0515", "raw PII value never copied into the ledger", secret_email not in blob)


def test_near_duplicate_cross_purpose_blocks_same_purpose_warns() -> None:
    text = "Please summarise the attached quarterly financial report for the board."
    cross = [
        _record(0, text, purpose="training"),
        _record(1, text + " ", purpose="held_out_evaluation"),
    ]
    near_cross = detect_near_duplicates(cross)
    report = evaluate_quality(_runs({"near_duplicate": near_cross}))
    found = [i for i in report.issues if i.reason_code == "near_duplicate_pair"]
    check(
        "FTF-0515",
        "cross-purpose near duplicate is blocking",
        len(found) >= 1 and all(i.severity == "blocking" for i in found),
    )
    same = [_record(0, text), _record(1, text + " ")]
    report2 = evaluate_quality(_runs({"near_duplicate": detect_near_duplicates(same)}))
    found2 = [i for i in report2.issues if i.reason_code == "near_duplicate_pair"]
    check(
        "FTF-0515",
        "same-purpose near duplicate is a warning",
        len(found2) >= 1 and all(i.severity == "warning" for i in found2),
    )


def test_tokenization_truncated_critical_turn_blocks() -> None:
    conv_bad = ConversationTokenization(
        batch_position=1,
        record_index=1,
        exact=True,
        message_token_counts=(5,),
        total_token_count=5,
        truncated_critical_turn=True,
        requires_explicit_truncation_policy=True,
    )
    conv_warn = ConversationTokenization(
        batch_position=0,
        record_index=0,
        exact=True,
        message_token_counts=(5,),
        total_token_count=5,
        truncated_critical_turn=False,
        requires_explicit_truncation_policy=True,
    )
    tok = TokenizationAnalysisReport(
        conversations=(conv_warn, conv_bad),
        distribution=None,
        issues=(),
        certifies_training_fit=False,
    )
    report = evaluate_quality(_runs({"tokenization": tok}))
    by_code = {i.reason_code: i for i in report.issues}
    check(
        "FTF-0515",
        "truncated critical turn is blocking",
        by_code.get("truncated_critical_turn") is not None
        and by_code["truncated_critical_turn"].severity == "blocking",
    )
    check(
        "FTF-0515",
        "truncation-policy need is a warning",
        by_code.get("requires_explicit_truncation_policy") is not None
        and by_code["requires_explicit_truncation_policy"].severity == "warning",
    )


def test_detector_row_issue_is_blocking_with_original_code() -> None:
    from workers.data.record_schema_validator import RecordValidationIssue

    bad = RecordSchemaValidationReport(
        issues=(
            RecordValidationIssue(
                batch_position=1, record_index=7, code="oversize", message="too big"
            ),
        ),
        accepted_batch_positions=(0,),
        rejected_batch_positions=(1,),
    )
    report = evaluate_quality(_runs({"record_schema": bad}))
    found = [i for i in report.issues if i.reason_code == "oversize"]
    check(
        "FTF-0515",
        "detector row issue keeps its own code and message",
        len(found) == 1 and found[0].message == "too big",
    )
    check(
        "FTF-0515",
        "detector row issue is blocking and located",
        bool(found)
        and found[0].severity == "blocking"
        and found[0].batch_positions == (1,)
        and found[0].record_indices == (7,),
    )


def test_tool_call_issue_uses_call_position() -> None:
    from workers.data.tool_argument_validator import ToolCallValidationIssue

    bad = ToolCallValidationReport(
        issues=(
            ToolCallValidationIssue(
                call_position=3,
                call_id="c1",
                tool_name="t",
                code="invalid_arguments",
                message="bad args",
            ),
        ),
        tool_schema_status="invalid",
    )
    report = evaluate_quality(_runs({"tool_arguments": bad}))
    found = [i for i in report.issues if i.reason_code == "invalid_arguments"]
    check(
        "FTF-0515",
        "tool-call issue is blocking and located by call position",
        len(found) == 1 and found[0].severity == "blocking" and found[0].batch_positions == (3,),
    )


# -------------------------------------------------------------- determinism


def _signature(report: QualityReport) -> list[tuple[str, str, str, tuple[int, ...]]]:
    return [(i.issue_id, i.reason_code, i.severity, i.batch_positions) for i in report.issues]


def test_ids_and_order_deterministic_across_input_order_and_hashseed() -> None:
    batch = [
        _record(0, "a", raw="dup"),
        _record(1, "b", raw="dup"),
        _record(2, "c@example.com mail me", raw="x"),
    ]
    reports = _clean_reports(batch)
    forward = evaluate_quality([DetectorRun(n, r) for n, r in reports.items()])
    backward = evaluate_quality([DetectorRun(n, r) for n, r in reversed(list(reports.items()))])
    check(
        "FTF-0515",
        "issue ids/order independent of run order",
        _signature(forward) == _signature(backward) and len(forward.issues) >= 2,
    )
    ids = [i.issue_id for i in forward.issues]
    check("FTF-0515", "issues sorted by id", ids == sorted(ids))
    check("FTF-0515", "issue ids are unique", len(set(ids)) == len(ids))
    code = "\n".join(
        [
            f"import sys; sys.path.insert(0, {str(ROOT)!r})",
            "from tests.tasks.test_ftf_0515 import (",
            "    _record, _clean_reports, DetectorRun, evaluate_quality)",
            "b = [_record(0, 'a', raw='dup'), _record(1, 'b', raw='dup'),",
            "     _record(2, 'c@example.com mail me', raw='x')]",
            "r = _clean_reports(b)",
            "runs = [DetectorRun(n, x) for n, x in r.items()]",
            "print([i.issue_id for i in evaluate_quality(runs).issues])",
        ]
    )
    outs = set()
    for seed in ("0", "1", "12345"):
        env = dict(os.environ, PYTHONHASHSEED=seed)
        outs.add(
            subprocess.run(
                [sys.executable, "-c", code],
                capture_output=True,
                text=True,
                env=env,
                cwd=ROOT,
                check=True,
            ).stdout
        )
    check("FTF-0515", "issue ids identical under different PYTHONHASHSEED", len(outs) == 1)
    check("FTF-0515", "subprocess ids equal in-process ids", outs == {str(ids) + "\n"})


# --------------------------------------------------------------- resolutions


def _blocking_report() -> QualityReport:
    batch = [
        _record(0, "a", purpose="training", raw="dup"),
        _record(1, "b", purpose="held_out_evaluation", raw="dup"),
    ]
    return evaluate_quality(_runs({"exact_duplicate": detect_exact_duplicates(batch)}))


def test_resolution_clears_blocking_issue_and_records_reviewer() -> None:
    report = _blocking_report()
    issue = report.unresolved_blocking[0]
    resolved = apply_resolutions(
        report, [IssueResolution(issue.issue_id, "resolved", " rev-1 ", " removed from training ")]
    )
    check("FTF-0515", "resolved issue stops blocking", not resolved.blocks_split)
    after = [i for i in resolved.issues if i.issue_id == issue.issue_id][0]
    check(
        "FTF-0515",
        "resolution state/reviewer/reason recorded",
        after.resolution_state == "resolved"
        and after.resolved_by == "rev-1"
        and after.resolution_reason == "removed from training",
    )
    check("FTF-0515", "original report unchanged (immutable)", report.blocks_split)
    assert_clear_for_split(resolved)


def test_waiver_only_for_warning() -> None:
    report = _blocking_report()
    blocking = report.unresolved_blocking[0]
    try:
        apply_resolutions(report, [IssueResolution(blocking.issue_id, "waived", "r", "why")])
        check("FTF-0515", "waiving a blocking issue is refused", False)
    except QualityEngineError:
        pass
    batch = [_record(0, "a", raw="dup"), _record(1, "b", raw="dup")]
    warn_report = evaluate_quality(_runs({"exact_duplicate": detect_exact_duplicates(batch)}))
    warn = [i for i in warn_report.issues if i.severity == "warning"][0]
    waived = apply_resolutions(warn_report, [IssueResolution(warn.issue_id, "waived", "r", "ok")])
    after = [i for i in waived.issues if i.issue_id == warn.issue_id][0]
    check("FTF-0515", "warning can be waived", after.resolution_state == "waived")


def test_bad_resolutions_are_refused() -> None:
    report = _blocking_report()
    iid = report.unresolved_blocking[0].issue_id
    cases = {
        "unknown id": [IssueResolution("0" * 64, "resolved", "r", "why")],
        "blank reviewer": [IssueResolution(iid, "resolved", "  ", "why")],
        "blank reason": [IssueResolution(iid, "resolved", "r", "")],
        "bad decision": [IssueResolution(iid, "ignored", "r", "why")],
        "duplicate id": [
            IssueResolution(iid, "resolved", "r", "a"),
            IssueResolution(iid, "resolved", "r", "b"),
        ],
    }
    for label, resolutions in cases.items():
        try:
            apply_resolutions(report, resolutions)
            check("FTF-0515", f"{label} refused", False)
        except QualityEngineError:
            pass


# ------------------------------------------------- negative: never fail open


def test_detector_exception_marks_blocking_never_clean() -> None:
    def boom() -> object:
        raise RuntimeError("secret internal detail")

    failed = run_detector("pii_secrets", boom)
    check(
        "FTF-0515",
        "exception recorded as error type only",
        failed.error == "RuntimeError" and failed.report is None,
    )
    runs = [r for r in _runs(drop=("pii_secrets",))] + [failed]
    report = evaluate_quality(runs)
    check("FTF-0515", "a failed detector is not clean", not report.is_clean)
    check(
        "FTF-0515",
        "a failed detector blocks split and training",
        report.blocks_split and report.blocks_training,
    )
    check("FTF-0515", "failure is a detector_failed issue", "detector_failed" in _rules_of(report))
    check("FTF-0515", "exception text never copied", "secret internal detail" not in repr(report))
    for stage in (assert_clear_for_split, assert_clear_for_training):
        try:
            stage(report)
            check("FTF-0515", f"{stage.__name__} refuses after detector exception", False)
        except QualityBlockedError:
            pass


def test_missing_detector_blocks() -> None:
    for name in sorted(REQUIRED_DETECTORS):
        report = evaluate_quality(_runs(drop=(name,)))
        check(
            "FTF-0515",
            f"missing {name} => detector_not_run blocking",
            "detector_not_run" in _rules_of(report) and report.blocks_split and not report.is_clean,
        )
        try:
            assert_clear_for_training(report)
            check("FTF-0515", f"training allowed with {name} missing", False)
        except QualityBlockedError:
            pass


def test_wrong_report_type_blocks() -> None:
    for bogus in (None, object(), {"issues": ()}, "clean"):
        report = evaluate_quality(_runs({"exact_duplicate": bogus}))
        check(
            "FTF-0515",
            f"wrong report type {type(bogus).__name__} blocks",
            "detector_report_invalid" in _rules_of(report) and report.blocks_split,
        )
    swapped = evaluate_quality(
        _runs({"exact_duplicate": detect_normalized_duplicates(CLEAN_BATCH)})
    )
    check(
        "FTF-0515",
        "another detector's report type is not accepted",
        "detector_report_invalid" in _rules_of(swapped),
    )


def test_detector_failures_cannot_be_resolved_by_a_reviewer() -> None:
    report = evaluate_quality(_runs(drop=("near_duplicate",)))
    failure = [i for i in report.issues if i.reason_code == "detector_not_run"][0]
    try:
        apply_resolutions(report, [IssueResolution(failure.issue_id, "resolved", "r", "trust me")])
        check("FTF-0515", "detector failure cannot be resolved", False)
    except QualityEngineError:
        pass
    check("FTF-0515", "still blocks after refused resolution", report.blocks_split)


def test_poisoning_is_always_blocking_and_blocks_split_and_training() -> None:
    item = QualityIssueInput(
        detector="poisoning_review",
        category="poisoning",
        severity="warning",
        reason_code="rare_trigger_sample",
        message="rare trigger string in output label",
        batch_positions=(1,),
        record_indices=(1,),
    )
    report = evaluate_quality(_runs(), extra_issues=[item])
    found = [i for i in report.issues if i.category == "poisoning"]
    check(
        "FTF-0515",
        "poisoning severity forced to blocking",
        len(found) == 1 and found[0].severity == "blocking",
    )
    check(
        "FTF-0515",
        "poisoning blocks split and training",
        report.blocks_split and report.blocks_training and not report.is_clean,
    )
    try:
        apply_resolutions(report, [IssueResolution(found[0].issue_id, "waived", "r", "ok")])
        check("FTF-0515", "poisoning cannot be waived", False)
    except QualityEngineError:
        pass
    reviewed = apply_resolutions(
        report, [IssueResolution(found[0].issue_id, "resolved", "rev", "reviewed provenance ok")]
    )
    check("FTF-0515", "reviewed poisoning issue no longer blocks", not reviewed.blocks_training)
    try:
        evaluate_quality(
            _runs(), extra_issues=[QualityIssueInput("x", "not_a_category", "blocking", "c", "m")]
        )
        check("FTF-0515", "unknown extra category refused", False)
    except QualityEngineError:
        pass


def test_unresolved_blocking_blocks_even_if_only_one_of_many() -> None:
    report = _blocking_report()
    try:
        assert_clear_for_split(report)
        check("FTF-0515", "assert_clear_for_split refuses unresolved blocking issue", False)
    except QualityBlockedError as exc:
        check(
            "FTF-0515",
            "error names the blocking issue ids",
            exc.issue_ids == tuple(i.issue_id for i in report.unresolved_blocking)
            and exc.stage == "split",
        )
    try:
        assert_clear_for_training(report)
        check("FTF-0515", "assert_clear_for_training refuses unresolved blocking issue", False)
    except QualityBlockedError as exc:
        check("FTF-0515", "training stage named", exc.stage == "training")


def test_unknown_or_duplicate_detector_runs_refused() -> None:
    try:
        evaluate_quality([DetectorRun("nonsense", None)])
        check("FTF-0515", "unknown detector refused", False)
    except QualityEngineError:
        pass
    runs = _runs()
    try:
        evaluate_quality(runs + [runs[0]])
        check("FTF-0515", "duplicate detector run refused", False)
    except QualityEngineError:
        pass
    try:
        run_detector("nonsense", lambda: None)
        check("FTF-0515", "run_detector unknown name refused", False)
    except QualityEngineError:
        pass


def main() -> None:
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()

    if FAILURES:
        print(f"FTF-0515 quality issue engine: {len(FAILURES)} failed")
        for failure in FAILURES:
            print(f"  {failure}")
        sys.exit(1)

    print("FTF-0515 quality issue engine: all checks passed")
    sys.exit(0)


if __name__ == "__main__":
    main()
