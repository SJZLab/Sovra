#!/usr/bin/env python3
"""
FTF-R016 -- Measure selected coding-model/executor pilot cost and enforce
budget tree.

Proves:
  1. A real two-task parallel wave (reusing FTF-R015's own registry/
     workspace/orchestrator, unmodified) produces an independent
     accepted-task denominator: the failing task is never counted, the
     passing task is -- `accepted_from_wave` reads only the orchestrator's
     own `approved_for_downstream`, never a task's own say-so.
  2. `cost_per_success` returns `None` (undefined) whenever any accepted
     measurement's own cost is genuinely unknown -- never `Decimal("0")`
     ("missing usage counted free" refused) -- and also when there are
     zero accepted tasks.
  3. `compare_to_baseline` returns `INSUFFICIENT_EVIDENCE` when either side
     is unknown, and the correct verdict when both are real numbers.
  4. A delegated-tree budget: a child node's own large declared limit
     cannot escape its parent's real remaining balance ("budget reset per
     subagent" refused by construction), and `reserve()` raises
     `BudgetExceeded` when the real available balance is insufficient.
  5. `run_within_budget` genuinely terminates a real, still-running OS
     process (SIGTERM) once its measured elapsed time reaches the tree's
     remaining budget -- proven by a marker file that is only ever written
     after the full sleep duration and is confirmed absent.
  6. `run_within_budget` lets a command that finishes within budget
     complete normally, with real non-negative CPU/wall measurements.
  7. `execute_with_real_retries` performs genuine repeated subprocess
     attempts against a real flaky command (a persisted counter file) and
     reports the real number of retries consumed, including the
     all-attempts-exhausted case.
  8. `enforce_model_route_policy` denies an external route under
     `private_mode=True` and allows an approved internal route, matching
     this task's own negative_acceptance ("external route in private
     source-code policy").
  9. Neither `usage_ledger.py` nor `report_executor_pilot.py` contains a
     hardcoded `$100`/`$300`/"guarantee" figure (negative_acceptance
     "reject $100/$300 guarantee").
  10. The real end-to-end pilot report (`report_executor_pilot.build_report`)
      runs one real backend task plus one real parallel wave, and reports
      `cost_per_success_usd == "UNKNOWN"` (token cost is genuinely
      unmeasurable in this sandbox, disclosed, never fabricated) while
      still reporting a correct independent accepted-task set.
  11. Real fault injection: temporarily removing the unknown-cost guard
      from the tracked `cost_per_success` lets a fresh subprocess wrongly
      compute a numeric cost from measurements with unknown cost;
      restoring the file byte-identical makes it correctly return
      `None` (unknown) again.

Run directly: python3 tests/implementation/test_executor_budget_limits.py
"""

from __future__ import annotations

import pathlib
import subprocess
import sys
import tempfile
from decimal import Decimal

ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

import scripts.report_executor_pilot as report_executor_pilot  # noqa: E402
from scripts.spec_verification import TaskNode  # noqa: E402
from scripts.task_claim import TaskClaimRegistry  # noqa: E402
from tools.implementation.task_orchestrator import TaskOrchestrator, WaveTask  # noqa: E402
from tools.implementation.usage_ledger import (  # noqa: E402
    BoundedTermination,
    BudgetExceeded,
    BudgetNode,
    ExternalRoutePolicyViolation,
    UsageMeasurement,
    accepted_from_wave,
    compare_to_baseline,
    cost_per_success,
    enforce_model_route_policy,
    execute_with_real_retries,
    run_within_budget,
)
from tools.implementation.workspace_manager import WorkspaceManager  # noqa: E402

FAILURES = []


def check(criterion_id, description, condition):
    if not condition:
        FAILURES.append(f"FAIL [{criterion_id}] {description}")


def _real_wave():
    nodes = {
        "DEP": TaskNode(id="DEP", dependencies=[], qualification_scenarios=[], required=True),
        "R016-A": TaskNode(
            id="R016-A", dependencies=["DEP"], qualification_scenarios=[], required=True
        ),
        "R016-B": TaskNode(
            id="R016-B", dependencies=["DEP"], qualification_scenarios=[], required=True
        ),
    }
    registry = TaskClaimRegistry(tasks=nodes, certification_states={"DEP": "CERTIFIED"})
    wm = WorkspaceManager(registry)
    orch = TaskOrchestrator(registry, wm, max_concurrency=4, budget_usd=10.0)
    tasks = [
        WaveTask(
            "R016-A",
            "agent-1",
            ("python3", "-c", "open('R016-A.marker', 'w').write('a')"),
            write_set={"a.py"},
        ),
        WaveTask(
            "R016-B",
            "agent-2",
            ("python3", "-c", "import sys; sys.exit(1)"),
            write_set={"b.py"},
        ),
    ]
    return orch.run_wave(tasks, reviewer_identity="reviewer-1")


