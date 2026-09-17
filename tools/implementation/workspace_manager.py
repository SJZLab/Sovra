"""tools/implementation/workspace_manager.py -- FTF-R015 isolated
concurrent-coding-execution workspaces.

Binds one real, isolated filesystem directory to one
`scripts.task_claim.TaskClaimRegistry` claim per task. Composition, not
reimplementation: every dependency-readiness, write-set/lock exclusivity,
TTL-expiry, concurrency/GPU-quota and non-self-certification decision is
delegated to the already-certified FTF-H034 registry
(`scripts/task_claim.py`). This module owns only the two concerns that
registry never claimed to cover: genuine per-task filesystem isolation, and
denial of nested delegation (Sections 57A.2/84.5: "no nested delegation may
multiply the concurrency cap").
"""

from __future__ import annotations

import contextlib
import shutil
import tempfile
import threading
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from scripts.task_claim import Claim, CompletedClaim, TaskClaimRegistry

_active_workspace_depth = threading.local()


class NestedDelegationDenied(Exception):
    """Opening a workspace from inside another already-active workspace's
    execution context is denied structurally -- a nested sub-agent could
    otherwise open further workspaces of its own and silently multiply the
    declared concurrency cap beyond what the orchestrator ever approved."""


@dataclass(frozen=True)
class Workspace:
    task_id: str
    claimant: str
    path: Path
    claim: Claim


class WorkspaceManager:
    def __init__(self, registry: TaskClaimRegistry, *, base_dir: Path | None = None) -> None:
        self._registry = registry
        self.base_dir = (
            Path(base_dir) if base_dir is not None else Path(tempfile.mkdtemp(prefix="r015-wave-"))
        )
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._open: dict[str, Workspace] = {}

    def _workdir(self, task_id: str, claimant: str) -> Path:
        return self.base_dir / f"{task_id}__{claimant}"

    def open_workspace(
        self,
        task_id: str,
        claimant: str,
        *,
        ttl_seconds: float = 3600.0,
        write_set: frozenset[str] | set[str] = frozenset(),
        locks: frozenset[str] | set[str] = frozenset(),
        gpu_required: bool = False,
    ) -> Workspace:
        if getattr(_active_workspace_depth, "value", 0) > 0:
            raise NestedDelegationDenied(
                f"{task_id}: opening a workspace from inside another active workspace's "
                "execution context is denied (no nested delegation)"
            )
        claim = self._registry.claim(
            task_id,
            claimant,
            ttl_seconds=ttl_seconds,
            write_set=write_set,
            locks=locks,
            gpu_required=gpu_required,
        )
        workdir = self._workdir(task_id, claimant)
        # A genuinely new directory -- never a reused/stale one from a prior
        # claim of the same task -- proves isolation is real, not assumed.
        workdir.mkdir(parents=True, exist_ok=False)
        workspace = Workspace(task_id=task_id, claimant=claimant, path=workdir, claim=claim)
        with self._lock:
            self._open[task_id] = workspace
        return workspace

    def close_workspace(
        self,
        task_id: str,
        claimant: str,
        certifier: str,
        *,
        keep_dir: bool = False,
    ) -> CompletedClaim:
        completed = self._registry.complete(task_id, claimant, certifier)
        with self._lock:
            self._open.pop(task_id, None)
        if not keep_dir:
            shutil.rmtree(self._workdir(task_id, claimant), ignore_errors=True)
        return completed

    def discard_workspace_dir(self, task_id: str, claimant: str) -> None:
        """Removes a workspace directory kept alive by `keep_dir=True` at
        close time, once nothing further needs to read it (e.g. after the
        wave's integrated-CI step has copied out what it needed)."""
        shutil.rmtree(self._workdir(task_id, claimant), ignore_errors=True)

    def is_dependency_stale(self, task_id: str) -> bool:
        """True when this task's own merged evidence relies on a
        dependency whose write-set was later touched by an overlapping
        completion -- the "stale branch dependencies" rejection surface
        (Section 57A.8)."""
        return self._registry.is_verification_stale(task_id)

    @contextlib.contextmanager
    def active(self, workspace: Workspace) -> Iterator[None]:
        """Marks the current thread as executing inside `workspace` for the
        duration of the `with` block, so a nested `open_workspace` call on
        this thread is denied. Exercised directly by tests; the real
        orchestrator's worker threads never need to nest, since a task's
        own command runs as a plain subprocess, not as a second claimant."""
        depth = getattr(_active_workspace_depth, "value", 0)
        _active_workspace_depth.value = depth + 1
        try:
            yield
        finally:
            _active_workspace_depth.value = depth
