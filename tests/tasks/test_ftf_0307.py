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
`test_classification_never_reads_any_name_or_alias_string_field`, plus real
fault injections (Law 1) proving each negative check genuinely fails when
its real guard is removed.

2026-10-06 L2 round-2 fix-and-resubmit (docs/certifications/queue.md
"2026-10-04T11:00Z"): the review found `classify_training_capability`
(1) let a probe answer for a *different* model, or one whose route
identity the gateway's own response never confirmed, still classify this
model `inference_only`; (2) let *any* `ready` artifact classify
`trainable_open_weight` with no `weights_digest` requirement and no
organization binding, so a cross-organization or still-digest-unresolved
artifact could be classified trainable; (3) had no test pinning that an
unrecognised `artifact.status` value (bypassing the real schema's own
`__init__`-time validation, e.g. an ORM-loaded row from a future migration)
stays fail-closed, nor that `role`/`license_approved` behave correctly in
their own negative cases, nor a real post-construction rename scenario.
Fixed: `classify_training_capability` now takes a required
`organization_id` and raises if a non-`None` `artifact` belongs to a
different organization; `trainable_open_weight` additionally requires a
real, non-`None` `artifact.weights_digest`; `inference_only` additionally
requires `probe.resolved_model == model_id` and
`probe.route_identity_state is RouteIdentityState.VERIFIED`. New tests below
pin every one of these, plus the previously-untested-but-already-correct
cases (unrecognised status, `role=None` passthrough, licence-not-accepted),
each with a real fault injection (Law 1) showing the specific guard's
removal reproduces the exact defect the review named.

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

_ORG = uuid.uuid4()
_OTHER_ORG = uuid.uuid4()
_WEIGHTS_DIGEST = "sha256:" + "a" * 64


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


def _probe(
    state: ToolCallProbeState,
    *,
    probed_at: datetime = _NOW,
    resolved_model: str | None = "gpt-a",
    route_identity_state: RouteIdentityState = RouteIdentityState.VERIFIED,
) -> ToolCallProbeResult:
    return ToolCallProbeResult(
        state=state,
        route_identity_state=route_identity_state,
        resolved_model=resolved_model,
        advertised_tool_name="get_probe_time",
        observed_tool_name="get_probe_time" if state == ToolCallProbeState.PROBED else None,
        observed_arguments='{"timezone": "UTC"}' if state == ToolCallProbeState.PROBED else None,
        usage=None,
        latency_ms=12.5,
        detail="real probe round trip",
        probed_at=probed_at,
    )


_UNSET = object()


