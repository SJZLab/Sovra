#!/usr/bin/env python3
"""FTF-0214 -- no-DB, no-external-egress tests for the pure serialization
helpers in `app.litellm.model_catalog_routes`.

This task's own real, live-wired integration evidence (real Postgres,
real local HTTPS gateway, real fault injection against
`require_model_access`) is `tests/integration/test_ftf_0214_model_catalog.py`
-- same split every sibling FTF-020x/H004 task already established
(tasks/ = pure-logic, no DB; integration/ = real Postgres + route).

This file covers the one genuinely new, DB-free surface this task adds:
the content-free serializers (`_serialize_entry`, `_serialize_anomaly`,
`_serialize_usage`, `_serialize_probe_result`) that turn the nine composed
modules' own real dataclasses into the JSON body the live routes return --
each exercised directly against a real instance of its own dataclass, not
a stub standing in for one.

Run directly: python3 tests/tasks/test_ftf_0214.py
"""

from __future__ import annotations

import contextlib
import http.client
import json
import os
import socket
import sys
import threading
import time
import urllib.parse
import uuid
from datetime import UTC, datetime
from pathlib import Path
from types import MappingProxyType, SimpleNamespace
from typing import Any

ROOT = Path(__file__).resolve().parent.parent.parent
API_ROOT = ROOT / "apps" / "api"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(API_ROOT))

import uvicorn  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from packages.litellm_client.catalog_anomaly_detector import (  # noqa: E402
    AnomalyKind,
    AnomalyRecord,
    AnomalyStatus,
)
from packages.litellm_client.last_known_good_catalog import (  # noqa: E402
    CatalogEntryStatus,
)
from packages.litellm_client.model_group_info_optional_enrichment import (  # noqa: E402
    GroupInfoState,
    ModelGroupEnrichmentResult,
)
from packages.litellm_client.real_chat_probe import (  # noqa: E402
    ChatProbeResult,
    ChatProbeState,
    ChatUsage,
    RouteIdentityState,
)
from packages.litellm_client.real_streaming_probe import (  # noqa: E402
    StreamProbeResult,
    StreamProbeState,
    StreamTerminationReason,
    ToolCallDelta,
)
from packages.litellm_client.real_structured_output_probe import (  # noqa: E402
    StructuredOutputProbeResult,
    StructuredOutputProbeState,
)
from packages.litellm_client.real_tool_call_probe import (  # noqa: E402
    ToolCallProbeResult,
    ToolCallProbeState,
)
from packages.litellm_client.v1_model_info_enrichment import (  # noqa: E402
    EnrichmentState,
    ModelInfoEnrichmentResult,
)
from packages.litellm_client.v1_models_discovery import (  # noqa: E402
    DiscoveredModel,
    DiscoveryState,
    ModelDiscoveryResult,
    ModelMetadataStatus,
)
from packages.model_catalog.access_snapshot import AccessSnapshotEntry  # noqa: E402

from app.auth.rbac_middleware import AuthenticatedPrincipal, Role  # noqa: E402
from app.litellm import model_catalog_routes as mcr  # noqa: E402
from app.litellm.connection_crud import ConnectionNotFoundError  # noqa: E402
from app.litellm.model_catalog_routes import (  # noqa: E402
    _serialize_anomaly,
    _serialize_entry,
    _serialize_probe_result,
    _serialize_usage,
    build_model_catalog_router,
)
from app.projects.project_crud import ProjectNotFoundError  # noqa: E402

FAILURES: list[str] = []


def check(criterion_id: str, description: str, condition: bool) -> None:
    if not condition:
        FAILURES.append(f"FAIL [{criterion_id}] {description}")


_NOW = datetime(2026, 10, 6, 12, 0, 0, tzinfo=UTC)


def test_serialize_entry_round_trips_real_fields() -> None:
    entry = AccessSnapshotEntry(
        id="model-a",
        status=CatalogEntryStatus.CALLABLE,
        metadata_status=ModelMetadataStatus.ENRICHED,
        first_seen_at=_NOW,
        last_seen_callable_at=_NOW,
        fields=MappingProxyType({"max_tokens": 8192}),
    )
    body = _serialize_entry(entry)
    check("FTF-0214", "a real entry serializes its real id", body["id"] == "model-a")
    check(
        "FTF-0214", "a real callable entry serializes its real status", body["status"] == "callable"
    )
    check(
        "FTF-0214",
        "a real callable entry serializes its real metadata_status",
        body["metadata_status"] == "enriched",
    )
    check(
        "FTF-0214",
        "a real entry's fields mapping is copied, not dropped",
        body["fields"] == {"max_tokens": 8192},
    )
    check(
        "FTF-0214",
        "a real entry's timestamps are real ISO strings",
        body["first_seen_at"] == _NOW.isoformat()
        and body["last_seen_callable_at"] == _NOW.isoformat(),
    )


def test_serialize_entry_never_fabricates_a_timestamp_for_a_never_callable_entry() -> None:
    entry = AccessSnapshotEntry(
        id="model-b",
        status=CatalogEntryStatus.REMOVED,
        metadata_status=ModelMetadataStatus.LIMITED,
        first_seen_at=_NOW,
        last_seen_callable_at=None,
        fields=MappingProxyType({}),
    )
    body = _serialize_entry(entry)
    check(
        "FTF-0214",
        "negative_acceptance: an entry never seen callable reports last_seen_callable_at=None, "
        "never a fabricated timestamp",
        body["last_seen_callable_at"] is None,
    )
    check(
        "FTF-0214",
        "a removed entry's empty fields serialize as an empty object, not omitted",
        body["fields"] == {},
    )


def test_serialize_anomaly_none_stays_none() -> None:
    check(
        "FTF-0214",
        "no anomaly serializes to None, never a fabricated empty-but-present record",
        _serialize_anomaly(None) is None,
    )


