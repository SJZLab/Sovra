#!/usr/bin/env python3
"""FTF-0214 -- real Postgres + real gateway `GET .../catalog` and
`POST .../models/{model_id}/probe` proof.

Proves this task's own acceptance clause ("Catalog shows actual access
freshness/capability/probe evidence, safe filters and explicit project
selection") and negative_acceptance ("No automatic model selection,
fabricated costs or hidden stale access; viewer cannot run paid probe")
through the real, live routes wired onto
`app.litellm.model_catalog_routes.build_model_catalog_router` -- never a
mocked route handler or a direct call to the nine composed, previously-
unwired FTF-0205/0206/0207/0208/0209/0210/0211/0212/0213/H004 modules
bypassing the HTTP layer. Same class of real-Postgres + real-local-HTTPS-
gateway proof `tests/integration/test_ftf_0204_v1_models_discovery.py`
and `tests/integration/test_ftf_h004_access_guard.py` (both already
CERTIFIED) already established, reused and extended here to also serve
`/model_group/info` and a dispatching `POST /v1/chat/completions` (plain,
streaming, tool-call and structured-output, distinguished by the real
request body each probe module itself sends).

Requires a real Postgres reachable at `SOVRA_DATABASE_URL` (the migration
owner role) plus `SOVRA_APP_DB_PASSWORD`, matching every sibling
integration test's own environment contract. Also requires `openssl`/
`update-ca-certificates` (BLOCKED_EXTERNAL, disclosed, if unavailable).

Run directly:
    SOVRA_DATABASE_URL=postgresql+psycopg://<migration-role>@host/db \\
    SOVRA_APP_DB_PASSWORD=<app-role-password> \\
    python3 tests/integration/test_ftf_0214_model_catalog.py
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

from app.auth.organization_workspace_schema import (  # noqa: E402
    APP_ROLE_NAME,
    Organization,
    Workspace,
)
from app.auth.rbac_middleware import AuthenticatedPrincipal, Role  # noqa: E402
from app.litellm import model_catalog_routes as model_catalog_routes_module  # noqa: E402
from app.litellm.model_catalog_routes import build_model_catalog_router  # noqa: E402
from app.projects.project_schema import Project  # noqa: E402

FAILURES: list[str] = []


def check(criterion_id: str, description: str, condition: bool) -> None:
    if not condition:
        FAILURES.append(f"FAIL [{criterion_id}] {description}")


def _required_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        print(
            f"BLOCKED_EXTERNAL: {name} is not set -- this task's real "
            "catalog/probe evidence requires a live Postgres (migration "
            "owner connection). Not silently skipped: see this file's own "
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

_SECRET_TOKEN = "test-vault-token-0214-integration"
_VALID_KEY = "sk-real-inference-key-0214-integration"


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

_TLS_TMP = Path(tempfile.mkdtemp(prefix="ftf-0214-int-tls-"))
_CA_TRUST_TARGET = Path("/usr/local/share/ca-certificates/ftf-0214-integration-test-ca.crt")
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
            "/CN=FTF-0214 Integration Test CA",
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


# -- The composed gateway: /v1/models, /model/info, /model_group/info and
#    a dispatching /v1/chat/completions (plain/streaming/tool/structured,
#    distinguished by the real request body each probe module sends). ----

_PROBE_TOOL_NAME = "get_probe_time"


class _GatewayState:
    def __init__(self) -> None:
        self.model_ids: list[str] = ["model-a", "model-b", "model-c"]
        self.request_count = 0


_STATE = _GatewayState()


def _sse(payload: dict[str, object]) -> bytes:
    return b"data: " + json.dumps(payload).encode("utf-8") + b"\n\n"


def _make_gateway_handler(*, valid_token: str) -> type[BaseHTTPRequestHandler]:
    class _GatewayHandler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.0"

        def log_message(self, format: str, *args: object) -> None:  # noqa: A002
            pass

        def do_GET(self) -> None:  # noqa: N802
            if self.headers.get("Authorization") != f"Bearer {valid_token}":
                self._respond(401, b'{"error":"invalid token"}')
                return
            if self.path == "/v1/models":
                _STATE.request_count += 1
                body = json.dumps({"data": [{"id": mid} for mid in _STATE.model_ids]}).encode(
                    "utf-8"
                )
                self._respond(200, body)
            elif self.path == "/model/info":
                body = json.dumps(
                    {
                        "data": [
                            {
                                "model_name": "model-a",
                                "model_info": {
                                    "max_tokens": 8192,
                                    "input_cost_per_token": 0.0,
                                },
                            }
                        ]
                    }
                ).encode("utf-8")
                self._respond(200, body)
            elif self.path == "/model_group/info":
                body = json.dumps(
                    {
                        "data": [
                            {
                                "model_group": "model-a",
                                "max_input_tokens": 8192,
                            }
                        ]
                    }
                ).encode("utf-8")
                self._respond(200, body)
            else:
                self._respond(404, b"")

        def do_POST(self) -> None:  # noqa: N802
            try:
                self._do_post()
            except OSError:
                return

        def _do_post(self) -> None:
            if self.path != "/v1/chat/completions":
                self._respond(404, b"")
                return
            if self.headers.get("Authorization") != f"Bearer {valid_token}":
                self._respond(401, b'{"error":"invalid token"}')
                return
            length = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(length) if length else b""
            try:
                request_body = json.loads(raw) if raw else {}
            except json.JSONDecodeError:
                request_body = {}

            if request_body.get("stream") is True:
                self._stream_response()
                return

            if isinstance(request_body.get("tools"), list):
                body = json.dumps(
                    {
                        "model": "model-a",
                        "choices": [
                            {
                                "index": 0,
                                "message": {
                                    "role": "assistant",
                                    "content": None,
                                    "tool_calls": [
                                        {
                                            "id": "call-1",
                                            "type": "function",
                                            "function": {
                                                "name": _PROBE_TOOL_NAME,
                                                "arguments": '{"unit": "iso8601"}',
                                            },
                                        }
                                    ],
                                },
                            }
                        ],
                        "usage": {
                            "prompt_tokens": 12,
                            "completion_tokens": 4,
                            "total_tokens": 16,
                        },
                    }
                ).encode("utf-8")
                self._respond(200, body)
                return

            if isinstance(request_body.get("response_format"), dict):
                body = json.dumps(
                    {
                        "model": "model-a",
                        "choices": [
                            {
                                "index": 0,
                                "message": {
                                    "role": "assistant",
                                    "content": json.dumps({"word": "pong", "count": 1}),
                                },
                            }
                        ],
                        "usage": {
                            "prompt_tokens": 10,
                            "completion_tokens": 3,
                            "total_tokens": 13,
                        },
                    }
                ).encode("utf-8")
                self._respond(200, body)
                return

            body = json.dumps(
                {
                    "model": "model-a",
                    "choices": [{"index": 0, "message": {"role": "assistant", "content": "pong"}}],
                    "usage": {
                        "prompt_tokens": 9,
                        "completion_tokens": 1,
                        "total_tokens": 10,
                    },
                }
            ).encode("utf-8")
            self._respond(200, body)

        def _stream_response(self) -> None:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            events = [
                _sse(
                    {
                        "id": "chatcmpl-real",
                        "model": "model-a",
                        "choices": [
                            {"index": 0, "delta": {"role": "assistant"}, "finish_reason": None}
                        ],
                    }
                ),
                _sse(
                    {
                        "id": "chatcmpl-real",
                        "choices": [
                            {"index": 0, "delta": {"content": "pong"}, "finish_reason": None}
                        ],
                    }
                ),
                _sse(
                    {
                        "id": "chatcmpl-real",
                        "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
                        "usage": {
                            "prompt_tokens": 9,
                            "completion_tokens": 1,
                            "total_tokens": 10,
                        },
                    }
                ),
                b"data: [DONE]\n\n",
            ]
            for event in events:
                self.wfile.write(event)
                self.wfile.flush()

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
        "FTF-0214",
        f"alembic upgrade head runs cleanly (exit {migration_result.returncode})\n"
        f"stdout: {migration_result.stdout}\nstderr: {migration_result.stderr}",
        migration_result.returncode == 0,
    )
    if FAILURES:
        print(f"FTF-0214 real catalog/probe proof: {len(FAILURES)} failed")
        for failure in FAILURES:
            print(f"  {failure}")
        sys.exit(1)

    if not _UPDATE_CA_AVAILABLE:
        print(
            "FTF-0214: this sandbox has no openssl/update-ca-certificates -- "
            "the live TLS-backed route checks are skipped (BLOCKED_EXTERNAL) "
            "and disclosed here. GitHub Actions' ubuntu-latest runner has both."
        )
        print("FTF-0214 real catalog/probe proof: BLOCKED_EXTERNAL")
        sys.exit(0)

    trusted_leaf, trusted_key = _generate_trusted_server_cert()
    gateway = _start_https_server(
        _make_gateway_handler(valid_token=_VALID_KEY), trusted_leaf, trusted_key
    )
    gateway_port = gateway.server_address[1]

    owner_engine = create_engine(migration_url)
    app_url = _app_role_url(migration_url, app_password)
    app_engine = create_engine(app_url)

    with Session(owner_engine) as session:
        org1 = Organization(name="Acme 0214", slug=f"acme-0214-{uuid.uuid4().hex[:8]}")
        org2 = Organization(name="Globex 0214", slug=f"globex-0214-{uuid.uuid4().hex[:8]}")
        session.add_all([org1, org2])
        session.flush()
        ws1 = Workspace(organization_id=org1.id, name="Acme Main", slug="main")
        session.add(ws1)
        session.flush()
        project1 = Project(
            organization_id=org1.id,
            workspace_id=ws1.id,
            name="acme-project-0214",
            project_type="generic_model_tuning",
            status="active",
            security_profile="standard",
            row_version=1,
            created_by="seed",
        )
        session.add(project1)
        session.commit()
        org1_id, org2_id, project1_id = org1.id, org2.id, project1.id

    fixture = _Fixture({"org1": org1_id, "org2": org2_id})
    session_factory = sessionmaker(bind=app_engine)

    def _test_policy() -> ConnectionPolicy:
        import ipaddress

        return ConnectionPolicy(
            name="test-litellm-gateway",
            allowed_schemes=frozenset({"https"}),
            allowed_ports=frozenset({gateway_port}),
            explicit_private_allowlist=(ipaddress.ip_network("127.0.0.0/8"),),
        )

    app = FastAPI()
    app.include_router(
        build_model_catalog_router(
            session_factory,
            principal_resolver=fixture.principal_resolver,
            role_lookup=fixture.role_lookup,
            secret_manager_config_factory=lambda: _SECRET_CONFIG,
            validation_policy_factory=_test_policy,
        )
    )

    org1_admin = fixture.header_for("org1", Role.ORGANIZATION_ADMIN)
    org1_viewer = fixture.header_for("org1", Role.VIEWER)
    org2_admin = fixture.header_for("org2", Role.ORGANIZATION_ADMIN)

    # A second, real, active connection row -- created directly (this
    # router never exposes create/revoke; those are FTF-0201's own routes
    # on `connection_routes.py`) so these checks need no sibling router.
    with Session(owner_engine) as session:
        from app.litellm.connection_schema import LiteLLMConnection

        active_conn = LiteLLMConnection(
            organization_id=org1_id,
            name="active-gateway",
            base_url=f"https://127.0.0.1:{gateway_port}",
            secret_ref="openbao://secret/litellm/org1/api_key",
            status="active",
        )
        revoked_conn = LiteLLMConnection(
            organization_id=org1_id,
            name="revoked-gateway",
            base_url=f"https://127.0.0.1:{gateway_port}",
            secret_ref="openbao://secret/litellm/org1/api_key",
            status="revoked",
        )
        org2_conn = LiteLLMConnection(
            organization_id=org2_id,
            name="org2-gateway",
            base_url=f"https://127.0.0.1:{gateway_port}",
            secret_ref="openbao://secret/litellm/org1/api_key",
            status="active",
        )
        fresh_conn = LiteLLMConnection(
            organization_id=org1_id,
            name="never-discovered-gateway",
            base_url=f"https://127.0.0.1:{gateway_port}",
            secret_ref="openbao://secret/litellm/org1/api_key",
            status="active",
        )
        session.add_all([active_conn, revoked_conn, org2_conn, fresh_conn])
        session.commit()
        active_id, revoked_id, org2_conn_id, fresh_id = (
            active_conn.id,
            revoked_conn.id,
            org2_conn.id,
            fresh_conn.id,
        )

    def catalog_path(org_id: uuid.UUID, connection_id: object, query: str = "") -> str:
        base = f"/api/v1/organizations/{org_id}/connections/litellm/{connection_id}/catalog"
        return base + query

    def probe_path(org_id: uuid.UUID, connection_id: object, model_id: str, query: str = "") -> str:
        base = (
            f"/api/v1/organizations/{org_id}/connections/litellm/{connection_id}"
            f"/models/{model_id}/probe"
        )
        return base + query

    with _running_app(app) as base_url:
        # -- role boundary: unauthenticated / viewer are denied before any
        #    catalog read or probe dial (negative_acceptance: "viewer
        #    cannot run paid probe" -- reusing CONNECTION_ADMIN_ROLES) --
        r = _request(base_url, "GET", catalog_path(org1_id, active_id))
        check("FTF-0214", "an unauthenticated catalog read is denied (401)", r.status == 401)
        r = _request(base_url, "GET", catalog_path(org1_id, active_id), principal=org1_viewer)
        check(
            "FTF-0214",
            "negative_acceptance: a real viewer-role principal is denied the catalog (403)",
            r.status == 403,
        )
        r = _request(
            base_url,
            "POST",
            probe_path(org1_id, active_id, "model-a", f"?project_id={project1_id}"),
            principal=org1_viewer,
            payload={"probe_type": "chat"},
        )
        check(
            "FTF-0214",
            "negative_acceptance: a real viewer-role principal cannot run a paid probe (403), "
            "never reaches the real gateway",
            r.status == 403,
        )

        # -- acceptance: real end-to-end composed catalog through the live route --
        r = _request(base_url, "GET", catalog_path(org1_id, active_id), principal=org1_admin)
        check(
            "FTF-0214",
            "a real catalog read against a reachable gateway succeeds (200)",
            r.status == 200,
        )
        body = r.json() if r.status == 200 else {}
        check(
            "FTF-0214",
            "acceptance: access_state is healthy for a real reachable gateway",
            body.get("access_state") == "healthy",
        )
        entries_by_id = {e["id"]: e for e in body.get("entries", [])}
        check(
            "FTF-0214",
            "acceptance: all three real discovered models are shown",
            set(entries_by_id) == {"model-a", "model-b", "model-c"},
        )
        check(
            "FTF-0214",
            "acceptance: model-a's real /model/info field is surfaced (capability evidence)",
            entries_by_id.get("model-a", {}).get("fields", {}).get("max_tokens") == 8192,
        )
        check(
            "FTF-0214",
            "negative_acceptance: model-b (no /model/info entry) never receives a fabricated field",
            entries_by_id.get("model-b", {}).get("fields", {}) == {},
        )
        check(
            "FTF-0214",
            "acceptance: freshness evidence (last_successful_check_at) is a real timestamp",
            isinstance(body.get("last_successful_check_at"), str),
        )
        check(
            "FTF-0214",
            "acceptance: no anomaly on the very first discovery",
            body.get("anomaly") is None,
        )
        groups = body.get("model_groups") or {}
        check(
            "FTF-0214",
            "acceptance: FTF-0206 /model_group/info enrichment is wired and surfaced",
            groups.get("entries", {}).get("model-a", {}).get("max_input_tokens") == 8192,
        )

        # -- explicit project selection: a real, owned project_id is accepted;
        #    a missing/cross-tenant one is denied before any effect --
        r = _request(
            base_url,
            "GET",
            catalog_path(org1_id, active_id, f"?project_id={project1_id}"),
            principal=org1_admin,
        )
        check(
            "FTF-0214",
            "a real owned project_id on the catalog read is accepted (200)",
            r.status == 200,
        )
        r = _request(
            base_url,
            "GET",
            catalog_path(org1_id, active_id, f"?project_id={uuid.uuid4()}"),
            principal=org1_admin,
        )
        check(
            "FTF-0214",
            "a nonexistent project_id is denied (404), never silently ignored",
            r.status == 404,
        )

        # -- safe filters: narrow, never widen, the already-authorized set --
        r = _request(
            base_url,
            "GET",
            catalog_path(org1_id, active_id, "?status=callable"),
            principal=org1_admin,
        )
        check(
            "FTF-0214",
            "a safe status filter narrows to only callable entries (200)",
            r.status == 200,
        )
        filtered = r.json().get("entries", []) if r.status == 200 else []
        check(
            "FTF-0214",
            "every returned entry under the filter is genuinely callable",
            all(e["status"] == "callable" for e in filtered),
        )
        r = _request(
            base_url,
            "GET",
            catalog_path(org1_id, active_id, "?status=not-a-real-status"),
            principal=org1_admin,
        )
        check(
            "FTF-0214",
            "an unrecognized filter value is rejected (400), never silently ignored",
            r.status == 400,
        )

        # -- acceptance (FTF-0209 anomaly): a real >50% drop in the active
        #    set is recorded as an anomaly, while removed ids stay visible
        #    for audit (FTF-0208) rather than disappearing silently --
        _STATE.model_ids = ["model-a"]
        r = _request(base_url, "GET", catalog_path(org1_id, active_id), principal=org1_admin)
        check(
            "FTF-0214",
            "the second real discovery (now 1 of 3 models) still succeeds (200)",
            r.status == 200,
        )
        body = r.json() if r.status == 200 else {}
        anomaly = body.get("anomaly")
        check(
            "FTF-0214",
            "acceptance: a real 3->1 active-model drop is recorded as an anomaly, not absorbed",
            anomaly is not None
            and anomaly.get("previous_count") == 3
            and anomaly.get("new_count") == 1,
        )
        entries_by_id = {e["id"]: e for e in body.get("entries", [])}
        check(
            "FTF-0214",
            "acceptance: a model no longer discovered is marked removed, not deleted",
            entries_by_id.get("model-b", {}).get("status") == "removed"
            and entries_by_id.get("model-c", {}).get("status") == "removed",
        )
        _STATE.model_ids = ["model-a", "model-b", "model-c"]  # restore for later checks

        # -- negative_acceptance: a revoked connection's catalog is never
        #    dialed against the real gateway again; history stays visible --
        requests_before = _STATE.request_count
        r = _request(base_url, "GET", catalog_path(org1_id, revoked_id), principal=org1_admin)
        check(
            "FTF-0214",
            "a revoked connection's catalog read still succeeds at the HTTP layer (200)",
            r.status == 200,
        )
        body = r.json() if r.status == 200 else {}
        check(
            "FTF-0214",
            "a revoked connection's catalog reports connection_status=revoked",
            body.get("connection_status") == "revoked",
        )
        check(
            "FTF-0214",
            "negative_acceptance: a revoked connection is never dialed against the real gateway",
            _STATE.request_count == requests_before,
        )

        # -- negative_acceptance: a foreign connection ID under the wrong
        #    organization's own path is denied identically to missing --
        r = _request(base_url, "GET", catalog_path(org2_id, active_id), principal=org2_admin)
        check(
            "FTF-0214",
            "negative_acceptance: a cross-tenant catalog read is denied like missing (404)",
            r.status == 404,
        )

        # -- POST .../probe: explicit project selection is required ------
        r = _request(
            base_url,
            "POST",
            probe_path(org1_id, active_id, "model-a"),
            principal=org1_admin,
            payload={"probe_type": "chat"},
        )
        check(
            "FTF-0214",
            "negative_acceptance: no automatic project selection -- a probe with "
            "no project_id is refused (400)",
            r.status == 400,
        )

        # -- negative_acceptance (FTF-H004 wiring): a model this admin has
        #    never discovered through this route cannot be probed, even
        #    though the connection genuinely exists, is active, and is
        #    genuinely owned by this exact organization ------------------
        r = _request(
            base_url,
            "POST",
            probe_path(org1_id, fresh_id, "model-a", f"?project_id={project1_id}"),
            principal=org1_admin,
            payload={"probe_type": "chat"},
        )
        check(
            "FTF-0214",
            "negative_acceptance: a probe against a never-discovered connection is "
            "denied (403), never dials the gateway blind",
            r.status == 403 and "no_successful_discovery_yet" in (r.json().get("detail") or ""),
        )

        # -- negative_acceptance: a probe against a foreign connection ID
        #    under the wrong organization's own path is denied identically
        #    to missing (404), before require_model_access is ever reached --
        r = _request(
            base_url,
            "POST",
            probe_path(org1_id, org2_conn_id, "model-a", f"?project_id={project1_id}"),
            principal=org1_admin,
            payload={"probe_type": "chat"},
        )
        check(
            "FTF-0214",
            "negative_acceptance: a probe against a foreign connection ID is denied identically "
            "to missing (404), never dials the real gateway on the wrong tenant's behalf",
            r.status == 404,
        )

        # -- re-discover model-a on `active_id` (fixed state) before probing
        r = _request(base_url, "GET", catalog_path(org1_id, active_id), principal=org1_admin)
        check("FTF-0214", "catalog re-discovery before probing succeeds (200)", r.status == 200)

        # -- acceptance: the four real probe kinds, each dispatched for real --
        r = _request(
            base_url,
            "POST",
            probe_path(org1_id, active_id, "model-a", f"?project_id={project1_id}"),
            principal=org1_admin,
            payload={"probe_type": "chat"},
        )
        check(
            "FTF-0214",
            "a real chat probe against a genuinely callable model succeeds (200)",
            r.status == 200,
        )
        body = r.json() if r.status == 200 else {}
        check(
            "FTF-0214",
            "acceptance: the real chat probe reports state=probed",
            body.get("state") == "probed",
        )
        check(
            "FTF-0214",
            "acceptance: the real chat probe records real usage",
            (body.get("usage") or {}).get("total_tokens") == 10,
        )

        r = _request(
            base_url,
            "POST",
            probe_path(org1_id, active_id, "model-a", f"?project_id={project1_id}"),
            principal=org1_admin,
            payload={"probe_type": "streaming"},
        )
        check("FTF-0214", "a real streaming probe succeeds (200)", r.status == 200)
        body = r.json() if r.status == 200 else {}
        check(
            "FTF-0214",
            "acceptance: the real streaming probe reconstructs the real streamed text",
            body.get("text") == "pong",
        )

        r = _request(
            base_url,
            "POST",
            probe_path(org1_id, active_id, "model-a", f"?project_id={project1_id}"),
            principal=org1_admin,
            payload={"probe_type": "tool_call"},
        )
        check("FTF-0214", "a real tool-call probe succeeds (200)", r.status == 200)
        body = r.json() if r.status == 200 else {}
        check(
            "FTF-0214",
            "acceptance: the real tool-call probe observes the real advertised tool name",
            body.get("observed_tool_name") == _PROBE_TOOL_NAME,
        )

        r = _request(
            base_url,
            "POST",
            probe_path(org1_id, active_id, "model-a", f"?project_id={project1_id}"),
            principal=org1_admin,
            payload={"probe_type": "structured_output"},
        )
        check("FTF-0214", "a real structured-output probe succeeds (200)", r.status == 200)
        body = r.json() if r.status == 200 else {}
        check(
            "FTF-0214",
            "acceptance: the real structured-output probe schema-validates the real response",
            body.get("parsed_content") == {"word": "pong", "count": 1},
        )

        # -- negative_acceptance: an unapproved probe_type value is refused --
        r = _request(
            base_url,
            "POST",
            probe_path(org1_id, active_id, "model-a", f"?project_id={project1_id}"),
            principal=org1_admin,
            payload={"probe_type": "not-a-real-probe-kind"},
        )
        check("FTF-0214", "an unrecognized probe_type is refused (400)", r.status == 400)

    # -- Additive fault injection (same convention this repository's own
    #    PRE-SUBMIT SELF-ATTACK requires): bypass the real H004 gate
    #    entirely and prove the exact check this route depends on is the
    #    one actually protecting it -- not a stub standing in for a check
    #    that was never really wired. ------------------------------------
    original_guard = model_catalog_routes_module.require_model_access

    def _bypass_guard(*args: object, **kwargs: object) -> None:
        return None

    model_catalog_routes_module.require_model_access = _bypass_guard
    try:
        bypass_app = FastAPI()
        bypass_app.include_router(
            build_model_catalog_router(
                session_factory,
                principal_resolver=fixture.principal_resolver,
                role_lookup=fixture.role_lookup,
                secret_manager_config_factory=lambda: _SECRET_CONFIG,
                validation_policy_factory=_test_policy,
            )
        )
        with _running_app(bypass_app) as bypass_base_url:
            r = _request(
                bypass_base_url,
                "POST",
                probe_path(org1_id, fresh_id, "model-a", f"?project_id={project1_id}"),
                principal=org1_admin,
                payload={"probe_type": "chat"},
            )
            check(
                "FTF-0214",
                "fault injection: with require_model_access bypassed, a never-discovered "
                "model is wrongly allowed to probe (200) -- proving the real guard (not "
                "this test) is what denies it above",
                r.status == 200,
            )
    finally:
        model_catalog_routes_module.require_model_access = original_guard

    if FAILURES:
        print(f"FTF-0214 real catalog/probe proof: {len(FAILURES)} failed")
        for failure in FAILURES:
            print(f"  {failure}")
        sys.exit(1)

    print("FTF-0214 real catalog/probe proof: all checks passed")
    sys.exit(0)


if __name__ == "__main__":
    main()
