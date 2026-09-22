#!/usr/bin/env python3
"""FTF-1301 -- real Postgres CostProfile proof.

Proves this task's own acceptance clause 1 ("Record dated provider/local
hardware TCO rates/currency/source and display unknowns honestly") and
negative_acceptance ("Token price zero is not local compute free;
unverified/expired rate cannot certify cheapest") against a real, live
PostgreSQL server -- never a mocked connection or an in-memory SQLite
stand-in (SQLite has no row-level security at all, so a passing test
against it would prove nothing about this task's actual acceptance
criterion).

Two real organizations (org1/org2), each with a real workspace and real
project(s), are seeded as the migration-owner role; every request below
runs through a real uvicorn server, with the router's own database
session bound to the real, distinct, least-privileged `sovra_app` role
FTF-0102's migration creates (never the migration-owner role -- confirmed
directly below), the same pattern
`tests/integration/test_ftf_0403_budget_policy.py` already established
for a different resource.

Requires a real Postgres reachable at `SOVRA_DATABASE_URL` (the migration
owner role) plus `SOVRA_APP_DB_PASSWORD`, matching every prior integration
test's own environment contract exactly. This repo's interactive dev
sandbox has no container-registry egress (same finding as every prior
integration-tested task) -- this evidence was instead produced locally
against a directly-installed PostgreSQL 16 cluster, and is wired into
CI's dedicated `cost-profile-integration` job (a GitHub Actions
`services: postgres` container, real registry egress on the hosted
runner).

Run directly (against an already-migrated or unmigrated database; this
script runs the migration itself):
    SOVRA_DATABASE_URL=postgresql+psycopg://<migration-role>@host/db \\
    SOVRA_APP_DB_PASSWORD=<app-role-password> \\
    python3 tests/integration/test_ftf_1301_cost_profile.py
"""

from __future__ import annotations

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
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
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
from app.costs.cost_profile_routes import build_cost_profile_router  # noqa: E402
from app.costs.cost_profile_schema import CostProfile  # noqa: E402
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


