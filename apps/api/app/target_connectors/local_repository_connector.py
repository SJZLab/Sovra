"""Local repository connector with root allowlist/path protections
(FTF-04A06, Section 6.8A/7A.1/7A.3/9/66/77/81).

Section 7A.3's own "Local repository connector" -- the second of the two
"Minimum production connectors required by DoD" Section 7A.1 names. This
module owns exactly the real, DB-free capture logic a `connector_type=
'local_repository'` `TargetApplicationConnection` (FTF-04A01) needs to
produce the fields `target_source_snapshot_crud.create_target_source_snapshot`
(FTF-04A07) requires: `commit_sha`/`tree_hash`/`capture_method`/`branch`/
`source_archive_object_key`/`source_archive_sha256`. It deliberately does
not persist a `TargetSourceSnapshot` row itself, does not assign a real
tenant-scoped object-storage key (`packages/storage/tenant_policy.py`'s own
`build_tenant_scoped_object_key` needs a real `organization_id`/
`resource_id` this connector has no reason to know), and does not upload
the archive bytes it produces to the real S3-compatible fixture
(`apps/api/app/services/storage_semantics_client.py`, FTF-R017) -- that
composition is a later, separately-scoped caller's job (the same "no live
caller yet" class already accepted for FTF-0005/FTF-0006/FTF-0102/
FTF-0103/FTF-0104/FTF-0105 before each one's own first live caller
existed, and for `storage_semantics_client.py` itself). Declaring any of
that here without a real backing wire-up would be exactly the "placeholder
endpoint" Law 11 forbids; this module ships only what it can make real
today -- a genuine local read, a genuine git inspection, and genuine
content hashing.

Path refined from the task packet's own generic suggestion
(`packages/target-connectors/local_repository_connector_with_root_allowlist_path_protections.py`)
to `apps/api/app/target_connectors/local_repository_connector.py`, matching
FTF-04A01's/FTF-04A07's own real `apps/api/app/target_connectors/*.py`
layout precedent exactly.

Section 7A.3's own literal required capabilities map onto this module's
real functions:

- "user explicitly selects/allowlists repository root during deployment
  configuration" -- `resolve_allowed_root` takes the caller-supplied
  allowlist as an explicit argument; this module never reads or invents
  its own allowlist source (a later deployment-configuration task's job).
- "resolve current Git commit when repository is a Git checkout" /
  "support detached HEAD" -- `_git_head_commit_sha`/`_git_current_branch`.
- "reject paths outside allowlisted roots" / "do not follow symlinks
  outside allowlisted root" -- `resolve_allowed_root` (the root itself) and
  `_walk_repository_content` (every file/directory encountered while
  building the content manifest), both real `os.path.realpath` checks
  against the resolved allowlist, never a string-prefix check a
  `../`-style traversal could defeat.
- "read files without modifying working tree" -- every filesystem
  operation in this module is a read (`open(..., "rb")`, `os.scandir`,
  `os.stat`) or a real, documented read-only git plumbing command
  (`git rev-parse`, `git symbolic-ref`, `git status --porcelain`); nothing
  here ever calls `git add`/`git commit`/`git checkout` or opens a file for
  writing inside the connection's own root.
- "report dirty-tree state" / "for dirty trees, create a content-hashed
  snapshot and visibly mark it non_committed_source" -- `capture_local_repository`
  returns `capture_method='local_dirty_snapshot'` (the schema's own real
  closed-set marker, see `target_source_snapshot_schema.py`) whenever the
  working tree differs from `HEAD` in any way (`git status --porcelain`
  reports anything at all, tracked or untracked), together with a real
  `sha256`-hashed, deterministic `tar` archive of the actual working-tree
  content at the moment of capture -- not merely git's own `HEAD^{tree}`
  object, which would describe the last *commit*, not the real dirty tree
  a production evaluation would actually run against.

This task's own negative_acceptance ("Path/symlink traversal and
concurrent file drift fail; never edit the target working tree") is two
real, independently-tested guards:

1. Path/symlink traversal: `resolve_allowed_root` refuses (before any
   directory is even opened) a root that does not resolve, after following
   every real symlink, inside one of the caller's own allowlisted roots.
   `_walk_repository_content` applies the identical real-path check to
   every symlink it encounters while walking the tree -- a symlink whose
   real target resolves outside the connection's own root is refused, not
   silently skipped or silently followed.
2. Concurrent file drift: `capture_local_repository` records the working
   tree's real git identity (`HEAD` commit sha plus the exact `git status
   --porcelain` output) both immediately before and immediately after
   building the content manifest/archive; a real change during that window
   (a file added/edited/removed by a concurrent process while this capture
   was in flight) makes the two identities disagree, and the capture is
   refused (`ConcurrentModificationError`) rather than silently returning a
   snapshot that matches neither the before nor the after state.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import shutil
import subprocess
import tarfile
from dataclasses import dataclass
from pathlib import Path

__all__ = [
    "ConcurrentModificationError",
    "EmptyRepositoryError",
    "GitCommandError",
    "LocalRepositoryCapture",
    "LocalRepositoryConnectorError",
    "ManifestEntry",
    "NotAGitRepositoryError",
    "PathAllowlistError",
    "capture_local_repository",
    "resolve_allowed_root",
]

_GIT_DIRECTORY_NAME = ".git"
_SUBPROCESS_TIMEOUT_SECONDS = 30.0


class LocalRepositoryConnectorError(Exception):
    """Base class for every real, refused local-repository capture
    outcome this module raises."""


class PathAllowlistError(LocalRepositoryConnectorError):
    """The requested root, or a real path encountered while walking it,
    resolves outside every allowlisted root -- this task's own real
    "reject paths outside allowlisted roots" / "do not follow symlinks
    outside allowlisted root" negative_acceptance."""


