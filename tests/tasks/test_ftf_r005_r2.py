#!/usr/bin/env python3
"""FTF-R005 round 2 -- MLflow projection: no-network unit/fault tests.

Round-2 findings (QUEUE Active 0e). The real-server and real-Postgres
evidence is `tests/integration/test_mlflow_durable_outbox.py`; this suite
drives the same code with a scripted client and a loopback HTTP stub:

R2-1  run-level drift (Section 78.3): a completed run is compared field by
      field with the Factory payload (hashes, params, metrics, status); a
      changed hash tag, an extra metric/param/hash, a missing or changed
      value is FAILED with drift codes, never SYNCED, never overwritten.
R2-2  Law 17: model-version PENDING_REGISTRATION / FAILED_REGISTRATION /
      missing or unknown status is surfaced by verify_binding and the
      projection, never passes and never reports drift None.
R2-3  bounded retries (Section 77.5): attempt limit, exponential next-retry
      time, entries not yet due are skipped, the limit FAILs visibly.
R2-5  the bearer token never appears in an error, whatever the failure.
R2-6  search_model_versions_by_tag pages beyond one page and refuses a
      non-advancing cursor.
R2-4  client_from_binding builds the adapter only from the MLflow
      ComponentBinding (the durable outbox itself: integration file).

Run directly: python3 tests/tasks/test_ftf_r005_r2.py
"""

from __future__ import annotations

import ipaddress
import sys
import uuid
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "apps" / "api"))
sys.path.insert(0, str(ROOT / "tests" / "tasks"))

import test_ftf_r005 as base  # noqa: E402
from packages.integrations.mlflow.client import (  # noqa: E402
    MAX_PAGES,
    MlflowClient,
    TrackingRejectedError,
    TrackingUnavailableError,
    run_metrics,
    run_params,
)
from packages.integrations.mlflow.durable_outbox import (  # noqa: E402
    DurableOutbox,
    _utcnow,
    client_from_binding,
)
from packages.integrations.mlflow.reconcile import (  # noqa: E402
    DEFAULT_RETRY_POLICY,
    InMemoryOutbox,
    OutboxEntry,
    RetryPolicy,
    SyncState,
    compute_run_drift,
    sync_pending,
    sync_run,
)
from packages.integrations.mlflow.registry_binding import (  # noqa: E402
    PROJECTION_FIELDS,
    TAG_ARTIFACT,
    RegistryDriftError,
    RegistryFailedError,
    RegistryPendingError,
    inspect_binding,
    project_registry_binding,
    verify_binding,
)
from packages.security.url_policy import ConnectionPolicy  # noqa: E402

from app.component_bindings.component_binding_crud import ActorInfo  # noqa: E402
from app.component_bindings.component_binding_schema import (  # noqa: E402
    DEFAULT_SECURITY_ZONE_SCOPE,
    ComponentBinding,
)
from app.db.tenant_context import TenantContext  # noqa: E402

Fake, Stub, make_run, tag_list = base.Fake, base.Stub, base.make_run, base.tag_list
make_binding, raises = base.make_binding, base.raises
ORG_A, JOB, MV, PREFIXES = base.ORG_A, base.JOB, base.MV, base.PREFIXES
FAILURES: list[str] = []
TOKEN = "s3cr3t-Token-VALUE-0123456789"


def check(criterion_id: str, description: str, condition: bool) -> None:
    if not condition:
        FAILURES.append(f"FAIL [{criterion_id}] {description}")


# -- R2-1 run-level drift --------------------------------------------------


def synced() -> tuple[Any, OutboxEntry]:
    fake = Fake()
    entry = InMemoryOutbox().add(
        make_run(
            metrics=(("m", 1.0), ("n", 2)),
            params=(("p", "v"), ("q", "w")),
            hashes=(("h", "a" * 64), ("k", "b" * 64)),
        )
    )
    assert sync_run(fake, entry).state == SyncState.SYNCED
    return fake, entry


def set_tag(run: dict[str, Any], key: str, value: str | None) -> None:
    run["data"]["tags"] = [t for t in run["data"]["tags"] if t["key"] != key]
    if value is not None:
        run["data"]["tags"].append({"key": key, "value": value})


def drift_after(mutate: Any) -> tuple[Any, OutboxEntry, int]:
    fake, entry = synced()
    mutate(fake.runs[0])
    before = len(fake.calls)
    sync_run(fake, entry)
    return fake, entry, before


