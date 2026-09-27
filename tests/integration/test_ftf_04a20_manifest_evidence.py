#!/usr/bin/env python3
"""FTF-04A20 -- real Postgres ManifestEvidence schema/API proof.

Proves this task's own acceptance clause 1 ("Store per-field source
commit/path/symbol/hash or user override provenance and confidence
method") and negative_acceptance ("Fabricated line refs, cross-snapshot
evidence and unknown values presented as detected are rejected") against
a real, live PostgreSQL server -- never a mocked connection or an
in-memory SQLite stand-in (SQLite has no row-level security at all, so a
passing test against it would prove nothing about this task's actual
acceptance criterion).

One real organization pair (org1/org2), each with a real workspace/
project/target-connection/snapshot chain, is seeded as the migration-owner
role (FTF-04A01/FTF-04A07, already-CERTIFIED). A real, draft `AgentManifest`
is seeded directly for org1's own project (FTF-04A19, already-CERTIFIED)
-- this task's own real parent resource. Every request below runs through
a real uvicorn server, with the router's own database session bound to
the real, distinct, least-privileged `sovra_app` role FTF-0102's migration
creates (never the migration-owner role -- confirmed directly below),
exactly like every prior integration test in this chain.

Requires a real Postgres reachable at `SOVRA_DATABASE_URL` (the migration
owner role) plus `SOVRA_APP_DB_PASSWORD`, matching every prior integration
test's own environment contract exactly. This repo's interactive dev
sandbox has no container-registry egress (same finding as every prior
integration-tested task) -- this evidence was instead produced locally
against a directly-installed PostgreSQL 16 cluster, and is wired into
CI's own dedicated integration job (a GitHub Actions `services: postgres`
container, real registry egress on the hosted runner).

Run directly (against an already-migrated or unmigrated database; this
script runs the migration itself):
    SOVRA_DATABASE_URL=postgresql+psycopg://<migration-role>@host/db \\
    SOVRA_APP_DB_PASSWORD=<app-role-password> \\
    python3 tests/integration/test_ftf_04a20_manifest_evidence.py
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
from app.target_connectors.agent_manifest_schema import AgentManifest  # noqa: E402
from app.target_connectors.manifest_evidence_routes import (  # noqa: E402
    build_manifest_evidence_router,
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


# -- Fixture: two organizations, one workspace_admin and one evaluator per
# organization ---------------------------------------------------------

_ISSUER = "https://issuer.example"


class _Fixture:
    def __init__(self, org_ids: dict[str, uuid.UUID]) -> None:
        self.org_ids = org_ids
        self.principals: dict[tuple[str, Role], AuthenticatedPrincipal] = {}
        self.memberships: dict[tuple[str, str, uuid.UUID], Role] = {}
        for label, org_id in org_ids.items():
            for role in (Role.WORKSPACE_ADMIN, Role.EVALUATOR):
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
        "FTF-04A20",
        f"alembic upgrade head runs cleanly (exit {migration_result.returncode})\n"
        f"stdout: {migration_result.stdout}\nstderr: {migration_result.stderr}",
        migration_result.returncode == 0,
    )
    if FAILURES:
        print(f"FTF-04A20 real ManifestEvidence schema/API proof: {len(FAILURES)} failed")
        for failure in FAILURES:
            print(f"  {failure}")
        sys.exit(1)

    owner_engine = create_engine(migration_url)
    app_url = _app_role_url(migration_url, app_password)
    app_engine = create_engine(app_url)

    check(
        "FTF-04A20",
        "the router's own database session is built from the restricted "
        "sovra_app role's connection URL, never the migration-owner role",
        app_engine.url.username == APP_ROLE_NAME
        and app_engine.url.username != owner_engine.url.username,
    )

    # -- Seed two real, distinct tenants, one project/connection/snapshot
    # each for org1, plus a real DRAFT AgentManifest for org1's project
    # (this task's own real parent resource) --
    now = datetime.now(UTC)
    with Session(owner_engine) as session:
        org1 = Organization(name="Acme 04A20", slug=f"acme-04a20-{uuid.uuid4().hex[:8]}")
        org2 = Organization(name="Globex 04A20", slug=f"globex-04a20-{uuid.uuid4().hex[:8]}")
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

        real_commit_sha = "1" * 40
        other_commit_sha = "9" * 40
        snapshot1 = TargetSourceSnapshot(
            organization_id=org1.id,
            target_application_connection_id=connection1.id,
            commit_sha=real_commit_sha,
            tree_hash="2" * 40,
            capture_method="github_resolved_commit",
        )
        snapshot2 = TargetSourceSnapshot(
            organization_id=org2.id,
            target_application_connection_id=connection2.id,
            commit_sha="5" * 40,
            tree_hash="6" * 40,
            capture_method="github_resolved_commit",
        )
        session.add_all([snapshot1, snapshot2])
        session.flush()

        manifest1 = AgentManifest(
            organization_id=org1.id,
            project_id=project1.id,
            target_source_snapshot_id=snapshot1.id,
            version=1,
            status="draft",
            agent_name="CustomerSupportAgent",
            created_by="seed",
        )
        session.add(manifest1)
        session.commit()

        org1_id, org2_id = org1.id, org2.id
        project1_id, project2_id = project1.id, project2.id
        manifest1_id = manifest1.id

    fixture = _Fixture({"org1": org1_id, "org2": org2_id})
    session_factory = sessionmaker(bind=app_engine)
    app = FastAPI()
    app.include_router(
        build_manifest_evidence_router(
            session_factory,
            principal_resolver=fixture.principal_resolver,
            role_lookup=fixture.role_lookup,
        )
    )

    org1_admin = fixture.header_for("org1", Role.WORKSPACE_ADMIN)
    org1_evaluator = fixture.header_for("org1", Role.EVALUATOR)
    org2_admin = fixture.header_for("org2", Role.WORKSPACE_ADMIN)

    def collection_path(org_id: uuid.UUID, project_id: uuid.UUID, manifest_id: object) -> str:
        return (
            f"/api/v1/organizations/{org_id}/projects/{project_id}/manifests/{manifest_id}/evidence"
        )

    def item_path(
        org_id: uuid.UUID, project_id: uuid.UUID, manifest_id: object, evidence_id: object
    ) -> str:
        return f"{collection_path(org_id, project_id, manifest_id)}/{evidence_id}"

    evidence1_id: str | None = None

    with _running_app(app) as base_url:
        # -- Unauthenticated / role-boundary denial --
        r = _request(base_url, "GET", collection_path(org1_id, project1_id, manifest1_id))
        check("FTF-04A20", "an unauthenticated list request is denied (401)", r.status == 401)

        r = _request(
            base_url,
            "POST",
            collection_path(org1_id, project1_id, manifest1_id),
            principal=org1_evaluator,
            payload={
                "field_path": "agent_entrypoint_json",
                "evidence_type": "user_input",
                "evidence_json": {"note": "manually confirmed"},
            },
        )
        check(
            "FTF-04A20",
            "an evaluator (Section 45's widest read baseline, but not "
            "{workspace_admin, organization_admin}) is denied create (403)",
            r.status == 403,
        )

        r = _request(
            base_url,
            "GET",
            collection_path(org1_id, project1_id, manifest1_id),
            principal=org1_evaluator,
        )
        check(
            "FTF-04A20",
            "an evaluator IS allowed read -- this task's own wide read baseline",
            r.status == 200,
        )

        # -- cross-tenant IDOR: org1's own real manifest, requested under
        # org2's own path by a real org2 workspace_admin --
        r = _request(
            base_url,
            "GET",
            collection_path(org2_id, project2_id, manifest1_id),
            principal=org2_admin,
        )
        check(
            "FTF-04A20",
            "the core IDOR case: org1's own real manifest_id, requested "
            "under org2's own path by a real org2 workspace_admin, is "
            "denied (404) identically to missing",
            r.status == 404,
        )

        # -- negative_acceptance: an unknown field_path is refused --
        r = _request(
            base_url,
            "POST",
            collection_path(org1_id, project1_id, manifest1_id),
            principal=org1_admin,
            payload={
                "field_path": "not_a_real_field",
                "evidence_type": "user_input",
                "evidence_json": {"note": "x"},
            },
        )
        check(
            "FTF-04A20",
            "negative_acceptance: an unknown field_path (not a real "
            "AgentManifest content field) is refused (400)",
            r.status == 400,
        )

        # -- negative_acceptance: cross-snapshot evidence is rejected --
        r = _request(
            base_url,
            "POST",
            collection_path(org1_id, project1_id, manifest1_id),
            principal=org1_admin,
            payload={
                "field_path": "agent_entrypoint_json",
                "evidence_type": "repo_file",
                "repository_commit_sha": other_commit_sha,
                "file_path": "src/agents/support.ts",
                "line_start": 10,
                "line_end": 20,
                "content_hash": "b" * 64,
                "confidence_score": 0.9,
                "evidence_json": {"matched": "class SupportAgent"},
            },
        )
        check(
            "FTF-04A20",
            "negative_acceptance: 'cross-snapshot evidence ... rejected' -- "
            "a repository_commit_sha that does not match manifest1's own "
            "snapshot commit is refused (409)",
            r.status == 409,
        )

        # -- acceptance: a real repo_file evidence row is created, anchored
        # to the manifest's own real snapshot commit --
        r = _request(
            base_url,
            "POST",
            collection_path(org1_id, project1_id, manifest1_id),
            principal=org1_admin,
            payload={
                "field_path": "agent_entrypoint_json",
                "evidence_type": "repo_file",
                "repository_commit_sha": real_commit_sha,
                "file_path": "src/agents/support.ts",
                "line_start": 10,
                "line_end": 20,
                "content_hash": "b" * 64,
                "confidence_score": 0.9,
                "evidence_json": {"matched": "class SupportAgent"},
            },
        )
        check(
            "FTF-04A20",
            "acceptance: a real, valid repo_file evidence create succeeds (201)",
            r.status == 201,
        )
        evidence1 = r.json() if r.status == 201 else {}
        check(
            "FTF-04A20",
            "the created evidence carries the real commit/path/line/hash/confidence",
            evidence1.get("repository_commit_sha") == real_commit_sha
            and evidence1.get("line_start") == 10
            and evidence1.get("confidence_score") == 0.9,
        )
        evidence1_id = evidence1.get("id")

        # -- acceptance: a real user_input evidence row (no repo provenance) --
        r = _request(
            base_url,
            "POST",
            collection_path(org1_id, project1_id, manifest1_id),
            principal=org1_admin,
            payload={
                "field_path": "mandate_json",
                "evidence_type": "user_input",
                "evidence_json": {"note": "product owner directly entered the mandate"},
            },
        )
        check(
            "FTF-04A20",
            "acceptance: 'user override provenance' -- a real user_input "
            "evidence create with no repo provenance succeeds (201)",
            r.status == 201 and r.json().get("repository_commit_sha") is None,
        )

        # -- negative_acceptance: fabricated line refs on a non-line-anchored type --
        r = _request(
            base_url,
            "POST",
            collection_path(org1_id, project1_id, manifest1_id),
            principal=org1_admin,
            payload={
                "field_path": "runtime_dependencies_json",
                "evidence_type": "config",
                "repository_commit_sha": real_commit_sha,
                "file_path": "package.json",
                "line_start": 3,
                "line_end": 5,
                "content_hash": "c" * 64,
                "confidence_score": 0.8,
                "evidence_json": {"matched": "engines.node"},
            },
        )
        check(
            "FTF-04A20",
            "negative_acceptance: 'Fabricated line refs ... rejected' -- a "
            "config row (not line-anchored) carrying line_start/line_end "
            "is refused (400)",
            r.status == 400,
        )

        # -- get one / list --
        r = _request(
            base_url,
            "GET",
            item_path(org1_id, project1_id, manifest1_id, evidence1_id),
            principal=org1_admin,
        )
        check("FTF-04A20", "getting the real evidence row by ID succeeds (200)", r.status == 200)

        r = _request(
            base_url,
            "GET",
            item_path(org1_id, project1_id, manifest1_id, uuid.uuid4()),
            principal=org1_admin,
        )
        check("FTF-04A20", "getting a nonexistent evidence ID is refused (404)", r.status == 404)

        r = _request(
            base_url,
            "GET",
            collection_path(org1_id, project1_id, manifest1_id),
            principal=org1_admin,
        )
        listed = r.json() if r.status == 200 else []
        check(
            "FTF-04A20",
            "listing shows both real evidence rows for manifest1",
            isinstance(listed, list) and len(listed) == 2,
        )

    # -- Real database-level defense-in-depth: the CHECK constraint
    # catches a fabricated-line-ref row even when the ORM-level check is
    # bypassed entirely (a raw INSERT through sovra_app) --
    with Session(app_engine) as session:
        session.execute(
            text("SELECT set_config('app.current_organization_id', :org_id, true)"),
            {"org_id": str(org1_id)},
        )
        try:
            session.execute(
                text(
                    "INSERT INTO manifest_evidence "
                    "(organization_id, agent_manifest_id, field_path, evidence_type, "
                    "repository_commit_sha, confidence_score, evidence_json) "
                    "VALUES (:org_id, :manifest_id, 'runtime_dependencies_json', 'config', "
                    ':commit_sha, 0.5, \'{"note":"x"}\'::jsonb)'
                ),
                {
                    "org_id": str(org1_id),
                    "manifest_id": str(manifest1_id),
                    "commit_sha": real_commit_sha,
                },
            )
            session.commit()
            check(
                "FTF-04A20",
                "negative_acceptance/defense-in-depth: a raw INSERT of a "
                "'config' row with NO file_path is refused directly by the "
                "real ck_manifest_evidence_file_path_iff_repo_cited CHECK constraint",
                False,
            )
        except DBAPIError:
            session.rollback()
            check(
                "FTF-04A20",
                "negative_acceptance/defense-in-depth: a raw INSERT of a "
                "'config' row with NO file_path is refused directly by the "
                "real ck_manifest_evidence_file_path_iff_repo_cited CHECK constraint",
                True,
            )

    with Session(app_engine) as session:
        session.execute(
            text("SELECT set_config('app.current_organization_id', :org_id, true)"),
            {"org_id": str(org1_id)},
        )
        try:
            session.execute(
                text(
                    "INSERT INTO manifest_evidence "
                    "(organization_id, agent_manifest_id, field_path, evidence_type, "
                    "evidence_json) VALUES (:org_id, :manifest_id, 'mandate_json', "
                    "'user_input', '{\"note\":\"x\"}'::jsonb)"
                ),
                {"org_id": str(org1_id), "manifest_id": str(manifest1_id)},
            )
            session.execute(
                text(
                    "UPDATE manifest_evidence SET confidence_score = 0.5 "
                    "WHERE evidence_type = 'user_input' AND agent_manifest_id = :manifest_id"
                ),
                {"manifest_id": str(manifest1_id)},
            )
            session.commit()
            check(
                "FTF-04A20",
                "negative_acceptance/defense-in-depth: sovra_app cannot "
                "UPDATE a manifest_evidence row at all (grant-surface -- "
                "immutable once written)",
                False,
            )
        except DBAPIError:
            session.rollback()
            check(
                "FTF-04A20",
                "negative_acceptance/defense-in-depth: sovra_app cannot "
                "UPDATE a manifest_evidence row at all (grant-surface -- "
                "immutable once written)",
                True,
            )

    # -- Grant-surface proof: sovra_app genuinely cannot DELETE a
    # manifest_evidence row directly --
    if evidence1_id:
        with Session(app_engine) as session:
            session.execute(
                text("SELECT set_config('app.current_organization_id', :org_id, true)"),
                {"org_id": str(org1_id)},
            )
            try:
                session.execute(
                    text("DELETE FROM manifest_evidence WHERE id = :id"),
                    {"id": evidence1_id},
                )
                session.commit()
                check(
                    "FTF-04A20",
                    "negative_acceptance/grant-surface: the restricted "
                    "sovra_app role cannot DELETE a manifest_evidence row directly",
                    False,
                )
            except DBAPIError:
                session.rollback()
                check(
                    "FTF-04A20",
                    "negative_acceptance/grant-surface: the restricted "
                    "sovra_app role cannot DELETE a manifest_evidence row directly",
                    True,
                )

    if FAILURES:
        print(f"FTF-04A20 real ManifestEvidence schema/API proof: {len(FAILURES)} failed")
        for failure in FAILURES:
            print(f"  {failure}")
        sys.exit(1)

    print("FTF-04A20 real ManifestEvidence schema/API proof: all checks passed")
    sys.exit(0)


if __name__ == "__main__":
    main()
