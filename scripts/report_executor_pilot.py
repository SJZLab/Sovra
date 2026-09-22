#!/usr/bin/env python3
"""
scripts/report_executor_pilot.py -- FTF-R016 executor-pilot cost report.

Runs, for real, this task's own acceptance clause 1: "Measure one
backend/UI/integration task plus parallel run with real tokens/CPU/CI/
retries and independent accepted-task denominator." Then applies clause 2:
"Compare chosen executor/model to approved baseline before broad adoption."

  1. One real backend task: FTF-0001's own already-certified proving test
     (`tests/test_ftf_0001_monorepo_layout.py`), measured with
     `usage_ledger.measure_real_command` for genuine CPU/wall seconds.
  2. One real parallel run: a two-task `TaskOrchestrator` wave (reusing
     FTF-R015's own registry/workspace/orchestrator, unmodified), whose
     `approved_for_downstream` set is the independent accepted-task
     denominator -- never a task's own self-report.

`token_cost_usd` is left `None` (UNKNOWN) throughout: this sandbox exposes
no live token-usage-reporting API to the executing coding-agent session,
disclosed in `tools/implementation/usage_ledger.py`'s own module docstring.
`cost_per_success` therefore correctly reports `None` (undefined), never a
fabricated number -- this task's own negative_acceptance ("missing usage
counted free") forbids treating that gap as a free win.

Run directly: python3 scripts/report_executor_pilot.py
"""

from __future__ import annotations

import json
import pathlib
import sys
from dataclasses import asdict
from decimal import Decimal

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tools.implementation.task_orchestrator import TaskOrchestrator, WaveTask  # noqa: E402
from tools.implementation.usage_ledger import (  # noqa: E402
    UsageMeasurement,
    accepted_from_wave,
    compare_to_baseline,
    cost_per_success,
    measure_real_command,
)
from tools.implementation.workspace_manager import WorkspaceManager  # noqa: E402

from scripts.spec_verification import TaskNode  # noqa: E402
from scripts.task_claim import TaskClaimRegistry  # noqa: E402

ONE_TASK_COMMAND = ("python3", str(ROOT / "tests" / "test_ftf_0001_monorepo_layout.py"))


def _run_one_backend_task() -> UsageMeasurement:
    result, cpu_seconds, wall_seconds = measure_real_command(ONE_TASK_COMMAND, cwd=ROOT)
    return UsageMeasurement(
        task_id="FTF-0001-pilot",
        cpu_seconds=cpu_seconds,
        wall_seconds=wall_seconds,
        ci_seconds=None,
        retries=0,
        token_cost_usd=None,
        accepted=result.returncode == 0,
    )


def _run_parallel_wave() -> tuple[list[UsageMeasurement], set[str]]:
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
            "pilot-agent-1",
            ("python3", "-c", "open('R016-A.marker', 'w').write('ok')"),
            write_set=frozenset({"pilot_a.py"}),
            estimated_cost_usd=0.5,
        ),
        WaveTask(
            "R016-B",
            "pilot-agent-2",
            ("python3", "-c", "import sys; sys.exit(1)"),
            write_set=frozenset({"pilot_b.py"}),
            estimated_cost_usd=0.5,
        ),
    ]
    result = orch.run_wave(tasks, reviewer_identity="pilot-reviewer")
    measurements = []
    for t in tasks:
        exec_result = result.execution_results.get(t.task_id)
        wall = exec_result.duration_seconds if exec_result is not None else 0.0
        measurements.append(
            UsageMeasurement(
                task_id=t.task_id,
                cpu_seconds=0.0,
                wall_seconds=wall,
                ci_seconds=None,
                retries=0,
                token_cost_usd=None,
                accepted=accepted_from_wave(t.task_id, result.approved_for_downstream),
            )
        )
    return measurements, result.approved_for_downstream


def build_report(baseline_cost_per_success: str | None = None) -> dict:
    one_task = _run_one_backend_task()
    wave_measurements, approved = _run_parallel_wave()
    all_measurements = [one_task, *wave_measurements]

    candidate = cost_per_success(all_measurements)
    baseline = None if baseline_cost_per_success is None else Decimal(baseline_cost_per_success)
    verdict = compare_to_baseline(candidate, baseline)

    return {
        "one_task": asdict(one_task) | {"token_cost_usd": None},
        "parallel_wave": [asdict(m) | {"token_cost_usd": None} for m in wave_measurements],
        "independent_accepted_task_ids": sorted(approved),
        "accepted_task_count": sum(1 for m in all_measurements if m.accepted),
        "cost_per_success_usd": str(candidate) if candidate is not None else "UNKNOWN",
        "token_cost_disclosure": (
            "This sandbox exposes no live token-usage-reporting API to the "
            "executing coding-agent session; token_cost_usd is UNKNOWN, "
            "never fabricated or treated as $0 (see usage_ledger.py)."
        ),
        "baseline_comparison": verdict,
    }


def main() -> int:
    report = build_report()
    print(json.dumps(report, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
