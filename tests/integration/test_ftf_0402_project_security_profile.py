#!/usr/bin/env python3
"""FTF-0402 -- real Postgres Project security profile inheritance proof.

Proves this task's own acceptance clause 1 ("Enforce selected privacy
profile across datasets, source, worker, models, judges and telemetry" --
at the one surface that already exists today, project creation/edit) and
negative_acceptance ("User cannot relax inherited organization policy by
changing a project field or using direct API") against a real, live
PostgreSQL server, through a real uvicorn server and FTF-0401's own real
`build_project_router` -- never a mocked connection, never calling
`apps/api/app/services/project_security_profile.py`'s pure functions
directly. Same real `services: postgres` container pattern as
`tests/integration/test_ftf_0401_project_schema_api.py` (this repo's
interactive dev sandbox has no container-registry egress; this evidence
was instead produced locally against a directly-installed PostgreSQL 16
cluster, and is wired into CI's dedicated
`project-security-profile-integration` job).

Two real organizations are seeded with two different real
`default_security_profile` values (org1 = the schema's own `"standard"`
default, org2 = `"air_gapped"`, set explicitly at creation) -- the real
inherited-policy floor this task's negative_acceptance is about.

Run directly (against an already-migrated or unmigrated database; this
script runs the migration itself):
    SOVRA_DATABASE_URL=postgresql+psycopg://<migration-role>@host/db \\
    SOVRA_APP_DB_PASSWORD=<app-role-password> \\
    python3 tests/integration/test_ftf_0402_project_security_profile.py
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

from app.auth.organization_workspace_schema import (  # noqa: E402
    APP_ROLE_NAME,
    Organization,
    Workspace,
)
from app.auth.rbac_middleware import AuthenticatedPrincipal, Role  # noqa: E402
from app.projects.project_routes import build_project_router  # noqa: E402

FAILURES: list[str] = []


def check(criterion_id: str, description: str, condition: bool) -> None:
    if not condition:
        FAILURES.append(f"FAIL [{criterion_id}] {description}")


def _required_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        print(
            f"BLOCKED_EXTERNAL: {name} is not set -- this task's real "
            "org-inheritance evidence requires a live Postgres (migration "
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


# -- Fixture: two organizations with two different real
# default_security_profile values, each with a workspace and a
# workspace_admin ---------------------------------------------------------

_ISSUER = "https://issuer.example"


class _Fixture:
    def __init__(self, org_ids: dict[str, uuid.UUID]) -> None:
        self.org_ids = org_ids
        self.principals: dict[tuple[str, Role], AuthenticatedPrincipal] = {}
        self.memberships: dict[tuple[str, str, uuid.UUID], Role] = {}
        for label, org_id in org_ids.items():
            subject = f"user-{label}-{Role.WORKSPACE_ADMIN.value}"
            principal = AuthenticatedPrincipal(issuer=_ISSUER, subject=subject)
            self.principals[(label, Role.WORKSPACE_ADMIN)] = principal
            self.memberships[(_ISSUER, subject, org_id)] = Role.WORKSPACE_ADMIN

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
        "FTF-0402",
        f"alembic upgrade head runs cleanly (exit {migration_result.returncode})\n"
        f"stdout: {migration_result.stdout}\nstderr: {migration_result.stderr}",
        migration_result.returncode == 0,
    )
    if FAILURES:
        print(f"FTF-0402 real security-profile inheritance proof: {len(FAILURES)} failed")
        for failure in FAILURES:
            print(f"  {failure}")
        sys.exit(1)

    owner_engine = create_engine(migration_url)
    app_url = _app_role_url(migration_url, app_password)
    app_engine = create_engine(app_url)

    # -- Seed two real tenants with two different real
    # default_security_profile values (migration-owner role, seeding only) --
    with Session(owner_engine) as session:
        org_standard = Organization(
            name="Acme 0402",
            slug=f"acme-0402-{uuid.uuid4().hex[:8]}",
            default_security_profile="standard",
        )
        org_air_gapped = Organization(
            name="Globex 0402",
            slug=f"globex-0402-{uuid.uuid4().hex[:8]}",
            default_security_profile="air_gapped",
        )
        session.add_all([org_standard, org_air_gapped])
        session.flush()
        ws_standard = Workspace(organization_id=org_standard.id, name="Acme Main", slug="main")
        ws_air_gapped = Workspace(
            organization_id=org_air_gapped.id, name="Globex Main", slug="main"
        )
        session.add_all([ws_standard, ws_air_gapped])
        session.commit()
        org_standard_id, org_air_gapped_id = org_standard.id, org_air_gapped.id
        ws_standard_id, ws_air_gapped_id = ws_standard.id, ws_air_gapped.id

    fixture = _Fixture({"standard-org": org_standard_id, "air-gapped-org": org_air_gapped_id})
    session_factory = sessionmaker(bind=app_engine)
    app = FastAPI()
    app.include_router(
        build_project_router(
            session_factory,
            principal_resolver=fixture.principal_resolver,
            role_lookup=fixture.role_lookup,
        )
    )

    standard_org_wa = fixture.header_for("standard-org", Role.WORKSPACE_ADMIN)
    air_gapped_org_wa = fixture.header_for("air-gapped-org", Role.WORKSPACE_ADMIN)

    def collection_path(org_id: uuid.UUID) -> str:
        return f"/api/v1/organizations/{org_id}/projects"

    def item_path(org_id: uuid.UUID, project_id: object) -> str:
        return f"{collection_path(org_id)}/{project_id}"

    with _running_app(app) as base_url:
        # -- A project created without naming security_profile at all under
        # an organization whose own default is already "standard" succeeds,
        # matching FTF-0401's own precedent exactly (no regression). --
        r = _request(
            base_url,
            "POST",
            collection_path(org_standard_id),
            principal=standard_org_wa,
            payload={
                "workspace_id": str(ws_standard_id),
                "name": "standard-default-project",
                "project_type": "generic_model_tuning",
            },
        )
        check(
            "FTF-0402",
            "creating a project under a 'standard'-default organization "
            "with no security_profile named succeeds (201)",
            r.status == 201,
        )
        standard_default_project = r.json() if r.status == 201 else {}
        check(
            "FTF-0402",
            "the created project's real, persisted security_profile is "
            "'standard' (the org's own default, matched exactly)",
            standard_default_project.get("security_profile") == "standard",
        )

        # -- negative_acceptance, at creation: omitting security_profile
        # under an "air_gapped"-default organization does NOT silently
        # create a laxer-than-policy "standard" project -- the client-side
        # "standard" default is itself checked against the real
        # organization floor and refused. --
        r = _request(
            base_url,
            "POST",
            collection_path(org_air_gapped_id),
            principal=air_gapped_org_wa,
            payload={
                "workspace_id": str(ws_air_gapped_id),
                "name": "should-be-refused-default",
                "project_type": "generic_model_tuning",
            },
        )
        check(
            "FTF-0402",
            "negative_acceptance: creating a project under an "
            "'air_gapped'-default organization with security_profile "
            "omitted (defaulting to 'standard') is refused (400) -- the "
            "organization's own real inherited floor is enforced even "
            "when the client never explicitly asked to relax it",
            r.status == 400,
        )

        # -- negative_acceptance, at creation: explicitly requesting a
        # laxer profile than the real organization default is refused --
        r = _request(
            base_url,
            "POST",
            collection_path(org_air_gapped_id),
            principal=air_gapped_org_wa,
            payload={
                "workspace_id": str(ws_air_gapped_id),
                "name": "should-be-refused-explicit",
                "project_type": "generic_model_tuning",
                "security_profile": "standard",
            },
        )
        check(
            "FTF-0402",
            "negative_acceptance: explicitly requesting security_profile="
            "'standard' under a real 'air_gapped'-default organization is "
            "refused (400) -- a project field cannot relax inherited "
            "organization policy",
            r.status == 400,
        )

        # -- an unrecognized profile name is refused, never silently
        # accepted as either more or less restrictive --
        r = _request(
            base_url,
            "POST",
            collection_path(org_air_gapped_id),
            principal=air_gapped_org_wa,
            payload={
                "workspace_id": str(ws_air_gapped_id),
                "name": "unknown-profile-name",
                "project_type": "generic_model_tuning",
                "security_profile": "super_secure",
            },
        )
        check(
            "FTF-0402",
            "an unrecognized security_profile name is refused (400), never " "silently accepted",
            r.status == 400,
        )

        # -- matching the real organization floor exactly succeeds --
        r = _request(
            base_url,
            "POST",
            collection_path(org_air_gapped_id),
            principal=air_gapped_org_wa,
            payload={
                "workspace_id": str(ws_air_gapped_id),
                "name": "matches-org-floor",
                "project_type": "generic_model_tuning",
                "security_profile": "air_gapped",
            },
        )
        check(
            "FTF-0402",
            "explicitly requesting security_profile='air_gapped' under an "
            "'air_gapped'-default organization succeeds (201) -- matching "
            "the inherited floor exactly is never treated as a relaxation",
            r.status == 201,
        )
        air_gapped_project = r.json() if r.status == 201 else {}
        check(
            "FTF-0402",
            "the created project's real, persisted security_profile is " "genuinely 'air_gapped'",
            air_gapped_project.get("security_profile") == "air_gapped",
        )
        air_gapped_project_id = air_gapped_project.get("id")

        # -- a project MAY choose a strictly stricter profile than its own
        # organization's floor (never blocked as "too restrictive") --
        r = _request(
            base_url,
            "POST",
            collection_path(org_standard_id),
            principal=standard_org_wa,
            payload={
                "workspace_id": str(ws_standard_id),
                "name": "stricter-than-org-floor",
                "project_type": "generic_model_tuning",
                "security_profile": "air_gapped",
            },
        )
        check(
            "FTF-0402",
            "a project MAY choose a profile stricter than its own "
            "organization's 'standard' floor (201, never refused as "
            "'too restrictive')",
            r.status == 201,
        )

        # -- negative_acceptance, at EDIT (PATCH): a direct API call
        # cannot relax an existing project's security_profile below its
        # own organization's real inherited floor --
        r = _request(
            base_url,
            "PATCH",
            item_path(org_air_gapped_id, air_gapped_project_id),
            principal=air_gapped_org_wa,
            payload={"security_profile": "standard", "expected_row_version": 1},
        )
        check(
            "FTF-0402",
            "negative_acceptance: a PATCH attempting to relax an existing "
            "project's security_profile from 'air_gapped' to 'standard' "
            "(below its own organization's real inherited floor) is "
            "refused (400)",
            r.status == 400,
        )

        # -- the failed relaxation attempt genuinely left the project
        # untouched -- still air_gapped, row_version unchanged --
        r = _request(
            base_url,
            "GET",
            item_path(org_air_gapped_id, air_gapped_project_id),
            principal=air_gapped_org_wa,
        )
        still_air_gapped = r.json() if r.status == 200 else {}
        check(
            "FTF-0402",
            "the project is genuinely untouched after the refused PATCH -- "
            "security_profile still 'air_gapped', row_version still 1 "
            "(the refused edit never partially applied)",
            r.status == 200
            and still_air_gapped.get("security_profile") == "air_gapped"
            and still_air_gapped.get("row_version") == 1,
        )

        # -- an edit that keeps or raises the profile succeeds and is
        # genuinely persisted --
        r = _request(
            base_url,
            "PATCH",
            item_path(org_air_gapped_id, air_gapped_project_id),
            principal=air_gapped_org_wa,
            payload={"security_profile": "air_gapped", "expected_row_version": 1},
        )
        check(
            "FTF-0402",
            "a PATCH that re-selects the same 'air_gapped' profile (equal "
            "to the org floor, not a relaxation) succeeds (200)",
            r.status == 200,
        )
        reconfirmed = r.json() if r.status == 200 else {}
        check(
            "FTF-0402",
            "the real, persisted security_profile after this successful "
            "edit is still genuinely 'air_gapped', row_version bumped to 2",
            reconfirmed.get("security_profile") == "air_gapped"
            and reconfirmed.get("row_version") == 2,
        )

    if FAILURES:
        print(f"FTF-0402 real security-profile inheritance proof: {len(FAILURES)} failed")
        for failure in FAILURES:
            print(f"  {failure}")
        sys.exit(1)

    print("FTF-0402 real security-profile inheritance proof: all checks passed")
    sys.exit(0)


if __name__ == "__main__":
    main()
