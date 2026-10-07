#!/usr/bin/env python3
"""FTF-0308 "Model-to-artifact mapping UI" -- real Postgres proof for the
new `GET .../models/{model_id}/training-capability` route added to
`app.litellm.model_catalog_routes` (see that module's own docstring for
why this task's own packet-named frontend-only `files` list needed this
backend refinement, the same precedent FTF-0214 already established).

Proves this task's own acceptance clause ("UI maps explicit accessible
alias to verified trainable artifact and displays provenance/limits")
and negative_acceptance ("Changing mapping invalidates related
qualification; no guessed weight identity from LiteLLM alias") through
the real, live route -- never a mocked route handler or a direct call to
FTF-0307's `classify_training_capability` bypassing the HTTP layer. Real
`TrainableArtifact` rows are driven to `ready` through the real,
already-CERTIFIED `build_trainable_artifact_router` (FTF-0301) mounted on
the same app/session_factory, the same two-router-on-one-app composition
`tests/integration/test_ftf_h004_access_guard.py` already established for
a different pair of sibling routers.

No real LiteLLM gateway is required: every connection's `secret_ref`
points at a secret this process's own (default, unconfigured) secret
backend cannot resolve, so `discover_models` deterministically returns a
real, typed, non-`DISCOVERED` failure state without ever dialing a
network socket -- the same "never raises for an ordinary connectivity/
secret failure" guarantee `v1_models_discovery.py`'s own docstring
already documents. This proves the route's real empty-reconciliation
fallback path for real, while keeping this file free of the TLS/sudo
machinery `test_ftf_0214_model_catalog.py` needs only for its own
`DISCOVERED`-state assertions (out of this task's own scope).

Requires a real Postgres reachable at `SOVRA_DATABASE_URL` (the migration
owner role) plus `SOVRA_APP_DB_PASSWORD`, matching every sibling
integration test's own environment contract.

Run directly:
    SOVRA_DATABASE_URL=postgresql+psycopg://<migration-role>@host/db \\
    SOVRA_APP_DB_PASSWORD=<app-role-password> \\
    python3 tests/integration/test_ftf_0308_model_artifact_mapping.py
"""

from __future__ import annotations

import contextlib
import http.client
import json
import os
import socket
import subprocess
import sys
import threading
import time
import urllib.parse
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

ROOT = Path(__file__).resolve().parent.parent.parent
API_ROOT = ROOT / "apps" / "api"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(API_ROOT))

import uvicorn  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import Session, sessionmaker  # noqa: E402

from app.artifacts.artifact_routes import build_trainable_artifact_router  # noqa: E402
from app.auth.organization_workspace_schema import APP_ROLE_NAME, Organization  # noqa: E402
from app.auth.rbac_middleware import AuthenticatedPrincipal, Role  # noqa: E402
from app.litellm.connection_schema import LiteLLMConnection  # noqa: E402
from app.litellm.model_catalog_routes import build_model_catalog_router  # noqa: E402

FAILURES: list[str] = []


def check(criterion_id: str, description: str, condition: bool) -> None:
    if not condition:
        FAILURES.append(f"FAIL [{criterion_id}] {description}")


def _required_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        print(
            f"BLOCKED_EXTERNAL: {name} is not set -- this task's real "
            "mapping/capability evidence requires a live Postgres "
            "(migration owner connection). Not silently skipped: see this "
            "file's own docstring for how to run it locally or in CI.",
            file=sys.stderr,
        )
        sys.exit(1)
    return value


def _app_role_url(migration_url: str, app_password: str) -> str:
    parts = urlsplit(migration_url)
    netloc = f"{APP_ROLE_NAME}:{app_password}@{parts.hostname}"
    if parts.port:
        netloc += f":{parts.port}"
    return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))


def _run_migration(database_url: str) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["SOVRA_DATABASE_URL"] = database_url
    return subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=str(API_ROOT),
        capture_output=True,
        text=True,
        env=env,
    )


def free_port() -> int:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


@contextlib.contextmanager
def _running_app(app: FastAPI) -> Iterator[str]:
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


class _Response:
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
    payload: dict[str, object] | None = None,
) -> _Response:
    headers: dict[str, str] = {}
    if principal is not None:
        headers["X-Test-Principal"] = principal
    body_bytes: bytes | None = None
    if payload is not None:
        body_bytes = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    parsed = urllib.parse.urlparse(base_url)
    conn = http.client.HTTPConnection(parsed.hostname or "127.0.0.1", parsed.port or 80, timeout=15)
    try:
        conn.request(method, path, body=body_bytes, headers=headers)
        response = conn.getresponse()
        raw = response.read()
        return _Response(status=response.status, body=raw)
    finally:
        conn.close()


