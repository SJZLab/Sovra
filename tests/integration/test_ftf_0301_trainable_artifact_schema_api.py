#!/usr/bin/env python3
"""FTF-0301 -- real Postgres TrainableArtifact schema/API proof.

Proves this task's own acceptance clause 1 ("Register versioned artifact
URI/revision/license with source grants and immutable identity after
resolution") and negative_acceptance ("Unsafe URI, unknown owner or
mutable unresolved revision cannot enter training as ready") against a
real, live PostgreSQL server -- never a mocked connection or an in-memory
SQLite stand-in (SQLite has no row-level security at all, so a passing
test against it would prove nothing about this task's actual acceptance
criterion).

Two real organizations (org1/org2) are seeded as the migration-owner
role; every request below runs through a real uvicorn server, with the
router's own database session bound to the real, distinct,
least-privileged `sovra_app` role FTF-0102's migration creates (never the
migration-owner role -- confirmed directly below), exactly like
`tests/integration/test_ftf_0201_litellm_connection_crud.py` already
established for a different resource.

Requires a real Postgres reachable at `SOVRA_DATABASE_URL` (the migration
owner role) plus `SOVRA_APP_DB_PASSWORD`, matching every prior
integration test's own environment contract exactly. This repo's
interactive dev sandbox has no container-registry egress (same finding as
every prior integration-tested task) -- this evidence was instead
produced locally against a directly-installed PostgreSQL 16 cluster, and
is wired into CI's dedicated `trainable-artifact-schema-api-integration`
job (a GitHub Actions `services: postgres` container, real registry
egress on the hosted runner).

Run directly (against an already-migrated or unmigrated database; this
script runs the migration itself):
    SOVRA_DATABASE_URL=postgresql+psycopg://<migration-role>@host/db \\
    SOVRA_APP_DB_PASSWORD=<app-role-password> \\
    python3 tests/integration/test_ftf_0301_trainable_artifact_schema_api.py
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
from sqlalchemy import create_engine, text  # noqa: E402
from sqlalchemy.exc import DBAPIError  # noqa: E402
from sqlalchemy.orm import Session, sessionmaker  # noqa: E402

from app.artifacts.artifact_routes import build_trainable_artifact_router  # noqa: E402
from app.artifacts.artifact_schema import TrainableArtifact  # noqa: E402
from app.auth.organization_workspace_schema import APP_ROLE_NAME, Organization  # noqa: E402
from app.auth.rbac_middleware import AuthenticatedPrincipal, Role  # noqa: E402
from app.auth.resource_authorization import authorize_resource_ownership  # noqa: E402
from app.auth.tenant_isolation_query_guards import TenantContextError  # noqa: E402
from app.db.tenant_context import TenantContext  # noqa: E402

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
        "FTF-0301",
        f"alembic upgrade head runs cleanly (exit {migration_result.returncode})\n"
        f"stdout: {migration_result.stdout}\nstderr: {migration_result.stderr}",
        migration_result.returncode == 0,
    )
    if FAILURES:
        print(f"FTF-0301 real TrainableArtifact schema/API proof: {len(FAILURES)} failed")
        for failure in FAILURES:
            print(f"  {failure}")
        sys.exit(1)

    owner_engine = create_engine(migration_url)
    app_url = _app_role_url(migration_url, app_password)
    app_engine = create_engine(app_url)

    check(
        "FTF-0301",
        "the router's own database session is built from the restricted "
        "sovra_app role's connection URL, never the migration-owner role",
        app_engine.url.username == APP_ROLE_NAME
        and app_engine.url.username != owner_engine.url.username,
    )

    # -- Seed two real, distinct tenants (migration-owner role, seeding only) --
    with Session(owner_engine) as session:
        org1 = Organization(name="Acme 0301", slug=f"acme-0301-{uuid.uuid4().hex[:8]}")
        org2 = Organization(name="Globex 0301", slug=f"globex-0301-{uuid.uuid4().hex[:8]}")
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
        # -- Unauthenticated / role-boundary denial --
        r = _request(base_url, "GET", collection_path(org1_id))
        check("FTF-0301", "an unauthenticated list request is denied (401)", r.status == 401)

        r = _request(base_url, "GET", collection_path(org1_id), principal=org1_evaluator)
        check(
            "FTF-0301",
            "an evaluator (a role Section 45 names, with no dedicated "
            "restrictive read sentence for artifacts) can read (200)",
            r.status == 200,
        )

        r = _request(
            base_url,
            "POST",
            collection_path(org1_id),
            principal=org1_evaluator,
            payload={
                "display_name": "should-be-denied",
                "source_type": "huggingface",
                "source_uri": "meta-llama/Llama-3.1-8B",
                "model_family": "llama",
                "base_model_revision": "a" * 40,
                "license_id": "llama3",
            },
        )
        check(
            "FTF-0301",
            "an evaluator is denied create (403) -- write is ml_engineer/"
            "organization_admin only (Section 45's own role table)",
            r.status == 403,
        )

        # -- negative_acceptance: rejected (unsafe) source_uri fails at creation --
        r = _request(
            base_url,
            "POST",
            collection_path(org1_id),
            principal=org1_ml,
            payload={
                "display_name": "insecure-artifact",
                "source_type": "local_filesystem",
                "source_uri": "/etc/passwd",
                "model_family": "llama",
                "base_model_revision": "a" * 40,
                "license_id": "apache-2.0",
            },
        )
        check(
            "FTF-0301",
            "negative_acceptance: an unsafe (absolute-path) source_uri fails "
            "at creation (400), never a fabricated success",
            r.status == 400,
        )

        # -- Real create --
        r = _request(
            base_url,
            "POST",
            collection_path(org1_id),
            principal=org1_ml,
            payload={
                "display_name": "llama-8b-candidate",
                "source_type": "huggingface",
                "source_uri": "meta-llama/Llama-3.1-8B",
                "model_family": "llama",
                "base_model_revision": "main",
                "license_id": "llama3",
            },
        )
        check("FTF-0301", "a real, valid create succeeds (201)", r.status == 201)
        created = r.json() if r.status == 201 else {}
        check(
            "FTF-0301",
            "the created artifact starts registered, never ready at creation",
            isinstance(created, dict)
            and created.get("status") == "registered"
            and created.get("display_name") == "llama-8b-candidate"
            and created.get("organization_id") == str(org1_id)
            and created.get("weights_digest") is None
            and created.get("license_accepted_by") is None,
        )
        artifact_id = created.get("id") if isinstance(created, dict) else None

        # -- negative_acceptance: mark-ready refused -- nothing satisfied yet --
        r = _request(
            base_url, "POST", item_path(org1_id, artifact_id) + "/mark-ready", principal=org1_ml
        )
        check(
            "FTF-0301",
            "negative_acceptance: mark-ready is refused (409) with an "
            "unaccepted license, unresolved digest and mutable revision "
            "('main') all still true",
            r.status == 409,
        )

        # -- Real accept-license: license_accepted_by is server-derived --
        r = _request(
            base_url, "POST", item_path(org1_id, artifact_id) + "/accept-license", principal=org1_ml
        )
        check("FTF-0301", "a real license acceptance succeeds (200)", r.status == 200)
        accepted = r.json() if r.status == 200 else {}
        check(
            "FTF-0301",
            "license_accepted_by is the real authenticated actor's own "
            "subject, never a client-supplied value (this task never took "
            "one as a request field)",
            isinstance(accepted, dict)
            and accepted.get("license_accepted_by")
            == fixture.principals[("org1", Role.ML_ENGINEER)].subject
            and accepted.get("license_accepted_at") is not None,
        )

        # -- mark-ready still refused: revision still mutable, digest unresolved --
        r = _request(
            base_url, "POST", item_path(org1_id, artifact_id) + "/mark-ready", principal=org1_ml
        )
        check(
            "FTF-0301",
            "negative_acceptance: mark-ready is still refused (409) once "
            "license is accepted but revision is still 'main' and "
            "weights_digest is still unresolved",
            r.status == 409,
        )

        # -- Resolve weights_digest alone first (revision still 'main') to
        # independently isolate the "mutable unresolved revision" gate from
        # the "digest not yet resolved" gate -- a fault injection that
        # widens `is_pinned_revision` to always report pinned would pass
        # this specific check even though it does not touch digest
        # resolution at all, which is exactly why this step exists as its
        # own assertion rather than resolving both fields in one PATCH. --
        r = _request(
            base_url,
            "PATCH",
            item_path(org1_id, artifact_id),
            principal=org1_ml,
            payload={"weights_digest": "sha256:" + "c" * 64},
        )
        check("FTF-0301", "resolving weights_digest alone succeeds (200)", r.status == 200)

        r = _request(
            base_url, "POST", item_path(org1_id, artifact_id) + "/mark-ready", principal=org1_ml
        )
        check(
            "FTF-0301",
            "negative_acceptance: mark-ready is refused (409) with a "
            "resolved digest but a still-mutable ('main') revision -- the "
            "mutable-revision gate is real, not merely masked by the "
            "digest-unresolved gate",
            r.status == 409,
        )

        # -- Now resolve the revision too --
        r = _request(
            base_url,
            "PATCH",
            item_path(org1_id, artifact_id),
            principal=org1_ml,
            payload={"base_model_revision": "a1b2c3d4e5f60718293a4b5c6d7e8f9012345678"},
        )
        check(
            "FTF-0301",
            "a real, valid edit by the owning organization succeeds (200)",
            r.status == 200,
        )

        # -- acceptance: mark-ready now succeeds --
        r = _request(
            base_url, "POST", item_path(org1_id, artifact_id) + "/mark-ready", principal=org1_ml
        )
        check(
            "FTF-0301",
            "acceptance: mark-ready succeeds (200) once source_uri is safe, "
            "license is accepted and revision+digest are pinned/resolved",
            r.status == 200,
        )
        ready = r.json() if r.status == 200 else {}
        check("FTF-0301", "the artifact is genuinely ready", ready.get("status") == "ready")

        # -- negative_acceptance: isolate the "unknown owner" (license) gate
        # from the other two -- a separate artifact with weights_digest
        # resolved and base_model_revision already pinned via PATCH, but
        # accept-license never called, must still be refused. Mirrors the
        # mutable-revision isolation above: a fault that disables only the
        # license-acceptance check (leaving source_uri and revision/digest
        # checks untouched) must not be able to hide behind the other two
        # conditions independently producing the same 409. --
        r = _request(
            base_url,
            "POST",
            collection_path(org1_id),
            principal=org1_ml,
            payload={
                "display_name": "license-isolation-check",
                "source_type": "huggingface",
                "source_uri": "meta-llama/Llama-3.1-8B",
                "model_family": "llama",
                "base_model_revision": "main",
                "license_id": "llama3",
            },
        )
        check(
            "FTF-0301",
            "a real, valid create for the license-isolation case succeeds (201)",
            r.status == 201,
        )
        license_check_created = r.json() if r.status == 201 else {}
        license_check_id = (
            license_check_created.get("id") if isinstance(license_check_created, dict) else None
        )

        r = _request(
            base_url,
            "PATCH",
            item_path(org1_id, license_check_id),
            principal=org1_ml,
            payload={"weights_digest": "sha256:" + "d" * 64},
        )
        check(
            "FTF-0301",
            "resolving weights_digest for the license-isolation artifact " "succeeds (200)",
            r.status == 200,
        )

        r = _request(
            base_url,
            "PATCH",
            item_path(org1_id, license_check_id),
            principal=org1_ml,
            payload={"base_model_revision": "b1c2d3e4f5061728394a5b6c7d8e9f0123456789"},
        )
        check(
            "FTF-0301",
            "pinning base_model_revision for the license-isolation artifact " "succeeds (200)",
            r.status == 200,
        )

        r = _request(
            base_url,
            "POST",
            item_path(org1_id, license_check_id) + "/mark-ready",
            principal=org1_ml,
        )
        check(
            "FTF-0301",
            "negative_acceptance: mark-ready is refused (409) with a "
            "resolved digest and a pinned revision but no license "
            "acceptance -- the 'unknown owner' gate is real, not merely "
            "masked by the other two conditions independently producing "
            "the same 409",
            r.status == 409,
        )

        r = _request(
            base_url,
            "POST",
            item_path(org1_id, license_check_id) + "/accept-license",
            principal=org1_ml,
        )
        check(
            "FTF-0301",
            "accepting the license for the license-isolation artifact " "succeeds (200)",
            r.status == 200,
        )

        r = _request(
            base_url,
            "POST",
            item_path(org1_id, license_check_id) + "/mark-ready",
            principal=org1_ml,
        )
        check(
            "FTF-0301",
            "acceptance: mark-ready succeeds (200) once the license is "
            "accepted after digest and revision were already resolved",
            r.status == 200,
        )

        # -- acceptance: 'immutable identity after resolution' -- editing a
        # ready artifact is refused --
        r = _request(
            base_url,
            "PATCH",
            item_path(org1_id, artifact_id),
            principal=org1_ml,
            payload={"display_name": "should-not-apply"},
        )
        check(
            "FTF-0301",
            "acceptance: 'immutable identity after resolution' -- editing a "
            "ready artifact is refused (409), not silently applied",
            r.status == 409,
        )

        # -- negative_acceptance: THE core IDOR case --
        r = _request(base_url, "GET", item_path(org2_id, artifact_id), principal=org2_ml)
        check(
            "FTF-0301",
            "negative_acceptance: a foreign artifact ID (belongs to org1) "
            "requested under org2's own path by a real org2 ml_engineer is "
            "denied identically to missing (404)",
            r.status == 404,
        )
        r = _request(
            base_url,
            "PATCH",
            item_path(org2_id, artifact_id),
            principal=org2_ml,
            payload={"display_name": "hijacked"},
        )
        check(
            "FTF-0301",
            "negative_acceptance: org2's ml_engineer cannot mutate org1's "
            "real artifact even via their own path (404)",
            r.status == 404,
        )
        r = _request(base_url, "DELETE", item_path(org2_id, artifact_id), principal=org2_ml)
        check(
            "FTF-0301",
            "negative_acceptance: org2's ml_engineer cannot archive org1's "
            "real artifact either (404)",
            r.status == 404,
        )

        # -- Confirm the foreign-org mutation attempts truly did not mutate it --
        r = _request(base_url, "GET", item_path(org1_id, artifact_id), principal=org1_ml)
        still_ready = r.json() if r.status == 200 else {}
        check(
            "FTF-0301",
            "the artifact is genuinely untouched after org2's denied "
            "mutation attempts -- still ready, still named llama-8b-candidate",
            r.status == 200
            and still_ready.get("status") == "ready"
            and still_ready.get("display_name") == "llama-8b-candidate",
        )

        # -- Real archive (idempotent) --
        r = _request(base_url, "DELETE", item_path(org1_id, artifact_id), principal=org1_ml)
        check(
            "FTF-0301", "a real archive by the owning organization succeeds (200)", r.status == 200
        )
        archived = r.json() if r.status == 200 else {}
        check(
            "FTF-0301",
            "the archive genuinely transitions status and sets archived_at",
            isinstance(archived, dict)
            and archived.get("status") == "archived"
            and archived.get("archived_at") is not None,
        )
        first_archived_at = archived.get("archived_at")

        r = _request(base_url, "DELETE", item_path(org1_id, artifact_id), principal=org1_ml)
        check(
            "FTF-0301",
            "acceptance: archiving an already-archived artifact is idempotent "
            "-- still 200, archived_at does not move",
            r.status == 200 and r.json().get("archived_at") == first_archived_at,
        )

        r = _request(
            base_url,
            "POST",
            item_path(org1_id, artifact_id) + "/mark-ready",
            principal=org1_ml,
        )
        check(
            "FTF-0301",
            "acceptance: marking an archived artifact ready is refused (409)",
            r.status == 409,
        )

        # -- Uniqueness: a second create with the same display_name in the
        # same organization is rejected by the real DB constraint --
        r = _request(
            base_url,
            "POST",
            collection_path(org1_id),
            principal=org1_ml,
            payload={
                "display_name": "unique-name-check",
                "source_type": "private_s3",
                "source_uri": "s3://acme-artifacts/models/x",
                "model_family": "llama",
                "base_model_revision": "b" * 40,
                "license_id": "apache-2.0",
            },
        )
        check("FTF-0301", "the first create with this name succeeds (201)", r.status == 201)
        r = _request(
            base_url,
            "POST",
            collection_path(org1_id),
            principal=org1_ml,
            payload={
                "display_name": "unique-name-check",
                "source_type": "private_s3",
                "source_uri": "s3://acme-artifacts/models/y",
                "model_family": "llama",
                "base_model_revision": "c" * 40,
                "license_id": "apache-2.0",
            },
        )
        check(
            "FTF-0301",
            "a duplicate display_name within the same organization is "
            "rejected by the real database unique constraint (400/409/500 "
            "surfaced as a real DB integrity error, never a silent duplicate)",
            r.status >= 400,
        )

    # -- acceptance: missing tenant context is refused, not treated as "0 rows" --
    with Session(app_engine) as session:
        raw_context = TenantContext(organization_id=org1_id)
        try:
            authorize_resource_ownership(
                session,
                raw_context,
                TrainableArtifact.__table__,
                TrainableArtifact.__table__.c.id,
                TrainableArtifact.__table__.c.organization_id,
                uuid.UUID(artifact_id) if artifact_id else uuid.uuid4(),
            )
            check(
                "FTF-0301",
                "acceptance: a session with no tenant context ever bound is "
                "refused (TenantContextError), not treated as an ordinary "
                "zero-row/foreign-tenant denial",
                False,
            )
        except TenantContextError:
            check(
                "FTF-0301",
                "acceptance: a session with no tenant context ever bound is "
                "refused (TenantContextError), not treated as an ordinary "
                "zero-row/foreign-tenant denial",
                True,
            )

    # -- Grant-surface proof: sovra_app genuinely cannot DELETE the row
    # directly (archive is a soft state transition, never a real row
    # deletion). --
    if artifact_id:
        with Session(app_engine) as session:
            set_tenant_context_sql = text(
                "SELECT set_config('app.current_organization_id', :org_id, true)"
            )
            session.execute(set_tenant_context_sql, {"org_id": str(org1_id)})
            try:
                session.execute(
                    text("DELETE FROM trainable_artifacts WHERE id = :id"),
                    {"id": artifact_id},
                )
                session.commit()
                check(
                    "FTF-0301",
                    "negative_acceptance/grant-surface: the restricted "
                    "sovra_app role cannot DELETE a trainable_artifacts row "
                    "directly -- archive is a soft state transition only",
                    False,
                )
            except DBAPIError:
                session.rollback()
                check(
                    "FTF-0301",
                    "negative_acceptance/grant-surface: the restricted "
                    "sovra_app role cannot DELETE a trainable_artifacts row "
                    "directly -- archive is a soft state transition only",
                    True,
                )

    if FAILURES:
        print(f"FTF-0301 real TrainableArtifact schema/API proof: {len(FAILURES)} failed")
        for failure in FAILURES:
            print(f"  {failure}")
        sys.exit(1)

    print("FTF-0301 real TrainableArtifact schema/API proof: all checks passed")
    sys.exit(0)


if __name__ == "__main__":
    main()
