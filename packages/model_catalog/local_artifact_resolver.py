"""Local artifact resolver (FTF-0304, Section 6.7/13/34/68/77/81).

Resolves a `TrainableArtifact` with `source_type == "local_filesystem"`
(FTF-0301's own `_validate_local_filesystem_uri` already guarantees the
*stored* `source_uri` is a bare, traversal-free relative path at
registration time -- `apps/api/app/artifacts/artifact_schema.py`) against
one real, operator-approved local root, and computes a deterministic file
manifest -- this task's own acceptance clause 1. It never resolves a
Hugging Face Hub, S3 bucket or private registry reference
(FTF-0302/FTF-0303's own, later, separately dependency-gated tasks) and
never computes/verifies `weights_digest` against the value already
registered on the `TrainableArtifact` row (FTF-0306's own job).

Deliberately does not import anything from `apps.api` (the schema-layer
shape check FTF-0301 already runs at registration time): this package
sits *beneath* `apps/api` in the dependency direction -- reusable by any
future worker that needs to resolve a local artifact, not only the API
process -- so it re-runs its own independent shape/escape/symlink/TOCTOU/
executable checks rather than reaching upward into the API app for them.
This is deliberate defense-in-depth, not duplication: never trust a value
crossing a layer boundary, the same "checked again, not merely trusted"
posture FTF-1301's negative-rate check already established across an
application-layer guard and a database CHECK constraint.

Path refinement recorded before READY: the task packet's own initial
owner path, `packages/model-catalog/local_artifact_resolver.py`, uses a
hyphen, which is not a valid Python package-directory name (`from
packages.model-catalog import ...` is a syntax error) -- every existing
sibling package in this repository (`packages/litellm_client`,
`packages/storage`) already uses an underscore for the identical reason.
This task refines the owner path to `packages/model_catalog/
local_artifact_resolver.py` -- same directory intent, a valid identifier,
no change to this task's own API/security scope. No live caller exists
yet (nothing in `apps/api`/`workers/*` imports this module) -- the same
disclosed "no live caller yet" class already accepted for
FTF-0102/FTF-0103/FTF-0104/FTF-0105/FTF-H003 before each one's own first
live caller existed; this task's own `files` list names only this module
and its test, not a route.

Negative_acceptance ("Symlink/path escape, changing file during read and
unsafe executable artifact are rejected") is enforced as follows:
  - shape-level path escape: `_reject_traversal_shape` refuses an
    absolute/home-relative/`..`-traversing/NUL-containing `source_uri`
    before any filesystem call is made;
  - symlink escape: `_reject_symlink_components` walks the *unresolved*
    path component by component and refuses the first symlink found --
    checked before `Path.resolve()` ever runs, so a symlink component can
    never be silently followed first; `_reject_symlinks_in_tree` then
    refuses any symlink found anywhere inside a resolved directory
    artifact, however deeply nested. Together, no symlink -- escaping the
    approved root or not -- is ever treated as a real member of a
    resolved artifact: a symlink that stays inside the boundary can still
    silently point a "file" at bytes the artifact never actually shipped.
    The resolved-path containment check (`root_real` a real ancestor of
    `candidate_real`) is kept as an independent, redundant second check
    of the same property rather than relying on the symlink checks alone;
  - changing file during read: `_hash_file_with_toctou_guard` stats each
    file immediately before opening it and again immediately after the
    read completes, and refuses the file if size, mtime, inode or device
    differ -- the classic check-then-use race a single upfront stat can
    miss;
  - unsafe executable artifact: the same guard refuses any regular file
    that carries an executable permission bit, and the directory walk
    above separately refuses any tree member that is neither a regular
    file nor a directory (a device/FIFO/socket has no legitimate place in
    a training artifact).
"""

from __future__ import annotations

import hashlib
import os
import stat
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

__all__ = [
    "ApprovedRootError",
    "ArtifactChangedDuringReadError",
    "ArtifactManifest",
    "ArtifactPathEscapeError",
    "ArtifactSymlinkError",
    "LocalArtifactResolutionError",
    "ResolvedArtifactFile",
    "UnsafeExecutableArtifactError",
    "resolve_local_artifact",
]

_EXECUTABLE_BITS = stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH
_READ_CHUNK_BYTES = 1 << 20


class LocalArtifactResolutionError(ValueError):
    """Base class for every refusal this resolver can raise."""


class ApprovedRootError(LocalArtifactResolutionError):
    """The approved local root itself is not a real, existing directory."""


class ArtifactPathEscapeError(LocalArtifactResolutionError):
    """`source_uri`, once resolved against the approved root, does not
    stay beneath it -- a shape-level `..`/absolute-path escape, or a
    resolved path whose real ancestor is not the approved root."""


class ArtifactSymlinkError(LocalArtifactResolutionError):
    """A symlink was found inside the artifact tree, at any depth."""


class ArtifactChangedDuringReadError(LocalArtifactResolutionError):
    """A file's size/mtime/inode/device changed between the stat taken
    immediately before hashing and the stat taken immediately after."""


class UnsafeExecutableArtifactError(LocalArtifactResolutionError):
    """A regular file inside the artifact tree carries an executable
    permission bit, or the tree contains a non-regular, non-directory
    member (device/FIFO/socket)."""


@dataclass(frozen=True)
class ResolvedArtifactFile:
    relative_path: str
    size_bytes: int
    sha256: str
    mode: int


@dataclass(frozen=True)
class ArtifactManifest:
    approved_root: str
    source_uri: str
    resolved_path: str
    files: tuple[ResolvedArtifactFile, ...]
    total_size_bytes: int
    manifest_sha256: str
    resolved_at: datetime


