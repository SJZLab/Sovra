#!/usr/bin/env python3
# mypy: disable-error-code="arg-type,attr-defined,return-value,assignment,call-arg"
# ruff: noqa: E501
"""FTF-0309-P1 -- training-capability read API (route tests over real HTTP,
DB/gateway seams replaced; the real-Postgres evidence is
tests/integration/test_ftf_0309_p1_model_capabilities.py).

Criteria (QUEUE Backlog, FTF-0309-P1), one behavioural check group each:
  D1  a ready mapped artifact -> trainable with artifact id and digest.
  D2  an unmapped model -> unknown/not trainable, never inferred from the alias.
  D3  a probe for another model or an unverified route identity is ignored.
  D4  missing/foreign project, connection and artifact -> identical 404.
  D5  viewer may read (no admin dependency); project_id is required (400).
  D6  a revoked connection is shown revoked, never trainable, never dialed.
  D7  discovery runs exactly once per request; a non-discovered state is 503.
  D8  method_support / min_gpu_vram_gb / chat_template are explicit unknown;
      the artifact's GPU profile is labelled declared.
  C1  03-interface.yaml declares the route; main.py mounts it.

Run directly: python3 tests/tasks/test_ftf_0309_p1.py
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
from types import SimpleNamespace
from typing import Any

import uvicorn
import yaml
from fastapi import FastAPI

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "apps" / "api"))

from packages.litellm_client.real_chat_probe import RouteIdentityState  # noqa: E402
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

from app.artifacts.artifact_crud import ArtifactNotFoundError  # noqa: E402
from app.auth.rbac_middleware import AuthenticatedPrincipal, Role  # noqa: E402
from app.litellm import model_capability_routes as mcr  # noqa: E402
from app.litellm.connection_crud import ConnectionNotFoundError  # noqa: E402
from app.projects.project_crud import ProjectNotFoundError  # noqa: E402

FAILURES: list[str] = []
CID = "FTF-0309-P1"


def check(criterion_id: str, description: str, condition: bool) -> None:
    if not condition:
        FAILURES.append(f"FAIL [{criterion_id}] {description}")


_NOW = datetime(2026, 10, 8, 12, 0, 0, tzinfo=UTC)
_PROBED_AT = datetime(2026, 10, 7, 9, 30, 0, tzinfo=UTC)
_ISSUER = "https://issuer.example"
_ORG_ID = uuid.uuid4()
_CONN_ID = uuid.uuid4()
_PROJECT_ID = uuid.uuid4()
_ART_ID = uuid.uuid4()
_DIGEST = "sha256:" + "b" * 64
_PRINCIPALS = {
    "admin": AuthenticatedPrincipal(issuer=_ISSUER, subject="admin-1"),
    "viewer": AuthenticatedPrincipal(issuer=_ISSUER, subject="viewer-1"),
}
_ROLES = {"admin-1": Role.ORGANIZATION_ADMIN, "viewer-1": Role.VIEWER}


def _principal_resolver(request: Any) -> AuthenticatedPrincipal | None:
    return _PRINCIPALS.get(request.headers.get("x-test-principal", ""))


def _role_lookup(issuer: str, subject: str, organization_id: uuid.UUID) -> Role | None:
    return _ROLES.get(subject) if organization_id == _ORG_ID else None


def _free_port() -> int:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


def _discovered(*ids: str) -> ModelDiscoveryResult:
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


def _probe(model: str | None, *, verified: bool = True, state=ToolCallProbeState.PROBED):
    return ToolCallProbeResult(
        state=state,
        route_identity_state=RouteIdentityState.VERIFIED
        if verified
        else RouteIdentityState.UNVERIFIED,
        resolved_model=model,
        advertised_tool_name="t",
        observed_tool_name="t",
        observed_arguments="{}",
        usage=None,
        latency_ms=1.0,
        detail="",
        probed_at=_PROBED_AT,
    )


class _Discovery:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.result = _discovered("llama-trainable", "gpt-x")

    def __call__(self, **kwargs: Any) -> ModelDiscoveryResult:
        self.calls.append(kwargs)
        return self.result


def _enrichment(**kwargs: Any) -> ModelInfoEnrichmentResult:
    return ModelInfoEnrichmentResult(
        state=EnrichmentState.COMPLETED, entries=(), detail="", checked_at=_NOW
    )


class _Env:
    """Replaces every DB-touching name the routes module imported."""

    def __init__(self) -> None:
        self.discovery = _Discovery()
        self.probe: ToolCallProbeResult | None = None
        self.probe_calls: list[tuple[Any, ...]] = []
        self.project_missing = False
        self.connection_missing = False
        self.connection_statuses = ["active", "active"]
        self.entry: SimpleNamespace | None = None
        self.artifact: SimpleNamespace | None = None
        self.artifact_missing = False

    def probe_lookup(self, org: Any, conn: Any, model: str) -> ToolCallProbeResult | None:
        self.probe_calls.append((org, conn, model))
        return self.probe

    def get_project(self, session: Any, ctx: Any, project_id: Any) -> Any:
        if self.project_missing or project_id != _PROJECT_ID:
            raise ProjectNotFoundError("nope")
        return SimpleNamespace(id=project_id)

    def get_connection(self, session: Any, ctx: Any, connection_id: Any) -> Any:
        if self.connection_missing:
            raise ConnectionNotFoundError("nope")
        status = self.connection_statuses.pop(0) if self.connection_statuses else "active"
        return SimpleNamespace(
            status=status, base_url="https://gw.example", secret_ref="openbao://secret/k"
        )

    def get_entry(self, session: Any, ctx: Any, **kwargs: Any) -> Any:
        return self.entry

    def get_artifact(self, session: Any, ctx: Any, artifact_id: Any) -> Any:
        if self.artifact_missing:
            raise ArtifactNotFoundError("nope")
        assert self.artifact is not None
        return self.artifact


def _artifact(**overrides: Any) -> SimpleNamespace:
    values: dict[str, Any] = {
        "id": _ART_ID,
        "organization_id": _ORG_ID,
        "status": "ready",
        "weights_digest": _DIGEST,
        "license_accepted_at": _NOW,
        "minimum_gpu_profile_json": {"gpu": "a100", "vram_gb": 40},
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _entry(artifact_id: uuid.UUID | None = _ART_ID, version: int = 3) -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid.uuid4(), trainable_artifact_id=artifact_id, catalog_version=version
    )


@contextlib.contextmanager
def _session_cm(session_factory: Any, tenant_context: Any):
    yield object()


@contextlib.contextmanager
def _running(env: _Env):
    names = {
        "tenant_scoped_session_for": _session_cm,
        "get_project": env.get_project,
        "get_connection": env.get_connection,
        "get_entry": env.get_entry,
        "get_artifact": env.get_artifact,
    }
    originals = {name: getattr(mcr, name) for name in names}
    for name, value in names.items():
        setattr(mcr, name, value)
    app = FastAPI()
    app.include_router(
        mcr.build_model_capability_router(
            lambda: None,
            principal_resolver=_principal_resolver,
            role_lookup=_role_lookup,
            secret_manager_config_factory=lambda: None,
            validation_policy_factory=lambda: SimpleNamespace(),
            discovery_function=env.discovery,
            enrichment_function=_enrichment,
            probe_lookup=env.probe_lookup,
        )
    )
    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
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
        for name, value in originals.items():
            setattr(mcr, name, value)


def _get(
    base: str,
    model: str = "llama-trainable",
    *,
    principal: str | None = "viewer",
    query: str | None = None,
) -> tuple[int, Any]:
    if query is None:
        query = f"?project_id={_PROJECT_ID}"
    path = (
        f"/api/v1/organizations/{_ORG_ID}/connections/litellm/{_CONN_ID}"
        f"/models/{model}/capabilities{query}"
    )
    headers = {} if principal is None else {"X-Test-Principal": principal}
    parsed = urllib.parse.urlparse(base)
    conn = http.client.HTTPConnection(parsed.hostname or "127.0.0.1", parsed.port or 80, timeout=10)
    try:
        conn.request("GET", path, headers=headers)
        response = conn.getresponse()
        raw = response.read()
        return response.status, json.loads(raw) if raw else None
    finally:
        conn.close()


def test_d1_ready_mapped_artifact_is_trainable() -> None:
    env = _Env()
    env.entry, env.artifact = _entry(), _artifact()
    with _running(env) as base:
        status, body = _get(base)
    check(CID, "D1 200", status == 200)
    check(
        CID,
        "D1 ready mapped artifact -> trainable_open_weight with id, digest, version",
        body["capability"] == "trainable_open_weight"
        and body["trainable"] is True
        and body["access"] == "granted"
        and body["artifact"]["artifact_id"] == str(_ART_ID)
        and body["artifact"]["availability"] == "ready"
        and body["artifact"]["weights_digest"] == _DIGEST
        and body["artifact"]["license_approved"] is True
        and body["catalog_version"] == 3
        and body["connection_status"] == "active",
    )
    env2 = _Env()
    env2.entry, env2.artifact = _entry(), _artifact(status="registered")
    with _running(env2) as base:
        _, body2 = _get(base)
    check(
        CID,
        "D1 a registered (not ready) artifact is not trainable",
        body2["capability"] == "unknown" and body2["trainable"] is False,
    )
    env3 = _Env()
    env3.entry, env3.artifact = _entry(), _artifact(weights_digest="sha256:short")
    with _running(env3) as base:
        _, body3 = _get(base)
    check(
        CID,
        "D1 a ready artifact without a valid digest is not trainable",
        body3["trainable"] is False,
    )
    env4 = _Env()
    env4.entry, env4.artifact = _entry(), _artifact(organization_id=uuid.uuid4())
    with _running(env4) as base:
        status4, _body4 = _get(base)
    check(CID, "D1 a cross-org artifact object is never classified: 404 not_found", status4 == 404)


def test_d2_unmapped_model_is_never_inferred_from_the_alias() -> None:
    env = _Env()
    env.entry = None
    env.discovery.result = _discovered("llama-trainable-open-weight", "gpt-x")
    with _running(env) as base:
        status, body = _get(base, "llama-trainable-open-weight")
    check(
        CID,
        "D2 unmapped alias whose name says 'trainable' is unknown and not trainable",
        status == 200
        and body["capability"] == "unknown"
        and body["trainable"] is False
        and body["artifact"]["artifact_id"] is None
        and body["artifact"]["availability"] == "none"
        and body["catalog_version"] == 0
        and body["capability_reason"] == "no_trainable_evidence",
    )
    env2 = _Env()
    env2.entry = _entry(artifact_id=None, version=2)
    with _running(env2) as base:
        _, body2 = _get(base)
    check(
        CID,
        "D2 an entry that was unmapped is unknown at its stored version",
        body2["capability"] == "unknown" and body2["catalog_version"] == 2,
    )
    env3 = _Env()
    env3.entry, env3.artifact = _entry(), _artifact()
    env3.discovery.result = _discovered("gpt-x")
    with _running(env3) as base:
        _, body3 = _get(base)
    check(
        CID,
        "D2 access and capability are independent: mapped but not listed -> not_granted + trainable",
        body3["access"] == "not_granted" and body3["capability"] == "trainable_open_weight",
    )


def test_d3_probe_for_another_model_or_route_identity_is_ignored() -> None:
    cases = [
        ("a different resolved model", _probe("other-model")),
        ("an unverified route identity", _probe("llama-trainable", verified=False)),
        (
            "a failed probe state",
            _probe("llama-trainable", state=ToolCallProbeState.NO_TOOL_CALL_ISSUED),
        ),
        ("an unresolved model", _probe(None)),
    ]
    for label, probe in cases:
        env = _Env()
        env.probe = probe
        with _running(env) as base:
            _, body = _get(base)
        check(
            CID,
            f"D3 {label} is ignored: unknown, not inference_only",
            body["capability"] == "unknown",
        )
    env = _Env()
    env.probe = _probe("llama-trainable")
    with _running(env) as base:
        _, body = _get(base)
    check(
        CID,
        "D3 a matching verified probe is dated evidence for inference_only",
        body["capability"] == "inference_only"
        and body["trainable"] is False
        and body["evidence"]["tool_call_probe_checked_at"] == _PROBED_AT.isoformat()
        and body["evidence"]["tool_call_probe_present"] is True
        and env.probe_calls == [(_ORG_ID, _CONN_ID, "llama-trainable")],
    )
    env = _Env()
    with _running(env) as base:
        _, body = _get(base)
    check(
        CID,
        "D3 with no probe the evidence says so (no probe is ever run by a GET)",
        body["evidence"]["tool_call_probe_checked_at"] is None
        and body["evidence"]["tool_call_probe_present"] is False,
    )


def test_d4_missing_or_foreign_resources_are_identical_404() -> None:
    bodies = []
    for label, setup in (
        ("project", lambda e: setattr(e, "project_missing", True)),
        ("connection", lambda e: setattr(e, "connection_missing", True)),
        (
            "artifact",
            lambda e: e.__dict__.update(artifact_missing=True, entry=_entry()),
        ),
    ):
        env = _Env()
        setup(env)
        with _running(env) as base:
            status, body = _get(base)
        check(
            CID,
            f"D4 missing {label} is 404 not_found",
            status == 404 and body == {"detail": "not_found"},
        )
        bodies.append(body)
    check(CID, "D4 the three 404 bodies are identical", bodies[0] == bodies[1] == bodies[2])
    env = _Env()
    with _running(env) as base:
        status, _ = _get(base, query=f"?project_id={uuid.uuid4()}")
    check(CID, "D4 a foreign/unknown project is 404", status == 404)
    check(CID, "D4 a missing project never reaches discovery", not env.discovery.calls)


def test_d5_roles_and_required_project() -> None:
    env = _Env()
    with _running(env) as base:
        check(CID, "D5 viewer may read", _get(base, principal="viewer")[0] == 200)
        check(CID, "D5 admin may read", _get(base, principal="admin")[0] == 200)
        check(CID, "D5 no principal is 401", _get(base, principal=None)[0] == 401)
        check(CID, "D5 missing project_id is 400", _get(base, query="")[0] == 400)
        check(CID, "D5 non-uuid project_id is 400", _get(base, query="?project_id=nope")[0] == 400)
        check(CID, "D5 over-long model name is 400", _get(base, "n" * 257)[0] == 400)
    n = len(env.discovery.calls)
    check(CID, "D5 only the two authorized reads reached discovery", n == 2)


def test_d6_revoked_connection_is_shown_revoked_never_trainable() -> None:
    env = _Env()
    env.connection_statuses = ["revoked", "revoked"]
    env.entry, env.artifact = _entry(), _artifact()
    env.probe = _probe("llama-trainable")
    with _running(env) as base:
        status, body = _get(base)
    check(
        CID,
        "D6 revoked -> 200 shown revoked, unknown, not trainable, not granted",
        status == 200
        and body["connection_status"] == "revoked"
        and body["capability"] == "unknown"
        and body["trainable"] is False
        and body["access"] == "not_granted"
        and body["capability_reason"] == "connection_revoked",
    )
    check(CID, "D6 a revoked connection is never dialed", not env.discovery.calls)
    env2 = _Env()
    env2.connection_statuses = ["active", "revoked"]
    env2.entry, env2.artifact = _entry(), _artifact()
    with _running(env2) as base:
        _, body2 = _get(base)
    check(
        CID,
        "D6 a revoke that lands during discovery wins",
        body2["connection_status"] == "revoked" and body2["trainable"] is False,
    )


def test_d7_one_discovery_per_request_and_gateway_failures() -> None:
    env = _Env()
    env.entry, env.artifact = _entry(), _artifact()
    with _running(env) as base:
        _get(base)
        check(CID, "D7 exactly one discovery for one request", len(env.discovery.calls) == 1)
        _get(base)
        check(CID, "D7 exactly one more for the next request", len(env.discovery.calls) == 2)
    for state in (DiscoveryState.TIMEOUT, DiscoveryState.INVALID_TOKEN):
        env = _Env()
        env.entry, env.artifact = _entry(), _artifact()
        env.discovery.result = ModelDiscoveryResult(
            state=state, models=(), model_count=None, content_hash=None, detail="", checked_at=_NOW
        )
        with _running(env) as base:
            status, body = _get(base)
        check(
            CID,
            f"D7 discovery state {state.value} is 503 gateway_unavailable, never a capability",
            status == 503 and body == {"detail": "gateway_unavailable"},
        )


def test_d8_unknown_dimensions_are_explicit_and_declared_is_labelled() -> None:
    env = _Env()
    env.entry, env.artifact = _entry(), _artifact()
    with _running(env) as base:
        _, body = _get(base)
    check(
        CID,
        "D8 method_support, chat_template are explicit unknown with a reason",
        body["method_support"] == {"state": "unknown", "reason": "no_backing_evidence"}
        and body["chat_template"] == {"state": "unknown", "reason": "no_backing_evidence"},
    )
    vram = body["min_gpu_vram_gb"]
    check(
        CID,
        "D8 tested VRAM is unknown; the artifact profile is labelled declared, never tested",
        vram["state"] == "unknown"
        and vram["reason"] == "not_tested"
        and vram["declared_evidence"] == "declared"
        and vram["declared_minimum_gpu_profile"] == {"gpu": "a100", "vram_gb": 40},
    )


def test_c1_contract_and_mount() -> None:
    spec = yaml.safe_load((ROOT / "packages/contracts/openapi/03-interface.yaml").read_text())
    key = (
        "/api/v1/organizations/{organization_id}/connections/litellm/{connection_id}"
        "/models/{model_name}/capabilities"
    )
    path = spec["paths"].get(key)
    check(CID, "C1 03-interface.yaml declares the route", path is not None)
    if path is not None:
        names = {p["name"]: p for p in path["parameters"]}
        check(
            CID,
            "C1 project_id is a required query parameter",
            names["project_id"]["required"] is True,
        )
        check(CID, "C1 GET only", "get" in path and "post" not in path)
    check(
        CID,
        "C1 the response schema is declared",
        "ModelCapabilities" in spec["components"]["schemas"],
    )
    main_src = (ROOT / "apps/api/app/main.py").read_text()
    check(
        CID,
        "C1 main.py mounts the router",
        "build_model_capability_router(" in main_src
        and "from app.litellm.model_capability_routes import" in main_src,
    )


def test_x_body_echoes_identifiers_and_has_the_exact_key_set() -> None:
    env = _Env()
    env.entry, env.artifact = _entry(), _artifact()
    with _running(env) as base:
        _, body = _get(base)
    check(
        CID,
        "X the body echoes connection, project and model name",
        body["litellm_connection_id"] == str(_CONN_ID)
        and body["project_id"] == str(_PROJECT_ID)
        and body["public_model_name"] == "llama-trainable",
    )
    check(
        CID,
        "X the body has exactly the frozen key set (role present as null, detail non-empty)",
        set(body)
        == {
            "litellm_connection_id",
            "project_id",
            "public_model_name",
            "connection_status",
            "access",
            "capability",
            "trainable",
            "capability_detail",
            "capability_reason",
            "role",
            "artifact",
            "catalog_version",
            "method_support",
            "min_gpu_vram_gb",
            "chat_template",
            "evidence",
        }
        and body["role"] is None
        and isinstance(body["capability_detail"], str)
        and body["capability_detail"] != ""
        and body["capability_reason"] is None,
    )
    check(
        CID,
        "X evidence carries the dated discovery check",
        body["evidence"]["discovery_checked_at"] == _NOW.isoformat(),
    )


def test_x_model_name_length_boundary() -> None:
    env = _Env()
    with _running(env) as base:
        check(CID, "X a 256-character model name is accepted", _get(base, "n" * 256)[0] == 200)
        check(CID, "X a 257-character model name is 400", _get(base, "n" * 257)[0] == 400)


def test_x_connection_status_is_compared_exactly() -> None:
    for statuses in ([" revoked", " revoked"], ["Revoked", "Revoked"], ["revoked ", "revoked "]):
        env = _Env()
        env.connection_statuses = list(statuses)
        env.entry, env.artifact = _entry(), _artifact()
        with _running(env) as base:
            _, body = _get(base)
        check(
            CID,
            f"X status {statuses[0]!r} is not 'revoked': it is not normalised into revoked",
            body["connection_status"] == statuses[0] and body["trainable"] is True,
        )
        check(
            CID,
            f"X status {statuses[0]!r} reaches discovery (only the exact string revokes)",
            len(env.discovery.calls) == 1,
        )
    env = _Env()
    env.connection_statuses = ["active", " revoked"]
    env.entry, env.artifact = _entry(), _artifact()
    with _running(env) as base:
        _, body = _get(base)
    check(
        CID,
        "X the in-transaction re-read also compares exactly",
        body["connection_status"] == "active" and body["trainable"] is True,
    )


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


def test_x_create_app_mounts_the_capabilities_route() -> None:
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
    check(CID, "X create_app returns a real FastAPI app", isinstance(app, FastAPI))
    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(200):
        if server.started:
            break
        time.sleep(0.02)
    try:
        status, _ = _get(f"http://127.0.0.1:{port}", principal=None)
    finally:
        server.should_exit = True
        thread.join(timeout=5)
    check(CID, "X .../capabilities is mounted on the real app (401, not 404)", status == 401)


def main() -> None:
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
            except Exception as exc:  # noqa: BLE001 - a crash is a failed test, reported by name
                FAILURES.append(f"FAIL [{CID}] {name} raised {type(exc).__name__}: {exc}")
    if FAILURES:
        print(f"FTF-0309-P1 capability route tests: {len(FAILURES)} failed")
        for failure in FAILURES:
            print(f"  {failure}")
        sys.exit(1)
    print("FTF-0309-P1 capability route tests: all checks passed")
    sys.exit(0)


if __name__ == "__main__":
    main()