def test_serialize_anomaly_real_record() -> None:
    record = AnomalyRecord(
        id=1,
        kind=AnomalyKind.MAJOR_REDUCTION,
        detected_at=_NOW,
        previous_count=10,
        new_count=2,
        retry_count=0,
        retry_ceiling=3,
        status=AnomalyStatus.OPEN,
        acknowledged_by=None,
        acknowledged_at=None,
    )
    body = _serialize_anomaly(record)
    assert body is not None
    check("FTF-0214", "a real anomaly's real id is reported", body["id"] == 1)
    check("FTF-0214", "a real anomaly's real kind is reported", body["kind"] == "major_reduction")
    check(
        "FTF-0214",
        "a real anomaly's real detected_at is reported",
        body["detected_at"] == _NOW.isoformat(),
    )
    check(
        "FTF-0214",
        "a real anomaly's real previous/new counts are reported",
        body["previous_count"] == 10 and body["new_count"] == 2,
    )
    check(
        "FTF-0214",
        "a real anomaly's real retry_count/retry_ceiling are reported",
        body["retry_count"] == 0 and body["retry_ceiling"] == 3,
    )
    check("FTF-0214", "a real open anomaly's status is reported", body["status"] == "open")
    check(
        "FTF-0214",
        "an unacknowledged anomaly reports acknowledged_by=None, never a fabricated actor",
        body["acknowledged_by"] is None and body["acknowledged_at"] is None,
    )


def test_serialize_usage_none_and_real() -> None:
    check(
        "FTF-0214",
        "no usage serializes to None, never a zero-filled object",
        _serialize_usage(None) is None,
    )
    usage = ChatUsage(prompt_tokens=9, completion_tokens=1, total_tokens=10)
    body = _serialize_usage(usage)
    assert body is not None
    check(
        "FTF-0214",
        "a real usage object's real token counts are reported",
        body["prompt_tokens"] == 9
        and body["completion_tokens"] == 1
        and body["total_tokens"] == 10,
    )


def test_serialize_probe_result_chat() -> None:
    result = ChatProbeResult(
        state=ChatProbeState.PROBED,
        route_identity_state=RouteIdentityState.VERIFIED,
        resolved_model="model-a",
        usage=ChatUsage(prompt_tokens=9, completion_tokens=1, total_tokens=10),
        latency_ms=12.5,
        detail="",
        probed_at=_NOW,
    )
    body = _serialize_probe_result("chat", result)
    check(
        "FTF-0214", "a real chat probe result serializes probe_type", body["probe_type"] == "chat"
    )
    check(
        "FTF-0214", "a real chat probe result serializes its real state", body["state"] == "probed"
    )
    check(
        "FTF-0214",
        "a real chat probe result never carries tool/structured/stream-only keys",
        "advertised_tool_name" not in body and "parsed_content" not in body and "text" not in body,
    )


def test_serialize_probe_result_tool_call() -> None:
    result = ToolCallProbeResult(
        state=ToolCallProbeState.PROBED,
        route_identity_state=RouteIdentityState.VERIFIED,
        resolved_model="model-a",
        advertised_tool_name="get_probe_time",
        observed_tool_name="get_probe_time",
        observed_arguments='{"unit":"iso8601"}',
        usage=None,
        latency_ms=5.0,
        detail="",
        probed_at=_NOW,
    )
    body = _serialize_probe_result("tool_call", result)
    check(
        "FTF-0214",
        "a real tool-call probe result reports the real advertised vs. observed tool identity",
        body["advertised_tool_name"] == "get_probe_time"
        and body["observed_tool_name"] == "get_probe_time",
    )
    check(
        "FTF-0214",
        "a tool-call result with no usage reports usage=None, never a zero-filled object",
        body["usage"] is None,
    )


def test_serialize_probe_result_structured_output() -> None:
    result = StructuredOutputProbeResult(
        state=StructuredOutputProbeState.PROBED,
        route_identity_state=RouteIdentityState.VERIFIED,
        resolved_model="model-a",
        parsed_content={"word": "pong", "count": 1},
        usage=None,
        latency_ms=5.0,
        detail="",
        probed_at=_NOW,
    )
    body = _serialize_probe_result("structured_output", result)
    check(
        "FTF-0214",
        "a real structured-output probe result's real schema-validated content is serialized",
        body["parsed_content"] == {"word": "pong", "count": 1},
    )


def test_serialize_probe_result_streaming() -> None:
    result = StreamProbeResult(
        state=StreamProbeState.PROBED,
        route_identity_state=RouteIdentityState.VERIFIED,
        resolved_model="model-a",
        text="pong",
        tool_calls=(ToolCallDelta(index=0, id="call-1", name="get_probe_time", arguments="{}"),),
        termination_reason=StreamTerminationReason.STOP,
        usage=ChatUsage(prompt_tokens=9, completion_tokens=1, total_tokens=10),
        chunk_count=3,
        latency_ms=8.0,
        detail="",
        probed_at=_NOW,
    )
    body = _serialize_probe_result("streaming", result)
    check(
        "FTF-0214",
        "a real streaming probe result's reconstructed text is serialized",
        body["text"] == "pong",
    )
    check(
        "FTF-0214",
        "a real streaming probe result's chunk_count is real, not fabricated",
        body["chunk_count"] == 3,
    )
    check(
        "FTF-0214",
        "a real streaming probe result's reconstructed tool-call deltas are serialized",
        body["tool_calls"] == [{"name": "get_probe_time", "arguments": "{}"}],
    )
    check(
        "FTF-0214",
        "a real streaming probe result's termination_reason is serialized by value, not enum repr",
        body["termination_reason"] == "stop",
    )


# -- Real FastAPI/uvicorn route-level tests, no DB -------------------------
#
# `tenant_scoped_session_for`/`get_connection`/`get_project`/`discover_models`/
# `enrich_visible_models`/`enrich_visible_model_groups`/`require_model_access`/
# the four `probe_*` functions are all module-level names imported into
# `app.litellm.model_catalog_routes` -- monkeypatched here (and restored in a
# `finally`) the same way this task's own real-Postgres integration suite
# monkeypatches `require_model_access` for its own fault injection. This lets
# every branch inside the two live route closures be driven deterministically
# without a real database or a real gateway, while `reconcile_aliases`/
# `build_access_snapshot`/`LastKnownGoodCatalog`/`CatalogAnomalyDetector`
# stay real (pure, no-I/O) so the actual composition is still exercised, not
# just its call sites.

_ISSUER = "https://issuer.example"
_ORG_ID = uuid.uuid4()
_ADMIN = AuthenticatedPrincipal(issuer=_ISSUER, subject="admin-1")
_VIEWER = AuthenticatedPrincipal(issuer=_ISSUER, subject="viewer-1")


