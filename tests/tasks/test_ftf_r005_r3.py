#!/usr/bin/env python3
"""FTF-R005 round 5 -- DurableOutbox: no-DB behavioural tests (mutation gate).

The real-Postgres evidence for the durable outbox is
`tests/integration/test_mlflow_durable_outbox.py`; the CI `mutation-gate`
only re-runs `tests/tasks` (no services), so round 4's gate left 51 mutants
of `packages/integrations/mlflow/durable_outbox.py` alive. This suite drives
the same module with the database layer replaced at its three seams
(`tenant_scoped_session_for`, `record_sync_outcome`, `execute_tenant_scoped`)
by recorders that capture exactly what the outbox asks of them, so every
decision the module makes is pinned by an exact assertion:

R5-1  add: tenant refusal, the admission call, conflict -> RunConflictError,
      entry fields (never due-tracked on admission).
R5-2  pending: the tenant/binding/status-scoped queries, evidence load,
      result_missing / result_mismatch (org and job separately) /
      result_changed resolve FAILED and are never returned, due time derived
      from `updated_at + policy.delay_ms(attempts)` only for attempted rows.
R5-3  record: SYNCED / PENDING / AMBIGUOUS / FAILED each take their own
      branch; an unrecognised state fails closed (never succeeded); the
      500-char error bound; attempt count persisted.
R6-3  record: a row already resolved by a concurrent sync (status no longer
      `pending`) is never rewritten by a late PENDING/SYNCED/FAILED/AMBIGUOUS
      outcome -- status, external id, last_error and attempt_count all stay
      exactly as the concurrent resolution left them (D2, D3b).

Run directly: python3 tests/tasks/test_ftf_r005_r3.py
"""

from __future__ import annotations

import sys
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "apps" / "api"))
sys.path.insert(0, str(ROOT / "tests" / "tasks"))

import test_ftf_r005 as base  # noqa: E402
from packages.integrations.mlflow import durable_outbox as mod  # noqa: E402
from packages.integrations.mlflow.durable_outbox import (  # noqa: E402
    DurableOutbox,
    idempotency_key_for,
)
from packages.integrations.mlflow.reconcile import (  # noqa: E402
    OutboxEntry,
    RetryPolicy,
    RunConflictError,
    SyncState,
    logical_run_key,
)

from app.component_bindings.component_binding_crud import (  # noqa: E402
    ActorInfo,
    SyncOutboxConflictError,
)
from app.db.tenant_context import TenantContext  # noqa: E402

make_run, raises = base.make_run, base.raises
ORG_A, ORG_B = base.ORG_A, base.ORG_B
FAILURES: list[str] = []
BINDING = uuid.UUID("00000000-0000-4000-8000-0000000000b1")
ACTOR = ActorInfo(actor_type="user", actor_id="u-1", request_id="req-1")
CTX = TenantContext(organization_id=ORG_A)
POLICY = RetryPolicy(max_attempts=5, base_delay_ms=1_000, max_delay_ms=300_000)
T0 = datetime(2026, 10, 9, 12, 0, 0, tzinfo=UTC)


def check(criterion_id: str, description: str, condition: bool) -> None:
    if not condition:
        FAILURES.append(f"FAIL [{criterion_id}] {description}")


class World:
    """Recorders standing in for the database seams."""

    def __init__(self) -> None:
        self.rows: dict[uuid.UUID, SimpleNamespace] = {}
        self.pending_ids: list[uuid.UUID] = []
        self.calls: list[dict[str, Any]] = []
        self.statements: list[tuple[uuid.UUID, Any]] = []
        self.session_contexts: list[Any] = []
        self.open_sessions = 0
        self.max_open_at_call = 0
        self.conflict = False
        self.session = object()

    def row(self, job_id: uuid.UUID, **over: Any) -> SimpleNamespace:
        fields: dict[str, Any] = {
            "factory_job_id": job_id,
            "idempotency_key": "unset",
            "status": "pending",
            "attempt_count": 0,
            "external_id": None,
            "last_error": None,
            # Deliberately not T0: R6-2 pins the retry due time to
            # `updated_at`, never `created_at` -- a fake that gives both the
            # same value could not tell the two apart.
            "created_at": T0 - timedelta(hours=1),
            "updated_at": T0,
        }
        fields.update(over)
        self.rows[job_id] = SimpleNamespace(**fields)
        return self.rows[job_id]


