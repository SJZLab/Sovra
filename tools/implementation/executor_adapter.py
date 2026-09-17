"""tools/implementation/executor_adapter.py -- FTF-R015 executor adapter.

Runs one real command for one claimed task inside its isolated workspace
directory and records genuine, non-fabricated execution evidence (exit
code, stdout/stderr, elapsed wall time) via a real `subprocess.run` call --
never a canned/simulated result. Section 81.3's "confirmation prompts are
not security boundaries; test... direct-call paths" is enforced
structurally here: only commands whose leading argv tokens match a small,
explicit allowlist may run at all.
"""

from __future__ import annotations

import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

# Deliberately small: this adapter's job is to prove workspace isolation and
# genuine pass/fail evidence, not to become a general command runner. `git`
# and `python3` cover every real command this task's own test suite needs;
# widening this list is itself a security-relevant change, not a default.
ALLOWED_COMMAND_PREFIXES: tuple[tuple[str, ...], ...] = (
    ("python3",),
    ("git",),
)


class PermissionBypassDenied(Exception):
    """A command outside the approved allowlist was submitted directly to
    the executor adapter. Rejected before any subprocess is spawned."""


@dataclass(frozen=True)
class ExecutionResult:
    task_id: str
    command: tuple[str, ...]
    exit_code: int
    stdout: str
    stderr: str
    duration_seconds: float
    started_at: float
    finished_at: float
    commit_sha: str | None = None

    @property
    def ci_passed(self) -> bool:
        return self.exit_code == 0


def _is_allowed(command: tuple[str, ...]) -> bool:
    return any(command[: len(prefix)] == prefix for prefix in ALLOWED_COMMAND_PREFIXES)


def _read_commit_sha(workspace_path: Path) -> str | None:
    """Real, non-fabricated commit evidence (acceptance clause 1's "record
    actual overlap, leases, commits, tests..."): a `git rev-parse HEAD` read
    against the task's own isolated workspace after its command ran. A task
    whose command never made a real commit (no `.git`, or an empty repo)
    genuinely has no commit to report -- this returns `None`, never a
    fabricated hash."""
    try:
        proc = subprocess.run(
            ("git", "rev-parse", "HEAD"),
            cwd=workspace_path,
            capture_output=True,
            text=True,
            timeout=10.0,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout.strip()


def execute_task(
    task_id: str,
    workspace_path: Path,
    command: tuple[str, ...],
    *,
    timeout_seconds: float = 30.0,
) -> ExecutionResult:
    if not command or not _is_allowed(command):
        raise PermissionBypassDenied(
            f"{task_id}: command {command!r} is outside the approved allowlist "
            f"{ALLOWED_COMMAND_PREFIXES}"
        )
    started_at = time.time()
    start = time.monotonic()
    proc = subprocess.run(
        command,
        cwd=workspace_path,
        capture_output=True,
        text=True,
        timeout=timeout_seconds,
    )
    duration = time.monotonic() - start
    finished_at = time.time()
    return ExecutionResult(
        task_id=task_id,
        command=command,
        exit_code=proc.returncode,
        stdout=proc.stdout,
        stderr=proc.stderr,
        duration_seconds=duration,
        started_at=started_at,
        finished_at=finished_at,
        commit_sha=_read_commit_sha(workspace_path),
    )