def _principal_resolver(request: Any) -> AuthenticatedPrincipal | None:
    header = request.headers.get("x-test-principal")
    if header == "admin":
        return _ADMIN
    if header == "viewer":
        return _VIEWER
    return None


def _role_lookup(issuer: str, subject: str, organization_id: uuid.UUID) -> Role | None:
    if organization_id != _ORG_ID:
        return None
    if subject == _ADMIN.subject:
        return Role.ORGANIZATION_ADMIN
    if subject == _VIEWER.subject:
        return Role.VIEWER
    return None


def free_port() -> int:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


@contextlib.contextmanager
def _running_uvicorn(app: FastAPI):
    port = free_port()
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(200):
        if server.started:
            break
        time.sleep(0.02)
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(timeout=5)


def _running_app():
    app = FastAPI()
    app.include_router(
        build_model_catalog_router(
            lambda: None,  # never actually called -- tenant_scoped_session_for is faked below
            principal_resolver=_principal_resolver,
            role_lookup=_role_lookup,
        )
    )
    return _running_uvicorn(app)


class _HttpResult:
    def __init__(self, status: int, body: bytes) -> None:
        self.status = status
        self.body = body

    def json(self) -> Any:
        return json.loads(self.body)


def _request(
    base_url: str,
    method: str,
    path: str,
    *,
    principal: str | None = None,
    payload: dict[str, object] | list[object] | None = None,
    raw_body: bytes | None = None,
) -> _HttpResult:
    headers: dict[str, str] = {}
    if principal is not None:
        headers["X-Test-Principal"] = principal
    body_bytes: bytes | None = None
    if raw_body is not None:
        body_bytes = raw_body
        headers["Content-Type"] = "application/json"
    elif payload is not None:
        body_bytes = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    parsed = urllib.parse.urlparse(base_url)
    conn = http.client.HTTPConnection(parsed.hostname or "127.0.0.1", parsed.port or 80, timeout=10)
    try:
        conn.request(method, path, body=body_bytes, headers=headers)
        response = conn.getresponse()
        raw = response.read()
        return _HttpResult(status=response.status, body=raw)
    finally:
        conn.close()


def catalog_path(connection_id: object, query: str = "") -> str:
    return f"/api/v1/organizations/{_ORG_ID}/connections/litellm/{connection_id}/catalog{query}"


def probe_path(connection_id: object, model_id: str, query: str = "") -> str:
    return (
        f"/api/v1/organizations/{_ORG_ID}/connections/litellm/{connection_id}"
        f"/models/{model_id}/probe{query}"
    )


@contextlib.contextmanager
def _patched(**overrides: Any):
    originals = {name: getattr(mcr, name) for name in overrides}
    for name, value in overrides.items():
        setattr(mcr, name, value)
    try:
        yield
    finally:
        for name, value in originals.items():
            setattr(mcr, name, value)


@contextlib.contextmanager
def _fake_session_cm(session_factory: Any, tenant_context: Any):
    yield object()


def _fake_tenant_scoped_session_for(session_factory: Any, tenant_context: Any):
    return _fake_session_cm(session_factory, tenant_context)


def _fake_connection(
    *,
    status: str = "active",
    base_url: str = "https://gateway.example",
    secret_ref: str = "openbao://secret/x",
) -> SimpleNamespace:
    return SimpleNamespace(status=status, base_url=base_url, secret_ref=secret_ref)


def _raising_get_connection(session: Any, tenant_context: Any, connection_id: Any) -> Any:
    raise ConnectionNotFoundError("not found")


def _raising_get_project(session: Any, tenant_context: Any, project_id: Any) -> Any:
    raise ProjectNotFoundError("not found")


def _ok_get_project(session: Any, tenant_context: Any, project_id: Any) -> Any:
    return SimpleNamespace(id=project_id)


def _discovery_result(ids: tuple[str, ...]) -> ModelDiscoveryResult:
    return ModelDiscoveryResult(
        state=DiscoveryState.DISCOVERED,
        models=tuple(
            DiscoveredModel(id=i, metadata_status=ModelMetadataStatus.LIMITED) for i in ids
        ),
        model_count=len(ids),
        content_hash="a" * 64,
        detail="",
        checked_at=_NOW,
    )


def _empty_enrichment() -> ModelInfoEnrichmentResult:
    return ModelInfoEnrichmentResult(
        state=EnrichmentState.COMPLETED,
        entries=(),
        detail="",
        checked_at=_NOW,
    )


def _empty_group_enrichment() -> ModelGroupEnrichmentResult:
    return ModelGroupEnrichmentResult(
        state=GroupInfoState.COMPLETED,
        entries=(),
        detail="",
        checked_at=_NOW,
    )


def test_unauthenticated_catalog_and_probe_are_denied() -> None:
    with _running_app() as base_url:
        r = _request(base_url, "GET", catalog_path(uuid.uuid4()))
        check("FTF-0214", "an unauthenticated catalog read is denied (401)", r.status == 401)
        r = _request(
            base_url,
            "POST",
            probe_path(uuid.uuid4(), "model-a", "?project_id=" + str(uuid.uuid4())),
            payload={"probe_type": "chat"},
        )
        check("FTF-0214", "an unauthenticated probe is denied (401)", r.status == 401)


def test_viewer_is_denied_catalog_and_probe() -> None:
    with _running_app() as base_url:
        r = _request(base_url, "GET", catalog_path(uuid.uuid4()), principal="viewer")
        check(
            "FTF-0214",
            "negative_acceptance: a viewer is denied the catalog (403), admin-only",
            r.status == 403,
        )
        r = _request(
            base_url,
            "POST",
            probe_path(uuid.uuid4(), "model-a", "?project_id=" + str(uuid.uuid4())),
            principal="viewer",
            payload={"probe_type": "chat"},
        )
        check(
            "FTF-0214",
            "negative_acceptance: a viewer cannot run a paid probe (403)",
            r.status == 403,
        )


def test_catalog_invalid_status_filter_is_rejected() -> None:
    with _running_app() as base_url:
        r = _request(
            base_url,
            "GET",
            catalog_path(uuid.uuid4(), "?status=not-a-real-status"),
            principal="admin",
        )
        check(
            "FTF-0214",
            "an unrecognized status filter is rejected (400), never silently ignored",
            r.status == 400,
        )