def test_run_level_drift() -> None:
    fake, entry = synced()
    check(
        "R2-1",
        "an untouched completed run is clean (no false drift)",
        entry.drift == () and compute_run_drift(fake.runs[0], entry.run) == (),
    )
    again = sync_run(fake, entry)
    check("R2-1", "clean replay stays SYNCED", again.state == SyncState.SYNCED)
    check(
        "R2-1",
        "an int metric equals the float MLflow reports",
        compute_run_drift(fake.runs[0], entry.run) == (),
    )

    cases: dict[str, tuple[Any, str]] = {
        "changed hash tag": (
            lambda r: set_tag(r, "factory.hash.h", "c" * 64),
            "hash_changed:factory.hash.h",
        ),
        "missing hash tag": (lambda r: set_tag(r, "factory.hash.k", None), "hash_missing:"),
        "extra hash tag": (
            lambda r: set_tag(r, "factory.hash.x", "d" * 64),
            "hash_extra:factory.hash.x",
        ),
        "extra metric": (
            lambda r: r["data"]["metrics"].append({"key": "extra", "value": 1.0}),
            "metric_extra:extra",
        ),
        "changed metric": (
            lambda r: r["data"]["metrics"].__setitem__(
                0, {"key": "m", "value": 9.0, "timestamp": 5, "step": 0}
            ),
            "metric_changed:m",
        ),
        "missing metric": (lambda r: r["data"]["metrics"].pop(1), "metric_missing:n"),
        "extra param": (
            lambda r: r["data"]["params"].append({"key": "z", "value": "1"}),
            "param_extra:z",
        ),
        "changed param": (
            lambda r: r["data"]["params"].__setitem__(0, {"key": "p", "value": "other"}),
            "param_changed:p",
        ),
        "missing param": (lambda r: r["data"]["params"].pop(1), "param_missing:q"),
        "changed status": (lambda r: r["info"].__setitem__("status", "FAILED"), "status_changed"),
    }
    for label, (mutate, code) in cases.items():
        f, e, before = drift_after(mutate)
        check("R2-1", f"{label}: reported FAILED, never SYNCED", e.state == SyncState.FAILED)
        check("R2-1", f"{label}: drift code {code!r}", any(c.startswith(code) for c in e.drift))
        check(
            "R2-1",
            f"{label}: error names the drift",
            (e.last_error or "").startswith("run drift:"),
        )
        check(
            "R2-1",
            f"{label}: nothing is written back over the drifted run",
            not any(c in ("create_run", "finish") or c.startswith("log:") for c in f.calls[before:])
            and "log_batch" not in f.calls[before:],
        )
        check("R2-1", f"{label}: no second run created", len(f.runs) == 1)

    # a drifted run whose payload tag was also changed keeps the older error
    f, e, _ = drift_after(lambda r: set_tag(r, "factory.payload_sha256", "0" * 64))
    check("R2-1", "payload-tag change still FAILED", e.state == SyncState.FAILED and e.drift == ())

    # several simultaneous drifts are all listed, sorted
    def two(r: dict[str, Any]) -> None:
        set_tag(r, "factory.hash.h", "c" * 64)
        r["data"]["metrics"].append({"key": "extra", "value": 1.0})

    f, e, _ = drift_after(two)
    check(
        "R2-1",
        "all drift codes are listed, sorted",
        e.drift == ("hash_changed:factory.hash.h", "metric_extra:extra"),
    )
    # a run with no data at all is wholly drifted, not clean
    check(
        "R2-1",
        "a run without data is drift, not clean",
        len(compute_run_drift({"info": {"status": "FINISHED"}}, entry.run)) >= 6,
    )
    check(
        "R2-1",
        "a bool metric value from the server is not a number",
        "metric_missing:m"
        in compute_run_drift(
            {"info": {"status": "FINISHED"}, "data": {"metrics": [{"key": "m", "value": True}]}},
            entry.run,
        ),
    )
    # recovery: the drift is cleared once the run matches again
    fake2, entry2 = synced()
    set_tag(fake2.runs[0], "factory.hash.h", "c" * 64)
    sync_run(fake2, entry2)
    set_tag(fake2.runs[0], "factory.hash.h", "a" * 64)
    healed = sync_run(fake2, entry2)
    check(
        "R2-1",
        "a healed run is SYNCED again with drift cleared",
        healed.state == SyncState.SYNCED and healed.drift == () and healed.last_error is None,
    )


# -- R2-3 bounded retries --------------------------------------------------


def test_bounded_retries() -> None:
    for bad in (0, -1, True, 1.5, "5", None):
        check("R2-3", f"max_attempts {bad!r} refused", raises(ValueError, RetryPolicy, bad))
        check(
            "R2-3",
            f"base_delay_ms {bad!r} refused",
            raises(ValueError, RetryPolicy, 3, bad, 10_000),
        )
    check("R2-3", "max < base refused", raises(ValueError, RetryPolicy, 3, 2000, 1000))
    check(
        "R2-3",
        "defaults are bounded",
        (DEFAULT_RETRY_POLICY.max_attempts, DEFAULT_RETRY_POLICY.base_delay_ms) == (5, 1000)
        and DEFAULT_RETRY_POLICY.max_delay_ms == 300_000,
    )
    pol = RetryPolicy(max_attempts=3, base_delay_ms=1000, max_delay_ms=8000)
    check(
        "R2-3",
        "delay doubles per attempt",
        [pol.delay_ms(n) for n in (1, 2, 3, 4)] == [1000, 2000, 4000, 8000],
    )
    check("R2-3", "delay is capped", pol.delay_ms(5) == 8000 and pol.delay_ms(10_000) == 8000)
    check("R2-3", "attempt 0 is treated as the first", pol.delay_ms(0) == 1000)

    fake, box = Fake(), InMemoryOutbox()
    entry = box.add(make_run())
    fake.fail["experiment"] = TrackingUnavailableError("down")
    sync_run(fake, entry, now_ms=10_000, policy=pol)
    check(
        "R2-3",
        "first outage: pending with a next-retry time",
        entry.state == SyncState.PENDING
        and entry.attempts == 1
        and entry.next_retry_at_ms == 11_000,
    )
    sync_run(fake, entry, now_ms=20_000, policy=pol)
    check(
        "R2-3",
        "second outage: pending, later retry",
        entry.state == SyncState.PENDING and entry.next_retry_at_ms == 22_000,
    )
    sync_run(fake, entry, now_ms=30_000, policy=pol)
    check(
        "R2-3",
        "attempt limit: FAILED, visible, no further retry",
        entry.state == SyncState.FAILED
        and entry.attempts == 3
        and entry.next_retry_at_ms is None
        and "retry limit reached after 3 attempts" in (entry.last_error or "")
        and box.pending() == [],
    )
    # not an outage: a rejection is never retried and carries no retry time
    f2, b2 = Fake(), InMemoryOutbox()
    e2 = b2.add(make_run())
    f2.fail["experiment"] = TrackingRejectedError("no")
    sync_run(f2, e2, now_ms=1, policy=pol)
    check("R2-3", "rejection has no retry time", e2.state == "FAILED" and not e2.next_retry_at_ms)
    # a success after outages clears the retry bookkeeping
    f3, b3 = Fake(), InMemoryOutbox()
    e3 = b3.add(make_run())
    f3.fail["experiment"] = TrackingUnavailableError("down")
    sync_run(f3, e3, now_ms=0, policy=pol)
    f3.fail.clear()
    sync_run(f3, e3, now_ms=5000, policy=pol)
    check(
        "R2-3",
        "success clears next retry and error",
        e3.state == SyncState.SYNCED and e3.next_retry_at_ms is None and e3.last_error is None,
    )

    # sync_pending: entries not yet due are skipped, due ones run, record() persists
    class Recording(InMemoryOutbox):
        def __init__(self) -> None:
            super().__init__()
            self.recorded: list[str] = []

        def record(self, entry: OutboxEntry) -> None:
            self.recorded.append(entry.entry_id)
            super().record(entry)

    f4, b4 = Fake(), Recording()
    not_due = b4.add(make_run())
    not_due.next_retry_at_ms = 1001
    due = b4.add(make_run(job_id=uuid.uuid4()))
    due.next_retry_at_ms = 1000
    res = sync_pending(f4, b4, now_ms=1000, policy=pol)
    check(
        "R2-3",
        "only the due entry (boundary inclusive) is attempted",
        [e.entry_id for e in res] == [due.entry_id]
        and not_due.attempts == 0
        and due.state == SyncState.SYNCED,
    )
    check("R2-3", "outcome is handed to the store", b4.recorded == [due.entry_id])
    check("R2-3", "a skipped entry is not recorded", not_due.entry_id not in b4.recorded)
    f5, b5 = Fake(), InMemoryOutbox()
    only = b5.add(make_run())
    f5.fail["experiment"] = TrackingUnavailableError("down")
    sync_pending(f5, b5, now_ms=0, policy=RetryPolicy(max_attempts=1))
    check("R2-3", "policy reaches sync_run: limit 1 fails at once", only.state == SyncState.FAILED)
    # an entry past the limit never comes back
    check("R2-3", "failed entry leaves pending", b5.pending() == [])
    check("R2-3", "second sweep is a no-op", sync_pending(f5, b5, now_ms=10**15) == [])


