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


# ------------------------------------------- exact-match / fail-closed edges


def test_vocabulary_is_exact_match_not_case_or_whitespace_tolerant() -> None:
    report = _blocking_report()
    iid = report.unresolved_blocking[0].issue_id
    for decision in (" resolved", "Resolved", "RESOLVED ", " waived", "WAIVED"):
        try:
            apply_resolutions(report, [IssueResolution(iid, decision, "r", "why")])
            check("FTF-0515", f"decision {decision!r} refused", False)
        except QualityEngineError:
            pass
    for variant in (iid.upper(), f" {iid}", f"{iid} "):
        try:
            apply_resolutions(report, [IssueResolution(variant, "resolved", "r", "why")])
            check("FTF-0515", f"issue id variant {variant!r} refused", False)
        except QualityEngineError:
            pass
    for name in (" pii_secrets", "PII_SECRETS", "pii_secrets "):
        try:
            run_detector(name, lambda: None)
            check("FTF-0515", f"run_detector name {name!r} refused", False)
        except QualityEngineError:
            pass
        try:
            evaluate_quality([DetectorRun(name, None)])
            check("FTF-0515", f"DetectorRun name {name!r} refused", False)
        except QualityEngineError:
            pass
    for category, severity in (
        (" poisoning", "blocking"),
        ("Poisoning", "blocking"),
        ("poisoning", " blocking"),
        ("poisoning", "Warning"),
        ("detector_failure", "blocking"),
    ):
        try:
            evaluate_quality(
                _runs(),
                extra_issues=[QualityIssueInput("x", category, severity, "c", "m")],
            )
            check("FTF-0515", f"extra issue {category!r}/{severity!r} refused", False)
        except QualityEngineError:
            pass


def test_run_detector_returns_report_under_its_own_name() -> None:
    ok = run_detector("exact_duplicate", lambda: detect_exact_duplicates(CLEAN_BATCH))
    check(
        "FTF-0515",
        "successful run keeps detector name and report",
        ok.detector == "exact_duplicate"
        and ok.error is None
        and isinstance(ok.report, type(detect_exact_duplicates(CLEAN_BATCH))),
    )
    failed = run_detector("near_duplicate", lambda: 1 / 0)
    check("FTF-0515", "failed run keeps detector name", failed.detector == "near_duplicate")


def _hand_built(severity: str, state: str = "open", failure: bool = False) -> QualityReport:
    from workers.data.quality_issue_engine import QualityIssue

    issue = QualityIssue(
        issue_id="a" * 64,
        detector="exact_duplicate",
        category="exact_duplicate",
        severity=severity,
        reason_code="x",
        message="m",
        batch_positions=(0,),
        record_indices=(0,),
        evidence_digest=None,
        resolution_state=state,
        is_detector_failure=failure,
    )
    return QualityReport(issues=(issue,), detectors_reported=REQUIRED_DETECTORS)


def test_unrecognised_severity_or_state_fails_closed() -> None:
    for severity in ("Warning", " warning", "WARNING", "critical", ""):
        check(
            "FTF-0515",
            f"severity {severity!r} is treated as blocking",
            _hand_built(severity).blocks_split,
        )
    for state in ("Resolved", " resolved", "WAIVED", "done", ""):
        check(
            "FTF-0515",
            f"resolution state {state!r} does not clear a blocking issue",
            _hand_built("blocking", state).blocks_training,
        )
    check("FTF-0515", "exact warning does not block", not _hand_built("warning").blocks_split)
    check(
        "FTF-0515",
        "exact resolved/waived clear",
        not _hand_built("blocking", "resolved").blocks_split
        and not _hand_built("warning", "waived").blocks_split,
    )
    for severity in ("Warning", " warning"):
        try:
            apply_resolutions(
                _hand_built(severity), [IssueResolution("a" * 64, "waived", "r", "why")]
            )
            check("FTF-0515", f"waiver of unrecognised severity {severity!r} refused", False)
        except QualityEngineError:
            pass
    try:
        apply_resolutions(
            _hand_built("warning", failure=True),
            [IssueResolution("a" * 64, "waived", "r", "why")],
        )
        check("FTF-0515", "a flagged detector failure cannot be waived", False)
    except QualityEngineError:
        pass


