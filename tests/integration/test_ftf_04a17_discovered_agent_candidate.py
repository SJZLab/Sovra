#!/usr/bin/env python3
"""FTF-04A17 -- real Postgres DiscoveredAgentCandidate schema/API proof.

Proves this task's own acceptance clause 1 ("Store every candidate with
traceable evidence and unresolved fields; expose paginated project-scoped
API") and negative_acceptance ("Candidate priority/confidence is not
calibrated probability or automatic in-scope selection") against a real,
live PostgreSQL server -- never a mocked connection or an in-memory
SQLite stand-in (SQLite has no row-level security at all, so a passing
test against it would prove nothing about this task's actual acceptance
criterion).

Two real organizations (org1/org2), each with a real workspace/project/
connection/snapshot (org1 gets two real snapshots, so a candidate can be
shown refusing to be reassigned to a *different* snapshot within the
*same* organization), are seeded as the migration-owner role, exactly
like FTF-04A07's own integration suite. Every request below runs through
a real uvicorn server, with the router's own database session bound to
the real, distinct, least-privileged `sovra_app` role (never the
migration-owner role -- confirmed directly below).

Requires a real Postgres reachable at `SOVRA_DATABASE_URL` (the migration
owner role) plus `SOVRA_APP_DB_PASSWORD`, matching every prior integration
test's own environment contract exactly. This repo's interactive dev
sandbox has no container-registry egress for a Docker Postgres image, but
this evidence was produced against a directly-installed PostgreSQL 16
cluster in this same sandbox (`apt`-installed, not pulled from a
container registry) -- the identical local-cluster approach every prior
integration-tested task in this chain already used, and wired into CI's
own dedicated integration job (a GitHub Actions `services: postgres`
container, real registry egress on the hosted runner).

Run directly (against an already-migrated or unmigrated database; this
script runs the migration itself):
    SOVRA_DATABASE_URL=postgresql+psycopg://<migration-role>@host/db \\
    SOVRA_APP_DB_PASSWORD=<app-role-password> \\
    python3 tests/integration/test_ftf_04a17_discovered_agent_candidate.py
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
from app.target_connectors.discovered_agent_candidate_routes import (  # noqa: E402
    build_discovered_agent_candidate_router,
)
from app.target_connectors.target_application_connection_schema import (  # noqa: E402
    TargetApplicationConnection,
)
from app.target_connectors.target_source_snapshot_schema import TargetSourceSnapshot  # noqa: E402

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


# -- Fixture: two organizations (each with a workspace + project), one
# organization_admin and one evaluator per organization ---------------------

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
        "FTF-04A17",
        f"alembic upgrade head runs cleanly (exit {migration_result.returncode})\n"
        f"stdout: {migration_result.stdout}\nstderr: {migration_result.stderr}",
        migration_result.returncode == 0,
    )
    if FAILURES:
        print(f"FTF-04A17 real DiscoveredAgentCandidate schema/API proof: {len(FAILURES)} failed")
        for failure in FAILURES:
            print(f"  {failure}")
        sys.exit(1)

    owner_engine = create_engine(migration_url)
    app_url = _app_role_url(migration_url, app_password)
    app_engine = create_engine(app_url)

    check(
        "FTF-04A17",
        "the router's own database session is built from the restricted "
        "sovra_app role's connection URL, never the migration-owner role",
        app_engine.url.username == APP_ROLE_NAME
        and app_engine.url.username != owner_engine.url.username,
    )

    # -- Seed two real, distinct tenants, each with one workspace/project/
    # connection, plus real TargetSourceSnapshot rows: two for org1 (to
    # prove cross-snapshot reassignment is refused within the SAME
    # organization) and one for org2 (the cross-tenant IDOR case)
    # (migration-owner role, seeding only) --
    now = datetime.now(UTC)
    with Session(owner_engine) as session:
        org1 = Organization(name="Acme 04A17", slug=f"acme-04a17-{uuid.uuid4().hex[:8]}")
        org2 = Organization(name="Globex 04A17", slug=f"globex-04a17-{uuid.uuid4().hex[:8]}")
        session.add_all([org1, org2])
        session.flush()
        ws1 = Workspace(organization_id=org1.id, name="Acme Main", slug="main")
        ws2 = Workspace(organization_id=org2.id, name="Globex Main", slug="main")
        session.add_all([ws1, ws2])
        session.flush()
        project1 = Project(
            organization_id=org1.id,
            workspace_id=ws1.id,
            name="acme-project",
            project_type="agentic_product",
            status="active",
            security_profile="standard",
            row_version=1,
            created_by="seed",
        )
        project2 = Project(
            organization_id=org2.id,
            workspace_id=ws2.id,
            name="globex-project",
            project_type="agentic_product",
            status="active",
            security_profile="standard",
            row_version=1,
            created_by="seed",
        )
        session.add_all([project1, project2])
        session.flush()

        connection1 = TargetApplicationConnection(
            organization_id=org1.id,
            project_id=project1.id,
            connector_type="local_repository",
            display_name="acme-primary-repo",
            repository_identity="acme/primary",
            access_mode="read_only",
            connection_status="active",
            last_validated_at=now,
            created_by="seed",
        )
        connection2 = TargetApplicationConnection(
            organization_id=org2.id,
            project_id=project2.id,
            connector_type="local_repository",
            display_name="globex-primary-repo",
            repository_identity="globex/primary",
            access_mode="read_only",
            connection_status="active",
            last_validated_at=now,
            created_by="seed",
        )
        session.add_all([connection1, connection2])
        session.flush()

        snapshot1 = TargetSourceSnapshot(
            organization_id=org1.id,
            target_application_connection_id=connection1.id,
            commit_sha="1" * 40,
            tree_hash="2" * 40,
            capture_method="local_git_commit",
            analysis_status="not_analyzed",
        )
        snapshot1b = TargetSourceSnapshot(
            organization_id=org1.id,
            target_application_connection_id=connection1.id,
            commit_sha="3" * 40,
            tree_hash="4" * 40,
            capture_method="local_git_commit",
            analysis_status="not_analyzed",
        )
        snapshot2 = TargetSourceSnapshot(
            organization_id=org2.id,
            target_application_connection_id=connection2.id,
            commit_sha="5" * 40,
            tree_hash="6" * 40,
            capture_method="local_git_commit",
            analysis_status="not_analyzed",
        )
        session.add_all([snapshot1, snapshot1b, snapshot2])
        session.commit()

        org1_id, org2_id = org1.id, org2.id
        project1_id, project2_id = project1.id, project2.id
        connection1_id, connection2_id = connection1.id, connection2.id
        snapshot1_id, snapshot1b_id, snapshot2_id = snapshot1.id, snapshot1b.id, snapshot2.id

    fixture = _Fixture({"org1": org1_id, "org2": org2_id})
    session_factory = sessionmaker(bind=app_engine)
    app = FastAPI()
    app.include_router(
        build_discovered_agent_candidate_router(
            session_factory,
            principal_resolver=fixture.principal_resolver,
            role_lookup=fixture.role_lookup,
        )
    )

    org1_admin = fixture.header_for("org1", Role.ORGANIZATION_ADMIN)
    org1_evaluator = fixture.header_for("org1", Role.EVALUATOR)
    org2_admin = fixture.header_for("org2", Role.ORGANIZATION_ADMIN)

    def collection_path(
        org_id: uuid.UUID, project_id: uuid.UUID, connection_id: uuid.UUID, snapshot_id: uuid.UUID
    ) -> str:
        return (
            f"/api/v1/organizations/{org_id}/projects/{project_id}"
            f"/target-connections/{connection_id}/snapshots/{snapshot_id}/candidates"
        )

    def item_path(
        org_id: uuid.UUID,
        project_id: uuid.UUID,
        connection_id: uuid.UUID,
        snapshot_id: uuid.UUID,
        candidate_id: object,
    ) -> str:
        return f"{collection_path(org_id, project_id, connection_id, snapshot_id)}/{candidate_id}"

    candidate1_id: str | None = None

    with _running_app(app) as base_url:
        # -- Unauthenticated / role-boundary denial --
        r = _request(
            base_url, "GET", collection_path(org1_id, project1_id, connection1_id, snapshot1_id)
        )
        check("FTF-04A17", "an unauthenticated list request is denied (401)", r.status == 401)

        r = _request(
            base_url,
            "GET",
            collection_path(org1_id, project1_id, connection1_id, snapshot1_id),
            principal=org1_evaluator,
        )
        check(
            "FTF-04A17",
            "an evaluator (a role Section 45 names, with no dedicated read "
            "sentence for candidates) is denied read (403) -- this "
            "resource is gated to organization_admin alone",
            r.status == 403,
        )

        r = _request(
            base_url,
            "POST",
            collection_path(org1_id, project1_id, connection1_id, snapshot1_id),
            principal=org1_evaluator,
            payload={
                "candidate_key": "entrypoint:app.py:Agent",
                "display_name": "Support agent",
                "confidence_score": 0.5,
            },
        )
        check("FTF-04A17", "an evaluator is denied create (403)", r.status == 403)

        # -- snapshot ownership: a snapshot_id from a different
        # organization is refused identically to a missing one (404) --
        r = _request(
            base_url,
            "GET",
            collection_path(org2_id, project2_id, connection1_id, snapshot1_id),
            principal=org2_admin,
        )
        check(
            "FTF-04A17",
            "the core IDOR case: org1's own real connection/snapshot, "
            "requested under org2's own path by a real org2 "
            "organization_admin, is denied (404) identically to missing",
            r.status == 404,
        )

        # -- acceptance: a real candidate with partial evidence is created --
        r = _request(
            base_url,
            "POST",
            collection_path(org1_id, project1_id, connection1_id, snapshot1_id),
            principal=org1_admin,
            payload={
                "candidate_key": "entrypoint:apps/api/agent.py:AgentClass",
                "display_name": "Primary support agent",
                "framework_or_runtime": "fastapi",
                "confidence_score": 0.72,
                "model_evidence_json": {"litellm_alias": "gpt-4o-mini"},
                "tool_evidence_json": None,
            },
        )
        check("FTF-04A17", "a real, valid candidate create succeeds (201)", r.status == 201)
        candidate1 = r.json() if r.status == 201 else {}
        check(
            "FTF-04A17",
            "acceptance: the created candidate carries its real traceable "
            "fields, a resolved evidence category, an honestly-null "
            "unresolved one, and starts at the single Release-1 "
            "discovery_status",
            isinstance(candidate1, dict)
            and candidate1.get("candidate_key") == "entrypoint:apps/api/agent.py:AgentClass"
            and candidate1.get("target_source_snapshot_id") == str(snapshot1_id)
            and candidate1.get("model_evidence_json") == {"litellm_alias": "gpt-4o-mini"}
            and candidate1.get("tool_evidence_json") is None
            and candidate1.get("discovery_status") == "candidate",
        )
        candidate1_id = candidate1.get("id")

        # -- negative_acceptance: duplicate candidate_key within the same
        # snapshot is refused (this task's own real deduplication identity) --
        r = _request(
            base_url,
            "POST",
            collection_path(org1_id, project1_id, connection1_id, snapshot1_id),
            principal=org1_admin,
            payload={
                "candidate_key": "entrypoint:apps/api/agent.py:AgentClass",
                "display_name": "Duplicate of the same entrypoint",
                "confidence_score": 0.1,
            },
        )
        check(
            "FTF-04A17",
            "negative_acceptance: a second candidate with the SAME "
            "candidate_key for the SAME snapshot is refused (409), not a "
            "silent duplicate row",
            r.status == 409,
        )

        # -- same candidate_key on a DIFFERENT snapshot (even same org) is fine --
        r = _request(
            base_url,
            "POST",
            collection_path(org1_id, project1_id, connection1_id, snapshot1b_id),
            principal=org1_admin,
            payload={
                "candidate_key": "entrypoint:apps/api/agent.py:AgentClass",
                "display_name": "Same entrypoint, different snapshot",
                "confidence_score": 0.3,
            },
        )
        check(
            "FTF-04A17",
            "the same candidate_key under a genuinely different snapshot "
            "is not a duplicate -- dedup is scoped per-snapshot (201)",
            r.status == 201,
        )

        # -- negative_acceptance: out-of-range confidence_score refused --
        r = _request(
            base_url,
            "POST",
            collection_path(org1_id, project1_id, connection1_id, snapshot1_id),
            principal=org1_admin,
            payload={
                "candidate_key": "entrypoint:other.py:Agent",
                "display_name": "Bad score candidate",
                "confidence_score": 1.5,
            },
        )
        check(
            "FTF-04A17",
            "a confidence_score above 1.0 is refused (400) -- advisory-only "
            "bounded shape, never accepted unchecked",
            r.status == 400,
        )

        # -- list is paginated: limit=1 returns exactly one item with a
        # real total count reflecting both real rows for this snapshot --
        r = _request(
            base_url,
            "POST",
            collection_path(org1_id, project1_id, connection1_id, snapshot1_id),
            principal=org1_admin,
            payload={
                "candidate_key": "entrypoint:second.py:Agent",
                "display_name": "Second candidate",
                "confidence_score": 0.2,
            },
        )
        check(
            "FTF-04A17",
            "a second, distinct candidate on snapshot1 is created (201)",
            r.status == 201,
        )

        snapshot1_path = collection_path(org1_id, project1_id, connection1_id, snapshot1_id)
        r = _request(
            base_url,
            "GET",
            snapshot1_path + "?limit=1&offset=0",
            principal=org1_admin,
        )
        page1 = r.json() if r.status == 200 else {}
        check(
            "FTF-04A17",
            "acceptance: 'expose paginated project-scoped API' -- limit=1 "
            "returns exactly one item, with a real total reflecting both "
            "real candidates on this snapshot",
            r.status == 200
            and isinstance(page1, dict)
            and len(page1.get("items", [])) == 1
            and page1.get("limit") == 1
            and page1.get("offset") == 0
            and page1.get("total") == 2,
        )

        r = _request(
            base_url,
            "GET",
            snapshot1_path + "?limit=1&offset=1",
            principal=org1_admin,
        )
        page2 = r.json() if r.status == 200 else {}
        check(
            "FTF-04A17",
            "the second page (offset=1) returns the other real candidate, "
            "not a repeat of the first",
            r.status == 200
            and len(page2.get("items", [])) == 1
            and page2["items"][0]["id"] != page1["items"][0]["id"],
        )

        # -- cross-snapshot scoping: snapshot1b's own real, separate
        # candidate list does not include snapshot1's real candidates --
        r = _request(
            base_url,
            "GET",
            collection_path(org1_id, project1_id, connection1_id, snapshot1b_id),
            principal=org1_admin,
        )
        listed_1b = r.json() if r.status == 200 else {}
        check(
            "FTF-04A17",
            "snapshot1b's own real, separate candidate list has exactly "
            "its own one real row -- candidate scoping is genuinely "
            "per-snapshot",
            r.status == 200 and listed_1b.get("total") == 1,
        )

        # -- cross-snapshot get: candidate1 requested under snapshot1b's
        # own item path (same organization, different snapshot) is
        # refused identically to missing --
        r = _request(
            base_url,
            "GET",
            item_path(org1_id, project1_id, connection1_id, snapshot1b_id, candidate1_id),
            principal=org1_admin,
        )
        check(
            "FTF-04A17",
            "a real candidate belonging to snapshot1, requested under "
            "snapshot1b's own path in the same organization, is denied "
            "(404) identically to missing",
            r.status == 404,
        )

        # -- IDOR: org2's own organization_admin cannot see org1's candidate --
        r = _request(
            base_url,
            "GET",
            item_path(org2_id, project2_id, connection2_id, snapshot2_id, candidate1_id),
            principal=org2_admin,
        )
        check(
            "FTF-04A17",
            "the core IDOR case: a real candidate belonging to org1, "
            "requested under org2's own path by a real org2 "
            "organization_admin, is denied (404) identically to missing",
            r.status == 404,
        )

    # -- Real database-level defense-in-depth proof: the UNIQUE constraint
    # catches a duplicate candidate_key even when the application-level
    # check is bypassed entirely (a raw INSERT through sovra_app) --
    with Session(app_engine) as session:
        session.execute(
            text("SELECT set_config('app.current_organization_id', :org_id, true)"),
            {"org_id": str(org1_id)},
        )
        try:
            session.execute(
                text(
                    "INSERT INTO discovered_agent_candidates "
                    "(organization_id, target_source_snapshot_id, candidate_key, "
                    "display_name, confidence_score) "
                    "VALUES (:org_id, :snapshot_id, :candidate_key, :display_name, 0.1)"
                ),
                {
                    "org_id": str(org1_id),
                    "snapshot_id": str(snapshot1_id),
                    "candidate_key": "entrypoint:apps/api/agent.py:AgentClass",
                    "display_name": "Raw duplicate insert",
                },
            )
            session.commit()
            check(
                "FTF-04A17",
                "negative_acceptance/defense-in-depth: a raw INSERT that "
                "bypasses the application-level duplicate check but still "
                "claims the same (snapshot, candidate_key) pair is refused "
                "directly by the real "
                "uq_discovered_agent_candidates_snapshot_id_candidate_key "
                "constraint",
                False,
            )
        except DBAPIError:
            session.rollback()
            check(
                "FTF-04A17",
                "negative_acceptance/defense-in-depth: a raw INSERT that "
                "bypasses the application-level duplicate check but still "
                "claims the same (snapshot, candidate_key) pair is refused "
                "directly by the real "
                "uq_discovered_agent_candidates_snapshot_id_candidate_key "
                "constraint",
                True,
            )

    # -- Real database-level defense-in-depth proof: the CHECK constraint
    # catches an out-of-range confidence_score even via a raw INSERT --
    with Session(app_engine) as session:
        session.execute(
            text("SELECT set_config('app.current_organization_id', :org_id, true)"),
            {"org_id": str(org1_id)},
        )
        try:
            session.execute(
                text(
                    "INSERT INTO discovered_agent_candidates "
                    "(organization_id, target_source_snapshot_id, candidate_key, "
                    "display_name, confidence_score) "
                    "VALUES (:org_id, :snapshot_id, :candidate_key, :display_name, 3.0)"
                ),
                {
                    "org_id": str(org1_id),
                    "snapshot_id": str(snapshot1_id),
                    "candidate_key": "entrypoint:raw-insert-bad-score.py:Agent",
                    "display_name": "Raw bad-score insert",
                },
            )
            session.commit()
            check(
                "FTF-04A17",
                "negative_acceptance/defense-in-depth: a raw INSERT "
                "claiming an out-of-range confidence_score is refused "
                "directly by the real "
                "ck_discovered_agent_candidates_confidence_score_range "
                "CHECK constraint",
                False,
            )
        except DBAPIError:
            session.rollback()
            check(
                "FTF-04A17",
                "negative_acceptance/defense-in-depth: a raw INSERT "
                "claiming an out-of-range confidence_score is refused "
                "directly by the real "
                "ck_discovered_agent_candidates_confidence_score_range "
                "CHECK constraint",
                True,
            )

    # -- Grant-surface proof: sovra_app genuinely cannot UPDATE or DELETE a
    # discovered_agent_candidates row directly -- a candidate is a fact
    # about what analysis found, enforced at the database privilege level. --
    if candidate1_id:
        with Session(app_engine) as session:
            session.execute(
                text("SELECT set_config('app.current_organization_id', :org_id, true)"),
                {"org_id": str(org1_id)},
            )
            try:
                session.execute(
                    text(
                        "UPDATE discovered_agent_candidates SET display_name = :name WHERE id = :id"
                    ),
                    {"name": "mutated", "id": candidate1_id},
                )
                session.commit()
                check(
                    "FTF-04A17",
                    "negative_acceptance/grant-surface: the restricted "
                    "sovra_app role cannot UPDATE a "
                    "discovered_agent_candidates row directly -- every "
                    "candidate is immutable",
                    False,
                )
            except DBAPIError:
                session.rollback()
                check(
                    "FTF-04A17",
                    "negative_acceptance/grant-surface: the restricted "
                    "sovra_app role cannot UPDATE a "
                    "discovered_agent_candidates row directly -- every "
                    "candidate is immutable",
                    True,
                )

            try:
                session.execute(
                    text("DELETE FROM discovered_agent_candidates WHERE id = :id"),
                    {"id": candidate1_id},
                )
                session.commit()
                check(
                    "FTF-04A17",
                    "negative_acceptance/grant-surface: the restricted "
                    "sovra_app role cannot DELETE a "
                    "discovered_agent_candidates row directly either",
                    False,
                )
            except DBAPIError:
                session.rollback()
                check(
                    "FTF-04A17",
                    "negative_acceptance/grant-surface: the restricted "
                    "sovra_app role cannot DELETE a "
                    "discovered_agent_candidates row directly either",
                    True,
                )

    if FAILURES:
        print(f"FTF-04A17 real DiscoveredAgentCandidate schema/API proof: {len(FAILURES)} failed")
        for failure in FAILURES:
            print(f"  {failure}")
        sys.exit(1)

    print("FTF-04A17 real DiscoveredAgentCandidate schema/API proof: all checks passed")
    sys.exit(0)


if __name__ == "__main__":
    main()
