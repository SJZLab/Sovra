#!/usr/bin/env python3
"""FTF-1706 -- Candidate discovery from org LiteLLM (no-DB unit/fault tests).

Runnable without Postgres, GPU or network egress: the discovery module is a
pure composer over already-resolved, CERTIFIED evidence (FTF-0207 alias
reconciliation, FTF-0307 capability classification, FTF-H005 route
attestation, FTF-0402 security profile), so the real functions are used
throughout and no collaborator is stubbed.

Acceptance:
  A1 candidates come only from the organization's real reconciled alias set
  A2 artifact / license / method / privacy availability is verified per model
Negative acceptance:
  N1 no hardcoded preferred model; an empty org yields no candidates
  N2 no alias is assumed fine-tunable (by name, metadata or default)

Run directly: python3 tests/reference/test_ftf_1706.py
"""

from __future__ import annotations

import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path
from types import MappingProxyType

ROOT = Path(__file__).resolve().parent.parent.parent
API_ROOT = ROOT / "apps" / "api"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(API_ROOT))

from evals.reference.candidate_discovery_from_org_litellm import (  # noqa: E402
    CandidateDiscoveryError,
    CandidateKind,
    GatewayState,
    LicenseState,
    MethodState,
    PrivacyState,
    ScopedEvidence,
    discover_candidates,
)
from packages.litellm_client.alias_reconciliation import (  # noqa: E402
    AliasReconciliationResult,
    ReconciledModel,
)
from packages.litellm_client.last_known_good_catalog import (  # noqa: E402
    AccessState,
    CatalogSnapshot,
)
from packages.litellm_client.periodic_sync_workflow import (  # noqa: E402
    SyncCycleResult,
    SyncCycleStatus,
)
from packages.litellm_client.real_chat_probe import RouteIdentityState  # noqa: E402
from packages.litellm_client.real_tool_call_probe import (  # noqa: E402
    ToolCallProbeResult,
    ToolCallProbeState,
)
from packages.litellm_client.route_attestation import (  # noqa: E402
    RouteAttestationResult,
    RouteAttestationState,
)
from packages.litellm_client.route_attestation import (  # noqa: E402
    RouteIdentityState as AttestedIdentityState,
)
from packages.litellm_client.v1_models_discovery import ModelMetadataStatus  # noqa: E402

from app.artifacts.artifact_schema import TrainableArtifact  # noqa: E402

FAILURES: list[str] = []
NOW = datetime(2026, 10, 8, tzinfo=UTC)
ORG = uuid.uuid4()
DIGEST = "sha256:" + "a" * 64


def check(criterion_id: str, description: str, condition: bool) -> None:
    if not condition:
        FAILURES.append(f"FAIL [{criterion_id}] {description}")


def _raises(exc_type: type[BaseException], fn, *args: object, **kwargs: object) -> bool:
    try:
        fn(*args, **kwargs)
        return False
    except exc_type:
        return True


def recon(*ids: str) -> AliasReconciliationResult:
    models = tuple(
        ReconciledModel(
            id=i, metadata_status=ModelMetadataStatus.LIMITED, fields=MappingProxyType({})
        )
        for i in ids
    )
    return AliasReconciliationResult(models=models, model_count=len(models), reconciled_at=NOW)


def artifact(
    *,
    status: str = "ready",
    digest: str | None = DIGEST,
    license_id: str = "apache-2.0",
    accepted: bool = True,
    source_type: str = "private_s3",
    gpu: dict[str, object] | None = None,
    org: uuid.UUID = ORG,
) -> TrainableArtifact:
    return TrainableArtifact(
        id=uuid.uuid4(),
        organization_id=org,
        display_name="a",
        source_type=source_type,
        source_uri="org/repo" if source_type == "huggingface" else "s3://my-bucket/models/m1",
        model_family="f",
        base_model_revision="0" * 40,
        weights_digest=digest,
        license_id=license_id,
        license_accepted_by="owner" if accepted else None,
        license_accepted_at=NOW if accepted else None,
        trust_remote_code_allowed=False,
        required_python_packages_json=[],
        minimum_gpu_profile_json={"min_gpu_vram_gb": 24} if gpu is None else gpu,
        status=status,
    )


