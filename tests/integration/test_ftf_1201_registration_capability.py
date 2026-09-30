#!/usr/bin/env python3
"""FTF-1201 -- real Postgres + real gateway registration-capability proof.

Proves this task's own acceptance clause ("Detect actual
organization-approved gateway management capability separately from
inference key") through the real, live `GET .../registration-capability`
route wired onto `app.litellm.connection_routes.build_litellm_connection_router`
-- never a mocked route handler or a direct call to
`packages.litellm_client.litellm_registration_capability_detection.detect_registration_capability`
bypassing the HTTP layer. Two real organizations (org1/org2), a real
uvicorn server, a real Postgres-backed `LiteLLMConnection` row read
through the restricted `sovra_app` role (never the migration-owner role),
and a real local HTTPS gateway plus a real local OpenBao-shaped secret
backend this suite starts itself -- same class of local-server proof
`tests/integration/test_ftf_0203_connection_validation.py` already
established, driven here end-to-end through the actual FastAPI route.

Requires a real Postgres reachable at `SOVRA_DATABASE_URL` (the migration
owner role) plus `SOVRA_APP_DB_PASSWORD`, matching every sibling
`connections/litellm/*` integration test's own environment contract
exactly. Also requires `openssl`/`update-ca-certificates` (this sandbox
has both; GitHub Actions' `ubuntu-latest` runner does too) to install a
real, ephemeral CA into the sandbox's own system trust store for the
duration of the run -- disclosed and skipped gracefully (BLOCKED_EXTERNAL
for the TLS-dependent checks only) if unavailable.

Run directly:
    SOVRA_DATABASE_URL=postgresql+psycopg://<migration-role>@host/db \\
    SOVRA_APP_DB_PASSWORD=<app-role-password> \\
    python3 tests/integration/test_ftf_1201_registration_capability.py
"""

from __future__ import annotations

import atexit
import contextlib
import http.client
import json
import os
import shutil
import socket
import ssl
import subprocess
import sys
import tempfile
import threading
import time
import urllib.parse
import uuid
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

ROOT = Path(__file__).resolve().parent.parent.parent
API_ROOT = ROOT / "apps" / "api"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(API_ROOT))

import uvicorn  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from packages.litellm_client.secret_manager_integration import OpenBaoConfig  # noqa: E402
from packages.security.url_policy import ConnectionPolicy  # noqa: E402
from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import Session, sessionmaker  # noqa: E402

from app.auth.organization_workspace_schema import APP_ROLE_NAME, Organization  # noqa: E402
from app.auth.rbac_middleware import AuthenticatedPrincipal, Role  # noqa: E402
from app.litellm.connection_routes import build_litellm_connection_router  # noqa: E402

FAILURES: list[str] = []


def check(criterion_id: str, description: str, condition: bool) -> None:
    if not condition:
        FAILURES.append(f"FAIL [{criterion_id}] {description}")


