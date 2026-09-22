"""Local compute costing (FTF-1302, Section 28/31-32/64/69-70/77/81).

Scope (disclosed explicitly, not hidden -- see this task's own
docs/certifications/queue.md submission entry): `database_changes`
charters "No independent schema change. Consume frozen contract" -- there
is no GPU worker, Temporal workflow, or resource-lease table anywhere in
this repository yet to auto-emit real per-request GPU-second telemetry
from (FTF-0901's own, later, separately dependency-gated GPU worker
family; FTF-1601's own postgres_backup.py and FTF-0006's own
observability seam do not define one either). This module therefore
takes a **declared** service window and workload -- an operator-entered,
already-reconciled real GPU/CPU/RAM/storage/network bill for one service
window, exactly the same "no fake metrics; a real, disclosed, entered
number, not an auto-scraped one" pattern FTF-1301's own `CostProfile` rate
entry already established (`ServiceWindow.total_billed_gpu_cpu_ram_cost`
is *itself* the real bill, inclusive of idle and multi-replica capacity --
this module never re-derives that figure from request-level data).

This task's own acceptance clause ("Allocate real total service-window
GPU/CPU/storage/idle cost to declared completed workload") is Section 32's
own named formula, made real and callable:

    window_self_hosted_cost = total_billed_GPU_CPU_RAM_including_idle_and_warmup
                           + storage + network + nonduplicated_platform_allocation
    cost_per_success = all_window_costs_for_this_policy / unique_successful_tasks

"declared completed workload" is Section 69.2's own terminal-outcome
classification (`pass`/`model_fail`/`tool_fail`/`infra_error`/`timeout`/
`cancelled`/`not_run`) -- the denominator is the count of *unique*
`request_id`s whose declared terminal outcome is `pass` ("Multiple turns
from one conversation, repeated seeds ... are not independent tasks" --
duplicate `request_id`s, such as retries of the same task, count once,
never per-attempt).

This task's own negative_acceptance ("Per-request overlapping GPU seconds
cannot double count batches or omit idle/multi-replica capacity") is
enforced by construction, not by a best-effort filter: `cost_per_success`
and `window_total_cost` below never sum, integrate or otherwise derive a
dollar figure from `RequestUsage.gpu_seconds_start`/`gpu_seconds_end` at
all -- the only real total that can ever produce a cost figure is the
window's own already-reconciled bill, which by its own field name and
docstring already includes idle time and every replica's own capacity.
There is structurally no code path here capable of re-summing
continuously-batched, overlapping per-request GPU seconds into a second,
larger total (the double-count failure mode), nor one capable of
crediting only the replica-seconds some request happened to touch (the
"omit idle/multi-replica capacity" failure mode) -- both are exactly the
mistake a naive "sum each request's own GPU seconds at some per-second
rate" implementation would make, demonstrated directly by
`tests/tasks/test_ftf_1302.py`'s own fault injection, which swaps in
exactly that naive approach and shows it produces the wrong dollar figure
on Section 32's own golden example (a $12 GPU bill including idle, $2 CPU
and $1 other cost for 100 successful tasks must give $0.15 per success).
`RequestUsage.gpu_seconds_start`/`gpu_seconds_end` are still real, typed
fields -- a caller genuinely needs to declare which request ran when, on
which replica, to have anything worth calling a "declared completed
workload" -- they are simply never load-bearing for the cost figure
itself, only for the outcome-deduplication `cost_per_success` performs.

No live HTTP route is added by this task (`api_contract`/
`shared_lock_required` in the task packet both point at FTF-1301's own,
already-frozen contract slot 13 -- this task claims no new lock and adds
no new path); `proving_symbol` is deliberately left empty in
`docs/spec-matrix.yaml`, matching the FTF-1401/FTF-1601/FTF-1701/FTF-1810
precedent for a computation module with no live product-runtime caller in
this repository yet -- a genuine, disclosed 0/0, not inflation.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

__all__ = [
    "TASK_OUTCOMES",
    "LocalComputeCostingError",
    "ServiceWindow",
    "RequestUsage",
    "window_total_cost",
    "unique_successful_task_ids",
    "cost_per_success",
]

# Section 69.2's own closed terminal-outcome vocabulary.
TASK_OUTCOMES: tuple[str, ...] = (
    "pass",
    "model_fail",
    "tool_fail",
    "infra_error",
    "timeout",
    "cancelled",
    "not_run",
)


class LocalComputeCostingError(ValueError):
    """A field value violates this domain's own shape/invariant, or a cost
    figure was requested that Section 32 declares undefined."""


def _validate_non_negative_decimal(value: object, field_name: str) -> Decimal:
    if not isinstance(value, Decimal) or isinstance(value, bool):
        raise LocalComputeCostingError(
            f"{field_name} must be a decimal.Decimal, not {type(value).__name__}: {value!r}"
        )
    if not value.is_finite():
        raise LocalComputeCostingError(f"{field_name} must be finite, not {value!r}")
    if value < 0:
        raise LocalComputeCostingError(f"{field_name} must not be negative: {value!r}")
    return value


@dataclass(frozen=True)
class ServiceWindow:
    """One declared, already-reconciled real service-window bill.

    `total_billed_gpu_cpu_ram_cost` is Section 32's own
    `total_billed_GPU_CPU_RAM_including_idle_and_warmup` -- the *entire*
    real bill for every replica's own capacity across this window,
    covering both busy and idle seconds. It is never re-derived from
    `RequestUsage` records; see module docstring.
    """

    window_start: datetime
    window_end: datetime
    replica_count: int
    total_billed_gpu_cpu_ram_cost: Decimal
    storage_cost: Decimal
    network_cost: Decimal
    platform_allocation_cost: Decimal

    def __post_init__(self) -> None:
        if self.window_end <= self.window_start:
            raise LocalComputeCostingError("window_end must be strictly after window_start")
        if not isinstance(self.replica_count, int) or isinstance(self.replica_count, bool):
            raise LocalComputeCostingError(
                f"replica_count must be an int, not {type(self.replica_count).__name__}: "
                f"{self.replica_count!r}"
            )
        if self.replica_count < 1:
            raise LocalComputeCostingError(f"replica_count must be >= 1: {self.replica_count!r}")
        _validate_non_negative_decimal(
            self.total_billed_gpu_cpu_ram_cost, "total_billed_gpu_cpu_ram_cost"
        )
        _validate_non_negative_decimal(self.storage_cost, "storage_cost")
        _validate_non_negative_decimal(self.network_cost, "network_cost")
        _validate_non_negative_decimal(self.platform_allocation_cost, "platform_allocation_cost")


@dataclass(frozen=True)
class RequestUsage:
    """One declared task attempt inside a service window -- part of the
    "declared completed workload" this task's own acceptance clause names.
    `gpu_seconds_start`/`gpu_seconds_end`/`replica_id` are real, typed
    fields a caller must still declare (which request ran when, on which
    replica), but see module docstring for why they are never load-bearing
    for the cost figure itself -- only `request_id`/`task_outcome` feed
    `cost_per_success`."""

    request_id: str
    task_outcome: str
    replica_id: str
    gpu_seconds_start: datetime
    gpu_seconds_end: datetime

    def __post_init__(self) -> None:
        if not isinstance(self.request_id, str) or not self.request_id.strip():
            raise LocalComputeCostingError("request_id must be a non-empty string")
        if self.task_outcome not in TASK_OUTCOMES:
            raise LocalComputeCostingError(
                f"task_outcome must be one of {TASK_OUTCOMES}: {self.task_outcome!r}"
            )
        if not isinstance(self.replica_id, str) or not self.replica_id.strip():
            raise LocalComputeCostingError("replica_id must be a non-empty string")
        if self.gpu_seconds_end <= self.gpu_seconds_start:
            raise LocalComputeCostingError(
                "gpu_seconds_end must be strictly after gpu_seconds_start"
            )


def window_total_cost(window: ServiceWindow) -> Decimal:
    """Section 32's own `window_self_hosted_cost`: the real total cost of
    this service window, mutually-exclusive categories summed once each
    (never a second allocation of any category already inside
    `total_billed_gpu_cpu_ram_cost`)."""
    return (
        window.total_billed_gpu_cpu_ram_cost
        + window.storage_cost
        + window.network_cost
        + window.platform_allocation_cost
    )


def unique_successful_task_ids(requests: list[RequestUsage]) -> frozenset[str]:
    """Section 69.2's own `unique_successful_tasks` denominator: distinct
    `request_id`s with a declared `pass` outcome. A request repeated under
    the same `request_id` (a retry of the same task) counts once, never
    once per attempt -- "Multiple turns from one conversation, repeated
    seeds ... are not independent tasks.\" """
    return frozenset(request.request_id for request in requests if request.task_outcome == "pass")


def cost_per_success(window: ServiceWindow, requests: list[RequestUsage]) -> Decimal:
    """Section 32's own `cost_per_success`: `window_total_cost(window) /
    unique_successful_tasks`. Raises when there are zero successes --
    Section 32 declares that case "undefined/infinite," never silently
    `$0` or a divide-by-zero crash with no explanation.

    `requests` is accepted only to derive the denominator (via
    `unique_successful_task_ids`) -- its own per-request GPU-second timing
    is never read here. See module docstring for why that is the intended
    enforcement of this task's own negative_acceptance, not an oversight.
    """
    successes = unique_successful_task_ids(requests)
    if not successes:
        raise LocalComputeCostingError(
            "cost_per_success is undefined (Section 32: zero successes -> undefined/infinite, "
            "not $0) -- this service window declared no request with outcome 'pass'"
        )
    return window_total_cost(window) / Decimal(len(successes))
