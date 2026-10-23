#!/usr/bin/env python3
# mypy: disable-error-code="arg-type,attr-defined,return-value,assignment,call-arg,index,misc,no-untyped-def,var-annotated,union-attr,operator,no-any-return"
# ruff: noqa: E501
"""FTF-H010-P4 -- real PostgreSQL 16 proof of model role assignments (migration 0037), the roles
and actions routes, and the pure continuation rules over real rows.

Never SQLite (no RLS, no triggers, no `~`). Requires `SOVRA_DATABASE_URL` (the migration owner,
like every sibling integration job) and `SOVRA_APP_DB_PASSWORD`. Every request runs through the
real routers on a real uvicorn server as the restricted `sovra_app` role.

Proves:

  M   migration 0037: upgrade -> downgrade -> upgrade, one head, the additive unique key on
      model_versions comes and goes with it
  G   grants: `sovra_app` has SELECT/INSERT, UPDATE on exactly (revoked_at, revoked_by,
      revoke_reason_code), no DELETE/TRUNCATE; ENABLE + FORCE RLS with one non-allow-all policy
  C   CHECK truth tables (role enum, blank assigned_by, idempotency key, sha256, revocation columns)
  T   role truth table: 5 roles x {validated endpoint, validated weights, validated adapter,
      unvalidated FTF-1001 row} x 6 organization roles -> exact status and code
  B   strict body table: exact role enum (`Judge`, `judge `, `ｊudge`, `judge<ZWSP>` refused),
      exact keys, idempotency key boundary 0/1/128/129 and non-ASCII
  I   idempotent replay: same key + same body -> 200, 0 new rows, 0 new audit; same key + other
      body -> 409
  K   concurrency: two threads posting `current_production` -> exactly one 201; the same role on
      the same version -> one 201; `judge` + `candidate` racing -> one succeeds
  S   self-judging refused by the trigger in both insert orders (raw SQL) and by the route
  R   revoke: 200, a second revoke 409, a raw un-revoke refused by the trigger, a raw role change
      refused by the grant, re-assignment after revoke
  X   cross-tenant: identical 404 for a foreign, a wrong-project and a missing id; a raw org2
      INSERT refused; META-LEAK markers never reach an error body or an audit row
  F   DATABASE faults (each inside a rolled-back owner transaction) flip a probe: each widened
      grant, the incumbent index, the idempotency index, each self-judging direction, the
      validation clause, un-revoke allowed, FORCE dropped
  A   actions route over real rows: every origin, revoked license, adapter base failures,
      endpoint ref/alias variants, the incumbent, viewer reads, 401 and cross-tenant 404

Run directly:
    SOVRA_DATABASE_URL=postgresql+psycopg://<superuser>@host/db \\
    SOVRA_APP_DB_PASSWORD=<app-role-password> \\
    python3 tests/integration/test_ftf_h010_p4_model_roles.py
"""

from __future__ import annotations

import contextlib
import http.client
import json
import logging
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
from app.litellm.connection_schema import LiteLLMConnection  # noqa: E402
from app.model_versions.model_role_routes import build_model_role_router  # noqa: E402
from app.projects.project_schema import Project  # noqa: E402

CID = "FTF-H010-P4"
FAILURES: list[str] = []
CHECKS = [0]
_ISSUER = "https://issuer.example"
MARKER = "LEAK-MARKER-7c1e"
ROLES = ("current_production", "baseline", "candidate", "teacher", "judge")
ORG_ROLES = (
    "organization_admin",
    "workspace_admin",
    "ml_engineer",
    "evaluator",
    "approver",
    "viewer",
)
ADMIN_ONLY = ("current_production", "judge")
WRITE_ROLES = ("organization_admin", "ml_engineer")


def check(criterion_id: str, description: str, condition: bool) -> None:
    CHECKS[0] += 1
    if not condition:
        FAILURES.append(f"FAIL [{criterion_id}] {description}")


def _required_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        print(f"BLOCKED_EXTERNAL: {name} is not set (needs a live Postgres)", file=sys.stderr)
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

    @property
    def detail(self) -> Any:
        try:
            return json.loads(self.body).get("detail")
        except Exception:  # noqa: BLE001
            return None


def _request(
    base: str,
    method: str,
    path: str,
    *,
    principal: str | None,
    payload: Any = None,
    raw: bytes | None = None,
) -> _Http:
    headers: dict[str, str] = {}
    if principal is not None:
        headers["X-Test-Principal"] = principal
    body: bytes | None = raw
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
    if body is not None:
        headers["Content-Type"] = "application/json"
    parsed = urllib.parse.urlparse(base)
    conn = http.client.HTTPConnection(parsed.hostname or "127.0.0.1", parsed.port or 80, timeout=30)
    try:
        conn.request(method, path, body=body, headers=headers)
        response = conn.getresponse()
        return _Http(response.status, response.read())
    finally:
        conn.close()


def _set_tenant(session: Session, org_id: uuid.UUID) -> None:
    session.execute(
        text("SELECT set_config('app.current_organization_id', :org_id, true)"),
        {"org_id": str(org_id)},
    )


def _constraint_name(exc: DBAPIError) -> str | None:
    diag = getattr(exc.orig, "diag", None)
    return None if diag is None else diag.constraint_name


def _sqlstate(exc: DBAPIError) -> str | None:
    diag = getattr(exc.orig, "diag", None)
    return None if diag is None else diag.sqlstate


