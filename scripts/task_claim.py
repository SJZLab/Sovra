"""FTF-H034 -- Safe parallel scheduler and task/evidence ownership.

Enforces task DAG readiness, expiring claims, write-set/named-lock
exclusivity, bounded (default 4 / hard ceiling 6) concurrency, a separate
GPU quota, a deterministic integration (merge) order, invalidation of
cached verification evidence for tasks whose write-set overlaps a newer
completion, and an independent-review guard (a claim's own claimant can
never also be its completion's certifier).

Every mutating operation on `TaskClaimRegistry` holds one internal
`threading.Lock` end-to-end, so concurrently racing callers (real OS
threads -- see the "safe parallel wave" proof in
tests/tasks/test_ftf_h034.py) serialize onto a single, deterministic
decision path instead of a caller-visible race. This module does not
attempt cross-process or cross-host locking: only one relay process runs
against `docs/implementation/TRACKER.json` in this repository today, and
claiming otherwise without a second, independently-verifiable process
actually racing against it would be exactly the kind of unearned claim
CLAUDE.md's Law 5 ("existence is not evidence") forbids.

Per this task's own negative_acceptance ("If runner lacks real subagent
capability record orchestration blocker, never fabricate workers"): this
repository has no live multi-agent dispatch harness able to hand a task to
a second, truly separate worker process today. The "actual safe parallel
wave" proof here is therefore built and evidenced at the level this
sandbox can genuinely exercise -- real concurrent OS threads racing the
registry's own locking and observing a correct, deterministic outcome --
rather than a fabricated claim that distinct subagent workers ran.
"""

from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from scripts.spec_verification import TASKS_FILE, TaskNode, load_tasks

REPO_ROOT = Path(__file__).resolve().parent.parent
SPEC_MATRIX_FILE = REPO_ROOT / "docs" / "spec-matrix.yaml"
TRACKER_FILE = REPO_ROOT / "docs" / "implementation" / "TRACKER.json"
ADDENDA_DIR = REPO_ROOT / "spec" / "addenda"

DEFAULT_MAX_CONCURRENCY = 4
HARD_MAX_CONCURRENCY = 6
DEFAULT_GPU_QUOTA = 1


class ClaimDenied(Exception):
    """A claim/completion request was structurally rejected.

    `reason_code` is a stable, machine-checkable identifier -- tests assert
    against it directly rather than parsing message prose.
    """

    def __init__(self, reason_code: str, detail: str) -> None:
        self.reason_code = reason_code
        super().__init__(f"{reason_code}: {detail}")


@dataclass(frozen=True)
class Claim:
    task_id: str
    claimant: str
    created_at: float
    ttl_seconds: float
    write_set: frozenset[str]
    locks: frozenset[str]
    gpu_required: bool = False

    def expires_at(self) -> float:
        return self.created_at + self.ttl_seconds

    def is_expired(self, now: float) -> bool:
        return now >= self.expires_at()


@dataclass(frozen=True)
class CompletedClaim:
    task_id: str
    claimant: str
    certifier: str
    created_at: float
    completed_at: float
    write_set: frozenset[str]


def load_certification_states(path: Path = SPEC_MATRIX_FILE) -> dict[str, str]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return {
        task_id: (entry.get("certification_state") or "NONE") for task_id, entry in data.items()
    }


