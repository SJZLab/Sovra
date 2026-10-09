#!/usr/bin/env python3
"""FTF-R005 round 7 -- no-network behavioural tests (mutation gate).

Round-7 findings (QUEUE Active 0e). The real-server / real-Postgres evidence
is `tests/integration/test_mlflow_durable_outbox.py`; this suite drives the
same code with a scripted client and the DB seams faked:

Q1   strictness is decided by Factory's own record (the completion tag) or a
     terminal status, never by the live status alone: a completed run whose
     payload tag is intact but whose status was flipped back to RUNNING is
     `status_changed` and its missing fields are drift, so it is FAILED.
Q2   the run is read back after the completion write and compared strictly
     before SYNCED: a value altered between the drift check and the write, a
     server that dropped or changed the completion tag, and a failing
     read-back never yield SYNCED.
D3b  `DurableOutbox.record` locks the row (FOR UPDATE) and reports a stored
     outcome that disagrees with the entry as `OutboxRowResolvedError`;
     `sync_pending` then reports the stored truth, never the stale outcome.
F7   a non-latin-1 bearer token gives FAILED "MLflow request could not be
     sent", never an uncaught UnicodeEncodeError.
F4   KILLED is a terminal run status.

Run directly: python3 tests/tasks/test_ftf_r005_r4.py
"""

from __future__ import annotations

import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "apps" / "api"))
sys.path.insert(0, str(ROOT / "tests" / "tasks"))

import test_ftf_r005 as base  # noqa: E402
import test_ftf_r005_r3 as r3  # noqa: E402
from packages.integrations.mlflow.client import (  # noqa: E402
    TrackingRejectedError,
    TrackingUnavailableError,
)
from packages.integrations.mlflow.reconcile import (  # noqa: E402
    TAG_PAYLOAD,
    InMemoryOutbox,
    OutboxEntry,
    OutboxRowResolvedError,
    RetryPolicy,
    SyncState,
    compute_run_drift,
    sync_pending,
    sync_run,
)

Fake, Stub, make_run = base.Fake, base.Stub, base.make_run
FAILURES: list[str] = []


def check(criterion_id: str, description: str, condition: bool) -> None:
    if not condition:
        FAILURES.append(f"FAIL [{criterion_id}] {description}")


def finished() -> tuple[Any, OutboxEntry]:
    fake = Fake()
    entry = InMemoryOutbox().add(make_run(metrics=(("m", 1.0),), params=(("p", "v"),)))
    assert sync_run(fake, entry).state == SyncState.SYNCED
    return fake, entry


def set_tag(run: dict[str, Any], key: str, value: str | None) -> None:
    run["data"]["tags"] = [t for t in run["data"]["tags"] if t["key"] != key]
    if value is not None:
        run["data"]["tags"].append({"key": key, "value": value})


# -- Q1 strictness from Factory's record, not the live status ---------------


def test_completed_run_flipped_to_running_is_drift() -> None:
    # Q1: payload tag intact, status flipped back to RUNNING.
    fake, entry = finished()
    fake.runs[0]["info"]["status"] = "RUNNING"
    result = sync_run(fake, entry)
    check("Q1", "the flipped run is FAILED, never SYNCED", result.state == SyncState.FAILED)
    check("Q1", "status_changed is reported", "status_changed" in result.drift)
    check("Q1", "the error names run drift", str(result.last_error).startswith("run drift:"))


def test_completed_run_flipped_to_running_with_a_missing_field_is_drift() -> None:
    # Q1b: tag intact + RUNNING + a hash tag deleted: missing is not suppressed.
    fake, entry = finished()
    run = fake.runs[0]
    run["info"]["status"] = "RUNNING"
    set_tag(run, "factory.hash.h", None)
    result = sync_run(fake, entry)
    check("Q1b", "FAILED", result.state == SyncState.FAILED)
    check("Q1b", "the missing hash is drift", "hash_missing:factory.hash.h" in result.drift)
    check("Q1b", "and so is the status", "status_changed" in result.drift)
    # Q1c: a metric and a param deleted as well.
    fake, entry = finished()
    run = fake.runs[0]
    run["info"]["status"] = "RUNNING"
    run["data"]["metrics"] = []
    run["data"]["params"] = []
    result = sync_run(fake, entry)
    check("Q1c", "FAILED", result.state == SyncState.FAILED)
    check(
        "Q1c",
        "missing metric and param are drift",
        "metric_missing:m" in result.drift and "param_missing:p" in result.drift,
    )


