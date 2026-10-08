#!/usr/bin/env python3
"""FTF-1305 -- Candidate ranking (no-DB unit/fault tests).

Runnable without Postgres or network egress -- this task adds no schema
(`database_changes`: "No independent schema change"; `contract_owner`:
FTF-1301). Clause -> test mapping:

* FTF-1305-A1 (acceptance: rank eligible existing/base/tuned candidates by
  measured/estimated economics and uncertainty): ranking order, kinds,
  interval uncertainty and the measured/estimated flag, construction guards,
  and composition with FTF-1304's `CostPerSuccessResult`.
* FTF-1305-N1 (negative_acceptance: parameter count alone or a low-quality
  cheap model cannot determine the winner): quality floor, unmeasured
  quality, parameter-count independence, tuned-candidate training
  amortization, unpriced and ineligible exclusion, overlap tie rule.

Run directly: python3 tests/tasks/test_ftf_1305.py
"""

from __future__ import annotations

import sys
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
API_ROOT = ROOT / "apps" / "api"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(API_ROOT))

from app.services.candidate_ranking import (  # noqa: E402
    CANDIDATE_KINDS,
    CandidateRankingError,
    CostInterval,
    RankingCandidate,
    RankingResult,
    rank_candidates,
)
from app.services.cost_per_success_calculator import (  # noqa: E402
    AttemptCost,
    CostPerSuccessResult,
    cost_per_success,
)

FAILURES: list[str] = []
D = Decimal


def check(criterion_id: str, description: str, condition: bool) -> None:
    if not condition:
        FAILURES.append(f"FAIL [{criterion_id}] {description}")


def _rejects(fn, *args: object, **kwargs: object) -> bool:
    try:
        fn(*args, **kwargs)
    except CandidateRankingError:
        return True
    return False


def interval(low: str, base: str, high: str, *, amortized: bool = False) -> CostInterval:
    return CostInterval(
        low=D(low), base=D(base), high=D(high), includes_training_amortization=amortized
    )


def cand(
    cid: str,
    kind: str = "base",
    *,
    cost: CostInterval | None = None,
    quality: str | None = "0.90",
    eligible: bool = True,
    incumbent: bool = False,
    basis: str = "measured",
    params: int | None = None,
    reason: str | None = None,
) -> RankingCandidate:
    return RankingCandidate(
        candidate_id=cid,
        kind=kind,
        eligible=eligible,
        quality_lower_bound=None if quality is None else D(quality),
        cost=cost,
        economics_basis=basis,
        is_incumbent=incumbent,
        ineligibility_reason=reason,
        parameter_count=params,
    )


FLOOR = D("0.80")


def test_clear_winner_and_order() -> None:
    result = rank_candidates(
        [
            cand("c", "existing", cost=interval("0.50", "0.60", "0.70")),
            cand("a", "base", cost=interval("0.10", "0.12", "0.14")),
            cand("b", "tuned", cost=interval("0.30", "0.35", "0.40", amortized=True)),
        ],
        min_quality=FLOOR,
    )
    check(
        "FTF-1305-A1",
        "cheapest qualifying candidate wins when no interval overlaps",
        result.status == "WINNER" and result.winner_id == "a",
    )
    check(
        "FTF-1305-A1",
        "candidates are ordered by base cost per success",
        [r.candidate_id for r in result.ranked] == ["a", "b", "c"]
        and [r.rank for r in result.ranked] == [1, 2, 3],
    )
    check(
        "FTF-1305-A1",
        "the result claims only a lowest-cost qualifying candidate among those "
        "tested, never a global optimum",
        result.claim == "lowest-cost qualifying candidate among those tested under this workload",
    )
    check(
        "FTF-1305-A1",
        "uncertainty is carried through to every ranked row",
        result.ranked[0].cost.low == D("0.10") and result.ranked[0].cost.high == D("0.14"),
    )
    check(
        "FTF-1305-A1",
        "all three kinds are accepted",
        {r.kind for r in result.ranked} == {"base", "tuned", "existing"},
    )


