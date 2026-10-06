#!/usr/bin/env python3
"""
FTF-H034 -- Safe parallel scheduler and task/evidence ownership.

Proves:
  1. A task whose dependencies are not all CERTIFIED is denied a claim
     (DEPENDENCY_NOT_READY) -- "deny... dependent starts."
  2. A task already actively claimed cannot be claimed again
     (TASK_ALREADY_CLAIMED) -- "deny overlapping claims."
  3. Two claims whose write-sets or named locks overlap are mutually
     exclusive (WRITE_SET_CONFLICT / LOCK_CONFLICT) -- the migration/
     security-lock and write-set-ownership requirement.
  4. Concurrency is bounded: the constructor refuses a configured limit
     above the hard ceiling of 6, and the default limit of 4 denies a 5th
     concurrent claim (CONCURRENCY_LIMIT_EXCEEDED).
  5. GPU-requiring claims draw from a separate, independently bounded
     quota pool.
  6. Completing an expired claim is denied (CLAIM_EXPIRED) -- "deny...
     stale completion."
  7. A claim's own claimant can never also certify its completion
     (SELF_CERTIFICATION_DENIED) -- "independent integrated review."
  8. Integration (merge) order is deterministic -- by real claim creation
     time, then task ID -- regardless of the order completions arrive in.
  9. Completing a claim invalidates the cached-verification-stale flag for
     any earlier completion whose write-set overlaps it -- "invalidate
     stale CI after integration change."
  10. A real "safe parallel wave": many actual OS threads racing the same
      claim simultaneously (via a barrier, not a fabricated sequential
      loop) -- exactly one succeeds, the rest are denied, and the
      registry's internal state is left consistent.
  11. Real fault injection: disabling the self-certification guard in the
      tracked module lets a fresh subprocess construct a self-certified
      completion without it being rejected -- confirming the guard is
      load-bearing, not decorative.
  12. `sync_certified_status` writes real CERTIFIED status/evidence into
      isolated fixture copies of `spec/tasks.json` /
      `docs/implementation/TRACKER.json` for exactly the IDs
      `docs/spec-matrix.yaml` already shows CERTIFIED, and touches nothing
      else -- no fabricated status for a NOT_STARTED task.

Run directly: python3 tests/tasks/test_ftf_h034.py
"""

from __future__ import annotations

import json
import pathlib
import subprocess
import sys
import threading

ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from scripts.spec_verification import TaskNode  # noqa: E402
from scripts.task_claim import (  # noqa: E402
    ClaimDenied,
    TaskClaimRegistry,
    certified_ids_missing_from_tasks_json,
    is_addendum_task,
    sync_certified_status,
)

FAILURES = []


def check(criterion_id, description, condition):
    if not condition:
        FAILURES.append(f"FAIL [{criterion_id}] {description}")


def _fixture_registry(**kwargs):
    nodes = {
        "T-A": TaskNode(id="T-A", dependencies=[], qualification_scenarios=[], required=True),
        "T-B": TaskNode(id="T-B", dependencies=["T-A"], qualification_scenarios=[], required=True),
        "T-C": TaskNode(id="T-C", dependencies=["T-A"], qualification_scenarios=[], required=True),
        "T-D": TaskNode(id="T-D", dependencies=["T-B"], qualification_scenarios=[], required=True),
    }
    cert_states = {"T-A": "CERTIFIED"}
    return TaskClaimRegistry(tasks=nodes, certification_states=cert_states, **kwargs)


def test_dependency_not_ready_denies_claim():
    registry = _fixture_registry()
    registry.claim("T-B", "alice", write_set={"f1"})
    check(
        "FTF-H034",
        "a task whose deps are all CERTIFIED can be claimed",
        registry.active_count() == 1,
    )

    try:
        registry.claim("T-D", "bob", write_set={"f2"})
        denied = False
        code = None
    except ClaimDenied as exc:
        denied = True
        code = exc.reason_code
    check(
        "FTF-H034",
        "a task depending on a not-yet-CERTIFIED task is denied a claim",
        denied and code == "DEPENDENCY_NOT_READY",
    )


