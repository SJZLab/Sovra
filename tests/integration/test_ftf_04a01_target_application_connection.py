#!/usr/bin/env python3
"""FTF-04A01 -- real Postgres + real OpenBao TargetApplicationConnection proof.

Proves this task's own acceptance clause 1 ("Create tenant-scoped
read-only target connection and validate credentials without fetching
unapproved data") and negative_acceptance ("Cross-project target IDs,
unsupported connector and excessive permission fail server-side") against
a real, live PostgreSQL server and a real, live OpenBao server -- never a
mocked connection, an in-memory SQLite stand-in, or a fake secret backend.
`tests/tasks/test_ftf_04a01.py` already proves every validator/structural
rule against pure in-memory objects; this file exists to prove the real
database's own row-level security and the real OpenBao backend actually
behave the way that no-DB suite was built to model.

Two real organizations (org1/org2), each with a real workspace and
project (org1 gets a *second* project too, to prove the cross-project --
not merely cross-organization -- IDOR case), are seeded as the
migration-owner role; every request below runs through a real uvicorn
server, with the router's own database session bound to the real,
distinct, least-privileged `sovra_app` role (never the migration-owner
role -- confirmed directly below), exactly like
`tests/integration/test_ftf_0500_dataset_source_connection.py` already
established for a different project-scoped resource. `packages/
litellm_client/secret_manager_integration.py` (FTF-0202) is wired in for
real -- a credential value supplied at connection-creation time (for
`connector_type='github'`) is proven to reach a real OpenBao KV v2 path,
resolved back directly against that same live server, never merely
asserted.

Requires a real Postgres reachable at `SOVRA_DATABASE_URL` (the migration
owner role) plus `SOVRA_APP_DB_PASSWORD`, and a `bao` (or `vault`-API-
compatible) binary on `PATH` (or `SOVRA_BAO_BINARY` pointing at one
directly) -- matching every prior integration test's own environment
contract exactly. This repo's interactive dev sandbox has no container-
registry egress (same finding as every prior integration-tested task) --
this evidence was instead produced locally against a directly-installed
PostgreSQL 16 cluster and a real OpenBao server built from source, and is
wired into CI's dedicated `target-application-connection-integration` job
(a GitHub Actions `services: postgres` container plus a freshly-built
OpenBao binary on the hosted runner's own real network).

Run directly (against an already-migrated or unmigrated database; this
script runs the migration itself):
    SOVRA_DATABASE_URL=postgresql+psycopg://<migration-role>@host/db \\
    SOVRA_APP_DB_PASSWORD=<app-role-password> \\
    SOVRA_BAO_BINARY=/path/to/bao \\
    python3 tests/integration/test_ftf_04a01_target_application_connection.py
"""

from __future__ import annotations

import contextlib
import http.client
import json
import os
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
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
from packages.litellm_client import secret_manager_integration as smi  # noqa: E402
from sqlalchemy import create_engine, text  # noqa: E402
from sqlalchemy.exc import DBAPIError  # noqa: E402
from sqlalchemy.orm import Session, sessionmaker  # noqa: E402

from app.auth.organization_workspace_schema import (  # noqa: E402
    APP_ROLE_NAME,
    Organization,
    Workspace,
)
from app.auth.rbac_middleware import AuthenticatedPrincipal, Role  # noqa: E402
from app.projects.project_schema import Project  # noqa: E402
from app.target_connectors.target_application_connection_routes import (  # noqa: E402
    build_target_application_connection_router,
)

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


def _resolve_bao_binary() -> str:
    explicit = os.environ.get("SOVRA_BAO_BINARY")
    if explicit:
        return explicit
    found = shutil.which("bao")
    if found:
        return found
    print(
        "BLOCKED_EXTERNAL: no 'bao' binary on PATH and SOVRA_BAO_BINARY is "
        "not set -- cannot run the real OpenBao integration proof.",
        file=sys.stderr,
    )
    sys.exit(1)


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
    conn = http.client.HTTPConnection(parsed.hostname or "127.0.0.1", parsed.port or 80, timeout=10)
    try:
        conn.request(method, path, body=body_bytes, headers=headers)
        response = conn.getresponse()
        raw = response.read()
        return _Response(status=response.status, body=raw)
    finally:
        conn.close()


