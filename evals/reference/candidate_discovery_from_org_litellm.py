"""Candidate discovery from org LiteLLM (FTF-1706, Section 0.10-0.12/0.21/54-55/66/68-69/77/81).

Section 0.10/0.11 require that the product discover the models the
configured LiteLLM credential may actually use and never assume every
alias is fine-tunable; Section 0.21 requires comparing existing, untuned
models before training anything. This module turns the organization's own
reconciled alias set into a candidate list, verifying per model the
dimensions the task's acceptance clause names: artifact availability,
license, training-method availability and privacy.

It is a pure, no-I/O composer over already-CERTIFIED evidence: FTF-0207's
`AliasReconciliationResult` (the only source of model ids -- nothing is
hardcoded or preferred), FTF-0307's `classify_training_capability`
(capability from a ready, digest-verified `TrainableArtifact` or a
model-bound, route-verified probe, never from a name), FTF-H005's
`RouteAttestationResult` (route identity) and FTF-0402's security profile
ordering. Evidence is supplied by the caller keyed by model id; this
module never looks anything up by alias name.

Rules, each pinned by a test:
- A model is `tunable` only with a ready artifact whose weights digest
  resolved, an accepted license that is on the organization's approved
  list, a declared GPU requirement that fits the available VRAM, and
  privacy availability. Anything else is `blocked` with stable reason
  codes; `capability_unknown` is blocked, never defaulted to tunable.
- An inference-only endpoint is an `untuned_comparator` (Section 0.12: it
  can still be the baseline); it makes no artifact, license or method
  claim, and is never reported as tunable.
- Privacy: `standard` needs nothing extra. `zero_egress` and `air_gapped`
  need an attestation with state ATTESTED, verified route identity and a
  resolved model equal to the candidate (Section 3.5; an unknown upstream
  identity cannot claim private qualification). `air_gapped` additionally
  refuses an artifact sourced from `huggingface`, a public registry that
  an offline lifecycle cannot reach.
- Method availability uses only the artifact's own declared
  `min_gpu_vram_gb`; no training recipe is inferred. FTF-0307 disclosed
  that richer method facts have no backing computation yet, so none is
  claimed here.

No database, network or schema change (`database_changes` clause); roles
pass through from the caller and are never inferred.
"""

from __future__ import annotations

import math
import sys
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import cast

_ROOT = Path(__file__).resolve().parent.parent.parent
_API_ROOT = _ROOT / "apps" / "api"
for _path in (str(_ROOT), str(_API_ROOT)):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from packages.litellm_client.alias_reconciliation import (  # noqa: E402
    AliasReconciliationResult,
)
from packages.litellm_client.last_known_good_catalog import (  # noqa: E402
    DEFAULT_FRESHNESS_TTL,
    AccessState,
)
from packages.litellm_client.periodic_sync_workflow import (  # noqa: E402
    SyncCycleResult,
    SyncCycleStatus,
)
from packages.litellm_client.real_tool_call_probe import ToolCallProbeResult  # noqa: E402
from packages.litellm_client.route_attestation import (  # noqa: E402
    RouteAttestationResult,
    RouteAttestationState,
    RouteIdentityState,
)

from app.artifacts.artifact_schema import TrainableArtifact  # noqa: E402
from app.artifacts.training_capability_classifier import (  # noqa: E402
    TrainingCapability,
    classify_training_capability,
)
from app.services.project_security_profile import (  # noqa: E402
    security_profile_rank,
    validate_security_profile_name,
)

__all__ = [
    "CandidateDiscoveryError",
    "CandidateDiscoveryResult",
    "CandidateKind",
    "CandidateVerdict",
    "GatewayState",
    "LicenseState",
    "MethodState",
    "PrivacyState",
    "ScopedEvidence",
    "discover_candidates",
]

_ZERO_EGRESS_RANK = security_profile_rank("zero_egress")
_AIR_GAPPED_RANK = security_profile_rank("air_gapped")


class CandidateDiscoveryError(ValueError):
    """A caller-side contract violation (malformed input), never a model outcome."""


class CandidateKind(str, Enum):
    TUNABLE = "tunable"
    UNTUNED_COMPARATOR = "untuned_comparator"
    BLOCKED = "blocked"


class LicenseState(str, Enum):
    APPROVED = "approved"
    NOT_ACCEPTED = "not_accepted"
    NOT_APPROVED = "not_approved"
    NOT_APPLICABLE = "not_applicable"


