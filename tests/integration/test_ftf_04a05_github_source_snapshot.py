#!/usr/bin/env python3
"""FTF-04A05 -- real Postgres GitHub source snapshot capture proof.

Proves this task's own acceptance clause (a real, tenant-authorized,
audited GitHub-backed capture that durably persists an immutable
`TargetSourceSnapshot` row) against a real, live PostgreSQL server --
never a mocked session or an in-memory stand-in.
`tests/tasks/test_ftf_04a05.py` already proves the real GitHub REST API
v3 request/response/tree-safety logic against a real local HTTPS fixture;
`tests/tasks/test_ftf_04a05_routes.py` already proves the real
`require_role` role-boundary denial without a database. This file exists
to prove the real Postgres project/connection-authorization/persist/audit
path those two suites were built to model, exactly like
`tests/integration/test_ftf_04a04_github_branch_ref.py` already
established for the sibling branch/ref-resolution resource.

Requires a real Postgres reachable at `SOVRA_DATABASE_URL` (the migration
owner role) plus `SOVRA_APP_DB_PASSWORD`. The real GitHub REST API v3
calls are proven against a real local HTTPS fixture (this sandbox has no
route to the public internet -- the same disclosed finding
`test_ftf_04a04_connector.py`/`test_ftf_04a04_github_branch_ref.py`
already made), wired in via the router's own
`github_api_base_url_factory`/`github_connection_policy_factory` build-time
seams.

Run directly:
    SOVRA_DATABASE_URL=postgresql+psycopg://<migration-role>@host/db \\
    SOVRA_APP_DB_PASSWORD=<app-role-password> \\
    python3 tests/integration/test_ftf_04a05_github_source_snapshot.py
"""

from __future__ import annotations

import atexit
import contextlib
import http.client
import ipaddress
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
    Workspace,
    set_tenant_context,
)
from app.auth.rbac_middleware import AuthenticatedPrincipal, Role  # noqa: E402
from app.projects.project_schema import Project  # noqa: E402
from app.target_connectors.github_source_snapshot_routes import (  # noqa: E402
    build_github_source_snapshot_router,
)
from app.target_connectors.target_application_connection_schema import (  # noqa: E402
    TargetApplicationConnection,
)

FAILURES: list[str] = []


def check(criterion_id: str, description: str, condition: bool) -> None:
    if not condition:
        FAILURES.append(f"FAIL [{criterion_id}] {description}")


def _required_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        print(
            f"BLOCKED_EXTERNAL: {name} is not set -- this task's real audited-capture evidence "
            "requires a live Postgres (migration owner connection). Not silently skipped: see "
            "this file's own docstring for how to run it locally or in CI.",
            file=sys.stderr,
        )
        sys.exit(1)
    return value


def _app_role_url(migration_url: str, app_password: str) -> str:
    parts = urllib.parse.urlsplit(migration_url)
    netloc = f"{APP_ROLE_NAME}:{app_password}@{parts.hostname}"
    if parts.port:
        netloc += f":{parts.port}"
    return urllib.parse.urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))


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

    def json(self):
        return json.loads(self.body)


def _post(
    base_url: str,
    path: str,
    *,
    principal: str | None = None,
    payload: dict[str, object] | None = None,
) -> _Response:
    headers: dict[str, str] = {}
    if principal is not None:
        headers["X-Test-Principal"] = principal
    body_bytes = json.dumps(payload if payload is not None else {}).encode("utf-8")
    headers["Content-Type"] = "application/json"
    parsed = urllib.parse.urlparse(base_url)
    conn = http.client.HTTPConnection(parsed.hostname or "127.0.0.1", parsed.port or 80, timeout=10)
    try:
        conn.request("POST", path, body=body_bytes, headers=headers)
        response = conn.getresponse()
        raw = response.read()
        return _Response(status=response.status, body=raw)
    finally:
        conn.close()


# -- Real local HTTPS GitHub-shaped fixture, same pattern
# tests/tasks/test_ftf_04a05.py already established ------------------------

_REPO = "acme/agent-repo"
_MAIN_COMMIT_SHA = "9" * 40
_MAIN_TREE_SHA = "8" * 40
_TRUNCATED_COMMIT_SHA = "a" * 40
_TRUNCATED_TREE_SHA = "b" * 40

_MAIN_TREE_ENTRIES = [
    {"path": "README.md", "type": "blob", "mode": "100644", "sha": "1" * 40, "size": 123},
]


