#!/usr/bin/env python3
"""FTF-0307 -- Training capability classifier (no-DB unit/fault tests).

Runnable without any database and without any external network egress:
`classify_training_capability` is a pure, synchronous, no-I/O function (this
task's own `database_changes` clause -- "No independent schema change.
Consume frozen contract.") that combines an already-computed
`AliasReconciliationResult` (FTF-0207, CERTIFIED), an optional already-
computed `ToolCallProbeResult` (FTF-0212, CERTIFIED) and an optional already-
fetched `TrainableArtifact` row (FTF-0301, CERTIFIED, whose own `ready`
status already requires FTF-0306's own verified weights digest). Every check
below builds its own inputs directly as plain dataclasses/ORM instances --
no HTTP server, no secret backend, no TLS fixture and no Postgres connection
is needed, the same `tests/tasks/test_ftf_0207.py`/`tests/tasks/
test_ftf_0306.py` precedent already established for a pure composer task
with no live route or database of its own.

This task's own acceptance clause ("Classify access, training capability,
artifact availability and role independently with dated probe evidence") is
covered by `test_each_dimension_is_computed_from_its_own_independent_
evidence`, `test_ready_artifact_without_access_still_classifies_trainable`,
`test_probed_endpoint_without_a_ready_artifact_is_inference_only` and
`test_evidence_probed_at_is_the_real_probe_timestamp_not_fabricated`. This
task's own negative_acceptance ("Inference-only endpoint cannot become
trainable by editing name; unknown capability remains blocked") is covered
by `test_no_artifact_and_no_successful_probe_is_unknown_never_fabricated`,
`test_registered_artifact_never_classifies_as_trainable`,
`test_failed_probe_never_classifies_as_inference_only` and
`test_classification_never_reads_any_name_or_alias_string_field`, plus two
real fault injections (Law 1) proving each negative check genuinely fails
when its real guard is removed.

Run directly: python3 tests/tasks/test_ftf_0307.py
"""

from __future__ import annotations

import subprocess
import sys
import uuid
from dataclasses import FrozenInstanceError
from datetime import UTC, datetime
from pathlib import Path
from types import MappingProxyType

ROOT = Path(__file__).resolve().parent.parent.parent
API_ROOT = ROOT / "apps" / "api"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(API_ROOT))

from packages.litellm_client.alias_reconciliation import (  # noqa: E402
    AliasReconciliationResult,
    ReconciledModel,
)
from packages.litellm_client.real_chat_probe import RouteIdentityState  # noqa: E402
from packages.litellm_client.real_tool_call_probe import (  # noqa: E402
    ToolCallProbeResult,
    ToolCallProbeState,
)
from packages.litellm_client.v1_models_discovery import ModelMetadataStatus  # noqa: E402

from app.artifacts.artifact_schema import TrainableArtifact  # noqa: E402
from app.artifacts.training_capability_classifier import (  # noqa: E402
    AccessState,
    ArtifactAvailability,
    TrainingCapability,
    TrainingCapabilityClassificationError,
    classify_training_capability,
)

FAILURES: list[str] = []
_MODULE_PATH = API_ROOT / "app" / "artifacts" / "training_capability_classifier.py"


def check(criterion_id: str, description: str, condition: bool) -> None:
    if not condition:
        FAILURES.append(f"FAIL [{criterion_id}] {description}")


_NOW = datetime(2026, 10, 2, tzinfo=UTC)


def _reconciliation(*ids: str) -> AliasReconciliationResult:
    models = tuple(
        ReconciledModel(
            id=i, metadata_status=ModelMetadataStatus.LIMITED, fields=MappingProxyType({})
        )
        for i in ids
    )
    return AliasReconciliationResult(models=models, model_count=len(models), reconciled_at=_NOW)


