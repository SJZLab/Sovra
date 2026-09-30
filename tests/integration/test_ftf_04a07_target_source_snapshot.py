#!/usr/bin/env python3
"""FTF-04A07 -- real Postgres TargetSourceSnapshot schema/API proof.

Proves this task's own acceptance clause 1 ("Store immutable snapshot
provenance, source identity, status and superseded relationship") and
negative_acceptance ("Existing snapshot cannot be mutated or reassigned
to another repository/project") against a real, live PostgreSQL server --
never a mocked connection or an in-memory SQLite stand-in (SQLite has no
row-level security at all, so a passing test against it would prove
nothing about this task's actual acceptance criterion).

Two real organizations (org1/org2), each with a real workspace and
project, are seeded as the migration-owner role, along with three real
`TargetApplicationConnection` rows (FTF-04A01, already-CERTIFIED): two
belonging to org1 (so a snapshot can be shown refusing to supersede a
*different* connection's own snapshot within the *same* organization) and
one belonging to org2 (the cross-tenant IDOR case). Every request below
runs through a real uvicorn server, with the router's own database
session bound to the real, distinct, least-privileged `sovra_app` role
FTF-0102's migration creates (never the migration-owner role -- confirmed
directly below), exactly like every prior integration test in this chain.

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
    python3 tests/integration/test_ftf_04a07_target_source_snapshot.py
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
from app.target_connectors.target_application_connection_schema import (  # noqa: E402
    TargetApplicationConnection,
)
from app.target_connectors.target_source_snapshot_routes import (  # noqa: E402
    build_target_source_snapshot_router,
)

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
# organization_admin and one evaluator per organization ---------------------

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
        "FTF-04A07",
        f"alembic upgrade head runs cleanly (exit {migration_result.returncode})\n"
        f"stdout: {migration_result.stdout}\nstderr: {migration_result.stderr}",
        migration_result.returncode == 0,
    )
    if FAILURES:
        print(f"FTF-04A07 real TargetSourceSnapshot schema/API proof: {len(FAILURES)} failed")
        for failure in FAILURES:
            print(f"  {failure}")
        sys.exit(1)

    owner_engine = create_engine(migration_url)
    app_url = _app_role_url(migration_url, app_password)
    app_engine = create_engine(app_url)

    check(
        "FTF-04A07",
        "the router's own database session is built from the restricted "
        "sovra_app role's connection URL, never the migration-owner role",
        app_engine.url.username == APP_ROLE_NAME
        and app_engine.url.username != owner_engine.url.username,
    )

    # -- Seed two real, distinct tenants, each with one workspace and one
    # real project, plus three real TargetApplicationConnection rows: two
    # for org1 (to prove cross-connection reassignment is refused within
    # the SAME organization) and one for org2 (the cross-tenant IDOR case)
    # (migration-owner role, seeding only) --
    now = datetime.now(UTC)
    with Session(owner_engine) as session:
        org1 = Organization(name="Acme 04A07", slug=f"acme-04a07-{uuid.uuid4().hex[:8]}")
        org2 = Organization(name="Globex 04A07", slug=f"globex-04a07-{uuid.uuid4().hex[:8]}")
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
        connection1b = TargetApplicationConnection(
            organization_id=org1.id,
            project_id=project1.id,
            connector_type="local_repository",
            display_name="acme-secondary-repo",
            repository_identity="acme/secondary",
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
        session.add_all([connection1, connection1b, connection2])
        session.commit()

        org1_id, org2_id = org1.id, org2.id
        project1_id, project2_id = project1.id, project2.id
        connection1_id = connection1.id
        connection1b_id = connection1b.id
        connection2_id = connection2.id

    fixture = _Fixture({"org1": org1_id, "org2": org2_id})
    session_factory = sessionmaker(bind=app_engine)
    app = FastAPI()
    app.include_router(
        build_target_source_snapshot_router(
            session_factory,
            principal_resolver=fixture.principal_resolver,
            role_lookup=fixture.role_lookup,
        )
    )

    org1_admin = fixture.header_for("org1", Role.ORGANIZATION_ADMIN)
    org1_evaluator = fixture.header_for("org1", Role.EVALUATOR)
    org2_admin = fixture.header_for("org2", Role.ORGANIZATION_ADMIN)

    def collection_path(org_id: uuid.UUID, project_id: uuid.UUID, connection_id: uuid.UUID) -> str:
        return (
            f"/api/v1/organizations/{org_id}/projects/{project_id}"
            f"/target-connections/{connection_id}/snapshots"
        )

    def item_path(
        org_id: uuid.UUID, project_id: uuid.UUID, connection_id: uuid.UUID, snapshot_id: object
    ) -> str:
        return f"{collection_path(org_id, project_id, connection_id)}/{snapshot_id}"

    real_commit_sha_1 = "1" * 40
    real_tree_hash_1 = "2" * 40
    real_commit_sha_2 = "3" * 40
    real_tree_hash_2 = "4" * 40

    snapshot1_id: str | None = None

    with _running_app(app) as base_url:
        # -- Unauthenticated / role-boundary denial --
        r = _request(base_url, "GET", collection_path(org1_id, project1_id, connection1_id))
        check("FTF-04A07", "an unauthenticated list request is denied (401)", r.status == 401)

        r = _request(
            base_url,
            "GET",
            collection_path(org1_id, project1_id, connection1_id),
            principal=org1_evaluator,
        )
        check(
            "FTF-04A07",
            "an evaluator (a role Section 45 names, with no dedicated read "
            "sentence for snapshots) is denied read (403) -- this resource "
            "is gated to organization_admin alone",
            r.status == 403,
        )

        r = _request(
            base_url,
            "POST",
            collection_path(org1_id, project1_id, connection1_id),
            principal=org1_evaluator,
            payload={
                "commit_sha": real_commit_sha_1,
                "tree_hash": real_tree_hash_1,
                "capture_method": "github_resolved_commit",
            },
        )
        check(
            "FTF-04A07",
            "an evaluator is denied create (403)",
            r.status == 403,
        )

        # -- connection ownership: a connection_id from a different
        # organization is refused identically to a missing one (404) --
        r = _request(
            base_url,
            "GET",
            collection_path(org2_id, project2_id, connection1_id),
            principal=org2_admin,
        )
        check(
            "FTF-04A07",
            "the core IDOR case: org1's own real connection, requested "
            "under org2's own path by a real org2 organization_admin, is "
            "denied (404) identically to missing",
            r.status == 404,
        )

        # -- acceptance: a real github_resolved_commit snapshot is created --
        r = _request(
            base_url,
            "POST",
            collection_path(org1_id, project1_id, connection1_id),
            principal=org1_admin,
            payload={
                "commit_sha": real_commit_sha_1,
                "tree_hash": real_tree_hash_1,
                "branch": "main",
                "capture_method": "github_resolved_commit",
            },
        )
        check("FTF-04A07", "a real, valid snapshot create succeeds (201)", r.status == 201)
        snapshot1 = r.json() if r.status == 201 else {}
        check(
            "FTF-04A07",
            "acceptance: the created snapshot carries its real provenance "
            "fields and starts at the single Release-1 analysis_status",
            isinstance(snapshot1, dict)
            and snapshot1.get("commit_sha") == real_commit_sha_1
            and snapshot1.get("tree_hash") == real_tree_hash_1
            and snapshot1.get("capture_method") == "github_resolved_commit"
            and snapshot1.get("analysis_status") == "not_analyzed"
            and snapshot1.get("supersedes_snapshot_id") is None,
        )
        snapshot1_id = snapshot1.get("id")

        # -- acceptance: a real superseding snapshot, same connection --
        r = _request(
            base_url,
            "POST",
            collection_path(org1_id, project1_id, connection1_id),
            principal=org1_admin,
            payload={
                "commit_sha": real_commit_sha_2,
                "tree_hash": real_tree_hash_2,
                "capture_method": "github_resolved_commit",
                "supersedes_snapshot_id": snapshot1_id,
            },
        )
        check(
            "FTF-04A07",
            "acceptance: a real snapshot superseding the prior one, on the "
            "SAME connection, succeeds (201)",
            r.status == 201,
        )
        snapshot2 = r.json() if r.status == 201 else {}
        check(
            "FTF-04A07",
            "the superseding snapshot's own real supersedes_snapshot_id "
            "points at the prior real snapshot",
            snapshot2.get("supersedes_snapshot_id") == snapshot1_id,
        )

        # -- negative_acceptance: "reassigned to another repository" --
        # snapshot1 belongs to connection1; claiming to supersede it under
        # connection1b's OWN path (same organization, different
        # connection) is refused identically to missing.
        r = _request(
            base_url,
            "POST",
            collection_path(org1_id, project1_id, connection1b_id),
            principal=org1_admin,
            payload={
                "commit_sha": real_commit_sha_2,
                "tree_hash": real_tree_hash_2,
                "capture_method": "github_resolved_commit",
                "supersedes_snapshot_id": snapshot1_id,
            },
        )
        check(
            "FTF-04A07",
            "negative_acceptance: a snapshot cannot be reassigned to "
            "another repository -- claiming to supersede connection1's own "
            "real snapshot under connection1b's own path (same "
            "organization, different connection) is refused (404) "
            "identically to a missing reference",
            r.status == 404,
        )

        # -- negative_acceptance: cross-tenant reassignment is refused --
        r = _request(
            base_url,
            "POST",
            collection_path(org2_id, project2_id, connection2_id),
            principal=org2_admin,
            payload={
                "commit_sha": real_commit_sha_2,
                "tree_hash": real_tree_hash_2,
                "capture_method": "github_resolved_commit",
                "supersedes_snapshot_id": snapshot1_id,
            },
        )
        check(
            "FTF-04A07",
            "negative_acceptance: org2 cannot claim to supersede org1's "
            "own real snapshot -- the cross-tenant case is refused (404) "
            "identically to missing",
            r.status == 404,
        )

        # -- negative_acceptance: a dirty snapshot with no archive is refused --
        r = _request(
            base_url,
            "POST",
            collection_path(org1_id, project1_id, connection1_id),
            principal=org1_admin,
            payload={
                "commit_sha": real_commit_sha_2,
                "tree_hash": real_tree_hash_2,
                "capture_method": "local_dirty_snapshot",
            },
        )
        check(
            "FTF-04A07",
            "a local_dirty_snapshot with no archive at all is refused (400)",
            r.status == 400,
        )

        # -- list shows both real snapshots for connection1, oldest first --
        r = _request(
            base_url,
            "GET",
            collection_path(org1_id, project1_id, connection1_id),
            principal=org1_admin,
        )
        listed = r.json() if r.status == 200 else []
        check(
            "FTF-04A07",
            "listing shows both real snapshots for connection1, oldest first",
            isinstance(listed, list)
            and len(listed) == 2
            and [s.get("commit_sha") for s in listed] == [real_commit_sha_1, real_commit_sha_2],
        )

        # -- cross-connection scoping: connection1b has no snapshots of its own --
        r = _request(
            base_url,
            "GET",
            collection_path(org1_id, project1_id, connection1b_id),
            principal=org1_admin,
        )
        check(
            "FTF-04A07",
            "connection1b's own real, separate snapshot list is empty -- "
            "snapshot scoping is genuinely per-connection",
            r.status == 200 and r.json() == [],
        )

        # -- cross-connection get: snapshot1 requested under connection1b's
        # own item path (same organization, different connection) is
        # refused identically to missing --
        r = _request(
            base_url,
            "GET",
            item_path(org1_id, project1_id, connection1b_id, snapshot1_id),
            principal=org1_admin,
        )
        check(
            "FTF-04A07",
            "a real snapshot belonging to connection1, requested under "
            "connection1b's own path in the same organization, is denied "
            "(404) identically to missing",
            r.status == 404,
        )

        # -- IDOR: org2's own organization_admin cannot see org1's snapshot --
        r = _request(
            base_url,
            "GET",
            item_path(org2_id, project2_id, connection2_id, snapshot1_id),
            principal=org2_admin,
        )
        check(
            "FTF-04A07",
            "the core IDOR case: a real snapshot belonging to org1, "
            "requested under org2's own path by a real org2 "
            "organization_admin, is denied (404) identically to missing",
            r.status == 404,
        )

    # -- Real database-level defense-in-depth proof: the CHECK constraint
    # catches a kind-inconsistent row even when the ORM-level check is
    # bypassed entirely (a raw INSERT through sovra_app) --
    with Session(app_engine) as session:
        session.execute(
            text("SELECT set_config('app.current_organization_id', :org_id, true)"),
            {"org_id": str(org1_id)},
        )
        try:
            session.execute(
                text(
                    "INSERT INTO target_source_snapshots "
                    "(organization_id, target_application_connection_id, commit_sha, "
                    "tree_hash, capture_method) "
                    "VALUES (:org_id, :connection_id, :commit_sha, :tree_hash, "
                    "'local_dirty_snapshot')"
                ),
                {
                    "org_id": str(org1_id),
                    "connection_id": str(connection1_id),
                    "commit_sha": "5" * 40,
                    "tree_hash": "6" * 40,
                },
            )
            session.commit()
            check(
                "FTF-04A07",
                "negative_acceptance/defense-in-depth: a raw INSERT that "
                "bypasses the ORM's own __init__ check but still claims a "
                "local_dirty_snapshot with NO archive is refused directly "
                "by the real ck_target_source_snapshots_dirty_requires_archive "
                "CHECK constraint",
                False,
            )
        except DBAPIError:
            session.rollback()
            check(
                "FTF-04A07",
                "negative_acceptance/defense-in-depth: a raw INSERT that "
                "bypasses the ORM's own __init__ check but still claims a "
                "local_dirty_snapshot with NO archive is refused directly "
                "by the real ck_target_source_snapshots_dirty_requires_archive "
                "CHECK constraint",
                True,
            )

    # -- Real database-level proof: the self-referencing composite foreign
    # key refuses a cross-tenant supersession even via a raw INSERT that
    # bypasses the CRUD layer's own application-level check entirely --
    with Session(app_engine) as session:
        session.execute(
            text("SELECT set_config('app.current_organization_id', :org_id, true)"),
            {"org_id": str(org2_id)},
        )
        try:
            session.execute(
                text(
                    "INSERT INTO target_source_snapshots "
                    "(organization_id, target_application_connection_id, commit_sha, "
                    "tree_hash, capture_method, supersedes_snapshot_id) "
                    "VALUES (:org_id, :connection_id, :commit_sha, :tree_hash, "
                    "'github_resolved_commit', :supersedes_id)"
                ),
                {
                    "org_id": str(org2_id),
                    "connection_id": str(connection2_id),
                    "commit_sha": "7" * 40,
                    "tree_hash": "8" * 40,
                    "supersedes_id": snapshot1_id,
                },
            )
            session.commit()
            check(
                "FTF-04A07",
                "negative_acceptance/defense-in-depth: a raw INSERT claiming "
                "org2's own new row supersedes org1's real snapshot is "
                "refused directly by the real self-referencing composite "
                "foreign key (organization_id, supersedes_snapshot_id) -- "
                "no (org2_id, snapshot1_id) row exists in this table",
                False,
            )
        except DBAPIError:
            session.rollback()
            check(
                "FTF-04A07",
                "negative_acceptance/defense-in-depth: a raw INSERT claiming "
                "org2's own new row supersedes org1's real snapshot is "
                "refused directly by the real self-referencing composite "
                "foreign key (organization_id, supersedes_snapshot_id) -- "
                "no (org2_id, snapshot1_id) row exists in this table",
                True,
            )

    # -- Grant-surface proof: sovra_app genuinely cannot UPDATE a
    # target_source_snapshots row directly -- Section 6.8B's "Immutable
    # source snapshot" enforced at the database privilege level. --
    if snapshot1_id:
        with Session(app_engine) as session:
            session.execute(
                text("SELECT set_config('app.current_organization_id', :org_id, true)"),
                {"org_id": str(org1_id)},
            )
            try:
                session.execute(
                    text("UPDATE target_source_snapshots SET commit_sha = :sha WHERE id = :id"),
                    {"sha": "9" * 40, "id": snapshot1_id},
                )
                session.commit()
                check(
                    "FTF-04A07",
                    "negative_acceptance/grant-surface: the restricted "
                    "sovra_app role cannot UPDATE a target_source_snapshots "
                    "row directly -- every snapshot is immutable",
                    False,
                )
            except DBAPIError:
                session.rollback()
                check(
                    "FTF-04A07",
                    "negative_acceptance/grant-surface: the restricted "
                    "sovra_app role cannot UPDATE a target_source_snapshots "
                    "row directly -- every snapshot is immutable",
                    True,
                )

            try:
                session.execute(
                    text("DELETE FROM target_source_snapshots WHERE id = :id"),
                    {"id": snapshot1_id},
                )
                session.commit()
                check(
                    "FTF-04A07",
                    "negative_acceptance/grant-surface: the restricted "
                    "sovra_app role cannot DELETE a target_source_snapshots "
                    "row directly either",
                    False,
                )
            except DBAPIError:
                session.rollback()
                check(
                    "FTF-04A07",
                    "negative_acceptance/grant-surface: the restricted "
                    "sovra_app role cannot DELETE a target_source_snapshots "
                    "row directly either",
                    True,
                )

    if FAILURES:
        print(f"FTF-04A07 real TargetSourceSnapshot schema/API proof: {len(FAILURES)} failed")
        for failure in FAILURES:
            print(f"  {failure}")
        sys.exit(1)

    print("FTF-04A07 real TargetSourceSnapshot schema/API proof: all checks passed")
    sys.exit(0)


if __name__ == "__main__":
    main()
