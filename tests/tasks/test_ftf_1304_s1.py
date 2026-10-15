#!/usr/bin/env python3
"""FTF-1304-S1 -- Compound per-step binding cost attribution (no-DB unit/fault tests).

Runnable without Postgres or network egress: this child slice of the CERTIFIED
FTF-1304 adds no schema, route, UI, contract edit, dependency, secret or keyed
service. One behavioural suite (`suite`) is run twice over:

1. against the real module -- every check must pass;
2. against a copy of the module with ONE protection removed (the fault table
   in `FAULTS`) -- the same suite must fail, naming which check caught it.

Criterion ids in every failure line: FTF-1304-S1-<clause>.

Run directly: python3 tests/tasks/test_ftf_1304_s1.py
"""

# ruff: noqa: E501  (the fault table quotes production source lines verbatim)
from __future__ import annotations

import dataclasses
import itertools
import os
import random
import subprocess
import sys
import types
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent.parent
API_ROOT = ROOT / "apps" / "api"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(API_ROOT))

from packages.litellm_client.real_chat_probe import RouteIdentityState  # noqa: E402

import app.services.compound_binding_cost_attribution as real_module  # noqa: E402
from app.services.candidate_ranking import (  # noqa: E402
    STATUS_INCONCLUSIVE_RETAIN_INCUMBENT,
    STATUS_WINNER,
    CandidateRankingError,
    CostInterval,
    RankingCandidate,
    rank_candidates,
)
from app.services.cost_per_success_calculator import cost_per_success  # noqa: E402
from app.services.local_compute_costing import ServiceWindow  # noqa: E402
from app.services.provider_token_costing import (  # noqa: E402
    ProviderRateCard,
    ProviderTokenUsage,
)
from app.target_connectors.production_faithful_target_agent_harness_contract import (  # noqa: E402
    AdapterDescribeResult,
    HarnessContractError,
)

MODULE_PATH = API_ROOT / "app" / "services" / "compound_binding_cost_attribution.py"
D = Decimal
T0 = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)
FAILURES: list[str] = []


class Recorder:
    def __init__(self) -> None:
        self.failed: list[str] = []

    def check(self, criterion_id: str, description: str, condition: bool) -> None:
        if not condition:
            self.failed.append(f"FAIL [{criterion_id}] {description}")


def raises(exc: type[BaseException], fn: Callable[..., object]) -> bool:
    try:
        fn()
    except exc:
        return True
    except Exception:
        return False
    return False


def load_variant(old: str | None, new: str | None) -> types.ModuleType:
    """The module itself (old=None) or a copy with exactly one source edit."""
    source = MODULE_PATH.read_text(encoding="utf-8")
    if old is not None:
        assert new is not None
        assert source.count(old) == 1, f"fault anchor must occur exactly once: {old!r}"
        source = source.replace(old, new)
    module = types.ModuleType("compound_variant")
    module.__dict__["__file__"] = str(MODULE_PATH)
    sys.modules["compound_variant"] = module
    exec(compile(source, str(MODULE_PATH), "exec"), module.__dict__)  # noqa: S102
    return module


# --------------------------------------------------------------------------- fixtures
def usage(i: int = 1000, o: int = 100, **extra: int) -> ProviderTokenUsage:
    return ProviderTokenUsage(input_tokens=i, output_tokens=o, **extra)


LARGE_CARD = ProviderRateCard(
    currency="USD", input_token_rate=D("0.000001"), output_token_rate=D("0.000005")
)
RATES = {"large-api": LARGE_CARD}


def window(
    total: str = "14",
    storage: str = "0.6",
    network: str = "0.4",
    platform: str = "0",
    start: datetime = T0,
    hours: int = 1,
) -> ServiceWindow:
    return ServiceWindow(
        window_start=start,
        window_end=start + timedelta(hours=hours),
        replica_count=1,
        total_billed_gpu_cpu_ram_cost=D(total),
        storage_cost=D(storage),
        network_cost=D(network),
        platform_allocation_cost=D(platform),
    )


def call(
    m: types.ModuleType,
    rid: str,
    step: str | None,
    alias: str,
    *,
    usage_: ProviderTokenUsage | None = None,
    minute: int = 1,
    state: RouteIdentityState = RouteIdentityState.VERIFIED,
    fingerprint: str | None = "fp-1",
    seconds: int = 30,
) -> Any:
    started = T0 + timedelta(minutes=minute)
    return m.ModelCallRecord(
        request_id=rid,
        step_key=step,
        route_alias=alias,
        identity_state=state,
        deployment_fingerprint=fingerprint,
        usage=usage_,
        started_at=started,
        ended_at=started + timedelta(seconds=seconds),
    )


def attempt(
    m: types.ModuleType,
    rid: str,
    outcome: str,
    calls: list[object],
    pending: str | None = None,
) -> Any:
    return m.AttemptTrace(
        request_id=rid,
        outcome=outcome,
        calls=tuple(calls),
        pending_cost=None if pending is None else D(pending),
    )


def small_binding(m: types.ModuleType, artifact: str = "base") -> Any:
    return m.CompoundBinding(
        default_route_alias="large-api",
        provider="gateway",
        default_pricing_mode="provider",
        default_artifact_kind="existing",
        default_step_kind="reasoning",
        steps=(
            m.StepBinding("classify", "small-local", "classify", "self_hosted", artifact),
            m.StepBinding("extract", "small-local", "extract", "self_hosted", artifact),
        ),
    )


def pair(m: types.ModuleType, rid: str, outcome: str = "pass", minute: int = 1) -> Any:
    """One attempt: a small self-hosted classify step plus a large provider call."""
    return attempt(
        m,
        rid,
        outcome,
        [
            call(m, rid, "classify", "small-local", minute=minute),
            call(m, rid, None, "large-api", usage_=usage(), minute=minute + 1),
        ],
    )


def self_window(m: types.ModuleType, window_: ServiceWindow, wid: str = "w1") -> Any:
    return m.SelfHostedWindow(wid, ("small-local",), window_, "USD")


def refused(
    rec: Recorder,
    cid: str,
    m: types.ModuleType,
    result: Any,
    status: str,
    code: str,
    rid: str | None,
    alias: str | None,
) -> None:
    rec.check(cid, f"status {status!r}, got {result.status!r}", result.status == status)
    rec.check(cid, f"no attempt costs on a refused attribution ({code})", not result.attempt_costs)
    rec.check(cid, "no route lines on a refused attribution", not result.route_lines)
    rec.check(cid, "no step-kind totals on a refused attribution", not result.step_kind_totals)
    match = [i for i in result.issues if i.code == code]
    rec.check(cid, f"issue code {code!r} present: {result.issues!r}", len(match) >= 1)
    if match:
        rec.check(
            cid,
            f"issue names request {rid!r} and route {alias!r}",
            (match[0].request_id, match[0].route_alias) == (rid, alias),
        )
        rec.check(cid, "issue status", match[0].status == status)