def test_compute_run_drift_strictness_inputs() -> None:
    run = make_run(metrics=(("m", 1.0),), params=(("p", "v"),))
    tagged_running = {
        "info": {"status": "RUNNING"},
        "data": {"tags": [{"key": TAG_PAYLOAD, "value": run.payload_sha256()}]},
    }
    drift = compute_run_drift(tagged_running, run)
    check("Q1", "tag + RUNNING is strict: status_changed", "status_changed" in drift)
    check("Q1", "tag + RUNNING is strict: missing param", "param_missing:p" in drift)
    untagged_running = {"info": {"status": "RUNNING"}, "data": {"tags": []}}
    check(
        "Q1",
        "no tag + RUNNING (an interrupted attempt) stays lenient about missing fields",
        compute_run_drift(untagged_running, run) == (),
    )
    for status in ("FINISHED", "FAILED", "KILLED"):
        record = {"info": {"status": status}, "data": {"tags": []}}
        got = compute_run_drift(record, make_run(status="FINISHED"))
        check(
            f"F4/{status}",
            "a terminal status without a tag is strict (missing fields are drift)",
            "param_missing:p" in got,
        )
    killed = {"info": {"status": "KILLED"}, "data": {"tags": []}}
    check(
        "F4",
        "KILLED is terminal and compared to the payload's own status",
        "status_changed" in compute_run_drift(killed, make_run(status="FINISHED")),
    )
    killed_ok = compute_run_drift(
        {
            "info": {"status": "KILLED"},
            "data": {
                "tags": [{"key": "factory.hash.h", "value": "a" * 64}],
                "params": [{"key": "p", "value": "v"}],
                "metrics": [{"key": "m", "value": 1.0}],
            },
        },
        make_run(status="KILLED"),
    )
    check("F4", "a KILLED run that matches a KILLED payload has no drift", killed_ok == ())
    no_info = {"data": {"tags": [{"key": TAG_PAYLOAD, "value": "x"}]}}
    check(
        "Q1",
        "a tag with no status at all is strict too",
        "status_changed" in compute_run_drift(no_info, run),
    )


# -- Q2 read-back before SYNCED ---------------------------------------------


def test_read_back_catches_a_tamper_after_the_drift_check() -> None:
    fake = Fake()
    entry = InMemoryOutbox().add(make_run(metrics=(("m", 0.875),)))

    def tamper(client: Any) -> None:
        for item in client.runs[0]["data"].get("metrics", []):
            item["value"] = 0.99

    fake.on_get_run = tamper
    result = sync_run(fake, entry)
    check("Q2", "a value altered after the write is FAILED, never SYNCED", result.state == "FAILED")
    check("Q2", "the changed metric is named", "metric_changed:m" in result.drift)
    check("Q2", "the error names run drift", str(result.last_error).startswith("run drift:"))


def test_read_back_catches_a_dropped_or_changed_completion_tag() -> None:
    for name, mutate, code in (
        ("dropped", lambda c: set_tag(c.runs[0], TAG_PAYLOAD, None), "payload_tag_missing"),
        ("changed", lambda c: set_tag(c.runs[0], TAG_PAYLOAD, "0" * 64), "payload_tag_changed"),
    ):
        fake = Fake()
        entry = InMemoryOutbox().add(make_run())
        fake.on_get_run = mutate
        result = sync_run(fake, entry)
        check(f"Q2/{name}", "FAILED, never SYNCED", result.state == SyncState.FAILED)
        check(f"Q2/{name}", f"{code} is reported", code in result.drift)
    fake = Fake()
    entry = InMemoryOutbox().add(make_run())
    fake.on_get_run = lambda c: c.runs[0]["info"].update(status="RUNNING")
    result = sync_run(fake, entry)
    check(
        "Q2/status",
        "a status the server did not keep is FAILED with status_changed",
        result.state == SyncState.FAILED and "status_changed" in result.drift,
    )


