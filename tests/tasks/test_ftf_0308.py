#!/usr/bin/env python3
"""FTF-0308 "Model-to-artifact mapping UI" -- no-DB, no-external-egress
tests for the new `GET .../models/{model_id}/training-capability` route
and its pure serializers in `app.litellm.model_catalog_routes`.

This task's own packet names a frontend-only `files` list; the real
backend composition this route needs (reusing `GET .../catalog`'s own
discovery/enrichment/reconciliation, FTF-0301's `artifact_crud.get_artifact`
and FTF-0307's `classify_training_capability`) is a disclosed refinement,
the same precedent this file's own FTF-0214 docstring already applied.
This file covers the DB-free surface directly: `_serialize_artifact_
provenance`/`_serialize_training_capability`/`_empty_reconciliation`
against real dataclass instances, and the route itself with
`tenant_scoped_session_for`/`get_connection`/`get_artifact`/
`discover_models`/`enrich_visible_models` monkeypatched (the exact same
technique `tests/tasks/test_ftf_0214.py` already established) -- never a
real database or a real gateway, while `reconcile_aliases` and
`classify_training_capability` stay real so the actual composition is
exercised, not just its call sites.

Real relevant integration (real Postgres, a real `TrainableArtifact` row,
the real route, role/IDOR boundaries): `tests/integration/
test_ftf_0308_model_artifact_mapping.py`.

Run directly: python3 tests/tasks/test_ftf_0308.py
"""

from __future__ import annotations

import contextlib
import http.client
import json
import sys
import threading
import time
import urllib.parse
import uuid
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

ROOT = Path(__file__).resolve().parent.parent.parent
API_ROOT = ROOT / "apps" / "api"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(API_ROOT))

import uvicorn  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from packages.litellm_client.alias_reconciliation import (  # noqa: E402
    AliasReconciliationResult,
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
from app.litellm import model_catalog_routes as mcr  # noqa: E402
from app.litellm.connection_crud import ConnectionNotFoundError  # noqa: E402
from app.litellm.model_catalog_routes import build_model_catalog_router  # noqa: E402

FAILURES: list[str] = []


def check(criterion_id: str, description: str, condition: bool) -> None:
    if not condition:
        FAILURES.append(f"FAIL [{criterion_id}] {description}")


_NOW = datetime(2026, 10, 7, 12, 0, 0, tzinfo=UTC)
_ORG_ID = uuid.uuid4()
_FOREIGN_ORG_ID = uuid.uuid4()
_ISSUER = "https://issuer.example"
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
    import socket

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
) -> _HttpResult:
    headers: dict[str, str] = {}
    if principal is not None:
        headers["X-Test-Principal"] = principal
    parsed = urllib.parse.urlparse(base_url)
    conn = http.client.HTTPConnection(parsed.hostname or "127.0.0.1", parsed.port or 80, timeout=10)
    try:
        conn.request(method, path, headers=headers)
        response = conn.getresponse()
        raw = response.read()
        return _HttpResult(status=response.status, body=raw)
    finally:
        conn.close()