def probe(
    model: str, *, state: ToolCallProbeState = ToolCallProbeState.PROBED
) -> ToolCallProbeResult:
    return ToolCallProbeResult(
        state=state,
        route_identity_state=RouteIdentityState.VERIFIED,
        resolved_model=model,
        advertised_tool_name="t",
        observed_tool_name="t",
        observed_arguments="{}",
        usage=None,
        latency_ms=1.0,
        detail="d",
        probed_at=NOW,
    )


def attestation(
    model: str,
    *,
    state: RouteAttestationState = RouteAttestationState.ATTESTED,
    identity: AttestedIdentityState = AttestedIdentityState.VERIFIED,
    resolved: str | None = None,
) -> RouteAttestationResult:
    return RouteAttestationResult(
        state=state,
        route_identity_state=identity,
        resolved_model=resolved if resolved is not None else model,
        deployment_fingerprint="fp",
        latency_ms=1.0,
        detail="d",
        attested_at=NOW,
    )


CONN = uuid.uuid4()


def scoped(value, *, org=None, conn=None):
    return ScopedEvidence(
        organization_id=org if org is not None else ORG,
        connection_id=conn if conn is not None else CONN,
        value=value,
    )


def cycle(
    *,
    access: AccessState = AccessState.HEALTHY,
    status: SyncCycleStatus = SyncCycleStatus.APPLIED,
    checked=NOW,
    with_snapshot: bool = True,
) -> SyncCycleResult:
    snapshot = CatalogSnapshot(
        entries=(),
        access_state=access,
        last_successful_check_at=checked,
        last_checked_at=checked,
        alert=None,
    )
    return SyncCycleResult(
        status=status,
        sequence=1,
        snapshot=snapshot if with_snapshot else None,
        anomaly=None,
        detail="d",
    )


def run(models, **kw):
    atts = kw.pop("attestations_by_model", {})
    args = dict(
        organization_id=ORG,
        connection_id=CONN,
        sync_cycle=scoped(cycle()),
        reconciliation=scoped(recon(*models)),
        artifacts_by_model={},
        probes_by_model={},
        attestations_by_model=(
            {k: scoped(v) for k, v in atts.items()} if isinstance(atts, dict) else atts
        ),
        security_profile="standard",
        approved_license_ids=frozenset({"apache-2.0"}),
        available_gpu_vram_gb=48,
        assigned_roles={},
        now=NOW,
    )
    args.update(kw)
    return discover_candidates(**args)


def by_id(result, model_id):
    return next(c for c in result.candidates if c.model_id == model_id)


def test_candidates_come_only_from_reconciled_set() -> None:
    res = run(["m-open"], artifacts_by_model={"m-open": artifact(), "ghost": artifact()})
    check(
        "FTF-1706-A1",
        "only reconciled aliases become candidates",
        [c.model_id for c in res.candidates] == ["m-open"],
    )
    healthy_empty = run([])
    check(
        "FTF-1706-N1",
        "a healthy, discovered and genuinely empty org yields no candidates (nothing hardcoded)",
        healthy_empty.candidates == ()
        and healthy_empty.model_count == 0
        and healthy_empty.state is GatewayState.DISCOVERED
        and healthy_empty.succeeded,
    )
    res2 = run(["zeta", "alpha"])
    check(
        "FTF-1706-N1",
        "order is by id, never by a preferred model",
        [c.model_id for c in res2.candidates] == ["alpha", "zeta"],
    )


def test_tunable_candidate_full_verification() -> None:
    res = run(["m-open"], artifacts_by_model={"m-open": artifact()})
    c = by_id(res, "m-open")
    check(
        "FTF-1706-A2",
        "ready+license+gpu is tunable",
        c.kind is CandidateKind.TUNABLE and c.blocked_reasons == (),
    )
    check("FTF-1706-A2", "license verified", c.license is LicenseState.APPROVED)
    check("FTF-1706-A2", "method available", c.method is MethodState.AVAILABLE)
    check("FTF-1706-A2", "privacy available under standard", c.privacy is PrivacyState.AVAILABLE)
    check("FTF-1706-A2", "artifact id carried", c.artifact_id is not None)


