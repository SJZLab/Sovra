#!/usr/bin/env python3
"""
FTF-0003 — Add test harnesses.

Proves the real pytest (Python) and Vitest (TypeScript) unit-test harnesses,
plus scripts/check_test_harness.py's negative-acceptance gate, actually
enforce FTF-0003's negative_acceptance criterion ("Zero collected tests,
focused .only or skipped required test must fail validation; remove
template sample tests") rather than silently passing.

Fixtures are written inside the repo (TemporaryDirectory rooted at REPO)
for the same reason as FTF-0002's test: tool base-path resolution can behave
differently for files under system /tmp.

Run directly: python3 tests/tasks/test_ftf_0003.py
Collectible by pytest too, but excluded from testpaths (pyproject.toml
scopes pytest to tests/unit) so it stays a task-evidence script like
tests/tasks/test_ftf_0002.py and tests/test_ftf_0001_monorepo_layout.py.
"""

import pathlib
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
GATE = ROOT / "scripts" / "check_test_harness.py"

FAILURES = []


def check(criterion_id, description, condition):
    if not condition:
        FAILURES.append(f"FAIL [{criterion_id}] {description}")


def run(cmd, cwd=ROOT):
    result = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    return result.returncode, result.stdout + result.stderr


def test_pytest_zero_collected_fails_validation():
    with tempfile.TemporaryDirectory(dir=ROOT) as tmp:
        empty = pathlib.Path(tmp) / "empty"
        empty.mkdir()
        report = pathlib.Path(tmp) / "empty.xml"
        code, _ = run(["uv", "run", "pytest", str(empty), f"--junitxml={report}"])
        check("FTF-0003", "pytest itself exits nonzero on zero collected tests", code != 0)
        gate_code, gate_out = run(["python3", str(GATE), "--junit", str(report)])
        check(
            "FTF-0003",
            "check_test_harness.py flags zero collected tests",
            gate_code != 0 and "zero collected tests" in gate_out,
        )


def test_pytest_skipped_test_fails_validation():
    with tempfile.TemporaryDirectory(dir=ROOT) as tmp:
        tmp_path = pathlib.Path(tmp)
        (tmp_path / "test_fixture.py").write_text(
            "import pytest\n"
            "\n"
            "def test_ok():\n"
            "    assert True\n"
            "\n"
            "@pytest.mark.skip(reason='fixture')\n"
            "def test_skipped():\n"
            "    assert True\n"
        )
        report = tmp_path / "skip.xml"
        run(["uv", "run", "pytest", str(tmp_path), f"--junitxml={report}"])
        gate_code, gate_out = run(["python3", str(GATE), "--junit", str(report)])
        check(
            "FTF-0003",
            "check_test_harness.py fails on a skipped required test",
            gate_code != 0 and "skipped" in gate_out,
        )

        clean = tmp_path / "clean.xml"
        (tmp_path / "test_fixture.py").write_text("def test_ok():\n    assert True\n")
        run(["uv", "run", "pytest", str(tmp_path), f"--junitxml={clean}"])
        gate_code, _ = run(["python3", str(GATE), "--junit", str(clean)])
        check("FTF-0003", "check_test_harness.py passes a clean report", gate_code == 0)


def test_vitest_zero_collected_fails_validation():
    with tempfile.TemporaryDirectory(dir=ROOT) as tmp:
        empty = pathlib.Path(tmp) / "empty"
        empty.mkdir()
        report = pathlib.Path(tmp) / "vempty.xml"
        code, _ = run(
            [
                "pnpm",
                "exec",
                "vitest",
                "run",
                "--root",
                str(empty),
                "--reporter=junit",
                f"--outputFile={report}",
            ]
        )
        check("FTF-0003", "Vitest itself exits nonzero on zero collected tests", code != 0)
        gate_code, gate_out = run(["python3", str(GATE), "--junit", str(report)])
        check(
            "FTF-0003",
            "check_test_harness.py flags Vitest's zero collected tests",
            gate_code != 0 and "zero collected tests" in gate_out,
        )


def test_vitest_focused_only_fails_validation():
    with tempfile.TemporaryDirectory(dir=ROOT) as tmp:
        tmp_path = pathlib.Path(tmp)
        (tmp_path / "x.test.ts").write_text(
            'import { test, expect } from "vitest";\n'
            "\n"
            'test.only("focused", () => { expect(true).toBe(true); });\n'
            'test("ignored", () => { expect(true).toBe(true); });\n'
        )
        report = tmp_path / "vonly.xml"
        run(
            [
                "pnpm",
                "exec",
                "vitest",
                "run",
                "--root",
                str(tmp_path),
                "--reporter=junit",
                f"--outputFile={report}",
            ]
        )
        gate_code, gate_out = run(["python3", str(GATE), "--junit", str(report)])
        check(
            "FTF-0003",
            "check_test_harness.py fails on a Vitest .only-focused test",
            gate_code != 0 and "skipped" in gate_out,
        )


def test_workspace_wide_commands_are_clean():
    # Proves the delivered harness actually runs clean on the real repo right
    # now — the commands CI actually runs, not just isolated fixtures.
    reports = ROOT / "reports"
    pytest_report = reports / "pytest-junit.xml"
    vitest_report = reports / "vitest-junit.xml"
    code, _ = run(["uv", "run", "pytest", "tests/unit", f"--junitxml={pytest_report}", "-ra"])
    check("FTF-0003", "repo-wide `pytest tests/unit` passes", code == 0)
    code, _ = run(
        [
            "pnpm",
            "exec",
            "vitest",
            "run",
            "--reporter=junit",
            f"--outputFile={vitest_report}",
        ]
    )
    check("FTF-0003", "repo-wide `vitest run` passes", code == 0)
    code, _ = run(
        [
            "python3",
            str(GATE),
            "--junit",
            str(pytest_report),
            "--junit",
            str(vitest_report),
        ]
    )
    check("FTF-0003", "check_test_harness.py passes on the real repo reports", code == 0)


def main():
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()

    if FAILURES:
        print(f"FTF-0003 test harnesses: {len(FAILURES)} check(s) failed")
        for f in FAILURES:
            print(f"  {f}")
        sys.exit(1)

    print("FTF-0003 test harnesses: all checks passed")
    sys.exit(0)


if __name__ == "__main__":
    main()
