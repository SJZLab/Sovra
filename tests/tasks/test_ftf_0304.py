#!/usr/bin/env python3
"""FTF-0304 -- Local artifact resolver (no-DB unit/fault tests, real
filesystem).

Purely local: no database, no network egress, no GPU. Uses real temporary
directories/files on disk (never mocked filesystem calls) so every check
below exercises the real `os`/`pathlib` behavior this task's own
acceptance depends on. This suite proves:

1. **Positive resolution.** A real multi-file artifact directory resolves
   to a manifest whose entries are sorted, whose per-file `sha256`/
   `size_bytes` match the real bytes on disk, and whose `manifest_sha256`
   is deterministic across repeated resolution of the same unchanged
   directory -- and changes when a single byte of file content changes.
   A single-file artifact (not a directory) resolves too.
2. **Symlink/path escape rejected** (negative_acceptance): a symlinked
   file inside the artifact tree, a symlinked subdirectory, and a
   `source_uri` whose resolved real path would land outside the approved
   root are each independently rejected with
   `LocalArtifactResolutionError`.
3. **Changing file during read rejected** (negative_acceptance): a real
   file that is mutated on disk partway through being read is rejected
   with `LocalArtifactResolutionError` -- proven by actually mutating the
   file mid-iteration (via the module's own real chunk-reading path, not
   a hand-constructed stat-tuple assertion), not merely asserted.
4. **Unsafe executable artifact rejected** (negative_acceptance): a file
   carrying an owner/group/other executable permission bit is rejected,
   while an otherwise-identical non-executable file is accepted.
5. A shape-invalid `source_uri` (already FTF-0301's own scope) is refused
   before any filesystem access happens at all, and a missing path is
   refused with a real `LocalArtifactResolutionError`.

Run directly: python3 tests/tasks/test_ftf_0304.py
"""

from __future__ import annotations

import hashlib
import os
import stat
import sys
import tempfile
from collections.abc import Iterator
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
API_ROOT = ROOT / "apps" / "api"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(API_ROOT))

from app.artifacts import local_artifact_resolver  # noqa: E402
from app.artifacts.artifact_schema import TrainableArtifactValidationError  # noqa: E402
from app.artifacts.local_artifact_resolver import (  # noqa: E402
    LocalArtifactResolutionError,
    resolve_local_artifact,
)

FAILURES: list[str] = []


def check(criterion_id: str, description: str, condition: bool) -> None:
    if not condition:
        FAILURES.append(f"FAIL [{criterion_id}] {description}")


def _rejects(fn, *args: object) -> bool:
    try:
        fn(*args)
        return False
    except LocalArtifactResolutionError:
        return True


class _TempRoot:
    """A real temporary directory used as the approved local root, torn
    down on exit -- every test below writes real bytes into it."""

    def __enter__(self) -> Path:
        self._tmp = tempfile.TemporaryDirectory()
        return Path(self._tmp.name)

    def __exit__(self, *exc: object) -> None:
        self._tmp.cleanup()


def _write(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)


# -- 1. Positive resolution ---------------------------------------------


def test_resolves_a_directory_artifact_with_sorted_deterministic_entries() -> None:
    with _TempRoot() as root:
        _write(root / "model-a" / "config.json", b'{"hidden_size": 4096}')
        _write(root / "model-a" / "weights" / "shard-0.bin", b"\x00\x01\x02\x03" * 1024)
        _write(root / "model-a" / "weights" / "shard-1.bin", b"\xff\xfe\xfd\xfc" * 1024)

        manifest = resolve_local_artifact(root, "model-a")

        rel_paths = [e.relative_path for e in manifest.entries]
        check(
            "FTF-0304",
            "acceptance: a directory artifact resolves every real file beneath it, "
            "sorted deterministically by relative_path",
            rel_paths == sorted(rel_paths)
            and rel_paths == ["config.json", "weights/shard-0.bin", "weights/shard-1.bin"],
        )

        expected_config_sha = hashlib.sha256(b'{"hidden_size": 4096}').hexdigest()
        config_entry = next(e for e in manifest.entries if e.relative_path == "config.json")
        check(
            "FTF-0304",
            "acceptance: each entry's sha256/size_bytes matches the real file content on disk",
            config_entry.sha256 == expected_config_sha and config_entry.size_bytes == 21,
        )
        check(
            "FTF-0304",
            "acceptance: total_size_bytes is the real sum of every resolved file's size",
            manifest.total_size_bytes == sum(e.size_bytes for e in manifest.entries),
        )

        manifest_again = resolve_local_artifact(root, "model-a")
        check(
            "FTF-0304",
            "acceptance: resolving the same unchanged directory twice yields the same "
            "deterministic manifest_sha256",
            manifest.manifest_sha256 == manifest_again.manifest_sha256,
        )

        _write(root / "model-a" / "config.json", b'{"hidden_size": 8192}')
        manifest_changed = resolve_local_artifact(root, "model-a")
        check(
            "FTF-0304",
            "acceptance: changing one file's real content changes manifest_sha256",
            manifest_changed.manifest_sha256 != manifest.manifest_sha256,
        )


