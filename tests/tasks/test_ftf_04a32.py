#!/usr/bin/env python3
"""FTF-04A32 -- Local repository integration test (Section 6/7A/66/77/81).

Qualifies the real `capture_local_repository` connector (FTF-04A06) against
real, allowlisted local Git checkouts built with the real `git` binary in
real temporary directories -- no mocked git command, no canned capture result
and no stubbed guard. Runnable without Postgres or network egress.

Acceptance:
  A1. clean allowlisted checkout   -> `local_git_commit`, real HEAD sha/tree.
  A2. detached-HEAD checkout       -> branch is None, real HEAD sha.
  A3. approved dirty snapshot      -> `local_dirty_snapshot` whose archive
      bytes agree, file by file, with the manifest and `tree_hash`.
Negative acceptance (each also proves no write to the target working tree):
  N1. symlink escape inside the repo is refused.
  N2. uncommitted source changing mid-capture is refused, for an already-dirty
      tracked file, an untracked file, and a file added during capture; and a
      real concurrent writer never produces a torn snapshot.
  N3. inaccessible/nonexistent/non-directory/outside-allowlist/sub-directory
      roots are refused.

Run directly: python3 tests/tasks/test_ftf_04a32.py
"""

from __future__ import annotations

import hashlib
import io
import os
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import threading
from collections.abc import Callable
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
API_ROOT = ROOT / "apps" / "api"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(API_ROOT))

from app.target_connectors import local_repository_connector as _connector  # noqa: E402
from app.target_connectors.local_repository_connector import (  # noqa: E402
    ConcurrentModificationError,
    LocalRepositoryCapture,
    LocalRepositoryConnectorError,
    NotAGitRepositoryError,
    PathAllowlistError,
    capture_local_repository,
)

FAILURES: list[str] = []


def check(criterion_id: str, description: str, condition: bool) -> None:
    if not condition:
        FAILURES.append(f"FAIL [{criterion_id}] {description}")


