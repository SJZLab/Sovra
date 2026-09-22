#!/usr/bin/env python3
"""FTF-0304 -- Local artifact resolver (no-DB unit/fault tests).

`packages/model_catalog/local_artifact_resolver.py` is pure filesystem
logic with no database, network or GPU dependency -- this repo's local
filesystem is always available, so this single suite *is* the "real
relevant integration" evidence the task packet's own acceptance clause 2
asks for (no separate `tests/integration/` suite exists for this task,
unlike every DB-backed task before it): every check below runs against
real directories/files/symlinks/permission bits created fresh under a
real `tempfile.TemporaryDirectory()`, never a mock filesystem.

This suite proves:

1. Acceptance clause 1 -- a real artifact (single file, and a nested
   directory tree) beneath a real approved root resolves to a
   deterministic file manifest: correct per-file sha256/size/mode,
   correct sorted relative paths, a `manifest_sha256` that is stable
   across repeated resolutions of identical bytes and changes when the
   bytes change.
2. negative_acceptance, all three named cases, each proven against a
   real filesystem object, not a mocked one:
   - "Symlink/path escape": a shape-level `..`/absolute-path escape: a
     top-level symlink escaping the approved root; a top-level symlink
     staying inside the approved root; a symlink nested inside a
     resolved directory artifact.
   - "changing file during read": a file whose bytes are mutated on disk
     between the pre-read and post-read `stat()` the resolver itself
     takes.
   - "unsafe executable artifact": a regular file carrying an executable
     permission bit; a FIFO (non-regular, non-directory member) inside
     the tree.
3. `ApprovedRootError` for a non-existent/non-directory approved root.
4. Fault injection (Law 1): the executable-bit guard is disabled in the
   real module source, the corresponding negative test is shown to
   genuinely fail as a result, the module is reverted byte-identical and
   the full suite re-confirmed clean.

Run directly: python3 tests/tasks/test_ftf_0304.py
"""

from __future__ import annotations

import hashlib
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from packages.model_catalog.local_artifact_resolver import (  # noqa: E402
    ApprovedRootError,
    ArtifactChangedDuringReadError,
    ArtifactPathEscapeError,
    ArtifactSymlinkError,
    UnsafeExecutableArtifactError,
    resolve_local_artifact,
)

RESOLVER_PATH = ROOT / "packages" / "model_catalog" / "local_artifact_resolver.py"

FAILURES: list[str] = []


def check(criterion_id: str, description: str, condition: bool) -> None:
    if not condition:
        FAILURES.append(f"FAIL [{criterion_id}] {description}")


def _expect_error(error_type: type[Exception], fn) -> bool:
    try:
        fn()
    except error_type:
        return True
    except Exception:
        return False
    return False


# -- 1. A single-file artifact resolves to a real, correct manifest --------


def test_single_file_artifact_resolves_deterministically() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "weights.bin").write_bytes(b"pretend-adapter-weights")

        manifest = resolve_local_artifact(root, "weights.bin")

        expected_sha256 = hashlib.sha256(b"pretend-adapter-weights").hexdigest()
        check(
            "FTF-0304",
            "single-file artifact resolves to exactly one file entry",
            len(manifest.files) == 1,
        )
        check(
            "FTF-0304",
            "single-file artifact's relative_path is its own filename",
            manifest.files[0].relative_path == "weights.bin",
        )
        check(
            "FTF-0304",
            "single-file artifact's sha256 matches a real independent hashlib computation",
            manifest.files[0].sha256 == expected_sha256,
        )
        check(
            "FTF-0304",
            "single-file artifact's size_bytes matches the real file size",
            manifest.files[0].size_bytes == len(b"pretend-adapter-weights"),
        )
        check(
            "FTF-0304",
            "total_size_bytes matches the sum of file sizes",
            manifest.total_size_bytes == len(b"pretend-adapter-weights"),
        )


# -- 2. A nested-directory artifact: sorted, deterministic, correct hashes -


def _write_nested_artifact(root: Path) -> Path:
    artifact = root / "candidate-adapter"
    (artifact / "nested").mkdir(parents=True)
    (artifact / "adapter_config.json").write_bytes(b'{"r": 16}')
    (artifact / "nested" / "adapter_model.safetensors").write_bytes(b"binary-weights-bytes")
    (artifact / "tokenizer.json").write_bytes(b"tokenizer-bytes")
    return artifact