class MethodState(str, Enum):
    AVAILABLE = "available"
    GPU_REQUIREMENT_UNDECLARED = "gpu_requirement_undeclared"
    GPU_BUDGET_UNSPECIFIED = "gpu_budget_unspecified"
    GPU_INSUFFICIENT = "gpu_insufficient"
    NOT_APPLICABLE = "not_applicable"


class PrivacyState(str, Enum):
    AVAILABLE = "available"
    UNVERIFIED = "unverified"
    ARTIFACT_SOURCE_NOT_ALLOWED = "artifact_source_not_allowed"


class GatewayState(str, Enum):
    """Law 17: whether the org gateway catalog behind a discovery can be
    trusted. Only DISCOVERED carries candidates; every other value is a
    visible non-success, never an empty organisation."""

    DISCOVERED = "discovered"
    SYNC_NOT_APPLIED = "sync_not_applied"
    NO_SUCCESSFUL_DISCOVERY = "no_successful_discovery"
    ACCESS_UNKNOWN = "access_unknown"
    ACCESS_REVOKED = "access_revoked"
    ACCESS_DEGRADED = "access_degraded"
    CATALOG_STALE = "catalog_stale"


@dataclass(frozen=True)
class ScopedEvidence:
    """Evidence together with the tenant and gateway connection it came from.

    Reconciliations, sync cycles and attestations carry no organization of
    their own, so the caller states the scope and discovery refuses any
    that differs from the organization and connection being discovered.
    """

    organization_id: uuid.UUID
    connection_id: uuid.UUID
    value: object


@dataclass(frozen=True)
class CandidateVerdict:
    model_id: str
    kind: CandidateKind
    capability: TrainingCapability
    artifact_id: str | None
    license: LicenseState
    method: MethodState
    privacy: PrivacyState
    role: str | None
    blocked_reasons: tuple[str, ...]


@dataclass(frozen=True)
class CandidateDiscoveryResult:
    state: GatewayState
    organization_id: uuid.UUID
    security_profile: str
    model_count: int
    tunable_count: int
    comparator_count: int
    blocked_count: int
    candidates: tuple[CandidateVerdict, ...]
    discovered_at: datetime

    @property
    def succeeded(self) -> bool:
        return self.state is GatewayState.DISCOVERED


def _as_non_negative(value: object) -> float | None:
    """The value as a float, or None for a bool, a non-number, NaN/inf or a negative."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    if not math.isfinite(value) or value < 0:
        return None
    return float(value)


def _scoped(
    evidence: object, label: str, organization_id: uuid.UUID, connection_id: uuid.UUID
) -> object:
    """The evidence's value, refusing any from another organization or connection."""
    if not isinstance(evidence, ScopedEvidence):
        raise CandidateDiscoveryError(f"{label} must be ScopedEvidence")
    if evidence.organization_id != organization_id:
        raise CandidateDiscoveryError(f"{label} belongs to another organization")
    if evidence.connection_id != connection_id:
        raise CandidateDiscoveryError(f"{label} belongs to another gateway connection")
    return evidence.value


def _mapping_of(value: object, label: str, item_type: type) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise CandidateDiscoveryError(f"{label} must be a mapping")
    for key, item in value.items():
        if not isinstance(key, str):
            raise CandidateDiscoveryError(f"{label} keys must be strings")
        if not isinstance(item, item_type):
            raise CandidateDiscoveryError(f"{label} values must be {item_type.__name__}")
    return value


def _validate_inputs(
    organization_id: object,
    connection_id: object,
    approved_license_ids: object,
    available_gpu_vram_gb: object,
) -> None:
    if not isinstance(organization_id, uuid.UUID):
        raise CandidateDiscoveryError("organization_id must be a real uuid.UUID")
    if not isinstance(connection_id, uuid.UUID):
        raise CandidateDiscoveryError("connection_id must be a real uuid.UUID")
    if not isinstance(approved_license_ids, frozenset) or not all(
        isinstance(item, str) for item in approved_license_ids
    ):
        raise CandidateDiscoveryError("approved_license_ids must be a frozenset of strings")
    if available_gpu_vram_gb is not None and _as_non_negative(available_gpu_vram_gb) is None:
        raise CandidateDiscoveryError(
            "available_gpu_vram_gb must be a finite non-negative number or None"
        )


