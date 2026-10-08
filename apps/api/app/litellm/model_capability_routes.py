"""Live `.../connections/litellm/{connection_id}/models/{model_name}/
capabilities` route (FTF-0309-P1, Section 0.12/3.5/6/13/34/68).

    GET   .../capabilities?project_id=   the four capability dimensions for one alias

This is the first live caller of FTF-0307's `classify_training_capability`.
It composes certified pieces only and never re-implements the classifier:

- a fresh FTF-0204 `discover_models` plus FTF-0205/FTF-0207 reconciliation
  (the `access` dimension);
- the FTF-0308-P1 explicit alias -> artifact mapping and its tenant-scoped
  `get_artifact` (the `capability` / artifact dimensions);
- the latest FTF-0212 tool-call probe result the catalog holds for exactly
  this model, supplied by `probe_lookup` (None when there is none). A GET
  never runs a probe.

Nothing in the repository persists a probe result yet (the probe route in
`model_catalog_routes.py` returns it and forgets it), so the default
absent `probe_lookup` means no probe evidence. `inference_only` is therefore unreachable on the
live path until a probe store exists; the classifier still ignores a probe for
a different model or an unverified route identity if one is ever supplied.

`method_support`, tested `min_gpu_vram_gb` and `chat_template` have no backing
computation (FTF-0307 disclosed this) and are reported as explicit
`"unknown"` with a reason code, never defaulted. The artifact's
`minimum_gpu_profile` is labelled `declared`, never `tested`.

Reads use `ARTIFACT_READ_ROLES` (a viewer may read). The route needs
`project_id`. Errors are `{"detail": "<stable_code>"}`: 400, 401, 403, 404
`not_found` (identical for a missing or foreign connection, artifact or
project), 503 `gateway_unavailable` (any discovery state other than
`discovered`: Law 17).
"""

from __future__ import annotations

import sys
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

_REPO_ROOT = Path(__file__).resolve().parents[4]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from packages.litellm_client.alias_reconciliation import (  # noqa: E402
    AliasReconciliationResult,
    reconcile_aliases,
)
from packages.litellm_client.connection_validation import (  # noqa: E402
    default_connection_validation_policy,
)
from packages.litellm_client.real_tool_call_probe import ToolCallProbeResult  # noqa: E402
from packages.litellm_client.secret_manager_integration import OpenBaoConfig  # noqa: E402
from packages.litellm_client.v1_model_info_enrichment import (  # noqa: E402
    enrich_visible_models,
)
from packages.litellm_client.v1_models_discovery import (  # noqa: E402
    DiscoveryState,
    ModelDiscoveryResult,
    discover_models,
)
from packages.security.url_policy import ConnectionPolicy  # noqa: E402

from app.artifacts.artifact_crud import ArtifactNotFoundError, get_artifact  # noqa: E402
from app.artifacts.artifact_routes import ARTIFACT_READ_ROLES  # noqa: E402
from app.artifacts.training_capability_classifier import (  # noqa: E402
    TrainingCapability,
    TrainingCapabilityClassification,
    TrainingCapabilityClassificationError,
    classify_training_capability,
)
from app.auth.rbac_middleware import (  # noqa: E402
    AuthorizedContext,
    PrincipalResolver,
    RoleLookup,
    default_principal_resolver,
    default_role_lookup,
    require_role,
)
from app.auth.tenant_isolation_query_guards import TenantContextError  # noqa: E402
from app.db.tenant_context import TenantContext, tenant_scoped_session_for  # noqa: E402
from app.litellm.connection_crud import ConnectionNotFoundError, get_connection  # noqa: E402
from app.litellm.connection_routes import _default_secret_manager_config  # noqa: E402
from app.litellm.model_catalog_mapping_store import (  # noqa: E402
    PUBLIC_MODEL_NAME_MAX_LENGTH,
    get_entry,
)
from app.projects.project_crud import ProjectNotFoundError, get_project  # noqa: E402

