#!/usr/bin/env python3
"""FTF-0501-P1 -- real Postgres proof for Dataset version + upload-session
persistence and the two read routes (migration 0024).

Proves against a live PostgreSQL server (never SQLite -- it has no row-level
security) every clause of the QUEUE.md FTF-0501-P1 contract:

- the migration upgrades, downgrades and re-upgrades; both tables ENABLE +
  FORCE row-level security with a non-allow-all `tenant_isolation` policy;
- the store's lifecycle (`create_upload_session`, `record_upload_rejected`,
  `record_upload_verified_and_create_dataset`, `expire_open_sessions`) and
  each refusal writes nothing;
- raw `sovra_app` SQL that bypasses the store is refused by the database
  itself: a dataset on an open / rejected / expired session fails the
  composite foreign key; marking a session verified fails on partial size,
  oversize, hash mismatch, content-type mismatch and finalized-after-expiry;
  the declared values, object key, purpose and expiry are not updatable; a
  verified session a dataset references cannot be modified; a raw org2
  INSERT against org1 is rejected by RLS; DELETE is refused on both tables;
- the two read routes on a real uvicorn server: 401/403, held-out datasets
  are 404 to ml_engineer (identical to a random id) and visible to
  workspace_admin, pagination bounds, cross-tenant 404, and no response
  ever carries an object key, bucket or storage URI.

Every negative has a positive control right next to it, so a green run
cannot come from a fixture that refuses everything.

Requires SOVRA_DATABASE_URL (migration-owner role) and SOVRA_APP_DB_PASSWORD.
Run: python3 tests/integration/test_ftf_0501_p1_dataset_uploads.py
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
from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta
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

from app.auth.audit_event_writer import AuditEvent  # noqa: E402
from app.auth.organization_workspace_schema import (  # noqa: E402
    APP_ROLE_NAME,
    Organization,
    Workspace,
)
from app.auth.rbac_middleware import AuthenticatedPrincipal, Role  # noqa: E402
from app.datasets import dataset_upload_store as store  # noqa: E402
from app.datasets.dataset_routes import build_dataset_router  # noqa: E402
from app.datasets.dataset_source_connection_crud import (  # noqa: E402
    DatasetSourceAuthorizationError,
    DatasetSourceConnectionNotFoundError,
)
from app.db.tenant_context import TenantContext, tenant_scoped_session_for  # noqa: E402
from app.projects.project_schema import Project  # noqa: E402

FAILURES: list[str] = []
CID = "FTF-0501-P1"
GIB = 1024**3


def check(description: str, condition: bool) -> None:
    if not condition:
        FAILURES.append(f"FAIL [{CID}] {description}")


def _required_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        print(f"BLOCKED_EXTERNAL: {name} is not set (see this file's docstring)", file=sys.stderr)
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
def _running_app(app: FastAPI) -> Iterator[str]:
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


class _Response:
    def __init__(self, status: int, body: bytes) -> None:
        self.status = status
        self.body = body

    def json(self) -> Any:
        return json.loads(self.body)


def _get(base_url: str, path: str, principal: str | None = None) -> _Response:
    headers = {"X-Test-Principal": principal} if principal else {}
    parsed = urllib.parse.urlparse(base_url)
    conn = http.client.HTTPConnection(parsed.hostname or "127.0.0.1", parsed.port or 80, timeout=10)
    try:
        conn.request("GET", path, headers=headers)
        response = conn.getresponse()
        return _Response(response.status, response.read())
    finally:
        conn.close()


_ISSUER = "https://issuer.example"


class _Fixture:
    def __init__(self, org_ids: dict[str, uuid.UUID]) -> None:
        self.principals: dict[tuple[str, Role], AuthenticatedPrincipal] = {}
        self.memberships: dict[tuple[str, str, uuid.UUID], Role] = {}
        for label, org_id in org_ids.items():
            for role in (Role.ML_ENGINEER, Role.WORKSPACE_ADMIN, Role.VIEWER):
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
            role = Role(role_value)
        except ValueError:
            return None
        return self.principals.get((label, role))

    def role_lookup(self, issuer: str, subject: str, organization_id: uuid.UUID) -> Role | None:
        return self.memberships.get((issuer, subject, organization_id))


def _sha(seed: str) -> str:
    return (seed * 64)[:64]


def main() -> None:
    migration_url = _required_env("SOVRA_DATABASE_URL")
    app_password = _required_env("SOVRA_APP_DB_PASSWORD")

    for args, label in (
        (["upgrade", "head"], "alembic upgrade head"),
        (["downgrade", "-1"], "alembic downgrade -1"),
        (["upgrade", "head"], "alembic upgrade head after downgrade -1"),
    ):
        result = _alembic(args, migration_url)
        check(
            f"{label} runs cleanly (exit {result.returncode})\n{result.stdout}\n{result.stderr}",
            result.returncode == 0,
        )
        if result.returncode != 0:
            _finish()
    heads = _alembic(["heads"], migration_url)
    check(
        f"exactly one Alembic head: {heads.stdout!r}",
        heads.stdout.count("(head)") == 1,
    )

    owner_engine = create_engine(migration_url)
    app_engine = create_engine(_app_role_url(migration_url, app_password))
    check("the app engine is the restricted sovra_app role", app_engine.url.username == "sovra_app")
    factory = sessionmaker(bind=app_engine, expire_on_commit=False)

    _check_rls_catalog(owner_engine)

    # ---- seed: two orgs; org1 has two projects -------------------------------
    with Session(owner_engine) as session:
        org1 = Organization(name="Acme 0501", slug=f"acme-0501-{uuid.uuid4().hex[:8]}")
        org2 = Organization(name="Globex 0501", slug=f"globex-0501-{uuid.uuid4().hex[:8]}")
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

        p1, p1b, p2 = project(org1, ws1, "p1"), project(org1, ws1, "p1b"), project(org2, ws2, "p2")
        session.add_all([p1, p1b, p2])
        session.commit()
        org1_id, org2_id = org1.id, org2.id
        p1_id, p1b_id, p2_id = p1.id, p1b.id, p2.id

    def seed_connection(
        org: uuid.UUID,
        project_id: uuid.UUID,
        name: str,
        source_type: str,
        purposes: list[str],
        status: str = "active",
    ) -> uuid.UUID:
        connection_id = uuid.uuid4()
        with owner_engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO dataset_source_connections (id, organization_id, project_id, "
                    "source_type, display_name, allowed_purposes_json, security_classification, "
                    "connection_status, created_by) VALUES (:id, :org, :project, :stype, :name, "
                    "CAST(:purposes AS jsonb), 'internal', :status, 'seed')"
                ),
                {
                    "id": connection_id,
                    "org": org,
                    "project": project_id,
                    "stype": source_type,
                    "name": name,
                    "purposes": json.dumps(purposes),
                    "status": status,
                },
            )
        return connection_id

    upload_conn = seed_connection(
        org1_id, p1_id, "upload-ok", "upload", ["training", "held_out_evaluation", "validation"]
    )
    training_only_conn = seed_connection(org1_id, p1_id, "upload-train", "upload", ["training"])
    revoked_conn = seed_connection(
        org1_id, p1_id, "upload-revoked", "upload", ["training"], status="revoked"
    )
    s3_conn = seed_connection(org1_id, p1_id, "s3-src", "s3", ["training"])
    org2_conn = seed_connection(org2_id, p2_id, "org2-upload", "upload", ["training"])
    # Foreign key target for raw-SQL cases below.
    raw_conn = seed_connection(org1_id, p1_id, "raw-upload", "upload", ["training"])

    ctx1 = TenantContext(organization_id=org1_id)
    ctx2 = TenantContext(organization_id=org2_id)
    actor = store.UploadActor(actor_type="user", actor_id="user-org1", request_id="req-0501")
    now = datetime.now(UTC)

    def fresh() -> datetime:
        # FTF-0501-P2: the database refuses a session whose created_at is more than
        # 60 seconds from its own clock, so every session the test opens is stamped
        # with the current time, never a start-of-run constant or a backdated value.
        return datetime.now(UTC)

    def counts() -> tuple[int, int, int]:
        with owner_engine.connect() as conn:
            sessions = conn.execute(text("SELECT count(*) FROM dataset_upload_sessions")).scalar()
            datasets = conn.execute(text("SELECT count(*) FROM datasets")).scalar()
            audits = conn.execute(text("SELECT count(*) FROM audit_events")).scalar()
        return int(sessions or 0), int(datasets or 0), int(audits or 0)

    def audit_actions(resource_id: str) -> list[str]:
        with Session(owner_engine) as session:
            rows = (
                session.query(AuditEvent.action)
                .filter(AuditEvent.resource_id == resource_id)
                .order_by(AuditEvent.id)
                .all()
            )
        return [row[0] for row in rows]

    def run_store(
        ctx: TenantContext, fn: Callable[[Session], Any]
    ) -> tuple[Any, BaseException | None]:
        try:
            with tenant_scoped_session_for(factory, ctx) as session:
                return fn(session), None
        except BaseException as exc:  # noqa: BLE001 -- the test inspects the type
            return None, exc

    def open_session(
        *,
        purpose: str = "training",
        size: int = 1000,
        sha: str | None = None,
        content_type: str = "application/jsonl",
        ttl: timedelta = timedelta(hours=1),
        at: datetime | None = None,
        connection: uuid.UUID = upload_conn,
        project_id: uuid.UUID = p1_id,
    ) -> store.DatasetUploadSession:
        record, error = run_store(
            ctx1,
            lambda s: store.create_upload_session(
                s,
                ctx1,
                project_id=project_id,
                connection_id=connection,
                purpose=purpose,
                declared_size_bytes=size,
                declared_sha256=sha or _sha("a"),
                declared_content_type=content_type,
                max_bytes=size * 2,
                ttl=ttl,
                now=at,
                actor=actor,
            ),
        )
        if error is not None:
            raise error
        return record

    def finalize(
        record: store.DatasetUploadSession,
        *,
        name: str = "support",
        size: int | None = None,
        sha: str | None = None,
        content_type: str | None = None,
        at: datetime | None = None,
    ) -> tuple[Any, BaseException | None]:
        return run_store(
            ctx1,
            lambda s: store.record_upload_verified_and_create_dataset(
                s,
                ctx1,
                project_id=record.project_id,
                upload_session_id=record.id,
                dataset_name=name,
                verified_size_bytes=size if size is not None else record.declared_size_bytes,
                verified_sha256=sha or record.declared_sha256,
                verified_content_type=content_type or record.declared_content_type,
                now=at,
                actor=actor,
            ),
        )

    # ---- create_upload_session -----------------------------------------------
    before = counts()
    s1_at = fresh()
    s1 = open_session(at=s1_at)
    check(
        "created session is open with the id-derived object key",
        s1.status == "open"
        and s1.object_key == f"tenants/{org1_id}/projects/{p1_id}/uploads/{s1.id}/source",
    )
    check(
        "session lifetime is bounded to the requested ttl",
        s1.expires_at == s1_at + timedelta(hours=1),
    )
    check(
        "creating a session writes exactly one session and one audit event",
        counts() == (before[0] + 1, before[1], before[2] + 1)
        and audit_actions(str(s1.id)) == ["dataset_upload_session.create"],
    )
    s_default_ttl, error = run_store(
        ctx1,
        lambda s: store.create_upload_session(
            s,
            ctx1,
            project_id=p1_id,
            connection_id=upload_conn,
            purpose="training",
            declared_size_bytes=10,
            declared_sha256=_sha("a"),
            declared_content_type="a/b",
            max_bytes=10,
            actor=actor,
        ),
    )
    check(f"a session can be created without an explicit ttl ({error!r})", error is None)
    check(
        "default ttl is exactly 24 hours (Section 67.5)",
        s_default_ttl.expires_at - s_default_ttl.created_at == timedelta(hours=24),
    )

    before = counts()
    refusals: list[tuple[str, Callable[[Session], Any], type[BaseException]]] = [
        (
            "ungranted purpose is refused (Law 16)",
            lambda s: store.create_upload_session(
                s,
                ctx1,
                project_id=p1_id,
                connection_id=training_only_conn,
                purpose="validation",
                declared_size_bytes=10,
                declared_sha256=_sha("a"),
                declared_content_type="a/b",
                max_bytes=10,
                actor=actor,
            ),
            DatasetSourceAuthorizationError,
        ),
        (
            "a revoked connection is refused",
            lambda s: store.create_upload_session(
                s,
                ctx1,
                project_id=p1_id,
                connection_id=revoked_conn,
                purpose="training",
                declared_size_bytes=10,
                declared_sha256=_sha("a"),
                declared_content_type="a/b",
                max_bytes=10,
                actor=actor,
            ),
            DatasetSourceAuthorizationError,
        ),
        (
            "a non-upload connection is refused",
            lambda s: store.create_upload_session(
                s,
                ctx1,
                project_id=p1_id,
                connection_id=s3_conn,
                purpose="training",
                declared_size_bytes=10,
                declared_sha256=_sha("a"),
                declared_content_type="a/b",
                max_bytes=10,
                actor=actor,
            ),
            store.NotAnUploadSourceError,
        ),
        (
            "another organization's connection is not found",
            lambda s: store.create_upload_session(
                s,
                ctx1,
                project_id=p1_id,
                connection_id=org2_conn,
                purpose="training",
                declared_size_bytes=10,
                declared_sha256=_sha("a"),
                declared_content_type="a/b",
                max_bytes=10,
                actor=actor,
            ),
            DatasetSourceConnectionNotFoundError,
        ),
        (
            "a connection of another project is not found",
            lambda s: store.create_upload_session(
                s,
                ctx1,
                project_id=p1b_id,
                connection_id=upload_conn,
                purpose="training",
                declared_size_bytes=10,
                declared_sha256=_sha("a"),
                declared_content_type="a/b",
                max_bytes=10,
                actor=actor,
            ),
            DatasetSourceConnectionNotFoundError,
        ),
        (
            "size above 10 GiB is refused",
            lambda s: store.create_upload_session(
                s,
                ctx1,
                project_id=p1_id,
                connection_id=upload_conn,
                purpose="training",
                declared_size_bytes=10 * GIB + 1,
                declared_sha256=_sha("a"),
                declared_content_type="a/b",
                max_bytes=10 * GIB + 1,
                actor=actor,
            ),
            ValueError,
        ),
        (
            "zero size is refused",
            lambda s: store.create_upload_session(
                s,
                ctx1,
                project_id=p1_id,
                connection_id=upload_conn,
                purpose="training",
                declared_size_bytes=0,
                declared_sha256=_sha("a"),
                declared_content_type="a/b",
                max_bytes=10,
                actor=actor,
            ),
            ValueError,
        ),
        (
            "declared above max_bytes is refused",
            lambda s: store.create_upload_session(
                s,
                ctx1,
                project_id=p1_id,
                connection_id=upload_conn,
                purpose="training",
                declared_size_bytes=11,
                declared_sha256=_sha("a"),
                declared_content_type="a/b",
                max_bytes=10,
                actor=actor,
            ),
            ValueError,
        ),
        (
            "ttl above 24 hours is refused",
            lambda s: store.create_upload_session(
                s,
                ctx1,
                project_id=p1_id,
                connection_id=upload_conn,
                purpose="training",
                declared_size_bytes=10,
                declared_sha256=_sha("a"),
                declared_content_type="a/b",
                max_bytes=10,
                ttl=timedelta(hours=24, seconds=1),
                actor=actor,
            ),
            ValueError,
        ),
        (
            "an uppercase sha256 is refused",
            lambda s: store.create_upload_session(
                s,
                ctx1,
                project_id=p1_id,
                connection_id=upload_conn,
                purpose="training",
                declared_size_bytes=10,
                declared_sha256="A" * 64,
                declared_content_type="a/b",
                max_bytes=10,
                actor=actor,
            ),
            ValueError,
        ),
        (
            "a boolean size is refused",
            lambda s: store.create_upload_session(
                s,
                ctx1,
                project_id=p1_id,
                connection_id=upload_conn,
                purpose="training",
                declared_size_bytes=True,
                declared_sha256=_sha("a"),
                declared_content_type="a/b",
                max_bytes=10,
                actor=actor,
            ),
            ValueError,
        ),
        (
            "an unknown purpose is refused",
            lambda s: store.create_upload_session(
                s,
                ctx1,
                project_id=p1_id,
                connection_id=upload_conn,
                purpose="anything",
                declared_size_bytes=10,
                declared_sha256=_sha("a"),
                declared_content_type="a/b",
                max_bytes=10,
                actor=actor,
            ),
            ValueError,
        ),
    ]
    for description, fn, expected in refusals:
        _, error = run_store(ctx1, fn)
        check(f"create_upload_session: {description}", isinstance(error, expected))
    check("every create refusal wrote nothing", counts() == before)

    # ---- finalize -> Dataset version ---------------------------------------------
    d1, error = finalize(s1, name="support")
    check(f"verified finalize creates dataset v1 ({error!r})", error is None and d1.version == 1)
    check(
        "dataset carries the verified hash/size and the session's purpose",
        d1 is not None
        and d1.verified_sha256 == s1.declared_sha256
        and d1.verified_size_bytes == s1.declared_size_bytes
        and d1.purpose == "training"
        and d1.upload_session_id == s1.id,
    )
    check(
        "finalize audited the dataset creation",
        d1 is not None and audit_actions(str(d1.id)) == ["dataset.create"],
    )
    s2 = open_session(sha=_sha("b"))
    d2, error = finalize(s2, name="support")
    check(
        "the next verified upload of the same name is version 2", error is None and d2.version == 2
    )
    s3 = open_session(sha=_sha("c"))
    d3, error = finalize(s3, name="other-name")
    check("a different name starts at version 1", error is None and d3.version == 1)

    snapshot = counts()
    again, error = finalize(s1, name="support")
    check(
        "finalizing a verified session again is refused (not open)",
        isinstance(error, store.UploadStateError),
    )

    mismatch_cases = (
        ("size", {"size": s2.declared_size_bytes + 1}, ("size_bytes",)),
        ("sha256", {"sha": _sha("f")}, ("sha256",)),
        ("content type", {"content_type": "text/csv"}, ("content_type",)),
    )
    for label, kwargs, names in mismatch_cases:
        record = open_session(sha=_sha("d"))
        before = counts()
        _, error = finalize(record, **kwargs)
        check(
            f"a {label} mismatch is refused naming the field",
            isinstance(error, store.UploadVerificationError) and error.mismatched == names,
        )
        check(f"a {label} mismatch wrote nothing", counts() == before)
        check(
            f"a {label} mismatch leaves the session open",
            _session_status(owner_engine, record.id) == "open",
        )
    both = open_session(sha=_sha("d"))
    _, error = finalize(both, size=1, sha=_sha("0"), content_type="x/y")
    check(
        "all three mismatches are reported together",
        isinstance(error, store.UploadVerificationError)
        and set(error.mismatched) == {"size_bytes", "sha256", "content_type"},
    )

    old = open_session(sha=_sha("e"), ttl=timedelta(hours=1))
    before = counts()
    _, error = finalize(old, at=old.expires_at + timedelta(hours=2))
    check("finalizing after expiry is refused", isinstance(error, store.UploadExpiredError))
    check("an expired finalize wrote nothing", counts() == before)
    edge = open_session(sha=_sha("e"), ttl=timedelta(hours=1))
    edge_dataset, error = finalize(edge, name="edge", at=edge.expires_at)
    check(
        "finalizing exactly at expires_at is allowed", error is None and edge_dataset.version == 1
    )

    revoked_later = seed_connection(org1_id, p1_id, "to-revoke", "upload", ["training"])
    late = open_session(connection=revoked_later, sha=_sha("1"))
    with owner_engine.begin() as conn:
        conn.execute(
            text(
                "UPDATE dataset_source_connections SET connection_status='revoked', "
                "revoked_at=now() WHERE id=:id"
            ),
            {"id": revoked_later},
        )
    before = counts()
    _, error = finalize(late, name="revoked-flow")
    check(
        "a source revoked after the session opened creates no dataset",
        isinstance(error, DatasetSourceAuthorizationError) and counts() == before,
    )

    _, error = run_store(
        ctx1,
        lambda s: store.record_upload_verified_and_create_dataset(
            s,
            ctx1,
            project_id=p1_id,
            upload_session_id=uuid.uuid4(),
            dataset_name="x",
            verified_size_bytes=1,
            verified_sha256=_sha("a"),
            verified_content_type="a/b",
            actor=actor,
        ),
    )
    check("an unknown session id is not found", isinstance(error, store.UploadSessionNotFoundError))
    foreign = open_session()
    _, error = run_store(
        ctx2,
        lambda s: store.record_upload_verified_and_create_dataset(
            s,
            ctx2,
            project_id=p1_id,
            upload_session_id=foreign.id,
            dataset_name="x",
            verified_size_bytes=foreign.declared_size_bytes,
            verified_sha256=foreign.declared_sha256,
            verified_content_type=foreign.declared_content_type,
            actor=actor,
        ),
    )
    check(
        "org2 cannot finalize org1's session (not found, identical to a missing id)",
        isinstance(error, store.UploadSessionNotFoundError)
        and _session_status(owner_engine, foreign.id) == "open",
    )
    _, error = run_store(
        ctx1,
        lambda s: store.record_upload_verified_and_create_dataset(
            s,
            ctx1,
            project_id=p1b_id,
            upload_session_id=foreign.id,
            dataset_name="x",
            verified_size_bytes=foreign.declared_size_bytes,
            verified_sha256=foreign.declared_sha256,
            verified_content_type=foreign.declared_content_type,
            actor=actor,
        ),
    )
    check(
        "a session is not reachable through another project",
        isinstance(error, store.UploadSessionNotFoundError),
    )
    for bad_name in ("", " ", " pad", "x" * 201):
        _, error = finalize(open_session(sha=_sha("2")), name=bad_name)
        check(f"dataset name {bad_name[:6]!r}... is refused", isinstance(error, ValueError))
    check("a refused finalize left every refused-name session open", counts()[1] == snapshot[1] + 1)

    # ---- reject / expire ----------------------------------------------------------
    to_reject = open_session(sha=_sha("3"))
    rejected, error = run_store(
        ctx1,
        lambda s: store.record_upload_rejected(
            s,
            ctx1,
            project_id=p1_id,
            upload_session_id=to_reject.id,
            reason="hash_mismatch",
            actor=actor,
        ),
    )
    check(
        "an open session can be rejected with a stable reason",
        error is None
        and rejected.status == "rejected"
        and rejected.rejection_reason == "hash_mismatch",
    )
    check(
        "rejection is audited", "dataset_upload_session.reject" in audit_actions(str(to_reject.id))
    )
    _, error = run_store(
        ctx1,
        lambda s: store.record_upload_rejected(
            s, ctx1, project_id=p1_id, upload_session_id=to_reject.id, reason="again", actor=actor
        ),
    )
    check("a rejected session cannot be rejected again", isinstance(error, store.UploadStateError))
    _, error = finalize(to_reject, name="from-rejected")
    check("a rejected session cannot create a dataset", isinstance(error, store.UploadStateError))
    _, error = run_store(
        ctx1,
        lambda s: store.record_upload_rejected(
            s, ctx1, project_id=p1_id, upload_session_id=s1.id, reason="late", actor=actor
        ),
    )
    check(
        "a verified session cannot be rejected",
        isinstance(error, store.UploadStateError)
        and _session_status(owner_engine, s1.id) == "verified",
    )

    def reject_with(reason: str) -> Callable[[Session], Any]:
        return lambda s: store.record_upload_rejected(
            s,
            ctx1,
            project_id=p1_id,
            upload_session_id=open_session().id,
            reason=reason,
            actor=actor,
        )

    for bad_reason in ("", "Has Space", "X", "a" * 65, "1abc"):
        _, error = run_store(ctx1, reject_with(bad_reason))
        check(f"reason {bad_reason[:8]!r} is refused", isinstance(error, ValueError))

    stale = open_session(sha=_sha("4"), ttl=timedelta(minutes=1))
    fresh_session = open_session(sha=_sha("5"), ttl=timedelta(hours=1))
    sweep_at = fresh() + timedelta(minutes=5)
    expired_count, error = run_store(
        ctx1, lambda s: store.expire_open_sessions(s, ctx1, now=sweep_at, actor=actor)
    )
    check(
        "expire_open_sessions reports at least the stale session",
        error is None and expired_count >= 1,
    )
    check(
        "only the past-expiry open session expired; fresh, verified and rejected are untouched",
        _session_status(owner_engine, stale.id) == "expired"
        and _session_status(owner_engine, fresh_session.id) == "open"
        and _session_status(owner_engine, s1.id) == "verified"
        and _session_status(owner_engine, to_reject.id) == "rejected",
    )
    _, error = finalize(stale, name="from-expired")
    check("an expired session cannot create a dataset", isinstance(error, store.UploadStateError))
    other_org_count, _ = run_store(
        ctx2,
        lambda s: store.expire_open_sessions(s, ctx2, now=now + timedelta(days=2), actor=actor),
    )
    check(
        "org2's expiry sweep never touches org1 sessions",
        other_org_count == 0 and _session_status(owner_engine, fresh_session.id) == "open",
    )

    # ---- raw sovra_app SQL against the database constraints ------------------------
    def raw(tenant: uuid.UUID, sql: str, **params: Any) -> tuple[bool, str]:
        try:
            with app_engine.begin() as conn:
                conn.execute(
                    text("SELECT set_config('app.current_organization_id', :org, true)"),
                    {"org": str(tenant)},
                )
                conn.execute(text(sql), params)
            return True, ""
        except DBAPIError as exc:
            return False, str(exc.orig)

    def raw_insert_session(
        org: uuid.UUID = org1_id, project_id: uuid.UUID = p1_id, **over: Any
    ) -> tuple[uuid.UUID, bool, str]:
        created_now = fresh()
        values: dict[str, Any] = {
            "id": uuid.uuid4(),
            "org": org,
            "project": project_id,
            "conn": raw_conn,
            "purpose": "training",
            "size": 500,
            "max": 1000,
            "sha": _sha("9"),
            "ctype": "application/jsonl",
            "status": "open",
            "created": created_now,
            "expires": created_now + timedelta(hours=1),
        }
        values.update(over)
        values.setdefault(
            "key",
            f"tenants/{values['org']}/projects/{values['project']}/uploads/{values['id']}/source",
        )
        ok, message = raw(
            org,
            "INSERT INTO dataset_upload_sessions (id, organization_id, project_id, "
            "dataset_source_connection_id, purpose, object_key, declared_size_bytes, max_bytes, "
            "declared_sha256, declared_content_type, status, created_by, created_at, expires_at, "
            "updated_at) VALUES (:id, :org, :project, :conn, :purpose, :key, :size, :max, :sha, "
            ":ctype, :status, 'raw', :created, :expires, :created)",
            **values,
        )
        return values["id"], ok, message

    def raw_verify(session_id: uuid.UUID, **over: Any) -> tuple[bool, str]:
        values: dict[str, Any] = {
            "id": session_id,
            "size": 500,
            "sha": _sha("9"),
            "ctype": "application/jsonl",
            "finalized": now + timedelta(minutes=5),
            "updated": now + timedelta(minutes=5),
        }
        values.update(over)
        return raw(
            org1_id,
            "UPDATE dataset_upload_sessions SET status='verified', verified_size_bytes=:size, "
            "verified_sha256=:sha, verified_content_type=:ctype, finalized_at=:finalized, "
            "updated_at=:updated WHERE id=:id",
            **values,
        )

    def raw_insert_dataset(session_id: uuid.UUID, **over: Any) -> tuple[bool, str]:
        values: dict[str, Any] = {
            "id": uuid.uuid4(),
            "org": org1_id,
            "project": p1_id,
            "name": f"raw-{uuid.uuid4().hex[:6]}",
            "version": 1,
            "purpose": "training",
            "session": session_id,
            "ustatus": "verified",
            "sha": _sha("9"),
            "size": 500,
        }
        values.update(over)
        return raw(
            org1_id,
            "INSERT INTO datasets (id, organization_id, project_id, name, version, purpose, "
            "upload_session_id, upload_status, verified_sha256, verified_size_bytes, created_by) "
            "VALUES (:id, :org, :project, :name, :version, :purpose, :session, :ustatus, :sha, "
            ":size, 'raw')",
            **values,
        )

    def expect(description: str, outcome: tuple[bool, str], needle: str) -> None:
        ok, message = outcome
        check(f"{description} (got ok={ok}: {message[:160]!r})", (not ok) and needle in message)

    open_id, ok, message = raw_insert_session()
    check(f"positive control: a well-formed raw session INSERT succeeds ({message[:120]})", ok)
    expect(
        "a dataset on an OPEN session fails the verified-session foreign key",
        raw_insert_dataset(open_id),
        "fk_datasets_verified_upload_session",
    )
    rej_id, _, _ = raw_insert_session()
    ok, _ = raw(
        org1_id,
        "UPDATE dataset_upload_sessions SET status='rejected', rejection_reason='x', finalized_at=now(), updated_at=now() WHERE id=:id",  # noqa: E501
        id=rej_id,
    )
    check("positive control: raw reject UPDATE succeeds", ok)
    expect(
        "a dataset on a REJECTED session fails the foreign key",
        raw_insert_dataset(rej_id),
        "fk_datasets_verified_upload_session",
    )
    exp_id, _, _ = raw_insert_session()
    ok, _ = raw(
        org1_id,
        "UPDATE dataset_upload_sessions SET status='expired', finalized_at=now(), updated_at=now() WHERE id=:id",  # noqa: E501
        id=exp_id,
    )
    check("positive control: raw expire UPDATE succeeds", ok)
    expect(
        "a dataset on an EXPIRED session fails the foreign key",
        raw_insert_dataset(exp_id),
        "fk_datasets_verified_upload_session",
    )

    partial_id, _, _ = raw_insert_session()
    expect(
        "verified with a partial size fails",
        raw_verify(partial_id, size=499),
        "ck_dataset_upload_sessions_verified_matches_declared",
    )
    oversize_id, _, _ = raw_insert_session()
    expect(
        "verified with an oversize upload fails",
        raw_verify(oversize_id, size=501),
        "ck_dataset_upload_sessions_verified_matches_declared",
    )
    hash_id, _, _ = raw_insert_session()
    expect(
        "verified with a hash mismatch fails",
        raw_verify(hash_id, sha=_sha("8")),
        "ck_dataset_upload_sessions_verified_matches_declared",
    )
    ctype_id, _, _ = raw_insert_session()
    expect(
        "verified with a content-type mismatch fails",
        raw_verify(ctype_id, ctype="text/csv"),
        "ck_dataset_upload_sessions_verified_matches_declared",
    )
    late_id, _, _ = raw_insert_session()
    expect(
        "verified finalized after expiry fails",
        raw_verify(late_id, finalized=now + timedelta(hours=2)),
        "ck_dataset_upload_sessions_verified_matches_declared",
    )
    nofin_id, _, _ = raw_insert_session()
    expect(
        "verified without finalized_at fails",
        raw_verify(nofin_id, finalized=None),
        "ck_dataset_upload_sessions",
    )
    rr_id, _, _ = raw_insert_session()
    expect(
        "rejected without a reason fails",
        raw(
            org1_id,
            "UPDATE dataset_upload_sessions SET status='rejected', finalized_at=now() WHERE id=:id",
            id=rr_id,
        ),
        "ck_dataset_upload_sessions_rejection_reason_iff_rejected",
    )
    expect(
        "a reason on a non-rejected session fails",
        raw(
            org1_id,
            "UPDATE dataset_upload_sessions SET rejection_reason='x' WHERE id=:id",
            id=rr_id,
        ),
        "ck_dataset_upload_sessions_rejection_reason_iff_rejected",
    )

    for column, value in (
        ("object_key", "tenants/x/projects/y/uploads/z/source"),
        ("declared_size_bytes", 1),
        ("declared_sha256", _sha("7")),
        ("declared_content_type", "text/csv"),
        ("max_bytes", 999),
        ("purpose", "validation"),
        ("expires_at", now + timedelta(hours=9)),
        ("dataset_source_connection_id", raw_conn),
        ("organization_id", org2_id),
        ("created_by", "someone"),
    ):
        expect(
            f"the app role cannot UPDATE {column}",
            raw(
                org1_id,
                f"UPDATE dataset_upload_sessions SET {column}=:v WHERE id=:id",
                v=value,
                id=open_id,
            ),
            "permission denied",
        )

    expect(
        "a session with a foreign object key is refused",
        raw_insert_session(key="tenants/other/projects/other/uploads/x/source")[1:],
        "ck_dataset_upload_sessions_object_key_shape",
    )
    expect(
        "a session over 10 GiB is refused by the database",
        raw_insert_session(size=10 * GIB + 1, max=10 * GIB + 1)[1:],
        "ck_dataset_upload_sessions_size_bounds",
    )
    expect(
        "a session with expiry beyond 24h is refused by the database",
        raw_insert_session(expires=now + timedelta(hours=25))[1:],
        "ck_dataset_upload_sessions_expiry_within_24h",
    )
    expect(
        "a session with an invalid sha256 is refused by the database",
        raw_insert_session(sha="XYZ")[1:],
        "ck_dataset_upload_sessions_declared_sha256",
    )
    expect(
        "a session with an unknown purpose is refused by the database",
        raw_insert_session(purpose="anything")[1:],
        "ck_dataset_upload_sessions_purpose",
    )
    expect(
        "a session inserted already verified is refused",
        raw_insert_session(status="verified")[1:],
        "a new upload session must be open",  # FTF-0501-P2 guard fires before the CHECKs
    )
    expect(
        "a session for a project of another organization is refused",
        raw_insert_session(project_id=p2_id)[1:],
        "fk_dataset_upload_sessions_organization_id_project_id",
    )

    good_id, _, _ = raw_insert_session()
    ok, message = raw_verify(good_id)
    check(f"positive control: a matching raw verify succeeds ({message[:120]})", ok)
    ok, message = raw_insert_dataset(good_id)
    check(f"positive control: a dataset on the verified session succeeds ({message[:120]})", ok)
    expect(
        "a second dataset on the same session fails (UNIQUE upload_session_id)",
        raw_insert_dataset(good_id),
        "uq_datasets_upload_session_id",
    )
    expect(
        "a dataset claiming upload_status 'open' fails",
        raw_insert_dataset(good_id, ustatus="open"),
        "ck_datasets_upload_verified",
    )

    def fresh_verified() -> uuid.UUID:
        new_id, _, _ = raw_insert_session()
        raw_verify(new_id)
        return new_id

    # Fresh verified sessions (no dataset yet), so the UNIQUE key cannot be the
    # constraint that fires: only the composite foreign key can refuse these.
    expect(
        "a dataset with a different hash than the session fails",
        raw_insert_dataset(fresh_verified(), sha=_sha("6")),
        "fk_datasets_verified_upload_session",
    )
    expect(
        "a dataset with a different size than the session fails",
        raw_insert_dataset(fresh_verified(), size=501),
        "fk_datasets_verified_upload_session",
    )
    expect(
        "a dataset with a different purpose than the session fails",
        raw_insert_dataset(fresh_verified(), purpose="validation"),
        "fk_datasets_verified_upload_session",
    )
    ok, message = raw_insert_dataset(fresh_verified())
    check(
        f"positive control: a matching dataset on a fresh verified session succeeds ({message[:100]})",  # noqa: E501
        ok,
    )
    expect(
        "a dataset with version 0 fails",
        raw_insert_dataset(uuid.uuid4(), version=0),
        "ck_datasets_version_min",
    )
    expect(
        "a dataset with a blank name fails",
        raw_insert_dataset(uuid.uuid4(), name=""),
        "ck_datasets_name_not_blank",
    )

    for column, value in (
        ("status", "open"),
        ("verified_sha256", _sha("5")),
        ("verified_size_bytes", 1),
        ("finalized_at", now),
        ("rejection_reason", "x"),
    ):
        ok, message = raw(
            org1_id,
            f"UPDATE dataset_upload_sessions SET {column}=:v WHERE id=:id",
            v=value,
            id=good_id,
        )
        check(
            f"a verified session a dataset references cannot be modified ({column}; got {message[:120]!r})",  # noqa: E501
            not ok and "a verified upload session is immutable" in message,
        )
    expect(
        "even touching only updated_at on a verified session is refused",
        raw(
            org1_id, "UPDATE dataset_upload_sessions SET updated_at=now() WHERE id=:id", id=good_id
        ),
        "a verified upload session is immutable",
    )
    ok, message = raw(
        org1_id, "UPDATE dataset_upload_sessions SET updated_at=now() WHERE id=:id", id=open_id
    )
    check(
        f"positive control: touching updated_at on an OPEN session is allowed ({message[:100]})", ok
    )
    for table in ("datasets", "dataset_upload_sessions"):
        expect(
            f"the app role cannot UPDATE {table}.organization_id",
            raw(org1_id, f"UPDATE {table} SET organization_id=:o WHERE true", o=org2_id),
            "permission denied",
        )
        expect(
            f"the app role cannot DELETE from {table}",
            raw(org1_id, f"DELETE FROM {table}"),
            "permission denied",
        )
    expect(
        "the app role cannot UPDATE datasets at all",
        raw(org1_id, "UPDATE datasets SET name='renamed' WHERE true"),
        "permission denied",
    )

    cross_id, ok, message = raw_insert_session(org=org2_id, project_id=p2_id, conn=org2_conn)
    check(f"positive control: org2 can insert its own session ({message[:100]})", ok)
    ok, message = raw(
        org2_id,
        "INSERT INTO dataset_upload_sessions (id, organization_id, project_id, dataset_source_connection_id, purpose, object_key, declared_size_bytes, max_bytes, declared_sha256, declared_content_type, status, created_by, created_at, expires_at, updated_at) VALUES (gen_random_uuid(), :o1, :p1, :c, 'training', 'x', 1, 1, :sha, 'a/b', 'open', 'x', now(), now(), now())",  # noqa: E501
        o1=org1_id,
        p1=p1_id,
        c=raw_conn,
        sha=_sha("a"),
    )
    check(
        f"org2 context cannot INSERT a row for org1 (RLS; got {message[:120]!r})",
        not ok and "row-level security" in message,
    )
    ok, message = raw(
        org2_id,
        "INSERT INTO datasets (id, organization_id, project_id, name, version, purpose, upload_session_id, upload_status, verified_sha256, verified_size_bytes, created_by) VALUES (gen_random_uuid(), :o1, :p1, 'x', 1, 'training', :s, 'verified', :sha, 500, 'x')",  # noqa: E501
        o1=org1_id,
        p1=p1_id,
        s=good_id,
        sha=_sha("9"),
    )
    check(
        f"org2 context cannot INSERT a dataset for org1 (RLS; got {message[:120]!r})",
        not ok and "row-level security" in message,
    )
    with app_engine.begin() as conn:
        conn.execute(
            text("SELECT set_config('app.current_organization_id', :o, true)"), {"o": str(org2_id)}
        )
        seen = conn.execute(
            text("SELECT count(*) FROM dataset_upload_sessions WHERE organization_id=:o1"),
            {"o1": org1_id},
        ).scalar()
        seen_ds = conn.execute(
            text("SELECT count(*) FROM datasets WHERE organization_id=:o1"), {"o1": org1_id}
        ).scalar()
    check("org2 context sees no org1 session or dataset rows", seen == 0 and seen_ds == 0)
    with app_engine.begin() as conn:
        unset = conn.execute(text("SELECT count(*) FROM dataset_upload_sessions")).scalar()
    check("with no tenant context the app role sees no session rows", unset == 0)
    del cross_id

    # ---- read routes on a real server ----------------------------------------------
    held_session = open_session(purpose="held_out_evaluation", sha=_sha("a"), at=fresh())
    held, error = finalize(held_session, name="held-set")
    check(f"seed: a held_out_evaluation dataset exists ({error!r})", error is None)
    if error is not None:
        _finish()
    p2_session = run_store(
        ctx2,
        lambda s: store.create_upload_session(
            s,
            ctx2,
            project_id=p2_id,
            connection_id=org2_conn,
            purpose="training",
            declared_size_bytes=5,
            declared_sha256=_sha("a"),
            declared_content_type="a/b",
            max_bytes=5,
            actor=actor,
        ),
    )[0]
    org2_dataset, error = run_store(
        ctx2,
        lambda s: store.record_upload_verified_and_create_dataset(
            s,
            ctx2,
            project_id=p2_id,
            upload_session_id=p2_session.id,
            dataset_name="g",
            verified_size_bytes=5,
            verified_sha256=_sha("a"),
            verified_content_type="a/b",
            actor=actor,
        ),
    )
    check(f"seed: an org2 dataset exists ({error!r})", error is None)

    fixture = _Fixture({"org1": org1_id, "org2": org2_id})
    app = FastAPI()
    app.include_router(
        build_dataset_router(
            factory, principal_resolver=fixture.principal_resolver, role_lookup=fixture.role_lookup
        )
    )
    mle = fixture.header_for("org1", Role.ML_ENGINEER)
    admin = fixture.header_for("org1", Role.WORKSPACE_ADMIN)
    viewer = fixture.header_for("org1", Role.VIEWER)
    org2_mle = fixture.header_for("org2", Role.ML_ENGINEER)

    def collection(org: uuid.UUID, project_id: uuid.UUID) -> str:
        return f"/api/v1/organizations/{org}/projects/{project_id}/datasets"

    with _running_app(app) as base:
        check("unauthenticated list is 401", _get(base, collection(org1_id, p1_id)).status == 401)
        check(
            "a viewer is denied (403)", _get(base, collection(org1_id, p1_id), viewer).status == 403
        )
        listing = _get(base, collection(org1_id, p1_id), mle)
        check("ml_engineer can list (200)", listing.status == 200)
        listed = listing.json()["items"] if listing.status == 200 else []
        listed_names = {item["name"] for item in listed}
        check("the list contains the verified datasets", {"support", "other-name"} <= listed_names)
        check(
            "ml_engineer's list never contains a held_out_evaluation dataset",
            all(item["purpose"] != "held_out_evaluation" for item in listed)
            and "held-set" not in listed_names,
        )
        admin_items = _get(base, collection(org1_id, p1_id), admin).json()["items"]
        check(
            "workspace_admin's list does contain the held_out_evaluation dataset",
            any(item["id"] == str(held.id) for item in admin_items),
        )
        check(
            "a list never contains another organization's dataset",
            all(item["organization_id"] == str(org1_id) for item in admin_items)
            and not any(item["id"] == str(org2_dataset.id) for item in admin_items),
        )

        held_mle = _get(base, f"{collection(org1_id, p1_id)}/{held.id}", mle)
        random_mle = _get(base, f"{collection(org1_id, p1_id)}/{uuid.uuid4()}", mle)
        check(
            "ml_engineer reading a held-out dataset gets 404 identical to a random id",
            held_mle.status == 404
            and held_mle.body == random_mle.body
            and held_mle.json() == {"detail": "not_found"},
        )
        held_admin = _get(base, f"{collection(org1_id, p1_id)}/{held.id}", admin)
        check(
            "workspace_admin can read the held-out dataset",
            held_admin.status == 200 and held_admin.json()["id"] == str(held.id),
        )
        one = _get(base, f"{collection(org1_id, p1_id)}/{d1.id}", mle)
        check(
            "get returns the dataset version",
            one.status == 200
            and one.json()["version"] == 1
            and one.json()["verified_sha256"] == d1.verified_sha256,
        )
        for response in (listing, one, held_admin):
            body = response.body.decode()
            check(
                "no response carries an object key, bucket or storage URI",
                all(
                    token not in body
                    for token in (
                        "object_key",
                        "tenants/",
                        "artifact_uri",
                        "bucket",
                        "s3://",
                        "presign",
                    )
                ),
            )
        check(
            "a dataset of another project is 404",
            _get(base, f"{collection(org1_id, p1b_id)}/{d1.id}", mle).status == 404,
        )
        check(
            "org2 reading org1's dataset through its own org path is 404 identical to random",
            _get(base, f"{collection(org2_id, p2_id)}/{d1.id}", org2_mle).body
            == _get(base, f"{collection(org2_id, p2_id)}/{uuid.uuid4()}", org2_mle).body,
        )
        cross = _get(base, f"{collection(org1_id, p1_id)}/{d1.id}", org2_mle)
        check(
            "an org2 principal on org1's path gets no data",
            cross.status in (403, 404) and "verified_sha256" not in cross.body.decode(),
        )
        check(
            "org2 listing org1's project is 404",
            _get(base, collection(org2_id, p1_id), org2_mle).status == 404,
        )
        check(
            "a missing project lists as 404",
            _get(base, collection(org1_id, uuid.uuid4()), mle).status == 404,
        )

        for query, code in (
            ("limit=0", "invalid_limit"),
            ("limit=201", "invalid_limit"),
            ("limit=abc", "invalid_limit"),
            ("after=nope", "invalid_after"),
        ):
            response = _get(base, f"{collection(org1_id, p1_id)}?{query}", mle)
            check(
                f"?{query} is 400 {code}",
                response.status == 400 and response.json() == {"detail": code},
            )
        page1 = _get(base, f"{collection(org1_id, p1_id)}?limit=2", mle).json()
        check(
            "limit=2 returns two items and a next_after cursor",
            len(page1["items"]) == 2 and page1["next_after"] == page1["items"][-1]["id"],
        )
        page2 = _get(
            base, f"{collection(org1_id, p1_id)}?limit=2&after={page1['next_after']}", mle
        ).json()
        check(
            "the next page continues after the cursor with no overlap",
            page2["items"] and all(item["id"] > page1["next_after"] for item in page2["items"]),
        )
        everything = _get(base, f"{collection(org1_id, p1_id)}?limit=200", mle).json()
        check(
            "a limit larger than the result set returns no cursor", everything["next_after"] is None
        )

    _finish()


def _session_status(engine: Any, session_id: uuid.UUID) -> str | None:
    with engine.connect() as conn:
        return conn.execute(
            text("SELECT status FROM dataset_upload_sessions WHERE id=:id"), {"id": session_id}
        ).scalar()


def _check_rls_catalog(owner_engine: Any) -> None:
    with owner_engine.connect() as conn:
        for table in ("dataset_upload_sessions", "datasets"):
            enabled, forced = conn.execute(
                text("SELECT relrowsecurity, relforcerowsecurity FROM pg_class WHERE relname=:t"),
                {"t": table},
            ).one()
            check(f"{table} has ENABLE and FORCE row-level security", bool(enabled and forced))
            policies = conn.execute(
                text(
                    "SELECT polname, pg_get_expr(polqual, polrelid) FROM pg_policy WHERE polrelid = CAST(:t AS regclass)"  # noqa: E501
                ),
                {"t": table},
            ).all()
            check(
                f"{table} has a tenant_isolation policy that is not allow-all",
                len(policies) == 1
                and policies[0][0] == "tenant_isolation"
                and "app.current_organization_id" in str(policies[0][1])
                and str(policies[0][1]).strip().lower() != "true",
            )
            for privilege, expected in (("SELECT", True), ("INSERT", True), ("DELETE", False)):
                granted = conn.execute(
                    text("SELECT has_table_privilege('sovra_app', :t, :p)"),
                    {"t": table, "p": privilege},
                ).scalar()
                check(f"sovra_app {privilege} on {table} is {expected}", bool(granted) is expected)
        check(
            "sovra_app has no table-level UPDATE on datasets",
            not conn.execute(
                text("SELECT has_table_privilege('sovra_app','datasets','UPDATE')")
            ).scalar(),
        )
        check(
            "sovra_app has no table-level UPDATE on dataset_upload_sessions (column-limited only)",
            not conn.execute(
                text("SELECT has_table_privilege('sovra_app','dataset_upload_sessions','UPDATE')")
            ).scalar(),
        )


def _finish() -> None:
    if FAILURES:
        print(f"FTF-0501-P1 dataset upload real proof: {len(FAILURES)} failed")
        for failure in FAILURES:
            print(f"  {failure}")
        sys.exit(1)
    print("FTF-0501-P1 dataset upload real proof: all checks passed")
    sys.exit(0)


if __name__ == "__main__":
    main()
