#!/usr/bin/env python3
"""FTF-0215 -- Periodic sync workflow.

Pure in-memory composition over `LastKnownGoodCatalog` (FTF-0208,
CERTIFIED) and `CatalogAnomalyDetector` (FTF-0209, CERTIFIED):
`PeriodicSyncWorkflow` is itself no-I/O (this task's own
`database_changes` clause), so no live HTTPS gateway fixture is needed
here -- both wrapped modules' own suites already independently prove
their real behavior; this task's own scope is the scheduling/ordering
layer composed on top of that already real, already-tested plumbing
(same disclosed "no live caller yet" precedent already established by
FTF-0202/FTF-0205/FTF-0207/FTF-0208/FTF-0209).

This task's own acceptance clause ("Periodic workflow records sync
snapshots, bounded retries and alerts without duplicate schedules") is
covered by `test_register_is_idempotent_without_duplicate_schedules`,
`test_successful_cycle_records_snapshot_and_history`,
`test_transient_failures_retry_then_reach_ceiling` and
`test_major_reduction_alert_still_surfaces_through_the_workflow`.

This task's own negative_acceptance ("Revoked key, concurrent sync and
out-of-order response cannot reinstate obsolete authorization") is
covered by `test_out_of_order_stale_response_cannot_reinstate_removed_ids`,
`test_concurrent_dispatch_only_the_newer_completion_is_applied` and
`test_revocation_cannot_be_undone_by_a_late_pre_revocation_response`.

Run directly: python3 tests/tasks/test_ftf_0215.py
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from packages.litellm_client.catalog_anomaly_detector import AnomalyError  # noqa: E402
from packages.litellm_client.periodic_sync_workflow import (  # noqa: E402
    DEFAULT_RETRY_CEILING,
    PeriodicSyncWorkflow,
    SyncCycleStatus,
)
from packages.litellm_client.v1_models_discovery import (  # noqa: E402
    DiscoveredModel,
    DiscoveryState,
    ModelDiscoveryResult,
    ModelMetadataStatus,
)

FAILURES: list[str] = []


def check(criterion_id: str, description: str, condition: bool) -> None:
    if not condition:
        FAILURES.append(f"FAIL [{criterion_id}] {description}")


_T0 = datetime(2026, 9, 25, 12, 0, 0, tzinfo=UTC)


def _discovered(*ids: str) -> ModelDiscoveryResult:
    models = tuple(DiscoveredModel(id=i, metadata_status=ModelMetadataStatus.LIMITED) for i in ids)
    return ModelDiscoveryResult(
        state=DiscoveryState.DISCOVERED,
        models=models,
        model_count=len(models),
        content_hash="irrelevant-to-this-task",
        detail="gateway reachable and authorized",
        checked_at=_T0,
    )


def _timeout() -> ModelDiscoveryResult:
    return ModelDiscoveryResult(
        state=DiscoveryState.TIMEOUT,
        models=(),
        model_count=None,
        content_hash=None,
        detail="gateway connection failed",
        checked_at=_T0,
    )


def _invalid_token() -> ModelDiscoveryResult:
    return ModelDiscoveryResult(
        state=DiscoveryState.INVALID_TOKEN,
        models=(),
        model_count=None,
        content_hash=None,
        detail="gateway rejected the inference credential",
        checked_at=_T0,
    )


# -- Acceptance: registration / duplicate schedules ------------------------


def test_register_is_idempotent_without_duplicate_schedules() -> None:
    workflow = PeriodicSyncWorkflow()
    check("FTF-0215", "the workflow starts unregistered", not workflow.is_registered)
    check("FTF-0215", "the first registration succeeds", workflow.register() is True)
    check("FTF-0215", "the workflow is now registered", workflow.is_registered)
    check(
        "FTF-0215",
        "a second registration attempt is refused as a duplicate schedule, not a new job",
        workflow.register() is False,
    )
    check(
        "FTF-0215",
        "a third, later duplicate attempt is refused the same way",
        workflow.register() is False,
    )


# -- Acceptance: snapshots / history ----------------------------------------


def test_successful_cycle_records_snapshot_and_history() -> None:
    workflow = PeriodicSyncWorkflow()
    sequence = workflow.begin_cycle()
    check("FTF-0215", "the first dispatched sequence is 1", sequence == 1)

    result = workflow.complete_cycle(sequence, _discovered("a", "b", "c"), now=_T0)
    check(
        "FTF-0215", "a fresh successful cycle is applied", result.status == SyncCycleStatus.APPLIED
    )
    check("FTF-0215", "the applied cycle carries its own sequence", result.sequence == 1)
    assert result.snapshot is not None
    check(
        "FTF-0215",
        "the returned snapshot reflects the real catalog state",
        len(result.snapshot.entries) == 3,
    )
    check(
        "FTF-0215",
        "the workflow's own live snapshot property agrees",
        len(workflow.snapshot.entries) == 3,
    )
    check("FTF-0215", "the cycle is recorded in history", workflow.history == (result,))
    check("FTF-0215", "last_applied_sequence advanced", workflow.last_applied_sequence == 1)


# -- Acceptance: bounded retries / alerts -----------------------------------


def test_transient_failures_retry_then_reach_ceiling() -> None:
    workflow = PeriodicSyncWorkflow(retry_ceiling=3)
    check(
        "FTF-0215",
        "this task reuses Section 3.3's own three-attempt ceiling by default",
        DEFAULT_RETRY_CEILING == 3,
    )

    seq1 = workflow.begin_cycle()
    r1 = workflow.complete_cycle(seq1, _timeout(), now=_T0)
    check(
        "FTF-0215",
        "attempt 1/3 schedules a bounded retry",
        r1.status == SyncCycleStatus.RETRY_SCHEDULED,
    )

    seq2 = workflow.begin_cycle()
    r2 = workflow.complete_cycle(seq2, _timeout(), now=_T0 + timedelta(seconds=1))
    check(
        "FTF-0215",
        "attempt 2/3 still schedules a bounded retry",
        r2.status == SyncCycleStatus.RETRY_SCHEDULED,
    )

    seq3 = workflow.begin_cycle()
    r3 = workflow.complete_cycle(seq3, _timeout(), now=_T0 + timedelta(seconds=2))
    check(
        "FTF-0215",
        "attempt 3/3 reaches the configured ceiling instead of retrying silently forever",
        r3.status == SyncCycleStatus.RETRY_CEILING_REACHED,
    )

    seq4 = workflow.begin_cycle()
    r4 = workflow.complete_cycle(seq4, _discovered("a", "b"), now=_T0 + timedelta(seconds=3))
    check(
        "FTF-0215",
        "a subsequent real success resets the consecutive-failure counter",
        r4.status == SyncCycleStatus.APPLIED,
    )

    seq5 = workflow.begin_cycle()
    r5 = workflow.complete_cycle(seq5, _timeout(), now=_T0 + timedelta(seconds=4))
    check(
        "FTF-0215",
        "the retry counter genuinely reset -- this is attempt 1/3 again, not continued escalation",
        r5.status == SyncCycleStatus.RETRY_SCHEDULED,
    )


def test_major_reduction_alert_still_surfaces_through_the_workflow() -> None:
    workflow = PeriodicSyncWorkflow()
    seq1 = workflow.begin_cycle()
    workflow.complete_cycle(seq1, _discovered(*[f"m{i}" for i in range(10)]), now=_T0)

    seq2 = workflow.begin_cycle()
    result = workflow.complete_cycle(seq2, _discovered("m0", "m1"), now=_T0 + timedelta(seconds=1))

    check(
        "FTF-0215",
        "a real >50% reduction surfaces a real anomaly through this workflow's own composition",
        result.anomaly is not None,
    )
    assert result.anomaly is not None
    check(
        "FTF-0215",
        "the anomaly records the genuine previous count",
        result.anomaly.previous_count == 10,
    )
    check("FTF-0215", "the anomaly records the genuine new count", result.anomaly.new_count == 2)

    acked = workflow.acknowledge_anomaly(result.anomaly.id, "admin@sjzlab.example", now=_T0)
    check(
        "FTF-0215",
        "acknowledge_anomaly is a real, working passthrough to the wrapped detector",
        acked.status.value == "acknowledged",
    )

    try:
        workflow.retry_anomaly(result.anomaly.id)
        check(
            "FTF-0215",
            "retry_anomaly rejects an already-acknowledged incident, same as the wrapped detector",
            False,
        )
    except AnomalyError:
        check(
            "FTF-0215",
            "retry_anomaly rejects an already-acknowledged incident, same as the wrapped detector",
            True,
        )


# -- Negative acceptance: out-of-order / concurrent -------------------------


def test_out_of_order_stale_response_cannot_reinstate_removed_ids() -> None:
    workflow = PeriodicSyncWorkflow()

    seq1 = workflow.begin_cycle()
    workflow.complete_cycle(seq1, _discovered("a", "b", "c"), now=_T0)

    seq2 = workflow.begin_cycle()
    workflow.complete_cycle(seq2, _discovered("a"), now=_T0 + timedelta(seconds=1))
    removed_ids = {e.id for e in workflow.snapshot.entries if e.status.value == "removed"}
    check(
        "FTF-0215",
        "b and c are genuinely marked removed after the newer cycle",
        removed_ids == {"b", "c"},
    )

    # A stale response for the *first* cycle (sequence 1) finally arrives,
    # after cycle 2 has already been applied -- an out-of-order delivery
    # that still (incorrectly, if applied) lists b/c as present.
    stale_result = workflow.complete_cycle(
        seq1, _discovered("a", "b", "c"), now=_T0 + timedelta(seconds=2)
    )
    check(
        "FTF-0215",
        "the stale, out-of-order response for an already-superseded sequence is dropped",
        stale_result.status == SyncCycleStatus.SKIPPED_OUT_OF_ORDER,
    )

    removed_ids_after = {e.id for e in workflow.snapshot.entries if e.status.value == "removed"}
    check(
        "FTF-0215",
        "b and c are still removed -- the stale response did not reinstate obsolete authorization",
        removed_ids_after == {"b", "c"},
    )
    check(
        "FTF-0215",
        "last_applied_sequence was not moved backward by the stale delivery",
        workflow.last_applied_sequence == 2,
    )


def test_concurrent_dispatch_only_the_newer_completion_is_applied() -> None:
    workflow = PeriodicSyncWorkflow()
    seq1 = workflow.begin_cycle()
    workflow.complete_cycle(seq1, _discovered("a", "b"), now=_T0)

    # Two more cycles are dispatched concurrently (e.g. a slow real network
    # call plus a timeout-triggered re-dispatch) before either completes.
    seq2 = workflow.begin_cycle()
    seq3 = workflow.begin_cycle()
    check(
        "FTF-0215",
        "concurrent dispatch issues distinct, increasing sequences",
        (seq2, seq3) == (2, 3),
    )

    # The newer (seq3) cycle's response arrives first and is applied.
    r3 = workflow.complete_cycle(seq3, _discovered("a"), now=_T0 + timedelta(seconds=2))
    check(
        "FTF-0215",
        "the newer concurrently-dispatched cycle is applied",
        r3.status == SyncCycleStatus.APPLIED,
    )

    # The older (seq2) cycle's response, dispatched earlier but arriving
    # later, must not be allowed to reinstate "b" (already removed by seq3).
    r2 = workflow.complete_cycle(seq2, _discovered("a", "b"), now=_T0 + timedelta(seconds=3))
    check(
        "FTF-0215",
        "the older concurrent cycle's late-arriving response is dropped as out-of-order",
        r2.status == SyncCycleStatus.SKIPPED_OUT_OF_ORDER,
    )
    removed_ids = {e.id for e in workflow.snapshot.entries if e.status.value == "removed"}
    check(
        "FTF-0215",
        "'b' stays removed -- the concurrent stale response never reinstated it",
        removed_ids == {"b"},
    )


def test_revocation_cannot_be_undone_by_a_late_pre_revocation_response() -> None:
    workflow = PeriodicSyncWorkflow()
    seq1 = workflow.begin_cycle()
    workflow.complete_cycle(seq1, _discovered("a", "b"), now=_T0)

    seq2 = workflow.begin_cycle()
    revoked = workflow.complete_cycle(seq2, _invalid_token(), now=_T0 + timedelta(seconds=1))
    check(
        "FTF-0215",
        "the revocation cycle is genuinely applied",
        revoked.status == SyncCycleStatus.APPLIED,
    )
    check(
        "FTF-0215",
        "the catalog is genuinely revoked",
        not workflow.snapshot.access_state.value == "healthy",
    )
    check(
        "FTF-0215",
        "no id is authorized once revoked",
        workflow.is_call_authorized("a", now=_T0 + timedelta(seconds=1)) is False,
    )

    # A slow pre-revocation discovery response (dispatched before the
    # revocation was even known) finally arrives -- it must not undo it.
    late = workflow.complete_cycle(seq1, _discovered("a", "b"), now=_T0 + timedelta(seconds=5))
    check(
        "FTF-0215",
        "the late pre-revocation response is dropped as out-of-order",
        late.status == SyncCycleStatus.SKIPPED_OUT_OF_ORDER,
    )
    check(
        "FTF-0215",
        "the catalog is still genuinely revoked after the late response",
        not workflow.snapshot.access_state.value == "healthy",
    )
    check(
        "FTF-0215",
        "no id is authorized after the late response either -- revocation was not undone",
        workflow.is_call_authorized("a", now=_T0 + timedelta(seconds=5)) is False,
    )


def main() -> None:
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()

    if FAILURES:
        print(f"FTF-0215 periodic sync workflow: {len(FAILURES)} failed")
        for failure in FAILURES:
            print(f"  {failure}")
        sys.exit(1)

    print("FTF-0215 periodic sync workflow: all checks passed")
    sys.exit(0)


if __name__ == "__main__":
    main()