# -- R2-2 Law 17 registry statuses -----------------------------------------


def test_registry_status() -> None:
    fake = Fake()
    binding = make_binding(fake)
    verify_binding(fake, binding, approved_source_prefixes=PREFIXES)
    check(
        "R2-2",
        "READY with a matching envelope passes",
        inspect_binding(fake, binding, approved_source_prefixes=PREFIXES) == ("READY", None),
    )
    record = fake.versions[0]
    for status, exc in (
        ("PENDING_REGISTRATION", RegistryPendingError),
        ("FAILED_REGISTRATION", RegistryFailedError),
        (None, RegistryPendingError),
        ("ready", RegistryPendingError),
        ("READY ", RegistryPendingError),
        ("", RegistryPendingError),
        (7, RegistryPendingError),
        ([], RegistryPendingError),
        ({}, RegistryPendingError),
    ):
        record["status"] = status
        check(
            "R2-2",
            f"status {status!r} is refused by verify_binding with {exc.__name__}",
            raises(exc, verify_binding, fake, binding, approved_source_prefixes=PREFIXES),
        )
        check(
            "R2-2",
            f"status {status!r} is not reported as drift",
            not raises(
                RegistryDriftError, verify_binding, fake, binding, approved_source_prefixes=PREFIXES
            ),
        )
        state, reason = inspect_binding(fake, binding, approved_source_prefixes=PREFIXES)
        check(
            "R2-2",
            f"status {status!r}: inspect never reports a clean binding",
            state != "READY" and bool(reason),
        )
    record["status"] = "PENDING_REGISTRATION"
    check(
        "R2-2",
        "pending is named PENDING_REGISTRATION",
        inspect_binding(fake, binding, approved_source_prefixes=PREFIXES)[0]
        == "PENDING_REGISTRATION",
    )
    record["status"] = "FAILED_REGISTRATION"
    check(
        "R2-2",
        "failure is named FAILED_REGISTRATION",
        inspect_binding(fake, binding, approved_source_prefixes=PREFIXES)[0]
        == "FAILED_REGISTRATION",
    )
    # forged approval-looking fields cannot make a pending version pass
    record["status"] = "PENDING_REGISTRATION"
    record["tags"] = list(record["tags"]) + tag_list({"factory.approved": "true"})
    record["current_stage"] = "Production"
    record["aliases"] = ["champion"]
    check(
        "R2-2",
        "forged tag/stage/alias cannot make a pending version READY",
        raises(
            RegistryPendingError, verify_binding, fake, binding, approved_source_prefixes=PREFIXES
        ),
    )
    # drift is still reported as drift even when the status is also not READY
    record["tags"] = [t for t in record["tags"] if t["key"] != TAG_ARTIFACT] + tag_list(
        {TAG_ARTIFACT: "e" * 64}
    )
    check(
        "R2-2",
        "digest drift wins over a not-READY status",
        inspect_binding(fake, binding, approved_source_prefixes=PREFIXES)[0] == "DRIFT",
    )

    body = project_registry_binding(
        binding, drift="registry version is not READY", registration_state="PENDING_REGISTRATION"
    )
    check(
        "R2-2",
        "projection carries the registration state and the reason",
        body["registration_state"] == "PENDING_REGISTRATION"
        and body["drift"] == "registry version is not READY"
        and tuple(body) == PROJECTION_FIELDS,
    )
    check(
        "R2-2",
        "projection refuses a not-READY state with drift None",
        raises(
            ValueError,
            project_registry_binding,
            binding,
            drift=None,
            registration_state="PENDING_REGISTRATION",
        ),
    )
    check(
        "R2-2",
        "READY projection has drift None and state READY",
        project_registry_binding(binding, drift=None)["registration_state"] == "READY"
        and project_registry_binding(binding, drift=None)["drift"] is None,
    )

    # bind while the version is still pending: the binding handle is returned,
    # the state is reported un-clean by inspect, a replay creates no second version
    pend = Fake()
    pend.after_create = lambda rec: rec.__setitem__("status", "PENDING_REGISTRATION")
    pbinding = make_binding(pend)
    check(
        "R2-2",
        "bind while PENDING returns the binding and reports pending",
        inspect_binding(pend, pbinding, approved_source_prefixes=PREFIXES)[0]
        == "PENDING_REGISTRATION",
    )
    make_binding(pend)
    check("R2-2", "replay while pending creates no second version", len(pend.versions) == 1)
    bad = Fake()
    bad.after_create = lambda rec: rec.__setitem__("status", "FAILED_REGISTRATION")
    bbinding = make_binding(bad)
    check(
        "R2-2",
        "bind while FAILED reports failure",
        inspect_binding(bad, bbinding, approved_source_prefixes=PREFIXES)[0]
        == "FAILED_REGISTRATION",
    )
    # bind still refuses real drift at creation time
    drifty = Fake()
    drifty.after_create = lambda rec: rec.__setitem__("run_id", "f" * 32)
    check(
        "R2-2",
        "bind still raises drift",
        raises(RegistryDriftError, make_binding, drifty),
    )


# -- R2-5 token never leaks ------------------------------------------------