__all__ = ["build_model_capability_router", "ProbeLookup"]

ProbeLookup = Callable[[uuid.UUID, uuid.UUID, str], ToolCallProbeResult | None]

_CAPABILITIES_PATH = (
    "/api/v1/organizations/{organization_id}/connections/litellm/{connection_id}"
    "/models/{model_name:path}/capabilities"
)


def _not_found() -> HTTPException:
    return HTTPException(status_code=404, detail="not_found")


def _unknown(reason: str) -> dict[str, object]:
    return {"state": "unknown", "reason": reason}


def _body(
    *,
    connection_id: uuid.UUID,
    project_id: uuid.UUID,
    model_name: str,
    connection_status: str,
    classification: TrainingCapabilityClassification,
    declared_gpu_profile: object,
    mapped_artifact_digest: str | None,
    catalog_version: int,
    probe: ToolCallProbeResult | None,
    discovery_checked_at: Any,
    unknown_reason: str | None,
) -> dict[str, object]:
    capability = classification.capability
    return {
        "litellm_connection_id": str(connection_id),
        "project_id": str(project_id),
        "public_model_name": model_name,
        "connection_status": connection_status,
        "access": classification.access.value,
        "capability": capability.value,
        "trainable": capability is TrainingCapability.TRAINABLE_OPEN_WEIGHT,
        "capability_detail": classification.detail,
        "capability_reason": unknown_reason,
        "role": classification.role,
        "artifact": {
            "artifact_id": classification.artifact_id,
            "availability": classification.artifact_availability.value,
            "license_approved": classification.license_approved,
            "weights_digest": mapped_artifact_digest,
        },
        "catalog_version": catalog_version,
        "method_support": _unknown("no_backing_evidence"),
        "min_gpu_vram_gb": {
            **_unknown("not_tested"),
            "declared_minimum_gpu_profile": declared_gpu_profile,
            "declared_evidence": "declared",
        },
        "chat_template": _unknown("no_backing_evidence"),
        "evidence": {
            "discovery_checked_at": (
                None if discovery_checked_at is None else str(discovery_checked_at.isoformat())
            ),
            "tool_call_probe_checked_at": (
                None
                if classification.evidence_probed_at is None
                else str(classification.evidence_probed_at.isoformat())
            ),
            "tool_call_probe_present": probe is not None,
        },
    }


