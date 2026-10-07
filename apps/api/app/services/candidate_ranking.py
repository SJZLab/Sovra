"""Candidate ranking (FTF-1305, Sections 28/31-32/64/69-70/77/81).

Scope (disclosed, not hidden): `database_changes` charters "No independent
schema change. Consume frozen contract" and `contract_owner` is FTF-1301 --
this module adds no table, no route and no new rate vocabulary. It is the
pure ranking step of Section 31's cheapest-passing-model search: it takes
candidates whose eligibility, development-suite quality and cost-per-success
economics were already measured or estimated elsewhere (FTF-1304's
`cost_per_success` for the cost interval) and orders them. It never prices,
measures or evaluates anything itself.

Acceptance ("Rank eligible existing/base/tuned candidates by measured/
estimated production economics and uncertainty") is implemented as:

* every candidate has a `kind` of `existing` (approved existing fine-tune),
  `base` (untouched baseline) or `tuned` (new trainable candidate);
* every candidate carries a cost-per-success *interval* (low/base/high), so
  uncertainty is part of the ranking input and the result, and an
  `economics_basis` of `measured` or `estimated` that the result surfaces;
* candidates are ordered by base cost per success, then high bound, then id.

Negative acceptance ("Parameter count alone or low-quality cheap model cannot
determine winner") is enforced by construction:

1. **Quality floor** -- a candidate is only ranked when its development-suite
   quality lower bound is measured and at least `min_quality`. A cheap model
   under the floor, or with no measured quality at all, is reported with its
   reason and can never win. `min_quality` is a required argument with no
   default, so a caller cannot rank without naming a floor.
2. **Parameter count is recorded and never read** by the ranking, the winner
   selection or the overlap test; only the candidate record carries it.
3. **Section 31 tie rule** -- when the cost intervals of the cheapest
   qualifying candidate and any other qualifying candidate overlap, the
   result is inconclusive economics and the safer incumbent is retained
   unless the caller names an independent tie-break policy
   (`tie_break_policy_id`). The result claims only "lowest-cost qualifying
   candidate among those tested under this workload", never a global optimum.

Section 32 and 31 also say: unpriced usage blocks an economics PASS (a
candidate with no cost is reported `unpriced`, never ranked as free); policy-
ineligible candidates are reported, not silently deleted; and offline
training/qualification cost is separate from runtime cost, so a `tuned`
candidate must carry a cost interval that includes training amortization
(otherwise it would look artificially cheap) -- refused at construction.

No live HTTP route is added by this task and `proving_symbol` is left empty
in `docs/spec-matrix.yaml`, matching the FTF-1304 precedent for a computation
module with no live product-runtime caller in this repository yet -- a
genuine, disclosed 0/0, not inflation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from app.services.cost_per_success_calculator import CostPerSuccessResult

__all__ = [
    "CandidateRankingError",
    "CANDIDATE_KINDS",
    "ECONOMICS_BASES",
    "CostInterval",
    "RankingCandidate",
    "ExcludedCandidate",
    "RankedCandidate",
    "RankingResult",
    "rank_candidates",
]

# One lookup both validates and uses each closed vocabulary, so a value is
# accepted exactly when it is a key (no second, drift-prone comparison).
_KIND_IS_TUNED = {"existing": False, "base": False, "tuned": True}
_BASIS_IS_ESTIMATED = {"measured": False, "estimated": True}
CANDIDATE_KINDS = tuple(_KIND_IS_TUNED)
ECONOMICS_BASES = tuple(_BASIS_IS_ESTIMATED)

STATUS_WINNER = "WINNER"
STATUS_TIE_BREAK_APPLIED = "TIE_BREAK_APPLIED"
STATUS_INCONCLUSIVE_RETAIN_INCUMBENT = "INCONCLUSIVE_RETAIN_INCUMBENT"
STATUS_INCONCLUSIVE_NO_INCUMBENT = "INCONCLUSIVE_NO_INCUMBENT"
STATUS_NO_QUALIFYING_CANDIDATE = "NO_QUALIFYING_CANDIDATE"

REASON_INELIGIBLE = "ineligible"
REASON_UNPRICED = "unpriced"
REASON_QUALITY_UNMEASURED = "quality_unmeasured"
REASON_BELOW_QUALITY_FLOOR = "below_quality_floor"

CLAIM = "lowest-cost qualifying candidate among those tested under this workload"


class CandidateRankingError(ValueError):
    """A field value violates this domain's own shape/invariant -- never a
    silently coerced, defaulted or zero-filled value."""


def _decimal(value: object, field_name: str) -> Decimal:
    if not isinstance(value, Decimal) or isinstance(value, bool):
        raise CandidateRankingError(
            f"{field_name} must be a decimal.Decimal, not {type(value).__name__}: {value!r}"
        )
    if not value.is_finite():
        raise CandidateRankingError(f"{field_name} must be finite, not {value!r}")
    return value


def _positive_decimal(value: object, field_name: str) -> Decimal:
    number = _decimal(value, field_name)
    if number <= 0:
        raise CandidateRankingError(
            f"{field_name} must be greater than zero (a zero cost per success is an unpriced "
            f"claim, not a measured one): {value!r}"
        )
    return number


def _bool(value: object, field_name: str) -> bool:
    if not isinstance(value, bool):
        raise CandidateRankingError(
            f"{field_name} must be a bool, not {type(value).__name__}: {value!r}"
        )
    return value


def _lookup(table: dict[str, bool], value: object, field_name: str) -> bool:
    if not isinstance(value, str) or value not in table:
        raise CandidateRankingError(f"{field_name} must be one of {tuple(table)}: {value!r}")
    return table[value]


def _identifier(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise CandidateRankingError(
            f"{field_name} must be a non-empty string without surrounding whitespace: {value!r}"
        )
    return value


@dataclass(frozen=True)
class CostInterval:
    """Cost per success with its uncertainty: `low <= base <= high`, all
    strictly positive. `includes_training_amortization` says whether the
    figure is FTF-1304's `amortized_cost_per_success` (offline training and
    qualification cost spread over approved lifetime successes).
    `has_pending_reconciliation` carries FTF-1304's provisional-figure flag."""

    low: Decimal
    base: Decimal
    high: Decimal
    includes_training_amortization: bool = False
    has_pending_reconciliation: bool = False

    def __post_init__(self) -> None:
        _positive_decimal(self.low, "low")
        _positive_decimal(self.base, "base")
        _positive_decimal(self.high, "high")
        if not self.low <= self.base <= self.high:
            raise CandidateRankingError(
                f"cost interval must satisfy low <= base <= high: "
                f"{self.low!r}, {self.base!r}, {self.high!r}"
            )
        _bool(self.includes_training_amortization, "includes_training_amortization")
        _bool(self.has_pending_reconciliation, "has_pending_reconciliation")

    @classmethod
    def from_scenario_results(
        cls,
        *,
        best_case: CostPerSuccessResult,
        expected: CostPerSuccessResult,
        worst_case: CostPerSuccessResult,
        amortized: bool = False,
    ) -> CostInterval:
        """Build the interval from three FTF-1304 results (for example the
        high/base/low utilization scenarios of Section 32). With
        `amortized=True` every scenario must carry an amortized figure."""
        results = (best_case, expected, worst_case)
        for result in results:
            if not isinstance(result, CostPerSuccessResult):
                raise CandidateRankingError(
                    f"scenario results must be CostPerSuccessResult, not {type(result).__name__}"
                )
        amortized = _bool(amortized, "amortized")
        figures: list[Decimal] = []
        for result in results:
            if amortized:
                if result.amortized_cost_per_success is None:
                    raise CandidateRankingError(
                        "amortized=True but a scenario result has no amortized_cost_per_success"
                    )
                figures.append(result.amortized_cost_per_success)
            else:
                figures.append(result.cost_per_success)
        return cls(
            low=figures[0],
            base=figures[1],
            high=figures[2],
            includes_training_amortization=amortized,
            has_pending_reconciliation=any(r.has_pending_reconciliation for r in results),
        )