def test_resolution_text_is_stripped_exactly() -> None:
    report = _blocking_report()
    iid = report.unresolved_blocking[0].issue_id
    for reviewer, reason in (("  rev  ", "  why  "), ("\trev", "why\n")):
        out = apply_resolutions(report, [IssueResolution(iid, "resolved", reviewer, reason)])
        after = [i for i in out.issues if i.issue_id == iid][0]
        check(
            "FTF-0515",
            "reviewer and reason stored fully stripped",
            after.resolved_by == "rev" and after.resolution_reason == "why",
        )


# ------------------------------------------------ round 2: ids, pairing, edges


def _multi_message_record(index: int, texts: list[str]) -> dict[str, object]:
    return {
        "record_index": index,
        "purpose": "training",
        "record_sha256": _hash(f"raw-{index}-{texts}"),
        "normalized_sha256": _hash(f"norm-{index}-{texts}"),
        "content": {
            "messages": [{"role": "user", "content": [{"type": "text", "text": t}]} for t in texts],
            "metadata": {},
        },
    }


def test_same_category_pii_findings_in_one_record_stay_distinct() -> None:
    batch = [_multi_message_record(0, ["a@example.com", "b@example.com", "c@example.com"])]
    pii = detect_pii_and_secrets(batch)
    report = evaluate_quality(_runs({"pii_secrets": pii}))
    found = [i for i in report.issues if i.detector == "pii_secrets"]
    check("FTF-0515", "three same-category PII findings stay three issues", len(found) == 3)
    check("FTF-0515", "their ids are distinct", len({i.issue_id for i in found}) == 3)
    if found:
        out = apply_resolutions(
            report, [IssueResolution(found[0].issue_id, "resolved", "rev", "why")]
        )
        check(
            "FTF-0515",
            "resolving one leaves the other two blocking",
            len(out.unresolved_blocking) == len(report.unresolved_blocking) - 1,
        )


def test_row_issues_with_same_code_and_position_but_different_message_are_distinct() -> None:
    from workers.data.record_schema_validator import RecordValidationIssue

    bad = RecordSchemaValidationReport(
        issues=(
            RecordValidationIssue(batch_position=1, record_index=7, code="bad", message="one"),
            RecordValidationIssue(batch_position=1, record_index=7, code="bad", message="two"),
        ),
        accepted_batch_positions=(0,),
        rejected_batch_positions=(1,),
    )
    report = evaluate_quality(_runs({"record_schema": bad}))
    found = [i for i in report.issues if i.reason_code == "bad"]
    check(
        "FTF-0515",
        "two row issues differing only in message both survive",
        len(found) == 2 and {i.message for i in found} == {"one", "two"},
    )


def test_row_issues_without_a_position_and_many_extras_are_all_kept() -> None:
    from workers.data.record_schema_validator import RecordValidationIssue

    bad = RecordSchemaValidationReport(
        issues=(
            RecordValidationIssue(batch_position=0, record_index=None, code="c1", message="m"),
            RecordValidationIssue(batch_position=1, record_index=1, code="c2", message="m"),
        ),
        accepted_batch_positions=(),
        rejected_batch_positions=(0, 1),
    )
    extras = [
        QualityIssueInput("poison", "poisoning", "blocking", "p1", "m1", (0,), (0,), "d1"),
        QualityIssueInput("poison", "poisoning", "blocking", "p1", "m2", (0,), (0,), "d2"),
    ]
    report = evaluate_quality(_runs({"record_schema": bad}), extra_issues=extras)
    check(
        "FTF-0515",
        "2 row issues and 2 extras all kept",
        len([i for i in report.issues if i.reason_code in ("c1", "c2")]) == 2
        and len([i for i in report.issues if i.reason_code == "p1"]) == 2,
    )
    ids = [i.issue_id for i in report.issues]
    check("FTF-0515", "issues sorted by id", ids == sorted(ids))
    extra = [i for i in report.issues if i.reason_code == "p1"]
    check(
        "FTF-0515",
        "extra issues carry their evidence digest",
        {i.evidence_digest for i in extra} == {"d1", "d2"},
    )
    out = apply_resolutions(
        report,
        [
            IssueResolution(extra[0].issue_id, "resolved", "r", "why"),
            IssueResolution(extra[1].issue_id, "resolved", "r", "why"),
        ],
    )
    check("FTF-0515", "two resolutions both applied", len(out.unresolved_blocking) == 2)
    check(
        "FTF-0515",
        "QualityBlockedError names exactly the blocking ids",
        _blocked_ids(out) == tuple(i.issue_id for i in out.unresolved_blocking),
    )


