#!/usr/bin/env python3
"""
FTF-R015 -- Enforce isolated concurrent coding execution and independent
integration.

Proves:
  1. A real wave of disjoint tasks executes with genuine wall-clock overlap
     (a `threading.Barrier` forces simultaneous start; each task's own
     recorded [started_at, finished_at] interval is shown to overlap every
     other's, not run sequentially) -- acceptance clause 1.
  2. Each task's workspace directory is a genuinely separate filesystem
     location containing only its own marker file, never a sibling's --
     real isolation, not assumed.
  3. Two claims whose write-sets overlap are mutually exclusive
     (WRITE_SET_CONFLICT), reused directly from FTF-H034's registry through
     `WorkspaceManager.open_workspace`.
  4. Completing an expired claim is denied (CLAIM_EXPIRED) -- expired
     leases.
  5. A task listing an unfinished same-wave sibling as a dependency is
     deferred, not run, until that sibling actually completes -- "unmerged
     dependencies."
  6. A task whose own prior evidence is stale (an overlapping later
     completion touched the same write-set) is rejected
     (STALE_DEPENDENCY) -- "stale branch dependencies."
  7. A task whose write-set touches a real CODEOWNERS-protected path is
     denied (PROTECTED_GATE_WRITE_DENIED) unless the wave's reviewer is the
     real protected-gate release authority -- "producer writes to gate
     state."
  8. A command outside the executor adapter's approved allowlist is denied
     (PERMISSION_BYPASS_DENIED), both directly and through a wave --
     "direct-tool permission bypass."
  9. A task whose own command exits nonzero is recorded as CI-failed and
     is never counted in `approved_for_downstream` -- "advancement after
     failed CI" is denied, while a sibling task in the same wave that
     genuinely passed is still approved.
  10. Opening a workspace from inside another already-active workspace's
      execution context is denied (NESTED_DELEGATION_DENIED).
  11. The orchestrator's constructor refuses a configured concurrency above
      the hard ceiling of 6.
  12. A wave whose declared reviewer is also one of its own claimants is
      denied (SELF_CERTIFICATION_DENIED) before any thread starts --
      "independent integrated merge gate."
  13. A wave whose total estimated cost exceeds its configured budget is
      denied (BUDGET_EXCEEDED) -- "whole-tree budgets."
  14. A wave requesting more tasks than the configured concurrency limit is
      denied (CONCURRENCY_LIMIT_EXCEEDED) before any thread starts.
  15. Real fault injection: temporarily removing the protected-gate-write
      check from the tracked `task_orchestrator.py` lets a fresh subprocess
      wrongly approve a protected-path write from an ordinary reviewer;
      restoring the file byte-identical rejects it again.

Run directly: python3 tests/implementation/test_safe_parallel_executor.py
"""

from __future__ import annotations

import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from scripts.spec_verification import TaskNode  # noqa: E402
from scripts.task_claim import ClaimDenied, TaskClaimRegistry  # noqa: E402
from tools.implementation.executor_adapter import (  # noqa: E402
    PermissionBypassDenied,
    execute_task,
)
from tools.implementation.task_orchestrator import (  # noqa: E402
    RELEASE_AUTHORITY_IDENTITY,
    OrchestrationDenied,
    TaskOrchestrator,
    WaveTask,
)
from tools.implementation.workspace_manager import (  # noqa: E402
    NestedDelegationDenied,
    WorkspaceManager,
)

FAILURES = []


def check(criterion_id, description, condition):
    if not condition:
        FAILURES.append(f"FAIL [{criterion_id}] {description}")


def _marker_command(task_id: str) -> tuple[str, ...]:
    # A short sleep before writing widens the overlap window past ordinary
    # process-start jitter, so a genuine-overlap assertion is meaningful
    # rather than a coin flip on a fast machine.
    return (
        "python3",
        "-c",
        f"import time; time.sleep(0.2); open('{task_id}.marker', 'w').write('{task_id}')",
    )


def _failing_command() -> tuple[str, ...]:
    return ("python3", "-c", "import sys, time; time.sleep(0.05); sys.exit(1)")