def _gateway_state(sync_cycle: SyncCycleResult, now: datetime) -> GatewayState:
    """DISCOVERED only for an applied cycle over a healthy, fresh catalog."""
    snapshot = sync_cycle.snapshot
    if sync_cycle.status is not SyncCycleStatus.APPLIED or snapshot is None:
        return GatewayState.SYNC_NOT_APPLIED
    if snapshot.access_state is AccessState.REVOKED:
        return GatewayState.ACCESS_REVOKED
    if snapshot.access_state is AccessState.DEGRADED:
        return GatewayState.ACCESS_DEGRADED
    if snapshot.access_state is not AccessState.HEALTHY:
        return GatewayState.ACCESS_UNKNOWN
    if snapshot.last_successful_check_at is None:
        return GatewayState.NO_SUCCESSFUL_DISCOVERY
    if now - snapshot.last_successful_check_at > DEFAULT_FRESHNESS_TTL:
        return GatewayState.CATALOG_STALE
    return GatewayState.DISCOVERED


def _privacy_attested(attestation: RouteAttestationResult | None, model_id: str) -> bool:
    return (
        attestation is not None
        and attestation.state is RouteAttestationState.ATTESTED
        and attestation.route_identity_state is RouteIdentityState.VERIFIED
        and attestation.resolved_model == model_id
    )


