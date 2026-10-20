#!/usr/bin/env python3
# mypy: disable-error-code="arg-type,attr-defined,return-value,assignment,call-arg,index,misc,no-untyped-def,var-annotated,union-attr,operator,no-any-return"
# ruff: noqa: E501
"""FTF-H010-P2 -- real PostgreSQL + real local TLS gateway proof of the import-validation
activity, job fencing and endpoint-only import (migration 0029).

Never SQLite (no RLS, no triggers, no `~`). Requires `SOVRA_DATABASE_URL` (a superuser migration
owner, like every sibling integration job) and `SOVRA_APP_DB_PASSWORD`. The gateway is a real
local HTTPS server (`/v1/models`, `/model/info`, `/v1/chat/completions` with
`x-litellm-model-id`) and the secret backend a real local OpenBao KV v2 stand-in, the same
fixtures `tests/tasks/test_ftf_h010_p*` and `test_ftf_h005_*` established.

Proves, against the live database as the restricted `sovra_app` role:

  M   migration 0029: upgrade 0028 -> head with a legacy P1 external_endpoint job present; the
      downgrade restores the 0028 trigger function body and the 0009 `ck_model_versions_kind_
      consistency` byte-identically; a downgrade with a NULL-reference endpoint row is refused and
      deletes nothing; upgrade again
  C   each new CHECK has a truth table, and the widened kind-consistency CHECK is exact
  T   the complete 5x5 transition matrix x epoch {same,+1,+2,-1} x lease {expired,live}
  G   the column-limited UPDATE grant is exactly the six columns; no DELETE; RLS on the table
  F   DATABASE faults (each applied inside a rolled-back transaction) flip a probe: CHECK widened
      to any endpoint row, trigger terminal-epoch clause dropped, re-claim before expiry allowed,
      result FK without project or origin, grant widened to requested_origin
  P   POST body: connection required/forbidden per origin, strict types, ref boundaries, foreign
      or missing connection 404 identical; FTF-1001's POST still cannot create a NULL-reference row
  A   the activity over the real routes + gateway: success shape, the outage table cases, alias
      exactness, missing header, revoked connection, legacy row, two threads claim -> one wins,
      re-claim after expiry at epoch+1, before expiry refused, a stale token writes nothing
  L   no database lock or open transaction while the gateway is being called
  X   META-LEAK: markers in ref, fingerprint, base_url and secret value never reach a failure
      code, an audit row, logs or an exception chain

Run directly:
    SOVRA_DATABASE_URL=postgresql+psycopg://<superuser>@host/db \\
    SOVRA_APP_DB_PASSWORD=<app-role-password> \\
    python3 tests/integration/test_ftf_h010_p2_import_validation.py
"""

from __future__ import annotations

import atexit
import contextlib
import http.client
import ipaddress
import json
import logging
import os
import shutil
import socket
import ssl
import subprocess
import sys
import tempfile
import threading
import time
import traceback
import urllib.parse
import uuid
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
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
from sqlalchemy import create_engine, text  # noqa: E402
from sqlalchemy.exc import DBAPIError  # noqa: E402
from sqlalchemy.orm import Session, sessionmaker  # noqa: E402

from app.artifacts.artifact_schema import TrainableArtifact  # noqa: E402
from app.auth.organization_workspace_schema import (  # noqa: E402
    APP_ROLE_NAME,
    Organization,
    Workspace,
)
from app.auth.rbac_middleware import AuthenticatedPrincipal, Role  # noqa: E402
from app.litellm.connection_schema import LiteLLMConnection  # noqa: E402
from app.model_versions import model_import_validation as activity  # noqa: E402
from app.model_versions.model_import_routes import build_model_import_router  # noqa: E402
from app.model_versions.model_version_routes import build_model_version_router  # noqa: E402
from app.projects.project_schema import Project  # noqa: E402

CID = "FTF-H010-P2"
FAILURES: list[str] = []
_ISSUER = "https://issuer.example"
# Built from parts so the markers never appear as literals in a traceback's source lines.
MARK_REF = "LEAK" + "REF"
MARK_FP = "LEAK" + "FP"
MARK_URL = "LEAK" + "URL"
MARK_SECRET = "LEAK" + "SECRET"
MARKERS = (MARK_REF, MARK_FP, MARK_URL, MARK_SECRET)
REF = f"org-agent-{MARK_REF}"
FINGERPRINT = f"deployment-{MARK_FP}"


def check(criterion_id: str, description: str, condition: bool) -> None:
    if not condition:
        FAILURES.append(f"FAIL [{criterion_id}] {description}")


def _required_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        print(
            f"BLOCKED_EXTERNAL: {name} is not set -- this task's real evidence requires a live "
            "Postgres (migration owner connection). Not silently skipped: see this file's own "
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


def _alembic(args: list[str], database_url: str) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    env["SOVRA_DATABASE_URL"] = database_url
    return subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=str(API_ROOT),
        capture_output=True,
        text=True,
        env=env,
    )


def _free_port() -> int:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


@contextlib.contextmanager
def _serve(app: FastAPI) -> Iterator[str]:
    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(300):
        if server.started:
            break
        time.sleep(0.02)
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(timeout=5)


class _Http:
    def __init__(self, status: int, body: bytes) -> None:
        self.status = status
        self.body = body

    def json(self) -> Any:
        return json.loads(self.body)


def _request(
    base: str, method: str, path: str, *, principal: str | None, payload: Any = None
) -> _Http:
    headers: dict[str, str] = {}
    if principal is not None:
        headers["X-Test-Principal"] = principal
    body: bytes | None = None
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    parsed = urllib.parse.urlparse(base)
    conn = http.client.HTTPConnection(parsed.hostname or "127.0.0.1", parsed.port or 80, timeout=30)
    try:
        conn.request(method, path, body=body, headers=headers)
        response = conn.getresponse()
        return _Http(response.status, response.read())
    finally:
        conn.close()


def _sqlstate(exc: DBAPIError) -> str | None:
    diag = getattr(exc.orig, "diag", None)
    return None if diag is None else diag.sqlstate


def _constraint(exc: DBAPIError) -> str | None:
    diag = getattr(exc.orig, "diag", None)
    return None if diag is None else diag.constraint_name


def _message(exc: DBAPIError) -> str:
    diag = getattr(exc.orig, "diag", None)
    return str(exc.orig) if diag is None else str(diag.message_primary)


# -- Real local secret backend (OpenBao KV v2 read subset) --------------------------------------

_SECRET_MOUNT = "secret"
_SECRET_PATH = "litellm/org1"
_SECRET_FIELD = "api_key"
_SECRET_VALUE = f"sk-{MARK_SECRET}-real-inference-key"
_SECRET_TOKEN = "test-vault-token"
# What the secret backend returns; a test swaps it to a key the gateway rejects.
_SECRET_VALUE_BOX = [_SECRET_VALUE]


class _SecretBackendHandler(BaseHTTPRequestHandler):
    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        pass

    def do_GET(self) -> None:  # noqa: N802
        body = b""
        status = 404
        if self.headers.get("X-Vault-Token") != _SECRET_TOKEN:
            status, body = 403, b'{"errors":["permission denied"]}'
        elif self.path == f"/v1/{_SECRET_MOUNT}/data/{_SECRET_PATH}":
            status = 200
            body = json.dumps(
                {
                    "data": {
                        "data": {_SECRET_FIELD: _SECRET_VALUE_BOX[0]},
                        "metadata": {"version": 1},
                    }
                }
            ).encode()
        self.send_response(status)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if body:
            self.wfile.write(body)


_SECRET_SERVER = ThreadingHTTPServer(("127.0.0.1", 0), _SecretBackendHandler)
threading.Thread(target=_SECRET_SERVER.serve_forever, daemon=True).start()
atexit.register(_SECRET_SERVER.shutdown)
_SECRET_CONFIG = OpenBaoConfig(
    address=f"http://127.0.0.1:{_SECRET_SERVER.server_address[1]}", token=_SECRET_TOKEN
)
_SECRET_REF = f"openbao://{_SECRET_MOUNT}/{_SECRET_PATH}/{_SECRET_FIELD}"

# -- Real local TLS gateway, same convention every sibling suite uses ------------------------------

_TLS_TMP = Path(tempfile.mkdtemp(prefix="ftf-h010p2-tls-"))
_CA_TRUST_TARGET = Path("/usr/local/share/ca-certificates/ftf-h010p2-test-ca.crt")


def _run(args: list[str]) -> None:
    subprocess.run(args, check=True, capture_output=True)


def _remove_ca_trust() -> None:
    subprocess.run(["sudo", "rm", "-f", str(_CA_TRUST_TARGET)], check=False, capture_output=True)
    subprocess.run(["sudo", "update-ca-certificates", "--fresh"], check=False, capture_output=True)