def _fixture_registry(*, certified: tuple[str, ...] = ("DEP",), **kwargs) -> TaskClaimRegistry:
    nodes = {
        "DEP": TaskNode(id="DEP", dependencies=[], qualification_scenarios=[], required=True),
        "R015-A": TaskNode(
            id="R015-A", dependencies=["DEP"], qualification_scenarios=[], required=True
        ),
        "R015-B": TaskNode(
            id="R015-B", dependencies=["DEP"], qualification_scenarios=[], required=True
        ),
        "R015-C": TaskNode(
            id="R015-C", dependencies=["DEP"], qualification_scenarios=[], required=True
        ),
        "R015-D": TaskNode(
            id="R015-D", dependencies=["R015-A"], qualification_scenarios=[], required=True
        ),
        "R015-E": TaskNode(
            id="R015-E", dependencies=["DEP"], qualification_scenarios=[], required=True
        ),
    }
    cert_states = {tid: "CERTIFIED" for tid in certified}
    return TaskClaimRegistry(tasks=nodes, certification_states=cert_states, **kwargs)


def test_real_overlapping_wave_isolated_and_integrated_ci():
    registry = _fixture_registry()
    wm = WorkspaceManager(registry)
    orch = TaskOrchestrator(registry, wm, max_concurrency=4, budget_usd=10.0)
    tasks = [
        WaveTask(
            "R015-A",
            "agent-1",
            _marker_command("R015-A"),
            write_set={"a.py"},
            estimated_cost_usd=1.0,
        ),
        WaveTask(
            "R015-B",
            "agent-2",
            _marker_command("R015-B"),
            write_set={"b.py"},
            estimated_cost_usd=1.0,
        ),
        WaveTask(
            "R015-C",
            "agent-3",
            _marker_command("R015-C"),
            write_set={"c.py"},
            estimated_cost_usd=1.0,
        ),
    ]
    result = orch.run_wave(tasks, reviewer_identity="reviewer-1")

    check(
        "FTF-R015",
        "all three disjoint tasks in the wave complete",
        set(result.completed) == {"R015-A", "R015-B", "R015-C"},
    )
    check("FTF-R015", "no task is CI-failed", result.ci_failed == set())
    check(
        "FTF-R015",
        "all three are approved for downstream advancement",
        result.approved_for_downstream == {"R015-A", "R015-B", "R015-C"},
    )
    check("FTF-R015", "the integrated CI step genuinely passes", result.integrated_ci_passed)

    intervals = [(r.started_at, r.finished_at) for r in result.execution_results.values()]
    latest_start = max(s for s, _ in intervals)
    earliest_finish = min(f for _, f in intervals)
    check(
        "FTF-R015",
        "every task's execution interval genuinely overlaps every other's "
        "(latest start happens before earliest finish) -- real concurrency, not sequential",
        latest_start < earliest_finish,
    )


def test_workspace_directories_are_genuinely_isolated():
    registry = _fixture_registry()
    wm = WorkspaceManager(registry)

    ws_a = wm.open_workspace("R015-A", "agent-1", write_set={"a.py"})
    ws_b = wm.open_workspace("R015-B", "agent-2", write_set={"b.py"})
    check("FTF-R015", "two tasks get distinct workspace directories", ws_a.path != ws_b.path)

    execute_task("R015-A", ws_a.path, _marker_command("R015-A"))
    execute_task("R015-B", ws_b.path, _marker_command("R015-B"))

    a_files = sorted(p.name for p in ws_a.path.iterdir())
    b_files = sorted(p.name for p in ws_b.path.iterdir())
    check("FTF-R015", "workspace A contains only its own marker", a_files == ["R015-A.marker"])
    check("FTF-R015", "workspace B contains only its own marker", b_files == ["R015-B.marker"])

    wm.close_workspace("R015-A", "agent-1", "reviewer-1")
    wm.close_workspace("R015-B", "agent-2", "reviewer-1")
    check(
        "FTF-R015",
        "closing without keep_dir removes the workspace directory",
        not ws_a.path.exists() and not ws_b.path.exists(),
    )


def test_write_set_conflict_denied():
    registry = _fixture_registry()
    wm = WorkspaceManager(registry)
    wm.open_workspace("R015-A", "agent-1", write_set={"shared.py"})
    denied = False
    code = None
    try:
        wm.open_workspace("R015-B", "agent-2", write_set={"shared.py"})
    except ClaimDenied as exc:
        denied = True
        code = exc.reason_code
    check(
        "FTF-R015",
        "two tasks whose write-sets overlap cannot both open a workspace",
        denied and code == "WRITE_SET_CONFLICT",
    )


def test_expired_lease_denied():
    fake_time = [1000.0]
    registry = _fixture_registry(clock=lambda: fake_time[0])
    wm = WorkspaceManager(registry)
    wm.open_workspace("R015-A", "agent-1", ttl_seconds=5.0, write_set={"a.py"})
    fake_time[0] += 10.0  # advance past the claim's TTL
    denied = False
    code = None
    try:
        wm.close_workspace("R015-A", "agent-1", "reviewer-1")
    except ClaimDenied as exc:
        denied = True
        code = exc.reason_code
    check(
        "FTF-R015",
        "completing a claim after its TTL expired is denied (CLAIM_EXPIRED)",
        denied and code == "CLAIM_EXPIRED",
    )