def test_directory_artifact_manifest_is_correct_and_sorted() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _write_nested_artifact(root)

        manifest = resolve_local_artifact(root, "candidate-adapter")

        relative_paths = [f.relative_path for f in manifest.files]
        check(
            "FTF-0304",
            "directory artifact discovers every real file, including nested ones",
            sorted(relative_paths)
            == [
                "adapter_config.json",
                "nested/adapter_model.safetensors",
                "tokenizer.json",
            ],
        )
        check(
            "FTF-0304",
            "manifest.files is itself already sorted by relative_path",
            relative_paths == sorted(relative_paths),
        )
        by_path = {f.relative_path: f for f in manifest.files}
        check(
            "FTF-0304",
            "a nested file's sha256 matches a real independent hashlib computation",
            by_path["nested/adapter_model.safetensors"].sha256
            == hashlib.sha256(b"binary-weights-bytes").hexdigest(),
        )
        check(
            "FTF-0304",
            "total_size_bytes matches the real sum of all discovered files",
            manifest.total_size_bytes
            == len(b'{"r": 16}') + len(b"binary-weights-bytes") + len(b"tokenizer-bytes"),
        )


def test_manifest_sha256_is_stable_and_content_sensitive() -> None:
    with tempfile.TemporaryDirectory() as tmp_a, tempfile.TemporaryDirectory() as tmp_b:
        root_a, root_b = Path(tmp_a), Path(tmp_b)
        _write_nested_artifact(root_a)
        _write_nested_artifact(root_b)

        manifest_a1 = resolve_local_artifact(root_a, "candidate-adapter")
        manifest_a2 = resolve_local_artifact(root_a, "candidate-adapter")
        manifest_b = resolve_local_artifact(root_b, "candidate-adapter")

        check(
            "FTF-0304",
            "manifest_sha256 is stable across repeated resolutions of the same bytes",
            manifest_a1.manifest_sha256 == manifest_a2.manifest_sha256,
        )
        check(
            "FTF-0304",
            "manifest_sha256 is identical for two independent trees with identical content",
            manifest_a1.manifest_sha256 == manifest_b.manifest_sha256,
        )

        (root_a / "candidate-adapter" / "tokenizer.json").write_bytes(b"different-bytes-now")
        manifest_a3 = resolve_local_artifact(root_a, "candidate-adapter")
        check(
            "FTF-0304",
            "manifest_sha256 changes when a real file's bytes change",
            manifest_a3.manifest_sha256 != manifest_a1.manifest_sha256,
        )


# -- 3. negative_acceptance: "Symlink/path escape" --------------------------


def test_shape_level_path_escape_is_rejected() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "real.bin").write_bytes(b"x")

        check(
            "FTF-0304",
            "an absolute source_uri is rejected",
            _expect_error(
                ArtifactPathEscapeError, lambda: resolve_local_artifact(root, "/etc/passwd")
            ),
        )
        check(
            "FTF-0304",
            "a '..' traversal segment is rejected",
            _expect_error(
                ArtifactPathEscapeError, lambda: resolve_local_artifact(root, "../outside.bin")
            ),
        )
        check(
            "FTF-0304",
            "a home-relative source_uri is rejected",
            _expect_error(ArtifactPathEscapeError, lambda: resolve_local_artifact(root, "~/x.bin")),
        )


def test_symlink_escaping_root_is_rejected() -> None:
    with tempfile.TemporaryDirectory() as tmp_root, tempfile.TemporaryDirectory() as tmp_outside:
        root = Path(tmp_root)
        outside = Path(tmp_outside) / "secret.bin"
        outside.write_bytes(b"outside-bytes")

        escaping_link = root / "escape.bin"
        escaping_link.symlink_to(outside)

        check(
            "FTF-0304",
            "a top-level symlink escaping the approved root is rejected",
            _expect_error(ArtifactSymlinkError, lambda: resolve_local_artifact(root, "escape.bin")),
        )


def test_symlink_inside_root_is_still_rejected() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        real_target = root / "real.bin"
        real_target.write_bytes(b"real-bytes")
        inside_link = root / "alias.bin"
        inside_link.symlink_to(real_target)

        check(
            "FTF-0304",
            "a symlink that stays inside the approved root is still rejected "
            "(it can still point a 'file' at bytes the artifact never shipped)",
            _expect_error(ArtifactSymlinkError, lambda: resolve_local_artifact(root, "alias.bin")),
        )


def test_symlink_nested_inside_directory_artifact_is_rejected() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        artifact = _write_nested_artifact(root)
        (artifact / "nested" / "sneaky_link.bin").symlink_to(artifact / "tokenizer.json")

        check(
            "FTF-0304",
            "a symlink nested inside a resolved directory artifact is rejected",
            _expect_error(
                ArtifactSymlinkError, lambda: resolve_local_artifact(root, "candidate-adapter")
            ),
        )


# -- 4. negative_acceptance: "changing file during read" --------------------


