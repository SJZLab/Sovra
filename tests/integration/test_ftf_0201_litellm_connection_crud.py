#!/usr/bin/env python3
"""FTF-0201 -- real Postgres LiteLLM connection CRUD proof.

Proves this task's own acceptance clause 1 ("Create/edit/revoke an
organization-scoped gateway connection with secret refs and explicit
TLS/URL policy") and negative_acceptance ("Foreign connection ID,
rejected URL or secret echo fails; cannot mutate another organization
gateway") against a real, live PostgreSQL server -- never a mocked
connection or an in-memory SQLite stand-in (SQLite has no row-level
security at all, so a passing test against it would prove nothing about
this task's actual acceptance criterion).

Two real organizations (org1/org2) are seeded as the migration-owner
role; every request below runs through a real uvicorn server, with the
router's own database session bound to the real, distinct,
least-privileged `sovra_app` role FTF-0102's migration creates (never the
migration-owner role -- confirmed directly below, not merely asserted by
convention), exactly like `tests/integration/test_ftf_0106_rbac_idor_security.py`
already established for a different resource.

Requires a real Postgres reachable at `SOVRA_DATABASE_URL` (the migration
owner role) plus `SOVRA_APP_DB_PASSWORD`, matching FTF-0102/FTF-0105/
FTF-H003/FTF-0106's own integration tests' environment contract exactly.
This repo's interactive dev sandbox has no container-registry egress
(same finding as FTF-0004/FTF-0102/FTF-0104/FTF-0105/FTF-H003/FTF-0106) --
this evidence was instead produced locally against a directly-installed
PostgreSQL 16 cluster, and is wired into CI's dedicated
`litellm-connection-crud-integration` job (a GitHub Actions
`services: postgres` container, real registry egress on the hosted
runner).

Run directly (against an already-migrated or unmigrated database; this
script runs the migration itself):
    SOVRA_DATABASE_URL=postgresql+psycopg://<migration-role>@host/db \\
    SOVRA_APP_DB_PASSWORD=<app-role-password> \\
    python3 tests/integration/test_ftf_0201_litellm_connection_crud.py
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

from app.auth.organization_workspace_schema import APP_ROLE_NAME, Organization  # noqa: E402
from app.auth.rbac_middleware import AuthenticatedPrincipal, Role  # noqa: E402
from app.auth.resource_authorization import authorize_resource_ownership  # noqa: E402
from app.auth.tenant_isolation_query_guards import TenantContextError  # noqa: E402
from app.db.tenant_context import TenantContext  # noqa: E402
from app.litellm.connection_routes import build_litellm_connection_router  # noqa: E402
from app.litellm.connection_schema import LiteLLMConnection  # noqa: E402

FAILURES: list[str] = []


def check(criterion_id: str, description: str, condition: bool) -> None:
    if not condition:
        FAILURES.append(f"FAIL [{criterion_id}] {description}")


def _required_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        print(
            f"BLOCKED_EXTERNAL: {name} is not set -- this task's real "
            "connection-CRUD evidence requires a live Postgres (migration "
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


# -- Fixture: two organizations, one organization_admin + one viewer each --

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
        "FTF-0201",
        f"alembic upgrade head runs cleanly (exit {migration_result.returncode})\n"
        f"stdout: {migration_result.stdout}\nstderr: {migration_result.stderr}",
        migration_result.returncode == 0,
    )
    if FAILURES:
        print(f"FTF-0201 real LiteLLM connection CRUD proof: {len(FAILURES)} failed")
        for failure in FAILURES:
            print(f"  {failure}")
        sys.exit(1)

    owner_engine = create_engine(migration_url)
    app_url = _app_role_url(migration_url, app_password)
    app_engine = create_engine(app_url)

    check(
        "FTF-0201",
        "the router's own database session is built from the restricted "
        "sovra_app role's connection URL, never the migration-owner role",
        app_engine.url.username == APP_ROLE_NAME
        and app_engine.url.username != owner_engine.url.username,
    )

    # -- Seed two real, distinct tenants (migration-owner role, seeding only) --
    with Session(owner_engine) as session:
        org1 = Organization(name="Acme 0201", slug=f"acme-0201-{uuid.uuid4().hex[:8]}")
        org2 = Organization(name="Globex 0201", slug=f"globex-0201-{uuid.uuid4().hex[:8]}")
        session.add_all([org1, org2])
        session.commit()
        org1_id, org2_id = org1.id, org2.id

    fixture = _Fixture({"org1": org1_id, "org2": org2_id})
    session_factory = sessionmaker(bind=app_engine)
    app = FastAPI()
    app.include_router(
        build_litellm_connection_router(
            session_factory,
            principal_resolver=fixture.principal_resolver,
            role_lookup=fixture.role_lookup,
        )
    )

    org1_admin = fixture.header_for("org1", Role.ORGANIZATION_ADMIN)
    org1_viewer = fixture.header_for("org1", Role.VIEWER)
    org2_admin = fixture.header_for("org2", Role.ORGANIZATION_ADMIN)

    def collection_path(org_id: uuid.UUID) -> str:
        return f"/api/v1/organizations/{org_id}/connections/litellm"

    def item_path(org_id: uuid.UUID, connection_id: object) -> str:
        return f"{collection_path(org_id)}/{connection_id}"

    with _running_app(app) as base_url:
        # -- Unauthenticated / role-boundary denial --
        r = _request(base_url, "GET", collection_path(org1_id))
        check("FTF-0201", "an unauthenticated list request is denied (401)", r.status == 401)

        r = _request(base_url, "GET", collection_path(org1_id), principal=org1_viewer)
        check(
            "FTF-0201",
            "a viewer (not organization_admin) is denied even a read (403) -- "
            "Section 45's own role table names organization_admin only",
            r.status == 403,
        )

        # -- negative_acceptance: rejected URL fails at creation --
        r = _request(
            base_url,
            "POST",
            collection_path(org1_id),
            principal=org1_admin,
            payload={
                "name": "insecure",
                "base_url": "http://insecure.example.com/v1",
                "secret_ref": "vault://litellm/org1/key",
            },
        )
        check(
            "FTF-0201",
            "negative_acceptance: a rejected (non-https) base_url fails at "
            "creation (400), never a fabricated success",
            r.status == 400,
        )

        # -- negative_acceptance: a raw secret value (not a reference) fails --
        r = _request(
            base_url,
            "POST",
            collection_path(org1_id),
            principal=org1_admin,
            payload={
                "name": "raw-secret",
                "base_url": "https://gateway.example.com/v1",
                "secret_ref": "sk-live-raw-secret-value-not-a-reference",
            },
        )
        check(
            "FTF-0201",
            "negative_acceptance: a raw secret value (not a vault://-shaped "
            "reference) fails at creation (400)",
            r.status == 400,
        )

        # -- Real create --
        r = _request(
            base_url,
            "POST",
            collection_path(org1_id),
            principal=org1_admin,
            payload={
                "name": "prod-gateway",
                "base_url": "https://gateway.example.com/v1",
                "secret_ref": "vault://litellm/org1/key",
            },
        )
        check("FTF-0201", "a real, valid create succeeds (201)", r.status == 201)
        created = r.json() if r.status == 201 else {}
        check(
            "FTF-0201",
            "negative_acceptance: the create response never echoes secret_ref",
            isinstance(created, dict) and "secret_ref" not in created,
        )
        check(
            "FTF-0201",
            "the created connection is active with the real submitted fields",
            isinstance(created, dict)
            and created.get("status") == "active"
            and created.get("name") == "prod-gateway"
            and created.get("organization_id") == str(org1_id),
        )
        connection_id = created.get("id") if isinstance(created, dict) else None

        # -- Real list --
        r = _request(base_url, "GET", collection_path(org1_id), principal=org1_admin)
        check("FTF-0201", "a real list succeeds (200)", r.status == 200)
        listed = r.json() if r.status == 200 else []
        check(
            "FTF-0201",
            "the listed connection is present and never carries secret_ref",
            isinstance(listed, list)
            and any(item.get("id") == connection_id for item in listed)
            and all("secret_ref" not in item for item in listed),
        )

        # -- Real get --
        r = _request(base_url, "GET", item_path(org1_id, connection_id), principal=org1_admin)
        check("FTF-0201", "a real get-by-id succeeds (200)", r.status == 200)

        # -- acceptance: a missing/random ID is a real 404 --
        r = _request(base_url, "GET", item_path(org1_id, uuid.uuid4()), principal=org1_admin)
        check("FTF-0201", "a real, never-created ID is a real 404", r.status == 404)

        # -- negative_acceptance: THE core IDOR case -- a real connection ID,
        # guessed/borrowed by the OTHER organization's own admin, substituted
        # into THEIR OWN org path -- must be denied identically to missing.
        r = _request(base_url, "GET", item_path(org2_id, connection_id), principal=org2_admin)
        check(
            "FTF-0201",
            "negative_acceptance: a foreign connection ID (belongs to org1) "
            "requested under org2's own path by a real org2 organization_admin "
            "is denied identically to missing (404), never a distinguishable "
            "403 that would leak its existence",
            r.status == 404,
        )
        r = _request(
            base_url,
            "PATCH",
            item_path(org2_id, connection_id),
            principal=org2_admin,
            payload={"name": "hijacked"},
        )
        check(
            "FTF-0201",
            "negative_acceptance: 'cannot mutate another organization "
            "gateway' -- org2's admin cannot edit org1's real connection "
            "even via their own path (404)",
            r.status == 404,
        )
        r = _request(base_url, "DELETE", item_path(org2_id, connection_id), principal=org2_admin)
        check(
            "FTF-0201",
            "negative_acceptance: org2's admin cannot revoke org1's real connection either (404)",
            r.status == 404,
        )

        # -- Confirm the foreign-org mutation attempts truly did not mutate it:
        r = _request(base_url, "GET", item_path(org1_id, connection_id), principal=org1_admin)
        still_active = r.json() if r.status == 200 else {}
        check(
            "FTF-0201",
            "the connection is genuinely untouched after org2's denied "
            "mutation attempts -- still active, still named prod-gateway",
            r.status == 200
            and still_active.get("status") == "active"
            and still_active.get("name") == "prod-gateway",
        )

        # -- Real edit, by the real owning organization --
        r = _request(
            base_url,
            "PATCH",
            item_path(org1_id, connection_id),
            principal=org1_admin,
            payload={"name": "prod-gateway-renamed"},
        )
        check(
            "FTF-0201",
            "a real, valid edit by the owning organization succeeds (200)",
            r.status == 200,
        )
        edited = r.json() if r.status == 200 else {}
        check(
            "FTF-0201",
            "the edit is genuinely applied and secret_ref is still never echoed",
            isinstance(edited, dict)
            and edited.get("name") == "prod-gateway-renamed"
            and "secret_ref" not in edited,
        )

        # -- negative_acceptance: a rejected URL fails on edit too --
        r = _request(
            base_url,
            "PATCH",
            item_path(org1_id, connection_id),
            principal=org1_admin,
            payload={"base_url": "https://gateway.example.com/v1?tls_verify=false"},
        )
        check(
            "FTF-0201",
            "negative_acceptance: a rejected base_url (tls_verify=false) fails on edit too (400)",
            r.status == 400,
        )

        # -- Real revoke (idempotent) --
        r = _request(base_url, "DELETE", item_path(org1_id, connection_id), principal=org1_admin)
        check(
            "FTF-0201",
            "a real revoke by the owning organization succeeds (200)",
            r.status == 200,
        )
        revoked = r.json() if r.status == 200 else {}
        check(
            "FTF-0201",
            "the revoke genuinely transitions status and sets revoked_at",
            isinstance(revoked, dict)
            and revoked.get("status") == "revoked"
            and revoked.get("revoked_at") is not None,
        )
        first_revoked_at = revoked.get("revoked_at")

        r = _request(base_url, "DELETE", item_path(org1_id, connection_id), principal=org1_admin)
        check(
            "FTF-0201",
            "acceptance: revoking an already-revoked connection is idempotent "
            "-- still 200, revoked_at does not move",
            r.status == 200 and r.json().get("revoked_at") == first_revoked_at,
        )

        # -- acceptance: a revoked connection cannot be edited (409) --
        r = _request(
            base_url,
            "PATCH",
            item_path(org1_id, connection_id),
            principal=org1_admin,
            payload={"name": "should-not-apply"},
        )
        check(
            "FTF-0201",
            "acceptance: editing a revoked connection is refused (409), not silently applied",
            r.status == 409,
        )

    # -- acceptance: missing tenant context is refused, not treated as "0 rows" --
    with Session(app_engine) as session:
        raw_context = TenantContext(organization_id=org1_id)
        try:
            authorize_resource_ownership(
                session,
                raw_context,
                LiteLLMConnection.__table__,
                LiteLLMConnection.__table__.c.id,
                LiteLLMConnection.__table__.c.organization_id,
                uuid.UUID(connection_id) if connection_id else uuid.uuid4(),
            )
            check(
                "FTF-0201",
                "acceptance: a session with no tenant context ever bound is "
                "refused (TenantContextError), not treated as an ordinary "
                "zero-row/foreign-tenant denial",
                False,
            )
        except TenantContextError:
            check(
                "FTF-0201",
                "acceptance: a session with no tenant context ever bound is "
                "refused (TenantContextError), not treated as an ordinary "
                "zero-row/foreign-tenant denial",
                True,
            )

    # -- Grant-surface proof: sovra_app genuinely cannot DELETE the row
    # directly (revoke is a soft state transition, never a real row
    # deletion -- confirmed at the database privilege level, not merely by
    # this router's own code choosing not to issue one). --
    if connection_id:
        with Session(app_engine) as session:
            set_tenant_context_sql = text(
                "SELECT set_config('app.current_organization_id', :org_id, true)"
            )
            session.execute(set_tenant_context_sql, {"org_id": str(org1_id)})
            try:
                session.execute(
                    text("DELETE FROM litellm_connections WHERE id = :id"),
                    {"id": connection_id},
                )
                session.commit()
                check(
                    "FTF-0201",
                    "negative_acceptance/grant-surface: the restricted "
                    "sovra_app role cannot DELETE a litellm_connections row "
                    "directly -- revoke is a soft state transition only",
                    False,
                )
            except DBAPIError:
                session.rollback()
                check(
                    "FTF-0201",
                    "negative_acceptance/grant-surface: the restricted "
                    "sovra_app role cannot DELETE a litellm_connections row "
                    "directly -- revoke is a soft state transition only",
                    True,
                )

    if FAILURES:
        print(f"FTF-0201 real LiteLLM connection CRUD proof: {len(FAILURES)} failed")
        for failure in FAILURES:
            print(f"  {failure}")
        sys.exit(1)

    print("FTF-0201 real LiteLLM connection CRUD proof: all checks passed")
    sys.exit(0)


if __name__ == "__main__":
    main()
