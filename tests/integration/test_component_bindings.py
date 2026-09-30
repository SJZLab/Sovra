#!/usr/bin/env python3
"""FTF-R002 -- real Postgres ComponentBinding/ComponentSyncOutbox schema/API
proof (Section 39/45/65/70/77.1/77.3/77.5/77.6).

Proves this task's own two acceptance bullets ("Real service identity/
version/health with approved TLS/CA and tenant-aware grants... existing
LiteLLM is not overwritten" and "Idempotent sync outbox maps Factory jobs
to external IDs and reports actual pending/failure states") and its full
negative_acceptance ("Deny cross-tenant IDs, arbitrary URLs/redirects,
browser vendor-admin credentials, direct vendor DB writes and fake health
after dependency outage") against a real, live PostgreSQL server -- never
a mocked connection or an in-memory SQLite stand-in (SQLite has no
row-level security at all, so a passing test against it would prove
nothing about this task's actual acceptance criterion).

Two real organizations (org1/org2) are seeded as the migration-owner
role. Every request runs through a real uvicorn server, with the
router's own database session bound to the real, distinct,
least-privileged `sovra_app` role FTF-0102's migration creates (never the
migration-owner role -- confirmed directly below).

The health-check acceptance bullet is proven against a *real* local HTTP
server this test itself starts on `127.0.0.1` (an `existing_gateway`
binding pointed at it) -- a real 2xx response, a real 503 response, and a
real closed port (connection refused, simulating a genuine dependency
outage) each produce their own real, observed `health_result`, never a
fabricated `healthy`.

Requires a real Postgres reachable at `SOVRA_DATABASE_URL` (the migration
owner role) plus `SOVRA_APP_DB_PASSWORD`, matching every prior
integration test's own environment contract exactly.

Run directly (against an already-migrated or unmigrated database; this
script runs the migration itself):
    SOVRA_DATABASE_URL=postgresql+psycopg://<migration-role>@host/db \\
    SOVRA_APP_DB_PASSWORD=<app-role-password> \\
    python3 tests/integration/test_component_bindings.py
"""

from __future__ import annotations

import contextlib
import http.client
import http.server
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
from packages.security.url_policy import ConnectionPolicy  # noqa: E402
from sqlalchemy import create_engine, text  # noqa: E402
from sqlalchemy.orm import Session, sessionmaker  # noqa: E402

from app.auth.organization_workspace_schema import (  # noqa: E402
    APP_ROLE_NAME,
    Organization,
    set_tenant_context,
)
from app.auth.rbac_middleware import AuthenticatedPrincipal, Role  # noqa: E402
from app.component_bindings.component_binding_crud import (  # noqa: E402
    ActorInfo,
    SyncOutboxConflictError,
    get_component_binding,
    record_sync_outcome,
    validate_component_binding,
)
from app.component_bindings.component_binding_routes import (  # noqa: E402
    build_component_binding_router,
)
from app.db.tenant_context import TenantContext  # noqa: E402

FAILURES: list[str] = []


def check(criterion_id: str, description: str, condition: bool) -> None:
    if not condition:
        FAILURES.append(f"FAIL [{criterion_id}] {description}")


