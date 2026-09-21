#!/usr/bin/env python3
"""FTF-0407 -- real Postgres critical-behavior-requirement proof.

Proves this task's own acceptance clause 1 ("Each critical responsibility
... links to deterministic outcome tests and runtime enforcement") and
negative_acceptance ("Unmeasurable or conflicting critical mandate blocks
activation") against a real, live PostgreSQL server, over a real uvicorn
HTTP round trip through both `build_tool_definition_router` (FTF-0404,
already CERTIFIED) and this task's own new, real, read-only
`build_critical_behavior_requirement_router` -- never a mocked connection
or an in-memory stand-in.

Same real Postgres/`sovra_app`-role discipline every prior integration
test in this repository already established (see
`tests/integration/test_ftf_0404_tool_schema_crud.py`'s own module
docstring). Requires a real Postgres reachable at `SOVRA_DATABASE_URL`
(the migration owner role) plus `SOVRA_APP_DB_PASSWORD`.

Run directly (against an already-migrated or unmigrated database; this
script runs the migration itself):
    SOVRA_DATABASE_URL=postgresql+psycopg://<migration-role>@host/db \\
    SOVRA_APP_DB_PASSWORD=<app-role-password> \\
    python3 tests/integration/test_ftf_0407_critical_behavior_requirements.py
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
from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import Session, sessionmaker  # noqa: E402

from app.auth.organization_workspace_schema import APP_ROLE_NAME, Organization, Workspace  # noqa: E402
from app.auth.rbac_middleware import AuthenticatedPrincipal, Role  # noqa: E402
from app.projects.project_schema import Project  # noqa: E402
from app.tools.critical_behavior_requirement_routes import (  # noqa: E402
    build_critical_behavior_requirement_router,
)
from app.tools.tool_definition_routes import build_tool_definition_router  # noqa: E402

FAILURES: list[str] = []


def check(criterion_id: str, description: str, condition: bool) -> None:
    if not condition:
        FAILURES.append(f"FAIL [{criterion_id}] {description}")


def _required_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        print(
            f"BLOCKED_EXTERNAL: {name} is not set -- this task's real "
            "enforcement/API evidence requires a live Postgres (migration "
            "owner connection). Not silently skipped: see this file's own "
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
        "FTF-0407",
        f"alembic upgrade head runs cleanly (exit {migration_result.returncode})\n"
        f"stdout: {migration_result.stdout}\nstderr: {migration_result.stderr}",
        migration_result.returncode == 0,
    )
    if FAILURES:
        print(f"FTF-0407 real critical-behavior-requirement proof: {len(FAILURES)} failed")
        for failure in FAILURES:
            print(f"  {failure}")
        sys.exit(1)

    owner_engine = create_engine(migration_url)
    app_url = _app_role_url(migration_url, app_password)
    app_engine = create_engine(app_url)

    with Session(owner_engine) as session:
        org1 = Organization(name="Acme 0407", slug=f"acme-0407-{uuid.uuid4().hex[:8]}")
        session.add(org1)
        session.flush()
        ws1 = Workspace(organization_id=org1.id, name="Acme Main", slug="main")
        session.add(ws1)
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
        session.add(project1)
        session.commit()
        org1_id = org1.id
        project1_id = project1.id

    fixture = _Fixture({"org1": org1_id})
    session_factory = sessionmaker(bind=app_engine)
    app = FastAPI()
    app.include_router(
        build_tool_definition_router(
            session_factory,
            principal_resolver=fixture.principal_resolver,
            role_lookup=fixture.role_lookup,
        )
    )
    app.include_router(
        build_critical_behavior_requirement_router(
            session_factory,
            principal_resolver=fixture.principal_resolver,
            role_lookup=fixture.role_lookup,
        )
    )

    org1_wa = fixture.header_for("org1", Role.WORKSPACE_ADMIN)
    org1_evaluator = fixture.header_for("org1", Role.EVALUATOR)

    def tools_path(org_id: uuid.UUID, project_id: uuid.UUID) -> str:
        return f"/api/v1/organizations/{org_id}/projects/{project_id}/tools"

    def requirements_path(org_id: uuid.UUID, project_id: uuid.UUID) -> str:
        return f"/api/v1/organizations/{org_id}/projects/{project_id}/requirements"

    input_schema = {"type": "object", "properties": {"order_id": {"type": "string"}}}
    output_schema = {"type": "object", "properties": {"status": {"type": "string"}}}

    with _running_app(app) as base_url:
        # -- negative_acceptance, "unmeasurable": is_critical=True on a
        # read_only tool is refused for real over a real HTTP round trip --
        r = _request(
            base_url,
            "POST",
            tools_path(org1_id, project1_id),
            principal=org1_wa,
            payload={
                "name": "lookup_order",
                "description": "Looks up an order by ID.",
                "input_schema": input_schema,
                "output_schema": output_schema,
                "side_effect_class": "read_only",
                "is_critical": True,
            },
        )
        check(
            "FTF-0407",
            "negative_acceptance: is_critical=True on a real read_only tool is "
            "refused (400) over a real HTTP create -- unmeasurable critical claim",
            r.status == 400,
        )

        # -- negative_acceptance, "conflicting": is_critical=True on a real
        # reversible_write tool with requires_confirmation=False is refused --
        r = _request(
            base_url,
            "POST",
            tools_path(org1_id, project1_id),
            principal=org1_wa,
            payload={
                "name": "update_order_status",
                "description": "Updates the status of an order.",
                "input_schema": input_schema,
                "output_schema": output_schema,
                "side_effect_class": "reversible_write",
                "requires_confirmation": False,
                "is_critical": True,
            },
        )
        check(
            "FTF-0407",
            "negative_acceptance: is_critical=True with requires_confirmation=False "
            "on a real-effect tool is refused (400) -- conflicting, unenforced claim",
            r.status == 400,
        )

        # -- acceptance: a genuinely enforced critical tool is created for real --
        r = _request(
            base_url,
            "POST",
            tools_path(org1_id, project1_id),
            principal=org1_wa,
            payload={
                "name": "update_order_status",
                "description": "Updates the status of an order.",
                "input_schema": input_schema,
                "output_schema": output_schema,
                "side_effect_class": "reversible_write",
                "requires_confirmation": True,
                "is_critical": True,
            },
        )
        check(
            "FTF-0407",
            "acceptance: is_critical=True with requires_confirmation=True on a "
            "real-effect tool is accepted (201)",
            r.status == 201,
        )

        # -- no regression: a plain read_only, non-critical tool (FTF-0404's own
        # certified default shape) still creates successfully --
        r = _request(
            base_url,
            "POST",
            tools_path(org1_id, project1_id),
            principal=org1_wa,
            payload={
                "name": "list_orders",
                "description": "Lists the caller's own orders.",
                "input_schema": input_schema,
                "output_schema": output_schema,
                "side_effect_class": "read_only",
            },
        )
        check(
            "FTF-0407",
            "no regression: a plain read_only, non-critical tool still creates (201)",
            r.status == 201,
        )

        # -- no regression: a high-risk, confirmed, non-critical tool (FTF-0406's
        # own certified shape) still creates successfully -- this task deliberately
        # does not force is_critical=True on a high-risk resolution --
        r = _request(
            base_url,
            "POST",
            tools_path(org1_id, project1_id),
            principal=org1_wa,
            payload={
                "name": "delete_account",
                "description": "Permanently deletes a user account.",
                "input_schema": input_schema,
                "output_schema": output_schema,
                "side_effect_class": "irreversible_write",
                "requires_confirmation": True,
            },
        )
        check(
            "FTF-0407",
            "no regression: a high-risk, confirmed, non-critical tool still creates "
            "(201) -- is_critical=False is never forced by this task",
            r.status == 201,
        )

        # -- real /requirements view: reads back the project's own current
        # tools, annotated with the real, already-enforced state --
        r = _request(
            base_url, "GET", requirements_path(org1_id, project1_id), principal=org1_evaluator
        )
        check(
            "FTF-0407",
            "the real /requirements route is reachable by a read role (200)",
            r.status == 200,
        )
        rows = r.json() if r.status == 200 else []
        by_name = {row.get("name"): row for row in rows} if isinstance(rows, list) else {}
        check(
            "FTF-0407",
            "the /requirements view lists exactly the three real tools created above",
            set(by_name) == {"update_order_status", "list_orders", "delete_account"},
        )
        check(
            "FTF-0407",
            "the critical, confirmed reversible_write tool is reported measurable "
            "and runtime_enforced",
            by_name.get("update_order_status", {}).get("is_critical") is True
            and by_name.get("update_order_status", {}).get("measurable") is True
            and by_name.get("update_order_status", {}).get("runtime_enforced") is True,
        )
        check(
            "FTF-0407",
            "the plain read_only tool is reported not measurable and not runtime_enforced",
            by_name.get("list_orders", {}).get("measurable") is False
            and by_name.get("list_orders", {}).get("runtime_enforced") is False,
        )
        check(
            "FTF-0407",
            "the high-risk, non-critical, confirmed tool is reported measurable "
            "and runtime_enforced regardless of is_critical",
            by_name.get("delete_account", {}).get("is_critical") is False
            and by_name.get("delete_account", {}).get("measurable") is True
            and by_name.get("delete_account", {}).get("runtime_enforced") is True,
        )

        # -- role boundary: /requirements is a read-only view, open to every
        # role Section 45 names (matching TOOL_READ_ROLES exactly) --
        r = _request(base_url, "GET", requirements_path(org1_id, project1_id))
        check(
            "FTF-0407",
            "an unauthenticated /requirements request is denied (401)",
            r.status == 401,
        )

    if FAILURES:
        print(f"FTF-0407 real critical-behavior-requirement proof: {len(FAILURES)} failed")
        for failure in FAILURES:
            print(f"  {failure}")
        sys.exit(1)

    print("FTF-0407 real critical-behavior-requirement proof: all checks passed")
    sys.exit(0)


if __name__ == "__main__":
    main()
