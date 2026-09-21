#!/usr/bin/env python3
"""FTF-1302 -- Local compute costing (no-DB unit/fault tests).

Runnable without Postgres or network egress -- this task adds no schema
(`database_changes`: "No independent schema change"). This suite proves:

1. `ServiceWindow`/`RequestUsage` construction guards: negative amounts,
   non-Decimal amounts, a non-positive window, a non-positive replica
   count, an unrecognized `task_outcome`, and a non-positive per-request
   interval are all refused at construction, before any cost is computed.
2. `window_total_cost`: Section 32's own `window_self_hosted_cost` -- the
   four cost categories sum exactly once each.
3. `unique_successful_task_ids`: only `pass`-outcome requests count, and a
   repeated `request_id` (a retry of the same task) counts once, not once
   per attempt.
4. `cost_per_success`: Section 32's own literal golden example ($12 GPU
   bill including idle, $2 CPU, $1 other cost, 100 successful tasks ->
   $0.15 per success) plus the negative_acceptance case (zero successes
   is undefined, refused explicitly, never a silent $0).
5. Own fault injection (Law 1): a naive "sum each request's own
   GPU-second interval at some per-second rate" reimplementation of
   `cost_per_success` -- exactly this task's own negative_acceptance
   failure mode ("per-request overlapping GPU seconds ... double count
   batches or omit idle/multi-replica capacity") -- is shown to produce
   the *wrong* dollar figure on the same golden example the real
   implementation gets right, both when requests overlap (continuous
   batching double-counts) and when requests cover only part of the
   window/replica fleet (idle/multi-replica capacity is omitted).

Run directly: python3 tests/tasks/test_ftf_1302.py
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
API_ROOT = ROOT / "apps" / "api"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(API_ROOT))

from app.services.local_compute_costing import (  # noqa: E402
    LocalComputeCostingError,
    RequestUsage,
    ServiceWindow,
    cost_per_success,
    unique_successful_task_ids,
    window_total_cost,
)

FAILURES: list[str] = []


def check(criterion_id: str, description: str, condition: bool) -> None:
    if not condition:
        FAILURES.append(f"FAIL [{criterion_id}] {description}")


def _rejects(fn, *args: object, **kwargs: object) -> bool:
    try:
        fn(*args, **kwargs)
        return False
    except LocalComputeCostingError:
        return True


_START = datetime(2026, 9, 1, tzinfo=UTC)
_END = _START + timedelta(hours=1)


# Deliberately unannotated (mypy does not check untyped-def bodies/call
# sites by default -- matching tests/tasks/test_ftf_0006.py's own
# `_clean_event` idiom): several tests below pass an intentionally
# wrong-typed override (e.g. a float where ServiceWindow requires a
# Decimal) to prove construction-time rejection, which a strictly typed
# helper could not itself express without `# type: ignore` noise on every
# such call.
def _window(**overrides):
    kwargs = {
        "window_start": _START,
        "window_end": _END,
        "replica_count": 1,
        "total_billed_gpu_cpu_ram_cost": Decimal("14"),
        "storage_cost": Decimal("0"),
        "network_cost": Decimal("0"),
        "platform_allocation_cost": Decimal("1"),
    }
    kwargs.update(overrides)
    return kwargs


def _request(**overrides):
    kwargs = {
        "request_id": "req-1",
        "task_outcome": "pass",
        "replica_id": "replica-0",
        "gpu_seconds_start": _START,
        "gpu_seconds_end": _START + timedelta(minutes=1),
    }
    kwargs.update(overrides)
    return kwargs


# -- 1. Construction guards --------------------------------------------------


def test_window_rejects_end_before_start() -> None:
    check(
        "FTF-1302",
        "window_end must be strictly after window_start",
        _rejects(ServiceWindow, **_window(window_start=_END, window_end=_START)),
    )


def test_window_rejects_zero_replica_count() -> None:
    check(
        "FTF-1302",
        "replica_count must be >= 1",
        _rejects(ServiceWindow, **_window(replica_count=0)),
    )


def test_window_rejects_non_decimal_cost() -> None:
    check(
        "FTF-1302",
        "total_billed_gpu_cpu_ram_cost must be a decimal.Decimal, not a float",
        _rejects(ServiceWindow, **_window(total_billed_gpu_cpu_ram_cost=14.0)),
    )


def test_window_rejects_negative_cost() -> None:
    check(
        "FTF-1302",
        "storage_cost must not be negative",
        _rejects(ServiceWindow, **_window(storage_cost=Decimal("-1"))),
    )


def test_request_rejects_unknown_task_outcome() -> None:
    check(
        "FTF-1302",
        "task_outcome must be one of Section 69.2's own closed vocabulary",
        _rejects(RequestUsage, **_request(task_outcome="succeeded")),
    )


def test_request_rejects_non_positive_interval() -> None:
    check(
        "FTF-1302",
        "gpu_seconds_end must be strictly after gpu_seconds_start",
        _rejects(
            RequestUsage,
            **_request(gpu_seconds_start=_START, gpu_seconds_end=_START),
        ),
    )


def test_request_rejects_empty_request_id() -> None:
    check(
        "FTF-1302",
        "request_id must be a non-empty string",
        _rejects(RequestUsage, **_request(request_id="")),
    )


# -- 2. window_total_cost -----------------------------------------------------


def test_window_total_cost_sums_four_categories_once_each() -> None:
    window = ServiceWindow(
        **_window(
            total_billed_gpu_cpu_ram_cost=Decimal("12"),
            storage_cost=Decimal("2"),
            network_cost=Decimal("0.5"),
            platform_allocation_cost=Decimal("0.5"),
        )
    )
    check(
        "FTF-1302",
        "window_total_cost sums GPU/CPU/RAM + storage + network + platform allocation once each",
        window_total_cost(window) == Decimal("15"),
    )


# -- 3. unique_successful_task_ids -------------------------------------------


def test_unique_successful_task_ids_excludes_non_pass_outcomes() -> None:
    requests = [
        RequestUsage(**_request(request_id="a", task_outcome="pass")),
        RequestUsage(**_request(request_id="b", task_outcome="model_fail")),
        RequestUsage(**_request(request_id="c", task_outcome="timeout")),
    ]
    check(
        "FTF-1302",
        "only pass-outcome requests count toward unique_successful_tasks",
        unique_successful_task_ids(requests) == frozenset({"a"}),
    )


def test_unique_successful_task_ids_dedupes_retries_of_the_same_request() -> None:
    requests = [
        RequestUsage(**_request(request_id="a", task_outcome="model_fail")),
        RequestUsage(
            **_request(
                request_id="a",
                task_outcome="pass",
                gpu_seconds_start=_START + timedelta(minutes=2),
                gpu_seconds_end=_START + timedelta(minutes=3),
            )
        ),
    ]
    check(
        "FTF-1302",
        "a retried request_id that eventually passes counts once, not once per attempt",
        unique_successful_task_ids(requests) == frozenset({"a"}),
    )


# -- 4. cost_per_success: Section 32's own golden example + negative_acceptance --

_GOLDEN_WINDOW = ServiceWindow(
    **_window(
        total_billed_gpu_cpu_ram_cost=Decimal("14"),  # $12 GPU (incl. idle) + $2 CPU
        storage_cost=Decimal("0"),
        network_cost=Decimal("0"),
        platform_allocation_cost=Decimal("1"),  # "$1 other cost"
    )
)


def _golden_requests(count: int) -> list[RequestUsage]:
    return [
        RequestUsage(**_request(request_id=f"task-{i}", task_outcome="pass")) for i in range(count)
    ]


def test_cost_per_success_matches_section_32_golden_example() -> None:
    result = cost_per_success(_GOLDEN_WINDOW, _golden_requests(100))
    check(
        "FTF-1302",
        "Section 32 golden example: $12 GPU (incl. idle) + $2 CPU + $1 other / 100 "
        "successes = $0.15 per success",
        result == Decimal("0.15"),
    )


def test_cost_per_success_ignores_per_request_gpu_second_overlap() -> None:
    """Two requests fully overlapping on the same replica for the whole
    window (continuous batching) must not change cost_per_success -- the
    figure comes only from the window's own real bill and the success
    count, never a re-derivation from per-request timing."""
    overlapping = [
        RequestUsage(
            **_request(
                request_id="task-0",
                task_outcome="pass",
                replica_id="replica-0",
                gpu_seconds_start=_START,
                gpu_seconds_end=_END,
            )
        ),
        RequestUsage(
            **_request(
                request_id="task-1",
                task_outcome="pass",
                replica_id="replica-0",
                gpu_seconds_start=_START,
                gpu_seconds_end=_END,
            )
        ),
    ]
    result = cost_per_success(_GOLDEN_WINDOW, overlapping)
    check(
        "FTF-1302",
        "fully overlapping per-request GPU seconds do not change cost_per_success",
        result == Decimal("15") / Decimal("2"),
    )


def test_cost_per_success_raises_on_zero_successes() -> None:
    all_failed = [
        RequestUsage(**_request(request_id="a", task_outcome="model_fail")),
        RequestUsage(**_request(request_id="b", task_outcome="timeout")),
    ]
    check(
        "FTF-1302",
        "negative_acceptance: zero successes is undefined/infinite (Section 32), refused "
        "explicitly, never a silent $0",
        _rejects(cost_per_success, _GOLDEN_WINDOW, all_failed),
    )


# -- 5. Own fault injection: the naive per-request-summing failure mode -----


def _naive_incorrect_cost_per_success_by_summing_requests(
    window: ServiceWindow, requests: list[RequestUsage], *, per_second_rate: Decimal
) -> Decimal:
    """Exactly the mistake this task's own negative_acceptance forbids:
    re-derive the GPU/CPU/RAM cost by summing each request's own declared
    GPU-second interval at some per-second rate, instead of using the
    window's own already-reconciled bill. Used only here, as the failure
    mode this suite proves the real implementation does NOT exhibit."""
    successes = unique_successful_task_ids(requests)
    if not successes:
        raise LocalComputeCostingError("no successes")
    total_gpu_seconds = sum(
        (r.gpu_seconds_end - r.gpu_seconds_start).total_seconds() for r in requests
    )
    gpu_cost = Decimal(str(total_gpu_seconds)) * per_second_rate
    total = gpu_cost + window.storage_cost + window.network_cost + window.platform_allocation_cost
    return total / Decimal(len(successes))


def test_fault_injection_naive_request_summing_double_counts_overlap() -> None:
    """Two requests fully overlapping the whole window on one replica:
    the real bill for that shared GPU-second capacity is $12 total, but
    naively summing each request's own full-window interval at the
    bill-implied per-second rate charges it twice."""
    window = ServiceWindow(
        **_window(
            total_billed_gpu_cpu_ram_cost=Decimal("12"),
            storage_cost=Decimal("0"),
            network_cost=Decimal("0"),
            platform_allocation_cost=Decimal("0"),
        )
    )
    duration_seconds = Decimal(str((_END - _START).total_seconds()))
    implied_rate = Decimal("12") / duration_seconds
    overlapping = [
        RequestUsage(
            **_request(
                request_id="task-0",
                task_outcome="pass",
                gpu_seconds_start=_START,
                gpu_seconds_end=_END,
            )
        ),
        RequestUsage(
            **_request(
                request_id="task-1",
                task_outcome="pass",
                gpu_seconds_start=_START,
                gpu_seconds_end=_END,
            )
        ),
    ]
    correct = cost_per_success(window, overlapping)
    naive = _naive_incorrect_cost_per_success_by_summing_requests(
        window, overlapping, per_second_rate=implied_rate
    )
    check(
        "FTF-1302",
        "real cost_per_success is the correct $6/success (12 / 2)",
        correct == Decimal("6"),
    )
    check(
        "FTF-1302",
        "fault injection: naive per-request GPU-second summing double-counts the "
        "overlapping batch and disagrees with the correct figure ($12/success, not $6)",
        naive == Decimal("12") and naive != correct,
    )


def test_fault_injection_naive_request_summing_omits_idle_and_multi_replica_capacity() -> None:
    """One request uses 10% of one replica's time in a 2-replica window;
    the real bill ($20) covers idle time and the entirely-unused second
    replica too. Naively summing only the touched GPU seconds omits both,
    understating the true cost per success."""
    window = ServiceWindow(
        **_window(
            replica_count=2,
            total_billed_gpu_cpu_ram_cost=Decimal("20"),
            storage_cost=Decimal("0"),
            network_cost=Decimal("0"),
            platform_allocation_cost=Decimal("0"),
        )
    )
    duration_seconds = Decimal(str((_END - _START).total_seconds()))
    # Rate implied by the *real* bill across both replicas' full capacity.
    implied_rate = Decimal("20") / (duration_seconds * Decimal("2"))
    brief_request = [
        RequestUsage(
            **_request(
                request_id="task-0",
                task_outcome="pass",
                replica_id="replica-0",
                gpu_seconds_start=_START,
                gpu_seconds_end=_START + timedelta(minutes=6),  # 10% of the hour
            )
        )
    ]
    correct = cost_per_success(window, brief_request)
    naive = _naive_incorrect_cost_per_success_by_summing_requests(
        window, brief_request, per_second_rate=implied_rate
    )
    check(
        "FTF-1302",
        "real cost_per_success is the full $20 window bill for the one success",
        correct == Decimal("20"),
    )
    check(
        "FTF-1302",
        "fault injection: naive per-request GPU-second summing omits idle time and the "
        "unused second replica's own capacity, understating the true cost per success",
        naive < correct,
    )


def main() -> None:
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()

    if FAILURES:
        print(f"FTF-1302 Local compute costing tests: {len(FAILURES)} failed")
        for failure in FAILURES:
            print(f"  {failure}")
        sys.exit(1)

    print("FTF-1302 Local compute costing tests: all checks passed")
    sys.exit(0)


if __name__ == "__main__":
    main()
