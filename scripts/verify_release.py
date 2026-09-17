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

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import yaml  # noqa: E402

from scripts.release_evidence_gate import (  # noqa: E402
    GateStatus,
    overall_status,
    run_release_evidence_gate,
    to_report,
)
from scripts.spec_verification import REPO_ROOT, load_tasks, verify_task  # noqa: E402

MATRIX_FILE = REPO_ROOT / "docs" / "spec-matrix.yaml"
RELEASE_OWNER = "SJZLab"
RELEASE_REPO = "Sovra"
RELEASE_BRANCH = "main"


def _current_commit_sha() -> str:
    import subprocess

    result = subprocess.run(  # noqa: S603, S607
        ["git", "rev-parse", "HEAD"], cwd=REPO_ROOT, capture_output=True, text=True, check=True
    )
    return result.stdout.strip()


def print_release_evidence_gate() -> bool:
    """FTF-H028: run the real, fail-closed CI/branch-protection/signature
    evidence gate against the current HEAD and print its report. Returns
    True only when every check is a real PASS -- BLOCKED_EXTERNAL and FAIL
    both count as not-ready, per Section 52's "Missing GitHub/GPU/provider
    access is BLOCKED_EXTERNAL and blocks full release.\" """
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    results = run_release_evidence_gate(
        owner=RELEASE_OWNER,
        repo=RELEASE_REPO,
        sha=_current_commit_sha(),
        branch=RELEASE_BRANCH,
        token=token,
    )
    report = to_report(results)
    print("verify_release: release-evidence-gate:")
    for check in report["checks"]:
        detail = f" — {check['detail']}" if check["detail"] else ""
        print(f"  {check['key']}: {check['status']}{detail}")
    return overall_status(results) is GateStatus.PASS


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

    tasks_ready = len(ready) == len(required)

    # Opt-in (FTF-H028): existing callers that invoke this script with no
    # flags -- every prior shift and certifier -- keep their exact previous
    # behavior/exit-code contract. --include-evidence-gate additionally
    # requires the real CI/branch-protection/signature evidence gate to
    # report PASS before the whole release can read READY.
    evidence_gate_ready = True
    if "--include-evidence-gate" in sys.argv[1:]:
        evidence_gate_ready = print_release_evidence_gate()

    if not tasks_ready or not evidence_gate_ready:
        print("verify_release: NOT READY — full release requires every required task ready")
        return 1

    print("verify_release: READY")
    return 0


if __name__ == "__main__":
    sys.exit(main())