# --------------------------------------------------------------------------- the suite
def suite(m: types.ModuleType, rec: Recorder, *, slow: bool) -> None:
    attribute = m.attribute_compound_costs

    # ---- G: golden -- Section 32's own example ($12 GPU incl. idle + $2 CPU + $1 other,
    # 100 successful tasks) on a self-hosted small route plus a priced large provider route.
    binding = small_binding(m)
    attempts = [pair(m, f"r{i:03d}") for i in range(100)]
    window_ = window()  # 14 + 0.6 + 0.4 = 15 exactly
    result = attribute(binding, attempts, RATES, [self_window(m, window_)])
    rec.check("FTF-1304-S1-G1", f"priced, got {result.status!r}", result.status == "priced")
    rec.check("FTF-1304-S1-G1", "100 attempt costs", len(result.attempt_costs) == 100)
    total_settled = sum((a.settled_cost for a in result.attempt_costs), D(0))
    rec.check(
        "FTF-1304-S1-G1", f"total 15.15 exactly, got {total_settled}", total_settled == D("15.15")
    )
    rec.check(
        "FTF-1304-S1-G1",
        "each attempt = 0.15 self-hosted share + 0.0015 provider",
        all(a.settled_cost == D("0.1515") for a in result.attempt_costs),
    )
    lines = {(ln.route_alias, ln.step_kind): ln for ln in result.route_lines}
    rec.check(
        "FTF-1304-S1-G1",
        "two route lines",
        set(lines) == {("small-local", "classify"), ("large-api", "reasoning")},
    )
    small = lines.get(("small-local", "classify"))
    large = lines.get(("large-api", "reasoning"))
    rec.check(
        "FTF-1304-S1-G1",
        "small line exact",
        small is not None and (small.calls, small.cost, small.input_tokens) == (100, D("15"), 0),
    )
    rec.check(
        "FTF-1304-S1-G1",
        "large line exact",
        large is not None
        and (large.calls, large.cost, large.input_tokens, large.output_tokens)
        == (100, D("0.15"), 100_000, 10_000),
    )
    rec.check(
        "FTF-1304-S1-G1",
        "route lines sum to attempts",
        sum((ln.cost for ln in result.route_lines), D(0)) == total_settled,
    )
    totals = dict(result.step_kind_totals)
    rec.check(
        "FTF-1304-S1-G1",
        f"step-kind totals exact, got {totals}",
        totals == {"classify": D("15"), "reasoning": D("0.15")},
    )
    rec.check(
        "FTF-1304-S1-G1",
        "step-kind totals sum to attempts",
        sum(totals.values(), D(0)) == total_settled,
    )
    cps = cost_per_success(list(result.attempt_costs))
    rec.check(
        "FTF-1304-S1-G1",
        f"cost per success 0.1515, got {cps.cost_per_success}",
        cps.cost_per_success == D("0.1515"),
    )
    rec.check("FTF-1304-S1-G1", "candidate kind existing", result.candidate_kind == "existing")
    rec.check("FTF-1304-S1-G1", "issues empty", result.issues == ())
    rec.check(
        "FTF-1304-S1-G1",
        "hashes are sha256 hex",
        len(result.attribution_sha256) == 64 and len(result.binding_sha256) == 64,
    )

    # ---- G2: all attempts counted, a retried request id counts once as a success.
    retry = [
        pair(m, "t1", "model_fail", minute=1),
        pair(m, "t1", "pass", minute=5),
        pair(m, "t2", "pass", minute=9),
        attempt(m, "t3", "not_run", []),
    ]
    out = attribute(binding, retry, RATES, [self_window(m, window("6", "0", "0"))])
    rec.check("FTF-1304-S1-G2", f"priced, got {out.status}", out.status == "priced")
    rec.check("FTF-1304-S1-G2", "all four attempts present", len(out.attempt_costs) == 4)
    by_outcome = sorted((a.request_id, a.outcome) for a in out.attempt_costs)
    rec.check("FTF-1304-S1-G2", "failed attempt kept", ("t1", "model_fail") in by_outcome)
    spent = sum((a.settled_cost for a in out.attempt_costs), D(0))
    rec.check(
        "FTF-1304-S1-G2",
        f"failed attempt calls are paid: 6 + 3*0.0015, got {spent}",
        spent == D("6.0045"),
    )
    res = cost_per_success(list(out.attempt_costs))
    rec.check(
        "FTF-1304-S1-G2",
        f"2 unique successes, got {res.cost_per_success}",
        res.cost_per_success == D("3.00225"),
    )
    pend = attribute(
        binding,
        [
            attempt(
                m, "p1", "pass", [call(m, "p1", None, "large-api", usage_=usage())], pending="0.5"
            )
        ],
        RATES,
        [],
    )
    rec.check(
        "FTF-1304-S1-G2",
        "pending cost carried through, never dropped",
        pend.status == "priced" and pend.attempt_costs[0].pending_cost == D("0.5"),
    )

    # ---- R: every refusal, with status, request id and route (no content).
    r = attribute(
        binding,
        [attempt(m, "x1", "pass", [call(m, "x1", "summarize", "small-local")])],
        RATES,
        [self_window(m, window())],
    )
    refused(
        rec, "FTF-1304-S1-R1", m, r, "binding_violation", "step_not_in_binding", "x1", "small-local"
    )
    r = attribute(
        binding,
        [attempt(m, "x2", "pass", [call(m, "x2", "classify", "other-small")])],
        RATES,
        [self_window(m, window())],
    )
    refused(
        rec, "FTF-1304-S1-R2", m, r, "binding_violation", "route_not_bound", "x2", "other-small"
    )
    r = attribute(
        binding,
        [attempt(m, "x3", "pass", [call(m, "x3", None, "small-local")])],
        RATES,
        [self_window(m, window())],
    )
    refused(
        rec, "FTF-1304-S1-R3", m, r, "binding_violation", "route_not_bound", "x3", "small-local"
    )
    r = attribute(
        binding, [attempt(m, "x4", "pass", [call(m, "x4", "classify", "large-api")])], RATES, []
    )
    refused(rec, "FTF-1304-S1-R4", m, r, "binding_violation", "route_not_bound", "x4", "large-api")
    for tag, state, fp in (
        ("R5", RouteIdentityState.UNVERIFIED, "fp-1"),
        ("R6", RouteIdentityState.VERIFIED, None),
        ("R6b", RouteIdentityState.UNVERIFIED, None),
    ):
        r = attribute(
            binding,
            [
                attempt(
                    m,
                    "x5",
                    "pass",
                    [call(m, "x5", None, "large-api", usage_=usage(), state=state, fingerprint=fp)],
                )
            ],
            RATES,
            [],
        )
        refused(
            rec,
            f"FTF-1304-S1-{tag}",
            m,
            r,
            "identity_unverified",
            "identity_unverified",
            "x5",
            "large-api",
        )
    r = attribute(
        binding,
        [
            attempt(
                m,
                "x6",
                "pass",
                [call(m, "x6", "classify", "small-local", state=RouteIdentityState.UNVERIFIED)],
            )
        ],
        RATES,
        [self_window(m, window())],
    )
    rec.check(
        "FTF-1304-S1-R5", "unverified self-hosted call refused", r.status == "identity_unverified"
    )
    r = attribute(
        binding, [attempt(m, "x7", "pass", [call(m, "x7", None, "large-api")])], RATES, []
    )
    refused(rec, "FTF-1304-S1-R7", m, r, "unpriced", "usage_missing", "x7", "large-api")
    r = attribute(
        binding,
        [attempt(m, "x8", "pass", [call(m, "x8", None, "large-api", usage_=usage())])],
        {},
        [],
    )
    refused(rec, "FTF-1304-S1-R8", m, r, "unpriced", "rate_card_missing", "x8", "large-api")
    r = attribute(
        binding,
        [
            attempt(
                m, "x9", "pass", [call(m, "x9", None, "large-api", usage_=usage(cache_tokens=5))]
            )
        ],
        RATES,
        [],
    )
    refused(rec, "FTF-1304-S1-R9", m, r, "unpriced", "rate_missing_for_usage", "x9", "large-api")
    r = attribute(
        binding, [attempt(m, "y1", "pass", [call(m, "y1", "classify", "small-local")])], RATES, []
    )
    refused(rec, "FTF-1304-S1-R10", m, r, "unpriced", "no_window", "y1", "small-local")
    late = window(start=T0 + timedelta(hours=5))
    r = attribute(
        binding,
        [attempt(m, "y2", "pass", [call(m, "y2", "classify", "small-local")])],
        RATES,
        [self_window(m, late)],
    )
    refused(rec, "FTF-1304-S1-R11", m, r, "unpriced", "outside_window_range", "y2", "small-local")
    straddle = [
        attempt(m, "y3", "pass", [call(m, "y3", "classify", "small-local", minute=59, seconds=120)])
    ]
    r = attribute(binding, straddle, RATES, [self_window(m, window())])
    refused(rec, "FTF-1304-S1-R11", m, r, "unpriced", "outside_window_range", "y3", "small-local")
    early = [attempt(m, "y3b", "pass", [call(m, "y3b", "classify", "small-local", minute=-1)])]
    r = attribute(binding, early, RATES, [self_window(m, window())])
    refused(rec, "FTF-1304-S1-R11", m, r, "unpriced", "outside_window_range", "y3b", "small-local")
    edge = [
        attempt(
            m, "y3c", "pass", [call(m, "y3c", "classify", "small-local", minute=59, seconds=60)]
        )
    ]
    r = attribute(binding, edge, RATES, [self_window(m, window())])
    rec.check(
        "FTF-1304-S1-R11", "a call ending exactly at window_end is inside", r.status == "priced"
    )
    two = [self_window(m, window(), "wa"), self_window(m, window(), "wb")]
    r = attribute(
        binding, [attempt(m, "y4", "pass", [call(m, "y4", "classify", "small-local")])], RATES, two
    )
    refused(rec, "FTF-1304-S1-R12", m, r, "unpriced", "window_ambiguous", "y4", "small-local")
    r = attribute(
        binding,
        [attempt(m, "y5", "pass", [call(m, "y5", None, "large-api", usage_=usage())])],
        RATES,
        [self_window(m, window())],
    )
    refused(rec, "FTF-1304-S1-R13", m, r, "unpriced", "window_without_calls", None, None)
    r = attribute(binding, [attempt(m, "y6", "pass", [])], RATES, [])
    refused(rec, "FTF-1304-S1-R14", m, r, "unpriced", "success_without_model_call", "y6", None)
    r = attribute(binding, [attempt(m, "y7", "tool_fail", [])], RATES, [])
    rec.check(
        "FTF-1304-S1-R14",
        "a failed attempt with no call is a priced $0",
        r.status == "priced" and r.attempt_costs[0].settled_cost == D(0),
    )
    mixed = attribute(
        binding,
        [
            attempt(m, "z1", "pass", [call(m, "z1", "bogus", "large-api")]),
            attempt(
                m,
                "z2",
                "pass",
                [call(m, "z2", None, "large-api", state=RouteIdentityState.UNVERIFIED)],
            ),
            attempt(m, "z3", "pass", [call(m, "z3", None, "large-api")]),
        ],
        RATES,
        [],
    )
    rec.check(
        "FTF-1304-S1-R15",
        "binding violation outranks identity and unpriced",
        mixed.status == "binding_violation",
    )
    rec.check(
        "FTF-1304-S1-R15",
        "every refusal listed",
        {i.code for i in mixed.issues}
        >= {"step_not_in_binding", "identity_unverified", "usage_missing"},
    )
    nobinding = attribute(
        binding,
        [
            attempt(
                m,
                "z2",
                "pass",
                [call(m, "z2", None, "large-api", state=RouteIdentityState.UNVERIFIED)],
            ),
            attempt(m, "z3", "pass", [call(m, "z3", None, "large-api")]),
        ],
        RATES,
        [],
    )
    rec.check(
        "FTF-1304-S1-R15", "identity outranks unpriced", nobinding.status == "identity_unverified"
    )
    # a refused attribution can never feed cost_per_success or ranking
    rec.check(
        "FTF-1304-S1-R16",
        "refused attribution cannot become a ranking candidate",
        raises(
            m.CompoundAttributionError,
            lambda: m.compound_ranking_candidate(
                "c",
                nobinding,
                quality_lower_bound=D("0.9"),
                interval_low=D("1"),
                interval_high=D("2"),
            ),
        ),
    )

    # ---- W: shared-window allocation (Section 32, no double-counted GPU second).
    shared = m.CompoundBinding(
        "large-api",
        "gateway",
        "provider",
        "existing",
        "reasoning",
        (
            m.StepBinding("classify", "small-a", "classify", "self_hosted", "base"),
            m.StepBinding("extract", "small-b", "extract", "self_hosted", "base"),
        ),
    )
    shared_attempts = [
        attempt(
            m,
            f"s{i}",
            "pass",
            [
                call(m, f"s{i}", "classify", "small-a", minute=i + 1),
                call(m, f"s{i}", "extract", "small-b", minute=i + 1),
            ],
        )
        for i in range(5)
    ]
    one_gpu = m.SelfHostedWindow("gpu", ("small-a", "small-b"), window("10", "0", "0"), "USD")
    out = attribute(shared, shared_attempts, {}, [one_gpu])
    spent = sum((a.settled_cost for a in out.attempt_costs), D(0))
    rec.check(
        "FTF-1304-S1-W1",
        f"one GPU shared by two routes costs 10 once, got {spent}",
        out.status == "priced" and spent == D("10"),
    )
    rec.check(
        "FTF-1304-S1-W1",
        "route lines add to the window total",
        sum((ln.cost for ln in out.route_lines), D(0)) == D("10"),
    )
    two_windows = [
        m.SelfHostedWindow("ga", ("small-a",), window("10", "0", "0"), "USD"),
        m.SelfHostedWindow("gb", ("small-b",), window("4", "0", "0"), "USD"),
    ]
    out2 = attribute(shared, shared_attempts, {}, two_windows)
    rec.check(
        "FTF-1304-S1-W1",
        "separate windows are each paid once",
        sum((a.settled_cost for a in out2.attempt_costs), D(0)) == D("14"),
    )

    counts = (3, 7, 1000) if slow else (3, 7, 33)
    for n in counts:
        many = [
            attempt(
                m,
                f"m{i:05d}",
                "pass",
                [call(m, f"m{i:05d}", "classify", "small-local", minute=i % 50 + 1)],
            )
            for i in range(n)
        ]
        total = D("10.000000000001") if n == 7 else D("100") / D(n) * D(n) + D("0.123456789012345")
        win = m.SelfHostedWindow("many", ("small-local",), window(str(total), "0", "0"), "USD")
        out = attribute(binding, many, {}, [win])
        costs = [a.settled_cost for a in out.attempt_costs]
        rec.check(
            "FTF-1304-S1-W2",
            f"{n} calls sum exactly to the window total",
            out.status == "priced" and sum(costs, D(0)) == total,
        )
        share = costs[0]
        rec.check(
            "FTF-1304-S1-W2",
            f"{n} calls: every share but one is equal",
            sum(1 for c in costs if c == share) >= n - 1,
        )
        rec.check(
            "FTF-1304-S1-W2", f"{n} calls: the shares are positive", all(c > 0 for c in costs)
        )
    cent = m.SelfHostedWindow("cent", ("small-local",), window("0.01", "0", "0"), "USD")
    three = [
        attempt(m, f"c{i}", "pass", [call(m, f"c{i}", "classify", "small-local", minute=i + 1)])
        for i in range(3)
    ]
    out = attribute(binding, three, {}, [cent])
    rec.check(
        "FTF-1304-S1-W2",
        "a cent over three calls loses nothing",
        sum((a.settled_cost for a in out.attempt_costs), D(0)) == D("0.01"),
    )

    # ---- T: step-kind totals add up when several routes share a step kind.
    kinds = m.CompoundBinding(
        "large-api",
        "gateway",
        "provider",
        "existing",
        "reasoning",
        (
            m.StepBinding("c1", "mid-a", "classify", "provider", "base"),
            m.StepBinding("c2", "mid-b", "classify", "provider", "base"),
        ),
    )
    cards = {**RATES, "mid-a": LARGE_CARD, "mid-b": LARGE_CARD}
    kind_attempt = attempt(
        m,
        "kk",
        "pass",
        [
            call(m, "kk", "c1", "mid-a", usage_=usage()),
            call(m, "kk", "c2", "mid-b", usage_=usage(2000, 0), minute=2),
            call(m, "kk", None, "large-api", usage_=usage(0, 200), minute=3),
        ],
    )
    out = attribute(kinds, [kind_attempt], cards, [])
    kind_totals = dict(out.step_kind_totals)
    rec.check(
        "FTF-1304-S1-T1",
        f"two routes of one step kind are summed, got {kind_totals}",
        out.status == "priced"
        and kind_totals == {"classify": D("0.0035"), "reasoning": D("0.001")},
    )
    rec.check(
        "FTF-1304-S1-T1",
        "step-kind totals equal the attempt total",
        sum(kind_totals.values(), D(0)) == out.attempt_costs[0].settled_cost,
    )

    # ---- V: the certified FTF-04A25 override check is used.
    describe = AdapterDescribeResult(
        protocol_version="1",
        artifact_digest="a" * 8,
        runtime_digest="b" * 8,
        supported_model_binding_keys=("classify", "extract"),
        input_schema_ref="in",
        output_schema_ref="out",
        tool_schema_ref="tool",
        trace_schema_ref="trace",
        required_dependencies=(),
    )
    mb = binding.validate(describe)
    rec.check(
        "FTF-1304-S1-V1",
        "validate returns the certified ModelBinding",
        (mb.model_id, mb.provider, mb.route_params)
        == ("large-api", "gateway", (("classify", "small-local"), ("extract", "small-local"))),
    )
    narrow = AdapterDescribeResult(
        protocol_version="1",
        artifact_digest="a" * 8,
        runtime_digest="b" * 8,
        supported_model_binding_keys=("classify",),
        input_schema_ref="in",
        output_schema_ref="out",
        tool_schema_ref="tool",
        trace_schema_ref="trace",
        required_dependencies=(),
    )
    rec.check(
        "FTF-1304-S1-V1",
        "an undeclared step key is refused",
        raises(HarnessContractError, lambda: binding.validate(narrow)),
    )
    rec.check(
        "FTF-1304-S1-V1",
        "route_params sorted by step key",
        m.CompoundBinding(
            "a",
            "p",
            "provider",
            "base",
            "reasoning",
            (
                m.StepBinding("z", "r1", "other", "provider", "base"),
                m.StepBinding("b", "r2", "other", "provider", "base"),
            ),
        )
        .to_model_binding()
        .route_params
        == (("b", "r2"), ("z", "r1")),
    )

    # ---- B: binding construction and hash.
    rec.check(
        "FTF-1304-S1-B1",
        "duplicate step key refused",
        raises(
            m.CompoundAttributionError,
            lambda: m.CompoundBinding(
                "a",
                "p",
                "provider",
                "base",
                "reasoning",
                (
                    m.StepBinding("s", "r1", "other", "provider", "base"),
                    m.StepBinding("s", "r2", "other", "provider", "base"),
                ),
            ),
        ),
    )
    rec.check(
        "FTF-1304-S1-B1",
        "a step reusing the default alias in another pricing mode refused",
        raises(
            m.CompoundAttributionError,
            lambda: m.CompoundBinding(
                "a",
                "p",
                "provider",
                "base",
                "reasoning",
                (m.StepBinding("s", "a", "other", "self_hosted", "base"),),
            ),
        ),
    )
    rec.check(
        "FTF-1304-S1-B1",
        "an alias in two pricing modes across steps refused",
        raises(
            m.CompoundAttributionError,
            lambda: m.CompoundBinding(
                "a",
                "p",
                "provider",
                "base",
                "reasoning",
                (
                    m.StepBinding("s", "r", "other", "self_hosted", "base"),
                    m.StepBinding("t", "r", "other", "provider", "base"),
                ),
            ),
        ),
    )
    rec.check(
        "FTF-1304-S1-B1",
        "an alias in two artifact kinds refused",
        raises(
            m.CompoundAttributionError,
            lambda: m.CompoundBinding(
                "a",
                "p",
                "provider",
                "base",
                "reasoning",
                (
                    m.StepBinding("s", "r", "other", "provider", "base"),
                    m.StepBinding("t", "r", "other", "provider", "tuned"),
                ),
            ),
        ),
    )
    rec.check(
        "FTF-1304-S1-B1",
        "the same alias twice with the same mode is allowed",
        small_binding(m).steps[0].route_alias == small_binding(m).steps[1].route_alias,
    )
    for field, kwargs in (
        ("step_key", dict(step_key="")),
        ("route_alias", dict(route_alias=" ")),
        ("step_kind", dict(step_kind="guess")),
        ("pricing_mode", dict(pricing_mode="free")),
        ("artifact_kind", dict(artifact_kind="magic")),
    ):
        base_args: dict[str, Any] = dict(
            step_key="s",
            route_alias="r",
            step_kind="other",
            pricing_mode="provider",
            artifact_kind="base",
        )
        base_args.update(kwargs)
        rec.check(
            "FTF-1304-S1-B2",
            f"StepBinding refuses bad {field}",
            raises(m.CompoundAttributionError, lambda a=base_args: m.StepBinding(**a)),
        )
    rec.check(
        "FTF-1304-S1-B2",
        "str subclass refused",
        raises(
            m.CompoundAttributionError,
            lambda: m.StepBinding(type("S", (str,), {})("s"), "r", "other", "provider", "base"),
        ),
    )
    rec.check(
        "FTF-1304-S1-B2",
        "default pricing mode checked",
        raises(
            m.CompoundAttributionError,
            lambda: m.CompoundBinding("a", "p", "nope", "base", "reasoning", ()),
        ),
    )
    rec.check(
        "FTF-1304-S1-B2",
        "default artifact kind checked",
        raises(
            m.CompoundAttributionError,
            lambda: m.CompoundBinding("a", "p", "provider", "nope", "reasoning", ()),
        ),
    )
    rec.check(
        "FTF-1304-S1-B2",
        "steps must be StepBinding",
        raises(
            m.CompoundAttributionError,
            lambda: m.CompoundBinding("a", "p", "provider", "base", "reasoning", ("x",)),
        ),
    )
    rec.check(
        "FTF-1304-S1-B2",
        "steps must be a sequence",
        raises(
            m.CompoundAttributionError,
            lambda: m.CompoundBinding("a", "p", "provider", "base", "reasoning", "x"),
        ),
    )

    def sha(**over: str) -> str:
        args = dict(
            alias="a",
            prov="p",
            mode="provider",
            kind="base",
            step="s",
            sroute="r",
            skind="other",
            smode="provider",
            sart="base",
        )
        args.update(over)
        return m.CompoundBinding(
            args["alias"],
            args["prov"],
            args["mode"],
            args["kind"],
            "reasoning",
            (
                m.StepBinding(
                    args["step"], args["sroute"], args["skind"], args["smode"], args["sart"]
                ),
            ),
        ).binding_sha256

    base_sha = sha()
    rec.check("FTF-1304-S1-B3", "binding hash is stable", base_sha == sha())
    for field, value in (
        ("alias", "a2"),
        ("prov", "p2"),
        ("mode", "self_hosted"),
        ("kind", "tuned"),
        ("step", "s2"),
        ("sroute", "r2"),
        ("skind", "extract"),
        ("smode", "self_hosted"),
        ("sart", "tuned"),
    ):
        rec.check(
            "FTF-1304-S1-B3", f"binding hash covers {field}", sha(**{field: value}) != base_sha
        )
    steps_ab = (
        m.StepBinding("a", "r1", "other", "provider", "base"),
        m.StepBinding("b", "r2", "other", "provider", "base"),
    )
    rec.check(
        "FTF-1304-S1-B3",
        "binding hash independent of step order",
        m.CompoundBinding("x", "p", "provider", "base", "reasoning", steps_ab).binding_sha256
        == m.CompoundBinding(
            "x", "p", "provider", "base", "reasoning", tuple(reversed(steps_ab))
        ).binding_sha256,
    )

    # ---- K: kind and FTF-1305.
    def kind(default: str, *arts: str) -> str:
        return m.CompoundBinding(
            "a",
            "p",
            "provider",
            default,
            "reasoning",
            tuple(
                m.StepBinding(f"s{i}", f"r{i}", "other", "provider", x) for i, x in enumerate(arts)
            ),
        ).candidate_kind()

    rec.check(
        "FTF-1304-S1-K1",
        "any tuned step makes the candidate tuned",
        kind("existing", "tuned", "base") == "tuned"
        and kind("tuned") == "tuned"
        and kind("base", "tuned") == "tuned",
    )
    rec.check(
        "FTF-1304-S1-K1",
        "existing without tuned is existing",
        kind("base", "existing") == "existing" and kind("existing", "base") == "existing",
    )
    rec.check("FTF-1304-S1-K1", "all base is base", kind("base", "base") == "base")

    tuned_binding = m.CompoundBinding(
        "large-api",
        "gateway",
        "provider",
        "existing",
        "reasoning",
        (m.StepBinding("classify", "small-local", "classify", "self_hosted", "tuned"),),
    )
    tuned_attempts = [pair(m, f"k{i}") for i in range(10)]
    tuned_attr = attribute(
        tuned_binding, tuned_attempts, RATES, [self_window(m, window("1", "0", "0"))]
    )
    rec.check(
        "FTF-1304-S1-K2",
        "tuned attribution priced",
        tuned_attr.status == "priced" and tuned_attr.candidate_kind == "tuned",
    )
    rec.check(
        "FTF-1304-S1-K2",
        "a compound tuned candidate without amortization is refused by FTF-1305",
        raises(
            CandidateRankingError,
            lambda: m.compound_ranking_candidate(
                "tuned",
                tuned_attr,
                quality_lower_bound=D("0.9"),
                interval_low=D("0.1"),
                interval_high=D("0.2"),
            ),
        ),
    )
    amort = m.compound_ranking_candidate(
        "tuned",
        tuned_attr,
        quality_lower_bound=D("0.9"),
        interval_low=D("0.1"),
        interval_high=D("0.5"),
        training_and_qualification_cost=D("100"),
        approved_expected_lifetime_successes=1000,
    )
    expected_base = D("1.015") / D(10) + D("100") / D(1000)
    rec.check(
        "FTF-1304-S1-K2",
        f"amortized base, got {amort.cost.base}",
        amort.cost.base == expected_base
        and amort.cost.includes_training_amortization
        and amort.kind == "tuned",
    )
    rec.check(
        "FTF-1304-S1-K2", "candidate is measured economics", amort.economics_basis == "measured"
    )
    rec.check(
        "FTF-1304-S1-K2",
        "half an amortization request refused",
        raises(
            Exception,
            lambda: m.compound_ranking_candidate(
                "t",
                tuned_attr,
                quality_lower_bound=D("0.9"),
                interval_low=D("0.1"),
                interval_high=D("0.5"),
                training_and_qualification_cost=D("100"),
            ),
        ),
    )
    rec.check(
        "FTF-1304-S1-K2",
        "interval must contain the computed base",
        raises(
            CandidateRankingError,
            lambda: m.compound_ranking_candidate(
                "t",
                tuned_attr,
                quality_lower_bound=D("0.9"),
                interval_low=D("5"),
                interval_high=D("6"),
                training_and_qualification_cost=D("100"),
                approved_expected_lifetime_successes=1000,
            ),
        ),
    )
    zero_success = attribute(
        binding, [pair(m, "q1", "model_fail")], RATES, [self_window(m, window())]
    )
    rec.check(
        "FTF-1304-S1-K2",
        "zero successes cannot rank",
        raises(
            m.CompoundAttributionError,
            lambda: m.compound_ranking_candidate(
                "z",
                zero_success,
                quality_lower_bound=D("0.9"),
                interval_low=D("0.1"),
                interval_high=D("0.2"),
            ),
        ),
    )
    rec.check(
        "FTF-1304-S1-K2",
        "attribution type checked",
        raises(
            m.CompoundAttributionError,
            lambda: m.compound_ranking_candidate(
                "z",
                "x",
                quality_lower_bound=D("0.9"),
                interval_low=D("0.1"),
                interval_high=D("0.2"),
            ),
        ),
    )
    pending_candidate = m.compound_ranking_candidate(
        "pend",
        attribute(
            binding,
            [
                attempt(
                    m, "p", "pass", [call(m, "p", None, "large-api", usage_=usage())], pending="0.5"
                )
            ],
            RATES,
            [],
        ),
        quality_lower_bound=D("0.9"),
        interval_low=D("0.1"),
        interval_high=D("5"),
    )
    rec.check(
        "FTF-1304-S1-K2",
        "pending reconciliation reaches the ranking interval",
        pending_candidate.cost.has_pending_reconciliation
        and pending_candidate.cost.base == D("0.5015"),
    )
    inc = m.compound_ranking_candidate(
        "inc",
        attribute(
            binding,
            [attempt(m, "i", "pass", [call(m, "i", None, "large-api", usage_=usage())])],
            RATES,
            [],
        ),
        quality_lower_bound=D("0.9"),
        interval_low=D("0.001"),
        interval_high=D("0.002"),
        is_incumbent=True,
    )
    rec.check(
        "FTF-1304-S1-K2",
        "incumbent flag passed through",
        inc.is_incumbent and inc.kind == "existing",
    )
    inelig = m.compound_ranking_candidate(
        "ne",
        attribute(
            binding,
            [attempt(m, "i", "pass", [call(m, "i", None, "large-api", usage_=usage())])],
            RATES,
            [],
        ),
        quality_lower_bound=None,
        interval_low=D("0.001"),
        interval_high=D("0.002"),
        eligible=False,
        ineligibility_reason="policy",
    )
    rec.check(
        "FTF-1304-S1-K2",
        "ineligibility passed through",
        (not inelig.eligible)
        and inelig.ineligibility_reason == "policy"
        and inelig.quality_lower_bound is None,
    )

    incumbent = RankingCandidate(
        "single",
        "existing",
        True,
        D("0.9"),
        CostInterval(D("0.8"), D("1.0"), D("1.2")),
        "measured",
        is_incumbent=True,
    )
    # compound: 0.1 per success, interval overlapping the incumbent's [0.8, 1.2]
    cheap_attr = attribute(
        binding,
        [pair(m, f"w{i}") for i in range(10)],
        RATES,
        [self_window(m, window("1", "0", "0"))],
    )
    cheap = m.compound_ranking_candidate(
        "compound",
        cheap_attr,
        quality_lower_bound=D("0.9"),
        interval_low=D("0.05"),
        interval_high=D("0.9"),
    )
    verdict = rank_candidates([incumbent, cheap], min_quality=D("0.5"))
    rec.check(
        "FTF-1304-S1-K3",
        f"overlapping intervals retain the incumbent, got {verdict.status}",
        verdict.status == STATUS_INCONCLUSIVE_RETAIN_INCUMBENT,
    )
    sure = m.compound_ranking_candidate(
        "compound",
        cheap_attr,
        quality_lower_bound=D("0.9"),
        interval_low=D("0.05"),
        interval_high=D("0.5"),
    )
    verdict = rank_candidates([incumbent, sure], min_quality=D("0.5"))
    rec.check(
        "FTF-1304-S1-K3",
        f"a non-overlapping cheaper compound wins, got {verdict.status}",
        verdict.status == STATUS_WINNER and verdict.winner_id == "compound",
    )

    # ---- E: record/trace shape.
    nv = RouteIdentityState.VERIFIED
    ok_args: dict[str, Any] = dict(
        request_id="r",
        step_key=None,
        route_alias="a",
        identity_state=nv,
        deployment_fingerprint="f",
        usage=None,
        started_at=T0,
        ended_at=T0 + timedelta(seconds=1),
    )
    rec.check(
        "FTF-1304-S1-E1", "valid record builds", m.ModelCallRecord(**ok_args).route_alias == "a"
    )
    for field, bad in (
        ("request_id", ""),
        ("step_key", " "),
        ("route_alias", ""),
        ("identity_state", "verified"),
        ("deployment_fingerprint", ""),
        ("usage", object()),
        ("started_at", datetime(2026, 1, 1)),
        ("ended_at", T0),
        ("ended_at", T0 - timedelta(seconds=1)),
        ("ended_at", "later"),
    ):
        args: dict[str, Any] = dict(ok_args)
        args[field] = bad
        rec.check(
            "FTF-1304-S1-E1",
            f"ModelCallRecord refuses bad {field}={bad!r}",
            raises(m.CompoundAttributionError, lambda a=args: m.ModelCallRecord(**a)),
        )
    rec.check(
        "FTF-1304-S1-E1",
        "naive ended_at refused",
        raises(
            m.CompoundAttributionError,
            lambda: m.ModelCallRecord(
                **{
                    **ok_args,
                    "started_at": T0.replace(tzinfo=None),
                    "ended_at": T0.replace(tzinfo=None) + timedelta(seconds=1),
                }
            ),
        ),
    )
    foreign = call(m, "other", None, "large-api", usage_=usage())
    rec.check(
        "FTF-1304-S1-E2",
        "a call inside another request's trace refused",
        raises(m.CompoundAttributionError, lambda: m.AttemptTrace("mine", "pass", (foreign,))),
    )
    rec.check(
        "FTF-1304-S1-E2",
        "outcome vocabulary enforced",
        raises(m.CompoundAttributionError, lambda: m.AttemptTrace("mine", "great", ())),
    )
    rec.check(
        "FTF-1304-S1-E2",
        "calls must be ModelCallRecord",
        raises(m.CompoundAttributionError, lambda: m.AttemptTrace("mine", "pass", ("x",))),
    )
    for bad_pending in (D("-1"), D("NaN"), 1, "1"):
        rec.check(
            "FTF-1304-S1-E2",
            f"pending_cost {bad_pending!r} refused",
            raises(
                m.CompoundAttributionError,
                lambda p=bad_pending: m.AttemptTrace("mine", "pass", (), pending_cost=p),
            ),
        )
    rec.check(
        "FTF-1304-S1-E3",
        "window list entries typed",
        raises(m.CompoundAttributionError, lambda: attribute(binding, [], RATES, ["w"])),
    )
    rec.check(
        "FTF-1304-S1-E3",
        "duplicate window ids refused",
        raises(
            m.CompoundAttributionError,
            lambda: attribute(
                binding,
                [],
                RATES,
                [self_window(m, window(), "same"), self_window(m, window(), "same")],
            ),
        ),
    )
    rec.check(
        "FTF-1304-S1-E3",
        "attempts typed",
        raises(m.CompoundAttributionError, lambda: attribute(binding, ["a"], RATES, [])),
    )
    rec.check(
        "FTF-1304-S1-E3",
        "binding typed",
        raises(m.CompoundAttributionError, lambda: attribute("b", [], RATES, [])),
    )
    rec.check(
        "FTF-1304-S1-E3",
        "rate cards must be a mapping",
        raises(
            m.CompoundAttributionError,
            lambda: attribute(binding, [], [("large-api", LARGE_CARD)], []),
        ),
    )
    rec.check(
        "FTF-1304-S1-E3",
        "rate card values typed",
        raises(m.CompoundAttributionError, lambda: attribute(binding, [], {"large-api": 1}, [])),
    )
    rec.check(
        "FTF-1304-S1-E3",
        "rate card aliases checked",
        raises(m.CompoundAttributionError, lambda: attribute(binding, [], {"": LARGE_CARD}, [])),
    )
    rec.check(
        "FTF-1304-S1-E3",
        "empty window aliases refused",
        raises(m.CompoundAttributionError, lambda: m.SelfHostedWindow("w", (), window(), "USD")),
    )
    rec.check(
        "FTF-1304-S1-E3",
        "duplicate window aliases refused",
        raises(
            m.CompoundAttributionError, lambda: m.SelfHostedWindow("w", ("a", "a"), window(), "USD")
        ),
    )
    rec.check(
        "FTF-1304-S1-E3",
        "blank window alias refused",
        raises(
            m.CompoundAttributionError, lambda: m.SelfHostedWindow("w", ("a", " "), window(), "USD")
        ),
    )
    rec.check(
        "FTF-1304-S1-E3",
        "window must be a ServiceWindow",
        raises(m.CompoundAttributionError, lambda: m.SelfHostedWindow("w", ("a",), "x", "USD")),
    )
    rec.check(
        "FTF-1304-S1-E3",
        "only the proportional policy exists",
        raises(
            m.CompoundAttributionError,
            lambda: m.SelfHostedWindow("w", ("a",), window(), "USD", allocation_policy="even"),
        ),
    )
    naive_window = ServiceWindow(
        window_start=datetime(2026, 10, 1),
        window_end=datetime(2026, 10, 2),
        replica_count=1,
        total_billed_gpu_cpu_ram_cost=D("1"),
        storage_cost=D(0),
        network_cost=D(0),
        platform_allocation_cost=D(0),
    )
    rec.check(
        "FTF-1304-S1-E3",
        "naive service window refused",
        raises(
            m.CompoundAttributionError, lambda: m.SelfHostedWindow("w", ("a",), naive_window, "USD")
        ),
    )

    # ---- N: strict, case-sensitive identity of every id, alias and key.
    for bad in (" x", "x ", "\tx", " "):
        rec.check(
            "FTF-1304-S1-N1",
            f"identifier {bad!r} refused",
            raises(
                m.CompoundAttributionError,
                lambda b=bad: m.StepBinding(b, "r", "other", "provider", "base"),
            ),
        )
    for bad in (" classify", "classify ", "CLASSIFY", "Classify"):
        rec.check(
            "FTF-1304-S1-N1",
            f"step_kind {bad!r} refused (exact vocabulary)",
            raises(
                m.CompoundAttributionError,
                lambda b=bad: m.StepBinding("s", "r", b, "provider", "base"),
            ),
        )
    rec.check(
        "FTF-1304-S1-N1",
        "pricing_mode and artifact_kind are exact vocabulary",
        raises(
            m.CompoundAttributionError, lambda: m.StepBinding("s", "r", "other", "Provider", "base")
        )
        and raises(
            m.CompoundAttributionError,
            lambda: m.StepBinding("s", "r", "other", "provider", " base"),
        ),
    )
    rec.check(
        "FTF-1304-S1-N1",
        "outcome is exact vocabulary",
        raises(m.CompoundAttributionError, lambda: m.AttemptTrace("r", "PASS", ()))
        and raises(m.CompoundAttributionError, lambda: m.AttemptTrace("r", " pass", ())),
    )
    case_steps = m.CompoundBinding(
        "Large-API",
        "gateway",
        "provider",
        "existing",
        "reasoning",
        (
            m.StepBinding("Classify", "Small-Local", "classify", "self_hosted", "base"),
            m.StepBinding("classify", "small-local", "classify", "self_hosted", "base"),
        ),
    )
    rec.check(
        "FTF-1304-S1-N2",
        "step keys and aliases differing only in case are distinct",
        len(case_steps.steps) == 2 and len(case_steps.route_table()) == 3,
    )
    rec.check(
        "FTF-1304-S1-N2",
        "a lower-case step key followed by its upper-case twin is not a duplicate",
        len(
            m.CompoundBinding(
                "a",
                "p",
                "provider",
                "base",
                "reasoning",
                (
                    m.StepBinding("classify", "r1", "other", "provider", "base"),
                    m.StepBinding("Classify", "r2", "other", "provider", "base"),
                ),
            ).steps
        )
        == 2,
    )
    cases = [
        ("FTF-1304-S1-N3", "step key in another case", "CLASSIFY", "small-local"),
        ("FTF-1304-S1-N3", "alias in another case", "classify", "SMALL-LOCAL"),
    ]
    plain = small_binding(m)
    for cid, what, step_key, alias in cases:
        r = attribute(
            plain,
            [attempt(m, "n1", "pass", [call(m, "n1", step_key, alias)])],
            RATES,
            [self_window(m, window())],
        )
        rec.check(
            cid, f"{what} is a binding violation, got {r.status}", r.status == "binding_violation"
        )
    r = attribute(
        plain,
        [attempt(m, "n2", "pass", [call(m, "n2", None, "LARGE-API", usage_=usage())])],
        RATES,
        [],
    )
    rec.check(
        "FTF-1304-S1-N3",
        "default alias in another case is a binding violation",
        r.status == "binding_violation",
    )
    upper = m.CompoundBinding("Large-API", "gateway", "provider", "existing", "reasoning", ())
    r = attribute(
        upper,
        [attempt(m, "n3", "pass", [call(m, "n3", None, "large-api", usage_=usage())])],
        {"Large-API": LARGE_CARD},
        [],
    )
    rec.check(
        "FTF-1304-S1-N3",
        "a mixed-case default alias is matched exactly",
        r.status == "binding_violation",
    )
    r = attribute(
        upper,
        [attempt(m, "n4", "pass", [call(m, "n4", None, "Large-API", usage_=usage())])],
        {"Large-API": LARGE_CARD},
        [],
    )
    rec.check("FTF-1304-S1-N3", "...and matches when spelled exactly", r.status == "priced")
    mixed_self = m.CompoundBinding(
        "large-api",
        "gateway",
        "provider",
        "existing",
        "reasoning",
        (m.StepBinding("classify", "Small-Local", "classify", "self_hosted", "base"),),
    )
    lowered = m.SelfHostedWindow("wl", ("small-local",), window(), "USD")
    r = attribute(
        mixed_self,
        [attempt(m, "n5", "pass", [call(m, "n5", "classify", "Small-Local")])],
        RATES,
        [lowered],
    )
    refused(rec, "FTF-1304-S1-N4", m, r, "unpriced", "no_window", "n5", "Small-Local")
    r = attribute(
        plain,
        [attempt(m, "n6", "pass", [call(m, "n6", "classify", "small-local")])],
        RATES,
        [m.SelfHostedWindow("wu", ("SMALL-LOCAL",), window(), "USD")],
    )
    refused(rec, "FTF-1304-S1-N4", m, r, "unpriced", "no_window", "n6", "small-local")
    foreign_case = (call(m, "R1", None, "large-api", usage_=usage()), "r1")
    rec.check(
        "FTF-1304-S1-N5",
        "a call for 'R1' inside the trace of 'r1' refused",
        raises(
            m.CompoundAttributionError,
            lambda: m.AttemptTrace(foreign_case[1], "pass", (foreign_case[0],)),
        ),
    )
    rec.check(
        "FTF-1304-S1-N5",
        "a call for 'r1' inside the trace of 'R1' refused",
        raises(
            m.CompoundAttributionError,
            lambda: m.AttemptTrace(
                "R1", "pass", (call(m, "r1", None, "large-api", usage_=usage()),)
            ),
        ),
    )
    zero_pending = m.AttemptTrace("r", "pass", (), pending_cost=D("0"))
    rec.check("FTF-1304-S1-N5", "a zero pending cost is allowed", zero_pending.pending_cost == D(0))
    n6_cases: tuple[tuple[str, Any], ...] = (
        ("set", {pair(m, "u1")}),
        ("generator", (x for x in [pair(m, "u2")])),
        ("dict", {"a": 1}),
    )
    for what, value in n6_cases:
        rec.check(
            "FTF-1304-S1-N6",
            f"attempts as a {what} refused",
            raises(m.CompoundAttributionError, lambda v=value: attribute(plain, v, RATES, [])),
        )
    rec.check(
        "FTF-1304-S1-N6",
        "steps as a set refused",
        raises(
            m.CompoundAttributionError,
            lambda: m.CompoundBinding(
                "a",
                "p",
                "provider",
                "base",
                "reasoning",
                {m.StepBinding("s", "r", "other", "provider", "base")},
            ),
        ),
    )
    rec.check(
        "FTF-1304-S1-N6",
        "calls as a set refused",
        raises(
            m.CompoundAttributionError,
            lambda: m.AttemptTrace("r", "pass", {call(m, "r", None, "large-api", usage_=usage())}),
        ),
    )
    rec.check(
        "FTF-1304-S1-N6",
        "windows as a generator refused",
        raises(m.CompoundAttributionError, lambda: attribute(plain, [], RATES, iter(()))),
    )
    rec.check(
        "FTF-1304-S1-N6",
        "a list of steps is accepted and frozen into a tuple",
        m.CompoundBinding(
            "a",
            "p",
            "provider",
            "base",
            "reasoning",
            [m.StepBinding("s", "r", "other", "provider", "base")],
        )
        .steps[0]
        .step_key
        == "s",
    )
    rec.check(
        "FTF-1304-S1-N6",
        "a string is not a sequence of aliases",
        raises(
            m.CompoundAttributionError,
            lambda: m.SelfHostedWindow("w", "small-local", window(), "USD"),
        ),
    )
    for bad_policy in ("PROPORTIONAL_TO_CALLS", " proportional_to_calls", "proportional_to_calls "):
        rec.check(
            "FTF-1304-S1-N7",
            f"allocation policy {bad_policy!r} refused",
            raises(
                m.CompoundAttributionError,
                lambda p=bad_policy: m.SelfHostedWindow(
                    "w", ("a",), window(), "USD", allocation_policy=p
                ),
            ),
        )
    exact_start = [attempt(m, "n8", "pass", [call(m, "n8", "classify", "small-local", minute=0)])]
    r = attribute(plain, exact_start, RATES, [self_window(m, window())])
    rec.check(
        "FTF-1304-S1-N8", "a call starting exactly at window_start is inside", r.status == "priced"
    )
    just_before = [attempt(m, "n9", "pass", [call(m, "n9", "classify", "small-local", minute=0)])]
    r = attribute(
        plain, just_before, RATES, [self_window(m, window(start=T0 + timedelta(seconds=1)))]
    )
    refused(rec, "FTF-1304-S1-N8", m, r, "unpriced", "outside_window_range", "n9", "small-local")
    # a refused attribution that still carries costs (a hand-built or tampered one) cannot rank
    tampered = dataclasses.replace(cheap_attr, status=m.AttributionStatus.UNPRICED)
    rec.check(
        "FTF-1304-S1-N9",
        "only a priced attribution ranks, whatever it carries",
        raises(
            m.CompoundAttributionError,
            lambda: m.compound_ranking_candidate(
                "t",
                tampered,
                quality_lower_bound=D("0.9"),
                interval_low=D("0.05"),
                interval_high=D("0.5"),
            ),
        ),
    )
    plain_text = dataclasses.replace(cheap_attr, status="PRICED")
    rec.check(
        "FTF-1304-S1-N9",
        "a status spelled 'PRICED' is not priced",
        raises(
            m.CompoundAttributionError,
            lambda: m.compound_ranking_candidate(
                "t",
                plain_text,
                quality_lower_bound=D("0.9"),
                interval_low=D("0.05"),
                interval_high=D("0.5"),
            ),
        ),
    )
    rec.check(
        "FTF-1304-S1-N9",
        "status values are the documented strings",
        [s.value for s in m.AttributionStatus]
        == ["priced", "binding_violation", "identity_unverified", "currency_mismatch", "unpriced"],
    )

    # ---- R2: currency (Section 32 "decimal arithmetic with currency") and the declared default step kind.
    jpy_card = ProviderRateCard(
        currency="JPY", input_token_rate=D("0.0001"), output_token_rate=D("0.0005")
    )
    eur_card = ProviderRateCard(
        currency="EUR", input_token_rate=D("0.000001"), output_token_rate=D("0.000005")
    )
    mid_binding = m.CompoundBinding(
        "large-api",
        "gateway",
        "provider",
        "existing",
        "reasoning",
        (m.StepBinding("extract", "mid-api", "extract", "provider", "base"),),
    )

    def mid_attempts(prefix: str, n: int = 2) -> list[Any]:
        return [
            attempt(
                m,
                f"{prefix}{i}",
                "pass",
                [
                    call(m, f"{prefix}{i}", "extract", "mid-api", usage_=usage(), minute=i + 1),
                    call(m, f"{prefix}{i}", None, "large-api", usage_=usage(), minute=i + 3),
                ],
            )
            for i in range(n)
        ]

    def mismatch_refused(cid: str, result: Any, routes: set[str]) -> None:
        rec.check(
            cid,
            f"status currency_mismatch, got {result.status!r}",
            result.status == "currency_mismatch",
        )
        rec.check(cid, "no attempt costs on a currency mismatch", not result.attempt_costs)
        rec.check(cid, "no route lines on a currency mismatch", not result.route_lines)
        rec.check(cid, "no step-kind totals on a currency mismatch", not result.step_kind_totals)
        rec.check(cid, "no single currency is reported", result.currency is None)
        named = {i.route_alias for i in result.issues if i.code == "currency_mismatch"}
        rec.check(cid, f"issues name every route involved {routes}, got {named}", named == routes)
        rec.check(
            cid,
            "issue status",
            all(
                i.status == "currency_mismatch"
                for i in result.issues
                if i.code == "currency_mismatch"
            ),
        )

    usd_jpy = attribute(
        mid_binding, mid_attempts("cu"), {"large-api": LARGE_CARD, "mid-api": jpy_card}, []
    )
    mismatch_refused("FTF-1304-S1-CUR1", usd_jpy, {"large-api", "mid-api"})
    usd_eur = attribute(
        mid_binding, mid_attempts("ce"), {"large-api": LARGE_CARD, "mid-api": eur_card}, []
    )
    mismatch_refused("FTF-1304-S1-CUR1", usd_eur, {"large-api", "mid-api"})
    rec.check(
        "FTF-1304-S1-CUR1",
        "a refused mismatch cannot become a ranking candidate",
        raises(
            m.CompoundAttributionError,
            lambda: m.compound_ranking_candidate(
                "c",
                usd_jpy,
                quality_lower_bound=D("0.9"),
                interval_low=D("0.05"),
                interval_high=D("0.5"),
            ),
        ),
    )
    reordered = attribute(
        mid_binding,
        list(reversed(mid_attempts("cu"))),
        {"mid-api": jpy_card, "large-api": LARGE_CARD},
        [],
    )
    rec.check(
        "FTF-1304-S1-CUR1",
        "a mismatch verdict does not depend on input order",
        reordered.attribution_sha256 == usd_jpy.attribution_sha256,
    )

    eur_window = m.SelfHostedWindow("weur", ("small-local",), window("7", "0", "0"), "EUR")
    provider_usd_window_eur = attribute(small_binding(m), [pair(m, "cw")], RATES, [eur_window])
    mismatch_refused("FTF-1304-S1-CUR2", provider_usd_window_eur, {"large-api", "small-local"})
    usd_window = m.SelfHostedWindow("wusd", ("small-local",), window("7", "0", "0"), "USD")
    all_usd = attribute(small_binding(m), [pair(m, "cw")], RATES, [usd_window])
    rec.check("FTF-1304-S1-CUR3", "all-USD attribution is priced", all_usd.status == "priced")
    rec.check(
        "FTF-1304-S1-CUR3",
        f"the single currency is exposed: {all_usd.currency!r}",
        all_usd.currency == "USD",
    )
    jpy_window = m.SelfHostedWindow("wjpy", ("small-local",), window("7", "0", "0"), "JPY")
    jpy_attempts = [
        attempt(
            m,
            "cj",
            "pass",
            [
                call(m, "cj", "classify", "small-local", minute=1),
                call(m, "cj", None, "large-api", usage_=usage(), minute=2),
            ],
        )
    ]
    all_jpy = attribute(small_binding(m), jpy_attempts, {"large-api": jpy_card}, [jpy_window])
    usd_twin = attribute(small_binding(m), jpy_attempts, {"large-api": jpy_card}, [usd_window])
    rec.check(
        "FTF-1304-S1-CUR3",
        f"an all-JPY attribution is priced in JPY: {all_jpy.status!r} {all_jpy.currency!r}",
        all_jpy.status == "priced" and all_jpy.currency == "JPY",
    )
    rec.check(
        "FTF-1304-S1-CUR3",
        "JPY provider + USD window is a mismatch, JPY provider + JPY window is not",
        usd_twin.status == "currency_mismatch" and all_jpy.status == "priced",
    )
    # Same numbers, different currency: the hash must tell them apart.
    jpy_same = attribute(
        small_binding(m),
        [pair(m, "cw")],
        {
            "large-api": ProviderRateCard(
                "JPY", LARGE_CARD.input_token_rate, LARGE_CARD.output_token_rate
            )
        },
        [m.SelfHostedWindow("wusd", ("small-local",), window("7", "0", "0"), "JPY")],
    )
    rec.check(
        "FTF-1304-S1-CUR3",
        "identical amounts in USD and JPY have different attribution hashes",
        jpy_same.status == "priced"
        and jpy_same.currency == "JPY"
        and [a.settled_cost for a in jpy_same.attempt_costs]
        == [a.settled_cost for a in all_usd.attempt_costs]
        and jpy_same.attribution_sha256 != all_usd.attribution_sha256,
    )
    # A rate card or window that no call used cannot taint a priced total.
    unused = attribute(
        small_binding(m),
        [pair(m, "cw")],
        {"large-api": LARGE_CARD, "never-called": jpy_card},
        [usd_window, m.SelfHostedWindow("wother", ("elsewhere",), window("1", "0", "0"), "EUR")],
    )
    rec.check(
        "FTF-1304-S1-CUR4",
        "an unused JPY rate card does not refuse a USD attribution (the unused EUR window is already unpriced)",
        unused.status == "unpriced"
        and not any(i.code == "currency_mismatch" for i in unused.issues),
    )
    unused_card_only = attribute(
        small_binding(m),
        [pair(m, "cw")],
        {"large-api": LARGE_CARD, "never-called": jpy_card},
        [usd_window],
    )
    rec.check(
        "FTF-1304-S1-CUR4",
        "an unused JPY rate card alone leaves a USD attribution priced",
        unused_card_only.status == "priced" and unused_card_only.currency == "USD",
    )
    rec.check(
        "FTF-1304-S1-CUR5",
        "a window needs a currency",
        raises(TypeError, lambda: m.SelfHostedWindow("w", ("a",), window())),
    )
    for bad_currency in ("", " ", " USD", 3, None):
        rec.check(
            "FTF-1304-S1-CUR5",
            f"window currency {bad_currency!r} refused",
            raises(
                m.CompoundAttributionError,
                lambda c=bad_currency: m.SelfHostedWindow("w", ("a",), window(), c),
            ),
        )

    # ---- R2: the default route's step kind is declared, never inferred.
    classify_default = m.CompoundBinding(
        "large-api",
        "gateway",
        "provider",
        "existing",
        "classify",
        (m.StepBinding("extract", "small-local", "extract", "self_hosted", "base"),),
    )
    default_attempt = [
        attempt(
            m,
            "dk",
            "pass",
            [
                call(m, "dk", "extract", "small-local", minute=1),
                call(m, "dk", None, "large-api", usage_=usage(), minute=2),
            ],
        )
    ]
    declared = attribute(
        classify_default, default_attempt, RATES, [self_window(m, window("7", "0", "0"))]
    )
    declared_lines = {(ln.route_alias, ln.step_kind) for ln in declared.route_lines}
    rec.check(
        "FTF-1304-S1-DSK1",
        f"the default route is labelled with its declared kind: {declared_lines}",
        declared.status == "priced"
        and declared_lines == {("large-api", "classify"), ("small-local", "extract")},
    )
    rec.check(
        "FTF-1304-S1-DSK1",
        "no 'reasoning' total appears when none was declared",
        "reasoning" not in dict(declared.step_kind_totals),
    )
    for kind_name in m.STEP_KINDS:
        labelled = m.CompoundBinding("a", "p", "provider", "base", kind_name, ())
        rec.check(
            "FTF-1304-S1-DSK1",
            f"default_step_kind {kind_name!r} accepted",
            labelled.default_step_kind == kind_name,
        )
    for bad_kind in ("guess", "Reasoning", " reasoning", "", None, 3):
        rec.check(
            "FTF-1304-S1-DSK2",
            f"default_step_kind {bad_kind!r} refused",
            raises(
                m.CompoundAttributionError,
                lambda k=bad_kind: m.CompoundBinding("a", "p", "provider", "base", k, ()),
            ),
        )
    rec.check(
        "FTF-1304-S1-DSK2",
        "default_step_kind is required",
        raises(TypeError, lambda: m.CompoundBinding("a", "p", "provider", "base", ())),
    )
    hashes = {
        m.CompoundBinding("a", "p", "provider", "base", k, ()).binding_sha256 for k in m.STEP_KINDS
    }
    rec.check(
        "FTF-1304-S1-DSK3",
        "binding_sha256 changes with the default step kind",
        len(hashes) == len(m.STEP_KINDS),
    )
    default_kind_changed = attribute(
        small_binding(m),
        [pair(m, "dk")],
        RATES,
        [self_window(m, window("7", "0", "0"))],
    )
    rec.check(
        "FTF-1304-S1-DSK3",
        "the same calls under a different declared default kind hash differently",
        default_kind_changed.status == "priced"
        and default_kind_changed.binding_sha256 != declared.binding_sha256
        and default_kind_changed.attribution_sha256 != declared.attribution_sha256,
    )

    # ---- O: tie-break and ordering are independent of input order.
    twin_binding = m.CompoundBinding(
        "large-api",
        "gateway",
        "provider",
        "existing",
        "reasoning",
        (
            m.StepBinding("classify", "small-local", "classify", "self_hosted", "base"),
            m.StepBinding("extract", "small-local", "extract", "self_hosted", "base"),
        ),
    )

    def twin(
        rid: str,
        outcome: str,
        step: str,
        *,
        pending: str | None = None,
        fp: str = "fp-1",
        u: ProviderTokenUsage | None = None,
    ) -> Any:
        return attempt(
            m,
            rid,
            outcome,
            [call(m, rid, step, "small-local", fingerprint=fp, usage_=u)],
            pending=pending,
        )

    twin_sets = {
        "request_id": [twin("pa", "pass", "classify"), twin("pb", "pass", "classify")],
        "outcome": [twin("p", "pass", "classify"), twin("p", "model_fail", "classify")],
        "pending_cost": [
            twin("p", "pass", "classify", pending="1"),
            twin("p", "pass", "classify", pending="2"),
        ],
        "pending_none": [twin("p", "pass", "classify"), twin("p", "pass", "classify", pending="2")],
        "step_key": [twin("p", "pass", "classify"), twin("p", "pass", "extract")],
        "fingerprint": [
            twin("p", "pass", "classify", fp="fp-a"),
            twin("p", "pass", "classify", fp="fp-b"),
        ],
        "usage": [
            twin("p", "pass", "classify", u=usage(1, 1)),
            twin("p", "pass", "classify", u=usage(2, 2)),
        ],
    }
    twin_window = [self_window(m, window("0.01", "0", "0"))]
    for field, pair_ in twin_sets.items():
        forward = attribute(twin_binding, pair_, {}, twin_window)
        backward = attribute(twin_binding, list(reversed(pair_)), {}, twin_window)
        rec.check(
            "FTF-1304-S1-O1",
            f"attempts differing only in {field}: same attempt_costs in either input order",
            forward.status == "priced" and forward.attempt_costs == backward.attempt_costs,
        )
        rec.check(
            "FTF-1304-S1-O1",
            f"attempts differing only in {field}: same hash in either input order",
            forward.attribution_sha256 == backward.attribution_sha256,
        )
    two_calls = [
        call(m, "p", "classify", "small-local", minute=1),
        call(m, "p", "classify", "small-local", minute=2),
    ]
    ordered_a = attribute(twin_binding, [attempt(m, "p", "pass", two_calls)], {}, twin_window)
    ordered_b = attribute(
        twin_binding, [attempt(m, "p", "pass", list(reversed(two_calls)))], {}, twin_window
    )
    rec.check(
        "FTF-1304-S1-O1",
        "a trace's call order does not matter",
        ordered_a.attempt_costs == ordered_b.attempt_costs,
    )
    split = attribute(
        plain,
        [
            attempt(
                m,
                "p",
                "pass",
                [
                    call(m, "p", "classify", "small-local", minute=1),
                    call(m, "p", "classify", "small-local", minute=1, seconds=31),
                ],
            )
        ],
        {},
        twin_window,
    )
    rec.check(
        "FTF-1304-S1-O1",
        "two calls differing only in end time both priced",
        split.status == "priced"
        and sum((a.settled_cost for a in split.attempt_costs), D(0)) == D("0.01"),
    )
    issue_order = attribute(
        plain,
        [
            attempt(m, "b", "pass", [call(m, "b", None, "large-api")]),
            attempt(m, "a", "pass", [call(m, "a", None, "large-api")]),
        ],
        RATES,
        [],
    )
    rec.check(
        "FTF-1304-S1-O2",
        "issues are sorted regardless of input order",
        [i.request_id for i in issue_order.issues] == ["a", "b"],
    )
    rec.check(
        "FTF-1304-S1-O2",
        "issues are tuples of typed records",
        all(isinstance(i, m.CompoundIssue) for i in issue_order.issues)
        and isinstance(issue_order.issues, tuple),
    )

    # ---- D: determinism -- shuffled order, same hashes.
    reference = attribute(
        binding,
        [pair(m, "d1", minute=1), pair(m, "d2", minute=7), pair(m, "d3", "model_fail", minute=13)],
        RATES,
        [self_window(m, window("7", "0", "0"))],
    )
    orders = list(
        itertools.permutations(
            [
                pair(m, "d1", minute=1),
                pair(m, "d2", minute=7),
                pair(m, "d3", "model_fail", minute=13),
            ]
        )
    )
    rec.check(
        "FTF-1304-S1-D1",
        "attempt order does not change the hash",
        all(
            attribute(
                binding, list(o), RATES, [self_window(m, window("7", "0", "0"))]
            ).attribution_sha256
            == reference.attribution_sha256
            for o in orders
        ),
    )
    rng = random.Random(7)
    calls_ = [
        call(m, "d9", "classify", "small-local", minute=2),
        call(m, "d9", None, "large-api", usage_=usage(), minute=3),
        call(m, "d9", "extract", "small-local", minute=4),
    ]
    ref9 = attribute(binding, [attempt(m, "d9", "pass", calls_)], RATES, [self_window(m, window())])
    stable = True
    for _ in range(6):
        rng.shuffle(calls_)
        stable = (
            stable
            and attribute(
                binding, [attempt(m, "d9", "pass", list(calls_))], RATES, [self_window(m, window())]
            ).attribution_sha256
            == ref9.attribution_sha256
        )
    rec.check("FTF-1304-S1-D1", "call order does not change the hash", stable)
    changed = attribute(
        binding, [pair(m, "d1", minute=1)], RATES, [self_window(m, window("7", "0", "0"))]
    )
    rec.check(
        "FTF-1304-S1-D1",
        "a different input changes the hash",
        changed.attribution_sha256 != reference.attribution_sha256,
    )
    other_binding = attribute(
        small_binding(m, "tuned"),
        [pair(m, "d1", minute=1)],
        RATES,
        [self_window(m, window("7", "0", "0"))],
    )
    rec.check(
        "FTF-1304-S1-D1",
        "the binding is bound into the attribution hash",
        other_binding.attribution_sha256 != changed.attribution_sha256,
    )
    pend1 = attribute(
        binding,
        [attempt(m, "e1", "pass", [call(m, "e1", None, "large-api", usage_=usage())], pending="1")],
        RATES,
        [],
    )
    pend2 = attribute(
        binding,
        [attempt(m, "e1", "pass", [call(m, "e1", None, "large-api", usage_=usage())], pending="2")],
        RATES,
        [],
    )
    rec.check(
        "FTF-1304-S1-D1",
        "pending cost is bound into the attribution hash",
        pend1.attribution_sha256 != pend2.attribution_sha256,
    )
    refusal_a = attribute(
        binding, [attempt(m, "f1", "pass", [call(m, "f1", None, "large-api")])], RATES, []
    )
    refusal_b = attribute(
        binding, [attempt(m, "f2", "pass", [call(m, "f2", None, "large-api")])], RATES, []
    )
    rec.check(
        "FTF-1304-S1-D1",
        "a refusal's hash names its issues",
        refusal_a.attribution_sha256 != refusal_b.attribution_sha256,
    )

    if slow:
        digests = set()
        for seed in ("1", "424242"):
            proc = subprocess.run(
                [sys.executable, str(Path(__file__).resolve()), "--print-hashes"],
                env={**os.environ, "PYTHONHASHSEED": seed},
                capture_output=True,
                text=True,
                check=True,
            )
            digests.add(proc.stdout.strip())
        rec.check(
            "FTF-1304-S1-D2",
            f"same hashes under two PYTHONHASHSEED values: {digests}",
            len(digests) == 1 and all(len(x) > 100 for x in digests),
        )