def test_independent_accepted_task_denominator_and_unknown_cost():
    result = _real_wave()
    check(
        "FTF-R016",
        "the passing task is independently accepted",
        accepted_from_wave("R016-A", result.approved_for_downstream) is True,
    )
    check(
        "FTF-R016",
        "the failing task is never independently accepted, whatever it might self-report",
        accepted_from_wave("R016-B", result.approved_for_downstream) is False,
    )

    measurements = [
        UsageMeasurement(
            task_id="R016-A",
            cpu_seconds=0.01,
            wall_seconds=0.05,
            ci_seconds=None,
            retries=0,
            token_cost_usd=None,
            accepted=accepted_from_wave("R016-A", result.approved_for_downstream),
        ),
        UsageMeasurement(
            task_id="R016-B",
            cpu_seconds=0.01,
            wall_seconds=0.05,
            ci_seconds=None,
            retries=0,
            token_cost_usd=Decimal("0.001"),  # even if the failing task reports a real cost...
            accepted=accepted_from_wave("R016-B", result.approved_for_downstream),
        ),
    ]
    check(
        "FTF-R016",
        "cost_per_success is UNKNOWN (None), not a fabricated number, because the one "
        "genuinely accepted measurement has unknown cost -- never counted as free",
        cost_per_success(measurements) is None,
    )


def test_cost_per_success_zero_successes_is_undefined():
    measurements = [
        UsageMeasurement(
            task_id="X",
            cpu_seconds=0.0,
            wall_seconds=0.0,
            ci_seconds=None,
            retries=0,
            token_cost_usd=Decimal("5"),
            accepted=False,
        )
    ]
    check(
        "FTF-R016",
        "zero accepted tasks makes cost_per_success undefined (None), not zero or infinite",
        cost_per_success(measurements) is None,
    )


def test_cost_per_success_computes_when_known():
    measurements = [
        UsageMeasurement(
            task_id="A",
            cpu_seconds=0.0,
            wall_seconds=0.0,
            ci_seconds=None,
            retries=0,
            token_cost_usd=Decimal("2"),
            accepted=True,
        ),
        UsageMeasurement(
            task_id="B",
            cpu_seconds=0.0,
            wall_seconds=0.0,
            ci_seconds=None,
            retries=0,
            token_cost_usd=Decimal("4"),
            accepted=True,
        ),
    ]
    check(
        "FTF-R016",
        "cost_per_success averages known accepted costs correctly (2 + 4 over 2 = 3)",
        cost_per_success(measurements) == Decimal("3"),
    )


def test_compare_to_baseline():
    check(
        "FTF-R016",
        "missing candidate or baseline data is INSUFFICIENT_EVIDENCE, never a manufactured win",
        compare_to_baseline(None, Decimal("1")) == "INSUFFICIENT_EVIDENCE"
        and compare_to_baseline(Decimal("1"), None) == "INSUFFICIENT_EVIDENCE",
    )
    check(
        "FTF-R016",
        "a genuinely cheaper candidate is reported CANDIDATE_CHEAPER",
        compare_to_baseline(Decimal("1"), Decimal("2")) == "CANDIDATE_CHEAPER",
    )
    check(
        "FTF-R016",
        "a genuinely more expensive candidate is reported BASELINE_CHEAPER",
        compare_to_baseline(Decimal("3"), Decimal("2")) == "BASELINE_CHEAPER",
    )
    check(
        "FTF-R016",
        "equal costs are reported TIE",
        compare_to_baseline(Decimal("2"), Decimal("2")) == "TIE",
    )


def test_delegated_tree_budget_reset_per_subagent_refused():
    root = BudgetNode("root", Decimal("10"))
    root.reserve(Decimal("8"))
    check(
        "FTF-R016",
        "root has $2 real remaining after reserving $8 of $10",
        root.available_usd() == Decimal("2"),
    )

    child = root.child("subagent-1", Decimal("100"))  # a large nominal limit of its own
    check(
        "FTF-R016",
        "a child's own large declared limit cannot escape the real parent remaining balance "
        "-- budget is not reset per subagent",
        child.available_usd() == Decimal("2"),
    )

    denied = False
    try:
        child.reserve(Decimal("5"))
    except BudgetExceeded:
        denied = True
    check(
        "FTF-R016",
        "reserving more than the real shared remaining balance is denied (BudgetExceeded), "
        "even though the child's own limit_usd=100 would otherwise allow it",
        denied,
    )
    check(
        "FTF-R016",
        "a denied reservation leaves the tree's balance unchanged",
        root.available_usd() == Decimal("2") and child.available_usd() == Decimal("2"),
    )

    child.reserve(Decimal("2"))
    check(
        "FTF-R016",
        "a reservation within the real remaining balance succeeds and is reflected at the root",
        root.available_usd() == Decimal("0"),
    )


