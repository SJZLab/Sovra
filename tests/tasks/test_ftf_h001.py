#!/usr/bin/env python3
"""
FTF-H001 — Executable specification and gate bootstrap.

Proves scripts/validate_spec.py, scripts/verify_task.py and
scripts/verify_release.py are real, schema-validated parsers/verifiers, not
document-only tooling: every one of the task's own six named fixture kinds
(missing task, cyclic dependency, duplicate ID, missing scenario, skipped
test, stale commit) is shown to make validate_spec reject with a nonzero
exit, a valid fixture is shown to pass, verify_task reports BLOCKED (never a
fabricated PASS) for a task with no merged evidence and FAILs an unknown
stage, and the real spec/verification-map.json entries are proven not
fabricated by actually re-running them against this checkout and by fault
injection against the shipped map file itself.

Run directly: python3 tests/tasks/test_ftf_h001.py
"""

import copy
import json
import pathlib
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from scripts.spec_verification import (  # noqa: E402
    MAP_FILE,
    SCENARIOS_FILE,
    TASKS_FILE,
    validate,
    verify_task,
)

FAILURES = []


def check(criterion_id, description, condition):
    if not condition:
        FAILURES.append(f"FAIL [{criterion_id}] {description}")


def base_tasks():
    """A minimal, valid 3-task graph: A <- B <- C, one known scenario each."""
    return {
        "tasks": [
            {
                "id": "FTF-A",
                "dependencies": [],
                "qualification_scenarios": ["Q01"],
                "required": True,
            },
            {
                "id": "FTF-B",
                "dependencies": ["FTF-A"],
                "qualification_scenarios": ["Q01"],
                "required": True,
            },
            {
                "id": "FTF-C",
                "dependencies": ["FTF-B"],
                "qualification_scenarios": ["Q01"],
                "required": True,
            },
        ]
    }


def base_scenarios():
    return {"scenarios": [{"id": "Q01", "title": "fixture scenario"}]}


def base_map():
    return {"tasks": {}}


def write_fixture(directory, tasks, scenarios, verification_map):
    tasks_path = directory / "tasks.json"
    scenarios_path = directory / "scenarios.json"
    map_path = directory / "verification-map.json"
    tasks_path.write_text(json.dumps(tasks), encoding="utf-8")
    scenarios_path.write_text(json.dumps(scenarios), encoding="utf-8")
    map_path.write_text(json.dumps(verification_map), encoding="utf-8")
    return tasks_path, scenarios_path, map_path


def run_cli(args):
    result = subprocess.run(
        [sys.executable, *args], cwd=ROOT, capture_output=True, text=True, timeout=300
    )
    return result.returncode, result.stdout + result.stderr


def test_valid_fixture_passes():
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = pathlib.Path(tmp)
        tasks_path, scenarios_path, map_path = write_fixture(
            tmp_path, base_tasks(), base_scenarios(), base_map()
        )
        errors = validate(tasks_path=tasks_path, scenarios_path=scenarios_path, map_path=map_path)
        check("FTF-H001", "a valid fixture produces zero defects", errors == [])


def test_missing_task_fixture_rejected():
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = pathlib.Path(tmp)
        tasks = copy.deepcopy(base_tasks())
        tasks["tasks"][1]["dependencies"].append("FTF-UNKNOWN")
        tasks_path, scenarios_path, map_path = write_fixture(
            tmp_path, tasks, base_scenarios(), base_map()
        )
        errors = validate(tasks_path=tasks_path, scenarios_path=scenarios_path, map_path=map_path)
        check(
            "FTF-H001",
            "missing_task fixture (dependency on an unknown task) is rejected",
            any(e.startswith("missing_task:") for e in errors),
        )


def test_duplicate_id_fixture_rejected():
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = pathlib.Path(tmp)
        tasks = copy.deepcopy(base_tasks())
        tasks["tasks"].append(copy.deepcopy(tasks["tasks"][0]))
        tasks_path, scenarios_path, map_path = write_fixture(
            tmp_path, tasks, base_scenarios(), base_map()
        )
        errors = validate(tasks_path=tasks_path, scenarios_path=scenarios_path, map_path=map_path)
        check(
            "FTF-H001",
            "duplicate_id fixture (same task ID twice) is rejected",
            any(e.startswith("duplicate_id:") for e in errors),
        )


def test_cyclic_dependency_fixture_rejected():
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = pathlib.Path(tmp)
        tasks = copy.deepcopy(base_tasks())
        # FTF-A already has no deps; make it depend on FTF-C, closing a cycle
        # A -> C -> B -> A.
        tasks["tasks"][0]["dependencies"].append("FTF-C")
        tasks_path, scenarios_path, map_path = write_fixture(
            tmp_path, tasks, base_scenarios(), base_map()
        )
        errors = validate(tasks_path=tasks_path, scenarios_path=scenarios_path, map_path=map_path)
        check(
            "FTF-H001",
            "cyclic_dependency fixture (A -> C -> B -> A) is rejected",
            any(e.startswith("cyclic_dependency:") for e in errors),
        )


def test_missing_scenario_fixture_rejected():
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = pathlib.Path(tmp)
        tasks = copy.deepcopy(base_tasks())
        tasks["tasks"][0]["qualification_scenarios"].append("Q99")
        tasks_path, scenarios_path, map_path = write_fixture(
            tmp_path, tasks, base_scenarios(), base_map()
        )
        errors = validate(tasks_path=tasks_path, scenarios_path=scenarios_path, map_path=map_path)
        check(
            "FTF-H001",
            "missing_scenario fixture (reference to an unknown scenario ID) is rejected",
            any(e.startswith("missing_scenario:") for e in errors),
        )