def test_estimated_and_provisional_economics_surface() -> None:
    estimated = rank_candidates(
        [cand("a", cost=interval("1", "2", "3"), basis="estimated")], min_quality=FLOOR
    )
    check(
        "FTF-1305-A1",
        "an estimated-basis winner is flagged as estimated",
        estimated.winner_id == "a" and estimated.winner_economics_estimated is True,
    )
    measured = rank_candidates([cand("a", cost=interval("1", "2", "3"))], min_quality=FLOOR)
    check(
        "FTF-1305-A1",
        "a measured-basis winner is not flagged estimated",
        measured.winner_economics_estimated is False
        and measured.winner_economics_provisional is False,
    )
    pending = CostInterval(low=D("1"), base=D("2"), high=D("3"), has_pending_reconciliation=True)
    provisional = rank_candidates([cand("a", cost=pending)], min_quality=FLOOR)
    check(
        "FTF-1305-A1",
        "a winner with unsettled charges is flagged provisional",
        provisional.winner_economics_provisional is True,
    )


def test_cost_interval_from_cost_per_success_results() -> None:
    def scenario(cost: str, pending: str | None = None) -> CostPerSuccessResult:
        return cost_per_success(
            [
                AttemptCost(
                    request_id="r1",
                    outcome="pass",
                    settled_cost=D(cost),
                    pending_cost=None if pending is None else D(pending),
                )
            ],
            training_and_qualification_cost=D("10"),
            approved_expected_lifetime_successes=100,
        )

    best, expected, worst = scenario("1"), scenario("2", "1"), scenario("4")
    plain = CostInterval.from_scenario_results(best_case=best, expected=expected, worst_case=worst)
    check(
        "FTF-1305-A1",
        "interval built from FTF-1304 results uses cost_per_success",
        (plain.low, plain.base, plain.high) == (D("1"), D("3"), D("4"))
        and plain.includes_training_amortization is False
        and plain.has_pending_reconciliation is True,
    )
    amortized = CostInterval.from_scenario_results(
        best_case=best, expected=expected, worst_case=worst, amortized=True
    )
    check(
        "FTF-1305-A1",
        "amortized=True uses amortized_cost_per_success (training cost added)",
        (amortized.low, amortized.base, amortized.high) == (D("1.1"), D("3.1"), D("4.1"))
        and amortized.includes_training_amortization is True,
    )
    no_amortization = cost_per_success(
        [AttemptCost(request_id="r1", outcome="pass", settled_cost=D("1"))]
    )
    check(
        "FTF-1305-A1",
        "amortized=True without an amortized figure is refused",
        _rejects(
            CostInterval.from_scenario_results,
            best_case=no_amortization,
            expected=no_amortization,
            worst_case=no_amortization,
            amortized=True,
        ),
    )
    check(
        "FTF-1305-A1",
        "a non-result scenario is refused",
        _rejects(
            CostInterval.from_scenario_results,
            best_case="x",
            expected=expected,
            worst_case=worst,
        ),
    )
    check(
        "FTF-1305-A1",
        "scenarios given in the wrong order (low > base) are refused",
        _rejects(
            CostInterval.from_scenario_results,
            best_case=worst,
            expected=expected,
            worst_case=best,
        ),
    )


def test_quality_floor_cheap_low_quality_cannot_win() -> None:
    result = rank_candidates(
        [
            cand("cheap", "base", cost=interval("0.01", "0.02", "0.03"), quality="0.79"),
            cand("good", "existing", cost=interval("0.50", "0.60", "0.70"), quality="0.80"),
        ],
        min_quality=FLOOR,
    )
    check(
        "FTF-1305-N1",
        "a cheaper model below the quality floor does not win",
        result.winner_id == "good" and result.status == "WINNER",
    )
    excluded = {e.candidate_id: e.reasons for e in result.excluded}
    check(
        "FTF-1305-N1",
        "the cheap low-quality model is reported with its reason, not deleted",
        excluded == {"cheap": ("below_quality_floor",)},
    )
    check(
        "FTF-1305-N1",
        "a quality exactly at the floor qualifies",
        [r.candidate_id for r in result.ranked] == ["good"],
    )

    only_cheap = rank_candidates(
        [cand("cheap", cost=interval("0.01", "0.02", "0.03"), quality="0.10")], min_quality=FLOOR
    )
    check(
        "FTF-1305-N1",
        "when only low-quality candidates exist there is no winner at all",
        only_cheap.status == "NO_QUALIFYING_CANDIDATE"
        and only_cheap.winner_id is None
        and only_cheap.ranked == (),
    )