def _required_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        print(
            f"BLOCKED_EXTERNAL: {name} is not set -- this task's real "
            "registration-capability evidence requires a live Postgres "
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


# -- Fixture: two organizations, one organization_admin + one viewer each --

_ISSUER = "https://issuer.example"


class _Fixture:
    def __init__(self, org_ids: dict[str, uuid.UUID]) -> None:
        self.org_ids = org_ids
        self.principals: dict[tuple[str, Role], AuthenticatedPrincipal] = {}
        self.memberships: dict[tuple[str, str, uuid.UUID], Role] = {}
        for label, org_id in org_ids.items():
            for role in (Role.ORGANIZATION_ADMIN, Role.VIEWER):
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


# -- Real local secret backend (OpenBao KV v2 read subset) ------------------

_SECRET_TOKEN = "test-vault-token-1201-integration"
_VALID_KEY = "sk-real-inference-key-1201-integration"


class _SecretBackendHandler(BaseHTTPRequestHandler):
    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        pass

    def do_GET(self) -> None:  # noqa: N802
        if self.headers.get("X-Vault-Token") != _SECRET_TOKEN:
            self._json(403, {"errors": ["permission denied"]})
            return
        if self.path == "/v1/secret/data/litellm/org1":
            self._json(200, {"data": {"data": {"api_key": _VALID_KEY}, "metadata": {"version": 1}}})
        else:
            self._json(404, {"errors": []})

    def _json(self, status: int, payload: dict[str, object]) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


_SECRET_SERVER = ThreadingHTTPServer(("127.0.0.1", 0), _SecretBackendHandler)
_SECRET_PORT = _SECRET_SERVER.server_address[1]
threading.Thread(target=_SECRET_SERVER.serve_forever, daemon=True).start()
atexit.register(_SECRET_SERVER.shutdown)
_SECRET_CONFIG = OpenBaoConfig(address=f"http://127.0.0.1:{_SECRET_PORT}", token=_SECRET_TOKEN)


# -- Real local HTTPS gateway, with a real CA installed into this sandbox's
#    own system trust store for the duration of the run -------------------

_TLS_TMP = Path(tempfile.mkdtemp(prefix="ftf-1201-int-tls-"))
_CA_TRUST_TARGET = Path("/usr/local/share/ca-certificates/ftf-1201-integration-test-ca.crt")
_UPDATE_CA_AVAILABLE = (
    shutil.which("update-ca-certificates") is not None
    and shutil.which("openssl") is not None
    and shutil.which("sudo") is not None
    and subprocess.run(["sudo", "-n", "true"], capture_output=True).returncode == 0
)


def _run_openssl(args: list[str]) -> None:
    subprocess.run(["openssl", *args], check=True, capture_output=True)


def _generate_trusted_server_cert() -> tuple[Path, Path]:
    ca_key = _TLS_TMP / "ca.key"
    ca_crt = _TLS_TMP / "ca.crt"
    leaf_key = _TLS_TMP / "leaf.key"
    leaf_csr = _TLS_TMP / "leaf.csr"
    leaf_crt = _TLS_TMP / "leaf.crt"
    leaf_ext = _TLS_TMP / "leaf.ext"

    _run_openssl(["genrsa", "-out", str(ca_key), "2048"])
    _run_openssl(
        [
            "req",
            "-x509",
            "-new",
            "-key",
            str(ca_key),
            "-sha256",
            "-days",
            "2",
            "-out",
            str(ca_crt),
            "-subj",
            "/CN=FTF-1201 Integration Test CA",
        ]
    )
    _run_openssl(["genrsa", "-out", str(leaf_key), "2048"])
    _run_openssl(
        ["req", "-new", "-key", str(leaf_key), "-out", str(leaf_csr), "-subj", "/CN=127.0.0.1"]
    )
    leaf_ext.write_text("subjectAltName=IP:127.0.0.1\n")
    _run_openssl(
        [
            "x509",
            "-req",
            "-in",
            str(leaf_csr),
            "-CA",
            str(ca_crt),
            "-CAkey",
            str(ca_key),
            "-CAcreateserial",
            "-out",
            str(leaf_crt),
            "-days",
            "2",
            "-sha256",
            "-extfile",
            str(leaf_ext),
        ]
    )
    subprocess.run(
        ["sudo", "cp", str(ca_crt), str(_CA_TRUST_TARGET)], check=True, capture_output=True
    )
    subprocess.run(["sudo", "update-ca-certificates"], check=True, capture_output=True)
    atexit.register(_remove_ca_trust)
    return leaf_crt, leaf_key


def _remove_ca_trust() -> None:
    subprocess.run(["sudo", "rm", "-f", str(_CA_TRUST_TARGET)], check=False, capture_output=True)
    subprocess.run(["sudo", "update-ca-certificates", "--fresh"], check=False, capture_output=True)


def _make_gateway_handler(*, valid_token: str, role: str) -> type[BaseHTTPRequestHandler]:
    class _GatewayHandler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: object) -> None:  # noqa: A002
            pass

        def do_GET(self) -> None:  # noqa: N802
            if self.path == "/user/info":
                if self.headers.get("Authorization") != f"Bearer {valid_token}":
                    self._respond(401, b'{"error":"invalid token"}')
                    return
                self._respond(200, json.dumps({"user_role": role}).encode("utf-8"))
            else:
                self._respond(404, b"")

        def _respond(self, status: int, body: bytes) -> None:
            self.send_response(status)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            if body:
                self.wfile.write(body)

    return _GatewayHandler