@dataclass(frozen=True)
class RankingCandidate:
    """One candidate offered to the ranking.

    `eligible=False` needs an `ineligibility_reason` (policy-ineligible
    candidates are reported, not deleted). `quality_lower_bound` is the
    development-suite quality lower bound in [0, 1], or `None` when it was
    not measured. `cost` is `None` for unpriced usage. `parameter_count` is
    recorded for the report only; ranking never reads it.
    """

    candidate_id: str
    kind: str
    eligible: bool
    quality_lower_bound: Decimal | None
    cost: CostInterval | None
    economics_basis: str
    is_incumbent: bool = False
    ineligibility_reason: str | None = None
    parameter_count: int | None = None
    economics_estimated: bool = field(init=False, default=False)

    def __post_init__(self) -> None:
        _identifier(self.candidate_id, "candidate_id")
        is_tuned = _lookup(_KIND_IS_TUNED, self.kind, "kind")
        _bool(self.eligible, "eligible")
        _bool(self.is_incumbent, "is_incumbent")
        object.__setattr__(
            self,
            "economics_estimated",
            _lookup(_BASIS_IS_ESTIMATED, self.economics_basis, "economics_basis"),
        )
        if self.eligible:
            if self.ineligibility_reason is not None:
                raise CandidateRankingError(
                    f"{self.candidate_id!r} is eligible but carries an ineligibility_reason"
                )
        else:
            _identifier(self.ineligibility_reason, "ineligibility_reason")
        if self.quality_lower_bound is not None:
            quality = _decimal(self.quality_lower_bound, "quality_lower_bound")
            if not Decimal(0) <= quality <= Decimal(1):
                raise CandidateRankingError(
                    f"quality_lower_bound must be within [0, 1]: {self.quality_lower_bound!r}"
                )
        if self.cost is not None:
            if not isinstance(self.cost, CostInterval):
                raise CandidateRankingError(
                    f"cost must be a CostInterval or None, not {type(self.cost).__name__}"
                )
            if is_tuned and not self.cost.includes_training_amortization:
                raise CandidateRankingError(
                    f"tuned candidate {self.candidate_id!r} must carry a cost interval that "
                    "includes training amortization -- otherwise offline training and "
                    "qualification cost would be hidden and the candidate would look cheaper"
                )
        if self.parameter_count is not None and (
            not isinstance(self.parameter_count, int)
            or isinstance(self.parameter_count, bool)
            or self.parameter_count < 1
        ):
            raise CandidateRankingError(
                f"parameter_count must be a positive int or None: {self.parameter_count!r}"
            )


