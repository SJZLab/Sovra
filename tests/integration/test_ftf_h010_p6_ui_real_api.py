#!/usr/bin/env python3
# mypy: disable-error-code="arg-type,attr-defined,return-value,assignment,call-arg,index,misc,no-untyped-def,var-annotated,union-attr,operator,no-any-return"
# ruff: noqa: E501
"""FTF-H010-P6 UI -- the real model version panel against the REAL FTF-1001 item route and the
REAL FTF-H010-P4 roles / revoke / actions routes.

`apps/web/tests/ftf_h010_p6.spec.ts` drives the panel against a mocked `fetch`. This suite boots
the real model-version and model-role routers on a real uvicorn server over PostgreSQL 16 as the
restricted `sovra_app` role, seeds real organizations / projects / artifacts / model versions /
succeeded import jobs (the same owner-role seeding FTF-H010-P4's own integration suite uses), then
runs `apps/web/tests/real_api/ftf_h010_p6_ui_real_api.realapi.ts` (Vitest + jsdom, the real
component and real `fetch`) against it.

Test doubles, disclosed: the principal header stands in for the OIDC session (as in every sibling
UI real-API job); `/__harness/revoke-license` is the owner-role stand-in for FTF-0305's revocation
(the same UPDATE FTF-H010-P4's suite uses) and is never product behaviour.

R1  endpoint version whose ref contains ft:/lora/llama/trainable/teacher, assigned `teacher` in the
    UI -> continue_tuning "Not available endpoint_not_trainable"; lineage Unknown, "No training
    artifact", the observed fingerprint
R2  imported adapter on its exact validated base -> continue_tuning allowed as a new run;
    exact_resume "Not available no_trusted_checkpoint"
R3  license revoked -> every use action not available (license_not_accepted)
R4  two admin panels assign current_production concurrently -> one succeeds, the other shows
    incumbent_exists
R5  judge on a candidate -> self_judging_role_conflict
R6  ml_engineer assigning judge -> the server's 403 shown
R7  an unvalidated FTF-1001 synchronous row -> import_not_validated on every action and on assign
R8  revoke whose response is lost, then Try again -> 409 role_not_assigned shown as the revoked state
R9  a foreign version -> not_found in every section
R10 an assign whose response is lost, then Try again -> 200 replay with the same key, one row

Afterwards this file checks the database independently.

Requires SOVRA_DATABASE_URL (migration owner), SOVRA_APP_DB_PASSWORD and Node with `pnpm install`.
Run: python3 tests/integration/test_ftf_h010_p6_ui_real_api.py
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import socket
import subprocess
import sys
import threading
import time
import uuid
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
from app.model_versions.model_version_routes import build_model_version_router  # noqa: E402
from app.projects.project_schema import Project  # noqa: E402

CID = "FTF-H010-P6-UI-F"
FAILURES: list[str] = []
_ISSUER = "https://issuer.example"
R1_REF = "ft:acme:llama-3-trainable-teacher-lora"


def check(description: str, condition: bool) -> None:
    if not condition:
        FAILURES.append(f"FAIL [{CID}] {description}")


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


@contextlib.contextmanager
def _running_app(app: FastAPI):
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(300):
        if server.started:
            break
        time.sleep(0.02)
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(timeout=5)


def main() -> None:
    migration_url = _required_env("SOVRA_DATABASE_URL")
    app_password = _required_env("SOVRA_APP_DB_PASSWORD")
    result = _alembic(["upgrade", "head"], migration_url)
    check(f"alembic upgrade head ({result.stdout}{result.stderr})", result.returncode == 0)
    if result.returncode != 0:
        return _finish()

    owner_engine = create_engine(migration_url)
    app_engine = create_engine(_app_role_url(migration_url, app_password))
    check(
        "the app engine is the restricted sovra_app role", app_engine.url.username == APP_ROLE_NAME
    )
    factory = sessionmaker(bind=app_engine, expire_on_commit=False)
    suffix = uuid.uuid4().hex[:8]

    with Session(owner_engine) as session:
        org1 = Organization(name="Acme H010P6", slug=f"acme-h010p6-{suffix}")
        org2 = Organization(name="Globex H010P6", slug=f"globex-h010p6-{suffix}")
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

        # One project per scenario that touches project-level indexes (incumbent, self-judging).
        names = ("p1", "p_r4", "p_r5")
        projects = {n: project(org1, ws1, n) for n in names}
        p_foreign = project(org2, ws2, "p2")
        session.add_all([*projects.values(), p_foreign])
        session.flush()

        def artifact(org: Organization, name: str, digest: str) -> TrainableArtifact:
            return TrainableArtifact(
                organization_id=org.id,
                display_name=f"{name}-{suffix}",
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

        arts = {
            "base": artifact(org1, "base", "b"),
            "adapter": artifact(org1, "adapter", "d"),
            "r3": artifact(org1, "r3", "c"),
            "foreign": artifact(org2, "foreign", "e"),
        }
        session.add_all(arts.values())
        conn1 = LiteLLMConnection(
            organization_id=org1.id,
            name=f"h010p6-gw-{suffix}",
            base_url="https://gateway.example",
            secret_ref="openbao://secret/litellm/org1/api_key",
        )
        conn2 = LiteLLMConnection(
            organization_id=org2.id,
            name=f"h010p6-gw2-{suffix}",
            base_url="https://gateway.example",
            secret_ref="openbao://secret/litellm/org2/api_key",
        )
        session.add_all([conn1, conn2])
        session.commit()
        org1_id, org2_id = org1.id, org2.id
        pid = {n: p.id for n, p in projects.items()}
        p_foreign_id = p_foreign.id
        art_id = {k: a.id for k, a in arts.items()}
        conn_by_org = {org1_id: conn1.id, org2_id: conn2.id}

    counter = {"n": 0}

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
        fingerprint: str | None = None,
        completeness: str = "unknown",
    ) -> uuid.UUID:
        """A model version seeded by the owner role, as FTF-H010-P4's suite does. kind: endpoint |
        weights | adapter | ftf1001. A validated one gets a succeeded import job naming it."""
        counter["n"] += 1
        vid = uuid.uuid4()
        params: dict[str, Any] = {
            "id": str(vid),
            "org": str(org_id),
            "project": str(project_id),
            "n": counter["n"],
            "ref": ref,
            "fp": fingerprint,
            "lc": completeness,
            "parent": None if parent is None else str(parent),
            "artifact": None if artifact_id is None else str(artifact_id),
            "digest": None if digest_char is None else "sha256:" + digest_char * 64,
        }
        cols = "id, organization_id, project_id, version_number, created_by, lineage_completeness, "
        if kind == "endpoint":
            sql = (
                f"INSERT INTO model_versions ({cols}import_kind, origin, external_provider_model_ref, "
                "observed_deployment_fingerprint) VALUES (:id, :org, :project, :n, 'seed', :lc, "
                "'endpoint', 'external_endpoint', :ref, :fp)"
            )
        elif kind == "ftf1001":
            sql = (
                f"INSERT INTO model_versions ({cols}import_kind, origin, endpoint_reference, "
                "external_provider_model_ref) VALUES (:id, :org, :project, :n, 'seed', :lc, "
                "'endpoint', 'external_endpoint', 'https://x.example/v1', :ref)"
            )
        elif kind == "weights":
            sql = (
                f"INSERT INTO model_versions ({cols}import_kind, origin, trainable_artifact_id, "
                "weights_digest, base_model_revision) VALUES (:id, :org, :project, :n, 'seed', :lc, "
                "'full_weight', 'imported_weights', :artifact, :digest, 'a1b2c3d4e5f6')"
            )
        else:
            sql = (
                f"INSERT INTO model_versions ({cols}import_kind, origin, adapter_only, "
                "parent_model_version_id, trainable_artifact_id, weights_digest, base_model_revision) "
                "VALUES (:id, :org, :project, :n, 'seed', :lc, 'adapter', 'imported_adapter', true, "
                ":parent, :artifact, :digest, 'a1b2c3d4e5f6')"
            )
        with Session(owner_engine) as s:
            s.execute(text(sql), params)
            if validated:
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
                        "conn": str(conn_by_org[org_id]) if kind == "endpoint" else None,
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

    p1 = pid["p1"]
    versions = {
        "r1": make_version(
            p1, kind="endpoint", validated=True, ref=R1_REF, fingerprint="fp_r1_observed"
        ),
        "base": make_version(
            p1,
            kind="weights",
            validated=True,
            artifact_id=art_id["base"],
            digest_char="b",
            completeness="partial",
        ),
    }
    versions["r2"] = make_version(
        p1,
        kind="adapter",
        validated=True,
        artifact_id=art_id["adapter"],
        digest_char="d",
        parent=versions["base"],
        completeness="partial",
    )
    versions["r3"] = make_version(
        p1, kind="weights", validated=True, artifact_id=art_id["r3"], digest_char="c"
    )
    versions["r4a"] = make_version(pid["p_r4"], kind="endpoint", validated=True, ref="prov/r4a")
    versions["r4b"] = make_version(pid["p_r4"], kind="endpoint", validated=True, ref="prov/r4b")
    versions["r5"] = make_version(pid["p_r5"], kind="endpoint", validated=True, ref="prov/r5")
    versions["r6"] = make_version(p1, kind="endpoint", validated=True, ref="prov/r6")
    versions["r7"] = make_version(p1, kind="ftf1001", validated=False, ref="prov/r7")
    versions["r8"] = make_version(p1, kind="endpoint", validated=True, ref="prov/r8")
    versions["r10"] = make_version(p1, kind="endpoint", validated=True, ref="prov/r10")
    versions["foreign"] = make_version(
        p_foreign_id, kind="endpoint", validated=True, org_id=org2_id, ref="prov/foreign"
    )

    principals: dict[str, AuthenticatedPrincipal] = {}
    memberships: dict[tuple[str, str, uuid.UUID], Role] = {}
    for role in (Role.ORGANIZATION_ADMIN, Role.ML_ENGINEER, Role.VIEWER):
        subject = f"user-org1-{role.value}"
        principals[role.value] = AuthenticatedPrincipal(issuer=_ISSUER, subject=subject)
        memberships[(_ISSUER, subject, org1_id)] = role
    # A second admin of the same organization: the "second browser context" of R4.
    principals["organization_admin_2"] = AuthenticatedPrincipal(
        issuer=_ISSUER, subject="user-org1-organization_admin-2"
    )
    memberships[(_ISSUER, "user-org1-organization_admin-2", org1_id)] = Role.ORGANIZATION_ADMIN

    def principal_resolver(request: Any) -> AuthenticatedPrincipal | None:
        header = request.headers.get("x-test-principal")
        return None if header is None else principals.get(header)

    def role_lookup(issuer: str, subject: str, organization_id: uuid.UUID) -> Role | None:
        return memberships.get((issuer, subject, organization_id))

    app = FastAPI()
    kw = {"principal_resolver": principal_resolver, "role_lookup": role_lookup}
    app.include_router(build_model_version_router(factory, **kw))
    app.include_router(build_model_role_router(factory, **kw))

    @app.post("/__harness/revoke-license")
    def revoke_license(payload: dict[str, Any]) -> dict[str, Any]:
        # STAND-IN for FTF-0305's revocation: clear acceptance and demote the artifact, exactly the
        # owner-role UPDATE FTF-H010-P4's suite uses for what revoke_license does.
        with Session(owner_engine) as s:
            s.execute(
                text(
                    "UPDATE trainable_artifacts SET license_accepted_by = NULL, license_accepted_at = NULL, "
                    "status = 'registered' WHERE id = :i"
                ),
                {"i": payload["artifact_id"]},
            )
            s.commit()
        return {"ok": True}

    seeds = {
        "org1": str(org1_id),
        "org2": str(org2_id),
        "projects": {k: str(v) for k, v in pid.items()},
        "foreign_project": str(p_foreign_id),
        "versions": {k: str(v) for k, v in versions.items()},
        "r3_artifact": str(art_id["r3"]),
        "r1_ref": R1_REF,
    }
    with _running_app(app) as base:
        env = dict(os.environ)
        env.update(
            {
                "FTF_H010P6_UI_API_BASE": base,
                "FTF_H010P6_UI_SEEDS": json.dumps(seeds),
            }
        )
        ran = subprocess.run(
            [
                "pnpm",
                "exec",
                "vitest",
                "run",
                "--reporter=verbose",
                "--config",
                "vitest.real-api.config.ts",
                "ftf_h010_p6_ui_real_api",
            ],
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
            timeout=1500,
        )
        print(ran.stdout[-9000:])
        print(ran.stderr[-3000:])
        check(f"the Vitest real-API run exits 0 (got {ran.returncode})", ran.returncode == 0)
        plain = re.sub(r"\x1b\[[0-9;]*m", "", ran.stdout)
        for name in ("R1", "R2", "R3", "R4", "R5", "R6", "R7", "R8", "R9", "R10"):
            check(
                f"the Vitest run executed and passed {name}",
                re.search(rf"✓.*\b{name}\b", plain) is not None,
            )

    # ---- independent database verification -------------------------------------------------------
    def rows(version: uuid.UUID) -> list[Any]:
        with owner_engine.connect() as conn:
            return list(
                conn.execute(
                    text(
                        "SELECT role, assigned_by, idempotency_key, revoked_at, revoked_by "
                        "FROM model_role_assignments WHERE model_version_id = :v ORDER BY assigned_at"
                    ),
                    {"v": str(version)},
                )
            )

    r1 = rows(versions["r1"])
    check(
        f"R1: exactly one active teacher row by the admin ({[tuple(r) for r in r1]})",
        len(r1) == 1
        and r1[0].role == "teacher"
        and r1[0].revoked_at is None
        and r1[0].assigned_by == "user-org1-organization_admin",
    )
    r4 = rows(versions["r4a"]) + rows(versions["r4b"])
    check(
        f"R4: exactly one current_production row in the project ({[tuple(r) for r in r4]})",
        len(r4) == 1 and r4[0].role == "current_production" and r4[0].revoked_at is None,
    )
    r5 = rows(versions["r5"])
    check(
        f"R5: only the candidate row exists, no judge ({[tuple(r) for r in r5]})",
        [r.role for r in r5] == ["candidate"],
    )
    check(
        f"R6: no row for the ml_engineer's judge attempt ({rows(versions['r6'])})",
        rows(versions["r6"]) == [],
    )
    check(
        f"R7: no row for the unvalidated version ({rows(versions['r7'])})",
        rows(versions["r7"]) == [],
    )
    r8 = rows(versions["r8"])
    check(
        f"R8: one candidate row, revoked once by the admin ({[tuple(r) for r in r8]})",
        len(r8) == 1
        and r8[0].revoked_at is not None
        and r8[0].revoked_by == "user-org1-organization_admin",
    )
    r10 = rows(versions["r10"])
    check(
        f"R10: the lost-then-retried assign left exactly one row with a v4 UUID key ({[tuple(r) for r in r10]})",
        len(r10) == 1
        and re.fullmatch(
            r"[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}",
            r10[0].idempotency_key,
        )
        is not None,
    )
    with owner_engine.connect() as conn:
        org2_rows = conn.execute(
            text("SELECT count(*) FROM model_role_assignments WHERE organization_id = :o"),
            {"o": str(org2_id)},
        ).scalar()
        viewer_rows = conn.execute(
            text(
                "SELECT count(*) FROM model_role_assignments WHERE assigned_by = 'user-org1-viewer'"
            )
        ).scalar()
    check(f"R9: nothing written in the other organization (got {org2_rows})", org2_rows == 0)
    check(f"no row assigned by the viewer (got {viewer_rows})", viewer_rows == 0)

    app_engine.dispose()
    owner_engine.dispose()
    _finish()


def _finish() -> None:
    if FAILURES:
        print(f"FTF-H010-P6 UI real API (PG16): {len(FAILURES)} failed")
        for failure in FAILURES:
            print(f"  {failure}")
        sys.exit(1)
    print("FTF-H010-P6 UI real API (PG16): all checks passed")
    sys.exit(0)


if __name__ == "__main__":
    main()
