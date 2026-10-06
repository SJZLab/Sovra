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
`test_lineage_fact_cannot_carry_a_value_while_unknown`.

2026-10-06 L2 round-2 review findings (`docs/certifications/queue.md`
"2026-10-04T11:00Z", PR #424) and their tests:
- a `registered`/`archived` main parent ->
  `test_registered_artifact_is_refused`, `test_archived_artifact_is_refused`;
- a cross-tenant main parent -> `test_cross_tenant_artifact_is_refused`;
- a main parent whose current digest/revision has drifted from the
  model_version's own recorded value ->
  `test_model_version_weights_digest_drift_is_refused`,
  `test_model_version_base_model_revision_drift_is_refused`;
- an adapter's base check being optional ->
  `test_adapter_without_resolved_base_artifact_is_refused`;
- a self-parent, by id or by digest ->
  `test_adapter_self_parent_by_id_is_refused`,
  `test_adapter_self_parent_by_digest_is_refused`;
- a cross-tenant resolved base -> `test_cross_tenant_resolved_base_artifact_is_refused`;
- no manifest digest / not tamper-evident ->
  `test_manifest_digest_is_present_and_verifiable`,
  `test_verify_artifact_lineage_manifest_detects_tampering`;
plus two real fault injections (Law 1): one proving the pre-existing
`base_model_revision` compare is load-bearing in isolation (now with
distinct digests so the new self-digest check cannot also catch it), and
one *additive* fault -- an early `return None` inserted right after the
id-match check -- proving every one of this round's new guards is
genuinely load-bearing together, not merely present.

Run directly: python3 tests/tasks/test_ftf_1002.py
"""

from __future__ import annotations

import os
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
    ArtifactLineageManifestTamperedError,
    LineageFact,
    build_artifact_lineage_manifest,
    verify_artifact_lineage_manifest,
)
from app.artifacts.artifact_schema import TrainableArtifact  # noqa: E402
from app.model_versions.model_version_schema import ModelVersion  # noqa: E402

FAILURES: list[str] = []
_MODULE_PATH = API_ROOT / "app" / "artifacts" / "artifact_lineage_manifest.py"


def check(criterion_id: str, description: str, condition: bool) -> None:
    if not condition:
        FAILURES.append(f"FAIL [{criterion_id}] {description}")


_ORG_ID = uuid.uuid4()
_OTHER_ORG_ID = uuid.uuid4()
_PROJECT_ID = uuid.uuid4()
_REAL_DIGEST = "sha256:" + "a" * 64
_BASE_DIGEST = "sha256:" + "b" * 64


def _artifact(
    *,
    artifact_id: uuid.UUID | None = None,
    organization_id: uuid.UUID | None = None,
    status: str = "ready",
    base_model_revision: str = "a1b2c3d4",
    weights_digest: str | None = _REAL_DIGEST,
    model_family: str = "tiny",
    required_python_packages: list[str] | None = None,
    license_id: str = "apache-2.0",
    license_accepted_by: str = "reviewer-1",
    license_accepted_at: datetime | None = None,
) -> TrainableArtifact:
    now = datetime.now(UTC)
    artifact = TrainableArtifact(
        organization_id=organization_id if organization_id is not None else _ORG_ID,
        display_name=f"artifact-{uuid.uuid4().hex[:8]}",
        source_type="local_filesystem",
        source_uri="model-a",
        model_family=model_family,
        base_model_revision=base_model_revision,
        weights_digest=weights_digest,
        license_id=license_id,
        trust_remote_code_allowed=False,
        status=status,
        required_python_packages_json=required_python_packages or [],
    )
    artifact.id = artifact_id if artifact_id is not None else uuid.uuid4()
    artifact.created_at = now
    artifact.updated_at = now
    if status == "ready":
        artifact.license_accepted_by = license_accepted_by
        artifact.license_accepted_at = (
            license_accepted_at if license_accepted_at is not None else now
        )
    return artifact


def _model_version(
    *,
    import_kind: str,
    organization_id: uuid.UUID | None = None,
    trainable_artifact_id: uuid.UUID | None = None,
    weights_digest: str | None = None,
    base_model_revision: str | None = None,
    endpoint_reference: str | None = None,
) -> ModelVersion:
    kwargs: dict[str, object] = {
        "organization_id": organization_id if organization_id is not None else _ORG_ID,
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


def _linked_full_weight_model_version(artifact: TrainableArtifact) -> ModelVersion:
    """A `full_weight` ModelVersion whose own recorded weights_digest/
    base_model_revision/organization_id genuinely match `artifact`'s --
    the real, non-drifted baseline every negative test below perturbs
    exactly one field away from."""
    return _model_version(
        import_kind="full_weight",
        organization_id=artifact.organization_id,
        trainable_artifact_id=artifact.id,
        weights_digest=artifact.weights_digest,
        base_model_revision=artifact.base_model_revision,
    )


# -- Positive: acceptance clause -------------------------------------------


def test_full_weight_import_binds_base_tokenizer_license_runtime_method() -> None:
    artifact = _artifact(required_python_packages=["transformers==4.44.0", "torch==2.4.0"])
    model_version = _linked_full_weight_model_version(artifact)
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
    base_artifact = _artifact(
        status="ready", base_model_revision="base-rev-1", weights_digest=_BASE_DIGEST
    )
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
    model_version = _linked_full_weight_model_version(artifact)
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
    model_version = _linked_full_weight_model_version(real_artifact)
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
    model_version = _linked_full_weight_model_version(artifact)
    base_artifact = _artifact(status="ready", weights_digest=_BASE_DIGEST)
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
    mismatched_base = _artifact(
        status="ready", base_model_revision="a-different-base-rev", weights_digest=_BASE_DIGEST
    )
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
    unresolved_base = _artifact(
        status="registered", base_model_revision="declared-base-rev", weights_digest=_BASE_DIGEST
    )
    try:
        build_artifact_lineage_manifest(
            model_version, artifact, resolved_base_artifact=unresolved_base
        )
        check("FTF-1002", "a non-'ready' resolved_base_artifact cannot validate an adapter", False)
    except AdapterBaseMismatchError:
        check("FTF-1002", "a non-'ready' resolved_base_artifact cannot validate an adapter", True)


# -- Negative: 2026-10-06 L2 round-2 findings ------------------------------


def test_registered_artifact_is_refused() -> None:
    artifact = _artifact(status="registered")
    model_version = _linked_full_weight_model_version(artifact)
    try:
        build_artifact_lineage_manifest(model_version, artifact)
        check("FTF-1002", "a 'registered' (still-mutable) parent is refused", False)
    except ArtifactLineageError:
        check("FTF-1002", "a 'registered' (still-mutable) parent is refused", True)


def test_archived_artifact_is_refused() -> None:
    artifact = _artifact(status="archived")
    model_version = _linked_full_weight_model_version(artifact)
    try:
        build_artifact_lineage_manifest(model_version, artifact)
        check("FTF-1002", "an 'archived' (closed) parent is refused", False)
    except ArtifactLineageError:
        check("FTF-1002", "an 'archived' (closed) parent is refused", True)


def test_cross_tenant_artifact_is_refused() -> None:
    artifact = _artifact(organization_id=_OTHER_ORG_ID)
    model_version = _model_version(
        import_kind="full_weight",
        organization_id=_ORG_ID,
        trainable_artifact_id=artifact.id,
        weights_digest=artifact.weights_digest,
        base_model_revision=artifact.base_model_revision,
    )
    try:
        build_artifact_lineage_manifest(model_version, artifact)
        check("FTF-1002", "a cross-tenant parent is refused", False)
    except ArtifactLineageError:
        check("FTF-1002", "a cross-tenant parent is refused", True)


def test_model_version_weights_digest_drift_is_refused() -> None:
    artifact = _artifact()
    model_version = _linked_full_weight_model_version(artifact)
    # Simulate the artifact's own current digest having drifted away from
    # what model_version's own immutable row already recorded at creation.
    artifact.weights_digest = "sha256:" + "f" * 64
    try:
        build_artifact_lineage_manifest(model_version, artifact)
        check("FTF-1002", "a weights_digest drifted from the model_version's own is refused", False)
    except ArtifactLineageError:
        check("FTF-1002", "a weights_digest drifted from the model_version's own is refused", True)


def test_model_version_base_model_revision_drift_is_refused() -> None:
    artifact = _artifact()
    model_version = _linked_full_weight_model_version(artifact)
    artifact.base_model_revision = "a-different-revision-now"
    try:
        build_artifact_lineage_manifest(model_version, artifact)
        check(
            "FTF-1002",
            "a base_model_revision drifted from the model_version's own is refused",
            False,
        )
    except ArtifactLineageError:
        check(
            "FTF-1002",
            "a base_model_revision drifted from the model_version's own is refused",
            True,
        )


def test_adapter_without_resolved_base_artifact_is_refused() -> None:
    artifact = _artifact(base_model_revision="declared-base-rev")
    model_version = _model_version(
        import_kind="adapter",
        trainable_artifact_id=artifact.id,
        weights_digest=artifact.weights_digest,
        base_model_revision=artifact.base_model_revision,
    )
    try:
        build_artifact_lineage_manifest(model_version, artifact)
        check(
            "FTF-1002",
            "an adapter import with no resolved_base_artifact at all is refused -- the base "
            "check is mandatory, not optional",
            False,
        )
    except ArtifactLineageError:
        check(
            "FTF-1002",
            "an adapter import with no resolved_base_artifact at all is refused -- the base "
            "check is mandatory, not optional",
            True,
        )


def test_adapter_self_parent_by_id_is_refused() -> None:
    artifact = _artifact(base_model_revision="declared-base-rev")
    model_version = _model_version(
        import_kind="adapter",
        trainable_artifact_id=artifact.id,
        weights_digest=artifact.weights_digest,
        base_model_revision=artifact.base_model_revision,
    )
    try:
        build_artifact_lineage_manifest(model_version, artifact, resolved_base_artifact=artifact)
        check(
            "FTF-1002", "an adapter resolving to itself (same id) as its own base is refused", False
        )
    except AdapterBaseMismatchError:
        check(
            "FTF-1002", "an adapter resolving to itself (same id) as its own base is refused", True
        )


def test_adapter_self_parent_by_digest_is_refused() -> None:
    artifact = _artifact(base_model_revision="declared-base-rev")
    model_version = _model_version(
        import_kind="adapter",
        trainable_artifact_id=artifact.id,
        weights_digest=artifact.weights_digest,
        base_model_revision=artifact.base_model_revision,
    )
    # A distinct row (different id), but it shares the adapter's own
    # weights_digest -- exactly the same self-referential defect by a
    # different field.
    same_digest_base = _artifact(
        status="ready", base_model_revision="declared-base-rev", weights_digest=_REAL_DIGEST
    )
    try:
        build_artifact_lineage_manifest(
            model_version, artifact, resolved_base_artifact=same_digest_base
        )
        check(
            "FTF-1002",
            "an adapter whose resolved base shares its own weights_digest is refused",
            False,
        )
    except AdapterBaseMismatchError:
        check(
            "FTF-1002",
            "an adapter whose resolved base shares its own weights_digest is refused",
            True,
        )


def test_cross_tenant_resolved_base_artifact_is_refused() -> None:
    artifact = _artifact(base_model_revision="declared-base-rev")
    model_version = _model_version(
        import_kind="adapter",
        trainable_artifact_id=artifact.id,
        weights_digest=artifact.weights_digest,
        base_model_revision=artifact.base_model_revision,
    )
    foreign_base = _artifact(
        organization_id=_OTHER_ORG_ID,
        status="ready",
        base_model_revision="declared-base-rev",
        weights_digest=_BASE_DIGEST,
    )
    try:
        build_artifact_lineage_manifest(
            model_version, artifact, resolved_base_artifact=foreign_base
        )
        check("FTF-1002", "a cross-tenant resolved_base_artifact is refused", False)
    except AdapterBaseMismatchError:
        check("FTF-1002", "a cross-tenant resolved_base_artifact is refused", True)


# -- Negative: 2026-10-06 L2 round-3 findings ------------------------------


def test_r3_blank_revision_on_both_sides_is_refused() -> None:
    """Round-3 DoD (a): format is validated in the module itself, not
    merely by `TrainableArtifact.__init__` (which an ORM row loaded from
    the database never runs). Set on BOTH `artifact` and `model_version`
    to the SAME blank value, post-construction -- isolated from the
    pre-existing drift check, which only compares the two against each
    other and would not catch a value that's equally blank on both
    sides."""
    artifact = _artifact()
    model_version = _linked_full_weight_model_version(artifact)
    artifact.base_model_revision = ""
    model_version.base_model_revision = ""
    try:
        build_artifact_lineage_manifest(model_version, artifact)
        check(
            "FTF-1002",
            "negative_acceptance: a blank base_model_revision (identical on both sides, so "
            "the drift check alone cannot catch it) is refused",
            False,
        )
    except ArtifactLineageError:
        check(
            "FTF-1002",
            "negative_acceptance: a blank base_model_revision (identical on both sides, so "
            "the drift check alone cannot catch it) is refused",
            True,
        )


def test_r3_malformed_digest_on_both_sides_is_refused() -> None:
    """Round-3 DoD (a): same isolation technique as the revision test
    above, for weights_digest. None/''/'abc'/uppercase/whitespace are all
    malformed."""
    for bad_digest in (None, "", "abc", "sha256:" + "A" * 64, "sha256:" + "a" * 64 + " "):
        artifact = _artifact()
        model_version = _linked_full_weight_model_version(artifact)
        artifact.weights_digest = bad_digest
        model_version.weights_digest = bad_digest
        try:
            build_artifact_lineage_manifest(model_version, artifact)
            check(
                "FTF-1002",
                f"negative_acceptance: a malformed weights_digest {bad_digest!r} (identical on "
                "both sides) is refused",
                False,
            )
        except ArtifactLineageError:
            check(
                "FTF-1002",
                f"negative_acceptance: a malformed weights_digest {bad_digest!r} (identical on "
                "both sides) is refused",
                True,
            )


def test_r3_resolved_base_model_family_mismatch_is_refused() -> None:
    """Round-3 DoD (b): "there is no model_family check" -- a resolved
    base of a different model_family than the adapter itself declares
    must be refused even when base_model_revision happens to match."""
    artifact = _artifact(base_model_revision="shared-rev", model_family="llama")
    model_version = _model_version(
        import_kind="adapter",
        trainable_artifact_id=artifact.id,
        weights_digest=artifact.weights_digest,
        base_model_revision=artifact.base_model_revision,
    )
    mismatched_family_base = _artifact(
        status="ready",
        base_model_revision="shared-rev",
        model_family="mistral",
        weights_digest=_BASE_DIGEST,
    )
    try:
        build_artifact_lineage_manifest(
            model_version, artifact, resolved_base_artifact=mismatched_family_base
        )
        check(
            "FTF-1002",
            "negative_acceptance: a resolved base of a different model_family (same "
            "base_model_revision string) is refused",
            False,
        )
    except AdapterBaseMismatchError:
        check(
            "FTF-1002",
            "negative_acceptance: a resolved base of a different model_family (same "
            "base_model_revision string) is refused",
            True,
        )


def test_r3_resolved_base_case_variant_digest_is_refused() -> None:
    """Round-3 DoD (b): "a case-variant self-digest evades the check" --
    an uppercase-variant digest (set post-construction, bypassing
    `TrainableArtifact.__init__`'s own validation, the same as a row
    loaded from the database would carry it) is refused by the round-3
    format-validation fix (DoD item (a)) before the self-digest
    comparison is even reached -- `validate_weights_digest` accepts only
    the canonical lowercase-hex pattern, so a case variant can never
    survive to be compared at all."""
    artifact = _artifact(base_model_revision="shared-rev-2")
    model_version = _model_version(
        import_kind="adapter",
        trainable_artifact_id=artifact.id,
        weights_digest=artifact.weights_digest,
        base_model_revision=artifact.base_model_revision,
    )
    case_variant_self = _artifact(status="ready", base_model_revision="shared-rev-2")
    assert artifact.weights_digest is not None
    case_variant_self.weights_digest = artifact.weights_digest.upper()
    try:
        build_artifact_lineage_manifest(
            model_version, artifact, resolved_base_artifact=case_variant_self
        )
        check(
            "FTF-1002",
            "negative_acceptance: a resolved base whose weights_digest is an uppercase variant "
            "of the adapter's own digest is refused",
            False,
        )
    except AdapterBaseMismatchError:
        check(
            "FTF-1002",
            "negative_acceptance: a resolved base whose weights_digest is an uppercase variant "
            "of the adapter's own digest is refused",
            True,
        )


def test_r3_resolved_base_id_and_digest_are_recorded_in_the_manifest() -> None:
    """Round-3 DoD (b): "the base id and digest are not recorded in the
    manifest" -- for an adapter import, the resolved base's own real
    identity (id, digest, model_family, source_uri) must be discoverable
    from the returned manifest, not only the adapter's own declared
    base_model_revision."""
    artifact = _artifact(base_model_revision="shared-rev-3")
    model_version = _model_version(
        import_kind="adapter",
        trainable_artifact_id=artifact.id,
        weights_digest=artifact.weights_digest,
        base_model_revision=artifact.base_model_revision,
    )
    base_artifact = _artifact(
        status="ready", base_model_revision="shared-rev-3", weights_digest=_BASE_DIGEST
    )
    manifest = build_artifact_lineage_manifest(
        model_version, artifact, resolved_base_artifact=base_artifact
    )
    assert base_artifact.weights_digest is not None
    check(
        "FTF-1002",
        "the resolved base artifact's own real id is recorded in the manifest's base fact",
        str(base_artifact.id) in manifest.base.detail,
    )
    check(
        "FTF-1002",
        "the resolved base artifact's own real weights_digest is recorded in the manifest's "
        "base fact",
        base_artifact.weights_digest in manifest.base.detail,
    )


def test_r3_endpoint_import_with_resolved_base_artifact_is_refused() -> None:
    """Round-3 DoD (c): "an endpoint import with a resolved_base_artifact
    silently ignores it" -- an endpoint has no local artifact at all, so
    a supplied base cannot be real evidence; it must be refused, not
    silently dropped."""
    model_version = _model_version(import_kind="endpoint")
    stray_base = _artifact(status="ready")
    try:
        build_artifact_lineage_manifest(model_version, None, resolved_base_artifact=stray_base)
        check(
            "FTF-1002",
            "negative_acceptance: an endpoint import with a resolved_base_artifact is refused, "
            "not silently ignored",
            False,
        )
    except ArtifactLineageError:
        check(
            "FTF-1002",
            "negative_acceptance: an endpoint import with a resolved_base_artifact is refused, "
            "not silently ignored",
            True,
        )


def test_lineage_fact_cannot_carry_a_value_while_unknown() -> None:
    try:
        LineageFact(known=False, value="sha256:" + "c" * 64, detail="should never construct")
        check("FTF-1002", "LineageFact refuses known=False with a non-None value", False)
    except ArtifactLineageError:
        check("FTF-1002", "LineageFact refuses known=False with a non-None value", True)


def test_manifest_is_frozen() -> None:
    artifact = _artifact()
    model_version = _linked_full_weight_model_version(artifact)
    manifest = build_artifact_lineage_manifest(model_version, artifact)
    try:
        manifest.import_kind = "adapter"  # type: ignore[misc]
        check("FTF-1002", "ArtifactLineageManifest is immutable", False)
    except FrozenInstanceError:
        check("FTF-1002", "ArtifactLineageManifest is immutable", True)


# -- Negative: manifest tamper-evidence (2026-10-06 L2 round-2) -----------


def test_manifest_digest_is_present_and_verifiable() -> None:
    artifact = _artifact()
    model_version = _linked_full_weight_model_version(artifact)
    manifest = build_artifact_lineage_manifest(model_version, artifact)
    check(
        "FTF-1002",
        "manifest carries a real sha256 manifest_digest",
        isinstance(manifest.manifest_digest, str)
        and manifest.manifest_digest.startswith("sha256:")
        and len(manifest.manifest_digest) == len("sha256:") + 64,
    )
    try:
        verify_artifact_lineage_manifest(manifest)
        check("FTF-1002", "verify_artifact_lineage_manifest accepts an untampered manifest", True)
    except ArtifactLineageManifestTamperedError:
        check("FTF-1002", "verify_artifact_lineage_manifest accepts an untampered manifest", False)


def test_verify_artifact_lineage_manifest_detects_tampering() -> None:
    artifact = _artifact()
    model_version = _linked_full_weight_model_version(artifact)
    manifest = build_artifact_lineage_manifest(model_version, artifact)
    # The frozen dataclass guard refuses ordinary attribute assignment
    # (test_manifest_is_frozen already proves that); object.__setattr__
    # bypasses it directly, simulating mutation/corruption after
    # construction -- exactly what verify_artifact_lineage_manifest exists
    # to detect.
    object.__setattr__(manifest, "import_kind", "adapter")
    try:
        verify_artifact_lineage_manifest(manifest)
        check(
            "FTF-1002",
            "verify_artifact_lineage_manifest detects a manifest mutated past the frozen guard",
            False,
        )
    except ArtifactLineageManifestTamperedError:
        check(
            "FTF-1002",
            "verify_artifact_lineage_manifest detects a manifest mutated past the frozen guard",
            True,
        )


# Round-3 DoD (d): "mutate EVERY fact in the tamper test" -- the test above
# only ever mutated `import_kind`. A field `_compute_manifest_digest` (or
# its caller) silently omitted from the canonical dict would go
# undetected forever if no test ever mutated exactly that field.
_MANIFEST_TAMPER_MUTATIONS: tuple[tuple[str, object], ...] = (
    ("model_version_id", "a-different-model-version-id"),
    ("import_kind", "adapter"),
    ("base", LineageFact(known=True, value="sha256:" + "9" * 64, detail="mutated")),
    ("tokenizer", LineageFact(known=True, value="sha256:" + "9" * 64, detail="mutated")),
    ("training", LineageFact(known=True, value="mutated-training", detail="mutated")),
    ("data", LineageFact(known=True, value="mutated-data", detail="mutated")),
    ("license", LineageFact(known=True, value="mutated-license", detail="mutated")),
    ("runtime", LineageFact(known=True, value="mutated-runtime", detail="mutated")),
    ("method", LineageFact(known=True, value="mutated-method", detail="mutated")),
)


def test_verify_artifact_lineage_manifest_detects_tampering_of_every_field() -> None:
    for field_name, mutated_value in _MANIFEST_TAMPER_MUTATIONS:
        artifact = _artifact(required_python_packages=["torch==2.4.0"])
        model_version = _linked_full_weight_model_version(artifact)
        manifest = build_artifact_lineage_manifest(model_version, artifact)
        object.__setattr__(manifest, field_name, mutated_value)
        try:
            verify_artifact_lineage_manifest(manifest)
            check(
                "FTF-1002",
                f"verify_artifact_lineage_manifest detects tampering of field {field_name!r} "
                "(a field the digest silently omitted would let exactly this mutation through)",
                False,
            )
        except ArtifactLineageManifestTamperedError:
            check(
                "FTF-1002",
                f"verify_artifact_lineage_manifest detects tampering of field {field_name!r}",
                True,
            )


def test_manifest_digest_differs_for_distinct_field_values_sharing_the_same_model_version_id() -> (
    None
):
    """Round-3 DoD (d): "add a test for the same id with a different
    digest" -- two manifests built for the SAME model_version_id but
    artifacts with distinct weights_digest values must not collide. A
    digest computed mostly or only from model_version_id (rather than
    genuinely from content) would pass every mutation test above (which
    always compares a manifest against its own mutated self) yet still
    collide across two genuinely different, legitimately-built
    manifests."""
    shared_mv_id = uuid.uuid4()

    artifact_a = _artifact(weights_digest="sha256:" + "1" * 64)
    mv_a = _model_version(
        import_kind="full_weight",
        organization_id=artifact_a.organization_id,
        trainable_artifact_id=artifact_a.id,
        weights_digest=artifact_a.weights_digest,
        base_model_revision=artifact_a.base_model_revision,
    )
    mv_a.id = shared_mv_id
    manifest_a = build_artifact_lineage_manifest(mv_a, artifact_a)

    artifact_b = _artifact(weights_digest="sha256:" + "2" * 64)
    mv_b = _model_version(
        import_kind="full_weight",
        organization_id=artifact_b.organization_id,
        trainable_artifact_id=artifact_b.id,
        weights_digest=artifact_b.weights_digest,
        base_model_revision=artifact_b.base_model_revision,
    )
    mv_b.id = shared_mv_id
    manifest_b = build_artifact_lineage_manifest(mv_b, artifact_b)

    check(
        "FTF-1002",
        "both manifests really share the same model_version_id (isolating this as the only "
        "shared field)",
        manifest_a.model_version_id == manifest_b.model_version_id,
    )
    check(
        "FTF-1002",
        "manifest_digest differs for two manifests sharing the same model_version_id but "
        "distinct base/tokenizer weights_digest values",
        manifest_a.manifest_digest != manifest_b.manifest_digest,
    )


# -- Real fault injection (Law 1) -------------------------------------------


def test_fault_injection_disabling_base_revision_compare_genuinely_fails() -> None:
    """Disable the real `base_model_revision` comparison that backs
    `AdapterBaseMismatchError` and prove the mismatch case this task's own
    negative_acceptance requires genuinely stops failing -- then restore
    the module byte-identical. Proves the guard is load-bearing, not
    merely present. The probe's `artifact`/`mismatched_base` deliberately
    carry distinct weights_digest values so this round's new self-digest
    check cannot also catch the fault -- isolating this one guard."""
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

def make_artifact(revision, digest, status="ready"):
    a = TrainableArtifact(
        organization_id=org_id, display_name="a-" + uuid.uuid4().hex[:8],
        source_type="local_filesystem", source_uri="model-a", model_family="tiny",
        base_model_revision=revision, weights_digest=digest,
        license_id="apache-2.0", trust_remote_code_allowed=False, status=status,
    )
    a.id = uuid.uuid4()
    a.created_at = now
    a.updated_at = now
    if status == "ready":
        a.license_accepted_by = "reviewer-1"
        a.license_accepted_at = now
    return a

artifact = make_artifact("declared-base-rev", "sha256:" + "a" * 64)
mismatched_base = make_artifact("a-different-base-rev", "sha256:" + "b" * 64)
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


def test_fault_injection_additive_early_return_bypasses_every_round2_guard() -> None:
    """Additive fault (Law 1's own 'an early return' example, not merely an
    edit of an existing line): insert a brand-new, unconditional
    `return None` immediately after the real id-match check and before
    every one of this round's new status/tenant/digest/revision/mandatory-
    base checks. Prove a scenario that should be refused (an 'archived'
    parent) instead silently returns `None` -- then restore the module
    byte-identical."""
    original = _MODULE_PATH.read_bytes()
    text = original.decode("utf-8")
    needle = '    if artifact.status != "ready":'
    check(
        "FTF-1002",
        "additive fault injection anchor line is present in the real module",
        needle in text,
    )
    mutated = text.replace(
        needle,
        "    return None  # additive fault: bypasses every round-2 guard below\n" + needle,
        1,
    )
    check(
        "FTF-1002", "additive fault injection actually changed the module source", mutated != text
    )

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

artifact = TrainableArtifact(
    organization_id=org_id, display_name="a-" + uuid.uuid4().hex[:8],
    source_type="local_filesystem", source_uri="model-a", model_family="tiny",
    base_model_revision="rev-1", weights_digest="sha256:" + "a" * 64,
    license_id="apache-2.0", trust_remote_code_allowed=False, status="archived",
)
artifact.id = uuid.uuid4()
artifact.created_at = now
artifact.updated_at = now
mv = ModelVersion(
    organization_id=org_id, project_id=uuid.uuid4(), version_number=1, import_kind="full_weight",
    trainable_artifact_id=artifact.id, weights_digest=artifact.weights_digest,
    base_model_revision=artifact.base_model_revision, created_by="user-1", created_at=now,
)
mv.id = uuid.uuid4()
result = build_artifact_lineage_manifest(mv, artifact)
print("NO_ERROR_RAISED result=" + repr(result))
"""
        completed = subprocess.run(
            [sys.executable, "-c", probe_script], capture_output=True, text=True, timeout=30
        )
        check(
            "FTF-1002",
            "additive fault injection: an early return after the id-match check genuinely lets "
            f"an archived parent through uncaught (stdout={completed.stdout!r} "
            f"stderr={completed.stderr!r})",
            completed.returncode == 0 and "NO_ERROR_RAISED" in completed.stdout,
        )
    finally:
        _MODULE_PATH.write_bytes(original)
        restored = _MODULE_PATH.read_bytes()
        check(
            "FTF-1002",
            "the module file is restored byte-identical after additive fault injection",
            restored == original,
        )


# -- Round-3 self-attack: fault injection for every new guard (STEP 5b) ---

_R3_PROBE_PRELUDE = f"""
import sys
sys.path.insert(0, {str(ROOT)!r})
sys.path.insert(0, {str(API_ROOT)!r})
import uuid
from datetime import datetime, UTC
from app.artifacts.artifact_lineage_manifest import (
    AdapterBaseMismatchError,
    ArtifactLineageError,
    build_artifact_lineage_manifest,
)
from app.artifacts.artifact_schema import TrainableArtifact
from app.model_versions.model_version_schema import ModelVersion

org_id = uuid.uuid4()
now = datetime.now(UTC)


def make_artifact(revision, digest, status="ready", model_family="tiny"):
    a = TrainableArtifact(
        organization_id=org_id, display_name="a-" + uuid.uuid4().hex[:8],
        source_type="local_filesystem", source_uri="model-a", model_family=model_family,
        base_model_revision=revision, weights_digest=digest,
        license_id="apache-2.0", trust_remote_code_allowed=False, status=status,
    )
    a.id = uuid.uuid4()
    a.created_at = now
    a.updated_at = now
    if status == "ready":
        a.license_accepted_by = "reviewer-1"
        a.license_accepted_at = now
    return a
"""


def _run_r3_fault(*, needle: str, replacement: str, probe_body: str, description: str) -> None:
    """Shared round-3 fault-injection runner: mutate `_MODULE_PATH` by
    replacing `needle` with `replacement` (asserted present/changed
    first), run `probe_body` (appended to `_R3_PROBE_PRELUDE`) in a fresh
    subprocess against the mutated module, check it prints
    `NO_ERROR_RAISED`, then restore the module file byte-identical no
    matter what happened above."""
    original = _MODULE_PATH.read_bytes()
    text = original.decode("utf-8")
    check("FTF-1002", f"[{description}] fault injection anchor is present", needle in text)
    mutated = text.replace(needle, replacement, 1)
    check(
        "FTF-1002", f"[{description}] fault injection actually changed the module", mutated != text
    )

    try:
        _MODULE_PATH.write_text(mutated, encoding="utf-8")
        completed = subprocess.run(
            [sys.executable, "-c", _R3_PROBE_PRELUDE + probe_body],
            capture_output=True,
            text=True,
            timeout=30,
        )
        check(
            "FTF-1002",
            f"[{description}] genuinely lets the attack through uncaught "
            f"(stdout={completed.stdout!r} stderr={completed.stderr!r})",
            completed.returncode == 0 and "NO_ERROR_RAISED" in completed.stdout,
        )
    finally:
        _MODULE_PATH.write_bytes(original)
        restored = _MODULE_PATH.read_bytes()
        check(
            "FTF-1002",
            f"[{description}] the module file is restored byte-identical after fault injection",
            restored == original,
        )


def test_fault_injection_r3_format_validation_bypass_is_caught() -> None:
    """Round-3 DoD (a), Law 1: deleting the shared-validator format check
    lets a blank base_model_revision (identical on both sides, so the
    pre-existing drift check alone cannot catch it) through uncaught."""
    needle = (
        "    try:\n"
        "        validate_weights_digest(model_version.weights_digest)\n"
        "        validate_weights_digest(artifact.weights_digest)\n"
        "        validate_base_model_revision(model_version.base_model_revision)\n"
        "        validate_base_model_revision(artifact.base_model_revision)\n"
        "    except TrainableArtifactValidationError as exc:\n"
        "        raise ArtifactLineageError(\n"
        '            f"model_version {model_version.id} or its parent artifact '
        '{artifact.id} carries a "\n'
        '            f"malformed or unresolved weights_digest/base_model_revision -- {exc}"\n'
        "        ) from exc\n"
    )
    _run_r3_fault(
        needle=needle,
        replacement="",
        probe_body="""
artifact = make_artifact("rev-1", "sha256:" + "a" * 64)
artifact.base_model_revision = ""  # post-construction: bypasses __init__'s own validation
mv = ModelVersion(
    organization_id=org_id, project_id=uuid.uuid4(), version_number=1, import_kind="full_weight",
    trainable_artifact_id=artifact.id, weights_digest=artifact.weights_digest,
    base_model_revision="rev-1", created_by="user-1", created_at=now,
)
mv.id = uuid.uuid4()
mv.base_model_revision = ""  # post-construction: bypasses __init__'s own validation
build_artifact_lineage_manifest(mv, artifact)
print("NO_ERROR_RAISED")
""",
        description="format-validation bypass (blank revision, identical both sides)",
    )


def test_fault_injection_r3_model_family_check_bypass_is_caught() -> None:
    """Round-3 DoD (b), Law 1, additive fault: a resolved base of a
    different model_family than the adapter declares is let through once
    the model_family check is removed."""
    needle = (
        "        if resolved_base_artifact.model_family != artifact.model_family:\n"
        "            raise AdapterBaseMismatchError(\n"
        '                f"resolved base artifact {resolved_base_artifact.id} declares '
        'model_family "\n'
        '                f"{resolved_base_artifact.model_family!r}, but adapter artifact '
        '{artifact.id} "\n'
        '                f"declares model_family {artifact.model_family!r} -- refusing a '
        'resolved base "\n'
        '                "from a different model family than the adapter itself declares"\n'
        "            )\n"
    )
    _run_r3_fault(
        needle=needle,
        replacement="",
        probe_body="""
artifact = make_artifact("shared-rev", "sha256:" + "a" * 64, model_family="llama")
mv = ModelVersion(
    organization_id=org_id, project_id=uuid.uuid4(), version_number=1, import_kind="adapter",
    trainable_artifact_id=artifact.id, weights_digest=artifact.weights_digest,
    base_model_revision=artifact.base_model_revision, created_by="user-1", created_at=now,
)
mv.id = uuid.uuid4()
base = make_artifact("shared-rev", "sha256:" + "b" * 64, model_family="mistral")
build_artifact_lineage_manifest(mv, artifact, resolved_base_artifact=base)
print("NO_ERROR_RAISED")
""",
        description="model_family check bypass (adapter llama, resolved base mistral)",
    )


def test_fault_injection_r3_resolved_base_format_validation_bypass_is_caught() -> None:
    """Round-3 DoD (b), Law 1: "the base digest is not validated" -- with
    the resolved base's own format-validation block removed, a malformed
    (case-variant) base digest is let through uncaught. This is the real
    guard that makes `test_r3_resolved_base_case_variant_digest_is_refused`
    pass: once this block is gone, a case-variant digest reaches the
    (intentionally non-case-insensitive) self-digest `==` comparison
    unvalidated and that comparison, correctly, does not catch it either
    (it is not the same digest, only a case variant) -- so the artifact is
    silently accepted."""
    needle = (
        "        try:\n"
        "            validate_weights_digest(resolved_base_artifact.weights_digest)\n"
        "            validate_base_model_revision(resolved_base_artifact.base_model_revision)\n"
        "        except TrainableArtifactValidationError as exc:\n"
        "            raise AdapterBaseMismatchError(\n"
        '                f"resolved base artifact {resolved_base_artifact.id} carries a '
        'malformed or "\n'
        '                f"unresolved weights_digest/base_model_revision -- {exc}"\n'
        "            ) from exc\n"
    )
    _run_r3_fault(
        needle=needle,
        replacement="",
        probe_body="""
artifact = make_artifact("shared-rev", "sha256:" + "a" * 64)
mv = ModelVersion(
    organization_id=org_id, project_id=uuid.uuid4(), version_number=1, import_kind="adapter",
    trainable_artifact_id=artifact.id, weights_digest=artifact.weights_digest,
    base_model_revision=artifact.base_model_revision, created_by="user-1", created_at=now,
)
mv.id = uuid.uuid4()
base = make_artifact("shared-rev", "sha256:" + "b" * 64)
base.weights_digest = "sha256:" + "B" * 64  # case-variant, malformed once unvalidated
build_artifact_lineage_manifest(mv, artifact, resolved_base_artifact=base)
print("NO_ERROR_RAISED")
""",
        description="resolved_base_artifact format-validation bypass (case-variant digest)",
    )


def test_fault_injection_r3_endpoint_resolved_base_refusal_bypass_is_caught() -> None:
    """Round-3 DoD (c), Law 1, additive fault: an endpoint import with a
    resolved_base_artifact is silently accepted once this round's new
    refusal is removed."""
    needle = (
        "        if resolved_base_artifact is not None:\n"
        "            # 2026-10-06 L2 round-3 finding: an endpoint import with a\n"
        "            # resolved_base_artifact silently ignored it. resolved_base_\n"
        "            # artifact is only meaningful for an adapter import (Section\n"
        "            # 68.1); an endpoint has no local artifact at all, so a\n"
        "            # supplied base cannot be real evidence either.\n"
        "            raise ArtifactLineageError(\n"
        "                f\"model_version {model_version.id} is import_kind='endpoint' -- \"\n"
        '                "resolved_base_artifact is only meaningful for an adapter import; '
        'refusing to "\n'
        '                "silently ignore a supplied base for an endpoint-only import"\n'
        "            )\n"
    )
    _run_r3_fault(
        needle=needle,
        replacement="",
        probe_body="""
mv = ModelVersion(
    organization_id=org_id, project_id=uuid.uuid4(), version_number=1, import_kind="endpoint",
    endpoint_reference="https://models.example.com/v1/prod", created_by="user-1", created_at=now,
)
mv.id = uuid.uuid4()
stray_base = make_artifact("rev-1", "sha256:" + "a" * 64)
build_artifact_lineage_manifest(mv, None, resolved_base_artifact=stray_base)
print("NO_ERROR_RAISED")
""",
        description="endpoint + resolved_base_artifact refusal bypass",
    )


# -- Round-4 return (2026-10-06T16:35Z, PR #490 returned): 3 blocking findings ----
#
# (d) every fixture used the SAME license_id="apache-2.0" -- a hardcoded license.value, a dropped
#     accepted_by, a dropped accepted_at, or an adapter's base.value silently switched to the
#     resolved base's own digest instead of the adapter artifact's own digest, would all have
#     stayed GREEN. Nothing ever varied to expose any of the four.
# (b) `_canonical_fact` dropping "detail" (or "value", or "known") stays GREEN, because every
#     existing tamper test swaps a whole LineageFact for a different one (always changing several
#     sub-fields together) -- never mutates exactly one sub-field in isolation. Also: two adapters
#     differing only in the resolved base's own id or digest must produce different
#     manifest_digests (the resolved-base part must genuinely be hashed, not merely described).
# (a) no behavioral test covered a None/blank/malformed/whitespace resolved-base weights_digest
#     with the adapter's own digest genuinely valid throughout.


def test_r4_license_fact_binds_the_real_per_artifact_license_and_acceptance_detail() -> None:
    """Round-4 DoD (d): two artifacts with distinct, non-default license_id/accepted_by/
    accepted_at values prove the manifest genuinely binds each artifact's own real license fact,
    not a value every prior fixture happened to share."""
    accepted_at_a = datetime(2025, 3, 1, 12, 0, 0, tzinfo=UTC)
    artifact_a = _artifact(
        license_id="mit", license_accepted_by="reviewer-alpha", license_accepted_at=accepted_at_a
    )
    manifest_a = build_artifact_lineage_manifest(
        _linked_full_weight_model_version(artifact_a), artifact_a
    )

    accepted_at_b = datetime(2025, 9, 15, 8, 30, 0, tzinfo=UTC)
    artifact_b = _artifact(
        license_id="gpl-3.0", license_accepted_by="reviewer-beta", license_accepted_at=accepted_at_b
    )
    manifest_b = build_artifact_lineage_manifest(
        _linked_full_weight_model_version(artifact_b), artifact_b
    )

    check(
        "FTF-1002",
        "artifact A's license fact carries its own non-default license_id, not a hardcoded "
        "'apache-2.0'",
        manifest_a.license.value == "mit",
    )
    check(
        "FTF-1002",
        "artifact A's license detail carries its own real accepted_by and accepted_at",
        "reviewer-alpha" in manifest_a.license.detail
        and accepted_at_a.isoformat() in manifest_a.license.detail,
    )
    check(
        "FTF-1002",
        "artifact B's license fact carries its own distinct license_id",
        manifest_b.license.value == "gpl-3.0",
    )
    check(
        "FTF-1002",
        "artifact B's license detail carries its own real accepted_by and accepted_at, distinct "
        "from artifact A's",
        "reviewer-beta" in manifest_b.license.detail
        and accepted_at_b.isoformat() in manifest_b.license.detail,
    )
    check(
        "FTF-1002",
        "the two artifacts' license facts are genuinely distinct, not a shared default",
        manifest_a.license.value != manifest_b.license.value
        and manifest_a.license.detail != manifest_b.license.detail,
    )


def test_r4_adapter_base_value_is_the_artifacts_own_digest_not_the_resolved_bases() -> None:
    """Round-4 DoD (d): no existing test asserted WHICH digest `manifest.base.value` carries for
    an adapter import -- only that it is `known`
    (`test_adapter_import_reports_method_adapter`). Assert the exact value: the adapter artifact's
    OWN weights_digest, never the resolved base artifact's digest (deliberately distinct here so
    the two cannot be confused)."""
    artifact = _artifact(base_model_revision="shared-rev-5", weights_digest=_REAL_DIGEST)
    model_version = _model_version(
        import_kind="adapter",
        trainable_artifact_id=artifact.id,
        weights_digest=artifact.weights_digest,
        base_model_revision=artifact.base_model_revision,
    )
    base_artifact = _artifact(
        status="ready", base_model_revision="shared-rev-5", weights_digest=_BASE_DIGEST
    )
    manifest = build_artifact_lineage_manifest(
        model_version, artifact, resolved_base_artifact=base_artifact
    )
    check(
        "FTF-1002",
        "an adapter's base fact value is the adapter artifact's OWN weights_digest",
        manifest.base.value == _REAL_DIGEST,
    )
    check(
        "FTF-1002",
        "an adapter's base fact value is never the resolved base artifact's own digest",
        manifest.base.value != _BASE_DIGEST,
    )


def test_r4_resolved_base_weights_digest_none_blank_malformed_or_whitespace_is_refused() -> None:
    """Round-4 DoD (a): None/blank/'abc'/whitespace on the resolved base's own weights_digest,
    with the adapter's own digest genuinely valid throughout (isolating this from
    `test_r3_malformed_digest_on_both_sides_is_refused`, which perturbs the ADAPTER's own digest,
    and from the case-variant-only `test_r3_resolved_base_case_variant_digest_is_refused`), must
    each be refused."""
    for bad_base_digest in (None, "", "abc", "sha256:" + "a" * 64 + " "):
        artifact = _artifact(base_model_revision="shared-rev-6")
        model_version = _model_version(
            import_kind="adapter",
            trainable_artifact_id=artifact.id,
            weights_digest=artifact.weights_digest,
            base_model_revision=artifact.base_model_revision,
        )
        base_artifact = _artifact(status="ready", base_model_revision="shared-rev-6")
        base_artifact.weights_digest = bad_base_digest  # post-construction: bypasses __init__
        try:
            build_artifact_lineage_manifest(
                model_version, artifact, resolved_base_artifact=base_artifact
            )
            check(
                "FTF-1002",
                f"negative_acceptance: a resolved base with weights_digest={bad_base_digest!r} "
                "(adapter's own digest genuinely valid) is refused",
                False,
            )
        except AdapterBaseMismatchError:
            check(
                "FTF-1002",
                f"negative_acceptance: a resolved base with weights_digest={bad_base_digest!r} "
                "(adapter's own digest genuinely valid) is refused",
                True,
            )


def test_r4_manifest_digest_differs_for_resolved_base_identity_or_digest_difference() -> None:
    """Round-4 DoD (b): 'stripping the resolved-base part before hashing stays GREEN' -- two
    adapters whose own fields are otherwise identical, differing ONLY in the resolved base
    artifact's id (same digest) or ONLY in the resolved base artifact's own digest (same id), must
    produce DIFFERENT manifest_digest values -- proving the resolved base's own identity is
    genuinely folded into the hashed content."""
    artifact = _artifact(base_model_revision="shared-rev-4")
    model_version = _model_version(
        import_kind="adapter",
        trainable_artifact_id=artifact.id,
        weights_digest=artifact.weights_digest,
        base_model_revision=artifact.base_model_revision,
    )

    base_1 = _artifact(
        status="ready", base_model_revision="shared-rev-4", weights_digest=_BASE_DIGEST
    )
    manifest_1 = build_artifact_lineage_manifest(
        model_version, artifact, resolved_base_artifact=base_1
    )

    base_2 = _artifact(
        status="ready", base_model_revision="shared-rev-4", weights_digest=_BASE_DIGEST
    )
    check(
        "FTF-1002",
        "the two resolved bases genuinely differ by id only (same digest)",
        base_1.id != base_2.id and base_1.weights_digest == base_2.weights_digest,
    )
    manifest_2 = build_artifact_lineage_manifest(
        model_version, artifact, resolved_base_artifact=base_2
    )
    check(
        "FTF-1002",
        "manifest_digest differs when only the resolved base artifact's id differs",
        manifest_1.manifest_digest != manifest_2.manifest_digest,
    )

    base_3 = _artifact(
        status="ready",
        base_model_revision="shared-rev-4",
        weights_digest="sha256:" + "7" * 64,
        artifact_id=base_1.id,
    )
    check(
        "FTF-1002",
        "the two resolved bases genuinely differ by digest only (same id)",
        base_3.id == base_1.id and base_3.weights_digest != base_1.weights_digest,
    )
    manifest_3 = build_artifact_lineage_manifest(
        model_version, artifact, resolved_base_artifact=base_3
    )
    check(
        "FTF-1002",
        "manifest_digest differs when only the resolved base artifact's weights_digest differs",
        manifest_1.manifest_digest != manifest_3.manifest_digest,
    )


def test_r4_tamper_detects_isolated_subfield_mutations() -> None:
    """Round-4 DoD (b): tamper each fact's value, known and detail SEPARATELY -- every existing
    tamper test (`test_verify_artifact_lineage_manifest_detects_tampering_of_every_field`) swaps
    the whole LineageFact for a different one, always changing several sub-fields together.
    Mutating exactly one sub-field, leaving the other two untouched, proves the digest genuinely
    covers each sub-field independently."""
    artifact = _artifact(required_python_packages=["torch==2.4.0"])
    model_version = _linked_full_weight_model_version(artifact)

    mutations: tuple[tuple[str, object], ...] = (
        ("value", "sha256:" + "9" * 64),
        ("known", False),
        ("detail", "tampered-detail-only"),
    )
    for label, new_value in mutations:
        manifest = build_artifact_lineage_manifest(model_version, artifact)
        object.__setattr__(manifest.base, label, new_value)
        try:
            verify_artifact_lineage_manifest(manifest)
            check(
                "FTF-1002",
                f"verify_artifact_lineage_manifest detects an isolated mutation of base.{label} "
                "alone (the other two sub-fields left untouched)",
                False,
            )
        except ArtifactLineageManifestTamperedError:
            check(
                "FTF-1002",
                f"verify_artifact_lineage_manifest detects an isolated mutation of base.{label} "
                "alone (the other two sub-fields left untouched)",
                True,
            )


# -- Round-4 fault injection (Law 1) ---------------------------------------

_R4_PROBE_PRELUDE = (
    _R3_PROBE_PRELUDE
    + """
from app.artifacts.artifact_lineage_manifest import (
    ArtifactLineageManifestTamperedError,
    verify_artifact_lineage_manifest,
)
"""
)


def _invalidate_pycache() -> None:
    """Several round-4 fault tests rewrite `_MODULE_PATH` in quick succession (write the fault,
    probe it, restore -- repeated per test). CPython's default bytecode cache is invalidated by
    source mtime+size, not content, so two rewrites landing within the same filesystem mtime
    tick can make the next subprocess's `import` silently serve a STALE cached `.pyc` instead of
    recompiling the file it just read fresh off disk -- a real failure mode observed while
    developing this round (three canonical_fact fault tests run back to back; the third saw the
    second's cached bytecode). Deleting any cached bytecode for this module after every write
    removes that risk outright, rather than relying on timing."""
    cache_dir = _MODULE_PATH.parent / "__pycache__"
    if not cache_dir.is_dir():
        return
    for cached in cache_dir.glob(f"{_MODULE_PATH.stem}.*.pyc"):
        cached.unlink(missing_ok=True)


_NO_PYCACHE_ENV = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}