class TaskClaimRegistry:
    def __init__(
        self,
        *,
        tasks: dict[str, TaskNode] | None = None,
        certification_states: dict[str, str] | None = None,
        max_concurrency: int = DEFAULT_MAX_CONCURRENCY,
        gpu_quota: int = DEFAULT_GPU_QUOTA,
        clock: Any = time.time,
    ) -> None:
        if max_concurrency > HARD_MAX_CONCURRENCY:
            raise ValueError(
                f"max_concurrency {max_concurrency} exceeds the hard ceiling {HARD_MAX_CONCURRENCY}"
            )
        if max_concurrency < 1:
            raise ValueError("max_concurrency must be at least 1")
        self._tasks = tasks if tasks is not None else load_tasks()
        self._certification_states = (
            certification_states
            if certification_states is not None
            else load_certification_states()
        )
        self._max_concurrency = max_concurrency
        self._gpu_quota = gpu_quota
        self._clock = clock
        self._lock = threading.Lock()
        self._active: dict[str, Claim] = {}
        self._completed: list[CompletedClaim] = []
        self._stale_verification: set[str] = set()

    def _expire_locked(self) -> None:
        now = self._clock()
        for task_id in [tid for tid, c in self._active.items() if c.is_expired(now)]:
            del self._active[task_id]

    def _dependencies_ready_locked(self, task_id: str) -> tuple[bool, str]:
        node = self._tasks.get(task_id)
        if node is None:
            return False, f"unknown task {task_id}"
        for dep in node.dependencies:
            state = self._certification_states.get(dep, "NONE")
            if state != "CERTIFIED":
                return False, f"dependency {dep} is {state}, not CERTIFIED"
        return True, ""

    def claim(
        self,
        task_id: str,
        claimant: str,
        *,
        ttl_seconds: float = 3600.0,
        write_set: frozenset[str] | set[str] = frozenset(),
        locks: frozenset[str] | set[str] = frozenset(),
        gpu_required: bool = False,
    ) -> Claim:
        write_set = frozenset(write_set)
        locks = frozenset(locks)
        with self._lock:
            self._expire_locked()

            ready, reason = self._dependencies_ready_locked(task_id)
            if not ready:
                raise ClaimDenied("DEPENDENCY_NOT_READY", f"{task_id}: {reason}")

            if task_id in self._active:
                raise ClaimDenied("TASK_ALREADY_CLAIMED", f"{task_id} already has an active claim")

            for existing in self._active.values():
                write_overlap = existing.write_set & write_set
                if write_overlap:
                    raise ClaimDenied(
                        "WRITE_SET_CONFLICT",
                        f"{task_id} write-set overlaps active claim {existing.task_id} "
                        f"on {sorted(write_overlap)}",
                    )
                lock_overlap = existing.locks & locks
                if lock_overlap:
                    raise ClaimDenied(
                        "LOCK_CONFLICT",
                        f"{task_id} requires lock(s) {sorted(lock_overlap)} "
                        f"already held by active claim {existing.task_id}",
                    )

            if len(self._active) >= self._max_concurrency:
                raise ClaimDenied(
                    "CONCURRENCY_LIMIT_EXCEEDED",
                    f"{len(self._active)} active claim(s) at the configured limit "
                    f"({self._max_concurrency}, hard ceiling {HARD_MAX_CONCURRENCY})",
                )

            if gpu_required:
                active_gpu = sum(1 for c in self._active.values() if c.gpu_required)
                if active_gpu >= self._gpu_quota:
                    raise ClaimDenied(
                        "GPU_QUOTA_EXCEEDED",
                        f"{active_gpu} active GPU claim(s) at the configured quota "
                        f"({self._gpu_quota})",
                    )

            new_claim = Claim(
                task_id=task_id,
                claimant=claimant,
                created_at=self._clock(),
                ttl_seconds=ttl_seconds,
                write_set=write_set,
                locks=locks,
                gpu_required=gpu_required,
            )
            self._active[task_id] = new_claim
            return new_claim

    def complete(self, task_id: str, claimant: str, certifier: str) -> CompletedClaim:
        with self._lock:
            self._expire_locked()
            existing = self._active.get(task_id)
            if existing is None:
                raise ClaimDenied(
                    "CLAIM_EXPIRED",
                    f"{task_id} has no active claim (expired, completed, or never claimed)",
                )
            if existing.claimant != claimant:
                raise ClaimDenied(
                    "CLAIMANT_MISMATCH",
                    f"{task_id} was claimed by {existing.claimant!r}, not {claimant!r}",
                )
            if certifier == claimant:
                raise ClaimDenied(
                    "SELF_CERTIFICATION_DENIED",
                    f"{task_id}: certifier {certifier!r} == claimant -- Law 4, no "
                    "self-certification",
                )

            del self._active[task_id]
            completed = CompletedClaim(
                task_id=task_id,
                claimant=claimant,
                certifier=certifier,
                created_at=existing.created_at,
                completed_at=self._clock(),
                write_set=existing.write_set,
            )
            self._completed.append(completed)

            for other in self._completed[:-1]:
                if other.write_set & completed.write_set:
                    self._stale_verification.add(other.task_id)

            return completed

    def merge_order(self) -> list[str]:
        """Deterministic integration order: by real claim creation time, then
        task ID -- independent of the order completions actually arrived in.
        """
        return [c.task_id for c in sorted(self._completed, key=lambda c: (c.created_at, c.task_id))]

    def is_verification_stale(self, task_id: str) -> bool:
        return task_id in self._stale_verification

    def active_count(self) -> int:
        with self._lock:
            self._expire_locked()
            return len(self._active)

    def snapshot(self) -> dict[str, Any]:
        """The real, current claim/lease/completion state -- the shape
        TRACKER.json's own header promises ("actual status, claims/leases,
        commits...") but this repository has never actually populated.
        """
        with self._lock:
            self._expire_locked()
            return {
                "active_claims": {
                    task_id: {
                        "claimant": c.claimant,
                        "claimed_at": c.created_at,
                        "expires_at": c.expires_at(),
                        "write_set": sorted(c.write_set),
                        "locks": sorted(c.locks),
                        "gpu_required": c.gpu_required,
                    }
                    for task_id, c in self._active.items()
                },
                "merge_order": self.merge_order(),
                "stale_verification": sorted(self._stale_verification),
            }