def test_unmeasured_quality_never_qualifies() -> None:
    result = rank_candidates(
        [
            cand("unknown", cost=interval("0.01", "0.02", "0.03"), quality=None),
            cand("known", cost=interval("0.50", "0.60", "0.70")),
        ],
        min_quality=FLOOR,
    )
    check(
        "FTF-1305-N1",
        "a candidate with no measured quality cannot win on cost",
        result.winner_id == "known"
        and {e.candidate_id: e.reasons for e in result.excluded}
        == {"unknown": ("quality_unmeasured",)},
    )


def test_parameter_count_cannot_determine_winner() -> None:
    def run(small_params: int | None, large_params: int | None) -> RankingResult:
        return rank_candidates(
            [
                cand("small", cost=interval("0.50", "0.60", "0.70"), params=small_params),
                cand("large", cost=interval("0.10", "0.12", "0.14"), params=large_params),
            ],
            min_quality=FLOOR,
        )

    baseline = run(7_000_000_000, 70_000_000_000)
    swapped = run(70_000_000_000, 7_000_000_000)
    absent = run(None, None)
    check(
        "FTF-1305-N1",
        "the cheaper larger model wins regardless of parameter counts",
        baseline.winner_id == swapped.winner_id == absent.winner_id == "large",
    )
    check(
        "FTF-1305-N1",
        "the ranked order is identical for every parameter-count assignment",
        [r.candidate_id for r in baseline.ranked]
        == [r.candidate_id for r in swapped.ranked]
        == [r.candidate_id for r in absent.ranked],
    )
    check(
        "FTF-1305-N1",
        "a non-positive or non-int parameter count is refused",
        _rejects(cand, "x", params=0)
        and _rejects(cand, "x", params=True)
        and _rejects(cand, "x", params=1.5),
    )


def test_tuned_candidate_must_include_training_amortization() -> None:
    check(
        "FTF-1305-N1",
        "a tuned candidate whose cost hides training amortization is refused",
        _rejects(cand, "t", "tuned", cost=interval("0.01", "0.02", "0.03")),
    )
    ok = cand("t", "tuned", cost=interval("0.01", "0.02", "0.03", amortized=True))
    check(
        "FTF-1305-N1",
        "a tuned candidate with an amortized interval is accepted",
        ok.kind == "tuned",
    )
    check(
        "FTF-1305-N1",
        "an unpriced tuned candidate is constructible (and later excluded)",
        cand("t", "tuned", cost=None).cost is None,
    )


def test_unpriced_and_ineligible_are_reported_not_ranked() -> None:
    result = rank_candidates(
        [
            cand("free", cost=None),
            cand(
                "blocked",
                cost=interval("0.01", "0.02", "0.03"),
                eligible=False,
                reason="private artifact not approved",
            ),
            cand("ok", cost=interval("0.50", "0.60", "0.70")),
        ],
        min_quality=FLOOR,
    )
    check("FTF-1305-N1", "an unpriced candidate is never ranked as free", result.winner_id == "ok")
    excluded = {e.candidate_id: e.reasons for e in result.excluded}
    check(
        "FTF-1305-N1",
        "unpriced and ineligible candidates are reported with their reasons",
        excluded == {"free": ("unpriced",), "blocked": ("ineligible",)},
    )
    blocked = [e for e in result.excluded if e.candidate_id == "blocked"][0]
    check(
        "FTF-1305-N1",
        "the ineligibility reason text is preserved",
        blocked.ineligibility_reason == "private artifact not approved",
    )
    multi = rank_candidates(
        [cand("m", cost=None, quality="0.1", eligible=False, reason="x")], min_quality=FLOOR
    )
    check(
        "FTF-1305-N1",
        "every applicable exclusion reason is reported, sorted",
        multi.excluded[0].reasons == ("below_quality_floor", "ineligible", "unpriced"),
    )