def test_unmerged_intra_wave_dependency_deferred():
    registry = _fixture_registry()
    wm = WorkspaceManager(registry)
    orch = TaskOrchestrator(registry, wm, max_concurrency=4, budget_usd=10.0)
    tasks = [
        WaveTask("R015-A", "agent-1", _marker_command("R015-A"), write_set={"a.py"}),
        WaveTask(
            "R015-D",
            "agent-2",
            _marker_command("R015-D"),
            write_set={"d.py"},
            depends_on_in_wave=frozenset({"R015-A"}),
        ),
    ]
    result = orch.run_wave(tasks, reviewer_identity="reviewer-1")
    check(
        "FTF-R015",
        "a task depending on an unfinished same-wave sibling is deferred, not run",
        result.deferred == ["R015-D"] and "R015-D" not in result.completed,
    )
    check(
        "FTF-R015", "the independent sibling still completes normally", "R015-A" in result.completed
    )


def test_stale_dependency_rejected():
    registry = _fixture_registry()
    wm = WorkspaceManager(registry)
    # Reproduce a real overlapping-completion staleness, the same mechanism
    # FTF-H034's own registry already proves: two claims whose write-sets
    # overlap, completed in sequence, mark the earlier one stale.
    registry.claim("R015-A", "agent-1", write_set={"shared.py"})
    registry.complete("R015-A", "agent-1", "reviewer-1")
    registry.claim("R015-B", "agent-2", write_set={"shared.py"})
    registry.complete("R015-B", "agent-2", "reviewer-1")
    check(
        "FTF-R015",
        "a real overlapping later completion marks the earlier task's evidence stale",
        wm.is_dependency_stale("R015-A"),
    )

    orch = TaskOrchestrator(registry, wm, max_concurrency=4, budget_usd=10.0)
    result = orch.run_wave(
        [WaveTask("R015-A", "agent-3", _marker_command("R015-A"), write_set={"other.py"})],
        reviewer_identity="reviewer-2",
    )
    check(
        "FTF-R015",
        "a task with stale evidence is rejected before its workspace ever opens",
        result.rejected.get("R015-A") == "STALE_DEPENDENCY",
    )


def test_protected_gate_write_denied_without_authority():
    registry = _fixture_registry()
    wm = WorkspaceManager(registry)
    orch = TaskOrchestrator(registry, wm, max_concurrency=4, budget_usd=10.0)
    result = orch.run_wave(
        [
            WaveTask(
                "R015-A",
                "agent-1",
                _marker_command("R015-A"),
                write_set={".github/workflows/ci.yml"},
            )
        ],
        reviewer_identity="session:ordinary-relay",
    )
    check(
        "FTF-R015",
        "a task writing a real CODEOWNERS-protected path is denied for an ordinary reviewer",
        result.rejected.get("R015-A") == "PROTECTED_GATE_WRITE_DENIED",
    )


def test_protected_gate_write_accepted_from_real_authority():
    registry = _fixture_registry()
    wm = WorkspaceManager(registry)
    orch = TaskOrchestrator(registry, wm, max_concurrency=4, budget_usd=10.0)
    result = orch.run_wave(
        [
            WaveTask(
                "R015-A",
                "agent-1",
                _marker_command("R015-A"),
                write_set={".github/workflows/ci.yml"},
            )
        ],
        reviewer_identity=RELEASE_AUTHORITY_IDENTITY,
    )
    check(
        "FTF-R015",
        "the same protected-path write is accepted when the reviewer is the real release authority",
        "R015-A" in result.approved_for_downstream,
    )


def test_permission_bypass_denied():
    threw = False
    try:
        execute_task("R015-A", ROOT, ("bash", "-c", "echo hi"))
    except PermissionBypassDenied:
        threw = True
    check(
        "FTF-R015",
        "a command outside the approved allowlist is denied directly by the adapter",
        threw,
    )

    registry = _fixture_registry()
    wm = WorkspaceManager(registry)
    orch = TaskOrchestrator(registry, wm, max_concurrency=4, budget_usd=10.0)
    result = orch.run_wave(
        [WaveTask("R015-A", "agent-1", ("bash", "-c", "echo hi"), write_set={"a.py"})],
        reviewer_identity="reviewer-1",
    )
    check(
        "FTF-R015",
        "a disallowed command submitted through a wave is denied "
        "(PERMISSION_BYPASS_DENIED), not silently run",
        result.rejected.get("R015-A") == "PERMISSION_BYPASS_DENIED",
    )


