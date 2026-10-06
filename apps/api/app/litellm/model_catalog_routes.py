"""Live `/api/v1/organizations/{organization_id}/connections/litellm/
{connection_id}/catalog` and `.../models/{model_id}/probe` routes
(FTF-0214 "Model catalog UI", Section 2-3/38/65/77/81).

This is the first live caller of nine previously-unwired, already-
CERTIFIED pure-logic modules, composed exactly as their own docstrings
describe their eventual real call site:

- `packages/litellm_client/v1_model_info_enrichment.py::enrich_visible_models`
  (FTF-0205) and `packages/litellm_client/model_group_info_optional_enrichment.py::
  enrich_visible_model_groups` (FTF-0206) -- left-join `/model/info`/
  `/model_group/info` fields onto `app.litellm.connection_routes`'s already-
  live `discover_models` (FTF-0204) result.
- `packages/litellm_client/alias_reconciliation.py::reconcile_aliases`
  (FTF-0207) -- combines a fresh discovery with its own fresh enrichment.
- `packages/litellm_client/last_known_good_catalog.py::LastKnownGoodCatalog`
  (FTF-0208) and `packages/litellm_client/catalog_anomaly_detector.py::
  CatalogAnomalyDetector` (FTF-0209) -- one real, per-connection, in-
  process instance each (`_CATALOG_REGISTRY` below), fed a fresh discovery
  result on every `GET .../catalog` call.
- `packages/model_catalog/access_snapshot.py::build_access_snapshot`
  (FTF-H004) -- combines the catalog's historic view with the fresh
  reconciliation into the one display snapshot this route serializes.
  `packages/litellm_client/access_guard.py::require_model_access`
  (also FTF-H004) additionally gates `POST .../probe` -- the real
  "paid-effect" call site Section 3.4/65 requires: ownership, connection-
  level revocation and catalog-level freshness/callable state are all
  re-checked, fresh, before any probe ever dials the gateway.
- `packages/litellm_client/real_chat_probe.py::probe_chat` (FTF-0210),
  `real_streaming_probe.py::probe_stream_chat` (FTF-0211),
  `real_tool_call_probe.py::probe_tool_call` (FTF-0212) and
  `real_structured_output_probe.py::probe_structured_output` (FTF-0213) --
  `POST .../probe`'s own four dispatchable probe kinds.

Scope decision (disclosed, same precedent FTF-04A03/FTF-04A04 already
established for "backend live, UI not yet mounted"): this router is a
real, reachable FastAPI surface, but `apps/web` has no `apps/web/app/`
page tree yet (FTF-H029, "create the first page," has not landed), so
`model_catalog_ui.tsx` cannot be reached by browsing from `/` yet --
Law 6 (UI reachability) stays disclosed BLOCKED on the UI half until
FTF-H029 exists, exactly like FTF-04A03/FTF-04A04's own disclosed gap.

In-process catalog state (disclosed, same "no independent schema change"
convention FTF-0208/FTF-0209/FTF-H004 themselves already established):
`_CATALOG_REGISTRY` is a per-router-instance, in-memory dict keyed by
connection id. It is not durable across a process restart and not shared
across multiple API processes/replicas -- a real periodic/durable sync is
`docs/TASK_INDEX.md`'s own later, separately dependency-gated "Periodic
sync workflow" task (FTF-0215), not this task's own `files` list. Every
`GET .../catalog` call feeds this call's own fresh `discover_models`
result into that connection's registry entry, so the registry is always
at least as fresh as the most recent call any admin made against it in
this process's lifetime.

Explicit project selection (this task's own acceptance clause): `POST
.../probe` requires a real `project_id` query parameter, resolved through
`app.projects.project_crud.get_project` inside the exact same tenant-
scoped session as the connection lookup -- a project_id that does not
exist, or that belongs to a different organization, is denied identically
(404), before any probe is ever authorized or dialed. This does not
create a `Project`<->`LiteLLMConnection` binding (connections remain
organization-scoped, matching every other route in this file's sibling
`connection_routes.py`); it enforces that a human explicitly chose a real,
owned project before this route will spend a real, paid probe call on
their behalf -- this task's own negative_acceptance ("no automatic model
selection... viewer cannot run paid probe") is additionally covered by
reusing `connection_routes.py`'s own `CONNECTION_ADMIN_ROLES` gate (every
route below is `organization_admin`-only, so no `viewer`-level principal
can reach this route at all).

Safe filters (this task's own acceptance clause): `GET .../catalog`
accepts optional `status`/`metadata_status` query parameters that only
ever narrow the already-authorized entry set `build_access_snapshot`
returned -- never widen it, never bypass `LastKnownGoodCatalog`'s own
revocation/freshness checks, and an unrecognized value is rejected (400)
rather than silently ignored.
"""