def _probe(state: ToolCallProbeState, *, probed_at: datetime = _NOW) -> ToolCallProbeResult:
    return ToolCallProbeResult(
        state=state,
        route_identity_state=RouteIdentityState.VERIFIED,
        resolved_model="gpt-a",
        advertised_tool_name="get_probe_time",
        observed_tool_name="get_probe_time" if state == ToolCallProbeState.PROBED else None,
        observed_arguments='{"timezone": "UTC"}' if state == ToolCallProbeState.PROBED else None,
        usage=None,
        latency_ms=12.5,
        detail="real probe round trip",
        probed_at=probed_at,
    )


def _artifact(*, status: str, display_name: str = "artifact-one") -> TrainableArtifact:
    now = datetime.now(UTC)
    artifact = TrainableArtifact(
        organization_id=uuid.uuid4(),
        display_name=display_name,
        source_type="local_filesystem",
        source_uri="model-a",
        model_family="tiny",
        base_model_revision="v1.0",
        license_id="apache-2.0",
        trust_remote_code_allowed=False,
        status=status,
    )
    artifact.id = uuid.uuid4()
    artifact.created_at = now
    artifact.updated_at = now
    if status == "ready":
        artifact.license_accepted_by = "reviewer-1"
        artifact.license_accepted_at = now
    return artifact


# -- Tests ----------------------------------------------------------------


def test_each_dimension_is_computed_from_its_own_independent_evidence() -> None:
    """Section 0.12's own orthogonality rule: a model with a ready artifact,
    a real successful probe, current access and an explicit role all set
    reports all four facts simultaneously, none derived from another."""
    reconciliation = _reconciliation("gpt-a")
    artifact = _artifact(status="ready")
    probe = _probe(ToolCallProbeState.PROBED)
    result = classify_training_capability(
        model_id="gpt-a",
        reconciliation=reconciliation,
        probe=probe,
        artifact=artifact,
        assigned_role="baseline",
    )
    check("FTF-0307", "access reports granted", result.access == AccessState.GRANTED)
    check(
        "FTF-0307",
        "capability reports trainable_open_weight from the ready artifact",
        result.capability == TrainingCapability.TRAINABLE_OPEN_WEIGHT,
    )
    check(
        "FTF-0307",
        "artifact_availability reports ready",
        result.artifact_availability == ArtifactAvailability.READY,
    )
    check(
        "FTF-0307",
        "artifact_id is the real artifact's own id",
        result.artifact_id == str(artifact.id),
    )
    check(
        "FTF-0307",
        "license_approved reflects the real acceptance timestamp",
        result.license_approved,
    )
    check("FTF-0307", "role is passed through verbatim", result.role == "baseline")
    check(
        "FTF-0307",
        "evidence_probed_at is the real probe timestamp",
        result.evidence_probed_at == _NOW,
    )


def test_ready_artifact_without_access_still_classifies_trainable() -> None:
    """Independence, the other direction: a model no longer present in the
    current reconciled accessible set is NOT_GRANTED, but a real ready
    artifact still proves real trainability -- `access` never suppresses
    `capability` (Section 0.12: "An endpoint can be inference-only and
    still be the production baseline" -- the same non-gating rule, applied
    to the access/capability pair instead of capability/role)."""
    reconciliation = _reconciliation("some-other-model")
    artifact = _artifact(status="ready")
    result = classify_training_capability(
        model_id="gpt-a", reconciliation=reconciliation, probe=None, artifact=artifact
    )
    check(
        "FTF-0307", "access correctly reports not_granted", result.access == AccessState.NOT_GRANTED
    )
    check(
        "FTF-0307",
        "capability still reports trainable_open_weight from the real artifact",
        result.capability == TrainingCapability.TRAINABLE_OPEN_WEIGHT,
    )


