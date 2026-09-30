"""LiteLLM periodic sync workflow (FTF-0215, Section 3.3/38/65/77/81).

`packages/litellm_client/last_known_good_catalog.py` (FTF-0208, CERTIFIED)
and `packages/litellm_client/catalog_anomaly_detector.py` (FTF-0209,
CERTIFIED) both already say explicitly, in their own module docstrings,
that "wiring this catalog into a durable per-connection service and an
actual periodic polling loop is ... FTF-0215 (the real wiring/route
task)." This module is that composition: a caller (a real cron/Temporal
trigger, wired separately -- see scope decision below) repeatedly drives
one `PeriodicSyncWorkflow` instance per LiteLLM connection through a
`begin_cycle()`/`complete_cycle()` pair around its own real
`discover_models` call.

This task's own acceptance clause ("Periodic workflow records sync
snapshots, bounded retries and alerts without duplicate schedules") is
two separate guarantees, deliberately kept as two separate guards rather
than one:

1. **"Without duplicate schedules"** is `register()`'s own one-shot
   idempotency latch: a caller's real periodic-schedule registration
   (whatever scheduler ultimately triggers this workflow's cycles) is
   refused as a no-op the second and every later time it is attempted for
   the same connection, so a retried or duplicated registration call
   never creates a second recurring job for one connection.
2. **"Bounded retries" and "alerts"** are `complete_cycle()`'s own
   consecutive-transient-failure counter (reusing Section 3.3's own
   three-attempt ceiling, the same constant `CatalogAnomalyDetector`
   already reuses for its own retry bound) and the already-CERTIFIED
   `CatalogAnomalyDetector`/`LastKnownGoodCatalog` alerting this module
   composes, never reimplements.

This task's own negative_acceptance ("Revoked key, concurrent sync and
out-of-order response cannot reinstate obsolete authorization") is
`complete_cycle()`'s own strict ordering gate: every dispatched cycle
carries a monotonically increasing `sequence` from `begin_cycle()`, and a
completed cycle is applied to the wrapped catalog/detector *only* if its
sequence is strictly newer than the last one actually applied --
evaluated and enforced *before* any catalog mutation. A slow real
discovery call whose response finally arrives after a later-dispatched
cycle has already completed and been applied (a genuinely concurrent
sync, or an out-of-order delivery of an old response) is dropped
entirely as `SKIPPED_OUT_OF_ORDER`: its result -- even a superficially
successful, fully populated `DiscoveryState.DISCOVERED` result -- never
reaches `LastKnownGoodCatalog.apply_discovery_result`, so it cannot
resurrect an id a fresher, already-applied cycle already removed, and it
cannot undo a fresher already-applied revocation. Revocation itself needs
no separate handling here: FTF-0208's own `_apply_revocation` already
downgrades every callable entry to stale and `is_call_authorized` already
refuses unconditionally once `access_state=revoked` -- this module's own
job is only to guarantee that a stale response can never reach that path
out of order and revert it.

Scope decision (disclosed, same precedent FTF-0202/FTF-0205/FTF-0207/
FTF-0208/FTF-0209 already established for "no live caller/route yet"):
this task's own packet `files` list names only this module and its test,
not a route file, an actual Temporal workflow registration, or a real
cron trigger -- there is no live scheduler in this repository yet for a
periodic job to be registered against. Wiring a real recurring trigger
(Temporal, per Section 77.3, or an interim scheduler) that calls
`register()` once per connection and then repeatedly drives
`begin_cycle()`/`complete_cycle()` on a timer is later, separately
claimed wiring work -- declaring an HTTP route or a live Temporal
schedule here with no real backing trigger would itself be exactly the
"placeholder" Law 11 forbids. `proving_symbol` stays blank and
`product_truth_check.py`'s product-true count is deliberately unchanged,
exactly as every one of this module's own listed dependencies already
disclosed for itself.

This module also closes the one open observation FTF-0209's own
certification record flagged for this task: `acknowledge_anomaly`/
`retry_anomaly` are real, thin passthroughs to the wrapped
`CatalogAnomalyDetector`, so a future real API route has one coherent
per-connection object to call acknowledge/retry against instead of
reaching into this workflow's private catalog/detector fields itself.

No independent schema change (this task's own `database_changes`
clause): `PeriodicSyncWorkflow` is an in-memory, no-I/O object, same
convention `LastKnownGoodCatalog`/`CatalogAnomalyDetector` themselves
already established -- it never calls the gateway itself (a caller
supplies each cycle's already-computed `ModelDiscoveryResult`, typically
the output of its own real `discover_models` call) and never persists
anything past process memory.

Every field on `SyncCycleResult` is content-free (Section observability:
"no secrets/raw prompts in task reports") -- only ids, counts, sequence
numbers and plain status/detail strings, never a credential or raw
response body.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum

from packages.litellm_client.catalog_anomaly_detector import (
    DEFAULT_RETRY_CEILING,
    AnomalyRecord,
    CatalogAnomalyDetector,
)
from packages.litellm_client.last_known_good_catalog import CatalogSnapshot, LastKnownGoodCatalog
from packages.litellm_client.v1_models_discovery import DiscoveryState, ModelDiscoveryResult

__all__ = [
    "DEFAULT_RETRY_CEILING",
    "PeriodicSyncWorkflow",
    "SyncCycleResult",
    "SyncCycleStatus",
]

# Section 3.3: "Retry only transient/429/5xx failures" -- a connectivity
# failure that says nothing about authorization one way or the other.
# Deliberately excludes DiscoveryState.INVALID_TOKEN (a definite signal,
# per FTF-0208's own `_apply_revocation`, never treated as transient) and
# every SECRET_* state (a configuration problem, not a transient gateway
# hiccup) -- same distinction Section 3.3 itself draws.
_TRANSIENT_STATES = frozenset(
    {
        DiscoveryState.TIMEOUT,
        DiscoveryState.DNS_FAILED,
        DiscoveryState.CONNECTION_REFUSED,
        DiscoveryState.UNREACHABLE,
    }
)


class SyncCycleStatus(str, Enum):
    """A completed cycle is always exactly one of these -- never merely
    "success"/"failure", so a caller/operator can tell a genuinely applied
    cycle apart from one this workflow deliberately refused to apply."""

    APPLIED = "applied"
    RETRY_SCHEDULED = "retry_scheduled"
    RETRY_CEILING_REACHED = "retry_ceiling_reached"
    SKIPPED_OUT_OF_ORDER = "skipped_out_of_order"


@dataclass(frozen=True)
class SyncCycleResult:
    status: SyncCycleStatus
    sequence: int
    snapshot: CatalogSnapshot | None
    anomaly: AnomalyRecord | None
    detail: str


class PeriodicSyncWorkflow:
    """Drives one LiteLLM connection's `LastKnownGoodCatalog` +
    `CatalogAnomalyDetector` pair through successive real
    `discover_models` cycles. One instance per connection; not
    thread-safe by itself, same convention every wrapped component
    already assumes at this layer."""

    def __init__(self, *, retry_ceiling: int = DEFAULT_RETRY_CEILING) -> None:
        self._catalog = LastKnownGoodCatalog()
        self._detector = CatalogAnomalyDetector(retry_ceiling=retry_ceiling)
        self._retry_ceiling = retry_ceiling
        self._registered = False
        self._next_sequence = 1
        self._last_applied_sequence = 0
        self._consecutive_transient_failures = 0
        self._history: list[SyncCycleResult] = []

    def register(self) -> bool:
        """Idempotent periodic-schedule registration for this connection
        (this task's own acceptance clause: "without duplicate
        schedules"). Returns `True` the first time this is called for
        this workflow instance, `False` for every later call -- a real
        scheduler's own retried/duplicated registration attempt is
        refused as a no-op rather than creating a second recurring job
        for the same connection."""
        if self._registered:
            return False
        self._registered = True
        return True

    @property
    def is_registered(self) -> bool:
        return self._registered

    def begin_cycle(self) -> int:
        """Issue the next monotonically increasing dispatch sequence for
        a real `discover_models` call this caller is about to make.
        Deliberately does not refuse a second dispatch while an earlier
        one is still outstanding -- a genuinely concurrent or
        timeout-triggered re-dispatch is a real condition this workflow
        must survive, not one it can prevent by construction;
        `complete_cycle`'s own ordering gate is what actually protects
        catalog state against it."""
        sequence = self._next_sequence
        self._next_sequence += 1
        return sequence

    def complete_cycle(
        self, sequence: int, result: ModelDiscoveryResult, *, now: datetime | None = None
    ) -> SyncCycleResult:
        """Apply one real `discover_models` outcome for the cycle
        `begin_cycle` issued as `sequence`. A `sequence` that is not
        strictly newer than the last one actually applied is a stale,
        out-of-order, or duplicate-concurrent delivery (this task's own
        negative_acceptance) and is dropped here, before the wrapped
        catalog/detector ever see it -- the one guarantee this module
        exists to add on top of `CatalogAnomalyDetector.record`'s own
        already-unconditional-once-reached application."""
        checked_at = now if now is not None else datetime.now(UTC)

        if sequence <= self._last_applied_sequence:
            record = SyncCycleResult(
                status=SyncCycleStatus.SKIPPED_OUT_OF_ORDER,
                sequence=sequence,
                snapshot=self._catalog.snapshot(),
                anomaly=None,
                detail=(
                    f"sequence {sequence} is not newer than the last applied sequence "
                    f"{self._last_applied_sequence} -- dropped as an out-of-order or "
                    "concurrent-stale response, never applied to the catalog"
                ),
            )
            self._history.append(record)
            return record

        self._last_applied_sequence = sequence
        snapshot, anomaly = self._detector.record(self._catalog, result, now=checked_at)

        if result.state in _TRANSIENT_STATES:
            self._consecutive_transient_failures += 1
            if self._consecutive_transient_failures >= self._retry_ceiling:
                status = SyncCycleStatus.RETRY_CEILING_REACHED
                detail = (
                    f"{self._consecutive_transient_failures} consecutive transient discovery "
                    f"failures ({result.state.value}) reached the retry ceiling -- requires "
                    "operator attention, not a further silent retry"
                )
            else:
                status = SyncCycleStatus.RETRY_SCHEDULED
                detail = (
                    f"transient discovery failure ({result.state.value}), "
                    f"{self._consecutive_transient_failures}/{self._retry_ceiling} -- "
                    "a bounded retry is expected"
                )
        else:
            self._consecutive_transient_failures = 0
            status = SyncCycleStatus.APPLIED
            detail = f"sync cycle applied ({result.state.value})"

        record = SyncCycleResult(
            status=status, sequence=sequence, snapshot=snapshot, anomaly=anomaly, detail=detail
        )
        self._history.append(record)
        return record

    def acknowledge_anomaly(
        self, anomaly_id: int, admin_identity: str, *, now: datetime | None = None
    ) -> AnomalyRecord:
        """Real, thin passthrough to the wrapped `CatalogAnomalyDetector`
        -- exists so a future real API route has one coherent
        per-connection object to call against, closing the gap FTF-0209's
        own certification record flagged for this task."""
        return self._detector.acknowledge(anomaly_id, admin_identity, now=now)

    def retry_anomaly(self, anomaly_id: int) -> AnomalyRecord:
        """Real, thin passthrough to the wrapped `CatalogAnomalyDetector`
        -- same reasoning as `acknowledge_anomaly`."""
        return self._detector.retry(anomaly_id)

    def is_call_authorized(
        self,
        model_id: str,
        *,
        now: datetime | None = None,
    ) -> bool:
        """Real, thin passthrough to the wrapped
        `LastKnownGoodCatalog.is_call_authorized` -- same reasoning as
        `acknowledge_anomaly`/`retry_anomaly`: one coherent per-connection
        object for a future real caller to check authorization against,
        rather than reaching into this workflow's own private catalog."""
        return self._catalog.is_call_authorized(model_id, now=now).authorized

    @property
    def snapshot(self) -> CatalogSnapshot:
        return self._catalog.snapshot()

    @property
    def history(self) -> tuple[SyncCycleResult, ...]:
        return tuple(self._history)

    @property
    def last_applied_sequence(self) -> int:
        return self._last_applied_sequence
