#!/usr/bin/env python3
# mypy: disable-error-code="arg-type,attr-defined,return-value,assignment,call-arg,index"
# ruff: noqa: E501
"""FTF-0309-P1 -- real Postgres proof of the training-capability read API.

Mounts the real FTF-0308-P1 mapping router and the real capability router on
a real uvicorn server over a live PostgreSQL (never SQLite: no RLS) and proves:

  - an alias mapped through the real POST is `trainable_open_weight` through
    the capability GET, with the artifact id, digest and catalog version;
  - an unmapped alias is `unknown`, never inferred from its name;
  - a revoked connection is shown revoked and never trainable, and is not dialed;
  - a foreign project, a foreign organization's connection and a random id are
    the same 404; a foreign organization in the path is 403; a viewer may read;
  - one discovery call per request; a non-`discovered` state is 503; the REAL
    default `discover_models` against an unreachable gateway is 503;
  - a GET writes no row (no entry, no change row, no audit event).

Requires `SOVRA_DATABASE_URL` (migration owner) and `SOVRA_APP_DB_PASSWORD`.
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
from packages.litellm_client.v1_model_info_enrichment import (  # noqa: E402
    EnrichmentState,
    ModelInfoEnrichmentResult,
)
from packages.litellm_client.v1_models_discovery import (  # noqa: E402
    DiscoveredModel,
    DiscoveryState,
    ModelDiscoveryResult,
    ModelMetadataStatus,
)
from sqlalchemy import create_engine, text  # noqa: E402
from sqlalchemy.orm import Session, sessionmaker  # noqa: E402

from app.artifacts.artifact_schema import TrainableArtifact  # noqa: E402
from app.auth.organization_workspace_schema import (  # noqa: E402
    APP_ROLE_NAME,
    Organization,
    Workspace,  # noqa: E402
)
from app.auth.rbac_middleware import AuthenticatedPrincipal, Role  # noqa: E402
from app.litellm.connection_schema import LiteLLMConnection  # noqa: E402
from app.litellm.model_capability_routes import build_model_capability_router  # noqa: E402
from app.litellm.model_catalog_mapping_routes import (  # noqa: E402
    build_model_catalog_mapping_router,
)
from app.projects.project_schema import Project  # noqa: E402

CID = "FTF-0309-P1"
FAILURES: list[str] = []
_NOW = datetime(2026, 10, 7, 12, 0, 0, tzinfo=UTC)
_ISSUER = "https://issuer.example"
AUDIT_ACTION = "model_catalog_entry.training_artifact_mapping_change"


def check(criterion_id: str, description: str, condition: bool) -> None:
    if not condition:
        FAILURES.append(f"FAIL [{criterion_id}] {description}")


def _required_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        print(
            f"BLOCKED_EXTERNAL: {name} is not set -- this task's real evidence requires a live "
            "Postgres (migration owner connection). Not silently skipped: see this file's "
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


class _Discovery:
    def __init__(self) -> None:
        self.calls = 0
        self.result = _discovered("gpt-x", "openai/gpt-4o")

    def __call__(self, **kwargs: Any) -> ModelDiscoveryResult:
        self.calls += 1
        return self.result


def _discovered(*ids: str) -> ModelDiscoveryResult:
    return ModelDiscoveryResult(
        state=DiscoveryState.DISCOVERED,
        models=tuple(
            DiscoveredModel(id=i, metadata_status=ModelMetadataStatus.LIMITED) for i in ids
        ),
        model_count=len(ids),
        content_hash="a" * 64,
        detail="",
        checked_at=_NOW,
    )


def _failed(state: DiscoveryState) -> ModelDiscoveryResult:
    return ModelDiscoveryResult(
        state=state, models=(), model_count=None, content_hash=None, detail="", checked_at=_NOW
    )


def _artifact(org_id: uuid.UUID, name: str, status: str) -> TrainableArtifact:
    return TrainableArtifact(
        organization_id=org_id,
        display_name=name,
        source_type="huggingface",
        source_uri="acme/model",
        model_family="llama",
        base_model_revision="a" * 40,
        license_id="mit",
        status=status,
    )


_DIGEST = "sha256:" + "b" * 64


def main() -> None:
    migration_url = _required_env("SOVRA_DATABASE_URL")
    app_password = _required_env("SOVRA_APP_DB_PASSWORD")

    result = _alembic(["upgrade", "head"], migration_url)
    check(CID, f"alembic upgrade head\n{result.stdout}\n{result.stderr}", result.returncode == 0)
    if FAILURES:
        for failure in FAILURES:
            print(f"  {failure}")
        sys.exit(1)

    owner_engine = create_engine(migration_url)
    app_engine = create_engine(_app_role_url(migration_url, app_password))
    session_factory = sessionmaker(bind=app_engine)

    suffix = uuid.uuid4().hex[:8]
    with Session(owner_engine) as session:
        org1 = Organization(name="Acme 0309P1", slug=f"acme-0309p1-{suffix}")
        org2 = Organization(name="Globex 0309P1", slug=f"globex-0309p1-{suffix}")
        session.add_all([org1, org2])
        session.flush()
        ws1 = Workspace(organization_id=org1.id, name="Main", slug="main")
        ws2 = Workspace(organization_id=org2.id, name="Main", slug="main")
        session.add_all([ws1, ws2])
        session.flush()

        def project(org: Organization, ws: Workspace, name: str) -> Project:
            return Project(
                organization_id=org.id,
                workspace_id=ws.id,
                name=name,
                project_type="generic_model_tuning",
                status="active",
                security_profile="standard",
                row_version=1,
                created_by="seed",
            )

        proj1, proj2 = project(org1, ws1, "p1-0309"), project(org2, ws2, "p2-0309")
        conn1 = LiteLLMConnection(
            organization_id=org1.id,
            name="gw1",
            base_url="https://gateway1.example.test",
            secret_ref="vault://sovra/gw1",
            status="active",
        )
        conn_rev = LiteLLMConnection(
            organization_id=org1.id,
            name="gw-rev",
            base_url="https://gateway-rev.example.test",
            secret_ref="vault://sovra/gwrev",
            status="active",
        )
        art = TrainableArtifact(
            organization_id=org1.id,
            display_name="artifact-a",
            source_type="huggingface",
            source_uri="acme/model",
            model_family="llama",
            base_model_revision="a" * 40,
            weights_digest=_DIGEST,
            license_id="mit",
            license_accepted_by="seed",
            license_accepted_at=_NOW,
            minimum_gpu_profile_json={"gpu": "a100", "vram_gb": 40},
            status="ready",
        )
        session.add_all([proj1, proj2, conn1, conn_rev, art])
        session.commit()
        org1_id, org2_id = org1.id, org2.id
        proj1_id, proj2_id = proj1.id, proj2.id
        conn1_id, conn_rev_id, art_id = conn1.id, conn_rev.id, art.id

    principals = {
        "admin1": (
            AuthenticatedPrincipal(issuer=_ISSUER, subject="admin-1"),
            Role.ORGANIZATION_ADMIN,
            org1_id,
        ),
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

    discovery = _Discovery()
    discovery.result = _discovered("trainable-alias", "unmapped-alias", "revoked-alias")
    app = FastAPI()
    for router in (
        build_model_catalog_mapping_router(
            session_factory,
            principal_resolver=principal_resolver,
            role_lookup=role_lookup,
            secret_manager_config_factory=lambda: None,
            discovery_function=discovery,
        ),
        build_model_capability_router(
            session_factory,
            principal_resolver=principal_resolver,
            role_lookup=role_lookup,
            secret_manager_config_factory=lambda: None,
            discovery_function=discovery,
            enrichment_function=lambda **kwargs: ModelInfoEnrichmentResult(
                state=EnrichmentState.COMPLETED, entries=(), detail="", checked_at=_NOW
            ),
        ),
    ):
        app.include_router(router)
    real_default_app = FastAPI()
    real_default_app.include_router(
        build_model_capability_router(
            session_factory,
            principal_resolver=principal_resolver,
            role_lookup=role_lookup,
            secret_manager_config_factory=lambda: None,
        )
    )

    def map_path(org: uuid.UUID, conn: uuid.UUID, model: str) -> str:
        return (
            f"/api/v1/organizations/{org}/connections/litellm/{conn}"
            f"/models/{model}/training-artifact"
        )

    def cap_path(org: uuid.UUID, conn: uuid.UUID, model: str, project_id: uuid.UUID | None) -> str:
        query = "" if project_id is None else f"?project_id={project_id}"
        return (
            f"/api/v1/organizations/{org}/connections/litellm/{conn}"
            f"/models/{model}/capabilities{query}"
        )

    def row_counts() -> tuple[int, int, int]:
        with Session(owner_engine) as s:
            return (
                int(s.execute(text("SELECT count(*) FROM model_catalog_entries")).scalar_one()),
                int(
                    s.execute(
                        text("SELECT count(*) FROM model_catalog_mapping_changes")
                    ).scalar_one()
                ),
                int(s.execute(text("SELECT count(*) FROM audit_events")).scalar_one()),
            )

    with _serve(app) as base:
        for alias, conn in (("trainable-alias", conn1_id), ("revoked-alias", conn_rev_id)):
            mapped = _request(
                base,
                "POST",
                map_path(org1_id, conn, alias),
                principal="admin1",
                payload={"trainable_artifact_id": str(art_id), "expected_catalog_version": 0},
            )
            check(CID, f"setup: mapping {alias} is 200 ({mapped.status})", mapped.status == 200)
        with Session(owner_engine) as s:
            s.execute(
                text("UPDATE litellm_connections SET status='revoked' WHERE id=:i"),
                {"i": conn_rev_id},
            )
            s.commit()

        counts_before = row_counts()
        discovery.calls = 0
        trainable = _request(
            base,
            "GET",
            cap_path(org1_id, conn1_id, "trainable-alias", proj1_id),
            principal="viewer1",
        )
        body = trainable.json() if trainable.status == 200 else {}
        check(
            CID,
            f"mapped ready artifact -> trainable (viewer read) ({trainable.status}: {trainable.body[:200]!r})",
            trainable.status == 200
            and body.get("capability") == "trainable_open_weight"
            and body.get("trainable") is True
            and body.get("access") == "granted"
            and body["artifact"]["artifact_id"] == str(art_id)
            and body["artifact"]["weights_digest"] == _DIGEST
            and body["catalog_version"] == 1
            and body["min_gpu_vram_gb"]["declared_evidence"] == "declared"
            and body["min_gpu_vram_gb"]["state"] == "unknown",
        )
        check(CID, "exactly one discovery for one GET", discovery.calls == 1)

        unmapped = _request(
            base,
            "GET",
            cap_path(org1_id, conn1_id, "unmapped-alias", proj1_id),
            principal="viewer1",
        )
        check(
            CID,
            "an unmapped alias is unknown and not trainable",
            unmapped.status == 200
            and unmapped.json()["capability"] == "unknown"
            and unmapped.json()["trainable"] is False
            and unmapped.json()["catalog_version"] == 0,
        )

        discovery.calls = 0
        revoked = _request(
            base,
            "GET",
            cap_path(org1_id, conn_rev_id, "revoked-alias", proj1_id),
            principal="viewer1",
        )
        check(
            CID,
            "a revoked connection is shown revoked, unknown and never trainable",
            revoked.status == 200
            and revoked.json()["connection_status"] == "revoked"
            and revoked.json()["capability"] == "unknown"
            and revoked.json()["trainable"] is False,
        )
        check(CID, "a revoked connection is never dialed", discovery.calls == 0)

        random_conn = _request(
            base,
            "GET",
            cap_path(org1_id, uuid.uuid4(), "trainable-alias", proj1_id),
            principal="admin1",
        )
        foreign_project = _request(
            base,
            "GET",
            cap_path(org1_id, conn1_id, "trainable-alias", proj2_id),
            principal="admin1",
        )
        random_project = _request(
            base,
            "GET",
            cap_path(org1_id, conn1_id, "trainable-alias", uuid.uuid4()),
            principal="admin1",
        )
        cross_tenant = _request(
            base,
            "GET",
            cap_path(org1_id, conn1_id, "trainable-alias", proj2_id),
            principal="admin2",
        )
        check(
            CID,
            "foreign project, random project and random connection are the same 404",
            random_conn.status == foreign_project.status == random_project.status == 404
            and random_conn.body == foreign_project.body == random_project.body,
        )
        check(
            CID,
            "a foreign organization's admin is refused for this organization's path (403)",
            cross_tenant.status == 403,
        )
        own_org_foreign_conn = _request(
            base,
            "GET",
            cap_path(org2_id, conn1_id, "trainable-alias", proj2_id),
            principal="admin2",
        )
        check(
            CID,
            "org2 asking for org1's connection under its own path is the same 404",
            own_org_foreign_conn.status == 404 and own_org_foreign_conn.body == random_conn.body,
        )
        check(
            CID,
            "missing project_id is 400, no principal 401",
            _request(
                base,
                "GET",
                cap_path(org1_id, conn1_id, "trainable-alias", None),
                principal="admin1",
            ).status
            == 400
            and _request(
                base,
                "GET",
                cap_path(org1_id, conn1_id, "trainable-alias", proj1_id),
                principal=None,
            ).status
            == 401,
        )

        discovery.result = _failed(DiscoveryState.TIMEOUT)
        down = _request(
            base,
            "GET",
            cap_path(org1_id, conn1_id, "trainable-alias", proj1_id),
            principal="admin1",
        )
        check(CID, "a non-discovered state is 503 gateway_unavailable", down.status == 503)
        discovery.result = _discovered("trainable-alias")

        check(
            CID, "no GET wrote an entry, change row or audit event", row_counts() == counts_before
        )

    with _serve(real_default_app) as base:
        unreachable = _request(
            base,
            "GET",
            cap_path(org1_id, conn1_id, "trainable-alias", proj1_id),
            principal="admin1",
        )
        check(
            CID,
            f"the REAL default discover_models against an unreachable gateway is 503 ({unreachable.status})",
            unreachable.status == 503,
        )

    if FAILURES:
        print(f"FTF-0309-P1 capability real proof: {len(FAILURES)} failed")
        for failure in FAILURES:
            print(f"  {failure}")
        sys.exit(1)
    print("FTF-0309-P1 capability real proof: all checks passed")
    sys.exit(0)


if __name__ == "__main__":
    main()