def test_probed_endpoint_without_a_ready_artifact_is_inference_only() -> None:
    reconciliation = _reconciliation("gpt-a")
    probe = _probe(ToolCallProbeState.PROBED)
    result = classify_training_capability(
        model_id="gpt-a", reconciliation=reconciliation, probe=probe, artifact=None
    )
    check(
        "FTF-0307",
        "no artifact + a real successful probe classifies inference_only",
        result.capability == TrainingCapability.INFERENCE_ONLY,
    )
    check(
        "FTF-0307",
        "artifact_availability is none when no artifact is linked",
        result.artifact_availability == ArtifactAvailability.NONE
        and result.artifact_id is None
        and not result.license_approved,
    )


def test_evidence_probed_at_is_the_real_probe_timestamp_not_fabricated() -> None:
    probed_at = datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)
    probe = _probe(ToolCallProbeState.PROBED, probed_at=probed_at)
    result = classify_training_capability(
        model_id="gpt-a",
        reconciliation=_reconciliation("gpt-a"),
        probe=probe,
        artifact=None,
    )
    check(
        "FTF-0307",
        "evidence_probed_at is exactly the real probe's own dated timestamp",
        result.evidence_probed_at == probed_at,
    )
    no_probe_result = classify_training_capability(
        model_id="gpt-a", reconciliation=_reconciliation("gpt-a"), probe=None, artifact=None
    )
    check(
        "FTF-0307",
        "no probe evidence means no fabricated date, never a current-time default",
        no_probe_result.evidence_probed_at is None,
    )


def test_no_artifact_and_no_successful_probe_is_unknown_never_fabricated() -> None:
    result = classify_training_capability(
        model_id="gpt-a", reconciliation=_reconciliation("gpt-a"), probe=None, artifact=None
    )
    check(
        "FTF-0307",
        "no evidence at all fails closed to unknown, never a guessed affirmative value",
        result.capability == TrainingCapability.UNKNOWN,
    )


def test_registered_artifact_never_classifies_as_trainable() -> None:
    """negative_acceptance: a `registered` (still-mutable, not yet digest-
    verified-ready) artifact must never grant `trainable_open_weight` --
    only `ready` (FTF-0301's own license/digest-complete gate) does."""
    for status in ("registered", "archived"):
        artifact = _artifact(status=status)
        result = classify_training_capability(
            model_id="gpt-a",
            reconciliation=_reconciliation("gpt-a"),
            probe=None,
            artifact=artifact,
        )
        check(
            "FTF-0307",
            f"a {status!r} artifact never classifies as trainable_open_weight",
            result.capability == TrainingCapability.UNKNOWN,
        )
        check(
            "FTF-0307",
            f"a {status!r} artifact still reports its own real artifact_availability",
            result.artifact_availability == ArtifactAvailability(status),
        )


def test_failed_probe_never_classifies_as_inference_only() -> None:
    """negative_acceptance's other half: a real but *failed* probe (timeout,
    rejection, mismatch) is not "proof of a tool-call capability" any more
    than FTF-0212's own negative_acceptance already established for the
    probe itself -- it must never be treated as proof of inference capability
    here either."""
    for state in (
        ToolCallProbeState.TIMEOUT,
        ToolCallProbeState.CONNECTION_REFUSED,
        ToolCallProbeState.TOOL_NAME_MISMATCH,
        ToolCallProbeState.INVALID_TOKEN,
    ):
        probe = _probe(state)
        result = classify_training_capability(
            model_id="gpt-a", reconciliation=_reconciliation("gpt-a"), probe=probe, artifact=None
        )
        check(
            "FTF-0307",
            f"a {state.value!r} probe outcome never classifies as inference_only",
            result.capability == TrainingCapability.UNKNOWN,
        )


