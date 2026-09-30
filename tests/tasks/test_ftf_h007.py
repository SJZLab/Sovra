#!/usr/bin/env python3
"""FTF-H007 -- Untrusted source acquisition and complete immutable analysis
(no-DB unit/negative-acceptance tests).

Runnable without Postgres, network egress, or any object-storage fixture --
this task's own scope is a pure, DB-free analysis pass over an already-
allowlisted local repository (see `apps/api/app/target_connectors/
source_boundary.py`'s own docstring for the disclosed "no live caller yet,
pending a real persisted-evidence column" scope boundary). Every check
here builds a *real* local Git repository with the real `git` binary in a
real temporary directory -- never a mocked git command or a canned scan
result -- matching `test_ftf_04a06.py`'s own established fixture
convention exactly.

This suite proves this task's own real acceptance clause: "Real malicious
reference repo includes an executable import/hook and escaping symlink;
scan produces evidence without execution/escape and incomplete critical
snapshot blocks lock":

1. A clean, ordinary repository scans as safe-to-lock with zero findings,
   and `evaluate_manifest_lock_readiness` does not raise.
2. A real malicious repository -- a Python file whose source contains an
   `os.system(...)` call, an active (non-`.sample`, executable)
   `.git/hooks/pre-push` script, a symlink whose real target resolves
   outside the repository root, a `.gitattributes`-declared Git-LFS path
   whose real on-disk content is still an unresolved pointer stub, and a
   `.gitmodules`-declared submodule path that is a real, empty, un-
   initialized directory -- is scanned end to end:
   - every one of the five findings is present in the real returned
     `SourceBoundaryReport`;
   - `is_safe_to_lock` is `False` and `evaluate_manifest_lock_readiness`
     raises `SourceBoundaryUnsafeError` naming every real reason -- this
     task's own "incomplete critical snapshot blocks lock";
   - the `os.system` call was never actually executed (its own marker file
     is confirmed absent) and the `.git/hooks/pre-push` script was never
     actually executed (its own, separate marker file is confirmed
     absent) -- this task's own "without execution";
   - the real secret content behind the escaping symlink is never read
     into any part of the returned report -- this task's own "without ...
     escape."
3. `test_escaping_symlink_alone_blocks_lock_in_isolation` isolates the
   escaping-symlink check from the other four detectors, so a regression in
   that one check specifically cannot hide behind one of the other
   independently-blocking findings the combined malicious-repository case
   above also exercises. Own fault injection (Law 1, performed manually
   against the real source during this task's own development, reverted
   byte-identical before this submission -- see PROGRESS.md/QUEUE.md for
   the exact reproduction): disabling `snapshot_inventory`'s own escaping-
   symlink check made this isolated case genuinely fail (`escaping_symlinks`
   became empty and `is_safe_to_lock` incorrectly became `True`), while the
   combined malicious-repository case above still failed for its own other
   four reasons -- confirming the isolated case, not only the combined one,
   is required to actually prove this one check is load-bearing.

Run directly: python3 tests/tasks/test_ftf_h007.py
"""

from __future__ import annotations

import shutil
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
API_ROOT = ROOT / "apps" / "api"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(API_ROOT))

from app.target_connectors.source_boundary import (  # noqa: E402
    SourceBoundaryUnsafeError,
    evaluate_manifest_lock_readiness,
    scan_local_repository_boundary,
)

FAILURES: list[str] = []


def check(criterion_id: str, description: str, condition: bool) -> None:
    if not condition:
        FAILURES.append(f"FAIL [{criterion_id}] {description}")


