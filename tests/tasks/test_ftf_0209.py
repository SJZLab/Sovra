#!/usr/bin/env python3
"""FTF-0209 -- Catalog anomaly detector.

Pure in-memory composition over `LastKnownGoodCatalog` (FTF-0208,
CERTIFIED): `CatalogAnomalyDetector` is itself no-I/O (this task's own
`database_changes` clause), so no local HTTPS gateway fixture is needed
here -- FTF-0208's own test suite already independently proves a real
end-to-end discovery sequence feeds `LastKnownGoodCatalog` correctly; this
task's own scope is the anomaly bookkeeping layered on top of that already
real, already-tested plumbing (same disclosed "no live caller yet" scope
precedent already established by FTF-0202/FTF-0205/FTF-0207/FTF-0208).

This task's own acceptance clause ("A >50% reduction triggers recorded
anomaly/retry/admin acknowledgement while immediate authorization remains
enforced") is covered by `test_major_reduction_opens_recorded_anomaly`,
`test_retry_bounded_by_ceiling_then_escalates`,
`test_acknowledge_closes_anomaly_without_restoring_authorization` and
`test_reduction_denies_removed_ids_immediately_regardless_of_anomaly_state`.

This task's own negative_acceptance ("Repeated empty list cannot cause
silent destructive deletion or restored unauthorized execution") is
covered by `test_repeated_empty_discovery_does_not_delete_or_fragment_
anomaly_history` and `test_acknowledge_never_touches_wrapped_catalog`.

Run directly: python3 tests/tasks/test_ftf_0209.py
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from packages.litellm_client.catalog_anomaly_detector import (  # noqa: E402
    DEFAULT_RETRY_CEILING,
    AnomalyError,
    AnomalyKind,
    AnomalyStatus,
    CatalogAnomalyDetector,
)
from packages.litellm_client.last_known_good_catalog import LastKnownGoodCatalog  # noqa: E402
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


_T0 = datetime(2026, 9, 23, 12, 0, 0, tzinfo=UTC)


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


def _invalid_token() -> ModelDiscoveryResult:
    return ModelDiscoveryResult(
        state=DiscoveryState.INVALID_TOKEN,
        models=(),
        model_count=None,
        content_hash=None,
        detail="gateway rejected the inference credential",
        checked_at=_T0,
    )


# -- Baseline / no-trigger cases ------------------------------------------


def test_first_successful_discovery_never_triggers_an_anomaly() -> None:
    catalog = LastKnownGoodCatalog()
    detector = CatalogAnomalyDetector()
    snapshot, anomaly = detector.record(catalog, _discovered("a", "b", "c"), now=_T0)
    check(
        "FTF-0209",
        "a first-ever successful discovery has no prior baseline to compare against",
        anomaly is None,
    )
    check("FTF-0209", "the catalog itself was still genuinely updated", len(snapshot.entries) == 3)
    check("FTF-0209", "no anomaly exists yet", detector.current is None and detector.history == ())


def test_minor_reduction_does_not_trigger() -> None:
    catalog = LastKnownGoodCatalog()
    detector = CatalogAnomalyDetector()
    detector.record(catalog, _discovered("a", "b", "c", "d"), now=_T0)
    t1 = _T0 + timedelta(seconds=1)
    _snapshot, anomaly = detector.record(catalog, _discovered("a", "b", "c"), now=t1)
    check("FTF-0209", "a 25% reduction (4 -> 3) is not a major reduction", anomaly is None)
    check("FTF-0209", "no anomaly was recorded", detector.current is None)


def test_repeated_empty_result_with_no_prior_baseline_never_fabricates_an_anomaly() -> None:
    catalog = LastKnownGoodCatalog()
    detector = CatalogAnomalyDetector()
    for i in range(5):
        _snapshot, anomaly = detector.record(catalog, _discovered(), now=_T0 + timedelta(seconds=i))
        check("FTF-0209", "an empty result with no prior baseline never triggers", anomaly is None)
    check("FTF-0209", "no anomaly was ever recorded", detector.history == ())


# -- Trigger, retry, escalation --------------------------------------------


def test_major_reduction_opens_recorded_anomaly() -> None:
    catalog = LastKnownGoodCatalog()
    detector = CatalogAnomalyDetector()
    detector.record(catalog, _discovered("a", "b", "c", "d", "e", "f", "g", "h", "i", "j"), now=_T0)
    t1 = _T0 + timedelta(seconds=1)
    _snapshot, anomaly = detector.record(catalog, _discovered("a", "b", "c"), now=t1)

    check("FTF-0209", "a 70% reduction (10 -> 3) opens a recorded anomaly", anomaly is not None)
    assert anomaly is not None
    check(
        "FTF-0209",
        "the anomaly is the major_reduction kind",
        anomaly.kind == AnomalyKind.MAJOR_REDUCTION,
    )
    check("FTF-0209", "the anomaly starts in the open status", anomaly.status == AnomalyStatus.OPEN)
    check("FTF-0209", "the anomaly records the real previous count", anomaly.previous_count == 10)
    check("FTF-0209", "the anomaly records the real new count", anomaly.new_count == 3)
    check("FTF-0209", "the anomaly starts with zero retries", anomaly.retry_count == 0)
    check(
        "FTF-0209",
        "the anomaly is genuinely persisted, not just returned",
        detector.current == anomaly and detector.get(anomaly.id) == anomaly,
    )
    check("FTF-0209", "the anomaly is retained in history", detector.history == (anomaly,))


def test_retry_bounded_by_ceiling_then_escalates() -> None:
    catalog = LastKnownGoodCatalog()
    detector = CatalogAnomalyDetector(retry_ceiling=3)
    detector.record(catalog, _discovered(*[f"m{i}" for i in range(10)]), now=_T0)
    _snapshot, anomaly = detector.record(
        catalog, _discovered("m0", "m1"), now=_T0 + timedelta(seconds=1)
    )
    assert anomaly is not None

    first = detector.retry(anomaly.id)
    check("FTF-0209", "retry 1 of 3 stays pending", first.status == AnomalyStatus.RETRY_PENDING)
    check("FTF-0209", "retry 1 increments the retry count", first.retry_count == 1)

    second = detector.retry(anomaly.id)
    check("FTF-0209", "retry 2 of 3 stays pending", second.status == AnomalyStatus.RETRY_PENDING)

    third = detector.retry(anomaly.id)
    check(
        "FTF-0209",
        "the third retry hits the configured ceiling and escalates",
        third.status == AnomalyStatus.ESCALATED and third.retry_count == 3,
    )
    check(
        "FTF-0209",
        "the default ceiling matches Section 3.3's own three-attempt bound",
        DEFAULT_RETRY_CEILING == 3,
    )

    try:
        detector.retry(999)
        check("FTF-0209", "retrying an unknown anomaly id is rejected", False)
    except AnomalyError:
        check("FTF-0209", "retrying an unknown anomaly id is rejected", True)


def test_reduction_denies_removed_ids_immediately_regardless_of_anomaly_state() -> None:
    catalog = LastKnownGoodCatalog()
    detector = CatalogAnomalyDetector()
    detector.record(catalog, _discovered("a", "b", "c", "d", "e", "f", "g", "h", "i", "j"), now=_T0)
    t1 = _T0 + timedelta(seconds=1)
    detector.record(catalog, _discovered("a"), now=t1)

    for removed_id in ("b", "c", "d", "e", "f", "g", "h", "i", "j"):
        check(
            "FTF-0209",
            f"removed id {removed_id} is denied immediately, before any retry/ack workflow runs",
            not catalog.is_call_authorized(removed_id, now=t1).authorized,
        )
    check(
        "FTF-0209",
        "the still-present id stays authorized",
        catalog.is_call_authorized("a", now=t1).authorized,
    )


# -- Acknowledgement --------------------------------------------------------


def test_acknowledge_closes_anomaly_without_restoring_authorization() -> None:
    catalog = LastKnownGoodCatalog()
    detector = CatalogAnomalyDetector()
    detector.record(catalog, _discovered(*[f"m{i}" for i in range(10)]), now=_T0)
    t1 = _T0 + timedelta(seconds=1)
    _snapshot, anomaly = detector.record(catalog, _discovered("m0"), now=t1)
    assert anomaly is not None

    acked = detector.acknowledge(anomaly.id, "admin@sjzlab.example", now=t1 + timedelta(minutes=1))
    check(
        "FTF-0209",
        "acknowledgement transitions the anomaly to acknowledged",
        acked.status == AnomalyStatus.ACKNOWLEDGED,
    )
    check(
        "FTF-0209",
        "acknowledgement records who acknowledged it",
        acked.acknowledged_by == "admin@sjzlab.example",
    )
    check("FTF-0209", "acknowledgement records when", acked.acknowledged_at is not None)

    for removed_id in ("m1", "m2", "m3", "m4", "m5", "m6", "m7", "m8", "m9"):
        check(
            "FTF-0209",
            f"removed id {removed_id} stays denied after acknowledgement -- ack never restores",
            not catalog.is_call_authorized(removed_id, now=t1).authorized,
        )

    try:
        detector.acknowledge(anomaly.id, "someone-else@sjzlab.example")
        check("FTF-0209", "double-acknowledging the same anomaly is rejected", False)
    except AnomalyError:
        check("FTF-0209", "double-acknowledging the same anomaly is rejected", True)

    try:
        detector.retry(anomaly.id)
        check("FTF-0209", "retrying an already-acknowledged anomaly is rejected", False)
    except AnomalyError:
        check("FTF-0209", "retrying an already-acknowledged anomaly is rejected", True)

    try:
        detector.acknowledge(anomaly.id, "")
        check("FTF-0209", "acknowledging with no admin identity is rejected", False)
    except AnomalyError:
        check("FTF-0209", "acknowledging with no admin identity is rejected", True)


def test_new_incident_after_acknowledgement_opens_a_fresh_record() -> None:
    catalog = LastKnownGoodCatalog()
    detector = CatalogAnomalyDetector()
    detector.record(catalog, _discovered(*[f"m{i}" for i in range(10)]), now=_T0)
    t1 = _T0 + timedelta(seconds=1)
    _snapshot, first = detector.record(catalog, _discovered("m0"), now=t1)
    assert first is not None
    detector.acknowledge(first.id, "admin@sjzlab.example", now=t1)

    t2 = t1 + timedelta(days=1)
    detector.record(catalog, _discovered(*[f"n{i}" for i in range(10)]), now=t2)
    t3 = t2 + timedelta(seconds=1)
    _snapshot, second = detector.record(catalog, _discovered("n0"), now=t3)

    check(
        "FTF-0209",
        "a fresh reduction after acknowledgement opens a new anomaly",
        second is not None,
    )
    assert second is not None
    check("FTF-0209", "the new anomaly has a distinct id", second.id != first.id)
    acked_first = detector.get(first.id)
    check(
        "FTF-0209",
        "the prior, acknowledged anomaly is still retained in history",
        acked_first is not None and acked_first.status == AnomalyStatus.ACKNOWLEDGED,
    )
    check(
        "FTF-0209", "history now contains both incidents", detector.history == (acked_first, second)
    )


# -- Negative acceptance: repeated empty list ------------------------------


def test_repeated_empty_discovery_does_not_delete_or_fragment_anomaly_history() -> None:
    catalog = LastKnownGoodCatalog()
    detector = CatalogAnomalyDetector(retry_ceiling=3)
    detector.record(catalog, _discovered(*[f"m{i}" for i in range(10)]), now=_T0)

    # First empty result: a genuine 10 -> 0 reduction, opens the incident.
    t = _T0 + timedelta(seconds=1)
    snapshot, opened = detector.record(catalog, _discovered(), now=t)
    check(
        "FTF-0209",
        "the first empty result after a real baseline is a major reduction",
        opened is not None,
    )
    assert opened is not None
    anomaly_id = opened.id

    # Every further empty result: the catalog's own active count is
    # already 0, so there is nothing further to "reduce" -- this must
    # never be read as license to fabricate a fresh incident, silently
    # drop the existing one, or physically remove any catalog entry.
    for i in range(4):
        t = t + timedelta(seconds=1)
        snapshot, anomaly = detector.record(catalog, _discovered(), now=t)
        check(
            "FTF-0209",
            f"cycle {i}: an already-empty catalog produces no further/duplicate anomaly",
            anomaly is None,
        )
        check(
            "FTF-0209",
            f"cycle {i}: the original incident is still on record, not silently dropped",
            detector.get(anomaly_id) is not None
            and detector.get(anomaly_id).status != AnomalyStatus.ACKNOWLEDGED,  # type: ignore[union-attr]
        )
        check(
            "FTF-0209",
            f"cycle {i}: no catalog entry is ever physically deleted",
            len(snapshot.entries) == 10,
        )
        for original_id in (f"m{n}" for n in range(10)):
            check(
                "FTF-0209",
                f"cycle {i}: {original_id} stays genuinely denied through repeated empty results",
                not catalog.is_call_authorized(original_id, now=t).authorized,
            )

    check(
        "FTF-0209",
        "exactly one incident is on record after five repeated-empty cycles, not five",
        len(detector.history) == 1,
    )
    check(
        "FTF-0209",
        "the retained incident's new_count reflects the real, original drop",
        detector.current is not None and detector.current.new_count == 0,
    )


def test_acknowledge_never_touches_wrapped_catalog() -> None:
    catalog = LastKnownGoodCatalog()
    detector = CatalogAnomalyDetector()
    detector.record(catalog, _discovered(*[f"m{i}" for i in range(10)]), now=_T0)
    t1 = _T0 + timedelta(seconds=1)
    _snapshot, anomaly = detector.record(catalog, _discovered(), now=t1)
    assert anomaly is not None

    before = catalog.snapshot()
    detector.retry(anomaly.id)
    detector.retry(anomaly.id)
    detector.acknowledge(anomaly.id, "admin@sjzlab.example", now=t1 + timedelta(minutes=5))
    after = catalog.snapshot()

    check(
        "FTF-0209",
        "retry/acknowledge produce byte-identical catalog state -- this detector never mutates it",
        before == after,
    )


def test_revocation_result_is_applied_but_never_evaluated_as_a_reduction() -> None:
    catalog = LastKnownGoodCatalog()
    detector = CatalogAnomalyDetector()
    detector.record(catalog, _discovered(*[f"m{i}" for i in range(10)]), now=_T0)
    t1 = _T0 + timedelta(seconds=1)
    snapshot, anomaly = detector.record(catalog, _invalid_token(), now=t1)

    check(
        "FTF-0209",
        "revocation is a distinct, already-handled Section 3.3 concept -- not a reduction anomaly",
        anomaly is None,
    )
    check(
        "FTF-0209",
        "the real catalog was still genuinely revoked",
        not catalog.is_call_authorized("m0", now=t1).authorized,
    )
    check(
        "FTF-0209",
        "the catalog snapshot reflects the real revocation",
        snapshot.access_state.value == "revoked",
    )


def main() -> None:
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()

    if FAILURES:
        print(f"FTF-0209 catalog anomaly detector: {len(FAILURES)} failed")
        for failure in FAILURES:
            print(f"  {failure}")
        sys.exit(1)

    print("FTF-0209 catalog anomaly detector: all checks passed")
    sys.exit(0)


if __name__ == "__main__":
    main()