class Result:
    def __init__(self, values: list[Any]) -> None:
        self._values = values

    def scalars(self) -> Result:
        return self

    def all(self) -> list[Any]:
        return list(self._values)

    def one(self) -> Any:
        if len(self._values) != 1:
            raise LookupError("expected exactly one row")
        return self._values[0]


def install(world: World) -> None:
    @contextmanager
    def scoped(factory: Any, context: Any) -> Any:
        world.session_contexts.append(context)
        world.open_sessions += 1
        try:
            yield world.session
        finally:
            world.open_sessions -= 1

    def record_sync_outcome(session: Any, context: Any, **kw: Any) -> SimpleNamespace:
        world.max_open_at_call = max(world.max_open_at_call, world.open_sessions)
        world.calls.append({"session": session, "context": context, **kw})
        if world.conflict:
            raise SyncOutboxConflictError("different key")
        job_id = kw["factory_job_id"]
        row = world.rows.get(job_id) or world.row(job_id, idempotency_key=kw["idempotency_key"])
        if kw.get("outcome") == "succeeded":
            row.status, row.external_id = "succeeded", kw.get("external_id")
        elif kw.get("outcome") == "failed":
            row.status, row.last_error = "failed", kw.get("error")
        return row

    def execute_tenant_scoped(session: Any, organization_id: Any, statement: Any) -> Result:
        world.statements.append((organization_id, statement))
        if "ORDER BY" in str(statement):
            return Result(list(world.pending_ids))
        wanted = [v for v in statement.compile().params.values() if isinstance(v, uuid.UUID)]
        return Result([r for r in world.rows.values() if r.factory_job_id in wanted])

    mod.tenant_scoped_session_for = scoped  # type: ignore[assignment]
    mod.record_sync_outcome = record_sync_outcome  # type: ignore[assignment]
    mod.execute_tenant_scoped = execute_tenant_scoped  # type: ignore[assignment]


class Evidence:
    def __init__(self, runs: dict[uuid.UUID, Any]) -> None:
        self.runs = runs
        self.loaded: list[tuple[uuid.UUID, uuid.UUID]] = []

    def load(self, organization_id: uuid.UUID, job_id: uuid.UUID) -> Any:
        self.loaded.append((organization_id, job_id))
        return self.runs.get(job_id)


def outbox(world: World, evidence: Evidence | None = None) -> DurableOutbox:
    install(world)
    return DurableOutbox(
        lambda: None,  # type: ignore[arg-type, return-value]
        CTX,
        binding_id=BINDING,
        results=evidence or Evidence({}),
        actor=ACTOR,
        policy=POLICY,
        clock=lambda: T0 + timedelta(seconds=90),
    )


def ms(moment: datetime) -> int:
    return int(moment.timestamp() * 1000)


# -- R5-1 add --------------------------------------------------------------


def test_add_refuses_a_foreign_organization_before_touching_the_database() -> None:
    world = World()
    box = outbox(world)
    check(
        "R5-1",
        "a result of another organization is a RunConflictError",
        raises(RunConflictError, box.add, make_run(organization_id=ORG_B)),
    )
    check("R5-1", "...and nothing was admitted or opened", not world.calls)
    check("R5-1", "...no session was opened", not world.session_contexts)


def test_add_admits_with_the_payload_idempotency_key() -> None:
    world = World()
    run = make_run()
    world.row(run.job_id, idempotency_key=idempotency_key_for(run), attempt_count=3)
    world.rows[run.job_id].external_id = "mlflow-9"
    world.rows[run.job_id].last_error = "boom"
    entry = outbox(world).add(run)
    check("R5-1", "exactly one admission call", len(world.calls) == 1)
    call = world.calls[0]
    check("R5-1", "the tenant context is passed", call["context"] is CTX)
    check("R5-1", "the session is the tenant-scoped one", call["session"] is world.session)
    check("R5-1", "the binding is passed", call["component_binding_id"] == BINDING)
    check("R5-1", "the job is passed", call["factory_job_id"] == run.job_id)
    check(
        "R5-1",
        "the key is mlflow:<payload sha256>",
        call["idempotency_key"] == f"mlflow:{run.payload_sha256()}",
    )
    check("R5-1", "the actor is passed", call["actor"] is ACTOR)
    check(
        "R5-1",
        "admission carries no outcome, error or external id",
        call.get("outcome") is None and "error" not in call and "external_id" not in call,
    )
    check("R5-1", "the tenant session was opened with the context", world.session_contexts == [CTX])
    check(
        "R5-1",
        "the entry id is the logical run key",
        entry.entry_id == logical_run_key(run.organization_id, run.job_id),
    )
    check("R5-1", "the entry carries the run", entry.run is run)
    check("R5-1", "attempts come from the row", entry.attempts == 3)
    check("R5-1", "the MLflow run id comes from the row", entry.mlflow_run_id == "mlflow-9")
    check("R5-1", "the last error comes from the row", entry.last_error == "boom")
    check(
        "R5-1",
        "an admission replay is never due-tracked, even with attempts",
        entry.next_retry_at_ms is None,
    )
    check("R5-1", "state is the OutboxEntry default (pending)", entry.state == SyncState.PENDING)