def test_license_checks() -> None:
    c = by_id(run(["m"], artifacts_by_model={"m": artifact(accepted=False)}), "m")
    check(
        "FTF-1706-A2",
        "unaccepted license blocks",
        c.kind is CandidateKind.BLOCKED and "license_not_accepted" in c.blocked_reasons,
    )
    c = by_id(run(["m"], artifacts_by_model={"m": artifact(license_id="gpl-3.0")}), "m")
    check(
        "FTF-1706-A2",
        "unapproved license id blocks",
        c.kind is CandidateKind.BLOCKED and "license_not_approved" in c.blocked_reasons,
    )
    c = by_id(
        run(["m"], artifacts_by_model={"m": artifact()}, approved_license_ids=frozenset()), "m"
    )
    check("FTF-1706-A2", "empty allow-list approves nothing", c.kind is CandidateKind.BLOCKED)


def test_method_checks() -> None:
    c = by_id(run(["m"], artifacts_by_model={"m": artifact(gpu={})}), "m")
    check(
        "FTF-1706-A2",
        "undeclared GPU need blocks",
        c.kind is CandidateKind.BLOCKED and "gpu_requirement_undeclared" in c.blocked_reasons,
    )
    c = by_id(run(["m"], artifacts_by_model={"m": artifact(gpu={"min_gpu_vram_gb": 80})}), "m")
    check(
        "FTF-1706-A2",
        "too little VRAM blocks",
        c.method is MethodState.GPU_INSUFFICIENT and "gpu_insufficient" in c.blocked_reasons,
    )
    c = by_id(run(["m"], artifacts_by_model={"m": artifact(gpu={"min_gpu_vram_gb": 48})}), "m")
    check("FTF-1706-A2", "VRAM exactly equal is enough", c.kind is CandidateKind.TUNABLE)
    c = by_id(run(["m"], artifacts_by_model={"m": artifact()}, available_gpu_vram_gb=None), "m")
    check(
        "FTF-1706-A2",
        "unspecified GPU budget blocks",
        c.kind is CandidateKind.BLOCKED and "gpu_budget_unspecified" in c.blocked_reasons,
    )
    # A row loaded from the database bypasses the constructor's validation.
    loaded = artifact()
    loaded.minimum_gpu_profile_json = {"min_gpu_vram_gb": True}
    c = by_id(run(["m"], artifacts_by_model={"m": loaded}), "m")
    check("FTF-1706-A2", "bool VRAM value is not a number", c.kind is CandidateKind.BLOCKED)


def test_privacy_checks() -> None:
    arts = {"m": artifact()}
    c = by_id(run(["m"], artifacts_by_model=arts, security_profile="zero_egress"), "m")
    check(
        "FTF-1706-A2",
        "zero_egress without attestation blocks",
        c.kind is CandidateKind.BLOCKED
        and "route_identity_unverified" in c.blocked_reasons
        and c.privacy is PrivacyState.UNVERIFIED,
    )
    for bad in (
        attestation("m", identity=AttestedIdentityState.UNVERIFIED),
        attestation("m", state=RouteAttestationState.UNREACHABLE),
        attestation("m", resolved="other"),
    ):
        c = by_id(
            run(
                ["m"],
                artifacts_by_model=arts,
                security_profile="zero_egress",
                attestations_by_model={"m": bad},
            ),
            "m",
        )
        check(
            "FTF-1706-A2",
            "a bad attestation never satisfies privacy",
            c.kind is CandidateKind.BLOCKED,
        )
    c = by_id(
        run(
            ["m"],
            artifacts_by_model=arts,
            security_profile="zero_egress",
            attestations_by_model={"m": attestation("m")},
        ),
        "m",
    )
    check(
        "FTF-1706-A2",
        "verified attestation satisfies zero_egress",
        c.kind is CandidateKind.TUNABLE and c.privacy is PrivacyState.AVAILABLE,
    )
    hf = {"m": artifact(source_type="huggingface")}
    c = by_id(
        run(
            ["m"],
            artifacts_by_model=hf,
            security_profile="air_gapped",
            attestations_by_model={"m": attestation("m")},
        ),
        "m",
    )
    check(
        "FTF-1706-A2",
        "air_gapped refuses a huggingface-sourced artifact",
        c.kind is CandidateKind.BLOCKED and "artifact_source_not_air_gapped" in c.blocked_reasons,
    )
    c = by_id(
        run(
            ["m"],
            artifacts_by_model=hf,
            security_profile="zero_egress",
            attestations_by_model={"m": attestation("m")},
        ),
        "m",
    )
    check(
        "FTF-1706-A2",
        "zero_egress alone does not refuse huggingface",
        c.kind is CandidateKind.TUNABLE,
    )
    c = by_id(
        run(
            ["m"],
            artifacts_by_model=arts,
            security_profile="air_gapped",
            attestations_by_model={"m": attestation("m")},
        ),
        "m",
    )
    check(
        "FTF-1706-A2", "air_gapped accepts a private_s3 artifact", c.kind is CandidateKind.TUNABLE
    )