class Logs(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.lines: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.lines.append(record.getMessage())


def main() -> None:
    migration_url = _required_env("SOVRA_DATABASE_URL")
    app_password = _required_env("SOVRA_APP_DB_PASSWORD")
    logs = Logs()
    logging.getLogger().addHandler(logs)

    # ---------------------------------------------------------------- M: migration
    for label, args in (
        ("upgrade head", ["upgrade", "head"]),
        ("downgrade -1", ["downgrade", "-1"]),
        ("upgrade head again", ["upgrade", "head"]),
    ):
        result = _alembic(args, migration_url)
        check(
            "M",
            f"alembic {label} runs cleanly\n{result.stdout}\n{result.stderr}",
            result.returncode == 0,
        )
        if label == "downgrade -1":
            owner_probe = create_engine(migration_url)
            with Session(owner_probe) as s:
                gone = s.execute(
                    text("SELECT to_regclass('public.model_role_assignments')")
                ).scalar()
                uq = s.execute(
                    text(
                        "SELECT count(*) FROM pg_constraint WHERE conname = 'uq_model_versions_organization_id_project_id_id'"
                    )
                ).scalar()
            owner_probe.dispose()
            check(
                "M",
                "the downgrade drops the table and the additive model_versions key",
                gone is None and uq == 0,
            )
    heads = _alembic(["heads"], migration_url)
    check(
        "M",
        f"exactly one head, 0037 on its chain: {heads.stdout!r}",
        heads.stdout.count("(head)") == 1 and "0037_model_role_assignments" in heads.stdout,
    )
    if FAILURES:
        print("\n".join(FAILURES))
        sys.exit(1)

    owner_engine = create_engine(migration_url)
    app_engine = create_engine(
        _app_role_url(migration_url, app_password), pool_size=20, max_overflow=20
    )
    session_factory = sessionmaker(bind=app_engine)
    check(
        "M",
        "requests run as the restricted sovra_app role",
        app_engine.url.username == APP_ROLE_NAME,
    )

    # ---------------------------------------------------------------- fixtures
    suffix = uuid.uuid4().hex[:8]
    with Session(owner_engine) as session:
        org1 = Organization(name="Acme H010P4", slug=f"acme-h010p4-{suffix}")
        org2 = Organization(name="Globex H010P4", slug=f"globex-h010p4-{suffix}")
        session.add_all([org1, org2])
        session.flush()
        ws1 = Workspace(organization_id=org1.id, name="Acme Main", slug="main")
        ws2 = Workspace(organization_id=org2.id, name="Globex Main", slug="main")
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

        p1, p1b, p2 = project(org1, ws1, "p1"), project(org1, ws1, "p1b"), project(org2, ws2, "p2")
        session.add_all([p1, p1b, p2])
        session.flush()

        def artifact(org: Organization, name: str, digest: str) -> TrainableArtifact:
            return TrainableArtifact(
                organization_id=org.id,
                display_name=name,
                source_type="huggingface",
                source_uri="acme-org/real-base-model",
                model_family="llama",
                base_model_revision="a1b2c3d4e5f6",
                weights_digest="sha256:" + digest * 64,
                license_id="apache-2.0",
                license_accepted_by="seed",
                license_accepted_at=datetime.now(UTC),
                trust_remote_code_allowed=False,
                required_python_packages_json=[],
                minimum_gpu_profile_json={},
                status="ready",
            )

        base_art = artifact(org1, "base-art", "b")
        weights_art = artifact(org1, "weights-art", "c")
        adapter_art = artifact(org1, "adapter-art", "d")
        extra_arts = [artifact(org1, f"extra-{i}", f"{i:x}") for i in range(1, 10)]
        session.add_all([base_art, weights_art, adapter_art, *extra_arts])
        conn = LiteLLMConnection(
            organization_id=org1.id,
            name="h010p4-gateway",
            base_url="https://gateway.example",
            secret_ref="openbao://secret/litellm/org1/api_key",
        )
        session.add(conn)
        session.flush()
        session.commit()
        org1_id, org2_id = org1.id, org2.id
        p1_id, p1b_id, p2_id = p1.id, p1b.id, p2.id
        base_art_id, weights_art_id, adapter_art_id = base_art.id, weights_art.id, adapter_art.id
        conn_id = conn.id
        ws1_id = ws1.id

    counter = {"n": 1000}

    def new_project() -> uuid.UUID:
        counter["n"] += 1
        with Session(owner_engine) as s:
            extra = Project(
                organization_id=org1_id,
                workspace_id=ws1_id,
                name=f"proj-{counter['n']}-{suffix}",
                project_type="generic_model_tuning",
                status="active",
                security_profile="standard",
                row_version=1,
                created_by="seed",
            )
            s.add(extra)
            s.commit()
            return extra.id

    def make_version(
        project_id: uuid.UUID,
        *,
        kind: str,
        validated: bool,
        org_id: uuid.UUID = org1_id,
        artifact_id: uuid.UUID | None = None,
        digest_char: str | None = None,
        parent: uuid.UUID | None = None,
        ref: str = "provider/model-a",
    ) -> uuid.UUID:
        """A model version (owner role). kind: endpoint | weights | adapter | ftf1001 | factory."""
        counter["n"] += 1
        vid = uuid.uuid4()
        params: dict[str, Any] = {
            "id": str(vid),
            "org": str(org_id),
            "project": str(project_id),
            "n": counter["n"],
            "ref": ref,
            "parent": None if parent is None else str(parent),
            "artifact": None if artifact_id is None else str(artifact_id),
            "digest": None if digest_char is None else "sha256:" + digest_char * 64,
        }
        if kind == "endpoint":
            sql = (
                "INSERT INTO model_versions (id, organization_id, project_id, version_number, import_kind, origin, "
                "external_provider_model_ref, created_by) VALUES (:id, :org, :project, :n, 'endpoint', "
                "'external_endpoint', :ref, 'seed')"
            )
        elif kind == "ftf1001":
            sql = (
                "INSERT INTO model_versions (id, organization_id, project_id, version_number, import_kind, origin, "
                "endpoint_reference, external_provider_model_ref, created_by) VALUES (:id, :org, :project, :n, "
                "'endpoint', 'external_endpoint', 'https://x.example/v1', :ref, 'seed')"
            )
        elif kind in ("weights", "factory"):
            origin = "imported_weights" if kind == "weights" else "factory_trained"
            sql = (
                "INSERT INTO model_versions (id, organization_id, project_id, version_number, import_kind, origin, "
                "trainable_artifact_id, weights_digest, base_model_revision, created_by) VALUES (:id, :org, "
                f":project, :n, 'full_weight', '{origin}', :artifact, :digest, 'a1b2c3d4e5f6', 'seed')"
            )
        else:
            sql = (
                "INSERT INTO model_versions (id, organization_id, project_id, version_number, import_kind, origin, "
                "adapter_only, parent_model_version_id, trainable_artifact_id, weights_digest, base_model_revision, "
                "created_by) VALUES (:id, :org, :project, :n, 'adapter', 'imported_adapter', true, :parent, "
                ":artifact, :digest, 'a1b2c3d4e5f6', 'seed')"
            )
        with Session(owner_engine) as s:
            s.execute(text(sql), params)
            if validated and kind in ("endpoint", "weights", "adapter"):
                origin = {
                    "endpoint": "external_endpoint",
                    "weights": "imported_weights",
                    "adapter": "imported_adapter",
                }[kind]
                job = uuid.uuid4()
                s.execute(
                    text(
                        "INSERT INTO model_import_jobs (id, organization_id, project_id, requested_origin, state, "
                        "created_by, litellm_connection_id, parent_model_version_id, trainable_artifact_id, "
                        "external_provider_model_ref) VALUES (:id, :org, :project, :origin, 'queued', 'seed', "
                        ":conn, :parent, :artifact, :ref)"
                    ),
                    {
                        "id": str(job),
                        "org": str(org_id),
                        "project": str(project_id),
                        "origin": origin,
                        "conn": str(conn_id) if kind == "endpoint" else None,
                        "parent": params["parent"] if kind == "adapter" else None,
                        "artifact": params["artifact"] if kind != "endpoint" else None,
                        "ref": ref if kind == "endpoint" else None,
                    },
                )
                s.execute(
                    text(
                        "UPDATE model_import_jobs SET state = 'validating', claim_epoch = 1, "
                        "lease_expires_at = now() + interval '1 hour' WHERE id = :id"
                    ),
                    {"id": str(job)},
                )
                s.execute(
                    text(
                        "UPDATE model_import_jobs SET state = 'succeeded', lease_expires_at = NULL, "
                        "result_model_version_id = :v WHERE id = :id"
                    ),
                    {"id": str(job), "v": str(vid)},
                )
            s.commit()
        return vid

    principals = {
        f"{role}1": (
            AuthenticatedPrincipal(issuer=_ISSUER, subject=f"{role}-1"),
            Role(role),
            org1_id,
        )
        for role in ORG_ROLES
    }
    principals["admin2"] = (
        AuthenticatedPrincipal(issuer=_ISSUER, subject="admin-2"),
        Role.ORGANIZATION_ADMIN,
        org2_id,
    )

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
        build_model_role_router(
            session_factory, principal_resolver=principal_resolver, role_lookup=role_lookup
        )
    )

    def roles_path(org: uuid.UUID, project: uuid.UUID, version: Any) -> str:
        return f"/api/v1/organizations/{org}/projects/{project}/model-versions/{version}/roles"

    def revoke_path(org: uuid.UUID, project: uuid.UUID, version: Any, role: str) -> str:
        return f"{roles_path(org, project, version)}/{urllib.parse.quote(role, safe='')}/revoke"

    def actions_path(org: uuid.UUID, project: uuid.UUID, version: Any) -> str:
        return f"/api/v1/organizations/{org}/projects/{project}/model-versions/{version}/actions"

    def owner_count(sql: str, **params: Any) -> int:
        with Session(owner_engine) as s:
            return int(s.execute(text(sql), params).scalar())

    def rows_for(version: uuid.UUID) -> int:
        return owner_count(
            "SELECT count(*) FROM model_role_assignments WHERE model_version_id = :v",
            v=str(version),
        )

    def audits() -> int:
        return owner_count("SELECT count(*) FROM audit_events WHERE action LIKE 'model_role.%'")

    def post_role(
        base: str,
        principal: str,
        version: Any,
        role: Any,
        key: Any,
        *,
        project: uuid.UUID = p1_id,
        org: uuid.UUID = org1_id,
    ) -> _Http:
        return _request(
            base,
            "POST",
            roles_path(org, project, version),
            principal=principal,
            payload={"role": role, "idempotency_key": key},
        )

    with _serve(app) as base:
        # ------------------------------------------------------------ G: grants + RLS
        with Session(owner_engine) as s:
            privs = {
                row[0]
                for row in s.execute(
                    text(
                        "SELECT privilege_type FROM information_schema.role_table_grants WHERE grantee = :r AND table_name = 'model_role_assignments'"
                    ),
                    {"r": APP_ROLE_NAME},
                )
            }
            upd_cols = {
                row[0]
                for row in s.execute(
                    text(
                        "SELECT column_name FROM information_schema.column_privileges WHERE grantee = :r AND table_name = 'model_role_assignments' AND privilege_type = 'UPDATE'"
                    ),
                    {"r": APP_ROLE_NAME},
                )
            }
            rls = s.execute(
                text(
                    "SELECT relrowsecurity, relforcerowsecurity FROM pg_class WHERE relname = 'model_role_assignments'"
                )
            ).one()
            policies = s.execute(
                text(
                    "SELECT polname, pg_get_expr(polqual, polrelid), pg_get_expr(polwithcheck, polrelid) FROM pg_policy WHERE polrelid = 'model_role_assignments'::regclass"
                )
            ).all()
        check(
            "G",
            f"sovra_app privileges are SELECT+INSERT only at table level ({sorted(privs)})",
            privs == {"SELECT", "INSERT"},
        )
        check(
            "G",
            f"UPDATE is limited to exactly the three revocation columns ({sorted(upd_cols)})",
            upd_cols == {"revoked_at", "revoked_by", "revoke_reason_code"},
        )
        check(
            "G",
            "no DELETE and no TRUNCATE privilege",
            "DELETE" not in privs and "TRUNCATE" not in privs,
        )
        check("G", "ENABLE + FORCE row level security", tuple(rls) == (True, True))
        check(
            "G",
            "one non-allow-all tenant policy",
            len(policies) == 1
            and "app.current_organization_id" in str(policies[0][1])
            and "app.current_organization_id" in str(policies[0][2]),
        )

        # ------------------------------------------------------------ T: role truth table
        kinds = ("endpoint", "weights", "adapter", "ftf1001")
        base_weights = make_version(
            p1_id, kind="weights", validated=True, artifact_id=base_art_id, digest_char="b"
        )
        cells = 0
        for kind in kinds:
            for role in ROLES:
                for org_role in ORG_ROLES:
                    tp = new_project() if role == "current_production" else p1_id
                    if kind == "endpoint":
                        vid = make_version(tp, kind="endpoint", validated=True)
                    elif kind == "weights":
                        vid = make_version(
                            tp,
                            kind="weights",
                            validated=True,
                            artifact_id=weights_art_id,
                            digest_char="c",
                        )
                    elif kind == "adapter":
                        vid = make_version(
                            tp,
                            kind="adapter",
                            validated=True,
                            artifact_id=adapter_art_id,
                            digest_char="d",
                            parent=base_weights,
                        )
                    else:
                        vid = make_version(tp, kind="ftf1001", validated=False)
                    r = post_role(
                        base, f"{org_role}1", vid, role, f"t-{uuid.uuid4().hex}", project=tp
                    )
                    cells += 1
                    if org_role not in WRITE_ROLES or (
                        role in ADMIN_ONLY and org_role != "organization_admin"
                    ):
                        want = (403, None)
                    elif kind == "ftf1001":
                        want = (409, "import_not_validated")
                    else:
                        want = (201, None)
                    got = (r.status, r.detail if r.status != 201 else None)
                    if want[0] == 403:
                        got = (r.status, None)
                    check("T", f"{kind} {role} as {org_role}: want {want} got {got}", got == want)
                    if want[0] != 201:
                        check(
                            "T",
                            f"{kind} {role} as {org_role}: a refusal writes no row",
                            rows_for(vid) == 0,
                        )
                    else:
                        body = r.json() if r.status == 201 else {}
                        check(
                            "T",
                            f"{kind} {role}: 201 body names the role and the actor",
                            body.get("role") == role
                            and body.get("assigned_by") == f"{org_role}-1"
                            and body.get("revoked_at") is None,
                        )
        check(
            "T",
            "role truth table covered every cell",
            cells == len(kinds) * len(ROLES) * len(ORG_ROLES),
        )
        unauth = _request(
            base,
            "POST",
            roles_path(org1_id, p1_id, base_weights),
            principal=None,
            payload={"role": "baseline", "idempotency_key": "k"},
        )
        check("T", "an unauthenticated POST is 401", unauth.status == 401)

        # ------------------------------------------------------------ B: strict body table
        v_body = make_version(
            p1_id, kind="weights", validated=True, artifact_id=weights_art_id, digest_char="c"
        )
        for bad_role in (
            "Judge",
            "judge ",
            " judge",
            "ｊudge",
            "judge​",
            "JUDGE",
            "judge\x00",
            "",
            "candidate;",
            "current-production",
        ):
            r = post_role(base, "organization_admin1", v_body, bad_role, "k-body")
            check(
                "B",
                f"role {bad_role!r} -> 400 invalid_role",
                r.status == 400 and r.detail == "invalid_role",
            )
        for bad_role in (None, 1, True, ["judge"], {"r": 1}):
            r = post_role(base, "organization_admin1", v_body, bad_role, "k-body")
            check(
                "B",
                f"non-str role {bad_role!r} -> 400 invalid_body",
                r.status == 400 and r.detail == "invalid_body",
            )
        for payload in (
            {"role": "baseline"},
            {"idempotency_key": "k"},
            {},
            {"role": "baseline", "idempotency_key": "k", "extra": 1},
            [],
            "x",
            1,
            None,
        ):
            r = _request(
                base,
                "POST",
                roles_path(org1_id, p1_id, v_body),
                principal="organization_admin1",
                payload=payload,
            )
            check(
                "B",
                f"body {payload!r} -> 400 invalid_body",
                r.status == 400 and r.detail == "invalid_body",
            )
        r = _request(
            base,
            "POST",
            roles_path(org1_id, p1_id, v_body),
            principal="organization_admin1",
            payload={"role": "baseline", "idempotency_key": "k", "organization_id": str(org2_id)},
        )
        check(
            "B",
            f"a body naming another organization is refused ({r.status}) and writes nothing",
            r.status in (400, 403) and rows_for(v_body) == 0,
        )
        for raw in (b"{", b"", b"\xff\xfe", b"nope"):
            r = _request(
                base,
                "POST",
                roles_path(org1_id, p1_id, v_body),
                principal="organization_admin1",
                raw=raw,
            )
            check(
                "B",
                f"raw body {raw!r} -> 400 invalid_body",
                r.status == 400 and r.detail == "invalid_body",
            )
        for key, ok in (
            ("", False),
            ("a", True),
            ("a" * 128, True),
            ("a" * 129, False),
            ("é", False),
            ("k\n", False),
            ("k\x7f", False),
            ("ｋ", False),
            (" ", True),
            ("~", True),
            (1, False),
            (None, False),
            (["k"], False),
        ):
            r = post_role(base, "organization_admin1", v_body, "teacher", key)
            if ok:
                check(
                    "B",
                    f"idempotency key {key[:12]!r} accepted (length {len(key)})",
                    r.status in (201, 200),
                )
                if r.status == 201:
                    _request(
                        base,
                        "POST",
                        revoke_path(org1_id, p1_id, v_body, "teacher"),
                        principal="organization_admin1",
                    )
            else:
                check(
                    "B",
                    f"idempotency key {str(key)[:12]!r} refused 400",
                    r.status == 400 and r.detail == "invalid_body",
                )
        r = post_role(base, "organization_admin1", v_body, "Judge", "k")
        check(
            "B",
            "an admin-only spelling that is not the exact enum is a 400, not a 403 or an assignment",
            r.status == 400,
        )
        r = post_role(base, "ml_engineer1", v_body, "Judge", "k-ml")
        check(
            "B",
            "the same 400 for a non-admin (enum is checked before the admin rule)",
            r.status == 400,
        )

        # ------------------------------------------------------------ I: idempotency
        v_idem = make_version(
            p1_id, kind="weights", validated=True, artifact_id=weights_art_id, digest_char="c"
        )
        before_rows, before_audit = rows_for(v_idem), audits()
        r1 = post_role(base, "ml_engineer1", v_idem, "baseline", "idem-1")
        check("I", "first POST is 201", r1.status == 201)
        check(
            "I",
            "the first POST wrote one row and one audit event",
            rows_for(v_idem) == before_rows + 1 and audits() == before_audit + 1,
        )
        r2 = post_role(base, "ml_engineer1", v_idem, "baseline", "idem-1")
        check(
            "I",
            "same key + same body replays 200 with the same assignment",
            r2.status == 200 and r2.json()["id"] == r1.json()["id"],
        )
        check(
            "I",
            "the replay adds 0 rows and 0 audit events",
            rows_for(v_idem) == before_rows + 1 and audits() == before_audit + 1,
        )
        r3 = post_role(base, "ml_engineer1", v_idem, "candidate", "idem-1")
        check(
            "I",
            "same key + a different role -> 409 idempotency_key_reused",
            r3.status == 409 and r3.detail == "idempotency_key_reused",
        )
        v_idem2 = make_version(
            p1_id, kind="weights", validated=True, artifact_id=weights_art_id, digest_char="c"
        )
        r4 = post_role(base, "ml_engineer1", v_idem2, "baseline", "idem-1")
        check(
            "I",
            "same key + the same role on another version -> 409 idempotency_key_reused",
            r4.status == 409 and r4.detail == "idempotency_key_reused",
        )
        check(
            "I",
            "a reuse writes nothing",
            rows_for(v_idem2) == 0 and rows_for(v_idem) == before_rows + 1,
        )
        r5 = post_role(base, "ml_engineer1", v_idem, "baseline", "idem-2")
        check(
            "I",
            "a new key for an assigned role -> 409 role_already_assigned",
            r5.status == 409 and r5.detail == "role_already_assigned",
        )
        r6 = post_role(base, "ml_engineer1", v_idem, "baseline", "idem-1", project=p1b_id)
        check(
            "I",
            "a wrong project path is a 404 even for a stored key",
            r6.status == 404 and r6.detail == "not_found",
        )

        # ------------------------------------------------------------ K: concurrency
        def race(calls: list[tuple[str, Any, Any, str]], project: uuid.UUID = p1_id) -> list[_Http]:
            barrier = threading.Barrier(len(calls))
            out: list[_Http | None] = [None] * len(calls)

            def run(i: int) -> None:
                principal, version, role, key = calls[i]
                barrier.wait()
                out[i] = post_role(base, principal, version, role, key, project=project)

            threads = [threading.Thread(target=run, args=(i,)) for i in range(len(calls))]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
            return [o for o in out if o is not None]

        for trial in range(8):
            proj = new_project()
            va = make_version(
                proj, kind="weights", validated=True, artifact_id=weights_art_id, digest_char="c"
            )
            vb = make_version(
                proj, kind="weights", validated=True, artifact_id=weights_art_id, digest_char="c"
            )
            res = race(
                [
                    (
                        "organization_admin1",
                        va,
                        "current_production",
                        f"inc-a-{trial}-{uuid.uuid4().hex[:6]}",
                    ),
                    (
                        "organization_admin1",
                        vb,
                        "current_production",
                        f"inc-b-{trial}-{uuid.uuid4().hex[:6]}",
                    ),
                ],
                project=proj,
            )
            statuses = sorted(r.status for r in res)
            check(
                "K",
                f"trial {trial}: two current_production posts -> exactly one 201 and one 409 ({statuses})",
                statuses == [201, 409],
            )
            check(
                "K",
                f"trial {trial}: the loser is incumbent_exists",
                sorted(str(r.detail) for r in res if r.status == 409) == ["incumbent_exists"],
            )
            check(
                "K",
                f"trial {trial}: exactly one active incumbent in the project",
                owner_count(
                    "SELECT count(*) FROM model_role_assignments WHERE project_id = :p AND role = 'current_production' AND revoked_at IS NULL",
                    p=str(proj),
                )
                == 1,
            )
            owner_winner = next(v for v in (va, vb) if rows_for(v) == 1)
            _request(
                base,
                "POST",
                revoke_path(org1_id, proj, owner_winner, "current_production"),
                principal="organization_admin1",
            )
            vc = make_version(
                p1_id, kind="weights", validated=True, artifact_id=weights_art_id, digest_char="c"
            )
            res = race(
                [
                    ("ml_engineer1", vc, "baseline", f"same-a-{trial}-{uuid.uuid4().hex[:6]}"),
                    ("ml_engineer1", vc, "baseline", f"same-b-{trial}-{uuid.uuid4().hex[:6]}"),
                ]
            )
            check(
                "K",
                f"trial {trial}: the same role on the same version -> one 201, one role_already_assigned",
                sorted(r.status for r in res) == [201, 409]
                and [r.detail for r in res if r.status == 409] == ["role_already_assigned"],
            )
            vd = make_version(
                p1_id, kind="weights", validated=True, artifact_id=weights_art_id, digest_char="c"
            )
            res = race(
                [
                    ("organization_admin1", vd, "judge", f"j-{trial}-{uuid.uuid4().hex[:6]}"),
                    ("ml_engineer1", vd, "candidate", f"c-{trial}-{uuid.uuid4().hex[:6]}"),
                ]
            )
            check(
                "K",
                f"trial {trial}: judge and candidate racing on one version -> one 201, one self_judging_role_conflict ({[r.status for r in res]})",
                sorted(r.status for r in res) == [201, 409]
                and [r.detail for r in res if r.status == 409] == ["self_judging_role_conflict"],
            )
            check(
                "K",
                f"trial {trial}: never both judge and candidate active",
                owner_count(
                    "SELECT count(*) FROM model_role_assignments WHERE model_version_id = :v AND revoked_at IS NULL",
                    v=str(vd),
                )
                == 1,
            )
            ve = make_version(
                p1_id, kind="weights", validated=True, artifact_id=weights_art_id, digest_char="c"
            )
            same_key = f"samekey-{trial}-{uuid.uuid4().hex[:6]}"
            res = race([("ml_engineer1", ve, "baseline", same_key)] * 4)
            check(
                "K",
                f"trial {trial}: 4 parallel posts with one key -> one 201, the rest 200 replays ({sorted(r.status for r in res)})",
                sorted(r.status for r in res) == [200, 200, 200, 201],
            )
            check("K", f"trial {trial}: one row for the parallel key", rows_for(ve) == 1)

        # ------------------------------------------------------------ S: self-judging
        v_self = make_version(
            p1_id, kind="weights", validated=True, artifact_id=weights_art_id, digest_char="c"
        )

        def raw_insert(
            org: uuid.UUID,
            version: uuid.UUID,
            role: str,
            key: str,
            *,
            tenant: uuid.UUID | None = None,
            project: uuid.UUID = p1_id,
            triggers_off: bool = False,
            **extra: Any,
        ) -> tuple[bool, str | None, str | None]:
            columns = {
                "organization_id": str(org),
                "project_id": str(project),
                "model_version_id": str(version),
                "role": role,
                "assigned_by": "raw",
                "request_id": "raw",
                "idempotency_key": key,
                "request_sha256": "a" * 64,
            }
            columns.update(extra)
            names = ", ".join(columns)
            values = ", ".join(f":{c}" for c in columns)
            if triggers_off:
                # the owner takes the user triggers out of the way inside a transaction that is
                # rolled back, then inserts AS sovra_app, so a CHECK / FK / RLS is what refuses
                with Session(owner_engine) as s:
                    try:
                        s.execute(text("ALTER TABLE model_role_assignments DISABLE TRIGGER USER"))
                        s.execute(text(f'SET LOCAL ROLE "{APP_ROLE_NAME}"'))
                        _set_tenant(s, tenant or org)
                        s.execute(
                            text(f"INSERT INTO model_role_assignments ({names}) VALUES ({values})"),
                            columns,
                        )
                        return True, None, None
                    except DBAPIError as exc:
                        return False, _constraint_name(exc), _sqlstate(exc)
                    finally:
                        s.rollback()
            with Session(app_engine) as s:
                _set_tenant(s, tenant or org)
                try:
                    s.execute(
                        text(f"INSERT INTO model_role_assignments ({names}) VALUES ({values})"),
                        columns,
                    )
                    s.commit()
                    return True, None, None
                except DBAPIError as exc:
                    s.rollback()
                    return False, _constraint_name(exc), _sqlstate(exc)

        ok, _, _ = raw_insert(org1_id, v_self, "candidate", "s-1")
        check("S", "control: a raw candidate insert succeeds", ok)
        ok, name, state = raw_insert(org1_id, v_self, "judge", "s-2")
        check(
            "S",
            f"judge after candidate refused BY NAME (got {name!r}/{state})",
            not ok and name == "tr_model_role_assignments_self_judging" and state == "23514",
        )
        v_self2 = make_version(
            p1_id, kind="weights", validated=True, artifact_id=weights_art_id, digest_char="c"
        )
        ok, _, _ = raw_insert(org1_id, v_self2, "judge", "s-3")
        check("S", "control: a raw judge insert succeeds", ok)
        ok, name, state = raw_insert(org1_id, v_self2, "candidate", "s-4")
        check(
            "S",
            f"candidate after judge refused BY NAME (got {name!r}/{state})",
            not ok and name == "tr_model_role_assignments_self_judging",
        )
        with Session(owner_engine) as s:
            s.execute(
                text(
                    "UPDATE model_role_assignments SET revoked_at = now(), revoked_by = 'x', revoke_reason_code = 'x_y' WHERE model_version_id = :v AND role = 'judge'"
                ),
                {"v": str(v_self2)},
            )
            s.commit()
        ok, _, _ = raw_insert(org1_id, v_self2, "candidate", "s-5")
        check("S", "a revoked judge no longer blocks a candidate", ok)
        v_self3 = make_version(
            p1_id, kind="weights", validated=True, artifact_id=weights_art_id, digest_char="c"
        )
        post_role(base, "ml_engineer1", v_self3, "candidate", "s-6")
        r = post_role(base, "organization_admin1", v_self3, "judge", "s-7")
        check(
            "S",
            "the route maps it to 409 self_judging_role_conflict",
            r.status == 409 and r.detail == "self_judging_role_conflict",
        )
        v_self4 = make_version(
            p1_id, kind="weights", validated=True, artifact_id=weights_art_id, digest_char="c"
        )
        post_role(base, "organization_admin1", v_self4, "judge", "s-8")
        r = post_role(base, "ml_engineer1", v_self4, "candidate", "s-9")
        check(
            "S",
            "and in the other order",
            r.status == 409 and r.detail == "self_judging_role_conflict",
        )
        for other in ("baseline", "teacher"):
            r = post_role(base, "ml_engineer1", v_self3, other, f"s-o-{other}")
            check("S", f"{other} is compatible with candidate", r.status == 201)

        # ------------------------------------------------------------ R: revoke
        v_rev = make_version(
            p1_id, kind="weights", validated=True, artifact_id=weights_art_id, digest_char="c"
        )
        first = post_role(base, "organization_admin1", v_rev, "current_production", "r-1").json()
        before_audit = audits()
        r = _request(
            base,
            "POST",
            revoke_path(org1_id, p1_id, v_rev, "current_production"),
            principal="organization_admin1",
        )
        body = r.json() if r.status == 200 else {}
        check(
            "R",
            "revoke -> 200 with the revocation columns set",
            r.status == 200
            and body.get("revoked_at")
            and body.get("revoked_by") == "organization_admin-1"
            and body.get("revoke_reason_code") == "revoked_by_actor"
            and body.get("id") == first["id"],
        )
        check("R", "a revoke writes one audit event", audits() == before_audit + 1)
        r = _request(
            base,
            "POST",
            revoke_path(org1_id, p1_id, v_rev, "current_production"),
            principal="organization_admin1",
        )
        check(
            "R",
            "a second revoke -> 409 role_not_assigned",
            r.status == 409 and r.detail == "role_not_assigned",
        )
        r = _request(
            base,
            "POST",
            revoke_path(org1_id, p1_id, v_rev, "judge"),
            principal="organization_admin1",
        )
        check(
            "R",
            "revoking a role that was never assigned -> 409 role_not_assigned",
            r.status == 409 and r.detail == "role_not_assigned",
        )
        r = _request(
            base,
            "POST",
            revoke_path(org1_id, p1_id, v_rev, "Judge"),
            principal="organization_admin1",
        )
        check(
            "R",
            "an inexact role in the path -> 400 invalid_role",
            r.status == 400 and r.detail == "invalid_role",
        )
        r = _request(
            base,
            "POST",
            revoke_path(org1_id, p1_id, v_rev, "current_production"),
            principal="ml_engineer1",
        )
        check("R", "an ml_engineer cannot revoke the incumbent (403)", r.status == 403)
        r = _request(
            base, "POST", revoke_path(org1_id, p1_id, v_rev, "baseline"), principal="viewer1"
        )
        check("R", "a viewer cannot revoke (403)", r.status == 403)
        r = _request(
            base,
            "POST",
            revoke_path(org1_id, p1_id, v_rev, "baseline"),
            principal="organization_admin1",
            payload={"reason": "x"},
        )
        check(
            "R",
            "a revoke body with fields -> 400 invalid_body",
            r.status == 400 and r.detail == "invalid_body",
        )
        again = post_role(base, "organization_admin1", v_rev, "current_production", "r-2")
        check(
            "R",
            "re-assignment after a revoke is a new row (201)",
            again.status == 201 and again.json()["id"] != first["id"],
        )
        listing = _request(base, "GET", roles_path(org1_id, p1_id, v_rev), principal="viewer1")
        check(
            "R",
            "GET roles lists the revoked and the active row",
            listing.status == 200
            and len(listing.json()) == 2
            and sorted(bool(x["revoked_at"]) for x in listing.json()) == [False, True],
        )

        def raw_update(
            sql: str, org: uuid.UUID = org1_id, **params: Any
        ) -> tuple[bool, str | None, str | None]:
            with Session(app_engine) as s:
                _set_tenant(s, org)
                try:
                    s.execute(text(sql), params)
                    s.commit()
                    return True, None, None
                except DBAPIError as exc:
                    s.rollback()
                    return False, _constraint_name(exc), _sqlstate(exc)

        ok, name, state = raw_update(
            "UPDATE model_role_assignments SET revoked_at = NULL, revoked_by = NULL, revoke_reason_code = NULL WHERE id = :id",
            id=first["id"],
        )
        check(
            "R",
            f"a raw un-revoke is refused BY NAME by the trigger (got {name!r})",
            not ok and name == "tr_model_role_assignments_update_guard_final",
        )
        ok, name, state = raw_update(
            "UPDATE model_role_assignments SET revoked_by = 'someone-else' WHERE id = :id",
            id=first["id"],
        )
        check(
            "R",
            f"a raw rewrite of a revocation is refused (final) (got {name!r})",
            not ok and name == "tr_model_role_assignments_update_guard_final",
        )
        ok, name, state = raw_update(
            "UPDATE model_role_assignments SET role = 'baseline' WHERE id = :id",
            id=again.json()["id"],
        )
        check(
            "R",
            f"a raw role change is refused by the grant (sqlstate {state})",
            not ok and state == "42501",
        )
        ok, name, state = raw_update(
            "UPDATE model_role_assignments SET model_version_id = :v WHERE id = :id",
            id=again.json()["id"],
            v=str(v_idem),
        )
        check(
            "R",
            f"a raw model_version_id change is refused by the grant (sqlstate {state})",
            not ok and state == "42501",
        )
        ok, name, state = raw_update(
            "UPDATE model_role_assignments SET revoked_at = NULL WHERE id = :id",
            id=again.json()["id"],
        )
        check(
            "R",
            f"a no-op un-revoke on an active row is refused (got {name!r})",
            not ok and name == "tr_model_role_assignments_update_guard_transition",
        )
        ok, name, state = raw_update(
            "DELETE FROM model_role_assignments WHERE id = :id", id=again.json()["id"]
        )
        check("R", f"a raw DELETE is refused (sqlstate {state})", not ok and state == "42501")
        with Session(owner_engine) as s:
            try:
                s.execute(
                    text(
                        "UPDATE model_role_assignments SET role = 'baseline', revoked_at = now(), revoked_by = 'x', revoke_reason_code = 'x' WHERE id = :id"
                    ),
                    {"id": again.json()["id"]},
                )
                s.commit()
                check(
                    "R",
                    "the owner rewriting role while revoking is refused by the frozen clause",
                    False,
                )
            except DBAPIError as exc:
                s.rollback()
                check(
                    "R",
                    f"the trigger applies to every role: role rewrite refused BY NAME (got {_constraint_name(exc)!r})",
                    _constraint_name(exc) == "tr_model_role_assignments_update_guard_frozen",
                )

        # ------------------------------------------------------------ C: CHECK truth tables
        v_chk = make_version(
            p1_id, kind="weights", validated=True, artifact_id=weights_art_id, digest_char="c"
        )
        for label, extra, key, constraint in (
            ("role enum (case)", {"role": "Judge"}, "c-1", "ck_model_role_assignments_role"),
            ("role enum (unknown)", {"role": "owner"}, "c-2", "ck_model_role_assignments_role"),
            (
                "blank assigned_by",
                {"assigned_by": "   "},
                "c-3",
                "ck_model_role_assignments_text_not_blank",
            ),
            (
                "blank request_id",
                {"request_id": ""},
                "c-3b",
                "ck_model_role_assignments_text_not_blank",
            ),
            (
                "idempotency key with a newline",
                {"idempotency_key": "k\n"},
                "c-4",
                "ck_model_role_assignments_idempotency_key",
            ),
            ("idempotency key 129 chars", {"idempotency_key": "k" * 129}, "c-5", None),
            (
                "empty idempotency key",
                {"idempotency_key": ""},
                "c-6",
                "ck_model_role_assignments_idempotency_key",
            ),
            (
                "sha256 upper case",
                {"request_sha256": "A" * 64},
                "c-7",
                "ck_model_role_assignments_request_sha256",
            ),
            (
                "sha256 short",
                {"request_sha256": "a" * 63},
                "c-8",
                "ck_model_role_assignments_request_sha256",
            ),
            (
                "revoked_at without revoked_by",
                {"revoked_at": datetime.now(UTC)},
                "c-9",
                "ck_model_role_assignments_revocation_columns",
            ),
        ):
            params = dict(extra)
            role = params.pop("role", "teacher")
            idem = params.pop("idempotency_key", key)
            ok, name, state = raw_insert(org1_id, v_chk, role, idem, triggers_off=True, **params)
            expect_state = "22001" if constraint is None else "23514"
            check(
                "C",
                f"{label} refused BY NAME {constraint} (got {name!r}/{state})",
                not ok and (constraint is None and state == expect_state or name == constraint),
            )
        ok, name, state = raw_insert(
            org1_id,
            v_chk,
            "teacher",
            "c-10",
            revoked_at=datetime.now(UTC),
            revoked_by="x",
            revoke_reason_code="r",
        )
        check(
            "C",
            f"a row inserted already revoked is refused BY NAME (got {name!r})",
            not ok and name == "tr_model_role_assignments_insert_guard_active",
        )
        v_unval = make_version(p1_id, kind="ftf1001", validated=False)
        ok, name, state = raw_insert(org1_id, v_unval, "baseline", "c-11")
        check(
            "C",
            f"a raw insert on an unvalidated version is refused BY NAME (got {name!r})",
            not ok and name == "tr_model_role_assignments_insert_guard_validated",
        )
        ok, name, state = raw_insert(org1_id, v_chk, "baseline", "c-12", project=p1b_id)
        check(
            "C",
            f"a version/project mismatch is refused by the validation trigger (got {name!r})",
            not ok and name == "tr_model_role_assignments_insert_guard_validated",
        )
        ok, name, state = raw_insert(
            org1_id, v_chk, "baseline", "c-12b", project=p1b_id, triggers_off=True
        )
        check(
            "C",
            f"and, triggers out of the way, by the composite FK (got {name!r})",
            not ok and name == "fk_model_role_assignments_model_version",
        )
        v_fac = make_version(
            p1_id, kind="factory", validated=True, artifact_id=weights_art_id, digest_char="c"
        )
        ok, _, _ = raw_insert(org1_id, v_fac, "baseline", "c-13")
        check("C", "a factory trained version needs no import job", ok)

        # ------------------------------------------------------------ X: cross tenant + META-LEAK
        v_x = make_version(
            p1_id, kind="weights", validated=True, artifact_id=weights_art_id, digest_char="c"
        )
        post_role(base, "organization_admin1", v_x, "baseline", "x-1")
        missing = uuid.uuid4()
        shapes = []
        for label, org, project, version in (
            ("foreign version under the caller's own org", org2_id, p2_id, v_x),
            ("foreign project in the URL", org1_id, p2_id, v_x),
            ("wrong project of the same org", org1_id, p1b_id, v_x),
            ("missing id", org2_id, p2_id, missing),
        ):
            who = "admin2" if org == org2_id else "organization_admin1"
            r = _request(
                base,
                "POST",
                roles_path(org, project, version),
                principal=who,
                payload={"role": "baseline", "idempotency_key": "x-k"},
            )
            shapes.append((label, r.status, r.body))
            r2 = _request(base, "GET", roles_path(org, project, version), principal=who)
            r3 = _request(base, "GET", actions_path(org, project, version), principal=who)
            r4 = _request(
                base, "POST", revoke_path(org, project, version, "baseline"), principal=who
            )
            check(
                "X",
                f"{label}: POST/GET/actions/revoke are all 404 not_found",
                (r.status, r2.status, r3.status, r4.status) == (404, 404, 404, 404)
                and r.detail == "not_found",
            )
        check(
            "X",
            "the 404 body is identical for foreign and missing",
            len({s[2] for s in shapes[:1] + shapes[3:]}) == 1,
        )
        r = _request(
            base,
            "POST",
            roles_path(org1_id, p1_id, v_x),
            principal="admin2",
            payload={"role": "baseline", "idempotency_key": "x-k"},
        )
        check("X", "an org2 principal on an org1 path is 403", r.status == 403)
        check(
            "X",
            "no org2 row was written",
            owner_count(
                "SELECT count(*) FROM model_role_assignments WHERE organization_id = :o",
                o=str(org2_id),
            )
            == 0,
        )
        ok, name, state = raw_insert(
            org2_id, v_x, "teacher", "x-raw", tenant=org1_id, triggers_off=True
        )
        check(
            "X",
            f"a raw org2 INSERT under the org1 context is refused by RLS (sqlstate {state})",
            not ok and state == "42501",
        )
        ok, name, state = raw_insert(
            org1_id, v_x, "teacher", "x-raw2", tenant=org2_id, triggers_off=True
        )
        check(
            "X",
            f"a raw org1 INSERT under the org2 context is refused by RLS (sqlstate {state})",
            not ok and state == "42501",
        )
        with Session(app_engine) as s:
            _set_tenant(s, org2_id)
            seen = s.execute(text("SELECT count(*) FROM model_role_assignments")).scalar()
        check("X", "the org2 context sees no org1 row", seen == 0)
        with Session(app_engine) as s:
            seen = s.execute(text("SELECT count(*) FROM model_role_assignments")).scalar()
        check("X", "no tenant context sees nothing", seen == 0)
        audit_before = audits()
        leak_roles = (f"judge{MARKER}", MARKER, f"{MARKER}\x00")
        leak_responses = [
            post_role(base, "organization_admin1", v_x, role, f"x-{MARKER}") for role in leak_roles
        ]
        leak_responses.append(post_role(base, "organization_admin1", v_x, "baseline", MARKER + "é"))
        leak_responses.append(
            _request(
                base,
                "POST",
                roles_path(org1_id, p1_id, v_x),
                principal="organization_admin1",
                payload={"role": "baseline", "idempotency_key": "k", MARKER: 1},
            )
        )
        reuse = post_role(base, "organization_admin1", v_x, "teacher", "x-1")
        check(
            "X",
            "META-LEAK: a rejected body never echoes the marker",
            all(MARKER.encode() not in r.body for r in leak_responses)
            and all(r.status == 400 for r in leak_responses),
        )
        check(
            "X",
            "META-LEAK: an idempotency_key_reused body carries the code only",
            reuse.status == 409 and reuse.json() == {"detail": "idempotency_key_reused"},
        )
        check("X", "META-LEAK: rejected bodies write no audit event", audits() == audit_before)
        with Session(owner_engine) as s:
            dumped = s.execute(
                text(
                    "SELECT coalesce(string_agg(row_to_json(a)::text, ' '), '') FROM audit_events a WHERE action LIKE 'model_role.%'"
                )
            ).scalar()
        check(
            "X",
            "META-LEAK: the marker is in no audit row and no log line",
            MARKER not in dumped and not any(MARKER in line for line in logs.lines),
        )
        check(
            "X",
            "audit rows carry the actor and never the idempotency key",
            "x-1" not in dumped.replace("model_role", ""),
        )

        # ------------------------------------------------------------ A: actions route
        def actions(
            org: uuid.UUID, project: uuid.UUID, version: Any, who: str = "viewer1"
        ) -> dict[str, Any]:
            r = _request(base, "GET", actions_path(org, project, version), principal=who)
            check("A", f"GET actions {version}: 200 for {who} (got {r.status})", r.status == 200)
            return r.json() if r.status == 200 else {"actions": {}}

        def codes(doc: dict[str, Any]) -> dict[str, str | None]:
            return {k: v["code"] for k, v in doc["actions"].items()}

        ALL_NOT_VALIDATED = dict.fromkeys(
            (
                "evaluate",
                "compare",
                "continue_tuning",
                "exact_resume",
                "request_qualification",
                "retain_incumbent",
                "deploy_route",
                "retire",
            ),
            "import_not_validated",
        )
        doc = actions(org1_id, p1_id, v_unval)
        check(
            "A",
            "an unvalidated FTF-1001 row: every action import_not_validated",
            codes(doc) == ALL_NOT_VALIDATED and doc["validated"] is False,
        )
        ep = make_version(p1_id, kind="endpoint", validated=True)
        doc = actions(org1_id, p1_id, ep)
        check(
            "A",
            f"validated endpoint: evaluate/compare allowed, continue_tuning endpoint_not_trainable, exact_resume no_trusted_checkpoint ({codes(doc)})",
            codes(doc)
            == {
                "evaluate": None,
                "compare": None,
                "continue_tuning": "endpoint_not_trainable",
                "exact_resume": "no_trusted_checkpoint",
                "request_qualification": "prerequisite_not_available",
                "retain_incumbent": "not_current_production",
                "deploy_route": "prerequisite_not_available",
                "retire": "prerequisite_not_available",
            },
        )
        for ref in (
            "ft:gpt-4o:org::abc",
            "my-lora-adapter",
            "meta/llama-3-8b",
            "trainable",
            "teacher",
            "provider/ft:lora/trainable/teacher/llama",
        ):
            e2 = make_version(p1_id, kind="endpoint", validated=True, ref=ref)
            post_role(base, "organization_admin1", e2, "teacher", f"ref-{uuid.uuid4().hex[:8]}")
            d2 = actions(org1_id, p1_id, e2)
            check(
                "A",
                f"endpoint ref {ref!r} with a teacher role stays untrainable",
                d2["actions"]["continue_tuning"]["code"] == "endpoint_not_trainable"
                and d2["actions"]["exact_resume"]["code"] == "no_trusted_checkpoint"
                and d2["roles"] == ["teacher"],
            )
        w = make_version(
            p1_id, kind="weights", validated=True, artifact_id=weights_art_id, digest_char="c"
        )
        doc = actions(org1_id, p1_id, w)
        plan = doc["actions"]["continue_tuning"]["plan"] or {}
        check(
            "A",
            f"validated weights: continue_tuning is a new_run at step 0 ({plan})",
            doc["actions"]["continue_tuning"]["allowed"]
            and plan.get("kind") == "new_run"
            and plan.get("start_step") == 0
            and plan.get("optimizer_state") == "none"
            and plan.get("parent_model_version_id") == str(w),
        )
        check(
            "A",
            "validated weights: exact_resume no_trusted_checkpoint",
            doc["actions"]["exact_resume"]["code"] == "no_trusted_checkpoint",
        )
        doc2 = actions(org1_id, p1_id, w)
        check(
            "A",
            "each plan proposes a fresh run id",
            plan.get("run_id") != (doc2["actions"]["continue_tuning"]["plan"] or {}).get("run_id"),
        )
        fac = make_version(
            p1_id, kind="factory", validated=True, artifact_id=weights_art_id, digest_char="c"
        )
        doc = actions(org1_id, p1_id, fac)
        check(
            "A",
            "factory trained: exact_resume is denied (no FTF-0713 checkpoint manifest exists) and continue_tuning is a new_run",
            doc["actions"]["exact_resume"]["code"] == "no_trusted_checkpoint"
            and doc["actions"]["continue_tuning"]["plan"]["kind"] == "new_run",
        )
        ad = make_version(
            p1_id,
            kind="adapter",
            validated=True,
            artifact_id=adapter_art_id,
            digest_char="d",
            parent=base_weights,
        )
        doc = actions(org1_id, p1_id, ad)
        check(
            "A",
            f"validated adapter on its exact base: evaluate allowed, continue_tuning a new_run ({codes(doc)})",
            doc["actions"]["evaluate"]["allowed"]
            and doc["actions"]["continue_tuning"]["plan"]["kind"] == "new_run"
            and doc["actions"]["exact_resume"]["code"] == "no_trusted_checkpoint",
        )
        # incumbent
        with Session(owner_engine) as s:
            s.execute(
                text(
                    "UPDATE model_role_assignments SET revoked_at = now(), revoked_by = 'x', "
                    "revoke_reason_code = 'x' WHERE project_id = :p AND role = 'current_production' "
                    "AND revoked_at IS NULL"
                ),
                {"p": str(p1_id)},
            )
            s.commit()
        inc = post_role(
            base, "organization_admin1", w, "current_production", f"inc-{uuid.uuid4().hex[:8]}"
        )
        check("A", f"the incumbent assignment succeeds ({inc.status})", inc.status == 201)
        doc = actions(org1_id, p1_id, w)
        check(
            "A",
            "the incumbent can retain_incumbent; deploy_route still needs its prerequisite",
            doc["actions"]["retain_incumbent"]["allowed"]
            and doc["actions"]["deploy_route"]["code"] == "prerequisite_not_available"
            and doc["roles"] == ["current_production"],
        )
        for who in ORG_ROLES:
            r = _request(base, "GET", actions_path(org1_id, p1_id, w), principal=f"{who}1")
            check(
                "A",
                f"every organization role may read the actions ({who}: {r.status})",
                r.status == 200,
            )
        check(
            "A",
            "an unauthenticated GET actions is 401",
            _request(base, "GET", actions_path(org1_id, p1_id, w), principal=None).status == 401,
        )
        # revoked license: owner clears the accepted fields and demotes the artifact (what revoke_license does)
        rev_art = make_version(
            p1_id, kind="weights", validated=True, artifact_id=weights_art_id, digest_char="c"
        )
        with Session(owner_engine) as s:
            s.execute(
                text(
                    "UPDATE trainable_artifacts SET license_accepted_by = NULL, license_accepted_at = NULL, status = 'registered' WHERE id = :id"
                ),
                {"id": str(weights_art_id)},
            )
            s.commit()
        doc = actions(org1_id, p1_id, rev_art)
        check(
            "A",
            f"a revoked license: every use action license_not_accepted ({codes(doc)})",
            all(
                doc["actions"][a]["code"] == "license_not_accepted"
                for a in (
                    "evaluate",
                    "compare",
                    "continue_tuning",
                    "exact_resume",
                    "request_qualification",
                    "retain_incumbent",
                    "deploy_route",
                )
            ),
        )
        with Session(owner_engine) as s:
            s.execute(
                text(
                    "UPDATE trainable_artifacts SET license_accepted_by = 'seed', license_accepted_at = now(), status = 'ready' WHERE id = :id"
                ),
                {"id": str(weights_art_id)},
            )
            s.commit()
        check(
            "A",
            "restoring the license restores the actions",
            actions(org1_id, p1_id, rev_art)["actions"]["continue_tuning"]["allowed"],
        )
        # adapter base failures
        for label, sql in (
            (
                "the base's license is revoked",
                "UPDATE trainable_artifacts SET license_accepted_by = NULL, license_accepted_at = NULL, status = 'registered' WHERE id = :id",
            ),
            (
                "the base artifact no longer matches the adapter's family",
                "UPDATE trainable_artifacts SET model_family = 'mistral' WHERE id = :id",
            ),
            (
                "the base's pinned revision drifted",
                "UPDATE trainable_artifacts SET base_model_revision = 'ffffffffffff' WHERE id = :id",
            ),
        ):
            with Session(owner_engine) as s:
                saved = s.execute(
                    text(
                        "SELECT license_accepted_by, license_accepted_at, status, model_family, base_model_revision FROM trainable_artifacts WHERE id = :id"
                    ),
                    {"id": str(base_art_id)},
                ).one()
                s.execute(text(sql), {"id": str(base_art_id)})
                s.commit()
            ad2 = make_version(
                p1_id,
                kind="adapter",
                validated=True,
                artifact_id=adapter_art_id,
                digest_char="d",
                parent=base_weights,
            )
            doc = actions(org1_id, p1_id, ad2)
            check(
                "A",
                f"adapter: {label} -> every use action adapter_base_incompatible or license ({codes(doc)})",
                all(
                    doc["actions"][a]["code"] in ("adapter_base_incompatible",)
                    for a in ("evaluate", "compare", "continue_tuning", "exact_resume")
                ),
            )
            with Session(owner_engine) as s:
                s.execute(
                    text(
                        "UPDATE trainable_artifacts SET license_accepted_by = :a, license_accepted_at = :b, status = :c, model_family = :d, base_model_revision = :e WHERE id = :id"
                    ),
                    {
                        "a": saved[0],
                        "b": saved[1],
                        "c": saved[2],
                        "d": saved[3],
                        "e": saved[4],
                        "id": str(base_art_id),
                    },
                )
                s.commit()
        orphan_parent = make_version(
            p1_id, kind="weights", validated=False, artifact_id=base_art_id, digest_char="b"
        )
        ad3 = make_version(
            p1_id,
            kind="adapter",
            validated=True,
            artifact_id=adapter_art_id,
            digest_char="d",
            parent=orphan_parent,
        )
        check(
            "A",
            "adapter whose parent base was never validated -> adapter_base_incompatible",
            actions(org1_id, p1_id, ad3)["actions"]["evaluate"]["code"]
            == "adapter_base_incompatible",
        )
        ad4 = make_version(
            p1_id,
            kind="adapter",
            validated=True,
            artifact_id=adapter_art_id,
            digest_char="d",
            parent=None,
        )
        check(
            "A",
            "adapter with no parent base -> adapter_base_incompatible",
            actions(org1_id, p1_id, ad4)["actions"]["evaluate"]["code"]
            == "adapter_base_incompatible",
        )
        check("A", "the actions read writes nothing", rows_for(ep) == 0)

        # ------------------------------------------------------------ F: DATABASE faults
        def probe_in_tx(setup: str | None, probe: Any) -> Any:
            """Apply `setup` (DDL as the owner) inside a transaction, run `probe(session)`, roll back."""
            with Session(owner_engine) as s:
                try:
                    if setup:
                        s.execute(text(setup))
                    return probe(s)
                finally:
                    s.rollback()

        def as_app(s: Session, org: uuid.UUID) -> None:
            s.execute(text(f'SET LOCAL ROLE "{APP_ROLE_NAME}"'))
            _set_tenant(s, org)

        def probe_insert(
            version: uuid.UUID, role: str, key: str, *, project: uuid.UUID = p1_id
        ) -> Any:
            def run(s: Session) -> bool:
                as_app(s, org1_id)
                try:
                    with s.begin_nested():
                        s.execute(
                            text(
                                "INSERT INTO model_role_assignments (organization_id, project_id, model_version_id, role, assigned_by, request_id, idempotency_key, request_sha256) VALUES (:o, :p, :v, :r, 'f', 'f', :k, :h)"
                            ),
                            {
                                "o": str(org1_id),
                                "p": str(project),
                                "v": str(version),
                                "r": role,
                                "k": key,
                                "h": "a" * 64,
                            },
                        )
                    return True
                except DBAPIError:
                    return False

            return run

        def function_def(name: str) -> str:
            with Session(owner_engine) as s:
                return s.execute(
                    text("SELECT pg_get_functiondef(oid) FROM pg_proc WHERE proname = :n"),
                    {"n": name},
                ).scalar()

        insert_def = function_def("model_role_assignments_insert_guard")
        update_def = function_def("model_role_assignments_update_guard")
        va, vb = (
            make_version(
                p1b_id, kind="weights", validated=True, artifact_id=weights_art_id, digest_char="c"
            )
            for _ in range(2)
        )

        def two_incumbents(s: Session) -> bool:
            first_ok = probe_insert(va, "current_production", "f-i1", project=p1b_id)(s)
            second_ok = probe_insert(vb, "current_production", "f-i2", project=p1b_id)(s)
            return first_ok and not second_ok

        # a clean project p1b holds incumbents from the K trials, so isolate with a fresh project via owner
        with Session(owner_engine) as s:
            fresh = Project(
                organization_id=org1_id,
                workspace_id=s.execute(
                    text("SELECT id FROM workspaces WHERE organization_id = :o LIMIT 1"),
                    {"o": str(org1_id)},
                ).scalar(),
                name=f"fresh-{suffix}",
                project_type="generic_model_tuning",
                status="active",
                security_profile="standard",
                row_version=1,
                created_by="seed",
            )
            s.add(fresh)
            s.commit()
            fresh_id = fresh.id
        fa, fb = (
            make_version(
                fresh_id,
                kind="weights",
                validated=True,
                artifact_id=weights_art_id,
                digest_char="c",
            )
            for _ in range(2)
        )

        def incumbents(s: Session) -> bool:
            return probe_insert(fa, "current_production", "f-i1", project=fresh_id)(
                s
            ) and not probe_insert(fb, "current_production", "f-i2", project=fresh_id)(s)

        check(
            "F",
            "control: the incumbent probe is true on the real schema",
            probe_in_tx(None, incumbents) is True,
        )
        check(
            "F",
            "fault: the incumbent index dropped flips the probe",
            probe_in_tx("DROP INDEX uq_model_role_assignments_incumbent", incumbents) is False,
        )
        ga, gb = (
            make_version(
                p1_id, kind="weights", validated=True, artifact_id=weights_art_id, digest_char="c"
            )
            for _ in range(2)
        )

        def duplicate_active(s: Session) -> bool:
            return probe_insert(ga, "teacher", "f-d1")(s) and not probe_insert(
                ga, "teacher", "f-d2"
            )(s)

        check(
            "F",
            "control: the active-role index probe is true",
            probe_in_tx(None, duplicate_active) is True,
        )
        check(
            "F",
            "fault: the active-role index dropped flips the probe",
            probe_in_tx("DROP INDEX uq_model_role_assignments_active", duplicate_active) is False,
        )

        def same_key(s: Session) -> bool:
            return probe_insert(ga, "baseline", "f-k")(s) and not probe_insert(
                gb, "baseline", "f-k"
            )(s)

        check(
            "F", "control: the idempotency index probe is true", probe_in_tx(None, same_key) is True
        )
        check(
            "F",
            "fault: the idempotency index dropped flips the probe",
            probe_in_tx("DROP INDEX uq_model_role_assignments_idempotency", same_key) is False,
        )

        def judge_after_candidate(s: Session) -> bool:
            return probe_insert(gb, "candidate", "f-j1")(s) and not probe_insert(
                gb, "judge", "f-j2"
            )(s)

        def candidate_after_judge(s: Session) -> bool:
            hb = make_version(
                p1_id, kind="weights", validated=True, artifact_id=weights_art_id, digest_char="c"
            )
            return probe_insert(hb, "judge", "f-j3")(s) and not probe_insert(
                hb, "candidate", "f-j4"
            )(s)

        check(
            "F",
            "control: judge-after-candidate probe is true",
            probe_in_tx(None, judge_after_candidate) is True,
        )
        check(
            "F",
            "control: candidate-after-judge probe is true",
            probe_in_tx(None, candidate_after_judge) is True,
        )
        no_dir1 = insert_def.replace("r.role = 'candidate'", "r.role = 'zzz_none'", 1)
        no_dir2 = insert_def.replace("r.role = 'judge'", "r.role = 'zzz_none'", 1)
        check(
            "F",
            "fault: the judge-after-candidate direction removed flips only that probe",
            probe_in_tx(no_dir1, judge_after_candidate) is False
            and probe_in_tx(no_dir1, candidate_after_judge) is True,
        )
        check(
            "F",
            "fault: the candidate-after-judge direction removed flips only that probe",
            probe_in_tx(no_dir2, candidate_after_judge) is False
            and probe_in_tx(no_dir2, judge_after_candidate) is True,
        )
        check(
            "F",
            "fault: the insert trigger dropped flips both probes",
            probe_in_tx(
                "DROP TRIGGER tr_model_role_assignments_insert_guard ON model_role_assignments",
                judge_after_candidate,
            )
            is False,
        )

        def refuses_unvalidated(s: Session) -> bool:
            return not probe_insert(v_unval, "baseline", "f-u1")(s)

        check(
            "F",
            "control: the unvalidated-version probe is true",
            probe_in_tx(None, refuses_unvalidated) is True,
        )
        check(
            "F",
            "fault: the validation clause removed flips the probe",
            probe_in_tx(
                insert_def.replace(
                    "IF version_origin IS DISTINCT FROM 'factory_trained' AND NOT EXISTS",
                    "IF false AND NOT EXISTS",
                    1,
                ),
                refuses_unvalidated,
            )
            is False,
        )

        def refuses_unrevoke(s: Session) -> bool:
            target = s.execute(
                text("SELECT id FROM model_role_assignments WHERE revoked_at IS NOT NULL LIMIT 1")
            ).scalar()
            as_app(s, org1_id)
            try:
                with s.begin_nested():
                    s.execute(
                        text(
                            "UPDATE model_role_assignments SET revoked_at = NULL, revoked_by = NULL, revoke_reason_code = NULL WHERE id = :id"
                        ),
                        {"id": str(target)},
                    )
                return False
            except DBAPIError:
                return True

        check(
            "F", "control: the un-revoke probe is true", probe_in_tx(None, refuses_unrevoke) is True
        )
        permissive = update_def.replace(
            "IF OLD.revoked_at IS NOT NULL THEN", "IF false THEN", 1
        ).replace("IF NEW.revoked_at IS NULL THEN", "IF false THEN", 1)
        check(
            "F",
            "fault: un-revoke allowed flips the probe",
            probe_in_tx(permissive, refuses_unrevoke) is False,
        )

        def priv(column: str) -> Any:
            def run(s: Session) -> bool:
                return bool(
                    s.execute(
                        text(
                            "SELECT has_column_privilege(:r, 'model_role_assignments', :c, 'UPDATE')"
                        ),
                        {"r": APP_ROLE_NAME, "c": column},
                    ).scalar()
                )

            return run

        for column in (
            "role",
            "model_version_id",
            "project_id",
            "organization_id",
            "assigned_by",
            "assigned_at",
            "request_id",
            "idempotency_key",
            "request_sha256",
            "id",
        ):
            check(
                "F",
                f"control: sovra_app has no UPDATE on {column}",
                probe_in_tx(None, priv(column)) is False,
            )
            check(
                "F",
                f"fault: a widened UPDATE grant on {column} flips the probe",
                probe_in_tx(
                    f'GRANT UPDATE ({column}) ON model_role_assignments TO "{APP_ROLE_NAME}"',
                    priv(column),
                )
                is True,
            )
        check(
            "F",
            "fault: a table-wide UPDATE grant flips the probe",
            probe_in_tx(
                f'GRANT UPDATE ON model_role_assignments TO "{APP_ROLE_NAME}"', priv("role")
            )
            is True,
        )
        check(
            "F",
            "fault: a DELETE grant flips the probe",
            probe_in_tx(
                f'GRANT DELETE ON model_role_assignments TO "{APP_ROLE_NAME}"',
                lambda s: bool(
                    s.execute(
                        text("SELECT has_table_privilege(:r, 'model_role_assignments', 'DELETE')"),
                        {"r": APP_ROLE_NAME},
                    ).scalar()
                ),
            )
            is True,
        )

        check(
            "F",
            "fault: FORCE dropped flips the pg_class probe",
            probe_in_tx(
                "ALTER TABLE model_role_assignments NO FORCE ROW LEVEL SECURITY",
                lambda s: s.execute(
                    text(
                        "SELECT relforcerowsecurity FROM pg_class WHERE relname = 'model_role_assignments'"
                    )
                ).scalar(),
            )
            is False,
        )
        check(
            "F",
            "fault: RLS disabled flips the pg_class probe",
            probe_in_tx(
                "ALTER TABLE model_role_assignments DISABLE ROW LEVEL SECURITY",
                lambda s: s.execute(
                    text(
                        "SELECT relrowsecurity FROM pg_class WHERE relname = 'model_role_assignments'"
                    )
                ).scalar(),
            )
            is False,
        )

        def cross_tenant_read(s: Session) -> int:
            as_app(s, org2_id)
            return int(s.execute(text("SELECT count(*) FROM model_role_assignments")).scalar())

        check(
            "F",
            "control: the org2 context reads no org1 row",
            probe_in_tx(None, cross_tenant_read) == 0,
        )
        check(
            "F",
            "fault: the tenant policy dropped (default deny) still reads none",
            probe_in_tx("DROP POLICY tenant_isolation ON model_role_assignments", cross_tenant_read)
            == 0,
        )
        check(
            "F",
            "fault: an allow-all policy flips the probe",
            probe_in_tx(
                "DROP POLICY tenant_isolation ON model_role_assignments; CREATE POLICY allow_all ON model_role_assignments USING (true)",
                cross_tenant_read,
            )
            > 0,
        )

    for failure in FAILURES:
        print(failure)
    if FAILURES:
        print(f"{len(FAILURES)} failure(s) of {CHECKS[0]} checks")
        sys.exit(1)
    print(f"OK {CID}: {CHECKS[0]} checks")


if __name__ == "__main__":
    try:
        main()
    except BaseException:
        for failure in FAILURES[:40]:
            print(failure)
        raise