def test_read_back_failure_is_never_synced() -> None:
    fake = Fake()
    fake.fail["get_run"] = TrackingUnavailableError("down")
    entry = InMemoryOutbox().add(make_run())
    result = sync_run(fake, entry)
    check(
        "Q2", "an unavailable read-back is PENDING, never SYNCED", result.state == SyncState.PENDING
    )
    check("Q2", "its error is recorded", result.last_error == "down")
    fake = Fake()
    fake.fail["get_run"] = TrackingRejectedError("nope")
    result = sync_run(fake, InMemoryOutbox().add(make_run()))
    check("Q2", "a rejected read-back is FAILED", result.state == SyncState.FAILED)


def test_clean_run_reads_back_synced_and_replay_does_not_rewrite() -> None:
    fake, entry = finished()
    check("Q2", "an untampered run is SYNCED", entry.state == SyncState.SYNCED)
    before = list(fake.calls)
    again = sync_run(fake, entry)
    check("Q2", "a replay is SYNCED", again.state == SyncState.SYNCED)
    check(
        "Q2",
        "a replay writes nothing",
        not [c for c in fake.calls[len(before) :] if c.startswith(("log", "finish"))],
    )


# -- D3b record locks the row and reports a disagreeing stored outcome ------


def test_record_locks_the_row_it_reads() -> None:
    world = r3.World()
    entry = r3.entry_for(SyncState.SYNCED, mlflow_run_id="mlflow-1")
    r3.seed_pending(world, entry)
    r3.outbox(world).record(entry)
    locked = [str(st) for _org, st in world.statements if "FOR UPDATE" in str(st)]
    check("D8", "the row read in record() is SELECT ... FOR UPDATE", len(locked) == 1)
    check("D8", "the lock is taken on the outbox table", "component_sync_outbox" in locked[0])
    world = r3.World()
    box = r3.outbox(world, r3.Evidence({}))
    box.pending()
    check(
        "D8",
        "the pending-id sweep is not a locking read",
        not [1 for _o, st in world.statements if "FOR UPDATE" in str(st)],
    )


def test_record_reports_a_disagreeing_stored_outcome() -> None:
    cases = (
        # (entry state, stored status, stored external id, stored error)
        (SyncState.PENDING, "succeeded", "mlflow-9", None),
        (SyncState.SYNCED, "failed", None, "run drift: x"),
        (SyncState.PENDING, "failed", None, "boom"),
        (SyncState.FAILED, "succeeded", "mlflow-9", None),
        (SyncState.SYNCED, "succeeded", "mlflow-OTHER", None),
    )
    for state, status, external_id, error in cases:
        world = r3.World()
        entry = r3.entry_for(state, last_error="late", mlflow_run_id="mlflow-1")
        r3.seed_pending(
            world, entry, status=status, external_id=external_id, last_error=error, attempt_count=3
        )
        try:
            r3.outbox(world).record(entry)
            check(f"D3b/{state}/{status}", "a disagreeing outcome is reported", False)
        except OutboxRowResolvedError as exc:
            check(f"D3b/{state}/{status}", "carries the stored status", exc.status == status)
            check(
                f"D3b/{state}/{status}",
                "carries the stored external id",
                exc.external_id == external_id,
            )
            check(f"D3b/{state}/{status}", "carries the stored error", exc.error == error)
        row = world.rows[entry.run.job_id]
        check(f"D3b/{state}/{status}", "no write was made", world.calls == [])
        check(f"D3b/{state}/{status}", "attempt_count untouched", row.attempt_count == 3)


def test_record_accepts_the_same_stored_outcome_as_a_replay() -> None:
    for state, status, external_id in (
        (SyncState.SYNCED, "succeeded", "mlflow-1"),
        (SyncState.FAILED, "failed", None),
        (SyncState.AMBIGUOUS, "failed", None),
    ):
        world = r3.World()
        entry = r3.entry_for(state, last_error="e", mlflow_run_id="mlflow-1")
        r3.seed_pending(world, entry, status=status, external_id=external_id, attempt_count=3)
        r3.outbox(world).record(entry)
        check(f"D3b/{state}", "a same-outcome replay is a silent no-op", world.calls == [])


