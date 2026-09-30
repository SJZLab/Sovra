"""Snapshot inventory contract (FTF-H007, Section 7A/66/77/81).

Path refined from the task packet's own generic suggestion
(`packages/target-connectors/contracts/snapshot_inventory.py`) to
`apps/api/app/target_connectors/snapshot_inventory.py`, matching every
sibling 04A-family task's own real `apps/api/app/target_connectors/*.py`
layout precedent (`packages/target-connectors/` does not exist anywhere in
this repository and, being hyphenated, is not even a valid Python import
path -- the same refinement FTF-0302/FTF-R002/FTF-04A05/FTF-04A06 already
recorded for the identical reason).

`local_repository_connector.py` (FTF-04A06, CERTIFIED, imported here
unmodified) already builds a real, allowlist-checked, symlink-safe file
manifest for a *clean* capture -- but it is deliberately **fail-fast**:
the first unsafe entry it meets (an escaping symlink) raises
`PathAllowlistError` and aborts the whole walk, which is exactly right for
"safely capture one unambiguous manifest to archive" but wrong for "find
and report every problem in this tree for security review" -- FTF-H007's
own real goal. This module owns a second, independent walk of the same
real, already-allowlist-resolved root: **continue-on-finding** rather than
abort-on-first-finding, so a single malicious entry does not hide every
other real file (or every other real problem) behind it. It reuses
`local_repository_connector.resolve_allowed_root` unmodified for the root
check itself -- never reimplementing that already-CERTIFIED logic -- and
applies the identical real `os.path.realpath`-based symlink-escape
technique `local_repository_connector._iter_repository_files` already
established, to every entry this walk meets, but records a finding instead
of raising.

This module owns exactly the *inventory* (what is in the tree, and whether
the tree itself is a complete, resolvable set of real bytes) --
`source_boundary.py` (FTF-H007's other owned file) owns the *safety*
findings computed from it (dangerous dynamic code, executable git hooks)
and the final lock-readiness decision. Splitting the two mirrors the same
"collect real facts, decide readiness separately" shape
`source_analysis_worker.py` (FTF-04A08) already established for its own
`SourceAnalysisEvidence`.
"""

from __future__ import annotations

import fnmatch
import hashlib
import os
from configparser import ConfigParser
from dataclasses import dataclass, field
from pathlib import Path

from app.target_connectors.local_repository_connector import resolve_allowed_root

__all__ = [
    "DEFAULT_MAX_ENTRIES",
    "InventoryEntry",
    "SnapshotInventory",
    "SnapshotInventoryError",
    "build_local_snapshot_inventory",
]

_GIT_DIRECTORY_NAME = ".git"
_GITATTRIBUTES_NAME = ".gitattributes"
_GITMODULES_NAME = ".gitmodules"
_LFS_POINTER_HEADER = "version https://git-lfs.github.com/spec/v1"

# A real, disclosed resource bound -- the same class of defense
# `source_analysis_worker.py`'s own `ResourceLimits` already established
# (unbounded input from an untrusted repository is itself a real risk).
# Exceeding it marks the inventory `truncated=True` rather than silently
# reporting a "complete" scan of only part of the tree.
DEFAULT_MAX_ENTRIES = 50_000


class SnapshotInventoryError(ValueError):
    """The requested root cannot be inventoried at all (not a real,
    resolvable directory) -- distinct from a per-entry finding, which is
    recorded on the returned `SnapshotInventory`, never raised."""


@dataclass(frozen=True)
class InventoryEntry:
    """One real regular file this walk actually read, or one real symlink
    it refused to read through. `escapes_root` is only ever `True` for a
    symlink entry; a regular file never sets it. `content` is the real
    bytes read for a regular file (never populated for a refused symlink,
    which this walk never dereferences for content at all)."""

    relative_path: str
    is_symlink: bool
    escapes_root: bool
    executable: bool
    size: int
    sha256: str | None
    content: bytes | None = field(repr=False, default=None)


@dataclass(frozen=True)
class SnapshotInventory:
    """The real, connector-agnostic facts this task's own goal names:
    whether the tree is complete, which submodules/LFS paths are declared
    but not actually resolved on disk, and every real entry (safe or not)
    this walk met -- never a claim about whether any of this is *safe*,
    which is `source_boundary.py`'s own separate job."""

    source_label: str
    entries: tuple[InventoryEntry, ...]
    truncated: bool
    declared_submodule_paths: tuple[str, ...]
    unresolved_submodule_paths: tuple[str, ...]
    lfs_tracked_patterns: tuple[str, ...]
    unresolved_lfs_paths: tuple[str, ...]
    is_dirty_working_tree: bool


def _parse_lfs_patterns(gitattributes_text: str) -> tuple[str, ...]:
    """Identical, minimal `.gitattributes` parsing to
    `github_source_snapshot_resolver._parse_lfs_patterns` (FTF-04A05) --
    a real, documented Git LFS convention, not reached into that module's
    own private symbol (this is ~10 lines of generic text parsing, not the
    security-critical logic that module owns)."""
    patterns: list[str] = []
    for line in gitattributes_text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        parts = stripped.split()
        if len(parts) < 2:
            continue
        pattern, attributes = parts[0], parts[1:]
        if any(attr == "filter=lfs" for attr in attributes):
            patterns.append(pattern)
    return tuple(patterns)


def _parse_submodule_paths(gitmodules_text: str) -> tuple[str, ...]:
    """Real, declarative `.gitmodules` parsing via the standard library's
    `ConfigParser` (INI-shaped, per the real Git convention) -- never
    executed, never shelled out to `git submodule`."""
    parser = ConfigParser()
    try:
        parser.read_string(gitmodules_text)
    except Exception:  # noqa: BLE001 -- a malformed .gitmodules is unresolved, not a crash
        return ()
    paths: list[str] = []
    for section in parser.sections():
        if parser.has_option(section, "path"):
            paths.append(parser.get(section, "path"))
    return tuple(paths)


