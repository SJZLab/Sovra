#!/usr/bin/env python3
"""FTF-0404 -- real Postgres Tool schema CRUD proof.

Proves this task's own acceptance clause 1 ("Create immutable-version tool
schemas with side-effect policy, evidence and project ownership") and
negative_acceptance ("Editing used tool schema creates new version and
drift, never changes historical manifest in place") against a real, live
PostgreSQL server -- never a mocked connection or an in-memory SQLite
stand-in (SQLite has no row-level security at all, so a passing test
against it would prove nothing about this task's actual acceptance
criterion).

Two real organizations (org1/org2), each with a real workspace and a real
project, are seeded as the migration-owner role; every request below runs
through a real uvicorn server, with the router's own database session
bound to the real, distinct, least-privileged `sovra_app` role FTF-0102's
migration creates (never the migration-owner role -- confirmed directly
below), exactly like `tests/integration/test_ftf_0401_project_schema_api.py`
already established for a different resource.

Requires a real Postgres reachable at `SOVRA_DATABASE_URL` (the migration
owner role) plus `SOVRA_APP_DB_PASSWORD`, matching every prior integration
test's own environment contract exactly. This repo's interactive dev
sandbox has no container-registry egress (same finding as every prior
integration-tested task) -- this evidence was instead produced locally
against a directly-installed PostgreSQL 16 cluster, and is wired into CI's
dedicated `tool-schema-crud-integration` job (a GitHub Actions
`services: postgres` container, real registry egress on the hosted
runner).

Run directly (against an already-migrated or unmigrated database; this
script runs the migration itself):
    SOVRA_DATABASE_URL=postgresql+psycopg://<migration-role>@host/db \\
    SOVRA_APP_DB_PASSWORD=<app-role-password> \\
    python3 tests/integration/test_ftf_0404_tool_schema_crud.py
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
from app.projects.project_schema import Project  # noqa: E402
from app.tools.tool_definition_routes import build_tool_definition_router  # noqa: E402
from app.tools.tool_definition_schema import ToolDefinition  # noqa: E402

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
# workspace_admin and one evaluator per organization ------------------------

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
        "FTF-0404",
        f"alembic upgrade head runs cleanly (exit {migration_result.returncode})\n"
        f"stdout: {migration_result.stdout}\nstderr: {migration_result.stderr}",
        migration_result.returncode == 0,
    )
    if FAILURES:
        print(f"FTF-0404 real Tool schema CRUD proof: {len(FAILURES)} failed")
        for failure in FAILURES:
            print(f"  {failure}")
        sys.exit(1)

    owner_engine = create_engine(migration_url)
    app_url = _app_role_url(migration_url, app_password)
    app_engine = create_engine(app_url)

    check(
        "FTF-0404",
        "the router's own database session is built from the restricted "
        "sovra_app role's connection URL, never the migration-owner role",
        app_engine.url.username == APP_ROLE_NAME
        and app_engine.url.username != owner_engine.url.username,
    )

    # -- Seed two real, distinct tenants, each with one workspace and one
    # real project (migration-owner role, seeding only) --
    with Session(owner_engine) as session:
        org1 = Organization(name="Acme 0404", slug=f"acme-0404-{uuid.uuid4().hex[:8]}")
        org2 = Organization(name="Globex 0404", slug=f"globex-0404-{uuid.uuid4().hex[:8]}")
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
            project_type="generic_model_tuning",
            status="active",
            security_profile="standard",
            row_version=1,
            created_by="seed",
        )
        project1b = Project(
            organization_id=org1.id,
            workspace_id=ws1.id,
            name="acme-project-2",
            project_type="generic_model_tuning",
            status="active",
            security_profile="standard",
            row_version=1,
            created_by="seed",
        )
        project2 = Project(
            organization_id=org2.id,
            workspace_id=ws2.id,
            name="globex-project",
            project_type="generic_model_tuning",
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
    app = FastAPI()
    app.include_router(
        build_tool_definition_router(
            session_factory,
            principal_resolver=fixture.principal_resolver,
            role_lookup=fixture.role_lookup,
        )
    )

    org1_wa = fixture.header_for("org1", Role.WORKSPACE_ADMIN)
    org1_evaluator = fixture.header_for("org1", Role.EVALUATOR)
    org2_wa = fixture.header_for("org2", Role.WORKSPACE_ADMIN)

    def collection_path(org_id: uuid.UUID, project_id: uuid.UUID) -> str:
        return f"/api/v1/organizations/{org_id}/projects/{project_id}/tools"

    def item_path(org_id: uuid.UUID, project_id: uuid.UUID, tool_id: object) -> str:
        return f"{collection_path(org_id, project_id)}/{tool_id}"

    input_schema = {"type": "object", "properties": {"order_id": {"type": "string"}}}
    output_schema = {"type": "object", "properties": {"status": {"type": "string"}}}

    with _running_app(app) as base_url:
        # -- Unauthenticated / role-boundary denial --
        r = _request(base_url, "GET", collection_path(org1_id, project1_id))
        check("FTF-0404", "an unauthenticated list request is denied (401)", r.status == 401)

        r = _request(
            base_url, "GET", collection_path(org1_id, project1_id), principal=org1_evaluator
        )
        check(
            "FTF-0404",
            "an evaluator (a role Section 45 names, with no dedicated restrictive "
            "read sentence for tool schemas) can read (200)",
            r.status == 200,
        )

        r = _request(
            base_url,
            "POST",
            collection_path(org1_id, project1_id),
            principal=org1_evaluator,
            payload={
                "name": "should_be_denied",
                "input_schema": input_schema,
                "output_schema": output_schema,
                "side_effect_class": "read_only",
            },
        )
        check(
            "FTF-0404",
            "an evaluator is denied create (403) -- write is workspace_admin/"
            "organization_admin only (Section 45's own role table)",
            r.status == 403,
        )

        # -- project ownership: a project_id from a different organization
        # is refused identically to a missing one (404) --
        r = _request(
            base_url,
            "POST",
            collection_path(org1_id, project2_id),
            principal=org1_wa,
            payload={
                "name": "cross_org_project",
                "input_schema": input_schema,
                "output_schema": output_schema,
                "side_effect_class": "read_only",
            },
        )
        check(
            "FTF-0404",
            "creating a tool against a project_id belonging to a different "
            "organization is refused (404), identical to a missing project_id",
            r.status == 404,
        )

        # -- Real create: version 1, no lineage yet --
        r = _request(
            base_url,
            "POST",
            collection_path(org1_id, project1_id),
            principal=org1_wa,
            payload={
                "name": "lookup_order",
                "description": "Looks up an order by ID.",
                "input_schema": input_schema,
                "output_schema": output_schema,
                "side_effect_class": "read_only",
            },
        )
        check("FTF-0404", "a real, valid tool create succeeds (201)", r.status == 201)
        tool_v1 = r.json() if r.status == 201 else {}
        check(
            "FTF-0404",
            "acceptance: a fresh tool starts at version 1, with no lineage/"
            "supersession yet, and a real, present schema_hash",
            isinstance(tool_v1, dict)
            and tool_v1.get("version") == 1
            and tool_v1.get("previous_version_id") is None
            and tool_v1.get("superseded_at") is None
            and bool(tool_v1.get("schema_hash")),
        )
        tool_v1_id = tool_v1.get("id")

        # -- negative_acceptance: a duplicate name in the same project while
        # a current version exists is rejected by the real DB constraint --
        r = _request(
            base_url,
            "POST",
            collection_path(org1_id, project1_id),
            principal=org1_wa,
            payload={
                "name": "lookup_order",
                "input_schema": input_schema,
                "output_schema": output_schema,
                "side_effect_class": "read_only",
            },
        )
        check(
            "FTF-0404",
            "a duplicate current tool name within the same project is "
            "rejected by the real database's own partial unique constraint",
            r.status >= 400,
        )

        # -- acceptance: edit creates a genuinely new version; the historical
        # row's own content is left untouched --
        r = _request(
            base_url,
            "PATCH",
            item_path(org1_id, project1_id, tool_v1_id),
            principal=org1_wa,
            payload={
                "description": "Looks up an order by ID (v2, richer schema).",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "order_id": {"type": "string"},
                        "include_line_items": {"type": "boolean"},
                    },
                },
                "side_effect_class": "read_only",
            },
        )
        check("FTF-0404", "the edit itself succeeds (200)", r.status == 200)
        tool_v2 = r.json() if r.status == 200 else {}
        check(
            "FTF-0404",
            "acceptance: the edit produces version 2, threading previous_version_id "
            "back to version 1's own real ID",
            isinstance(tool_v2, dict)
            and tool_v2.get("version") == 2
            and tool_v2.get("previous_version_id") == tool_v1_id
            and tool_v2.get("superseded_at") is None,
        )
        check(
            "FTF-0404",
            "the edited version's schema_hash genuinely changed (real content change)",
            tool_v2.get("schema_hash") != tool_v1.get("schema_hash"),
        )
        tool_v2_id = tool_v2.get("id")

        r = _request(
            base_url, "GET", item_path(org1_id, project1_id, tool_v1_id), principal=org1_wa
        )
        historical_v1 = r.json() if r.status == 200 else {}
        check(
            "FTF-0404",
            "negative_acceptance: fetching the historical version 1 by its "
            "own ID still returns its exact original, byte-identical content "
            "-- never mutated in place by the edit",
            r.status == 200
            and historical_v1.get("description") == "Looks up an order by ID."
            and historical_v1.get("input_schema") == input_schema
            and historical_v1.get("schema_hash") == tool_v1.get("schema_hash")
            and historical_v1.get("version") == 1,
        )
        check(
            "FTF-0404",
            "the historical version 1 IS marked superseded -- the one real "
            "change an edit makes to the row it replaces",
            historical_v1.get("superseded_at") is not None,
        )

        # -- negative_acceptance: the historical (now-superseded) version
        # cannot itself be edited again --
        r = _request(
            base_url,
            "PATCH",
            item_path(org1_id, project1_id, tool_v1_id),
            principal=org1_wa,
            payload={"description": "trying to edit history directly"},
        )
        check(
            "FTF-0404",
            "negative_acceptance: attempting to edit the superseded version "
            "1 directly (by its own ID) is refused (409) -- only the "
            "current version can be edited",
            r.status == 409,
        )

        # -- list only shows the current version, not the superseded one --
        r = _request(base_url, "GET", collection_path(org1_id, project1_id), principal=org1_wa)
        listed = r.json() if r.status == 200 else []
        check(
            "FTF-0404",
            "listing shows exactly one row for this tool name -- the "
            "current version 2, not the superseded version 1",
            isinstance(listed, list)
            and sum(1 for t in listed if t.get("name") == "lookup_order") == 1
            and next(t for t in listed if t.get("name") == "lookup_order").get("version") == 2,
        )

        # -- cross-project scoping: the same organization's other project
        # cannot reach this tool via its own path (identical to missing) --
        r = _request(
            base_url, "GET", item_path(org1_id, project1b_id, tool_v2_id), principal=org1_wa
        )
        check(
            "FTF-0404",
            "a real tool belonging to project1, requested under a "
            "*different* project's own path in the same organization, is "
            "denied (404) identically to missing",
            r.status == 404,
        )

        # -- IDOR: org2's own workspace_admin cannot see/mutate org1's tool --
        r = _request(
            base_url, "GET", item_path(org2_id, project1_id, tool_v2_id), principal=org2_wa
        )
        check(
            "FTF-0404",
            "the core IDOR case: a real tool belonging to org1, requested "
            "under org2's own path by a real org2 workspace_admin, is "
            "denied (404) identically to missing",
            r.status == 404,
        )
        r = _request(
            base_url,
            "PATCH",
            item_path(org2_id, project1_id, tool_v2_id),
            principal=org2_wa,
            payload={"description": "hijacked"},
        )
        check(
            "FTF-0404",
            "the same IDOR case for PATCH: denied (404) identically to missing",
            r.status == 404,
        )

        r = _request(
            base_url, "GET", item_path(org1_id, project1_id, tool_v2_id), principal=org1_wa
        )
        still_there = r.json() if r.status == 200 else {}
        check(
            "FTF-0404",
            "the current version is genuinely untouched after org2's "
            "denied mutation attempt -- still version 2, still its own "
            "original description",
            r.status == 200
            and still_there.get("version") == 2
            and still_there.get("description") == "Looks up an order by ID (v2, richer schema).",
        )

    # -- acceptance: missing tenant context is refused, not treated as "0 rows" --
    with Session(app_engine) as session:
        raw_context = TenantContext(organization_id=org1_id)
        try:
            authorize_resource_ownership(
                session,
                raw_context,
                ToolDefinition.__table__,
                ToolDefinition.__table__.c.id,
                ToolDefinition.__table__.c.organization_id,
                uuid.UUID(tool_v2_id) if tool_v2_id else uuid.uuid4(),
            )
            check(
                "FTF-0404",
                "acceptance: a session with no tenant context ever bound is "
                "refused (TenantContextError), not treated as an ordinary "
                "zero-row/foreign-tenant denial",
                False,
            )
        except TenantContextError:
            check(
                "FTF-0404",
                "acceptance: a session with no tenant context ever bound is "
                "refused (TenantContextError), not treated as an ordinary "
                "zero-row/foreign-tenant denial",
                True,
            )

    # -- Grant-surface proof: sovra_app genuinely cannot DELETE a
    # tool_definitions row directly (never removed, only ever superseded). --
    if tool_v2_id:
        with Session(app_engine) as session:
            set_tenant_context_sql = text(
                "SELECT set_config('app.current_organization_id', :org_id, true)"
            )
            session.execute(set_tenant_context_sql, {"org_id": str(org1_id)})
            try:
                session.execute(
                    text("DELETE FROM tool_definitions WHERE id = :id"),
                    {"id": tool_v2_id},
                )
                session.commit()
                check(
                    "FTF-0404",
                    "negative_acceptance/grant-surface: the restricted "
                    "sovra_app role cannot DELETE a tool_definitions row "
                    "directly -- a version is never removed, only superseded",
                    False,
                )
            except DBAPIError:
                session.rollback()
                check(
                    "FTF-0404",
                    "negative_acceptance/grant-surface: the restricted "
                    "sovra_app role cannot DELETE a tool_definitions row "
                    "directly -- a version is never removed, only superseded",
                    True,
                )

    if FAILURES:
        print(f"FTF-0404 real Tool schema CRUD proof: {len(FAILURES)} failed")
        for failure in FAILURES:
            print(f"  {failure}")
        sys.exit(1)

    print("FTF-0404 real Tool schema CRUD proof: all checks passed")
    sys.exit(0)


if __name__ == "__main__":
    main()