def _blocked_ids(report: QualityReport) -> tuple[str, ...]:
    try:
        assert_clear_for_split(report)
    except QualityBlockedError as exc:
        return exc.issue_ids
    return ()


def test_pairs_are_sorted_together_not_independently() -> None:
    items = [
        QualityIssueInput("poison", "poisoning", "blocking", "p", "m", (5, 2), (50, 20)),
        QualityIssueInput("poison", "poisoning", "blocking", "q", "m", (2, 5), (None, 9)),
    ]
    report = evaluate_quality(_runs(), extra_issues=items)
    p = [i for i in report.issues if i.reason_code == "p"][0]
    q = [i for i in report.issues if i.reason_code == "q"][0]
    check(
        "FTF-0515",
        "position/index pairs stay paired after sort",
        p.batch_positions == (2, 5) and p.record_indices == (20, 50),
    )
    check(
        "FTF-0515",
        "a None index sorts with its position",
        q.batch_positions == (2, 5) and q.record_indices == (None, 9),
    )
    ties = [
        QualityIssueInput("poison", "poisoning", "blocking", "t1", "m", (3, 3), (9, 7)),
        QualityIssueInput("poison", "poisoning", "blocking", "t2", "m", (3, 3), (None, 7)),
    ]
    tied = evaluate_quality(_runs(), extra_issues=ties)
    t1 = [i for i in tied.issues if i.reason_code == "t1"][0]
    t2 = [i for i in tied.issues if i.reason_code == "t2"][0]
    check("FTF-0515", "equal positions order by index", t1.record_indices == (7, 9))
    check("FTF-0515", "a None index sorts after a real one", t2.record_indices == (7, None))
    try:
        evaluate_quality(
            _runs(),
            extra_issues=[QualityIssueInput("p", "poisoning", "blocking", "x", "m", (1, 2), (1,))],
        )
        check("FTF-0515", "unpaired indices refused", False)
    except QualityEngineError:
        pass


def test_empty_input_reports_all_detectors_not_run_and_blocks() -> None:
    report = evaluate_quality([])
    found = [i for i in report.issues if i.reason_code == "detector_not_run"]
    check("FTF-0515", "empty input => 7 detector_not_run issues", len(found) == 7)
    check("FTF-0515", "empty input blocks split and training", report.blocks_split)
    check("FTF-0515", "empty input blocks training", report.blocks_training)


def test_detector_error_wins_over_report_and_empty_error_blocks() -> None:
    clean = _clean_reports()
    for err in ("ValueError", ""):
        runs = [
            DetectorRun(n, report=r, error=err if n == "pii_secrets" else None)
            for n, r in clean.items()
        ]
        report = evaluate_quality(runs)
        failed = [i for i in report.issues if i.reason_code == "detector_failed"]
        check("FTF-0515", f"error {err!r} with a report is a detector_failed", len(failed) == 1)
        check(
            "FTF-0515",
            f"error {err!r} detector is absent from detectors_reported",
            "pii_secrets" not in report.detectors_reported and report.blocks_split,
        )


def test_detector_failure_issues_are_refused_by_apply_resolutions() -> None:
    report = evaluate_quality(
        [DetectorRun("pii_secrets", error="Boom"), DetectorRun("exact_duplicate", report=None)]
    )
    for code in ("detector_failed", "detector_report_invalid"):
        issue = [i for i in report.issues if i.reason_code == code][0]
        check("FTF-0515", f"{code} flagged as detector failure", issue.is_detector_failure)
        try:
            apply_resolutions(report, [IssueResolution(issue.issue_id, "resolved", "r", "why")])
            check("FTF-0515", f"{code} cannot be resolved", False)
        except QualityEngineError:
            pass


