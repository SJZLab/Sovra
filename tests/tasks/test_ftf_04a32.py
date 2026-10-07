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

import functools
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
from typing import Any

ROOT = Path(__file__).resolve().parent.parent.parent
API_ROOT = ROOT / "apps" / "api"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(API_ROOT))

from app.target_connectors import local_repository_connector as _connector  # noqa: E402
from app.target_connectors.local_repository_connector import (  # noqa: E402
    ConcurrentModificationError,
    DangerousGitConfigError,
    LocalRepositoryCapture,
    LocalRepositoryConnectorError,
    NotAGitRepositoryError,
    PathAllowlistError,
    PopulatedSubmoduleError,
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

    def body(tmp: Path, edited: str) -> None:
        repo = _new_checkout(tmp)
        (repo / "a.txt").write_text("dirty1\n")
        original = _connector._build_archive

        def torn(root: Path, entries: list[_connector.ManifestEntry]) -> bytes:
            (root / "a.txt").write_text(edited)
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

    for edited in ("dirty2 CHANGED\n", "dirty2\n"):  # different and same length
        _with_tmp(functools.partial(body, edited=edited))


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


def _wrap(name: str, hook: Callable[[Path], object]) -> Callable[[], None]:
    """Run a REAL filesystem/git action just before the named connector probe."""
    original = getattr(_connector, name)

    def wrapper(root: Path, *args: object, **kwargs: object) -> object:
        hook(root)
        return original(root, *args, **kwargs)

    setattr(_connector, name, wrapper)
    return lambda: setattr(_connector, name, original)


def test_n2_same_length_edit_during_archive_refused() -> None:
    def body(tmp: Path) -> None:
        repo = _new_checkout(tmp)
        (repo / "a.txt").write_text("dirty1\n")
        restore = _inject_before_archive(lambda r: (r / "a.txt").write_text("dirty2\n"))
        try:
            check(
                "FTF-04A32-N2",
                "a same-length in-place edit is refused",
                _refused(
                    lambda: capture_local_repository(repo, [tmp]), ConcurrentModificationError
                ),
            )
        finally:
            restore()

    _with_tmp(body)


def test_n2_clean_capture_drift_refused() -> None:
    def untracked(repo: Path) -> None:
        (repo / "late.txt").write_text("x\n")

    def empty_commit(repo: Path) -> None:
        _git(repo, "commit", "-q", "--allow-empty", "-m", "moved")

    for label, hook in (("untracked file", untracked), ("HEAD moved", empty_commit)):

        def body(tmp: Path, label: str = label, hook: Callable[[Path], object] = hook) -> None:
            repo = _new_checkout(tmp)
            restore = _wrap("_git_head_tree_hash", hook)
            try:
                check(
                    "FTF-04A32-N2",
                    f"clean capture refused when {label} appears mid-capture",
                    _refused(
                        lambda: capture_local_repository(repo, [tmp]), ConcurrentModificationError
                    ),
                )
            finally:
                restore()

        _with_tmp(body)


def test_n2_dirty_capture_status_or_head_drift_refused() -> None:
    def restage(repo: Path) -> None:  # status " M" -> "M ", manifest unchanged
        _git(repo, "add", "a.txt")

    def empty_commit(repo: Path) -> None:  # HEAD moves, status and manifest unchanged
        _git(repo, "commit", "-q", "--allow-empty", "-m", "moved")

    for label, hook in (("index restaged", restage), ("HEAD moved", empty_commit)):

        def body(tmp: Path, label: str = label, hook: Callable[[Path], object] = hook) -> None:
            repo = _new_checkout(tmp)
            (repo / "a.txt").write_text("dirty\n")
            restore = _inject_before_archive(hook)
            try:
                check(
                    "FTF-04A32-N2",
                    f"dirty capture refused when {label} mid-capture",
                    _refused(
                        lambda: capture_local_repository(repo, [tmp]), ConcurrentModificationError
                    ),
                )
            finally:
                restore()

        _with_tmp(body)


def test_n3_dangerous_filter_driver_and_populated_submodule_refused() -> None:
    def body(tmp: Path) -> None:
        repo = _new_checkout(tmp)
        _git(repo, "config", "filter.evil.clean", "true")
        before = _fingerprint(repo)
        check(
            "FTF-04A32-N3",
            "a repo-defined filter driver is refused before git can run it",
            _refused(lambda: capture_local_repository(repo, [tmp]), DangerousGitConfigError),
        )
        check("FTF-04A32-N3", "refusal wrote nothing to the target", _fingerprint(repo) == before)

        repo2 = _new_checkout(tmp, "repo2")
        sub = _new_checkout(repo2, "sub")
        _git(
            repo2,
            "update-index",
            "--add",
            "--cacheinfo",
            f"160000,{_git(sub, 'rev-parse', 'HEAD')},sub",
        )
        _git(repo2, "commit", "-q", "-m", "gitlink")
        check(
            "FTF-04A32-N3",
            "a populated submodule is refused",
            _refused(lambda: capture_local_repository(repo2, [tmp]), PopulatedSubmoduleError),
        )

    _with_tmp(body)


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


def test_n4_walker_symlink_and_read_edges() -> None:
    """Each walker branch gets its own case so a fault in one cannot hide
    behind another refusal (FTF-04A32-N1/N3 clause-level faults)."""

    def dirty_repo(allowed: Path) -> Path:
        repo = _new_checkout(allowed)
        (repo / "a.txt").write_text("dirty\n")
        return repo

    def body(tmp: Path) -> None:
        outside = tmp / "outside"
        outside.mkdir()
        (outside / "secret.txt").write_text("TOP SECRET\n")

        # Dangling symlink: refused, not skipped.
        a = tmp / "a"
        a.mkdir()
        repo = dirty_repo(a)
        (repo / "dangling").symlink_to(repo / "missing-target")
        check(
            "FTF-04A32-N1",
            "dangling symlink inside the repo is refused",
            _refused(lambda: capture_local_repository(repo, [a]), PathAllowlistError),
        )

        # File symlink alone escaping the root.
        b = tmp / "b"
        b.mkdir()
        repo = dirty_repo(b)
        (repo / "filelink").symlink_to(outside / "secret.txt")
        check(
            "FTF-04A32-N1",
            "file symlink escaping the root is refused on its own",
            _refused(lambda: capture_local_repository(repo, [b]), PathAllowlistError),
        )

        # Directory symlink alone escaping the root.
        c = tmp / "c"
        c.mkdir()
        repo = dirty_repo(c)
        (repo / "dirlink").symlink_to(outside, target_is_directory=True)
        check(
            "FTF-04A32-N1",
            "directory symlink escaping the root is refused on its own",
            _refused(lambda: capture_local_repository(repo, [c]), PathAllowlistError),
        )

        # In-root symlink cycle terminates and is walked exactly once.
        d = tmp / "d"
        d.mkdir()
        repo = dirty_repo(d)
        (repo / "loop").symlink_to(repo, target_is_directory=True)
        cycle_ok = True
        try:
            cap = capture_local_repository(repo, [d])
        except RecursionError:
            cycle_ok = False
        except Exception:
            cycle_ok = False
        else:
            paths = [e.relative_path for e in cap.manifest_entries]
            cycle_ok = (
                len(paths) == len(set(paths))
                and "a.txt" in paths
                and not any(p.startswith("loop/") for p in paths)
            )
        check("FTF-04A32-N1", "in-root symlink cycle terminates without duplicates", cycle_ok)

        # Only the exact top-level `.git` is excluded: look-alike names are content.
        e = tmp / "e"
        e.mkdir()
        repo = dirty_repo(e)
        (repo / ".GIT").mkdir()
        (repo / ".GIT" / "x.txt").write_text("upper\n")
        (repo / " .git").mkdir()
        (repo / " .git" / "y.txt").write_text("space\n")
        cap = capture_local_repository(repo, [e])
        name_set = {entry.relative_path for entry in cap.manifest_entries}
        check(
            "FTF-04A32-A2",
            "look-alike .git names are captured; the real .git is not",
            {".GIT/x.txt", " .git/y.txt", "a.txt"} <= name_set
            and not any(p.startswith(".git/") for p in name_set),
        )

        if os.geteuid() != 0:  # root bypasses mode bits; CI runs unprivileged
            f = tmp / "f"
            f.mkdir()
            repo = dirty_repo(f)
            (repo / "secret.bin").write_text("x\n")
            (repo / "secret.bin").chmod(0o000)
            try:
                check(
                    "FTF-04A32-N3",
                    "an unreadable file fails with a connector error",
                    _refused(
                        lambda: capture_local_repository(repo, [f]),
                        LocalRepositoryConnectorError,
                    ),
                )
            finally:
                (repo / "secret.bin").chmod(0o644)

    _with_tmp(body)


def test_n1_prefix_sibling_symlink_refused() -> None:
    """F09: `<tmp>/repo-evil` shares a string prefix with the root `<tmp>/repo`
    but is outside it; a string-prefix containment check would let it through."""

    def body(tmp: Path) -> None:
        repo = _new_checkout(tmp)
        evil = tmp / "repo-evil"
        evil.mkdir()
        (evil / "secret.txt").write_text("TOP SECRET\n")
        (repo / "a.txt").write_text("dirty\n")
        (repo / "sibling").symlink_to(evil / "secret.txt")
        before = _fingerprint(repo)
        check(
            "FTF-04A32-N1",
            "symlink to a prefix-sibling path is refused",
            _refused(lambda: capture_local_repository(repo, [tmp]), PathAllowlistError),
        )
        check("FTF-04A32-N1", "refusal wrote nothing to the target", _fingerprint(repo) == before)

    _with_tmp(body)


def test_a3_same_length_content_and_rename_and_mode_change_tree_hash() -> None:
    """F19/F20/F18: every manifest dimension (content, path, mode) moves tree_hash."""

    def body(tmp: Path) -> None:
        repo = _new_checkout(tmp)
        (repo / "a.txt").write_text("dirty1\n")
        (repo / "pkg" / "new.txt").write_text("untracked\n")
        base = capture_local_repository(repo, [tmp])
        check(
            "FTF-04A32-A3",
            "baseline is a dirty snapshot",
            base.capture_method == "local_dirty_snapshot",
        )

        # F19: same-length content edit.
        (repo / "a.txt").write_text("dirty2\n")
        same_len = capture_local_repository(repo, [tmp])
        check(
            "FTF-04A32-A3",
            "a same-length content edit changes tree_hash",
            same_len.tree_hash != base.tree_hash,
        )
        check(
            "FTF-04A32-A3",
            "a same-length content edit changes the archive hash",
            same_len.source_archive_sha256 != base.source_archive_sha256,
        )
        check(
            "FTF-04A32-A3",
            "archive holds the new same-length bytes",
            _archive_files(same_len).get("a.txt") == b"dirty2\n",
        )

        # F20: rename with identical content.
        (repo / "pkg" / "new.txt").rename(repo / "pkg" / "renamed.txt")
        renamed = capture_local_repository(repo, [tmp])
        check(
            "FTF-04A32-A3",
            "a rename with identical content changes tree_hash",
            renamed.tree_hash != same_len.tree_hash,
        )
        check(
            "FTF-04A32-A3",
            "renamed path is in the manifest and the old one is not",
            {e.relative_path for e in renamed.manifest_entries} >= {"pkg/renamed.txt"}
            and "pkg/new.txt" not in {e.relative_path for e in renamed.manifest_entries},
        )

        # F18/F15/F16: chmod +x only.
        before_mode = capture_local_repository(repo, [tmp])
        (repo / "pkg" / "b.txt").chmod(0o755)
        after_mode = capture_local_repository(repo, [tmp])
        check(
            "FTF-04A32-A3",
            "a chmod +x-only change changes tree_hash",
            after_mode.tree_hash != before_mode.tree_hash,
        )
        flags = {e.relative_path: e.executable for e in after_mode.manifest_entries}
        check("FTF-04A32-A3", "a.txt is not executable", flags.get("a.txt") is False)
        check("FTF-04A32-A3", "bin.sh is executable", flags.get("bin.sh") is True)
        check("FTF-04A32-A3", "chmod'd pkg/b.txt is executable", flags.get("pkg/b.txt") is True)
        assert after_mode.source_archive_bytes is not None
        with tarfile.open(fileobj=io.BytesIO(after_mode.source_archive_bytes)) as archive:
            modes = {m.name: m.mode for m in archive.getmembers()}
        check("FTF-04A32-A3", "tar mode of bin.sh is 0o755", modes.get("bin.sh") == 0o755)
        check("FTF-04A32-A3", "tar mode of a.txt is 0o644", modes.get("a.txt") == 0o644)
        check("FTF-04A32-A3", "tar mode of chmod'd b.txt is 0o755", modes.get("pkg/b.txt") == 0o755)

    _with_tmp(body)


def test_n2_same_length_edit_after_archive_before_final_walk_refused() -> None:
    """F19 (post-archive half): the dirty signature is unchanged by a content
    edit to an already-dirty file, so only the final manifest re-walk can see it."""

    def body(tmp: Path) -> None:
        repo = _new_checkout(tmp)
        (repo / "a.txt").write_text("dirty1\n")
        original = _connector._git_dirty_signature
        calls = {"n": 0}

        def edit_then_probe(root: Path) -> str:
            calls["n"] += 1
            if calls["n"] == 2:  # post-archive probe: same-length edit lands just before it
                (root / "a.txt").write_text("dirty2\n")
            return original(root)

        _connector._git_dirty_signature = edit_then_probe
        try:
            check(
                "FTF-04A32-N2",
                "a same-length edit after the archive build is refused",
                _refused(
                    lambda: capture_local_repository(repo, [tmp]), ConcurrentModificationError
                ),
            )
        finally:
            _connector._git_dirty_signature = original

    _with_tmp(body)


def test_n4_in_root_symlinks_are_followed() -> None:
    """F14: a file symlink and a directory symlink that stay inside the root are captured."""

    def body(tmp: Path) -> None:
        repo = _new_checkout(tmp)
        (repo / "a.txt").write_text("dirty\n")
        (repo / "filelink").symlink_to(repo / "pkg" / "b.txt")
        (repo / "dirlink").symlink_to(repo / "pkg", target_is_directory=True)
        cap = capture_local_repository(repo, [tmp])
        paths = {e.relative_path for e in cap.manifest_entries}
        check("FTF-04A32-N1", "in-root file symlink is captured", "filelink" in paths)
        check("FTF-04A32-N1", "in-root dir symlink target is captured", "dirlink/b.txt" in paths)

    _with_tmp(body)


def test_f1_git_ignored_files_never_captured() -> None:
    """FTF-04A06-F1 (Law 16): the snapshot file set is git's own view; ignored
    files (a `.env`, a nested ignored dir, an ignored symlink) are never read."""

    def body(tmp: Path) -> None:
        outside = tmp / "outside"
        outside.mkdir()
        (outside / "secret.txt").write_text("TOP SECRET\n")
        allowed = tmp / "allowed"
        allowed.mkdir()
        repo = _new_checkout(allowed)
        (repo / ".gitignore").write_text(".env\nbuild/\nignored-link\n")
        _git(repo, "add", ".gitignore")
        _git(repo, "commit", "-qm", "ignore")
        (repo / "a.txt").write_text("dirty\n")
        (repo / "pkg" / "new.txt").write_text("untracked, not ignored\n")
        (repo / ".env").write_text("API_KEY=hunter2\n")
        (repo / "build" / "deep").mkdir(parents=True)
        (repo / "build" / "deep" / "out.bin").write_text("artifact\n")
        (repo / ".git" / "info" / "exclude").write_text("local-only.txt\n")
        (repo / "local-only.txt").write_text("excluded via info/exclude\n")
        # An ignored symlink escaping the root is skipped, never followed.
        (repo / "ignored-link").symlink_to(outside / "secret.txt")
        before = _fingerprint(repo)

        cap = capture_local_repository(repo, [allowed])
        paths = {e.relative_path for e in cap.manifest_entries}
        files = _archive_files(cap)
        check("FTF-04A06-F1", "dirty snapshot taken", cap.capture_method == "local_dirty_snapshot")
        check("FTF-04A06-F1", "ignored .env absent from the manifest", ".env" not in paths)
        check("FTF-04A06-F1", "ignored .env absent from the archive", ".env" not in files)
        check(
            "FTF-04A06-F1",
            "nested ignored dir absent from manifest and archive",
            not any(p.startswith("build/") for p in paths | set(files)),
        )
        check(
            "FTF-04A06-F1",
            "info/exclude file absent",
            "local-only.txt" not in paths and "local-only.txt" not in files,
        )
        check(
            "FTF-04A06-F1",
            "ignored escaping symlink is neither refused nor followed",
            "ignored-link" not in paths and "ignored-link" not in files,
        )
        check(
            "FTF-04A06-F1",
            "no archive byte holds the ignored secret",
            b"hunter2" not in (cap.source_archive_bytes or b""),
        )
        check(
            "FTF-04A06-F1",
            "un-ignored untracked file and tracked dirty file are present",
            files.get("pkg/new.txt") == b"untracked, not ignored\n"
            and files.get("a.txt") == b"dirty\n"
            and ".gitignore" in files,
        )
        check("FTF-04A06-F1", "capture wrote nothing to the target", _fingerprint(repo) == before)

        # Changing ignored content must not move the tree_hash or archive.
        (repo / ".env").write_text("API_KEY=rotated\n")
        (repo / "build" / "deep" / "out.bin").write_text("artifact 2\n")
        again = capture_local_repository(repo, [allowed])
        check(
            "FTF-04A06-F1",
            "ignored-content edits do not change tree_hash or archive",
            again.tree_hash == cap.tree_hash
            and again.source_archive_sha256 == cap.source_archive_sha256,
        )

        # A symlink that is NOT ignored but points at an ignored in-root file
        # must not smuggle the ignored bytes in through the link.
        (repo / "envlink").symlink_to(repo / ".env")
        linked = capture_local_repository(repo, [allowed])
        check(
            "FTF-04A06-F1",
            "a non-ignored symlink to an ignored file does not leak its bytes",
            b"rotated" not in (linked.source_archive_bytes or b"")
            and "envlink" not in {e.relative_path for e in linked.manifest_entries},
        )

    _with_tmp(body)


def test_f1_ignored_directory_is_pruned_not_walked() -> None:
    """An escaping symlink inside a git-ignored directory is never reached, so
    the capture is not refused; the same link outside an ignored dir still is."""

    def body(tmp: Path) -> None:
        outside = tmp / "outside"
        outside.mkdir()
        (outside / "secret.txt").write_text("TOP SECRET\n")
        allowed = tmp / "allowed"
        allowed.mkdir()
        repo = _new_checkout(allowed)
        (repo / ".gitignore").write_text("node_modules/\n")
        _git(repo, "add", ".gitignore")
        _git(repo, "commit", "-qm", "ignore")
        (repo / "a.txt").write_text("dirty\n")
        (repo / "node_modules").mkdir()
        (repo / "node_modules" / "esc").symlink_to(outside / "secret.txt")
        cap = capture_local_repository(repo, [allowed])
        paths = {e.relative_path for e in cap.manifest_entries}
        check(
            "FTF-04A06-F1",
            "ignored dir with an escaping symlink is pruned, not refused",
            "a.txt" in paths and not any(p.startswith("node_modules") for p in paths),
        )
        (repo / "pkg" / "esc").symlink_to(outside / "secret.txt")
        check(
            "FTF-04A06-F1",
            "the same escaping link in a non-ignored dir is still refused",
            _refused(lambda: capture_local_repository(repo, [allowed]), PathAllowlistError),
        )

    _with_tmp(body)


def test_f1_unreadable_git_view_fails_closed() -> None:
    """If git cannot enumerate the file set, nothing is treated as "not ignored"."""

    def body(tmp: Path) -> None:
        not_a_repo = tmp / "plain"
        not_a_repo.mkdir()
        (not_a_repo / ".env").write_text("API_KEY=hunter2\n")
        check(
            "FTF-04A06-F1",
            "a failed git file listing raises instead of capturing everything",
            _refused(
                lambda: _connector._iter_repository_files(not_a_repo), _connector.GitCommandError
            ),
        )

    _with_tmp(body)


def _dirty_ignore_repo(tmp: Path, gitignore: str) -> tuple[Path, Path]:
    allowed = tmp / "allowed"
    allowed.mkdir()
    repo = _new_checkout(allowed)
    (repo / ".gitignore").write_text(gitignore)
    _git(repo, "add", ".gitignore")
    _git(repo, "commit", "-qm", "ignore")
    (repo / "a.txt").write_text("dirty\n")
    return allowed, repo


def _manifest_paths(cap: LocalRepositoryCapture) -> set[str]:
    return {e.relative_path for e in cap.manifest_entries}


def test_f1_nested_gitignore_and_negation_semantics() -> None:
    """A nested `.gitignore` applies below its own directory only, and a
    negation re-includes: not a top-level-only emulation."""

    def body(tmp: Path) -> None:
        allowed, repo = _dirty_ignore_repo(tmp, "*.env\n!keep.env\n")
        (repo / "pkg" / ".gitignore").write_text("secret.*\n")
        (repo / "pkg" / "sub").mkdir()
        (repo / "other").mkdir()
        for rel in ("pkg/secret.key", "pkg/sub/secret.key", "other/secret.key"):
            (repo / rel).write_text("S\n")
        (repo / "x.env").write_text("S\n")
        (repo / "keep.env").write_text("K\n")
        cap = capture_local_repository(repo, [allowed])
        paths = _manifest_paths(cap)
        files = _archive_files(cap)
        check(
            "FTF-04A06-F1",
            "nested .gitignore hides pkg/secret.key and pkg/sub/secret.key",
            "pkg/secret.key" not in paths
            and "pkg/sub/secret.key" not in paths
            and "pkg/secret.key" not in files
            and "pkg/sub/secret.key" not in files,
        )
        check(
            "FTF-04A06-F1",
            "a nested pattern does not leak to a sibling directory",
            "other/secret.key" in paths and files.get("other/secret.key") == b"S\n",
        )
        check(
            "FTF-04A06-F1",
            "negation re-includes keep.env while *.env stays ignored",
            "keep.env" in paths and "x.env" not in paths and "x.env" not in files,
        )

    _with_tmp(body)


def test_f1_ignored_paths_are_never_opened_or_listed() -> None:
    """Law 16: no ignored file is opened or read, and no ignored directory is
    even listed (real pruning), including one behind an ignored symlink."""

    def body(tmp: Path) -> None:
        allowed, repo = _dirty_ignore_repo(tmp, ".env\nnode_modules/\nlink-to-ignored\n")
        (repo / ".env").write_text("API_KEY=hunter2\n")
        (repo / "node_modules" / "deep").mkdir(parents=True)
        (repo / "node_modules" / "deep" / "x.js").write_text("x\n")
        (repo / "link-to-ignored").symlink_to(repo / "node_modules")
        touched: list[str] = []
        listed: list[str] = []
        builtins_module: Any = sys.modules["builtins"]
        path_cls: Any = Path
        os_module: Any = os
        real_read, real_open = path_cls.read_bytes, path_cls.open
        real_builtin_open, real_scandir = builtins_module.open, os_module.scandir

        def spy_read(self: Path, *args: Any, **kwargs: Any) -> bytes:
            touched.append(str(self))
            result: bytes = real_read(self, *args, **kwargs)
            return result

        def spy_open(self: Path, *args: Any, **kwargs: Any) -> Any:
            touched.append(str(self))
            return real_open(self, *args, **kwargs)

        def spy_builtin(file: Any, *args: Any, **kwargs: Any) -> Any:
            touched.append(str(file))
            return real_builtin_open(file, *args, **kwargs)

        def spy_scandir(path: Any = ".") -> Any:
            listed.append(str(path))
            return real_scandir(path)

        path_cls.read_bytes, path_cls.open = spy_read, spy_open
        builtins_module.open, os_module.scandir = spy_builtin, spy_scandir
        try:
            cap = capture_local_repository(repo, [allowed])
        finally:
            path_cls.read_bytes, path_cls.open = real_read, real_open
            builtins_module.open, os_module.scandir = real_builtin_open, real_scandir
        check(
            "FTF-04A06-F1",
            "capture still reads the visible files (the spy is live)",
            any(t.endswith("a.txt") for t in touched) and "a.txt" in _manifest_paths(cap),
        )
        check(
            "FTF-04A06-F1",
            "no ignored file is ever opened or read",
            not any(".env" in t or "node_modules" in t or "link-to-ignored" in t for t in touched),
        )
        check(
            "FTF-04A06-F1",
            "no ignored directory is ever listed (pruned, not walked)",
            not any("node_modules" in d or "link-to-ignored" in d for d in listed),
        )

    _with_tmp(body)


def test_f1_odd_filenames_captured_and_ignored_odd_names_absent() -> None:
    """Spaces, non-ASCII and an embedded newline survive git's NUL-separated
    listing in the manifest and archive; an ignored odd name stays absent."""

    def body(tmp: Path) -> None:
        allowed, repo = _dirty_ignore_repo(tmp, "ignored *\n")
        odd = ("with space.txt", "caf\u00e9-\u65e5\u672c.txt", "new\nline.txt")
        (repo / "pkg" / "tracked name.txt").write_text("t0\n")
        _git(repo, "add", "pkg/tracked name.txt")
        _git(repo, "commit", "-qm", "odd")
        (repo / "pkg" / "tracked name.txt").write_text("t1\n")  # tracked-modified
        for name in odd:
            (repo / name).write_text(f"untracked {name!r}\n")  # untracked
        (repo / "ignored space.txt").write_text("S\n")
        (repo / "ignored caf\u00e9.txt").write_text("S\n")
        (repo / "ignored \nx.txt").write_text("S\n")
        cap = capture_local_repository(repo, [allowed])
        paths, files = _manifest_paths(cap), _archive_files(cap)
        check(
            "FTF-04A06-F1",
            "untracked odd names are in the manifest and archive with their bytes",
            all(n in paths and files.get(n) == f"untracked {n!r}\n".encode() for n in odd),
        )
        check(
            "FTF-04A06-F1",
            "a tracked-modified odd name is captured with its dirty bytes",
            files.get("pkg/tracked name.txt") == b"t1\n",
        )
        check(
            "FTF-04A06-F1",
            "ignored odd names are absent from manifest and archive",
            not any(p.startswith("ignored ") for p in paths)
            and not any(p.startswith("ignored ") for p in files),
        )
        check(
            "FTF-04A06-F1",
            "the archive agrees with the manifest for odd names",
            _archive_agrees_with_manifest(cap),
        )

    _with_tmp(body)


def test_f1_unreadable_ignored_directory_does_not_break_capture() -> None:
    def body(tmp: Path) -> None:
        if os.geteuid() == 0:  # root bypasses mode bits; CI runs unprivileged
            return
        allowed, repo = _dirty_ignore_repo(tmp, "node_modules/\n")
        (repo / "node_modules").mkdir()
        (repo / "node_modules" / "x.js").write_text("x\n")
        (repo / "node_modules").chmod(0o000)
        try:
            cap = capture_local_repository(repo, [allowed])
            ok = "a.txt" in _manifest_paths(cap)
        except LocalRepositoryConnectorError:
            ok = False
        finally:
            (repo / "node_modules").chmod(0o755)
        check("FTF-04A06-F1", "an unreadable ignored dir never breaks the capture", ok)

    _with_tmp(body)


def test_f1_unreadable_visible_file_refused() -> None:
    """A visible file whose read fails is refused, never silently skipped. A
    read-failure stub covers privileged hosts; a real chmod-000 covers CI."""

    def body(tmp: Path) -> None:
        allowed, repo = _dirty_ignore_repo(tmp, "")
        (repo / "pkg" / "locked.txt").write_text("L\n")
        path_cls: Any = Path
        real_read = path_cls.read_bytes

        def failing(self: Path, *args: Any, **kwargs: Any) -> bytes:
            if self.name == "locked.txt":
                raise PermissionError(13, "denied")
            result: bytes = real_read(self, *args, **kwargs)
            return result

        path_cls.read_bytes = failing
        try:
            refused = _refused(
                lambda: capture_local_repository(repo, [allowed]), LocalRepositoryConnectorError
            )
        finally:
            path_cls.read_bytes = real_read
        check("FTF-04A06-F1", "an unreadable visible file is refused (read error)", refused)
        if os.geteuid() != 0:
            (repo / "pkg" / "locked.txt").chmod(0o000)
            try:
                check(
                    "FTF-04A06-F1",
                    "a chmod-000 visible file is refused, not skipped",
                    _refused(
                        lambda: capture_local_repository(repo, [allowed]),
                        LocalRepositoryConnectorError,
                    ),
                )
            finally:
                (repo / "pkg" / "locked.txt").chmod(0o644)

    _with_tmp(body)


def test_n3_unreadable_directory_refused() -> None:
    """The walker's directory-read failure is a connector error, not a crash or a skip."""

    def body(tmp: Path) -> None:
        if os.geteuid() == 0:  # root bypasses mode bits; CI runs unprivileged
            return
        repo = _new_checkout(tmp)
        (repo / "a.txt").write_text("dirty\n")
        (repo / "pkg").chmod(0o000)
        try:
            check(
                "FTF-04A32-N3",
                "an unreadable directory fails with a connector error",
                _refused(
                    lambda: capture_local_repository(repo, [tmp]),
                    LocalRepositoryConnectorError,
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