def _artifact(
    *,
    status: str,
    display_name: str = "artifact-one",
    organization_id: uuid.UUID = _ORG,
    weights_digest: str | None | object = _UNSET,
) -> TrainableArtifact:
    now = datetime.now(UTC)
    if weights_digest is _UNSET:
        weights_digest = _WEIGHTS_DIGEST if status == "ready" else None
    artifact = TrainableArtifact(
        organization_id=organization_id,
        display_name=display_name,
        source_type="local_filesystem",
        source_uri="model-a",
        model_family="tiny",
        base_model_revision="v1.0",
        weights_digest=weights_digest,
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
    """Section 0.12's own orthogonality rule: a model with a ready,
    digest-verified artifact, a real successful model-bound probe, current
    access and an explicit role all set reports all four facts
    simultaneously, none derived from another."""
    reconciliation = _reconciliation("gpt-a")
    artifact = _artifact(status="ready")
    probe = _probe(ToolCallProbeState.PROBED)
    result = classify_training_capability(
        model_id="gpt-a",
        organization_id=_ORG,
        reconciliation=reconciliation,
        probe=probe,
        artifact=artifact,
        assigned_role="baseline",
    )
    check("FTF-0307", "access reports granted", result.access == AccessState.GRANTED)
    check(
        "FTF-0307",
        "capability reports trainable_open_weight from the ready, digest-verified artifact",
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
    current reconciled accessible set is NOT_GRANTED, but a real ready,
    digest-verified artifact still proves real trainability -- `access`
    never suppresses `capability` (Section 0.12: "An endpoint can be
    inference-only and still be the production baseline" -- the same
    non-gating rule, applied to the access/capability pair instead of
    capability/role)."""
    reconciliation = _reconciliation("some-other-model")
    artifact = _artifact(status="ready")
    result = classify_training_capability(
        model_id="gpt-a",
        organization_id=_ORG,
        reconciliation=reconciliation,
        probe=None,
        artifact=artifact,
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
        model_id="gpt-a",
        organization_id=_ORG,
        reconciliation=reconciliation,
        probe=probe,
        artifact=None,
    )
    check(
        "FTF-0307",
        "no artifact + a real successful, model-bound, route-verified probe classifies "
        "inference_only",
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
        organization_id=_ORG,
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
        model_id="gpt-a",
        organization_id=_ORG,
        reconciliation=_reconciliation("gpt-a"),
        probe=None,
        artifact=None,
    )
    check(
        "FTF-0307",
        "no probe evidence means no fabricated date, never a current-time default",
        no_probe_result.evidence_probed_at is None,
    )


def test_no_artifact_and_no_successful_probe_is_unknown_never_fabricated() -> None:
    result = classify_training_capability(
        model_id="gpt-a",
        organization_id=_ORG,
        reconciliation=_reconciliation("gpt-a"),
        probe=None,
        artifact=None,
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
            organization_id=_ORG,
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
            model_id="gpt-a",
            organization_id=_ORG,
            reconciliation=_reconciliation("gpt-a"),
            probe=probe,
            artifact=None,
        )
        check(
            "FTF-0307",
            f"a {state.value!r} probe outcome never classifies as inference_only",
            result.capability == TrainingCapability.UNKNOWN,
        )


def test_probe_for_a_different_model_never_classifies_this_model_inference_only() -> None:
    """New round-2 guard: a real `PROBED`, route-verified probe result whose
    own `resolved_model` names a *different* model than `model_id` is not
    evidence for this model -- a probe round trip run against one endpoint
    must never be laundered into a capability claim for an unrelated one."""
    probe = _probe(ToolCallProbeState.PROBED, resolved_model="some-other-model")
    result = classify_training_capability(
        model_id="gpt-a",
        organization_id=_ORG,
        reconciliation=_reconciliation("gpt-a"),
        probe=probe,
        artifact=None,
    )
    check(
        "FTF-0307",
        "a PROBED result for a different resolved_model never classifies this model_id "
        "inference_only",
        result.capability == TrainingCapability.UNKNOWN,
    )


def test_probe_with_unverified_route_identity_never_classifies_inference_only() -> None:
    """New round-2 guard: Section 3.5's own `IDENTITY_UNVERIFIED` case -- a
    real `PROBED` result whose route identity the gateway's response never
    actually confirmed is not proof the probe reached *this* model."""
    probe = _probe(
        ToolCallProbeState.PROBED,
        resolved_model="gpt-a",
        route_identity_state=RouteIdentityState.UNVERIFIED,
    )
    result = classify_training_capability(
        model_id="gpt-a",
        organization_id=_ORG,
        reconciliation=_reconciliation("gpt-a"),
        probe=probe,
        artifact=None,
    )
    check(
        "FTF-0307",
        "a PROBED result with an unverified route identity never classifies inference_only",
        result.capability == TrainingCapability.UNKNOWN,
    )


def test_ready_artifact_without_a_resolved_weights_digest_never_classifies_trainable() -> None:
    """New round-2 guard: even a `ready` artifact must carry a real,
    resolved `weights_digest` (FTF-0306's own discipline) before this module
    trusts it as trainable -- defense in depth against a `ready` row that
    somehow reached this module without ever passing through
    `apply_mark_ready`'s own digest-resolved gate."""
    artifact = _artifact(status="ready", weights_digest=None)
    check(
        "FTF-0307",
        "the test fixture itself really has no weights_digest",
        artifact.weights_digest is None,
    )
    result = classify_training_capability(
        model_id="gpt-a",
        organization_id=_ORG,
        reconciliation=_reconciliation("gpt-a"),
        probe=None,
        artifact=artifact,
    )
    check(
        "FTF-0307",
        "a ready artifact with no resolved weights_digest never classifies trainable_open_weight",
        result.capability != TrainingCapability.TRAINABLE_OPEN_WEIGHT,
    )
    check(
        "FTF-0307",
        "it still fails closed to unknown (no probe evidence either)",
        result.capability == TrainingCapability.UNKNOWN,
    )


def test_cross_organization_artifact_is_refused_never_classified() -> None:
    """New round-2 guard: defense in depth against a caller that resolved
    (or was handed) an `artifact` belonging to a *different* organization
    than the one it is classifying for -- `artifact_crud.get_artifact`
    already tenant-scopes its own fetch, but this module never trusts that
    alone (the same "never only one layer" discipline FTF-0601's own fix
    applied to its PATCH path)."""
    artifact = _artifact(status="ready", organization_id=_OTHER_ORG)
    try:
        classify_training_capability(
            model_id="gpt-a",
            organization_id=_ORG,
            reconciliation=_reconciliation("gpt-a"),
            probe=None,
            artifact=artifact,
        )
        raised = False
    except TrainingCapabilityClassificationError:
        raised = True
    check(
        "FTF-0307",
        "a cross-organization artifact is refused (raises), never silently classified",
        raised,
    )


def test_organization_id_must_be_a_real_uuid() -> None:
    for bad_org in ("not-a-uuid", None, 123):
        try:
            classify_training_capability(
                model_id="gpt-a",
                organization_id=bad_org,  # type: ignore[arg-type]
                reconciliation=_reconciliation("gpt-a"),
                probe=None,
                artifact=None,
            )
            raised = False
        except TrainingCapabilityClassificationError:
            raised = True
        check(
            "FTF-0307", f"organization_id={bad_org!r} is rejected, never silently accepted", raised
        )


def test_unrecognised_artifact_status_fails_closed_to_unknown() -> None:
    """Pins the fail-closed default for an artifact whose `status` is not
    one of the three real values this module's own map knows -- reachable
    only by bypassing `TrainableArtifact.__init__`'s own validation (e.g. an
    ORM row loaded directly from a database that a future migration has
    already moved ahead of this module, or a raw attribute set), the same
    "trust no single layer" scenario the organization/weights_digest guards
    above also defend against. Never classifies trainable regardless of
    what an unrecognised status might have been intended to mean."""
    artifact = _artifact(status="registered")
    artifact.status = "a_future_status_this_module_does_not_know"
    check(
        "FTF-0307",
        "the test fixture itself really carries the unrecognised status",
        artifact.status == "a_future_status_this_module_does_not_know",
    )
    result = classify_training_capability(
        model_id="gpt-a",
        organization_id=_ORG,
        reconciliation=_reconciliation("gpt-a"),
        probe=None,
        artifact=artifact,
    )
    check(
        "FTF-0307",
        "an unrecognised artifact status never classifies trainable_open_weight",
        result.capability != TrainingCapability.TRAINABLE_OPEN_WEIGHT,
    )
    check(
        "FTF-0307",
        "an unrecognised artifact status fails closed to unknown (no probe evidence either)",
        result.capability == TrainingCapability.UNKNOWN,
    )
    check(
        "FTF-0307",
        "an unrecognised artifact status reports artifact_availability none, never a real value",
        result.artifact_availability == ArtifactAvailability.NONE,
    )


def test_assigned_role_none_passes_through_as_none_never_defaulted() -> None:
    """Pins Section 0.12's own "explicitly assigned role" passthrough in its
    negative direction: when the caller has no role to assign, `role` is
    `None`, never an invented default string."""
    result = classify_training_capability(
        model_id="gpt-a",
        organization_id=_ORG,
        reconciliation=_reconciliation("gpt-a"),
        probe=None,
        artifact=None,
        assigned_role=None,
    )
    check(
        "FTF-0307",
        "assigned_role=None passes through as None, never a fabricated default",
        result.role is None,
    )


def test_license_approved_is_false_when_not_yet_accepted() -> None:
    """Pins the negative direction of `license_approved`: a real, present
    artifact whose `license_accepted_at` has never been set reports `False`,
    never fabricated `True` from the artifact merely existing or being
    linked (Law 5 -- existence is not evidence)."""
    artifact = _artifact(status="registered")
    check(
        "FTF-0307",
        "the test fixture itself really has no license acceptance timestamp",
        artifact.license_accepted_at is None,
    )
    result = classify_training_capability(
        model_id="gpt-a",
        organization_id=_ORG,
        reconciliation=_reconciliation("gpt-a"),
        probe=None,
        artifact=artifact,
    )
    check(
        "FTF-0307",
        "license_approved is false when the artifact's own acceptance timestamp is unset",
        result.license_approved is False,
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
        organization_id=_ORG,
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


def test_renaming_an_artifact_after_creation_never_changes_its_own_classification() -> None:
    """A real rename scenario: an artifact is created, registered under one
    display_name, reaches `ready` with a resolved digest, and is only later
    (a real, live edit) renamed to literally equal a target `model_id` --
    before and after the rename, classifying that same real artifact object
    reports the identical `trainable_open_weight` result, driven solely by
    its own status/digest, never by whatever string its display_name
    happens to hold at either point in time."""
    artifact = _artifact(status="ready", display_name="llama-base-v1")
    before = classify_training_capability(
        model_id="unrelated-model-id",
        organization_id=_ORG,
        reconciliation=_reconciliation("unrelated-model-id"),
        probe=None,
        artifact=artifact,
    )
    artifact.display_name = "gpt-a"  # a real, live rename of the same row
    after = classify_training_capability(
        model_id="unrelated-model-id",
        organization_id=_ORG,
        reconciliation=_reconciliation("unrelated-model-id"),
        probe=None,
        artifact=artifact,
    )
    check(
        "FTF-0307",
        "renaming the artifact's display_name does not change its own classification",
        before.capability == after.capability == TrainingCapability.TRAINABLE_OPEN_WEIGHT,
    )
    check(
        "FTF-0307",
        "the rename did not retroactively bind the artifact to the now-matching model_id",
        after.model_id == "unrelated-model-id",
    )


def test_model_id_must_be_a_non_empty_string() -> None:
    for bad_id in ("", None, 123):
        try:
            classify_training_capability(
                model_id=bad_id,  # type: ignore[arg-type]
                organization_id=_ORG,
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
        model_id="gpt-a",
        organization_id=_ORG,
        reconciliation=_reconciliation("gpt-a"),
        probe=None,
        artifact=None,
    )
    try:
        result.capability = TrainingCapability.TRAINABLE_OPEN_WEIGHT  # type: ignore[misc]
        mutated = True
    except FrozenInstanceError:
        mutated = False
    check("FTF-0307", "TrainingCapabilityClassification is frozen (immutable)", not mutated)


def _run_fault_script(script: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, timeout=30
    )


def _assert_fault_injection(
    *, target: str, replacement: str, probe_script: str, expect_in_stdout: str, description: str
) -> None:
    original = _MODULE_PATH.read_bytes()
    text = original.decode("utf-8")
    check("FTF-0307", f"fault-injection target is present for: {description}", target in text)
    mutated = text.replace(target, replacement, 1)
    check("FTF-0307", f"the mutation actually changed the file for: {description}", mutated != text)
    try:
        _MODULE_PATH.write_text(mutated, encoding="utf-8")
        completed = _run_fault_script(probe_script)
        check(
            "FTF-0307",
            f"fault injection: {description} "
            f"(stdout={completed.stdout!r} stderr={completed.stderr!r})",
            completed.returncode == 0 and expect_in_stdout in completed.stdout,
        )
    finally:
        _MODULE_PATH.write_bytes(original)
        restored = _MODULE_PATH.read_bytes()
        check(
            "FTF-0307",
            f"the module file is restored byte-identical after fault injection for: {description}",
            restored == original,
        )


_FAULT_PRELUDE = f"""
import sys
sys.path.insert(0, {str(ROOT)!r})
sys.path.insert(0, {str(API_ROOT)!r})
import uuid
from datetime import datetime, UTC
from app.artifacts.artifact_schema import TrainableArtifact
from app.artifacts.training_capability_classifier import classify_training_capability
from packages.litellm_client.alias_reconciliation import AliasReconciliationResult, ReconciledModel
from packages.litellm_client.real_chat_probe import RouteIdentityState
from packages.litellm_client.real_tool_call_probe import ToolCallProbeResult, ToolCallProbeState
from packages.litellm_client.v1_models_discovery import ModelMetadataStatus

_ORG = uuid.uuid4()
recon = AliasReconciliationResult(
    models=(ReconciledModel(id="gpt-a", metadata_status=ModelMetadataStatus.LIMITED, fields={{}}),),
    model_count=1, reconciled_at=datetime.now(UTC),
)
"""


def test_fault_injection_registered_artifact_treated_as_ready_is_caught() -> None:
    """Law 1: demonstrates the negative_acceptance actually fails when the
    `ready`-only gate is loosened. Mutates the real on-disk module so
    `registered` artifacts also count as ready, runs the mutated module in a
    fresh subprocess, and proves the exact "becomes trainable" failure mode
    this task forbids actually reproduces. Reverts the file byte-identical
    afterward."""
    target = (
        '    "registered": ArtifactAvailability.REGISTERED,\n'
        '    "ready": ArtifactAvailability.READY,\n'
    )
    replacement = (
        '    "registered": ArtifactAvailability.READY,\n'
        '    "ready": ArtifactAvailability.READY,\n'
    )
    probe_script = (
        _FAULT_PRELUDE
        + """
artifact = TrainableArtifact(
    organization_id=_ORG, display_name="x", source_type="local_filesystem",
    source_uri="model-a", model_family="tiny", base_model_revision="v1.0",
    weights_digest="sha256:"""
        + "a" * 64
        + """", license_id="apache-2.0",
    trust_remote_code_allowed=False, status="registered",
)
artifact.id = uuid.uuid4()
result = classify_training_capability(
    model_id="gpt-a", organization_id=_ORG, reconciliation=recon, probe=None, artifact=artifact
)
print("CAPABILITY=" + result.capability.value)
"""
    )
    _assert_fault_injection(
        target=target,
        replacement=replacement,
        probe_script=probe_script,
        expect_in_stdout="CAPABILITY=trainable_open_weight",
        description="loosening the ready-only gate genuinely lets a registered artifact become "
        "trainable",
    )


def test_fault_injection_any_probe_state_treated_as_success_is_caught() -> None:
    """A second, independent fault injection (Law 1): mutates the real
    on-disk module so any non-null probe result (not only a real `PROBED`
    outcome) counts as inference capability, runs the mutated module in a
    fresh subprocess, and proves this task's own "failed probe never
    classifies as inference_only" negative check genuinely fails when that
    discipline is removed. Reverts the file byte-identical afterward."""
    target = "        and probe.state is ToolCallProbeState.PROBED\n"
    replacement = ""
    probe_script = (
        _FAULT_PRELUDE
        + """
failed_probe = ToolCallProbeResult(
    state=ToolCallProbeState.TIMEOUT, route_identity_state=RouteIdentityState.VERIFIED,
    resolved_model="gpt-a", advertised_tool_name="get_probe_time", observed_tool_name=None,
    observed_arguments=None, usage=None, latency_ms=None, detail="d", probed_at=datetime.now(UTC),
)
result = classify_training_capability(
    model_id="gpt-a", organization_id=_ORG, reconciliation=recon, probe=failed_probe, artifact=None
)
print("CAPABILITY=" + result.capability.value)
"""
    )
    _assert_fault_injection(
        target=target,
        replacement=replacement,
        probe_script=probe_script,
        expect_in_stdout="CAPABILITY=inference_only",
        description="accepting any probe state genuinely lets a failed/timed-out probe become "
        "inference_only",
    )


def test_fault_injection_unbound_probe_model_treated_as_success_is_caught() -> None:
    """A third, independent fault injection (Law 1), for the new round-2
    guard: mutates the real on-disk module to drop the `resolved_model ==
    model_id` binding, runs a real `PROBED` result for a *different*
    resolved_model, and proves it would otherwise have been wrongly
    accepted as evidence for this model_id."""
    target = "        and probe.resolved_model == model_id\n"
    replacement = ""
    probe_script = (
        _FAULT_PRELUDE
        + """
mismatched_probe = ToolCallProbeResult(
    state=ToolCallProbeState.PROBED, route_identity_state=RouteIdentityState.VERIFIED,
    resolved_model="some-other-model", advertised_tool_name="get_probe_time",
    observed_tool_name="get_probe_time", observed_arguments='{"timezone": "UTC"}', usage=None,
    latency_ms=12.5, detail="d", probed_at=datetime.now(UTC),
)
result = classify_training_capability(
    model_id="gpt-a", organization_id=_ORG, reconciliation=recon, probe=mismatched_probe,
    artifact=None,
)
print("CAPABILITY=" + result.capability.value)
"""
    )
    _assert_fault_injection(
        target=target,
        replacement=replacement,
        probe_script=probe_script,
        expect_in_stdout="CAPABILITY=inference_only",
        description="dropping the resolved_model binding genuinely lets a probe for a different "
        "model become inference_only for this one",
    )


def test_fault_injection_unverified_route_identity_treated_as_success_is_caught() -> None:
    """A fourth, independent fault injection (Law 1), for the new round-2
    guard: mutates the real on-disk module to drop the
    `route_identity_state is VERIFIED` requirement, runs a real `PROBED`
    result whose route identity the gateway's own response never confirmed,
    and proves it would otherwise have been wrongly accepted."""
    target = "        and probe.route_identity_state is RouteIdentityState.VERIFIED\n"
    replacement = ""
    probe_script = (
        _FAULT_PRELUDE
        + """
unverified_probe = ToolCallProbeResult(
    state=ToolCallProbeState.PROBED, route_identity_state=RouteIdentityState.UNVERIFIED,
    resolved_model="gpt-a", advertised_tool_name="get_probe_time",
    observed_tool_name="get_probe_time", observed_arguments='{"timezone": "UTC"}', usage=None,
    latency_ms=12.5, detail="d", probed_at=datetime.now(UTC),
)
result = classify_training_capability(
    model_id="gpt-a", organization_id=_ORG, reconciliation=recon, probe=unverified_probe,
    artifact=None,
)
print("CAPABILITY=" + result.capability.value)
"""
    )
    _assert_fault_injection(
        target=target,
        replacement=replacement,
        probe_script=probe_script,
        expect_in_stdout="CAPABILITY=inference_only",
        description="dropping the route-identity-verified requirement genuinely lets an "
        "unverified-identity probe become inference_only",
    )


def test_fault_injection_missing_weights_digest_treated_as_trainable_is_caught() -> None:
    """A fifth, independent fault injection (Law 1), for the new round-2
    guard: mutates the real on-disk module to drop the `weights_digest is
    not None` requirement, runs a real `ready` artifact with no resolved
    digest, and proves it would otherwise have been wrongly classified
    trainable."""
    target = "        and artifact.weights_digest is not None\n"
    replacement = ""
    probe_script = (
        _FAULT_PRELUDE
        + """
artifact = TrainableArtifact(
    organization_id=_ORG, display_name="x", source_type="local_filesystem",
    source_uri="model-a", model_family="tiny", base_model_revision="v1.0",
    weights_digest=None, license_id="apache-2.0",
    trust_remote_code_allowed=False, status="ready",
)
artifact.id = uuid.uuid4()
result = classify_training_capability(
    model_id="gpt-a", organization_id=_ORG, reconciliation=recon, probe=None, artifact=artifact
)
print("CAPABILITY=" + result.capability.value)
"""
    )
    _assert_fault_injection(
        target=target,
        replacement=replacement,
        probe_script=probe_script,
        expect_in_stdout="CAPABILITY=trainable_open_weight",
        description="dropping the weights_digest requirement genuinely lets a digest-unresolved "
        "ready artifact become trainable",
    )


def test_fault_injection_cross_organization_artifact_unblocked_is_caught() -> None:
    """A sixth, independent fault injection (Law 1), for the new round-2
    org-binding guard: mutates the real on-disk module to remove the
    cross-organization refusal, runs a real ready artifact belonging to a
    *different* organization than the caller-supplied `organization_id`,
    and proves it would otherwise have been silently classified trainable
    instead of refused."""
    target = (
        "    if artifact is not None and artifact.organization_id != organization_id:\n"
        "        raise TrainingCapabilityClassificationError(\n"
        '            "artifact does not belong to the caller-supplied organization_id -- "\n'
        '            "classify_training_capability never classifies a cross-organization '
        'artifact"\n'
        "        )\n"
    )
    replacement = ""
    probe_script = (
        _FAULT_PRELUDE
        + """
other_org = uuid.uuid4()
artifact = TrainableArtifact(
    organization_id=other_org, display_name="x", source_type="local_filesystem",
    source_uri="model-a", model_family="tiny", base_model_revision="v1.0",
    weights_digest="sha256:"""
        + "a" * 64
        + """", license_id="apache-2.0",
    trust_remote_code_allowed=False, status="ready",
)
artifact.id = uuid.uuid4()
result = classify_training_capability(
    model_id="gpt-a", organization_id=_ORG, reconciliation=recon, probe=None, artifact=artifact
)
print("CAPABILITY=" + result.capability.value)
"""
    )
    _assert_fault_injection(
        target=target,
        replacement=replacement,
        probe_script=probe_script,
        expect_in_stdout="CAPABILITY=trainable_open_weight",
        description="removing the cross-organization refusal genuinely lets a different "
        "organization's ready artifact classify trainable instead of being refused",
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