def _run_r4_fault(*, needle: str, replacement: str, probe_body: str, description: str) -> None:
    """Same contract as `_run_r3_fault` (mutate `_MODULE_PATH`, run `probe_body` in a fresh
    subprocess against `_R4_PROBE_PRELUDE`, expect `NO_ERROR_RAISED`, always restore
    byte-identical) -- kept separate because round 4's probes need `verify_artifact_lineage_
    manifest`/`ArtifactLineageManifestTamperedError`, which `_R3_PROBE_PRELUDE` does not import.
    Also invalidates the bytecode cache around every write (see `_invalidate_pycache`)."""
    original = _MODULE_PATH.read_bytes()
    text = original.decode("utf-8")
    check("FTF-1002", f"[{description}] fault injection anchor is present", needle in text)
    mutated = text.replace(needle, replacement, 1)
    check(
        "FTF-1002", f"[{description}] fault injection actually changed the module", mutated != text
    )
    try:
        _MODULE_PATH.write_text(mutated, encoding="utf-8")
        _invalidate_pycache()
        completed = subprocess.run(
            [sys.executable, "-c", _R4_PROBE_PRELUDE + probe_body],
            capture_output=True,
            text=True,
            timeout=30,
            env=_NO_PYCACHE_ENV,
        )
        check(
            "FTF-1002",
            f"[{description}] genuinely lets the attack through uncaught "
            f"(stdout={completed.stdout!r} stderr={completed.stderr!r})",
            completed.returncode == 0 and "NO_ERROR_RAISED" in completed.stdout,
        )
    finally:
        _MODULE_PATH.write_bytes(original)
        _invalidate_pycache()
        restored = _MODULE_PATH.read_bytes()
        check(
            "FTF-1002",
            f"[{description}] the module file is restored byte-identical after fault injection",
            restored == original,
        )