def test_overlapping_claim_denied():
    registry = _fixture_registry()
    registry.claim("T-A", "alice", write_set={"f1"})
    try:
        registry.claim("T-A", "bob", write_set={"f9"})
        denied, code = False, None
    except ClaimDenied as exc:
        denied, code = True, exc.reason_code
    check(
        "FTF-H034",
        "a task already actively claimed cannot be claimed a second time",
        denied and code == "TASK_ALREADY_CLAIMED",
    )


def test_write_set_conflict_denied():
    registry = _fixture_registry()
    registry.claim("T-A", "alice", write_set={"shared/file.py", "a.py"})
    try:
        registry.claim("T-B", "bob", write_set={"shared/file.py", "b.py"})
        denied, code = False, None
    except ClaimDenied as exc:
        denied, code = True, exc.reason_code
    check(
        "FTF-H034",
        "an overlapping write-set across two different tasks is denied",
        denied and code == "WRITE_SET_CONFLICT",
    )


def test_lock_conflict_denied():
    registry = _fixture_registry()
    registry.claim("T-A", "alice", write_set={"a.py"}, locks={"root:quality-contract"})
    try:
        registry.claim("T-B", "bob", write_set={"b.py"}, locks={"root:quality-contract"})
        denied, code = False, None
    except ClaimDenied as exc:
        denied, code = True, exc.reason_code
    check(
        "FTF-H034",
        "two tasks requiring the same named lock (e.g. a migration chain) are mutually exclusive",
        denied and code == "LOCK_CONFLICT",
    )
    registry.complete("T-A", "alice", "reviewer")
    registry.claim("T-B", "bob", write_set={"b.py"}, locks={"root:quality-contract"})
    check(
        "FTF-H034",
        "the lock is released once its holder completes, unblocking a genuinely queued task",
        registry.active_count() == 1,
    )


def test_concurrency_ceiling_enforced():
    try:
        TaskClaimRegistry(tasks={}, certification_states={}, max_concurrency=7)
        rejected = False
    except ValueError:
        rejected = True
    check(
        "FTF-H034",
        "the constructor refuses a configured concurrency limit above the hard ceiling of 6",
        rejected,
    )

    nodes = {
        f"T-{i}": TaskNode(id=f"T-{i}", dependencies=[], qualification_scenarios=[], required=True)
        for i in range(6)
    }
    registry = TaskClaimRegistry(tasks=nodes, certification_states={})
    for i in range(4):
        registry.claim(f"T-{i}", f"worker-{i}", write_set={f"f{i}.py"})
    try:
        registry.claim("T-4", "worker-4", write_set={"f4.py"})
        denied, code = False, None
    except ClaimDenied as exc:
        denied, code = True, exc.reason_code
    check(
        "FTF-H034",
        "a 5th concurrent claim is denied under the default max-4 concurrency limit",
        denied and code == "CONCURRENCY_LIMIT_EXCEEDED",
    )


def test_gpu_quota_is_separate_from_general_concurrency():
    nodes = {
        f"T-{i}": TaskNode(id=f"T-{i}", dependencies=[], qualification_scenarios=[], required=True)
        for i in range(6)
    }
    registry = TaskClaimRegistry(tasks=nodes, certification_states={}, gpu_quota=1)
    registry.claim("T-0", "gpu-worker-0", write_set={"g0"}, gpu_required=True)
    try:
        registry.claim("T-1", "gpu-worker-1", write_set={"g1"}, gpu_required=True)
        denied, code = False, None
    except ClaimDenied as exc:
        denied, code = True, exc.reason_code
    check(
        "FTF-H034",
        "a second GPU-requiring claim is denied once the separate GPU quota is exhausted",
        denied and code == "GPU_QUOTA_EXCEEDED",
    )
    registry.claim("T-2", "cpu-worker", write_set={"c2"}, gpu_required=False)
    check(
        "FTF-H034",
        "a non-GPU claim is unaffected by the GPU quota being exhausted",
        registry.active_count() == 2,
    )