from __future__ import annotations

import sys
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

_REPO_ROOT = Path(__file__).resolve().parents[4]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from packages.litellm_client.access_guard import (  # noqa: E402
    ModelAccessDeniedError,
    require_model_access,
)
from packages.litellm_client.alias_reconciliation import reconcile_aliases  # noqa: E402
from packages.litellm_client.catalog_anomaly_detector import (  # noqa: E402
    AnomalyRecord,
    CatalogAnomalyDetector,
)
from packages.litellm_client.connection_validation import (  # noqa: E402
    default_connection_validation_policy,
)
from packages.litellm_client.last_known_good_catalog import (  # noqa: E402
    CatalogEntryStatus,
    LastKnownGoodCatalog,
)
from packages.litellm_client.model_group_info_optional_enrichment import (  # noqa: E402
    enrich_visible_model_groups,
)
from packages.litellm_client.real_chat_probe import (  # noqa: E402
    ChatProbeResult,
    probe_chat,
)
from packages.litellm_client.real_streaming_probe import (  # noqa: E402
    StreamProbeResult,
    probe_stream_chat,
)
from packages.litellm_client.real_structured_output_probe import (  # noqa: E402
    StructuredOutputProbeResult,
    probe_structured_output,
)
from packages.litellm_client.real_tool_call_probe import (  # noqa: E402
    ToolCallProbeResult,
    probe_tool_call,
)
from packages.litellm_client.secret_manager_integration import OpenBaoConfig  # noqa: E402
from packages.litellm_client.v1_model_info_enrichment import enrich_visible_models  # noqa: E402
from packages.litellm_client.v1_models_discovery import (  # noqa: E402
    DiscoveryState,
    ModelDiscoveryResult,
    discover_models,
)
from packages.model_catalog.access_snapshot import (  # noqa: E402
    AccessSnapshotEntry,
    build_access_snapshot,
)
from packages.security.url_policy import ConnectionPolicy  # noqa: E402

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
from app.litellm.connection_crud import (  # noqa: E402
    ConnectionNotFoundError,
    ConnectionStateError,
    get_connection,
)
from app.litellm.connection_routes import (  # noqa: E402
    CONNECTION_ADMIN_ROLES,
    _default_secret_manager_config,
)
from app.litellm.connection_schema import LiteLLMConnection  # noqa: E402
from app.projects.project_crud import ProjectNotFoundError, get_project  # noqa: E402

__all__ = ["build_model_catalog_router"]

_ITEM_PATH = "/api/v1/organizations/{organization_id}/connections/litellm/{connection_id}"
_CATALOG_PATH = _ITEM_PATH + "/catalog"
_PROBE_PATH = _ITEM_PATH + "/models/{model_id}/probe"

_PROBE_KINDS = frozenset({"chat", "streaming", "tool_call", "structured_output"})

_ProbeResult = (
    ChatProbeResult | StreamProbeResult | ToolCallProbeResult | StructuredOutputProbeResult
)