# --------------------------------------------------------------------------- the fault table
FAULTS: list[tuple[str, str, str, str]] = [
    (
        "F-STEP-CALLS-DROPPED",
        "calls with a step_key dropped (only the default route priced)",
        "            call = _Call(index, record, step_kind)\n",
        "            if record.step_key is not None:\n                continue\n            call = _Call(index, record, step_kind)\n",
    ),
    (
        "F-MISSING-USAGE-ZERO",
        "missing usage priced as 0",
        "    if record.usage is None or card is None:\n",
        "    if record.usage is None:\n        return Decimal(0)\n    if card is None:\n",
    ),
    (
        "F-UNKNOWN-ROUTE-IGNORED",
        "unknown route alias ignored",
        "            if record.route_alias != bound_alias or record.route_alias not in routes:\n",
        "            if False:\n",
    ),
    (
        "F-IDENTITY-SKIPPED",
        "identity check skipped",
        "                record.identity_state is not RouteIdentityState.VERIFIED\n                or record.deployment_fingerprint is None\n",
        "                False\n",
    ),
    (
        "F-NO-WINDOW-FREE",
        "self-hosted call without a window priced at 0",
        "        issues.append(CompoundIssue(STATUS_UNPRICED, code, record.request_id, record.route_alias))\n    for window in windows:\n",
        "        call.cost = Decimal(0)\n    for window in windows:\n",
    ),
    (
        "F-SHARED-WINDOW-DOUBLE-COUNT",
        "shared window allocated per route (double count)",
        "        shares = _allocate(window_total_cost(window.window), len(members))\n        for member, share in zip(members, shares, strict=True):\n            member.cost = share\n",
        "        for alias in window.route_aliases:\n            group = [x for x in members if x.record.route_alias == alias]\n            if group:\n                for member, share in zip(\n                    group, _allocate(window_total_cost(window.window), len(group)), strict=True\n                ):\n                    member.cost = share\n",
    ),
    (
        "F-RESIDUAL-DROPPED",
        "residual dropped (sum != window total)",
        "        shares.append(total - share * Decimal(count - 1))\n",
        "        shares.append(share)\n",
    ),
    (
        "F-STEP-KIND-TOTALS-WRONG",
        "step-kind totals not equal to attempt total",
        "        totals[line.step_kind] = totals.get(line.step_kind, Decimal(0)) + line.cost\n",
        "        totals[line.step_kind] = line.cost\n",
    ),
    (
        "F-FAILED-ATTEMPTS-EXCLUDED",
        "failed attempts excluded",
        "    for index, attempt in enumerate(ordered):\n",
        '    for index, attempt in enumerate(ordered):\n        if attempt.outcome != "pass":\n            continue\n',
    ),
    (
        "F-BINDING-HASH-NO-STEPS",
        "binding_sha256 excludes the step map",
        'document["steps"] = sorted(document["steps"], key=lambda step: step["step_key"])',
        'document["steps"] = []',
    ),
    (
        "F-TUNED-AS-BASE",
        "tuned step reported as base (amortization skipped)",
        '        if "tuned" in kinds:\n            return "tuned"\n',
        '        if "tuned" in kinds:\n            return "base"\n',
    ),
    (
        "F-OVERRIDE-NOT-VALIDATED",
        "validate_model_binding_override not called",
        "        return validate_model_binding_override(describe_result, self.to_model_binding())\n",
        "        return self.to_model_binding()\n",
    ),
    (
        "F-CURRENCY-CHECK-DROPPED",
        "mixed currencies summed into one total",
        "    if len(aliases_by_currency) > 1:\n",
        "    if False:\n",
    ),
    (
        "F-CURRENCY-PRECEDENCE-DROPPED",
        "currency_mismatch missing from the status precedence",
        "    STATUS_CURRENCY_MISMATCH,\n    STATUS_UNPRICED,\n)",
        "    STATUS_UNPRICED,\n)",
    ),
    (
        "F-PROVIDER-CURRENCY-IGNORED",
        "rate card currency never read",
        "        call.currency = card.currency\n",
        "        pass\n",
    ),
    (
        "F-WINDOW-CURRENCY-IGNORED",
        "window currency never read",
        "            member.currency = window.currency\n",
        "            pass\n",
    ),
    (
        "F-CURRENCY-NOT-IN-HASH",
        "currency left out of attribution_sha256",
        '            "currency": currency,\n',
        "",
    ),
    (
        "F-CURRENCY-NOT-EXPOSED",
        "single currency not exposed on the attribution",
        "        currency=currency if status is STATUS_PRICED else None,\n",
        "        currency=None,\n",
    ),
    (
        "F-DEFAULT-STEP-KIND-HARDCODED",
        "default-route calls labelled with a hard-coded kind",
        "                step_kind = binding.default_step_kind\n",
        '                step_kind = "reasoning"\n',
    ),
    (
        "F-DEFAULT-STEP-KIND-NOT-IN-HASH",
        "default step kind left out of binding_sha256",
        "        document = asdict(self)\n",
        '        document = asdict(self)\n        document.pop("default_step_kind")\n',
    ),
    (
        "F-DEFAULT-STEP-KIND-UNVALIDATED",
        "default step kind not validated",
        '        _choice(self.default_step_kind, STEP_KINDS, "default_step_kind")\n',
        "",
    ),
]