def test_near_duplicate_provenance_keeps_index_distinct_from_position() -> None:
    batch = [
        _record(10, "please reset my router password right now", purpose="training"),
        _record(20, "please reset my router password right now!", purpose="held_out_evaluation"),
    ]
    report = evaluate_quality(_runs({"near_duplicate": detect_near_duplicates(batch)}))
    found = [i for i in report.issues if i.reason_code == "near_duplicate_pair"]
    check(
        "FTF-0515",
        "near-dup pair carries positions and distinct record indices",
        len(found) == 1
        and found[0].batch_positions == (0, 1)
        and found[0].record_indices == (10, 20),
    )


def test_non_poisoning_extra_warning_stays_warning_and_a_warning_can_be_resolved() -> None:
    report = evaluate_quality(
        _runs(), extra_issues=[QualityIssueInput("x", "pii_secrets", "warning", "w", "m")]
    )
    found = [i for i in report.issues if i.reason_code == "w"]
    check(
        "FTF-0515",
        "a non-poisoning extra warning stays a warning and does not block",
        len(found) == 1 and found[0].severity == "warning" and not report.blocks_split,
    )
    warn = _hand_built("warning")
    out = apply_resolutions(warn, [IssueResolution("a" * 64, "resolved", "rev", "why")])
    check(
        "FTF-0515",
        "a warning can be resolved (not only waived)",
        out.issues[0].resolution_state == "resolved" and out.issues[0].resolved_by == "rev",
    )


# ------------------------------------------------- round 3 (verifier findings)


def _one(report: QualityReport, **match: object) -> list:
    return [i for i in report.issues if all(getattr(i, k) == v for k, v in match.items())]


def test_provenance_keeps_record_index_distinct_from_batch_position() -> None:
    # PII: record_index 10 sits at batch position 0.
    pii = detect_pii_and_secrets([_record(10, "mail me at jane.doe@example.com")])
    report = evaluate_quality(_runs({"pii_secrets": pii}))
    found = _one(report, detector="pii_secrets")
    check(
        "FTF-0515",
        "PII issue: batch_positions==(0,) and record_indices==(10,)",
        len(found) >= 1
        and all(i.batch_positions == (0,) and i.record_indices == (10,) for i in found),
    )
    # Tokenization: record_index 7 at batch position 0, critical and policy.
    for critical, code in (
        (True, "truncated_critical_turn"),
        (False, "requires_explicit_truncation_policy"),
    ):
        conv = ConversationTokenization(
            batch_position=0,
            record_index=7,
            exact=True,
            message_token_counts=(5,),
            total_token_count=5,
            truncated_critical_turn=critical,
            requires_explicit_truncation_policy=True,
        )
        tok = TokenizationAnalysisReport(
            conversations=(conv,), distribution=None, issues=(), certifies_training_fit=False
        )
        found = _one(evaluate_quality(_runs({"tokenization": tok})), reason_code=code)
        check(
            "FTF-0515",
            f"tokenization {code}: position (0,) index (7,)",
            len(found) == 1
            and found[0].batch_positions == (0,)
            and found[0].record_indices == (7,),
        )
    # Normalized-duplicate group: indices 10 and 20, digest == the group's.
    batch = [_record(10, "Hello  World!"), _record(20, "hello world")]
    nrep = detect_normalized_duplicates(batch)
    found = _one(
        evaluate_quality(_runs({"normalized_duplicate": nrep})),
        detector="normalized_duplicate",
        reason_code="normalized_duplicate_group",
    )
    check(
        "FTF-0515",
        "normalized-duplicate group: positions (0,1), indices (10,20), group digest",
        len(found) == 1
        and found[0].batch_positions == (0, 1)
        and found[0].record_indices == (10, 20)
        and bool(nrep.duplicate_groups)
        and found[0].evidence_digest == nrep.duplicate_groups[0].digest,
    )


