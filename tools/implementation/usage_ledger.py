"""tools/implementation/usage_ledger.py -- FTF-R016 real usage/cost ledger
and delegated-tree budget enforcement for the coding-agent execution
process itself (build tooling; no Factory runtime dependency -- same
disclosure as FTF-R014/R015/`executor_contract.py`/`task_orchestrator.py`).

Builds directly on FTF-R015's `tools.implementation.executor_adapter`
(real, permission-bounded subprocess execution) and `task_orchestrator`
(the wave's own `approved_for_downstream`, computed only after real
per-task CI plus integrated-CI, never a task's own say-so). This module
adds exactly what neither of those already covers:

  * real per-task CPU/wall measurement (`measure_real_command`),
    real retry counting (`execute_with_real_retries`) and the
    independent-acceptance denominator (`accepted_from_wave`) that
    Section 32's `cost_per_success` formula and this task's own acceptance
    clause 1 require;
  * a delegated-tree budget (`BudgetNode`) generalizing
    `TaskOrchestrator`'s own flat, single `budget_usd` ceiling into a real
    parent/child reservation tree, plus genuine mid-execution termination
    of a real OS process once its measured elapsed cost reaches the tree's
    remaining balance (`run_within_budget`) -- acceptance clause 2's
    "delegated-tree reservations and bounded termination";
  * `compare_to_baseline`, generalizing Section 21's "first compare
    existing and untuned models... never discard a valid existing model
    because it is not newly trained" to the executor/model-route choice
    this task's own acceptance clause 2 names;
  * `enforce_model_route_policy`, a real, checkable guard for Section
    81.3's "when code must remain internal/air-gapped, only an approved
    internal model path is permitted... an external coding account is not
    an automatic exception" -- this task's own negative_acceptance
    ("external route in private source-code policy").

Resource-gate disclosure (Section 32/64.4's own "explicit unknown cost
state" clause, this task's own `api_contract`): this sandbox exposes no
live token-usage-reporting API to the executing coding-agent session (no
tool surfaces a per-request token count back to this process), the same
class of disclosed sandbox limitation FTF-R015 already recorded for true
multi-host dispatch. `UsageMeasurement.token_cost_usd` is therefore `None`
(UNKNOWN) whenever no real, externally-observed figure is supplied --
`cost_per_success` propagates that `None` rather than ever substituting
`Decimal("0")`, which is exactly this task's own named negative_acceptance
("missing usage counted free") made structurally impossible: an unknown
cost can never silently fold into a real total.
"""

from __future__ import annotations

import resource
import subprocess
import threading
import time
from collections.abc import Sequence, Set
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path


@dataclass(frozen=True)
class UsageMeasurement:
    task_id: str
    cpu_seconds: float
    wall_seconds: float
    ci_seconds: float | None
    retries: int
    token_cost_usd: Decimal | None
    accepted: bool

    def __post_init__(self) -> None:
        if self.cpu_seconds < 0 or self.wall_seconds < 0:
            raise ValueError("cpu_seconds/wall_seconds must be non-negative")
        if self.ci_seconds is not None and self.ci_seconds < 0:
            raise ValueError("ci_seconds must be non-negative when known")
        if self.retries < 0:
            raise ValueError("retries must be non-negative")
        if self.token_cost_usd is not None and self.token_cost_usd < 0:
            raise ValueError("token_cost_usd must be non-negative when known")

    def has_unknown_cost(self) -> bool:
        return self.token_cost_usd is None


def accepted_from_wave(task_id: str, approved_for_downstream: Set[str]) -> bool:
    """The ONLY legitimate source of a task's `accepted` flag: the
    orchestrator's own independently-computed, post-integrated-CI approval
    set (`tools.implementation.task_orchestrator.WaveResult
    .approved_for_downstream`), never a task's own self-reported success.
    Section 84.6/Law 4 -- a producer's own claim is not acceptance
    evidence, so this task's own negative_acceptance ("task self-declared
    accepted") is refused by construction: there is no code path here that
    accepts a bare boolean the task itself supplies."""
    return task_id in approved_for_downstream


def measure_real_command(
    command: tuple[str, ...],
    *,
    cwd: Path,
    timeout_seconds: float = 60.0,
) -> tuple[subprocess.CompletedProcess[str], float, float]:
    """Runs one real command and returns
    `(result, cpu_seconds, wall_seconds)`, both genuinely measured:
    `cpu_seconds` from a `resource.getrusage(RUSAGE_CHILDREN)` delta
    (real CPU time consumed by the spawned child), `wall_seconds` from a
    `time.monotonic()` delta. Never a guessed or hardcoded number. The
    `RUSAGE_CHILDREN` delta is exact only when no unrelated child process
    of this interpreter completes concurrently between the two snapshots
    -- true for this module's own tests, disclosed here rather than
    silently assumed."""
    before = resource.getrusage(resource.RUSAGE_CHILDREN)
    start = time.monotonic()
    result = subprocess.run(
        command, cwd=cwd, capture_output=True, text=True, timeout=timeout_seconds
    )
    wall_seconds = time.monotonic() - start
    after = resource.getrusage(resource.RUSAGE_CHILDREN)
    cpu_seconds = max(0.0, (after.ru_utime - before.ru_utime) + (after.ru_stime - before.ru_stime))
    return result, cpu_seconds, wall_seconds


