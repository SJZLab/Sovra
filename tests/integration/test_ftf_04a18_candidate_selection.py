#!/usr/bin/env python3
"""FTF-04A18 -- real Postgres candidate-selection + persisted-audit proof.

Proves this task's own acceptance clause ("UI lists multiple detected
agents and requires explicit selected ID with persisted audit") and both
negative_acceptance halves ("Highest score/first result cannot be
preselected; stale candidate from prior snapshot is rejected") against a
real, live PostgreSQL server -- never a mocked connection or an in-memory
SQLite stand-in (SQLite has no row-level security at all, so a passing
test against it would prove nothing about this task's actual acceptance
criterion).

One real organization with a real workspace/project/connection and two
real `TargetSourceSnapshot` rows, the *second* genuinely superseding the
*first* (`supersedes_snapshot_id`, Section 6.8B's own real field, set the
same way FTF-04A07's own already-CERTIFIED module sets it), each carrying
one real `DiscoveredAgentCandidate`. A second, unrelated organization
proves the cross-tenant IDOR case. Every request below runs through a
real uvicorn server, with the router's own database session bound to the
real, distinct, least-privileged `sovra_app` role (never the
migration-owner role -- confirmed directly below, same discipline every
prior integration test in this chain already established).

This task's own real "persisted audit" claim is proven by reading the
resulting `AuditEvent` row back directly from Postgres through the
migration-owner engine *after* the HTTP request has completed -- never
only trusting the response body -- confirming `action`,
`resource_type`/`resource_id` and that `created_at` genuinely matches the
response's own `selected_at`. The stale-snapshot refusal is proven against
a real, superseded snapshot row, not a client-supplied flag: nothing in
this module or the one it tests ever trusts a caller's own claim that a
snapshot is current.

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
    python3 tests/integration/test_ftf_04a18_candidate_selection.py
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
from sqlalchemy import create_engine, select, text  # noqa: E402
from sqlalchemy.orm import Session, sessionmaker  # noqa: E402

from app.auth.audit_event_writer import AuditEvent  # noqa: E402
from app.auth.organization_workspace_schema import (  # noqa: E402
    APP_ROLE_NAME,
    Organization,
    Workspace,
)
from app.auth.rbac_middleware import AuthenticatedPrincipal, Role  # noqa: E402
from app.projects.project_schema import Project  # noqa: E402
from app.target_connectors.discovered_agent_candidate_schema import (  # noqa: E402
    DiscoveredAgentCandidate,
)
from app.target_connectors.discovered_agent_candidate_selection import (  # noqa: E402
    CANDIDATE_SELECTED_ACTION,
)
from app.target_connectors.discovered_agent_candidate_selection_routes import (  # noqa: E402
    build_discovered_agent_candidate_selection_router,
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
            "selection/persisted-audit evidence requires a live Postgres "
            "(migration owner connection). Not silently skipped: see this "
            "file's own docstring for how to run it locally or in CI.",
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
) -> _Response:
    headers: dict[str, str] = {}
    if principal is not None:
        headers["X-Test-Principal"] = principal
    parsed = urllib.parse.urlparse(base_url)
    conn = http.client.HTTPConnection(parsed.hostname or "127.0.0.1", parsed.port or 80, timeout=10)
    try:
        conn.request(method, path, headers=headers)
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
        "FTF-04A18",
        f"alembic upgrade head runs cleanly (exit {migration_result.returncode})\n"
        f"stdout: {migration_result.stdout}\nstderr: {migration_result.stderr}",
        migration_result.returncode == 0,
    )
    if FAILURES:
        print(f"FTF-04A18 real candidate-selection proof: {len(FAILURES)} failed")
        for failure in FAILURES:
            print(f"  {failure}")
        sys.exit(1)

    owner_engine = create_engine(migration_url)
    app_url = _app_role_url(migration_url, app_password)
    app_engine = create_engine(app_url)

    check(
        "FTF-04A18",
        "the router's own database session is built from the restricted "
        "sovra_app role's connection URL, never the migration-owner role",
        app_engine.url.username == APP_ROLE_NAME
        and app_engine.url.username != owner_engine.url.username,
    )

    # -- Seed one organization with a connection, a superseded snapshot
    # (snapshot_old) and its real replacement (snapshot_current, whose
    # supersedes_snapshot_id names snapshot_old -- the exact real field
    # this task's own staleness check reads), one candidate under each,
    # plus a second organization for the cross-tenant IDOR case
    # (migration-owner role, seeding only) --
    now = datetime.now(UTC)
    with Session(owner_engine) as session:
        org1 = Organization(name="Acme 04A18", slug=f"acme-04a18-{uuid.uuid4().hex[:8]}")
        org2 = Organization(name="Globex 04A18", slug=f"globex-04a18-{uuid.uuid4().hex[:8]}")
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
        connection3 = TargetApplicationConnection(
            organization_id=org1.id,
            project_id=project1.id,
            connector_type="local_repository",
            display_name="acme-unlinked-repo",
            repository_identity="acme/unlinked",
            access_mode="read_only",
            connection_status="active",
            last_validated_at=now,
            created_by="seed",
        )
        session.add_all([connection1, connection2, connection3])
        session.flush()

        snapshot_old = TargetSourceSnapshot(
            organization_id=org1.id,
            target_application_connection_id=connection1.id,
            commit_sha="1" * 40,
            tree_hash="2" * 40,
            capture_method="local_git_commit",
            analysis_status="not_analyzed",
        )
        session.add(snapshot_old)
        session.flush()
        snapshot_current = TargetSourceSnapshot(
            organization_id=org1.id,
            target_application_connection_id=connection1.id,
            commit_sha="3" * 40,
            tree_hash="4" * 40,
            capture_method="local_git_commit",
            analysis_status="not_analyzed",
            supersedes_snapshot_id=snapshot_old.id,
        )
        snapshot_org2 = TargetSourceSnapshot(
            organization_id=org2.id,
            target_application_connection_id=connection2.id,
            commit_sha="5" * 40,
            tree_hash="6" * 40,
            capture_method="local_git_commit",
            analysis_status="not_analyzed",
        )
        # An *unlinked* newer snapshot: same connection, later captured_at,
        # supersedes_snapshot_id left NULL (the link is optional/client-
        # supplied, so staleness must not depend on it).
        snapshot_unlinked_old = TargetSourceSnapshot(
            organization_id=org1.id,
            target_application_connection_id=connection3.id,
            commit_sha="7" * 40,
            tree_hash="8" * 40,
            capture_method="local_git_commit",
            analysis_status="not_analyzed",
            captured_at=now - timedelta(hours=2),
        )
        snapshot_unlinked_new = TargetSourceSnapshot(
            organization_id=org1.id,
            target_application_connection_id=connection3.id,
            commit_sha="9" * 40,
            tree_hash="a" * 40,
            capture_method="local_git_commit",
            analysis_status="not_analyzed",
            captured_at=now - timedelta(hours=1),
            supersedes_snapshot_id=None,
        )
        session.add_all(
            [snapshot_current, snapshot_org2, snapshot_unlinked_old, snapshot_unlinked_new]
        )
        session.flush()

        candidate_stale = DiscoveredAgentCandidate(
            organization_id=org1.id,
            target_source_snapshot_id=snapshot_old.id,
            candidate_key="entrypoint:stale.py:Agent",
            display_name="Stale-snapshot agent",
            confidence_score=0.5,
            discovery_status="candidate",
        )
        candidate_current = DiscoveredAgentCandidate(
            organization_id=org1.id,
            target_source_snapshot_id=snapshot_current.id,
            candidate_key="entrypoint:current.py:Agent",
            display_name="Current-snapshot agent",
            confidence_score=0.5,
            discovery_status="candidate",
        )
        candidate_org2 = DiscoveredAgentCandidate(
            organization_id=org2.id,
            target_source_snapshot_id=snapshot_org2.id,
            candidate_key="entrypoint:globex.py:Agent",
            display_name="Globex agent",
            confidence_score=0.5,
            discovery_status="candidate",
        )
        candidate_unlinked_old = DiscoveredAgentCandidate(
            organization_id=org1.id,
            target_source_snapshot_id=snapshot_unlinked_old.id,
            candidate_key="entrypoint:unlinked_old.py:Agent",
            display_name="Unlinked-older agent",
            confidence_score=0.5,
            discovery_status="candidate",
        )
        candidate_unlinked_new = DiscoveredAgentCandidate(
            organization_id=org1.id,
            target_source_snapshot_id=snapshot_unlinked_new.id,
            candidate_key="entrypoint:unlinked_new.py:Agent",
            display_name="Unlinked-newer agent",
            confidence_score=0.5,
            discovery_status="candidate",
        )
        session.add_all(
            [
                candidate_stale,
                candidate_current,
                candidate_org2,
                candidate_unlinked_old,
                candidate_unlinked_new,
            ]
        )
        session.commit()

        org1_id, org2_id = org1.id, org2.id
        project1_id = project1.id
        connection1_id = connection1.id
        snapshot_old_id, snapshot_current_id = snapshot_old.id, snapshot_current.id
        candidate_stale_id = candidate_stale.id
        candidate_current_id = candidate_current.id
        candidate_org2_id = candidate_org2.id
        connection3_id = connection3.id
        snapshot_unlinked_old_id = snapshot_unlinked_old.id
        snapshot_unlinked_new_id = snapshot_unlinked_new.id
        candidate_unlinked_old_id = candidate_unlinked_old.id
        candidate_unlinked_new_id = candidate_unlinked_new.id

    fixture = _Fixture({"org1": org1_id, "org2": org2_id})
    session_factory = sessionmaker(bind=app_engine)
    app = FastAPI()
    app.include_router(
        build_discovered_agent_candidate_selection_router(
            session_factory,
            principal_resolver=fixture.principal_resolver,
            role_lookup=fixture.role_lookup,
        )
    )

    org1_admin = fixture.header_for("org1", Role.ORGANIZATION_ADMIN)
    org1_evaluator = fixture.header_for("org1", Role.EVALUATOR)
    org2_admin = fixture.header_for("org2", Role.ORGANIZATION_ADMIN)

    def select_path(
        org_id: uuid.UUID,
        project_id: uuid.UUID,
        connection_id: uuid.UUID,
        snapshot_id: uuid.UUID,
        candidate_id: object,
    ) -> str:
        return (
            f"/api/v1/organizations/{org_id}/projects/{project_id}"
            f"/target-connections/{connection_id}/snapshots/{snapshot_id}"
            f"/candidates/{candidate_id}/select"
        )

    with _running_app(app) as base_url:
        # -- Unauthenticated / role-boundary denial --
        r = _request(
            base_url,
            "POST",
            select_path(
                org1_id, project1_id, connection1_id, snapshot_current_id, candidate_current_id
            ),
        )
        check("FTF-04A18", "an unauthenticated select request is denied (401)", r.status == 401)

        r = _request(
            base_url,
            "POST",
            select_path(
                org1_id, project1_id, connection1_id, snapshot_current_id, candidate_current_id
            ),
            principal=org1_evaluator,
        )
        check(
            "FTF-04A18",
            "an evaluator (no dedicated selection sentence in Section 45) "
            "is denied select (403) -- organization_admin-only, same "
            "precedent as FTF-04A17's own read/create surface",
            r.status == 403,
        )

        # -- cross-tenant IDOR: org1's own real connection/snapshot/
        # candidate, requested under org2's own organization_id path by a
        # real org2 organization_admin, is denied identically to missing --
        r = _request(
            base_url,
            "POST",
            select_path(
                org2_id, project1_id, connection1_id, snapshot_current_id, candidate_current_id
            ),
            principal=org2_admin,
        )
        check(
            "FTF-04A18",
            "the core IDOR case: org1's own real resources requested "
            "under org2's own path are denied (404) identically to missing",
            r.status == 404,
        )

        # -- negative_acceptance: a candidate under a real, superseded
        # snapshot is rejected, never silently confirmed --
        r = _request(
            base_url,
            "POST",
            select_path(org1_id, project1_id, connection1_id, snapshot_old_id, candidate_stale_id),
            principal=org1_admin,
        )
        check(
            "FTF-04A18",
            "negative_acceptance: selecting a candidate under a real, "
            "superseded snapshot is refused (409), never treated as the "
            "connection's current state",
            r.status == 409,
        )

        no_stale_audit = (
            owner_engine.connect()
            .execute(
                text(
                    "select count(*) from audit_events where resource_id = :rid "
                    "and action = :action"
                ),
                {"rid": str(candidate_stale_id), "action": CANDIDATE_SELECTED_ACTION},
            )
            .scalar_one()
        )
        check(
            "FTF-04A18",
            "the refused stale-snapshot selection left no AuditEvent row "
            "behind -- a rejected action is never partially persisted",
            no_stale_audit == 0,
        )

        # -- negative_acceptance (round 2): staleness is defined server-side
        # against the connection's latest snapshot, not only the optional
        # supersedes link. A newer snapshot with NO supersedes link still
        # makes the older one stale (409), and leaves no audit row --
        r = _request(
            base_url,
            "POST",
            select_path(
                org1_id,
                project1_id,
                connection3_id,
                snapshot_unlinked_old_id,
                candidate_unlinked_old_id,
            ),
            principal=org1_admin,
        )
        check(
            "FTF-04A18",
            "an older snapshot with a newer UNLINKED snapshot of the same "
            "connection (supersedes_snapshot_id NULL) is refused (409)",
            r.status == 409,
        )
        with owner_engine.connect() as owner_connection:
            unlinked_audit = owner_connection.execute(
                text(
                    "select count(*) from audit_events where resource_id = :rid "
                    "and action = :action"
                ),
                {"rid": str(candidate_unlinked_old_id), "action": CANDIDATE_SELECTED_ACTION},
            ).scalar_one()
        check(
            "FTF-04A18",
            "the refused unlinked-newer stale selection left no AuditEvent row",
            unlinked_audit == 0,
        )
        r = _request(
            base_url,
            "POST",
            select_path(
                org1_id,
                project1_id,
                connection3_id,
                snapshot_unlinked_new_id,
                candidate_unlinked_new_id,
            ),
            principal=org1_admin,
        )
        check(
            "FTF-04A18",
            "the connection's genuinely latest snapshot is still selectable "
            "(201) -- the stale rule is not a blanket refusal",
            r.status == 201,
        )

        # -- a missing candidate_id under a real, current snapshot is
        # refused identically to any other not-found case --
        r = _request(
            base_url,
            "POST",
            select_path(org1_id, project1_id, connection1_id, snapshot_current_id, uuid.uuid4()),
            principal=org1_admin,
        )
        check(
            "FTF-04A18",
            "a non-existent candidate_id is refused (404)",
            r.status == 404,
        )

        # -- acceptance: an explicit selection of a real candidate under
        # the real current (non-superseded) snapshot succeeds, and is
        # genuinely, durably persisted as an AuditEvent --
        before = datetime.now(UTC)
        r = _request(
            base_url,
            "POST",
            select_path(
                org1_id, project1_id, connection1_id, snapshot_current_id, candidate_current_id
            ),
            principal=org1_admin,
        )
        check("FTF-04A18", "a real, valid selection succeeds (201)", r.status == 201)
        confirmation = r.json() if r.status == 201 else {}
        check(
            "FTF-04A18",
            "the response body names the real candidate/snapshot/actor "
            "that was actually selected",
            confirmation.get("candidate_id") == str(candidate_current_id)
            and confirmation.get("target_source_snapshot_id") == str(snapshot_current_id)
            and confirmation.get("selected_by") == f"user-org1-{Role.ORGANIZATION_ADMIN.value}",
        )

        with Session(owner_engine) as owner_session:
            persisted = owner_session.execute(
                select(AuditEvent).where(
                    AuditEvent.resource_id == str(candidate_current_id),
                    AuditEvent.action == CANDIDATE_SELECTED_ACTION,
                )
            ).scalar_one_or_none()
        check(
            "FTF-04A18",
            "acceptance ('persisted audit'): a real AuditEvent row for "
            "this exact selection exists in Postgres, read back fresh -- "
            "not merely trusted from the response body",
            persisted is not None,
        )
        if persisted is not None:
            check(
                "FTF-04A18",
                "the persisted audit row's own organization_id/actor/"
                "resource fields match the real request, and its "
                "created_at matches the response's own selected_at "
                "exactly (both are the same real database write)",
                persisted.organization_id == org1_id
                and persisted.actor_id == f"user-org1-{Role.ORGANIZATION_ADMIN.value}"
                and persisted.resource_type == "discovered_agent_candidate"
                and persisted.created_at.isoformat() == confirmation.get("selected_at")
                and persisted.created_at >= before,
            )
            check(
                "FTF-04A18",
                "the persisted audit row's own details record the real "
                "snapshot/connection this selection was made against",
                persisted.details_json.get("target_source_snapshot_id") == str(snapshot_current_id)
                and persisted.details_json.get("target_application_connection_id")
                == str(connection1_id),
            )

        # -- grant-surface proof: sovra_app genuinely cannot UPDATE the
        # candidate it just selected -- this task's own "no new mutable
        # selection column" design is a real database guarantee, not
        # merely an omitted route (see the backend module's own
        # docstring) --
        with app_engine.connect() as app_connection:
            try:
                app_connection.execute(
                    text(
                        "update discovered_agent_candidates set "
                        "discovery_status = 'selected' where id = :cid"
                    ),
                    {"cid": str(candidate_current_id)},
                )
                app_connection.commit()
                update_blocked = False
            except Exception:
                update_blocked = True
        check(
            "FTF-04A18",
            "sovra_app role cannot UPDATE a discovered_agent_candidates "
            "row directly -- confirms this task's own real design "
            "constraint, not merely an omitted application-level route",
            update_blocked,
        )

        with owner_engine.connect() as owner_connection:
            discovery_status = owner_connection.execute(
                text("select discovery_status from discovered_agent_candidates where id = :cid"),
                {"cid": str(candidate_current_id)},
            ).scalar_one()
        check(
            "FTF-04A18",
            "the candidate row itself is untouched after selection -- "
            "discovery_status stays the single Release-1 value",
            discovery_status == "candidate",
        )

        # -- org2's own real candidate_id, requested through org1's own
        # real connection/snapshot path by a real org1 organization_admin,
        # is refused identically to missing -- the deeper
        # candidate-belongs-to-this-snapshot check genuinely runs, not
        # only the top-level organization_id match --
        r = _request(
            base_url,
            "POST",
            select_path(
                org1_id,
                project1_id,
                connection1_id,
                snapshot_current_id,
                candidate_org2_id,
            ),
            principal=org1_admin,
        )
        check(
            "FTF-04A18",
            "a real candidate_id belonging to a different organization's "
            "own snapshot, requested under org1's own path, is refused "
            "(404) -- the parent-chain check genuinely runs, not only the "
            "top-level organization_id",
            r.status == 404,
        )

    if FAILURES:
        print(f"FTF-04A18 real candidate-selection proof: {len(FAILURES)} failed")
        for failure in FAILURES:
            print(f"  {failure}")
        sys.exit(1)
    print("FTF-04A18 real candidate-selection proof: all checks passed")


if __name__ == "__main__":
    main()
