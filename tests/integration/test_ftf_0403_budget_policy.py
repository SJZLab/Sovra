#!/usr/bin/env python3
"""FTF-0403 -- real Postgres Budget policy proof.

Proves this task's own acceptance clause 1 ("Persist decimal budget
authorization, cost currency, scopes and approval history") and
negative_acceptance ("Negative/unknown rates and concurrent allocation
beyond available reservation cannot be accepted") against a real, live
PostgreSQL server -- never a mocked connection or an in-memory SQLite
stand-in (SQLite has no row-level security at all, so a passing test
against it would prove nothing about this task's actual acceptance
criterion).

Two real organizations (org1/org2), each with a real workspace and real
project(s), are seeded as the migration-owner role; every request below
runs through a real uvicorn server, with the router's own database
session bound to the real, distinct, least-privileged `sovra_app` role
FTF-0102's migration creates (never the migration-owner role -- confirmed
directly below), exactly like `tests/integration/test_ftf_0404_tool_schema_crud.py`
already established for a different resource.

The concurrent-reservation proof (`_test_concurrent_reservations_never_
overcommit`) is this task's own central negative_acceptance made real: two
real, concurrently-issued HTTP reserve requests, each individually within
budget but together exceeding it, prove that at most one can ever
succeed -- proof against the real database and the real HTTP stack, not a
single-threaded/mocked simulation.

Requires a real Postgres reachable at `SOVRA_DATABASE_URL` (the migration
owner role) plus `SOVRA_APP_DB_PASSWORD`, matching every prior integration
test's own environment contract exactly. This repo's interactive dev
sandbox has no container-registry egress (same finding as every prior
integration-tested task) -- this evidence was instead produced locally
against a directly-installed PostgreSQL 16 cluster, and is wired into
CI's dedicated `budget-policy-integration` job (a GitHub Actions
`services: postgres` container, real registry egress on the hosted
runner).

Run directly (against an already-migrated or unmigrated database; this
script runs the migration itself):
    SOVRA_DATABASE_URL=postgresql+psycopg://<migration-role>@host/db \\
    SOVRA_APP_DB_PASSWORD=<app-role-password> \\
    python3 tests/integration/test_ftf_0403_budget_policy.py
"""

from __future__ import annotations

import concurrent.futures
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
from sqlalchemy.exc import DBAPIError, IntegrityError  # noqa: E402
from sqlalchemy.orm import Session, sessionmaker  # noqa: E402

