#!/usr/bin/env python3
"""FTF-0601 -- real Postgres EvaluationSuite schema/API proof.

Proves this task's own acceptance clause ("Create suite schema with
development/final purpose, versioned mandate/runtime binding and
reviewer role") and negative_acceptance ("Wrong project/manifest,
unversioned grading logic and mixed train/final data grants rejected")
against a real, live PostgreSQL server -- never a mocked connection or an
in-memory SQLite stand-in (SQLite has no row-level security at all, so a
passing test against it would prove nothing about this task's actual
acceptance criterion).

Two real organizations (org1 with two real projects/connections/
snapshots/manifests, org2 for the cross-tenant IDOR case) are seeded as
the migration-owner role (FTF-0102/FTF-1001/FTF-04A01/FTF-04A07/
FTF-04A19, already-CERTIFIED). Every request below runs through a real
uvicorn server, with the router's own database session bound to the
real, distinct, least-privileged `sovra_app` role FTF-0102's migration
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
    python3 tests/integration/test_ftf_0601_evaluation_suite.py
"""

from __future__ import annotations

import contextlib
import http.client
import importlib
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

_CRUD_MODULE_PATH = API_ROOT / "app" / "evaluation" / "evaluation_suite_crud.py"

import uvicorn  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from sqlalchemy import create_engine, func, select, text  # noqa: E402
from sqlalchemy.exc import DBAPIError  # noqa: E402
from sqlalchemy.orm import Session, sessionmaker  # noqa: E402

from app.auth.organization_workspace_schema import (  # noqa: E402
    APP_ROLE_NAME,
    Organization,
    Workspace,
)
from app.auth.rbac_middleware import AuthenticatedPrincipal, Role  # noqa: E402
from app.db.tenant_context import TenantContext, tenant_scoped_session_for  # noqa: E402
from app.evaluation import evaluation_suite_crud as _evaluation_suite_crud_module  # noqa: E402
from app.evaluation import evaluation_suite_routes as _evaluation_suite_routes_module  # noqa: E402
from app.evaluation.evaluation_suite_routes import build_evaluation_suite_router  # noqa: E402
from app.evaluation.evaluation_suite_schema import PURPOSE_FINAL, EvaluationSuite  # noqa: E402
from app.projects.project_schema import Project  # noqa: E402
from app.target_connectors.agent_manifest_schema import AgentManifest  # noqa: E402
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


def _run_downgrade_upgrade_roundtrip(database_url: str) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["SOVRA_DATABASE_URL"] = database_url
    downgrade = subprocess.run(
        [sys.executable, "-m", "alembic", "downgrade", "-1"],
        cwd=str(API_ROOT),
        capture_output=True,
        text=True,
        env=env,
    )
    if downgrade.returncode != 0:
        return downgrade
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


# -- Fixture: two organizations, one workspace_admin, one evaluator and one
# viewer per organization -------------------------------------------------

_ISSUER = "https://issuer.example"


