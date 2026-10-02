#!/usr/bin/env python3
"""FTF-1002 -- Artifact lineage manifest (no-DB unit/fault tests).

Runnable without any database and without any external network egress:
`build_artifact_lineage_manifest` is a pure, synchronous, no-I/O function
(this task's own `database_changes` clause -- "No independent schema
change. Consume frozen contract.") that composes an already-fetched
`ModelVersion` (FTF-1001, CERTIFIED) and an optional already-fetched
`TrainableArtifact` (FTF-0301, CERTIFIED, whose own `ready` status already
requires FTF-0306's own verified weights digest). Every check below builds
its own inputs directly as plain ORM instances -- no HTTP server, no
secret backend, no TLS fixture and no Postgres connection is needed, the
same `tests/tasks/test_ftf_0307.py` precedent already established for a
pure composer task with no live route or database of its own.

This task's own acceptance clause ("Artifact lineage binds base/tokenizer/
training/data/license/runtime/method and records unknown external
ancestry") is covered by `test_full_weight_import_binds_base_tokenizer_
license_runtime_method`, `test_adapter_import_reports_method_adapter`,
`test_endpoint_import_reports_every_dimension_unknown` and
`test_runtime_is_unknown_when_no_packages_are_declared`. This task's own
negative_acceptance ("Unknown parent cannot be fabricated; adapter-base
mismatch invalidates load/qualification") is covered by
`test_weight_backed_import_with_no_artifact_is_refused`,
`test_artifact_not_matching_trainable_artifact_id_is_refused`,
`test_endpoint_import_with_a_real_artifact_is_refused`,
`test_resolved_base_artifact_only_meaningful_for_adapter`,
`test_adapter_base_mismatch_is_refused`,
`test_unresolved_base_artifact_cannot_validate_an_adapter` and
`test_lineage_fact_cannot_carry_a_value_while_unknown`, plus a real fault
injection (Law 1) proving the base-revision-mismatch check genuinely fails
when its real guard is removed.

Run directly: python3 tests/tasks/test_ftf_1002.py
"""

from __future__ import annotations

import subprocess
import sys
import uuid
from dataclasses import FrozenInstanceError
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
API_ROOT = ROOT / "apps" / "api"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(API_ROOT))

from app.artifacts.artifact_lineage_manifest import (  # noqa: E402
    AdapterBaseMismatchError,
    ArtifactLineageError,
    LineageFact,
    build_artifact_lineage_manifest,
)
from app.artifacts.artifact_schema import TrainableArtifact  # noqa: E402
from app.model_versions.model_version_schema import ModelVersion  # noqa: E402

FAILURES: list[str] = []
_MODULE_PATH = API_ROOT / "app" / "artifacts" / "artifact_lineage_manifest.py"


def check(criterion_id: str, description: str, condition: bool) -> None:
    if not condition:
        FAILURES.append(f"FAIL [{criterion_id}] {description}")


_ORG_ID = uuid.uuid4()
_PROJECT_ID = uuid.uuid4()
_REAL_DIGEST = "sha256:" + "a" * 64
_BASE_DIGEST = "sha256:" + "b" * 64


def _artifact(
    *,
    artifact_id: uuid.UUID | None = None,
    status: str = "ready",
    base_model_revision: str = "a1b2c3d4",
    weights_digest: str | None = _REAL_DIGEST,
    required_python_packages: list[str] | None = None,
) -> TrainableArtifact:
    now = datetime.now(UTC)
    artifact = TrainableArtifact(
        organization_id=_ORG_ID,
        display_name=f"artifact-{uuid.uuid4().hex[:8]}",
        source_type="local_filesystem",
        source_uri="model-a",
        model_family="tiny",
        base_model_revision=base_model_revision,
        weights_digest=weights_digest,
        license_id="apache-2.0",
        trust_remote_code_allowed=False,
        status=status,
        required_python_packages_json=required_python_packages or [],
    )
    artifact.id = artifact_id if artifact_id is not None else uuid.uuid4()
    artifact.created_at = now
    artifact.updated_at = now
    if status == "ready":
        artifact.license_accepted_by = "reviewer-1"
        artifact.license_accepted_at = now
    return artifact