def test_add_maps_a_key_conflict_to_run_conflict() -> None:
    world = World()
    world.conflict = True
    box = outbox(world)
    try:
        box.add(make_run())
        check("R5-1", "a conflicting key is refused", False)
    except RunConflictError as exc:
        check(
            "R5-1",
            "the conflict is chained from the R002 error",
            isinstance(exc.__cause__, SyncOutboxConflictError),
        )
        check("R5-1", "the message names the cause", "different result" in str(exc))


# -- R5-2 pending ----------------------------------------------------------


def test_pending_queries_are_scoped_to_tenant_binding_and_pending_status() -> None:
    world = World()
    outbox(world).pending()
    check("R5-2", "one query for the pending ids", len(world.statements) == 1)
    org, statement = world.statements[0]
    check("R5-2", "the guard is told the tenant organization", org == ORG_A)
    sql = " ".join(str(statement).split())
    params = statement.compile().params
    check(
        "R5-2", "selects the job id", sql.startswith("SELECT component_sync_outbox.factory_job_id")
    )
    check(
        "R5-2", "filters by organization with =", "component_sync_outbox.organization_id = " in sql
    )
    check(
        "R5-2", "filters by binding with =", "component_sync_outbox.component_binding_id = " in sql
    )
    check("R5-2", "filters by status with =", "component_sync_outbox.status = " in sql)
    check("R5-2", "no inequality in the scope", "!=" not in sql and "<>" not in sql)
    check(
        "R5-2",
        "bound to this tenant, this binding and 'pending'",
        sorted(map(str, params.values())) == sorted([str(ORG_A), str(BINDING), "pending"]),
    )
    check(
        "R5-2",
        "oldest first, id as the tie-break",
        "ORDER BY component_sync_outbox.created_at, component_sync_outbox.id" in sql,
    )


def test_pending_returns_due_tracked_entries_for_attempted_rows_only() -> None:
    world = World()
    run_new, run_retry = make_run(job_id=uuid.uuid4()), make_run(job_id=uuid.uuid4())
    world.pending_ids = [run_new.job_id, run_retry.job_id]
    world.row(run_new.job_id, idempotency_key=idempotency_key_for(run_new), attempt_count=0)
    world.row(
        run_retry.job_id,
        idempotency_key=idempotency_key_for(run_retry),
        attempt_count=3,
        external_id="r-1",
        last_error="e",
        updated_at=T0,
    )
    evidence = Evidence({run_new.job_id: run_new, run_retry.job_id: run_retry})
    entries = outbox(world, evidence).pending()
    check("R5-2", "both rows come back in order", [e.run for e in entries] == [run_new, run_retry])
    check(
        "R5-2",
        "evidence was loaded per job, for this tenant",
        evidence.loaded
        == [
            (ORG_A, run_new.job_id),
            (ORG_A, run_retry.job_id),
        ],
    )
    new, retry = entries
    check("R5-2", "a never-attempted row has no retry time", new.next_retry_at_ms is None)
    check("R5-2", "a never-attempted row has attempts 0", new.attempts == 0)
    check(
        "R5-2",
        "an attempted row is due updated_at + delay_ms(attempts)",
        retry.next_retry_at_ms == ms(T0) + POLICY.delay_ms(3) == ms(T0) + 4_000,
    )
    check(
        "R6-4",
        "...not created_at + delay (created_at is 1h earlier, a real fixture "
        "field, not an attribute a fake merely lacks)",
        retry.next_retry_at_ms != ms(T0 - timedelta(hours=1)) + POLICY.delay_ms(3),
    )
    check("R5-2", "attempts from the row", retry.attempts == 3)
    check("R5-2", "mlflow run id from the row", retry.mlflow_run_id == "r-1")
    check("R5-2", "last error from the row", retry.last_error == "e")
    check(
        "R5-2",
        "entry id is the logical run key",
        retry.entry_id == logical_run_key(run_retry.organization_id, run_retry.job_id),
    )
    check("R5-2", "nothing was failed", all(c.get("outcome") is None for c in world.calls))
    check("R5-2", "the due time uses the entry's attempts", POLICY.delay_ms(1) == 1_000)
    one = World()
    run_one = make_run(job_id=uuid.uuid4())
    one.pending_ids = [run_one.job_id]
    one.row(run_one.job_id, idempotency_key=idempotency_key_for(run_one), attempt_count=1)
    got = outbox(one, Evidence({run_one.job_id: run_one})).pending()
    check(
        "R5-2", "one attempt is due after the base delay", got[0].next_retry_at_ms == ms(T0) + 1_000
    )