def _run_r4_value_fault(
    *, needle: str, replacement: str, probe_body: str, description: str, expected_marker: str
) -> None:
    """Like `_run_r4_fault`, but for a fault that corrupts a VALUE instead of bypassing a raise.
    `probe_body` must print a line containing `expected_marker` only when the fault's wrong value
    is actually observed -- proving the corresponding behavioral assertion in this file would go
    RED under this exact fault. Also invalidates the bytecode cache around every write (see
    `_invalidate_pycache`)."""
    original = _MODULE_PATH.read_bytes()
    text = original.decode("utf-8")
    check("FTF-1002", f"[{description}] fault injection anchor is present", needle in text)
    mutated = text.replace(needle, replacement, 1)
    check(
        "FTF-1002", f"[{description}] fault injection actually changed the module", mutated != text
    )
    try:
        _MODULE_PATH.write_text(mutated, encoding="utf-8")
        _invalidate_pycache()
        completed = subprocess.run(
            [sys.executable, "-c", _R4_PROBE_PRELUDE + probe_body],
            capture_output=True,
            text=True,
            timeout=30,
            env=_NO_PYCACHE_ENV,
        )
        check(
            "FTF-1002",
            f"[{description}] genuinely produces the wrong value, which the behavioral "
            f"assertion would catch (stdout={completed.stdout!r} stderr={completed.stderr!r})",
            completed.returncode == 0 and expected_marker in completed.stdout,
        )
    finally:
        _MODULE_PATH.write_bytes(original)
        _invalidate_pycache()
        restored = _MODULE_PATH.read_bytes()
        check(
            "FTF-1002",
            f"[{description}] the module file is restored byte-identical after fault injection",
            restored == original,
        )