def test_bounded_termination_kills_a_real_running_process():
    with tempfile.TemporaryDirectory() as tmp:
        cwd = pathlib.Path(tmp)
        marker = cwd / "finished.marker"
        node = BudgetNode("term-test", Decimal("0.05"))  # tiny budget
        command = (
            "python3",
            "-c",
            f"import time; time.sleep(5); open({str(marker)!r}, 'w').write('done')",
        )
        terminated = False
        elapsed = None
        try:
            run_within_budget(
                node,
                "R016-term",
                command,
                cwd=cwd,
                rate_usd_per_second=Decimal("1"),  # budget_seconds = 0.05
            )
        except BoundedTermination as exc:
            terminated = True
            elapsed = exc.elapsed_seconds
        check(
            "FTF-R016",
            "a real long-running process is genuinely terminated once the tree budget "
            "is reached (BoundedTermination raised)",
            terminated,
        )
        check(
            "FTF-R016",
            "the process was cut off well before its natural 5s completion",
            elapsed is not None and elapsed < 3.0,
        )
        check(
            "FTF-R016",
            "the marker file (written only after the full 5s sleep) is genuinely absent -- "
            "the process was actually killed, not merely flagged",
            not marker.exists(),
        )


def test_run_within_budget_allows_completion_inside_budget():
    with tempfile.TemporaryDirectory() as tmp:
        cwd = pathlib.Path(tmp)
        node = BudgetNode("ok-test", Decimal("10"))
        command = ("python3", "-c", "print('hi')")
        measurement = run_within_budget(
            node, "R016-ok", command, cwd=cwd, rate_usd_per_second=Decimal("1")
        )
        check(
            "FTF-R016",
            "a command finishing well within budget completes normally with real "
            "non-negative measurements",
            measurement.wall_seconds >= 0.0 and measurement.cpu_seconds >= 0.0,
        )


def test_execute_with_real_retries():
    with tempfile.TemporaryDirectory() as tmp:
        cwd = pathlib.Path(tmp)
        state_file = cwd / "attempts.count"
        command = (
            "python3",
            "-c",
            "import pathlib, sys\n"
            f"p = pathlib.Path({str(state_file)!r})\n"
            "n = int(p.read_text()) if p.exists() else 0\n"
            "n += 1\n"
            "p.write_text(str(n))\n"
            "sys.exit(0 if n >= 3 else 1)\n",
        )
        result, retries = execute_with_real_retries(command, cwd=cwd, max_retries=5)
        check(
            "FTF-R016",
            "a real flaky command needing 2 real retries before succeeding reports retries == 2",
            result.returncode == 0 and retries == 2,
        )

    with tempfile.TemporaryDirectory() as tmp2:
        cwd2 = pathlib.Path(tmp2)
        always_fail = ("python3", "-c", "import sys; sys.exit(1)")
        result2, retries2 = execute_with_real_retries(always_fail, cwd=cwd2, max_retries=3)
        check(
            "FTF-R016",
            "a command that always fails exhausts all real retries and reports failure",
            result2.returncode != 0 and retries2 == 3,
        )


def test_external_route_policy():
    internal = frozenset({"internal://litellm/approved-route"})
    denied = False
    try:
        enforce_model_route_policy(
            "https://api.example-external-provider.com/v1",
            private_mode=True,
            approved_internal_routes=internal,
        )
    except ExternalRoutePolicyViolation:
        denied = True
    check(
        "FTF-R016",
        "an external route is denied under private_mode -- no automatic exception",
        denied,
    )

    allowed_exception = None
    try:
        enforce_model_route_policy(
            "internal://litellm/approved-route",
            private_mode=True,
            approved_internal_routes=internal,
        )
    except ExternalRoutePolicyViolation as exc:
        allowed_exception = exc
    check(
        "FTF-R016",
        "an approved internal route is allowed under private_mode",
        allowed_exception is None,
    )

    external_when_not_private = None
    try:
        enforce_model_route_policy(
            "https://api.example-external-provider.com/v1",
            private_mode=False,
            approved_internal_routes=internal,
        )
    except ExternalRoutePolicyViolation as exc:
        external_when_not_private = exc
    check(
        "FTF-R016",
        "an external route is allowed when private_mode is not requested",
        external_when_not_private is None,
    )