def test_overlap_is_inconclusive_and_retains_incumbent() -> None:
    candidates = [
        cand("challenger", "tuned", cost=interval("0.10", "0.15", "0.30", amortized=True)),
        cand("incumbent", "existing", cost=interval("0.20", "0.25", "0.40"), incumbent=True),
        cand("far", "base", cost=interval("5", "6", "7")),
    ]
    result = rank_candidates(candidates, min_quality=FLOOR)
    check(
        "FTF-1305-N1",
        "overlapping intervals are inconclusive and the incumbent is retained "
        "even though the challenger is cheaper at the base estimate",
        result.status == "INCONCLUSIVE_RETAIN_INCUMBENT" and result.winner_id == "incumbent",
    )
    check(
        "FTF-1305-N1",
        "the overlap group names exactly the overlapping candidates",
        result.overlap_group == ("challenger", "incumbent"),
    )

    no_incumbent = rank_candidates(
        [
            cand("a", cost=interval("0.10", "0.15", "0.30")),
            cand("b", cost=interval("0.20", "0.25", "0.40")),
        ],
        min_quality=FLOOR,
    )
    check(
        "FTF-1305-N1",
        "overlap with no incumbent yields no winner, not the cheaper guess",
        no_incumbent.status == "INCONCLUSIVE_NO_INCUMBENT" and no_incumbent.winner_id is None,
    )

    policy = rank_candidates(candidates, min_quality=FLOOR, tie_break_policy_id="policy-7")
    check(
        "FTF-1305-N1",
        "an independent tie-break policy lets the cheapest base estimate win and is recorded",
        policy.status == "TIE_BREAK_APPLIED"
        and policy.winner_id == "challenger"
        and policy.tie_break_policy_id == "policy-7",
    )


def test_touching_intervals_overlap_and_separated_do_not() -> None:
    touching = rank_candidates(
        [
            cand("a", cost=interval("0.10", "0.15", "0.20")),
            cand("b", cost=interval("0.20", "0.25", "0.30")),
        ],
        min_quality=FLOOR,
    )
    check(
        "FTF-1305-N1",
        "intervals that touch at a boundary overlap",
        touching.status == "INCONCLUSIVE_NO_INCUMBENT",
    )
    separated = rank_candidates(
        [
            cand("a", cost=interval("0.10", "0.15", "0.20")),
            cand("b", cost=interval("0.21", "0.25", "0.30")),
        ],
        min_quality=FLOOR,
    )
    check(
        "FTF-1305-N1",
        "separated intervals give a clear winner",
        separated.status == "WINNER" and separated.winner_id == "a",
    )


def test_incumbent_outside_overlap_does_not_block_a_clear_winner() -> None:
    result = rank_candidates(
        [
            cand("cheap", cost=interval("0.10", "0.12", "0.14")),
            cand("old", "existing", cost=interval("1", "2", "3"), incumbent=True),
        ],
        min_quality=FLOOR,
    )
    check(
        "FTF-1305-N1",
        "a clearly cheaper qualifying candidate beats a non-overlapping incumbent",
        result.status == "WINNER" and result.winner_id == "cheap",
    )


def test_incumbent_failing_quality_is_not_retained() -> None:
    result = rank_candidates(
        [
            cand("a", cost=interval("0.10", "0.15", "0.30")),
            cand(
                "old",
                "existing",
                cost=interval("0.20", "0.25", "0.40"),
                incumbent=True,
                quality="0.50",
            ),
        ],
        min_quality=FLOOR,
    )
    check(
        "FTF-1305-N1",
        "an incumbent below the quality floor is not retained",
        result.winner_id == "a" and result.status == "WINNER",
    )


