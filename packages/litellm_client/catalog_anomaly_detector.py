"""LiteLLM catalog anomaly detector (FTF-0209, Section 3.3/65/77/81).

Scope decision, disclosed (path refinement per Section 75.1/84.3 -- reuse
the existing coherent module tree rather than the task packet's own
literal `packages/litellm-client/` suggestion, which does not match this
repository's real, already-CERTIFIED `packages/litellm_client/`
directory used by every sibling FTF-020x module): this module lives at
`packages/litellm_client/catalog_anomaly_detector.py`, alongside
`last_known_good_catalog.py` (FTF-0208, CERTIFIED), which it wraps.

Section 3.3's own sentence this task's acceptance clause is built on:
"Unexpected >50% reduction raises an alert and requires review of the
inventory, not restoration of old permissions." FTF-0208's
`LastKnownGoodCatalog._apply_successful` already sets a transient,
content-free `CatalogSnapshot.alert` string on exactly that condition,
but that string is overwritten to `None` on the very next successful
discovery cycle regardless of whether a human ever saw it -- there is
nothing there a caller can list, retry against, or explicitly close.
This task's own acceptance clause ("A >50% reduction triggers recorded
anomaly/retry/admin acknowledgement while immediate authorization
remains enforced") is that separate, persisted layer: `AnomalyRecord`
survives across cycles until an explicit `acknowledge(...)` call, tracks
a bounded `retry(...)` count (Section 3.3's own "three-attempt ceiling",
reused here for consistency rather than inventing a second number), and
never itself grants, restores, or otherwise touches call authorization.

"...while immediate authorization remains enforced" is why `record(...)`
always applies the real discovery result to the wrapped
`LastKnownGoodCatalog` *unconditionally, before* evaluating whether an
anomaly should be opened/updated -- nothing in this detector's own retry
or acknowledgement bookkeeping can gate, delay, or short-circuit that
prior call, so `LastKnownGoodCatalog.is_call_authorized` (FTF-0208's own
sole authorization gate) is always driven by the real, current
discovery outcome, never by this detector's own state.

This task's own negative_acceptance ("Repeated empty list cannot cause
silent destructive deletion or restored unauthorized execution") is two
separate guarantees, both structural rather than merely tested:
1. No destructive deletion: this module has no delete/purge/reset
   operation anywhere in its public surface. An open incident is
   updated in place (`retry_count`/`new_count` refreshed) across
   however many further reduced/empty successful responses arrive
   while it is unacknowledged; it is never discarded, replaced with a
   fresh record, or silently dropped from `history`.
2. No restored unauthorized execution: `acknowledge(...)` is a pure
   audit-trail transition on this detector's own `AnomalyRecord` --
   it never reaches into the wrapped `LastKnownGoodCatalog`'s entries
   at all, so no number of retries or an eventual acknowledgement can
   ever turn a real, already-removed id back into an authorized one.
   Only a genuinely fresh, successful discovery response that lists
   that id again can do that, through `LastKnownGoodCatalog`'s own
   ordinary `_apply_successful` path -- exactly the same path this
   detector's own `record(...)` always calls first, on its own,
   regardless of anomaly state.

No independent schema change (this task's own `database_changes`
clause): `CatalogAnomalyDetector` is an in-memory, no-I/O object, same
convention `LastKnownGoodCatalog` itself already established. No API
route is wired by this task (same disclosed "no live caller yet"
precedent already established by FTF-0202/FTF-0205/FTF-0207/FTF-0208);
`proving_symbol` stays blank and `product_truth_check.py`'s product-true
count is deliberately unchanged. Wiring this detector into a durable
per-connection service, a real admin-acknowledgement UI/route and the
periodic polling loop is `docs/TASK_INDEX.md`'s own later "Periodic sync
workflow" task (FTF-0215) -- not this task's own `files` list.

Every field on `AnomalyRecord` is content-free (Section observability:
"no secrets/raw prompts in task reports") -- only ids, counts and plain
identifiers, never a credential or raw response body.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime
from enum import Enum

from packages.litellm_client.last_known_good_catalog import (
    CatalogEntryStatus,
    CatalogSnapshot,
    LastKnownGoodCatalog,
)
from packages.litellm_client.v1_models_discovery import DiscoveryState, ModelDiscoveryResult

__all__ = [
    "AnomalyError",
    "AnomalyKind",
    "AnomalyRecord",
    "AnomalyStatus",
    "CatalogAnomalyDetector",
    "DEFAULT_RETRY_CEILING",
]

# Section 3.3's own three-attempt ceiling for transient/429/5xx discovery
# retries, reused here as the bound on how many bounded re-attempts an
# operator may record against one open anomaly before it is escalated and
# must wait on an explicit admin disposition.
DEFAULT_RETRY_CEILING = 3

# Section 3.3: "Unexpected >50% reduction raises an alert" -- a drop to
# strictly less than half the previously observed active (callable) count
# is "unexpected". Kept as this module's own constant (not imported from
# `last_known_good_catalog`, a private detail there) so this detector's
# own trigger condition is self-contained and independently testable.
_MAJOR_REDUCTION_THRESHOLD = 0.5


class AnomalyKind(str, Enum):
    MAJOR_REDUCTION = "major_reduction"


class AnomalyStatus(str, Enum):
    """An anomaly is always exactly one of these -- it only ever moves
    forward (never silently reverts to "no anomaly") until an explicit
    `acknowledge(...)` call."""

    OPEN = "open"
    RETRY_PENDING = "retry_pending"
    ESCALATED = "escalated"
    ACKNOWLEDGED = "acknowledged"


@dataclass(frozen=True)
class AnomalyRecord:
    id: int
    kind: AnomalyKind
    detected_at: datetime
    previous_count: int
    new_count: int
    retry_count: int
    retry_ceiling: int
    status: AnomalyStatus
    acknowledged_by: str | None
    acknowledged_at: datetime | None


class AnomalyError(Exception):
    """A caller error against this detector's own state machine (unknown
    anomaly id, retrying/re-acknowledging an already-acknowledged
    incident) -- never raised for a real gateway/discovery failure, which
    is the wrapped `LastKnownGoodCatalog`'s own separate concern."""