def is_addendum_task(task_id: str, addenda_dir: Path = ADDENDA_DIR) -> bool:
    """True if `task_id` is a Release-1 addendum (ADR 0002) -- a task added
    after `spec/tasks.json` was frozen at 300, with its own contract at
    `spec/addenda/<task_id>.json` instead of an entry in that frozen file."""
    return (addenda_dir / f"{task_id}.json").is_file()


def certified_ids_missing_from_tasks_json(
    certified_ids: Any,
    tasks_by_id: dict[str, Any],
    addenda_dir: Path = ADDENDA_DIR,
) -> set[str]:
    """CERTIFIED ids that `spec/tasks.json` does not genuinely reflect,
    excluding real addendum ids (ADR 0002 freezes that file at 300 -- an
    addendum id is never expected to appear there). The exclusion is
    narrow: an id with no `spec/addenda/<id>.json` file is never excused,
    so a genuine gap for a non-addendum task still reports missing."""
    return {
        tid
        for tid in certified_ids
        if not is_addendum_task(tid, addenda_dir)
        and tasks_by_id.get(tid, {}).get("status") != "CERTIFIED"
    }


def sync_certified_status(
    *,
    certification_states: dict[str, str] | None = None,
    spec_matrix: dict[str, Any] | None = None,
    tasks_path: Path = TASKS_FILE,
    tracker_path: Path = TRACKER_FILE,
    addenda_dir: Path = ADDENDA_DIR,
) -> list[str]:
    """Write the real, already-CERTIFIED status/evidence-hash into
    `spec/tasks.json` and `docs/implementation/TRACKER.json` for every task
    `docs/spec-matrix.yaml` (the established source of truth) already shows
    CERTIFIED.

    Both target files have carried every task as `NOT_STARTED`/empty
    evidence since the original bootstrap -- an honest, disclosed gap in
    what their own header comments promise ("actual status..."). This
    function fixes exactly that, and only that: it never marks a task
    CERTIFIED, sets commits/evidence, or invents a claim/lease for anything
    `docs/spec-matrix.yaml` does not already independently show certified.

    `spec/tasks.json` is frozen at 300 real tasks (ADR 0002): a CERTIFIED
    addendum id (`spec/addenda/<id>.json` exists) has no entry there by
    design and is skipped for that file, but still gets its own
    status/evidence entry created or updated in `TRACKER.json`, which is
    not frozen.

    Returns the sorted union of task IDs updated in either file.
    """
    if certification_states is None:
        certification_states = load_certification_states()
    if spec_matrix is None:
        spec_matrix = yaml.safe_load(SPEC_MATRIX_FILE.read_text(encoding="utf-8"))

    certified_ids = sorted(
        tid for tid, state in certification_states.items() if state == "CERTIFIED"
    )

    tasks_data = json.loads(tasks_path.read_text(encoding="utf-8"))
    tasks_by_id = {t["id"]: t for t in tasks_data["tasks"]}
    tracker_data = json.loads(tracker_path.read_text(encoding="utf-8"))
    tracker_tasks = tracker_data.setdefault("tasks", {})

    tasks_updated: list[str] = []
    tracker_updated: list[str] = []
    for task_id in certified_ids:
        entry = spec_matrix.get(task_id, {})
        evidence_hash = entry.get("evidence_hash", "")

        task = tasks_by_id.get(task_id)
        if task is not None and (
            task.get("status") != "CERTIFIED" or evidence_hash not in task.get("evidence", [])
        ):
            task["status"] = "CERTIFIED"
            task["evidence"] = [evidence_hash] if evidence_hash else task.get("evidence", [])
            tasks_updated.append(task_id)

        tracker_task = tracker_tasks.get(task_id)
        if tracker_task is None:
            if is_addendum_task(task_id, addenda_dir):
                tracker_tasks[task_id] = {
                    "status": "CERTIFIED",
                    "commits": [],
                    "evidence": [evidence_hash] if evidence_hash else [],
                    "independent_review": None,
                    "blockers": [],
                }
                tracker_updated.append(task_id)
        elif tracker_task.get("status") != "CERTIFIED" or (
            evidence_hash and evidence_hash not in tracker_task.get("evidence", [])
        ):
            tracker_task["status"] = "CERTIFIED"
            if evidence_hash and evidence_hash not in tracker_task.get("evidence", []):
                tracker_task["evidence"] = [evidence_hash]
            tracker_updated.append(task_id)

    if tasks_updated:
        tasks_path.write_text(
            json.dumps(tasks_data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
    if tracker_updated:
        tracker_path.write_text(
            json.dumps(tracker_data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )

    return sorted(set(tasks_updated) | set(tracker_updated))