def test_input_guards() -> None:
    good = cand("a", cost=interval("1", "2", "3"))
    check(
        "FTF-1305-A1",
        "empty candidate list refused",
        _rejects(rank_candidates, [], min_quality=FLOOR),
    )
    check(
        "FTF-1305-A1",
        "duplicate ids refused",
        _rejects(
            rank_candidates, [good, cand("a", cost=interval("1", "2", "3"))], min_quality=FLOOR
        ),
    )
    check(
        "FTF-1305-A1",
        "two incumbents refused",
        _rejects(
            rank_candidates,
            [cand("a", incumbent=True, cost=None), cand("b", incumbent=True, cost=None)],
            min_quality=FLOOR,
        ),
    )
    check(
        "FTF-1305-A1",
        "min_quality outside (0, 1] or non-Decimal refused",
        _rejects(rank_candidates, [good], min_quality=D("0"))
        and _rejects(rank_candidates, [good], min_quality=D("1.01"))
        and _rejects(rank_candidates, [good], min_quality=0.8)
        and _rejects(rank_candidates, [good], min_quality=D("NaN")),
    )
    check(
        "FTF-1305-A1",
        "a blank tie-break policy id is refused",
        _rejects(rank_candidates, [good], min_quality=FLOOR, tie_break_policy_id=" "),
    )
    check(
        "FTF-1305-A1",
        "non-candidate list members refused",
        _rejects(rank_candidates, ["a"], min_quality=FLOOR),
    )
    check(
        "FTF-1305-A1",
        "unknown kind and unknown basis refused",
        _rejects(cand, "x", "bogus") and _rejects(cand, "x", basis="guessed"),
    )
    check(
        "FTF-1305-A1",
        "blank or padded candidate id refused",
        _rejects(cand, "") and _rejects(cand, " a"),
    )
    check(
        "FTF-1305-A1",
        "quality outside [0, 1] or float refused",
        _rejects(cand, "x", quality="1.1")
        and _rejects(cand, "x", quality="-0.1")
        and _rejects(RankingCandidate, "x", "base", True, 0.9, None, "measured"),
    )
    check(
        "FTF-1305-A1",
        "ineligible needs a reason; eligible must not carry one",
        _rejects(cand, "x", eligible=False) and _rejects(cand, "x", reason="because"),
    )
    check(
        "FTF-1305-A1",
        "cost interval must be ordered, positive, finite, Decimal",
        _rejects(CostInterval, D("2"), D("1"), D("3"))
        and _rejects(CostInterval, D("0"), D("1"), D("3"))
        and _rejects(CostInterval, D("1"), D("2"), D("Infinity"))
        and _rejects(CostInterval, 1, 2, 3)
        and _rejects(CostInterval, D("1"), D("2"), D("3"), includes_training_amortization=1),
    )
    check(
        "FTF-1305-A1",
        "a non-CostInterval cost is refused",
        _rejects(RankingCandidate, "x", "base", True, D("0.9"), (1, 2, 3), "measured"),
    )


def _raises_domain_error_not_type_error(fn, *args: object, **kwargs: object) -> bool:
    """True only if `fn` raises `CandidateRankingError`. A raw `TypeError`
    (an unhashable value reaching a bare `in table` check once the
    `isinstance` guard in `_lookup` is dropped) is a distinct, reported
    failure, not treated as an acceptable rejection."""
    try:
        fn(*args, **kwargs)
    except CandidateRankingError:
        return True
    except TypeError:
        return False
    return False


def _error_text(fn, *args: object, **kwargs: object) -> str:
    try:
        fn(*args, **kwargs)
    except CandidateRankingError as exc:
        return str(exc)
    return ""