def test_token_never_leaks() -> None:
    def leaks(fn: Any) -> bool | None:
        try:
            fn()
        except Exception as exc:  # noqa: BLE001 - the point is the message of any failure
            text = " ".join([str(exc), repr(exc), str(exc.__cause__), str(exc.__context__)])
            return TOKEN in text
        return None

    # a server that echoes the token in its error code, message or body
    scenarios: dict[str, tuple[int, bytes]] = {
        "400 error_code is the token": (400, f'{{"error_code": "{TOKEN}"}}'.encode()),
        "400 message is the token": (
            400,
            f'{{"error_code": "INVALID", "message": "{TOKEN}"}}'.encode(),
        ),
        "500 body is the token": (500, TOKEN.encode()),
        "200 non-JSON body": (200, f"Bearer {TOKEN}".encode()),
        "200 duplicate key is the token": (200, f'{{"{TOKEN}": 1, "{TOKEN}": 2}}'.encode()),
        "200 NaN constant": (200, b'{"x": NaN}'),
    }
    for label, (status, body) in scenarios.items():
        stub = Stub()
        stub.reply(status, body)
        client = stub.client(token=TOKEN)
        result = leaks(lambda c=client: c.get_or_create_experiment("sovra/x"))
        check("R2-5", f"{label}: the call fails", result is not None)
        check("R2-5", f"{label}: the token is not in the error", result is False)
    # a code-shaped error code is still surfaced (the scrub is not a blanket drop)
    stub = Stub()
    stub.reply(400, b'{"error_code": "INVALID_PARAMETER_VALUE"}')
    try:
        stub.client(token=TOKEN).get_or_create_experiment("sovra/x")
    except TrackingRejectedError as exc:
        check("R2-5", "a code-shaped error code is kept", "INVALID_PARAMETER_VALUE" in str(exc))
    # a lower-case / long / non-code error code is dropped
    for odd in ("invalid", "A" * 65, "BAD CODE", "1ABC"):
        stub = Stub()
        stub.reply(400, f'{{"error_code": "{odd}"}}'.encode())
        try:
            stub.client(token=TOKEN).get_or_create_experiment("sovra/x")
        except TrackingRejectedError as exc:
            check("R2-5", f"non-code error_code {odd!r} is not echoed", odd not in str(exc))
    # network failure and policy denial
    down = Stub()
    down.server.shutdown()
    down.server.server_close()
    check(
        "R2-5",
        "unreachable server: unavailable and no token",
        leaks(lambda: down.client(token=TOKEN, timeout=2).get_or_create_experiment("sovra/x"))
        is False,
    )
    denied = MlflowClient(
        f"http://127.0.0.1:{down.port}",
        ConnectionPolicy(name="deny", allowed_ports=frozenset({1})),
        token=TOKEN,
    )
    check(
        "R2-5",
        "policy denial: rejected and no token",
        leaks(lambda: denied.get_or_create_experiment("sovra/x")) is False,
    )
    # end to end: the token is not in the outbox entry's last_error either
    stub = Stub()
    stub.reply(200, f'{{"{TOKEN}": 1, "{TOKEN}": 2}}'.encode())
    entry = InMemoryOutbox().add(make_run())
    sync_run(stub.client(token=TOKEN), entry)
    check(
        "R2-5",
        "outbox last_error never holds the token",
        entry.state == SyncState.FAILED
        and entry.last_error is not None
        and TOKEN not in entry.last_error,
    )
    # the token is still really sent
    stub = Stub()
    stub.reply(200, {"experiment": {"experiment_id": "7", "lifecycle_stage": "active"}})
    stub.client(token=TOKEN).get_or_create_experiment("sovra/x")
    check("R2-5", "the bearer token is still sent", stub.seen[-1]["auth"] == f"Bearer {TOKEN}")


# -- R2-6 model-version paging ---------------------------------------------


def version(n: int, match: bool) -> dict[str, Any]:
    return {
        "name": "m",
        "version": str(n),
        "tags": [{"key": "factory.model_version_id", "value": "x" if match else "y"}],
    }


def test_version_paging() -> None:
    stub = Stub()
    stub.reply(
        200, {"model_versions": [version(1, True), version(2, False)], "next_page_token": "p2"}
    )
    stub.reply(200, {"model_versions": [version(3, True)]})
    got = stub.client().search_model_versions_by_tag("m", "factory.model_version_id", "x")
    check(
        "R2-6",
        "matches from every page are returned",
        [v["version"] for v in got] == ["1", "3"],
    )
    check(
        "R2-6",
        "first request asks for the page size and no token",
        "max_results=200" in stub.seen[0]["path"] and "page_token" not in stub.seen[0]["path"],
    )
    check("R2-6", "second request carries the cursor", "page_token=p2" in stub.seen[1]["path"])
    check("R2-6", "exactly two requests", len(stub.seen) == 2)

    same = Stub()
    same.reply(200, {"model_versions": [], "next_page_token": "p2"})
    same.reply(200, {"model_versions": [], "next_page_token": "p2"})
    check(
        "R2-6",
        "a cursor that does not advance is refused",
        raises(TrackingRejectedError, same.client().search_model_versions_by_tag, "m", "k", "x"),
    )
    endless = Stub()
    for n in range(MAX_PAGES + 3):
        endless.reply(200, {"model_versions": [], "next_page_token": f"t{n}"})
    check(
        "R2-6",
        "an endless listing is cut off at the page bound",
        raises(TrackingRejectedError, endless.client().search_model_versions_by_tag, "m", "k", "x")
        and len(endless.seen) == MAX_PAGES,
    )
    for label, token in (("int", 5), ("list", ["a"]), ("true", True)):
        odd = Stub()
        odd.reply(200, {"model_versions": [], "next_page_token": token})
        check(
            "R2-6",
            f"a {label} cursor is refused",
            raises(TrackingRejectedError, odd.client().search_model_versions_by_tag, "m", "k", "x"),
        )
    empty = Stub()
    empty.reply(200, {"model_versions": [version(1, True)], "next_page_token": ""})
    check(
        "R2-6",
        "an empty cursor ends the listing",
        len(empty.client().search_model_versions_by_tag("m", "factory.model_version_id", "x")) == 1
        and len(empty.seen) == 1,
    )
    bad = Stub()
    bad.reply(200, {"model_versions": [1]})
    check(
        "R2-6",
        "a malformed page is refused",
        raises(TrackingRejectedError, bad.client().search_model_versions_by_tag, "m", "k", "x"),
    )


# -- R2-4 client from the tenant's binding ---------------------------------


def mlflow_binding(**over: Any) -> ComponentBinding:
    fields: dict[str, Any] = {
        "organization_id": ORG_A,
        "component_key": "mlflow",
        "security_zone_scope": DEFAULT_SECURITY_ZONE_SCOPE,
        "project_scope": None,
        "deployment_mode": "internal_service",
        "approved_endpoint": "https://mlflow.sovra.internal:5000",
        "secret_reference": None,
        "network_policy_identity": "mlflow-net-policy",
        "health_result": "unknown",
        "compatibility_lock_reference": "mlflow",
    }
    fields.update(over)
    return ComponentBinding(**fields)