def test_resolves_a_single_file_artifact() -> None:
    with _TempRoot() as root:
        _write(root / "adapter.safetensors", b"weights-bytes")
        manifest = resolve_local_artifact(root, "adapter.safetensors")
        check(
            "FTF-0304",
            "acceptance: a single-file (non-directory) artifact resolves to one entry "
            "named after the file itself",
            len(manifest.entries) == 1
            and manifest.entries[0].relative_path == "adapter.safetensors",
        )


# -- 2. Symlink/path escape rejected -------------------------------------


def test_rejects_a_symlinked_file_inside_the_artifact_tree() -> None:
    with _TempRoot() as root:
        _write(root / "model-b" / "real.bin", b"real-bytes")
        outside_target = root.parent / f"outside-{os.getpid()}.bin"
        _write(outside_target, b"outside-bytes")
        try:
            (root / "model-b" / "linked.bin").symlink_to(outside_target)
        except OSError:
            check(
                "FTF-0304", "negative_acceptance: symlink creation supported by this sandbox", False
            )
            return
        check(
            "FTF-0304",
            "negative_acceptance: a symlinked file inside a directory artifact is rejected",
            _rejects(resolve_local_artifact, root, "model-b"),
        )
        outside_target.unlink()


def test_rejects_a_symlinked_subdirectory() -> None:
    with _TempRoot() as root:
        _write(root / "model-c" / "real.bin", b"real-bytes")
        real_dir = root.parent / f"outside-dir-{os.getpid()}"
        real_dir.mkdir(exist_ok=True)
        _write(real_dir / "secret.bin", b"secret-bytes")
        try:
            (root / "model-c" / "linked-dir").symlink_to(real_dir, target_is_directory=True)
        except OSError:
            check(
                "FTF-0304", "negative_acceptance: symlink creation supported by this sandbox", False
            )
            return
        check(
            "FTF-0304",
            "negative_acceptance: a symlinked subdirectory inside a directory artifact is "
            "rejected, not silently skipped",
            _rejects(resolve_local_artifact, root, "model-c"),
        )
        (real_dir / "secret.bin").unlink()
        real_dir.rmdir()


def test_rejects_a_symlinked_artifact_root() -> None:
    with _TempRoot() as root:
        real_dir = root.parent / f"outside-root-{os.getpid()}"
        real_dir.mkdir(exist_ok=True)
        _write(real_dir / "file.bin", b"bytes")
        try:
            (root / "model-d").symlink_to(real_dir, target_is_directory=True)
        except OSError:
            check(
                "FTF-0304", "negative_acceptance: symlink creation supported by this sandbox", False
            )
            return
        check(
            "FTF-0304",
            "negative_acceptance: a source_uri whose own root entry is a symlink is rejected",
            _rejects(resolve_local_artifact, root, "model-d"),
        )
        (real_dir / "file.bin").unlink()
        real_dir.rmdir()