def test_boundaries_and_exact_vocabulary() -> None:
    check(
        "FTF-1305-A1",
        "a trailing-space id is refused (not only a leading one)",
        _rejects(cand, "a "),
    )
    check(
        "FTF-1305-A1",
        "equal low/base/high bounds are accepted (a point estimate)",
        interval("1", "1", "1").low == D("1")
        and interval("1", "1", "2").base == D("1")
        and interval("1", "2", "2").high == D("2"),
    )
    check(
        "FTF-1305-A1",
        "kind is matched exactly: case and whitespace variants are refused",
        _rejects(cand, "x", "BASE")
        and _rejects(cand, "x", " base")
        and _rejects(cand, "x", "Tuned"),
    )
    check(
        "FTF-1305-A1",
        "economics_basis is matched exactly: variants are refused",
        _rejects(cand, "x", basis="Measured")
        and _rejects(cand, "x", basis=" estimated")
        and _rejects(cand, "x", basis=None),
    )
    check(
        "FTF-1305-A1",
        "quality bounds 0 and 1 are valid",
        cand("x", quality="0").quality_lower_bound == D("0")
        and cand("y", quality="1").quality_lower_bound == D("1"),
    )
    check(
        "FTF-1305-A1",
        "a parameter count of exactly 1 is valid",
        cand("x", params=1).parameter_count == 1,
    )
    check(
        "FTF-1305-A1",
        "min_quality of exactly 1 is valid and 1.0 qualifies only a perfect lower bound",
        rank_candidates(
            [
                cand("p", cost=interval("1", "2", "3"), quality="1"),
                cand("q", cost=interval("0.1", "0.2", "0.3"), quality="0.99"),
            ],
            min_quality=D("1"),
        ).winner_id
        == "p",
    )
    point = interval("1", "1", "1")
    twin = rank_candidates([cand("a", cost=point), cand("b", cost=point)], min_quality=FLOOR)
    check(
        "FTF-1305-N1",
        "two identical point-estimate costs overlap and are inconclusive",
        twin.status == "INCONCLUSIVE_NO_INCUMBENT",
    )
    wide = rank_candidates(
        [cand("a", cost=interval("1", "1", "5")), cand("b", cost=interval("2", "2", "3"))],
        min_quality=FLOOR,
    )
    check(
        "FTF-1305-N1",
        "a contained interval overlaps its container",
        wide.status == "INCONCLUSIVE_NO_INCUMBENT",
    )
    best = cost_per_success([AttemptCost(request_id="r", outcome="pass", settled_cost=D("1"))])
    message = _error_text(
        CostInterval.from_scenario_results,
        best_case=best,
        expected=best,
        worst_case=best,
        amortized=True,
    )
    check(
        "FTF-1305-A1",
        "the missing-amortization refusal names the cause, not a generic error",
        "no amortized_cost_per_success" in message,
    )


def test_fault_injection_cost_only_ranking_picks_the_wrong_winner() -> None:
    """Own fault injection (Law 1): a cost-only reimplementation (the exact
    failure mode this task's negative_acceptance forbids) picks a cheap
    low-quality model that the real implementation refuses."""
    candidates = [
        cand("cheap", cost=interval("0.01", "0.02", "0.03"), quality="0.20"),
        cand("good", cost=interval("0.50", "0.60", "0.70"), quality="0.95"),
    ]
    naive_winner = min(candidates, key=lambda c: c.cost.base).candidate_id  # type: ignore[union-attr]
    real_winner = rank_candidates(candidates, min_quality=FLOOR).winner_id
    check(
        "FTF-1305-N1",
        "fault injection: a cost-only ranking would pick the cheap low-quality "
        "model; the real ranking picks the qualifying one",
        naive_winner == "cheap" and real_winner == "good",
    )


def _order(result: RankingResult) -> list[str]:
    return [r.candidate_id for r in result.ranked]


def test_round2_ranking_order_is_deterministic() -> None:
    import itertools

    tied = [
        cand(cid, cost=interval("1", "2", "3"), incumbent=False, params=p)
        for cid, p in (("m", 5), ("a", 900), ("z", 1))
    ]
    for perm in itertools.permutations(tied):
        result = rank_candidates(list(perm), min_quality=FLOOR)
        check(
            "FTF-1305-N1",
            "equal base and high: id ascending in every input order",
            _order(result) == ["a", "m", "z"],
        )
    by_high = [
        cand("a", cost=interval("1", "2", "4")),
        cand("b", cost=interval("1", "2", "3")),
    ]
    for pair_h in (by_high, by_high[::-1]):
        check(
            "FTF-1305-N1",
            "equal base: the lower high bound ranks first, ahead of the id",
            _order(rank_candidates(list(pair_h), min_quality=FLOOR)) == ["b", "a"],
        )
    by_low = [
        cand("z", cost=interval("0.1", "2", "3")),
        cand("a", cost=interval("1.5", "2", "3")),
    ]
    for pair_l in (by_low, by_low[::-1]):
        check(
            "FTF-1305-N1",
            "same base and high: the low bound does not decide, the id does",
            _order(rank_candidates(list(pair_l), min_quality=FLOOR)) == ["a", "z"],
        )
    base_first = [
        cand("a", cost=interval("1.9", "2.1", "3")),
        cand("z", cost=interval("0.1", "2.0", "3")),
    ]
    for pair_b in (base_first, base_first[::-1]):
        check(
            "FTF-1305-N1",
            "a lower base ranks first even with a higher low bound and a later id",
            _order(rank_candidates(list(pair_b), min_quality=FLOOR)) == ["z", "a"],
        )
    sizes = [
        cand("b", cost=interval("1", "2", "3"), params=1),
        cand("a", cost=interval("1", "2", "3"), params=10**12),
    ]
    for pair_s in (sizes, sizes[::-1]):
        check(
            "FTF-1305-N1",
            "equal cost, different parameter_count: order fixed by id in both insertion orders",
            _order(rank_candidates(list(pair_s), min_quality=FLOOR)) == ["a", "b"],
        )