def failed_calls(world: World) -> list[dict[str, Any]]:
    return [c for c in world.calls if c.get("outcome") == "failed"]


def test_pending_fails_a_missing_result_visibly() -> None:
    world = World()
    job = uuid.uuid4()
    world.pending_ids = [job]
    world.row(job, idempotency_key="mlflow:abc")
    entries = outbox(world, Evidence({})).pending()
    check("R5-2", "an unloadable row is not returned", entries == [])
    calls = failed_calls(world)
    check("R5-2", "it is resolved failed exactly once", len(calls) == 1)
    call = calls[0]
    check(
        "R5-2",
        "with the Law 17 reason",
        call["error"] == "result_missing: Factory evidence has no result",
    )
    check("R5-2", "against the stored idempotency key", call["idempotency_key"] == "mlflow:abc")
    check(
        "R5-2",
        "for this binding and job",
        call["component_binding_id"] == BINDING and call["factory_job_id"] == job,
    )
    check(
        "R5-2", "as this actor in this context", call["actor"] is ACTOR and call["context"] is CTX
    )
    check("R5-2", "the row is now failed", world.rows[job].status == "failed")


def test_pending_fails_an_identity_mismatch_for_org_and_job_separately() -> None:
    for label, over in (
        ("organization differs", {"organization_id": ORG_B}),
        ("job differs", {"job_id": uuid.uuid4()}),
    ):
        world = World()
        job = uuid.uuid4()
        world.pending_ids = [job]
        world.row(job, idempotency_key="mlflow:abc")
        returned = make_run(**({"job_id": job} | over))
        entries = outbox(world, Evidence({job: returned})).pending()
        check("R5-2", f"{label}: not returned", entries == [])
        calls = failed_calls(world)
        check("R5-2", f"{label}: failed once", len(calls) == 1)
        check(
            "R5-2",
            f"{label}: result_mismatch reason",
            bool(calls) and calls[0]["error"] == "result_mismatch: evidence identity differs",
        )
        check("R5-2", f"{label}: row failed", world.rows[job].status == "failed")


def test_pending_fails_changed_evidence_outside_the_read_session() -> None:
    world = World()
    job = uuid.uuid4()
    run = make_run(job_id=job)
    other = make_run(job_id=job, completed_at_ms=999)
    world.pending_ids = [job]
    world.row(job, idempotency_key=idempotency_key_for(other))
    entries = outbox(world, Evidence({job: run})).pending()
    check("R5-2", "a result changed after admission is not returned", entries == [])
    calls = failed_calls(world)
    check("R5-2", "it is failed once", len(calls) == 1)
    check(
        "R5-2",
        "with the result_changed reason",
        bool(calls) and calls[0]["error"] == "result_changed: evidence differs from admission",
    )
    check(
        "R5-2",
        "against the admitted key",
        bool(calls) and calls[0]["idempotency_key"] == idempotency_key_for(other),
    )
    check("R5-2", "the failure never runs inside the read session", world.max_open_at_call == 1)
    check("R5-2", "the row is failed", world.rows[job].status == "failed")