def test_catalog_invalid_metadata_filter_is_rejected() -> None:
    with _running_app() as base_url:
        r = _request(
            base_url,
            "GET",
            catalog_path(uuid.uuid4(), "?metadata_status=not-a-real-status"),
            principal="admin",
        )
        check(
            "FTF-0214",
            "an unrecognized metadata_status filter is rejected (400), never silently ignored",
            r.status == 400,
        )


def test_catalog_missing_connection_is_404() -> None:
    with _patched(
        tenant_scoped_session_for=_fake_tenant_scoped_session_for,
        get_connection=_raising_get_connection,
    ):
        with _running_app() as base_url:
            r = _request(base_url, "GET", catalog_path(uuid.uuid4()), principal="admin")
            check(
                "FTF-0214",
                "a genuinely missing connection is denied identically to foreign (404)",
                r.status == 404,
            )


def test_catalog_bad_project_id_and_not_found_project_are_rejected() -> None:
    with _patched(
        tenant_scoped_session_for=_fake_tenant_scoped_session_for,
        get_connection=lambda *a, **k: _fake_connection(),
        get_project=_raising_get_project,
        discover_models=lambda **k: _discovery_result(("model-a",)),
        enrich_visible_models=lambda **k: _empty_enrichment(),
        enrich_visible_model_groups=lambda **k: _empty_group_enrichment(),
    ):
        with _running_app() as base_url:
            r = _request(
                base_url,
                "GET",
                catalog_path(uuid.uuid4(), "?project_id=not-a-uuid"),
                principal="admin",
            )
            check(
                "FTF-0214",
                "a malformed project_id is rejected (400), never silently skipped",
                r.status == 400,
            )
            r = _request(
                base_url,
                "GET",
                catalog_path(uuid.uuid4(), f"?project_id={uuid.uuid4()}"),
                principal="admin",
            )
            check(
                "FTF-0214",
                "a nonexistent project_id is rejected (404), never silently skipped",
                r.status == 404,
            )


def test_catalog_happy_path_composes_discovery_and_enrichment() -> None:
    enrichment = ModelInfoEnrichmentResult(
        state=EnrichmentState.COMPLETED,
        entries=(),
        detail="",
        checked_at=_NOW,
    )
    with _patched(
        tenant_scoped_session_for=_fake_tenant_scoped_session_for,
        get_connection=lambda *a, **k: _fake_connection(status="active"),
        get_project=_ok_get_project,
        discover_models=lambda **k: _discovery_result(("model-a", "model-b")),
        enrich_visible_models=lambda **k: enrichment,
        enrich_visible_model_groups=lambda **k: _empty_group_enrichment(),
    ):
        with _running_app() as base_url:
            r = _request(base_url, "GET", catalog_path(uuid.uuid4()), principal="admin")
            check(
                "FTF-0214", "a real active-connection catalog read succeeds (200)", r.status == 200
            )
            body = r.json() if r.status == 200 else {}
            check(
                "FTF-0214",
                "acceptance: both real discovered models are shown as callable",
                {e["id"] for e in body.get("entries", [])} == {"model-a", "model-b"}
                and all(e["status"] == "callable" for e in body.get("entries", [])),
            )
            check(
                "FTF-0214",
                "acceptance: access_state is healthy for a real reachable gateway",
                body.get("access_state") == "healthy",
            )
            check(
                "FTF-0214",
                "acceptance: the optional model_groups enrichment is surfaced",
                body.get("model_groups") is not None,
            )
            check("FTF-0214", "no anomaly on the first discovery", body.get("anomaly") is None)


def test_catalog_revoked_connection_never_calls_discover_models() -> None:
    calls = {"count": 0}

    def _counting_discover_models(**kwargs: Any) -> ModelDiscoveryResult:
        calls["count"] += 1
        return _discovery_result(("model-a",))

    with _patched(
        tenant_scoped_session_for=_fake_tenant_scoped_session_for,
        get_connection=lambda *a, **k: _fake_connection(status="revoked"),
        get_project=_ok_get_project,
        discover_models=_counting_discover_models,
        enrich_visible_models=lambda **k: _empty_enrichment(),
        enrich_visible_model_groups=lambda **k: _empty_group_enrichment(),
    ):
        with _running_app() as base_url:
            r = _request(base_url, "GET", catalog_path(uuid.uuid4()), principal="admin")
            check(
                "FTF-0214",
                "a revoked connection's catalog read still succeeds (200)",
                r.status == 200,
            )
            body = r.json() if r.status == 200 else {}
            check(
                "FTF-0214",
                "a revoked connection reports connection_status=revoked",
                body.get("connection_status") == "revoked",
            )
            check(
                "FTF-0214",
                "negative_acceptance: a revoked connection never dials the real gateway",
                calls["count"] == 0,
            )
            check(
                "FTF-0214",
                "a revoked connection's model_groups is never populated (no fresh reconciliation)",
                body.get("model_groups") is None,
            )


def test_catalog_revoked_after_discovery_shows_revoked_and_stale_never_healthy() -> None:
    # Round 2 blocking 1 ("no hidden stale access"): a connection discovered
    # healthy/callable and revoked afterwards must read revoked/stale, with
    # an already-removed entry staying removed (not re-labelled stale).
    state: dict[str, Any] = {"status": "active", "ids": ("model-a", "model-b")}

    with _patched(
        tenant_scoped_session_for=_fake_tenant_scoped_session_for,
        get_connection=lambda *a, **k: _fake_connection(status=state["status"]),
        get_project=_ok_get_project,
        discover_models=lambda **k: _discovery_result(state["ids"]),
        enrich_visible_models=lambda **k: _empty_enrichment(),
        enrich_visible_model_groups=lambda **k: _empty_group_enrichment(),
    ):
        with _running_app() as base_url:
            cid = uuid.uuid4()
            r = _request(base_url, "GET", catalog_path(cid), principal="admin")
            state["ids"] = ("model-a",)
            r = _request(base_url, "GET", catalog_path(cid), principal="admin")
            body = r.json() if r.status == 200 else {}
            by_id = {e["id"]: e["status"] for e in body.get("entries", [])}
            check(
                "FTF-0214",
                "before revocation: access healthy, model-a callable, model-b removed",
                body.get("access_state") == "healthy"
                and by_id == {"model-a": "callable", "model-b": "removed"},
            )
            state["status"] = "revoked"
            r = _request(base_url, "GET", catalog_path(cid), principal="admin")
            body = r.json() if r.status == 200 else {}
            by_id = {e["id"]: e["status"] for e in body.get("entries", [])}
            check(
                "FTF-0214",
                "round 2: after revocation access_state is revoked, never healthy",
                body.get("access_state") == "revoked"
                and body.get("connection_status") == "revoked",
            )
            check(
                "FTF-0214",
                "round 2: after revocation the callable entry is stale and the removed entry "
                "stays removed",
                by_id == {"model-a": "stale", "model-b": "removed"},
            )
            check(
                "FTF-0214",
                "round 2: a revoked entry carries no capability fields",
                all(e.get("fields") == {} for e in body.get("entries", [])),
            )