def test_round2_overlap_with_costlier_incumbent_outside_group() -> None:
    result = rank_candidates(
        [
            cand("cheap", cost=interval("1.0", "1.5", "2.0")),
            cand("close", cost=interval("1.8", "2.2", "2.6")),
            cand("old", "existing", cost=interval("5", "6", "7"), incumbent=True),
        ],
        min_quality=FLOOR,
    )
    check(
        "FTF-1305-N1",
        "an incumbent outside the overlap group is not retained",
        result.status == "INCONCLUSIVE_NO_INCUMBENT" and result.winner_id is None,
    )
    check(
        "FTF-1305-N1",
        "the overlap group holds only the overlapping candidates",
        result.overlap_group == ("cheap", "close"),
    )


def test_round2_three_way_overlap_group_in_rank_order() -> None:
    result = rank_candidates(
        [
            cand("a", cost=interval("1.4", "2.4", "3.4")),
            cand("z", cost=interval("1.0", "1.5", "2.0")),
            cand("m", cost=interval("1.8", "2.0", "2.6")),
        ],
        min_quality=FLOOR,
    )
    check(
        "FTF-1305-N1",
        "three overlapping candidates are all in the group, in rank (not id) order",
        result.overlap_group == ("z", "m", "a") and result.winner_id is None,
    )
    chained = rank_candidates(
        [
            cand("a", cost=interval("1", "1.5", "2")),
            cand("b", cost=interval("1.9", "2.5", "3")),
            cand("c", cost=interval("2.9", "3.5", "4")),
        ],
        min_quality=FLOOR,
    )
    check(
        "FTF-1305-N1",
        "overlap is judged against the cheapest candidate only, not chained",
        chained.overlap_group == ("a", "b"),
    )


def test_round2_pending_reconciliation_on_any_scenario_is_provisional() -> None:
    def scenario(cost: str, pending: str | None = None) -> CostPerSuccessResult:
        return cost_per_success(
            [
                AttemptCost(
                    request_id="r1",
                    outcome="pass",
                    settled_cost=D(cost),
                    pending_cost=None if pending is None else D(pending),
                )
            ]
        )

    best, expected, worst = scenario("1"), scenario("2"), scenario("4")
    pending_best, pending_worst = scenario("1", "1"), scenario("4", "1")
    flags = {
        "none": (best, expected, worst, False),
        "best-case only": (pending_best, expected, worst, True),
        "worst-case only": (best, expected, pending_worst, True),
    }
    for label, (b, e, w, want) in flags.items():
        built = CostInterval.from_scenario_results(best_case=b, expected=e, worst_case=w)
        winner = rank_candidates([cand("a", cost=built)], min_quality=FLOOR)
        check(
            "FTF-1305-A1",
            f"pending reconciliation ({label}) -> interval flag {want}",
            built.has_pending_reconciliation is want,
        )
        check(
            "FTF-1305-A1",
            f"pending reconciliation ({label}) -> winner_economics_provisional {want}",
            winner.winner_economics_provisional is want,
        )