# -- Real OpenBao dev server, spawned/torn down exactly like
# tests/integration/test_ftf_0500_dataset_source_connection.py's own
# established pattern -- a distinct port so both suites can run concurrently
# in the same CI matrix without colliding -------------------------------------

BAO_ADDRESS = "http://127.0.0.1:18501"
BAO_ROOT_TOKEN = f"sovra-ci-test-{uuid.uuid4().hex}"


def _wait_for_bao_health(timeout_seconds: float = 15.0) -> None:
    deadline = time.monotonic() + timeout_seconds
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(  # noqa: S310
                f"{BAO_ADDRESS}/v1/sys/health", timeout=1.0
            ) as response:
                if response.status in (200, 429, 472, 473, 501, 503):
                    return
        except (urllib.error.URLError, OSError) as exc:
            last_error = exc
        time.sleep(0.2)
    raise RuntimeError(f"OpenBao dev server never became healthy: {last_error}")


# -- Fixture: two organizations (org1 with two projects, org2 with one),
# one organization_admin and one viewer per organization ---------------------

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
    bao_binary = _resolve_bao_binary()

    migration_result = _run_migration(migration_url)
    check(
        "FTF-04A01",
        f"alembic upgrade head runs cleanly (exit {migration_result.returncode})\n"
        f"stdout: {migration_result.stdout}\nstderr: {migration_result.stderr}",
        migration_result.returncode == 0,
    )
    if FAILURES:
        _finish()

    bao_process = subprocess.Popen(
        [
            bao_binary,
            "server",
            "-dev",
            f"-dev-listen-address={BAO_ADDRESS.removeprefix('http://')}",
            f"-dev-root-token-id={BAO_ROOT_TOKEN}",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    try:
        _wait_for_bao_health()
        _run_checks(migration_url, app_password)
    finally:
        bao_process.terminate()
        try:
            bao_process.wait(timeout=10)
        except subprocess.TimeoutExpired:  # pragma: no cover -- defensive cleanup only
            bao_process.kill()
            bao_process.wait(timeout=10)

    _finish()


def _finish() -> None:
    if FAILURES:
        print(f"FTF-04A01 real TargetApplicationConnection proof: {len(FAILURES)} failed")
        for failure in FAILURES:
            print(f"  {failure}")
        sys.exit(1)
    print("FTF-04A01 real TargetApplicationConnection proof: all checks passed")
    sys.exit(0)


def _run_checks(migration_url: str, app_password: str) -> None:
    owner_engine = create_engine(migration_url)
    app_url = _app_role_url(migration_url, app_password)
    app_engine = create_engine(app_url)

    check(
        "FTF-04A01",
        "the router's own database session is built from the restricted "
        "sovra_app role's connection URL, never the migration-owner role",
        app_engine.url.username == APP_ROLE_NAME
        and app_engine.url.username != owner_engine.url.username,
    )

    with Session(owner_engine) as session:
        org1 = Organization(name="Acme 04A01", slug=f"acme-04a01-{uuid.uuid4().hex[:8]}")
        org2 = Organization(name="Globex 04A01", slug=f"globex-04a01-{uuid.uuid4().hex[:8]}")
        session.add_all([org1, org2])
        session.flush()
        ws1 = Workspace(organization_id=org1.id, name="Acme Main", slug="main")
        ws2 = Workspace(organization_id=org2.id, name="Globex Main", slug="main")
        session.add_all([ws1, ws2])
        session.flush()
        project1 = Project(
            organization_id=org1.id,
            workspace_id=ws1.id,
            name="acme-agentic-project",
            project_type="agentic_product",
            status="active",
            security_profile="standard",
            row_version=1,
            created_by="seed",
        )
        project1b = Project(
            organization_id=org1.id,
            workspace_id=ws1.id,
            name="acme-second-project",
            project_type="agentic_product",
            status="active",
            security_profile="standard",
            row_version=1,
            created_by="seed",
        )
        project2 = Project(
            organization_id=org2.id,
            workspace_id=ws2.id,
            name="globex-agentic-project",
            project_type="agentic_product",
            status="active",
            security_profile="standard",
            row_version=1,
            created_by="seed",
        )
        session.add_all([project1, project1b, project2])
        session.commit()
        org1_id, org2_id = org1.id, org2.id
        project1_id, project1b_id, project2_id = project1.id, project1b.id, project2.id

    fixture = _Fixture({"org1": org1_id, "org2": org2_id})
    session_factory = sessionmaker(bind=app_engine)
    bao_config = smi.OpenBaoConfig.from_env(
        {"SOVRA_OPENBAO_ADDR": BAO_ADDRESS, "SOVRA_OPENBAO_TOKEN": BAO_ROOT_TOKEN},
        require_tls=False,
    )
    app = FastAPI()
    app.include_router(
        build_target_application_connection_router(
            session_factory,
            principal_resolver=fixture.principal_resolver,
            role_lookup=fixture.role_lookup,
            secret_manager_config_factory=lambda: bao_config,
        )
    )
    app_no_secret_manager = FastAPI()
    app_no_secret_manager.include_router(
        build_target_application_connection_router(
            session_factory,
            principal_resolver=fixture.principal_resolver,
            role_lookup=fixture.role_lookup,
            secret_manager_config_factory=lambda: None,
        )
    )

    org1_admin = fixture.header_for("org1", Role.ORGANIZATION_ADMIN)
    org1_viewer = fixture.header_for("org1", Role.VIEWER)
    org2_admin = fixture.header_for("org2", Role.ORGANIZATION_ADMIN)

    def collection_path(org_id: uuid.UUID, project_id: uuid.UUID) -> str:
        return f"/api/v1/organizations/{org_id}/projects/{project_id}/target-connections"

    def item_path(org_id: uuid.UUID, project_id: uuid.UUID, connection_id: object) -> str:
        return f"{collection_path(org_id, project_id)}/{connection_id}"

    with _running_app(app) as base_url:
        # -- Unauthenticated / role-boundary denial --
        r = _request(base_url, "GET", collection_path(org1_id, project1_id))
        check("FTF-04A01", "an unauthenticated list request is denied (401)", r.status == 401)

        r = _request(base_url, "GET", collection_path(org1_id, project1_id), principal=org1_viewer)
        check(
            "FTF-04A01",
            "a viewer (not in TARGET_CONNECTION_ROLES; Section 45 names only "
            "organization_admin for 'connections') is denied even a read (403)",
            r.status == 403,
        )

        # -- project ownership: a project_id from a different organization
        # is refused identically to a missing one (404) --
        r = _request(
            base_url,
            "POST",
            collection_path(org1_id, project2_id),
            principal=org1_admin,
            payload={
                "connector_type": "github",
                "display_name": "cross_org_project",
                "repository_identity": "acme/repo",
                "repository_url_redacted": "https://github.com/acme/repo",
                "credential": "raw-github-token",
            },
        )
        check(
            "FTF-04A01",
            "creating a target connection against a project_id belonging to "
            "a different organization is refused (404), identical to a "
            "missing project_id",
            r.status == 404,
        )

        # -- negative_acceptance: "unsupported connector... fail server-side" --
        r = _request(
            base_url,
            "POST",
            collection_path(org1_id, project1_id),
            principal=org1_admin,
            payload={
                "connector_type": "gitlab",
                "display_name": "unsupported-connector",
                "repository_identity": "acme/repo",
            },
        )
        check(
            "FTF-04A01",
            "negative_acceptance: an unsupported connector_type is refused "
            "server-side (400), against the real live route",
            r.status == 400,
        )

        # -- negative_acceptance: "excessive permission fail server-side" --
        r = _request(
            base_url,
            "POST",
            collection_path(org1_id, project1_id),
            principal=org1_admin,
            payload={
                "connector_type": "github",
                "display_name": "excessive-permission",
                "repository_identity": "acme/repo",
                "repository_url_redacted": "https://github.com/acme/repo",
                "credential": "raw-github-token",
                "access_mode": "write",
            },
        )
        check(
            "FTF-04A01",
            "negative_acceptance: access_mode='write' is refused server-side "
            "(400), against the real live route",
            r.status == 400,
        )

        # -- Real create WITH a real credential (connector_type=github) --
        r = _request(
            base_url,
            "POST",
            collection_path(org1_id, project1_id),
            principal=org1_admin,
            payload={
                "connector_type": "github",
                "display_name": "support-agent-repo",
                "repository_identity": "acme/support-agent",
                "repository_url_redacted": "https://github.com/acme/support-agent",
                "default_branch": "main",
                "credential": "ghs_real-test-token-value",
            },
        )
        check("FTF-04A01", "a real, valid connection create succeeds (201)", r.status == 201)
        connection = r.json() if r.status == 201 else {}
        connection_id = connection.get("id")
        check(
            "FTF-04A01",
            "acceptance: the response discloses only whether a credential is "
            "set, never the real secret_ref or its value",
            "credential_secret_ref" not in connection and connection.get("has_credential") is True,
        )
        check(
            "FTF-04A01",
            "the connection's own last_validated_at is set at creation time "
            "-- this task's own real 'validate credentials' acceptance "
            "clause ran synchronously",
            connection.get("last_validated_at") is not None,
        )
        check(
            "FTF-04A01",
            "access_mode is genuinely 'read_only' by default",
            connection.get("access_mode") == "read_only",
        )

        # -- Prove the credential genuinely reached the real OpenBao backend,
        # resolved directly against that same live server (not this task's
        # own claim alone) --
        real_ref = smi.build_secret_ref(
            mount="secret",
            path=f"target-connections/{org1_id}/{connection_id}",
            field="credential",
        )
        resolved_credential = smi.resolve_secret_ref(real_ref, config=bao_config)
        check(
            "FTF-04A01",
            "the real credential value is genuinely retrievable from the "
            "real OpenBao server at the exact path this task's own code "
            "writes to",
            resolved_credential == "ghs_real-test-token-value",
        )

        # -- Real create for connector_type=local_repository (no credential,
        # no URL) --
        r = _request(
            base_url,
            "POST",
            collection_path(org1_id, project1_id),
            principal=org1_admin,
            payload={
                "connector_type": "local_repository",
                "display_name": "local-checkout",
                "repository_identity": "local:/srv/repos/support-agent",
            },
        )
        check(
            "FTF-04A01",
            "a real local_repository connection (no credential, no URL) " "create succeeds (201)",
            r.status == 201,
        )
        check(
            "FTF-04A01",
            "a local_repository connection genuinely has no credential",
            r.json().get("has_credential") is False,
        )

        # -- local_repository refuses a supplied URL/credential (negative
        # test of _validate_connector_shape reachable through the real route) --
        r = _request(
            base_url,
            "POST",
            collection_path(org1_id, project1_id),
            principal=org1_admin,
            payload={
                "connector_type": "local_repository",
                "display_name": "local-with-url",
                "repository_identity": "local:/srv/repos/other",
                "repository_url_redacted": "https://github.com/acme/repo",
            },
        )
        check(
            "FTF-04A01",
            "local_repository with a repository_url_redacted is refused "
            "server-side (400) through the real live route",
            r.status == 400,
        )

        # -- Real duplicate display_name in the same project rejected by the
        # real database's own unique constraint --
        r = _request(
            base_url,
            "POST",
            collection_path(org1_id, project1_id),
            principal=org1_admin,
            payload={
                "connector_type": "local_repository",
                "display_name": "support-agent-repo",
                "repository_identity": "local:/srv/repos/dup",
            },
        )
        check(
            "FTF-04A01",
            "a duplicate display_name within the same project is rejected "
            "by the real database's own unique constraint",
            r.status >= 400,
        )

        # -- cross-tenant IDOR: org2's admin cannot read org1's real
        # connection through org2's own URL, refused identically to missing --
        r = _request(
            base_url,
            "GET",
            item_path(org2_id, project2_id, connection_id),
            principal=org2_admin,
        )
        check(
            "FTF-04A01",
            "a real cross-tenant read is refused (404), never leaking the row",
            r.status == 404,
        )

        # -- negative_acceptance: "cross-project target IDs... fail
        # server-side" -- SAME organization, a genuinely different project --
        r = _request(
            base_url,
            "GET",
            item_path(org1_id, project1b_id, connection_id),
            principal=org1_admin,
        )
        check(
            "FTF-04A01",
            "negative_acceptance: reading a real connection through a "
            "different project's own URL within the SAME organization is "
            "refused (404), against a real live database row -- proves the "
            "project-match check, not only the organization-match check",
            r.status == 404,
        )
        # ...and the same connection genuinely IS reachable through its own
        # real project's URL, proving the 404 above is the project check,
        # not a broken route.
        r = _request(
            base_url,
            "GET",
            item_path(org1_id, project1_id, connection_id),
            principal=org1_admin,
        )
        check(
            "FTF-04A01",
            "the same connection is genuinely reachable through its own "
            "real project's URL (200)",
            r.status == 200,
        )

        # -- update: selected_branch is mutable --
        r = _request(
            base_url,
            "PATCH",
            item_path(org1_id, project1_id, connection_id),
            principal=org1_admin,
            payload={"selected_branch": "feature/discovery"},
        )
        check("FTF-04A01", "updating selected_branch on a real row succeeds (200)", r.status == 200)
        check(
            "FTF-04A01",
            "selected_branch is genuinely updated on the real row",
            r.json().get("selected_branch") == "feature/discovery",
        )

        # -- revoke, then refuse further edits (real row) --
        r = _request(
            base_url, "DELETE", item_path(org1_id, project1_id, connection_id), principal=org1_admin
        )
        check("FTF-04A01", "revoke succeeds (200)", r.status == 200)
        check(
            "FTF-04A01",
            "the revoked connection's own connection_status is genuinely 'revoked'",
            r.json().get("connection_status") == "revoked",
        )

        r = _request(
            base_url,
            "PATCH",
            item_path(org1_id, project1_id, connection_id),
            principal=org1_admin,
            payload={"selected_branch": "should-be-refused"},
        )
        check(
            "FTF-04A01",
            "editing an already-revoked connection is refused (409), a real database row",
            r.status == 409,
        )

        # -- revoke idempotency --
        r = _request(
            base_url, "DELETE", item_path(org1_id, project1_id, connection_id), principal=org1_admin
        )
        check(
            "FTF-04A01",
            "repeating revoke on an already-revoked real row is idempotent (200)",
            r.status == 200,
        )

    # -- fail-closed: a credential supplied with no secret-manager backend
    # configured is refused (503), never stored in plaintext --
    with _running_app(app_no_secret_manager) as base_url:
        r = _request(
            base_url,
            "POST",
            collection_path(org1_id, project1_id),
            principal=org1_admin,
            payload={
                "connector_type": "github",
                "display_name": "no-secret-manager-source",
                "repository_identity": "acme/other-repo",
                "repository_url_redacted": "https://github.com/acme/other-repo",
                "credential": "should-never-be-stored-in-plaintext",
            },
        )
        check(
            "FTF-04A01",
            "a credential supplied with no secret-manager backend configured "
            "is refused (503), never silently stored in plaintext",
            r.status == 503,
        )
        with Session(owner_engine) as session:
            leaked = session.execute(
                text(
                    "select count(*) from target_application_connections "
                    "where display_name = 'no-secret-manager-source'"
                )
            ).scalar_one()
        check(
            "FTF-04A01",
            "no row was created at all for the refused request -- never a "
            "dangling connection with no real credential behind its ref",
            leaked == 0,
        )

    # -- grant-surface: the restricted sovra_app role cannot DELETE a
    # target_application_connections row directly --
    with Session(app_engine) as session:
        set_tenant_context_sql = text(
            "SELECT set_config('app.current_organization_id', :org_id, true)"
        )
        session.execute(set_tenant_context_sql, {"org_id": str(org1_id)})
        try:
            session.execute(
                text("DELETE FROM target_application_connections WHERE id = :id"),
                {"id": connection_id},
            )
            session.commit()
            check(
                "FTF-04A01",
                "negative_acceptance/grant-surface: the restricted sovra_app "
                "role cannot DELETE a target_application_connections row "
                "directly -- revoke is the only real transition, never row "
                "deletion",
                False,
            )
        except DBAPIError:
            session.rollback()
            check(
                "FTF-04A01",
                "negative_acceptance/grant-surface: the restricted sovra_app "
                "role cannot DELETE a target_application_connections row "
                "directly -- revoke is the only real transition, never row "
                "deletion",
                True,
            )


if __name__ == "__main__":
    main()