_LICENSE_FACT_NEEDLE = (
    "    license_fact = LineageFact(\n"
    "        known=True,\n"
    "        value=artifact.license_id,\n"
    '        detail=f"accepted_by={artifact.license_accepted_by!r} at {accepted_at!r}",\n'
    "    )\n"
)


def test_fault_injection_r4_hardcoded_license_value_is_caught() -> None:
    """Round-4 DoD (d), Law 1: hardcoding license.value to 'apache-2.0' lets a distinct real
    license_id ('mit') go unreported -- caught by `test_r4_license_fact_binds_the_real_per_
    artifact_license_and_acceptance_detail`."""
    replacement = _LICENSE_FACT_NEEDLE.replace("value=artifact.license_id,", 'value="apache-2.0",')
    _run_r4_value_fault(
        needle=_LICENSE_FACT_NEEDLE,
        replacement=replacement,
        probe_body="""
artifact = make_artifact("rev-1", "sha256:" + "a" * 64)
artifact.license_id = "mit"
mv = ModelVersion(
    organization_id=org_id, project_id=uuid.uuid4(), version_number=1, import_kind="full_weight",
    trainable_artifact_id=artifact.id, weights_digest=artifact.weights_digest,
    base_model_revision=artifact.base_model_revision, created_by="user-1", created_at=now,
)
mv.id = uuid.uuid4()
manifest = build_artifact_lineage_manifest(mv, artifact)
print("FAULT_VALUE=" + repr(manifest.license.value))
""",
        description="hardcoded license.value ('apache-2.0' instead of the real license_id)",
        expected_marker="FAULT_VALUE='apache-2.0'",
    )