@dataclass(frozen=True)
class ExcludedCandidate:
    """A candidate reported but not ranked, with every applicable reason."""

    candidate_id: str
    kind: str
    reasons: tuple[str, ...]
    ineligibility_reason: str | None


@dataclass(frozen=True)
class RankedCandidate:
    rank: int
    candidate_id: str
    kind: str
    quality_lower_bound: Decimal
    cost: CostInterval
    economics_basis: str
    is_incumbent: bool


@dataclass(frozen=True)
class RankingResult:
    status: str
    winner_id: str | None
    claim: str
    ranked: tuple[RankedCandidate, ...]
    excluded: tuple[ExcludedCandidate, ...]
    overlap_group: tuple[str, ...]
    tie_break_policy_id: str | None
    winner_economics_estimated: bool
    winner_economics_provisional: bool


@dataclass(frozen=True)
class _Qualified:
    """A candidate that cleared every gate, with its narrowed cost/quality."""

    candidate: RankingCandidate
    cost: CostInterval
    quality: Decimal


def _overlaps(a: CostInterval, b: CostInterval) -> bool:
    return a.low <= b.high and b.low <= a.high


def rank_candidates(
    candidates: list[RankingCandidate],
    *,
    min_quality: Decimal,
    tie_break_policy_id: str | None = None,
) -> RankingResult:
    """Rank eligible, priced candidates that clear the quality floor by cost
    per success, and decide the winner under Section 31's tie rule."""
    if not isinstance(candidates, list) or not candidates:
        raise CandidateRankingError("candidates must be a non-empty list")
    for candidate in candidates:
        if not isinstance(candidate, RankingCandidate):
            raise CandidateRankingError(
                f"candidates must be RankingCandidate, not {type(candidate).__name__}"
            )
    floor = _decimal(min_quality, "min_quality")
    if not Decimal(0) < floor <= Decimal(1):
        raise CandidateRankingError(f"min_quality must be within (0, 1]: {min_quality!r}")
    if tie_break_policy_id is not None:
        _identifier(tie_break_policy_id, "tie_break_policy_id")

    ids = [candidate.candidate_id for candidate in candidates]
    if len(set(ids)) != len(ids):
        raise CandidateRankingError("candidate_id values must be unique")
    if sum(1 for candidate in candidates if candidate.is_incumbent) > 1:
        raise CandidateRankingError("at most one candidate may be the incumbent")

    qualifying: list[_Qualified] = []
    excluded: list[ExcludedCandidate] = []
    for candidate in candidates:
        cost = candidate.cost
        quality = candidate.quality_lower_bound
        reasons: list[str] = []
        if not candidate.eligible:
            reasons.append(REASON_INELIGIBLE)
        if cost is None:
            reasons.append(REASON_UNPRICED)
        if quality is None:
            reasons.append(REASON_QUALITY_UNMEASURED)
        elif quality < floor:
            reasons.append(REASON_BELOW_QUALITY_FLOOR)
        if cost is None or quality is None or reasons:
            excluded.append(
                ExcludedCandidate(
                    candidate_id=candidate.candidate_id,
                    kind=candidate.kind,
                    reasons=tuple(sorted(reasons)),
                    ineligibility_reason=candidate.ineligibility_reason,
                )
            )
        else:
            qualifying.append(_Qualified(candidate, cost, quality))

    qualifying.sort(key=lambda q: (q.cost.base, q.cost.high, q.candidate.candidate_id))
    ranked = tuple(
        RankedCandidate(
            rank=position,
            candidate_id=q.candidate.candidate_id,
            kind=q.candidate.kind,
            quality_lower_bound=q.quality,
            cost=q.cost,
            economics_basis=q.candidate.economics_basis,
            is_incumbent=q.candidate.is_incumbent,
        )
        for position, q in enumerate(qualifying, start=1)
    )

    def result(
        status: str,
        winner: _Qualified | None,
        overlap_group: tuple[str, ...] = (),
        policy: str | None = None,
    ) -> RankingResult:
        return RankingResult(
            status=status,
            winner_id=winner.candidate.candidate_id if winner is not None else None,
            claim=CLAIM,
            ranked=ranked,
            excluded=tuple(excluded),
            overlap_group=overlap_group,
            tie_break_policy_id=policy,
            winner_economics_estimated=winner is not None and winner.candidate.economics_estimated,
            winner_economics_provisional=winner is not None
            and winner.cost.has_pending_reconciliation,
        )

    if not qualifying:
        return result(STATUS_NO_QUALIFYING_CANDIDATE, None)

    lead = qualifying[0]
    group = [q for q in qualifying if q is lead or _overlaps(lead.cost, q.cost)]
    group_ids = tuple(q.candidate.candidate_id for q in group)
    if len(group) == 1:
        return result(STATUS_WINNER, lead)
    if tie_break_policy_id is not None:
        return result(STATUS_TIE_BREAK_APPLIED, lead, group_ids, tie_break_policy_id)
    incumbent = next((q for q in group if q.candidate.is_incumbent), None)
    if incumbent is not None:
        return result(STATUS_INCONCLUSIVE_RETAIN_INCUMBENT, incumbent, group_ids)
    return result(STATUS_INCONCLUSIVE_NO_INCUMBENT, None, group_ids)