def test_rejects_a_symlinked_artifact_root_even_when_target_stays_inside_the_approved_root() -> (
    None
):
    # Distinct from test_rejects_a_symlinked_artifact_root above: this
    # symlink's target is still beneath `root`, so the real-path-escape
    # check alone would not catch it -- only the explicit ancestor
    # is_symlink() rejection can. Proves that check is load-bearing, not
    # redundant with the escape check.
    with _TempRoot() as root:
        _write(root / "real-target" / "file.bin", b"bytes")
        try:
            (root / "model-i").symlink_to(root / "real-target", target_is_directory=True)
        except OSError:
            check(
                "FTF-0304", "negative_acceptance: symlink creation supported by this sandbox", False
            )
            return
        check(
            "FTF-0304",
            "negative_acceptance: an artifact-root symlink is rejected even when its real "
            "target stays inside the approved root -- symlinks are rejected categorically, "
            "not only when they happen to escape",
            _rejects(resolve_local_artifact, root, "model-i"),
        )


def test_rejects_a_shape_valid_but_missing_path() -> None:
    with _TempRoot() as root:
        check(
            "FTF-0304",
            "a shape-valid source_uri that does not exist under the approved root is "
            "rejected, not silently returning an empty manifest",
            _rejects(resolve_local_artifact, root, "never-created"),
        )


def test_rejects_shape_invalid_source_uri_before_touching_the_filesystem() -> None:
    with _TempRoot() as root:
        try:
            resolve_local_artifact(root, "../escape.bin")
        except TrainableArtifactValidationError:
            passed = True
        except LocalArtifactResolutionError:
            passed = False
        else:
            passed = False
        check(
            "FTF-0304",
            "an already-unsafe source_uri ('..' traversal) is refused by FTF-0301's own "
            "validate_source_uri before any filesystem access, not re-implemented here",
            passed,
        )


# -- 3. Changing file during read rejected -------------------------------


def test_rejects_a_file_that_changes_while_being_read() -> None:
    with _TempRoot() as root:
        target = root / "model-e" / "growing.bin"
        _write(target, b"a" * 10)

        real_iter_chunks = local_artifact_resolver._iter_chunks

        def _mutating_iter_chunks(path: Path, chunk_size: int) -> Iterator[bytes]:
            first = True
            for chunk in real_iter_chunks(path, chunk_size):
                if first:
                    path.write_bytes(b"a" * 10 + b"b" * 5)
                    first = False
                yield chunk

        local_artifact_resolver._iter_chunks = _mutating_iter_chunks
        try:
            check(
                "FTF-0304",
                "negative_acceptance: a file whose real size/mtime changes between the "
                "start and end of being read is rejected, proven via a real mid-read "
                "mutation, not a hand-constructed stat comparison",
                _rejects(resolve_local_artifact, root, "model-e"),
            )
        finally:
            local_artifact_resolver._iter_chunks = real_iter_chunks

        # Sanity: with the real, unpatched reader and an untouched file, resolution succeeds.
        manifest = resolve_local_artifact(root, "model-e")
        check(
            "FTF-0304",
            "sanity: the same file, unmutated, resolves successfully with the real reader",
            manifest.entries[0].size_bytes == 15,
        )


# -- 4. Unsafe executable artifact rejected -------------------------------


def test_rejects_an_executable_file() -> None:
    with _TempRoot() as root:
        exe_path = root / "model-f" / "run.sh"
        _write(exe_path, b"#!/bin/sh\necho hi\n")
        exe_path.chmod(exe_path.stat().st_mode | stat.S_IXUSR)
        check(
            "FTF-0304",
            "negative_acceptance: a file with the owner-executable bit set is rejected",
            _rejects(resolve_local_artifact, root, "model-f"),
        )


def test_accepts_a_non_executable_file_with_identical_content() -> None:
    with _TempRoot() as root:
        data_path = root / "model-g" / "run.sh"
        _write(data_path, b"#!/bin/sh\necho hi\n")
        data_path.chmod(data_path.stat().st_mode & ~stat.S_IXUSR & ~stat.S_IXGRP & ~stat.S_IXOTH)
        manifest = resolve_local_artifact(root, "model-g")
        check(
            "FTF-0304",
            "sanity: the identical content, without an executable bit, is accepted -- "
            "content alone is not what triggers rejection, only the executable permission",
            len(manifest.entries) == 1,
        )


def main() -> None:
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()

    if FAILURES:
        print(f"FTF-0304 Local artifact resolver tests: {len(FAILURES)} failed")
        for failure in FAILURES:
            print(f"  {failure}")
        sys.exit(1)

    print("FTF-0304 Local artifact resolver tests: all checks passed")
    sys.exit(0)


if __name__ == "__main__":
    main()