def test_catalog_non_revoked_status_still_calls_discover_models() -> None:
    # Distinguishes the exact literal "revoked" comparison (not a normalized
    # or inverted one) -- a status that is merely a different case of the
    # same word must still take the real, non-revoked (gateway-dialing)
    # branch, exactly like "active" does.
    calls = {"count": 0}

    def _counting_discover_models(**kwargs: Any) -> ModelDiscoveryResult:
        calls["count"] += 1
        return _discovery_result(("model-a",))

    with _patched(
        tenant_scoped_session_for=_fake_tenant_scoped_session_for,
        get_connection=lambda *a, **k: _fake_connection(status="Revoked"),
        get_project=_ok_get_project,
        discover_models=_counting_discover_models,
        enrich_visible_models=lambda **k: _empty_enrichment(),
        enrich_visible_model_groups=lambda **k: _empty_group_enrichment(),
    ):
        with _running_app() as base_url:
            r = _request(base_url, "GET", catalog_path(uuid.uuid4()), principal="admin")
            check(
                "FTF-0214",
                "a status that is not the exact literal 'revoked' still dials the real gateway",
                calls["count"] == 1,
            )
            body = r.json() if r.status == 200 else {}
            check(
                "FTF-0214",
                "its own model_groups enrichment is populated on that non-revoked path",
                body.get("model_groups") is not None,
            )


def test_catalog_only_the_literal_discovered_state_triggers_enrichment() -> None:
    # A discovery result whose own `state` is not the exact, real
    # `DiscoveryState.DISCOVERED` member (a bogus value no real
    # `discover_models` call could ever return) must never be treated as
    # a success -- enrichment/reconciliation is attempted only for a
    # genuine DISCOVERED result, never a normalized/lenient match.
    calls = {"count": 0}

    def _counting_enrich_visible_models(**kwargs: Any) -> ModelInfoEnrichmentResult:
        calls["count"] += 1
        return _empty_enrichment()

    bogus_result = ModelDiscoveryResult(
        state="Discovered",  # type: ignore[arg-type]
        models=(),
        model_count=0,
        content_hash=None,
        detail="",
        checked_at=_NOW,
    )
    with _patched(
        tenant_scoped_session_for=_fake_tenant_scoped_session_for,
        get_connection=lambda *a, **k: _fake_connection(),
        get_project=_ok_get_project,
        discover_models=lambda **k: bogus_result,
        enrich_visible_models=_counting_enrich_visible_models,
        enrich_visible_model_groups=lambda **k: _empty_group_enrichment(),
    ):
        with _running_app() as base_url:
            r = _request(base_url, "GET", catalog_path(uuid.uuid4()), principal="admin")
            check("FTF-0214", "a real catalog read still succeeds (200)", r.status == 200)
            check(
                "FTF-0214",
                "negative_acceptance: only the exact literal DISCOVERED state triggers "
                "enrichment, never a normalized/lenient match",
                calls["count"] == 0,
            )
            body = r.json() if r.status == 200 else {}
            check(
                "FTF-0214",
                "a non-DISCOVERED result never populates model_groups either",
                body.get("model_groups") is None,
            )


def test_catalog_non_discovered_result_never_crashes_and_skips_groups() -> None:
    # FTF-0209/H004 composition guard: when the connection is NOT revoked
    # but this cycle's own discovery did not succeed (no fresh
    # reconciliation exists), the model_groups enrichment must be skipped
    # entirely, never attempted against a `None` reconciliation.
    with _patched(
        tenant_scoped_session_for=_fake_tenant_scoped_session_for,
        get_connection=lambda *a, **k: _fake_connection(status="active"),
        get_project=_ok_get_project,
        discover_models=lambda **k: ModelDiscoveryResult(
            state=DiscoveryState.INVALID_TOKEN,
            models=(),
            model_count=None,
            content_hash=None,
            detail="gateway rejected the inference credential",
            checked_at=_NOW,
        ),
        enrich_visible_models=lambda **k: _empty_enrichment(),
        enrich_visible_model_groups=lambda **k: _empty_group_enrichment(),
    ):
        with _running_app() as base_url:
            r = _request(base_url, "GET", catalog_path(uuid.uuid4()), principal="admin")
            check(
                "FTF-0214",
                "a non-revoked connection whose discovery itself failed still succeeds (200), "
                "never crashes",
                r.status == 200,
            )
            body = r.json() if r.status == 200 else {}
            check(
                "FTF-0214",
                "model_groups stays None when no fresh reconciliation exists this cycle",
                body.get("model_groups") is None,
            )


