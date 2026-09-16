#!/usr/bin/env python3
"""
FTF-0002 — Add formatting/lint/type checks.

Proves the real Python (ruff + mypy) and TypeScript (eslint + tsc) checks
configured in pyproject.toml / eslint.config.mjs / tsconfig*.json actually
catch a deliberately introduced defect (exit nonzero) and pass on
equivalent clean code (exit zero) — per FTF-0002's acceptance criterion
("Deliberately introduce formatting/lint/type defects for Python and TS;
each required command exits nonzero") and negative_acceptance ("No
exclusion of production package, altered severity or continue-on-error
to bypass a failing tool").

Fixtures are written inside the repo (under a TemporaryDirectory rooted
at REPO root, removed on exit) rather than under the system /tmp, because
both ruff/mypy's project config discovery and ESLint's flat-config base
path resolve relative to where the fixture lives — a file under /tmp is
outside ESLint's base path and gets silently ignored rather than linted,
which would make this test look like a pass for the wrong reason.

Run directly: python3 tests/tasks/test_ftf_0002.py
Also collectible by pytest once FTF-0003 (test harness) lands.
"""

import pathlib
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent.parent

FAILURES = []


def check(criterion_id, description, condition):
    if not condition:
        FAILURES.append(f"FAIL [{criterion_id}] {description}")


def run(cmd, cwd=ROOT):
    result = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    return result.returncode, result.stdout + result.stderr


BAD_PY = 'import os\n\n\ndef f(x: int) -> int:\n    y: int = "not an int"\n    return x\n'
GOOD_PY = "def f(x: int) -> int:\n    return x\n"
BAD_PY_FORMAT = "def f( x ):\n    return   x\n"

BAD_TS = (
    "const unused = 1;\n"
    "function f(x: number): number {\n"
    '  const y: number = "not a number";\n'
    "  return x;\n"
    "}\n"
)
GOOD_TS = "export function f(x: number): number {\n  return x;\n}\n"


def test_ruff_check_catches_python_lint_defect():
    with tempfile.TemporaryDirectory(dir=ROOT) as tmp:
        tmp_path = pathlib.Path(tmp)
        bad = tmp_path / "bad.py"
        bad.write_text(BAD_PY)
        code, output = run(["uv", "run", "ruff", "check", str(bad)])
        check(
            "FTF-0002",
            "ruff check exits nonzero on unused-import/unused-variable defect",
            code != 0,
        )
        check("FTF-0002", "ruff check reports the unused import (F401)", "F401" in output)

        good = tmp_path / "good.py"
        good.write_text(GOOD_PY)
        code, output = run(["uv", "run", "ruff", "check", str(good)])
        check("FTF-0002", "ruff check exits zero on clean equivalent code", code == 0)


def test_ruff_format_check_catches_python_format_defect():
    with tempfile.TemporaryDirectory(dir=ROOT) as tmp:
        tmp_path = pathlib.Path(tmp)
        bad = tmp_path / "bad_format.py"
        bad.write_text(BAD_PY_FORMAT)
        code, _ = run(["uv", "run", "ruff", "format", "--check", str(bad)])
        check("FTF-0002", "ruff format --check exits nonzero on misformatted code", code != 0)

        good = tmp_path / "good_format.py"
        good.write_text(GOOD_PY)
        code, _ = run(["uv", "run", "ruff", "format", "--check", str(good)])
        check("FTF-0002", "ruff format --check exits zero on clean equivalent code", code == 0)


def test_mypy_catches_python_type_defect():
    with tempfile.TemporaryDirectory(dir=ROOT) as tmp:
        tmp_path = pathlib.Path(tmp)
        bad = tmp_path / "bad_types.py"
        bad.write_text(BAD_PY)
        code, output = run(["uv", "run", "mypy", "--config-file", "pyproject.toml", str(bad)])
        check("FTF-0002", "mypy exits nonzero on str-assigned-to-int defect", code != 0)
        check("FTF-0002", "mypy reports the assignment error", "assignment" in output.lower())

        good = tmp_path / "good_types.py"
        good.write_text(GOOD_PY)
        code, _ = run(["uv", "run", "mypy", "--config-file", "pyproject.toml", str(good)])
        check("FTF-0002", "mypy exits zero on clean equivalent code", code == 0)


def test_eslint_catches_typescript_lint_defect():
    with tempfile.TemporaryDirectory(dir=ROOT) as tmp:
        tmp_path = pathlib.Path(tmp)
        bad = tmp_path / "bad.ts"
        bad.write_text(BAD_TS)
        code, output = run(["pnpm", "exec", "eslint", str(bad)])
        check("FTF-0002", "eslint exits nonzero on unused-variable defect", code != 0)
        check(
            "FTF-0002",
            "eslint reports @typescript-eslint/no-unused-vars",
            "no-unused-vars" in output,
        )

        good = tmp_path / "good.ts"
        good.write_text(GOOD_TS)
        code, _ = run(["pnpm", "exec", "eslint", str(good)])
        check("FTF-0002", "eslint exits zero on clean equivalent code", code == 0)


def test_tsc_catches_typescript_type_defect():
    with tempfile.TemporaryDirectory(dir=ROOT) as tmp:
        tmp_path = pathlib.Path(tmp)
        bad = tmp_path / "bad_types.ts"
        bad.write_text(BAD_TS)
        code, output = run(["pnpm", "exec", "tsc", "--noEmit", "--strict", str(bad)])
        check("FTF-0002", "tsc exits nonzero on string-assigned-to-number defect", code != 0)
        check("FTF-0002", "tsc reports TS2322 (type mismatch)", "TS2322" in output)

        good = tmp_path / "good_types.ts"
        good.write_text(GOOD_TS)
        code, _ = run(["pnpm", "exec", "tsc", "--noEmit", "--strict", str(good)])
        check("FTF-0002", "tsc exits zero on clean equivalent code", code == 0)


def test_workspace_wide_commands_are_clean():
    # Proves the committed repo itself passes its own gates right now — the
    # commands CI actually runs, not just isolated fixtures.
    code, output = run(["uv", "run", "ruff", "check", "."])
    check("FTF-0002", "repo-wide `ruff check .` passes", code == 0)
    code, output = run(["uv", "run", "ruff", "format", "--check", "."])
    check("FTF-0002", "repo-wide `ruff format --check .` passes", code == 0)
    code, output = run(["uv", "run", "mypy", "."])
    check("FTF-0002", "repo-wide `mypy .` passes", code == 0)
    code, output = run(["pnpm", "run", "lint"])
    check("FTF-0002", "repo-wide `pnpm run lint` passes", code == 0)
    code, output = run(["pnpm", "run", "format:check"])
    check("FTF-0002", "repo-wide `pnpm run format:check` passes", code == 0)
    code, output = run(["pnpm", "run", "typecheck"])
    check("FTF-0002", "repo-wide `pnpm run typecheck` passes", code == 0)


def main():
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()

    if FAILURES:
        print(f"FTF-0002 formatting/lint/type checks: {len(FAILURES)} check(s) failed")
        for f in FAILURES:
            print(f"  {f}")
        sys.exit(1)

    print("FTF-0002 formatting/lint/type checks: all checks passed")
    sys.exit(0)


if __name__ == "__main__":
    main()
