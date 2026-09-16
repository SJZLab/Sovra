"""FTF-H001 — spec/task/evidence parsers and verifiers.

Backs scripts/validate_spec.py, scripts/verify_task.py and
scripts/verify_release.py. Resolves the frozen task/dependency/scenario graph
in spec/tasks.json against the real, currently re-executable verification
evidence recorded in spec/verification-map.json. A task absent from the map
has no merged evidence and is reported BLOCKED, never a fabricated PASS —
this module never substitutes its own judgment for an actual command's real
exit code.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
TASKS_FILE = REPO_ROOT / "spec" / "tasks.json"
SCENARIOS_FILE = REPO_ROOT / "spec" / "qualification-inventory.json"
MAP_FILE = REPO_ROOT / "spec" / "verification-map.json"

VALID_STAGES = {"task"}


class SpecLedgerError(ValueError):
    """A structural defect in the frozen task/dependency graph."""


@dataclass
class TaskNode:
    id: str
    dependencies: list[str]
    qualification_scenarios: list[str]
    required: bool


def load_tasks(path: Path = TASKS_FILE) -> dict[str, TaskNode]:
    data = json.loads(path.read_text(encoding="utf-8"))
    nodes: dict[str, TaskNode] = {}
    for task in data["tasks"]:
        task_id = task["id"]
        if task_id in nodes:
            raise SpecLedgerError(f"duplicate_id: {task_id} appears more than once in {path}")
        nodes[task_id] = TaskNode(
            id=task_id,
            dependencies=list(task.get("dependencies", [])),
            qualification_scenarios=list(task.get("qualification_scenarios", [])),
            required=bool(task.get("required", True)),
        )
    return nodes


def load_scenarios(path: Path = SCENARIOS_FILE) -> set[str]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return {s["id"] for s in data["scenarios"]}


def load_map(path: Path = MAP_FILE) -> dict[str, Any]:
    result: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return result


def check_structural(nodes: dict[str, TaskNode], scenarios: set[str]) -> list[str]:
    """missing_task / cyclic_dependency / missing_scenario checks.

    duplicate_id is rejected earlier, in load_tasks, since a dict can't hold
    two fixtures with the same key long enough to compare here.
    """
    errors: list[str] = []
    for node in nodes.values():
        for dep in node.dependencies:
            if dep not in nodes:
                errors.append(f"missing_task: {node.id} depends on unknown task {dep}")
        for scenario in node.qualification_scenarios:
            if scenario not in scenarios:
                errors.append(f"missing_scenario: {node.id} references unknown scenario {scenario}")

    # Kahn's algorithm, restricted to edges whose target actually exists so a
    # missing_task defect above doesn't also mask a real cycle below.
    graph: dict[str, list[str]] = {tid: [] for tid in nodes}
    indegree: dict[str, int] = {tid: 0 for tid in nodes}
    for node in nodes.values():
        for dep in node.dependencies:
            if dep in nodes:
                graph[dep].append(node.id)
                indegree[node.id] += 1
    queue = [tid for tid, deg in indegree.items() if deg == 0]
    seen = 0
    while queue:
        current = queue.pop()
        seen += 1
        for child in graph[current]:
            indegree[child] -= 1
            if indegree[child] == 0:
                queue.append(child)
    if seen != len(nodes):
        stuck = sorted(tid for tid, deg in indegree.items() if deg > 0)
        errors.append(
            f"cyclic_dependency: {len(stuck)} task(s) never reach zero in-degree: {stuck[:5]}"
        )
    return errors


def git_commit_exists(commit: str, cwd: Path = REPO_ROOT) -> bool:
    if not commit:
        return False
    result = subprocess.run(
        ["git", "cat-file", "-e", f"{commit}^{{commit}}"],
        cwd=cwd,
        capture_output=True,
        timeout=10,
    )
    return result.returncode == 0


def check_evidence(nodes: dict[str, TaskNode], evidence_map: dict[str, Any]) -> list[str]:
    """stale_commit / skipped_test / unknown_task / empty_commands checks."""
    errors: list[str] = []
    for task_id, entry in evidence_map.items():
        if task_id not in nodes:
            errors.append(f"unknown_task: verification-map references unknown task {task_id}")
            continue
        commit = entry.get("commit", "")
        if not git_commit_exists(commit):
            errors.append(
                f"stale_commit: {task_id} evidence commit {commit!r} does not resolve "
                "in this repository"
            )
        if int(entry.get("expected_skipped", 0)) > 0:
            errors.append(
                f"skipped_test: {task_id} evidence records "
                f"{entry['expected_skipped']} skipped test(s)"
            )
        if not entry.get("commands"):
            errors.append(f"empty_commands: {task_id} evidence has no verification commands")
    return errors


def validate(
    *,
    tasks_path: Path = TASKS_FILE,
    scenarios_path: Path = SCENARIOS_FILE,
    map_path: Path = MAP_FILE,
) -> list[str]:
    try:
        nodes = load_tasks(tasks_path)
    except SpecLedgerError as exc:
        return [str(exc)]
    scenarios = load_scenarios(scenarios_path)
    errors = check_structural(nodes, scenarios)
    evidence_map = load_map(map_path).get("tasks", {})
    errors += check_evidence(nodes, evidence_map)
    return errors


@dataclass
class VerifyResult:
    task_id: str
    stage: str
    outcome: str  # "PASS" | "FAIL" | "BLOCKED"
    reason: str
    commands_run: list[list[str]] = field(default_factory=list)


def verify_task(
    task_id: str,
    stage: str,
    *,
    map_path: Path = MAP_FILE,
    repo_root: Path = REPO_ROOT,
) -> VerifyResult:
    if stage not in VALID_STAGES:
        return VerifyResult(
            task_id, stage, "FAIL", f"unknown stage {stage!r}; valid stages: {sorted(VALID_STAGES)}"
        )

    full_map = load_map(map_path).get("tasks", {})
    entry = full_map.get(task_id)
    if entry is None:
        return VerifyResult(
            task_id, stage, "BLOCKED", f"{task_id} has no merged verification evidence yet"
        )

    commit = entry.get("commit", "")
    if not git_commit_exists(commit, cwd=repo_root):
        return VerifyResult(
            task_id,
            stage,
            "FAIL",
            f"evidence commit {commit!r} does not resolve in this repository (stale)",
        )

    commands: list[list[str]] = entry.get("commands") or []
    if not commands:
        return VerifyResult(task_id, stage, "FAIL", "empty command mapping")

    expected_marker = entry.get("expected_output_contains")
    timeout = int(entry.get("timeout_seconds", 300))
    ran: list[list[str]] = []
    for command in commands:
        ran.append(command)
        try:
            result = subprocess.run(
                command,
                shell=False,
                cwd=repo_root,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            return VerifyResult(
                task_id, stage, "FAIL", f"command timed out after {timeout}s: {command}", ran
            )
        if result.returncode != 0:
            tail = (result.stdout + result.stderr)[-2000:]
            return VerifyResult(
                task_id,
                stage,
                "FAIL",
                f"command exited {result.returncode}: {command}\n{tail}",
                ran,
            )
        if expected_marker and expected_marker not in result.stdout:
            return VerifyResult(
                task_id,
                stage,
                "FAIL",
                f"expected marker {expected_marker!r} not found in output of {command}",
                ran,
            )

    reason = f"{len(commands)} command(s) passed against evidence commit {commit}"
    return VerifyResult(task_id, stage, "PASS", reason, ran)