def test_file_changed_during_read_is_rejected() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        artifact = root / "artifact"
        artifact.mkdir()
        target = artifact / "weights.bin"
        target.write_bytes(b"original-bytes")

        real_open = Path.open

        def mutate_then_open(self, *args, **kwargs):
            if self == target:
                # Bypass Path.write_bytes (which itself calls self.open() and
                # would recurse into this very mock) -- write through the
                # real, unpatched open directly.
                with real_open(target, "wb") as fh:
                    fh.write(b"mutated-bytes-of-a-different-length")
            return real_open(self, *args, **kwargs)

        with mock.patch.object(Path, "open", mutate_then_open):
            caught = _expect_error(
                ArtifactChangedDuringReadError, lambda: resolve_local_artifact(root, "artifact")
            )

        check(
            "FTF-0304",
            "a file mutated on disk between the pre-read and post-read stat is rejected",
            caught,
        )


# -- 5. negative_acceptance: "unsafe executable artifact" -------------------


def test_executable_file_is_rejected() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        artifact = root / "artifact"
        artifact.mkdir()
        script = artifact / "run.sh"
        script.write_bytes(b"#!/bin/sh\necho hi\n")
        script.chmod(0o755)

        check(
            "FTF-0304",
            "a regular file carrying an executable permission bit is rejected",
            _expect_error(
                UnsafeExecutableArtifactError, lambda: resolve_local_artifact(root, "artifact")
            ),
        )


def test_non_regular_file_is_rejected() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        artifact = root / "artifact"
        artifact.mkdir()
        (artifact / "weights.bin").write_bytes(b"real-bytes")
        os.mkfifo(artifact / "pipe")

        check(
            "FTF-0304",
            "a non-regular file (FIFO) inside the artifact tree is rejected",
            _expect_error(
                UnsafeExecutableArtifactError, lambda: resolve_local_artifact(root, "artifact")
            ),
        )


# -- 6. ApprovedRootError ----------------------------------------------------


def test_approved_root_must_be_a_real_directory() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        missing_root = root / "does-not-exist"

        check(
            "FTF-0304",
            "a non-existent approved_root raises ApprovedRootError",
            _expect_error(ApprovedRootError, lambda: resolve_local_artifact(missing_root, "x.bin")),
        )

        a_file, not_a_dir = root / "im-a-file", None
        a_file.write_bytes(b"x")
        check(
            "FTF-0304",
            "an approved_root that is a file, not a directory, raises ApprovedRootError",
            _expect_error(ApprovedRootError, lambda: resolve_local_artifact(a_file, "x.bin")),
        )
        del not_a_dir


# -- 7. Fault injection (Law 1) ----------------------------------------------


_FAULT_INJECTION_PROBE = """
import sys
sys.path.insert(0, {root!r})
import tempfile
from pathlib import Path
from packages.model_catalog.local_artifact_resolver import (
    UnsafeExecutableArtifactError,
    resolve_local_artifact,
)

with tempfile.TemporaryDirectory() as tmp:
    root = Path(tmp)
    artifact = root / "artifact"
    artifact.mkdir()
    script = artifact / "run.sh"
    script.write_bytes(b"#!/bin/sh\\necho hi\\n")
    script.chmod(0o755)
    try:
        resolve_local_artifact(root, "artifact")
        print("NOT_REJECTED")
    except UnsafeExecutableArtifactError:
        print("REJECTED")
"""


def test_fault_injection_executable_bit_guard_is_real() -> None:
    """Disables the executable-bit guard for real, proves the negative
    test above genuinely depends on it (fails without it), then restores
    the module byte-identical."""
    original = RESOLVER_PATH.read_bytes()
    try:
        src = original.decode("utf-8")
        needle = "if before.st_mode & _EXECUTABLE_BITS:"
        assert needle in src, "expected executable-bit guard not found -- module shape changed"
        mutated = src.replace(needle, "if False and before.st_mode & _EXECUTABLE_BITS:")
        assert mutated != src, "fault injection did not change the file"
        RESOLVER_PATH.write_text(mutated, encoding="utf-8")

        probe = _FAULT_INJECTION_PROBE.format(root=str(ROOT))
        result = subprocess.run(
            [sys.executable, "-c", probe], capture_output=True, text=True, timeout=30
        )
        check(
            "FTF-0304",
            "fault injection: disabling the executable-bit guard is genuinely caught "
            f"by this suite -- the probe no longer rejects it (stdout={result.stdout!r} "
            f"stderr={result.stderr!r})",
            result.returncode == 0 and "NOT_REJECTED" in result.stdout,
        )
    finally:
        RESOLVER_PATH.write_bytes(original)
        restored = RESOLVER_PATH.read_bytes()
        check(
            "FTF-0304",
            "the module file is restored byte-identical after fault injection",
            restored == original,
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