def _model_version(
    *,
    import_kind: str,
    trainable_artifact_id: uuid.UUID | None = None,
    weights_digest: str | None = None,
    base_model_revision: str | None = None,
    endpoint_reference: str | None = None,
) -> ModelVersion:
    kwargs: dict[str, object] = {
        "organization_id": _ORG_ID,
        "project_id": _PROJECT_ID,
        "version_number": 1,
        "import_kind": import_kind,
        "created_by": "user-1",
        "created_at": datetime.now(UTC),
    }
    if import_kind == "endpoint":
        kwargs["endpoint_reference"] = endpoint_reference or "https://models.example.com/v1/prod"
    else:
        kwargs["trainable_artifact_id"] = trainable_artifact_id
        kwargs["weights_digest"] = weights_digest
        kwargs["base_model_revision"] = base_model_revision
    model_version = ModelVersion(**kwargs)
    model_version.id = uuid.uuid4()
    return model_version


# -- Positive: acceptance clause -------------------------------------------


def test_full_weight_import_binds_base_tokenizer_license_runtime_method() -> None:
    artifact = _artifact(required_python_packages=["transformers==4.44.0", "torch==2.4.0"])
    model_version = _model_version(
        import_kind="full_weight",
        trainable_artifact_id=artifact.id,
        weights_digest=artifact.weights_digest,
        base_model_revision=artifact.base_model_revision,
    )
    manifest = build_artifact_lineage_manifest(model_version, artifact)

    check(
        "FTF-1002",
        "base is known and carries the real resolved weights_digest",
        manifest.base.known and manifest.base.value == _REAL_DIGEST,
    )
    check(
        "FTF-1002",
        "tokenizer is known and bound to the same digest as base",
        manifest.tokenizer.known and manifest.tokenizer.value == manifest.base.value,
    )
    check(
        "FTF-1002",
        "license is known and carries the real license_id",
        manifest.license.known and manifest.license.value == "apache-2.0",
    )
    check(
        "FTF-1002",
        "runtime is known from the real declared required_python_packages",
        manifest.runtime.known and manifest.runtime.value == "torch==2.4.0,transformers==4.44.0",
    )
    check(
        "FTF-1002",
        "method is known and reports the real import_kind",
        manifest.method.known and manifest.method.value == "full_weight",
    )
    check(
        "FTF-1002",
        "training is unknown external ancestry, never fabricated",
        not manifest.training.known and manifest.training.value is None,
    )
    check(
        "FTF-1002",
        "data is unknown external ancestry, never fabricated",
        not manifest.data.known and manifest.data.value is None,
    )


def test_adapter_import_reports_method_adapter() -> None:
    artifact = _artifact(base_model_revision="base-rev-1")
    model_version = _model_version(
        import_kind="adapter",
        trainable_artifact_id=artifact.id,
        weights_digest=artifact.weights_digest,
        base_model_revision=artifact.base_model_revision,
    )
    base_artifact = _artifact(status="ready", base_model_revision="base-rev-1")
    manifest = build_artifact_lineage_manifest(
        model_version, artifact, resolved_base_artifact=base_artifact
    )
    check(
        "FTF-1002",
        "method reports 'adapter' for an adapter import",
        manifest.method.known and manifest.method.value == "adapter",
    )
    check("FTF-1002", "a matching resolved_base_artifact does not raise", manifest.base.known)


def test_endpoint_import_reports_every_dimension_unknown() -> None:
    model_version = _model_version(import_kind="endpoint")
    manifest = build_artifact_lineage_manifest(model_version, None)
    for name in ("base", "tokenizer", "training", "data", "license", "runtime", "method"):
        fact: LineageFact = getattr(manifest, name)
        check(
            "FTF-1002",
            f"endpoint import reports {name} as unknown external ancestry, never fabricated",
            not fact.known and fact.value is None,
        )


def test_runtime_is_unknown_when_no_packages_are_declared() -> None:
    artifact = _artifact(required_python_packages=[])
    model_version = _model_version(
        import_kind="full_weight",
        trainable_artifact_id=artifact.id,
        weights_digest=artifact.weights_digest,
        base_model_revision=artifact.base_model_revision,
    )
    manifest = build_artifact_lineage_manifest(model_version, artifact)
    check(
        "FTF-1002",
        "runtime is honestly reported unknown, not a fabricated empty-but-known value",
        not manifest.runtime.known and manifest.runtime.value is None,
    )


# -- Negative: unknown parent cannot be fabricated -------------------------