def print_hashes() -> None:
    m = real_module
    binding = small_binding(m)
    result = m.attribute_compound_costs(
        binding,
        [pair(m, "h1"), pair(m, "h2", minute=9)],
        RATES,
        [self_window(m, window("7", "0", "0"))],
    )
    refusal = m.attribute_compound_costs(
        binding, [attempt(m, "h3", "pass", [call(m, "h3", None, "large-api")])], RATES, []
    )
    print(binding.binding_sha256, result.attribution_sha256, refusal.attribution_sha256)


def main() -> None:
    if "--print-hashes" in sys.argv:
        print_hashes()
        return

    rec = Recorder()
    suite(real_module, rec, slow=True)
    FAILURES.extend(rec.failed)

    table: list[str] = []
    for fault_id, description, old, new in FAULTS:
        mutant_rec = Recorder()
        variant = load_variant(old, new)  # an anchor that no longer matches is a test bug
        try:
            suite(variant, mutant_rec, slow=False)
        except Exception as error:  # an uncaught exception is a behavioural difference too
            mutant_rec.failed.append(f"FAIL [crash:{type(error).__name__}] {fault_id}")
        if not mutant_rec.failed:
            FAILURES.append(f"FAIL [FTF-1304-S1-FAULT] {fault_id} ({description}) stayed GREEN")
        else:
            caught_by = sorted({line.split("]")[0].split("[")[1] for line in mutant_rec.failed})
            table.append(f"  {fault_id}: RED via {', '.join(caught_by[:4])}")

    if "--table" in sys.argv:
        print("\n".join(table))

    if FAILURES:
        print(f"FTF-1304-S1 Compound binding cost attribution tests: {len(FAILURES)} failed")
        for failure in FAILURES:
            print(f"  {failure}")
        sys.exit(1)

    print("FTF-1304-S1 Compound binding cost attribution tests: all checks passed")
    sys.exit(0)


if __name__ == "__main__":
    main()