def test_client_from_binding() -> None:
    policy = ConnectionPolicy(
        name="p", explicit_private_allowlist=(ipaddress.ip_network("10.0.0.0/8"),)
    )
    client = client_from_binding(mlflow_binding(), policy=policy)
    check(
        "R2-4",
        "client targets the binding's approved endpoint",
        isinstance(client, MlflowClient) and client._base == "https://mlflow.sovra.internal:5000",
    )
    check("R2-4", "no secret reference means no token", client._token is None)
    check(
        "R2-4",
        "another component's binding is refused",
        raises(
            TrackingRejectedError,
            client_from_binding,
            mlflow_binding(component_key="vllm"),
            policy=policy,
        ),
    )
    check(
        "R2-4",
        "a non-service MLflow binding is refused",
        raises(
            TrackingRejectedError,
            client_from_binding,
            mlflow_binding(deployment_mode="worker_package", approved_endpoint=None),
            policy=policy,
        ),
    )
    check(
        "R2-4",
        "an endpoint with a path is refused, not repaired",
        raises(
            TrackingRejectedError,
            client_from_binding,
            mlflow_binding(approved_endpoint="https://mlflow.sovra.internal:5000/x"),
            policy=policy,
        ),
    )
    ref = "vault://sovra/mlflow-token"
    secret = mlflow_binding(secret_reference=ref)
    check(
        "R2-4",
        "a secret reference without a resolver is refused",
        raises(TrackingRejectedError, client_from_binding, secret, policy=policy),
    )
    seen: list[str] = []

    def resolve(reference: str) -> str:
        seen.append(reference)
        return TOKEN

    with_token = client_from_binding(secret, policy=policy, resolve_secret=resolve)
    check("R2-4", "the resolver gets the binding's own reference", seen == [ref])
    check("R2-4", "the resolved token reaches the client", with_token._token == TOKEN)
    check(
        "R2-4",
        "the token is not in the client's repr",
        TOKEN not in repr(with_token) and ref not in repr(with_token),
    )


# -- R2-7 exact matching, exact types, sweep bookkeeping ---------------------


def test_exact_key_and_value_matching() -> None:
    """Keys and values are compared exactly: no case or whitespace folding."""
    run = make_run(
        metrics=(("Eval/Acc", 1.0), ("eval/acc", 2.0)),
        params=(("Mixed.Key", "V"), ("mixed.key", "v")),
        hashes=(("Art.Sha", "c" * 64),),
    )
    fake = Fake()
    entry = InMemoryOutbox().add(run)
    check("R2-7", "mixed-case keys sync", sync_run(fake, entry).state == SyncState.SYNCED)
    check(
        "R2-7",
        "mixed-case keys and values are a clean replay (no drift)",
        compute_run_drift(fake.runs[0], run) == (),
    )

    def with_data(**kinds: list[dict[str, Any]]) -> tuple[str, ...]:
        base_record = {k: list(v) for k, v in fake.runs[0]["data"].items()}
        base_record.update(kinds)
        return compute_run_drift({"info": fake.runs[0].get("info", {}), "data": base_record}, run)

    good_params = [{"key": "Mixed.Key", "value": "V"}, {"key": "mixed.key", "value": "v"}]
    for label, params, want in (
        (
            "param value case",
            [{"key": "Mixed.Key", "value": "v"}, good_params[1]],
            "param_changed:Mixed.Key",
        ),
        (
            "param value padding",
            [{"key": "Mixed.Key", "value": "V "}, good_params[1]],
            "param_changed:Mixed.Key",
        ),
        (
            "param key case",
            [{"key": "MIXED.KEY", "value": "V"}, good_params[1]],
            "param_extra:MIXED.KEY",
        ),
        (
            "param key padding",
            [{"key": " Mixed.Key", "value": "V"}, good_params[1]],
            "param_extra: Mixed.Key",
        ),
    ):
        got = with_data(params=params)
        check("R2-7", f"{label} is drift {want}", want in got)
        if "key" in label:
            check(
                "R2-7",
                f"{label} also reports the missing exact key",
                "param_missing:Mixed.Key" in got,
            )
    good_metrics = [{"key": "Eval/Acc", "value": 1.0}, {"key": "eval/acc", "value": 2.0}]
    got = with_data(metrics=[{"key": "EVAL/ACC", "value": 1.0}, good_metrics[1]])
    check(
        "R2-7",
        "metric key case is drift",
        "metric_extra:EVAL/ACC" in got and "metric_missing:Eval/Acc" in got,
    )
    got = with_data(metrics=[{"key": "Eval/Acc ", "value": 1.0}, good_metrics[1]])
    check("R2-7", "metric key padding is drift", "metric_extra:Eval/Acc " in got)
    base_tags = list(fake.runs[0]["data"]["tags"])
    upper = [dict(t) for t in base_tags]
    for t in upper:
        if t["key"] == "factory.hash.Art.Sha":
            t["value"] = "C" * 64
    check(
        "R2-7",
        "hash value case is drift",
        "hash_changed:factory.hash.Art.Sha" in with_data(tags=upper),
    )
    padded = [dict(t) for t in base_tags]
    for t in padded:
        if t["key"] == "factory.hash.Art.Sha":
            t["value"] = "c" * 64 + " "
    check(
        "R2-7",
        "hash value padding is drift",
        "hash_changed:factory.hash.Art.Sha" in with_data(tags=padded),
    )
    check("R2-7", "unchanged data stays clean", with_data() == ())
    check(
        "R2-7",
        "run_params reads exact keys",
        run_params(fake.runs[0]) == {"Mixed.Key": "V", "mixed.key": "v"},
    )


def test_malformed_run_data_items_are_skipped() -> None:
    record = {
        "data": {
            "metrics": ["key", ["key"], 5, None, {"value": 1.0}, {"key": "m", "value": 2}],
            "params": ["key", ["key"], 5, {"value": "x"}, {"key": "p", "value": "v"}],
        }
    }
    check(
        "R2-7", "non-dict metric items are skipped, not indexed", run_metrics(record) == {"m": 2.0}
    )
    check("R2-7", "non-dict param items are skipped, not indexed", run_params(record) == {"p": "v"})