def _git(cwd: Path, *args: str) -> str:
    git_binary = shutil.which("git")
    assert git_binary is not None, "git binary required"
    result = subprocess.run(
        [git_binary, "-c", "user.name=t", "-c", "user.email=t@example.invalid", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _new_checkout(parent: Path, name: str = "repo") -> Path:
    repo = parent / name
    repo.mkdir()
    _git(repo, "init", "-q")
    (repo / "a.txt").write_text("v1\n")
    (repo / "bin.sh").write_text("#!/bin/sh\n")
    (repo / "bin.sh").chmod(0o755)
    (repo / "pkg").mkdir()
    (repo / "pkg" / "b.txt").write_text("b\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "c1")
    return repo


def _fingerprint(repo: Path) -> dict[str, object]:
    """Everything a write could change: every non-.git file's bytes/mode/mtime,
    plus the git-visible state (HEAD, refs, index entries, status)."""
    files: dict[str, tuple[str, int, int]] = {}
    for path in sorted(repo.rglob("*")):
        rel = path.relative_to(repo)
        if rel.parts and rel.parts[0] == ".git":
            continue
        st = path.lstat()
        digest = (
            hashlib.sha256(path.read_bytes()).hexdigest()
            if stat.S_ISREG(st.st_mode)
            else os.readlink(path)
            if stat.S_ISLNK(st.st_mode)
            else "dir"
        )
        files[str(rel)] = (digest, st.st_mode, st.st_mtime_ns)
    return {
        "files": files,
        "head": _git(repo, "rev-parse", "HEAD"),
        "refs": _git(repo, "for-each-ref"),
        "index": _git(repo, "ls-files", "-s"),
        "status": _git(repo, "status", "--porcelain=v1", "--untracked-files=all"),
    }


def _refused(fn: Callable[[], object], error: type[Exception]) -> bool:
    try:
        fn()
    except error:
        return True
    return False


def _archive_files(capture: LocalRepositoryCapture) -> dict[str, bytes]:
    assert capture.source_archive_bytes is not None
    out: dict[str, bytes] = {}
    with tarfile.open(fileobj=io.BytesIO(capture.source_archive_bytes)) as archive:
        for member in archive.getmembers():
            handle = archive.extractfile(member)
            assert handle is not None
            out[member.name] = handle.read()
    return out


def _archive_agrees_with_manifest(capture: LocalRepositoryCapture) -> bool:
    files = _archive_files(capture)
    manifest = {e.relative_path: e for e in capture.manifest_entries}
    if set(files) != set(manifest):
        return False
    return all(
        hashlib.sha256(files[p]).hexdigest() == e.sha256 and len(files[p]) == e.size
        for p, e in manifest.items()
    )


def _with_tmp(body: Callable[[Path], None]) -> None:
    with tempfile.TemporaryDirectory() as raw:
        body(Path(raw).resolve())


def _inject_before_archive(action: Callable[[Path], object]) -> Callable[[], None]:
    """Perform a REAL filesystem change at the instant the connector has
    walked the tree but not yet read the bytes for the archive -- the real
    guards stay unstubbed; only the timing of a genuine writer is fixed."""
    original = _connector._build_archive

    def drifting(root: Path, entries: list[_connector.ManifestEntry]) -> bytes:
        action(root)
        return original(root, entries)

    _connector._build_archive = drifting

    def restore() -> None:
        _connector._build_archive = original

    return restore


# --------------------------------------------------------------------- A1-A3


def test_a1_clean_allowlisted_checkout() -> None:
    def body(tmp: Path) -> None:
        repo = _new_checkout(tmp)
        before = _fingerprint(repo)
        cap = capture_local_repository(repo, [tmp])
        check(
            "FTF-04A32-A1",
            "clean capture is local_git_commit",
            cap.capture_method == "local_git_commit",
        )
        check(
            "FTF-04A32-A1",
            "commit_sha is real HEAD",
            cap.commit_sha == _git(repo, "rev-parse", "HEAD"),
        )
        check(
            "FTF-04A32-A1",
            "tree_hash is real HEAD^{tree}",
            cap.tree_hash == _git(repo, "rev-parse", "HEAD^{tree}"),
        )
        check(
            "FTF-04A32-A1",
            "branch is the real branch",
            cap.branch == _git(repo, "branch", "--show-current"),
        )
        check("FTF-04A32-A1", "no archive for a clean commit", cap.source_archive_bytes is None)
        check("FTF-04A32-A1", "capture wrote nothing to the target", _fingerprint(repo) == before)

    _with_tmp(body)


def test_a2_detached_head() -> None:
    def body(tmp: Path) -> None:
        repo = _new_checkout(tmp)
        (repo / "a.txt").write_text("v2\n")
        _git(repo, "commit", "-qam", "c2")
        _git(repo, "checkout", "-q", "--detach", "HEAD~1")
        before = _fingerprint(repo)
        cap = capture_local_repository(repo, [tmp])
        check(
            "FTF-04A32-A2",
            "detached capture is local_git_commit",
            cap.capture_method == "local_git_commit",
        )
        check("FTF-04A32-A2", "detached branch is None", cap.branch is None)
        check(
            "FTF-04A32-A2",
            "detached sha is real HEAD",
            cap.commit_sha == _git(repo, "rev-parse", "HEAD"),
        )
        check("FTF-04A32-A2", "capture wrote nothing to the target", _fingerprint(repo) == before)

    _with_tmp(body)


def test_a3_approved_dirty_snapshot() -> None:
    def body(tmp: Path) -> None:
        repo = _new_checkout(tmp)
        (repo / "a.txt").write_text("dirty tracked\n")
        (repo / "pkg" / "new.txt").write_text("untracked\n")
        (repo / "staged.txt").write_text("staged\n")
        _git(repo, "add", "staged.txt")
        before = _fingerprint(repo)
        cap = capture_local_repository(repo, [tmp])
        check(
            "FTF-04A32-A3",
            "dirty capture is local_dirty_snapshot",
            cap.capture_method == "local_dirty_snapshot",
        )
        check("FTF-04A32-A3", "capture wrote nothing to the target", _fingerprint(repo) == before)
        check("FTF-04A32-A3", "archive present", cap.source_archive_bytes is not None)
        check(
            "FTF-04A32-A3",
            "archive sha256 is of the archive bytes",
            cap.source_archive_bytes is not None
            and cap.source_archive_sha256 == hashlib.sha256(cap.source_archive_bytes).hexdigest(),
        )
        check(
            "FTF-04A32-A3",
            "archive agrees with manifest file by file",
            _archive_agrees_with_manifest(cap),
        )
        files = _archive_files(cap)
        check(
            "FTF-04A32-A3",
            "archive holds the dirty bytes",
            files.get("a.txt") == b"dirty tracked\n",
        )
        check(
            "FTF-04A32-A3",
            "archive holds untracked file",
            files.get("pkg/new.txt") == b"untracked\n",
        )
        check("FTF-04A32-A3", "archive holds staged file", files.get("staged.txt") == b"staged\n")
        check("FTF-04A32-A3", "archive excludes .git", not any(p.startswith(".git") for p in files))
        check(
            "FTF-04A32-A3",
            "executable bit preserved",
            any(e.relative_path == "bin.sh" and e.executable for e in cap.manifest_entries),
        )
        again = capture_local_repository(repo, [tmp])
        check(
            "FTF-04A32-A3",
            "re-capture of identical content is byte-identical",
            again.tree_hash == cap.tree_hash
            and again.source_archive_sha256 == cap.source_archive_sha256,
        )
        (repo / "a.txt").write_text("dirty tracked 2\n")
        changed = capture_local_repository(repo, [tmp])
        check(
            "FTF-04A32-A3",
            "different content gives a different tree_hash",
            changed.tree_hash != cap.tree_hash,
        )

    _with_tmp(body)


# ------------------------------------------------------------------------ N1


def test_n1_symlink_escape_refused_without_writes() -> None:
    def body(tmp: Path) -> None:
        allowed = tmp / "allowed"
        allowed.mkdir()
        outside = tmp / "outside"
        outside.mkdir()
        (outside / "secret.txt").write_text("TOP SECRET\n")
        repo = _new_checkout(allowed)
        (repo / "link").symlink_to(outside / "secret.txt")
        (repo / "dirlink").symlink_to(outside, target_is_directory=True)
        before = _fingerprint(repo)
        check(
            "FTF-04A32-N1",
            "symlink escape inside the repo is refused",
            _refused(lambda: capture_local_repository(repo, [allowed]), PathAllowlistError),
        )
        check("FTF-04A32-N1", "refusal wrote nothing to the target", _fingerprint(repo) == before)

        # A root that is itself a symlink out of the allowlist is refused too.
        evil = allowed / "evil"
        evil.symlink_to(outside, target_is_directory=True)
        check(
            "FTF-04A32-N1",
            "allowlisted symlink pointing outside is refused",
            _refused(lambda: capture_local_repository(evil, [allowed]), PathAllowlistError),
        )

    _with_tmp(body)


# ------------------------------------------------------------------------ N2


def test_n2_changing_source_refused_for_dirty_tracked_file() -> None:
    def body(tmp: Path) -> None:
        repo = _new_checkout(tmp)
        (repo / "a.txt").write_text("dirty1\n")  # already "M a.txt"
        restore = _inject_before_archive(lambda r: (r / "a.txt").write_text("dirty2 CHANGED\n"))
        try:
            check(
                "FTF-04A32-N2",
                "in-place edit of an already-dirty tracked file is refused (no torn snapshot)",
                _refused(
                    lambda: capture_local_repository(repo, [tmp]), ConcurrentModificationError
                ),
            )
        finally:
            restore()

    _with_tmp(body)


def test_n2_changing_source_refused_for_untracked_file() -> None:
    def body(tmp: Path) -> None:
        repo = _new_checkout(tmp)
        (repo / "scratch.txt").write_text("u1\n")  # already "?? scratch.txt"
        restore = _inject_before_archive(lambda r: (r / "scratch.txt").write_text("u2 CHANGED\n"))
        try:
            check(
                "FTF-04A32-N2",
                "in-place edit of an untracked file is refused (no torn snapshot)",
                _refused(
                    lambda: capture_local_repository(repo, [tmp]), ConcurrentModificationError
                ),
            )
        finally:
            restore()

    _with_tmp(body)


def test_n2_edit_reverted_before_final_walk_still_refused_by_archive_check() -> None:
    """The torn archive is only visible to the per-file byte check: the content
    is restored before the manifest re-walk, so a re-walk alone sees nothing."""

    def body(tmp: Path) -> None:
        repo = _new_checkout(tmp)
        (repo / "a.txt").write_text("dirty1\n")
        original = _connector._build_archive

        def torn(root: Path, entries: list[_connector.ManifestEntry]) -> bytes:
            (root / "a.txt").write_text("dirty2 CHANGED\n")
            try:
                return original(root, entries)
            finally:
                (root / "a.txt").write_text("dirty1\n")

        _connector._build_archive = torn
        try:
            check(
                "FTF-04A32-N2",
                "a torn archive is refused even if the edit is reverted before the final walk",
                _refused(
                    lambda: capture_local_repository(repo, [tmp]), ConcurrentModificationError
                ),
            )
        finally:
            _connector._build_archive = original

    _with_tmp(body)


def test_n2_file_added_during_capture_refused() -> None:
    def body(tmp: Path) -> None:
        repo = _new_checkout(tmp)
        (repo / "a.txt").write_text("dirty\n")
        restore = _inject_before_archive(lambda r: (r / "late.txt").write_text("late\n"))
        try:
            check(
                "FTF-04A32-N2",
                "a file added during capture is refused",
                _refused(
                    lambda: capture_local_repository(repo, [tmp]), ConcurrentModificationError
                ),
            )
        finally:
            restore()

    _with_tmp(body)


def test_n2_edit_after_archive_before_final_check_refused() -> None:
    def body(tmp: Path) -> None:
        repo = _new_checkout(tmp)
        (repo / "a.txt").write_text("dirty1\n")
        original = _connector._git_dirty_signature
        calls = {"n": 0}

        def edit_then_probe(root: Path) -> str:
            calls["n"] += 1
            if calls["n"] == 2:  # the post-archive probe: edit lands just before it
                (root / "a.txt").write_text("dirty2 CHANGED\n")
            return original(root)

        _connector._git_dirty_signature = edit_then_probe
        try:
            check(
                "FTF-04A32-N2",
                "edit between archive build and the final check is refused",
                _refused(
                    lambda: capture_local_repository(repo, [tmp]), ConcurrentModificationError
                ),
            )
        finally:
            _connector._git_dirty_signature = original

    _with_tmp(body)


def test_n2_real_concurrent_writer_never_yields_torn_snapshot() -> None:
    def body(tmp: Path) -> None:
        repo = _new_checkout(tmp)
        for i in range(40):
            (repo / "pkg" / f"f{i}.txt").write_text(f"base {i}\n" * 200)
        (repo / "a.txt").write_text("dirty\n")
        stop = threading.Event()

        def writer() -> None:
            n = 0
            while not stop.is_set():
                n += 1
                (repo / "pkg" / "f7.txt").write_text(f"writer {n}\n" * 200)

        thread = threading.Thread(target=writer)
        thread.start()
        torn = 0
        returned = 0
        try:
            for _ in range(40):
                try:
                    cap = capture_local_repository(repo, [tmp])
                except ConcurrentModificationError:
                    continue
                returned += 1
                if not _archive_agrees_with_manifest(cap):
                    torn += 1
        finally:
            stop.set()
            thread.join()
        check(
            "FTF-04A32-N2",
            f"real concurrent writer produced {torn} torn snapshot(s) of {returned}",
            torn == 0,
        )

    _with_tmp(body)


# ------------------------------------------------------------------------ N3


def test_n3_unusable_roots_refused_without_writes() -> None:
    def body(tmp: Path) -> None:
        allowed = tmp / "allowed"
        allowed.mkdir()
        repo = _new_checkout(allowed)
        before = _fingerprint(repo)

        outside = _new_checkout(tmp, "outside_repo")
        check(
            "FTF-04A32-N3",
            "root outside the allowlist is refused",
            _refused(lambda: capture_local_repository(outside, [allowed]), PathAllowlistError),
        )
        check(
            "FTF-04A32-N3",
            "empty allowlist refuses by default",
            _refused(lambda: capture_local_repository(repo, []), PathAllowlistError),
        )
        check(
            "FTF-04A32-N3",
            "nonexistent root is refused",
            _refused(
                lambda: capture_local_repository(allowed / "nope", [allowed]), PathAllowlistError
            ),
        )
        dangling = allowed / "dangling"
        dangling.symlink_to(allowed / "missing-target")
        check(
            "FTF-04A32-N3",
            "dangling-symlink root is refused",
            _refused(lambda: capture_local_repository(dangling, [allowed]), PathAllowlistError),
        )
        plain_file = allowed / "file.txt"
        plain_file.write_text("x")
        check(
            "FTF-04A32-N3",
            "a regular file is not a usable root",
            _refused(
                lambda: capture_local_repository(plain_file, [allowed]),
                LocalRepositoryConnectorError,
            ),
        )
        check(
            "FTF-04A32-N3",
            "a sub-directory of a checkout is not a worktree top level",
            _refused(
                lambda: capture_local_repository(repo / "pkg", [allowed]), NotAGitRepositoryError
            ),
        )
        plain_dir = allowed / "not_git"
        plain_dir.mkdir()
        check(
            "FTF-04A32-N3",
            "a non-git directory is refused",
            _refused(
                lambda: capture_local_repository(plain_dir, [allowed]), NotAGitRepositoryError
            ),
        )
        # `allowed/../allowed_evil` style traversal and prefix-sibling are refused.
        sibling = tmp / "allowed_evil"
        sibling.mkdir()
        evil_repo = _new_checkout(sibling)
        check(
            "FTF-04A32-N3",
            "prefix-sibling directory is not treated as inside the allowlist",
            _refused(lambda: capture_local_repository(evil_repo, [allowed]), PathAllowlistError),
        )
        check(
            "FTF-04A32-N3",
            "../ traversal out of the allowlist is refused",
            _refused(
                lambda: capture_local_repository(
                    allowed / ".." / "allowed_evil" / "repo", [allowed]
                ),
                PathAllowlistError,
            ),
        )
        check("FTF-04A32-N3", "refusals wrote nothing to the target", _fingerprint(repo) == before)

    _with_tmp(body)


def test_n3_unreadable_root_refused() -> None:
    if os.geteuid() == 0:
        return  # root bypasses mode bits; CI runs unprivileged and exercises this

    def body(tmp: Path) -> None:
        repo = _new_checkout(tmp)
        (repo / "a.txt").write_text("dirty\n")
        (repo / "pkg").chmod(0o000)
        try:
            check(
                "FTF-04A32-N3",
                "a repository with an unreadable directory fails with a connector error",
                _refused(
                    lambda: capture_local_repository(repo, [tmp]), LocalRepositoryConnectorError
                ),
            )
        finally:
            (repo / "pkg").chmod(0o755)

    _with_tmp(body)


def main() -> None:
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()

    if FAILURES:
        print(f"FTF-04A32 local repository integration tests: {len(FAILURES)} failed")
        for failure in FAILURES:
            print(f"  {failure}")
        sys.exit(1)

    print("FTF-04A32 local repository integration tests: all checks passed")
    sys.exit(0)


if __name__ == "__main__":
    main()