def test_exact_detector_normalized_group_is_blocking_with_provenance() -> None:
    # equal normalized_sha256, different record_sha256 => only the normalized
    # hash matches; reported by the exact_duplicate detector.
    batch = [
        _record(10, "a", raw="raw-a", norm="same-norm"),
        _record(20, "b", raw="raw-b", norm="same-norm"),
    ]
    rep = detect_exact_duplicates(batch)
    report = evaluate_quality(_runs({"exact_duplicate": rep}))
    found = _one(report, detector="exact_duplicate", reason_code="normalized_duplicate_group")
    check("FTF-0515", "exact-detector normalized group yields one issue", len(found) == 1)
    check(
        "FTF-0515",
        "no raw group exists for a normalized-only match",
        _one(report, reason_code="raw_duplicate_group") == [],
    )
    if found and rep.normalized_duplicate_groups:
        group = rep.normalized_duplicate_groups[0]
        issue = found[0]
        check("FTF-0515", "normalized group is blocking", issue.severity == "blocking")
        check("FTF-0515", "category is exact_duplicate", issue.category == "exact_duplicate")
        check("FTF-0515", "batch positions (0,1)", issue.batch_positions == (0, 1))
        check("FTF-0515", "record indices (10,20)", issue.record_indices == (10, 20))
        check("FTF-0515", "digest == group digest", issue.evidence_digest == group.digest)
        check(
            "FTF-0515",
            "digest is the shared normalized hash",
            issue.evidence_digest == _hash("same-norm"),
        )
    check("FTF-0515", "it blocks split", report.blocks_split)


def test_pii_without_quarantine_is_warning_and_reason_names_category() -> None:
    from workers.data.pii_secrets_detector import PiiSecretsFinding, PiiSecretsReport

    def finding(category: str, quarantine: bool) -> PiiSecretsFinding:
        return PiiSecretsFinding(
            batch_position=0,
            record_index=10,
            message_index=0,
            content_path="content[0].text",
            category=category,
            match_length=5,
            requires_quarantine=quarantine,
        )

    warn = PiiSecretsReport(findings=(finding("email", False),), issues=())
    found = _one(evaluate_quality(_runs({"pii_secrets": warn})), detector="pii_secrets")
    check(
        "FTF-0515",
        "requires_quarantine=False => warning, reason pii_secret_email",
        len(found) == 1
        and found[0].severity == "warning"
        and found[0].reason_code == "pii_secret_email",
    )
    check(
        "FTF-0515",
        "a lone PII warning does not block",
        not evaluate_quality(_runs({"pii_secrets": warn})).blocks_split,
    )
    block = PiiSecretsReport(findings=(finding("phone", True),), issues=())
    found = _one(evaluate_quality(_runs({"pii_secrets": block})), detector="pii_secrets")
    check(
        "FTF-0515",
        "requires_quarantine=True => blocking, reason pii_secret_phone",
        len(found) == 1
        and found[0].severity == "blocking"
        and found[0].reason_code == "pii_secret_phone",
    )


def test_conversation_with_both_flags_yields_exactly_one_critical_issue() -> None:
    conv = ConversationTokenization(
        batch_position=4,
        record_index=9,
        exact=True,
        message_token_counts=(5,),
        total_token_count=5,
        truncated_critical_turn=True,
        requires_explicit_truncation_policy=True,
    )
    tok = TokenizationAnalysisReport(
        conversations=(conv,), distribution=None, issues=(), certifies_training_fit=False
    )
    found = _one(evaluate_quality(_runs({"tokenization": tok})), detector="tokenization")
    check(
        "FTF-0515",
        "both flags => exactly one issue, truncated_critical_turn, blocking",
        len(found) == 1
        and found[0].reason_code == "truncated_critical_turn"
        and found[0].severity == "blocking",
    )


_NEAR_TEXT = "Please summarise the attached quarterly financial report for the board."


def _near_batch() -> list[dict[str, object]]:
    return [
        _record(10, _NEAR_TEXT, purpose="training"),
        _record(20, _NEAR_TEXT, purpose="held_out_evaluation"),
    ]