def test_no_hardcoded_price_guarantee():
    ledger_src = (ROOT / "tools" / "implementation" / "usage_ledger.py").read_text(encoding="utf-8")
    report_src = (ROOT / "scripts" / "report_executor_pilot.py").read_text(encoding="utf-8")
    forbidden = ("$100", "$300", "guarantee")
    hits = [
        term
        for term in forbidden
        for src in (ledger_src, report_src)
        if term.lower() in src.lower()
    ]
    check(
        "FTF-R016",
        "no hardcoded $100/$300 delivery-ceiling guarantee anywhere in this task's own code",
        hits == [],
    )


def test_real_pilot_report_end_to_end():
    report = report_executor_pilot.build_report()
    check(
        "FTF-R016",
        "the real pilot report includes one real backend-task measurement",
        report["one_task"]["task_id"] == "FTF-0001-pilot",
    )
    check(
        "FTF-R016",
        "the real pilot report includes the real parallel wave's two tasks",
        {m["task_id"] for m in report["parallel_wave"]} == {"R016-A", "R016-B"},
    )
    check(
        "FTF-R016",
        "the independent accepted-task set includes the passing wave task, "
        "excludes the failing one",
        "R016-A" in report["independent_accepted_task_ids"]
        and "R016-B" not in report["independent_accepted_task_ids"],
    )
    check(
        "FTF-R016",
        "cost_per_success is honestly UNKNOWN (token cost is unmeasurable in this sandbox), "
        "never a fabricated number and never $0",
        report["cost_per_success_usd"] == "UNKNOWN",
    )
    check(
        "FTF-R016",
        "the baseline comparison is INSUFFICIENT_EVIDENCE when no baseline cost is supplied",
        report["baseline_comparison"] == "INSUFFICIENT_EVIDENCE",
    )


def test_fault_injection_unknown_cost_guard():
    """Real fault injection against the tracked module on disk: temporarily
    remove the unknown-cost guard from `cost_per_success`, run a fresh
    subprocess that imports the edited file and shows it wrongly computes a
    numeric cost from measurements with unknown cost, then restore
    byte-identical and confirm it correctly reports unknown again."""
    target = ROOT / "tools" / "implementation" / "usage_ledger.py"
    original = target.read_text(encoding="utf-8")

    guard_block = "    if any(m.has_unknown_cost() for m in accepted):\n        return None\n"
    check(
        "FTF-R016",
        "the unknown-cost guard is present in the real tracked source before fault injection",
        guard_block in original,
    )
    mutated = original.replace(guard_block, "")
    check("FTF-R016", "fault injection actually removed the guard", mutated != original)
    target.write_text(mutated, encoding="utf-8")

    probe = f"""
import sys
from decimal import Decimal
sys.path.insert(0, {str(ROOT)!r})
from tools.implementation.usage_ledger import UsageMeasurement, cost_per_success

measurements = [
    UsageMeasurement(
        task_id="X", cpu_seconds=0.0, wall_seconds=0.0, ci_seconds=None,
        retries=0, token_cost_usd=None, accepted=True,
    )
]
result = cost_per_success(measurements)
print("NUMERIC" if result is not None else "UNKNOWN")
"""
    try:
        proc = subprocess.run(
            [sys.executable, "-c", probe], capture_output=True, text=True, timeout=30
        )
        check(
            "FTF-R016",
            "with the guard removed, a fresh subprocess wrongly treats an unknown cost as "
            "a real (zero) numeric total",
            proc.stdout.strip() == "NUMERIC",
        )
    finally:
        target.write_text(original, encoding="utf-8")

    restored = target.read_text(encoding="utf-8")
    check(
        "FTF-R016",
        "the tracked module was restored byte-identical after fault injection",
        restored == original,
    )

    proc_after = subprocess.run(
        [sys.executable, "-c", probe], capture_output=True, text=True, timeout=30
    )
    check(
        "FTF-R016",
        "after restoring, an unknown cost is correctly reported UNKNOWN again",
        proc_after.stdout.strip() == "UNKNOWN",
    )


def main() -> int:
    test_independent_accepted_task_denominator_and_unknown_cost()
    test_cost_per_success_zero_successes_is_undefined()
    test_cost_per_success_computes_when_known()
    test_compare_to_baseline()
    test_delegated_tree_budget_reset_per_subagent_refused()
    test_bounded_termination_kills_a_real_running_process()
    test_run_within_budget_allows_completion_inside_budget()
    test_execute_with_real_retries()
    test_external_route_policy()
    test_no_hardcoded_price_guarantee()
    test_real_pilot_report_end_to_end()
    test_fault_injection_unknown_cost_guard()

    total_checks = 34
    if FAILURES:
        print(f"{len(FAILURES)} check(s) failed out of {total_checks}:")
        for failure in FAILURES:
            print(f"  {failure}")
        return 1
    print(f"All {total_checks} checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