def test_fault_injection_r4_license_detail_drops_accepted_by_is_caught() -> None:
    """Round-4 DoD (d), Law 1: hardcoding accepted_by=None in the license detail drops the real
    reviewer identity -- caught by the same test above."""
    replacement = _LICENSE_FACT_NEEDLE.replace(
        'detail=f"accepted_by={artifact.license_accepted_by!r} at {accepted_at!r}",',
        'detail=f"accepted_by=None at {accepted_at!r}",',
    )
    _run_r4_value_fault(
        needle=_LICENSE_FACT_NEEDLE,
        replacement=replacement,
        probe_body="""
artifact = make_artifact("rev-1", "sha256:" + "a" * 64)
artifact.license_accepted_by = "reviewer-x"
mv = ModelVersion(
    organization_id=org_id, project_id=uuid.uuid4(), version_number=1, import_kind="full_weight",
    trainable_artifact_id=artifact.id, weights_digest=artifact.weights_digest,
    base_model_revision=artifact.base_model_revision, created_by="user-1", created_at=now,
)
mv.id = uuid.uuid4()
manifest = build_artifact_lineage_manifest(mv, artifact)
print("FAULT_DETAIL=" + manifest.license.detail)
""",
        description="license detail drops the real accepted_by (hardcoded to None)",
        expected_marker="accepted_by=None",
    )