# -- Fixture: two organizations, admin/viewer/ml_engineer principals each --

_ISSUER = "https://issuer.example"


class _Fixture:
    def __init__(self, org_ids: dict[str, uuid.UUID]) -> None:
        self.org_ids = org_ids
        self.principals: dict[tuple[str, Role], AuthenticatedPrincipal] = {}
        self.memberships: dict[tuple[str, str, uuid.UUID], Role] = {}
        for label, org_id in org_ids.items():
            for role in (Role.ORGANIZATION_ADMIN, Role.VIEWER, Role.ML_ENGINEER):
                subject = f"user-{label}-{role.value}"
                principal = AuthenticatedPrincipal(issuer=_ISSUER, subject=subject)
                self.principals[(label, role)] = principal
                self.memberships[(_ISSUER, subject, org_id)] = role

    def header_for(self, label: str, role: Role) -> str:
        return f"{label}:{role.value}"

    def principal_resolver(self, request):
        header = request.headers.get("x-test-principal")
        if header is None:
            return None
        label, _, role_value = header.partition(":")
        try:
            role = Role(role_value)
        except ValueError:
            return None
        return self.principals.get((label, role))

    def role_lookup(self, issuer: str, subject: str, organization_id: uuid.UUID):
        return self.memberships.get((issuer, subject, organization_id))


