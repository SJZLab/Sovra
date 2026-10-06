"""Training capability classifier (FTF-0307, Section 6.6/6.7/13/34/68/77/81).

Section 0.12's own rule -- "Model capability, availability, and project role
are separate dimensions... An endpoint can be inference-only and still be the
production baseline. A teacher role does not determine trainability." -- has
no single place in this repository where a caller can resolve all of a given
model alias's own capability/access/artifact-availability/role facts from one
call. This task's own acceptance clause ("Classify access, training
capability, artifact availability and role independently with dated probe
evidence") is exactly that: a pure, no-I/O composer over four already-
CERTIFIED sibling tasks' own real outputs --

- FTF-0207's `AliasReconciliationResult` (is this `model_id` currently in the
  organization's own real, authorized, reconciled alias set at all -- the
  `access` dimension);
- FTF-0212's `ToolCallProbeResult` (a real, dated, bounded tool-call probe
  round trip against the gateway -- the only real signal this task's own
  dependency set carries for "this endpoint genuinely answers real inference
  calls", used for `TrainingCapability.INFERENCE_ONLY`);
- FTF-0301's `TrainableArtifact` row (the real registration/license/status
  record -- the `artifact_availability`/`artifact_id`/`license_approved`
  facts); and
- FTF-0306's own resolved-digest discipline, which is *why* this task trusts
  `TrainableArtifact.status == "ready"` as real evidence of trainability at
  all (reaching `ready` already requires a verified weights digest -- see
  `artifact_crud.py`'s own `apply_mark_ready` gate).

Path refined from the task packet's own `packages/model-catalog/...` (not a
uv workspace member, not a valid Python import path) to
`apps/api/app/artifacts/`, the same refinement precedent FTF-0301/FTF-0304/
FTF-0305/FTF-0306 already established for this exact contract owner
(`contract_owner: FTF-0301`).

Each of the four dimensions this module returns is computed from its own
independent evidence source and never gates another -- reapplying Section
0.12's own orthogonality rule as this module's own defense-in-depth
boundary, the same "independently, not derived from each other" discipline
`alias_reconciliation.py` (FTF-0207) already applied to its own join. In
particular: `access` never influences `capability` (a model can be fully
trainable-evidenced via a real artifact while *also*, independently and
simultaneously, no longer appearing in the current gateway discovery --
both facts are reported, neither is suppressed by the other), and `role` is
always a pure passthrough of whatever the caller already explicitly assigned
elsewhere (Section 0.12: "explicitly assigned role") -- this module never
computes, infers or defaults it from any other field.

This task's own negative_acceptance ("Inference-only endpoint cannot become
trainable by editing name; unknown capability remains blocked") is enforced
by construction, not a text/name heuristic: `classify_training_capability`
never accepts, reads or compares any display-name/alias-string field when
deciding `capability` -- the sole inputs to that decision are
`artifact.status` (a real, already-validated enum column; `registered`/
`archived` never qualify, only `ready` does) and `probe.state` (a real probe
outcome enum; only the exact `ToolCallProbeState.PROBED` state qualifies,
every failure/rejection/timeout state does not). A model with no real
`ready` artifact and no real `PROBED` probe result always falls through to
`TrainingCapability.UNKNOWN` -- the fail-closed default this task's own
"unknown capability remains blocked" clause requires (a later, not-yet-built
training-wizard-entry gate is the actual enforcement point for "no model may
enter the training wizard without a resolved capability record", Section 13
-- this task's own job is only to never hand that gate a fabricated
non-`UNKNOWN` value).

Scope decision, disclosed (same class of narrowing FTF-0301/FTF-1001's own
docstrings already applied to their neighboring sections): Section 13's own
fuller `TrainingCapability` registry schema also names `method_support`,
`recommended_precision`, `quantization_support`, `min_gpu_vram_gb`,
`recommended_gpu_vram_gb`, `supports_gradient_checkpointing`,
`supports_flash_attention` and `zero_egress_compatible` -- real Axolotl/
hardware-profile facts this task's own dependency set (FTF-0207/FTF-0212/
FTF-0301/FTF-0306) carries no signal for at all. Declaring any of those
here without a real backing computation would be exactly the "placeholder
endpoint" Law 11 forbids; they are a separate, not-yet-dependency-gated
task's own job. `TrainingCapability.MANAGED_FINE_TUNABLE` is declared for
Section 0.12's own enum completeness but is never returned by this module
for the same reason -- no task in this module's own dependency chain proves
a provider's gateway-managed fine-tuning capability, so claiming it here
would be an unearned classification.

No independent schema change (this task's own `database_changes` clause):
like `reconcile_aliases` (FTF-0207), `classify_training_capability` is a
pure, synchronous, no-I/O function. It never queries the database itself and
never persists its own result; a caller supplies an already-fetched,
tenant-scoped `TrainableArtifact` (via FTF-0301's own already-CERTIFIED
`artifact_crud.get_artifact`, which already enforces organization
membership) and an already-computed `AliasReconciliationResult`/
`ToolCallProbeResult`. There is today no persisted alias-to-artifact
binding anywhere in this repository's schema (`ModelCatalogEntry.
trainable_artifact_id` is Section 6.6's own future column, not yet a real
migrated table) -- which `TrainableArtifact`, if any, corresponds to a given
`model_id` is therefore an explicit caller-supplied input to this function,
never a name-based or metadata-based lookup this module performs itself.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from enum import Enum

from packages.litellm_client.alias_reconciliation import AliasReconciliationResult
from packages.litellm_client.real_chat_probe import RouteIdentityState
from packages.litellm_client.real_tool_call_probe import ToolCallProbeResult, ToolCallProbeState

from app.artifacts.artifact_schema import ALLOWED_STATUSES, TrainableArtifact

__all__ = [
    "AccessState",
    "ArtifactAvailability",
    "TrainingCapability",
    "TrainingCapabilityClassification",
    "TrainingCapabilityClassificationError",
    "classify_training_capability",
]


class TrainingCapabilityClassificationError(ValueError):
    """`model_id` does not satisfy the required non-empty-string shape."""


class AccessState(str, Enum):
    """Whether `model_id` is currently present in the organization's own
    real, reconciled, authorized alias set -- independent of whether it is
    also trainable (Section 0.12's own orthogonality rule)."""

    GRANTED = "granted"
    NOT_GRANTED = "not_granted"


class TrainingCapability(str, Enum):
    """Section 0.12's own literal four-value enumeration, verbatim."""

    TRAINABLE_OPEN_WEIGHT = "trainable_open_weight"
    MANAGED_FINE_TUNABLE = "managed_fine_tunable"
    INFERENCE_ONLY = "inference_only"
    UNKNOWN = "unknown"


class ArtifactAvailability(str, Enum):
    """A direct, lossless mapping of Section 6.7's own `TrainableArtifact.
    status` enumeration, plus `NONE` for "no artifact is linked at all"."""

    NONE = "none"
    REGISTERED = "registered"
    READY = "ready"
    ARCHIVED = "archived"


_ARTIFACT_STATUS_TO_AVAILABILITY: dict[str, ArtifactAvailability] = {
    "registered": ArtifactAvailability.REGISTERED,
    "ready": ArtifactAvailability.READY,
    "archived": ArtifactAvailability.ARCHIVED,
}
if set(_ARTIFACT_STATUS_TO_AVAILABILITY) != set(ALLOWED_STATUSES):
    raise AssertionError(
        "training_capability_classifier's status map has drifted from "
        "artifact_schema.ALLOWED_STATUSES -- update both together"
    )


@dataclass(frozen=True)
class TrainingCapabilityClassification:
    model_id: str
    access: AccessState
    capability: TrainingCapability
    artifact_id: str | None
    artifact_availability: ArtifactAvailability
    license_approved: bool
    role: str | None
    evidence_probed_at: datetime | None
    detail: str


def classify_training_capability(
    *,
    model_id: str,
    organization_id: uuid.UUID,
    reconciliation: AliasReconciliationResult,
    probe: ToolCallProbeResult | None = None,
    artifact: TrainableArtifact | None = None,
    assigned_role: str | None = None,
) -> TrainingCapabilityClassification:
    """Combine already-resolved evidence into the four independent
    dimensions this task's own acceptance clause names. Never performs a
    network call, a database query, or a name/string-based lookup of its
    own -- every input is already-resolved data the caller supplies.

    Defense in depth (never trusting a single upstream check alone, the
    same discipline FTF-0601's own PATCH-path fix already applied): even
    though `artifact_crud.get_artifact` already tenant-scopes its own
    fetch and `apply_mark_ready` already requires a resolved
    `weights_digest` before `status` can reach `ready`, this module
    re-checks both itself -- a cross-organization `artifact` argument is
    a caller contract violation (raises, like an invalid `model_id`), and
    a `ready` artifact with no resolved `weights_digest` never classifies
    as trainable. Probe evidence is bound to `model_id` with a verified
    route identity (Section 3.5) before it can prove `inference_only` --
    a probe answer for a different model, or one whose identity the
    gateway's own response never confirmed, is not evidence for this
    model and fails closed to `unknown`.
    """
    if not isinstance(model_id, str) or not model_id:
        raise TrainingCapabilityClassificationError("model_id must be a non-empty string")
    if not isinstance(organization_id, uuid.UUID):
        raise TrainingCapabilityClassificationError("organization_id must be a real uuid.UUID")
    if artifact is not None and artifact.organization_id != organization_id:
        raise TrainingCapabilityClassificationError(
            "artifact does not belong to the caller-supplied organization_id -- "
            "classify_training_capability never classifies a cross-organization artifact"
        )

    access = AccessState.NOT_GRANTED
    for reconciled in reconciliation.models:
        if reconciled.id == model_id:
            access = AccessState.GRANTED
            break

    if artifact is None:
        artifact_id: str | None = None
        artifact_availability = ArtifactAvailability.NONE
        license_approved = False
    else:
        artifact_id = str(artifact.id) if artifact.id is not None else None
        artifact_availability = _ARTIFACT_STATUS_TO_AVAILABILITY.get(
            artifact.status, ArtifactAvailability.NONE
        )
        license_approved = artifact.license_accepted_at is not None

    evidence_probed_at = probe.probed_at if probe is not None else None

    if (
        artifact is not None
        and artifact_availability is ArtifactAvailability.READY
        and artifact.weights_digest is not None
    ):
        capability = TrainingCapability.TRAINABLE_OPEN_WEIGHT
        detail = (
            "backed by a ready TrainableArtifact with a resolved weights_digest "
            "(full_weight/adapter)"
        )
    elif (
        probe is not None
        and probe.state is ToolCallProbeState.PROBED
        and probe.resolved_model == model_id
        and probe.route_identity_state is RouteIdentityState.VERIFIED
    ):
        capability = TrainingCapability.INFERENCE_ONLY
        detail = (
            "no ready, digest-verified artifact; a real dated probe bound to this model_id with "
            "a verified route identity confirms the endpoint answers inference calls"
        )
    else:
        capability = TrainingCapability.UNKNOWN
        detail = (
            "no ready digest-verified artifact and no model-bound, route-verified real probe "
            "evidence -- fails closed"
        )

    return TrainingCapabilityClassification(
        model_id=model_id,
        access=access,
        capability=capability,
        artifact_id=artifact_id,
        artifact_availability=artifact_availability,
        license_approved=license_approved,
        role=assigned_role,
        evidence_probed_at=evidence_probed_at,
        detail=detail,
    )