def test_round2_result_rows_preserve_inputs() -> None:
    result = rank_candidates(
        [
            cand("a", cost=interval("1", "2", "3"), quality="0.85", incumbent=True),
            cand("b", "existing", cost=interval("5", "6", "7"), quality="0.95"),
        ],
        min_quality=FLOOR,
    )
    rows = {r.candidate_id: r for r in result.ranked}
    check(
        "FTF-1305-A1",
        "ranked rows keep the input quality_lower_bound",
        rows["a"].quality_lower_bound == D("0.85") and rows["b"].quality_lower_bound == D("0.95"),
    )
    check(
        "FTF-1305-A1",
        "ranked rows keep is_incumbent",
        rows["a"].is_incumbent is True and rows["b"].is_incumbent is False,
    )
    none = rank_candidates(
        [cand("a", cost=interval("1", "2", "3"), quality="0.10")],
        min_quality=FLOOR,
        tie_break_policy_id="policy-1",
    )
    check(
        "FTF-1305-N1",
        "tie_break_policy_id is None when nothing qualifies, even if a policy was named",
        none.status == "NO_QUALIFYING_CANDIDATE" and none.tie_break_policy_id is None,
    )
    check(
        "FTF-1305-A1",
        "the candidate kinds are exactly existing, base, tuned",
        CANDIDATE_KINDS == ("existing", "base", "tuned"),
    )


def test_round3_primary_sort_key_is_base_before_high() -> None:
    cheap_base = cand("cheap_base", cost=interval("1", "1", "9"))
    pricier_base = cand("pricier_base", cost=interval("2", "2", "3"))
    for pair in ([cheap_base, pricier_base], [pricier_base, cheap_base]):
        result = rank_candidates(list(pair), min_quality=FLOOR)
        check(
            "FTF-1305-A1",
            "a lower base cost per success ranks first even though its high bound is "
            "much larger -- base is the primary sort key, high only breaks a base tie",
            _order(result) == ["cheap_base", "pricier_base"],
        )


def test_round3_lookup_rejects_unhashable_and_wrong_type_kind_and_basis() -> None:
    bad_kinds: list[object] = [[], {}, 1, b"base"]
    for bad_kind in bad_kinds:
        check(
            "FTF-1305-A1",
            f"kind={bad_kind!r} (unhashable or wrong type) raises CandidateRankingError, "
            "not a bare TypeError from an unguarded membership test",
            _raises_domain_error_not_type_error(cand, "x", bad_kind),
        )
    bad_bases: list[object] = [[], {}, 1, b"measured"]
    for bad_basis in bad_bases:
        check(
            "FTF-1305-A1",
            f"economics_basis={bad_basis!r} (unhashable or wrong type) raises "
            "CandidateRankingError, not a bare TypeError",
            _raises_domain_error_not_type_error(cand, "x", basis=bad_basis),
        )


def test_round3_equal_base_and_high_orders_by_id_regardless_of_other_fields() -> None:
    import itertools

    # Fields are deliberately decorrelated from id order, so a mutant sort key
    # that reads is_incumbent, kind or quality ahead of id reorders the
    # result instead of coincidentally reproducing the real a/k/z order.
    candidates = [
        cand(
            "a",
            "tuned",
            cost=interval("0.5", "2", "3", amortized=True),
            incumbent=False,
            quality="0.85",
        ),
        cand("k", "existing", cost=interval("1", "2", "3"), incumbent=True, quality="0.95"),
        cand("z", "base", cost=interval("1.5", "2", "3"), incumbent=False, quality="1"),
    ]
    for perm in itertools.permutations(candidates):
        result = rank_candidates(list(perm), min_quality=FLOOR)
        check(
            "FTF-1305-N1",
            "equal base and high cost: is_incumbent, kind and quality do not affect "
            "order in any input permutation -- only candidate_id does",
            _order(result) == ["a", "k", "z"],
        )


def main() -> None:
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()

    if FAILURES:
        print(f"FTF-1305 Candidate ranking tests: {len(FAILURES)} failed")
        for failure in FAILURES:
            print(f"  {failure}")
        sys.exit(1)

    print("FTF-1305 Candidate ranking tests: all checks passed")
    sys.exit(0)


if __name__ == "__main__":
    main()