def _start_https_server(
    handler_cls: type[BaseHTTPRequestHandler], certfile: Path, keyfile: Path
) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler_cls)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(certfile=str(certfile), keyfile=str(keyfile))
    server.socket = context.wrap_socket(server.socket, server_side=True)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    atexit.register(server.shutdown)
    return server


def main() -> None:
    migration_url = _required_env("SOVRA_DATABASE_URL")
    app_password = _required_env("SOVRA_APP_DB_PASSWORD")

    migration_result = _run_migration(migration_url)
    check(
        "FTF-1201",
        f"alembic upgrade head runs cleanly (exit {migration_result.returncode})\n"
        f"stdout: {migration_result.stdout}\nstderr: {migration_result.stderr}",
        migration_result.returncode == 0,
    )
    if FAILURES:
        print(f"FTF-1201 real registration-capability proof: {len(FAILURES)} failed")
        for failure in FAILURES:
            print(f"  {failure}")
        sys.exit(1)

    if not _UPDATE_CA_AVAILABLE:
        print(
            "FTF-1201: this sandbox has no openssl/update-ca-certificates -- "
            "the live TLS-backed route checks are skipped (BLOCKED_EXTERNAL) "
            "and disclosed here. GitHub Actions' ubuntu-latest runner has both."
        )
        print("FTF-1201 real registration-capability proof: BLOCKED_EXTERNAL")
        sys.exit(0)

    trusted_leaf, trusted_key = _generate_trusted_server_cert()
    admin_gateway = _start_https_server(
        _make_gateway_handler(valid_token=_VALID_KEY, role="proxy_admin"), trusted_leaf, trusted_key
    )
    admin_port = admin_gateway.server_address[1]
    viewer_gateway = _start_https_server(
        _make_gateway_handler(valid_token=_VALID_KEY, role="proxy_admin_viewer"),
        trusted_leaf,
        trusted_key,
    )
    viewer_port = viewer_gateway.server_address[1]

    owner_engine = create_engine(migration_url)
    app_url = _app_role_url(migration_url, app_password)
    app_engine = create_engine(app_url)

    with Session(owner_engine) as session:
        org1 = Organization(name="Acme 1201", slug=f"acme-1201-{uuid.uuid4().hex[:8]}")
        org2 = Organization(name="Globex 1201", slug=f"globex-1201-{uuid.uuid4().hex[:8]}")
        session.add_all([org1, org2])
        session.commit()
        org1_id, org2_id = org1.id, org2.id

    fixture = _Fixture({"org1": org1_id, "org2": org2_id})
    session_factory = sessionmaker(bind=app_engine)

    def _test_capability_policy() -> ConnectionPolicy:
        import ipaddress

        return ConnectionPolicy(
            name="test-litellm-gateway",
            allowed_schemes=frozenset({"https"}),
            allowed_ports=frozenset({admin_port, viewer_port}),
            explicit_private_allowlist=(ipaddress.ip_network("127.0.0.0/8"),),
        )

    app = FastAPI()
    app.include_router(
        build_litellm_connection_router(
            session_factory,
            principal_resolver=fixture.principal_resolver,
            role_lookup=fixture.role_lookup,
            secret_manager_config_factory=lambda: _SECRET_CONFIG,
            capability_policy_factory=_test_capability_policy,
        )
    )

    org1_admin = fixture.header_for("org1", Role.ORGANIZATION_ADMIN)
    org2_admin = fixture.header_for("org2", Role.ORGANIZATION_ADMIN)

    def collection_path(org_id: uuid.UUID) -> str:
        return f"/api/v1/organizations/{org_id}/connections/litellm"

    def item_path(org_id: uuid.UUID, connection_id: object) -> str:
        return f"{collection_path(org_id)}/{connection_id}"

    def capability_path(org_id: uuid.UUID, connection_id: object) -> str:
        return f"{item_path(org_id, connection_id)}/registration-capability"

    with _running_app(app) as base_url:
        # -- Real create: a connection whose gateway genuinely reports proxy_admin
        r = _request(
            base_url,
            "POST",
            collection_path(org1_id),
            principal=org1_admin,
            payload={
                "name": "admin-gateway",
                "base_url": f"https://127.0.0.1:{admin_port}",
                "secret_ref": "openbao://secret/litellm/org1/api_key",
            },
        )
        check("FTF-1201", "a real connection create succeeds (201)", r.status == 201)
        admin_id = r.json().get("id") if r.status == 201 else None

        # -- Real create: a connection whose gateway genuinely reports proxy_admin_viewer
        r = _request(
            base_url,
            "POST",
            collection_path(org1_id),
            principal=org1_admin,
            payload={
                "name": "viewer-gateway",
                "base_url": f"https://127.0.0.1:{viewer_port}",
                "secret_ref": "openbao://secret/litellm/org1/api_key",
            },
        )
        check("FTF-1201", "a second real connection create succeeds (201)", r.status == 201)
        viewer_id = r.json().get("id") if r.status == 201 else None

        # -- Unauthenticated / role-boundary denial --
        r = _request(base_url, "GET", capability_path(org1_id, admin_id or uuid.uuid4()))
        check(
            "FTF-1201",
            "an unauthenticated registration-capability request is denied (401)",
            r.status == 401,
        )

        # -- acceptance: real end-to-end management_capable through the live route --
        r = _request(base_url, "GET", capability_path(org1_id, admin_id), principal=org1_admin)
        check(
            "FTF-1201",
            "a real registration-capability call against a genuine "
            "proxy_admin gateway succeeds (200)",
            r.status == 200,
        )
        body = r.json() if r.status == 200 else {}
        check(
            "FTF-1201",
            "the live route reports management_capable for a real proxy_admin role",
            body.get("state") == "management_capable",
        )
        check(
            "FTF-1201",
            "the live route records the real observed_role",
            body.get("observed_role") == "proxy_admin",
        )

        # -- negative_acceptance: a read-only admin viewer role never reports capable --
        r = _request(base_url, "GET", capability_path(org1_id, viewer_id), principal=org1_admin)
        check(
            "FTF-1201",
            "a real registration-capability call against a proxy_admin_viewer gateway "
            "still succeeds at the HTTP layer (200)",
            r.status == 200,
        )
        body = r.json() if r.status == 200 else {}
        check(
            "FTF-1201",
            "negative_acceptance: a real proxy_admin_viewer role is reported as "
            "inference_only, never management_capable",
            body.get("state") == "inference_only",
        )
        check(
            "FTF-1201",
            "the live route records proxy_admin_viewer as the real observed_role",
            body.get("observed_role") == "proxy_admin_viewer",
        )

        # -- negative_acceptance: the IDOR case -- a real connection ID
        # belonging to org1, requested under org2's own path by a real
        # org2 organization_admin -- denied identically to missing.
        r = _request(base_url, "GET", capability_path(org2_id, admin_id), principal=org2_admin)
        check(
            "FTF-1201",
            "negative_acceptance: checking a foreign connection ID under the "
            "wrong organization's own path is denied identically to missing "
            "(404), never dials the real gateway on the wrong tenant's behalf",
            r.status == 404,
        )

        # -- acceptance: a revoked connection cannot have capability detected (409) --
        r = _request(base_url, "DELETE", item_path(org1_id, admin_id), principal=org1_admin)
        check(
            "FTF-1201",
            "a real revoke succeeds (200) ahead of the revoked-capability check",
            r.status == 200,
        )
        r = _request(base_url, "GET", capability_path(org1_id, admin_id), principal=org1_admin)
        check(
            "FTF-1201",
            "acceptance: a revoked connection is refused capability detection (409), "
            "never silently dialed",
            r.status == 409,
        )

    if FAILURES:
        print(f"FTF-1201 real registration-capability proof: {len(FAILURES)} failed")
        for failure in FAILURES:
            print(f"  {failure}")
        sys.exit(1)

    print("FTF-1201 real registration-capability proof: all checks passed")
    sys.exit(0)


if __name__ == "__main__":
    main()