def test_fault_injection_r4_license_detail_drops_accepted_at_is_caught() -> None:
    """Round-4 DoD (d), Law 1: hardcoding 'at None' in the license detail drops the real
    acceptance timestamp -- caught by the same test above."""
    replacement = _LICENSE_FACT_NEEDLE.replace(
        'detail=f"accepted_by={artifact.license_accepted_by!r} at {accepted_at!r}",',
        'detail=f"accepted_by={artifact.license_accepted_by!r} at None",',
    )
    _run_r4_value_fault(
        needle=_LICENSE_FACT_NEEDLE,
        replacement=replacement,
        probe_body="""
artifact = make_artifact("rev-1", "sha256:" + "a" * 64)
mv = ModelVersion(
    organization_id=org_id, project_id=uuid.uuid4(), version_number=1, import_kind="full_weight",
    trainable_artifact_id=artifact.id, weights_digest=artifact.weights_digest,
    base_model_revision=artifact.base_model_revision, created_by="user-1", created_at=now,
)
mv.id = uuid.uuid4()
manifest = build_artifact_lineage_manifest(mv, artifact)
print("FAULT_DETAIL=" + manifest.license.detail)
""",
        description="license detail drops the real accepted_at (hardcoded to None)",
        expected_marker=" at None",
    )