def test_stale_completion_denied():
    fake_time = {"now": 1_000.0}
    nodes = {"T-A": TaskNode(id="T-A", dependencies=[], qualification_scenarios=[], required=True)}
    registry = TaskClaimRegistry(
        tasks=nodes, certification_states={}, clock=lambda: fake_time["now"]
    )
    registry.claim("T-A", "alice", ttl_seconds=60, write_set={"a.py"})
    fake_time["now"] += 120  # past the claim's TTL
    try:
        registry.complete("T-A", "alice", "reviewer")
        denied, code = False, None
    except ClaimDenied as exc:
        denied, code = True, exc.reason_code
    check(
        "FTF-H034",
        "completing an expired claim is denied rather than silently accepted",
        denied and code == "CLAIM_EXPIRED",
    )


def test_self_certification_denied():
    registry = _fixture_registry()
    registry.claim("T-A", "alice", write_set={"a.py"})
    try:
        registry.complete("T-A", "alice", "alice")
        denied, code = False, None
    except ClaimDenied as exc:
        denied, code = True, exc.reason_code
    check(
        "FTF-H034",
        "a claimant certifying its own completion is denied (Law 4: no self-certification)",
        denied and code == "SELF_CERTIFICATION_DENIED",
    )

    registry.complete("T-A", "alice", "reviewer-bob")
    check("FTF-H034", "an independent certifier is accepted", len(registry._completed) == 1)


def test_claimant_mismatch_denied():
    registry = _fixture_registry()
    registry.claim("T-A", "alice", write_set={"a.py"})
    try:
        registry.complete("T-A", "mallory", "reviewer")
        denied, code = False, None
    except ClaimDenied as exc:
        denied, code = True, exc.reason_code
    check(
        "FTF-H034",
        "completing someone else's claim under a different claimant identity is denied",
        denied and code == "CLAIMANT_MISMATCH",
    )


def test_merge_order_is_deterministic_regardless_of_completion_arrival_order():
    fake_time = {"now": 0.0}
    nodes = {
        tid: TaskNode(id=tid, dependencies=[], qualification_scenarios=[], required=True)
        for tid in ("T-A", "T-B", "T-C")
    }
    registry = TaskClaimRegistry(
        tasks=nodes, certification_states={}, clock=lambda: fake_time["now"]
    )

    fake_time["now"] = 1.0
    registry.claim("T-C", "w", write_set={"c"})
    fake_time["now"] = 2.0
    registry.claim("T-A", "w", write_set={"a"})
    fake_time["now"] = 3.0
    registry.claim("T-B", "w", write_set={"b"})

    # Complete in an arrival order that does not match creation order.
    fake_time["now"] = 10.0
    registry.complete("T-B", "w", "reviewer")
    registry.complete("T-A", "w", "reviewer")
    registry.complete("T-C", "w", "reviewer")

    check(
        "FTF-H034",
        "merge order reflects real claim creation time (C, A, B), not completion arrival order",
        registry.merge_order() == ["T-C", "T-A", "T-B"],
    )


def test_completion_invalidates_overlapping_earlier_verification():
    nodes = {
        tid: TaskNode(id=tid, dependencies=[], qualification_scenarios=[], required=True)
        for tid in ("T-A", "T-B")
    }
    registry = TaskClaimRegistry(tasks=nodes, certification_states={})
    registry.claim("T-A", "w", write_set={"shared/module.py"})
    registry.complete("T-A", "w", "reviewer")
    check(
        "FTF-H034",
        "a completion with no overlapping earlier completion starts out not stale",
        not registry.is_verification_stale("T-A"),
    )

    registry.claim("T-B", "w2", write_set={"shared/module.py", "other.py"})
    registry.complete("T-B", "w2", "reviewer2")
    check(
        "FTF-H034",
        "T-A's cached verification is invalidated once T-B's completion touches the same file",
        registry.is_verification_stale("T-A"),
    )
    check(
        "FTF-H034",
        "T-B itself (the newer completion) is not marked stale by its own completion",
        not registry.is_verification_stale("T-B"),
    )


