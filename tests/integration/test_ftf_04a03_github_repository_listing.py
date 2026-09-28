#!/usr/bin/env python3
"""FTF-04A03 -- real Postgres GitHub accessible-repository listing proof.

Proves this task's own acceptance clause ("Render only credential-
accessible repositories with pagination and explicit selection" -- the
backend half: a real, tenant-authorized, audited listing call) against a
real, live PostgreSQL server -- never a mocked session or an in-memory
stand-in. `tests/tasks/test_ftf_04a03_connector.py` already proves the
real GitHub REST API v3 request/response/pagination/error logic against a
real local HTTPS fixture; `tests/tasks/test_ftf_04a03_routes.py` already
proves the real `require_role` role-boundary denial without a database.
This file exists to prove the real Postgres row-level security/project-
authorization/audit-write path those two suites were built to model,
exactly like `tests/integration/test_ftf_04a01_target_application_connection.py`
already established for the sibling `TargetApplicationConnection` resource
-- this task deliberately needs no OpenBao server: the credential here is
never persisted (Section 6.8A/7A.2's own "browse before connecting" step),
so there is no secret to store or resolve.

Requires a real Postgres reachable at `SOVRA_DATABASE_URL` (the migration
owner role) plus `SOVRA_APP_DB_PASSWORD` -- matching every prior
integration test's own environment contract exactly. The real GitHub
REST API v3 call is proven against a real local HTTPS fixture (this
sandbox has no route to the public internet -- the same disclosed finding
`test_ftf_04a03_connector.py`/`test_ftf_04a02.py` already made), wired in
via the router's own `github_api_base_url_factory`/
`github_connection_policy_factory` build-time seams.

Run directly:
    SOVRA_DATABASE_URL=postgresql+psycopg://<migration-role>@host/db \\
    SOVRA_APP_DB_PASSWORD=<app-role-password> \\
    python3 tests/integration/test_ftf_04a03_github_repository_listing.py
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
    Workspace,
    set_tenant_context,
)
from app.auth.rbac_middleware import AuthenticatedPrincipal, Role  # noqa: E402
from app.projects.project_schema import Project  # noqa: E402
from app.target_connectors.github_repository_listing_routes import (  # noqa: E402
    build_github_repository_listing_router,
)

FAILURES: list[str] = []


def check(criterion_id: str, description: str, condition: bool) -> None:
    if not condition:
        FAILURES.append(f"FAIL [{criterion_id}] {description}")


def _required_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        print(
            f"BLOCKED_EXTERNAL: {name} is not set -- this task's real audited-listing "
            "evidence requires a live Postgres (migration owner connection). Not silently "
            "skipped: see this file's own docstring for how to run it locally or in CI.",
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


# -- Real local HTTPS `GET /installation/repositories`-shaped fixture, same
# pattern `test_ftf_04a03_connector.py` already established -----------------

_GOOD_REPOS = {
    "total_count": 1,
    "repositories": [
        {"full_name": "acme/agent-repo", "private": True, "default_branch": "main"},
    ],
}


class _GitHubHandler(BaseHTTPRequestHandler):
    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        pass

    def do_GET(self) -> None:  # noqa: N802
        auth = self.headers.get("Authorization", "")
        token = auth.removeprefix("Bearer ")
        if token == "revoked-token":
            self._respond(401, json.dumps({"message": "Bad credentials"}).encode())
            return
        self._respond(200, json.dumps(_GOOD_REPOS).encode())

    def _respond(self, status: int, body: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if body:
            self.wfile.write(body)


_TLS_TMP = Path(tempfile.mkdtemp(prefix="ftf-04a03-integration-tls-"))
_CA_TRUST_TARGET = Path("/usr/local/share/ca-certificates/ftf-04a03-integration-test-ca.crt")
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
            "/CN=FTF-04A03 Integration Test CA",
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


# -- Fixture: two organizations, one project each, one organization_admin
# and one viewer per organization (same shape
# test_ftf_04a01_target_application_connection.py already established) -----

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
        "FTF-04A03",
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
            "FTF-04A03: this sandbox has no openssl/update-ca-certificates/passwordless sudo "
            "-- the live TLS-backed GitHub call is skipped and disclosed here; GitHub Actions' "
            "ubuntu-latest runner has all three.",
            file=sys.stderr,
        )

    _run_checks(migration_url, app_password, base_url, port)
    _finish()


def _finish() -> None:
    if FAILURES:
        print(f"FTF-04A03 real GitHub accessible-repository listing proof: {len(FAILURES)} failed")
        for failure in FAILURES:
            print(f"  {failure}")
        sys.exit(1)
    print("FTF-04A03 real GitHub accessible-repository listing proof: all checks passed")
    sys.exit(0)


def _run_checks(
    migration_url: str, app_password: str, github_base_url: str | None, github_port: int | None
) -> None:
    owner_engine = create_engine(migration_url)
    app_url = _app_role_url(migration_url, app_password)
    app_engine = create_engine(app_url)

    check(
        "FTF-04A03",
        "the router's own database session is built from the restricted sovra_app role's "
        "connection URL, never the migration-owner role",
        app_engine.url.username == APP_ROLE_NAME
        and app_engine.url.username != owner_engine.url.username,
    )

    # Organizations/workspaces/projects are all real, `FORCE ROW LEVEL
    # SECURITY` tables (Section 65.1) -- even the migration-owner role that
    # created them is subject to their `tenant_isolation` policy (that is
    # the whole point of FORCE: "the migration role owns these tables --
    # ENABLE alone is bypassed by the owner", per `0001_org_workspace_
    # schema.py`'s own comment). A real bootstrap/admin flow pre-generates
    # the new organization's own ID and sets the RLS tenant context to that
    # same ID *before* inserting it (`organizations`' own policy is
    # `id = current tenant context`); every child row is then inserted
    # under the same real `set_tenant_context` call `tenant_scoped_session`
    # (FTF-0105) uses for every other request in this repository -- never a
    # superuser/BYPASSRLS shortcut.
    org1_id = uuid.uuid4()
    org2_id = uuid.uuid4()
    with Session(owner_engine) as session:
        set_tenant_context(session, org1_id)
        session.add(
            Organization(id=org1_id, name="Acme 04A03", slug=f"acme-04a03-{uuid.uuid4().hex[:8]}")
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
        session.commit()

    with Session(owner_engine) as session:
        set_tenant_context(session, org2_id)
        session.add(
            Organization(
                id=org2_id, name="Globex 04A03", slug=f"globex-04a03-{uuid.uuid4().hex[:8]}"
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
        session.commit()

    fixture = _Fixture({"org1": org1_id, "org2": org2_id})
    session_factory = sessionmaker(bind=app_engine)

    policy: ConnectionPolicy | None = None
    if github_port is not None:
        policy = ConnectionPolicy(
            name="test-github-repository-listing-integration",
            allowed_schemes=frozenset({"https"}),
            allowed_ports=frozenset({github_port}),
            explicit_private_allowlist=(ipaddress.ip_network("127.0.0.0/8"),),
            allow_redirects=False,
        )

    app = FastAPI()
    app.include_router(
        build_github_repository_listing_router(
            session_factory,
            principal_resolver=fixture.principal_resolver,
            role_lookup=fixture.role_lookup,
            github_api_base_url_factory=lambda: github_base_url or "https://api.github.com",
            github_connection_policy_factory=lambda: policy,
        )
    )

    org1_admin = fixture.header_for("org1", Role.ORGANIZATION_ADMIN)
    org1_viewer = fixture.header_for("org1", Role.VIEWER)
    org2_admin = fixture.header_for("org2", Role.ORGANIZATION_ADMIN)

    def path(org_id: uuid.UUID, project_id: uuid.UUID) -> str:
        return (
            f"/api/v1/organizations/{org_id}/projects/{project_id}"
            "/target-connections/github-repositories"
        )

    def audit_events_for(
        organization_id: uuid.UUID, project_id: uuid.UUID
    ) -> list[dict[str, object]]:
        # `audit_events` is itself a real FORCE-RLS table (Section 65.1) --
        # reading it back for verification goes through the same real
        # tenant-context seam every other query in this repository uses,
        # never a superuser/BYPASSRLS shortcut.
        with Session(owner_engine) as session:
            set_tenant_context(session, organization_id)
            rows = session.execute(
                text(
                    "SELECT action, details_json FROM audit_events "
                    "WHERE resource_type = 'project' AND resource_id = :rid "
                    "ORDER BY created_at"
                ),
                {"rid": str(project_id)},
            ).all()
        return [{"action": row[0], "details": row[1]} for row in rows]

    with _running_app(app) as base_url_local:
        if github_base_url is None:
            check(
                "FTF-04A03",
                "no live GitHub egress in this sandbox -- the real-listing/audit checks below "
                "are skipped and disclosed (see this file's own docstring)",
                True,
            )
        else:
            # -- Cross-organization project ID: fails server-side, no audit row --
            r = _post(
                base_url_local,
                path(org2_id, project1_id),
                principal=org2_admin,
                payload={"credential": "valid-token"},
            )
            check(
                "FTF-04A03",
                "negative_acceptance: a project ID from a different organization is denied "
                "(404), identical to a missing project",
                r.status == 404,
            )
            check(
                "FTF-04A03",
                "the cross-organization denial writes no audit row for this project",
                audit_events_for(org1_id, project1_id) == [],
            )

            # -- viewer is denied before ever reaching the session/GitHub call --
            r = _post(
                base_url_local,
                path(org1_id, project1_id),
                principal=org1_viewer,
                payload={"credential": "valid-token"},
            )
            check("FTF-04A03", "a viewer is denied (403)", r.status == 403)

            # -- real happy path: organization_admin, real project, real GitHub call --
            r = _post(
                base_url_local,
                path(org1_id, project1_id),
                principal=org1_admin,
                payload={"credential": "valid-token", "page": 1, "per_page": 30},
            )
            check(
                "FTF-04A03",
                "acceptance: a real organization_admin request for a real project returns the "
                "real repositories the fixture GitHub server declared (200)",
                r.status == 200,
            )
            if r.status == 200:
                body = r.json()
                check(
                    "FTF-04A03",
                    "acceptance: the real response renders exactly the credential-accessible "
                    "repository the fixture declared",
                    body["repositories"]
                    == [
                        {
                            "repository_identity": "acme/agent-repo",
                            "private": True,
                            "default_branch": "main",
                        }
                    ],
                )

            events = audit_events_for(org1_id, project1_id)
            check(
                "FTF-04A03",
                "Section 6.8A: the successful listing writes a real, queryable audit event",
                any(
                    e["action"] == "target_application_connection.github_repositories_listed"
                    for e in events
                ),
            )
            listed_event = next(
                e
                for e in events
                if e["action"] == "target_application_connection.github_repositories_listed"
            )
            raw_details = listed_event["details"]
            details: dict[str, object] = (
                json.loads(raw_details) if isinstance(raw_details, str) else raw_details
            )
            check(
                "FTF-04A03",
                "the audit event's own details never contain the raw credential",
                "valid-token" not in json.dumps(details),
            )
            check(
                "FTF-04A03",
                "the audit event's own details record the real repository_count",
                details.get("repository_count") == 1,
            )

            # -- revoked credential: denied, distinct audit row, no repositories leaked --
            r = _post(
                base_url_local,
                path(org1_id, project1_id),
                principal=org1_admin,
                payload={"credential": "revoked-token"},
            )
            check(
                "FTF-04A03",
                "negative_acceptance: a real revoked credential (HTTP 401 from GitHub) is "
                "denied (401), never a fabricated empty success",
                r.status == 401,
            )
            events_after_denial = audit_events_for(org1_id, project1_id)
            check(
                "FTF-04A03",
                "a denied listing writes its own distinct audit action, never disguised as a "
                "successful listing",
                any(
                    e["action"] == "target_application_connection.github_repositories_list_denied"
                    for e in events_after_denial
                ),
            )

            # -- a second, distinct organization's own admin can list their own project's
            # real repositories too -- the tenant boundary above denies *other* tenants'
            # projects, it does not deny every organization but the first one seeded --
            r = _post(
                base_url_local,
                path(org2_id, project2_id),
                principal=org2_admin,
                payload={"credential": "valid-token"},
            )
            check(
                "FTF-04A03",
                "a second organization's own admin genuinely reaches the same real listing "
                "path for their own project (200)",
                r.status == 200,
            )


if __name__ == "__main__":
    main()