def test_fault_injection_r4_adapter_base_value_uses_resolved_base_digest_is_caught() -> None:
    """Round-4 DoD (d), Law 1: switching an adapter's base.value to the resolved base artifact's
    own digest (instead of the adapter artifact's own digest) -- caught by
    `test_r4_adapter_base_value_is_the_artifacts_own_digest_not_the_resolved_bases`."""
    needle = (
        "    base = LineageFact(\n"
        "        known=True,\n"
        "        value=artifact.weights_digest,\n"
        "        detail=base_detail,\n"
        "    )\n"
    )
    replacement = needle.replace(
        "value=artifact.weights_digest,",
        "value=(resolved_base_artifact.weights_digest if resolved_base_artifact is not None "
        "else artifact.weights_digest),",
    )
    _run_r4_value_fault(
        needle=needle,
        replacement=replacement,
        probe_body="""
artifact = make_artifact("shared-rev-x", "sha256:" + "a" * 64)
mv = ModelVersion(
    organization_id=org_id, project_id=uuid.uuid4(), version_number=1, import_kind="adapter",
    trainable_artifact_id=artifact.id, weights_digest=artifact.weights_digest,
    base_model_revision=artifact.base_model_revision, created_by="user-1", created_at=now,
)
mv.id = uuid.uuid4()
base = make_artifact("shared-rev-x", "sha256:" + "f" * 64)
manifest = build_artifact_lineage_manifest(mv, artifact, resolved_base_artifact=base)
print("FAULT_VALUE=" + repr(manifest.base.value))
""",
        description="adapter base.value switched to the resolved base artifact's own digest",
        expected_marker="FAULT_VALUE='sha256:" + "f" * 64 + "'",
    )


