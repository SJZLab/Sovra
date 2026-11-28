#!/usr/bin/env python3
"""FTF-1504-F1 -- project scoping of the 7 target-connection child handlers on real Postgres.

Before this fix, these handlers did `del organization_id, project_id` and resolved the
connection by organization only (RLS), so a principal with access to project A could read or
write project B's snapshots and candidates in the same organization by putting any project id
in the path:

* target_source_snapshot_routes: POST/GET snapshots, GET snapshot;
* discovered_agent_candidate_routes: POST/GET candidates, GET candidate;
* discovered_agent_candidate_selection_routes: POST select.

For every handler this test proves, through a real uvicorn server whose sessions use the
restricted `sovra_app` role:

* the own project answers its existing success status and body;
* the connection's real project being a DIFFERENT project of the same organization, or a
  random project id, answers 404 `not_found` (same as a missing row, never 403), and a write
  handler persists nothing;
* a principal of another organization still gets the existing 403.

Requires `SOVRA_DATABASE_URL` (migration owner) and `SOVRA_APP_DB_PASSWORD`, like every
integration test in this chain.
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
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

ROOT = Path(__file__).resolve().parent.parent.parent
API_ROOT = ROOT / "apps" / "api"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(API_ROOT))

import uvicorn  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from sqlalchemy import create_engine, func, select  # noqa: E402
from sqlalchemy.orm import Session, sessionmaker  # noqa: E402

from app.auth.audit_event_writer import AuditEvent  # noqa: E402
from app.auth.organization_workspace_schema import (  # noqa: E402
    APP_ROLE_NAME,
    Organization,
    Workspace,
)
from app.auth.rbac_middleware import AuthenticatedPrincipal, Role  # noqa: E402
from app.projects.project_schema import Project  # noqa: E402
from app.target_connectors.discovered_agent_candidate_routes import (  # noqa: E402
    build_discovered_agent_candidate_router,
)
from app.target_connectors.discovered_agent_candidate_schema import (  # noqa: E402
    DiscoveredAgentCandidate,
)
from app.target_connectors.discovered_agent_candidate_selection_routes import (  # noqa: E402
    build_discovered_agent_candidate_selection_router,
)
from app.target_connectors.target_application_connection_schema import (  # noqa: E402
    TargetApplicationConnection,
)
from app.target_connectors.target_source_snapshot_routes import (  # noqa: E402
    build_target_source_snapshot_router,
)
from app.target_connectors.target_source_snapshot_schema import TargetSourceSnapshot  # noqa: E402

CID = "FTF-1504-F1"
FAILURES: list[str] = []
CHECKS = [0]


def check(description: str, condition: bool) -> None:
    CHECKS[0] += 1
    if not condition:
        FAILURES.append(f"FAIL [{CID}] {description}")


def _required_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        print(f"BLOCKED_EXTERNAL: {name} is not set (live Postgres required)", file=sys.stderr)
        sys.exit(1)
    return value


def _app_role_url(migration_url: str, app_password: str) -> str:
    parts = urlsplit(migration_url)
    netloc = f"{APP_ROLE_NAME}:{app_password}@{parts.hostname}"
    if parts.port:
        netloc += f":{parts.port}"
    return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))


def _run_migration(database_url: str) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    env["SOVRA_DATABASE_URL"] = database_url
    return subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=str(API_ROOT),
        capture_output=True,
        text=True,
        env=env,
    )


def _free_port() -> int:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = int(sock.getsockname()[1])
    sock.close()
    return port


@contextlib.contextmanager
def _running_app(app: FastAPI) -> Iterator[str]:
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


def _request(
    base_url: str, method: str, path: str, *, principal: str, payload: Any = None
) -> tuple[int, bytes]:
    headers = {"X-Test-Principal": principal}
    body = None
    if payload is not None:
        body = json.dumps(payload).encode()
        headers["Content-Type"] = "application/json"
    parsed = urllib.parse.urlparse(base_url)
    conn = http.client.HTTPConnection(parsed.hostname or "127.0.0.1", parsed.port or 80, timeout=10)
    try:
        conn.request(method, path, body=body, headers=headers)
        response = conn.getresponse()
        return response.status, response.read()
    finally:
        conn.close()


_ISSUER = "https://issuer.example"


class _Fixture:
    def __init__(self, org_ids: dict[str, uuid.UUID]) -> None:
        self.principals: dict[str, AuthenticatedPrincipal] = {}
        self.memberships: dict[tuple[str, str, uuid.UUID], Role] = {}
        for label, org_id in org_ids.items():
            subject = f"user-{label}-admin"
            self.principals[label] = AuthenticatedPrincipal(issuer=_ISSUER, subject=subject)
            self.memberships[(_ISSUER, subject, org_id)] = Role.ORGANIZATION_ADMIN

    def principal_resolver(self, request: Any) -> AuthenticatedPrincipal | None:
        return self.principals.get(request.headers.get("x-test-principal", ""))

    def role_lookup(self, issuer: str, subject: str, organization_id: uuid.UUID) -> Role | None:
        return self.memberships.get((issuer, subject, organization_id))


def _project(org_id: uuid.UUID, ws_id: uuid.UUID, name: str) -> Project:
    return Project(
        organization_id=org_id,
        workspace_id=ws_id,
        name=name,
        project_type="agentic_product",
        status="active",
        security_profile="standard",
        row_version=1,
        created_by="seed",
    )


def _connection(org_id: uuid.UUID, project_id: uuid.UUID, name: str) -> TargetApplicationConnection:
    return TargetApplicationConnection(
        organization_id=org_id,
        project_id=project_id,
        connector_type="local_repository",
        display_name=name,
        repository_identity=f"acme/{name}",
        access_mode="read_only",
        connection_status="active",
        last_validated_at=datetime.now(UTC),
        created_by="seed",
    )


def main() -> None:
    migration_url = _required_env("SOVRA_DATABASE_URL")
    app_password = _required_env("SOVRA_APP_DB_PASSWORD")
    migrated = _run_migration(migration_url)
    check(
        f"alembic upgrade head (exit {migrated.returncode}): {migrated.stderr[-400:]}",
        migrated.returncode == 0,
    )
    if FAILURES:
        _finish()
    owner_engine = create_engine(migration_url)
    app_engine = create_engine(_app_role_url(migration_url, app_password))
    check(
        "the routers' sessions use the restricted sovra_app role",
        app_engine.url.username == APP_ROLE_NAME,
    )

    suffix = uuid.uuid4().hex[:8]
    with Session(owner_engine) as session:
        org1 = Organization(name="Acme F1", slug=f"acme-f1-{suffix}")
        org2 = Organization(name="Globex F1", slug=f"globex-f1-{suffix}")
        session.add_all([org1, org2])
        session.flush()
        ws1 = Workspace(organization_id=org1.id, name="Main", slug="main")
        ws2 = Workspace(organization_id=org2.id, name="Main", slug="main")
        session.add_all([ws1, ws2])
        session.flush()
        project_a = _project(org1.id, ws1.id, "project-a")
        project_b = _project(org1.id, ws1.id, "project-b")  # same organization, other project
        project_g = _project(org2.id, ws2.id, "project-g")
        session.add_all([project_a, project_b, project_g])
        session.flush()
        conn_a = _connection(org1.id, project_a.id, "conn-a")
        conn_b = _connection(org1.id, project_b.id, "conn-b")
        session.add_all([conn_a, conn_b])
        session.flush()
        snap_a = TargetSourceSnapshot(
            organization_id=org1.id,
            target_application_connection_id=conn_a.id,
            commit_sha="a" * 40,
            tree_hash="b" * 40,
            capture_method="local_git_commit",
            analysis_status="not_analyzed",
        )
        session.add(snap_a)
        session.flush()
        cand_a = DiscoveredAgentCandidate(
            organization_id=org1.id,
            target_source_snapshot_id=snap_a.id,
            candidate_key="entrypoint:a.py:Agent",
            display_name="Project A agent",
            confidence_score=0.5,
            discovery_status="candidate",
        )
        session.add(cand_a)
        session.commit()
        org1_id, org2_id = org1.id, org2.id
        pa, pb, pg = project_a.id, project_b.id, project_g.id
        ca, cb = conn_a.id, conn_b.id
        sa, cda = snap_a.id, cand_a.id

    fixture = _Fixture({"org1": org1_id, "org2": org2_id})
    session_factory = sessionmaker(bind=app_engine)
    app = FastAPI()
    for build in (
        build_target_source_snapshot_router,
        build_discovered_agent_candidate_router,
        build_discovered_agent_candidate_selection_router,
    ):
        app.include_router(
            build(
                session_factory,
                principal_resolver=fixture.principal_resolver,
                role_lookup=fixture.role_lookup,
            )
        )

    def base(org: uuid.UUID, project: uuid.UUID, conn: uuid.UUID = ca) -> str:
        return f"/api/v1/organizations/{org}/projects/{project}/target-connections/{conn}"

    def counts() -> tuple[int, int, int]:
        with Session(owner_engine) as session:
            return (
                session.scalar(select(func.count()).select_from(TargetSourceSnapshot)) or 0,
                session.scalar(select(func.count()).select_from(DiscoveredAgentCandidate)) or 0,
                session.scalar(select(func.count()).select_from(AuditEvent)) or 0,
            )

    snapshot_payload = {
        "commit_sha": "c" * 40,
        "tree_hash": "d" * 40,
        "capture_method": "local_git_commit",
    }
    candidate_payload = {
        "candidate_key": "entrypoint:new.py:Agent",
        "display_name": "New agent",
        "confidence_score": 0.6,
    }
    handlers: list[tuple[str, str, Any, Any, int]] = [
        # (label, method, path-suffix builder, payload, own-project success status)
        ("POST select", "POST", lambda: f"/snapshots/{sa}/candidates/{cda}/select", None, 201),
        ("POST snapshots", "POST", lambda: "/snapshots", snapshot_payload, 201),
        ("GET snapshots", "GET", lambda: "/snapshots", None, 200),
        ("GET snapshot", "GET", lambda: f"/snapshots/{sa}", None, 200),
        ("POST candidates", "POST", lambda: f"/snapshots/{sa}/candidates", candidate_payload, 201),
        ("GET candidates", "GET", lambda: f"/snapshots/{sa}/candidates", None, 200),
        ("GET candidate", "GET", lambda: f"/snapshots/{sa}/candidates/{cda}", None, 200),
    ]

    with _running_app(app) as url:
        for label, method, suffix_of, payload, ok_status in handlers:
            before = counts()
            for other_label, project in (
                ("another project of the same organization", pb),
                ("a random project id", uuid.uuid4()),
            ):
                status, body = _request(
                    url,
                    method,
                    base(org1_id, project) + suffix_of(),
                    principal="org1",
                    payload=payload,
                )
                check(
                    f"{label} with {other_label} in the path -> 404 not_found "
                    f"(got {status} {body[:80]!r})",
                    status == 404 and json.loads(body) == {"detail": "not_found"},
                )
            check(f"{label}: nothing was written for the refused projects", counts() == before)
            status, body = _request(
                url, method, base(org1_id, pg) + suffix_of(), principal="org2", payload=payload
            )
            check(f"{label} by another organization's admin -> 403 (got {status})", status == 403)
            status, body = _request(
                url, method, base(org1_id, pa) + suffix_of(), principal="org1", payload=payload
            )
            check(
                f"{label} under the connection's own project -> {ok_status} (got {status} "
                f"{body[:120]!r})",
                status == ok_status,
            )
            if method == "GET" and status == 200:
                again = _request(url, method, base(org1_id, pa) + suffix_of(), principal="org1")
                check(f"{label}: the own-project body is stable byte for byte", again[1] == body)
            if label == "GET snapshot":
                check("GET snapshot returns the seeded snapshot", json.loads(body)["id"] == str(sa))
            if label == "GET candidate":
                check(
                    "GET candidate returns the seeded candidate", json.loads(body)["id"] == str(cda)
                )

        # project B's own connection is still reachable under project B only
        status, _ = _request(url, "GET", base(org1_id, pb, cb) + "/snapshots", principal="org1")
        check(f"project B's own connection under project B -> 200 (got {status})", status == 200)
        status, _ = _request(url, "GET", base(org1_id, pa, cb) + "/snapshots", principal="org1")
        check(f"project B's connection under project A -> 404 (got {status})", status == 404)

    _finish()


def _finish() -> None:
    if FAILURES:
        print(f"{CID} project scoping: {len(FAILURES)} failed")
        for failure in FAILURES:
            print(f"  {failure}")
        sys.exit(1)
    print(f"{CID} project scoping: all {CHECKS[0]} checks passed")


if __name__ == "__main__":
    main()
