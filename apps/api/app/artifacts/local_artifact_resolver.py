"""Local artifact resolver (FTF-0304, Section 6.7/13/34/68/77/81).

Refined from the task packet's own suggested path
(`packages/model-catalog/local_artifact_resolver.py`) to this one --
`packages/model-catalog` is not a `uv` workspace member and, being
hyphenated, is not even a valid Python import path. FTF-0301/FTF-0305
already established the precedent of refining that same suggested
`packages/model-catalog/...` prefix into a real `apps/api/app/artifacts/*`
module instead of creating an unimportable package; this task follows the
identical refinement for the identical reason.

FTF-0301 (`artifact_schema.py`, CERTIFIED) already validates a
`local_filesystem` `TrainableArtifact.source_uri` down to *shape*
(relative, no `..` traversal segment, no scheme, no NUL byte, non-empty)
at construction/edit time -- see `validate_source_uri`/
`_validate_local_filesystem_uri`. This task's own acceptance scope is
narrower and later than that: given a shape-valid `source_uri` and an
operator-approved local root directory, actually walk the real bytes on
disk beneath that root and compute a deterministic file manifest --
this task's own acceptance clause ("Resolve artifact beneath approved
local root and calculate deterministic file manifest"). Resolving the
*approved root itself* from real deployment configuration (a future
FastAPI app's own settings) has no live caller yet in this repository --
the same disclosed-gap precedent already independently CERTIFIED for
FTF-0103/FTF-0104/FTF-0105/FTF-H003/FTF-0301/FTF-0402/FTF-0404/FTF-0405 --
so `approved_root` is this module's own explicit parameter, not read from
a global here.

This task's own negative_acceptance ("Symlink/path escape, changing file
during read and unsafe executable artifact are rejected") is enforced by
three independent checks, each with its own real test:

1. **Symlink/path escape.** No path component between `approved_root` and
   the resolved artifact -- and no file or directory anywhere inside a
   directory artifact -- may be a symlink; `os.walk(..., followlinks=False)`
   alone is not enough, since it still lists (without descending into) a
   symlinked subdirectory, so that case is checked and rejected explicitly
   rather than silently skipped. As defense in depth beyond the shape check
   `validate_source_uri` already performs, the fully resolved real path is
   also independently reconfirmed to still live beneath the resolved real
   root.
2. **Changing file during read.** Every file's `(size, mtime_ns)` is
   captured immediately before and immediately after it is read and
   hashed; a mismatch raises rather than silently returning a manifest
   entry for bytes that were never actually all read as one consistent
   version -- a TOCTOU race, not a safe artifact.
3. **Unsafe executable artifact.** Any file carrying an owner/group/other
   executable permission bit is rejected outright; a trainable artifact is
   data to be loaded by a pinned, approved training/serving stack, never
   an executable to be run.

Deliberately does not itself compute `weights_digest` (Section 6.7's own
column) or write it back onto any `TrainableArtifact` row -- that is
FTF-0306 ("Base model revision/digest resolver")'s own, later, separately
dependency-gated job, and no live caller wires this module's own
`manifest_sha256` into that column here. Declaring that resolution
complete without a real backing call would be exactly the "placeholder
endpoint" Law 11 forbids.
"""

from __future__ import annotations

import hashlib
import os
import stat
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from app.artifacts.artifact_schema import validate_source_uri

__all__ = [
    "LocalArtifactFileEntry",
    "LocalArtifactManifest",
    "LocalArtifactResolutionError",
    "resolve_local_artifact",
]

_CHUNK_SIZE = 1 << 20  # 1 MiB
_EXECUTABLE_BITS = stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH


class LocalArtifactResolutionError(ValueError):
    """The requested `local_filesystem` artifact cannot be safely
    resolved -- a symlink/path escape, a file that changed while being
    read, an unsafe executable artifact, a missing path or a path that is
    neither a regular file nor a directory."""


@dataclass(frozen=True)
class LocalArtifactFileEntry:
    """One regular file inside a resolved local artifact.

    `relative_path` is POSIX-style (forward slashes), relative to the
    artifact's own root -- not the approved root, and not the host's
    absolute filesystem path, so the manifest never leaks the operator's
    real directory layout."""

    relative_path: str
    size_bytes: int
    sha256: str


@dataclass(frozen=True)
class LocalArtifactManifest:
    """A deterministic manifest of every regular file inside a resolved
    local artifact -- same `source_uri` and same real bytes always
    produce the same `manifest_sha256`, computed only from the sorted
    `(relative_path, sha256, size_bytes)` triples, never from filesystem
    metadata like mtime/inode/owner that carries no content meaning."""

    source_uri: str
    entries: tuple[LocalArtifactFileEntry, ...]
    total_size_bytes: int
    manifest_sha256: str


def _stat_signature(path: Path) -> tuple[int, int]:
    st = path.stat()
    return (st.st_size, st.st_mtime_ns)


def _iter_chunks(path: Path, chunk_size: int) -> Iterator[bytes]:
    with open(path, "rb") as fh:
        while True:
            chunk = fh.read(chunk_size)
            if not chunk:
                return
            yield chunk