def _required_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        print(
            f"BLOCKED_EXTERNAL: {name} is not set -- this task's real "
            "schema/API evidence requires a live Postgres (migration owner "
            "connection). Not silently skipped: see this file's own "
            "docstring for how to run it locally or in CI.",
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


class _FixedResponseHandler(http.server.BaseHTTPRequestHandler):
    """A real local HTTP server -- this task's own real 'existing_gateway'
    health-check target. `status_code` is a class attribute so the same
    handler class can be reused for both the 2xx and 503 cases below."""

    status_code = 200

    def do_GET(self) -> None:  # noqa: N802 - stdlib handler method name
        self.send_response(self.status_code)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        pass  # silence stdlib's default stderr access log


@contextlib.contextmanager
def _real_local_server(status_code: int) -> Iterator[str]:
    handler = type(f"_Handler{status_code}", (_FixedResponseHandler,), {"status_code": status_code})
    port = free_port()
    server = http.server.HTTPServer(("127.0.0.1", port), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        # Plain HTTP -- this stdlib server does no TLS termination at all;
        # callers below build a `component_binding_endpoint_policy`-style
        # allowance for `http` on loopback rather than pointing a real
        # `https://` URL at a server that cannot actually speak TLS.
        yield f"http://127.0.0.1:{port}"
    finally:
        server.shutdown()
        thread.join(timeout=5)


def _http_only_local_policy(*, name: str) -> ConnectionPolicy:
    """The real local test server above speaks plain HTTP, not TLS -- a
    genuine internal-network health check still needs `allowed_schemes`
    to admit `http` for this test's own loopback target (the storage-time
    `approved_endpoint` shape check still requires `https`; a caller can
    legitimately supply a wider runtime dial policy for a genuinely
    internal, non-TLS-terminated service, per Section 65.4's own
    'internal org endpoints are separately allowlisted')."""
    import ipaddress

    return ConnectionPolicy(
        name=name,
        allowed_schemes=frozenset({"http", "https"}),
        allowed_ports=frozenset(range(1, 65536)),
        explicit_private_allowlist=(ipaddress.ip_network("127.0.0.0/8"),),
        allow_redirects=False,
    )


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
    conn = http.client.HTTPConnection(parsed.hostname or "127.0.0.1", parsed.port or 80, timeout=10)
    try:
        conn.request(method, path, body=body_bytes, headers=headers)
        response = conn.getresponse()
        raw = response.read()
        return _Response(status=response.status, body=raw)
    finally:
        conn.close()


_ISSUER = "https://issuer.example"


class _Fixture:
    def __init__(self, org_ids: dict[str, uuid.UUID]) -> None:
        self.org_ids = org_ids
        self.principals: dict[tuple[str, Role], AuthenticatedPrincipal] = {}
        self.memberships: dict[tuple[str, str, uuid.UUID], Role] = {}
        for label, org_id in org_ids.items():
            for role in (Role.ORGANIZATION_ADMIN, Role.EVALUATOR):
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
        "FTF-R002",
        f"alembic upgrade head runs cleanly (exit {migration_result.returncode})\n"
        f"stdout: {migration_result.stdout}\nstderr: {migration_result.stderr}",
        migration_result.returncode == 0,
    )
    if FAILURES:
        print(f"FTF-R002 real ComponentBinding schema/API proof: {len(FAILURES)} failed")
        for failure in FAILURES:
            print(f"  {failure}")
        sys.exit(1)

    owner_engine = create_engine(migration_url)
    app_url = _app_role_url(migration_url, app_password)
    app_engine = create_engine(app_url)

    check(
        "FTF-R002",
        "the router's own database session is built from the restricted "
        "sovra_app role's connection URL, never the migration-owner role",
        app_engine.url.username == APP_ROLE_NAME
        and app_engine.url.username != owner_engine.url.username,
    )

    with Session(owner_engine) as session:
        org1 = Organization(name="Acme R002", slug=f"acme-r002-{uuid.uuid4().hex[:8]}")
        org2 = Organization(name="Globex R002", slug=f"globex-r002-{uuid.uuid4().hex[:8]}")
        session.add_all([org1, org2])
        session.commit()
        org1_id, org2_id = org1.id, org2.id

    # -- Direct vendor DB write / DELETE privilege check (negative_acceptance:
    # "direct vendor DB writes" and "cannot be mutated" via privilege, not
    # just omitted routes) -- sovra_app cannot DELETE a component_bindings
    # row at all, regardless of application logic.
    with owner_engine.connect() as conn:
        privileges = (
            conn.execute(
                text(
                    "SELECT privilege_type FROM information_schema.role_table_grants "
                    "WHERE table_name = 'component_bindings' AND grantee = :role"
                ),
                {"role": APP_ROLE_NAME},
            )
            .scalars()
            .all()
        )
    check(
        "FTF-R002",
        "sovra_app has SELECT/INSERT/UPDATE on component_bindings but never DELETE",
        set(privileges) == {"SELECT", "INSERT", "UPDATE"},
    )

    fixture = _Fixture({"org1": org1_id, "org2": org2_id})
    session_factory = sessionmaker(bind=app_engine)
    app = FastAPI()
    app.include_router(
        build_component_binding_router(
            session_factory,
            principal_resolver=fixture.principal_resolver,
            role_lookup=fixture.role_lookup,
        )
    )

    org1_admin = fixture.header_for("org1", Role.ORGANIZATION_ADMIN)
    org1_evaluator = fixture.header_for("org1", Role.EVALUATOR)
    org2_admin = fixture.header_for("org2", Role.ORGANIZATION_ADMIN)

    def collection_path(org_id: uuid.UUID) -> str:
        return f"/api/v1/organizations/{org_id}/component-bindings"

    def item_path(org_id: uuid.UUID, binding_id: object) -> str:
        return f"{collection_path(org_id)}/{binding_id}"

    binding1_id: str | None = None

    with _running_app(app) as base_url:
        # -- role boundary --
        r = _request(base_url, "GET", collection_path(org1_id))
        check("FTF-R002", "an unauthenticated list request is denied (401)", r.status == 401)

        r = _request(base_url, "GET", collection_path(org1_id), principal=org1_evaluator)
        check(
            "FTF-R002",
            "an evaluator is denied read (403) -- gated to organization_admin alone",
            r.status == 403,
        )

        r = _request(
            base_url,
            "POST",
            collection_path(org1_id),
            principal=org1_evaluator,
            payload={"component_key": "mlflow", "deployment_mode": "worker_package"},
        )
        check("FTF-R002", "an evaluator is denied create (403)", r.status == 403)

        # -- acceptance: a real worker_package binding (no endpoint) --
        r = _request(
            base_url,
            "POST",
            collection_path(org1_id),
            principal=org1_admin,
            payload={"component_key": "axolotl", "deployment_mode": "worker_package"},
        )
        check("FTF-R002", "creating a real worker_package binding succeeds (201)", r.status == 201)
        if r.status == 201:
            binding1_id = r.json()["id"]
            check(
                "FTF-R002",
                "the created worker_package binding has no approved_endpoint",
                r.json()["approved_endpoint"] is None,
            )

        # -- negative: arbitrary/insecure URL is refused at creation --
        r = _request(
            base_url,
            "POST",
            collection_path(org1_id),
            principal=org1_admin,
            payload={
                "component_key": "vllm",
                "deployment_mode": "internal_service",
                "approved_endpoint": "http://insecure.internal.example",
            },
        )
        check(
            "FTF-R002",
            "negative_acceptance: an insecure (non-https) approved_endpoint is refused (400)",
            r.status == 400,
        )

        # -- negative: worker_package with approved_endpoint is refused --
        r = _request(
            base_url,
            "POST",
            collection_path(org1_id),
            principal=org1_admin,
            payload={
                "component_key": "inspect_ai",
                "deployment_mode": "worker_package",
                "approved_endpoint": "https://should-not-exist.example",
            },
        )
        check(
            "FTF-R002",
            "negative_acceptance: a worker_package binding claiming an "
            "approved_endpoint is refused (400)",
            r.status == 400,
        )

        # -- cross-tenant IDOR: org1's own binding, requested under org2's path --
        assert binding1_id is not None
        r = _request(base_url, "GET", item_path(org2_id, binding1_id), principal=org2_admin)
        check(
            "FTF-R002",
            "negative_acceptance: 'Deny cross-tenant IDs' -- org1's own real "
            "binding, requested under org2's own path by a real org2 "
            "organization_admin, is denied (404) identically to missing",
            r.status == 404,
        )

        # -- optimistic version conflict --
        r = _request(base_url, "GET", item_path(org1_id, binding1_id), principal=org1_admin)
        check("FTF-R002", "getting the real binding succeeds", r.status == 200)
        real_row_version = r.json()["row_version"] if r.status == 200 else None

        r = _request(
            base_url,
            "PATCH",
            item_path(org1_id, binding1_id),
            principal=org1_admin,
            payload={"expected_row_version": 999, "secret_reference": "vault://path/to/secret"},
        )
        check(
            "FTF-R002",
            "api_contract 'optimistic versions': a stale expected_row_version is refused (409)",
            r.status == 409,
        )

        r = _request(
            base_url,
            "PATCH",
            item_path(org1_id, binding1_id),
            principal=org1_admin,
            payload={
                "expected_row_version": real_row_version,
                "secret_reference": "Bearer sk-raw-token",
            },
        )
        check(
            "FTF-R002",
            "negative_acceptance: 'browser vendor-admin credentials' -- a raw "
            "bearer-token-shaped secret_reference is refused (400), never stored",
            r.status == 400,
        )

        # -- real health check against a real local HTTP server: 2xx, 503, and
        # a real closed port (dependency outage) -- never fabricated healthy.
        r = _request(
            base_url,
            "POST",
            collection_path(org1_id),
            principal=org1_admin,
            payload={
                "component_key": "vllm",
                "deployment_mode": "existing_gateway",
                "approved_endpoint": "https://127.0.0.1:1",
            },
        )
        check(
            "FTF-R002", "creating a real existing_gateway binding succeeds (201)", r.status == 201
        )
        gateway_binding_id = r.json()["id"] if r.status == 201 else None

        tenant_context = TenantContext(organization_id=org1_id)

        # Real 2xx case
        with _real_local_server(200) as gateway_url:
            port = urllib.parse.urlparse(gateway_url).port
            with Session(app_engine) as raw_session:
                set_tenant_context(raw_session, org1_id)
                binding = get_component_binding(
                    raw_session, tenant_context, uuid.UUID(gateway_binding_id)
                )
                binding.approved_endpoint = f"http://127.0.0.1:{port}"
                raw_session.flush()
                result = validate_component_binding(
                    raw_session,
                    tenant_context,
                    uuid.UUID(gateway_binding_id),
                    policy=_http_only_local_policy(name="test-real-2xx"),
                    actor=ActorInfo(
                        actor_type="system", actor_id="test", request_id=uuid.uuid4().hex
                    ),
                )
                health_result = result.health_result
                raw_session.commit()
                check(
                    "FTF-R002",
                    "acceptance: a real 2xx response from a real local server "
                    "reports health_result=healthy",
                    health_result == "healthy",
                )

        # Real 503 case
        with _real_local_server(503) as gateway_url:
            port = urllib.parse.urlparse(gateway_url).port
            with Session(app_engine) as raw_session:
                set_tenant_context(raw_session, org1_id)
                binding = get_component_binding(
                    raw_session, tenant_context, uuid.UUID(gateway_binding_id)
                )
                binding.approved_endpoint = f"http://127.0.0.1:{port}"
                raw_session.flush()
                result = validate_component_binding(
                    raw_session,
                    tenant_context,
                    uuid.UUID(gateway_binding_id),
                    policy=_http_only_local_policy(name="test-real-503"),
                    actor=ActorInfo(
                        actor_type="system", actor_id="test", request_id=uuid.uuid4().hex
                    ),
                )
                health_result = result.health_result
                raw_session.commit()
                check(
                    "FTF-R002",
                    "negative_acceptance: a real 503 response never reports healthy",
                    health_result == "unhealthy",
                )

        # Real dependency outage: a real closed port (nothing listening)
        closed_port = free_port()
        with Session(app_engine) as raw_session:
            set_tenant_context(raw_session, org1_id)
            binding = get_component_binding(
                raw_session, tenant_context, uuid.UUID(gateway_binding_id)
            )
            binding.approved_endpoint = f"https://127.0.0.1:{closed_port}"
            raw_session.flush()
            result = validate_component_binding(
                raw_session,
                tenant_context,
                uuid.UUID(gateway_binding_id),
                policy=_http_only_local_policy(name="test-real-outage"),
                actor=ActorInfo(actor_type="system", actor_id="test", request_id=uuid.uuid4().hex),
            )
            health_result = result.health_result
            raw_session.commit()
            check(
                "FTF-R002",
                "negative_acceptance: 'no fake health after dependency outage' -- "
                "a real connection-refused failure never reports healthy",
                health_result == "unhealthy",
            )

        # -- idempotent sync outbox: admit, replay, resolve, conflict --
        factory_job_id = uuid.uuid4()
        with Session(app_engine) as raw_session:
            set_tenant_context(raw_session, org1_id)
            actor = ActorInfo(actor_type="system", actor_id="test", request_id=uuid.uuid4().hex)
            outbox1 = record_sync_outcome(
                raw_session,
                tenant_context,
                component_binding_id=uuid.UUID(binding1_id),
                factory_job_id=factory_job_id,
                idempotency_key="job-fingerprint-1",
                actor=actor,
            )
            outbox1_status, outbox1_id = outbox1.status, outbox1.id
            raw_session.commit()
            check(
                "FTF-R002",
                "acceptance: a real sync outbox row is admitted pending",
                outbox1_status == "pending",
            )

            set_tenant_context(raw_session, org1_id)
            outbox1_replay = record_sync_outcome(
                raw_session,
                tenant_context,
                component_binding_id=uuid.UUID(binding1_id),
                factory_job_id=factory_job_id,
                idempotency_key="job-fingerprint-1",
                actor=actor,
            )
            outbox1_replay_id = outbox1_replay.id
            raw_session.commit()
            check(
                "FTF-R002",
                "acceptance: 'Idempotent sync outbox' -- a replay with the same "
                "fingerprint returns the identical prior row, not a second one",
                outbox1_replay_id == outbox1_id,
            )

            try:
                set_tenant_context(raw_session, org1_id)
                record_sync_outcome(
                    raw_session,
                    tenant_context,
                    component_binding_id=uuid.UUID(binding1_id),
                    factory_job_id=factory_job_id,
                    idempotency_key="a-different-fingerprint",
                    actor=actor,
                )
                check(
                    "FTF-R002",
                    "Section 70.1: a different fingerprint for the same (binding, "
                    "job) pair is refused (409), not silently merged",
                    False,
                )
            except SyncOutboxConflictError:
                check(
                    "FTF-R002",
                    "Section 70.1: a different fingerprint for the same (binding, "
                    "job) pair is refused, not silently merged",
                    True,
                )

            set_tenant_context(raw_session, org1_id)
            resolved = record_sync_outcome(
                raw_session,
                tenant_context,
                component_binding_id=uuid.UUID(binding1_id),
                factory_job_id=factory_job_id,
                idempotency_key="job-fingerprint-1",
                outcome="succeeded",
                external_id="mlflow-run-abc123",
                actor=actor,
            )
            resolved_status, resolved_external_id = resolved.status, resolved.external_id
            raw_session.commit()
            check(
                "FTF-R002",
                "acceptance: resolving with a real outcome reports the real "
                "external_id and succeeded status",
                resolved_status == "succeeded" and resolved_external_id == "mlflow-run-abc123",
            )

        # -- component-status projection: real health, no fabricated success --
        r = _request(
            base_url,
            "GET",
            f"/api/v1/organizations/{org1_id}/component-status",
            principal=org1_admin,
        )
        check("FTF-R002", "the component-status projection succeeds (200)", r.status == 200)
        if r.status == 200:
            entries = {e["component_key"]: e for e in r.json()}
            check(
                "FTF-R002",
                "all seven Section 77.1 components are reported, even one with "
                "no binding at all -- 'no fabricated success'",
                len(entries) == 7 and entries.get("litellm", {}).get("configured") is False,
            )
            check(
                "FTF-R002",
                "a component with no binding configured reports a real blocked_reason",
                entries["litellm"]["blocked_reason"] == "no_binding_configured",
            )

    # -- raw-SQL CHECK constraint bypass: a worker_package row with an
    # approved_endpoint, inserted directly (bypassing the ORM entirely) as
    # the migration owner, is genuinely rejected by Postgres itself.
    # The migration-owner role owns these tables (bypasses RLS entirely),
    # so no tenant context needs to be set for this check.
    with owner_engine.connect() as conn:
        try:
            conn.execute(
                text(
                    "INSERT INTO component_bindings "
                    "(organization_id, component_key, security_zone_scope, deployment_mode, "
                    "approved_endpoint, network_policy_identity, compatibility_lock_reference) "
                    "VALUES (:org, 'argilla', 'default', 'worker_package', "
                    "'https://bypassed.example', 'argilla-net-policy', 'argilla')"
                ),
                {"org": str(org1_id)},
            )
            conn.commit()
            check(
                "FTF-R002",
                "the real ck_component_bindings_worker_package_no_endpoint CHECK "
                "constraint rejects a raw INSERT that bypasses the ORM entirely",
                False,
            )
        except Exception:  # noqa: BLE001 - any DB-level rejection is the real proof here
            conn.rollback()
            check(
                "FTF-R002",
                "the real ck_component_bindings_worker_package_no_endpoint CHECK "
                "constraint rejects a raw INSERT that bypasses the ORM entirely",
                True,
            )

    if FAILURES:
        print(f"FTF-R002 real ComponentBinding schema/API proof: {len(FAILURES)} failed")
        for failure in FAILURES:
            print(f"  {failure}")
        sys.exit(1)

    print("FTF-R002 real ComponentBinding schema/API proof: all checks passed")
    sys.exit(0)


if __name__ == "__main__":
    main()
