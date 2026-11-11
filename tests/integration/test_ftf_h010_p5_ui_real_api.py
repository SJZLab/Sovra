#!/usr/bin/env python3
# mypy: disable-error-code="arg-type,attr-defined,return-value,assignment,call-arg,index,misc,no-untyped-def,var-annotated,union-attr,operator,no-any-return"
# ruff: noqa: E501
"""FTF-H010-P5 UI -- the real import wizard against the REAL FTF-H010-P1/P2/P3 routes.

`apps/web/tests/ftf_h010_p5.spec.ts` drives the wizard against a mocked `fetch`. This suite closes
the UI CHECKLIST's item (f): it boots the real model-import, model-version, artifact, project,
LiteLLM connection and catalog routers on a real uvicorn server over PostgreSQL 16 as the restricted
`sovra_app` role, seeds real organizations / projects / connections / artifacts, then runs
`apps/web/tests/real_api/ftf_h010_p5_ui_real_api.realapi.ts` (Vitest + jsdom, the real component and
real `fetch`) against it.

STAND-IN, DISCLOSED: no production dispatcher exists (FTF-0801/H016), so a job stays `queued` until
something validates it. This harness exposes `/__harness/advance`, which calls the REAL certified
`claim_model_import_job` + `validate_endpoint_import` / `validate_weights_import` /
`validate_adapter_import` for one job. It stands in for the absent dispatcher; it is not product
behaviour. Endpoint success reuses FTF-H010-P2's own local TLS gateway and OpenBao KV stand-ins
(real-gateway proof stays BLOCKED_EXTERNAL); weights/adapter use FTF-H010-P3's tiny stdlib
safetensors fixtures over a real approved-root directory.

R1  endpoint import: queued shown, then validated -> succeeded, lineage Unknown, no digest in the DOM
R2  a resolved-model echo that differs -> failed route_identity_unverified
R3  gateway unreachable -> blocked_external (never failed / success)
R4  local full weights -> succeeded, lineage Partial
R5  digest mismatch -> failed digest_mismatch
R6  adapter on a mismatched base -> adapter_base_mismatch
R7  license revoked before validation -> license_not_accepted
R8  viewer / evaluator denied; ml_engineer on the endpoint branch -> admin-required (and the server 403s)
R9  a foreign project -> not_found shown, nothing written
R10 reload mid-queue -> the job is restored
R11 the POST response dropped -> exactly one job row, and the wizard shows it
R12 a private_s3 artifact -> blocked_external metadata_files_unavailable

Afterwards this file checks the database independently: exactly the rows the scenarios must have
produced, NULL artifact_sha256-equivalents/endpoint columns for the endpoint version, nothing in
the other organization.

Requires SOVRA_DATABASE_URL (migration owner), SOVRA_APP_DB_PASSWORD, openssl + sudo +
update-ca-certificates (the P2 TLS stand-in), and Node with `pnpm install` done.
Run: python3 tests/integration/test_ftf_h010_p5_ui_real_api.py
"""

from __future__ import annotations

import contextlib
import importlib.util
import ipaddress
import json
import os
import re
import socket
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "apps" / "api"))

import uvicorn  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from packages.security.url_policy import ConnectionPolicy  # noqa: E402
from sqlalchemy import create_engine, text  # noqa: E402
from sqlalchemy.orm import Session, sessionmaker  # noqa: E402

from app.artifacts.artifact_routes import build_trainable_artifact_router  # noqa: E402
from app.artifacts.artifact_schema import TrainableArtifact  # noqa: E402
from app.artifacts.base_model_revision_digest_resolver import canonical_weights_digest  # noqa: E402
from app.artifacts.local_artifact_resolver import resolve_local_artifact  # noqa: E402
from app.auth.organization_workspace_schema import (  # noqa: E402
    APP_ROLE_NAME,
    Organization,
    Workspace,
)
from app.auth.rbac_middleware import AuthenticatedPrincipal, Role  # noqa: E402
from app.litellm.connection_routes import build_litellm_connection_router  # noqa: E402
from app.litellm.connection_schema import LiteLLMConnection  # noqa: E402
from app.litellm.model_catalog_routes import build_model_catalog_router  # noqa: E402
from app.model_versions import model_import_validation as activity  # noqa: E402
from app.model_versions import model_import_weights_validation as p3act  # noqa: E402
from app.model_versions.model_import_routes import build_model_import_router  # noqa: E402
from app.model_versions.model_version_routes import build_model_version_router  # noqa: E402
from app.projects.project_routes import build_project_router  # noqa: E402
from app.projects.project_schema import Project  # noqa: E402