from app.auth.organization_workspace_schema import (  # noqa: E402
    APP_ROLE_NAME,
    Organization,
    Workspace,
)
from app.auth.rbac_middleware import AuthenticatedPrincipal, Role  # noqa: E402
from app.auth.resource_authorization import authorize_resource_ownership  # noqa: E402
from app.auth.tenant_isolation_query_guards import TenantContextError  # noqa: E402
from app.budget.budget_policy_routes import build_budget_policy_router  # noqa: E402
from app.budget.budget_policy_schema import BudgetPolicy  # noqa: E402
from app.db.tenant_context import TenantContext  # noqa: E402
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
        "FTF-0403",
        f"alembic upgrade head runs cleanly (exit {migration_result.returncode})\n"
        f"stdout: {migration_result.stdout}\nstderr: {migration_result.stderr}",
        migration_result.returncode == 0,
    )
    if FAILURES:
        print(f"FTF-0403 real Budget policy proof: {len(FAILURES)} failed")
        for failure in FAILURES:
            print(f"  {failure}")
        sys.exit(1)

    owner_engine = create_engine(migration_url)
    app_url = _app_role_url(migration_url, app_password)
    app_engine = create_engine(app_url)

    check(
        "FTF-0403",
        "the router's own database session is built from the restricted "
        "sovra_app role's connection URL, never the migration-owner role",
        app_engine.url.username == APP_ROLE_NAME
        and app_engine.url.username != owner_engine.url.username,
    )

    with Session(owner_engine) as session:
        org1 = Organization(name="Acme 0403", slug=f"acme-0403-{uuid.uuid4().hex[:8]}")
        org2 = Organization(name="Globex 0403", slug=f"globex-0403-{uuid.uuid4().hex[:8]}")
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
        project_concurrency = Project(
            organization_id=org1.id,
            workspace_id=ws1.id,
            name="acme-project-concurrency",
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
        session.add_all([project1, project1b, project_concurrency, project2])
        session.commit()
        org1_id, org2_id = org1.id, org2.id
        project1_id = project1.id
        project1b_id = project1b.id
        project_concurrency_id = project_concurrency.id
        _project2_id = project2.id  # seeded for realism; not referenced directly below

    fixture = _Fixture({"org1": org1_id, "org2": org2_id})
    session_factory = sessionmaker(bind=app_engine)
    app = FastAPI()
    app.include_router(
        build_budget_policy_router(
            session_factory,
            principal_resolver=fixture.principal_resolver,
            role_lookup=fixture.role_lookup,
        )
    )

    org1_wa = fixture.header_for("org1", Role.WORKSPACE_ADMIN)
    org1_evaluator = fixture.header_for("org1", Role.EVALUATOR)
    org2_wa = fixture.header_for("org2", Role.WORKSPACE_ADMIN)

    def path(org_id: uuid.UUID, project_id: uuid.UUID, suffix: str = "") -> str:
        return f"/api/v1/organizations/{org_id}/projects/{project_id}/budget-policy{suffix}"

    with _running_app(app) as base_url:
        # -- Unauthenticated / role-boundary denial --
        r = _request(base_url, "GET", path(org1_id, project1_id))
        check("FTF-0403", "an unauthenticated GET is denied (401)", r.status == 401)
        r = _request(
            base_url,
            "POST",
            path(org1_id, project1_id),
            principal=org1_evaluator,
            payload={"total_amount": "1000.00", "currency": "USD", "scopes": ["training"]},
        )
        check("FTF-0403", "an evaluator cannot create a budget policy (403)", r.status == 403)

        # -- Real create: acceptance clause 1 --
        r = _request(
            base_url,
            "POST",
            path(org1_id, project1_id),
            principal=org1_wa,
            payload={
                "total_amount": "1000.00",
                "currency": "USD",
                "scopes": ["training", "evaluation"],
            },
        )
        check("FTF-0403", "a real create succeeds (201)", r.status == 201)
        body = r.json()
        check(
            "FTF-0403",
            "the real persisted total_amount/currency/scopes round-trip exactly",
            body.get("total_amount") == "1000.0000"
            and body.get("currency") == "USD"
            and body.get("scopes") == ["evaluation", "training"],
        )
        policy_id = body.get("id")
        check(
            "FTF-0403",
            "a freshly created policy has zero reserved and full available amount",
            body.get("reserved_amount") == "0.0000" and body.get("available_amount") == "1000.0000",
        )

        # -- Real FK wiring: projects.budget_policy_id now points to the
        # real budget_policies row this task added the FK for --
        with Session(owner_engine) as session:
            row = session.execute(
                text("SELECT budget_policy_id FROM projects WHERE id = :id"),
                {"id": str(project1_id)},
            ).scalar_one()
            check(
                "FTF-0403",
                "acceptance: FTF-0401's own disclosed budget_policy_id column now "
                "genuinely references this task's real budget_policies row",
                str(row) == policy_id,
            )

        # -- Duplicate create on the same project is refused --
        r = _request(
            base_url,
            "POST",
            path(org1_id, project1_id),
            principal=org1_wa,
            payload={"total_amount": "500.00", "currency": "USD", "scopes": ["training"]},
        )
        check(
            "FTF-0403",
            "creating a second budget policy for the same project is refused (409)",
            r.status == 409,
        )

        # -- Cross-project IDOR (same organization) --
        r = _request(base_url, "GET", path(org1_id, project1b_id), principal=org1_wa)
        check(
            "FTF-0403",
            "a different project in the same organization has no budget policy of "
            "its own (404) -- never leaks project1's row",
            r.status == 404,
        )

        # -- Cross-organization IDOR --
        r = _request(base_url, "GET", path(org2_id, project1_id), principal=org2_wa)
        check(
            "FTF-0403",
            "IDOR: org2 cannot reach org1's project/policy via its own project path (404)",
            r.status == 404,
        )

        # -- Negative acceptance: negative/unknown rates refused --
        r = _request(
            base_url,
            "POST",
            path(org1_id, project1b_id),
            principal=org1_wa,
            payload={"total_amount": "-5.00", "currency": "USD", "scopes": ["training"]},
        )
        check(
            "FTF-0403",
            "negative_acceptance: a negative total_amount is refused (400)",
            r.status == 400,
        )
        r = _request(
            base_url,
            "POST",
            path(org1_id, project1b_id),
            principal=org1_wa,
            payload={"total_amount": "500.00", "currency": "ZZZ", "scopes": ["training"]},
        )
        check(
            "FTF-0403",
            "negative_acceptance: an unrecognized ('unknown') currency is refused (400)",
            r.status == 400,
        )

        # -- Reserve: within budget --
        r = _request(
            base_url,
            "POST",
            path(org1_id, project1_id, "/reserve"),
            principal=org1_wa,
            payload={"amount": "400.00", "scope": "training"},
        )
        check("FTF-0403", "a real reservation within budget succeeds (200)", r.status == 200)
        check(
            "FTF-0403",
            "reserved_amount/available_amount reflect the real reservation",
            r.json().get("reserved_amount") == "400.0000"
            and r.json().get("available_amount") == "600.0000",
        )

        # -- Reserve: unauthorized scope --
        r = _request(
            base_url,
            "POST",
            path(org1_id, project1_id, "/reserve"),
            principal=org1_wa,
            payload={"amount": "10.00", "scope": "serving"},
        )
        check(
            "FTF-0403",
            "reserving for a scope this policy never authorized is refused (400)",
            r.status == 400,
        )

        # -- Reserve: exceeds remaining headroom --
        r = _request(
            base_url,
            "POST",
            path(org1_id, project1_id, "/reserve"),
            principal=org1_wa,
            payload={"amount": "700.00", "scope": "training"},
        )
        check(
            "FTF-0403",
            "negative_acceptance: a reservation exceeding available headroom is "
            "refused (409), not silently truncated or over-committed",
            r.status == 409,
        )
        r = _request(base_url, "GET", path(org1_id, project1_id), principal=org1_wa)
        check(
            "FTF-0403",
            "the refused over-reservation left reserved_amount genuinely unchanged",
            r.json().get("reserved_amount") == "400.0000",
        )

        # -- Revise: refuses shrinking below reserved --
        current_row_version = r.json().get("row_version")
        r = _request(
            base_url,
            "PATCH",
            path(org1_id, project1_id),
            principal=org1_wa,
            payload={"total_amount": "300.00", "expected_row_version": current_row_version},
        )
        check(
            "FTF-0403",
            "negative_acceptance: revising total_amount below what is already "
            "reserved (400 already reserved) is refused (409)",
            r.status == 409,
        )

        # -- Revise: stale row_version refused --
        r = _request(
            base_url,
            "PATCH",
            path(org1_id, project1_id),
            principal=org1_wa,
            payload={"total_amount": "2000.00", "expected_row_version": current_row_version + 99},
        )
        check("FTF-0403", "revising with a stale row_version is refused (409)", r.status == 409)

        # -- Revise: a real, valid increase --
        r = _request(
            base_url,
            "PATCH",
            path(org1_id, project1_id),
            principal=org1_wa,
            payload={"total_amount": "2000.00", "expected_row_version": current_row_version},
        )
        check("FTF-0403", "a real, valid revision succeeds (200)", r.status == 200)
        check(
            "FTF-0403",
            "the real new total_amount is persisted and row_version advanced",
            r.json().get("total_amount") == "2000.0000"
            and r.json().get("row_version") == current_row_version + 1,
        )

        # -- Release: within reserved --
        r = _request(
            base_url,
            "POST",
            path(org1_id, project1_id, "/release"),
            principal=org1_wa,
            payload={"amount": "100.00", "scope": "training"},
        )
        check("FTF-0403", "a real release succeeds (200)", r.status == 200)
        check(
            "FTF-0403",
            "reserved_amount reflects the real release",
            r.json().get("reserved_amount") == "300.0000",
        )

        # -- Release: over-release refused --
        r = _request(
            base_url,
            "POST",
            path(org1_id, project1_id, "/release"),
            principal=org1_wa,
            payload={"amount": "9000.00", "scope": "training"},
        )
        check(
            "FTF-0403",
            "negative_acceptance: releasing more than is reserved is refused (409), "
            "never driving reserved_amount negative",
            r.status == 409,
        )

        # -- History: this task's own "approval history" acceptance clause --
        r = _request(base_url, "GET", path(org1_id, project1_id, "/history"), principal=org1_wa)
        check("FTF-0403", "history is reachable (200)", r.status == 200)
        actions = [e.get("action") for e in r.json()]
        check(
            "FTF-0403",
            "acceptance: the real approval/allocation history carries every real "
            "mutation performed above, in order",
            actions
            == [
                "budget_policy.create",
                "budget_policy.reserve",
                "budget_policy.revise",
                "budget_policy.release",
            ],
        )

        # -- Concurrency: this task's own central negative_acceptance --
        r = _request(
            base_url,
            "POST",
            path(org1_id, project_concurrency_id),
            principal=org1_wa,
            payload={"total_amount": "100.00", "currency": "USD", "scopes": ["training"]},
        )
        check("FTF-0403", "concurrency fixture: real policy created (201)", r.status == 201)

        def _reserve_60() -> int:
            resp = _request(
                base_url,
                "POST",
                path(org1_id, project_concurrency_id, "/reserve"),
                principal=org1_wa,
                payload={"amount": "60.00", "scope": "training"},
            )
            return resp.status

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            statuses = list(pool.map(lambda _: _reserve_60(), range(2)))
        check(
            "FTF-0403",
            "negative_acceptance: two concurrent reservations that together exceed "
            "the policy's own total (60+60=120 > 100) never both succeed -- exactly "
            "one 200 and one 409, proven against the real database under real "
            "concurrent HTTP requests",
            sorted(statuses) == [200, 409],
        )
        r = _request(base_url, "GET", path(org1_id, project_concurrency_id), principal=org1_wa)
        check(
            "FTF-0403",
            "acceptance: the final reserved_amount reflects exactly the one "
            "reservation that really committed -- never both, never neither",
            r.json().get("reserved_amount") == "60.0000",
        )

    # -- acceptance: missing tenant context is refused, not treated as "0 rows" --
    with Session(app_engine) as session:
        raw_context = TenantContext(organization_id=org1_id)
        try:
            authorize_resource_ownership(
                session,
                raw_context,
                BudgetPolicy.__table__,
                BudgetPolicy.__table__.c.id,
                BudgetPolicy.__table__.c.organization_id,
                uuid.UUID(policy_id) if policy_id else uuid.uuid4(),
            )
            check(
                "FTF-0403",
                "acceptance: a session with no tenant context ever bound is "
                "refused (TenantContextError), not treated as an ordinary "
                "zero-row/foreign-tenant denial",
                False,
            )
        except TenantContextError:
            check(
                "FTF-0403",
                "acceptance: a session with no tenant context ever bound is "
                "refused (TenantContextError), not treated as an ordinary "
                "zero-row/foreign-tenant denial",
                True,
            )

    # -- Grant-surface proof: sovra_app genuinely cannot DELETE a
    # budget_policies row directly (never removed, only ever revised). --
    if policy_id:
        with Session(app_engine) as session:
            set_tenant_context_sql = text(
                "SELECT set_config('app.current_organization_id', :org_id, true)"
            )
            session.execute(set_tenant_context_sql, {"org_id": str(org1_id)})
            try:
                session.execute(
                    text("DELETE FROM budget_policies WHERE id = :id"), {"id": policy_id}
                )
                session.commit()
                check(
                    "FTF-0403",
                    "negative_acceptance/grant-surface: the restricted sovra_app "
                    "role cannot DELETE a budget_policies row directly",
                    False,
                )
            except DBAPIError:
                session.rollback()
                check(
                    "FTF-0403",
                    "negative_acceptance/grant-surface: the restricted sovra_app "
                    "role cannot DELETE a budget_policies row directly",
                    True,
                )

    # -- Real DB-level backstop: UNIQUE(project_id) refuses a second row for
    # the same project even bypassing this task's own application check. --
    with Session(owner_engine) as session:
        session.execute(text("SET row_security = off"))
        try:
            session.execute(
                text(
                    "INSERT INTO budget_policies "
                    "(organization_id, project_id, total_amount, currency, "
                    "scopes_json, created_by) "
                    "VALUES (:org_id, :project_id, 1, 'USD', "
                    "'[\"training\"]'::jsonb, 'raw-insert')"
                ),
                {"org_id": str(org1_id), "project_id": str(project1_id)},
            )
            session.commit()
            check(
                "FTF-0403",
                "the real UNIQUE(project_id) constraint refuses a second row for "
                "the same project, even at the raw SQL level",
                False,
            )
        except IntegrityError:
            session.rollback()
            check(
                "FTF-0403",
                "the real UNIQUE(project_id) constraint refuses a second row for "
                "the same project, even at the raw SQL level",
                True,
            )

    if FAILURES:
        print(f"FTF-0403 real Budget policy proof: {len(FAILURES)} failed")
        for failure in FAILURES:
            print(f"  {failure}")
        sys.exit(1)

    print("FTF-0403 real Budget policy proof: all checks passed")
    sys.exit(0)


if __name__ == "__main__":
    main()