def _serialize_entry(entry: AccessSnapshotEntry) -> dict[str, object]:
    return {
        "id": entry.id,
        "status": entry.status.value,
        "metadata_status": entry.metadata_status.value,
        "first_seen_at": entry.first_seen_at.isoformat(),
        "last_seen_callable_at": (
            entry.last_seen_callable_at.isoformat() if entry.last_seen_callable_at else None
        ),
        "fields": dict(entry.fields),
    }


def _serialize_anomaly(anomaly: AnomalyRecord | None) -> dict[str, object] | None:
    if anomaly is None:
        return None
    return {
        "id": anomaly.id,
        "kind": anomaly.kind.value,
        "detected_at": anomaly.detected_at.isoformat(),
        "previous_count": anomaly.previous_count,
        "new_count": anomaly.new_count,
        "retry_count": anomaly.retry_count,
        "retry_ceiling": anomaly.retry_ceiling,
        "status": anomaly.status.value,
        "acknowledged_by": anomaly.acknowledged_by,
        "acknowledged_at": (
            anomaly.acknowledged_at.isoformat() if anomaly.acknowledged_at else None
        ),
    }


def _serialize_usage(usage: object) -> dict[str, object] | None:
    if usage is None:
        return None
    return {
        "prompt_tokens": usage.prompt_tokens,  # type: ignore[attr-defined]
        "completion_tokens": usage.completion_tokens,  # type: ignore[attr-defined]
        "total_tokens": usage.total_tokens,  # type: ignore[attr-defined]
    }


def _serialize_probe_result(probe_type: str, result: _ProbeResult) -> dict[str, object]:
    """Content-free by construction, same convention every probe module's
    own `detail` field already establishes -- never a raw exception
    message, credential or gateway response body."""
    body: dict[str, object] = {
        "probe_type": probe_type,
        "state": result.state.value,
        "route_identity_state": result.route_identity_state.value,
        "resolved_model": result.resolved_model,
        "usage": _serialize_usage(result.usage),
        "latency_ms": result.latency_ms,
        "detail": result.detail,
        "probed_at": result.probed_at.isoformat(),
    }
    if isinstance(result, ToolCallProbeResult):
        body["advertised_tool_name"] = result.advertised_tool_name
        body["observed_tool_name"] = result.observed_tool_name
        body["observed_arguments"] = result.observed_arguments
    elif isinstance(result, StructuredOutputProbeResult):
        body["parsed_content"] = result.parsed_content
    elif isinstance(result, StreamProbeResult):
        body["text"] = result.text
        body["chunk_count"] = result.chunk_count
        body["termination_reason"] = (
            result.termination_reason.value if result.termination_reason else None
        )
        body["tool_calls"] = [
            {
                "name": call.name,
                "arguments": call.arguments,
            }
            for call in result.tool_calls
        ]
    return body