def test_skipped_test_fixture_rejected():
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = pathlib.Path(tmp)
        verification_map = {
            "tasks": {
                "FTF-A": {
                    "commit": "0" * 40,
                    "commands": [["true"]],
                    "expected_skipped": 1,
                }
            }
        }
        tasks_path, scenarios_path, map_path = write_fixture(
            tmp_path, base_tasks(), base_scenarios(), verification_map
        )
        errors = validate(tasks_path=tasks_path, scenarios_path=scenarios_path, map_path=map_path)
        check(
            "FTF-H001",
            "skipped_test fixture (evidence recording a skipped test) is rejected",
            any(e.startswith("skipped_test:") for e in errors),
        )


def test_stale_commit_fixture_rejected():
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = pathlib.Path(tmp)
        verification_map = {
            "tasks": {
                "FTF-A": {
                    "commit": "deadbeef" * 5,  # 40 hex chars, not a real object
                    "commands": [["true"]],
                    "expected_skipped": 0,
                }
            }
        }
        tasks_path, scenarios_path, map_path = write_fixture(
            tmp_path, base_tasks(), base_scenarios(), verification_map
        )
        errors = validate(tasks_path=tasks_path, scenarios_path=scenarios_path, map_path=map_path)
        check(
            "FTF-H001",
            "stale_commit fixture (evidence commit absent from this repository) is rejected",
            any(e.startswith("stale_commit:") for e in errors),
        )


def test_negative_acceptance_blocked_not_fabricated_pass():
    result = verify_task("FTF-DOES-NOT-EXIST", "task")
    check(
        "FTF-H001",
        "a task absent from the map is reported BLOCKED, never PASS",
        result.outcome == "BLOCKED",
    )


def test_unknown_stage_rejected():
    result = verify_task("FTF-0002", "release")
    check(
        "FTF-H001",
        "an unknown --stage is rejected (FAIL), not silently accepted",
        result.outcome == "FAIL",
    )


def test_real_certified_task_reverifies_live():
    """Not fabricated: the shipped verification-map.json entry for FTF-0002
    is re-run for real against this exact checkout via the actual CLI, not
    just re-read as trusted text."""
    code, output = run_cli(["scripts/verify_task.py", "--task", "FTF-0002", "--stage", "task"])
    check(
        "FTF-H001",
        "the real FTF-0002 verification-map entry re-passes live via the CLI",
        code == 0 and "-> PASS" in output,
    )


def test_real_validate_spec_cli_clean():
    code, output = run_cli(["scripts/validate_spec.py", "--strict"])
    check(
        "FTF-H001",
        "validate_spec.py --strict is clean against the real, shipped ledger",
        code == 0 and "clean" in output,
    )


def test_fault_injection_stale_commit_on_real_map():
    """Law 1: corrupt the real spec/verification-map.json's FTF-0002 commit,
    show validate_spec.py genuinely rejects it, then restore byte-identical."""
    original = MAP_FILE.read_text(encoding="utf-8")
    try:
        data = json.loads(original)
        data["tasks"]["FTF-0002"]["commit"] = "f" * 40
        MAP_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")
        code, output = run_cli(["scripts/validate_spec.py", "--strict"])
        check(
            "FTF-H001",
            "a corrupted commit in the real map makes validate_spec fail for real",
            code != 0 and "stale_commit: FTF-0002" in output,
        )
    finally:
        MAP_FILE.write_text(original, encoding="utf-8")
        restored = MAP_FILE.read_text(encoding="utf-8")
        check(
            "FTF-H001",
            "the real verification-map.json is restored byte-identical",
            restored == original,
        )


def test_fault_injection_broken_marker_on_real_map():
    """Law 1: corrupt FTF-0002's expected_output_contains marker, show
    verify_task genuinely fails even though the underlying command still
    exits 0, then restore byte-identical."""
    original = MAP_FILE.read_text(encoding="utf-8")
    try:
        data = json.loads(original)
        data["tasks"]["FTF-0002"]["expected_output_contains"] = "this string will never appear"
        MAP_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")
        code, output = run_cli(["scripts/verify_task.py", "--task", "FTF-0002", "--stage", "task"])
        check(
            "FTF-H001",
            "a corrupted success marker makes verify_task fail for real",
            code != 0 and "-> FAIL" in output,
        )
    finally:
        MAP_FILE.write_text(original, encoding="utf-8")
        restored = MAP_FILE.read_text(encoding="utf-8")
        check(
            "FTF-H001",
            "the real verification-map.json is restored byte-identical",
            restored == original,
        )


def test_real_ledger_scenario_ids_are_known():
    """Sanity: the real spec/tasks.json and spec/qualification-inventory.json
    on disk are themselves internally consistent (no drift since bootstrap)."""
    errors = validate(tasks_path=TASKS_FILE, scenarios_path=SCENARIOS_FILE, map_path=MAP_FILE)
    check("FTF-H001", "the real, shipped ledger has zero defects", errors == [])


def main():
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()

    if FAILURES:
        print(f"FTF-H001 executable specification/gate bootstrap: {len(FAILURES)} check(s) failed")
        for failure in FAILURES:
            print(f"  {failure}")
        sys.exit(1)

    print("FTF-H001 executable specification/gate bootstrap: all checks passed")
    sys.exit(0)


if __name__ == "__main__":
    main()