def test_inference_only_is_comparator_not_tunable() -> None:
    c = by_id(run(["gpt-x"], probes_by_model={"gpt-x": probe("gpt-x")}), "gpt-x")
    check(
        "FTF-1706-N2",
        "inference-only endpoint is an untuned comparator",
        c.kind is CandidateKind.UNTUNED_COMPARATOR,
    )
    check(
        "FTF-1706-N2",
        "inference-only has no artifact/license/method claims",
        c.license is LicenseState.NOT_APPLICABLE
        and c.method is MethodState.NOT_APPLICABLE
        and c.artifact_id is None,
    )
    c = by_id(
        run(["gpt-x"], probes_by_model={"gpt-x": probe("gpt-x")}, security_profile="zero_egress"),
        "gpt-x",
    )
    check(
        "FTF-1706-A2",
        "inference-only under zero_egress needs attestation",
        c.kind is CandidateKind.BLOCKED,
    )


def test_unknown_is_blocked_never_assumed_tunable() -> None:
    for name in ("llama-3-8b-lora", "tunable-model", "my-finetune"):
        c = by_id(run([name]), name)
        check(
            "FTF-1706-N2",
            f"{name}: no evidence stays blocked",
            c.kind is CandidateKind.BLOCKED and "capability_unknown" in c.blocked_reasons,
        )
    c = by_id(run(["m"], artifacts_by_model={"m": artifact(status="registered")}), "m")
    check(
        "FTF-1706-N2",
        "registered (not ready) artifact is not tunable",
        c.kind is CandidateKind.BLOCKED,
    )
    c = by_id(run(["m"], artifacts_by_model={"m": artifact(digest=None)}), "m")
    check(
        "FTF-1706-N2",
        "ready artifact without digest is not tunable",
        c.kind is CandidateKind.BLOCKED,
    )
    c = by_id(run(["m"], probes_by_model={"other": probe("other")}), "m")
    check("FTF-1706-N2", "another model's probe is no evidence", c.kind is CandidateKind.BLOCKED)


def test_unknown_under_strict_profile_reports_only_capability() -> None:
    c = by_id(run(["m"], security_profile="air_gapped"), "m")
    check(
        "FTF-1706-N2",
        "unknown capability under air_gapped is blocked for capability alone",
        c.kind is CandidateKind.BLOCKED and c.blocked_reasons == ("capability_unknown",),
    )


def test_roles_passthrough_and_input_guards() -> None:
    res = run(["m"], artifacts_by_model={"m": artifact()}, assigned_roles={"m": "baseline"})
    check(
        "FTF-1706-A1", "assigned role passes through unchanged", by_id(res, "m").role == "baseline"
    )
    check("FTF-1706-A1", "no role is inferred", by_id(run(["m"]), "m").role is None)
    check(
        "FTF-1706-A1",
        "cross-org artifact is a contract violation",
        _raises(Exception, run, ["m"], artifacts_by_model={"m": artifact(org=uuid.uuid4())}),
    )
    bad_profiles: list[object] = [[], {}, 1, None, b"standard", "nope"]
    for bad_profile in bad_profiles:
        check(
            "FTF-1706-A1",
            f"bad security_profile {bad_profile!r} raises",
            _raises(Exception, run, ["m"], security_profile=bad_profile),
        )
    bad_licenses: list[object] = [[], "apache-2.0", None, frozenset({1})]
    for bad_license in bad_licenses:
        check(
            "FTF-1706-A1",
            f"bad approved_license_ids {bad_license!r} raises",
            _raises(CandidateDiscoveryError, run, ["m"], approved_license_ids=bad_license),
        )
    bad_vrams: list[object] = [True, -1, "48", [], {}]
    for bad_vram in bad_vrams:
        check(
            "FTF-1706-A1",
            f"bad vram {bad_vram!r} raises",
            _raises(CandidateDiscoveryError, run, ["m"], available_gpu_vram_gb=bad_vram),
        )
    check(
        "FTF-1706-A1",
        "bad organization_id raises",
        _raises(CandidateDiscoveryError, run, ["m"], organization_id="x"),
    )