def _trusted_leaf() -> tuple[Path, Path]:
    if not (
        shutil.which("update-ca-certificates")
        and shutil.which("openssl")
        and shutil.which("sudo")
        and subprocess.run(["sudo", "-n", "true"], capture_output=True).returncode == 0
    ):
        print(
            "BLOCKED_EXTERNAL: this proof needs openssl, sudo and update-ca-certificates to "
            "trust a local CA for the run; not silently skipped.",
            file=sys.stderr,
        )
        sys.exit(1)
    ca_key, ca_crt = _TLS_TMP / "ca.key", _TLS_TMP / "ca.crt"
    leaf_key, leaf_csr, leaf_crt, leaf_ext = (
        _TLS_TMP / "leaf.key",
        _TLS_TMP / "leaf.csr",
        _TLS_TMP / "leaf.crt",
        _TLS_TMP / "leaf.ext",
    )
    _run(["openssl", "genrsa", "-out", str(ca_key), "2048"])
    _run(
        [
            "openssl",
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
            "/CN=FTF-H010-P2 Test CA",
            # Python 3.13+ verifies strictly: a CA needs basicConstraints, keyUsage and a SKI.
            "-addext",
            "basicConstraints=critical,CA:TRUE",
            "-addext",
            "keyUsage=critical,keyCertSign,cRLSign",
            "-addext",
            "subjectKeyIdentifier=hash",
        ]
    )
    _run(["openssl", "genrsa", "-out", str(leaf_key), "2048"])
    _run(
        [
            "openssl",
            "req",
            "-new",
            "-key",
            str(leaf_key),
            "-out",
            str(leaf_csr),
            "-subj",
            "/CN=127.0.0.1",
        ]
    )
    leaf_ext.write_text(
        "subjectAltName=IP:127.0.0.1\n"
        "basicConstraints=critical,CA:FALSE\n"
        "keyUsage=critical,digitalSignature,keyEncipherment\n"
        "extendedKeyUsage=serverAuth\n"
        "subjectKeyIdentifier=hash\n"
        "authorityKeyIdentifier=keyid,issuer\n"
    )
    _run(
        [
            "openssl",
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
    _run(["sudo", "cp", str(ca_crt), str(_CA_TRUST_TARGET)])
    _run(["sudo", "update-ca-certificates"])
    atexit.register(_remove_ca_trust)
    return leaf_crt, leaf_key


@dataclass
class GatewayState:
    prefix: str = "/" + MARK_URL
    models: list[str] = field(default_factory=lambda: [REF])
    models_status: int = 200
    chat_status: int = 200
    fingerprint: str | None = FINGERPRINT
    on_request: Any = None
    requests: list[str] = field(default_factory=list)


GATEWAY = GatewayState()


class _GatewayHandler(BaseHTTPRequestHandler):
    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        pass

    def _send(self, status: int, body: bytes, fingerprint: str | None = None) -> None:
        self.send_response(status)
        if fingerprint is not None:
            self.send_header("x-litellm-model-id", fingerprint)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if body:
            self.wfile.write(body)

    def _route(self) -> str:
        path = self.path
        return path[len(GATEWAY.prefix) :] if path.startswith(GATEWAY.prefix) else "?" + path

    def do_GET(self) -> None:  # noqa: N802
        route = self._route()
        GATEWAY.requests.append(f"GET {route}")
        if GATEWAY.on_request is not None:
            GATEWAY.on_request("GET " + route)
        if self.headers.get("Authorization") != f"Bearer {_SECRET_VALUE}":
            self._send(401, b'{"error":"no key"}')
        elif route == "/v1/models":
            body = json.dumps({"data": [{"id": m} for m in GATEWAY.models]}).encode()
            self._send(GATEWAY.models_status, body if GATEWAY.models_status == 200 else b"{}")
        else:
            self._send(404, b"")

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length", "0"))
        if length:
            self.rfile.read(length)
        route = self._route()
        GATEWAY.requests.append(f"POST {route}")
        if GATEWAY.on_request is not None:
            GATEWAY.on_request("POST " + route)
        if self.headers.get("Authorization") != f"Bearer {_SECRET_VALUE}":
            self._send(401, b'{"error":{"type":"invalid_request_error","message":"no key"}}')
            return
        if route != "/v1/chat/completions":
            self._send(404, b"")
            return
        body = json.dumps(
            {
                "id": "c1",
                "model": REF,
                "choices": [{"index": 0, "message": {"role": "assistant", "content": "pong"}}],
            }
        ).encode()
        self._send(
            GATEWAY.chat_status, body if GATEWAY.chat_status == 200 else b"{}", GATEWAY.fingerprint
        )


def _start_gateway() -> ThreadingHTTPServer:
    leaf, key = _trusted_leaf()
    server = ThreadingHTTPServer(("127.0.0.1", 0), _GatewayHandler)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(certfile=str(leaf), keyfile=str(key))
    server.socket = context.wrap_socket(server.socket, server_side=True)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    atexit.register(server.shutdown)
    return server


def _policy_factory(port: int) -> Any:
    def make() -> ConnectionPolicy:
        return ConnectionPolicy(
            name="test-litellm-gateway",
            allowed_schemes=frozenset({"https"}),
            allowed_ports=frozenset({port}),
            explicit_private_allowlist=(ipaddress.ip_network("127.0.0.0/8"),),
        )

    return make


class _LogCapture(logging.Handler):
    def __init__(self) -> None:
        super().__init__(level=logging.DEBUG)
        self.lines: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.lines.append(record.getMessage() + " " + str(record.exc_text or ""))


# -- main ----------------------------------------------------------------------------------------


def main() -> None:
    migration_url = _required_env("SOVRA_DATABASE_URL")
    app_password = _required_env("SOVRA_APP_DB_PASSWORD")
    gateway_server = _start_gateway()
    gateway_port = gateway_server.server_address[1]

    def alembic(label: str, *args: str) -> subprocess.CompletedProcess[str]:
        result = _alembic(list(args), migration_url)
        check(
            CID,
            f"M: alembic {label} runs cleanly (exit {result.returncode})\n{result.stdout}\n{result.stderr}",
            result.returncode == 0,
        )
        return result

    alembic("upgrade to 0028", "upgrade", "0028_model_import_state_machine")
    owner_engine = create_engine(migration_url)

    def func_source() -> str:
        with Session(owner_engine) as s:
            return s.execute(
                text(
                    "SELECT prosrc FROM pg_proc WHERE proname = 'model_import_jobs_state_transition_guard'"
                )
            ).scalar_one()

    def constraint_def(name: str) -> str:
        with Session(owner_engine) as s:
            return s.execute(
                text(
                    "SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conname = :n AND conrelid = 'model_versions'::regclass"
                ),
                {"n": name},
            ).scalar_one()

    src_0028 = func_source()
    kind_0009 = constraint_def("ck_model_versions_kind_consistency")

    suffix = uuid.uuid4().hex[:8]
    with Session(owner_engine) as session:
        org1 = Organization(name="Acme H010P2", slug=f"acme-h010p2-{suffix}")
        org2 = Organization(name="Globex H010P2", slug=f"globex-h010p2-{suffix}")
        session.add_all([org1, org2])
        session.flush()
        ws1 = Workspace(organization_id=org1.id, name="Acme Main", slug="main")
        ws2 = Workspace(organization_id=org2.id, name="Globex Main", slug="main")
        session.add_all([ws1, ws2])
        session.flush()
        projects = []
        for org, ws, name in (
            (org1, ws1, "acme-a"),
            (org1, ws1, "acme-b"),
            (org2, ws2, "globex-a"),
        ):
            project = Project(
                organization_id=org.id,
                workspace_id=ws.id,
                name=name,
                project_type="generic_model_tuning",
                status="active",
                security_profile="standard",
                row_version=1,
                created_by="seed",
            )
            session.add(project)
            projects.append(project)
        session.flush()
        artifact = TrainableArtifact(
            organization_id=org1.id,
            display_name="artifact-org1",
            source_type="huggingface",
            source_uri="acme-org/real-base-model",
            model_family="llama",
            base_model_revision="a1b2c3d4e5f6",
            weights_digest="sha256:" + "b" * 64,
            license_id="apache-2.0",
            license_accepted_by="seed",
            license_accepted_at=datetime.now(UTC),
            trust_remote_code_allowed=False,
            required_python_packages_json=[],
            minimum_gpu_profile_json={},
            status="ready",
        )
        session.add(artifact)
        session.commit()
        org1_id, org2_id = org1.id, org2.id
        project_a, project_b, project_g = (p.id for p in projects)
        artifact_id = artifact.id
        # a legacy FTF-H010-P1 external_endpoint job (no connection column existed yet)
        legacy_job = uuid.uuid4()
        session.execute(
            text(
                "INSERT INTO model_import_jobs (id, organization_id, project_id, requested_origin, external_provider_model_ref, state, created_by) VALUES (:id, :org, :project, 'external_endpoint', 'legacy-ref', 'queued', 'legacy')"
            ),
            {"id": str(legacy_job), "org": str(org1_id), "project": str(project_a)},
        )
        session.commit()

    alembic("upgrade head over the legacy row", "upgrade", "head")
    src_0029 = func_source()
    kind_0029 = constraint_def("ck_model_versions_kind_consistency")
    check(
        CID,
        "M: 0029 replaces the trigger function and the kind CHECK",
        src_0029 != src_0028 and kind_0029 != kind_0009,
    )
    check(
        CID,
        "M: the 0029 function carries the expiry-gated re-claim and the terminal-epoch rule",
        "OLD.lease_expires_at < now()" in src_0029
        and "IF NEW.claim_epoch <> OLD.claim_epoch THEN" in src_0029,
    )
    with Session(owner_engine) as s:
        legacy = s.execute(
            text(
                "SELECT state, litellm_connection_id, claim_epoch FROM model_import_jobs WHERE id = :id"
            ),
            {"id": str(legacy_job)},
        ).one()
        not_valid = s.execute(
            text(
                "SELECT NOT convalidated FROM pg_constraint WHERE conname = 'ck_model_import_jobs_connection_matches_origin'"
            )
        ).scalar_one()
    check(
        CID,
        "M: the legacy external_endpoint row survived the upgrade untouched",
        legacy.state == "queued"
        and legacy.litellm_connection_id is None
        and legacy.claim_epoch == 0,
    )
    check(
        CID,
        "M: the connection CHECK is NOT VALID (legacy rows) yet still enforced for new rows",
        not_valid is True,
    )

    # Three steps back from head: this migration (0031) first, then the two unrelated
    # migrations that now sit between it and 0028 on main -- 0030_dataset_split_manifests,
    # then 0029_artifact_ready_license (each independently claimed off 0028/0029 by a
    # different relay session; this migration was renumbered twice and rechained each time,
    # see 0031_model_import_validation.py's own docstring).
    alembic("downgrade -3", "downgrade", "-3")
    check(
        CID,
        "M: the downgrade restores the 0028 trigger function body byte-identically",
        func_source() == src_0028,
    )
    check(
        CID,
        "M: the downgrade restores the 0009 kind-consistency CHECK byte-identically",
        constraint_def("ck_model_versions_kind_consistency") == kind_0009,
    )
    with Session(owner_engine) as s:
        cols = {
            r[0]
            for r in s.execute(
                text(
                    "SELECT column_name FROM information_schema.columns WHERE table_name = 'model_import_jobs'"
                )
            )
        }
        grant_cols = {
            r[0]
            for r in s.execute(
                text(
                    "SELECT column_name FROM information_schema.column_privileges WHERE table_name = 'model_import_jobs' AND grantee = :r AND privilege_type = 'UPDATE'"
                ),
                {"r": APP_ROLE_NAME},
            )
        }
    check(
        CID,
        "M: the downgrade removes the four columns and restores the three-column grant",
        not (
            {"litellm_connection_id", "claim_epoch", "lease_expires_at", "result_model_version_id"}
            & cols
        )
        and grant_cols == {"state", "failure_code", "updated_at"},
    )
    alembic("upgrade head again", "upgrade", "head")
    check(CID, "M: re-upgrading reproduces the 0029 function body", func_source() == src_0029)

    app_engine = create_engine(_app_role_url(migration_url, app_password))
    session_factory = sessionmaker(bind=app_engine)
    check(
        CID,
        "the activity and the routes run as the restricted sovra_app role",
        app_engine.url.username == APP_ROLE_NAME,
    )

    # ---- seed connections (org1 two, org2 one) and a result ModelVersion via the real constructor --
    with Session(owner_engine) as s:
        conn1 = LiteLLMConnection(
            organization_id=org1_id,
            name="gw-1",
            base_url=f"https://127.0.0.1:{gateway_port}{GATEWAY.prefix}",
            secret_ref=_SECRET_REF,
        )
        conn2 = LiteLLMConnection(
            organization_id=org1_id,
            name="gw-2",
            base_url=f"https://127.0.0.1:{gateway_port}",
            secret_ref=_SECRET_REF,
        )
        conn_other = LiteLLMConnection(
            organization_id=org2_id,
            name="gw-other",
            base_url="https://other.example",
            secret_ref=_SECRET_REF,
        )
        s.add_all([conn1, conn2, conn_other])
        s.commit()
        conn1_id, conn2_id, conn_other_id = conn1.id, conn2.id, conn_other.id

    def set_tenant(session: Session, org: uuid.UUID) -> None:
        session.execute(
            text("SELECT set_config('app.current_organization_id', :o, true)"), {"o": str(org)}
        )

    def app_sql(
        org: uuid.UUID, sql: str, params: dict[str, Any] | None = None
    ) -> DBAPIError | None:
        with Session(app_engine) as session:
            set_tenant(session, org)
            try:
                session.execute(text(sql), params or {})
                session.commit()
                return None
            except DBAPIError as exc:
                session.rollback()
                return exc

    def owner_sql(sql: str, params: dict[str, Any] | None = None) -> None:
        with Session(owner_engine) as session:
            session.execute(text(sql), params or {})
            session.commit()

    version_counter = [100]

    def insert_version(
        extra: dict[str, Any], *, org: uuid.UUID = org1_id, project: uuid.UUID = project_a
    ) -> tuple[uuid.UUID, DBAPIError | None]:
        version_counter[0] += 1
        vid = uuid.uuid4()
        columns = {
            "id": str(vid),
            "organization_id": str(org),
            "project_id": str(project),
            "version_number": version_counter[0],
            "created_by": "p2",
            **extra,
        }
        names = ", ".join(columns)
        values = ", ".join(f":{c}" for c in columns)
        return vid, app_sql(org, f"INSERT INTO model_versions ({names}) VALUES ({values})", columns)

    # ---- C: CHECK truth tables ---------------------------------------------------------------------
    endpoint_cols = {"import_kind": "endpoint", "origin": "external_endpoint"}
    new_form, exc = insert_version({**endpoint_cols, "external_provider_model_ref": "alias-1"})
    check(
        CID,
        f"C1: an endpoint row with NULL endpoint_reference + explicit external_endpoint + provider ref is accepted (got {exc!r})",
        exc is None,
    )
    cases: list[tuple[str, dict[str, Any], str | None]] = [
        (
            "NULL reference, no provider ref",
            {**endpoint_cols},
            "ck_model_versions_external_endpoint_consistency",
        ),
        (
            "NULL reference, provider ref, origin factory_trained",
            {
                "import_kind": "endpoint",
                "origin": "factory_trained",
                "external_provider_model_ref": "x",
            },
            "ck_model_versions_kind_consistency",
        ),
        (
            "NULL reference, provider ref, origin imported_weights",
            {
                "import_kind": "endpoint",
                "origin": "imported_weights",
                "external_provider_model_ref": "x",
            },
            "ck_model_versions_kind_consistency",
        ),
        (
            "reference + digest (negative acceptance: no fictitious hash)",
            {
                **endpoint_cols,
                "endpoint_reference": "https://e.example",
                "external_provider_model_ref": "x",
                "weights_digest": "sha256:" + "a" * 64,
            },
            None,
        ),
        (
            "NULL reference + artifact",
            {
                **endpoint_cols,
                "external_provider_model_ref": "x",
                "trainable_artifact_id": str(artifact_id),
            },
            None,
        ),
        (
            "NULL reference + base revision",
            {**endpoint_cols, "external_provider_model_ref": "x", "base_model_revision": "abc123"},
            None,
        ),
    ]
    for label, extra, expected in cases:
        _, exc = insert_version(extra)
        check(
            CID,
            f"C2: endpoint row [{label}] is refused (got {exc!r})",
            exc is not None and _sqlstate(exc) == "23514",
        )
        if exc is not None and expected is not None:
            check(
                CID,
                f"C2: [{label}] is refused BY {expected} (got {_constraint(exc)!r})",
                _constraint(exc) == expected,
            )
    _, exc = insert_version(
        {
            "import_kind": "full_weight",
            "origin": "imported_weights",
            "trainable_artifact_id": str(artifact_id),
            "weights_digest": "sha256:" + "b" * 64,
            "base_model_revision": "a1b2c3d4e5f6",
        }
    )
    check(
        CID,
        f"C3: a full_weight row with a digest and artifact is still accepted (got {exc!r})",
        exc is None,
    )
    _, exc = insert_version(
        {
            "import_kind": "full_weight",
            "origin": "imported_weights",
            "trainable_artifact_id": str(artifact_id),
            "weights_digest": "sha256:" + "b" * 64,
            "base_model_revision": "a1b2c3d4e5f6",
            "endpoint_reference": "https://e.example",
        }
    )
    check(
        CID,
        "C3: a full_weight row carrying an endpoint_reference is still refused",
        exc is not None and _constraint(exc) == "ck_model_versions_kind_consistency",
    )

    def seed_job(
        state: str,
        epoch: int = 0,
        lease_sql: str = "NULL",
        *,
        result: uuid.UUID | None = None,
        failure: str | None = None,
        origin: str = "external_endpoint",
        org: uuid.UUID = org1_id,
        project: uuid.UUID = project_a,
        connection: uuid.UUID | None = conn1_id,
        ref: str | None = "p2-ref",
    ) -> uuid.UUID:
        """Owner insert with triggers/FKs bypassed (`session_replication_role = replica`), so any
        state, epoch and lease can be seeded; the update under test then runs as sovra_app."""
        job_id = uuid.uuid4()
        with Session(owner_engine) as s:
            s.execute(text("SET session_replication_role = replica"))
            s.execute(
                text(
                    f"INSERT INTO model_import_jobs (id, organization_id, project_id, requested_origin, external_provider_model_ref, litellm_connection_id, state, claim_epoch, lease_expires_at, failure_code, result_model_version_id, created_by) VALUES (:id, :org, :project, :origin, :ref, :conn, :state, :epoch, {lease_sql}, :failure, :result, 'p2')"
                ),
                {
                    "id": str(job_id),
                    "org": str(org),
                    "project": str(project),
                    "origin": origin,
                    "ref": ref,
                    "conn": None if connection is None else str(connection),
                    "state": state,
                    "epoch": epoch,
                    "failure": failure,
                    "result": None if result is None else str(result),
                },
            )
            s.execute(text("SET session_replication_role = DEFAULT"))
            s.commit()
        return job_id

    # ---- C: job CHECK truth tables (owner-seeded rows are bypassed, so use sovra_app INSERT/UPDATE) ----
    def raw_job_insert(extra: dict[str, Any]) -> DBAPIError | None:
        columns = {
            "id": str(uuid.uuid4()),
            "organization_id": str(org1_id),
            "project_id": str(project_a),
            "requested_origin": "external_endpoint",
            "external_provider_model_ref": "t",
            "litellm_connection_id": str(conn1_id),
            "created_by": "p2",
            **extra,
        }
        columns = {k: v for k, v in columns.items() if v is not ...}
        names = ", ".join(columns)
        values = ", ".join(f":{c}" for c in columns)
        return app_sql(
            org1_id, f"INSERT INTO model_import_jobs ({names}) VALUES ({values})", columns
        )

    exc = raw_job_insert({})
    check(
        CID,
        f"C4: a well-formed external_endpoint job with a connection inserts at queued (got {exc!r})",
        exc is None,
    )
    exc = raw_job_insert({"litellm_connection_id": None})
    check(
        CID,
        "C4: ck_..._connection_matches_origin refuses external_endpoint without a connection",
        exc is not None and _constraint(exc) == "ck_model_import_jobs_connection_matches_origin",
    )
    exc = raw_job_insert(
        {"requested_origin": "imported_weights", "trainable_artifact_id": str(artifact_id)}
    )
    check(
        CID,
        "C4: ck_..._connection_matches_origin refuses a non-endpoint origin carrying a connection",
        exc is not None and _constraint(exc) == "ck_model_import_jobs_connection_matches_origin",
    )
    exc = raw_job_insert(
        {
            "requested_origin": "imported_weights",
            "trainable_artifact_id": str(artifact_id),
            "litellm_connection_id": None,
        }
    )
    check(CID, f"C4: a non-endpoint job without a connection inserts (got {exc!r})", exc is None)
    exc = raw_job_insert({"litellm_connection_id": str(conn_other_id)})
    check(
        CID,
        "C4: a foreign-tenant connection is refused by the composite FK",
        exc is not None
        and _constraint(exc) == "fk_model_import_jobs_organization_id_litellm_connection_id",
    )
    for label, extra, name in (
        ("claim_epoch 1 at queued", {"claim_epoch": 1}, "model_import_jobs_state_transition"),
        (
            "a lease at queued",
            {"lease_expires_at": datetime.now(UTC) + timedelta(hours=1)},
            "model_import_jobs_state_transition",
        ),
        (
            "a result at queued",
            {"result_model_version_id": str(new_form)},
            "model_import_jobs_state_transition",
        ),
        (
            "state validating",
            {
                "state": "validating",
                "claim_epoch": 1,
                "lease_expires_at": datetime.now(UTC) + timedelta(hours=1),
            },
            "model_import_jobs_state_transition",
        ),
    ):
        exc = raw_job_insert(extra)
        check(
            CID,
            f"C5: an INSERT with {label} is refused by the trigger (got {exc!r})",
            exc is not None and _sqlstate(exc) == "23514" and name in _message(exc),
        )
    exc = raw_job_insert({"claim_epoch": -1})
    check(
        CID, "C5: a negative claim_epoch is refused", exc is not None and _sqlstate(exc) == "23514"
    )

    def update_job(
        job_id: uuid.UUID, sets: str, params: dict[str, Any] | None = None, org: uuid.UUID = org1_id
    ) -> DBAPIError | None:
        return app_sql(
            org,
            f"UPDATE model_import_jobs SET {sets} WHERE id = :id",
            {"id": str(job_id), **(params or {})},
        )

    result_version = (
        new_form  # origin external_endpoint, project_a, org1: valid as a result for the jobs below
    )
    for label, state, epoch, lease, failure, result in (
        ("queued with epoch 1", "queued", 1, "NULL", None, None),
        ("validating without a lease", "validating", 1, "NULL", None, None),
        ("failed with a lease", "failed", 1, "now() + interval '1 hour'", "x", None),
        ("succeeded without a result", "succeeded", 1, "NULL", None, None),
        (
            "validating with a result",
            "validating",
            1,
            "now() + interval '1 hour'",
            None,
            result_version,
        ),
        ("failed without a failure code", "failed", 1, "NULL", None, None),
    ):
        job = seed_job("validating", 1, "now() + interval '1 hour'")
        exc = update_job(
            job,
            f"state = :s, claim_epoch = :e, lease_expires_at = {lease}, failure_code = :f, result_model_version_id = :r",
            {"s": state, "e": epoch, "f": failure, "r": None if result is None else str(result)},
        )
        check(
            CID,
            f"C6: [{label}] is refused (got {exc!r})",
            exc is not None and _sqlstate(exc) == "23514",
        )
    job = seed_job("validating", 1, "now() + interval '1 hour'")
    exc = update_job(
        job,
        "state = 'succeeded', claim_epoch = 1, lease_expires_at = NULL, result_model_version_id = :r",
        {"r": str(result_version)},
    )
    check(
        CID,
        f"C6: validating -> succeeded with a same-project, same-origin result is accepted (got {exc!r})",
        exc is None,
    )

    # ---- F-prep: result FK + grants -------------------------------------------------------------------
    other_origin_version, exc = insert_version(
        {
            "import_kind": "full_weight",
            "origin": "imported_weights",
            "trainable_artifact_id": str(artifact_id),
            "weights_digest": "sha256:" + "b" * 64,
            "base_model_revision": "a1b2c3d4e5f6",
        }
    )
    other_project_version, exc2 = insert_version(
        {**endpoint_cols, "external_provider_model_ref": "alias-b"}, project=project_b
    )
    check(
        CID,
        "F0: the cross-origin and cross-project result candidates exist",
        exc is None and exc2 is None,
    )

    def result_fk_probe() -> bool:
        """True when a succeeded job may point at a result of another project or another origin."""
        bad = 0
        for target in (other_origin_version, other_project_version):
            job = seed_job("validating", 1, "now() + interval '1 hour'")
            exc = update_job(
                job,
                "state = 'succeeded', lease_expires_at = NULL, result_model_version_id = :r",
                {"r": str(target)},
            )
            bad += exc is None
        return bad > 0

    check(
        CID,
        "G1: the result FK refuses a result of another origin and of another project (composite FK)",
        result_fk_probe() is False,
    )
    job = seed_job("validating", 1, "now() + interval '1 hour'")
    exc = update_job(
        job,
        "state = 'succeeded', lease_expires_at = NULL, result_model_version_id = :r",
        {"r": str(other_project_version)},
    )
    check(
        CID,
        "G1: ... by name",
        exc is not None and _constraint(exc) == "fk_model_import_jobs_result_model_version",
    )

    privileges = {}
    with Session(owner_engine) as s:
        for column in (
            "state",
            "failure_code",
            "updated_at",
            "result_model_version_id",
            "claim_epoch",
            "lease_expires_at",
            "requested_origin",
            "litellm_connection_id",
            "organization_id",
            "project_id",
            "external_provider_model_ref",
            "created_by",
            "created_at",
            "id",
            "parent_model_version_id",
            "trainable_artifact_id",
        ):
            privileges[column] = s.execute(
                text("SELECT has_column_privilege(:r, 'model_import_jobs', :c, 'UPDATE')"),
                {"r": APP_ROLE_NAME, "c": column},
            ).scalar_one()
        can_delete = s.execute(
            text("SELECT has_table_privilege(:r, 'model_import_jobs', 'DELETE')"),
            {"r": APP_ROLE_NAME},
        ).scalar_one()
        rls = s.execute(
            text(
                "SELECT relrowsecurity, relforcerowsecurity FROM pg_class WHERE relname = 'model_import_jobs'"
            )
        ).one()
    granted = {c for c, ok in privileges.items() if ok}
    check(
        CID,
        f"G2: the UPDATE grant is exactly the six outcome/fence columns (got {sorted(granted)})",
        granted
        == {
            "state",
            "failure_code",
            "updated_at",
            "result_model_version_id",
            "claim_epoch",
            "lease_expires_at",
        },
    )
    check(CID, "G2: sovra_app can never DELETE a job", can_delete is False)
    check(
        CID,
        "G2: row level security is ENABLED and FORCED on model_import_jobs",
        rls.relrowsecurity and rls.relforcerowsecurity,
    )
    job = seed_job("queued")
    for column, value in (
        ("requested_origin", "'imported_weights'"),
        ("litellm_connection_id", "NULL"),
        ("project_id", f"'{project_b}'"),
    ):
        exc = update_job(job, f"{column} = {value}")
        check(
            CID,
            f"G3: UPDATE of {column} is denied (sqlstate {exc and _sqlstate(exc)})",
            exc is not None and _sqlstate(exc) == "42501",
        )
    with Session(app_engine) as session:
        set_tenant(session, org2_id)
        n = session.execute(text("SELECT count(*) FROM model_import_jobs")).scalar_one()
        session.commit()
    check(CID, "G4: the org2 context sees none of org1's jobs", n == 0)
    exc = app_sql(
        org1_id,
        "INSERT INTO model_import_jobs (id, organization_id, project_id, requested_origin, external_provider_model_ref, litellm_connection_id, created_by) VALUES (:id, :org, :project, 'external_endpoint', 'x', :conn, 'p2')",
        {
            "id": str(uuid.uuid4()),
            "org": str(org2_id),
            "project": str(project_g),
            "conn": str(conn_other_id),
        },
    )
    check(
        CID,
        "G4: a raw org2 INSERT under the org1 context is rejected by RLS",
        exc is not None and _sqlstate(exc) == "42501",
    )

    # ---- T: the complete transition matrix -----------------------------------------------------------------
    states = ("queued", "validating", "failed", "succeeded", "blocked_external")
    terminal = ("failed", "succeeded", "blocked_external")
    deltas = {"same": 0, "+1": 1, "+2": 2, "-1": -1}

    def oracle(source: str, target: str, delta: str, lease: str) -> bool:
        if source in terminal:
            return False
        if source == "queued":
            if target == "queued":
                return delta == "same"
            return target == "validating" and delta == "+1"
        if target == "validating":
            return delta == "same" or (delta == "+1" and lease == "expired")
        return target in terminal and delta == "same"

    def seed_source(source: str, lease: str) -> tuple[uuid.UUID, int]:
        if source == "queued":
            return seed_job("queued"), 0
        if source == "validating":
            return seed_job(
                "validating",
                3,
                "now() - interval '1 hour'"
                if lease == "expired"
                else "now() + interval '30 minutes'",
            ), 3
        return seed_job(
            source,
            3,
            failure=None if source == "succeeded" else "seed",
            result=result_version if source == "succeeded" else None,
        ), 3

    def target_sets(target: str, epoch: int) -> tuple[str, dict[str, Any]]:
        base = "state = :s, claim_epoch = :e, updated_at = now()"
        params: dict[str, Any] = {"s": target, "e": epoch}
        if target == "queued":
            return (
                base
                + ", lease_expires_at = NULL, failure_code = NULL, result_model_version_id = NULL",
                params,
            )
        if target == "validating":
            return (
                base
                + ", lease_expires_at = now() + interval '2 hours', failure_code = NULL, result_model_version_id = NULL",
                params,
            )
        if target == "succeeded":
            return (
                base
                + ", lease_expires_at = NULL, failure_code = NULL, result_model_version_id = :r",
                {**params, "r": str(result_version)},
            )
        return (
            base
            + ", lease_expires_at = NULL, failure_code = 'matrix', result_model_version_id = NULL",
            params,
        )

    cells = 0
    for source in states:
        for target in states:
            for delta_label, delta in deltas.items():
                for lease in ("expired", "live"):
                    job, epoch0 = seed_source(source, lease)
                    sets, params = target_sets(target, epoch0 + delta)
                    exc = update_job(job, sets, params)
                    want = oracle(source, target, delta_label, lease)
                    cells += 1
                    label = f"T: {source} -> {target}, epoch {delta_label}, lease {lease}"
                    check(
                        CID,
                        f"{label} is {'allowed' if want else 'refused'} (got {'allowed' if exc is None else _message(exc)})",
                        (exc is None) == want,
                    )
                    if exc is not None:
                        check(
                            CID,
                            f"{label} is refused with sqlstate 23514",
                            _sqlstate(exc) == "23514",
                        )
                    else:
                        with Session(owner_engine) as s:
                            row = s.execute(
                                text(
                                    "SELECT state, claim_epoch FROM model_import_jobs WHERE id = :id"
                                ),
                                {"id": str(job)},
                            ).one()
                        check(
                            CID,
                            f"{label} reads back",
                            (row.state, row.claim_epoch) == (target, epoch0 + delta),
                        )
    check(CID, f"T: the matrix covers 5 x 5 x 4 x 2 = 200 cells (ran {cells})", cells == 200)
    for label, source_lease, new_lease, want in (
        (
            "renewal with a later lease",
            "now() + interval '30 minutes'",
            "now() + interval '40 minutes'",
            True,
        ),
        (
            "renewal with the same lease",
            "'2030-01-01 00:00:00+00'::timestamptz",
            "'2030-01-01 00:00:00+00'::timestamptz",
            False,
        ),
        (
            "renewal with an earlier lease",
            "'2030-01-01 00:00:00+00'::timestamptz",
            "'2029-01-01 00:00:00+00'::timestamptz",
            False,
        ),
    ):
        job = seed_job("validating", 3, source_lease)
        exc = update_job(job, f"lease_expires_at = {new_lease}")
        check(
            CID,
            f"T: [{label}] is {'allowed' if want else 'refused'} (got {exc!r})",
            (exc is None) == want,
        )

    # ---- F: DATABASE faults, each in a rolled-back owner transaction --------------------------------------
    def in_fault(ddl: list[str], probe: Any) -> bool:
        """Applies `ddl` as the owner in one transaction, runs `probe` there as sovra_app, rolls back."""
        with Session(owner_engine) as s:
            try:
                for statement in ddl:
                    s.execute(text(statement))
                s.execute(text(f'SET LOCAL ROLE "{APP_ROLE_NAME}"'))
                set_tenant(s, org1_id)
                return bool(probe(s))
            finally:
                s.rollback()

    def tx_update(s: Session, job: uuid.UUID, sets: str) -> bool:
        """True when the UPDATE was accepted (in a savepoint, so a refusal does not poison `s`)."""
        savepoint = s.begin_nested()
        try:
            s.execute(text(f"UPDATE model_import_jobs SET {sets} WHERE id = :id"), {"id": str(job)})
            savepoint.rollback()
            return True
        except DBAPIError:
            savepoint.rollback()
            return False

    def seed_in_tx(s: Session, *, state: str, epoch: int, lease_sql: str) -> uuid.UUID:
        job_id = uuid.uuid4()
        s.execute(text("RESET ROLE"))
        s.execute(text("SET LOCAL session_replication_role = replica"))
        s.execute(
            text(
                f"INSERT INTO model_import_jobs (id, organization_id, project_id, requested_origin, external_provider_model_ref, litellm_connection_id, state, claim_epoch, lease_expires_at, created_by) VALUES (:id, :org, :project, 'external_endpoint', 'f', :conn, :state, :epoch, {lease_sql}, 'p2')"
            ),
            {
                "id": str(job_id),
                "org": str(org1_id),
                "project": str(project_a),
                "conn": str(conn1_id),
                "state": state,
                "epoch": epoch,
            },
        )
        s.execute(text("SET LOCAL session_replication_role = DEFAULT"))
        s.execute(text(f'SET LOCAL ROLE "{APP_ROLE_NAME}"'))
        return job_id

    def probe_terminal_other_epoch(s: Session) -> bool:
        job = seed_in_tx(s, state="validating", epoch=3, lease_sql="now() + interval '1 hour'")
        return tx_update(
            s, job, "state = 'failed', failure_code = 'x', claim_epoch = 4, lease_expires_at = NULL"
        )

    def probe_reclaim_before_expiry(s: Session) -> bool:
        job = seed_in_tx(s, state="validating", epoch=3, lease_sql="now() + interval '1 hour'")
        return tx_update(s, job, "claim_epoch = 4, lease_expires_at = now() + interval '2 hours'")

    def probe_null_ref_no_provider(s: Session) -> bool:
        savepoint = s.begin_nested()
        try:
            s.execute(
                text(
                    "INSERT INTO model_versions (id, organization_id, project_id, version_number, import_kind, origin, created_by) VALUES (gen_random_uuid(), :o, :p, 99999, 'endpoint', 'factory_trained', 'f')"
                ),
                {"o": str(org1_id), "p": str(project_a)},
            )
            savepoint.rollback()
            return True
        except DBAPIError:
            savepoint.rollback()
            return False

    def probe_origin_update(s: Session) -> bool:
        job = seed_in_tx(s, state="queued", epoch=0, lease_sql="NULL")
        return tx_update(s, job, "requested_origin = 'external_endpoint'")

    def probe_result_fk(s: Session) -> bool:
        job = seed_in_tx(s, state="validating", epoch=1, lease_sql="now() + interval '1 hour'")
        return tx_update(
            s,
            job,
            f"state = 'succeeded', lease_expires_at = NULL, result_model_version_id = '{other_project_version}'",
        )

    drop_terminal_clause = src_0029.replace(
        "IF NEW.claim_epoch <> OLD.claim_epoch THEN", "IF false THEN"
    )
    drop_expiry = src_0029.replace(" AND OLD.lease_expires_at < now()", "")
    check(
        CID,
        "F: the fault text for the terminal-epoch clause occurs in the real function",
        drop_terminal_clause != src_0029 and drop_expiry != src_0029,
    )

    def replace_function(body: str) -> str:
        return (
            "CREATE OR REPLACE FUNCTION model_import_jobs_state_transition_guard() RETURNS trigger LANGUAGE plpgsql AS $fault$"
            + body
            + "$fault$"
        )

    for name, probe, honest_bad, ddl in (
        (
            "trigger terminal-epoch clause dropped",
            probe_terminal_other_epoch,
            False,
            [replace_function(drop_terminal_clause)],
        ),
        (
            "re-claim before expiry allowed",
            probe_reclaim_before_expiry,
            False,
            [replace_function(drop_expiry)],
        ),
        (
            "kind CHECK widened to any endpoint row",
            probe_null_ref_no_provider,
            False,
            [
                "ALTER TABLE model_versions DROP CONSTRAINT ck_model_versions_kind_consistency",
                "ALTER TABLE model_versions DROP CONSTRAINT ck_model_versions_origin_import_kind_consistency",
                "ALTER TABLE model_versions DROP CONSTRAINT ck_model_versions_external_endpoint_consistency",
                "ALTER TABLE model_versions ADD CONSTRAINT ck_model_versions_kind_consistency CHECK (import_kind <> 'endpoint' OR true)",
            ],
        ),
        (
            "grant widened to requested_origin",
            probe_origin_update,
            False,
            [f'GRANT UPDATE (requested_origin) ON model_import_jobs TO "{APP_ROLE_NAME}"'],
        ),
        (
            "result FK without project or origin",
            probe_result_fk,
            False,
            [
                "ALTER TABLE model_import_jobs DROP CONSTRAINT fk_model_import_jobs_result_model_version",
                "ALTER TABLE model_import_jobs ADD CONSTRAINT fk_model_import_jobs_result_model_version FOREIGN KEY (organization_id, result_model_version_id) REFERENCES model_versions (organization_id, id)",
            ],
        ),
    ):
        honest = in_fault([], probe)
        faulty = in_fault(ddl, probe)
        check(
            CID,
            f"F: honest database refuses the [{name}] probe (probe says bad outcome: {honest})",
            honest is honest_bad,
        )
        check(
            CID,
            f"F: the fault [{name}] turns the probe RED (bad outcome observed: {faulty})",
            faulty is True,
        )
    with Session(owner_engine) as s:
        check(
            CID,
            "F: every fault transaction rolled back (function body unchanged)",
            s.execute(
                text(
                    "SELECT prosrc FROM pg_proc WHERE proname = 'model_import_jobs_state_transition_guard'"
                )
            ).scalar_one()
            == src_0029,
        )

    # ---- P / A: routes + activity ----------------------------------------------------------------------------------
    principals = {
        "ml1": (AuthenticatedPrincipal(issuer=_ISSUER, subject="ml-1"), Role.ML_ENGINEER, org1_id),
        "viewer1": (
            AuthenticatedPrincipal(issuer=_ISSUER, subject="viewer-1"),
            Role.VIEWER,
            org1_id,
        ),
        "admin2": (
            AuthenticatedPrincipal(issuer=_ISSUER, subject="admin-2"),
            Role.ORGANIZATION_ADMIN,
            org2_id,
        ),
    }

    def principal_resolver(request: Any) -> AuthenticatedPrincipal | None:
        entry = principals.get(request.headers.get("x-test-principal", ""))
        return None if entry is None else entry[0]

    def role_lookup(issuer: str, subject: str, organization_id: uuid.UUID) -> Role | None:
        for principal, role, org in principals.values():
            if principal.subject == subject and org == organization_id:
                return role
        return None

    app = FastAPI()
    app.include_router(
        build_model_import_router(
            session_factory, principal_resolver=principal_resolver, role_lookup=role_lookup
        )
    )
    app.include_router(
        build_model_version_router(
            session_factory, principal_resolver=principal_resolver, role_lookup=role_lookup
        )
    )

    def imports(org: uuid.UUID, project: uuid.UUID) -> str:
        return f"/api/v1/organizations/{org}/projects/{project}/model-imports"

    def versions(org: uuid.UUID, project: uuid.UUID) -> str:
        return f"/api/v1/organizations/{org}/projects/{project}/model-versions"

    with _serve(app) as base:

        def post_import(
            ref: Any = REF,
            connection: Any = conn2_id,
            origin: str = "external_endpoint",
            project: uuid.UUID = project_a,
            **extra: Any,
        ) -> _Http:
            payload: dict[str, Any] = {"requested_origin": origin, **extra}
            if ref is not ...:
                payload["external_provider_model_ref"] = ref
            if connection is not ...:
                payload["litellm_connection_id"] = (
                    str(connection) if isinstance(connection, uuid.UUID) else connection
                )
            return _request(
                base, "POST", imports(org1_id, project), principal="ml1", payload=payload
            )

        # -- P: strict POST body ------------------------------------------------------------
        r = post_import()
        check(
            CID,
            f"P1: a valid external_endpoint request is 201 at queued (got {r.status})",
            r.status == 201 and r.json()["state"] == "queued",
        )
        body = r.json() if r.status == 201 else {}
        check(
            CID,
            "P1: the 201 body carries the connection and a NULL result",
            body.get("litellm_connection_id") == str(conn2_id)
            and body.get("result_model_version_id") is None,
        )
        with Session(owner_engine) as s:
            row = s.execute(
                text("SELECT claim_epoch, lease_expires_at FROM model_import_jobs WHERE id = :id"),
                {"id": body["id"]},
            ).one()
        check(
            CID,
            "P1: the stored job starts at epoch 0 with no lease",
            (row.claim_epoch, row.lease_expires_at) == (0, None),
        )
        for label, connection in (
            ("missing", ...),
            ("null", None),
            ("int", 7),
            ("bool", True),
            ("list", []),
            ("dict", {}),
            ("float", 1.5),
            ("empty string", ""),
            ("non-uuid", "nope"),
        ):
            r = post_import(connection=connection)
            check(
                CID,
                f"P2: litellm_connection_id {label} on external_endpoint -> 400 invalid_body (got {r.status} {r.body[:80]!r})",
                r.status == 400 and r.json() == {"detail": "invalid_body"},
            )
        for origin in ("imported_weights", "imported_adapter"):
            r = post_import(origin=origin, ref=...)
            check(
                CID,
                f"P3: {origin} with a connection -> 400 invalid_body",
                r.status == 400 and r.json() == {"detail": "invalid_body"},
            )
        r = post_import(connection=conn_other_id)
        missing = post_import(connection=uuid.uuid4())
        check(
            CID,
            f"P4: a foreign connection is 404 (got {r.status})",
            r.status == 404 and r.json() == {"detail": "not_found"},
        )
        check(
            CID,
            "P4: ... byte-identical to a missing connection",
            missing.status == 404 and missing.body == r.body,
        )
        for label, ref, ok in (
            ("0 chars", "", False),
            ("1 char", "x", True),
            ("2048 chars", "x" * 2048, True),
            ("2049 chars", "x" * 2049, False),
            ("whitespace only", "   ", False),
            ("NUL", "a\x00b", False),
            ("CR", "a\rb", False),
            ("LF", "a\nb", False),
            ("lone surrogate", "a\ud800b", False),
            ("a tab", "a\tb", False),
        ):
            r = post_import(ref=ref)
            check(
                CID,
                f"P5: external_provider_model_ref [{label}] -> {'201' if ok else '400'} (got {r.status})",
                (r.status == 201) if ok else (r.status == 400),
            )
        r = post_import(ref=7)
        check(CID, "P5: a numeric ref is 400", r.status == 400)
        r = _request(
            base,
            "POST",
            imports(org1_id, project_a),
            principal="viewer1",
            payload={
                "requested_origin": "external_endpoint",
                "external_provider_model_ref": "x",
                "litellm_connection_id": str(conn2_id),
            },
        )
        check(CID, "P6: a viewer cannot POST (403)", r.status == 403)
        r = _request(
            base,
            "POST",
            imports(org1_id, project_a),
            principal="ml1",
            payload={
                "requested_origin": "external_endpoint",
                "external_provider_model_ref": "x",
                "litellm_connection_id": str(conn2_id),
                "claim_epoch": 9,
            },
        )
        check(CID, "P6: claim_epoch is not an accepted body key", r.status == 400)
        r = _request(
            base,
            "POST",
            imports(org2_id, project_g),
            principal="admin2",
            payload={
                "requested_origin": "external_endpoint",
                "external_provider_model_ref": "x",
                "litellm_connection_id": str(conn1_id),
            },
        )
        check(CID, "P6: org2 cannot name org1's connection (404)", r.status == 404)

        # FTF-1001's POST still cannot create a NULL-reference row, even when sent a provider ref
        for label, payload in (
            ("no reference", {"import_kind": "endpoint"}),
            (
                "only a provider ref",
                {"import_kind": "endpoint", "external_provider_model_ref": "alias-x"},
            ),
            (
                "provider ref and origin",
                {
                    "import_kind": "endpoint",
                    "external_provider_model_ref": "alias-x",
                    "origin": "external_endpoint",
                },
            ),
            (
                "null reference",
                {
                    "import_kind": "endpoint",
                    "endpoint_reference": None,
                    "external_provider_model_ref": "alias-x",
                },
            ),
            (
                "http reference",
                {"import_kind": "endpoint", "endpoint_reference": "http://e.example"},
            ),
        ):
            r = _request(
                base, "POST", versions(org1_id, project_a), principal="ml1", payload=payload
            )
            check(CID, f"P7: FTF-1001 POST [{label}] -> 400 (got {r.status})", r.status == 400)
        with Session(owner_engine) as s:
            n_null = s.execute(
                text(
                    "SELECT count(*) FROM model_versions WHERE import_kind = 'endpoint' AND endpoint_reference IS NULL AND external_provider_model_ref LIKE 'alias-x%'"
                )
            ).scalar_one()
        check(CID, "P7: no NULL-reference row came from FTF-1001's POST", n_null == 0)
        r = _request(
            base,
            "POST",
            versions(org1_id, project_a),
            principal="ml1",
            payload={
                "import_kind": "endpoint",
                "endpoint_reference": "https://models.acme.example.com/v1/production",
            },
        )
        check(
            CID,
            "P7: FTF-1001's own https endpoint create still works unchanged",
            r.status == 201
            and r.json()["endpoint_reference"] == "https://models.acme.example.com/v1/production",
        )

        # -- A: the activity -----------------------------------------------------------------------
        logs = _LogCapture()
        root = logging.getLogger()
        root.addHandler(logs)
        root.setLevel(logging.DEBUG)
        policy = _policy_factory(gateway_port)
        lock_findings: list[str] = []

        def lock_probe(job_id: uuid.UUID | None) -> Any:
            def probe(request: str) -> None:
                with Session(owner_engine) as s:
                    busy = s.execute(
                        text(
                            "SELECT count(*) FROM pg_stat_activity WHERE usename = :u AND datname = current_database() AND state <> 'idle' AND pid <> pg_backend_pid()"
                        ),
                        {"u": APP_ROLE_NAME},
                    ).scalar_one()
                    if busy:
                        lock_findings.append(f"{request}: {busy} sovra_app session(s) not idle")
                    if job_id is not None:
                        try:
                            s.execute(
                                text(
                                    "SELECT id FROM model_import_jobs WHERE id = :id FOR UPDATE NOWAIT"
                                ),
                                {"id": str(job_id)},
                            )
                        except DBAPIError:
                            lock_findings.append(
                                f"{request}: the job row is locked during the gateway call"
                            )
                    s.rollback()

            return probe

        def run(
            ref: str = REF,
            connection: uuid.UUID = conn1_id,
            project: uuid.UUID = project_a,
            *,
            claim_now: datetime | None = None,
            lease: int = 600,
            validate: bool = True,
            parent: uuid.UUID | None = None,
            hook_job: bool = True,
        ) -> tuple[uuid.UUID, Any, Any]:
            r = _request(
                base,
                "POST",
                imports(org1_id, project),
                principal="ml1",
                payload={
                    "requested_origin": "external_endpoint",
                    "external_provider_model_ref": ref,
                    "litellm_connection_id": str(connection),
                    **({} if parent is None else {"parent_model_version_id": str(parent)}),
                },
            )
            assert r.status == 201, (r.status, r.body)
            job_id = uuid.UUID(r.json()["id"])
            token = activity.claim_model_import_job(
                session_factory,
                org1_id,
                project,
                job_id,
                origins=("external_endpoint",),
                worker_id="worker-1",
                lease_seconds=lease,
                now=claim_now or datetime.now(UTC),
            )
            if hook_job:
                GATEWAY.on_request = lock_probe(job_id)
            outcome = None
            if validate:
                outcome = activity.validate_endpoint_import(
                    session_factory,
                    token,
                    secret_config=_SECRET_CONFIG,
                    now=datetime.now(UTC),
                    policy_factory=policy,
                    timeout=10.0,
                )
            GATEWAY.on_request = None
            return job_id, token, outcome

        def job_row(job_id: uuid.UUID) -> Any:
            with Session(owner_engine) as s:
                return s.execute(
                    text(
                        "SELECT state, failure_code, claim_epoch, lease_expires_at, result_model_version_id FROM model_import_jobs WHERE id = :id"
                    ),
                    {"id": str(job_id)},
                ).one()

        def audit_rows(job_id: uuid.UUID) -> list[Any]:
            with Session(owner_engine) as s:
                return list(
                    s.execute(
                        text(
                            "SELECT actor_type, actor_id, action, resource_type, details_json::text AS details FROM audit_events WHERE resource_id = :id AND action = 'model_import.validate'"
                        ),
                        {"id": str(job_id)},
                    )
                )

        def n_versions(project: uuid.UUID = project_a) -> int:
            with Session(owner_engine) as s:
                return s.execute(
                    text("SELECT count(*) FROM model_versions WHERE project_id = :p"),
                    {"p": str(project)},
                ).scalar_one()

        leak_surfaces: list[tuple[str, str]] = []

        def note(label: str, job_id: uuid.UUID, outcome: Any) -> None:
            row = job_row(job_id)
            leak_surfaces.append((f"{label}: failure_code", repr(row.failure_code)))
            leak_surfaces.append((f"{label}: audit", repr([tuple(a) for a in audit_rows(job_id)])))
            leak_surfaces.append((f"{label}: outcome", repr(outcome)))

        # success: the ModelVersion is endpoint-only
        before = n_versions()
        job_id, token, outcome = run()
        row = job_row(job_id)
        check(
            CID,
            f"A1: the happy path succeeds (got {outcome})",
            outcome is not None and outcome.state == "succeeded" and outcome.failure_code is None,
        )
        check(
            CID,
            "A1: the job is succeeded at epoch 1, lease cleared, result recorded",
            (row.state, row.claim_epoch, row.lease_expires_at) == ("succeeded", 1, None)
            and row.result_model_version_id == outcome.model_version_id,
        )
        with Session(owner_engine) as s:
            mv = s.execute(
                text(
                    "SELECT import_kind, origin, external_provider_model_ref, endpoint_reference, observed_deployment_fingerprint, lineage_completeness, weights_digest, trainable_artifact_id, base_model_revision, tokenizer_revision, chat_template_sha256, serving_config_sha256, adapter_only, created_by, version_number FROM model_versions WHERE id = :id"
                ),
                {"id": str(outcome.model_version_id)},
            ).one()
        check(
            CID,
            "A1: the ModelVersion is endpoint-only: verbatim ref, NULL endpoint_reference, the attested fingerprint",
            (
                mv.import_kind,
                mv.origin,
                mv.external_provider_model_ref,
                mv.endpoint_reference,
                mv.observed_deployment_fingerprint,
            )
            == ("endpoint", "external_endpoint", REF, None, FINGERPRINT),
        )
        check(
            CID,
            "A1: ... lineage unknown and no hash, artifact, revision, tokenizer, template or serving config",
            (
                mv.lineage_completeness,
                mv.weights_digest,
                mv.trainable_artifact_id,
                mv.base_model_revision,
                mv.tokenizer_revision,
                mv.chat_template_sha256,
                mv.serving_config_sha256,
                mv.adapter_only,
            )
            == ("unknown", None, None, None, None, None, None, False),
        )
        check(CID, "A1: ... created_by is the requester, not the worker", mv.created_by == "ml-1")
        check(CID, "A1: exactly one ModelVersion was added", n_versions() == before + 1)
        audits = audit_rows(job_id)
        check(
            CID,
            "A1: exactly one audit row, by the worker, with the job id, origin and outcome only",
            len(audits) == 1
            and audits[0].actor_type == "system"
            and audits[0].actor_id == "worker-1"
            and json.loads(audits[0].details)
            == {"job_id": str(job_id), "origin": "external_endpoint", "outcome": "succeeded"},
        )
        r = _request(base, "GET", f"{imports(org1_id, project_a)}/{job_id}", principal="ml1")
        check(
            CID,
            "A2: the job GET shows succeeded with the result id",
            r.status == 200
            and r.json()["state"] == "succeeded"
            and r.json()["result_model_version_id"] == str(outcome.model_version_id),
        )
        r = _request(
            base,
            "GET",
            f"{versions(org1_id, project_a)}/{outcome.model_version_id}",
            principal="ml1",
        )
        check(
            CID,
            f"A2: FTF-1001's GET shows the ModelVersion with a null endpoint_reference (got {r.status})",
            r.status == 200
            and r.json()["endpoint_reference"] is None
            and r.json()["import_kind"] == "endpoint",
        )
        r = _request(
            base,
            "GET",
            f"{versions(org2_id, project_g)}/{outcome.model_version_id}",
            principal="admin2",
        )
        check(CID, "A2: another tenant cannot read it (404)", r.status == 404)
        check(
            CID,
            "A2: a succeeded job cannot be claimed again",
            activity_error(
                lambda: activity.claim_model_import_job(
                    session_factory,
                    org1_id,
                    project_a,
                    job_id,
                    origins=("external_endpoint",),
                    worker_id="w",
                    lease_seconds=60,
                    now=datetime.now(UTC),
                )
            )
            == "job_terminal",
        )
        note("success", job_id, outcome)

        # outage / refusal table over the real gateway
        scenarios: list[tuple[str, dict[str, Any], bool, tuple[str, str]]] = [
            ("gateway rejects the credential (401)", {}, True, ("failed", "gateway_access_denied")),
            (
                "gateway 500 on /v1/models",
                {"models_status": 500},
                False,
                ("blocked_external", "gateway_unavailable"),
            ),
            (
                "ref not listed",
                {"models": ["someone-else"]},
                False,
                ("failed", "alias_not_authorized"),
            ),
            (
                "ref listed only as a Unicode variant",
                {"models": [REF.upper()]},
                False,
                ("failed", "alias_not_authorized"),
            ),
            (
                "chat 500",
                {"chat_status": 500},
                False,
                ("blocked_external", "route_attestation_unavailable"),
            ),
            (
                "deployment header missing",
                {"fingerprint": None},
                False,
                ("failed", "route_identity_unverified"),
            ),
        ]
        for label, config, bad_key, (want_state, want_code) in scenarios:
            GATEWAY.models, GATEWAY.models_status, GATEWAY.chat_status, GATEWAY.fingerprint = (
                [REF],
                200,
                200,
                FINGERPRINT,
            )
            for key, value in config.items():
                setattr(GATEWAY, key, value)
            versions_before = n_versions()
            _SECRET_VALUE_BOX[0] = "sk-rejected-by-the-gateway" if bad_key else _SECRET_VALUE
            try:
                job_id, token, outcome = run()
            finally:
                _SECRET_VALUE_BOX[0] = _SECRET_VALUE
            row = job_row(job_id)
            check(
                CID,
                f"A3: [{label}] -> {want_state}/{want_code} (got {outcome and (outcome.state, outcome.failure_code)})",
                outcome is not None
                and (outcome.state, outcome.failure_code) == (want_state, want_code)
                and (row.state, row.failure_code) == (want_state, want_code),
            )
            check(
                CID,
                f"A3: [{label}] wrote no ModelVersion, never succeeded, lease cleared, one audit row",
                n_versions() == versions_before
                and row.state != "succeeded"
                and row.lease_expires_at is None
                and row.result_model_version_id is None
                and len(audit_rows(job_id)) == 1,
            )
            note(label, job_id, outcome)
        GATEWAY.models, GATEWAY.models_status, GATEWAY.chat_status, GATEWAY.fingerprint = (
            [REF],
            200,
            200,
            FINGERPRINT,
        )

        # gateway down: a connection to a closed port
        closed_port = _free_port()
        with Session(owner_engine) as s:
            down = LiteLLMConnection(
                organization_id=org1_id,
                name=f"gw-down-{suffix}",
                base_url=f"https://127.0.0.1:{closed_port}",
                secret_ref=_SECRET_REF,
            )
            s.add(down)
            s.commit()
            down_id = down.id
        r = _request(
            base,
            "POST",
            imports(org1_id, project_a),
            principal="ml1",
            payload={
                "requested_origin": "external_endpoint",
                "external_provider_model_ref": REF,
                "litellm_connection_id": str(down_id),
            },
        )
        down_job = uuid.UUID(r.json()["id"])
        down_token = activity.claim_model_import_job(
            session_factory,
            org1_id,
            project_a,
            down_job,
            origins=("external_endpoint",),
            worker_id="worker-1",
            lease_seconds=600,
            now=datetime.now(UTC),
        )
        down_policy = _policy_factory(closed_port)
        down_outcome = activity.validate_endpoint_import(
            session_factory,
            down_token,
            secret_config=_SECRET_CONFIG,
            now=datetime.now(UTC),
            policy_factory=down_policy,
            timeout=5.0,
        )
        check(
            CID,
            f"A4: a refused connection is blocked_external/gateway_unavailable (got {down_outcome})",
            (down_outcome.state, down_outcome.failure_code)
            == ("blocked_external", "gateway_unavailable"),
        )
        note("gateway down", down_job, down_outcome)

        # revoked connection
        r = _request(
            base,
            "POST",
            imports(org1_id, project_a),
            principal="ml1",
            payload={
                "requested_origin": "external_endpoint",
                "external_provider_model_ref": REF,
                "litellm_connection_id": str(conn2_id),
            },
        )
        rev_job = uuid.UUID(r.json()["id"])
        rev_token = activity.claim_model_import_job(
            session_factory,
            org1_id,
            project_a,
            rev_job,
            origins=("external_endpoint",),
            worker_id="worker-1",
            lease_seconds=600,
            now=datetime.now(UTC),
        )
        owner_sql(
            "UPDATE litellm_connections SET status = 'revoked', revoked_at = now() WHERE id = :id",
            {"id": str(conn2_id)},
        )
        calls_before = len(GATEWAY.requests)
        rev_outcome = activity.validate_endpoint_import(
            session_factory,
            rev_token,
            secret_config=_SECRET_CONFIG,
            now=datetime.now(UTC),
            policy_factory=policy,
            timeout=5.0,
        )
        check(
            CID,
            f"A5: a revoked connection is failed/connection_revoked (got {rev_outcome})",
            (rev_outcome.state, rev_outcome.failure_code) == ("failed", "connection_revoked"),
        )
        check(CID, "A5: ... with no gateway call", len(GATEWAY.requests) == calls_before)
        note("revoked", rev_job, rev_outcome)
        owner_sql(
            "UPDATE litellm_connections SET status = 'active', revoked_at = NULL WHERE id = :id",
            {"id": str(conn2_id)},
        )

        # legacy row without a connection
        check(
            CID,
            "A6: a legacy external_endpoint job without a connection is job_invalid, never claimed",
            activity_error(
                lambda: activity.claim_model_import_job(
                    session_factory,
                    org1_id,
                    project_a,
                    legacy_job,
                    origins=("external_endpoint",),
                    worker_id="w",
                    lease_seconds=60,
                    now=datetime.now(UTC),
                )
            )
            == "job_invalid"
            and job_row(legacy_job).state == "queued",
        )

        # fencing: two threads claim the same job -> exactly one wins
        for round_ in range(6):
            r = _request(
                base,
                "POST",
                imports(org1_id, project_a),
                principal="ml1",
                payload={
                    "requested_origin": "external_endpoint",
                    "external_provider_model_ref": REF,
                    "litellm_connection_id": str(conn1_id),
                },
            )
            race_job = uuid.UUID(r.json()["id"])
            barrier = threading.Barrier(2)

            def claimer(
                worker: str, race_job: uuid.UUID = race_job, barrier: threading.Barrier = barrier
            ) -> Any:
                barrier.wait()
                try:
                    return activity.claim_model_import_job(
                        session_factory,
                        org1_id,
                        project_a,
                        race_job,
                        origins=("external_endpoint",),
                        worker_id=worker,
                        lease_seconds=600,
                        now=datetime.now(UTC),
                    )
                except activity.ModelImportActivityError as exc:
                    return exc.code

            with ThreadPoolExecutor(max_workers=2) as pool:
                results = list(pool.map(claimer, ("worker-a", "worker-b")))
            winners = [x for x in results if not isinstance(x, str)]
            losers = [x for x in results if isinstance(x, str)]
            check(
                CID,
                f"A7 round {round_}: exactly one of two concurrent claims wins (got {results})",
                len(winners) == 1 and losers == ["job_already_claimed"],
            )
            check(
                CID,
                f"A7 round {round_}: the row is validating at epoch 1",
                (job_row(race_job).state, job_row(race_job).claim_epoch) == ("validating", 1),
            )

        # re-claim after expiry takes epoch + 1; before expiry refused; a stale token writes nothing
        r = _request(
            base,
            "POST",
            imports(org1_id, project_a),
            principal="ml1",
            payload={
                "requested_origin": "external_endpoint",
                "external_provider_model_ref": REF,
                "litellm_connection_id": str(conn1_id),
            },
        )
        fence_job = uuid.UUID(r.json()["id"])
        long_ago = datetime.now(UTC) - timedelta(hours=2)
        t1 = activity.claim_model_import_job(
            session_factory,
            org1_id,
            project_a,
            fence_job,
            origins=("external_endpoint",),
            worker_id="worker-old",
            lease_seconds=60,
            now=long_ago,
        )
        check(
            CID,
            "A8: the first claim is epoch 1 and its lease is already in the past",
            t1.epoch == 1 and job_row(fence_job).lease_expires_at < datetime.now(UTC),
        )
        t2 = activity.claim_model_import_job(
            session_factory,
            org1_id,
            project_a,
            fence_job,
            origins=("external_endpoint",),
            worker_id="worker-new",
            lease_seconds=600,
            now=datetime.now(UTC),
        )
        check(
            CID,
            "A8: a re-claim after expiry gets epoch + 1",
            t2.epoch == 2 and job_row(fence_job).claim_epoch == 2,
        )
        check(
            CID,
            "A8: a re-claim before expiry is refused",
            activity_error(
                lambda: activity.claim_model_import_job(
                    session_factory,
                    org1_id,
                    project_a,
                    fence_job,
                    origins=("external_endpoint",),
                    worker_id="worker-third",
                    lease_seconds=600,
                    now=datetime.now(UTC),
                )
            )
            == "job_already_claimed"
            and job_row(fence_job).claim_epoch == 2,
        )
        versions_before, audits_before_n = n_versions(), len(audit_rows(fence_job))
        stale = activity_error(
            lambda: activity.validate_endpoint_import(
                session_factory,
                t1,
                secret_config=_SECRET_CONFIG,
                now=datetime.now(UTC),
                policy_factory=policy,
                timeout=5.0,
            )
        )
        check(
            CID,
            f"A8: the old epoch's validation is fenced_out (got {stale})",
            stale == "fenced_out",
        )
        check(
            CID,
            "A8: ... and wrote no ModelVersion, no audit row, and left the job as it was",
            n_versions() == versions_before
            and len(audit_rows(fence_job)) == audits_before_n
            and (job_row(fence_job).state, job_row(fence_job).claim_epoch) == ("validating", 2),
        )
        # taken over while the gateway call is in flight
        takeover_done = []

        def takeover(request: str) -> None:
            if not takeover_done and request.startswith("POST"):
                takeover_done.append(request)
                with Session(owner_engine) as s:
                    s.execute(text("SET session_replication_role = replica"))
                    s.execute(
                        text(
                            "UPDATE model_import_jobs SET claim_epoch = claim_epoch + 1, lease_expires_at = now() + interval '3 hours' WHERE id = :id"
                        ),
                        {"id": str(fence_job)},
                    )
                    s.commit()

        GATEWAY.on_request = takeover
        versions_before = n_versions()
        mid = activity_error(
            lambda: activity.validate_endpoint_import(
                session_factory,
                t2,
                secret_config=_SECRET_CONFIG,
                now=datetime.now(UTC),
                policy_factory=policy,
                timeout=10.0,
            )
        )
        GATEWAY.on_request = None
        check(
            CID,
            f"A9: a job taken over while the gateway call is in flight is fenced_out (got {mid})",
            mid == "fenced_out" and takeover_done,
        )
        check(
            CID,
            "A9: ... with no ModelVersion, no audit row, the job validating at the new epoch",
            n_versions() == versions_before
            and len(audit_rows(fence_job)) == audits_before_n
            and (job_row(fence_job).state, job_row(fence_job).claim_epoch) == ("validating", 3),
        )

        check(
            CID,
            f"L1: no database session was in a transaction and no job lock was held during any gateway call (findings: {lock_findings[:3]})",
            not lock_findings,
        )
        check(CID, "L1: the probe really ran during the gateway calls", len(GATEWAY.requests) >= 10)

        # -- X: META-LEAK --------------------------------------------------------------------------------
        root.removeHandler(logs)
        surfaces = leak_surfaces + [("logs", "\n".join(logs.lines))]
        for surface, text_ in surfaces:
            for marker in MARKERS:
                check(CID, f"X1: {marker} is absent from [{surface}]", marker not in text_)
        errors: list[BaseException] = []
        for fn in (
            lambda: activity.claim_model_import_job(
                session_factory,
                org1_id,
                project_a,
                fence_job,
                origins=("external_endpoint",),
                worker_id="w",
                lease_seconds=60,
                now=datetime.now(UTC),
            ),
            lambda: activity.validate_endpoint_import(
                session_factory,
                t1,
                secret_config=_SECRET_CONFIG,
                now=datetime.now(UTC),
                policy_factory=policy,
            ),
            lambda: activity.claim_model_import_job(
                session_factory,
                org1_id,
                project_a,
                uuid.uuid4(),
                origins=("external_endpoint",),
                worker_id="w",
                lease_seconds=60,
                now=datetime.now(UTC),
            ),
        ):
            try:
                fn()
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)
        check(
            CID,
            "X2: three refusals raised activity errors",
            len(errors) == 3
            and all(isinstance(e, activity.ModelImportActivityError) for e in errors),
        )
        for exc in errors:
            rendered = "".join(traceback.format_exception(exc)) + repr(exc.args)
            check(
                CID,
                f"X2: the {exc.code} error chain carries no marker and no cause",
                exc.__cause__ is None and not any(m in rendered for m in MARKERS),
            )

    # ---- M: downgrade refusal with a NULL-reference row, nothing deleted ---------------------------------------------
    with Session(owner_engine) as s:
        n_null = s.execute(
            text(
                "SELECT count(*) FROM model_versions WHERE import_kind = 'endpoint' AND endpoint_reference IS NULL"
            )
        ).scalar_one()
    check(
        CID,
        f"M: NULL-reference endpoint rows exist before the downgrade attempt ({n_null})",
        n_null > 0,
    )
    refused = _alembic(["downgrade", "-1"], migration_url)
    with Session(owner_engine) as s:
        n_after = s.execute(
            text(
                "SELECT count(*) FROM model_versions WHERE import_kind = 'endpoint' AND endpoint_reference IS NULL"
            )
        ).scalar_one()
    current = _alembic(["current"], migration_url)
    check(
        CID,
        "M: a downgrade with NULL-reference endpoint rows is refused",
        refused.returncode != 0 and "cannot downgrade 0031" in (refused.stderr + refused.stdout),
    )
    check(
        CID,
        "M: ... deleted nothing and left the schema at 0031",
        n_after == n_null
        and "0031_model_import_validation" in current.stdout + current.stderr
        and func_source() == src_0029,
    )
    owner_sql(
        "DELETE FROM model_import_jobs WHERE result_model_version_id IS NOT NULL OR state <> 'queued' OR litellm_connection_id IS NOT NULL"
    )
    owner_sql(
        "DELETE FROM model_versions WHERE import_kind = 'endpoint' AND endpoint_reference IS NULL"
    )
    alembic("downgrade -3 after removing the NULL-reference rows", "downgrade", "-3")
    check(
        CID,
        "M: that downgrade restores both definitions byte-identically",
        func_source() == src_0028
        and constraint_def("ck_model_versions_kind_consistency") == kind_0009,
    )
    alembic("final upgrade head", "upgrade", "head")

    if FAILURES:
        print(f"FTF-H010-P2 real import-validation proof: {len(FAILURES)} failed")
        for failure in FAILURES[:60]:
            print(f"  {failure}")
        sys.exit(1)
    print("FTF-H010-P2 real import-validation proof: all checks passed")


def activity_error(fn: Any) -> str | None:
    try:
        fn()
    except activity.ModelImportActivityError as exc:
        return exc.code
    return None


if __name__ == "__main__":
    try:
        main()
    except BaseException:
        # A crash must still show every failed check collected so far.
        for failure in FAILURES[:60]:
            print(f"  {failure}", file=sys.stderr)
        raise