def build_model_capability_router(
    session_factory: Callable[[], Session],
    *,
    principal_resolver: PrincipalResolver = default_principal_resolver,
    role_lookup: RoleLookup = default_role_lookup,
    secret_manager_config_factory: Callable[
        [], OpenBaoConfig | None
    ] = _default_secret_manager_config,
    validation_policy_factory: Callable[
        [], ConnectionPolicy
    ] = default_connection_validation_policy,
    discovery_function: Callable[..., ModelDiscoveryResult] = discover_models,
    enrichment_function: Callable[..., Any] = enrich_visible_models,
    probe_lookup: ProbeLookup | None = None,
) -> APIRouter:
    """`discovery_function`, `enrichment_function` and `probe_lookup` default
    to the real implementations; they are seams so a test can supply results
    without a live gateway or probe store."""
    router = APIRouter()
    read_dependency = Depends(
        require_role(
            ARTIFACT_READ_ROLES, principal_resolver=principal_resolver, role_lookup=role_lookup
        )
    )

    @router.get(_CAPABILITIES_PATH)
    async def read_capabilities(
        request: Request,
        organization_id: uuid.UUID,
        connection_id: uuid.UUID,
        model_name: str,
        ctx: AuthorizedContext = read_dependency,
    ) -> JSONResponse:
        del organization_id
        if not model_name or len(model_name) > PUBLIC_MODEL_NAME_MAX_LENGTH:
            raise HTTPException(status_code=400, detail="invalid_model_name")
        raw_project_id = request.query_params.get("project_id")
        if raw_project_id is None:
            raise HTTPException(status_code=400, detail="project_id is required")
        try:
            project_id = uuid.UUID(raw_project_id)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="project_id must be a UUID") from exc

        tenant_context = TenantContext.from_authorized_context(ctx)
        try:
            with tenant_scoped_session_for(session_factory, tenant_context) as session:
                try:
                    get_project(session, tenant_context, project_id)
                except ProjectNotFoundError as exc:
                    raise _not_found() from exc
                connection = get_connection(session, tenant_context, connection_id)
                connection_status = connection.status
                base_url = connection.base_url
                secret_ref = connection.secret_ref
        except (ConnectionNotFoundError, TenantContextError) as exc:
            raise _not_found() from exc

        reconciliation: AliasReconciliationResult
        discovery_checked_at = None
        revoked = connection_status == "revoked"
        if revoked:
            # A revoked connection is never dialed and never trainable.
            reconciliation = AliasReconciliationResult(
                models=(), model_count=0, reconciled_at=datetime.now(UTC)
            )
        else:
            discovery = discovery_function(
                base_url=base_url,
                secret_ref=secret_ref,
                secret_config=secret_manager_config_factory(),
                policy=validation_policy_factory(),
            )
            if discovery.state is not DiscoveryState.DISCOVERED:
                raise HTTPException(status_code=503, detail="gateway_unavailable")
            discovery_checked_at = discovery.checked_at
            enrichment = enrichment_function(
                base_url=base_url,
                secret_ref=secret_ref,
                secret_config=secret_manager_config_factory(),
                policy=validation_policy_factory(),
                visible_model_ids=tuple(model.id for model in discovery.models),
            )
            reconciliation = reconcile_aliases(discovery=discovery, enrichment=enrichment)

        try:
            with tenant_scoped_session_for(session_factory, tenant_context) as session:
                # Re-read: a revoke that landed during discovery must win.
                if get_connection(session, tenant_context, connection_id).status == "revoked":
                    revoked = True
                    connection_status = "revoked"
                    reconciliation = AliasReconciliationResult(
                        models=(), model_count=0, reconciled_at=datetime.now(UTC)
                    )
                entry = get_entry(
                    session,
                    tenant_context,
                    connection_id=connection_id,
                    public_model_name=model_name,
                )
                artifact = None
                catalog_version = 0
                if entry is not None:
                    catalog_version = entry.catalog_version
                    if entry.trainable_artifact_id is not None and not revoked:
                        artifact = get_artifact(
                            session, tenant_context, entry.trainable_artifact_id
                        )
                # No probe result is persisted anywhere yet (module docstring):
                # without a lookup there is no probe evidence.
                probe = (
                    None
                    if revoked or probe_lookup is None
                    else probe_lookup(tenant_context.organization_id, connection_id, model_name)
                )
                classification = classify_training_capability(
                    model_id=model_name,
                    organization_id=tenant_context.organization_id,
                    reconciliation=reconciliation,
                    probe=probe,
                    artifact=artifact,
                )
                reason: str | None = None
                if classification.capability is TrainingCapability.UNKNOWN:
                    reason = "connection_revoked" if revoked else "no_trainable_evidence"
                body = _body(
                    connection_id=connection_id,
                    project_id=project_id,
                    model_name=model_name,
                    connection_status=connection_status,
                    classification=classification,
                    declared_gpu_profile=(
                        None if artifact is None else artifact.minimum_gpu_profile_json
                    ),
                    mapped_artifact_digest=None if artifact is None else artifact.weights_digest,
                    catalog_version=catalog_version,
                    probe=probe,
                    discovery_checked_at=discovery_checked_at,
                    unknown_reason=reason,
                )
        except (
            ConnectionNotFoundError,
            ArtifactNotFoundError,
            TenantContextError,
            TrainingCapabilityClassificationError,
        ) as exc:
            # A cross-organization artifact is a foreign row: same 404 as a missing one.
            raise _not_found() from exc
        return JSONResponse(body)

    return router