def test_cursor_comparison_is_exact() -> None:
    stub = Stub()
    stub.reply(200, {"model_versions": [version(1, True)], "next_page_token": "abc"})
    stub.reply(200, {"model_versions": [version(2, True)], "next_page_token": "ABC"})
    stub.reply(200, {"model_versions": [version(3, True)]})
    got = stub.client().search_model_versions_by_tag("m", "factory.model_version_id", "x")
    check(
        "R2-7",
        "cursors that differ only by case are different cursors",
        [v["version"] for v in got] == ["1", "2", "3"] and len(stub.seen) == 3,
    )
    padded = Stub()
    padded.reply(200, {"model_versions": [], "next_page_token": "abc"})
    padded.reply(200, {"model_versions": [], "next_page_token": "abc "})
    padded.reply(200, {"model_versions": []})
    padded.client().search_model_versions_by_tag("m", "k", "x")
    check("R2-7", "a padded cursor is a different cursor", len(padded.seen) == 3)
    # Same class, sibling method: run search pages by the same cursor rule.
    runs = Stub()
    runs.reply(200, {"runs": [{"info": {"run_id": "1"}}], "next_page_token": "abc"})
    runs.reply(200, {"runs": [{"info": {"run_id": "2"}}], "next_page_token": "ABC"})
    runs.reply(200, {"runs": [{"info": {"run_id": "3"}}], "next_page_token": "ABC "})
    runs.reply(200, {"runs": [{"info": {"run_id": "4"}}]})
    found = runs.client().search_runs_by_tag("1", "k", "v")
    check(
        "R2-7",
        "run-search cursors that differ by case or padding are different cursors",
        [r["info"]["run_id"] for r in found] == ["1", "2", "3", "4"] and len(runs.seen) == 4,
    )
    stuck = Stub()
    stuck.reply(200, {"runs": [], "next_page_token": "abc"})
    stuck.reply(200, {"runs": [], "next_page_token": "abc"})
    check(
        "R2-7",
        "a run-search cursor that does not advance is refused",
        raises(TrackingRejectedError, stuck.client().search_runs_by_tag, "1", "k", "v"),
    )


def test_retry_policy_bounds() -> None:
    equal = RetryPolicy(max_attempts=3, base_delay_ms=1000, max_delay_ms=1000)
    check(
        "R2-7",
        "max delay equal to base delay is allowed",
        equal.delay_ms(1) == equal.delay_ms(9) == 1000,
    )
    check(
        "R2-7",
        "max delay below base delay is refused",
        raises(ValueError, RetryPolicy, 3, 1000, 999),
    )


def test_inmemory_store_records_what_it_is_given() -> None:
    store = InMemoryOutbox()
    entry = store.add(make_run())
    store.record(replace(entry, state=SyncState.FAILED, last_error="x"))
    check(
        "R2-7",
        "record() replaces the stored entry",
        store.entries[entry.entry_id].state == SyncState.FAILED
        and store.entries[entry.entry_id].last_error == "x",
    )


def test_durable_outbox_construction_and_clock() -> None:
    now = _utcnow()
    check(
        "R2-7",
        "the default clock is a timezone-aware current UTC time",
        isinstance(now, datetime)
        and now.tzinfo is not None
        and abs(datetime.now(UTC) - now) < timedelta(seconds=5),
    )
    ctx = TenantContext(organization_id=ORG_A)
    actor = ActorInfo(actor_type="user", actor_id="u", request_id="r")

    def build(binding_id: object) -> DurableOutbox:
        return DurableOutbox(
            lambda: None,  # type: ignore[arg-type, return-value]
            ctx,
            binding_id=binding_id,  # type: ignore[arg-type]
            results=None,  # type: ignore[arg-type]
            actor=actor,
        )

    for bad in (str(uuid.uuid4()), None, 5):
        check("R2-7", f"binding_id {bad!r} must be a uuid.UUID", raises(ValueError, build, bad))
    good = build(uuid.uuid4())
    check(
        "R2-7",
        "a uuid binding id builds with the default policy",
        good.policy is DEFAULT_RETRY_POLICY,
    )


def test_binding_must_be_the_internal_mlflow_service() -> None:
    policy = ConnectionPolicy(
        name="p", explicit_private_allowlist=(ipaddress.ip_network("10.0.0.0/8"),)
    )
    cases = (
        ("component key case", {"component_key": "MLflow"}),
        ("component key padding", {"component_key": "mlflow "}),
        ("deployment mode case", {"deployment_mode": "Internal_Service"}),
        ("deployment mode padding", {"deployment_mode": "internal_service "}),
        ("deployment mode with an endpoint", {"deployment_mode": "worker_package"}),
        ("no approved endpoint", {"approved_endpoint": None}),
        ("empty approved endpoint", {"approved_endpoint": ""}),
    )
    for label, over in cases:
        # Set after construction: the ORM validators would reject some of
        # these values up front; the adapter must not depend on that.
        binding = mlflow_binding()
        for field_name, value in over.items():
            setattr(binding, field_name, value)
        try:
            client_from_binding(binding, policy=policy)
            check("R2-7", f"{label} is refused", False)
        except TrackingRejectedError:
            pass
    built = client_from_binding(mlflow_binding(), policy=policy, timeout=3.5)
    check("R2-7", "the caller's timeout reaches the client", built._timeout == 3.5)


# -- R2-8 DurableOutbox behaviour against a recording fake of its DB seams ---


class _Row:
    def __init__(self, **over: Any) -> None:
        self.attempt_count = 0
        self.updated_at = datetime(2026, 1, 2, 3, 4, 5, 678000, tzinfo=UTC)
        self.external_id = None
        self.last_error = None
        self.idempotency_key = "mlflow:unset"
        self.__dict__.update(over)


class _Scalars:
    def __init__(self, items: list[Any]) -> None:
        self._items = items

    def all(self) -> list[Any]:
        return list(self._items)

    def one(self) -> Any:
        assert len(self._items) == 1
        return self._items[0]


class _Result:
    def __init__(self, items: list[Any]) -> None:
        self._items = items

    def scalars(self) -> _Scalars:
        return _Scalars(self._items)


class _Seams:
    """Replaces the three DB seams `durable_outbox` imports, recording every
    call, so the outbox's own logic is exercised without a database."""

    def __init__(self) -> None:
        self.sessions: list[tuple[Any, Any]] = []
        self.statements: list[tuple[Any, Any]] = []
        self.outcomes: list[dict[str, Any]] = []
        self.pending_ids: list[uuid.UUID] = []
        self.rows: dict[uuid.UUID, _Row] = {}
        self.conflict = False

    def session_for(self, factory: Any, context: Any) -> Any:
        import contextlib

        self.sessions.append((factory, context))

        @contextlib.contextmanager
        def cm() -> Any:
            yield "SESSION"

        return cm()

    def execute(self, session: Any, organization_id: Any, statement: Any) -> _Result:
        assert session == "SESSION"
        self.statements.append((organization_id, statement))
        params = list(statement.compile().params.values())
        if "pending" in params:
            return _Result(list(self.pending_ids))
        job = [v for v in params if isinstance(v, uuid.UUID) and v in self.rows]
        return _Result([self.rows[job[0]]])

    def record(self, session: Any, context: Any, **kwargs: Any) -> _Row:
        assert session == "SESSION"
        kwargs["_context"] = context
        self.outcomes.append(kwargs)
        if self.conflict:
            from app.component_bindings.component_binding_crud import SyncOutboxConflictError

            raise SyncOutboxConflictError("different key")
        return self.rows.setdefault(kwargs["factory_job_id"], _Row())