def test_pending_row_lookup_is_scoped_to_tenant_binding_and_job() -> None:
    world = World()
    job = uuid.uuid4()
    run = make_run(job_id=job)
    world.pending_ids = [job]
    world.row(job, idempotency_key=idempotency_key_for(run))
    outbox(world, Evidence({job: run})).pending()
    lookups = [(o, s) for o, s in world.statements if "ORDER BY" not in str(s)]
    check("R5-2", "one row lookup", len(lookups) == 1)
    org, statement = lookups[0]
    sql = " ".join(str(statement).split())
    check("R5-2", "guard told the tenant organization", org == ORG_A)
    check("R5-2", "organization filter is =", "component_sync_outbox.organization_id = " in sql)
    check("R5-2", "binding filter is =", "component_sync_outbox.component_binding_id = " in sql)
    check("R5-2", "job filter is =", "component_sync_outbox.factory_job_id = " in sql)
    check("R5-2", "no inequality", "!=" not in sql and "<>" not in sql)
    check(
        "R5-2",
        "bound to this tenant, binding and job",
        sorted(map(str, statement.compile().params.values()))
        == sorted([str(ORG_A), str(BINDING), str(job)]),
    )


# -- R5-3 record -----------------------------------------------------------


def entry_for(state: str, **over: Any) -> OutboxEntry:
    run = make_run()
    fields: dict[str, Any] = {
        "entry_id": logical_run_key(run.organization_id, run.job_id),
        "run": run,
        "state": state,
        "attempts": 4,
        "mlflow_run_id": "mlflow-7",
        "last_error": None,
    }
    fields.update(over)
    return OutboxEntry(**fields)


def seed_pending(world: World, entry: OutboxEntry, **over: Any) -> None:
    """A `record()` call always follows a row `add()` or `pending()` already
    put there -- R6-3 below proves `record()` itself never changes a row
    that is not (still) `pending`, so every test of its normal branches
    needs a real pending row to start from."""
    fields: dict[str, Any] = {
        "idempotency_key": idempotency_key_for(entry.run),
        "status": "pending",
    }
    fields.update(over)
    world.row(entry.run.job_id, **fields)


def test_record_synced_resolves_succeeded_with_the_external_id() -> None:
    world = World()
    entry = entry_for(SyncState.SYNCED, last_error="stale")
    seed_pending(world, entry)
    outbox(world).record(entry)
    check("R5-3", "one call", len(world.calls) == 1)
    call = world.calls[0]
    key = idempotency_key_for(entry.run)
    check("R5-3", "outcome succeeded", call["outcome"] == "succeeded")
    check("R5-3", "external id is the MLflow run id", call["external_id"] == "mlflow-7")
    check("R5-3", "no error on success", call.get("error") is None)
    check(
        "R5-3",
        "key, binding, job, actor, context",
        (
            call["idempotency_key"] == key
            and call["component_binding_id"] == BINDING
            and call["factory_job_id"] == entry.run.job_id
            and call["actor"] is ACTOR
            and call["context"] is CTX
            and call["session"] is world.session
        ),
    )
    check("R5-3", "attempts persisted", world.rows[entry.run.job_id].attempt_count == 4)
    check("R5-3", "row succeeded", world.rows[entry.run.job_id].status == "succeeded")


def test_record_pending_keeps_the_row_pending_and_bounds_the_error() -> None:
    cases = (
        ("short error", "timeout", "timeout"),
        ("exactly the bound", "e" * 500, "e" * 500),
        ("over the bound", "e" * 501, "e" * 500),
        ("far over the bound", "x" * 900, "x" * 500),
        ("empty error", "", None),
        ("no error", None, None),
    )
    for label, given, stored in cases:
        world = World()
        entry = entry_for(SyncState.PENDING, last_error=given)
        seed_pending(world, entry)
        outbox(world).record(entry)
        row = world.rows[entry.run.job_id]
        call = world.calls[0]
        check("R5-3", f"pending/{label}: admits without an outcome", call.get("outcome") is None)
        check(
            "R5-3",
            f"pending/{label}: no error or external id",
            "error" not in call and "external_id" not in call,
        )
        check("R5-3", f"pending/{label}: stored error", row.last_error == stored)
        check("R5-3", f"pending/{label}: attempts persisted", row.attempt_count == 4)
        check(
            "R5-3",
            f"pending/{label}: updated_at is the clock",
            row.updated_at == T0 + timedelta(seconds=90),
        )
        check("R5-3", f"pending/{label}: still pending", row.status == "pending")