def test_catalog_metadata_filter_narrows_a_mixed_metadata_set() -> None:
    # `metadata_status` on a catalog entry reflects FTF-0204's own
    # discovery-time classification (whatever `/v1/models` itself
    # reported inline), never FTF-0205's separate `/model/info`
    # enrichment -- `build_access_snapshot` only ever copies
    # `catalog_snapshot.entries[i].metadata_status` through unchanged
    # (see that module's own docstring). A mixed discovery result is the
    # real way to get one of each for this filter to narrow.
    mixed_discovery = ModelDiscoveryResult(
        state=DiscoveryState.DISCOVERED,
        models=(
            DiscoveredModel(id="model-a", metadata_status=ModelMetadataStatus.ENRICHED),
            DiscoveredModel(id="model-b", metadata_status=ModelMetadataStatus.LIMITED),
        ),
        model_count=2,
        content_hash="a" * 64,
        detail="",
        checked_at=_NOW,
    )
    with _patched(
        tenant_scoped_session_for=_fake_tenant_scoped_session_for,
        get_connection=lambda *a, **k: _fake_connection(),
        get_project=_ok_get_project,
        discover_models=lambda **k: mixed_discovery,
        enrich_visible_models=lambda **k: _empty_enrichment(),
        enrich_visible_model_groups=lambda **k: _empty_group_enrichment(),
    ):
        with _running_app() as base_url:
            r = _request(
                base_url,
                "GET",
                catalog_path(uuid.uuid4(), "?metadata_status=enriched"),
                principal="admin",
            )
            body = r.json() if r.status == 200 else {}
            check(
                "FTF-0214",
                "a safe metadata_status=enriched filter returns exactly the real enriched entry",
                {e["id"] for e in body.get("entries", [])} == {"model-a"},
            )
            r = _request(
                base_url,
                "GET",
                catalog_path(uuid.uuid4(), "?metadata_status=limited"),
                principal="admin",
            )
            body = r.json() if r.status == 200 else {}
            check(
                "FTF-0214",
                "a safe metadata_status=limited filter returns exactly the real limited entry",
                {e["id"] for e in body.get("entries", [])} == {"model-b"},
            )


def test_catalog_safe_filters_narrow_a_mixed_entry_set() -> None:
    with _patched(
        tenant_scoped_session_for=_fake_tenant_scoped_session_for,
        get_connection=lambda *a, **k: _fake_connection(),
        get_project=_ok_get_project,
        discover_models=lambda **k: _discovery_result(("model-a", "model-b")),
        enrich_visible_models=lambda **k: _empty_enrichment(),
        enrich_visible_model_groups=lambda **k: _empty_group_enrichment(),
    ):
        connection_id = uuid.uuid4()
        with _running_app() as base_url:
            # First call: both callable.
            r = _request(base_url, "GET", catalog_path(connection_id), principal="admin")
            check("FTF-0214", "the first discovery call succeeds", r.status == 200)

            # Every subsequent call in this test keeps discovering only
            # model-a -- each GET re-discovers fresh by design (freshness,
            # Section 3.3), so model-b must stay `removed` across every one
            # of these calls, not just a single one, for the filter checks
            # below to mean anything real.
            with _patched(discover_models=lambda **k: _discovery_result(("model-a",))):
                r = _request(base_url, "GET", catalog_path(connection_id), principal="admin")
                check("FTF-0214", "the second discovery call succeeds", r.status == 200)
                body = r.json() if r.status == 200 else {}
                check(
                    "FTF-0214",
                    "a model no longer discovered is marked removed, not deleted",
                    {e["id"]: e["status"] for e in body.get("entries", [])}
                    == {"model-a": "callable", "model-b": "removed"},
                )

                r = _request(
                    base_url,
                    "GET",
                    catalog_path(connection_id, "?status=callable"),
                    principal="admin",
                )
                body = r.json() if r.status == 200 else {}
                check(
                    "FTF-0214",
                    "a safe status=callable filter returns exactly the real callable entry",
                    {e["id"] for e in body.get("entries", [])} == {"model-a"},
                )

                r = _request(
                    base_url,
                    "GET",
                    catalog_path(connection_id, "?status=removed"),
                    principal="admin",
                )
                body = r.json() if r.status == 200 else {}
                check(
                    "FTF-0214",
                    "a safe status=removed filter returns exactly the real removed entry",
                    {e["id"] for e in body.get("entries", [])} == {"model-b"},
                )

                r = _request(base_url, "GET", catalog_path(connection_id), principal="admin")
                body = r.json() if r.status == 200 else {}
                check(
                    "FTF-0214",
                    "no filter returns every real entry, both callable and removed",
                    {e["id"] for e in body.get("entries", [])} == {"model-a", "model-b"},
                )


def test_probe_requires_project_id_and_valid_body() -> None:
    project_id = uuid.uuid4()
    with _running_app() as base_url:
        r = _request(
            base_url, "POST", probe_path(uuid.uuid4(), "model-a"), principal="admin", payload={}
        )
        check(
            "FTF-0214",
            "negative_acceptance: no automatic project selection -- a probe with no "
            "project_id is refused (400)",
            r.status == 400,
        )

        # A valid probe_type is included so a bad project_id can only be
        # caught by its own guard -- never masked by a later, unrelated
        # 400 (e.g. a missing probe_type) that would survive even if this
        # guard were removed entirely.
        r = _request(
            base_url,
            "POST",
            probe_path(uuid.uuid4(), "model-a", "?project_id=not-a-uuid"),
            principal="admin",
            payload={"probe_type": "chat"},
        )
        check(
            "FTF-0214",
            "a malformed project_id on probe is refused with its own real reason (400)",
            r.status == 400 and r.json().get("detail") == "project_id must be a UUID",
        )

        r = _request(
            base_url,
            "POST",
            probe_path(uuid.uuid4(), "model-a", f"?project_id={project_id}"),
            principal="admin",
            raw_body=b"{not valid json",
        )
        check(
            "FTF-0214",
            "an unparseable JSON body is refused with its own real reason (400)",
            r.status == 400 and r.json().get("detail") == "request body must be valid JSON",
        )

        r = _request(
            base_url,
            "POST",
            probe_path(uuid.uuid4(), "model-a", f"?project_id={project_id}"),
            principal="admin",
            payload=["chat"],
        )
        check(
            "FTF-0214",
            "a JSON body that is not an object is refused with its own real reason (400)",
            r.status == 400 and r.json().get("detail") == "request body must be a JSON object",
        )

        r = _request(
            base_url,
            "POST",
            probe_path(uuid.uuid4(), "model-a", f"?project_id={project_id}"),
            principal="admin",
            payload={"probe_type": "not-a-real-kind"},
        )
        check("FTF-0214", "an unrecognized probe_type is refused (400)", r.status == 400)
        r = _request(
            base_url,
            "POST",
            probe_path(uuid.uuid4(), "model-a", f"?project_id={project_id}"),
            principal="admin",
            payload={"probe_type": 12345},
        )
        check("FTF-0214", "a non-string probe_type is refused (400)", r.status == 400)


def test_probe_missing_project_is_rejected_before_any_dial() -> None:
    with _patched(
        tenant_scoped_session_for=_fake_tenant_scoped_session_for,
        get_project=_raising_get_project,
    ):
        with _running_app() as base_url:
            r = _request(
                base_url,
                "POST",
                probe_path(uuid.uuid4(), "model-a", f"?project_id={uuid.uuid4()}"),
                principal="admin",
                payload={"probe_type": "chat"},
            )
            check(
                "FTF-0214",
                "a nonexistent project on the probe route is rejected (404)",
                r.status == 404,
            )