class _Fixture:
    def __init__(self, org_ids: dict[str, uuid.UUID]) -> None:
        self.org_ids = org_ids
        self.principals: dict[tuple[str, Role], AuthenticatedPrincipal] = {}
        self.memberships: dict[tuple[str, str, uuid.UUID], Role] = {}
        for label, org_id in org_ids.items():
            for role in (
                Role.ORGANIZATION_ADMIN,
                Role.WORKSPACE_ADMIN,
                Role.EVALUATOR,
                Role.VIEWER,
            ):
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
        "FTF-0601",
        f"alembic upgrade head runs cleanly (exit {migration_result.returncode})\n"
        f"stdout: {migration_result.stdout}\nstderr: {migration_result.stderr}",
        migration_result.returncode == 0,
    )
    if FAILURES:
        print(f"FTF-0601 real EvaluationSuite schema/API proof: {len(FAILURES)} failed")
        for failure in FAILURES:
            print(f"  {failure}")
        sys.exit(1)

    roundtrip_result = _run_downgrade_upgrade_roundtrip(migration_url)
    check(
        "FTF-0601",
        f"the head migration (0019_eval_suites_reviewer_norm) round-trips downgrade -1 -> "
        f"upgrade head cleanly "
        f"(exit {roundtrip_result.returncode})\nstdout: {roundtrip_result.stdout}\n"
        f"stderr: {roundtrip_result.stderr}",
        roundtrip_result.returncode == 0,
    )

    owner_engine = create_engine(migration_url)
    app_url = _app_role_url(migration_url, app_password)
    app_engine = create_engine(app_url)

    check(
        "FTF-0601",
        "the router's own database session is built from the restricted "
        "sovra_app role's connection URL, never the migration-owner role",
        app_engine.url.username == APP_ROLE_NAME
        and app_engine.url.username != owner_engine.url.username,
    )

    # -- Seed two real, distinct tenants, one project/connection/snapshot/
    # locked-manifest each for org1 (plus a second project for the
    # cross-project-mismatch case), one for org2 --
    now = datetime.now(UTC)
    with Session(owner_engine) as session:
        org1 = Organization(name="Acme 0601", slug=f"acme-0601-{uuid.uuid4().hex[:8]}")
        org2 = Organization(name="Globex 0601", slug=f"globex-0601-{uuid.uuid4().hex[:8]}")
        session.add_all([org1, org2])
        session.flush()
        ws1 = Workspace(organization_id=org1.id, name="Acme Main", slug="main")
        ws2 = Workspace(organization_id=org2.id, name="Globex Main", slug="main")
        session.add_all([ws1, ws2])
        session.flush()

        project1 = Project(
            organization_id=org1.id,
            workspace_id=ws1.id,
            name="acme-project-1",
            project_type="agentic_product",
            status="active",
            security_profile="standard",
            row_version=1,
            created_by="seed",
        )
        project1b = Project(
            organization_id=org1.id,
            workspace_id=ws1.id,
            name="acme-project-2",
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
        session.add_all([project1, project1b, project2])
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
        connection1b = TargetApplicationConnection(
            organization_id=org1.id,
            project_id=project1b.id,
            connector_type="local_repository",
            display_name="acme-secondary-repo",
            repository_identity="acme/secondary",
            access_mode="read_only",
            connection_status="active",
            last_validated_at=now,
            created_by="seed",
        )
        session.add_all([connection1, connection1b])
        session.flush()

        snapshot1 = TargetSourceSnapshot(
            organization_id=org1.id,
            target_application_connection_id=connection1.id,
            commit_sha="1" * 40,
            tree_hash="2" * 40,
            capture_method="github_resolved_commit",
        )
        snapshot1b = TargetSourceSnapshot(
            organization_id=org1.id,
            target_application_connection_id=connection1b.id,
            commit_sha="3" * 40,
            tree_hash="4" * 40,
            capture_method="github_resolved_commit",
        )
        session.add_all([snapshot1, snapshot1b])
        session.flush()

        # project1's own real LOCKED manifest -- this task's own real
        # "versioned mandate/runtime binding" anchor.
        locked_manifest = AgentManifest(
            organization_id=org1.id,
            project_id=project1.id,
            target_source_snapshot_id=snapshot1.id,
            version=1,
            status="locked",
            agent_name="CustomerSupportAgent",
            agent_entrypoint_json={"entry": "src/agent.py"},
            current_model_binding_json={"model": "gpt-x"},
            runtime_dependencies_json={"python": "3.11"},
            mandate_json={"mandate": "help customers"},
            manifest_sha256="a" * 64,
            created_by="seed",
            approved_by="approver-seed",
            approved_at=now,
        )
        # project1's own real DRAFT manifest -- negative_acceptance:
        # binding to a not-yet-locked manifest must be refused.
        draft_manifest = AgentManifest(
            organization_id=org1.id,
            project_id=project1.id,
            target_source_snapshot_id=snapshot1.id,
            version=2,
            status="draft",
            agent_name="CustomerSupportAgentV2",
            created_by="seed",
        )
        # project1b's own real LOCKED manifest -- negative_acceptance:
        # binding project1's suite to this one must be refused (wrong project).
        other_project_manifest = AgentManifest(
            organization_id=org1.id,
            project_id=project1b.id,
            target_source_snapshot_id=snapshot1b.id,
            version=1,
            status="locked",
            agent_name="OtherAgent",
            agent_entrypoint_json={"entry": "src/other.py"},
            current_model_binding_json={"model": "gpt-y"},
            runtime_dependencies_json={"python": "3.12"},
            manifest_sha256="b" * 64,
            created_by="seed",
            approved_by="approver-seed",
            approved_at=now,
        )
        session.add_all([locked_manifest, draft_manifest, other_project_manifest])
        session.commit()

        org1_id, org2_id = org1.id, org2.id
        project1_id = project1.id
        locked_manifest_id = locked_manifest.id
        draft_manifest_id = draft_manifest.id
        other_project_manifest_id = other_project_manifest.id

    fixture = _Fixture({"org1": org1_id, "org2": org2_id})
    session_factory = sessionmaker(bind=app_engine)
    app = FastAPI()
    app.include_router(
        build_evaluation_suite_router(
            session_factory,
            principal_resolver=fixture.principal_resolver,
            role_lookup=fixture.role_lookup,
        )
    )

    org1_admin = fixture.header_for("org1", Role.WORKSPACE_ADMIN)
    org1_evaluator = fixture.header_for("org1", Role.EVALUATOR)
    org1_viewer = fixture.header_for("org1", Role.VIEWER)
    org2_admin = fixture.header_for("org2", Role.WORKSPACE_ADMIN)

    def collection_path(org_id: uuid.UUID, project_id: uuid.UUID) -> str:
        return f"/api/v1/organizations/{org_id}/projects/{project_id}/evaluation-suites"

    def item_path(org_id: uuid.UUID, project_id: uuid.UUID, suite_id: object) -> str:
        return f"{collection_path(org_id, project_id)}/{suite_id}"

    def lock_path(org_id: uuid.UUID, project_id: uuid.UUID, suite_id: object) -> str:
        return f"{item_path(org_id, project_id, suite_id)}/lock"

    suite1_id: str | None = None

    with _running_app(app) as base_url:
        # -- Unauthenticated / role-boundary denial --
        r = _request(base_url, "GET", collection_path(org1_id, project1_id))
        check("FTF-0601", "an unauthenticated list request is denied (401)", r.status == 401)

        r = _request(
            base_url,
            "POST",
            collection_path(org1_id, project1_id),
            principal=org1_viewer,
            payload={
                "agent_manifest_id": str(locked_manifest_id),
                "purpose": "development",
                "name": "Viewer attempt",
                "grading_logic_version": 1,
            },
        )
        check(
            "FTF-0601",
            "a viewer (Section 45's widest read baseline, but not "
            "{evaluator, workspace_admin, organization_admin}) is denied create (403)",
            r.status == 403,
        )

        r = _request(base_url, "GET", collection_path(org1_id, project1_id), principal=org1_viewer)
        check(
            "FTF-0601",
            "a viewer IS allowed read -- this task's own wide read baseline",
            r.status == 200,
        )

        # -- cross-tenant IDOR: org1's own real project, requested under
        # org2's own path by a real org2 workspace_admin --
        r = _request(base_url, "GET", collection_path(org2_id, project1_id), principal=org2_admin)
        check(
            "FTF-0601",
            "the core IDOR case: org1's own real project_id, requested "
            "under org2's own path by a real org2 workspace_admin, is "
            "denied (404) identically to missing",
            r.status == 404,
        )

        # -- negative_acceptance: mixed purpose value is refused --
        r = _request(
            base_url,
            "POST",
            collection_path(org1_id, project1_id),
            principal=org1_evaluator,
            payload={
                "agent_manifest_id": str(locked_manifest_id),
                "purpose": "mixed",
                "name": "Bad purpose",
                "grading_logic_version": 1,
            },
        )
        check(
            "FTF-0601",
            "negative_acceptance: 'mixed train/final data grants ... "
            "rejected' -- a 'mixed' purpose value is refused (400)",
            r.status == 400,
        )

        # -- negative_acceptance: unversioned grading logic is refused --
        r = _request(
            base_url,
            "POST",
            collection_path(org1_id, project1_id),
            principal=org1_evaluator,
            payload={
                "agent_manifest_id": str(locked_manifest_id),
                "purpose": "development",
                "name": "No grading version",
                "grading_logic_version": 0,
            },
        )
        check(
            "FTF-0601",
            "negative_acceptance: 'unversioned grading logic ... rejected' "
            "-- grading_logic_version=0 is refused (400)",
            r.status == 400,
        )

        # -- negative_acceptance: wrong manifest (belongs to a different project) --
        r = _request(
            base_url,
            "POST",
            collection_path(org1_id, project1_id),
            principal=org1_evaluator,
            payload={
                "agent_manifest_id": str(other_project_manifest_id),
                "purpose": "development",
                "name": "Wrong project manifest",
                "grading_logic_version": 1,
            },
        )
        check(
            "FTF-0601",
            "negative_acceptance: 'Wrong project/manifest ... rejected' -- "
            "a manifest belonging to a different project is refused (409)",
            r.status == 409,
        )

        # -- negative_acceptance: wrong manifest (not locked) --
        r = _request(
            base_url,
            "POST",
            collection_path(org1_id, project1_id),
            principal=org1_evaluator,
            payload={
                "agent_manifest_id": str(draft_manifest_id),
                "purpose": "development",
                "name": "Draft manifest binding",
                "grading_logic_version": 1,
            },
        )
        check(
            "FTF-0601",
            "negative_acceptance: 'Wrong ... manifest ... rejected' -- a "
            "manifest that is not currently locked is refused (409)",
            r.status == 409,
        )

        # -- acceptance: a real, valid development suite create succeeds --
        r = _request(
            base_url,
            "POST",
            collection_path(org1_id, project1_id),
            principal=org1_evaluator,
            payload={
                "agent_manifest_id": str(locked_manifest_id),
                "purpose": "development",
                "name": "Golden path suite",
                "grading_logic_version": 1,
            },
        )
        check(
            "FTF-0601",
            "acceptance: a real, valid development suite create succeeds (201)",
            r.status == 201,
        )
        suite1 = r.json() if r.status == 201 else {}
        check(
            "FTF-0601",
            "the created suite carries its real purpose/manifest binding/version",
            suite1.get("purpose") == "development"
            and suite1.get("agent_manifest_id") == str(locked_manifest_id)
            and suite1.get("version") == 1
            and suite1.get("status") == "draft",
        )
        suite1_id = suite1.get("id")

        # -- L2 finding regression (QUEUE.md/docs/certifications/queue.md
        # 2026-10-01 review): the update path previously assigned
        # name/grading_logic_version/reviewer_id directly, skipping the
        # same real validators __init__ already applies at creation. Three
        # real HTTP-level cases, each against this still-draft suite1,
        # confirmed to leave the row's own real content unchanged -- not
        # merely that the HTTP response code looks right.
        r = _request(
            base_url,
            "PATCH",
            item_path(org1_id, project1_id, suite1_id),
            principal=org1_evaluator,
            payload={"grading_logic_version": 0},
        )
        check(
            "FTF-0601",
            "PATCH grading_logic_version=0 is refused as a real 400, not a "
            "500 from an unhandled database IntegrityError",
            r.status == 400,
        )

        r = _request(
            base_url,
            "PATCH",
            item_path(org1_id, project1_id, suite1_id),
            principal=org1_evaluator,
            payload={"name": ""},
        )
        check("FTF-0601", "PATCH name='' is refused (400), not silently accepted", r.status == 400)

        r = _request(
            base_url,
            "PATCH",
            item_path(org1_id, project1_id, suite1_id),
            principal=org1_evaluator,
            payload={"reviewer_id": ""},
        )
        check(
            "FTF-0601",
            "PATCH reviewer_id='' is refused (400) -- an empty string must "
            "never pass as a real reviewer identity",
            r.status == 400,
        )

        r = _request(
            base_url, "GET", item_path(org1_id, project1_id, suite1_id), principal=org1_evaluator
        )
        unchanged = r.json() if r.status == 200 else {}
        check(
            "FTF-0601",
            "none of the three refused PATCH attempts actually changed the "
            "suite's own real stored content",
            unchanged.get("grading_logic_version") == 1
            and unchanged.get("name") == "Golden path suite"
            and unchanged.get("reviewer_id") is None,
        )

        # The same reviewer_id='' value must also never let a subsequent
        # lock succeed, even if some other code path assigned it directly
        # (defense in depth -- lock_evaluation_suite's own check, not only
        # the PATCH validator above).
        r = _request(
            base_url, "POST", lock_path(org1_id, project1_id, suite1_id), principal=org1_evaluator
        )
        check(
            "FTF-0601",
            "locking suite1 still fails (409, no real reviewer was ever "
            "actually persisted by the refused PATCH attempts above)",
            r.status == 409,
        )

        # -- acceptance: a second suite for the same project gets version 2 --
        r = _request(
            base_url,
            "POST",
            collection_path(org1_id, project1_id),
            principal=org1_evaluator,
            payload={
                "agent_manifest_id": str(locked_manifest_id),
                "purpose": "final",
                "name": "Final qualification suite",
                "grading_logic_version": 1,
            },
        )
        check("FTF-0601", "a second suite for the same project succeeds (201)", r.status == 201)
        suite2 = r.json() if r.status == 201 else {}
        check(
            "FTF-0601",
            "the second suite gets a real, incremented version",
            suite2.get("version") == 2,
        )
        suite2_id = suite2.get("id")

        # -- negative_acceptance: locking without a reviewer is refused --
        r = _request(
            base_url, "POST", lock_path(org1_id, project1_id, suite1_id), principal=org1_evaluator
        )
        check(
            "FTF-0601",
            "locking a suite with no reviewer_id assigned is refused (409) "
            "-- Section 69.1's own independence rule requires a real reviewer",
            r.status == 409,
        )

        # -- L2 round-2: an arbitrary, non-member reviewer_id string is
        # refused at assignment time (self-asserted free text is no longer
        # accepted -- it must name a real member of the suite's own
        # organization) --
        r = _request(
            base_url,
            "PATCH",
            item_path(org1_id, project1_id, suite1_id),
            principal=org1_evaluator,
            payload={"reviewer_id": "totally-fake-nobody"},
        )
        check(
            "FTF-0601",
            "L2 round-2: assigning a reviewer_id that names no real "
            "organization member is refused (400), not silently accepted",
            r.status == 400,
        )

        # -- assign a real member as reviewer via PATCH, then lock
        # attempted by someone else (the suite's own creator) is refused:
        # only the assigned reviewer principal may perform the lock --
        r = _request(
            base_url,
            "PATCH",
            item_path(org1_id, project1_id, suite1_id),
            principal=org1_evaluator,
            payload={"reviewer_id": f"user-org1-{Role.WORKSPACE_ADMIN.value}"},
        )
        check(
            "FTF-0601",
            "assigning a real organization member as reviewer via PATCH succeeds (200)",
            r.status == 200,
        )

        r = _request(
            base_url, "POST", lock_path(org1_id, project1_id, suite1_id), principal=org1_evaluator
        )
        check(
            "FTF-0601",
            "L2 round-2: the suite's own creator, who is not its assigned "
            "reviewer, is refused when attempting to lock it (403) -- "
            "Section 69.1: only the real assigned reviewer may lock",
            r.status == 403,
        )

        r = _request(
            base_url, "POST", lock_path(org1_id, project1_id, suite1_id), principal=org1_admin
        )
        check(
            "FTF-0601",
            "acceptance: locking a development suite succeeds when "
            "performed by its own real, assigned reviewer principal (200)",
            r.status == 200,
        )
        check(
            "FTF-0601",
            "the locked suite carries a real locked_at",
            r.json().get("status") == "locked" and r.json().get("locked_at") is not None,
        )

        # -- negative_acceptance: re-locking an already-locked suite is refused --
        r = _request(
            base_url, "POST", lock_path(org1_id, project1_id, suite1_id), principal=org1_evaluator
        )
        check("FTF-0601", "re-locking an already-locked suite is refused (409)", r.status == 409)

        # -- negative_acceptance: editing a locked suite is refused --
        r = _request(
            base_url,
            "PATCH",
            item_path(org1_id, project1_id, suite1_id),
            principal=org1_evaluator,
            payload={"name": "Renamed after lock"},
        )
        check("FTF-0601", "editing a locked suite's content is refused (409)", r.status == 409)

        # -- Section 69.1: a FINAL suite locked by its own creator with no
        # distinct reviewer is refused (self-review) --
        r = _request(
            base_url,
            "PATCH",
            item_path(org1_id, project1_id, suite2_id),
            principal=org1_evaluator,
            payload={"reviewer_id": f"user-org1-{Role.EVALUATOR.value}"},
        )
        check(
            "FTF-0601",
            "assigning the creator itself as reviewer via PATCH succeeds (200)",
            r.status == 200,
        )

        r = _request(
            base_url, "POST", lock_path(org1_id, project1_id, suite2_id), principal=org1_evaluator
        )
        check(
            "FTF-0601",
            "Section 69.1: locking a FINAL suite whose reviewer_id equals "
            "its own created_by (self-review) is refused (409)",
            r.status == 409,
        )

        # -- assign a genuinely distinct reviewer and lock succeeds --
        r = _request(
            base_url,
            "PATCH",
            item_path(org1_id, project1_id, suite2_id),
            principal=org1_admin,
            payload={"reviewer_id": f"user-org1-{Role.WORKSPACE_ADMIN.value}"},
        )
        check("FTF-0601", "assigning a distinct reviewer via PATCH succeeds (200)", r.status == 200)

        r = _request(
            base_url, "POST", lock_path(org1_id, project1_id, suite2_id), principal=org1_admin
        )
        check(
            "FTF-0601",
            "acceptance: locking a FINAL suite with a genuinely distinct "
            "reviewer succeeds (200)",
            r.status == 200,
        )

        # -- L2 round-2 independence bypass: a padded/case-variant of the
        # creator's own identity as reviewer on a new FINAL suite still
        # refuses at lock (409), even though it is a real organization
        # member (itself) and so passes the membership check --
        r = _request(
            base_url,
            "POST",
            collection_path(org1_id, project1_id),
            principal=org1_evaluator,
            payload={
                "agent_manifest_id": str(locked_manifest_id),
                "purpose": "final",
                "name": "Padded self-review suite",
                "grading_logic_version": 1,
            },
        )
        check("FTF-0601", "a third suite for the same project succeeds (201)", r.status == 201)
        suite3 = r.json() if r.status == 201 else {}
        suite3_id = suite3.get("id")

        # Use the creator's own real subject, padded and case-flipped --
        # normalizes (strip+casefold) to the exact same identity.
        creator_subject = f"user-org1-{Role.EVALUATOR.value}"
        padded_case_variant_self = f"  {creator_subject.upper()}  "
        r = _request(
            base_url,
            "PATCH",
            item_path(org1_id, project1_id, suite3_id),
            principal=org1_evaluator,
            payload={"reviewer_id": padded_case_variant_self},
        )
        check(
            "FTF-0601",
            "assigning a padded/case-variant of the creator's own identity "
            "as reviewer succeeds at PATCH time -- it normalizes to a real "
            "member (itself), so the membership check alone does not catch "
            "this (200)",
            r.status == 200,
        )

        r = _request(
            base_url, "POST", lock_path(org1_id, project1_id, suite3_id), principal=org1_evaluator
        )
        check(
            "FTF-0601",
            "L2 round-2 independence bypass: locking a FINAL suite whose "
            "reviewer_id is a padded, case-variant of its own created_by "
            "is refused (409) -- normalize_identity (strip+casefold) "
            "catches what a bare `==` missed",
            r.status == 409,
        )

        # -- round-3 DoD (1): independence tested with at least 3 actor
        # roles (organization_admin, workspace_admin, evaluator), not only
        # evaluator -- the F19 finding was that a role-keyed independence
        # bypass would stay green because the self-review case was never
        # exercised with an admin-named actor. Each of the other two write
        # roles independently creates/self-reviews/self-locks a FINAL
        # suite and is refused exactly like the evaluator case above.
        for admin_role, admin_label in (
            (Role.WORKSPACE_ADMIN, "workspace_admin"),
            (Role.ORGANIZATION_ADMIN, "organization_admin"),
        ):
            admin_header = fixture.header_for("org1", admin_role)
            admin_subject = f"user-org1-{admin_role.value}"

            r = _request(
                base_url,
                "POST",
                collection_path(org1_id, project1_id),
                principal=admin_header,
                payload={
                    "agent_manifest_id": str(locked_manifest_id),
                    "purpose": "final",
                    "name": f"{admin_label} self-review suite",
                    "grading_logic_version": 1,
                },
            )
            check(
                "FTF-0601",
                f"round-3 (1): a {admin_label} actor can create a FINAL suite (201)",
                r.status == 201,
            )
            admin_suite = r.json() if r.status == 201 else {}
            admin_suite_id = admin_suite.get("id")

            r = _request(
                base_url,
                "PATCH",
                item_path(org1_id, project1_id, admin_suite_id),
                principal=admin_header,
                payload={"reviewer_id": admin_subject},
            )
            check(
                "FTF-0601",
                f"round-3 (1): the {admin_label} actor assigning itself as "
                "reviewer succeeds at PATCH time (200) -- it is a real "
                "member (itself)",
                r.status == 200,
            )

            r = _request(
                base_url,
                "POST",
                lock_path(org1_id, project1_id, admin_suite_id),
                principal=admin_header,
            )
            check(
                "FTF-0601",
                f"round-3 (1): locking a FINAL suite self-reviewed by this "
                f"same {admin_label} actor is refused (409) -- independence "
                "holds for every write role, not only evaluator (F19)",
                r.status == 409,
            )

        # -- round-3 (2) / F06: a non-member reviewer_id on POST create is
        # refused (400), not only on PATCH (already covered above) --
        r = _request(
            base_url,
            "POST",
            collection_path(org1_id, project1_id),
            principal=org1_evaluator,
            payload={
                "agent_manifest_id": str(locked_manifest_id),
                "purpose": "development",
                "name": "POST with non-member reviewer",
                "grading_logic_version": 1,
                "reviewer_id": "totally-fake-nobody-on-create",
            },
        )
        check(
            "FTF-0601",
            "round-3 (2) / F06: POST create with a non-member reviewer_id "
            "is refused (400), not silently accepted and not a 500/409",
            r.status == 400,
        )

        # -- round-3 F19 fault injection: temporarily add an additive
        # independence bypass keyed on the *performing actor's* own
        # identity ending in 'admin' (exactly the identity shape the two
        # admin write roles above genuinely have: 'workspace_admin'/
        # 'organization_admin') and prove, through the real HTTP router
        # (never a direct CRUD call -- this must exercise the exact
        # production request path) that this genuinely regresses the
        # previously clean 409 -- then restore the module file
        # byte-identical.
        original_crud_source = _CRUD_MODULE_PATH.read_bytes()
        crud_text = original_crud_source.decode("utf-8")
        f19_needle = (
            "if suite.purpose == PURPOSE_FINAL and reviewer_normalized == creator_normalized:"
        )
        check(
            "FTF-0601",
            "F19 fault injection target line is present in the real module",
            f19_needle in crud_text,
        )
        f19_mutated = crud_text.replace(
            f19_needle,
            "if suite.purpose == PURPOSE_FINAL and reviewer_normalized == "
            'creator_normalized and not actor.actor_id.endswith("admin"):',
        )
        check(
            "FTF-0601",
            "F19 fault injection actually changed the module source",
            f19_mutated != crud_text,
        )

        f19_actor_id = f"user-org1-{Role.ORGANIZATION_ADMIN.value}"
        f19_tenant_context = TenantContext(organization_id=org1_id)
        with tenant_scoped_session_for(session_factory, f19_tenant_context) as f19_session:
            f19_next_version = (
                f19_session.execute(
                    select(func.coalesce(func.max(EvaluationSuite.version), 0)).where(
                        EvaluationSuite.project_id == project1_id
                    )
                ).scalar_one()
                + 1
            )
            f19_suite = EvaluationSuite(
                organization_id=org1_id,
                project_id=project1_id,
                agent_manifest_id=locked_manifest_id,
                version=f19_next_version,
                status="draft",
                purpose=PURPOSE_FINAL,
                name="F19 fault-injection admin self-review suite",
                grading_logic_version=1,
                reviewer_id=f19_actor_id,
                created_by=f19_actor_id,
            )
            f19_session.add(f19_suite)
            f19_session.flush()
            f19_suite_id = f19_suite.id

        def _lock_f19_suite_via_fresh_router() -> _Response:
            # Rebuilds the router fresh from whatever is currently loaded
            # in `_evaluation_suite_crud_module`/`_evaluation_suite_routes_module`
            # -- the real production wiring (require_role -> route ->
            # lock_evaluation_suite), never a direct CRUD call that would
            # skip the router's own exception-to-HTTP-status mapping.
            probe_app = FastAPI()
            probe_app.include_router(
                _evaluation_suite_routes_module.build_evaluation_suite_router(
                    session_factory,
                    principal_resolver=lambda req: (
                        AuthenticatedPrincipal(issuer=_ISSUER, subject=f19_actor_id)
                        if req.headers.get("x-test-principal") == "f19-probe"
                        else None
                    ),
                    role_lookup=lambda issuer, subject, organization_id: (
                        Role.ORGANIZATION_ADMIN
                        if subject == f19_actor_id and organization_id == org1_id
                        else None
                    ),
                )
            )
            with _running_app(probe_app) as probe_base_url:
                return _request(
                    probe_base_url,
                    "POST",
                    lock_path(org1_id, project1_id, f19_suite_id),
                    principal="f19-probe",
                )

        baseline_response = _lock_f19_suite_via_fresh_router()
        check(
            "FTF-0601",
            "F19 baseline: the real, unmutated module still refuses (409), "
            "through the real HTTP router, an admin-named actor's own "
            "self-review on a FINAL suite",
            baseline_response.status == 409,
        )

        try:
            _CRUD_MODULE_PATH.write_text(f19_mutated, encoding="utf-8")
            importlib.reload(_evaluation_suite_crud_module)
            importlib.reload(_evaluation_suite_routes_module)
            fault_response = _lock_f19_suite_via_fresh_router()
            check(
                "FTF-0601",
                "F19: an additive independence bypass keyed on an "
                f"admin-named actor's own identity (real response status "
                f"{fault_response.status}) genuinely regresses the "
                "previously clean 409 -- the real "
                "ck_evaluation_suites_independent_final_reviewer DB CHECK "
                "still blocks the underlying UPDATE, but surfaces as an "
                "unhandled 500 instead of the clean 409 this round's own "
                "3-actor-role coverage (round-3 (1)) proves is otherwise "
                "load-bearing for every write role, not only evaluator",
                fault_response.status != 409,
            )
        finally:
            _CRUD_MODULE_PATH.write_bytes(original_crud_source)
            importlib.reload(_evaluation_suite_crud_module)
            importlib.reload(_evaluation_suite_routes_module)
            restored_crud_source = _CRUD_MODULE_PATH.read_bytes()
            check(
                "FTF-0601",
                "the evaluation_suite_crud.py module file is restored "
                "byte-identical after F19 fault injection",
                restored_crud_source == original_crud_source,
            )
            check(
                "FTF-0601",
                "F19 post-restore: the reloaded, restored module once "
                "again refuses (409), through the real HTTP router, the "
                "identical admin-named self-review case",
                _lock_f19_suite_via_fresh_router().status == 409,
            )

        # -- L2 round-2: grading_logic_version above the Postgres Integer
        # column's own 4-byte signed range is refused on both POST and
        # PATCH (400), never reaching the database as a raw IntegerError --
        r = _request(
            base_url,
            "POST",
            collection_path(org1_id, project1_id),
            principal=org1_evaluator,
            payload={
                "agent_manifest_id": str(locked_manifest_id),
                "purpose": "development",
                "name": "Overflowing version",
                "grading_logic_version": 2147483648,
            },
        )
        check(
            "FTF-0601",
            "L2 round-2: grading_logic_version=2147483648 on POST is "
            "refused (400), not a 500 from a raw database IntegerError",
            r.status == 400,
        )

        r = _request(
            base_url,
            "PATCH",
            item_path(org1_id, project1_id, suite3_id),
            principal=org1_evaluator,
            payload={"grading_logic_version": 2147483648},
        )
        check(
            "FTF-0601",
            "L2 round-2: grading_logic_version=2147483648 on PATCH is " "refused (400)",
            r.status == 400,
        )

        # -- L2 round-2: a whitespace-only name is refused on both POST and
        # PATCH --
        r = _request(
            base_url,
            "POST",
            collection_path(org1_id, project1_id),
            principal=org1_evaluator,
            payload={
                "agent_manifest_id": str(locked_manifest_id),
                "purpose": "development",
                "name": "   ",
                "grading_logic_version": 1,
            },
        )
        check(
            "FTF-0601",
            "L2 round-2: a whitespace-only name on POST is refused (400)",
            r.status == 400,
        )

        r = _request(
            base_url,
            "PATCH",
            item_path(org1_id, project1_id, suite3_id),
            principal=org1_evaluator,
            payload={"name": "\t\n "},
        )
        check(
            "FTF-0601",
            "L2 round-2: a whitespace-only name on PATCH is refused (400)",
            r.status == 400,
        )

        # -- L2 round-2 defense-in-depth: a raw INSERT with a
        # whitespace-only reviewer_id (bypassing validate_reviewer_id_or_none
        # entirely) still refuses at the real lock_evaluation_suite's own
        # application-level check (409), not only at the database CHECK --
        with Session(owner_engine) as raw_session:
            raw_session.execute(
                text("SELECT set_config('app.current_organization_id', :org_id, true)"),
                {"org_id": str(org1_id)},
            )
            raw_suite_id = raw_session.execute(
                text(
                    "INSERT INTO evaluation_suites "
                    "(id, organization_id, project_id, agent_manifest_id, version, status, "
                    "purpose, name, grading_logic_version, created_by, reviewer_id) "
                    "VALUES (:id, :org_id, :project_id, :manifest_id, 96, 'draft', "
                    "'development', 'raw whitespace reviewer', 1, 'creator', '   ') "
                    "RETURNING id"
                ),
                {
                    "id": str(uuid.uuid4()),
                    "org_id": str(org1_id),
                    "project_id": str(project1_id),
                    "manifest_id": str(locked_manifest_id),
                },
            ).scalar_one()
            raw_session.commit()

        r = _request(
            base_url,
            "POST",
            lock_path(org1_id, project1_id, raw_suite_id),
            principal=org1_evaluator,
        )
        check(
            "FTF-0601",
            "L2 round-2 defense-in-depth: locking a raw-inserted row whose "
            "reviewer_id is whitespace-only (bypassing application "
            "validation entirely) is refused (409) by "
            "lock_evaluation_suite's own real check",
            r.status == 409,
        )

        # -- get one / list --
        r = _request(
            base_url, "GET", item_path(org1_id, project1_id, suite1_id), principal=org1_admin
        )
        check("FTF-0601", "getting the real suite row by ID succeeds (200)", r.status == 200)

        r = _request(
            base_url, "GET", item_path(org1_id, project1_id, uuid.uuid4()), principal=org1_admin
        )
        check("FTF-0601", "getting a nonexistent suite ID is refused (404)", r.status == 404)

        r = _request(base_url, "GET", collection_path(org1_id, project1_id), principal=org1_admin)
        listed = r.json() if r.status == 200 else []
        check(
            "FTF-0601",
            "listing shows all seven real suites for project1 (the two "
            "original golden-path suites, the round-2 padded-self-review "
            "suite, the round-3 workspace_admin/organization_admin "
            "self-review suites, the F19 fault-injection probe suite, and "
            "the raw-inserted defense-in-depth row), ordered by version",
            isinstance(listed, list)
            and len(listed) == 7
            and [s["version"] for s in listed] == [1, 2, 3, 4, 5, 6, 96],
        )

    # -- Real database-level defense-in-depth: the CHECK constraints catch
    # a bypassing raw INSERT even when the ORM-level check is skipped
    # entirely --
    with Session(app_engine) as session:
        session.execute(
            text("SELECT set_config('app.current_organization_id', :org_id, true)"),
            {"org_id": str(org1_id)},
        )
        try:
            session.execute(
                text(
                    "INSERT INTO evaluation_suites "
                    "(organization_id, project_id, agent_manifest_id, version, status, "
                    "purpose, name, grading_logic_version, created_by) "
                    "VALUES (:org_id, :project_id, :manifest_id, 99, 'draft', 'mixed', "
                    "'raw insert', 1, 'raw')"
                ),
                {
                    "org_id": str(org1_id),
                    "project_id": str(project1_id),
                    "manifest_id": str(locked_manifest_id),
                },
            )
            session.commit()
            check(
                "FTF-0601",
                "negative_acceptance/defense-in-depth: a raw INSERT with "
                "purpose='mixed' is refused directly by the real "
                "ck_evaluation_suites_purpose CHECK constraint",
                False,
            )
        except DBAPIError:
            session.rollback()
            check(
                "FTF-0601",
                "negative_acceptance/defense-in-depth: a raw INSERT with "
                "purpose='mixed' is refused directly by the real "
                "ck_evaluation_suites_purpose CHECK constraint",
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
                    "INSERT INTO evaluation_suites "
                    "(organization_id, project_id, agent_manifest_id, version, status, "
                    "purpose, name, grading_logic_version, created_by, reviewer_id, "
                    "locked_at) "
                    "VALUES (:org_id, :project_id, :manifest_id, 98, 'locked', 'final', "
                    "'raw self-review', 1, 'same-person', 'same-person', now())"
                ),
                {
                    "org_id": str(org1_id),
                    "project_id": str(project1_id),
                    "manifest_id": str(locked_manifest_id),
                },
            )
            session.commit()
            check(
                "FTF-0601",
                "negative_acceptance/defense-in-depth: a raw INSERT of a "
                "locked final suite with reviewer_id == created_by is "
                "refused directly by ck_evaluation_suites_independent_final_reviewer",
                False,
            )
        except DBAPIError:
            session.rollback()
            check(
                "FTF-0601",
                "negative_acceptance/defense-in-depth: a raw INSERT of a "
                "locked final suite with reviewer_id == created_by is "
                "refused directly by ck_evaluation_suites_independent_final_reviewer",
                True,
            )

    # -- L2 finding regression, database level: a raw INSERT of a locked
    # suite with reviewer_id='' (not NULL -- the real gap the old
    # ck_evaluation_suites_reviewer_present_when_locked CHECK left open,
    # since '' satisfies IS NOT NULL) is refused directly by the real,
    # fixed CHECK constraint -- never relying on application code alone.
    with Session(app_engine) as session:
        session.execute(
            text("SELECT set_config('app.current_organization_id', :org_id, true)"),
            {"org_id": str(org1_id)},
        )
        try:
            session.execute(
                text(
                    "INSERT INTO evaluation_suites "
                    "(organization_id, project_id, agent_manifest_id, version, status, "
                    "purpose, name, grading_logic_version, created_by, reviewer_id, "
                    "locked_at) "
                    "VALUES (:org_id, :project_id, :manifest_id, 97, 'locked', "
                    "'development', 'raw empty reviewer', 1, 'creator', '', now())"
                ),
                {
                    "org_id": str(org1_id),
                    "project_id": str(project1_id),
                    "manifest_id": str(locked_manifest_id),
                },
            )
            session.commit()
            check(
                "FTF-0601",
                "negative_acceptance/defense-in-depth: a raw INSERT of a "
                "locked suite with reviewer_id='' is refused directly by "
                "the real, fixed ck_evaluation_suites_reviewer_present_when_locked CHECK",
                False,
            )
        except DBAPIError:
            session.rollback()
            check(
                "FTF-0601",
                "negative_acceptance/defense-in-depth: a raw INSERT of a "
                "locked suite with reviewer_id='' is refused directly by "
                "the real, fixed ck_evaluation_suites_reviewer_present_when_locked CHECK",
                True,
            )

    # -- Grant-surface proof: sovra_app genuinely cannot DELETE an
    # evaluation_suites row directly --
    if suite1_id:
        with Session(app_engine) as session:
            session.execute(
                text("SELECT set_config('app.current_organization_id', :org_id, true)"),
                {"org_id": str(org1_id)},
            )
            try:
                session.execute(
                    text("DELETE FROM evaluation_suites WHERE id = :id"), {"id": suite1_id}
                )
                session.commit()
                check(
                    "FTF-0601",
                    "negative_acceptance/grant-surface: the restricted "
                    "sovra_app role cannot DELETE an evaluation_suites row directly",
                    False,
                )
            except DBAPIError:
                session.rollback()
                check(
                    "FTF-0601",
                    "negative_acceptance/grant-surface: the restricted "
                    "sovra_app role cannot DELETE an evaluation_suites row directly",
                    True,
                )

    if FAILURES:
        print(f"FTF-0601 real EvaluationSuite schema/API proof: {len(FAILURES)} failed")
        for failure in FAILURES:
            print(f"  {failure}")
        sys.exit(1)

    print("FTF-0601 real EvaluationSuite schema/API proof: all checks passed")
    sys.exit(0)


if __name__ == "__main__":
    main()