def _hash_file_integrity_checked(path: Path, *, chunk_size: int = _CHUNK_SIZE) -> tuple[str, int]:
    """Hashes `path`'s real bytes, refusing (`LocalArtifactResolutionError`)
    if its `(size, mtime_ns)` differs between the moment reading starts and
    the moment it finishes -- this task's own "changing file during read"
    negative_acceptance case, made executable rather than merely asserted.
    Reads via the module-level `_iter_chunks` (not inlined) so a test can
    substitute a chunk source that mutates the real file mid-iteration and
    prove this check actually fires, not just that its comparison logic is
    plausible in isolation."""
    before = _stat_signature(path)
    digest = hashlib.sha256()
    size = 0
    for chunk in _iter_chunks(path, chunk_size):
        digest.update(chunk)
        size += len(chunk)
    after = _stat_signature(path)
    if before != after:
        raise LocalArtifactResolutionError(
            f"file changed while being read, not a safe artifact: {path}"
        )
    return digest.hexdigest(), size


def _reject_symlink_ancestors(root: Path, source_uri: str) -> Path:
    node = root
    for part in Path(source_uri).parts:
        node = node / part
        if node.is_symlink():
            raise LocalArtifactResolutionError(f"symlink not allowed in artifact path: {node}")
        if not node.exists():
            raise LocalArtifactResolutionError(f"artifact path does not exist: {node}")
    return node


def _confirm_no_real_path_escape(root: Path, node: Path) -> None:
    real_root = root.resolve(strict=True)
    real_node = node.resolve(strict=True)
    if real_node != real_root and real_root not in real_node.parents:
        raise LocalArtifactResolutionError(f"artifact path escapes approved root: {node}")


def _collect_files(node: Path) -> list[Path]:
    if node.is_file():
        return [node]
    if not node.is_dir():
        raise LocalArtifactResolutionError(
            f"artifact path is neither a regular file nor a directory: {node}"
        )
    files: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(node, followlinks=False):
        dirpath_p = Path(dirpath)
        for dirname in dirnames:
            candidate = dirpath_p / dirname
            if candidate.is_symlink():
                raise LocalArtifactResolutionError(
                    f"symlink not allowed in artifact tree: {candidate}"
                )
        for filename in filenames:
            candidate = dirpath_p / filename
            if candidate.is_symlink():
                raise LocalArtifactResolutionError(
                    f"symlink not allowed in artifact tree: {candidate}"
                )
            files.append(candidate)
    if not files:
        raise LocalArtifactResolutionError(f"artifact contains no regular files: {node}")
    return files


def _manifest_sha256(entries: tuple[LocalArtifactFileEntry, ...]) -> str:
    digest = hashlib.sha256()
    for entry in entries:
        digest.update(entry.relative_path.encode("utf-8"))
        digest.update(b"\0")
        digest.update(entry.sha256.encode("ascii"))
        digest.update(b"\0")
        digest.update(str(entry.size_bytes).encode("ascii"))
        digest.update(b"\n")
    return digest.hexdigest()


def resolve_local_artifact(approved_root: str | Path, source_uri: str) -> LocalArtifactManifest:
    """Resolves a `local_filesystem` `TrainableArtifact.source_uri`
    beneath an operator-approved root directory into a deterministic file
    manifest. Raises `LocalArtifactResolutionError` for every case this
    task's own negative_acceptance names; raises
    `TrainableArtifactValidationError` (re-exported by
    `artifact_schema.validate_source_uri`) first if `source_uri` does not
    even meet FTF-0301's own shape rules."""
    validate_source_uri("local_filesystem", source_uri)

    root = Path(approved_root).resolve(strict=True)
    if not root.is_dir():
        raise LocalArtifactResolutionError(f"approved_root is not a directory: {root}")

    node = _reject_symlink_ancestors(root, source_uri)
    _confirm_no_real_path_escape(root, node)

    files = _collect_files(node)
    artifact_base = node if node.is_dir() else node.parent

    entries: list[LocalArtifactFileEntry] = []
    total_size = 0
    for file_path in files:
        st = file_path.lstat()
        if not stat.S_ISREG(st.st_mode):
            raise LocalArtifactResolutionError(f"artifact entry is not a regular file: {file_path}")
        if st.st_mode & _EXECUTABLE_BITS:
            raise LocalArtifactResolutionError(
                f"executable artifact file is not allowed: {file_path}"
            )
        digest, size = _hash_file_integrity_checked(file_path)
        relative_path = file_path.relative_to(artifact_base).as_posix()
        entries.append(
            LocalArtifactFileEntry(relative_path=relative_path, size_bytes=size, sha256=digest)
        )
        total_size += size

    entries.sort(key=lambda entry: entry.relative_path)
    entries_tuple = tuple(entries)
    return LocalArtifactManifest(
        source_uri=source_uri,
        entries=entries_tuple,
        total_size_bytes=total_size,
        manifest_sha256=_manifest_sha256(entries_tuple),
    )