def test_probe_missing_connection_is_404() -> None:
    with _patched(
        tenant_scoped_session_for=_fake_tenant_scoped_session_for,
        get_project=_ok_get_project,
        get_connection=_raising_get_connection,
    ):
        with _running_app() as base_url:
            r = _request(
                base_url,
                "POST",
                probe_path(uuid.uuid4(), "model-a", f"?project_id={uuid.uuid4()}"),
                principal="admin",
                payload={"probe_type": "chat"},
            )
            check(
                "FTF-0214",
                "a genuinely missing connection on the probe route is denied "
                "identically to foreign (404)",
                r.status == 404,
            )


def test_probe_denial_from_require_model_access_is_surfaced() -> None:
    from packages.litellm_client.access_guard import (
        ModelAccessDenialReason,
        ModelAccessDeniedError,
    )

    def _deny(*args: Any, **kwargs: Any) -> None:
        raise ModelAccessDeniedError(ModelAccessDenialReason.NOT_IN_CATALOG)

    with _patched(
        tenant_scoped_session_for=_fake_tenant_scoped_session_for,
        get_project=_ok_get_project,
        get_connection=lambda *a, **k: _fake_connection(),
        require_model_access=_deny,
    ):
        with _running_app() as base_url:
            r = _request(
                base_url,
                "POST",
                probe_path(uuid.uuid4(), "model-a", f"?project_id={uuid.uuid4()}"),
                principal="admin",
                payload={"probe_type": "chat"},
            )
            check(
                "FTF-0214",
                "a real H004 access denial is surfaced as 403 with its real reason",
                r.status == 403 and "not_in_catalog" in r.json().get("detail", ""),
            )


def test_filters_and_probe_kind_are_exact_match_never_normalized() -> None:
    # Negative: the real default code, with a case/whitespace-variant of a valid
    # value present, must refuse it -- never silently normalize it into a match.
    with _running_app() as base_url:
        for variant in ("Callable", " callable", "callable "):
            r = _request(
                base_url,
                "GET",
                catalog_path(uuid.uuid4(), "?status=" + urllib.parse.quote(variant)),
                principal="admin",
            )
            check(
                "FTF-0214",
                f"status filter {variant!r} is not normalized into a valid value (400)",
                r.status == 400,
            )
        for variant in ("Limited", " limited", "limited "):
            r = _request(
                base_url,
                "GET",
                catalog_path(uuid.uuid4(), "?metadata_status=" + urllib.parse.quote(variant)),
                principal="admin",
            )
            check(
                "FTF-0214",
                f"metadata_status filter {variant!r} is not normalized (400)",
                r.status == 400,
            )
        for variant in ("Chat", " chat", "chat ", "TOOL_CALL"):
            r = _request(
                base_url,
                "POST",
                probe_path(uuid.uuid4(), "model-a", "?project_id=" + str(uuid.uuid4())),
                principal="admin",
                payload={"probe_type": variant},
            )
            check(
                "FTF-0214",
                f"probe_type {variant!r} is not normalized into a valid kind (400)",
                r.status == 400,
            )


def test_probe_dispatches_each_kind_to_its_own_real_probe_function() -> None:
    def _result_for(kind: str) -> Any:
        usage = ChatUsage(prompt_tokens=1, completion_tokens=1, total_tokens=2)
        if kind == "chat":
            return ChatProbeResult(
                state=ChatProbeState.PROBED,
                route_identity_state=RouteIdentityState.VERIFIED,
                resolved_model="model-a",
                usage=usage,
                latency_ms=1.0,
                detail="",
                probed_at=_NOW,
            )
        if kind == "streaming":
            return StreamProbeResult(
                state=StreamProbeState.PROBED,
                route_identity_state=RouteIdentityState.VERIFIED,
                resolved_model="model-a",
                text="pong",
                tool_calls=(),
                termination_reason=StreamTerminationReason.STOP,
                usage=usage,
                chunk_count=1,
                latency_ms=1.0,
                detail="",
                probed_at=_NOW,
            )
        if kind == "tool_call":
            return ToolCallProbeResult(
                state=ToolCallProbeState.PROBED,
                route_identity_state=RouteIdentityState.VERIFIED,
                resolved_model="model-a",
                advertised_tool_name="get_probe_time",
                observed_tool_name="get_probe_time",
                observed_arguments="{}",
                usage=usage,
                latency_ms=1.0,
                detail="",
                probed_at=_NOW,
            )
        return StructuredOutputProbeResult(
            state=StructuredOutputProbeState.PROBED,
            route_identity_state=RouteIdentityState.VERIFIED,
            resolved_model="model-a",
            parsed_content={"word": "pong", "count": 1},
            usage=usage,
            latency_ms=1.0,
            detail="",
            probed_at=_NOW,
        )

    for kind in ("chat", "streaming", "tool_call", "structured_output"):
        calls = {"chat": 0, "streaming": 0, "tool_call": 0, "structured_output": 0}

        def _mk(name: str, counters: dict[str, int] = calls):
            def _f(**kwargs: Any) -> Any:
                counters[name] += 1
                return _result_for(name)

            return _f

        with _patched(
            tenant_scoped_session_for=_fake_tenant_scoped_session_for,
            get_project=_ok_get_project,
            get_connection=lambda *a, **k: _fake_connection(),
            require_model_access=lambda *a, **k: None,
            probe_chat=_mk("chat"),
            probe_stream_chat=_mk("streaming"),
            probe_tool_call=_mk("tool_call"),
            probe_structured_output=_mk("structured_output"),
        ):
            with _running_app() as base_url:
                r = _request(
                    base_url,
                    "POST",
                    probe_path(uuid.uuid4(), "model-a", f"?project_id={uuid.uuid4()}"),
                    principal="admin",
                    payload={"probe_type": kind},
                )
                check(
                    f"FTF-0214-{kind}",
                    f"a real {kind} probe request succeeds (200)",
                    r.status == 200,
                )
                check(
                    f"FTF-0214-{kind}",
                    f"a {kind} probe dispatches to exactly its own real probe function, no other",
                    calls == {k: (1 if k == kind else 0) for k in calls},
                )
                body = r.json() if r.status == 200 else {}
                check(
                    f"FTF-0214-{kind}",
                    f"the real {kind} probe result's probe_type is echoed back",
                    body.get("probe_type") == kind,
                )