CID = "FTF-H010-P5-UI-F"
FAILURES: list[str] = []
HERE = Path(__file__).resolve().parent
_ISSUER = "https://issuer.example"


def _load(name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name[:-3], HERE / name)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


p2 = _load("test_ftf_h010_p2_import_validation.py")
p3 = _load("test_ftf_h010_p3_weights_import.py")


def check(description: str, condition: bool) -> None:
    if not condition:
        FAILURES.append(f"FAIL [{CID}] {description}")


class _Fixture:
    LABELS = ("org1", "org2")

    def __init__(self, org_ids: dict[str, uuid.UUID]) -> None:
        self.principals: dict[tuple[str, Role], AuthenticatedPrincipal] = {}
        self.memberships: dict[tuple[str, str, uuid.UUID], Role] = {}
        for label, org_id in org_ids.items():
            for role in (Role.ORGANIZATION_ADMIN, Role.ML_ENGINEER, Role.EVALUATOR, Role.VIEWER):
                subject = f"user-{label}-{role.value}"
                self.principals[(label, role)] = AuthenticatedPrincipal(
                    issuer=_ISSUER, subject=subject
                )
                self.memberships[(_ISSUER, subject, org_id)] = role

    @staticmethod
    def header_for(label: str, role: Role) -> str:
        return f"{label}:{role.value}"

    def principal_resolver(self, request: Any) -> AuthenticatedPrincipal | None:
        header = request.headers.get("x-test-principal")
        if header is None:
            return None
        label, _, role_value = header.partition(":")
        try:
            return self.principals.get((label, Role(role_value)))
        except ValueError:
            return None

    def role_lookup(self, issuer: str, subject: str, organization_id: uuid.UUID) -> Role | None:
        return self.memberships.get((issuer, subject, organization_id))


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
    migration_url = p3._required_env("SOVRA_DATABASE_URL")
    app_password = p3._required_env("SOVRA_APP_DB_PASSWORD")
    gateway_server = p2._start_gateway()
    gateway_port = gateway_server.server_address[1]
    dead = socket.socket()
    dead.bind(("127.0.0.1", 0))
    dead_port = dead.getsockname()[1]
    dead.close()  # nothing listens here: a refused connection

    result = p3._alembic(["upgrade", "head"], migration_url)
    check(f"alembic upgrade head ({result.stdout}{result.stderr})", result.returncode == 0)
    if result.returncode != 0:
        return _finish()

    owner_engine = create_engine(migration_url)
    app_engine = create_engine(p3._app_role_url(migration_url, app_password))
    check(
        "the app engine is the restricted sovra_app role", app_engine.url.username == APP_ROLE_NAME
    )
    factory = sessionmaker(bind=app_engine, expire_on_commit=False)

    approved_root = Path(tempfile.mkdtemp(prefix="ftf-h010p5-root-"))
    suffix = uuid.uuid4().hex[:8]
    with Session(owner_engine) as session:
        org1 = Organization(name="Acme H010P5", slug=f"acme-h010p5-{suffix}")
        org2 = Organization(name="Globex H010P5", slug=f"globex-h010p5-{suffix}")
        session.add_all([org1, org2])
        session.flush()
        ws1 = Workspace(organization_id=org1.id, name="Main", slug="main")
        ws2 = Workspace(organization_id=org2.id, name="Main", slug="main")
        session.add_all([ws1, ws2])
        session.flush()
        projects = []
        for org, ws, name in ((org1, ws1, "p1"), (org2, ws2, "p2")):
            project = Project(
                organization_id=org.id,
                workspace_id=ws.id,
                name=name,
                project_type="generic_model_tuning",
                status="active",
                security_profile="standard",
                row_version=1,
                created_by="seed",
            )
            session.add(project)
            projects.append(project)
        session.commit()
        org1_id, org2_id = org1.id, org2.id
        proj1, proj2 = (p.id for p in projects)
        conn_ok = LiteLLMConnection(
            organization_id=org1_id,
            name="gw-ok",
            base_url=f"https://127.0.0.1:{gateway_port}{p2.GATEWAY.prefix}",
            secret_ref=p2._SECRET_REF,
        )
        conn_dead = LiteLLMConnection(
            organization_id=org1_id,
            name="gw-dead",
            base_url=f"https://127.0.0.1:{dead_port}",
            secret_ref=p2._SECRET_REF,
        )
        session.add_all([conn_ok, conn_dead])
        session.commit()
        conn_ok_id, conn_dead_id = conn_ok.id, conn_dead.id

    counter = [0]

    def add_artifact(
        directory: str,
        files: dict[str, bytes],
        *,
        accepted: bool = True,
        revision: str = p3.REVISION,
        digest: str | None = None,
        source_type: str = "local_filesystem",
        source_uri: str | None = None,
    ) -> uuid.UUID:
        base = approved_root / directory
        base.mkdir(parents=True, exist_ok=True)
        for name, data in files.items():
            (base / name).write_bytes(data)
        if digest is None:
            digest = canonical_weights_digest(
                resolve_local_artifact(approved_root, directory).manifest_sha256
            )
        counter[0] += 1
        with Session(owner_engine) as session:
            artifact = TrainableArtifact(
                organization_id=org1_id,
                display_name=f"ui-artifact-{suffix}-{counter[0]}",
                source_type=source_type,
                source_uri=source_uri or directory,
                model_family="llama",
                base_model_revision=revision,
                weights_digest=digest,
                license_id="apache-2.0",
                license_accepted_by="owner" if accepted else None,
                license_accepted_at=datetime.now(UTC),
                trust_remote_code_allowed=False,
                required_python_packages_json=[],
                minimum_gpu_profile_json={},
                status="ready",
            )
            session.add(artifact)
            session.commit()
            return artifact.id

    art_base = add_artifact("base", p3.weights_files())  # validated in setup -> the adapter base
    art_r4 = add_artifact("w-ok", p3.weights_files())
    art_r5 = add_artifact("w-digest", p3.weights_files(), digest="sha256:" + "0" * 64)
    art_r6 = add_artifact("a-rev", p3.adapter_files(), revision="ffffffffffff")
    art_r7 = add_artifact("w-license", p3.weights_files())
    art_r11 = add_artifact("w-lost", p3.weights_files())
    art_r12 = add_artifact(
        "w-s3",
        p3.weights_files(),
        source_type="private_s3",
        source_uri="s3://ui-bucket/weights",
        digest="sha256:" + "1" * 64,
    )

    fixture = _Fixture({"org1": org1_id, "org2": org2_id})
    policy = ConnectionPolicy(
        name="test-litellm-gateway",
        allowed_schemes=frozenset({"https"}),
        allowed_ports=frozenset({gateway_port, dead_port}),
        explicit_private_allowlist=(ipaddress.ip_network("127.0.0.0/8"),),
    )

    # ---- setup (labelled stand-in): validate the base weights job so adapters have a base ----------
    from app.db.tenant_context import TenantContext, tenant_scoped_session_for
    from app.model_versions.model_import_crud import ActorInfo, create_model_import_job

    def setup_weights_job(artifact: uuid.UUID) -> uuid.UUID:
        ctx = TenantContext(organization_id=org1_id)
        with tenant_scoped_session_for(factory, ctx) as session:
            job = create_model_import_job(
                session,
                ctx,
                project_id=proj1,
                requested_origin="imported_weights",
                parent_model_version_id=None,
                trainable_artifact_id=artifact,
                external_provider_model_ref=None,
                actor=ActorInfo(actor_type="user", actor_id="seed", request_id=uuid.uuid4().hex),
            )
            job_id = job.id
        token = activity.claim_model_import_job(
            factory,
            org1_id,
            proj1,
            job_id,
            origins=("imported_weights",),
            worker_id="setup",
            lease_seconds=600,
            now=datetime.now(UTC),
        )
        outcome = p3act.validate_weights_import(
            factory, token, approved_root=approved_root, now=datetime.now(UTC)
        )
        check(f"setup: the base weights job succeeded ({outcome})", outcome.state == "succeeded")
        return outcome.model_version_id

    base_version = setup_weights_job(art_base)
    # The adapter artifact for R6 must name that base; the mismatched revision makes it fail.

    # ---- the app: real routers + the stand-in dispatcher -----------------------------------------
    app = FastAPI()
    kw = {"principal_resolver": fixture.principal_resolver, "role_lookup": fixture.role_lookup}
    app.include_router(build_model_import_router(factory, **kw))
    app.include_router(build_model_version_router(factory, **kw))
    app.include_router(build_trainable_artifact_router(factory, **kw))
    app.include_router(build_project_router(factory, **kw))
    app.include_router(
        build_litellm_connection_router(
            factory,
            **kw,
            secret_manager_config_factory=lambda: p2._SECRET_CONFIG,
            validation_policy_factory=lambda: policy,
        )
    )
    app.include_router(
        build_model_catalog_router(
            factory,
            **kw,
            secret_manager_config_factory=lambda: p2._SECRET_CONFIG,
            validation_policy_factory=lambda: policy,
        )
    )

    @app.post("/__harness/advance")
    def advance(payload: dict[str, Any]) -> dict[str, Any]:
        # STAND-IN for the absent FTF-0801/H016 dispatcher: claims and validates ONE job with the
        # real certified activities. Labelled, never product behaviour.
        job_id = uuid.UUID(payload["job_id"])
        with Session(owner_engine) as s:
            row = s.execute(
                text(
                    "SELECT organization_id, project_id, requested_origin, external_provider_model_ref "
                    "FROM model_import_jobs WHERE id = :i"
                ),
                {"i": str(job_id)},
            ).one()
        origin = row.requested_origin
        token = activity.claim_model_import_job(
            factory,
            row.organization_id,
            row.project_id,
            job_id,
            origins=(origin,),
            worker_id="stand-in-dispatcher",
            lease_seconds=600,
            now=datetime.now(UTC),
        )
        if origin == "external_endpoint":
            ref = row.external_provider_model_ref
            p2.GATEWAY.models = [ref]
            p2.GATEWAY.echo = payload.get("echo", ref)
            outcome = activity.validate_endpoint_import(
                factory,
                token,
                secret_config=p2._SECRET_CONFIG,
                now=datetime.now(UTC),
                policy_factory=lambda: policy,
                timeout=10.0,
            )
        else:
            fn = (
                p3act.validate_weights_import
                if origin == "imported_weights"
                else p3act.validate_adapter_import
            )
            outcome = fn(factory, token, approved_root=approved_root, now=datetime.now(UTC))
        return {"state": outcome.state, "failure_code": outcome.failure_code}

    @app.post("/__harness/revoke-license")
    def revoke_license(payload: dict[str, Any]) -> dict[str, Any]:
        with Session(owner_engine) as s:
            s.execute(
                text("UPDATE trainable_artifacts SET license_accepted_by = NULL WHERE id = :i"),
                {"i": payload["artifact_id"]},
            )
            s.commit()
        return {"ok": True}

    seeds = {
        "org1": str(org1_id),
        "org2": str(org2_id),
        "project": str(proj1),
        "foreign_project": str(proj2),
        "conn_ok": str(conn_ok_id),
        "conn_dead": str(conn_dead_id),
        "base_version": str(base_version),
        "artifacts": {
            "base": str(art_base),
            "r4": str(art_r4),
            "r5": str(art_r5),
            "r6": str(art_r6),
            "r7": str(art_r7),
            "r11": str(art_r11),
            "r12": str(art_r12),
        },
        "ref": p2.REF,
    }
    with _running_app(app) as base:
        env = dict(os.environ)
        env.update(
            {
                "FTF_H010P5_UI_API_BASE": base,
                "FTF_H010P5_UI_SEEDS": json.dumps(seeds),
                "FTF_H010P5_UI_PRINCIPALS": json.dumps(
                    {
                        role.value: fixture.header_for("org1", role)
                        for role in (
                            Role.ORGANIZATION_ADMIN,
                            Role.ML_ENGINEER,
                            Role.EVALUATOR,
                            Role.VIEWER,
                        )
                    }
                ),
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
                "ftf_h010_p5_ui_real_api",
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
        for name in ("R1", "R2", "R3", "R4", "R5", "R6", "R7", "R8", "R9", "R10", "R11", "R12"):
            check(
                f"the Vitest run executed and passed {name}",
                re.search(rf"✓.*\b{name}\b", plain) is not None,
            )

    # ---- independent database verification -------------------------------------------------------
    def jobs(where: str, params: dict[str, Any]) -> list[Any]:
        with owner_engine.connect() as conn:
            return list(
                conn.execute(
                    text(
                        "SELECT id, requested_origin, state, failure_code, result_model_version_id, "
                        "trainable_artifact_id, external_provider_model_ref, created_by "
                        f"FROM model_import_jobs WHERE {where}"
                    ),
                    params,
                )
            )

    ref = p2.REF
    r1 = jobs("external_provider_model_ref = :r", {"r": ref + "-r1"})
    check(f"R1: exactly one endpoint job (got {len(r1)})", len(r1) == 1)
    if r1:
        check(
            f"R1: it succeeded with a result version ({r1[0].state})",
            r1[0].state == "succeeded" and r1[0].result_model_version_id is not None,
        )
        check(
            "R1: created by the authenticated admin",
            r1[0].created_by == "user-org1-organization_admin",
        )
        with owner_engine.connect() as conn:
            version = conn.execute(
                text(
                    "SELECT import_kind, origin, lineage_completeness, weights_digest, "
                    "trainable_artifact_id, base_model_revision, external_provider_model_ref "
                    "FROM model_versions WHERE id = :i"
                ),
                {"i": r1[0].result_model_version_id},
            ).one()
        check(
            f"R1: the endpoint version has no digest, artifact or revision ({tuple(version)})",
            version.import_kind == "endpoint"
            and version.weights_digest is None
            and version.trainable_artifact_id is None
            and version.base_model_revision is None
            and version.external_provider_model_ref == ref + "-r1",
        )
    r2 = jobs("external_provider_model_ref = :r", {"r": ref + "-r2"})
    check(
        "R2: one job, failed route_identity_unverified, no version",
        len(r2) == 1
        and (r2[0].state, r2[0].failure_code) == ("failed", "route_identity_unverified")
        and r2[0].result_model_version_id is None,
    )
    r3 = jobs("external_provider_model_ref = :r", {"r": ref + "-r3"})
    check(
        "R3: one job, blocked_external gateway_unavailable, no version",
        len(r3) == 1
        and (r3[0].state, r3[0].failure_code) == ("blocked_external", "gateway_unavailable")
        and r3[0].result_model_version_id is None,
    )
    expected = {
        "r4": ("succeeded", None),
        "r5": ("failed", "digest_mismatch"),
        "r6": ("failed", "adapter_base_mismatch"),
        "r7": ("failed", "license_not_accepted"),
        "r11": ("queued", None),
        "r12": ("blocked_external", "metadata_files_unavailable"),
    }
    for key, (state, code) in expected.items():
        rows = jobs(
            "trainable_artifact_id = :a AND created_by LIKE 'user-org1-%'",
            {"a": str(seeds["artifacts"][key])},
        )
        check(f"{key.upper()}: exactly one job (got {len(rows)})", len(rows) == 1)
        if rows:
            check(
                f"{key.upper()}: ({rows[0].state}, {rows[0].failure_code}) == ({state}, {code})",
                (rows[0].state, rows[0].failure_code) == (state, code),
            )
    with owner_engine.connect() as conn:
        org2_jobs = conn.execute(
            text("SELECT count(*) FROM model_import_jobs WHERE organization_id = :o"),
            {"o": org2_id},
        ).scalar()
        r8_jobs = conn.execute(
            text(
                "SELECT count(*) FROM model_import_jobs WHERE created_by IN ('user-org1-viewer','user-org1-evaluator')"
            )
        ).scalar()
        r9_jobs = conn.execute(
            text("SELECT count(*) FROM model_import_jobs WHERE project_id = :p"), {"p": proj2}
        ).scalar()
    check(f"R8: viewer/evaluator created no job (got {r8_jobs})", r8_jobs == 0)
    check(f"R9: the foreign project has no job (got {r9_jobs})", r9_jobs == 0)
    check(f"no job in the other organization (got {org2_jobs})", org2_jobs == 0)

    app_engine.dispose()
    owner_engine.dispose()
    _finish()


def _finish() -> None:
    if FAILURES:
        print(f"FTF-H010-P5 UI real API (PG16): {len(FAILURES)} failed")
        for failure in FAILURES:
            print(f"  {failure}")
        sys.exit(1)
    print("FTF-H010-P5 UI real API (PG16): all checks passed")
    sys.exit(0)


if __name__ == "__main__":
    main()