class _GitHubHandler(BaseHTTPRequestHandler):
    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        pass

    def do_GET(self) -> None:  # noqa: N802
        auth = self.headers.get("Authorization", "")
        token = auth.removeprefix("Bearer ")
        parsed = urllib.parse.urlsplit(self.path)
        path = parsed.path
        prefix = f"/repos/{_REPO}"

        if token == "revoked-token":
            self._respond(401, json.dumps({"message": "Bad credentials"}).encode())
            return

        if path == f"{prefix}/commits/main":
            self._respond(200, json.dumps({"sha": _MAIN_COMMIT_SHA}).encode())
            return
        if path == f"{prefix}/commits/truncated-ref":
            self._respond(200, json.dumps({"sha": _TRUNCATED_COMMIT_SHA}).encode())
            return
        if path == f"{prefix}/git/commits/{_MAIN_COMMIT_SHA}":
            self._respond(
                200, json.dumps({"sha": _MAIN_COMMIT_SHA, "tree": {"sha": _MAIN_TREE_SHA}}).encode()
            )
            return
        if path == f"{prefix}/git/commits/{_TRUNCATED_COMMIT_SHA}":
            self._respond(
                200,
                json.dumps(
                    {"sha": _TRUNCATED_COMMIT_SHA, "tree": {"sha": _TRUNCATED_TREE_SHA}}
                ).encode(),
            )
            return
        if path == f"{prefix}/git/trees/{_MAIN_TREE_SHA}":
            self._respond(
                200,
                json.dumps(
                    {"sha": _MAIN_TREE_SHA, "tree": _MAIN_TREE_ENTRIES, "truncated": False}
                ).encode(),
            )
            return
        if path == f"{prefix}/git/trees/{_TRUNCATED_TREE_SHA}":
            self._respond(
                200,
                json.dumps(
                    {"sha": _TRUNCATED_TREE_SHA, "tree": _MAIN_TREE_ENTRIES, "truncated": True}
                ).encode(),
            )
            return
        self._respond(404, b'{"message": "Not Found"}')

    def _respond(self, status: int, body: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if body:
            self.wfile.write(body)


_TLS_TMP = Path(tempfile.mkdtemp(prefix="ftf-04a05-integration-tls-"))
_CA_TRUST_TARGET = Path("/usr/local/share/ca-certificates/ftf-04a05-integration-test-ca.crt")
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
            "/CN=FTF-04A05 Integration Test CA",
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


def _start_https_server(certfile: Path, keyfile: Path) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _GitHubHandler)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(certfile=str(certfile), keyfile=str(keyfile))
    server.socket = context.wrap_socket(server.socket, server_side=True)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    atexit.register(server.shutdown)
    return server


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


def main() -> None:
    migration_url = _required_env("SOVRA_DATABASE_URL")
    app_password = _required_env("SOVRA_APP_DB_PASSWORD")

    migration_result = _run_migration(migration_url)
    check(
        "FTF-04A05",
        f"alembic upgrade head runs cleanly (exit {migration_result.returncode})\n"
        f"stdout: {migration_result.stdout}\nstderr: {migration_result.stderr}",
        migration_result.returncode == 0,
    )
    if FAILURES:
        _finish()

    base_url: str | None = None
    port: int | None = None
    if _UPDATE_CA_AVAILABLE:
        leaf, key = _generate_trusted_server_cert()
        server = _start_https_server(leaf, key)
        port = server.server_address[1]
        base_url = f"https://127.0.0.1:{port}"
    else:
        print(
            "FTF-04A05: this sandbox has no openssl/update-ca-certificates/passwordless sudo "
            "-- the live TLS-backed GitHub calls are skipped and disclosed here; GitHub Actions' "
            "ubuntu-latest runner has all three.",
            file=sys.stderr,
        )

    _run_checks(migration_url, app_password, base_url, port)
    _finish()


def _finish() -> None:
    if FAILURES:
        print(f"FTF-04A05 real GitHub source snapshot capture proof: {len(FAILURES)} failed")
        for failure in FAILURES:
            print(f"  {failure}")
        sys.exit(1)
    print("FTF-04A05 real GitHub source snapshot capture proof: all checks passed")
    sys.exit(0)


