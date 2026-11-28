#!/usr/bin/env python3
"""Run one gate proof step only when the pull request touches its inputs.

Usage (ci.yml): python3 "$RUNNER_TEMP/ci_gate_step.py" uv run python3 tests/tasks/test_x.py

The gate job has about 190 per-task proof steps. On a pull request a step runs when its test file
changed, when the test file names a changed source file or module, when a suite-wide input
changed, or when the PR body has a `ci-full` line. Otherwise it is skipped and says so. On every
other event (the nightly run on main, a manual dispatch) every step runs. Any doubt runs the step:
a missing test file, an unreadable diff or an unknown event never skips.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

# A change to one of these can move the result of any proof.
FULL_RUN_FILES = {
    "scripts/ci_gate_step.py",
    "scripts/ci_policy.py",
    "pyproject.toml",
    "uv.lock",
    "package.json",
    "pnpm-lock.yaml",
    "pnpm-workspace.yaml",
}
FULL_LINE = re.compile(r"^[ \t]*ci-full[ \t]*$", re.M | re.I)
MODULE_NAME = re.compile(r"\b(?:apps|workers|packages|evals)(?:\.[A-Za-z_]\w*)+")
API_IMPORT = re.compile(r"^[ \t]*(?:from|import)[ \t]+(app(?:\.[A-Za-z_]\w*)+)", re.M)
PATH_LITERAL = re.compile(
    r"(?:apps|workers|packages|scripts|infra|evals|spec|docs|tools|tests|\.github)/[\w./-]*\w"
)
EXTENSION = re.compile(r"\.(?:py|tsx?|jsx?|ya?ml|md|json|toml|sql|sh)$")
# Imported by nearly every API test; a new router mount is covered by the item's own proof.
IGNORED = {"apps/api/app/main"}


def named_inputs(test_text: str) -> set[str]:
    """Extension-less repository paths a test file names, by import or by path literal."""
    names = {m.group(0).replace(".", "/") for m in MODULE_NAME.finditer(test_text)}
    names |= {"apps/api/" + m.group(1).replace(".", "/") for m in API_IMPORT.finditer(test_text)}
    names |= {EXTENSION.sub("", m.group(0)) for m in PATH_LITERAL.finditer(test_text)}
    return names - IGNORED


def decide(test: str, files: list[str], test_text: str | None, body: str) -> tuple[bool, str]:
    """(run?, reason). Fails toward running."""
    if test_text is None:
        return True, "test file not readable"
    if FULL_LINE.search(body or ""):
        return True, "ci-full in the PR body"
    wide = sorted(FULL_RUN_FILES.intersection(files))
    if wide:
        return True, f"suite-wide input changed: {wide[0]}"
    if test in files:
        return True, "the test file changed"
    names = named_inputs(test_text)
    for f in files:
        s = EXTENSION.sub("", f)
        for n in names:
            if s == n or s.startswith(n + "/") or n.startswith(s + "/"):
                return True, f"names changed input {f}"
    return False, "no input of this proof changed"


def changed_files(base: str) -> list[str]:
    out = subprocess.run(
        ["git", "diff", "--name-only", "--no-renames", f"{base}...HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    return sorted(line for line in out.splitlines() if line.strip())


def main(argv: list[str]) -> int:
    cmd = argv[1:]
    if not cmd:
        print("usage: ci_gate_step.py <command running one tests/...py file>", file=sys.stderr)
        return 2
    tests = [a for a in cmd if a.startswith("tests/") and a.endswith(".py")]
    run, why = True, "not a pull request"
    if os.environ.get("GITHUB_EVENT_NAME", "") == "pull_request" and len(tests) == 1:
        try:
            files = changed_files("origin/" + (os.environ.get("BASE_REF") or "main"))
            path = Path(tests[0])
            text = path.read_text(encoding="utf-8") if path.is_file() else None
            run, why = decide(tests[0], files, text, os.environ.get("PR_BODY", ""))
        except Exception as exc:  # any doubt runs the step
            run, why = True, f"could not decide ({exc})"
    if not run:
        print(f"gate-step: SKIPPED {tests[0]}: {why}; the nightly full run on main covers it")
        return 0
    print(f"gate-step: running ({why})", flush=True)
    return subprocess.call(cmd)


if __name__ == "__main__":
    sys.exit(main(sys.argv))