def test_real_parallel_wave_exactly_one_claim_wins():
    nodes = {"T-A": TaskNode(id="T-A", dependencies=[], qualification_scenarios=[], required=True)}
    registry = TaskClaimRegistry(tasks=nodes, certification_states={})

    n_threads = 8
    barrier = threading.Barrier(n_threads)
    results: list[str] = []
    results_lock = threading.Lock()

    def worker(worker_id: int) -> None:
        barrier.wait()  # force genuinely simultaneous OS-level contention
        try:
            registry.claim("T-A", f"worker-{worker_id}", write_set={"a.py"})
            outcome = "WON"
        except ClaimDenied as exc:
            outcome = exc.reason_code
        with results_lock:
            results.append(outcome)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(n_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)

    check(
        "FTF-H034",
        "a real 8-thread parallel wave against the same task ID: exactly one thread wins the claim",
        results.count("WON") == 1,
    )
    check(
        "FTF-H034",
        "every losing thread was denied TASK_ALREADY_CLAIMED, not some other/undefined outcome",
        results.count("TASK_ALREADY_CLAIMED") == n_threads - 1,
    )
    check(
        "FTF-H034",
        "the registry's own state is left consistent after the race (exactly 1 active claim)",
        registry.active_count() == 1,
    )


def test_self_certification_guard_is_load_bearing_real_fault_injection():
    module_path = ROOT / "scripts" / "task_claim.py"
    original = module_path.read_text(encoding="utf-8")

    guard_block = (
        "            if certifier == claimant:\n"
        "                raise ClaimDenied(\n"
        '                    "SELF_CERTIFICATION_DENIED",\n'
        '                    f"{task_id}: certifier {certifier!r} == claimant -- Law 4, no "\n'
        '                    "self-certification",\n'
        "                )\n"
    )
    check(
        "FTF-H034",
        "the exact guard block to be removed is present in the tracked file before mutating it",
        guard_block in original,
    )
    mutated = original.replace(guard_block, "")
    check(
        "FTF-H034", "the fault-injection replacement actually changed the file", mutated != original
    )

    try:
        module_path.write_text(mutated, encoding="utf-8")
        script = (
            "import sys; sys.path.insert(0, '.')\n"
            "from scripts.task_claim import TaskClaimRegistry\n"
            "r = TaskClaimRegistry(tasks={}, certification_states={})\n"
            "from scripts.spec_verification import TaskNode\n"
            "n = TaskNode(id='T-A', dependencies=[], qualification_scenarios=[], required=True)\n"
            "r._tasks['T-A'] = n\n"
            "r.claim('T-A', 'alice', write_set={'a.py'})\n"
            "r.complete('T-A', 'alice', 'alice')\n"
            "print('CONSTRUCTED_WITHOUT_RAISING')\n"
        )
        after = subprocess.run(
            [sys.executable, "-c", script], cwd=ROOT, capture_output=True, text=True, timeout=30
        )
    finally:
        module_path.write_text(original, encoding="utf-8")

    restored = module_path.read_text(encoding="utf-8")
    check("FTF-H034", "the fault-injected module was restored byte-identical", restored == original)
    check(
        "FTF-H034",
        "with the self-certification guard disabled, a fresh subprocess can complete a "
        "self-certified claim without it being rejected -- confirming the guard is load-bearing",
        after.returncode == 0 and "CONSTRUCTED_WITHOUT_RAISING" in after.stdout,
    )