def test_near_duplicate_ids_stable_across_hashseed_with_many_methods() -> None:
    rep = detect_near_duplicates(_near_batch())
    cands = rep.flagged_candidates
    check(
        "FTF-0515",
        "fixture flags a pair by 2+ methods",
        len(cands) == 1 and len(cands[0].flagged_methods) >= 2,
    )
    ids = [i.issue_id for i in evaluate_quality(_runs({"near_duplicate": rep})).issues]
    code = "\n".join(
        [
            f"import sys; sys.path.insert(0, {str(ROOT)!r})",
            "from tests.tasks.test_ftf_0515 import (",
            "    _near_batch, _runs, evaluate_quality, detect_near_duplicates)",
            "rep = detect_near_duplicates(_near_batch())",
            "r = evaluate_quality(_runs({'near_duplicate': rep}))",
            "print([i.issue_id for i in r.issues])",
        ]
    )
    outs = set()
    for seed in ("0", "1", "2", "77", "12345"):
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
    check("FTF-0515", "near-dup issue ids identical across PYTHONHASHSEED", len(outs) == 1)
    check("FTF-0515", "subprocess near-dup ids equal in-process ids", outs == {str(ids) + "\n"})
    msgs = [i.message for i in evaluate_quality(_runs({"near_duplicate": rep})).issues]
    check(
        "FTF-0515",
        "flagged methods are listed sorted in the message",
        len(msgs) == 1 and msgs[0].endswith(",".join(sorted(cands[0].flagged_methods))),
    )


def test_resolving_one_of_many_leaves_others_and_order_unchanged() -> None:
    from workers.data.record_schema_validator import RecordValidationIssue

    bad = RecordSchemaValidationReport(
        issues=tuple(
            RecordValidationIssue(batch_position=n, record_index=n, code="c", message=f"m{n}")
            for n in range(6)
        ),
        accepted_batch_positions=(),
        rejected_batch_positions=tuple(range(6)),
    )
    report = evaluate_quality(_runs({"record_schema": bad}))
    before = [i.issue_id for i in report.issues]
    for target in (before[0], before[2], before[-1]):
        out = apply_resolutions(report, [IssueResolution(target, "resolved", "rev", "why")])
        check(
            "FTF-0515", "resolution keeps ids and order", [i.issue_id for i in out.issues] == before
        )
        others = [
            (a, b) for a, b in zip(report.issues, out.issues, strict=True) if a.issue_id != target
        ]
        check(
            "FTF-0515",
            "every other issue is unchanged (still open)",
            len(others) == len(before) - 1 and all(a == b for a, b in others),
        )
        hit = [i for i in out.issues if i.issue_id == target]
        check(
            "FTF-0515",
            "exactly the named issue is resolved",
            len(hit) == 1
            and hit[0].resolution_state == "resolved"
            and sum(i.resolution_state != "open" for i in out.issues) == 1,
        )


def test_issue_id_identity_depends_on_each_identity_field() -> None:
    from workers.data.record_schema_validator import RecordValidationIssue

    def row(index: int | None) -> RecordSchemaValidationReport:
        return RecordSchemaValidationReport(
            issues=(
                RecordValidationIssue(batch_position=0, record_index=index, code="c", message="m"),
            ),
            accepted_batch_positions=(),
            rejected_batch_positions=(0,),
        )

    def ids(rep: RecordSchemaValidationReport) -> list[str]:
        return [i.issue_id for i in evaluate_quality(_runs({"record_schema": rep})).issues]

    check("FTF-0515", "equal inputs give equal ids", ids(row(5)) == ids(row(5)))
    check("FTF-0515", "ids differ when only record_index differs", ids(row(5)) != ids(row(6)))
    check("FTF-0515", "ids differ for None vs an index", ids(row(None)) != ids(row(0)))

    def extra(digest: str | None) -> list[str]:
        rep = evaluate_quality(
            _runs(),
            extra_issues=[
                QualityIssueInput("x", "pii_secrets", "warning", "w", "m", (0,), (0,), digest)
            ],
        )
        return [i.issue_id for i in rep.issues if i.reason_code == "w"]

    check("FTF-0515", "equal digest gives equal ids", extra("a" * 64) == extra("a" * 64))
    check("FTF-0515", "ids differ when only the digest differs", extra("a" * 64) != extra("b" * 64))
    check("FTF-0515", "ids differ for digest vs no digest", extra("a" * 64) != extra(None))


def test_invalid_report_detector_is_not_listed_as_reported() -> None:
    report = evaluate_quality(_runs({"pii_secrets": object()}))
    check(
        "FTF-0515",
        "detector_report_invalid detector absent from detectors_reported",
        "pii_secrets" not in report.detectors_reported
        and _one(report, reason_code="detector_report_invalid", detector="pii_secrets") != [],
    )
    check("FTF-0515", "and it is not clean", not report.is_clean)


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