def test_advancement_after_failed_ci_denied():
    registry = _fixture_registry()
    wm = WorkspaceManager(registry)
    orch = TaskOrchestrator(registry, wm, max_concurrency=4, budget_usd=10.0)
    tasks = [
        WaveTask("R015-A", "agent-1", _marker_command("R015-A"), write_set={"a.py"}),
        WaveTask("R015-B", "agent-2", _failing_command(), write_set={"b.py"}),
    ]
    result = orch.run_wave(tasks, reviewer_identity="reviewer-1")
    check("FTF-R015", "the failing task is recorded CI-failed", "R015-B" in result.ci_failed)
    check(
        "FTF-R015",
        "a CI-failed task is never approved for downstream advancement",
        "R015-B" not in result.approved_for_downstream,
    )
    check(
        "FTF-R015",
        "a genuinely passing sibling in the same wave is still approved",
        "R015-A" in result.approved_for_downstream,
    )
    check(
        "FTF-R015",
        "the integrated CI step still passes for the approved subset alone",
        result.integrated_ci_passed,
    )


def test_nested_delegation_denied():
    registry = _fixture_registry()
    wm = WorkspaceManager(registry)
    workspace = wm.open_workspace("R015-A", "agent-1", write_set={"a.py"})
    denied = False
    with wm.active(workspace):
        try:
            wm.open_workspace("R015-B", "agent-1-sub", write_set={"b.py"})
        except NestedDelegationDenied:
            denied = True
    check(
        "FTF-R015",
        "opening a workspace from inside another active workspace's context is denied",
        denied,
    )
    wm.close_workspace("R015-A", "agent-1", "reviewer-1")


def test_concurrency_above_hard_ceiling_rejected():
    registry = _fixture_registry()
    wm = WorkspaceManager(registry)
    raised = False
    try:
        TaskOrchestrator(registry, wm, max_concurrency=7)
    except ValueError:
        raised = True
    check(
        "FTF-R015",
        "the orchestrator refuses a configured concurrency above the hard ceiling of 6",
        raised,
    )


def test_reviewer_self_certification_denied_at_wave_level():
    registry = _fixture_registry()
    wm = WorkspaceManager(registry)
    orch = TaskOrchestrator(registry, wm, max_concurrency=4, budget_usd=10.0)
    denied = False
    code = None
    try:
        orch.run_wave(
            [WaveTask("R015-A", "agent-1", _marker_command("R015-A"), write_set={"a.py"})],
            reviewer_identity="agent-1",
        )
    except OrchestrationDenied as exc:
        denied = True
        code = exc.reason_code
    check(
        "FTF-R015",
        "a wave whose reviewer is also one of its own claimants is denied before any thread starts",
        denied and code == "SELF_CERTIFICATION_DENIED",
    )


def test_whole_wave_budget_exceeded_denied():
    registry = _fixture_registry()
    wm = WorkspaceManager(registry)
    orch = TaskOrchestrator(registry, wm, max_concurrency=4, budget_usd=1.0)
    denied = False
    code = None
    try:
        orch.run_wave(
            [
                WaveTask(
                    "R015-A",
                    "agent-1",
                    _marker_command("R015-A"),
                    write_set={"a.py"},
                    estimated_cost_usd=5.0,
                )
            ],
            reviewer_identity="reviewer-1",
        )
    except OrchestrationDenied as exc:
        denied = True
        code = exc.reason_code
    check(
        "FTF-R015",
        "a wave whose total estimated cost exceeds its configured budget is denied",
        denied and code == "BUDGET_EXCEEDED",
    )


def test_concurrency_limit_exceeded_for_wave_size():
    registry = _fixture_registry()
    wm = WorkspaceManager(registry)
    orch = TaskOrchestrator(registry, wm, max_concurrency=2, budget_usd=10.0)
    denied = False
    code = None
    try:
        orch.run_wave(
            [
                WaveTask("R015-A", "agent-1", _marker_command("R015-A"), write_set={"a.py"}),
                WaveTask("R015-B", "agent-2", _marker_command("R015-B"), write_set={"b.py"}),
                WaveTask("R015-C", "agent-3", _marker_command("R015-C"), write_set={"c.py"}),
            ],
            reviewer_identity="reviewer-1",
        )
    except OrchestrationDenied as exc:
        denied = True
        code = exc.reason_code
    check(
        "FTF-R015",
        "a wave requesting more tasks than the configured concurrency limit is denied "
        "before any thread starts",
        denied and code == "CONCURRENCY_LIMIT_EXCEEDED",
    )