def test_sync_certified_status_touches_only_already_certified_ids(tmp_path=None):
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = pathlib.Path(tmp)
        tasks_path = tmp_dir / "tasks.json"
        tracker_path = tmp_dir / "tracker.json"

        tasks_path.write_text(
            json.dumps(
                {
                    "tasks": [
                        {"id": "T-CERTIFIED", "status": "NOT_STARTED", "evidence": []},
                        {"id": "T-PENDING", "status": "NOT_STARTED", "evidence": []},
                    ]
                }
            ),
            encoding="utf-8",
        )
        tracker_path.write_text(
            json.dumps(
                {
                    "tasks": {
                        "T-CERTIFIED": {"status": "NOT_STARTED", "evidence": []},
                        "T-PENDING": {"status": "NOT_STARTED", "evidence": []},
                    }
                }
            ),
            encoding="utf-8",
        )

        updated = sync_certified_status(
            certification_states={"T-CERTIFIED": "CERTIFIED", "T-PENDING": "PROVISIONAL"},
            spec_matrix={
                "T-CERTIFIED": {"evidence_hash": "deadbeef"},
                "T-PENDING": {"evidence_hash": ""},
            },
            tasks_path=tasks_path,
            tracker_path=tracker_path,
        )
        check(
            "FTF-H034",
            "sync_certified_status reports exactly the one CERTIFIED task it updated",
            updated == ["T-CERTIFIED"],
        )

        tasks_after = json.loads(tasks_path.read_text(encoding="utf-8"))
        by_id = {t["id"]: t for t in tasks_after["tasks"]}
        check(
            "FTF-H034",
            "the CERTIFIED task's real status/evidence hash is written into spec/tasks.json",
            by_id["T-CERTIFIED"]["status"] == "CERTIFIED"
            and by_id["T-CERTIFIED"]["evidence"] == ["deadbeef"],
        )
        check(
            "FTF-H034",
            "a merely-PROVISIONAL task is left untouched (NOT_STARTED, no fabricated status)",
            by_id["T-PENDING"]["status"] == "NOT_STARTED" and by_id["T-PENDING"]["evidence"] == [],
        )

        tracker_after = json.loads(tracker_path.read_text(encoding="utf-8"))
        check(
            "FTF-H034",
            "the CERTIFIED task's real status is written into TRACKER.json",
            tracker_after["tasks"]["T-CERTIFIED"]["status"] == "CERTIFIED",
        )
        check(
            "FTF-H034",
            "TRACKER.json's PROVISIONAL task is left untouched",
            tracker_after["tasks"]["T-PENDING"]["status"] == "NOT_STARTED",
        )


def test_real_committed_tracker_and_tasks_reflect_actual_certified_set():
    from scripts.task_claim import TASKS_FILE, TRACKER_FILE, load_certification_states

    cert_states = load_certification_states()
    certified_ids = {tid for tid, state in cert_states.items() if state == "CERTIFIED"}
    check(
        "FTF-H034",
        "the real repo has at least one CERTIFIED task to check against",
        len(certified_ids) > 0,
    )

    tasks_data = json.loads(TASKS_FILE.read_text(encoding="utf-8"))
    by_id = {t["id"]: t for t in tasks_data["tasks"]}
    tracker_data = json.loads(TRACKER_FILE.read_text(encoding="utf-8"))

    # spec/tasks.json is frozen at 300 real tasks (ADR 0002): a genuine
    # Release-1 addendum id has no entry there by design, so it is excused
    # from this specific file's check only -- never from the TRACKER check
    # below, and never for an id that is not a real addendum.
    missing_from_tasks = certified_ids_missing_from_tasks_json(certified_ids, by_id)
    all_reflected_in_tracker = all(
        tracker_data.get("tasks", {}).get(tid, {}).get("status") == "CERTIFIED"
        for tid in certified_ids
    )
    check(
        "FTF-H034",
        "spec/tasks.json's status field genuinely reflects every currently-CERTIFIED task, "
        "except a real spec/addenda/<id>.json addendum id which the frozen 300-task file "
        "never carries by design (this task's own delivery ran sync_certified_status "
        "against the real repo files)",
        len(missing_from_tasks) == 0,
    )
    check(
        "FTF-H034",
        "TRACKER.json's status field genuinely reflects every currently-CERTIFIED task, "
        "including addendum ids (TRACKER.json is not frozen)",
        all_reflected_in_tracker,
    )