# -- app.main.create_app: this task's own router is genuinely mounted ------
#
# `main.py`'s own `app.include_router(build_model_catalog_router(...))`
# addition sits inside `create_app`, so this change's own mutation targets
# include the whole function. Reuses `tests/tasks/test_ftf_0103a.py`'s own
# already-CERTIFIED no-DB env fixture (`create_engine`/`sessionmaker` are
# lazy -- no real connection is ever attempted at app-build time).


def _full_app_env() -> dict[str, str]:
    return {
        "SOVRA_ENVIRONMENT": "staging",
        "SOVRA_DATABASE_URL": "postgresql+psycopg://sovra_app:s3cr3t@db.internal:5432/sovra",
        "SOVRA_LITELLM_GATEWAY_URL": "https://litellm.internal.example",
        "SOVRA_PRIVACY_POLICY": "standard",
        "SOVRA_TLS_VERIFY": "true",
        "SOVRA_MAX_TRAINING_BUDGET_USD": "500.00",
        "SOVRA_LITELLM_API_KEY_SECRET_REF": "vault://sovra/litellm-api-key",
        "SOVRA_DATABASE_PASSWORD_SECRET_REF": "openbao://sovra/db-password",
        # Plain http:// OIDC endpoints -- deliberately rejected in
        # production, accepted in staging (this is also this test's own
        # discriminator for the real `production` flag resolution below).
        "SOVRA_OIDC_ISSUER": "http://idp.internal.example",
        "SOVRA_OIDC_CLIENT_ID": "sovra-factory",
        "SOVRA_OIDC_CLIENT_SECRET_REF": "vault://sovra/oidc-client-secret",
        "SOVRA_OIDC_REDIRECT_URI": "http://factory.internal.example/auth/callback",
        "SOVRA_OIDC_AUTHORIZATION_ENDPOINT": "http://idp.internal.example/authorize",
        "SOVRA_OIDC_TOKEN_ENDPOINT": "http://idp.internal.example/token",
        "SOVRA_OIDC_JWKS_URI": "http://idp.internal.example/jwks",
        "SOVRA_SESSION_SIGNING_KEY_SECRET_REF": "vault://sovra/session-signing-key",
        "SOVRA_OIDC_ALLOWED_ORIGIN": "http://factory.internal.example",
        "SOVRA_OIDC_CLIENT_SECRET_VALUE": "not-a-real-secret",
        "SOVRA_SESSION_SIGNING_KEY_VALUE": "not-a-real-signing-key",
    }


def test_create_app_mounts_the_real_catalog_router_and_resolves_production_flag() -> None:
    env = _full_app_env()
    added = set(env) - set(os.environ)
    old = {k: os.environ[k] for k in env if k in os.environ}
    os.environ.update(env)
    try:
        import app.main as main_module

        app = main_module.create_app()
    finally:
        for key in added:
            os.environ.pop(key, None)
        os.environ.update(old)

    check(
        "FTF-0214",
        "create_app returns a real app, not a short-circuited None",
        app is not None,
    )

    # Real HTTP calls, not internal route introspection (FastAPI wraps an
    # included router opaquely) -- an unauthenticated 401 (rather than a
    # routing 404) proves this task's own two routes are genuinely
    # mounted and reachable on the real, fully-built app. The role gate
    # runs before any session/DB access, so this needs no real database.
    with _running_uvicorn(app) as base_url:
        r = _request(base_url, "GET", catalog_path(uuid.uuid4()))
        check(
            "FTF-0214",
            "this task's own catalog route is genuinely mounted and reachable (401, not 404)",
            r.status == 401,
        )
        r = _request(
            base_url, "POST", probe_path(uuid.uuid4(), "model-a", f"?project_id={uuid.uuid4()}")
        )
        check(
            "FTF-0214",
            "this task's own probe route is genuinely mounted and reachable (401, not 404)",
            r.status == 401,
        )


def main() -> None:
    test_serialize_entry_round_trips_real_fields()
    test_serialize_entry_never_fabricates_a_timestamp_for_a_never_callable_entry()
    test_serialize_anomaly_none_stays_none()
    test_serialize_anomaly_real_record()
    test_serialize_usage_none_and_real()
    test_serialize_probe_result_chat()
    test_serialize_probe_result_tool_call()
    test_serialize_probe_result_structured_output()
    test_serialize_probe_result_streaming()

    test_unauthenticated_catalog_and_probe_are_denied()
    test_viewer_is_denied_catalog_and_probe()
    test_catalog_invalid_status_filter_is_rejected()
    test_catalog_invalid_metadata_filter_is_rejected()
    test_catalog_missing_connection_is_404()
    test_catalog_bad_project_id_and_not_found_project_are_rejected()
    test_catalog_happy_path_composes_discovery_and_enrichment()
    test_catalog_revoked_connection_never_calls_discover_models()
    test_catalog_revoked_after_discovery_shows_revoked_and_stale_never_healthy()
    test_catalog_non_revoked_status_still_calls_discover_models()
    test_catalog_only_the_literal_discovered_state_triggers_enrichment()
    test_catalog_non_discovered_result_never_crashes_and_skips_groups()
    test_catalog_metadata_filter_narrows_a_mixed_metadata_set()
    test_catalog_safe_filters_narrow_a_mixed_entry_set()
    test_probe_requires_project_id_and_valid_body()
    test_probe_missing_project_is_rejected_before_any_dial()
    test_probe_missing_connection_is_404()
    test_probe_denial_from_require_model_access_is_surfaced()
    test_filters_and_probe_kind_are_exact_match_never_normalized()
    test_probe_dispatches_each_kind_to_its_own_real_probe_function()
    test_create_app_mounts_the_real_catalog_router_and_resolves_production_flag()

    if FAILURES:
        print(f"FTF-0214 tests: {len(FAILURES)} failed")
        for failure in FAILURES:
            print(f"  {failure}")
        sys.exit(1)

    print("FTF-0214 tests: all checks passed")
    sys.exit(0)


if __name__ == "__main__":
    main()