def test_failed_gateway_is_never_an_empty_org() -> None:
    cases = {
        "revoked": (cycle(access=AccessState.REVOKED), GatewayState.ACCESS_REVOKED),
        "unknown": (cycle(access=AccessState.UNKNOWN), GatewayState.ACCESS_UNKNOWN),
        "degraded": (cycle(access=AccessState.DEGRADED), GatewayState.ACCESS_DEGRADED),
        "stale": (
            cycle(checked=datetime(2026, 10, 7, tzinfo=UTC)),
            GatewayState.CATALOG_STALE,
        ),
        "never discovered": (
            cycle(checked=None),
            GatewayState.NO_SUCCESSFUL_DISCOVERY,
        ),
        "retry scheduled": (
            cycle(status=SyncCycleStatus.RETRY_SCHEDULED),
            GatewayState.SYNC_NOT_APPLIED,
        ),
        "retry ceiling": (
            cycle(status=SyncCycleStatus.RETRY_CEILING_REACHED),
            GatewayState.SYNC_NOT_APPLIED,
        ),
        "out of order": (
            cycle(status=SyncCycleStatus.SKIPPED_OUT_OF_ORDER),
            GatewayState.SYNC_NOT_APPLIED,
        ),
        "no snapshot": (cycle(with_snapshot=False), GatewayState.SYNC_NOT_APPLIED),
    }
    for label, (bad_cycle, expected) in cases.items():
        res = run(
            ["m-open"], artifacts_by_model={"m-open": artifact()}, sync_cycle=scoped(bad_cycle)
        )
        check(
            "FTF-1706-N1",
            f"{label} gateway is a non-success state {expected.value}",
            res.state is expected and not res.succeeded,
        )
        check(
            "FTF-1706-N1",
            f"{label} gateway reports no tunable candidate from stale evidence",
            res.candidates == () and res.tunable_count == 0,
        )
    fresh = run(["m"], sync_cycle=scoped(cycle(checked=NOW)))
    check("FTF-1706-N1", "a fresh healthy catalog is DISCOVERED", fresh.succeeded)
    for bad in (None, "x", cycle()):
        check(
            "FTF-1706-N1",
            f"sync_cycle {type(bad).__name__} that is not ScopedEvidence raises",
            _raises(CandidateDiscoveryError, run, ["m"], sync_cycle=bad),
        )
    check(
        "FTF-1706-N1",
        "sync_cycle wrapping a non-SyncCycleResult raises",
        _raises(CandidateDiscoveryError, run, ["m"], sync_cycle=scoped("x")),
    )
    check(
        "FTF-1706-N1",
        "reconciliation wrapping a non-reconciliation raises",
        _raises(CandidateDiscoveryError, run, ["m"], reconciliation=scoped("x")),
    )


def test_finite_vram_only() -> None:
    nan, inf = float("nan"), float("inf")
    for bad in (nan, inf, -inf):
        check(
            "FTF-1706-A2",
            f"available vram {bad!r} is refused",
            _raises(CandidateDiscoveryError, run, ["m"], available_gpu_vram_gb=bad),
        )
        loaded = artifact()
        loaded.minimum_gpu_profile_json = {"min_gpu_vram_gb": bad}  # row loaded from the database
        res = run(["m"], artifacts_by_model={"m": loaded})
        c = by_id(res, "m")
        check(
            "FTF-1706-A2",
            f"required vram {bad!r} is never a satisfiable requirement",
            c.kind is CandidateKind.BLOCKED
            and c.method is MethodState.GPU_REQUIREMENT_UNDECLARED
            and "gpu_requirement_undeclared" in c.blocked_reasons,
        )
    loaded = artifact()
    loaded.minimum_gpu_profile_json = {"min_gpu_vram_gb": "24"}
    res = run(["m"], artifacts_by_model={"m": loaded})
    check(
        "FTF-1706-A2",
        "a string required vram is undeclared, never coerced",
        by_id(res, "m").method is MethodState.GPU_REQUIREMENT_UNDECLARED,
    )