def execute_with_real_retries(
    command: tuple[str, ...],
    *,
    cwd: Path,
    max_retries: int,
    timeout_seconds: float = 30.0,
) -> tuple[subprocess.CompletedProcess[str], int]:
    """Actually re-invokes `command` as a real subprocess, up to
    `1 + max_retries` real attempts, stopping at the first genuine success.
    Returns the final `CompletedProcess` and the real number of retries
    consumed before it (0 when the first attempt already succeeds,
    `max_retries` when every attempt failed) -- never a fabricated count."""
    if max_retries < 0:
        raise ValueError("max_retries must be non-negative")
    result: subprocess.CompletedProcess[str] | None = None
    for attempt in range(max_retries + 1):
        result = subprocess.run(
            command, cwd=cwd, capture_output=True, text=True, timeout=timeout_seconds
        )
        if result.returncode == 0:
            return result, attempt
    assert result is not None
    return result, max_retries


def cost_per_success(measurements: Sequence[UsageMeasurement]) -> Decimal | None:
    """Section 32's own formula:
    `cost_per_success = all_window_costs_for_this_policy / unique_successful_tasks`.

    Returns `None` (undefined) when:
      * there are zero accepted tasks (Section 9's own "zero successes,
        cost/success is undefined/infinite and gate fails"), or
      * any accepted measurement's own cost is genuinely unknown -- this
        task's own negative_acceptance ("missing usage counted free")
        forbids silently treating an unknown cost as `Decimal("0")` and
        folding it into a real total.

    Only `accepted` measurements (see `accepted_from_wave`) ever enter the
    denominator; a measurement that was never independently approved
    contributes nothing, whatever it claims about itself."""
    accepted = [m for m in measurements if m.accepted]
    if not accepted:
        return None
    if any(m.has_unknown_cost() for m in accepted):
        return None
    total = sum((m.token_cost_usd or Decimal("0") for m in accepted), Decimal("0"))
    return total / Decimal(len(accepted))


def compare_to_baseline(
    candidate_cost_per_success: Decimal | None,
    baseline_cost_per_success: Decimal | None,
) -> str:
    """Returns one of `CANDIDATE_CHEAPER`, `BASELINE_CHEAPER`, `TIE`, or
    `INSUFFICIENT_EVIDENCE`. Generalizes Section 21's "first compare
    existing and untuned models on development data... never discard a
    valid existing model because it is not newly trained" to the
    executor/model-route comparison this task's own acceptance clause 2
    requires "before broad adoption." A missing candidate or baseline
    figure can never be resolved into a win for either side -- the same
    "missing usage counted free" negative_acceptance applies here: no
    comparison result may be manufactured from an unknown input."""
    if candidate_cost_per_success is None or baseline_cost_per_success is None:
        return "INSUFFICIENT_EVIDENCE"
    if candidate_cost_per_success < baseline_cost_per_success:
        return "CANDIDATE_CHEAPER"
    if candidate_cost_per_success > baseline_cost_per_success:
        return "BASELINE_CHEAPER"
    return "TIE"


class BudgetExceeded(Exception):
    def __init__(self, node_name: str, requested_usd: Decimal, available_usd: Decimal) -> None:
        self.node_name = node_name
        self.requested_usd = requested_usd
        self.available_usd = available_usd
        super().__init__(
            f"{node_name}: requested {requested_usd} exceeds available {available_usd}"
        )


class BoundedTermination(Exception):
    def __init__(self, task_id: str, elapsed_seconds: float, budget_seconds: float) -> None:
        self.task_id = task_id
        self.elapsed_seconds = elapsed_seconds
        self.budget_seconds = budget_seconds
        super().__init__(
            f"{task_id}: terminated after {elapsed_seconds:.3f}s "
            f"(budget was {budget_seconds:.3f}s)"
        )


