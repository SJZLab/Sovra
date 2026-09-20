#!/usr/bin/env python3
"""FTF-0401 -- real Postgres Project schema/API proof.

Proves this task's own acceptance clause 1 ("Create/edit agentic or
generic project with versioned security/budget/owner settings and saved
UI state") and negative_acceptance ("Agentic project without approved
target/runtime remains setup-incomplete; no silent generic downgrade")
against a real, live PostgreSQL server -- never a mocked connection or an
in-memory SQLite stand-in (SQLite has no row-level security at all, so a
passing test against it would prove nothing about this task's actual
acceptance criterion).

Two real organizations (org1/org2), each with a real workspace, are seeded
as the migration-owner role; every request below runs through a real
uvicorn server, with the router's own database session bound to the real,
distinct, least-privileged `sovra_app` role FTF-0102's migration creates
(never the migration-owner role -- confirmed directly below), exactly like
`tests/integration/test_ftf_0301_trainable_artifact_schema_api.py` already
established for a different resource.

Requires a real Postgres reachable at `SOVRA_DATABASE_URL` (the migration
owner role) plus `SOVRA_APP_DB_PASSWORD`, matching every prior integration
test's own environment contract exactly. This repo's interactive dev
sandbox has no container-registry egress (same finding as every prior
integration-tested task) -- this evidence was instead produced locally
against a directly-installed PostgreSQL 16 cluster, and is wired into CI's
dedicated `project-schema-api-integration` job (a GitHub Actions
`services: postgres` container, real registry egress on the hosted
runner).

Run directly (against an already-migrated or unmigrated database; this
script runs the migration itself):
    SOVRA_DATABASE_URL=postgresql+psycopg://<migration-role>@host/db \\
    SOVRA_APP_DB_PASSWORD=<app-role-password> \\
    python3 tests/integration/test_ftf_0401_project_schema_api.py
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
from app.auth.resource_authorization import authorize_resource_ownership  # noqa: E402
from app.auth.tenant_isolation_query_guards import TenantContextError  # noqa: E402
from app.db.tenant_context import TenantContext  # noqa: E402
from app.projects.project_routes import build_project_router  # noqa: E402
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


# -- Fixture: two organizations (each with a workspace), one workspace_admin
# and one evaluator per organization --------------------------------------

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
        "FTF-0401",
        f"alembic upgrade head runs cleanly (exit {migration_result.returncode})\n"
        f"stdout: {migration_result.stdout}\nstderr: {migration_result.stderr}",
        migration_result.returncode == 0,
    )
    if FAILURES:
        print(f"FTF-0401 real Project schema/API proof: {len(FAILURES)} failed")
        for failure in FAILURES:
            print(f"  {failure}")
        sys.exit(1)

    owner_engine = create_engine(migration_url)
    app_url = _app_role_url(migration_url, app_password)
    app_engine = create_engine(app_url)

    check(
        "FTF-0401",
        "the router's own database session is built from the restricted "
        "sovra_app role's connection URL, never the migration-owner role",
        app_engine.url.username == APP_ROLE_NAME
        and app_engine.url.username != owner_engine.url.username,
    )

    # -- Seed two real, distinct tenants + one workspace each (migration-owner
    # role, seeding only) --
    with Session(owner_engine) as session:
        org1 = Organization(name="Acme 0401", slug=f"acme-0401-{uuid.uuid4().hex[:8]}")
        org2 = Organization(name="Globex 0401", slug=f"globex-0401-{uuid.uuid4().hex[:8]}")
        session.add_all([org1, org2])
        session.flush()
        ws1 = Workspace(organization_id=org1.id, name="Acme Main", slug="main")
        ws2 = Workspace(organization_id=org2.id, name="Globex Main", slug="main")
        session.add_all([ws1, ws2])
        session.commit()
        org1_id, org2_id = org1.id, org2.id
        ws1_id, ws2_id = ws1.id, ws2.id

    fixture = _Fixture({"org1": org1_id, "org2": org2_id})
    session_factory = sessionmaker(bind=app_engine)
    app = FastAPI()
    app.include_router(
        build_project_router(
            session_factory,
            principal_resolver=fixture.principal_resolver,
            role_lookup=fixture.role_lookup,
        )
    )

    org1_wa = fixture.header_for("org1", Role.WORKSPACE_ADMIN)
    org1_evaluator = fixture.header_for("org1", Role.EVALUATOR)
    org2_wa = fixture.header_for("org2", Role.WORKSPACE_ADMIN)

    def collection_path(org_id: uuid.UUID) -> str:
        return f"/api/v1/organizations/{org_id}/projects"

    def item_path(org_id: uuid.UUID, project_id: object) -> str:
        return f"{collection_path(org_id)}/{project_id}"

    with _running_app(app) as base_url:
        # -- Unauthenticated / role-boundary denial --
        r = _request(base_url, "GET", collection_path(org1_id))
        check("FTF-0401", "an unauthenticated list request is denied (401)", r.status == 401)

        r = _request(base_url, "GET", collection_path(org1_id), principal=org1_evaluator)
        check(
            "FTF-0401",
            "an evaluator (a role Section 45 names, with no dedicated restrictive "
            "read sentence for projects) can read (200)",
            r.status == 200,
        )

        r = _request(
            base_url,
            "POST",
            collection_path(org1_id),
            principal=org1_evaluator,
            payload={
                "workspace_id": str(ws1_id),
                "name": "should-be-denied",
                "project_type": "generic_model_tuning",
            },
        )
        check(
            "FTF-0401",
            "an evaluator is denied create (403) -- write is workspace_admin/"
            "organization_admin only (Section 45's own role table)",
            r.status == 403,
        )

        # -- negative_acceptance: agentic_product with no agent_objective fails --
        r = _request(
            base_url,
            "POST",
            collection_path(org1_id),
            principal=org1_wa,
            payload={
                "workspace_id": str(ws1_id),
                "name": "agentic-missing-objective",
                "project_type": "agentic_product",
            },
        )
        check(
            "FTF-0401",
            "negative_acceptance: an agentic_product project with no "
            "agent_objective fails at creation (400)",
            r.status == 400,
        )

        # -- negative_acceptance: generic project with an agent_objective fails --
        r = _request(
            base_url,
            "POST",
            collection_path(org1_id),
            principal=org1_wa,
            payload={
                "workspace_id": str(ws1_id),
                "name": "generic-with-objective",
                "project_type": "generic_model_tuning",
                "agent_objective": "should not be allowed",
            },
        )
        check(
            "FTF-0401",
            "negative_acceptance: a generic_model_tuning project carrying an "
            "agent_objective fails at creation (400) -- genuinely "
            "distinguishable kinds, not just a label",
            r.status == 400,
        )

        # -- workspace ownership: a workspace_id from a different organization
        # is refused identically to a missing one (404) --
        r = _request(
            base_url,
            "POST",
            collection_path(org1_id),
            principal=org1_wa,
            payload={
                "workspace_id": str(ws2_id),
                "name": "cross-org-workspace",
                "project_type": "generic_model_tuning",
            },
        )
        check(
            "FTF-0401",
            "creating a project against a workspace_id belonging to a "
            "different organization is refused (404), identical to a "
            "missing workspace_id",
            r.status == 404,
        )

        # -- Real create: generic_model_tuning starts active --
        r = _request(
            base_url,
            "POST",
            collection_path(org1_id),
            principal=org1_wa,
            payload={
                "workspace_id": str(ws1_id),
                "name": "generic-tuning-project",
                "project_type": "generic_model_tuning",
                "description": "Fine-tune a small classifier.",
            },
        )
        check("FTF-0401", "a real, valid generic project create succeeds (201)", r.status == 201)
        generic_project = r.json() if r.status == 201 else {}
        check(
            "FTF-0401",
            "a generic_model_tuning project starts active",
            isinstance(generic_project, dict)
            and generic_project.get("status") == "active"
            and generic_project.get("row_version") == 1
            and generic_project.get("workspace_id") == str(ws1_id),
        )

        # -- Real create: agentic_product starts setup_incomplete, never active --
        r = _request(
            base_url,
            "POST",
            collection_path(org1_id),
            principal=org1_wa,
            payload={
                "workspace_id": str(ws1_id),
                "name": "agentic-product-project",
                "project_type": "agentic_product",
                "agent_objective": "Automate PR review comments.",
            },
        )
        check("FTF-0401", "a real, valid agentic project create succeeds (201)", r.status == 201)
        agentic_project = r.json() if r.status == 201 else {}
        check(
            "FTF-0401",
            "negative_acceptance: an agentic_product project always starts "
            "setup_incomplete, never active -- no silent generic downgrade",
            isinstance(agentic_project, dict)
            and agentic_project.get("status") == "setup_incomplete"
            and agentic_project.get("agent_objective") == "Automate PR review comments.",
        )
        agentic_project_id = agentic_project.get("id")

        # -- project_type is immutable: submitting it in a PATCH has no
        # effect on the real, persisted value --
        r = _request(
            base_url,
            "PATCH",
            item_path(org1_id, agentic_project_id),
            principal=org1_wa,
            payload={
                "project_type": "generic_model_tuning",
                "description": "still agentic",
                "expected_row_version": 1,
            },
        )
        check("FTF-0401", "the PATCH itself succeeds (200)", r.status == 200)
        patched = r.json() if r.status == 200 else {}
        check(
            "FTF-0401",
            "acceptance: project_type is immutable -- a PATCH cannot silently "
            "downgrade an agentic project to generic, even if the field is "
            "submitted",
            isinstance(patched, dict)
            and patched.get("project_type") == "agentic_product"
            and patched.get("status") == "setup_incomplete"
            and patched.get("row_version") == 2,
        )

        # -- optimistic concurrency: a stale expected_row_version is refused --
        r = _request(
            base_url,
            "PATCH",
            item_path(org1_id, agentic_project_id),
            principal=org1_wa,
            payload={"description": "stale write", "expected_row_version": 1},
        )
        check(
            "FTF-0401",
            "acceptance: a stale expected_row_version is refused (409) -- "
            "real optimistic concurrency, not a last-write-wins race",
            r.status == 409,
        )

        # -- save ui_state: succeeds, does not bump row_version --
        r = _request(
            base_url,
            "POST",
            item_path(org1_id, agentic_project_id) + "/ui-state",
            principal=org1_wa,
            payload={"ui_state": {"wizard_step": 2, "draft_target_repo": "acme/app"}},
        )
        check("FTF-0401", "a real ui_state save succeeds (200)", r.status == 200)
        with_state = r.json() if r.status == 200 else {}
        check(
            "FTF-0401",
            "acceptance: saved UI state round-trips exactly, and saving it "
            "does not bump row_version (not a versioned setting)",
            isinstance(with_state, dict)
            and with_state.get("ui_state") == {"wizard_step": 2, "draft_target_repo": "acme/app"}
            and with_state.get("row_version") == 2,
        )

        # -- IDOR: org2's own workspace_admin cannot see/mutate org1's project --
        r = _request(base_url, "GET", item_path(org2_id, agentic_project_id), principal=org2_wa)
        check(
            "FTF-0401",
            "the core IDOR case: a real project belonging to org1, requested "
            "under org2's own path by a real org2 workspace_admin, is denied "
            "(404) identically to missing",
            r.status == 404,
        )
        r = _request(
            base_url,
            "PATCH",
            item_path(org2_id, agentic_project_id),
            principal=org2_wa,
            payload={"description": "hijacked", "expected_row_version": 2},
        )
        check(
            "FTF-0401",
            "the same IDOR case for PATCH: denied (404) identically to missing",
            r.status == 404,
        )
        r = _request(base_url, "DELETE", item_path(org2_id, agentic_project_id), principal=org2_wa)
        check(
            "FTF-0401",
            "the same IDOR case for archive: denied (404) identically to missing",
            r.status == 404,
        )

        r = _request(base_url, "GET", item_path(org1_id, agentic_project_id), principal=org1_wa)
        still_there = r.json() if r.status == 200 else {}
        check(
            "FTF-0401",
            "the project is genuinely untouched after org2's denied mutation "
            "attempts -- still setup_incomplete, still row_version 2",
            r.status == 200
            and still_there.get("status") == "setup_incomplete"
            and still_there.get("row_version") == 2,
        )

        # -- Real archive (idempotent) --
        r = _request(base_url, "DELETE", item_path(org1_id, agentic_project_id), principal=org1_wa)
        check(
            "FTF-0401", "a real archive by the owning organization succeeds (200)", r.status == 200
        )
        archived = r.json() if r.status == 200 else {}
        check(
            "FTF-0401",
            "the archive genuinely transitions status and sets archived_at",
            isinstance(archived, dict)
            and archived.get("status") == "archived"
            and archived.get("archived_at") is not None,
        )
        first_archived_at = archived.get("archived_at")

        r = _request(base_url, "DELETE", item_path(org1_id, agentic_project_id), principal=org1_wa)
        check(
            "FTF-0401",
            "acceptance: archiving an already-archived project is idempotent "
            "-- still 200, archived_at does not move",
            r.status == 200 and r.json().get("archived_at") == first_archived_at,
        )

        r = _request(
            base_url,
            "PATCH",
            item_path(org1_id, agentic_project_id),
            principal=org1_wa,
            payload={"description": "edit after archive", "expected_row_version": 2},
        )
        check(
            "FTF-0401",
            "acceptance: editing an archived project is refused (409)",
            r.status == 409,
        )

        # -- Uniqueness: a second create with the same name in the same
        # workspace is rejected by the real DB constraint --
        r = _request(
            base_url,
            "POST",
            collection_path(org1_id),
            principal=org1_wa,
            payload={
                "workspace_id": str(ws1_id),
                "name": "unique-name-check",
                "project_type": "generic_model_tuning",
            },
        )
        check("FTF-0401", "the first create with this name succeeds (201)", r.status == 201)
        r = _request(
            base_url,
            "POST",
            collection_path(org1_id),
            principal=org1_wa,
            payload={
                "workspace_id": str(ws1_id),
                "name": "unique-name-check",
                "project_type": "generic_model_tuning",
            },
        )
        check(
            "FTF-0401",
            "a duplicate name within the same workspace is rejected by the "
            "real database unique constraint (a real DB integrity error, "
            "never a silent duplicate)",
            r.status >= 400,
        )

    # -- acceptance: missing tenant context is refused, not treated as "0 rows" --
    with Session(app_engine) as session:
        raw_context = TenantContext(organization_id=org1_id)
        try:
            authorize_resource_ownership(
                session,
                raw_context,
                Project.__table__,
                Project.__table__.c.id,
                Project.__table__.c.organization_id,
                uuid.UUID(agentic_project_id) if agentic_project_id else uuid.uuid4(),
            )
            check(
                "FTF-0401",
                "acceptance: a session with no tenant context ever bound is "
                "refused (TenantContextError), not treated as an ordinary "
                "zero-row/foreign-tenant denial",
                False,
            )
        except TenantContextError:
            check(
                "FTF-0401",
                "acceptance: a session with no tenant context ever bound is "
                "refused (TenantContextError), not treated as an ordinary "
                "zero-row/foreign-tenant denial",
                True,
            )

    # -- Grant-surface proof: sovra_app genuinely cannot DELETE the row
    # directly (archive is a soft state transition, never a real row
    # deletion). --
    if agentic_project_id:
        with Session(app_engine) as session:
            set_tenant_context_sql = text(
                "SELECT set_config('app.current_organization_id', :org_id, true)"
            )
            session.execute(set_tenant_context_sql, {"org_id": str(org1_id)})
            try:
                session.execute(
                    text("DELETE FROM projects WHERE id = :id"),
                    {"id": agentic_project_id},
                )
                session.commit()
                check(
                    "FTF-0401",
                    "negative_acceptance/grant-surface: the restricted "
                    "sovra_app role cannot DELETE a projects row directly -- "
                    "archive is a soft state transition only",
                    False,
                )
            except DBAPIError:
                session.rollback()
                check(
                    "FTF-0401",
                    "negative_acceptance/grant-surface: the restricted "
                    "sovra_app role cannot DELETE a projects row directly -- "
                    "archive is a soft state transition only",
                    True,
                )

    if FAILURES:
        print(f"FTF-0401 real Project schema/API proof: {len(FAILURES)} failed")
        for failure in FAILURES:
            print(f"  {failure}")
        sys.exit(1)

    print("FTF-0401 real Project schema/API proof: all checks passed")
    sys.exit(0)


if __name__ == "__main__":
    main()