def test_resolved_error_applies_the_stored_truth_to_the_entry() -> None:
    entry = r3.entry_for(SyncState.SYNCED, last_error=None, mlflow_run_id="mine")
    entry.next_retry_at_ms = 5
    OutboxRowResolvedError("failed", None, "run drift: hash_changed:x").apply_to(entry)
    check("D3b", "a failed row is reported FAILED", entry.state == SyncState.FAILED)
    check("D3b", "with the stored error", entry.last_error == "run drift: hash_changed:x")
    check("D3b", "and no retry time", entry.next_retry_at_ms is None)
    OutboxRowResolvedError("failed", None, "ambiguous: 2 runs").apply_to(entry)
    check("D3b", "an ambiguous-prefixed row is AMBIGUOUS", entry.state == SyncState.AMBIGUOUS)
    OutboxRowResolvedError("failed", None, None).apply_to(entry)
    check("D3b", "a failed row with no error still says why", "failed" in str(entry.last_error))
    OutboxRowResolvedError("succeeded", "mlflow-9", None).apply_to(entry)
    check("D3b", "a succeeded row is SYNCED", entry.state == SyncState.SYNCED)
    check("D3b", "with the stored MLflow run id", entry.mlflow_run_id == "mlflow-9")
    check("D3b", "and no error", entry.last_error is None)


class RacedStore:
    """A store whose row was resolved by another worker before record()."""

    def __init__(self, entry: OutboxEntry, error: Exception) -> None:
        self.entries = [entry]
        self.error = error
        self.policy = RetryPolicy()

    def pending(self) -> list[OutboxEntry]:
        return list(self.entries)

    def record(self, entry: OutboxEntry) -> None:
        raise self.error


def test_sync_pending_reports_the_stored_truth_not_the_stale_outcome() -> None:
    fake = Fake()
    entry = InMemoryOutbox().add(make_run())
    store = RacedStore(entry, OutboxRowResolvedError("failed", None, "run drift: x"))
    done = sync_pending(fake, store, now_ms=1)  # type: ignore[arg-type]
    check("D3b-report", "the sweep returns the entry", done == [entry])
    check(
        "D3b-report",
        "a SYNCED attempt over a failed row is reported FAILED",
        entry.state == SyncState.FAILED,
    )
    check("D3b-report", "with the row's error", entry.last_error == "run drift: x")
    check("D3b-report", "the MLflow side really was written", len(fake.runs) == 1)
    # a PENDING attempt (outage) over a row another worker already synced
    fake = Fake()
    fake.fail["create_run"] = TrackingUnavailableError("down")
    entry = InMemoryOutbox().add(make_run())
    store = RacedStore(entry, OutboxRowResolvedError("succeeded", "mlflow-9", None))
    sync_pending(fake, store, now_ms=1)  # type: ignore[arg-type]
    check(
        "D3b-report",
        "reported SYNCED with the stored id",
        entry.state == SyncState.SYNCED and entry.mlflow_run_id == "mlflow-9",
    )
    # any other error from record propagates
    fake = Fake()
    entry = InMemoryOutbox().add(make_run())
    store = RacedStore(entry, RuntimeError("db down"))
    try:
        sync_pending(fake, store, now_ms=1)  # type: ignore[arg-type]
        check("D3b-report", "an unrelated store error is not swallowed", False)
    except RuntimeError:
        pass


# -- F7 a non-latin-1 bearer token -------------------------------------------


def test_non_latin1_token_is_failed_not_raised() -> None:
    for token in ("tokŁen", "t中", "\U0001f600"):
        stub = Stub()
        stub.reply(200, {"experiment": {"experiment_id": "7", "lifecycle_stage": "active"}})
        client = stub.client(token=token)
        try:
            client.get_or_create_experiment("sovra/x")
            check("F7", f"{token!r}: refused", False)
        except TrackingRejectedError as exc:
            check(
                "F7", f"{token!r}: static message", str(exc) == "MLflow request could not be sent"
            )
            check("F7", f"{token!r}: the token is not echoed", token not in str(exc))
        except UnicodeEncodeError:
            check("F7", f"{token!r}: uncaught UnicodeEncodeError", False)
        result = sync_run(client, InMemoryOutbox().add(make_run()))
        check("F7", f"{token!r}: sync_run is FAILED, not raised", result.state == SyncState.FAILED)
        check(
            "F7",
            f"{token!r}: last_error is the static message",
            result.last_error == "MLflow request could not be sent",
        )


def main() -> None:
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
    if FAILURES + r3.FAILURES:
        print(f"FTF-R005 round-7 tests: {len(FAILURES)} failed")
        for failure in FAILURES:
            print(f"  {failure}")
        sys.exit(1)
    print("FTF-R005 round-7 tests: all checks passed")
    sys.exit(0)


if __name__ == "__main__":
    _ = datetime, UTC, uuid
    main()