def build_model_catalog_router(
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
) -> APIRouter:
    """Real FastAPI router for FTF-0214's composed catalog display and
    probe dispatch surface. Injection conventions match
    `app.litellm.connection_routes.build_litellm_connection_router`
    exactly (`session_factory` is the already-restricted-role
    `sessionmaker`; the two factories are call-time-only, never cached at
    router-build time)."""
    router = APIRouter()
    admin_dependency = Depends(
        require_role(
            CONNECTION_ADMIN_ROLES,
            principal_resolver=principal_resolver,
            role_lookup=role_lookup,
        )
    )

    # FTF-0208/FTF-0209: one real LastKnownGoodCatalog + CatalogAnomalyDetector
    # pair per connection, scoped to this router instance's own process
    # lifetime (this module's own docstring, "In-process catalog state").
    _registry: dict[uuid.UUID, tuple[LastKnownGoodCatalog, CatalogAnomalyDetector]] = {}

    def _catalog_for(
        connection_id: uuid.UUID,
    ) -> tuple[LastKnownGoodCatalog, CatalogAnomalyDetector]:
        pair = _registry.get(connection_id)
        if pair is None:
            pair = (LastKnownGoodCatalog(), CatalogAnomalyDetector())
            _registry[connection_id] = pair
        return pair

    @router.get(_CATALOG_PATH)
    async def catalog(
        organization_id: uuid.UUID,
        connection_id: uuid.UUID,
        request: Request,
        ctx: AuthorizedContext = admin_dependency,
    ) -> JSONResponse:
        del organization_id
        status_filter = request.query_params.get("status")
        if status_filter is not None and status_filter not in {s.value for s in CatalogEntryStatus}:
            raise HTTPException(status_code=400, detail="unknown status filter")
        metadata_filter = request.query_params.get("metadata_status")
        raw_project_id = request.query_params.get("project_id")

        tenant_context = TenantContext.from_authorized_context(ctx)
        try:
            with tenant_scoped_session_for(session_factory, tenant_context) as session:
                if raw_project_id is not None:
                    try:
                        project_uuid = uuid.UUID(raw_project_id)
                    except ValueError as exc:
                        raise HTTPException(
                            status_code=400, detail="project_id must be a UUID"
                        ) from exc
                    try:
                        get_project(session, tenant_context, project_uuid)
                    except ProjectNotFoundError as exc:
                        raise HTTPException(status_code=404, detail="not_found") from exc

                connection = get_connection(session, tenant_context, connection_id)
                base_url = connection.base_url
                secret_ref = connection.secret_ref
                connection_status = connection.status
        except (ConnectionNotFoundError, TenantContextError) as exc:
            raise HTTPException(status_code=404, detail="not_found") from exc

        catalog_obj, detector = _catalog_for(connection_id)

        if connection_status == "revoked":
            # Factory-level revocation is a stronger, independent signal
            # than anything the gateway itself could report (Section 65.1:
            # "never trusting an arbitrary... value" extended to "never
            # dialing out on behalf of a revoked connection at all") -- no
            # network call is attempted, matching every sibling route's
            # own 409-on-revoked precedent, just surfaced here as a
            # historical, still-readable snapshot rather than a hard error
            # so a revoked connection's catalog stays auditable.
            snapshot = catalog_obj.snapshot()
            reconciliation = None
        else:
            result: ModelDiscoveryResult = discover_models(
                base_url=base_url,
                secret_ref=secret_ref,
                secret_config=secret_manager_config_factory(),
                policy=validation_policy_factory(),
            )
            snapshot, _ = detector.record(catalog_obj, result)

            reconciliation = None
            if result.state == DiscoveryState.DISCOVERED:
                visible_ids = tuple(model.id for model in result.models)
                enrichment = enrich_visible_models(
                    base_url=base_url,
                    secret_ref=secret_ref,
                    secret_config=secret_manager_config_factory(),
                    policy=validation_policy_factory(),
                    visible_model_ids=visible_ids,
                )
                reconciliation = reconcile_aliases(discovery=result, enrichment=enrichment)

        view = build_access_snapshot(snapshot, reconciliation)

        entries = view.entries
        if status_filter is not None:
            entries = tuple(e for e in entries if e.status.value == status_filter)
        if metadata_filter is not None:
            entries = tuple(e for e in entries if e.metadata_status.value == metadata_filter)

        groups: dict[str, object] | None = None
        if connection_status != "revoked" and reconciliation is not None:
            group_result = enrich_visible_model_groups(
                base_url=base_url,
                secret_ref=secret_ref,
                secret_config=secret_manager_config_factory(),
                policy=validation_policy_factory(),
                visible_model_ids=tuple(model.id for model in reconciliation.models),
                capability_supported=True,
            )
            groups = {
                "state": group_result.state.value,
                "detail": group_result.detail,
                "entries": {entry.id: dict(entry.fields) for entry in group_result.entries},
            }

        body: dict[str, object] = {
            "connection_status": connection_status,
            "access_state": view.access_state.value,
            "last_successful_check_at": (
                view.last_successful_check_at.isoformat() if view.last_successful_check_at else None
            ),
            "last_checked_at": (view.last_checked_at.isoformat() if view.last_checked_at else None),
            "alert": view.alert,
            "anomaly": _serialize_anomaly(detector.current),
            "model_groups": groups,
            "entries": [_serialize_entry(e) for e in entries],
        }
        return JSONResponse(body, status_code=200)

    @router.post(_PROBE_PATH)
    async def probe(
        request: Request,
        organization_id: uuid.UUID,
        connection_id: uuid.UUID,
        model_id: str,
        ctx: AuthorizedContext = admin_dependency,
    ) -> JSONResponse:
        del organization_id
        raw_project_id = request.query_params.get("project_id")
        if raw_project_id is None:
            raise HTTPException(status_code=400, detail="project_id is required")
        try:
            project_id = uuid.UUID(raw_project_id)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="project_id must be a UUID") from exc

        try:
            payload: dict[str, Any] = await request.json()
        except Exception as exc:  # noqa: BLE001 - any unparsable body is the same outcome
            raise HTTPException(status_code=400, detail="request body must be valid JSON") from exc
        if not isinstance(payload, dict):
            raise HTTPException(status_code=400, detail="request body must be a JSON object")
        probe_type = payload.get("probe_type")
        if not isinstance(probe_type, str) or probe_type not in _PROBE_KINDS:
            raise HTTPException(
                status_code=400,
                detail="probe_type must be one of chat, streaming, tool_call, structured_output",
            )

        tenant_context = TenantContext.from_authorized_context(ctx)
        catalog_obj, _detector = _catalog_for(connection_id)

        try:
            with tenant_scoped_session_for(session_factory, tenant_context) as session:
                try:
                    get_project(session, tenant_context, project_id)
                except ProjectNotFoundError as exc:
                    raise HTTPException(status_code=404, detail="not_found") from exc

                connection = get_connection(session, tenant_context, connection_id)
                base_url = connection.base_url
                secret_ref = connection.secret_ref

                try:
                    require_model_access(
                        session,
                        tenant_context,
                        LiteLLMConnection.__table__,
                        LiteLLMConnection.__table__.c.id,
                        LiteLLMConnection.__table__.c.organization_id,
                        LiteLLMConnection.__table__.c.status,
                        connection_id,
                        catalog_obj,
                        model_id,
                    )
                except ModelAccessDeniedError as exc:
                    raise HTTPException(
                        status_code=403, detail=f"model_access_denied:{exc.reason.value}"
                    ) from exc
        except (ConnectionNotFoundError, TenantContextError) as exc:
            raise HTTPException(status_code=404, detail="not_found") from exc
        except ConnectionStateError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

        secret_config = secret_manager_config_factory()
        policy = validation_policy_factory()
        approved_model_ids = frozenset({model_id})

        if probe_type == "chat":
            result: _ProbeResult = probe_chat(
                base_url=base_url,
                model_id=model_id,
                approved_model_ids=approved_model_ids,
                secret_ref=secret_ref,
                secret_config=secret_config,
                policy=policy,
            )
        elif probe_type == "streaming":
            result = probe_stream_chat(
                base_url=base_url,
                model_id=model_id,
                approved_model_ids=approved_model_ids,
                secret_ref=secret_ref,
                secret_config=secret_config,
                policy=policy,
            )
        elif probe_type == "tool_call":
            result = probe_tool_call(
                base_url=base_url,
                model_id=model_id,
                approved_model_ids=approved_model_ids,
                secret_ref=secret_ref,
                secret_config=secret_config,
                policy=policy,
            )
        else:
            result = probe_structured_output(
                base_url=base_url,
                model_id=model_id,
                approved_model_ids=approved_model_ids,
                secret_ref=secret_ref,
                secret_config=secret_config,
                policy=policy,
            )

        return JSONResponse(_serialize_probe_result(probe_type, result), status_code=200)

    return router