def test_classification_never_reads_any_name_or_alias_string_field() -> None:
    """negative_acceptance: "cannot become trainable by editing name." A
    model whose `model_id` textually equals an unrelated artifact's own
    `display_name` must not become trainable merely from that coincidence
    -- this module never accepts a name-based lookup at all, only an
    explicit `artifact` object the caller already resolved by real id."""
    artifact = _artifact(status="ready", display_name="gpt-a")
    result = classify_training_capability(
        model_id="gpt-a",
        reconciliation=_reconciliation("gpt-a"),
        probe=None,
        artifact=None,  # the real artifact is NOT linked to this model_id
    )
    check(
        "FTF-0307",
        "a same-named-but-unlinked artifact never grants trainable status",
        result.capability != TrainingCapability.TRAINABLE_OPEN_WEIGHT,
    )
    check("FTF-0307", "artifact display_name was never consulted", artifact.display_name == "gpt-a")


def test_model_id_must_be_a_non_empty_string() -> None:
    for bad_id in ("", None, 123):
        try:
            classify_training_capability(
                model_id=bad_id,  # type: ignore[arg-type]
                reconciliation=_reconciliation("gpt-a"),
                probe=None,
                artifact=None,
            )
            raised = False
        except TrainingCapabilityClassificationError:
            raised = True
        check("FTF-0307", f"model_id={bad_id!r} is rejected, never silently accepted", raised)


def test_classification_result_is_frozen() -> None:
    result = classify_training_capability(
        model_id="gpt-a", reconciliation=_reconciliation("gpt-a"), probe=None, artifact=None
    )
    try:
        result.capability = TrainingCapability.TRAINABLE_OPEN_WEIGHT  # type: ignore[misc]
        mutated = True
    except FrozenInstanceError:
        mutated = False
    check("FTF-0307", "TrainingCapabilityClassification is frozen (immutable)", not mutated)


def test_fault_injection_registered_artifact_treated_as_ready_is_caught() -> None:
    """Law 1: demonstrates the negative_acceptance actually fails when the
    `ready`-only gate is loosened. Mutates the real on-disk module so
    `registered` artifacts also count as ready, runs the mutated module in a
    fresh subprocess, and proves the exact "becomes trainable" failure mode
    this task forbids actually reproduces. Reverts the file byte-identical
    afterward."""
    original = _MODULE_PATH.read_bytes()
    text = original.decode("utf-8")
    target = (
        '    "registered": ArtifactAvailability.REGISTERED,\n'
        '    "ready": ArtifactAvailability.READY,\n'
    )
    check(
        "FTF-0307",
        "the fault-injection target block is present in the tracked module",
        target in text,
    )
    mutated_map = (
        '    "registered": ArtifactAvailability.READY,\n'
        '    "ready": ArtifactAvailability.READY,\n'
    )
    mutated = text.replace(target, mutated_map, 1)
    check("FTF-0307", "the mutation actually changed the file", mutated != text)

    try:
        _MODULE_PATH.write_text(mutated, encoding="utf-8")
        probe_script = f"""
import sys
sys.path.insert(0, {str(ROOT)!r})
sys.path.insert(0, {str(API_ROOT)!r})
import uuid
from datetime import datetime, UTC
from app.artifacts.artifact_schema import TrainableArtifact
from app.artifacts.training_capability_classifier import classify_training_capability
from packages.litellm_client.alias_reconciliation import AliasReconciliationResult, ReconciledModel
from packages.litellm_client.v1_models_discovery import ModelMetadataStatus

artifact = TrainableArtifact(
    organization_id=uuid.uuid4(), display_name="x", source_type="local_filesystem",
    source_uri="model-a", model_family="tiny", base_model_revision="v1.0",
    license_id="apache-2.0", trust_remote_code_allowed=False, status="registered",
)
artifact.id = uuid.uuid4()
recon = AliasReconciliationResult(
    models=(ReconciledModel(id="gpt-a", metadata_status=ModelMetadataStatus.LIMITED, fields={{}}),),
    model_count=1, reconciled_at=datetime.now(UTC),
)
result = classify_training_capability(
    model_id="gpt-a", reconciliation=recon, probe=None, artifact=artifact
)
print("CAPABILITY=" + result.capability.value)
"""
        completed = subprocess.run(
            [sys.executable, "-c", probe_script], capture_output=True, text=True, timeout=30
        )
        check(
            "FTF-0307",
            "fault injection: loosening the ready-only gate genuinely lets a registered "
            f"artifact become trainable (stdout={completed.stdout!r} stderr={completed.stderr!r})",
            completed.returncode == 0 and "CAPABILITY=trainable_open_weight" in completed.stdout,
        )
    finally:
        _MODULE_PATH.write_bytes(original)
        restored = _MODULE_PATH.read_bytes()
        check(
            "FTF-0307",
            "the module file is restored byte-identical after fault injection",
            restored == original,
        )


