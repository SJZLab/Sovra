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
            cycle_ok = len(paths) == len(set(paths)) and "a.txt" in paths
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