def _with_seams(fn: Any) -> Any:
    from packages.integrations.mlflow import durable_outbox as mod

    seams = _Seams()
    saved = (mod.tenant_scoped_session_for, mod.execute_tenant_scoped, mod.record_sync_outcome)
    mod.tenant_scoped_session_for = seams.session_for
    mod.execute_tenant_scoped = seams.execute
    mod.record_sync_outcome = seams.record
    try:
        return fn(seams)
    finally:
        mod.tenant_scoped_session_for, mod.execute_tenant_scoped, mod.record_sync_outcome = saved


class _Results:
    def __init__(self, runs: dict[uuid.UUID, Any]) -> None:
        self.runs = runs
        self.calls: list[tuple[uuid.UUID, uuid.UUID]] = []

    def load(self, organization_id: uuid.UUID, job_id: uuid.UUID) -> Any:
        self.calls.append((organization_id, job_id))
        return self.runs.get(job_id)


_BINDING = uuid.UUID("00000000-0000-4000-8000-0000000000b1")
_ACTOR = ActorInfo(actor_type="user", actor_id="u", request_id="r")
_NOW = datetime(2030, 1, 1, tzinfo=UTC)


def _outbox(runs: dict[uuid.UUID, Any], policy: RetryPolicy | None = None) -> DurableOutbox:
    kwargs: dict[str, Any] = {}
    if policy is not None:
        kwargs["policy"] = policy
    return DurableOutbox(
        lambda: None,  # type: ignore[arg-type, return-value]
        TenantContext(organization_id=ORG_A),
        binding_id=_BINDING,
        results=_Results(runs),
        actor=_ACTOR,
        clock=lambda: _NOW,
        **kwargs,
    )


def test_durable_outbox_add() -> None:
    from packages.integrations.mlflow.durable_outbox import idempotency_key_for
    from packages.integrations.mlflow.reconcile import RunConflictError, logical_run_key

    run = make_run()

    def ok(seams: _Seams) -> None:
        seams.rows[JOB] = _Row(attempt_count=3, external_id="r-1", last_error="boom")
        entry = _outbox({}).add(run)
        check("R2-8", "add admits through record_sync_outcome once", len(seams.outcomes) == 1)
        sent = seams.outcomes[0]
        check(
            "R2-8",
            "add sends binding, job, key and actor, and no outcome",
            sent["component_binding_id"] == _BINDING
            and sent["factory_job_id"] == JOB
            and sent["idempotency_key"] == idempotency_key_for(run)
            and sent["actor"] == _ACTOR
            and "outcome" not in sent
            and sent["_context"].organization_id == ORG_A,
        )
        check(
            "R2-8",
            "add maps the row onto the entry and never tracks a due time",
            entry.entry_id == logical_run_key(ORG_A, JOB)
            and entry.run is run
            and entry.attempts == 3
            and entry.mlflow_run_id == "r-1"
            and entry.last_error == "boom"
            and entry.next_retry_at_ms is None
            and entry.state == SyncState.PENDING,
        )

    _with_seams(ok)

    def foreign(seams: _Seams) -> None:
        other = make_run(organization_id=base.ORG_B)
        try:
            _outbox({}).add(other)
            check("R2-8", "a result of another organization is refused", False)
        except RunConflictError as exc:
            check(
                "R2-8", "the refusal names the organization", "different organization" in str(exc)
            )
        check(
            "R2-8", "a refused add touches no database", not seams.outcomes and not seams.sessions
        )

    _with_seams(foreign)

    def conflict(seams: _Seams) -> None:
        seams.conflict = True
        try:
            _outbox({}).add(run)
            check("R2-8", "a different queued result is refused", False)
        except RunConflictError as exc:
            check(
                "R2-8",
                "the conflict is the stable RunConflictError with its cause",
                "different result is already queued" in str(exc)
                and type(exc.__cause__).__name__ == "SyncOutboxConflictError",
            )

    _with_seams(conflict)