def test_weight_backed_import_with_no_artifact_is_refused() -> None:
    model_version = _model_version(
        import_kind="full_weight",
        trainable_artifact_id=uuid.uuid4(),
        weights_digest=_REAL_DIGEST,
        base_model_revision="a1b2c3d4",
    )
    try:
        build_artifact_lineage_manifest(model_version, None)
        check("FTF-1002", "a weight-backed import with no artifact is refused", False)
    except ArtifactLineageError:
        check("FTF-1002", "a weight-backed import with no artifact is refused", True)


def test_artifact_not_matching_trainable_artifact_id_is_refused() -> None:
    real_artifact = _artifact()
    wrong_artifact = _artifact()
    model_version = _model_version(
        import_kind="full_weight",
        trainable_artifact_id=real_artifact.id,
        weights_digest=real_artifact.weights_digest,
        base_model_revision=real_artifact.base_model_revision,
    )
    try:
        build_artifact_lineage_manifest(model_version, wrong_artifact)
        check(
            "FTF-1002",
            "a caller cannot fabricate a parent other than the one actually linked",
            False,
        )
    except ArtifactLineageError:
        check(
            "FTF-1002",
            "a caller cannot fabricate a parent other than the one actually linked",
            True,
        )


def test_endpoint_import_with_a_real_artifact_is_refused() -> None:
    model_version = _model_version(import_kind="endpoint")
    artifact = _artifact()
    try:
        build_artifact_lineage_manifest(model_version, artifact)
        check("FTF-1002", "an endpoint import with a supplied artifact is refused", False)
    except ArtifactLineageError:
        check("FTF-1002", "an endpoint import with a supplied artifact is refused", True)


def test_resolved_base_artifact_only_meaningful_for_adapter() -> None:
    artifact = _artifact()
    model_version = _model_version(
        import_kind="full_weight",
        trainable_artifact_id=artifact.id,
        weights_digest=artifact.weights_digest,
        base_model_revision=artifact.base_model_revision,
    )
    base_artifact = _artifact(status="ready")
    try:
        build_artifact_lineage_manifest(
            model_version, artifact, resolved_base_artifact=base_artifact
        )
        check(
            "FTF-1002",
            "resolved_base_artifact on a full_weight import is refused, not silently ignored",
            False,
        )
    except ArtifactLineageError:
        check(
            "FTF-1002",
            "resolved_base_artifact on a full_weight import is refused, not silently ignored",
            True,
        )


# -- Negative: adapter-base mismatch invalidates load/qualification -------


def test_adapter_base_mismatch_is_refused() -> None:
    artifact = _artifact(base_model_revision="declared-base-rev")
    model_version = _model_version(
        import_kind="adapter",
        trainable_artifact_id=artifact.id,
        weights_digest=artifact.weights_digest,
        base_model_revision=artifact.base_model_revision,
    )
    mismatched_base = _artifact(status="ready", base_model_revision="a-different-base-rev")
    try:
        build_artifact_lineage_manifest(
            model_version, artifact, resolved_base_artifact=mismatched_base
        )
        check("FTF-1002", "a real adapter/base revision mismatch is refused", False)
    except AdapterBaseMismatchError:
        check("FTF-1002", "a real adapter/base revision mismatch is refused", True)


def test_unresolved_base_artifact_cannot_validate_an_adapter() -> None:
    artifact = _artifact(base_model_revision="declared-base-rev")
    model_version = _model_version(
        import_kind="adapter",
        trainable_artifact_id=artifact.id,
        weights_digest=artifact.weights_digest,
        base_model_revision=artifact.base_model_revision,
    )
    unresolved_base = _artifact(status="registered", base_model_revision="declared-base-rev")
    try:
        build_artifact_lineage_manifest(
            model_version, artifact, resolved_base_artifact=unresolved_base
        )
        check("FTF-1002", "a non-'ready' resolved_base_artifact cannot validate an adapter", False)
    except AdapterBaseMismatchError:
        check("FTF-1002", "a non-'ready' resolved_base_artifact cannot validate an adapter", True)


def test_lineage_fact_cannot_carry_a_value_while_unknown() -> None:
    try:
        LineageFact(known=False, value="sha256:" + "c" * 64, detail="should never construct")
        check("FTF-1002", "LineageFact refuses known=False with a non-None value", False)
    except ArtifactLineageError:
        check("FTF-1002", "LineageFact refuses known=False with a non-None value", True)