def test_tenant_and_connection_binding() -> None:
    other_org, other_conn = uuid.uuid4(), uuid.uuid4()
    recon_other_org = scoped(recon("m"), org=other_org)
    recon_other_conn = scoped(recon("m"), conn=other_conn)
    check(
        "FTF-1706-A1",
        "a reconciliation from another organization is refused",
        _raises(CandidateDiscoveryError, run, ["m"], reconciliation=recon_other_org),
    )
    check(
        "FTF-1706-A1",
        "a reconciliation from another connection is refused",
        _raises(CandidateDiscoveryError, run, ["m"], reconciliation=recon_other_conn),
    )
    check(
        "FTF-1706-A1",
        "a sync cycle from another organization is refused",
        _raises(CandidateDiscoveryError, run, ["m"], sync_cycle=scoped(cycle(), org=other_org)),
    )
    check(
        "FTF-1706-A1",
        "a sync cycle from another connection is refused",
        _raises(CandidateDiscoveryError, run, ["m"], sync_cycle=scoped(cycle(), conn=other_conn)),
    )
    for label, kw in (("organization", {"org": other_org}), ("connection", {"conn": other_conn})):
        att = {"m": scoped(attestation("m"), **kw)}
        check(
            "FTF-1706-A2",
            f"an attestation from another {label} is refused (zero_egress)",
            _raises(
                CandidateDiscoveryError,
                run,
                ["m"],
                security_profile="zero_egress",
                attestations_by_model=att,
            ),
        )
    check(
        "FTF-1706-A1",
        "an attestation that is not ScopedEvidence is refused",
        _raises(
            CandidateDiscoveryError,
            run,
            ["m"],
            security_profile="zero_egress",
            attestations_by_model=MappingProxyType({"m": attestation("m")}),
        ),
    )
    check(
        "FTF-1706-A1",
        "an unscoped attestation entry for an unlisted model is still refused",
        _raises(
            CandidateDiscoveryError,
            run,
            ["m"],
            attestations_by_model={"ghost": scoped(attestation("ghost"), org=other_org)},
        ),
    )
    check(
        "FTF-1706-A1",
        "bad connection_id raises",
        _raises(CandidateDiscoveryError, run, ["m"], connection_id="x"),
    )


def test_fail_closed_input_types() -> None:
    class Unhashable(list):
        pass

    art = artifact()
    object.__setattr__(art, "license_id", [])  # a row loaded around the constructor
    check(
        "FTF-1706-A2",
        "an unhashable license_id raises CandidateDiscoveryError, never TypeError",
        _raises(CandidateDiscoveryError, run, ["m"], artifacts_by_model={"m": art}),
    )
    bad_maps: list[object] = [None, [], "x", 1]
    for bad in bad_maps:
        check(
            "FTF-1706-A1",
            f"artifacts_by_model {bad!r} raises CandidateDiscoveryError",
            _raises(CandidateDiscoveryError, run, ["m"], artifacts_by_model=bad),
        )
        check(
            "FTF-1706-A1",
            f"probes_by_model {bad!r} raises CandidateDiscoveryError",
            _raises(CandidateDiscoveryError, run, ["m"], probes_by_model=bad),
        )
        check(
            "FTF-1706-A1",
            f"attestations_by_model {bad!r} raises CandidateDiscoveryError",
            _raises(CandidateDiscoveryError, run, ["m"], attestations_by_model=bad),
        )
        check(
            "FTF-1706-A1",
            f"assigned_roles {bad!r} raises CandidateDiscoveryError",
            _raises(CandidateDiscoveryError, run, ["m"], assigned_roles=bad),
        )
    check(
        "FTF-1706-A1",
        "a non-artifact value raises CandidateDiscoveryError",
        _raises(CandidateDiscoveryError, run, ["m"], artifacts_by_model={"m": "not-an-artifact"}),
    )
    check(
        "FTF-1706-A1",
        "a non-probe value raises CandidateDiscoveryError",
        _raises(CandidateDiscoveryError, run, ["m"], probes_by_model={"m": object()}),
    )
    check(
        "FTF-1706-A1",
        "a non-attestation value raises CandidateDiscoveryError",
        _raises(
            CandidateDiscoveryError,
            run,
            ["m"],
            attestations_by_model={"m": scoped("x")},
        ),
    )
    for bad_role in (None, 1, ["baseline"], b"baseline"):
        check(
            "FTF-1706-A1",
            f"non-string role {bad_role!r} raises CandidateDiscoveryError",
            _raises(CandidateDiscoveryError, run, ["m"], assigned_roles={"m": bad_role}),
        )
    check(
        "FTF-1706-A1",
        "a non-string model key raises CandidateDiscoveryError",
        _raises(CandidateDiscoveryError, run, ["m"], artifacts_by_model={1: artifact()}),
    )