def test_record_failed_and_ambiguous_resolve_failed_with_their_prefix() -> None:
    cases = (
        (SyncState.FAILED, "boom", "boom"),
        (SyncState.FAILED, None, "sync failed"),
        (SyncState.FAILED, "", "sync failed"),
        (SyncState.AMBIGUOUS, "two runs", "ambiguous: two runs"),
        (SyncState.AMBIGUOUS, None, "ambiguous: sync failed"),
    )
    for state, given, expected in cases:
        world = World()
        entry = entry_for(state, last_error=given)
        seed_pending(world, entry)
        outbox(world).record(entry)
        call = world.calls[0]
        label = f"{state}/{given!r}"
        check("R5-3", f"{label}: outcome failed", call["outcome"] == "failed")
        check("R5-3", f"{label}: error text", call["error"] == expected)
        check("R5-3", f"{label}: no external id", "external_id" not in call)
        check(
            "R5-3", f"{label}: attempts persisted", world.rows[entry.run.job_id].attempt_count == 4
        )
        check("R5-3", f"{label}: row failed", world.rows[entry.run.job_id].status == "failed")


def test_record_fails_closed_on_an_unrecognised_or_denormalised_state() -> None:
    for state in ("synced", " SYNCED", "SYNCED ", "Synced", "tracking_sync_pending", "weird"):
        world = World()
        entry = entry_for(state, last_error="odd")
        seed_pending(world, entry)
        outbox(world).record(entry)
        call = world.calls[0]
        check("R5-3", f"{state!r}: never recorded as succeeded", call.get("outcome") != "succeeded")
        check("R5-3", f"{state!r}: failed visibly", call.get("outcome") == "failed")
        check("R5-3", f"{state!r}: no ambiguous prefix", call.get("error") == "odd")
    world = World()
    odd_entry = entry_for(" ambiguous", last_error="odd")
    seed_pending(world, odd_entry)
    outbox(world).record(odd_entry)
    check(
        "R5-3",
        "a denormalised AMBIGUOUS is a plain failure, not an ambiguous one",
        world.calls[0].get("error") == "odd",
    )


def test_record_pending_ignores_the_run_when_the_state_is_not_pending() -> None:
    world = World()
    entry = entry_for(SyncState.FAILED, last_error="x")
    seed_pending(world, entry)
    outbox(world).record(entry)
    row = world.rows[entry.run.job_id]
    check("R5-3", "a failed entry does not touch updated_at", row.updated_at == T0)


# -- R6-3 record never rewrites an already-resolved row ---------------------


def test_record_never_rewrites_a_row_resolved_concurrently() -> None:
    # D2: a stale PENDING outcome (this process's own attempt, raced by a
    # concurrent sync that already resolved the row `succeeded`) must not
    # overwrite that row's status, external id, last_error or attempt count.
    world = World()
    pending_entry = entry_for(SyncState.PENDING, last_error="transient timeout")
    seed_pending(
        world,
        pending_entry,
        status="succeeded",
        external_id="mlflow-already-synced",
        attempt_count=2,
        last_error=None,
        updated_at=T0,
    )
    outbox(world).record(pending_entry)
    row = world.rows[pending_entry.run.job_id]
    check("R6-3", "no record_sync_outcome call was made at all", world.calls == [])
    check("R6-3", "status untouched", row.status == "succeeded")
    check("R6-3", "external id untouched", row.external_id == "mlflow-already-synced")
    check("R6-3", "attempt_count untouched by the stale pending write", row.attempt_count == 2)
    check("R6-3", "last_error untouched", row.last_error is None)
    check("R6-3", "updated_at untouched", row.updated_at == T0)

    # D3b: a late SYNCED/FAILED outcome racing a row a concurrent sync
    # already resolved `failed` must not rewrite its attempt count either.
    for state in (SyncState.SYNCED, SyncState.FAILED, SyncState.AMBIGUOUS):
        world = World()
        late_entry = entry_for(state, last_error="late", attempts=9)
        seed_pending(
            world,
            late_entry,
            status="failed",
            last_error="original failure",
            attempt_count=3,
            external_id=None,
        )
        outbox(world).record(late_entry)
        row = world.rows[late_entry.run.job_id]
        check(f"R6-3/{state}", "no record_sync_outcome call was made at all", world.calls == [])
        check(f"R6-3/{state}", "status stays failed", row.status == "failed")
        check(f"R6-3/{state}", "attempt_count untouched by the late write", row.attempt_count == 3)
        check(f"R6-3/{state}", "last_error untouched", row.last_error == "original failure")
        check(f"R6-3/{state}", "external id untouched", row.external_id is None)


def main() -> None:
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
    if FAILURES:
        print(f"FTF-R005 round-5 tests: {len(FAILURES)} failed")
        for failure in FAILURES:
            print(f"  {failure}")
        sys.exit(1)
    print("FTF-R005 round-5 tests: all checks passed")
    sys.exit(0)


if __name__ == "__main__":
    main()