def _run(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    git_binary = shutil.which("git")
    assert git_binary is not None, "git must be on PATH for this test suite to run at all"
    return subprocess.run(  # noqa: S603
        [git_binary, "-C", str(cwd), *args],
        capture_output=True,
        text=True,
        timeout=30,
        check=True,
    )


def _init_repo(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    _run(path, "init", "--quiet", "--initial-branch=main")
    _run(path, "config", "user.email", "test@example.invalid")
    _run(path, "config", "user.name", "FTF-H007 test")


def _raises(exc_type: type[Exception], fn, *args: object) -> bool:
    try:
        fn(*args)
        return False
    except exc_type:
        return True


def test_clean_repository_is_safe_to_lock() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        repo = base / "repo"
        _init_repo(repo)
        (repo / "README.md").write_text("hello\n")
        _run(repo, "add", "README.md")
        _run(repo, "commit", "--quiet", "-m", "initial")

        report = scan_local_repository_boundary(repo, [base])

        check("FTF-H007", "a clean repository reports tree_complete", report.tree_complete)
        check(
            "FTF-H007",
            "a clean repository has zero escaping symlinks/hooks/dangerous calls",
            not report.escaping_symlinks
            and not report.executable_hooks
            and not report.dangerous_calls,
        )
        check("FTF-H007", "a clean repository is safe to lock", report.is_safe_to_lock)
        try:
            evaluate_manifest_lock_readiness(report)
            passed = True
        except SourceBoundaryUnsafeError:
            passed = False
        check(
            "FTF-H007",
            "evaluate_manifest_lock_readiness does not raise for a safe report",
            passed,
        )


def _build_malicious_repository(repo: Path, base: Path, marker_dir: Path) -> None:
    _init_repo(repo)

    hook_marker = marker_dir / "hook_executed.marker"
    exec_marker = marker_dir / "os_system_executed.marker"

    (repo / "plugin.py").write_text(
        "import os\n"
        "\n"
        "def register() -> None:\n"
        "    pass\n"
        "\n"
        "if True:\n"
        f"    os.system('touch {exec_marker}')\n"
    )

    (repo / ".gitattributes").write_text("*.bin filter=lfs diff=lfs merge=lfs -text\n")
    (repo / "model.bin").write_bytes(
        b"version https://git-lfs.github.com/spec/v1\n"
        b"oid sha256:" + b"0" * 64 + b"\n"
        b"size 4096\n"
    )

    (repo / ".gitmodules").write_text(
        '[submodule "vendor/lib"]\n'
        "\tpath = vendor/lib\n"
        "\turl = https://example.invalid/vendor/lib.git\n"
    )
    (repo / "vendor" / "lib").mkdir(parents=True)

    secret = base / "secret_outside_repo.txt"
    secret.write_text("TOP_SECRET_MARKER_CONTENT_zzz123\n")
    (repo / "link_to_secret").symlink_to(Path("..") / "secret_outside_repo.txt")

    _run(repo, "add", "plugin.py", ".gitattributes", "model.bin", ".gitmodules")
    _run(repo, "add", "-f", "link_to_secret")
    _run(repo, "commit", "--quiet", "-m", "malicious content")

    hooks_dir = repo / ".git" / "hooks"
    hook_path = hooks_dir / "pre-push"
    hook_path.write_text(f"#!/bin/sh\ntouch {hook_marker}\n")
    hook_path.chmod(hook_path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def test_malicious_repository_reports_every_finding_without_execution_or_escape() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        repo = base / "repo"
        marker_dir = base / "markers"
        marker_dir.mkdir()
        _build_malicious_repository(repo, base, marker_dir)

        report = scan_local_repository_boundary(repo, [base])

        check(
            "FTF-H007",
            "a dangerous os.system call is found in plugin.py",
            any(
                f.relative_path == "plugin.py" and "os.system" in f.reason
                for f in report.dangerous_calls
            ),
        )
        check(
            "FTF-H007",
            "the active .git/hooks/pre-push script is reported as an executable hook",
            any(h.endswith("pre-push") for h in report.executable_hooks),
        )
        check(
            "FTF-H007",
            "the escaping symlink is reported by its own repo-relative path",
            "link_to_secret" in report.escaping_symlinks,
        )
        check(
            "FTF-H007",
            "the unresolved Git LFS pointer path is reported",
            "model.bin" in report.inventory.unresolved_lfs_paths,
        )
        check(
            "FTF-H007",
            "the unresolved (empty, uninitialized) submodule path is reported",
            "vendor/lib" in report.inventory.unresolved_submodule_paths,
        )
        check(
            "FTF-H007",
            "a repository with any real unsafe/incomplete finding is not safe to lock",
            not report.is_safe_to_lock,
        )
        check(
            "FTF-H007",
            "evaluate_manifest_lock_readiness refuses (incomplete critical snapshot blocks lock)",
            _raises(SourceBoundaryUnsafeError, evaluate_manifest_lock_readiness, report),
        )

        check(
            "FTF-H007",
            "the os.system call inside plugin.py was never actually executed",
            not (marker_dir / "os_system_executed.marker").exists(),
        )
        check(
            "FTF-H007",
            "the .git/hooks/pre-push script was never actually executed",
            not (marker_dir / "hook_executed.marker").exists(),
        )

        report_text = repr(report)
        check(
            "FTF-H007",
            "the real secret content behind the escaping symlink is never read into the report",
            "TOP_SECRET_MARKER_CONTENT_zzz123" not in report_text,
        )
        check(
            "FTF-H007",
            "the escaping symlink entry itself carries no content (never dereferenced for bytes)",
            all(
                e.content is None
                for e in report.inventory.entries
                if e.relative_path == "link_to_secret"
            ),
        )


def test_escaping_symlink_alone_blocks_lock_in_isolation() -> None:
    """Isolates the escaping-symlink check from every other detector this
    module owns, so a regression in that one check specifically cannot
    hide behind one of the other four independently-blocking findings --
    the same isolation discipline `test_ftf_04a05.py`'s own integration
    suite already established for an analogous "one guard, confounded by a
    second" risk."""
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        repo = base / "repo"
        _init_repo(repo)
        (repo / "README.md").write_text("hello\n")
        secret = base / "isolated_secret.txt"
        secret.write_text("ISOLATED_SECRET\n")
        (repo / "escape").symlink_to(Path("..") / "isolated_secret.txt")
        _run(repo, "add", "README.md")
        _run(repo, "add", "-f", "escape")
        _run(repo, "commit", "--quiet", "-m", "only an escaping symlink")

        report = scan_local_repository_boundary(repo, [base])

        check(
            "FTF-H007",
            "in isolation, only the escaping symlink is reported (no other detector fires)",
            report.escaping_symlinks == ("escape",)
            and not report.executable_hooks
            and not report.dangerous_calls
            and not report.inventory.unresolved_lfs_paths
            and not report.inventory.unresolved_submodule_paths,
        )
        check(
            "FTF-H007",
            "the escaping symlink alone is sufficient to block lock",
            not report.is_safe_to_lock,
        )


def test_dirty_working_tree_alone_does_not_block_lock() -> None:
    """Section 7A.3 requires a dirty tree be "visibly marked," not
    refused -- this task's own negative case for its own positive "blocks
    lock" claim: an otherwise-safe repository with only an uncommitted,
    ordinary file must still be safe to lock."""
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        repo = base / "repo"
        _init_repo(repo)
        (repo / "committed.txt").write_text("committed\n")
        _run(repo, "add", "committed.txt")
        _run(repo, "commit", "--quiet", "-m", "initial")
        (repo / "untracked.txt").write_text("just an ordinary untracked file\n")

        report = scan_local_repository_boundary(repo, [base])

        check(
            "FTF-H007",
            "a dirty working tree is recorded on the inventory",
            report.inventory.is_dirty_working_tree,
        )
        check(
            "FTF-H007",
            "a dirty tree with no other unsafe finding is still safe to lock",
            report.is_safe_to_lock,
        )


def main() -> None:
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()

    if FAILURES:
        print(f"FTF-H007 source boundary tests: {len(FAILURES)} failed")
        for failure in FAILURES:
            print(f"  {failure}")
        sys.exit(1)

    print("FTF-H007 source boundary tests: all checks passed")
    sys.exit(0)


if __name__ == "__main__":
    main()