@contextmanager
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
            for role in (Role.ORGANIZATION_ADMIN, Role.ML_ENGINEER):
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
        "FTF-1301",
        f"alembic upgrade head runs cleanly (exit {migration_result.returncode})\n"
        f"stdout: {migration_result.stdout}\nstderr: {migration_result.stderr}",
        migration_result.returncode == 0,
    )
    if FAILURES:
        print(f"FTF-1301 real CostProfile proof: {len(FAILURES)} failed")
        for failure in FAILURES:
            print(f"  {failure}")
        sys.exit(1)

    owner_engine = create_engine(migration_url)
    app_url = _app_role_url(migration_url, app_password)
    app_engine = create_engine(app_url)

    check(
        "FTF-1301",
        "the router's own database session is built from the restricted "
        "sovra_app role's connection URL, never the migration-owner role",
        app_engine.url.username == APP_ROLE_NAME
        and app_engine.url.username != owner_engine.url.username,
    )

    with Session(owner_engine) as session:
        org1 = Organization(name="Acme 1301", slug=f"acme-1301-{uuid.uuid4().hex[:8]}")
        org2 = Organization(name="Globex 1301", slug=f"globex-1301-{uuid.uuid4().hex[:8]}")
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
        session.add_all([project1, project2])
        session.commit()
        org1_id, org2_id = org1.id, org2.id
        project1_id = project1.id
        project2_id = project2.id

    fixture = _Fixture({"org1": org1_id, "org2": org2_id})
    session_factory = sessionmaker(bind=app_engine)
    app = FastAPI()
    app.include_router(
        build_cost_profile_router(
            session_factory,
            principal_resolver=fixture.principal_resolver,
            role_lookup=fixture.role_lookup,
        )
    )

    org1_admin = fixture.header_for("org1", Role.ORGANIZATION_ADMIN)
    org1_engineer = fixture.header_for("org1", Role.ML_ENGINEER)
    org2_admin = fixture.header_for("org2", Role.ORGANIZATION_ADMIN)

    def collection_path(org_id: uuid.UUID) -> str:
        return f"/api/v1/organizations/{org_id}/cost-profiles"

    def item_path(org_id: uuid.UUID, cost_profile_id: str) -> str:
        return f"{collection_path(org_id)}/{cost_profile_id}"

    def verify_path(org_id: uuid.UUID, cost_profile_id: str) -> str:
        return f"{item_path(org_id, cost_profile_id)}/verify"

    def search_path(org_id: uuid.UUID, project_id: uuid.UUID) -> str:
        return f"/api/v1/organizations/{org_id}/projects/{project_id}/costs/cost-per-success/search"

    now = datetime.now(UTC)

    with _running_app(app) as base_url:
        # -- Unauthenticated / role-boundary denial (Section 45: admin-only,
        # including reads) --
        r = _request(base_url, "GET", collection_path(org1_id))
        check("FTF-1301", "an unauthenticated GET is denied (401)", r.status == 401)
        r = _request(base_url, "GET", collection_path(org1_id), principal=org1_engineer)
        check(
            "FTF-1301",
            "an ml_engineer cannot even list cost profiles (403) -- Section 45 "
            "names cost profiles as organization_admin-only, including reads",
            r.status == 403,
        )
        r = _request(
            base_url,
            "POST",
            collection_path(org1_id),
            principal=org1_engineer,
            payload={
                "profile_type": "provider",
                "label": "x",
                "rate_unit": "per_input_token",
                "rate_amount": "0.001",
                "currency": "USD",
                "source": "s",
                "rate_recorded_at": now.isoformat(),
            },
        )
        check("FTF-1301", "an ml_engineer cannot create a cost profile (403)", r.status == 403)

        # -- Real create: acceptance clause 1 --
        r = _request(
            base_url,
            "POST",
            collection_path(org1_id),
            principal=org1_admin,
            payload={
                "profile_type": "provider",
                "label": "OpenAI gpt-4o-mini",
                "rate_unit": "per_input_token",
                "rate_amount": "0.00000015",
                "currency": "USD",
                "source": "https://openai.com/api/pricing",
                "rate_recorded_at": now.isoformat(),
            },
        )
        check("FTF-1301", "a real create succeeds (201)", r.status == 201)
        body = r.json()
        check(
            "FTF-1301",
            "the real persisted rate/currency/source/profile_type round-trip exactly",
            body.get("rate_amount") == "0.0000001500"
            and body.get("currency") == "USD"
            and body.get("source") == "https://openai.com/api/pricing"
            and body.get("profile_type") == "provider",
        )
        check(
            "FTF-1301",
            "a freshly created rate observation is unverified",
            body.get("verified_at") is None,
        )
        provider_profile_id = body.get("id")

        # -- Cross-organization IDOR --
        r = _request(base_url, "GET", item_path(org2_id, provider_profile_id), principal=org2_admin)
        check(
            "FTF-1301",
            "IDOR: org2 cannot reach org1's cost profile via its own path (404)",
            r.status == 404,
        )

        # -- Negative acceptance at the API boundary: negative rate refused --
        r = _request(
            base_url,
            "POST",
            collection_path(org1_id),
            principal=org1_admin,
            payload={
                "profile_type": "provider",
                "label": "bad",
                "rate_unit": "per_input_token",
                "rate_amount": "-1",
                "currency": "USD",
                "source": "s",
                "rate_recorded_at": now.isoformat(),
            },
        )
        check("FTF-1301", "a negative rate is refused (400)", r.status == 400)

        # -- Search: before verification, the real rate is disqualified as
        # 'unverified', never silently hidden from the result set --
        r = _request(base_url, "GET", search_path(org1_id, project1_id), principal=org1_admin)
        check("FTF-1301", "search succeeds (200)", r.status == 200)
        search_body = r.json()
        matching = [row for row in search_body["results"] if row["id"] == provider_profile_id]
        check(
            "FTF-1301",
            "the unverified rate is returned by search, disqualified with an honest reason "
            "-- never silently dropped",
            len(matching) == 1
            and matching[0]["certifiable_cheapest"] is False
            and matching[0]["disqualified_reason"] == "unverified",
        )
        check(
            "FTF-1301",
            "display unknowns honestly: current_cheapest_certified is null, never a "
            "fabricated fallback, when nothing yet qualifies",
            search_body["current_cheapest_certified"] is None,
        )

        # -- Cross-organization IDOR on search (org2 cannot search via org1's project) --
        r = _request(base_url, "GET", search_path(org2_id, project1_id), principal=org2_admin)
        check(
            "FTF-1301",
            "IDOR: org2 cannot search cost profiles via org1's own project_id (404)",
            r.status == 404,
        )

        # -- Verify: an admin independently re-confirms the rate --
        r = _request(
            base_url, "POST", verify_path(org1_id, provider_profile_id), principal=org1_admin
        )
        check("FTF-1301", "verify succeeds (200)", r.status == 200)
        check(
            "FTF-1301",
            "verify genuinely advances verified_at",
            r.json().get("verified_at") is not None,
        )

        r = _request(base_url, "GET", search_path(org1_id, project1_id), principal=org1_admin)
        search_body = r.json()
        matching = [row for row in search_body["results"] if row["id"] == provider_profile_id]
        check(
            "FTF-1301",
            "acceptance/negative_acceptance: a verified, unexpired, non-zero provider rate "
            "now certifies cheapest for real",
            matching[0]["certifiable_cheapest"] is True
            and matching[0]["disqualified_reason"] is None,
        )
        check(
            "FTF-1301",
            "current_cheapest_certified now reflects the one real certified rate",
            search_body["current_cheapest_certified"] is not None
            and search_body["current_cheapest_certified"]["id"] == provider_profile_id,
        )

        # -- Negative acceptance: 'token price zero is not local compute
        # free' -- a verified, unexpired *provider* zero rate never certifies --
        r = _request(
            base_url,
            "POST",
            collection_path(org1_id),
            principal=org1_admin,
            payload={
                "profile_type": "provider",
                "label": "Promo tier",
                "rate_unit": "per_input_token",
                "rate_amount": "0",
                "currency": "USD",
                "source": "https://example.com/promo",
                "rate_recorded_at": now.isoformat(),
            },
        )
        zero_provider_id = r.json().get("id")
        _request(base_url, "POST", verify_path(org1_id, zero_provider_id), principal=org1_admin)
        r = _request(
            base_url,
            "GET",
            search_path(org1_id, project1_id) + "?profile_type=provider",
            principal=org1_admin,
        )
        matching = [row for row in r.json()["results"] if row["id"] == zero_provider_id]
        check(
            "FTF-1301",
            "negative_acceptance: a verified, unexpired provider rate of exactly zero "
            "still never certifies cheapest, proven against the real database",
            len(matching) == 1
            and matching[0]["certifiable_cheapest"] is False
            and matching[0]["disqualified_reason"] == "zero_provider_rate_unconfirmed",
        )

        # -- The zero-rate rule is provider-specific: a verified, unexpired
        # local_hardware zero rate DOES certify (proves the rule is not a
        # blanket zero refusal, against the real database) --
        r = _request(
            base_url,
            "POST",
            collection_path(org1_id),
            principal=org1_admin,
            payload={
                "profile_type": "local_hardware",
                "label": "Amortized A100 node",
                "rate_unit": "per_input_token",
                "rate_amount": "0",
                "currency": "USD",
                "source": "internal TCO worksheet",
                "rate_recorded_at": now.isoformat(),
            },
        )
        zero_local_id = r.json().get("id")
        _request(base_url, "POST", verify_path(org1_id, zero_local_id), principal=org1_admin)
        r = _request(base_url, "GET", search_path(org1_id, project1_id), principal=org1_admin)
        matching = [row for row in r.json()["results"] if row["id"] == zero_local_id]
        check(
            "FTF-1301",
            "a verified, unexpired local_hardware zero rate certifies cheapest for real "
            "(the zero-rate rule is provider-specific, not a blanket refusal)",
            len(matching) == 1
            and matching[0]["certifiable_cheapest"] is True
            and matching[0]["disqualified_reason"] is None,
        )

        # -- Expired takes priority even once verified --
        r = _request(
            base_url,
            "POST",
            collection_path(org1_id),
            principal=org1_admin,
            payload={
                "profile_type": "provider",
                "label": "Stale quote",
                "rate_unit": "per_output_token",
                "rate_amount": "0.0000003",
                "currency": "USD",
                "source": "https://example.com/stale",
                "rate_recorded_at": (now - timedelta(days=100)).isoformat(),
                "expires_at": (now - timedelta(days=1)).isoformat(),
            },
        )
        expired_id = r.json().get("id")
        _request(base_url, "POST", verify_path(org1_id, expired_id), principal=org1_admin)
        r = _request(base_url, "GET", search_path(org1_id, project1_id), principal=org1_admin)
        matching = [row for row in r.json()["results"] if row["id"] == expired_id]
        check(
            "FTF-1301",
            "negative_acceptance: an expired rate cannot certify cheapest even once "
            "verified, proven against the real database",
            len(matching) == 1
            and matching[0]["certifiable_cheapest"] is False
            and matching[0]["disqualified_reason"] == "expired",
        )

        # -- current_cheapest_certified picks the real minimum among
        # certified candidates, never an arbitrary one --
        r = _request(
            base_url,
            "POST",
            collection_path(org1_id),
            principal=org1_admin,
            payload={
                "profile_type": "provider",
                "label": "Cheaper competitor",
                "rate_unit": "per_input_token",
                "rate_amount": "0.00000005",
                "currency": "USD",
                "source": "https://example.com/cheaper",
                "rate_recorded_at": now.isoformat(),
            },
        )
        cheaper_id = r.json().get("id")
        _request(base_url, "POST", verify_path(org1_id, cheaper_id), principal=org1_admin)
        r = _request(
            base_url,
            "GET",
            search_path(org1_id, project1_id) + "?profile_type=provider&rate_unit=per_input_token",
            principal=org1_admin,
        )
        r_body = r.json()
        check(
            "FTF-1301",
            "current_cheapest_certified is the real minimum rate among certified "
            "candidates (the new, cheaper, verified rate), not the first created",
            r_body["current_cheapest_certified"] is not None
            and r_body["current_cheapest_certified"]["id"] == cheaper_id,
        )

        # -- Real missing-tenant-context proof (FTF-H003's own real guard) --
        with Session(app_engine) as session:
            raw_context = TenantContext(organization_id=org1_id)
            try:
                authorize_resource_ownership(
                    session,
                    raw_context,
                    CostProfile.__table__,
                    CostProfile.__table__.c.id,
                    CostProfile.__table__.c.organization_id,
                    uuid.UUID(provider_profile_id) if provider_profile_id else uuid.uuid4(),
                )
                check(
                    "FTF-1301",
                    "a session with no tenant context ever bound is refused "
                    "(TenantContextError), not treated as an ordinary zero-row denial",
                    False,
                )
            except TenantContextError:
                check(
                    "FTF-1301",
                    "a session with no tenant context ever bound is refused "
                    "(TenantContextError), not treated as an ordinary zero-row denial",
                    True,
                )

        # -- Real RLS backstop: even a raw, unfiltered SELECT by ID under
        # org1's own tenant context returns zero rows for org2's own data
        # (FORCE ROW LEVEL SECURITY is genuinely enforced by Postgres
        # itself, not merely by this task's own WHERE clause) --
        r = _request(
            base_url,
            "POST",
            collection_path(org2_id),
            principal=org2_admin,
            payload={
                "profile_type": "provider",
                "label": "Globex-only rate",
                "rate_unit": "per_hour",
                "rate_amount": "5",
                "currency": "USD",
                "source": "https://example.com/globex",
                "rate_recorded_at": now.isoformat(),
            },
        )
        globex_profile_id = r.json().get("id")
        with Session(app_engine) as session:
            session.execute(
                text("SELECT set_config('app.current_organization_id', :org_id, true)"),
                {"org_id": str(org1_id)},
            )
            row = session.execute(
                text("SELECT 1 FROM cost_profiles WHERE id = :id"), {"id": globex_profile_id}
            ).scalar_one_or_none()
            check(
                "FTF-1301",
                "FORCE ROW LEVEL SECURITY is real: a raw SELECT by ID under org1's own "
                "tenant context returns zero rows for org2's own cost profile",
                row is None,
            )

        # -- Grant-surface proof: sovra_app genuinely cannot DELETE a
        # cost_profiles row directly (never removed, only ever recorded/verified). --
        with Session(app_engine) as session:
            session.execute(
                text("SELECT set_config('app.current_organization_id', :org_id, true)"),
                {"org_id": str(org1_id)},
            )
            try:
                session.execute(
                    text("DELETE FROM cost_profiles WHERE id = :id"), {"id": provider_profile_id}
                )
                session.commit()
                check(
                    "FTF-1301",
                    "the restricted sovra_app role cannot DELETE a cost_profiles row directly",
                    False,
                )
            except DBAPIError:
                session.rollback()
                check(
                    "FTF-1301",
                    "the restricted sovra_app role cannot DELETE a cost_profiles row directly",
                    True,
                )

    # -- Real DB-level backstop: CHECK(rate_amount >= 0) refuses a negative
    # rate even bypassing this task's own application-level validation. --
    with Session(owner_engine) as session:
        session.execute(text("SET row_security = off"))
        try:
            session.execute(
                text(
                    "INSERT INTO cost_profiles "
                    "(organization_id, profile_type, label, rate_unit, rate_amount, "
                    "currency, source, rate_recorded_at, created_by) "
                    "VALUES (:org_id, 'provider', 'raw-insert', 'per_hour', -1, 'USD', "
                    "'s', now(), 'raw-insert')"
                ),
                {"org_id": str(org1_id)},
            )
            session.commit()
            check(
                "FTF-1301",
                "the real CHECK(rate_amount >= 0) constraint refuses a negative rate, "
                "even at the raw SQL level",
                False,
            )
        except IntegrityError:
            session.rollback()
            check(
                "FTF-1301",
                "the real CHECK(rate_amount >= 0) constraint refuses a negative rate, "
                "even at the raw SQL level",
                True,
            )

    _ = project2_id  # seeded for realism; not referenced directly beyond fixture wiring

    if FAILURES:
        print(f"FTF-1301 real CostProfile proof: {len(FAILURES)} failed")
        for failure in FAILURES:
            print(f"  {failure}")
        sys.exit(1)

    print("FTF-1301 real CostProfile proof: all checks passed")
    sys.exit(0)


if __name__ == "__main__":
    main()