def test_manifest_is_frozen() -> None:
    artifact = _artifact()
    model_version = _model_version(
        import_kind="full_weight",
        trainable_artifact_id=artifact.id,
        weights_digest=artifact.weights_digest,
        base_model_revision=artifact.base_model_revision,
    )
    manifest = build_artifact_lineage_manifest(model_version, artifact)
    try:
        manifest.import_kind = "adapter"  # type: ignore[misc]
        check("FTF-1002", "ArtifactLineageManifest is immutable", False)
    except FrozenInstanceError:
        check("FTF-1002", "ArtifactLineageManifest is immutable", True)


# -- Real fault injection (Law 1) -------------------------------------------


def test_fault_injection_disabling_base_revision_compare_genuinely_fails() -> None:
    """Disable the real `base_model_revision` comparison that backs
    `AdapterBaseMismatchError` and prove the mismatch case this task's own
    negative_acceptance requires genuinely stops failing -- then restore
    the module byte-identical. Proves the guard is load-bearing, not
    merely present."""
    original = _MODULE_PATH.read_bytes()
    text = original.decode("utf-8")
    needle = "if resolved_base_artifact.base_model_revision != artifact.base_model_revision:"
    check("FTF-1002", "fault injection target line is present in the real module", needle in text)
    mutated = text.replace(needle, "if False:")
    check("FTF-1002", "fault injection actually changed the module source", mutated != text)

    try:
        _MODULE_PATH.write_text(mutated, encoding="utf-8")
        probe_script = f"""
import sys
sys.path.insert(0, {str(ROOT)!r})
sys.path.insert(0, {str(API_ROOT)!r})
import uuid
from datetime import datetime, UTC
from app.artifacts.artifact_lineage_manifest import build_artifact_lineage_manifest
from app.artifacts.artifact_schema import TrainableArtifact
from app.model_versions.model_version_schema import ModelVersion

org_id = uuid.uuid4()
now = datetime.now(UTC)

def make_artifact(revision, status="ready"):
    a = TrainableArtifact(
        organization_id=org_id, display_name="a-" + uuid.uuid4().hex[:8],
        source_type="local_filesystem", source_uri="model-a", model_family="tiny",
        base_model_revision=revision, weights_digest="sha256:" + "a" * 64,
        license_id="apache-2.0", trust_remote_code_allowed=False, status=status,
    )
    a.id = uuid.uuid4()
    a.created_at = now
    a.updated_at = now
    if status == "ready":
        a.license_accepted_by = "reviewer-1"
        a.license_accepted_at = now
    return a

artifact = make_artifact("declared-base-rev")
mismatched_base = make_artifact("a-different-base-rev")
mv = ModelVersion(
    organization_id=org_id, project_id=uuid.uuid4(), version_number=1, import_kind="adapter",
    trainable_artifact_id=artifact.id, weights_digest=artifact.weights_digest,
    base_model_revision=artifact.base_model_revision, created_by="user-1", created_at=now,
)
mv.id = uuid.uuid4()
build_artifact_lineage_manifest(mv, artifact, resolved_base_artifact=mismatched_base)
print("NO_ERROR_RAISED")
"""
        completed = subprocess.run(
            [sys.executable, "-c", probe_script], capture_output=True, text=True, timeout=30
        )
        check(
            "FTF-1002",
            "fault injection: disabling the base-revision compare genuinely lets a mismatched "
            f"adapter/base pairing through uncaught (stdout={completed.stdout!r} "
            f"stderr={completed.stderr!r})",
            completed.returncode == 0 and "NO_ERROR_RAISED" in completed.stdout,
        )
    finally:
        _MODULE_PATH.write_bytes(original)
        restored = _MODULE_PATH.read_bytes()
        check(
            "FTF-1002",
            "the module file is restored byte-identical after fault injection",
            restored == original,
        )


def main() -> None:
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()

    if FAILURES:
        print(f"FTF-1002 artifact lineage manifest: {len(FAILURES)} failed")
        for failure in FAILURES:
            print(f"  {failure}")
        sys.exit(1)

    print("FTF-1002 artifact lineage manifest: all checks passed")
    sys.exit(0)


if __name__ == "__main__":
    main()