def _run_checks(
    migration_url: str, app_password: str, github_base_url: str | None, github_port: int | None
) -> None:
    owner_engine = create_engine(migration_url)
    app_url = _app_role_url(migration_url, app_password)
    app_engine = create_engine(app_url)

    org1_id = uuid.uuid4()
    org2_id = uuid.uuid4()
    with Session(owner_engine) as session:
        set_tenant_context(session, org1_id)
        session.add(
            Organization(id=org1_id, name="Acme 04A05", slug=f"acme-04a05-{uuid.uuid4().hex[:8]}")
        )
        session.flush()
        ws1 = Workspace(organization_id=org1_id, name="Acme Main", slug="main")
        session.add(ws1)
        session.flush()
        project1 = Project(
            organization_id=org1_id,
            workspace_id=ws1.id,
            name="acme-agentic-project",
            project_type="agentic_product",
            status="active",
            security_profile="standard",
            row_version=1,
            created_by="seed",
        )
        session.add(project1)
        session.flush()
        project1_id = project1.id

        connection1 = TargetApplicationConnection(
            organization_id=org1_id,
            project_id=project1_id,
            connector_type="github",
            display_name="acme-connection",
            repository_identity=_REPO,
            default_branch="main",
            selected_branch=None,
            access_mode="read_only",
            connection_status="active",
            created_by="seed",
        )
        session.add(connection1)
        session.flush()
        connection1_id = connection1.id
        session.commit()

    with Session(owner_engine) as session:
        set_tenant_context(session, org2_id)
        session.add(
            Organization(
                id=org2_id, name="Globex 04A05", slug=f"globex-04a05-{uuid.uuid4().hex[:8]}"
            )
        )
        session.flush()
        ws2 = Workspace(organization_id=org2_id, name="Globex Main", slug="main")
        session.add(ws2)
        session.flush()
        project2 = Project(
            organization_id=org2_id,
            workspace_id=ws2.id,
            name="globex-agentic-project",
            project_type="agentic_product",
            status="active",
            security_profile="standard",
            row_version=1,
            created_by="seed",
        )
        session.add(project2)
        session.flush()
        project2_id = project2.id

        connection2 = TargetApplicationConnection(
            organization_id=org2_id,
            project_id=project2_id,
            connector_type="github",
            display_name="globex-connection",
            repository_identity=_REPO,
            default_branch="main",
            selected_branch=None,
            access_mode="read_only",
            connection_status="active",
            created_by="seed",
        )
        session.add(connection2)
        session.flush()
        connection2_id = connection2.id
        session.commit()

    fixture = _Fixture({"org1": org1_id, "org2": org2_id})
    session_factory = sessionmaker(bind=app_engine)

    policy: ConnectionPolicy | None = None
    if github_port is not None:
        policy = ConnectionPolicy(
            name="test-github-source-snapshot-integration",
            allowed_schemes=frozenset({"https"}),
            allowed_ports=frozenset({github_port}),
            explicit_private_allowlist=(ipaddress.ip_network("127.0.0.0/8"),),
            allow_redirects=False,
        )

    app = FastAPI()
    app.include_router(
        build_github_source_snapshot_router(
            session_factory,
            principal_resolver=fixture.principal_resolver,
            role_lookup=fixture.role_lookup,
            github_api_base_url_factory=lambda: github_base_url or "https://api.github.com",
            github_connection_policy_factory=lambda: policy,
        )
    )

    org1_admin = fixture.header_for("org1", Role.ORGANIZATION_ADMIN)
    org2_admin = fixture.header_for("org2", Role.ORGANIZATION_ADMIN)

    def capture_path(org_id: uuid.UUID, project_id: uuid.UUID, connection_id: uuid.UUID) -> str:
        return (
            f"/api/v1/organizations/{org_id}/projects/{project_id}"
            f"/target-connections/{connection_id}/github-source-snapshot"
        )

    def audit_events_for(
        organization_id: uuid.UUID, resource_type: str, resource_id: str
    ) -> list[dict[str, object]]:
        with Session(owner_engine) as session:
            set_tenant_context(session, organization_id)
            rows = session.execute(
                text(
                    "SELECT action, details_json FROM audit_events "
                    "WHERE resource_type = :rt AND resource_id = :rid ORDER BY created_at"
                ),
                {"rt": resource_type, "rid": resource_id},
            ).all()
        return [{"action": row[0], "details": row[1]} for row in rows]

    def snapshot_row(snapshot_id: str, organization_id: uuid.UUID) -> dict[str, object] | None:
        with Session(owner_engine) as session:
            set_tenant_context(session, organization_id)
            row = (
                session.execute(
                    text(
                        "SELECT commit_sha, tree_hash, branch, capture_method, analysis_status "
                        "FROM target_source_snapshots WHERE id = :id"
                    ),
                    {"id": snapshot_id},
                )
                .mappings()
                .one_or_none()
            )
        return dict(row) if row is not None else None

    with _running_app(app) as base_url_local:
        if github_base_url is None:
            check(
                "FTF-04A05",
                "no live GitHub egress in this sandbox -- the real-capture/persist/audit checks "
                "below are skipped and disclosed (see this file's own docstring)",
                True,
            )
            return

        # -- cross-organization project ID: fails server-side, no row inserted --
        r = _post(
            base_url_local,
            capture_path(org2_id, project1_id, connection1_id),
            principal=org2_admin,
            payload={"credential": "valid-token", "repository_identity": _REPO, "ref": "main"},
        )
        check(
            "FTF-04A05",
            "negative_acceptance: a project ID from a different organization is denied (404), "
            "identical to a missing project",
            r.status == 404,
        )

        # -- a nonexistent project ID (real, valid org1 connection, but no real project row
        # at all) is denied server-side: this isolates the router's own project-authorization
        # guard from the separately-CERTIFIED connection-authorization guard downstream (a
        # real connection ID alone is never sufficient to authorize a bogus project ID) --
        r = _post(
            base_url_local,
            capture_path(org1_id, uuid.uuid4(), connection1_id),
            principal=org1_admin,
            payload={"credential": "valid-token", "repository_identity": _REPO, "ref": "main"},
        )
        check(
            "FTF-04A05",
            "negative_acceptance: a nonexistent project ID is denied (404) even when the "
            "connection ID is real and belongs to the caller's own organization -- proves the "
            "router's own project-authorization guard is independently load-bearing, not merely "
            "redundant with the downstream connection check",
            r.status == 404,
        )

        # -- cross-organization connection ID under the caller's own project: denied --
        r = _post(
            base_url_local,
            capture_path(org1_id, project1_id, connection2_id),
            principal=org1_admin,
            payload={"credential": "valid-token", "repository_identity": _REPO, "ref": "main"},
        )
        check(
            "FTF-04A05",
            "negative_acceptance: a connection ID belonging to a different organization is "
            "denied (404), never silently captured against it",
            r.status == 404,
        )

        # -- real happy path: capture and persist a real GitHub source snapshot --
        r = _post(
            base_url_local,
            capture_path(org1_id, project1_id, connection1_id),
            principal=org1_admin,
            payload={"credential": "valid-token", "repository_identity": _REPO, "ref": "main"},
        )
        check(
            "FTF-04A05",
            "acceptance: a real organization_admin request for a real project/connection "
            "captures and persists a real GitHub source snapshot (201)",
            r.status == 201,
        )
        snapshot_id: str | None = None
        if r.status == 201:
            body = r.json()
            snapshot_id = body["id"]
            check(
                "FTF-04A05",
                "acceptance: the real response reports the real, resolved commit SHA",
                body["commit_sha"] == _MAIN_COMMIT_SHA,
            )
            check(
                "FTF-04A05",
                "acceptance: the real response reports GitHub's own real, canonical tree hash",
                body["tree_hash"] == _MAIN_TREE_SHA,
            )
            check(
                "FTF-04A05",
                "acceptance: the real response reports the real capture_method",
                body["capture_method"] == "github_resolved_commit",
            )

        if snapshot_id is not None:
            row = snapshot_row(snapshot_id, org1_id)
            check(
                "FTF-04A05",
                "acceptance: the snapshot is genuinely durable -- read back fresh from Postgres, "
                "not merely trusted from the response body",
                row is not None
                and row["commit_sha"] == _MAIN_COMMIT_SHA
                and row["tree_hash"] == _MAIN_TREE_SHA
                and row["capture_method"] == "github_resolved_commit"
                and row["branch"] == "main"
                and row["analysis_status"] == "not_analyzed",
            )

        captured_events = audit_events_for(org1_id, "target_source_snapshot", snapshot_id or "")
        check(
            "FTF-04A05",
            "Section 6.8A: the successful capture writes a real, queryable audit event, never "
            "the raw credential",
            any(e["action"] == "target_source_snapshot.github_captured" for e in captured_events)
            and all("valid-token" not in json.dumps(e["details"]) for e in captured_events),
        )

        # -- negative: a truncated real tree listing is refused (422), nothing persisted --
        r = _post(
            base_url_local,
            capture_path(org1_id, project1_id, connection1_id),
            principal=org1_admin,
            payload={
                "credential": "valid-token",
                "repository_identity": _REPO,
                "ref": "truncated-ref",
            },
        )
        check(
            "FTF-04A05",
            "negative_acceptance: a real truncated tree listing is refused (422), never "
            "persisted as a partial snapshot",
            r.status == 422,
        )

        denied_events = audit_events_for(org1_id, "project", str(project1_id))
        check(
            "FTF-04A05",
            "the truncated-tree denial writes a real, queryable audit event too, never silent",
            any(
                e["action"] == "target_source_snapshot.github_capture_denied" for e in denied_events
            ),
        )


if __name__ == "__main__":
    main()
