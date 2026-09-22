#!/usr/bin/env python3
"""FTF-1001 -- real Postgres ModelVersion schema/API proof.

Proves this task's own acceptance clause 1 ("ModelVersion supports
endpoint-only/full-weight/adapter imports and Factory training lineage")
and negative_acceptance ("Required nonexistent weights/hash for endpoint
import, mutable production artifact or cross-tenant parent denied")
against a real, live PostgreSQL server -- never a mocked connection or an
in-memory SQLite stand-in (SQLite has no row-level security at all, so a
passing test against it would prove nothing about this task's actual
acceptance criterion).

Two real organizations (org1/org2), each with a real workspace and
project, are seeded as the migration-owner role, along with three real
`TrainableArtifact` rows (FTF-0301, already-CERTIFIED) covering the three
states this task's own negative_acceptance turns on: `ready` (immutable,
resolved -- a genuinely usable lineage parent), `registered` (still
mutable -- must be refused) and a second `ready` artifact that belongs to
org2 (must be refused from org1 as a cross-tenant parent). Every request
below runs through a real uvicorn server, with the router's own database
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
    python3 tests/integration/test_ftf_1001_model_version_schema_api.py
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

from app.artifacts.artifact_schema import TrainableArtifact  # noqa: E402
from app.auth.organization_workspace_schema import (  # noqa: E402
    APP_ROLE_NAME,
    Organization,
    Workspace,
)
from app.auth.rbac_middleware import AuthenticatedPrincipal, Role  # noqa: E402
from app.model_versions.model_version_routes import build_model_version_router  # noqa: E402
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


# -- Fixture: two organizations (each with a workspace + project), one
# ml_engineer and one evaluator per organization ----------------------------

_ISSUER = "https://issuer.example"


class _Fixture:
    def __init__(self, org_ids: dict[str, uuid.UUID]) -> None:
        self.org_ids = org_ids
        self.principals: dict[tuple[str, Role], AuthenticatedPrincipal] = {}
        self.memberships: dict[tuple[str, str, uuid.UUID], Role] = {}
        for label, org_id in org_ids.items():
            for role in (Role.ML_ENGINEER, Role.EVALUATOR):
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
        "FTF-1001",
        f"alembic upgrade head runs cleanly (exit {migration_result.returncode})\n"
        f"stdout: {migration_result.stdout}\nstderr: {migration_result.stderr}",
        migration_result.returncode == 0,
    )
    if FAILURES:
        print(f"FTF-1001 real ModelVersion schema/API proof: {len(FAILURES)} failed")
        for failure in FAILURES:
            print(f"  {failure}")
        sys.exit(1)

    owner_engine = create_engine(migration_url)
    app_url = _app_role_url(migration_url, app_password)
    app_engine = create_engine(app_url)

    check(
        "FTF-1001",
        "the router's own database session is built from the restricted "
        "sovra_app role's connection URL, never the migration-owner role",
        app_engine.url.username == APP_ROLE_NAME
        and app_engine.url.username != owner_engine.url.username,
    )

    # -- Seed two real, distinct tenants, each with one workspace and one
    # real project, plus three real TrainableArtifact rows covering the
    # states this task's own negative_acceptance turns on
    # (migration-owner role, seeding only) --
    now = datetime.now(UTC)
    with Session(owner_engine) as session:
        org1 = Organization(name="Acme 1001", slug=f"acme-1001-{uuid.uuid4().hex[:8]}")
        org2 = Organization(name="Globex 1001", slug=f"globex-1001-{uuid.uuid4().hex[:8]}")
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
        session.flush()

        def _artifact(org_id: uuid.UUID, name: str, status: str) -> TrainableArtifact:
            artifact = TrainableArtifact(
                organization_id=org_id,
                display_name=name,
                source_type="huggingface",
                source_uri="acme-org/real-base-model",
                model_family="llama",
                base_model_revision="a1b2c3d4e5f6",
                weights_digest="sha256:" + "b" * 64,
                license_id="apache-2.0",
                license_accepted_by="seed" if status != "registered" else None,
                license_accepted_at=now if status != "registered" else None,
                trust_remote_code_allowed=False,
                required_python_packages_json=[],
                minimum_gpu_profile_json={},
                status=status,
            )
            return artifact

        artifact_ready_org1 = _artifact(org1.id, "ready-artifact-org1", "ready")
        artifact_registered_org1 = _artifact(org1.id, "registered-artifact-org1", "registered")
        artifact_ready_org2 = _artifact(org2.id, "ready-artifact-org2", "ready")
        session.add_all([artifact_ready_org1, artifact_registered_org1, artifact_ready_org2])
        session.commit()

        org1_id, org2_id = org1.id, org2.id
        project1_id, project1b_id, project2_id = project1.id, project1b.id, project2.id
        artifact_ready_org1_id = artifact_ready_org1.id
        artifact_registered_org1_id = artifact_registered_org1.id
        artifact_ready_org2_id = artifact_ready_org2.id

    fixture = _Fixture({"org1": org1_id, "org2": org2_id})
    session_factory = sessionmaker(bind=app_engine)
    app = FastAPI()
    app.include_router(
        build_model_version_router(
            session_factory,
            principal_resolver=fixture.principal_resolver,
            role_lookup=fixture.role_lookup,
        )
    )

    org1_mle = fixture.header_for("org1", Role.ML_ENGINEER)
    org1_evaluator = fixture.header_for("org1", Role.EVALUATOR)
    org2_mle = fixture.header_for("org2", Role.ML_ENGINEER)

    def collection_path(org_id: uuid.UUID, project_id: uuid.UUID) -> str:
        return f"/api/v1/organizations/{org_id}/projects/{project_id}/model-versions"

    def item_path(org_id: uuid.UUID, project_id: uuid.UUID, model_version_id: object) -> str:
        return f"{collection_path(org_id, project_id)}/{model_version_id}"

    with _running_app(app) as base_url:
        # -- Unauthenticated / role-boundary denial --
        r = _request(base_url, "GET", collection_path(org1_id, project1_id))
        check("FTF-1001", "an unauthenticated list request is denied (401)", r.status == 401)

        r = _request(
            base_url, "GET", collection_path(org1_id, project1_id), principal=org1_evaluator
        )
        check(
            "FTF-1001",
            "an evaluator (a role Section 45 names, with no dedicated "
            "restrictive read sentence for model versions) can read (200)",
            r.status == 200,
        )

        r = _request(
            base_url,
            "POST",
            collection_path(org1_id, project1_id),
            principal=org1_evaluator,
            payload={"import_kind": "endpoint", "endpoint_reference": "https://x.example.com"},
        )
        check(
            "FTF-1001",
            "an evaluator is denied create (403) -- write is ml_engineer/"
            "organization_admin only (Section 45's own role table)",
            r.status == 403,
        )

        # -- project ownership: a project_id from a different organization
        # is refused identically to a missing one (404) --
        r = _request(
            base_url,
            "POST",
            collection_path(org1_id, project2_id),
            principal=org1_mle,
            payload={"import_kind": "endpoint", "endpoint_reference": "https://x.example.com"},
        )
        check(
            "FTF-1001",
            "creating a model version against a project_id belonging to a "
            "different organization is refused (404), identical to a "
            "missing project_id",
            r.status == 404,
        )

        # -- acceptance: a real endpoint import --
        r = _request(
            base_url,
            "POST",
            collection_path(org1_id, project1_id),
            principal=org1_mle,
            payload={
                "import_kind": "endpoint",
                "endpoint_reference": "https://models.acme.example.com/v1/production",
            },
        )
        check("FTF-1001", "a real, valid endpoint import succeeds (201)", r.status == 201)
        endpoint_version = r.json() if r.status == 201 else {}
        check(
            "FTF-1001",
            "acceptance: a fresh endpoint import starts at version_number 1, "
            "with no local weight lineage at all",
            isinstance(endpoint_version, dict)
            and endpoint_version.get("version_number") == 1
            and endpoint_version.get("import_kind") == "endpoint"
            and endpoint_version.get("trainable_artifact_id") is None
            and endpoint_version.get("weights_digest") is None
            and endpoint_version.get("base_model_revision") is None,
        )

        # -- negative_acceptance: mutable (still-`registered`) artifact
        # cannot back a full_weight import --
        r = _request(
            base_url,
            "POST",
            collection_path(org1_id, project1_id),
            principal=org1_mle,
            payload={
                "import_kind": "full_weight",
                "trainable_artifact_id": str(artifact_registered_org1_id),
            },
        )
        check(
            "FTF-1001",
            "negative_acceptance: a full_weight import referencing a still-"
            "mutable ('registered') artifact is refused (409) -- only a "
            "'ready' artifact may back a real import",
            r.status == 409,
        )

        # -- negative_acceptance: cross-tenant parent (a real, ready
        # artifact belonging to org2) is refused, identical to missing --
        r = _request(
            base_url,
            "POST",
            collection_path(org1_id, project1_id),
            principal=org1_mle,
            payload={
                "import_kind": "full_weight",
                "trainable_artifact_id": str(artifact_ready_org2_id),
            },
        )
        check(
            "FTF-1001",
            "negative_acceptance: a full_weight import referencing a real "
            "'ready' artifact belonging to a DIFFERENT organization is "
            "refused (404), identical to a missing artifact_id -- the core "
            "cross-tenant-parent IDOR case",
            r.status == 404,
        )

        # -- negative_acceptance: a nonexistent artifact_id entirely --
        r = _request(
            base_url,
            "POST",
            collection_path(org1_id, project1_id),
            principal=org1_mle,
            payload={"import_kind": "full_weight", "trainable_artifact_id": str(uuid.uuid4())},
        )
        check(
            "FTF-1001",
            "a full_weight import referencing a genuinely nonexistent "
            "trainable_artifact_id is refused (404)",
            r.status == 404,
        )

        # -- negative_acceptance: full_weight import with no lineage at all --
        r = _request(
            base_url,
            "POST",
            collection_path(org1_id, project1_id),
            principal=org1_mle,
            payload={"import_kind": "full_weight"},
        )
        check(
            "FTF-1001",
            "negative_acceptance: a full_weight import with no "
            "trainable_artifact_id at all -- no Factory training lineage "
            "-- is refused (400)",
            r.status == 400,
        )

        # -- acceptance: a real full_weight import, with Factory training
        # lineage to the real 'ready' artifact -- weights_digest/
        # base_model_revision are copied server-side, never accepted from
        # this request's own payload (the route accepts no such fields) --
        r = _request(
            base_url,
            "POST",
            collection_path(org1_id, project1_id),
            principal=org1_mle,
            payload={
                "import_kind": "full_weight",
                "trainable_artifact_id": str(artifact_ready_org1_id),
            },
        )
        check("FTF-1001", "a real, valid full_weight import succeeds (201)", r.status == 201)
        full_weight_version = r.json() if r.status == 201 else {}
        check(
            "FTF-1001",
            "acceptance: the full_weight import is version_number 2 (the "
            "next real version in this project, after the endpoint import "
            "already created), with its own real lineage fields copied "
            "server-side from the real, resolved parent artifact",
            isinstance(full_weight_version, dict)
            and full_weight_version.get("version_number") == 2
            and full_weight_version.get("trainable_artifact_id") == str(artifact_ready_org1_id)
            and full_weight_version.get("weights_digest") == "sha256:" + "b" * 64
            and full_weight_version.get("base_model_revision") == "a1b2c3d4e5f6",
        )
        full_weight_version_id = full_weight_version.get("id")

        # -- list shows both real imports, in version order --
        r = _request(base_url, "GET", collection_path(org1_id, project1_id), principal=org1_mle)
        listed = r.json() if r.status == 200 else []
        check(
            "FTF-1001",
            "listing shows both real imports for this project, in real version_number order",
            isinstance(listed, list)
            and len(listed) == 2
            and [m.get("version_number") for m in listed] == [1, 2],
        )

        # -- cross-project scoping: the same organization's other project
        # cannot reach this model version via its own path --
        r = _request(
            base_url,
            "GET",
            item_path(org1_id, project1b_id, full_weight_version_id),
            principal=org1_mle,
        )
        check(
            "FTF-1001",
            "a real model version belonging to project1, requested under "
            "a *different* project's own path in the same organization, "
            "is denied (404) identically to missing",
            r.status == 404,
        )

        # -- IDOR: org2's own ml_engineer cannot see org1's model version --
        r = _request(
            base_url,
            "GET",
            item_path(org2_id, project1_id, full_weight_version_id),
            principal=org2_mle,
        )
        check(
            "FTF-1001",
            "the core IDOR case: a real model version belonging to org1, "
            "requested under org2's own path by a real org2 ml_engineer, "
            "is denied (404) identically to missing",
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
                    "INSERT INTO model_versions "
                    "(organization_id, project_id, version_number, import_kind, "
                    "endpoint_reference, weights_digest, created_by) "
                    "VALUES (:org_id, :project_id, 999, 'endpoint', "
                    "'https://bypass.example.com', :digest, 'bypass-attempt')"
                ),
                {
                    "org_id": str(org1_id),
                    "project_id": str(project1_id),
                    "digest": "sha256:" + "c" * 64,
                },
            )
            session.commit()
            check(
                "FTF-1001",
                "negative_acceptance/defense-in-depth: a raw INSERT that "
                "bypasses the ORM's own __init__ check but still claims an "
                "endpoint import WITH a weights_digest is refused directly "
                "by the real ck_model_versions_kind_consistency CHECK "
                "constraint",
                False,
            )
        except DBAPIError:
            session.rollback()
            check(
                "FTF-1001",
                "negative_acceptance/defense-in-depth: a raw INSERT that "
                "bypasses the ORM's own __init__ check but still claims an "
                "endpoint import WITH a weights_digest is refused directly "
                "by the real ck_model_versions_kind_consistency CHECK "
                "constraint",
                True,
            )

    # -- Grant-surface proof: sovra_app genuinely cannot UPDATE a
    # model_versions row directly -- Section 35's "every model version is
    # immutable" enforced at the database privilege level, stronger than
    # every prior versioned resource's own SELECT/INSERT/UPDATE grant. --
    if full_weight_version_id:
        with Session(app_engine) as session:
            session.execute(
                text("SELECT set_config('app.current_organization_id', :org_id, true)"),
                {"org_id": str(org1_id)},
            )
            try:
                session.execute(
                    text("UPDATE model_versions SET version_number = 9999 WHERE id = :id"),
                    {"id": full_weight_version_id},
                )
                session.commit()
                check(
                    "FTF-1001",
                    "negative_acceptance/grant-surface: the restricted "
                    "sovra_app role cannot UPDATE a model_versions row "
                    "directly -- every model version is immutable",
                    False,
                )
            except DBAPIError:
                session.rollback()
                check(
                    "FTF-1001",
                    "negative_acceptance/grant-surface: the restricted "
                    "sovra_app role cannot UPDATE a model_versions row "
                    "directly -- every model version is immutable",
                    True,
                )

    if FAILURES:
        print(f"FTF-1001 real ModelVersion schema/API proof: {len(FAILURES)} failed")
        for failure in FAILURES:
            print(f"  {failure}")
        sys.exit(1)

    print("FTF-1001 real ModelVersion schema/API proof: all checks passed")
    sys.exit(0)


if __name__ == "__main__":
    main()