def test_sync_certified_status_creates_tracker_entry_for_addendum_id_without_touching_tasks_json():
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = pathlib.Path(tmp)
        tasks_path = tmp_dir / "tasks.json"
        tracker_path = tmp_dir / "tracker.json"
        addenda_dir = tmp_dir / "addenda"
        addenda_dir.mkdir()
        (addenda_dir / "FTF-ADDX.json").write_text("{}", encoding="utf-8")

        original_tasks_text = json.dumps(
            {"tasks": [{"id": "T-CERTIFIED", "status": "CERTIFIED", "evidence": ["x"]}]}
        )
        tasks_path.write_text(original_tasks_text, encoding="utf-8")
        tracker_path.write_text(
            json.dumps({"tasks": {"T-CERTIFIED": {"status": "CERTIFIED", "evidence": ["x"]}}}),
            encoding="utf-8",
        )

        updated = sync_certified_status(
            certification_states={"FTF-ADDX": "CERTIFIED", "T-CERTIFIED": "CERTIFIED"},
            spec_matrix={
                "FTF-ADDX": {"evidence_hash": "addx-hash"},
                "T-CERTIFIED": {"evidence_hash": "x"},
            },
            tasks_path=tasks_path,
            tracker_path=tracker_path,
            addenda_dir=addenda_dir,
        )
        check(
            "FTF-H034",
            "sync_certified_status reports the addendum id as updated (it changed TRACKER.json)",
            "FTF-ADDX" in updated,
        )

        tasks_after = tasks_path.read_text(encoding="utf-8")
        check(
            "FTF-H034",
            "an addendum id with no spec/tasks.json entry leaves that frozen file untouched "
            "(ADR 0002: 300 tasks, never widened)",
            tasks_after == original_tasks_text,
        )

        tracker_after = json.loads(tracker_path.read_text(encoding="utf-8"))
        check(
            "FTF-H034",
            "a new TRACKER.json entry is created for the CERTIFIED addendum id, with its "
            "real evidence hash",
            tracker_after["tasks"].get("FTF-ADDX")
            == {
                "status": "CERTIFIED",
                "commits": [],
                "evidence": ["addx-hash"],
                "independent_review": None,
                "blockers": [],
            },
        )


def test_tasks_json_gap_exclusion_is_limited_to_real_addenda():
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        addenda_dir = pathlib.Path(tmp) / "addenda"
        addenda_dir.mkdir()
        (addenda_dir / "FTF-ADDX.json").write_text("{}", encoding="utf-8")

        missing = certified_ids_missing_from_tasks_json(
            {"FTF-ADDX", "FTF-NOTREAL"}, tasks_by_id={}, addenda_dir=addenda_dir
        )
        check(
            "FTF-H034",
            "a real addendum id (spec/addenda/<id>.json exists) missing from tasks.json is "
            "excused -- the frozen 300-task index never carries it by design",
            "FTF-ADDX" not in missing,
        )
        check(
            "FTF-H034",
            "a non-addendum CERTIFIED id missing from tasks.json is NOT excused -- turns RED "
            "so the exclusion cannot silently widen past spec/addenda",
            "FTF-NOTREAL" in missing,
        )
        check(
            "FTF-H034",
            "is_addendum_task agrees: false for a non-addendum id, true for a real one",
            is_addendum_task("FTF-NOTREAL", addenda_dir) is False
            and is_addendum_task("FTF-ADDX", addenda_dir) is True,
        )


def main():
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()

    if FAILURES:
        print(
            f"FTF-H034 safe parallel scheduler and task/evidence ownership: {len(FAILURES)} failed"
        )
        for f in FAILURES:
            print(f"  {f}")
        sys.exit(1)

    print("FTF-H034 safe parallel scheduler and task/evidence ownership: all checks passed")
    sys.exit(0)


if __name__ == "__main__":
    main()