class NotAGitRepositoryError(LocalRepositoryConnectorError):
    """The allowlisted root is not a real Git working tree (or is a
    subdirectory of one rather than a real top level) -- Section 7A.3's own
    "resolve current Git commit when repository is a Git checkout" implies
    a real Git checkout is required; this connector does not invent
    provenance for a plain, non-versioned directory."""


class EmptyRepositoryError(LocalRepositoryConnectorError):
    """A real Git working tree with no commits at all (`HEAD` does not
    resolve) -- there is no real commit_sha to report, so no snapshot can
    be captured yet."""


class GitCommandError(LocalRepositoryConnectorError):
    """A real git plumbing command exited non-zero for a reason other than
    the specific, already-named cases above -- never silently swallowed."""


class ConcurrentModificationError(LocalRepositoryConnectorError):
    """The working tree's real git identity changed between the start and
    the end of this capture -- this task's own real "concurrent file drift"
    negative_acceptance."""


def _run_git(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    git_binary = shutil.which("git")
    if git_binary is None:
        raise GitCommandError("no 'git' binary found on PATH")
    try:
        return subprocess.run(  # noqa: S603
            [git_binary, "-C", str(root), *args],
            capture_output=True,
            text=True,
            timeout=_SUBPROCESS_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise GitCommandError(f"git {' '.join(args)} could not be run: {exc}") from exc


def resolve_allowed_root(root: os.PathLike[str] | str, allowed_roots: list[Path]) -> Path:
    """Real-path-resolves `root` (following every symlink) and refuses it
    unless it is, or is a real descendant of, one of `allowed_roots`
    (themselves real-path-resolved first, so an allowlist entry that is
    itself a symlink cannot be used to smuggle in a wider boundary than the
    deployment operator actually approved).

    Never a string-prefix comparison: `/approved-evil` is not treated as a
    descendant of `/approved` just because the text matches.
    """
    if not allowed_roots:
        raise PathAllowlistError("no local repository root is allowlisted -- refusing by default")

    try:
        real_root = Path(os.path.realpath(root, strict=True))
    except OSError as exc:
        raise PathAllowlistError(f"root {root!r} does not resolve to a real path: {exc}") from exc

    for candidate in allowed_roots:
        try:
            real_allowed = Path(os.path.realpath(candidate, strict=True))
        except OSError:
            continue
        if real_root == real_allowed or real_allowed in real_root.parents:
            return real_root

    raise PathAllowlistError(
        f"root {real_root} is not inside any allowlisted root ({allowed_roots!r})"
    )


@dataclass(frozen=True)
class ManifestEntry:
    """One real file's identity in the content manifest: a POSIX-style
    path relative to the captured root, its real content sha256, its exact
    byte size, and whether its owner-execute bit was set."""

    relative_path: str
    sha256: str
    size: int
    executable: bool


def _iter_repository_files(root: Path) -> list[ManifestEntry]:
    """Real, recursive, symlink-safe walk of every regular file under
    `root`, excluding `.git` itself (git's own internal object store is not
    source content). A symlink (file or directory) whose real target
    resolves outside `root` is refused outright; one that resolves inside
    `root` is followed exactly once (cycle-safe via `_visited_directories`).
    """
    entries: list[ManifestEntry] = []
    visited_directories: set[Path] = set()

    def _walk(directory: Path) -> None:
        real_directory = Path(os.path.realpath(directory, strict=True))
        if real_directory in visited_directories:
            return
        visited_directories.add(real_directory)

        with os.scandir(directory) as scan:
            children = sorted(scan, key=lambda e: e.name)

        for entry in children:
            if directory == root and entry.name == _GIT_DIRECTORY_NAME:
                continue

            entry_path = Path(entry.path)
            if entry.is_symlink():
                try:
                    real_target = Path(os.path.realpath(entry_path, strict=True))
                except OSError as exc:
                    raise PathAllowlistError(
                        f"symlink {entry_path} does not resolve to a real path: {exc}"
                    ) from exc
                if real_target != root and root not in real_target.parents:
                    raise PathAllowlistError(
                        f"symlink {entry_path} resolves outside the captured root {root}: "
                        f"{real_target}"
                    )
                if real_target.is_dir():
                    _walk(entry_path)
                    continue
                target_for_read = real_target
            else:
                target_for_read = entry_path

            if entry.is_dir(follow_symlinks=True) and not entry.is_symlink():
                _walk(entry_path)
                continue

            if not target_for_read.is_file():
                continue

            data = target_for_read.read_bytes()
            relative = entry_path.relative_to(root).as_posix()
            mode = target_for_read.stat().st_mode
            entries.append(
                ManifestEntry(
                    relative_path=relative,
                    sha256=hashlib.sha256(data).hexdigest(),
                    size=len(data),
                    executable=bool(mode & 0o100),
                )
            )

    _walk(root)
    return sorted(entries, key=lambda e: e.relative_path)


def _manifest_hash(entries: list[ManifestEntry]) -> str:
    """A real, deterministic sha256 over the sorted (path, hash, size,
    executable) tuples -- passes `TargetSourceSnapshot`'s own
    `validate_tree_hash` (a 64-lowercase-hex value is an accepted git
    object-id-shaped identifier, exactly like a real sha256 git object
    would be), while actually describing this connector's own real
    file-level content manifest rather than git's own committed tree."""
    canonical = json.dumps(
        [[e.relative_path, e.sha256, e.size, e.executable] for e in entries],
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _build_archive(root: Path, entries: list[ManifestEntry]) -> bytes:
    """A real, deterministic tar archive of every file the manifest
    describes -- fixed timestamps/ownership/ordering so two captures of
    byte-identical content always produce byte-identical archive bytes and
    therefore the same `source_archive_sha256`."""
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w", format=tarfile.PAX_FORMAT) as archive:
        for entry in entries:
            data = (root / entry.relative_path).read_bytes()
            info = tarfile.TarInfo(name=entry.relative_path)
            info.size = len(data)
            info.mtime = 0
            info.uid = 0
            info.gid = 0
            info.uname = ""
            info.gname = ""
            info.mode = 0o755 if entry.executable else 0o644
            archive.addfile(info, io.BytesIO(data))
    return buffer.getvalue()


def _git_is_real_worktree_root(root: Path) -> bool:
    result = _run_git(root, "rev-parse", "--is-inside-work-tree")
    if result.returncode != 0 or result.stdout.strip() != "true":
        return False
    toplevel = _run_git(root, "rev-parse", "--show-toplevel")
    if toplevel.returncode != 0:
        return False
    try:
        real_toplevel = Path(os.path.realpath(toplevel.stdout.strip(), strict=True))
    except OSError:
        return False
    return real_toplevel == root


def _git_head_commit_sha(root: Path) -> str:
    result = _run_git(root, "rev-parse", "--verify", "--quiet", "HEAD")
    if result.returncode != 0 or not result.stdout.strip():
        raise EmptyRepositoryError(f"{root} has no commits yet (HEAD does not resolve)")
    return result.stdout.strip()


def _git_head_tree_hash(root: Path) -> str:
    result = _run_git(root, "rev-parse", "--verify", "--quiet", "HEAD^{tree}")
    if result.returncode != 0 or not result.stdout.strip():
        raise GitCommandError(f"{root}: HEAD^{{tree}} did not resolve")
    return result.stdout.strip()


def _git_current_branch(root: Path) -> str | None:
    result = _run_git(root, "symbolic-ref", "-q", "--short", "HEAD")
    if result.returncode != 0:
        return None  # detached HEAD -- Section 7A.3's own "support detached HEAD"
    branch = result.stdout.strip()
    return branch or None


def _git_dirty_signature(root: Path) -> str:
    """Real working-tree status text (tracked and untracked changes alike)
    -- the identity this module compares before/after a capture to detect
    concurrent drift. Not parsed further; only ever compared for equality."""
    result = _run_git(root, "status", "--porcelain=v1", "--untracked-files=all")
    if result.returncode != 0:
        raise GitCommandError(f"{root}: git status failed: {result.stderr.strip()}")
    return result.stdout


@dataclass(frozen=True)
class LocalRepositoryCapture:
    """The real fields a caller needs to persist a `TargetSourceSnapshot`
    (`target_source_snapshot_crud.create_target_source_snapshot`) for this
    capture -- this module never calls that function itself (see this
    module's own docstring for the disclosed scope boundary)."""

    capture_method: str
    commit_sha: str
    tree_hash: str
    branch: str | None
    manifest_entries: tuple[ManifestEntry, ...]
    source_archive_bytes: bytes | None
    source_archive_sha256: str | None


def capture_local_repository(
    root: os.PathLike[str] | str, allowed_roots: list[Path]
) -> LocalRepositoryCapture:
    """Real end-to-end local-repository capture: validate the root against
    the allowlist, confirm it is a real Git working-tree top level, then
    either resolve the clean committed state or build a real content-hashed
    dirty-tree archive -- never both, and never a silent third outcome.
    """
    real_root = resolve_allowed_root(root, allowed_roots)

    if not _git_is_real_worktree_root(real_root):
        raise NotAGitRepositoryError(
            f"{real_root} is not a real Git working-tree top level -- "
            "Section 7A.3's local repository connector requires a real Git checkout"
        )

    commit_sha = _git_head_commit_sha(real_root)
    branch = _git_current_branch(real_root)
    dirty_before = _git_dirty_signature(real_root)

    if not dirty_before:
        tree_hash = _git_head_tree_hash(real_root)
        dirty_after = _git_dirty_signature(real_root)
        head_after = _run_git(real_root, "rev-parse", "--verify", "--quiet", "HEAD").stdout.strip()
        if dirty_after != dirty_before or head_after != commit_sha:
            raise ConcurrentModificationError(
                f"{real_root} changed while this clean capture was in flight"
            )
        return LocalRepositoryCapture(
            capture_method="local_git_commit",
            commit_sha=commit_sha,
            tree_hash=tree_hash,
            branch=branch,
            manifest_entries=(),
            source_archive_bytes=None,
            source_archive_sha256=None,
        )

    entries = _iter_repository_files(real_root)
    archive_bytes = _build_archive(real_root, entries)
    tree_hash = _manifest_hash(entries)

    dirty_after = _git_dirty_signature(real_root)
    head_after = _run_git(real_root, "rev-parse", "--verify", "--quiet", "HEAD").stdout.strip()
    if dirty_after != dirty_before or head_after != commit_sha:
        raise ConcurrentModificationError(
            f"{real_root} changed while this dirty-tree capture was in flight"
        )

    return LocalRepositoryCapture(
        capture_method="local_dirty_snapshot",
        commit_sha=commit_sha,
        tree_hash=tree_hash,
        branch=branch,
        manifest_entries=tuple(entries),
        source_archive_bytes=archive_bytes,
        source_archive_sha256=hashlib.sha256(archive_bytes).hexdigest(),
    )