def _is_unresolved_lfs_pointer(data: bytes) -> bool:
    """A real Git LFS pointer file is a small, plain-text stub -- the
    actual tracked bytes were never fetched into this working tree. Only
    ever inspects content this walk already safely read; never fetches
    anything."""
    if len(data) > 1024:
        return False
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return False
    return text.startswith(_LFS_POINTER_HEADER)


def build_local_snapshot_inventory(
    root: os.PathLike[str] | str,
    allowed_roots: list[Path],
    *,
    max_entries: int = DEFAULT_MAX_ENTRIES,
    is_dirty_working_tree: bool = False,
) -> SnapshotInventory:
    """Real, continue-on-finding inventory of a real, allowlist-resolved
    local repository root. Raises only `PathAllowlistError` (from the
    reused, unmodified `resolve_allowed_root`) if the root itself is
    outside the allowlist -- every per-entry problem inside an otherwise
    approved root is a recorded `InventoryEntry`/list member, never an
    exception, so one malicious entry can never hide the rest of the real
    evidence.

    `is_dirty_working_tree` is never computed here -- this module never
    shells out to `git status` itself, matching this task's own "Consume
    existing tenant-scoped schemas" scope: `local_repository_connector.
    capture_local_repository`'s own real, already-CERTIFIED
    `capture_method` (`local_git_commit` vs. `local_dirty_snapshot`) is the
    one real source of that fact; a caller that already ran it passes the
    result straight through rather than this module re-deriving it."""
    real_root = resolve_allowed_root(root, allowed_roots)

    entries: list[InventoryEntry] = []
    truncated = False
    visited_directories: set[Path] = set()
    gitattributes_text: str | None = None
    gitmodules_text: str | None = None

    def _walk(directory: Path) -> None:
        nonlocal truncated
        if truncated:
            return
        try:
            real_directory = Path(os.path.realpath(directory, strict=True))
        except OSError:
            return
        if real_directory in visited_directories:
            return
        visited_directories.add(real_directory)

        try:
            with os.scandir(directory) as scan:
                children = sorted(scan, key=lambda e: e.name)
        except OSError:
            return

        for entry in children:
            if len(entries) >= max_entries:
                truncated = True
                return
            if directory == real_root and entry.name == _GIT_DIRECTORY_NAME:
                continue

            entry_path = Path(entry.path)
            if entry.is_symlink():
                try:
                    real_target = Path(os.path.realpath(entry_path, strict=True))
                except OSError:
                    # A dangling symlink is neither a resolvable file nor a
                    # provable escape -- recorded, never silently dropped.
                    relative = entry_path.relative_to(real_root).as_posix()
                    entries.append(
                        InventoryEntry(
                            relative_path=relative,
                            is_symlink=True,
                            escapes_root=False,
                            executable=False,
                            size=0,
                            sha256=None,
                        )
                    )
                    continue
                escapes = real_target != real_root and real_root not in real_target.parents
                relative = entry_path.relative_to(real_root).as_posix()
                if escapes:
                    # This task's own real "produces evidence without ...
                    # escape" acceptance: the escaping target is never
                    # opened, read, or descended into -- only its own
                    # relative path (inside the approved root) is recorded.
                    entries.append(
                        InventoryEntry(
                            relative_path=relative,
                            is_symlink=True,
                            escapes_root=True,
                            executable=False,
                            size=0,
                            sha256=None,
                        )
                    )
                    continue
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
            relative = entry_path.relative_to(real_root).as_posix()
            mode = target_for_read.stat().st_mode
            entries.append(
                InventoryEntry(
                    relative_path=relative,
                    is_symlink=entry.is_symlink(),
                    escapes_root=False,
                    executable=bool(mode & 0o100),
                    size=len(data),
                    sha256=hashlib.sha256(data).hexdigest(),
                    content=data,
                )
            )

    _walk(real_root)
    entries.sort(key=lambda e: e.relative_path)

    for e in entries:
        if e.relative_path == _GITATTRIBUTES_NAME and e.content is not None:
            gitattributes_text = e.content.decode("utf-8", errors="replace")
        elif e.relative_path == _GITMODULES_NAME and e.content is not None:
            gitmodules_text = e.content.decode("utf-8", errors="replace")

    lfs_patterns = _parse_lfs_patterns(gitattributes_text) if gitattributes_text else ()
    unresolved_lfs: list[str] = []
    if lfs_patterns:
        for e in entries:
            if e.content is None:
                continue
            if any(fnmatch.fnmatch(e.relative_path, pattern) for pattern in lfs_patterns):
                if _is_unresolved_lfs_pointer(e.content):
                    unresolved_lfs.append(e.relative_path)

    declared_submodules = _parse_submodule_paths(gitmodules_text) if gitmodules_text else ()
    unresolved_submodules: list[str] = []
    for sub_path in declared_submodules:
        candidate = real_root / sub_path
        try:
            resolved = Path(os.path.realpath(candidate, strict=True))
        except OSError:
            unresolved_submodules.append(sub_path)
            continue
        if not resolved.is_dir() or not any(resolved.iterdir()):
            unresolved_submodules.append(sub_path)

    return SnapshotInventory(
        source_label="local_repository",
        entries=tuple(entries),
        truncated=truncated,
        declared_submodule_paths=declared_submodules,
        unresolved_submodule_paths=tuple(unresolved_submodules),
        lfs_tracked_patterns=lfs_patterns,
        unresolved_lfs_paths=tuple(unresolved_lfs),
        is_dirty_working_tree=is_dirty_working_tree,
    )