def test_survivor_behaviours() -> None:
    meta = MappingProxyType({"supports_fine_tuning": True})
    gateway_says_tunable = AliasReconciliationResult(
        models=(ReconciledModel(id="m", metadata_status=ModelMetadataStatus.LIMITED, fields=meta),),
        model_count=1,
        reconciled_at=NOW,
    )
    res = run(["m"], reconciliation=scoped(gateway_says_tunable))
    check(
        "FTF-1706-N2",
        "gateway supports_fine_tuning metadata never makes a model tunable",
        by_id(res, "m").kind is CandidateKind.BLOCKED
        and res.tunable_count == 0
        and by_id(res, "m").blocked_reasons == ("capability_unknown",),
    )
    res = run(["Model-A"], artifacts_by_model={"model-a": artifact()})
    check(
        "FTF-1706-N2",
        "model ids are case-exact: a differently-cased artifact key is not bound",
        by_id(res, "Model-A").kind is CandidateKind.BLOCKED and res.tunable_count == 0,
    )
    res = run(["model"], artifacts_by_model={"model-extended": artifact()})
    check(
        "FTF-1706-N2",
        "artifact binding is exact-key, never a prefix match",
        by_id(res, "model").kind is CandidateKind.BLOCKED,
    )
    res = run(
        ["zz-open", "aa-blocked", "mm-comp"],
        artifacts_by_model={"zz-open": artifact()},
        probes_by_model={"mm-comp": probe("mm-comp")},
    )
    check(
        "FTF-1706-N1",
        "more than two models stay ordered by id, never evidence-first",
        [c.model_id for c in res.candidates] == ["aa-blocked", "mm-comp", "zz-open"],
    )
    res = run(
        ["a", "b", "c"],
        security_profile="zero_egress",
        probes_by_model={"a": probe("a"), "b": probe("b"), "c": probe("c")},
        attestations_by_model={"a": attestation("a"), "b": attestation("b", resolved="other")},
    )
    check(
        "FTF-1706-A2",
        "comparator_count excludes models blocked by zero_egress privacy",
        res.comparator_count == 1 and res.blocked_count == 2,
    )
    check(
        "FTF-1706-A1",
        "no role is inferred for a comparator",
        all(c.role is None for c in res.candidates),
    )
    vocabulary = {
        "capability_unknown",
        "license_not_accepted",
        "license_not_approved",
        "gpu_requirement_undeclared",
        "gpu_budget_unspecified",
        "gpu_insufficient",
        "route_identity_unverified",
        "artifact_source_not_air_gapped",
    }
    wide = run(
        ["u", "n", "g", "r"],
        security_profile="air_gapped",
        artifacts_by_model={
            "n": artifact(accepted=False, source_type="huggingface"),
            "g": artifact(gpu={}),
        },
    )
    reasons = {r for c in wide.candidates for r in c.blocked_reasons}
    check(
        "FTF-1706-A2",
        "blocked reasons stay inside the closed vocabulary (no URI or detail text)",
        reasons <= vocabulary and all("/" not in r and ":" not in r for r in reasons),
    )


def test_counts_are_actual() -> None:
    res = run(
        ["a", "b", "c"],
        artifacts_by_model={"a": artifact()},
        probes_by_model={"b": probe("b")},
    )
    check(
        "FTF-1706-A1",
        "counts reflect real verdicts",
        (res.tunable_count, res.comparator_count, res.blocked_count) == (1, 1, 1),
    )
    check(
        "FTF-1706-A1",
        "model_count equals reconciled count",
        res.model_count == 3 and res.security_profile == "standard",
    )


def main() -> int:
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
    if FAILURES:
        print("\n".join(FAILURES))
        print(f"{len(FAILURES)} check(s) failed")
        return 1
    print("all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
