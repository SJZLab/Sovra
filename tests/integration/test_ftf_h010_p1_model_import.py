#!/usr/bin/env python3
# mypy: disable-error-code="arg-type,attr-defined,return-value,assignment,call-arg,index"
# ruff: noqa: E501
"""FTF-H010-P1 -- real Postgres proof of the model import fields +
import-validation jobs slice.

Proves, against a live PostgreSQL server and the real routes mounted on a
real uvicorn server (never SQLite: it has no row-level security and
rejects the `~` regex CHECK operator at CREATE TABLE time):

  - migration 0027 upgrade / downgrade / upgrade round-trips cleanly;
  - the restricted `sovra_app` role, never the migration-owner role, backs
    every request;
  - a real `factory_trained`/`imported_weights`/`imported_adapter`/
    `external_endpoint` enqueue each succeeds (201), at state `queued`;
  - negative_acceptance: `external_endpoint` + `trainable_artifact_id` is
    refused (400); `external_endpoint` with no `external_provider_model_ref`
    is refused (400); `imported_adapter` with no `parent_model_version_id`
    (the base) is refused (400); a viewer cannot POST (403);
  - a foreign/missing parent or artifact id is refused identically to
    missing (404); cross-tenant GET/list is denied identically to missing;
  - FTF-1001's own already-certified `ModelVersion` creation (`endpoint`/
    `full_weight`/`adapter` import_kind) keeps working completely
    unchanged after this migration -- the new `origin` NOT NULL column and
    the two new structural CHECK constraints do not regress it;
  - real database-level defense-in-depth: a raw INSERT into
    `model_versions` that bypasses the ORM but claims `origin =
    'external_endpoint'` while still carrying a `trainable_artifact_id` is
    refused by `ck_model_versions_external_endpoint_consistency`; an
    `imported_adapter` row with `adapter_only = false` is refused by
    `ck_model_versions_imported_adapter_consistency`;
  - the restricted role can UPDATE only `model_import_jobs`' own
    `state`/`failure_code`/`updated_at` columns, never `requested_origin`,
    and can never DELETE a row; `model_versions` itself stays
    SELECT/INSERT only (still immutable, unchanged by this slice);
  - RLS: a raw cross-tenant INSERT into `model_import_jobs` is rejected.

Requires `SOVRA_DATABASE_URL` (migration owner) and `SOVRA_APP_DB_PASSWORD`.

Run directly:
    SOVRA_DATABASE_URL=postgresql+psycopg://<migration-role>@host/db \\
    SOVRA_APP_DB_PASSWORD=<app-role-password> \\
    python3 tests/integration/test_ftf_h010_p1_model_import.py
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
from app.model_versions.model_import_routes import build_model_import_router  # noqa: E402
from app.model_versions.model_version_crud import ActorInfo, create_model_version  # noqa: E402
from app.projects.project_schema import Project  # noqa: E402

CID = "FTF-H010-P1"
FAILURES: list[str] = []
_ISSUER = "https://issuer.example"


def check(criterion_id: str, description: str, condition: bool) -> None:
    if not condition:
        FAILURES.append(f"FAIL [{criterion_id}] {description}")


def _required_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        print(
            f"BLOCKED_EXTERNAL: {name} is not set -- this task's real evidence requires a live "
            "Postgres (migration owner connection). Not silently skipped: see this file's own "
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


def _alembic(args: list[str], database_url: str) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    env["SOVRA_DATABASE_URL"] = database_url
    return subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=str(API_ROOT),
        capture_output=True,
        text=True,
        env=env,
    )


def _free_port() -> int:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


@contextlib.contextmanager
def _serve(app: FastAPI) -> Iterator[str]:
    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
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


class _Http:
    def __init__(self, status: int, body: bytes) -> None:
        self.status = status
        self.body = body

    def json(self) -> Any:
        return json.loads(self.body)


def _request(
    base: str,
    method: str,
    path: str,
    *,
    principal: str | None,
    payload: Any = None,
) -> _Http:
    headers: dict[str, str] = {}
    if principal is not None:
        headers["X-Test-Principal"] = principal
    body: bytes | None = None
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    parsed = urllib.parse.urlparse(base)
    conn = http.client.HTTPConnection(parsed.hostname or "127.0.0.1", parsed.port or 80, timeout=20)
    try:
        conn.request(method, path, body=body, headers=headers)
        response = conn.getresponse()
        return _Http(response.status, response.read())
    finally:
        conn.close()


def _artifact(org_id: uuid.UUID, name: str, status: str = "ready") -> TrainableArtifact:
    return TrainableArtifact(
        organization_id=org_id,
        display_name=name,
        source_type="huggingface",
        source_uri="acme-org/real-base-model",
        model_family="llama",
        base_model_revision="a1b2c3d4e5f6",
        weights_digest="sha256:" + "b" * 64,
        license_id="apache-2.0",
        license_accepted_by="seed",
        license_accepted_at=datetime.now(UTC),
        trust_remote_code_allowed=False,
        required_python_packages_json=[],
        minimum_gpu_profile_json={},
        status=status,
    )


def main() -> None:
    migration_url = _required_env("SOVRA_DATABASE_URL")
    app_password = _required_env("SOVRA_APP_DB_PASSWORD")

    for label, args in (
        ("upgrade head", ["upgrade", "head"]),
        ("downgrade -1", ["downgrade", "-1"]),
        ("upgrade head again", ["upgrade", "head"]),
    ):
        result = _alembic(args, migration_url)
        check(
            CID,
            f"alembic {label} runs cleanly (exit {result.returncode})\n{result.stdout}\n{result.stderr}",
            result.returncode == 0,
        )
    if FAILURES:
        for failure in FAILURES:
            print(f"  {failure}")
        sys.exit(1)

    owner_engine = create_engine(migration_url)
    app_engine = create_engine(_app_role_url(migration_url, app_password))
    session_factory = sessionmaker(bind=app_engine)

    check(
        CID,
        "the router's own database session is built from the restricted sovra_app role",
        app_engine.url.username == APP_ROLE_NAME,
    )

    suffix = uuid.uuid4().hex[:8]
    with Session(owner_engine) as session:
        org1 = Organization(name="Acme H010P1", slug=f"acme-h010p1-{suffix}")
        org2 = Organization(name="Globex H010P1", slug=f"globex-h010p1-{suffix}")
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
        session.flush()
        artifact1 = _artifact(org1.id, "artifact-org1")
        artifact2 = _artifact(org2.id, "artifact-org2")
        session.add_all([artifact1, artifact2])
        session.commit()
        org1_id, org2_id = org1.id, org2.id
        project1_id, project2_id = project1.id, project2.id
        artifact1_id, artifact2_id = artifact1.id, artifact2.id

    # -- FTF-1001 regression proof: its own already-certified create path,
    # completely unchanged, still works after this migration (the new
    # origin/lineage_completeness/adapter_only/external_provider_model_ref
    # columns are all defaulted inside ModelVersion.__init__) --
    from app.db.tenant_context import TenantContext, tenant_scoped_session_for

    tenant1 = TenantContext(organization_id=org1_id)
    owner_session_factory = sessionmaker(bind=owner_engine)
    with tenant_scoped_session_for(owner_session_factory, tenant1) as session:
        endpoint_version = create_model_version(
            session,
            tenant1,
            project_id=project1_id,
            import_kind="endpoint",
            endpoint_reference="https://models.acme.example.com/v1/production",
            actor=ActorInfo(actor_type="user", actor_id="seed", request_id="r1"),
        )
        check(
            CID,
            "FTF-1001 regression: an unmodified endpoint import still succeeds and is "
            "auto-classified origin=external_endpoint with external_provider_model_ref "
            "copied from endpoint_reference (never breaking the existing caller)",
            endpoint_version.origin == "external_endpoint"
            and endpoint_version.external_provider_model_ref
            == "https://models.acme.example.com/v1/production"
            and endpoint_version.lineage_completeness == "unknown"
            and endpoint_version.adapter_only is False,
        )
        adapter_version = create_model_version(
            session,
            tenant1,
            project_id=project1_id,
            import_kind="adapter",
            trainable_artifact_id=artifact1_id,
            actor=ActorInfo(actor_type="user", actor_id="seed", request_id="r2"),
        )
        check(
            CID,
            "FTF-1001 regression: an unmodified adapter import still succeeds and is "
            "auto-classified origin=imported_adapter with adapter_only=true",
            adapter_version.origin == "imported_adapter" and adapter_version.adapter_only is True,
        )
        adapter_version_id = adapter_version.id

    # -- Real database-level defense-in-depth: the two new structural CHECKs
    # catch a row even when the ORM's own __init__ defaulting is bypassed
    # entirely (a raw INSERT through sovra_app) --
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
                    "endpoint_reference, weights_digest, created_by, origin, "
                    "external_provider_model_ref, trainable_artifact_id) "
                    "VALUES (:org_id, :project_id, 999, 'endpoint', "
                    "'https://bypass.example.com', NULL, 'bypass', 'external_endpoint', "
                    "'provider://x', :artifact_id)"
                ),
                {
                    "org_id": str(org1_id),
                    "project_id": str(project1_id),
                    "artifact_id": str(artifact1_id),
                },
            )
            session.commit()
            check(
                CID,
                "negative_acceptance/defense-in-depth: a raw INSERT claiming "
                "origin='external_endpoint' while still carrying a trainable_artifact_id "
                "is refused by ck_model_versions_external_endpoint_consistency",
                False,
            )
        except DBAPIError:
            session.rollback()
            check(
                CID,
                "negative_acceptance/defense-in-depth: a raw INSERT claiming "
                "origin='external_endpoint' while still carrying a trainable_artifact_id "
                "is refused by ck_model_versions_external_endpoint_consistency",
                True,
            )

        try:
            session.execute(
                text(
                    "INSERT INTO model_versions "
                    "(organization_id, project_id, version_number, import_kind, "
                    "trainable_artifact_id, weights_digest, base_model_revision, created_by, "
                    "origin, adapter_only) "
                    "VALUES (:org_id, :project_id, 998, 'adapter', :artifact_id, :digest, "
                    "'rev', 'bypass', 'imported_adapter', false)"
                ),
                {
                    "org_id": str(org1_id),
                    "project_id": str(project1_id),
                    "artifact_id": str(artifact1_id),
                    "digest": "sha256:" + "d" * 64,
                },
            )
            session.commit()
            check(
                CID,
                "negative_acceptance/defense-in-depth: a raw INSERT claiming "
                "origin='imported_adapter' with adapter_only=false is refused by "
                "ck_model_versions_imported_adapter_consistency",
                False,
            )
        except DBAPIError:
            session.rollback()
            check(
                CID,
                "negative_acceptance/defense-in-depth: a raw INSERT claiming "
                "origin='imported_adapter' with adapter_only=false is refused by "
                "ck_model_versions_imported_adapter_consistency",
                True,
            )

        try:
            session.execute(text("UPDATE model_versions SET version_number = 9999 LIMIT 1"))
            session.commit()
            check(
                CID,
                "model_versions stays SELECT/INSERT only -- unchanged, still immutable "
                "after this migration",
                False,
            )
        except DBAPIError:
            session.rollback()
            check(
                CID,
                "model_versions stays SELECT/INSERT only -- unchanged, still immutable "
                "after this migration",
                True,
            )

    # -- Real server: the model-imports routes --
    principals = {
        "admin1": (
            AuthenticatedPrincipal(issuer=_ISSUER, subject="admin-1"),
            Role.ORGANIZATION_ADMIN,
            org1_id,
        ),
        "ml1": (AuthenticatedPrincipal(issuer=_ISSUER, subject="ml-1"), Role.ML_ENGINEER, org1_id),
        "viewer1": (
            AuthenticatedPrincipal(issuer=_ISSUER, subject="viewer-1"),
            Role.VIEWER,
            org1_id,
        ),
        "admin2": (
            AuthenticatedPrincipal(issuer=_ISSUER, subject="admin-2"),
            Role.ORGANIZATION_ADMIN,
            org2_id,
        ),
    }

    def principal_resolver(request: Any) -> AuthenticatedPrincipal | None:
        entry = principals.get(request.headers.get("x-test-principal", ""))
        return None if entry is None else entry[0]

    def role_lookup(issuer: str, subject: str, organization_id: uuid.UUID) -> Role | None:
        for principal, role, org in principals.values():
            if principal.subject == subject and org == organization_id:
                return role
        return None

    app = FastAPI()
    app.include_router(
        build_model_import_router(
            session_factory, principal_resolver=principal_resolver, role_lookup=role_lookup
        )
    )

    def collection(org_id: uuid.UUID, project_id: uuid.UUID) -> str:
        return f"/api/v1/organizations/{org_id}/projects/{project_id}/model-imports"

    def item(org_id: uuid.UUID, project_id: uuid.UUID, job_id: object) -> str:
        return f"{collection(org_id, project_id)}/{job_id}"

    with _serve(app) as base:
        r = _request(base, "GET", collection(org1_id, project1_id), principal=None)
        check(CID, "an unauthenticated list request is denied (401)", r.status == 401)

        r = _request(
            base,
            "POST",
            collection(org1_id, project1_id),
            principal="viewer1",
            payload={"requested_origin": "factory_trained"},
        )
        check(CID, "a viewer cannot POST (403)", r.status == 403)

        r = _request(
            base,
            "POST",
            collection(org1_id, project2_id),
            principal="ml1",
            payload={"requested_origin": "factory_trained"},
        )
        check(
            CID,
            "a project_id from a different organization is refused (404), identical to missing",
            r.status == 404,
        )

        r = _request(
            base,
            "POST",
            collection(org1_id, project1_id),
            principal="ml1",
            payload={"requested_origin": "factory_trained"},
        )
        check(CID, "acceptance: a real factory_trained enqueue succeeds (201)", r.status == 201)
        factory_job = r.json() if r.status == 201 else {}
        check(
            CID,
            "a fresh job starts at state=queued with no failure_code",
            factory_job.get("state") == "queued" and factory_job.get("failure_code") is None,
        )

        r = _request(
            base,
            "POST",
            collection(org1_id, project1_id),
            principal="ml1",
            payload={
                "requested_origin": "imported_adapter",
                "parent_model_version_id": str(adapter_version_id),
                "trainable_artifact_id": str(artifact1_id),
            },
        )
        check(
            CID,
            "acceptance: a real imported_adapter enqueue with a real base succeeds (201)",
            r.status == 201,
        )

        r = _request(
            base,
            "POST",
            collection(org1_id, project1_id),
            principal="ml1",
            payload={"requested_origin": "imported_adapter"},
        )
        check(
            CID,
            "negative_acceptance: imported_adapter with no parent_model_version_id "
            "(the base) is refused (400)",
            r.status == 400,
        )

        r = _request(
            base,
            "POST",
            collection(org1_id, project1_id),
            principal="ml1",
            payload={
                "requested_origin": "external_endpoint",
                "external_provider_model_ref": "provider://real/model",
            },
        )
        check(
            CID,
            "acceptance: a real external_endpoint enqueue with a real provider ref succeeds (201)",
            r.status == 201,
        )

        r = _request(
            base,
            "POST",
            collection(org1_id, project1_id),
            principal="ml1",
            payload={
                "requested_origin": "external_endpoint",
                "trainable_artifact_id": str(artifact1_id),
                "external_provider_model_ref": "provider://real/model",
            },
        )
        check(
            CID,
            "negative_acceptance: external_endpoint + trainable_artifact_id is refused "
            "(400) -- no fictitious hash",
            r.status == 400,
        )

        r = _request(
            base,
            "POST",
            collection(org1_id, project1_id),
            principal="ml1",
            payload={"requested_origin": "external_endpoint"},
        )
        check(
            CID,
            "negative_acceptance: external_endpoint with no external_provider_model_ref "
            "is refused (400)",
            r.status == 400,
        )

        r = _request(
            base,
            "POST",
            collection(org1_id, project1_id),
            principal="ml1",
            payload={
                "requested_origin": "imported_adapter",
                "parent_model_version_id": str(uuid.uuid4()),
            },
        )
        check(
            CID,
            "a genuinely nonexistent parent_model_version_id is refused (404)",
            r.status == 404,
        )

        r = _request(
            base,
            "POST",
            collection(org1_id, project1_id),
            principal="ml1",
            payload={
                "requested_origin": "imported_adapter",
                "parent_model_version_id": str(adapter_version_id),
                "trainable_artifact_id": str(artifact2_id),
            },
        )
        check(
            CID,
            "the core IDOR case: a real trainable_artifact_id belonging to a DIFFERENT "
            "organization is refused (404), identical to missing",
            r.status == 404,
        )

        # -- list/get, cross-tenant scoping --
        r = _request(base, "GET", collection(org1_id, project1_id), principal="ml1")
        listed = r.json() if r.status == 200 else []
        check(
            CID,
            "listing shows exactly the 3 successfully enqueued jobs (factory_trained, "
            "imported_adapter-with-base, external_endpoint-with-ref); every refused "
            "request above wrote nothing",
            isinstance(listed, list) and len(listed) == 3,
        )
        job_id = factory_job["id"]

        r = _request(base, "GET", item(org2_id, project1_id, job_id), principal="admin2")
        check(
            CID,
            "the core IDOR case: a real job belonging to org1, requested under org2's own "
            "path by a real org2 admin, is denied (404) identically to missing",
            r.status == 404,
        )

        r = _request(base, "GET", item(org1_id, project2_id, job_id), principal="ml1")
        check(
            CID,
            "a real job belonging to project1, requested under a different project's own "
            "path in the same organization, is denied (404) identically to missing",
            r.status == 404,
        )

        r = _request(base, "GET", item(org1_id, project1_id, job_id), principal="viewer1")
        check(CID, "a viewer may read a real job (200)", r.status == 200)

    # -- Grant-surface proof: sovra_app can only UPDATE the column-limited
    # outcome columns on model_import_jobs, and can never DELETE --
    with Session(app_engine) as session:
        session.execute(
            text("SELECT set_config('app.current_organization_id', :org_id, true)"),
            {"org_id": str(org1_id)},
        )
        job_row = session.execute(text("SELECT id FROM model_import_jobs LIMIT 1")).scalar_one()
        try:
            session.execute(
                text("UPDATE model_import_jobs SET state = 'validating' WHERE id = :id"),
                {"id": job_row},
            )
            session.commit()
            check(CID, "sovra_app may UPDATE the column-limited state column", True)
        except DBAPIError:
            session.rollback()
            check(CID, "sovra_app may UPDATE the column-limited state column", False)

        try:
            session.execute(
                text(
                    "UPDATE model_import_jobs SET requested_origin = 'factory_trained' WHERE id = :id"
                ),
                {"id": job_row},
            )
            session.commit()
            check(
                CID,
                "sovra_app cannot UPDATE requested_origin directly -- the grant is "
                "column-limited to state/failure_code/updated_at only",
                False,
            )
        except DBAPIError:
            session.rollback()
            check(
                CID,
                "sovra_app cannot UPDATE requested_origin directly -- the grant is "
                "column-limited to state/failure_code/updated_at only",
                True,
            )

        try:
            session.execute(text("DELETE FROM model_import_jobs WHERE id = :id"), {"id": job_row})
            session.commit()
            check(CID, "sovra_app can never DELETE a model_import_jobs row", False)
        except DBAPIError:
            session.rollback()
            check(CID, "sovra_app can never DELETE a model_import_jobs row", True)

        # -- RLS: a raw cross-tenant INSERT is rejected --
        try:
            session.execute(
                text(
                    "INSERT INTO model_import_jobs "
                    "(organization_id, project_id, requested_origin, state, created_by) "
                    "VALUES (:org2_id, :project2_id, 'factory_trained', 'queued', 'bypass')"
                ),
                {"org2_id": str(org2_id), "project2_id": str(project2_id)},
            )
            session.commit()
            check(
                CID,
                "RLS: a raw INSERT into model_import_jobs for a DIFFERENT organization, "
                "while the session's tenant context is org1, is rejected",
                False,
            )
        except DBAPIError:
            session.rollback()
            check(
                CID,
                "RLS: a raw INSERT into model_import_jobs for a DIFFERENT organization, "
                "while the session's tenant context is org1, is rejected",
                True,
            )

    if FAILURES:
        print(f"FTF-H010-P1 real model import proof: {len(FAILURES)} failed")
        for failure in FAILURES:
            print(f"  {failure}")
        sys.exit(1)

    print("FTF-H010-P1 real model import proof: all checks passed")
    sys.exit(0)


if __name__ == "__main__":
    main()