_CANONICAL_FACT_NEEDLE = (
    "def _canonical_fact(fact: LineageFact) -> dict[str, object]:\n"
    '    return {"known": fact.known, "value": fact.value, "detail": fact.detail}\n'
)


def _canonical_fact_drop_probe(tamper_field: str, tamper_value: str) -> str:
    return f"""
artifact = make_artifact("rev-1", "sha256:" + "a" * 64)
mv = ModelVersion(
    organization_id=org_id, project_id=uuid.uuid4(), version_number=1, import_kind="full_weight",
    trainable_artifact_id=artifact.id, weights_digest=artifact.weights_digest,
    base_model_revision=artifact.base_model_revision, created_by="user-1", created_at=now,
)
mv.id = uuid.uuid4()
manifest = build_artifact_lineage_manifest(mv, artifact)
object.__setattr__(manifest.base, {tamper_field!r}, {tamper_value})
verify_artifact_lineage_manifest(manifest)
print("NO_ERROR_RAISED")
"""


def test_fault_injection_r4_canonical_fact_drops_detail_key_is_caught() -> None:
    """Round-4 DoD (b), Law 1: dropping 'detail' from `_canonical_fact`'s own dict lets a
    detail-only tamper through undetected -- caught by
    `test_r4_tamper_detects_isolated_subfield_mutations`."""
    replacement = _CANONICAL_FACT_NEEDLE.replace(
        '{"known": fact.known, "value": fact.value, "detail": fact.detail}',
        '{"known": fact.known, "value": fact.value}',
    )
    _run_r4_fault(
        needle=_CANONICAL_FACT_NEEDLE,
        replacement=replacement,
        probe_body=_canonical_fact_drop_probe("detail", '"tampered-detail-only"'),
        description="_canonical_fact drops 'detail' (a detail-only tamper goes undetected)",
    )


def test_fault_injection_r4_canonical_fact_drops_value_key_is_caught() -> None:
    """Round-4 DoD (b), Law 1: dropping 'value' from `_canonical_fact`'s own dict lets a
    value-only tamper through undetected -- caught by the same test above."""
    replacement = _CANONICAL_FACT_NEEDLE.replace(
        '{"known": fact.known, "value": fact.value, "detail": fact.detail}',
        '{"known": fact.known, "detail": fact.detail}',
    )
    _run_r4_fault(
        needle=_CANONICAL_FACT_NEEDLE,
        replacement=replacement,
        probe_body=_canonical_fact_drop_probe("value", '"sha256:" + "9" * 64'),
        description="_canonical_fact drops 'value' (a value-only tamper goes undetected)",
    )


def test_fault_injection_r4_canonical_fact_drops_known_key_is_caught() -> None:
    """Round-4 DoD (b), Law 1: dropping 'known' from `_canonical_fact`'s own dict lets a
    known-only tamper through undetected -- caught by the same test above."""
    replacement = _CANONICAL_FACT_NEEDLE.replace(
        '{"known": fact.known, "value": fact.value, "detail": fact.detail}',
        '{"value": fact.value, "detail": fact.detail}',
    )
    _run_r4_fault(
        needle=_CANONICAL_FACT_NEEDLE,
        replacement=replacement,
        probe_body=_canonical_fact_drop_probe("known", "False"),
        description="_canonical_fact drops 'known' (a known-only tamper goes undetected)",
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
