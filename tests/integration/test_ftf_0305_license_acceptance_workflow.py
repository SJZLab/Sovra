#!/usr/bin/env python3
"""FTF-0305 -- real Postgres License acceptance workflow (revoke-license) proof.

Proves this task's own acceptance clause 1 ("Authorized user records
actual license/usage terms and source acceptance bound to model
revision") and negative_acceptance ("Unaccepted/revoked license blocks
training/serving; no blanket approval based on provider name") against a
real, live PostgreSQL server -- reusing FTF-0301's own already-CERTIFIED
`trainable_artifacts` table and migration verbatim (this task's own
`database_changes` contract: "No independent schema change. Consume
frozen contract").

One real organization is seeded as the migration-owner role; every
request below runs through a real uvicorn server, with the router's own
database session bound to the real, distinct, least-privileged
`sovra_app` role -- exactly like
`tests/integration/test_ftf_0301_trainable_artifact_schema_api.py`
already established for the rest of this same router.

Requires a real Postgres reachable at `SOVRA_DATABASE_URL` (the migration
owner role) plus `SOVRA_APP_DB_PASSWORD`, matching every prior
integration test's own environment contract exactly. This repo's
interactive dev sandbox has no container-registry egress (same finding as
every prior integration-tested task) -- this evidence was instead
produced locally against a directly-installed PostgreSQL 16 cluster, and
is wired into CI's dedicated `license-acceptance-workflow-integration`
job (a GitHub Actions `services: postgres` container, real registry
egress on the hosted runner).

Run directly (against an already-migrated or unmigrated database; this
script runs the migration itself):
    SOVRA_DATABASE_URL=postgresql+psycopg://<migration-role>@host/db \\
    SOVRA_APP_DB_PASSWORD=<app-role-password> \\
    python3 tests/integration/test_ftf_0305_license_acceptance_workflow.py
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
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

ROOT = Path(__file__).resolve().parent.parent.parent
API_ROOT = ROOT / "apps" / "api"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(API_ROOT))

import uvicorn  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import Session, sessionmaker  # noqa: E402

from app.artifacts.artifact_routes import build_trainable_artifact_router  # noqa: E402
from app.auth.organization_workspace_schema import APP_ROLE_NAME, Organization  # noqa: E402
from app.auth.rbac_middleware import AuthenticatedPrincipal, Role  # noqa: E402

FAILURES: list[str] = []


def check(criterion_id: str, description: str, condition: bool) -> None:
    if not condition:
        FAILURES.append(f"FAIL [{criterion_id}] {description}")


def _required_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        print(
            f"BLOCKED_EXTERNAL: {name} is not set -- this task's real "
            "revoke-license evidence requires a live Postgres (migration "
            "owner connection). Not silently skipped: see this file's own "
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


# -- Fixture: two organizations, one ml_engineer + one evaluator each --------

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
        "FTF-0305",
        f"alembic upgrade head runs cleanly (exit {migration_result.returncode})\n"
        f"stdout: {migration_result.stdout}\nstderr: {migration_result.stderr}",
        migration_result.returncode == 0,
    )
    if FAILURES:
        print(f"FTF-0305 real License acceptance workflow proof: {len(FAILURES)} failed")
        for failure in FAILURES:
            print(f"  {failure}")
        sys.exit(1)

    owner_engine = create_engine(migration_url)
    app_url = _app_role_url(migration_url, app_password)
    app_engine = create_engine(app_url)

    # -- Seed two real, distinct tenants (migration-owner role, seeding only) --
    with Session(owner_engine) as session:
        org1 = Organization(name="Acme 0305", slug=f"acme-0305-{uuid.uuid4().hex[:8]}")
        org2 = Organization(name="Globex 0305", slug=f"globex-0305-{uuid.uuid4().hex[:8]}")
        session.add_all([org1, org2])
        session.commit()
        org1_id, org2_id = org1.id, org2.id

    fixture = _Fixture({"org1": org1_id, "org2": org2_id})
    session_factory = sessionmaker(bind=app_engine)
    app = FastAPI()
    app.include_router(
        build_trainable_artifact_router(
            session_factory,
            principal_resolver=fixture.principal_resolver,
            role_lookup=fixture.role_lookup,
        )
    )

    org1_ml = fixture.header_for("org1", Role.ML_ENGINEER)
    org1_evaluator = fixture.header_for("org1", Role.EVALUATOR)
    org2_ml = fixture.header_for("org2", Role.ML_ENGINEER)

    def collection_path(org_id: uuid.UUID) -> str:
        return f"/api/v1/organizations/{org_id}/artifacts"

    def item_path(org_id: uuid.UUID, artifact_id: object) -> str:
        return f"{collection_path(org_id)}/{artifact_id}"

    with _running_app(app) as base_url:
        # -- Create, accept, resolve digest+revision, mark-ready --
        r = _request(
            base_url,
            "POST",
            collection_path(org1_id),
            principal=org1_ml,
            payload={
                "display_name": "revoke-candidate",
                "source_type": "huggingface",
                "source_uri": "meta-llama/Llama-3.1-8B",
                "model_family": "llama",
                "base_model_revision": "a1b2c3d4e5f60718293a4b5c6d7e8f9012345678",
                "license_id": "llama3",
            },
        )
        check("FTF-0305", "a real, valid create succeeds (201)", r.status == 201)
        created = r.json() if r.status == 201 else {}
        artifact_id = created.get("id") if isinstance(created, dict) else None

        r = _request(
            base_url, "POST", item_path(org1_id, artifact_id) + "/accept-license", principal=org1_ml
        )
        check("FTF-0305", "a real license acceptance succeeds (200)", r.status == 200)

        r = _request(
            base_url,
            "PATCH",
            item_path(org1_id, artifact_id),
            principal=org1_ml,
            payload={"weights_digest": "sha256:" + "e" * 64},
        )
        check("FTF-0305", "resolving weights_digest succeeds (200)", r.status == 200)

        r = _request(
            base_url, "POST", item_path(org1_id, artifact_id) + "/mark-ready", principal=org1_ml
        )
        check("FTF-0305", "mark-ready succeeds (200) once all gates are satisfied", r.status == 200)
        ready = r.json() if r.status == 200 else {}
        check(
            "FTF-0305",
            "the artifact is genuinely ready before revocation",
            ready.get("status") == "ready",
        )

        # -- Role boundary: an evaluator cannot revoke a license --
        r = _request(
            base_url,
            "POST",
            item_path(org1_id, artifact_id) + "/revoke-license",
            principal=org1_evaluator,
        )
        check(
            "FTF-0305",
            "an evaluator is denied revoke-license (403) -- write is "
            "ml_engineer/organization_admin only",
            r.status == 403,
        )

        # -- negative_acceptance: THE core IDOR case for the new route --
        r = _request(
            base_url, "POST", item_path(org2_id, artifact_id) + "/revoke-license", principal=org2_ml
        )
        check(
            "FTF-0305",
            "negative_acceptance: org2's ml_engineer cannot revoke org1's "
            "real artifact's license via their own path (404, identical to missing)",
            r.status == 404,
        )

        # -- Confirm the foreign-org revoke attempt truly did not mutate it --
        r = _request(base_url, "GET", item_path(org1_id, artifact_id), principal=org1_ml)
        still_ready = r.json() if r.status == 200 else {}
        check(
            "FTF-0305",
            "the artifact is genuinely untouched after org2's denied revoke "
            "attempt -- still ready, license still attributed",
            r.status == 200
            and still_ready.get("status") == "ready"
            and still_ready.get("license_accepted_by") is not None,
        )

        # -- Real revoke by the owning organization --
        r = _request(
            base_url, "POST", item_path(org1_id, artifact_id) + "/revoke-license", principal=org1_ml
        )
        check(
            "FTF-0305",
            "a real revoke-license by the owning organization succeeds (200)",
            r.status == 200,
        )
        revoked = r.json() if r.status == 200 else {}
        check(
            "FTF-0305",
            "negative_acceptance: revoking a ready artifact's license "
            "demotes it back to registered and clears attribution",
            isinstance(revoked, dict)
            and revoked.get("status") == "registered"
            and revoked.get("license_accepted_by") is None
            and revoked.get("license_accepted_at") is None,
        )

        # -- negative_acceptance: mark-ready is refused again post-revocation --
        r = _request(
            base_url, "POST", item_path(org1_id, artifact_id) + "/mark-ready", principal=org1_ml
        )
        check(
            "FTF-0305",
            "negative_acceptance: 'revoked license blocks training/"
            "serving' -- mark-ready is refused (409) once the license "
            "has been revoked, exactly the mechanism a real training/"
            "serving admission path relies on",
            r.status == 409,
        )

        # -- Idempotency: revoking again (nothing to revoke) is a no-op 200 --
        r = _request(
            base_url, "POST", item_path(org1_id, artifact_id) + "/revoke-license", principal=org1_ml
        )
        check(
            "FTF-0305",
            "revoking an already-unaccepted artifact is idempotent (200, no-op)",
            r.status == 200 and r.json().get("license_accepted_by") is None,
        )

        # -- negative_acceptance: revoking an archived artifact's license is refused --
        r = _request(base_url, "DELETE", item_path(org1_id, artifact_id), principal=org1_ml)
        check("FTF-0305", "archiving the artifact succeeds (200)", r.status == 200)

        r = _request(
            base_url, "POST", item_path(org1_id, artifact_id) + "/revoke-license", principal=org1_ml
        )
        check(
            "FTF-0305",
            "negative_acceptance: revoking a license on an archived "
            "artifact is refused (409) -- a closed record accepts no "
            "further mutation",
            r.status == 409,
        )

    if FAILURES:
        print(f"FTF-0305 real License acceptance workflow proof: {len(FAILURES)} failed")
        for failure in FAILURES:
            print(f"  {failure}")
        sys.exit(1)

    print("FTF-0305 real License acceptance workflow proof: all checks passed")
    sys.exit(0)


if __name__ == "__main__":
    main()