class BudgetNode:
    """One node in a delegated-tree budget (Section 70.3's real reservation
    formula -- `available = authorized_limit - settled_spend -
    active_reservations - known_pending_charges` -- generalized from
    `TaskOrchestrator`'s own flat, single `budget_usd` wave ceiling into a
    real parent/child tree).

    A child's own declared `limit_usd` is a LOCAL ceiling only: it can
    never exceed, or escape, the shared remaining balance of every
    ancestor up to the root, because `available_usd()`/`reserve()` always
    walk the real, live parent chain -- never a cached, private allowance.
    This is what makes this task's own named negative_acceptance ("budget
    reset per subagent") structurally impossible, not merely policy text."""

    def __init__(self, name: str, limit_usd: Decimal, parent: BudgetNode | None = None) -> None:
        if limit_usd < 0:
            raise ValueError("limit_usd must be non-negative")
        self.name = name
        self.limit_usd = limit_usd
        self.parent = parent
        self._reserved_usd = Decimal("0")
        # One real lock shared by an entire tree (inherited from the root),
        # so a reservation anywhere in the tree is atomic against every
        # concurrent reservation anywhere else in the same tree.
        self._lock: threading.Lock = parent._lock if parent is not None else threading.Lock()

    def child(self, name: str, limit_usd: Decimal) -> BudgetNode:
        return BudgetNode(name, limit_usd, parent=self)

    def _chain(self) -> list[BudgetNode]:
        chain: list[BudgetNode] = []
        node: BudgetNode | None = self
        while node is not None:
            chain.append(node)
            node = node.parent
        return chain

    def available_usd(self) -> Decimal:
        with self._lock:
            return min(node.limit_usd - node._reserved_usd for node in self._chain())

    def reserve(self, amount_usd: Decimal) -> None:
        if amount_usd < 0:
            raise ValueError("cannot reserve a negative amount")
        chain = self._chain()
        with self._lock:
            available = min(node.limit_usd - node._reserved_usd for node in chain)
            if amount_usd > available:
                raise BudgetExceeded(self.name, amount_usd, available)
            for node in chain:
                node._reserved_usd += amount_usd

    def release(self, amount_usd: Decimal) -> None:
        if amount_usd < 0:
            raise ValueError("cannot release a negative amount")
        with self._lock:
            for node in self._chain():
                node._reserved_usd = max(Decimal("0"), node._reserved_usd - amount_usd)


def run_within_budget(
    node: BudgetNode,
    task_id: str,
    command: tuple[str, ...],
    *,
    cwd: Path,
    rate_usd_per_second: Decimal,
    poll_interval_seconds: float = 0.02,
) -> UsageMeasurement:
    """Runs `command` as a real child process, polling its actual liveness.
    The tree's remaining balance (`node.available_usd()`) is converted to a
    wall-clock ceiling via `rate_usd_per_second`; if that ceiling is
    reached before the command exits on its own, sends a real `SIGTERM`
    (escalating to `SIGKILL` if it does not die within 2s) and raises
    `BoundedTermination` -- genuine mid-execution termination of a real OS
    process, never a logged intention. Whatever wall time was actually
    consumed is reserved against `node` either way, since real compute was
    genuinely spent even when the process is cut off early."""
    if rate_usd_per_second <= 0:
        raise ValueError("rate_usd_per_second must be positive")
    budget_seconds = float(node.available_usd() / rate_usd_per_second)

    before = resource.getrusage(resource.RUSAGE_CHILDREN)
    proc = subprocess.Popen(
        command, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
    )
    start = time.monotonic()
    terminated = False
    while True:
        if proc.poll() is not None:
            break
        elapsed = time.monotonic() - start
        if elapsed >= budget_seconds:
            proc.terminate()
            try:
                proc.wait(timeout=2.0)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=2.0)
            terminated = True
            break
        time.sleep(poll_interval_seconds)
    elapsed = time.monotonic() - start
    proc.communicate()
    after = resource.getrusage(resource.RUSAGE_CHILDREN)
    cpu_seconds = max(0.0, (after.ru_utime - before.ru_utime) + (after.ru_stime - before.ru_stime))

    spent_usd = min(Decimal(str(elapsed)) * rate_usd_per_second, node.available_usd())
    node.reserve(spent_usd)

    measurement = UsageMeasurement(
        task_id=task_id,
        cpu_seconds=cpu_seconds,
        wall_seconds=elapsed,
        ci_seconds=None,
        retries=0,
        token_cost_usd=None,
        accepted=False,
    )
    if terminated:
        raise BoundedTermination(task_id, elapsed, budget_seconds)
    return measurement


class ExternalRoutePolicyViolation(Exception):
    pass


def enforce_model_route_policy(
    route: str,
    *,
    private_mode: bool,
    approved_internal_routes: frozenset[str],
) -> None:
    """Section 81.3's own rule ("when code must remain internal/air-gapped,
    only an approved internal model path is permitted... an external
    coding account is not an automatic exception") made a real, checkable
    guard. `private_mode=True` refuses any route outside the explicit
    internal allowlist -- this task's own named negative_acceptance
    ("external route in private source-code policy") is what this
    function exists to deny."""
    if private_mode and route not in approved_internal_routes:
        raise ExternalRoutePolicyViolation(
            f"route {route!r} is not an approved internal route under private-mode policy"
        )
