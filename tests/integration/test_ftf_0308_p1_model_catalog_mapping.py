#!/usr/bin/env python3
# mypy: disable-error-code="arg-type,attr-defined,return-value,assignment,call-arg,index"
# ruff: noqa: E501
"""FTF-0308-P1 -- real Postgres proof of the model catalog mapping slice.

Proves, against a live PostgreSQL server and the real routes mounted on a
real uvicorn server (never SQLite: it has no row-level security):

  - migration 0022 upgrade / downgrade / upgrade, ENABLE + FORCE RLS and a
    non-allow-all tenant policy on both tables;
  - POST maps an alias to a ready artifact: one entry at version 1, one change
    row (0 -> 1), one audit event; a remap bumps to 2 with history; the same
    artifact again writes nothing; unmap bumps and clears the artifact;
  - a stale `expected_catalog_version` is a 409 and writes nothing, and
    `require_current_mapping` rejects the superseded version;
  - artifact not ready -> 409, foreign or missing artifact -> identical 404;
  - an alias the gateway does not list -> 409; a non-`discovered` state -> 503;
    the REAL default `discover_models` against an unreachable gateway -> 503;
    none of them writes a row;
  - cross-tenant GET/POST/changes -> the same 404 as a random connection id, a
    foreign organization in the path -> 403, a raw cross-tenant INSERT is
    rejected by RLS;
  - the app role cannot UPDATE or DELETE the append-only change log nor DELETE
    an entry; composite foreign keys, version CHECKs and the unique key hold;
  - a viewer may read but not write; a revoked connection cannot be mapped.

The gateway's `/v1/models` answer is supplied through the router's
`discovery_function` seam (a real `ModelDiscoveryResult`); the one real-default
negative case above runs the unmodified `discover_models`.

Requires `SOVRA_DATABASE_URL` (migration owner) and `SOVRA_APP_DB_PASSWORD`.

Run directly:
    SOVRA_DATABASE_URL=postgresql+psycopg://<migration-role>@host/db \\
    SOVRA_APP_DB_PASSWORD=<app-role-password> \\
    python3 tests/integration/test_ftf_0308_p1_model_catalog_mapping.py
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
from packages.litellm_client.v1_models_discovery import (  # noqa: E402
    DiscoveredModel,
    DiscoveryState,
    ModelDiscoveryResult,
    ModelMetadataStatus,
)
from sqlalchemy import create_engine, select, text  # noqa: E402
from sqlalchemy.exc import DBAPIError, IntegrityError  # noqa: E402
from sqlalchemy.orm import Session, sessionmaker  # noqa: E402

from app.artifacts.artifact_schema import TrainableArtifact  # noqa: E402
from app.auth.audit_event_writer import AuditEvent  # noqa: E402
from app.auth.organization_workspace_schema import (  # noqa: E402
    APP_ROLE_NAME,
    Organization,
    set_tenant_context,
)
from app.auth.rbac_middleware import AuthenticatedPrincipal, Role  # noqa: E402
from app.db.tenant_context import TenantContext  # noqa: E402
from app.litellm.connection_schema import LiteLLMConnection  # noqa: E402
from app.litellm.model_catalog_mapping_routes import (  # noqa: E402
    build_model_catalog_mapping_router,
)
from app.litellm.model_catalog_mapping_store import (  # noqa: E402
    MappingChangedError,
    ModelCatalogEntryNotFoundError,
    ModelCatalogEntryRecord,
    ModelCatalogMappingChangeRecord,
    require_current_mapping,
)

CID = "FTF-0308-P1"
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


def main() -> None:
    migration_url = _required_env("SOVRA_DATABASE_URL")
    app_password = _required_env("SOVRA_APP_DB_PASSWORD")

    # -- Migration: upgrade, downgrade -1, upgrade -------------------------------
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

    suffix = uuid.uuid4().hex[:8]
    with Session(owner_engine) as session:
        org1 = Organization(name="Acme 0308P1", slug=f"acme-0308p1-{suffix}")
        org2 = Organization(name="Globex 0308P1", slug=f"globex-0308p1-{suffix}")
        session.add_all([org1, org2])
        session.flush()
        conn1 = LiteLLMConnection(
            organization_id=org1.id,
            name="gw1",
            base_url="https://gateway1.example.test",
            secret_ref="vault://sovra/gw1",
            status="active",
        )
        conn1b = LiteLLMConnection(
            organization_id=org1.id,
            name="gw1b",
            base_url="https://gateway1b.example.test",
            secret_ref="vault://sovra/gw1b",
            status="active",
        )
        conn2 = LiteLLMConnection(
            organization_id=org2.id,
            name="gw2",
            base_url="https://gateway2.example.test",
            secret_ref="vault://sovra/gw2",
            status="active",
        )
        art_a = _artifact(org1.id, "artifact-a", "ready")
        art_b = _artifact(org1.id, "artifact-b", "ready")
        art_reg = _artifact(org1.id, "artifact-registered", "registered")
        art_foreign = _artifact(org2.id, "artifact-foreign", "ready")
        session.add_all([conn1, conn1b, conn2, art_a, art_b, art_reg, art_foreign])
        session.commit()
        org1_id, org2_id = org1.id, org2.id
        conn1_id, conn1b_id, conn2_id = conn1.id, conn1b.id, conn2.id
        a_id, b_id, reg_id, foreign_id = art_a.id, art_b.id, art_reg.id, art_foreign.id

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

    discovery = _Discovery()
    app = FastAPI()
    app.include_router(
        build_model_catalog_mapping_router(
            session_factory,
            principal_resolver=principal_resolver,
            role_lookup=role_lookup,
            secret_manager_config_factory=lambda: None,
            discovery_function=discovery,
        )
    )
    real_default_app = FastAPI()
    real_default_app.include_router(
        build_model_catalog_mapping_router(
            session_factory,
            principal_resolver=principal_resolver,
            role_lookup=role_lookup,
            secret_manager_config_factory=lambda: None,
        )
    )

    def path(org: uuid.UUID, conn: uuid.UUID, model: str = "gpt-x", suffix: str = "") -> str:
        return (
            f"/api/v1/organizations/{org}/connections/litellm/{conn}"
            f"/models/{model}/training-artifact{suffix}"
        )

    def count_entries() -> int:
        with Session(owner_engine) as s:
            return int(s.execute(text("SELECT count(*) FROM model_catalog_entries")).scalar_one())

    def count_changes() -> int:
        with Session(owner_engine) as s:
            return int(
                s.execute(text("SELECT count(*) FROM model_catalog_mapping_changes")).scalar_one()
            )

    def audit_count(entry_id: str | None = None) -> int:
        with Session(owner_engine) as s:
            statement = select(AuditEvent).where(AuditEvent.action == AUDIT_ACTION)
            if entry_id is not None:
                statement = statement.where(AuditEvent.resource_id == entry_id)
            return len(s.execute(statement).scalars().all())

    with _serve(app) as base:
        # -- Nothing mapped yet ------------------------------------------------
        before = _request(base, "GET", path(org1_id, conn1_id), principal="viewer1")
        check(
            CID,
            "GET before any mapping is unmapped at version 0, no entry id",
            before.status == 200
            and before.json()["mapping_state"] == "unmapped"
            and before.json()["catalog_version"] == 0
            and before.json()["model_catalog_entry_id"] is None,
        )
        check(CID, "reading wrote no entry", count_entries() == 0)

        # -- First mapping -------------------------------------------------------
        first = _request(
            base,
            "POST",
            path(org1_id, conn1_id),
            principal="ml1",
            payload={"trainable_artifact_id": str(a_id), "expected_catalog_version": 0},
        )
        body = first.json() if first.status == 200 else {}
        check(
            CID,
            f"first mapping is 200 mapped at version 1 ({first.status}: {first.body[:200]!r})",
            first.status == 200
            and body.get("mapping_state") == "mapped"
            and body.get("catalog_version") == 1
            and body.get("changed") is True
            and body.get("trainable_artifact_id") == str(a_id)
            and body.get("artifact", {}).get("id") == str(a_id)
            and body.get("last_changed_at") is not None,
        )
        entry_id = body.get("model_catalog_entry_id")
        check(
            CID,
            "first mapping wrote one entry, one change row and one audit event",
            count_entries() == 1 and count_changes() == 1 and audit_count(entry_id) == 1,
        )
        with Session(owner_engine) as s:
            entry = s.execute(select(ModelCatalogEntryRecord)).scalars().one()
            change = s.execute(select(ModelCatalogMappingChangeRecord)).scalars().one()
            audit = (
                s.execute(select(AuditEvent).where(AuditEvent.action == AUDIT_ACTION))
                .scalars()
                .one()
            )
        check(
            CID,
            "the entry row is scoped to org1/conn1, mapped by the principal's subject",
            entry.organization_id == org1_id
            and entry.litellm_connection_id == conn1_id
            and entry.public_model_name == "gpt-x"
            and entry.trainable_artifact_id == a_id
            and entry.catalog_version == 1
            and entry.mapped_by == "ml-1",
        )
        check(
            CID,
            "the change row records 0 -> 1, none -> A, the actor and a request id",
            change.previous_catalog_version == 0
            and change.new_catalog_version == 1
            and change.previous_trainable_artifact_id is None
            and change.new_trainable_artifact_id == a_id
            and change.actor == "ml-1"
            and len(change.request_id) == 32
            and change.organization_id == org1_id,
        )
        check(
            CID,
            "the audit event carries the request id of the change and the artifact ids",
            audit.request_id == change.request_id
            and audit.organization_id == org1_id
            and audit.actor_id == "ml-1"
            and audit.resource_type == "model_catalog_entry"
            and audit.details_json["new_trainable_artifact_id"] == str(a_id)
            and audit.details_json["new_catalog_version"] == 1
            and audit.details_json["previous_catalog_version"] == 0,
        )
        got = _request(base, "GET", path(org1_id, conn1_id), principal="viewer1").json()
        check(
            CID,
            "GET after the mapping reads it back with the artifact and a change time",
            got["mapping_state"] == "mapped"
            and got["trainable_artifact_id"] == str(a_id)
            and got["catalog_version"] == 1
            and got["artifact"]["status"] == "ready"
            and got["last_changed_at"] is not None
            and got["last_verified_accessible_at"] is not None,
        )

        # -- Same artifact: a no-op -------------------------------------------------
        calls_before = discovery.calls
        again = _request(
            base,
            "POST",
            path(org1_id, conn1_id),
            principal="ml1",
            payload={"trainable_artifact_id": str(a_id), "expected_catalog_version": 1},
        )
        check(
            CID,
            "the same artifact is a 200 no-op: changed false, version 1",
            again.status == 200
            and again.json()["changed"] is False
            and again.json()["catalog_version"] == 1,
        )
        check(
            CID,
            "the no-op ran a fresh discovery but wrote no row and no audit event",
            discovery.calls == calls_before + 1
            and count_changes() == 1
            and audit_count() == 1
            and count_entries() == 1,
        )

        # -- Stale expected version --------------------------------------------------
        stale = _request(
            base,
            "POST",
            path(org1_id, conn1_id),
            principal="ml1",
            payload={"trainable_artifact_id": str(b_id), "expected_catalog_version": 0},
        )
        check(
            CID,
            "a stale expected version is 409 catalog_version_conflict",
            stale.status == 409 and stale.json() == {"detail": "catalog_version_conflict"},
        )
        check(CID, "the stale call wrote nothing", count_changes() == 1 and audit_count() == 1)

        # -- Remap A -> B ------------------------------------------------------------
        remap = _request(
            base,
            "POST",
            path(org1_id, conn1_id),
            principal="admin1",
            payload={"trainable_artifact_id": str(b_id), "expected_catalog_version": 1},
        )
        remap_body = remap.json() if remap.status == 200 else {}
        check(
            CID,
            "remap A -> B is 200 at version 2 on the same entry",
            remap.status == 200
            and remap_body.get("catalog_version") == 2
            and remap_body.get("changed") is True
            and remap_body.get("trainable_artifact_id") == str(b_id)
            and remap_body.get("model_catalog_entry_id") == entry_id,
        )
        check(
            CID,
            "the remap gave one entry, two change rows and two audit events",
            count_entries() == 1 and count_changes() == 2 and audit_count(entry_id) == 2,
        )
        with Session(app_engine) as s:
            set_tenant_context(s, org1_id)
            ctx1 = TenantContext(organization_id=org1_id)
            try:
                require_current_mapping(s, ctx1, uuid.UUID(entry_id), 1)
                superseded = None
            except MappingChangedError as exc:
                superseded = exc
            current = require_current_mapping(s, ctx1, uuid.UUID(entry_id), 2)
        check(
            CID,
            "require_current_mapping rejects version 1 after the remap and accepts version 2",
            superseded is not None
            and superseded.observed_catalog_version == 1
            and superseded.current_catalog_version == 2
            and current.trainable_artifact_id == b_id,
        )
        with Session(app_engine) as s:
            set_tenant_context(s, org2_id)
            try:
                require_current_mapping(
                    s, TenantContext(organization_id=org2_id), uuid.UUID(entry_id), 2
                )
                foreign_view = "allowed"
            except ModelCatalogEntryNotFoundError:
                foreign_view = "not_found"
        check(
            CID,
            "another organization cannot see the entry through the hook",
            foreign_view == "not_found",
        )
        changes = _request(
            base, "GET", path(org1_id, conn1_id, suffix="/changes"), principal="viewer1"
        )
        listed = changes.json()["changes"] if changes.status == 200 else []
        check(
            CID,
            "the history lists B newest then A, with versions and the previous artifact",
            [c["new_catalog_version"] for c in listed] == [2, 1]
            and listed[0]["previous_trainable_artifact_id"] == str(a_id)
            and listed[0]["new_trainable_artifact_id"] == str(b_id)
            and listed[1]["previous_trainable_artifact_id"] is None,
        )
        limited = _request(
            base, "GET", path(org1_id, conn1_id, suffix="/changes?limit=1"), principal="viewer1"
        )
        check(CID, "limit=1 returns only the newest", len(limited.json()["changes"]) == 1)

        # -- Artifact guards -----------------------------------------------------------
        before_rows = (count_changes(), audit_count())
        not_ready = _request(
            base,
            "POST",
            path(org1_id, conn1_id),
            principal="ml1",
            payload={"trainable_artifact_id": str(reg_id)},
        )
        check(
            CID,
            "a registered (not ready) artifact is 409 artifact_not_ready",
            not_ready.status == 409 and not_ready.json() == {"detail": "artifact_not_ready"},
        )
        foreign = _request(
            base,
            "POST",
            path(org1_id, conn1_id),
            principal="ml1",
            payload={"trainable_artifact_id": str(foreign_id)},
        )
        missing = _request(
            base,
            "POST",
            path(org1_id, conn1_id),
            principal="ml1",
            payload={"trainable_artifact_id": str(uuid.uuid4())},
        )
        check(
            CID,
            "a foreign artifact is the same 404 not_found as a missing one",
            foreign.status == 404
            and missing.status == 404
            and foreign.json() == missing.json() == {"detail": "not_found"},
        )
        check(
            CID,
            "no artifact refusal wrote anything",
            (count_changes(), audit_count()) == before_rows,
        )

        # -- Gateway guards ---------------------------------------------------------------
        rows_before = (count_entries(), count_changes(), audit_count())
        discovery.result = _discovered("someone-else")
        absent = _request(
            base,
            "POST",
            path(org1_id, conn1_id, "gpt-y"),
            principal="ml1",
            payload={"trainable_artifact_id": str(a_id)},
        )
        check(
            CID,
            "an alias the gateway does not list is 409 model_not_accessible",
            absent.status == 409 and absent.json() == {"detail": "model_not_accessible"},
        )
        for state in DiscoveryState:
            if state is DiscoveryState.DISCOVERED:
                continue
            discovery.result = _failed(state)
            down = _request(
                base,
                "POST",
                path(org1_id, conn1_id, "gpt-y"),
                principal="ml1",
                payload={"trainable_artifact_id": str(a_id)},
            )
            check(
                CID,
                f"discovery state {state.value} is 503 gateway_unavailable",
                down.status == 503 and down.json() == {"detail": "gateway_unavailable"},
            )
        check(
            CID,
            "no gateway refusal created an entry, a change row or an audit event",
            (count_entries(), count_changes(), audit_count()) == rows_before,
        )
        discovery.result = _discovered("gpt-x", "openai/gpt-4o", "gpt-y")

        # -- A '/' in the alias and a second alias on a second connection ------------------
        slash = _request(
            base,
            "POST",
            path(org1_id, conn1_id, "openai/gpt-4o"),
            principal="ml1",
            payload={"trainable_artifact_id": str(a_id), "expected_catalog_version": 0},
        )
        check(
            CID,
            "an alias containing '/' maps and reads back under its exact name",
            slash.status == 200
            and _request(
                base, "GET", path(org1_id, conn1_id, "openai/gpt-4o"), principal="viewer1"
            ).json()["public_model_name"]
            == "openai/gpt-4o",
        )
        other_conn = _request(
            base,
            "POST",
            path(org1_id, conn1b_id),
            principal="ml1",
            payload={"trainable_artifact_id": str(a_id), "expected_catalog_version": 0},
        )
        check(
            CID,
            "the same alias on another connection is an independent entry at version 1",
            other_conn.status == 200
            and other_conn.json()["catalog_version"] == 1
            and other_conn.json()["model_catalog_entry_id"] != entry_id
            and _request(base, "GET", path(org1_id, conn1_id), principal="viewer1").json()[
                "catalog_version"
            ]
            == 2,
        )

        # -- Roles --------------------------------------------------------------------------
        viewer_post = _request(
            base,
            "POST",
            path(org1_id, conn1_id),
            principal="viewer1",
            payload={"trainable_artifact_id": str(a_id)},
        )
        check(CID, "a viewer cannot POST (403)", viewer_post.status == 403)
        check(
            CID,
            "no principal is 401 on all three routes",
            all(
                _request(base, m, path(org1_id, conn1_id, suffix=s), principal=None).status == 401
                for m, s in (("GET", ""), ("POST", ""), ("GET", "/changes"))
            ),
        )

        # -- Cross-tenant ---------------------------------------------------------------------
        random_conn = uuid.uuid4()
        foreign_conn_get = _request(base, "GET", path(org2_id, conn1_id), principal="admin2")
        random_conn_get = _request(base, "GET", path(org2_id, random_conn), principal="admin2")
        check(
            CID,
            "org2 reading org1's connection gets the same 404 as a random connection id",
            foreign_conn_get.status == 404
            and random_conn_get.status == 404
            and foreign_conn_get.json() == random_conn_get.json() == {"detail": "not_found"},
        )
        discovery_calls = discovery.calls
        foreign_post = _request(
            base,
            "POST",
            path(org2_id, conn1_id),
            principal="admin2",
            payload={"trainable_artifact_id": None},
        )
        check(
            CID,
            "org2 POSTing against org1's connection is 404 with no discovery call",
            foreign_post.status == 404 and discovery.calls == discovery_calls,
        )
        foreign_changes = _request(
            base, "GET", path(org2_id, conn1_id, suffix="/changes"), principal="admin2"
        )
        check(CID, "org2 reading org1's history is 404", foreign_changes.status == 404)
        into_org1 = _request(base, "GET", path(org1_id, conn1_id), principal="admin2")
        check(CID, "org2's user addressing org1's path is 403", into_org1.status == 403)
        discovery.result = _discovered("gpt-x")
        own = _request(
            base,
            "POST",
            path(org2_id, conn2_id),
            principal="admin2",
            payload={"trainable_artifact_id": str(a_id)},
        )
        check(
            CID,
            "org2 mapping its own connection to org1's artifact is the same 404 not_found",
            own.status == 404 and own.json() == {"detail": "not_found"},
        )
        mapped2 = _request(
            base,
            "POST",
            path(org2_id, conn2_id),
            principal="admin2",
            payload={"trainable_artifact_id": str(foreign_id)},
        )
        check(
            CID,
            "org2 can map its own alias to its own artifact",
            mapped2.status == 200 and mapped2.json()["catalog_version"] == 1,
        )
        org2_view = _request(base, "GET", path(org2_id, conn2_id), principal="admin2").json()
        check(
            CID,
            "org2's own read shows only org2's mapping and artifact",
            org2_view["trainable_artifact_id"] == str(foreign_id)
            and org2_view["artifact"]["organization_id"] == str(org2_id),
        )

        # -- Unmap -----------------------------------------------------------------------------
        unmap = _request(
            base,
            "POST",
            path(org1_id, conn1_id),
            principal="ml1",
            payload={"trainable_artifact_id": None, "expected_catalog_version": 2},
        )
        check(
            CID,
            "unmapping with the right version is 200 unmapped at version 3",
            unmap.status == 200
            and unmap.json()["mapping_state"] == "unmapped"
            and unmap.json()["catalog_version"] == 3
            and unmap.json()["artifact"] is None
            and unmap.json()["changed"] is True,
        )
        with Session(owner_engine) as s:
            last = (
                s.execute(
                    select(ModelCatalogMappingChangeRecord).where(
                        ModelCatalogMappingChangeRecord.model_catalog_entry_id
                        == uuid.UUID(entry_id),
                        ModelCatalogMappingChangeRecord.new_catalog_version == 3,
                    )
                )
                .scalars()
                .one()
            )
            mapped_by = s.execute(
                select(ModelCatalogEntryRecord.mapped_by).where(
                    ModelCatalogEntryRecord.id == uuid.UUID(entry_id)
                )
            ).scalar_one()
        check(
            CID,
            "the unmap is recorded B -> none and clears mapped_by",
            last.previous_trainable_artifact_id == b_id
            and last.new_trainable_artifact_id is None
            and mapped_by is None,
        )

        # -- Revoked connection --------------------------------------------------------------------
        with Session(owner_engine) as s:
            s.execute(
                text("UPDATE litellm_connections SET status='revoked' WHERE id=:id"),
                {"id": conn1b_id},
            )
            s.commit()
        revoked_post = _request(
            base,
            "POST",
            path(org1_id, conn1b_id),
            principal="ml1",
            payload={"trainable_artifact_id": str(b_id)},
        )
        check(
            CID,
            "a revoked connection is 409 connection_revoked",
            revoked_post.status == 409 and revoked_post.json() == {"detail": "connection_revoked"},
        )
        check(
            CID,
            "a revoked connection's mapping stays readable",
            _request(base, "GET", path(org1_id, conn1b_id), principal="viewer1").status == 200,
        )

    # -- The REAL default discovery against an unreachable gateway ------------------------------
    with Session(owner_engine) as s:
        dead = LiteLLMConnection(
            organization_id=org1_id,
            name="gw-dead",
            base_url="https://127.0.0.1:9",
            secret_ref="vault://sovra/dead",
            status="active",
        )
        s.add(dead)
        s.commit()
        dead_id = dead.id
    entries_before = count_entries()
    with _serve(real_default_app) as real_base:
        dead_post = _request(
            real_base,
            "POST",
            path(org1_id, dead_id),
            principal="ml1",
            payload={"trainable_artifact_id": str(a_id)},
        )
    check(
        CID,
        f"the real default discover_models against an unreachable gateway is 503 ({dead_post.status})",
        dead_post.status == 503 and dead_post.json() == {"detail": "gateway_unavailable"},
    )
    check(CID, "...and no entry was created", count_entries() == entries_before)

    # -- RLS, grants, constraints --------------------------------------------------------------------
    with Session(owner_engine) as s:
        flags = {
            row[0]: (row[1], row[2])
            for row in s.execute(
                text(
                    "SELECT relname, relrowsecurity, relforcerowsecurity FROM pg_class "
                    "WHERE relname IN ('model_catalog_entries','model_catalog_mapping_changes')"
                )
            )
        }
        policies = s.execute(
            text(
                "SELECT tablename, policyname, qual, with_check FROM pg_policies "
                "WHERE tablename IN ('model_catalog_entries','model_catalog_mapping_changes')"
            )
        ).all()
    check(
        CID,
        "both tables have ENABLE and FORCE row level security",
        flags
        == {"model_catalog_entries": (True, True), "model_catalog_mapping_changes": (True, True)},
    )
    check(
        CID,
        "each table has one tenant_isolation policy scoped to app.current_organization_id, not allow-all",
        len(policies) == 2
        and all(
            p[1] == "tenant_isolation"
            and "app.current_organization_id" in (p[2] or "")
            and "app.current_organization_id" in (p[3] or "")
            for p in policies
        ),
    )

    with Session(app_engine) as s:
        set_tenant_context(s, org2_id)
        seen = s.execute(text("SELECT count(*) FROM model_catalog_entries")).scalar_one()
        seen_changes = s.execute(
            text("SELECT count(*) FROM model_catalog_mapping_changes")
        ).scalar_one()
        org2_entry_ids = {
            str(r[0]) for r in s.execute(text("SELECT organization_id FROM model_catalog_entries"))
        }
    check(
        CID,
        "under org2's tenant context only org2's rows are visible (RLS)",
        seen == 1 and org2_entry_ids == {str(org2_id)} and seen_changes == 1,
    )

    def refused(fn: Any, error: type[Exception] = DBAPIError) -> bool:
        try:
            fn()
        except error:
            return True
        return False

    def as_org(org: uuid.UUID, statement: str, params: dict[str, Any] | None = None) -> None:
        with Session(app_engine) as s:
            set_tenant_context(s, org)
            s.execute(text(statement), params or {})
            s.commit()

    check(
        CID,
        "a raw INSERT of an org1 entry under org2's tenant context is rejected by RLS",
        refused(
            lambda: as_org(
                org2_id,
                "INSERT INTO model_catalog_entries (organization_id, litellm_connection_id, public_model_name) "
                "VALUES (:o, :c, 'raw')",
                {"o": org1_id, "c": conn1_id},
            )
        ),
    )
    with Session(owner_engine) as s:
        some_entry = s.execute(
            text("SELECT id FROM model_catalog_entries WHERE organization_id=:o LIMIT 1"),
            {"o": org1_id},
        ).scalar_one()
    check(
        CID,
        "a raw INSERT of an org1 change row under org2's tenant context is rejected by RLS",
        refused(
            lambda: as_org(
                org2_id,
                "INSERT INTO model_catalog_mapping_changes (organization_id, model_catalog_entry_id, "
                "previous_catalog_version, new_catalog_version, actor, request_id) "
                "VALUES (:o, :e, 0, 1, 'x', 'r')",
                {"o": org1_id, "e": some_entry},
            )
        ),
    )
    check(
        CID,
        "the app role cannot UPDATE the append-only change log",
        refused(
            lambda: as_org(org1_id, "UPDATE model_catalog_mapping_changes SET actor='tampered'")
        ),
    )
    check(
        CID,
        "the app role cannot DELETE from the change log",
        refused(lambda: as_org(org1_id, "DELETE FROM model_catalog_mapping_changes")),
    )
    check(
        CID,
        "the app role cannot DELETE an entry",
        refused(lambda: as_org(org1_id, "DELETE FROM model_catalog_entries")),
    )
    with Session(owner_engine) as s:
        unchanged = s.execute(
            text("SELECT count(*) FROM model_catalog_mapping_changes WHERE actor='tampered'")
        ).scalar_one()
    check(CID, "no change row was tampered with", unchanged == 0)
    try:
        as_org(
            org1_id,
            "UPDATE model_catalog_entries SET updated_at = now() WHERE organization_id = :o",
            {"o": org1_id},
        )
        entry_update_ok = True
    except DBAPIError:
        entry_update_ok = False
    check(CID, "the app role may UPDATE its own entries (the mapping moves)", entry_update_ok)

    def owner_insert(statement: str, params: dict[str, Any]) -> None:
        with Session(owner_engine) as s:
            s.execute(text(statement), params)
            s.commit()

    check(
        CID,
        "composite FK: an org1 entry cannot point at org2's connection",
        refused(
            lambda: owner_insert(
                "INSERT INTO model_catalog_entries (organization_id, litellm_connection_id, public_model_name) "
                "VALUES (:o, :c, 'x-conn')",
                {"o": org1_id, "c": conn2_id},
            ),
            IntegrityError,
        ),
    )
    check(
        CID,
        "composite FK: an org1 entry cannot point at org2's artifact",
        refused(
            lambda: owner_insert(
                "INSERT INTO model_catalog_entries (organization_id, litellm_connection_id, public_model_name, "
                "trainable_artifact_id) VALUES (:o, :c, 'x-art', :a)",
                {"o": org1_id, "c": conn1_id, "a": foreign_id},
            ),
            IntegrityError,
        ),
    )
    check(
        CID,
        "unique (connection, name): the same alias cannot be inserted twice",
        refused(
            lambda: owner_insert(
                "INSERT INTO model_catalog_entries (organization_id, litellm_connection_id, public_model_name) "
                "VALUES (:o, :c, 'gpt-x')",
                {"o": org1_id, "c": conn1_id},
            ),
            IntegrityError,
        ),
    )
    check(
        CID,
        "CHECK: a blank model name is refused",
        refused(
            lambda: owner_insert(
                "INSERT INTO model_catalog_entries (organization_id, litellm_connection_id, public_model_name) "
                "VALUES (:o, :c, '')",
                {"o": org1_id, "c": conn1_id},
            ),
            IntegrityError,
        ),
    )
    check(
        CID,
        "CHECK: catalog_version 0 is refused",
        refused(
            lambda: owner_insert(
                "INSERT INTO model_catalog_entries (organization_id, litellm_connection_id, public_model_name, "
                "catalog_version) VALUES (:o, :c, 'v0', 0)",
                {"o": org1_id, "c": conn1_id},
            ),
            IntegrityError,
        ),
    )
    check(
        CID,
        "CHECK: a change that skips a version is refused",
        refused(
            lambda: owner_insert(
                "INSERT INTO model_catalog_mapping_changes (organization_id, model_catalog_entry_id, "
                "previous_catalog_version, new_catalog_version, actor, request_id) "
                "VALUES (:o, :e, 1, 3, 'x', 'r')",
                {"o": org1_id, "e": some_entry},
            ),
            IntegrityError,
        ),
    )
    check(
        CID,
        "composite FK: a change cannot reference another organization's entry",
        refused(
            lambda: owner_insert(
                "INSERT INTO model_catalog_mapping_changes (organization_id, model_catalog_entry_id, "
                "previous_catalog_version, new_catalog_version, actor, request_id) "
                "VALUES (:o, :e, 0, 1, 'x', 'r')",
                {"o": org2_id, "e": some_entry},
            ),
            IntegrityError,
        ),
    )
    check(
        CID,
        "RESTRICT: a connection with mapping entries cannot be deleted",
        refused(
            lambda: owner_insert("DELETE FROM litellm_connections WHERE id=:c", {"c": conn1_id}),
            IntegrityError,
        ),
    )
    check(
        CID,
        "RESTRICT: an artifact referenced by an entry cannot be deleted",
        refused(
            lambda: owner_insert("DELETE FROM trainable_artifacts WHERE id=:a", {"a": foreign_id}),
            IntegrityError,
        ),
    )

    if FAILURES:
        print(f"FTF-0308-P1 model catalog mapping real proof: {len(FAILURES)} failed")
        for failure in FAILURES:
            print(f"  {failure}")
        sys.exit(1)
    print("FTF-0308-P1 model catalog mapping real proof: all checks passed")
    sys.exit(0)


if __name__ == "__main__":
    main()