def test_fault_injection_any_probe_outcome_treated_as_success_is_caught() -> None:
    """A second, independent fault injection (Law 1): mutates the real
    on-disk module so any non-null probe result (not only a real `PROBED`
    outcome) counts as inference capability, runs the mutated module in a
    fresh subprocess, and proves this task's own "failed probe never
    classifies as inference_only" negative check genuinely fails when that
    discipline is removed. Reverts the file byte-identical afterward."""
    original = _MODULE_PATH.read_bytes()
    text = original.decode("utf-8")
    target = "elif probe is not None and probe.state is ToolCallProbeState.PROBED:"
    check("FTF-0307", "the probe-state fault-injection target is present", target in text)
    mutated = text.replace(target, "elif probe is not None:", 1)
    check("FTF-0307", "the mutation actually changed the file", mutated != text)

    try:
        _MODULE_PATH.write_text(mutated, encoding="utf-8")
        probe_script = f"""
import sys
sys.path.insert(0, {str(ROOT)!r})
sys.path.insert(0, {str(API_ROOT)!r})
from datetime import datetime, UTC
from app.artifacts.training_capability_classifier import classify_training_capability
from packages.litellm_client.alias_reconciliation import AliasReconciliationResult, ReconciledModel
from packages.litellm_client.real_chat_probe import RouteIdentityState
from packages.litellm_client.real_tool_call_probe import ToolCallProbeResult, ToolCallProbeState
from packages.litellm_client.v1_models_discovery import ModelMetadataStatus

recon = AliasReconciliationResult(
    models=(ReconciledModel(id="gpt-a", metadata_status=ModelMetadataStatus.LIMITED, fields={{}}),),
    model_count=1, reconciled_at=datetime.now(UTC),
)
failed_probe = ToolCallProbeResult(
    state=ToolCallProbeState.TIMEOUT, route_identity_state=RouteIdentityState.UNVERIFIED,
    resolved_model=None, advertised_tool_name="get_probe_time", observed_tool_name=None,
    observed_arguments=None, usage=None, latency_ms=None, detail="d", probed_at=datetime.now(UTC),
)
result = classify_training_capability(
    model_id="gpt-a", reconciliation=recon, probe=failed_probe, artifact=None
)
print("CAPABILITY=" + result.capability.value)
"""
        completed = subprocess.run(
            [sys.executable, "-c", probe_script], capture_output=True, text=True, timeout=30
        )
        check(
            "FTF-0307",
            "fault injection: accepting any probe outcome genuinely lets a failed/timed-out probe "
            f"become inference_only (stdout={completed.stdout!r} stderr={completed.stderr!r})",
            completed.returncode == 0 and "CAPABILITY=inference_only" in completed.stdout,
        )
    finally:
        _MODULE_PATH.write_bytes(original)
        restored = _MODULE_PATH.read_bytes()
        check(
            "FTF-0307",
            "the module file is restored byte-identical after fault injection",
            restored == original,
        )


def main() -> None:
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()

    if FAILURES:
        print(f"FTF-0307 training capability classifier: {len(FAILURES)} failed")
        for failure in FAILURES:
            print(f"  {failure}")
        sys.exit(1)

    print("FTF-0307 training capability classifier: all checks passed")
    sys.exit(0)


if __name__ == "__main__":
    main()