def _reject_traversal_shape(source_uri: object) -> str:
    if not isinstance(source_uri, str) or not source_uri.strip():
        raise LocalArtifactResolutionError("source_uri must be a non-empty string")
    if "\x00" in source_uri:
        raise ArtifactPathEscapeError("source_uri must not contain a NUL byte")
    if "://" in source_uri or source_uri.startswith("/") or source_uri.startswith("~"):
        raise ArtifactPathEscapeError(
            f"source_uri must be a bare relative path, not {source_uri!r}"
        )
    if ".." in source_uri.split("/"):
        raise ArtifactPathEscapeError(
            f"source_uri must not contain a '..' traversal segment: {source_uri!r}"
        )
    return source_uri


def _reject_symlink_components(root_real: Path, source_uri: str) -> None:
    """Refuses a symlink anywhere between `root_real` and the leaf named
    by `source_uri`'s own path components -- checked against the
    *unresolved* path, so a symlink component is refused before
    `Path.resolve()` ever gets a chance to follow it."""
    current = root_real
    for part in Path(source_uri).parts:
        current = current / part
        if current.is_symlink():
            raise ArtifactSymlinkError(f"{current} is a symlink -- refused")


def _reject_symlinks_in_tree(path: Path) -> None:
    if not path.is_dir():
        return
    for dirpath, dirnames, filenames in os.walk(path, followlinks=False):
        parent = Path(dirpath)
        for name in (*dirnames, *filenames):
            if (parent / name).is_symlink():
                raise ArtifactSymlinkError(f"{parent / name} is a symlink -- refused")


def _hash_file_with_toctou_guard(path: Path) -> tuple[str, int, int]:
    before = path.stat()
    if not stat.S_ISREG(before.st_mode):
        raise UnsafeExecutableArtifactError(f"{path} is not a regular file")
    if before.st_mode & _EXECUTABLE_BITS:
        raise UnsafeExecutableArtifactError(f"{path} carries an executable permission bit")

    digest = hashlib.sha256()
    with path.open("rb") as fh:
        while chunk := fh.read(_READ_CHUNK_BYTES):
            digest.update(chunk)

    after = path.stat()
    if (
        before.st_size != after.st_size
        or before.st_mtime_ns != after.st_mtime_ns
        or before.st_ino != after.st_ino
        or before.st_dev != after.st_dev
    ):
        raise ArtifactChangedDuringReadError(f"{path} changed while it was being read -- refused")

    return digest.hexdigest(), after.st_size, stat.S_IMODE(after.st_mode)


def _discover_regular_files(candidate_real: Path) -> list[Path]:
    if candidate_real.is_file():
        return [candidate_real]
    if not candidate_real.is_dir():
        raise UnsafeExecutableArtifactError(
            f"{candidate_real} is neither a regular file nor a directory"
        )
    discovered: list[Path] = []
    for entry in candidate_real.rglob("*"):
        if entry.is_dir():
            continue
        if entry.is_file():
            discovered.append(entry)
        else:
            raise UnsafeExecutableArtifactError(f"{entry} is not a regular file or directory")
    return discovered


def resolve_local_artifact(approved_root: str | Path, source_uri: str) -> ArtifactManifest:
    """Resolve `source_uri` beneath one real, operator-approved local
    root and compute a deterministic file manifest.

    Raises `ApprovedRootError`/`ArtifactPathEscapeError`/
    `ArtifactSymlinkError`/`ArtifactChangedDuringReadError`/
    `UnsafeExecutableArtifactError` (all `LocalArtifactResolutionError`
    subclasses) rather than ever returning a manifest for bytes this
    function could not fully vouch for.
    """
    _reject_traversal_shape(source_uri)

    root = Path(approved_root)
    try:
        root_real = root.resolve(strict=True)
    except OSError as exc:
        raise ApprovedRootError(f"approved_root does not exist: {approved_root!r}") from exc
    if not root_real.is_dir():
        raise ApprovedRootError(f"approved_root is not a directory: {approved_root!r}")

    _reject_symlink_components(root_real, source_uri)

    candidate = root_real / source_uri
    try:
        candidate_real = candidate.resolve(strict=True)
    except OSError as exc:
        raise LocalArtifactResolutionError(
            f"source_uri does not resolve to a real path beneath approved_root: {source_uri!r}"
        ) from exc

    if candidate_real != root_real and root_real not in candidate_real.parents:
        raise ArtifactPathEscapeError(
            f"resolved path {candidate_real} escapes approved_root {root_real}"
        )

    _reject_symlinks_in_tree(candidate_real)

    file_paths = _discover_regular_files(candidate_real)
    manifest_base = candidate_real if candidate_real.is_dir() else candidate_real.parent

    resolved_files: list[ResolvedArtifactFile] = []
    total_size = 0
    for file_path in file_paths:
        sha256_hex, size_bytes, mode = _hash_file_with_toctou_guard(file_path)
        resolved_files.append(
            ResolvedArtifactFile(
                relative_path=file_path.relative_to(manifest_base).as_posix(),
                size_bytes=size_bytes,
                sha256=sha256_hex,
                mode=mode,
            )
        )
        total_size += size_bytes

    resolved_files.sort(key=lambda f: f.relative_path)

    manifest_digest = hashlib.sha256()
    for resolved_file in resolved_files:
        manifest_digest.update(
            f"{resolved_file.relative_path}\0{resolved_file.sha256}\0"
            f"{resolved_file.size_bytes}\0{resolved_file.mode:o}\n".encode()
        )

    return ArtifactManifest(
        approved_root=str(root_real),
        source_uri=source_uri,
        resolved_path=str(candidate_real),
        files=tuple(resolved_files),
        total_size_bytes=total_size,
        manifest_sha256=manifest_digest.hexdigest(),
        resolved_at=datetime.now(UTC),
    )