def capability_path(connection_id: object, model_id: str, query: str = "") -> str:
    return (
        f"/api/v1/organizations/{_ORG_ID}/connections/litellm/{connection_id}"
        f"/models/{model_id}/training-capability{query}"
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
    # `expunge` is a no-op here (a SimpleNamespace fixture never expires the
    # way a real SQLAlchemy session does) -- the route's own real
    # `session.expunge(artifact)` call is proven for real only by
    # `tests/integration/test_ftf_0308_model_artifact_mapping.py` against a
    # real Postgres session, not this no-DB fixture.
    yield SimpleNamespace(expunge=lambda obj: None)


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


def _raising_get_artifact(session: Any, tenant_context: Any, artifact_id: Any) -> Any:
    raise ArtifactNotFoundError("not found")


def _fake_artifact(
    *,
    artifact_id: uuid.UUID | None = None,
    organization_id: uuid.UUID = _ORG_ID,
    status: str = "ready",
    weights_digest: str | None = "sha256:" + "a" * 64,
    license_accepted_at: datetime | None = _NOW,
) -> SimpleNamespace:
    return SimpleNamespace(
        id=artifact_id if artifact_id is not None else uuid.uuid4(),
        organization_id=organization_id,
        display_name="llama-3-8b-base",
        source_type="huggingface",
        source_uri="meta-llama/Meta-Llama-3-8B",
        model_family="llama-3",
        base_model_revision="abc123def456",
        weights_digest=weights_digest,
        license_id="llama-3-community",
        status=status,
        trust_remote_code_allowed=False,
        required_python_packages_json=["transformers>=4.40"],
        minimum_gpu_profile_json={"min_gpu_vram_gb": 24},
        license_accepted_at=license_accepted_at,
    )


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


def _unreachable_discovery_result() -> ModelDiscoveryResult:
    return ModelDiscoveryResult(
        state=DiscoveryState.UNREACHABLE,
        models=(),
        model_count=None,
        content_hash=None,
        detail="gateway returned unexpected status 503",
        checked_at=_NOW,
    )


def _empty_enrichment() -> ModelInfoEnrichmentResult:
    return ModelInfoEnrichmentResult(
        state=EnrichmentState.COMPLETED, entries=(), detail="", checked_at=_NOW
    )


def _raising_discover_models(**kwargs: Any) -> Any:
    raise AssertionError("discover_models must never be called for a revoked connection")


# -- Pure serializer/helper tests -------------------------------------------


def test_empty_reconciliation_is_a_real_zero_model_result() -> None:
    result = mcr._empty_reconciliation(now=_NOW)
    check(
        "FTF-0308",
        "_empty_reconciliation is a genuine AliasReconciliationResult with zero models",
        isinstance(result, AliasReconciliationResult)
        and result.models == ()
        and result.model_count == 0,
    )


def test_serialize_artifact_provenance_round_trips_real_fields() -> None:
    artifact = _fake_artifact()
    body = mcr._serialize_artifact_provenance(artifact)  # type: ignore[arg-type]
    check(
        "FTF-0308",
        "artifact provenance serializer carries display_name/source/family/revision/digest/license",
        body["display_name"] == artifact.display_name
        and body["source_type"] == artifact.source_type
        and body["source_uri"] == artifact.source_uri
        and body["model_family"] == artifact.model_family
        and body["base_model_revision"] == artifact.base_model_revision
        and body["weights_digest"] == artifact.weights_digest
        and body["license_id"] == artifact.license_id,
    )
    check(
        "FTF-0308",
        "artifact provenance serializer carries limits (required packages / gpu profile)",
        body["required_python_packages"] == ["transformers>=4.40"]
        and body["minimum_gpu_profile"] == {"min_gpu_vram_gb": 24},
    )


def test_serialize_training_capability_null_artifact_when_none_given() -> None:
    reconciliation = AliasReconciliationResult(models=(), model_count=0, reconciled_at=_NOW)
    from app.artifacts.training_capability_classifier import classify_training_capability

    classification = classify_training_capability(
        model_id="model-a", organization_id=_ORG_ID, reconciliation=reconciliation, artifact=None
    )
    body = mcr._serialize_training_capability(classification, None)
    check(
        "FTF-0308",
        "no artifact given -> artifact field is null, never a fabricated placeholder object",
        body["artifact"] is None,
    )
    check(
        "FTF-0308",
        "no artifact given -> capability is unknown (fail-closed default)",
        body["capability"] == "unknown" and body["artifact_availability"] == "none",
    )


# -- Real FastAPI/uvicorn route-level tests, no DB ---------------------------


def test_unauthenticated_and_viewer_are_denied() -> None:
    with _running_app() as base_url:
        r = _request(base_url, "GET", capability_path(uuid.uuid4(), "model-a"))
        check(
            "FTF-0308",
            "the route is genuinely mounted and reachable (401, not 404) when unauthenticated",
            r.status == 401,
        )
        r = _request(base_url, "GET", capability_path(uuid.uuid4(), "model-a"), principal="viewer")
        check(
            "FTF-0308",
            "a viewer principal is denied (403), same role gate as GET .../catalog",
            r.status == 403,
        )


def test_missing_connection_is_404() -> None:
    with _patched(
        tenant_scoped_session_for=_fake_tenant_scoped_session_for,
        get_connection=_raising_get_connection,
    ):
        with _running_app() as base_url:
            r = _request(
                base_url, "GET", capability_path(uuid.uuid4(), "model-a"), principal="admin"
            )
            check(
                "FTF-0308",
                "a genuinely missing/foreign connection is denied (404)",
                r.status == 404,
            )


def test_malformed_artifact_id_is_rejected() -> None:
    with _patched(
        tenant_scoped_session_for=_fake_tenant_scoped_session_for,
        get_connection=lambda *a, **k: _fake_connection(status="revoked"),
    ):
        with _running_app() as base_url:
            r = _request(
                base_url,
                "GET",
                capability_path(uuid.uuid4(), "model-a", "?artifact_id=not-a-uuid"),
                principal="admin",
            )
            check(
                "FTF-0308",
                "a malformed artifact_id is rejected (400), never silently ignored",
                r.status == 400,
            )


def test_foreign_artifact_id_is_404_same_as_missing() -> None:
    with _patched(
        tenant_scoped_session_for=_fake_tenant_scoped_session_for,
        get_connection=lambda *a, **k: _fake_connection(status="revoked"),
        get_artifact=_raising_get_artifact,
    ):
        with _running_app() as base_url:
            artifact_id = uuid.uuid4()
            r = _request(
                base_url,
                "GET",
                capability_path(uuid.uuid4(), "model-a", f"?artifact_id={artifact_id}"),
                principal="admin",
            )
            check(
                "FTF-0308",
                "negative_acceptance / IDOR: a foreign-organization artifact_id is denied (404), "
                "identical to a nonexistent one -- never leaks cross-tenant existence",
                r.status == 404,
            )


def test_revoked_connection_never_calls_discover_models() -> None:
    with _patched(
        tenant_scoped_session_for=_fake_tenant_scoped_session_for,
        get_connection=lambda *a, **k: _fake_connection(status="revoked"),
        get_artifact=lambda *a, **k: _fake_artifact(),
        discover_models=_raising_discover_models,
    ):
        with _running_app() as base_url:
            r = _request(
                base_url, "GET", capability_path(uuid.uuid4(), "model-a"), principal="admin"
            )
            check(
                "FTF-0308",
                "a revoked connection never dials the gateway again (GET .../catalog's own rule)",
                r.status == 200,
            )
            body = r.json()
            check(
                "FTF-0308",
                "a revoked connection's reconciliation is empty -> access not_granted",
                body["access"] == "not_granted",
            )


def test_no_artifact_id_given_is_unknown_capability() -> None:
    with _patched(
        tenant_scoped_session_for=_fake_tenant_scoped_session_for,
        get_connection=lambda *a, **k: _fake_connection(status="active"),
        discover_models=lambda **k: _discovery_result(("model-a",)),
        enrich_visible_models=lambda **k: _empty_enrichment(),
    ):
        with _running_app() as base_url:
            r = _request(
                base_url, "GET", capability_path(uuid.uuid4(), "model-a"), principal="admin"
            )
            check(
                "FTF-0308",
                "no artifact_id given -> 200 with a structurally unknown capability",
                r.status == 200,
            )
            body = r.json()
            check(
                "FTF-0308",
                "negative_acceptance: no artifact selected -> never a guessed capability",
                body["capability"] == "unknown" and body["artifact"] is None,
            )
            check(
                "FTF-0308",
                "access is still independently reported even with no artifact selected",
                body["access"] == "granted",
            )


def test_ready_artifact_with_valid_digest_and_license_is_trainable() -> None:
    artifact_id = uuid.uuid4()
    artifact = _fake_artifact(artifact_id=artifact_id)
    with _patched(
        tenant_scoped_session_for=_fake_tenant_scoped_session_for,
        get_connection=lambda *a, **k: _fake_connection(status="active"),
        get_artifact=lambda *a, **k: artifact,
        discover_models=lambda **k: _discovery_result(("model-a",)),
        enrich_visible_models=lambda **k: _empty_enrichment(),
    ):
        with _running_app() as base_url:
            r = _request(
                base_url,
                "GET",
                capability_path(uuid.uuid4(), "model-a", f"?artifact_id={artifact_id}"),
                principal="admin",
            )
            check("FTF-0308", "a resolvable artifact_id is accepted (200)", r.status == 200)
            body = r.json()
            check(
                "FTF-0308",
                "acceptance: a ready, license-accepted, digest-resolved artifact is trainable",
                body["capability"] == "trainable_open_weight",
            )
            check(
                "FTF-0308",
                "the response carries real provenance/limits, not a bare id",
                body["artifact"]["display_name"] == "llama-3-8b-base",
            )
            check(
                "FTF-0308",
                "license_approved reflects the real artifact field",
                body["license_approved"] is True,
            )
            check(
                "FTF-0308",
                "artifact_id in the body matches the real artifact row",
                body["artifact_id"] == str(artifact_id),
            )


def test_archived_artifact_is_never_trainable_even_if_a_digest_is_present() -> None:
    artifact = _fake_artifact(status="archived")
    with _patched(
        tenant_scoped_session_for=_fake_tenant_scoped_session_for,
        get_connection=lambda *a, **k: _fake_connection(status="active"),
        get_artifact=lambda *a, **k: artifact,
        discover_models=lambda **k: _discovery_result(("model-a",)),
        enrich_visible_models=lambda **k: _empty_enrichment(),
    ):
        with _running_app() as base_url:
            r = _request(
                base_url,
                "GET",
                capability_path(uuid.uuid4(), "model-a", f"?artifact_id={artifact.id}"),
                principal="admin",
            )
            body = r.json()
            check(
                "FTF-0308",
                "negative_acceptance: an archived artifact is never trainable, digest aside",
                body["capability"] != "trainable_open_weight"
                and body["artifact_availability"] == "archived",
            )


def test_ready_artifact_without_a_valid_digest_fails_closed() -> None:
    # Defense in depth: a caller-supplied artifact that claims status=ready
    # but carries no valid resolved digest (should be impossible through
    # the real apply_mark_ready gate, but this route never trusts a single
    # upstream check alone -- same discipline classify_training_capability's
    # own docstring already states) never classifies as trainable.
    artifact = _fake_artifact(status="ready", weights_digest=None)
    with _patched(
        tenant_scoped_session_for=_fake_tenant_scoped_session_for,
        get_connection=lambda *a, **k: _fake_connection(status="active"),
        get_artifact=lambda *a, **k: artifact,
        discover_models=lambda **k: _discovery_result(("model-a",)),
        enrich_visible_models=lambda **k: _empty_enrichment(),
    ):
        with _running_app() as base_url:
            r = _request(
                base_url,
                "GET",
                capability_path(uuid.uuid4(), "model-a", f"?artifact_id={artifact.id}"),
                principal="admin",
            )
            body = r.json()
            check(
                "FTF-0308",
                "fail-closed: status=ready with no valid weights_digest never classifies trainable",
                body["capability"] != "trainable_open_weight",
            )


def test_non_discovered_result_never_crashes_and_yields_not_granted() -> None:
    with _patched(
        tenant_scoped_session_for=_fake_tenant_scoped_session_for,
        get_connection=lambda *a, **k: _fake_connection(status="active"),
        get_artifact=lambda *a, **k: _fake_artifact(),
        discover_models=lambda **k: _unreachable_discovery_result(),
    ):
        with _running_app() as base_url:
            r = _request(
                base_url,
                "GET",
                capability_path(uuid.uuid4(), "model-a", f"?artifact_id={uuid.uuid4()}"),
                principal="admin",
            )
            check(
                "FTF-0308",
                "an UNREACHABLE discovery result never crashes the route (200)",
                r.status == 200,
            )
            body = r.json()
            check(
                "FTF-0308",
                "access falls back to not_granted when discovery never reached DISCOVERED, "
                "while capability (an independent dimension) is still computed from the artifact",
                body["access"] == "not_granted",
            )


def test_changing_the_mapping_recomputes_fresh_never_reuses_a_stale_result() -> None:
    """negative_acceptance: "Changing mapping invalidates related qualification" --
    there is no server-side cache keyed on model_id, so two calls with two
    different artifact_id values for the exact same model_id must return
    two independently correct classifications, never the first call's
    result replayed for the second."""
    ready = _fake_artifact(status="ready")
    archived = _fake_artifact(status="archived")
    artifacts = {ready.id: ready, archived.id: archived}
    with _patched(
        tenant_scoped_session_for=_fake_tenant_scoped_session_for,
        get_connection=lambda *a, **k: _fake_connection(status="active"),
        get_artifact=lambda session, ctx, artifact_id: artifacts[artifact_id],
        discover_models=lambda **k: _discovery_result(("model-a",)),
        enrich_visible_models=lambda **k: _empty_enrichment(),
    ):
        with _running_app() as base_url:
            r1 = _request(
                base_url,
                "GET",
                capability_path(uuid.uuid4(), "model-a", f"?artifact_id={ready.id}"),
                principal="admin",
            ).json()
            r2 = _request(
                base_url,
                "GET",
                capability_path(uuid.uuid4(), "model-a", f"?artifact_id={archived.id}"),
                principal="admin",
            ).json()
            check(
                "FTF-0308",
                "changing the mapped artifact_id changes the returned capability (no stale reuse)",
                r1["capability"] == "trainable_open_weight"
                and r2["capability"] != "trainable_open_weight",
            )
            check(
                "FTF-0308",
                "each response's artifact_id matches its own request, never the previous call's",
                r1["artifact_id"] == str(ready.id) and r2["artifact_id"] == str(archived.id),
            )


def main() -> None:
    test_empty_reconciliation_is_a_real_zero_model_result()
    test_serialize_artifact_provenance_round_trips_real_fields()
    test_serialize_training_capability_null_artifact_when_none_given()

    test_unauthenticated_and_viewer_are_denied()
    test_missing_connection_is_404()
    test_malformed_artifact_id_is_rejected()
    test_foreign_artifact_id_is_404_same_as_missing()
    test_revoked_connection_never_calls_discover_models()
    test_no_artifact_id_given_is_unknown_capability()
    test_ready_artifact_with_valid_digest_and_license_is_trainable()
    test_archived_artifact_is_never_trainable_even_if_a_digest_is_present()
    test_ready_artifact_without_a_valid_digest_fails_closed()
    test_non_discovered_result_never_crashes_and_yields_not_granted()
    test_changing_the_mapping_recomputes_fresh_never_reuses_a_stale_result()

    if FAILURES:
        print(f"FTF-0308 tests: {len(FAILURES)} failed")
        for failure in FAILURES:
            print(f"  {failure}")
        sys.exit(1)

    print("FTF-0308 tests: all checks passed")
    sys.exit(0)


if __name__ == "__main__":
    main()