class CatalogAnomalyDetector:
    """Observes successive discovery outcomes applied to one
    `LastKnownGoodCatalog` and records a >50% active-model-count
    reduction as a persisted anomaly requiring bounded retry, then
    explicit admin acknowledgement -- a pure audit/ops layer. Not
    thread-safe by itself, same convention `LastKnownGoodCatalog` already
    assumes at this layer."""

    def __init__(self, *, retry_ceiling: int = DEFAULT_RETRY_CEILING) -> None:
        if retry_ceiling < 1:
            raise ValueError("retry_ceiling must be >= 1")
        self._retry_ceiling = retry_ceiling
        self._next_id = 1
        self._current: AnomalyRecord | None = None
        self._history: list[AnomalyRecord] = []

    def record(
        self,
        catalog: LastKnownGoodCatalog,
        result: ModelDiscoveryResult,
        *,
        now: datetime | None = None,
    ) -> tuple[CatalogSnapshot, AnomalyRecord | None]:
        """Apply one real discovery outcome to `catalog` and, only for a
        genuinely successful response, evaluate this detector's own
        reduction trigger. Returns the catalog's resulting snapshot and,
        if this call opened or updated an anomaly, that record -- `None`
        if this call did not trigger one (whether or not an unrelated
        earlier anomaly is still open).
        """
        checked_at = now if now is not None else datetime.now(UTC)
        before = catalog.snapshot()
        previous_active_count = sum(
            1 for entry in before.entries if entry.status == CatalogEntryStatus.CALLABLE
        )

        # Unconditional and first: nothing below this line can ever gate,
        # delay or short-circuit the real catalog update, which is this
        # task's own "immediate authorization remains enforced" clause.
        after = catalog.apply_discovery_result(result, now=checked_at)

        if result.state != DiscoveryState.DISCOVERED:
            return after, None

        new_active_count = sum(
            1 for entry in after.entries if entry.status == CatalogEntryStatus.CALLABLE
        )
        triggered = (
            previous_active_count > 0
            and new_active_count < previous_active_count * _MAJOR_REDUCTION_THRESHOLD
        )
        if not triggered:
            return after, None

        return after, self._open_or_update(previous_active_count, new_active_count, checked_at)

    def _open_or_update(
        self, previous_count: int, new_count: int, checked_at: datetime
    ) -> AnomalyRecord:
        if self._current is not None and self._current.status != AnomalyStatus.ACKNOWLEDGED:
            updated = replace(self._current, new_count=new_count)
            self._store(updated)
            return updated

        record = AnomalyRecord(
            id=self._next_id,
            kind=AnomalyKind.MAJOR_REDUCTION,
            detected_at=checked_at,
            previous_count=previous_count,
            new_count=new_count,
            retry_count=0,
            retry_ceiling=self._retry_ceiling,
            status=AnomalyStatus.OPEN,
            acknowledged_by=None,
            acknowledged_at=None,
        )
        self._next_id += 1
        self._current = record
        self._history.append(record)
        return record

    def retry(self, anomaly_id: int) -> AnomalyRecord:
        """Record one bounded re-attempt against an open anomaly (Section
        3.3's own three-attempt ceiling, reused here). This only updates
        this detector's own bookkeeping -- it never itself touches the
        wrapped catalog; a real fresh discovery call through `record(...)`
        is the only thing that can ever change a real access decision.
        """
        record = self._require(anomaly_id)
        if record.status == AnomalyStatus.ACKNOWLEDGED:
            raise AnomalyError(f"anomaly {anomaly_id} is already acknowledged")
        new_retry_count = record.retry_count + 1
        new_status = (
            AnomalyStatus.ESCALATED
            if new_retry_count >= record.retry_ceiling
            else AnomalyStatus.RETRY_PENDING
        )
        updated = replace(record, retry_count=new_retry_count, status=new_status)
        self._store(updated)
        return updated

    def acknowledge(
        self, anomaly_id: int, admin_identity: str, *, now: datetime | None = None
    ) -> AnomalyRecord:
        """Explicit human disposition, required before an anomaly is
        considered closed -- reachable from any open status. Deliberately
        touches only this detector's own `AnomalyRecord`: it never reads
        or writes the wrapped `LastKnownGoodCatalog`, so it can never
        restore any removed id's callable status (Section 3.3: "...
        requires review of the inventory, not restoration of old
        permissions"; this task's own negative_acceptance: "...or
        restored unauthorized execution")."""
        if not admin_identity:
            raise AnomalyError("admin_identity is required to acknowledge an anomaly")
        record = self._require(anomaly_id)
        if record.status == AnomalyStatus.ACKNOWLEDGED:
            raise AnomalyError(f"anomaly {anomaly_id} is already acknowledged")
        checked_at = now if now is not None else datetime.now(UTC)
        updated = replace(
            record,
            status=AnomalyStatus.ACKNOWLEDGED,
            acknowledged_by=admin_identity,
            acknowledged_at=checked_at,
        )
        self._store(updated)
        return updated

    def get(self, anomaly_id: int) -> AnomalyRecord | None:
        if self._current is not None and self._current.id == anomaly_id:
            return self._current
        for record in self._history:
            if record.id == anomaly_id:
                return record
        return None

    @property
    def current(self) -> AnomalyRecord | None:
        return self._current

    @property
    def history(self) -> tuple[AnomalyRecord, ...]:
        return tuple(self._history)

    def _require(self, anomaly_id: int) -> AnomalyRecord:
        record = self.get(anomaly_id)
        if record is None:
            raise AnomalyError(f"unknown anomaly id: {anomaly_id}")
        return record

    def _store(self, updated: AnomalyRecord) -> None:
        if self._current is not None and self._current.id == updated.id:
            self._current = updated
        for index, record in enumerate(self._history):
            if record.id == updated.id:
                self._history[index] = updated
                break