def test_fault_injection_protected_gate_check():
    """Real fault injection against the tracked module on disk: temporarily
    remove the protected-gate-write denial, run a fresh subprocess that
    imports the edited file and runs a wave writing a protected path from an
    ordinary reviewer, confirm it wrongly gets approved, then restore
    byte-identical and confirm it is rejected again."""
    target = ROOT / "tools" / "implementation" / "task_orchestrator.py"
    original = target.read_text(encoding="utf-8")

    guard_block = (
        "            touched_protected = [\n"
        "                f for f in t.write_set if _touches_protected_path(f, protected_paths)\n"
        "            ]\n"
        "            if touched_protected and reviewer_identity != RELEASE_AUTHORITY_IDENTITY:\n"
        '                rejected[t.task_id] = "PROTECTED_GATE_WRITE_DENIED"\n'
        "                continue\n"
    )
    check(
        "FTF-R015",
        "the protected-gate-write guard block is present in the real tracked source "
        "before fault injection",
        guard_block in original,
    )
    mutated = original.replace(guard_block, "")
    check("FTF-R015", "fault injection actually removed the guard block", mutated != original)
    target.write_text(mutated, encoding="utf-8")

    probe = f"""
import sys
sys.path.insert(0, {str(ROOT)!r})
from scripts.spec_verification import TaskNode
from scripts.task_claim import TaskClaimRegistry
from tools.implementation.task_orchestrator import TaskOrchestrator, WaveTask
from tools.implementation.workspace_manager import WorkspaceManager

nodes = {{
    "DEP": TaskNode(id="DEP", dependencies=[], qualification_scenarios=[], required=True),
    "R015-A": TaskNode(
        id="R015-A", dependencies=["DEP"], qualification_scenarios=[], required=True
    ),
}}
registry = TaskClaimRegistry(tasks=nodes, certification_states={{"DEP": "CERTIFIED"}})
wm = WorkspaceManager(registry)
orch = TaskOrchestrator(registry, wm, max_concurrency=4, budget_usd=10.0)
result = orch.run_wave(
    [WaveTask("R015-A", "agent-1",
              ("python3", "-c", "open('R015-A.marker', 'w').write('R015-A')"),
              write_set={{".github/workflows/ci.yml"}})],
    reviewer_identity="session:ordinary-relay",
)
print("APPROVED" if "R015-A" in result.approved_for_downstream else "DENIED")
"""

    try:
        proc = subprocess.run(
            [sys.executable, "-c", probe], capture_output=True, text=True, timeout=30
        )
        check(
            "FTF-R015",
            "with the guard removed, a fresh subprocess wrongly approves a protected-path "
            "write from an ordinary reviewer",
            proc.stdout.strip() == "APPROVED",
        )
    finally:
        target.write_text(original, encoding="utf-8")

    restored = target.read_text(encoding="utf-8")
    check(
        "FTF-R015",
        "the tracked module was restored byte-identical after fault injection",
        restored == original,
    )

    proc_after = subprocess.run(
        [sys.executable, "-c", probe], capture_output=True, text=True, timeout=30
    )
    check(
        "FTF-R015",
        "after restoring, the same protected-path write is denied again",
        proc_after.stdout.strip() == "DENIED",
    )


def main() -> int:
    test_real_overlapping_wave_isolated_and_integrated_ci()
    test_workspace_directories_are_genuinely_isolated()
    test_write_set_conflict_denied()
    test_expired_lease_denied()
    test_unmerged_intra_wave_dependency_deferred()
    test_stale_dependency_rejected()
    test_protected_gate_write_denied_without_authority()
    test_protected_gate_write_accepted_from_real_authority()
    test_permission_bypass_denied()
    test_advancement_after_failed_ci_denied()
    test_nested_delegation_denied()
    test_concurrency_above_hard_ceiling_rejected()
    test_reviewer_self_certification_denied_at_wave_level()
    test_whole_wave_budget_exceeded_denied()
    test_concurrency_limit_exceeded_for_wave_size()
    test_fault_injection_protected_gate_check()

    total_checks = 32
    if FAILURES:
        print(f"{len(FAILURES)} check(s) failed out of {total_checks}:")
        for failure in FAILURES:
            print(f"  {failure}")
        return 1
    print(f"All {total_checks} checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