def discover_candidates(
    *,
    organization_id: uuid.UUID,
    connection_id: uuid.UUID,
    sync_cycle: ScopedEvidence,
    reconciliation: ScopedEvidence,
    artifacts_by_model: Mapping[str, TrainableArtifact],
    probes_by_model: Mapping[str, ToolCallProbeResult],
    attestations_by_model: Mapping[str, ScopedEvidence],
    security_profile: str,
    approved_license_ids: frozenset[str],
    available_gpu_vram_gb: float | None,
    assigned_roles: Mapping[str, str],
    now: datetime | None = None,
) -> CandidateDiscoveryResult:
    """Verdicts for every model in `reconciliation`, ordered by model id.

    Never raises for a model-level outcome (those are `blocked` verdicts
    with reason codes); raises `CandidateDiscoveryError` for malformed
    input or evidence scoped to another organization/connection, and lets
    `classify_training_capability` raise for a cross-organization artifact
    (a caller contract violation). A gateway that is not a healthy, fresh,
    applied catalog returns a non-DISCOVERED `state` with no candidates
    (Law 17: never an empty organisation).
    """
    _validate_inputs(organization_id, connection_id, approved_license_ids, available_gpu_vram_gb)
    profile = validate_security_profile_name(security_profile)
    profile_rank = security_profile_rank(profile)
    discovered_at = now if now is not None else datetime.now(UTC)

    cycle = _scoped(sync_cycle, "sync_cycle", organization_id, connection_id)
    if not isinstance(cycle, SyncCycleResult):
        raise CandidateDiscoveryError("sync_cycle must wrap a SyncCycleResult")
    recon_value = _scoped(reconciliation, "reconciliation", organization_id, connection_id)
    if not isinstance(recon_value, AliasReconciliationResult):
        raise CandidateDiscoveryError("reconciliation must wrap an AliasReconciliationResult")
    artifacts = cast(
        Mapping[str, TrainableArtifact],
        _mapping_of(artifacts_by_model, "artifacts_by_model", TrainableArtifact),
    )
    probes = cast(
        Mapping[str, ToolCallProbeResult],
        _mapping_of(probes_by_model, "probes_by_model", ToolCallProbeResult),
    )
    if not isinstance(attestations_by_model, Mapping):
        raise CandidateDiscoveryError("attestations_by_model must be a mapping")
    attestations: dict[str, RouteAttestationResult] = {}
    for key, evidence in attestations_by_model.items():
        if not isinstance(key, str):
            raise CandidateDiscoveryError("attestations_by_model keys must be strings")
        value = _scoped(evidence, "attestation", organization_id, connection_id)
        if not isinstance(value, RouteAttestationResult):
            raise CandidateDiscoveryError("attestation must wrap a RouteAttestationResult")
        attestations[key] = value
    if not isinstance(assigned_roles, Mapping) or not all(
        isinstance(k, str) and isinstance(v, str) for k, v in assigned_roles.items()
    ):
        raise CandidateDiscoveryError("assigned_roles must map string model ids to string roles")

    gateway_state = _gateway_state(cycle, discovered_at)
    if gateway_state is not GatewayState.DISCOVERED:
        return CandidateDiscoveryResult(
            state=gateway_state,
            organization_id=organization_id,
            security_profile=profile,
            model_count=0,
            tunable_count=0,
            comparator_count=0,
            blocked_count=0,
            candidates=(),
            discovered_at=discovered_at,
        )

    reconciliation_models = recon_value.models
    verdicts: list[CandidateVerdict] = []
    for reconciled in sorted(reconciliation_models, key=lambda m: m.id):
        model_id = reconciled.id
        artifact = artifacts.get(model_id)
        classification = classify_training_capability(
            model_id=model_id,
            organization_id=organization_id,
            reconciliation=recon_value,
            probe=probes.get(model_id),
            artifact=artifact,
            assigned_role=assigned_roles.get(model_id),
        )
        capability = classification.capability
        reasons: list[str] = []
        license_state = LicenseState.NOT_APPLICABLE
        method_state = MethodState.NOT_APPLICABLE

        if capability is TrainingCapability.TRAINABLE_OPEN_WEIGHT:
            assert artifact is not None
            if artifact.license_accepted_at is None:
                license_state = LicenseState.NOT_ACCEPTED
                reasons.append("license_not_accepted")
            elif not isinstance(artifact.license_id, str):
                raise CandidateDiscoveryError("artifact license_id must be a string")
            elif artifact.license_id not in approved_license_ids:
                license_state = LicenseState.NOT_APPROVED
                reasons.append("license_not_approved")
            else:
                license_state = LicenseState.APPROVED

            profile_json = artifact.minimum_gpu_profile_json
            required = (
                profile_json.get("min_gpu_vram_gb") if isinstance(profile_json, dict) else None
            )
            required_gb = _as_non_negative(required)
            if required_gb is None:
                method_state = MethodState.GPU_REQUIREMENT_UNDECLARED
                reasons.append("gpu_requirement_undeclared")
            elif available_gpu_vram_gb is None:
                method_state = MethodState.GPU_BUDGET_UNSPECIFIED
                reasons.append("gpu_budget_unspecified")
            elif required_gb > available_gpu_vram_gb:
                method_state = MethodState.GPU_INSUFFICIENT
                reasons.append("gpu_insufficient")
            else:
                method_state = MethodState.AVAILABLE
        elif capability is not TrainingCapability.INFERENCE_ONLY:
            reasons.append("capability_unknown")

        privacy_state = PrivacyState.AVAILABLE
        if capability is not TrainingCapability.UNKNOWN and profile_rank >= _ZERO_EGRESS_RANK:
            if not _privacy_attested(attestations.get(model_id), model_id):
                privacy_state = PrivacyState.UNVERIFIED
                reasons.append("route_identity_unverified")
            elif (
                profile_rank >= _AIR_GAPPED_RANK
                and capability is TrainingCapability.TRAINABLE_OPEN_WEIGHT
                and artifact is not None
                and artifact.source_type == "huggingface"
            ):
                privacy_state = PrivacyState.ARTIFACT_SOURCE_NOT_ALLOWED
                reasons.append("artifact_source_not_air_gapped")

        if reasons:
            kind = CandidateKind.BLOCKED
        elif capability is TrainingCapability.TRAINABLE_OPEN_WEIGHT:
            kind = CandidateKind.TUNABLE
        else:
            kind = CandidateKind.UNTUNED_COMPARATOR

        verdicts.append(
            CandidateVerdict(
                model_id=model_id,
                kind=kind,
                capability=capability,
                artifact_id=classification.artifact_id,
                license=license_state,
                method=method_state,
                privacy=privacy_state,
                role=classification.role,
                blocked_reasons=tuple(reasons),
            )
        )

    return CandidateDiscoveryResult(
        state=GatewayState.DISCOVERED,
        organization_id=organization_id,
        security_profile=profile,
        model_count=len(verdicts),
        tunable_count=sum(v.kind is CandidateKind.TUNABLE for v in verdicts),
        comparator_count=sum(v.kind is CandidateKind.UNTUNED_COMPARATOR for v in verdicts),
        blocked_count=sum(v.kind is CandidateKind.BLOCKED for v in verdicts),
        candidates=tuple(verdicts),
        discovered_at=discovered_at,
    )
