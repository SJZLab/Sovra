"""Compound per-step binding cost attribution (FTF-1304-S1, child slice of
the CERTIFIED FTF-1304; Section 0 item 19, 3.4-3.5, 29, 31-32, 66.3-66.4,
69-70, 77, 81; AF-22; Q15, Q19).

A compound candidate runs routine sub-steps (classify/extract/summarize) on
a small model and reasoning on a large one: Section 3.5, "A deliberate
fallback policy is evaluated and priced as a separate compound system."
This module prices that candidate honestly. It is a refinement of FTF-1304,
not a new criterion, and it composes only certified code:

* FTF-04A25 `ModelBinding` / `validate_model_binding_override` -- a step
  key is a `route_params` key the adapter's `describe()` declared;
* FTF-1303 `ProviderTokenUsage` / `ProviderRateCard` / `provider_call_cost`;
* FTF-1302 `ServiceWindow` / `window_total_cost`;
* FTF-1304 `AttemptCost` / `cost_per_success` / `TASK_OUTCOMES`;
* FTF-1305 `CostInterval` / `RankingCandidate`;
* FTF-0210 `RouteIdentityState`.

Refusals are a *status*, never a number (parent negative clause, "Zero
successes, pending charges and unobserved fallback use cannot yield
misleading cheap passing metric"): a call on a route or step the binding
does not declare (`binding_violation` -- a silent fallback or substitution),
a call whose route identity is not verified (`identity_unverified`), and
any call that cannot be priced (`unpriced` -- a provider call with no
usage or rate card, a self-hosted call outside every service window; never
$0, "Do not assume self-hosted inference is free"). Only a `priced`
attribution carries `attempt_costs`, so `cost_per_success` cannot be
computed from a refused one.

Self-hosted calls are priced by allocating each window's `window_total_cost`
exactly once over all calls on its routes inside the window, in proportion
to call count (Section 32, "No double-counting the same shared GPU
second"). The share is exact Decimal with the residual on the last call in
canonical order, so allocations sum exactly to the window total.

Disclosed decisions the task entry left open: the default route needs its
own pricing mode and artifact kind, so `CompoundBinding` carries
`default_pricing_mode` / `default_artifact_kind` (required, never guessed);
a `pass` attempt with no model call at all is `unpriced` (a free success
would be a misleading cheap metric); a service window that no call lands in
is `unpriced` (its real bill cannot be attributed and must not be dropped).

Law 12: no threshold, routing-choice or selection input exists here, so the
module cannot tune on final results; which steps to route is chosen on
development data in FTF-1307. Zero-egress: pure and synchronous, no I/O.
UNWIRED by design (live callers will be FTF-1306/1307/1309, all ABSENT);
`proving_symbol` stays blank, FTF-1304/1305 precedent. The benefit is only
proven when FTF-1306 runs both candidates through the target harness on the
same development suite; this slice makes that comparison honest, it does not
measure it.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import datetime
from decimal import ROUND_DOWN, Decimal, localcontext
from enum import StrEnum

from packages.litellm_client.real_chat_probe import RouteIdentityState

from app.services.candidate_ranking import CostInterval, RankingCandidate
from app.services.cost_per_success_calculator import (
    AttemptCost,
    CostPerSuccessError,
    cost_per_success,
)
from app.services.local_compute_costing import TASK_OUTCOMES, ServiceWindow, window_total_cost
from app.services.provider_token_costing import (
    ProviderRateCard,
    ProviderTokenCostingError,
    ProviderTokenUsage,
    provider_call_cost,
)
from app.target_connectors.production_faithful_target_agent_harness_contract import (
    AdapterDescribeResult,
    ModelBinding,
    validate_model_binding_override,
)

__all__ = [
    "ARTIFACT_KINDS",
    "ALLOCATION_POLICY",
    "PRICING_MODES",
    "STATUS_BINDING_VIOLATION",
    "STATUS_IDENTITY_UNVERIFIED",
    "STATUS_PRICED",
    "STATUS_UNPRICED",
    "STEP_KINDS",
    "AttributionStatus",
    "AttemptTrace",
    "CompoundAttribution",
    "CompoundAttributionError",
    "CompoundBinding",
    "CompoundIssue",
    "ModelCallRecord",
    "RouteLine",
    "SelfHostedWindow",
    "StepBinding",
    "attribute_compound_costs",
    "compound_ranking_candidate",
]

STEP_KINDS: tuple[str, ...] = ("reasoning", "classify", "extract", "summarize", "other")
PRICING_MODES: tuple[str, ...] = ("provider", "self_hosted")
ARTIFACT_KINDS: tuple[str, ...] = ("existing", "base", "tuned")
ALLOCATION_POLICY = "proportional_to_calls"


class AttributionStatus(StrEnum):
    PRICED = "priced"
    BINDING_VIOLATION = "binding_violation"
    IDENTITY_UNVERIFIED = "identity_unverified"
    UNPRICED = "unpriced"


STATUS_PRICED = AttributionStatus.PRICED
STATUS_BINDING_VIOLATION = AttributionStatus.BINDING_VIOLATION
STATUS_IDENTITY_UNVERIFIED = AttributionStatus.IDENTITY_UNVERIFIED
STATUS_UNPRICED = AttributionStatus.UNPRICED
# Precedence when several refusals apply: the most serious one names the result.
_STATUS_PRECEDENCE: tuple[AttributionStatus, ...] = (
    STATUS_BINDING_VIOLATION,
    STATUS_IDENTITY_UNVERIFIED,
    STATUS_UNPRICED,
)
if TASK_OUTCOMES[0] != "pass":  # the success outcome is located by position below
    raise ImportError("TASK_OUTCOMES no longer starts with 'pass'")
_PASS_OUTCOME_INDEX = 0

_DEFAULT_ROUTE_STEP_KIND = "reasoning"
_SHARE_QUANTUM = Decimal("1E-12")
_ALLOCATION_PRECISION = 80


class CompoundAttributionError(ValueError):
    """A field violates this domain's own shape/invariant. A priced-or-refused
    outcome of attribution itself is a status, never this exception."""


def _identifier(value: object, field_name: str) -> str:
    if type(value) is not str or not value or value != value.strip():
        raise CompoundAttributionError(
            f"{field_name} must be a non-empty string without surrounding whitespace: {value!r}"
        )
    return value


def _choice(value: object, allowed: tuple[str, ...], field_name: str) -> str:
    if type(value) is not str or value not in allowed:
        raise CompoundAttributionError(f"{field_name} must be one of {allowed}: {value!r}")
    return value


def _aware(value: object, field_name: str) -> datetime:
    if not isinstance(value, datetime) or value.utcoffset() is None:
        raise CompoundAttributionError(f"{field_name} must be a timezone-aware datetime: {value!r}")
    return value


def _tuple_of(value: object, item_type: type, field_name: str) -> tuple:
    if not isinstance(value, tuple | list):
        raise CompoundAttributionError(f"{field_name} must be a tuple or list: {value!r}")
    items = tuple(value)
    for item in items:
        if not isinstance(item, item_type):
            raise CompoundAttributionError(
                f"{field_name} must hold {item_type.__name__}, not {type(item).__name__}"
            )
    return items


def _sha256(document: object) -> str:
    text = json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(text.encode("ascii")).hexdigest()


@dataclass(frozen=True)
class StepBinding:
    """One routed sub-step. `step_kind` is a user-confirmed label carried for
    reporting only; it is never inferred and never changes a price."""

    step_key: str
    route_alias: str
    step_kind: str
    pricing_mode: str
    artifact_kind: str

    def __post_init__(self) -> None:
        _identifier(self.step_key, "step_key")
        _identifier(self.route_alias, "route_alias")
        _choice(self.step_kind, STEP_KINDS, "step_kind")
        _choice(self.pricing_mode, PRICING_MODES, "pricing_mode")
        _choice(self.artifact_kind, ARTIFACT_KINDS, "artifact_kind")


@dataclass(frozen=True)
class CompoundBinding:
    """The default route plus per-step routes of one compound candidate."""

    default_route_alias: str
    provider: str
    default_pricing_mode: str
    default_artifact_kind: str
    steps: tuple[StepBinding, ...]

    def __post_init__(self) -> None:
        _identifier(self.default_route_alias, "default_route_alias")
        _identifier(self.provider, "provider")
        _choice(self.default_pricing_mode, PRICING_MODES, "default_pricing_mode")
        _choice(self.default_artifact_kind, ARTIFACT_KINDS, "default_artifact_kind")
        steps = _tuple_of(self.steps, StepBinding, "steps")
        object.__setattr__(self, "steps", steps)
        seen_keys: set[str] = set()
        modes: dict[str, tuple[str, str]] = {
            self.default_route_alias: (self.default_pricing_mode, self.default_artifact_kind)
        }
        for step in steps:
            if step.step_key in seen_keys:
                raise CompoundAttributionError(f"duplicate step_key {step.step_key!r}")
            seen_keys.add(step.step_key)
            known = modes.setdefault(step.route_alias, (step.pricing_mode, step.artifact_kind))
            if known != (step.pricing_mode, step.artifact_kind):
                raise CompoundAttributionError(
                    f"route alias {step.route_alias!r} is bound with two different pricing "
                    "modes or artifact kinds -- one alias is one deployment and one price"
                )

    def route_table(self) -> dict[str, tuple[str, str]]:
        """route alias -> (pricing_mode, artifact_kind)."""
        table = {self.default_route_alias: (self.default_pricing_mode, self.default_artifact_kind)}
        for step in self.steps:
            table[step.route_alias] = (step.pricing_mode, step.artifact_kind)
        return table

    def to_model_binding(self) -> ModelBinding:
        return ModelBinding(
            model_id=self.default_route_alias,
            provider=self.provider,
            route_params=tuple(sorted((s.step_key, s.route_alias) for s in self.steps)),
        )

    def validate(self, describe_result: AdapterDescribeResult) -> ModelBinding:
        """Every step key must be a `route_params` key the adapter declared
        (FTF-04A25's own check; an undeclared key is refused)."""
        return validate_model_binding_override(describe_result, self.to_model_binding())

    def candidate_kind(self) -> str:
        kinds = {self.default_artifact_kind} | {s.artifact_kind for s in self.steps}
        if "tuned" in kinds:
            return "tuned"
        if "existing" in kinds:
            return "existing"
        return "base"

    @property
    def binding_sha256(self) -> str:
        document = asdict(self)
        document["steps"] = sorted(document["steps"], key=lambda step: step["step_key"])
        return _sha256(document)


@dataclass(frozen=True)
class ModelCallRecord:
    """One model call in the attempt's correlated trace (Section 66.3
    `collect_outcome`). `step_key=None` means the default route."""

    request_id: str
    step_key: str | None
    route_alias: str
    identity_state: RouteIdentityState
    deployment_fingerprint: str | None
    usage: ProviderTokenUsage | None
    started_at: datetime
    ended_at: datetime

    def __post_init__(self) -> None:
        _identifier(self.request_id, "request_id")
        if self.step_key is not None:
            _identifier(self.step_key, "step_key")
        _identifier(self.route_alias, "route_alias")
        if not isinstance(self.identity_state, RouteIdentityState):
            raise CompoundAttributionError(
                f"identity_state must be a RouteIdentityState: {self.identity_state!r}"
            )
        if self.deployment_fingerprint is not None:
            _identifier(self.deployment_fingerprint, "deployment_fingerprint")
        if self.usage is not None and not isinstance(self.usage, ProviderTokenUsage):
            raise CompoundAttributionError(
                f"usage must be a ProviderTokenUsage or None, not {type(self.usage).__name__}"
            )
        _aware(self.started_at, "started_at")
        _aware(self.ended_at, "ended_at")
        if self.ended_at <= self.started_at:
            raise CompoundAttributionError("ended_at must be strictly after started_at")


@dataclass(frozen=True)
class AttemptTrace:
    request_id: str
    outcome: str
    calls: tuple[ModelCallRecord, ...]
    pending_cost: Decimal | None = None

    def __post_init__(self) -> None:
        _identifier(self.request_id, "request_id")
        _choice(self.outcome, TASK_OUTCOMES, "outcome")
        object.__setattr__(self, "calls", _tuple_of(self.calls, ModelCallRecord, "calls"))
        for call in self.calls:
            if call.request_id != self.request_id:
                raise CompoundAttributionError(
                    f"call for request_id {call.request_id!r} is inside the trace of "
                    f"{self.request_id!r}"
                )
        if self.pending_cost is not None and (
            not isinstance(self.pending_cost, Decimal)
            or not self.pending_cost.is_finite()
            or self.pending_cost < 0
        ):
            raise CompoundAttributionError(
                f"pending_cost must be a finite non-negative Decimal or None: {self.pending_cost!r}"
            )


@dataclass(frozen=True)
class SelfHostedWindow:
    """One reconciled service window shared by every listed route alias."""

    window_id: str
    route_aliases: tuple[str, ...]
    window: ServiceWindow
    allocation_policy: str = ALLOCATION_POLICY

    def __post_init__(self) -> None:
        _identifier(self.window_id, "window_id")
        aliases = _tuple_of(self.route_aliases, str, "route_aliases")
        if not aliases or len(set(aliases)) != len(aliases):
            raise CompoundAttributionError("route_aliases must be non-empty and unique")
        for alias in aliases:
            _identifier(alias, "route_aliases entry")
        object.__setattr__(self, "route_aliases", aliases)
        if not isinstance(self.window, ServiceWindow):
            raise CompoundAttributionError(
                f"window must be a ServiceWindow, not {type(self.window).__name__}"
            )
        _aware(self.window.window_start, "window_start")
        _aware(self.window.window_end, "window_end")
        if self.allocation_policy != "proportional_to_calls":
            raise CompoundAttributionError(
                f"allocation_policy must be {ALLOCATION_POLICY!r}: {self.allocation_policy!r}"
            )


@dataclass(frozen=True)
class CompoundIssue:
    """A refusal reason. Carries the request id, route and a code only --
    never request or response content."""

    status: AttributionStatus
    code: str
    request_id: str | None
    route_alias: str | None


@dataclass(frozen=True)
class RouteLine:
    route_alias: str
    step_kind: str
    calls: int
    input_tokens: int
    output_tokens: int
    cost: Decimal


@dataclass(frozen=True)
class CompoundAttribution:
    status: AttributionStatus
    attempt_costs: tuple[AttemptCost, ...]
    route_lines: tuple[RouteLine, ...]
    step_kind_totals: tuple[tuple[str, Decimal], ...]
    issues: tuple[CompoundIssue, ...]
    candidate_kind: str
    binding_sha256: str
    attribution_sha256: str


def _attempt_order_key(attempt: AttemptTrace) -> tuple[str, str, str, tuple[str, ...]]:
    """A total order over attempts that ignores input order, so the residual
    cent of a shared window always lands on the same attempt."""
    return (
        attempt.request_id,
        attempt.outcome,
        str(attempt.pending_cost),
        tuple(sorted(repr(c) for c in attempt.calls)),
    )


def _allocate(total: Decimal, count: int) -> list[Decimal]:
    """Split `total` over `count` calls: an equal truncated share each, the
    residual on the last, so the shares sum to `total` exactly."""
    with localcontext() as context:
        context.prec = _ALLOCATION_PRECISION
        share = (total / Decimal(count)).quantize(_SHARE_QUANTUM, rounding=ROUND_DOWN)
        shares = [share] * (count - 1)
        shares.append(total - share * Decimal(count - 1))
    return shares


@dataclass
class _Call:
    attempt_index: int
    record: ModelCallRecord
    step_kind: str
    cost: Decimal | None = None


def attribute_compound_costs(
    binding: CompoundBinding,
    attempts: Sequence[AttemptTrace],
    rate_cards: Mapping[str, ProviderRateCard],
    windows: Sequence[SelfHostedWindow],
) -> CompoundAttribution:
    """Price every call of every attempt against the binding, or refuse."""
    if not isinstance(binding, CompoundBinding):
        raise CompoundAttributionError("binding must be a CompoundBinding")
    ordered = sorted(
        _tuple_of(attempts, AttemptTrace, "attempts"),
        key=_attempt_order_key,
    )
    if not isinstance(rate_cards, Mapping):
        raise CompoundAttributionError("rate_cards must be a mapping of route alias to rate card")
    for alias, card in rate_cards.items():
        _identifier(alias, "rate card route alias")
        if not isinstance(card, ProviderRateCard):
            raise CompoundAttributionError(f"rate card for {alias!r} must be a ProviderRateCard")
    window_list = _tuple_of(windows, SelfHostedWindow, "windows")
    if len({w.window_id for w in window_list}) != len(window_list):
        raise CompoundAttributionError("window_id values must be unique")

    routes = binding.route_table()
    steps = {s.step_key: s for s in binding.steps}
    issues: list[CompoundIssue] = []
    calls: list[_Call] = []
    self_hosted: list[_Call] = []

    for index, attempt in enumerate(ordered):
        if TASK_OUTCOMES.index(attempt.outcome) == _PASS_OUTCOME_INDEX and not attempt.calls:
            issues.append(
                CompoundIssue(
                    STATUS_UNPRICED, "success_without_model_call", attempt.request_id, None
                )
            )
        for record in sorted(attempt.calls, key=repr):
            if record.step_key is None:
                bound_alias = binding.default_route_alias
                step_kind = _DEFAULT_ROUTE_STEP_KIND
            elif record.step_key in steps:
                bound_alias = steps[record.step_key].route_alias
                step_kind = steps[record.step_key].step_kind
            else:
                issues.append(
                    CompoundIssue(
                        STATUS_BINDING_VIOLATION,
                        "step_not_in_binding",
                        record.request_id,
                        record.route_alias,
                    )
                )
                continue
            if record.route_alias != bound_alias or record.route_alias not in routes:
                issues.append(
                    CompoundIssue(
                        STATUS_BINDING_VIOLATION,
                        "route_not_bound",
                        record.request_id,
                        record.route_alias,
                    )
                )
                continue
            call = _Call(index, record, step_kind)
            calls.append(call)
            if (
                record.identity_state is not RouteIdentityState.VERIFIED
                or record.deployment_fingerprint is None
            ):
                issues.append(
                    CompoundIssue(
                        STATUS_IDENTITY_UNVERIFIED,
                        "identity_unverified",
                        record.request_id,
                        record.route_alias,
                    )
                )
                continue
            if routes[record.route_alias][0] == "provider":
                call.cost = _price_provider_call(call, rate_cards, issues)
            else:
                self_hosted.append(call)

    _allocate_windows(self_hosted, window_list, issues)

    status = STATUS_PRICED
    for candidate_status in _STATUS_PRECEDENCE:
        if any(issue.status is candidate_status for issue in issues):
            status = candidate_status
            break

    attempt_costs: tuple[AttemptCost, ...] = ()
    route_lines: tuple[RouteLine, ...] = ()
    step_kind_totals: tuple[tuple[str, Decimal], ...] = ()
    if status is STATUS_PRICED:
        attempt_costs = _build_attempt_costs(ordered, calls)
        route_lines = _build_route_lines(calls)
        step_kind_totals = _build_step_kind_totals(route_lines)

    result_issues = tuple(sorted(issues, key=repr))
    return CompoundAttribution(
        status=status,
        attempt_costs=attempt_costs,
        route_lines=route_lines,
        step_kind_totals=step_kind_totals,
        issues=result_issues,
        candidate_kind=binding.candidate_kind(),
        binding_sha256=binding.binding_sha256,
        attribution_sha256=_attribution_sha256(
            binding, status, attempt_costs, route_lines, step_kind_totals, result_issues
        ),
    )


def _price_provider_call(
    call: _Call, rate_cards: Mapping[str, ProviderRateCard], issues: list[CompoundIssue]
) -> Decimal | None:
    record = call.record
    card = rate_cards.get(record.route_alias)
    if record.usage is None or card is None:
        code = "usage_missing" if record.usage is None else "rate_card_missing"
        issues.append(CompoundIssue(STATUS_UNPRICED, code, record.request_id, record.route_alias))
        return None
    try:
        return provider_call_cost(record.usage, card)
    except ProviderTokenCostingError:
        issues.append(
            CompoundIssue(
                STATUS_UNPRICED, "rate_missing_for_usage", record.request_id, record.route_alias
            )
        )
        return None


def _allocate_windows(
    self_hosted: list[_Call], windows: tuple[SelfHostedWindow, ...], issues: list[CompoundIssue]
) -> None:
    assigned: dict[str, list[_Call]] = {w.window_id: [] for w in windows}
    for call in self_hosted:
        record = call.record
        by_alias = [w for w in windows if record.route_alias in w.route_aliases]
        covering = [
            w
            for w in by_alias
            if w.window.window_start <= record.started_at and record.ended_at <= w.window.window_end
        ]
        if len(covering) == 1:
            assigned[covering[0].window_id].append(call)
            continue
        if covering:
            code = "window_ambiguous"
        elif by_alias:
            code = "outside_window_range"
        else:
            code = "no_window"
        issues.append(CompoundIssue(STATUS_UNPRICED, code, record.request_id, record.route_alias))
    for window in windows:
        members = assigned[window.window_id]
        if not members:
            issues.append(CompoundIssue(STATUS_UNPRICED, "window_without_calls", None, None))
            continue
        members.sort(key=lambda c: (c.attempt_index, repr(c.record)))
        shares = _allocate(window_total_cost(window.window), len(members))
        for member, share in zip(members, shares, strict=True):
            member.cost = share


def _build_attempt_costs(
    ordered: list[AttemptTrace], calls: list[_Call]
) -> tuple[AttemptCost, ...]:
    totals = [Decimal(0)] * len(ordered)
    for call in calls:
        assert call.cost is not None
        totals[call.attempt_index] += call.cost
    built = [
        AttemptCost(
            request_id=attempt.request_id,
            outcome=attempt.outcome,
            settled_cost=totals[index],
            pending_cost=attempt.pending_cost,
        )
        for index, attempt in enumerate(ordered)
    ]
    return tuple(built)


def _build_route_lines(calls: list[_Call]) -> tuple[RouteLine, ...]:
    grouped: dict[tuple[str, str], list[_Call]] = {}
    for call in calls:
        grouped.setdefault((call.record.route_alias, call.step_kind), []).append(call)
    lines: list[RouteLine] = []
    for (alias, step_kind), members in sorted(grouped.items()):
        cost = Decimal(0)
        input_tokens = 0
        output_tokens = 0
        for member in members:
            assert member.cost is not None
            cost += member.cost
            if member.record.usage is not None:
                input_tokens += member.record.usage.input_tokens
                output_tokens += member.record.usage.output_tokens
        lines.append(RouteLine(alias, step_kind, len(members), input_tokens, output_tokens, cost))
    return tuple(lines)


def _build_step_kind_totals(lines: tuple[RouteLine, ...]) -> tuple[tuple[str, Decimal], ...]:
    totals: dict[str, Decimal] = {}
    for line in lines:
        totals[line.step_kind] = totals.get(line.step_kind, Decimal(0)) + line.cost
    return tuple(sorted(totals.items()))


def _attribution_sha256(
    binding: CompoundBinding,
    status: AttributionStatus,
    attempt_costs: tuple[AttemptCost, ...],
    route_lines: tuple[RouteLine, ...],
    step_kind_totals: tuple[tuple[str, Decimal], ...],
    issues: tuple[CompoundIssue, ...],
) -> str:
    return _sha256(
        {
            "binding_sha256": binding.binding_sha256,
            "status": status,
            "attempt_costs": sorted(
                (
                    [
                        a.request_id,
                        a.outcome,
                        str(a.settled_cost),
                        None if a.pending_cost is None else str(a.pending_cost),
                    ]
                    for a in attempt_costs
                ),
                key=json.dumps,
            ),
            "route_lines": [
                [ln.route_alias, ln.step_kind, ln.calls, ln.input_tokens, ln.output_tokens]
                + [str(ln.cost)]
                for ln in route_lines
            ],
            "step_kind_totals": [[kind, str(total)] for kind, total in step_kind_totals],
            "issues": [[i.status, i.code, i.request_id, i.route_alias] for i in issues],
        }
    )


def compound_ranking_candidate(
    candidate_id: str,
    attribution: CompoundAttribution,
    *,
    quality_lower_bound: Decimal | None,
    interval_low: Decimal,
    interval_high: Decimal,
    eligible: bool = True,
    ineligibility_reason: str | None = None,
    is_incumbent: bool = False,
    training_and_qualification_cost: Decimal | None = None,
    approved_expected_lifetime_successes: int | None = None,
) -> RankingCandidate:
    """FTF-1305 `RankingCandidate` for a priced compound attribution. The base
    cost comes from the attribution itself; `interval_low`/`interval_high` are
    the caller's scenario bounds of the same kind of figure (amortized when
    amortization is supplied). `kind` is `tuned` if any step is tuned, so
    FTF-1305 refuses it without training amortization."""
    if not isinstance(attribution, CompoundAttribution):
        raise CompoundAttributionError("attribution must be a CompoundAttribution")
    if attribution.status is not STATUS_PRICED:
        raise CompoundAttributionError(
            f"attribution is {attribution.status!r}, not priced -- a refused attribution "
            "cannot become a ranking candidate"
        )
    try:
        result = cost_per_success(
            list(attribution.attempt_costs),
            training_and_qualification_cost=training_and_qualification_cost,
            approved_expected_lifetime_successes=approved_expected_lifetime_successes,
        )
    except CostPerSuccessError as error:
        raise CompoundAttributionError(f"no cost per success: {error}") from error
    amortized = result.amortized_cost_per_success is not None
    base = result.amortized_cost_per_success if amortized else result.cost_per_success
    assert base is not None
    interval = CostInterval(
        low=interval_low,
        base=base,
        high=interval_high,
        includes_training_amortization=amortized,
        has_pending_reconciliation=result.has_pending_reconciliation,
    )
    return RankingCandidate(
        candidate_id=candidate_id,
        kind=attribution.candidate_kind,
        eligible=eligible,
        quality_lower_bound=quality_lower_bound,
        cost=interval,
        economics_basis="measured",
        is_incumbent=is_incumbent,
        ineligibility_reason=ineligibility_reason,
    )
