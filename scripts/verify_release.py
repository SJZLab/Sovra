#!/usr/bin/env python3
"""
verify_release.py — FTF-H001 whole-release gate (Tier 3, release-only).

Iterates every required task in spec/tasks.json and reports whether the
release is ready: every required task must have merged evidence in
spec/verification-map.json, an independently CERTIFIED entry in
docs/spec-matrix.yaml, AND its mapped commands must still pass live today.
This script is release-scope only — CLAUDE.md's Definition of Done is
explicit that "L4 gate passed before PROMOTE (ship-gate = release only;
relay never dispatches it)"; the relay's own per-task DoD stops at
verify_task.py --stage task.

Run: python3 scripts/verify_release.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import yaml  # noqa: E402

from scripts.spec_verification import REPO_ROOT, load_tasks, verify_task  # noqa: E402

MATRIX_FILE = REPO_ROOT / "docs" / "spec-matrix.yaml"


def main() -> int:
    nodes = load_tasks()
    matrix = yaml.safe_load(MATRIX_FILE.read_text(encoding="utf-8")) or {}

    required = sorted(task_id for task_id, node in nodes.items() if node.required)
    ready: list[str] = []
    blocked: list[str] = []
    failed: list[str] = []
    for task_id in required:
        certified = matrix.get(task_id, {}).get("certification_state") == "CERTIFIED"
        result = verify_task(task_id, "task")
        if result.outcome == "PASS" and certified:
            ready.append(task_id)
        elif result.outcome == "BLOCKED":
            blocked.append(task_id)
        else:
            failed.append(task_id)

    print(
        f"verify_release: {len(ready)}/{len(required)} required task(s) have live-passing, "
        "CERTIFIED evidence"
    )
    if failed:
        preview = failed[:10]
        suffix = "..." if len(failed) > 10 else ""
        print(f"  FAILED ({len(failed)}): {preview}{suffix}")
    print(f"  BLOCKED (not started): {len(blocked)}")

    if len(ready) != len(required):
        print("verify_release: NOT READY — full release requires every required task ready")
        return 1

    print("verify_release: READY")
    return 0


if __name__ == "__main__":
    sys.exit(main())