def test_durable_outbox_pending_sweep() -> None:
    from packages.integrations.mlflow.durable_outbox import _ms, idempotency_key_for

    check(
        "R2-8",
        "_ms is whole milliseconds",
        _ms(datetime(1970, 1, 1, 0, 0, 1, 500000, tzinfo=UTC)) == 1500,
    )
    j_ok, j_zero, j_missing, j_org, j_job, j_changed = (uuid.uuid4() for _ in range(6))
    good = make_run(job_id=j_ok)
    zero = make_run(job_id=j_zero)
    runs = {
        j_ok: good,
        j_zero: zero,
        j_org: make_run(job_id=j_org, organization_id=base.ORG_B),
        j_job: make_run(job_id=uuid.uuid4()),
        j_changed: make_run(job_id=j_changed, completed_at_ms=999),
    }
    policy = RetryPolicy(max_attempts=5, base_delay_ms=1000, max_delay_ms=300000)

    def sweep(seams: _Seams) -> None:
        seams.pending_ids = [j_ok, j_zero, j_missing, j_org, j_job, j_changed]
        updated = datetime(2026, 5, 6, 7, 8, 9, 250000, tzinfo=UTC)
        seams.rows = {
            j_ok: _Row(
                attempt_count=3,
                updated_at=updated,
                external_id="r-9",
                last_error="old",
                idempotency_key=idempotency_key_for(good),
            ),
            j_zero: _Row(attempt_count=0, idempotency_key=idempotency_key_for(zero)),
            j_missing: _Row(idempotency_key="mlflow:k-missing"),
            j_org: _Row(idempotency_key="mlflow:k-org"),
            j_job: _Row(idempotency_key="mlflow:k-job"),
            j_changed: _Row(idempotency_key="mlflow:not-the-current-hash"),
        }
        outbox = _outbox(runs, policy)
        entries = outbox.pending()
        check(
            "R2-8",
            "only the intact rows come back, in listing order",
            [e.run.job_id for e in entries] == [j_ok, j_zero],
        )
        first, second = entries
        expected_ms = int(updated.timestamp() * 1000) + 4000
        check(
            "R2-8",
            "a retried row's due time is updated_at + the policy delay",
            first.next_retry_at_ms
            == expected_ms
            == policy.delay_ms(3) + int(updated.timestamp() * 1000)
            and first.attempts == 3
            and first.mlflow_run_id == "r-9"
            and first.last_error == "old"
            and first.entry_id.startswith("run-"),
        )
        check("R2-8", "a never-attempted row has no due time", second.next_retry_at_ms is None)
        loads = outbox._results.calls  # type: ignore[attr-defined]
        check(
            "R2-8",
            "evidence is loaded per pending job for this organization",
            loads[0] == (ORG_A, j_ok) and len(loads) == 6,
        )
        failed = {o["factory_job_id"]: o for o in seams.outcomes if o.get("outcome") == "failed"}
        check(
            "R2-8",
            "exactly the four unloadable rows are resolved failed, nothing else",
            set(failed) == {j_missing, j_org, j_job, j_changed} and len(seams.outcomes) == 4,
        )
        check(
            "R2-8",
            "each unloadable row fails with its own visible reason and key",
            failed[j_missing]["error"] == "result_missing: Factory evidence has no result"
            and failed[j_org]["error"] == "result_mismatch: evidence identity differs"
            and failed[j_job]["error"] == "result_mismatch: evidence identity differs"
            and failed[j_changed]["error"] == "result_changed: evidence differs from admission"
            and failed[j_missing]["idempotency_key"] == "mlflow:k-missing"
            and failed[j_changed]["idempotency_key"] == "mlflow:not-the-current-hash"
            and failed[j_missing]["component_binding_id"] == _BINDING
            and failed[j_missing]["actor"] == _ACTOR,
        )
        listing = [st for _, st in seams.statements if "pending" in st.compile().params.values()]
        check("R2-8", "the listing runs once", len(listing) == 1)
        sql = str(listing[0].compile())
        params = list(listing[0].compile().params.values())
        check(
            "R2-8",
            "the listing is scoped to org, binding and pending, oldest first",
            sorted(map(str, params)) == sorted([str(ORG_A), str(_BINDING), "pending"])
            and sql.count(" = ") == 3
            and "!=" not in sql
            and "<>" not in sql
            and "ORDER BY" in sql
            and "created_at" in sql,
        )
        check(
            "R2-8",
            "every statement is passed the caller's organization",
            all(o == ORG_A for o, _ in seams.statements),
        )
        row_stmts = [st for _, st in seams.statements if st is not listing[0]]
        for st in row_stmts:
            rsql = str(st.compile())
            rparams = list(st.compile().params.values())
            check(
                "R2-8",
                "a row lookup is scoped to org, binding and job by equality",
                ORG_A in rparams
                and _BINDING in rparams
                and rsql.count(" = ") == 3
                and "!=" not in rsql
                and "<>" not in rsql,
            )

    _with_seams(sweep)


def test_durable_outbox_record_states() -> None:
    from packages.integrations.mlflow.durable_outbox import idempotency_key_for

    run = make_run()
    key = idempotency_key_for(run)

    def go(seams: _Seams) -> None:
        outbox = _outbox({})

        def put(**over: Any) -> tuple[dict[str, Any], _Row]:
            seams.outcomes.clear()
            seams.rows.clear()
            entry = OutboxEntry(entry_id="e", run=run, attempts=2, **over)
            outbox.record(entry)
            return seams.outcomes[0], seams.rows[JOB]

        sent, row = put(state=SyncState.SYNCED, mlflow_run_id="r-7", last_error="x")
        check(
            "R2-8",
            "SYNCED resolves succeeded with the MLflow run id",
            len(seams.outcomes) == 1
            and sent["outcome"] == "succeeded"
            and sent["external_id"] == "r-7"
            and "error" not in sent
            and sent["idempotency_key"] == key
            and sent["component_binding_id"] == _BINDING
            and sent["factory_job_id"] == JOB
            and sent["actor"] == _ACTOR
            and row.attempt_count == 2,
        )
        sent, row = put(state=SyncState.PENDING, last_error="e" * 600)
        check(
            "R2-8",
            "PENDING only records the attempt, with a 500-character error and the clock",
            "outcome" not in sent
            and "error" not in sent
            and row.last_error == "e" * 500
            and row.updated_at == _NOW
            and row.attempt_count == 2,
        )
        sent, row = put(state=SyncState.PENDING, last_error="e" * 500)
        check("R2-8", "a 500-character error is kept whole", row.last_error == "e" * 500)
        for empty in (None, ""):
            sent, row = put(state=SyncState.PENDING, last_error=empty)
            check("R2-8", f"PENDING with error {empty!r} stores none", row.last_error is None)
        sent, row = put(state=SyncState.FAILED, last_error="retry limit reached")
        check(
            "R2-8",
            "FAILED resolves failed with the error exactly as given",
            sent["outcome"] == "failed"
            and sent["error"] == "retry limit reached"
            and "external_id" not in sent
            and row.attempt_count == 2,
        )
        sent, _ = put(state=SyncState.FAILED, last_error=None)
        check("R2-8", "FAILED without an error says sync failed", sent["error"] == "sync failed")
        sent, _ = put(state=SyncState.FAILED, last_error="")
        check("R2-8", "FAILED with an empty error says sync failed", sent["error"] == "sync failed")
        sent, _ = put(state=SyncState.AMBIGUOUS, last_error="no ack")
        check(
            "R2-8",
            "AMBIGUOUS is failed with an ambiguous: prefix",
            sent["outcome"] == "failed" and sent["error"] == "ambiguous: no ack",
        )
        sent, _ = put(state=SyncState.AMBIGUOUS, last_error=None)
        check("R2-8", "AMBIGUOUS without an error", sent["error"] == "ambiguous: sync failed")
        sent, _ = put(state="SOMETHING_ELSE", last_error="z")
        check(
            "R2-8",
            "an unknown state is treated as failed, never as synced",
            sent["outcome"] == "failed" and sent["error"] == "z",
        )

    _with_seams(go)


def main() -> None:
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
    if FAILURES:
        print(f"FTF-R005 round-2 tests: {len(FAILURES)} failed")
        for failure in FAILURES:
            print(f"  {failure}")
        sys.exit(1)
    print("FTF-R005 round-2 tests: all checks passed")
    sys.exit(0)


if __name__ == "__main__":
    main()