def main() -> None:
    migration_url = _required_env("SOVRA_DATABASE_URL")
    app_password = _required_env("SOVRA_APP_DB_PASSWORD")

    migration_result = _run_migration(migration_url)
    check(
        "FTF-0308",
        f"alembic upgrade head runs cleanly (exit {migration_result.returncode})\n"
        f"stdout: {migration_result.stdout}\nstderr: {migration_result.stderr}",
        migration_result.returncode == 0,
    )
    if FAILURES:
        print(f"FTF-0308 real model-to-artifact mapping proof: {len(FAILURES)} failed")
        for failure in FAILURES:
            print(f"  {failure}")
        sys.exit(1)

    owner_engine = create_engine(migration_url)
    app_url = _app_role_url(migration_url, app_password)
    app_engine = create_engine(app_url)

    # -- Seed two real, distinct tenants (migration-owner role, seeding only) --
    with Session(owner_engine) as session:
        org1 = Organization(name="Acme 0308", slug=f"acme-0308-{uuid.uuid4().hex[:8]}")
        org2 = Organization(name="Globex 0308", slug=f"globex-0308-{uuid.uuid4().hex[:8]}")
        session.add_all([org1, org2])
        session.commit()
        org1_id, org2_id = org1.id, org2.id

        # Every secret_ref below is deliberately unresolvable by this
        # process's own (unconfigured) secret backend -- discover_models
        # therefore returns a real, typed non-DISCOVERED failure state
        # without ever dialing a network socket (see this file's own
        # docstring). No live LiteLLM gateway is required.
        active_conn = LiteLLMConnection(
            organization_id=org1_id,
            name="org1-active",
            base_url="https://gateway.example",
            secret_ref="openbao://secret/litellm/org1/api_key",
            status="active",
        )
        revoked_conn = LiteLLMConnection(
            organization_id=org1_id,
            name="org1-revoked",
            base_url="https://gateway.example",
            secret_ref="openbao://secret/litellm/org1/api_key",
            status="revoked",
        )
        session.add_all([active_conn, revoked_conn])
        session.commit()
        active_conn_id, revoked_conn_id = active_conn.id, revoked_conn.id

    fixture = _Fixture({"org1": org1_id, "org2": org2_id})
    session_factory = sessionmaker(bind=app_engine)
    app = FastAPI()
    app.include_router(
        build_trainable_artifact_router(
            session_factory,
            principal_resolver=fixture.principal_resolver,
            role_lookup=fixture.role_lookup,
        )
    )
    app.include_router(
        build_model_catalog_router(
            session_factory,
            principal_resolver=fixture.principal_resolver,
            role_lookup=fixture.role_lookup,
        )
    )

    org1_admin = fixture.header_for("org1", Role.ORGANIZATION_ADMIN)
    org1_viewer = fixture.header_for("org1", Role.VIEWER)
    org1_ml = fixture.header_for("org1", Role.ML_ENGINEER)
    org2_ml = fixture.header_for("org2", Role.ML_ENGINEER)

    def artifacts_path(org_id: uuid.UUID) -> str:
        return f"/api/v1/organizations/{org_id}/artifacts"

    def artifact_item_path(org_id: uuid.UUID, artifact_id: object) -> str:
        return f"{artifacts_path(org_id)}/{artifact_id}"

    def capability_path(
        org_id: uuid.UUID, connection_id: object, model_id: str, query: str = ""
    ) -> str:
        return (
            f"/api/v1/organizations/{org_id}/connections/litellm/{connection_id}"
            f"/models/{model_id}/training-capability{query}"
        )

    def _create_ready_artifact(org_id: uuid.UUID, principal: str, display_name: str) -> str:
        r = _request(
            base_url,
            "POST",
            artifacts_path(org_id),
            principal=principal,
            payload={
                "display_name": display_name,
                "source_type": "huggingface",
                "source_uri": "meta-llama/Meta-Llama-3-8B",
                "model_family": "llama-3",
                "base_model_revision": "main",
                "license_id": "llama3",
            },
        )
        check("FTF-0308", f"creating artifact {display_name} succeeds (201)", r.status == 201)
        aid = r.json()["id"]
        r = _request(
            base_url,
            "PATCH",
            artifact_item_path(org_id, aid),
            principal=principal,
            payload={
                "weights_digest": "sha256:" + "b" * 64,
                "base_model_revision": "a" * 40,
            },
        )
        check(
            "FTF-0308",
            f"pinning digest/revision for {display_name} succeeds (200)",
            r.status == 200,
        )
        r = _request(
            base_url,
            "POST",
            artifact_item_path(org_id, aid) + "/accept-license",
            principal=principal,
        )
        check("FTF-0308", f"accepting license for {display_name} succeeds (200)", r.status == 200)
        r = _request(
            base_url, "POST", artifact_item_path(org_id, aid) + "/mark-ready", principal=principal
        )
        check("FTF-0308", f"mark-ready for {display_name} succeeds (200)", r.status == 200)
        check("FTF-0308", f"{display_name} is genuinely ready", r.json().get("status") == "ready")
        return aid

    with _running_app(app) as base_url:
        # -- Role-boundary denial (same gate GET .../catalog already uses) --
        r = _request(base_url, "GET", capability_path(org1_id, active_conn_id, "model-a"))
        check(
            "FTF-0308",
            "an unauthenticated request is denied (401), route genuinely reachable",
            r.status == 401,
        )
        r = _request(
            base_url,
            "GET",
            capability_path(org1_id, active_conn_id, "model-a"),
            principal=org1_viewer,
        )
        check("FTF-0308", "a viewer principal is denied (403)", r.status == 403)

        # -- Real org1 ready artifact, created through the real FTF-0301 route --
        org1_artifact_id = _create_ready_artifact(org1_id, org1_ml, "llama-8b-mapping-target")

        # -- Real org2 artifact (any state) to prove the cross-tenant denial --
        r = _request(
            base_url,
            "POST",
            artifacts_path(org2_id),
            principal=org2_ml,
            payload={
                "display_name": "globex-own-artifact",
                "source_type": "huggingface",
                "source_uri": "meta-llama/Meta-Llama-3-8B",
                "model_family": "llama-3",
                "base_model_revision": "main",
                "license_id": "llama3",
            },
        )
        check("FTF-0308", "creating org2's own artifact succeeds (201)", r.status == 201)
        org2_artifact_id = r.json()["id"]

        # -- Malformed artifact_id is rejected --
        r = _request(
            base_url,
            "GET",
            capability_path(org1_id, active_conn_id, "model-a", "?artifact_id=not-a-uuid"),
            principal=org1_admin,
        )
        check("FTF-0308", "a malformed artifact_id is rejected (400)", r.status == 400)

        # -- Missing connection is 404 --
        r = _request(
            base_url, "GET", capability_path(org1_id, uuid.uuid4(), "model-a"), principal=org1_admin
        )
        check("FTF-0308", "a nonexistent connection is denied (404)", r.status == 404)

        # -- negative_acceptance / IDOR: org2's real artifact_id is denied under org1 --
        r = _request(
            base_url,
            "GET",
            capability_path(org1_id, active_conn_id, "model-a", f"?artifact_id={org2_artifact_id}"),
            principal=org1_admin,
        )
        check(
            "FTF-0308",
            "negative_acceptance/IDOR: a real but foreign-organization artifact_id is denied "
            "(404), identical to a nonexistent one -- no cross-tenant existence oracle",
            r.status == 404,
        )

        # -- No artifact_id given: structurally unknown, never guessed --
        r = _request(
            base_url,
            "GET",
            capability_path(org1_id, active_conn_id, "model-a"),
            principal=org1_admin,
        )
        check("FTF-0308", "a request with no artifact_id succeeds (200)", r.status == 200)
        body = r.json()
        check(
            "FTF-0308",
            "negative_acceptance: with no artifact explicitly selected, capability is "
            "unknown and artifact is null -- never a name-based guess from the alias",
            body.get("capability") == "unknown" and body.get("artifact") is None,
        )
        check(
            "FTF-0308",
            "access is independently reported even with no artifact selected, and a "
            "deliberately-unresolvable secret_ref yields not_granted without a live gateway",
            body.get("access") == "not_granted",
        )

        # -- acceptance: the real org1 ready artifact maps to a real trainable classification --
        r = _request(
            base_url,
            "GET",
            capability_path(org1_id, active_conn_id, "model-a", f"?artifact_id={org1_artifact_id}"),
            principal=org1_admin,
        )
        check("FTF-0308", "a real, owned, ready artifact_id succeeds (200)", r.status == 200)
        body = r.json()
        check(
            "FTF-0308",
            "acceptance: a real ready+license-accepted+digest-resolved artifact classifies "
            "trainable_open_weight, and the response carries real provenance/limits",
            body.get("capability") == "trainable_open_weight"
            and body.get("license_approved") is True
            and isinstance(body.get("artifact"), dict)
            and body["artifact"].get("display_name") == "llama-8b-mapping-target"
            and body["artifact"].get("minimum_gpu_profile") == {},
        )
        check(
            "FTF-0308",
            "the response's artifact_id is the real row's own id",
            body.get("artifact_id") == org1_artifact_id,
        )

        # -- negative_acceptance: changing the mapping (org1 ready vs org1's still-registered
        # artifact) recomputes fresh, never reuses the prior call's classification --
        r = _request(
            base_url,
            "POST",
            artifacts_path(org1_id),
            principal=org1_ml,
            payload={
                "display_name": "not-yet-ready-artifact",
                "source_type": "huggingface",
                "source_uri": "meta-llama/Meta-Llama-3-8B",
                "model_family": "llama-3",
                "base_model_revision": "main",
                "license_id": "llama3",
            },
        )
        check(
            "FTF-0308",
            "creating a second, still-registered org1 artifact succeeds (201)",
            r.status == 201,
        )
        registered_artifact_id = r.json()["id"]

        r2 = _request(
            base_url,
            "GET",
            capability_path(
                org1_id, active_conn_id, "model-a", f"?artifact_id={registered_artifact_id}"
            ),
            principal=org1_admin,
        )
        body2 = r2.json()
        check(
            "FTF-0308",
            "negative_acceptance: re-mapping to a different, not-yet-ready artifact changes "
            "the classification away from trainable_open_weight -- no stale reuse of the "
            "prior call's result",
            body2.get("capability") != "trainable_open_weight"
            and body2.get("artifact_id") == registered_artifact_id,
        )

        # -- revoked connection: never errors, access falls back to not_granted --
        r = _request(
            base_url,
            "GET",
            capability_path(
                org1_id, revoked_conn_id, "model-a", f"?artifact_id={org1_artifact_id}"
            ),
            principal=org1_admin,
        )
        check(
            "FTF-0308",
            "a revoked connection's capability read still succeeds (200)",
            r.status == 200,
        )
        revoked_body = r.json()
        check(
            "FTF-0308",
            "a revoked connection yields not_granted access, while capability (an "
            "independent dimension, Section 0.12) is still computed from the real artifact",
            revoked_body.get("access") == "not_granted"
            and revoked_body.get("capability") == "trainable_open_weight",
        )

    if FAILURES:
        print(f"FTF-0308 real model-to-artifact mapping proof: {len(FAILURES)} failed")
        for failure in FAILURES:
            print(f"  {failure}")
        sys.exit(1)

    print("FTF-0308 real model-to-artifact mapping proof: all checks passed")
    sys.exit(0)


if __name__ == "__main__":
    main()
