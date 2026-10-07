#!/usr/bin/env python3
"""FTF-0501-P1 -- Dataset version + upload-session persistence and read API
(no-DB tests; the live Postgres evidence is
`tests/integration/test_ftf_0501_p1_dataset_uploads.py`, run by CI's
`dataset-upload-integration` job).

Without any database this suite proves:

M  the tracked migration `0024_dataset_uploads.py`: chained onto
   `0023_evaluation_cases` as the single head, ENABLE + FORCE RLS and a
   non-allow-all `tenant_isolation` policy on both tables, the grants
   (sessions: SELECT/INSERT + column-limited UPDATE; datasets: SELECT/INSERT;
   never DELETE), every CHECK / foreign key / unique key, and the
   verified-session freeze trigger -- each shown able to fail by editing the
   file on disk;
S  the store over a real SQLite session (only the Postgres tenant guard, the
   audit writer and the FTF-0500 authorization/project seams are replaced):
   lifecycle, every refusal writes nothing, the session UPDATE reaches the
   database before the dataset INSERT, versions count up per (project, name),
   the version-conflict mapping only fires for the version constraint;
R  the two read routes on a real uvicorn server with the store patched:
   roles, held-out hiding, 404 bodies, limit / after parsing, pagination, the
   exact response shape and the absence of any storage field;
C  the interface contract (05-interface.yaml) and the app wiring.

Run directly: python3 tests/tasks/test_ftf_0501_p1.py
"""

from __future__ import annotations

import contextlib
import http.client
import json
import os
import re
import socket
import subprocess
import sys
import threading
import time
import urllib.parse
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import uvicorn
import yaml
from fastapi import FastAPI
from sqlalchemy import create_engine, event
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "apps" / "api"))

from app.auth.rbac_middleware import AuthenticatedPrincipal, Role  # noqa: E402
from app.datasets import dataset_routes as routes  # noqa: E402
from app.datasets import dataset_upload_store as store  # noqa: E402
from app.datasets.dataset_source_connection_crud import (  # noqa: E402
    DatasetSourceAuthorizationError,
    DatasetSourceConnectionNotFoundError,
)

FAILURES: list[str] = []
CID = "FTF-0501-P1"
GIB = 1024**3
SHA_A = "a" * 64
SHA_B = "b" * 64


def check(criterion_id: str, description: str, condition: bool) -> None:
    if not condition:
        FAILURES.append(f"FAIL [{criterion_id}] {description}")


MIGRATION_PATH = ROOT / "apps" / "api" / "alembic" / "versions" / "0024_dataset_uploads.py"
INTERFACE_PATH = ROOT / "packages" / "contracts" / "openapi" / "05-interface.yaml"
MAIN_PATH = ROOT / "apps" / "api" / "app" / "main.py"
_NOW = datetime(2026, 10, 7, 12, 0, 0, tzinfo=UTC)


def _raises(fn: Any, error: type[BaseException]) -> bool:
    try:
        fn()
    except error:
        return True
    except Exception:  # noqa: BLE001 - a different error is not the expected refusal
        return False
    return False


# -- M: migration shape --------------------------------------------------------


def _migration() -> str:
    return MIGRATION_PATH.read_text()


def _migration_findings(src: str) -> list[str]:
    """Every shape rule the migration must satisfy; returns the broken ones."""
    broken: list[str] = []

    def need(label: str, ok: bool) -> None:
        if not ok:
            broken.append(label)

    need("revision id", 'revision = "0024_dataset_uploads"' in src)
    need("chained onto 0023_evaluation_cases", 'down_revision = "0023_evaluation_cases"' in src)
    need("revision id fits alembic_version (<=32)", len("0024_dataset_uploads") <= 32)
    need("ENABLE rls", 'f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY"' in src)
    need("FORCE rls", 'f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY"' in src)
    need("tenant policy on both", 'for table in ("dataset_upload_sessions", "datasets"):' in src)
    need("policy name", "CREATE POLICY tenant_isolation ON {table} FOR ALL" in src)
    need("policy using", "USING (organization_id = {tenant_setting})" in src)
    need("policy with check", "WITH CHECK (organization_id = {tenant_setting})" in src)
    need("not allow-all", "USING (true)" not in src and "WITH CHECK (true)" not in src)
    need("sessions grant", 'GRANT SELECT, INSERT ON dataset_upload_sessions TO "{APP_ROLE_NAME}"' in src)
    need("datasets grant", 'GRANT SELECT, INSERT ON datasets TO "{APP_ROLE_NAME}"' in src)
    need(
        "column-limited UPDATE",
        "GRANT UPDATE ({_SESSION_UPDATE_COLUMNS}) ON dataset_upload_sessions" in src
        and '_SESSION_UPDATE_COLUMNS = (\n    "status, verified_size_bytes, verified_sha256, '
        'verified_content_type, "\n    "finalized_at, rejection_reason, updated_at"\n)' in src,
    )
    need("no DELETE grant", "GRANT DELETE" not in src and ", DELETE" not in src.split("def downgrade")[0])
    need("no table-wide UPDATE grant", "GRANT SELECT, INSERT, UPDATE" not in src and "GRANT UPDATE ON" not in src)
    need("10 GiB bound", "_MAX_UPLOAD_BYTES = 10 * 1024**3" in src)
    need(
        "purpose vocabulary",
        "('training','validation','held_out_evaluation','candidate_development','baseline_only')" in src,
    )
    need(
        "object key derived from ids",
        "object_key = 'tenants/' || organization_id::text || '/projects/' || "
        "project_id::text || '/uploads/' || id::text || '/source'" in src
        or ("\"object_key = 'tenants/' || organization_id::text" in src and "'/uploads/' || id::text || '/source'" in src),
    )
    need("size bounds", "declared_size_bytes > 0 AND declared_size_bytes <= max_bytes" in src and "AND max_bytes <= {_MAX_UPLOAD_BYTES}" in src)
    need("24h expiry", "expires_at <= created_at + interval '24 hours'" in src)
    need("sha256 shape", "declared_sha256 ~ '^[0-9a-f]{64}$'" in src)
    need(
        "verified matches declared",
        "verified_size_bytes = declared_size_bytes" in src
        and "AND verified_sha256 = declared_sha256" in src
        and "AND verified_content_type = declared_content_type" in src
        and "finalized_at IS NOT NULL AND finalized_at <= expires_at" in src,
    )
    need("rejection reason iff rejected", "(status = 'rejected') = (rejection_reason IS NOT NULL)" in src)
    need("closed sessions have finalized_at", "status = 'open' OR finalized_at IS NOT NULL" in src)
    need("status vocabulary", "status in ('open','verified','rejected','expired')" in src)
    need("dataset verified check", "upload_status = 'verified'" in src)
    need("dataset version min", '"version >= 1"' in src)
    need("dataset name not blank", "\"name <> ''\"" in src)
    need("dataset unique version", '"project_id", "name", "version", name="uq_datasets_project_name_version"' in src)
    need("one dataset per session", '"upload_session_id", name="uq_datasets_upload_session_id"' in src)
    need("composite verified foreign key", 'name="fk_datasets_verified_upload_session"' in src)
    need("fk covers status/hash/size", '"dataset_upload_sessions.status"' in src and '"dataset_upload_sessions.verified_sha256"' in src and '"dataset_upload_sessions.verified_size_bytes"' in src and '"dataset_upload_sessions.purpose"' in src)
    need("connection fk is RESTRICT", 'ondelete="RESTRICT",\n            name="fk_dataset_upload_sessions_connection"' in src)
    need("project composite fk", 'name="fk_dataset_upload_sessions_organization_id_project_id"' in src)
    need("freeze trigger created", "CREATE TRIGGER tr_dataset_upload_sessions_freeze_verified" in src and "BEFORE UPDATE ON dataset_upload_sessions" in src)
    need("freeze function refuses verified", "IF OLD.status = 'verified' THEN " in src and "a verified upload session is immutable" in src)
    need("downgrade drops trigger", "DROP TRIGGER IF EXISTS tr_dataset_upload_sessions_freeze_verified" in src and "DROP FUNCTION IF EXISTS dataset_upload_sessions_freeze_verified()" in src)
    need("downgrade revokes + drops policy", 'REVOKE ALL ON {table} FROM "{APP_ROLE_NAME}"' in src and "DROP POLICY IF EXISTS tenant_isolation ON {table}" in src)
    need(
        "downgrade drops datasets before sessions",
        src.index('op.drop_table("datasets")') < src.index('op.drop_table("dataset_upload_sessions")'),
    )
    need("connection unique key added and removed", "uq_dataset_source_connections_org_project_id" in src and src.count("uq_dataset_source_connections_org_project_id") >= 2)
    return broken


def test_m1_migration_shape() -> None:
    broken = _migration_findings(_migration())
    check(CID, f"the migration satisfies every shape rule (broken: {broken})", not broken)


def test_m1_fault_injection_against_the_file_on_disk() -> None:
    src = _migration()
    faults = {
        "drop FORCE RLS": ('f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY"', '"SELECT 1"'),
        "drop ENABLE RLS": ('f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY"', '"SELECT 1"'),
        "allow-all policy": ("USING (organization_id = {tenant_setting})", "USING (true)"),
        "allow-all check": ("WITH CHECK (organization_id = {tenant_setting})", "WITH CHECK (true)"),
        "grant DELETE on datasets": ('GRANT SELECT, INSERT ON datasets', "GRANT SELECT, INSERT, DELETE ON datasets"),
        "grant UPDATE on datasets": ('GRANT SELECT, INSERT ON datasets', "GRANT SELECT, INSERT, UPDATE ON datasets"),
        "widen session UPDATE columns": ('"status, verified_size_bytes,', '"object_key, status, verified_size_bytes,'),
        "drop the 24h expiry check": ("expires_at <= created_at + interval '24 hours'", "true"),
        "drop the verified-matches check": ("verified_size_bytes = declared_size_bytes", "true"),
        "drop the datasets verified check": ("upload_status = 'verified'", "true"),
        "drop the freeze trigger": ("CREATE TRIGGER tr_dataset_upload_sessions_freeze_verified", "-- CREATE TRIGGER x"),
        "let the freeze function pass": ("IF OLD.status = 'verified' THEN ", "IF false THEN "),
        "wrong parent revision": ('down_revision = "0023_evaluation_cases"', 'down_revision = "0022_model_catalog_entries"'),
        "drop the dataset FK to the verified identity": ('name="fk_datasets_verified_upload_session"', 'name="fk_datasets_other"'),
        "cascade the connection fk": ('ondelete="RESTRICT",\n            name="fk_dataset_upload_sessions_connection"', 'ondelete="CASCADE",\n            name="fk_dataset_upload_sessions_connection"'),
    }
    for label, (old, new) in faults.items():
        check(CID, f"fault {label!r}: the pattern exists in the file", old in src)
        check(
            CID,
            f"fault {label!r} is caught by the shape rules",
            bool(_migration_findings(src.replace(old, new, 1))),
        )


def _offline_sql(revision_range: str, action: str) -> str:
    env = {**os.environ, "SOVRA_DATABASE_URL": "postgresql+psycopg://x:y@localhost:5432/z"}
    result = subprocess.run(
        [sys.executable, "-m", "alembic", action, revision_range, "--sql"],
        cwd=str(ROOT / "apps" / "api"),
        capture_output=True,
        text=True,
        env=env,
    )
    check(CID, f"alembic {action} {revision_range} --sql exits 0 ({result.stderr[-200:]})", result.returncode == 0)
    return result.stdout


def test_m2_migration_generates_the_expected_sql_offline() -> None:
    up = _offline_sql("0023_evaluation_cases:0024_dataset_uploads", "upgrade")
    for needle in (
        "CREATE TABLE dataset_upload_sessions",
        "CREATE TABLE datasets",
        "ALTER TABLE dataset_upload_sessions ENABLE ROW LEVEL SECURITY",
        "ALTER TABLE dataset_upload_sessions FORCE ROW LEVEL SECURITY",
        "ALTER TABLE datasets ENABLE ROW LEVEL SECURITY",
        "ALTER TABLE datasets FORCE ROW LEVEL SECURITY",
        'GRANT SELECT, INSERT ON datasets TO "sovra_app"',
        'GRANT SELECT, INSERT ON dataset_upload_sessions TO "sovra_app"',
        'GRANT UPDATE (status, verified_size_bytes, verified_sha256, verified_content_type, finalized_at, rejection_reason, updated_at) ON dataset_upload_sessions TO "sovra_app"',
        "CREATE TRIGGER tr_dataset_upload_sessions_freeze_verified",
        "UPDATE alembic_version SET version_num='0024_dataset_uploads'",
    ):
        check(CID, f"the upgrade SQL contains: {needle[:80]}", needle in up)
    check(
        CID,
        "the upgrade creates exactly two tenant_isolation policies and never grants DELETE",
        up.count("CREATE POLICY tenant_isolation ON") == 2 and "GRANT DELETE" not in up and "USING (true)" not in up,
    )
    down = _offline_sql("0024_dataset_uploads:0023_evaluation_cases", "downgrade")
    for needle in (
        "DROP TRIGGER IF EXISTS tr_dataset_upload_sessions_freeze_verified ON dataset_upload_sessions",
        "DROP FUNCTION IF EXISTS dataset_upload_sessions_freeze_verified()",
        "DROP TABLE datasets",
        "DROP TABLE dataset_upload_sessions",
        'REVOKE ALL ON datasets FROM "sovra_app"',
        "UPDATE alembic_version SET version_num='0023_evaluation_cases'",
    ):
        check(CID, f"the downgrade SQL contains: {needle[:80]}", needle in down)
    check(
        CID,
        "the downgrade drops datasets before the sessions they reference",
        down.index("DROP TABLE datasets") < down.index("DROP TABLE dataset_upload_sessions"),
    )


def test_m3_single_alembic_head() -> None:
    versions = MIGRATION_PATH.parent
    parents: dict[str, str | None] = {}
    for path in versions.glob("0*.py"):
        text_ = path.read_text()
        rev = re.search(r'^revision\s*=\s*"([^"]+)"', text_, re.M)
        down = re.search(r'^down_revision\s*=\s*(?:"([^"]+)"|None)', text_, re.M)
        if rev:
            parents[rev.group(1)] = down.group(1) if down else None
    heads = set(parents) - {p for p in parents.values() if p}
    check(CID, f"exactly one Alembic head and it is 0024_dataset_uploads (heads: {sorted(heads)})", heads == {"0024_dataset_uploads"})


# -- S: store over SQLite ------------------------------------------------------


class _Audit:
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    def __call__(self, session: Session, **kwargs: Any) -> None:
        self.events.append(kwargs)

    def actions(self) -> list[str]:
        return [event_["action"] for event_ in self.events]


def _passthrough_execute(session: Session, organization_id: uuid.UUID, statement: Any) -> Any:
    return session.execute(statement)


_ORG = uuid.uuid4()
_PROJECT = uuid.uuid4()
_OTHER_PROJECT = uuid.uuid4()
_CONN = uuid.uuid4()
_CTX: Any = SimpleNamespace(organization_id=_ORG)
_ACTOR = store.UploadActor(actor_type="user", actor_id="user-1", request_id="req-1")


class _World:
    """The FTF-0500 seams: which connection exists, its type, status, purposes."""

    def __init__(self) -> None:
        self.source_type = "upload"
        self.purposes = ["training", "held_out_evaluation"]
        self.status = "active"
        self.missing = False
        self.known_projects = {_PROJECT, _OTHER_PROJECT}

    def authorize(self, session: Any, context: Any, project_id: Any, connection_id: Any, purpose: str) -> Any:
        if self.missing:
            raise DatasetSourceConnectionNotFoundError("missing")
        if self.status != "active" or purpose not in self.purposes:
            raise DatasetSourceAuthorizationError("denied")
        return SimpleNamespace(id=connection_id, source_type=self.source_type)

    def authorize_project(self, session: Any, context: Any, project_id: Any) -> None:
        if project_id not in self.known_projects:
            raise store.ProjectNotFoundError("project")


@contextlib.contextmanager
def _store_env():
    engine = create_engine("sqlite://")
    store.DatasetUploadSession.__table__.create(engine)
    store.Dataset.__table__.create(engine)
    statements: list[str] = []

    @event.listens_for(engine, "before_cursor_execute")
    def _record(conn: Any, cursor: Any, statement: str, *rest: Any) -> None:
        statements.append(statement)

    audit = _Audit()
    world = _World()
    saved = (
        store.execute_tenant_scoped,
        store.write_audit_event,
        store.authorize_dataset_source_purpose,
        store._authorize_project_or_raise,
    )
    store.execute_tenant_scoped = _passthrough_execute
    store.write_audit_event = audit
    store.authorize_dataset_source_purpose = world.authorize
    store._authorize_project_or_raise = world.authorize_project
    try:
        with Session(engine) as session:
            yield session, audit, world, statements
    finally:
        (
            store.execute_tenant_scoped,
            store.write_audit_event,
            store.authorize_dataset_source_purpose,
            store._authorize_project_or_raise,
        ) = saved
        engine.dispose()


def _create(session: Session, **over: Any) -> store.DatasetUploadSession:
    args: dict[str, Any] = {
        "project_id": _PROJECT,
        "connection_id": _CONN,
        "purpose": "training",
        "declared_size_bytes": 1000,
        "declared_sha256": SHA_A,
        "declared_content_type": "application/jsonl",
        "max_bytes": 2000,
        "now": _NOW,
        "actor": _ACTOR,
    }
    args.update(over)
    return store.create_upload_session(session, _CTX, **args)


def _finalize(session: Session, record: store.DatasetUploadSession, **over: Any) -> store.Dataset:
    args: dict[str, Any] = {
        "project_id": record.project_id,
        "upload_session_id": record.id,
        "dataset_name": "support",
        "verified_size_bytes": record.declared_size_bytes,
        "verified_sha256": record.declared_sha256,
        "verified_content_type": record.declared_content_type,
        "now": _NOW + timedelta(minutes=10),
        "actor": _ACTOR,
    }
    args.update(over)
    return store.record_upload_verified_and_create_dataset(session, _CTX, **args)


def _sessions(session: Session) -> list[store.DatasetUploadSession]:
    return list(session.query(store.DatasetUploadSession).all())


def _datasets(session: Session) -> list[store.Dataset]:
    return list(session.query(store.Dataset).all())


def test_s1_create_session_shape_audit_and_defaults() -> None:
    with _store_env() as (session, audit, _world, _statements):
        record = _create(session)
        check(CID, "a created session is open", record.status == "open")
        check(
            CID,
            "the object key is derived from the row's own ids",
            record.object_key == f"tenants/{_ORG}/projects/{_PROJECT}/uploads/{record.id}/source",
        )
        check(CID, "organization, project and connection are recorded", record.organization_id == _ORG and record.project_id == _PROJECT and record.dataset_source_connection_id == _CONN)
        check(CID, "declared values are recorded exactly", record.declared_size_bytes == 1000 and record.max_bytes == 2000 and record.declared_sha256 == SHA_A and record.declared_content_type == "application/jsonl" and record.purpose == "training")
        check(CID, "created_by is the actor id, created/updated are `now`", record.created_by == "user-1" and record.created_at == _NOW and record.updated_at == _NOW)
        check(CID, "the default lifetime is exactly 24 hours", record.expires_at == _NOW + timedelta(hours=24))
        check(CID, "nothing verified is recorded yet", record.verified_sha256 is None and record.verified_size_bytes is None and record.verified_content_type is None and record.finalized_at is None and record.rejection_reason is None)
        check(CID, "exactly one audit event, exact fields", audit.actions() == ["dataset_upload_session.create"])
        event_ = audit.events[0]
        check(
            CID,
            "the audit event carries the actor, resource and details but never the object key",
            event_["organization_id"] == _ORG
            and event_["actor_type"] == "user"
            and event_["actor_id"] == "user-1"
            and event_["resource_type"] == "dataset_upload_session"
            and event_["resource_id"] == str(record.id)
            and event_["request_id"] == "req-1"
            and event_["details"]
            == {
                "project_id": str(_PROJECT),
                "dataset_source_connection_id": str(_CONN),
                "purpose": "training",
                "declared_size_bytes": 1000,
            }
            and "tenants/" not in json.dumps(event_["details"]),
        )
        short = _create(session, ttl=timedelta(minutes=5))
        check(CID, "an explicit shorter ttl is honoured", short.expires_at == _NOW + timedelta(minutes=5))
        edge = _create(session, ttl=timedelta(hours=24))
        check(CID, "a ttl of exactly 24 hours is allowed", edge.expires_at == _NOW + timedelta(hours=24))
        ten = _create(session, declared_size_bytes=10 * GIB, max_bytes=10 * GIB)
        check(CID, "exactly 10 GiB is allowed", ten.declared_size_bytes == 10 * GIB)
        one = _create(session, declared_size_bytes=1, max_bytes=1)
        check(CID, "a one-byte upload is allowed", one.declared_size_bytes == 1)
        equal = _create(session, declared_size_bytes=7, max_bytes=7)
        check(CID, "declared == max_bytes is allowed", equal.max_bytes == 7)
        stamp = store.create_upload_session(
            session, _CTX, project_id=_PROJECT, connection_id=_CONN, purpose="training",
            declared_size_bytes=5, declared_sha256=SHA_A, declared_content_type="a/b",
            max_bytes=5, actor=_ACTOR,
        )
        check(CID, "without `now` the session is stamped with the current time", abs((stamp.created_at - datetime.now(UTC)).total_seconds()) < 60 and stamp.created_at.tzinfo is not None)
        got = store.get_upload_session(session, _CTX, _PROJECT, record.id)
        check(CID, "get_upload_session returns the row", got.id == record.id)


def test_s2_create_refusals_write_nothing() -> None:
    cases: list[tuple[str, dict[str, Any], type[BaseException], Any]] = [
        ("zero size", {"declared_size_bytes": 0, "max_bytes": 10}, ValueError, None),
        ("negative size", {"declared_size_bytes": -1, "max_bytes": 10}, ValueError, None),
        ("size above max_bytes", {"declared_size_bytes": 11, "max_bytes": 10}, ValueError, None),
        ("over 10 GiB", {"declared_size_bytes": 10 * GIB + 1, "max_bytes": 10 * GIB + 1}, ValueError, None),
        ("max_bytes over 10 GiB", {"declared_size_bytes": 5, "max_bytes": 10 * GIB + 1}, ValueError, None),
        ("boolean size", {"declared_size_bytes": True, "max_bytes": 10}, ValueError, None),
        ("float size", {"declared_size_bytes": 5.0, "max_bytes": 10}, ValueError, None),
        ("boolean max", {"declared_size_bytes": 1, "max_bytes": True}, ValueError, None),
        ("uppercase sha", {"declared_sha256": "A" * 64}, ValueError, None),
        ("short sha", {"declared_sha256": "a" * 63}, ValueError, None),
        ("long sha", {"declared_sha256": "a" * 65}, ValueError, None),
        ("sha with trailing newline", {"declared_sha256": "a" * 64 + "\n"}, ValueError, None),
        ("non-string sha", {"declared_sha256": 5}, ValueError, None),
        ("blank content type", {"declared_content_type": "  "}, ValueError, None),
        ("padded content type", {"declared_content_type": " a/b"}, ValueError, None),
        ("empty content type", {"declared_content_type": ""}, ValueError, None),
        ("long content type", {"declared_content_type": "x" * 256}, ValueError, None),
        ("non-string content type", {"declared_content_type": None}, ValueError, None),
        ("zero ttl", {"ttl": timedelta(0)}, ValueError, None),
        ("negative ttl", {"ttl": timedelta(seconds=-1)}, ValueError, None),
        ("ttl over 24h", {"ttl": timedelta(hours=24, seconds=1)}, ValueError, None),
        ("non-timedelta ttl", {"ttl": 60}, ValueError, None),
        ("unknown purpose", {"purpose": "anything"}, ValueError, None),
        ("naive now", {"now": datetime(2026, 1, 1)}, ValueError, None),
        ("non-datetime now", {"now": "2026-01-01"}, ValueError, None),
    ]
    for label, over, error, _extra in cases:
        with _store_env() as (session, audit, _world, _statements):
            check(CID, f"create refuses {label}", _raises(lambda o=over: _create(session, **o), error))
            check(CID, f"create refusing {label} wrote nothing", not _sessions(session) and not audit.events)
    with _store_env() as (session, audit, world, _statements):
        world.purposes = ["validation"]
        check(CID, "an ungranted purpose is refused (Law 16)", _raises(lambda: _create(session), DatasetSourceAuthorizationError))
        world.purposes = ["training"]
        world.status = "revoked"
        check(CID, "a revoked connection is refused", _raises(lambda: _create(session), DatasetSourceAuthorizationError))
        world.status = "active"
        world.missing = True
        check(CID, "a missing/foreign connection is not found", _raises(lambda: _create(session), DatasetSourceConnectionNotFoundError))
        world.missing = False
        for source_type in ("s3", "minio", "database", "agent_traces", "api"):
            world.source_type = source_type
            check(CID, f"a {source_type} connection is not an upload source", _raises(lambda: _create(session), store.NotAnUploadSourceError))
        check(CID, "none of the refused creates wrote a session or an audit event", not _sessions(session) and not audit.events)
        world.source_type = "upload"
        check(CID, "positive control: the same call succeeds once the world allows it", _create(session).status == "open")


def test_s3_finalize_creates_versions_in_order_and_audits() -> None:
    with _store_env() as (session, audit, _world, statements):
        first = _create(session)
        statements.clear()
        dataset = _finalize(session, first)
        check(CID, "the first verified upload is version 1", dataset.version == 1)
        check(CID, "the dataset copies the verified hash, size and purpose", dataset.verified_sha256 == SHA_A and dataset.verified_size_bytes == 1000 and dataset.purpose == "training" and dataset.upload_status == "verified")
        check(CID, "the dataset names its project, organization, session and creator", dataset.project_id == _PROJECT and dataset.organization_id == _ORG and dataset.upload_session_id == first.id and dataset.created_by == "user-1" and dataset.name == "support")
        check(CID, "created_at is the finalize time", dataset.created_at == _NOW + timedelta(minutes=10))
        check(CID, "the session is verified with matching verified values and finalize time", first.status == "verified" and first.verified_size_bytes == 1000 and first.verified_sha256 == SHA_A and first.verified_content_type == "application/jsonl" and first.finalized_at == _NOW + timedelta(minutes=10) and first.updated_at == _NOW + timedelta(minutes=10))
        update_at = next((i for i, s in enumerate(statements) if s.startswith("UPDATE dataset_upload_sessions")), -1)
        insert_at = next((i for i, s in enumerate(statements) if s.startswith("INSERT INTO datasets")), -1)
        check(CID, "the session UPDATE reaches the database before the dataset INSERT (the composite foreign key needs the verified row)", 0 <= update_at < insert_at)
        check(CID, "finalize audits the dataset creation with exact details", audit.actions() == ["dataset_upload_session.create", "dataset.create"] and audit.events[-1]["resource_id"] == str(dataset.id) and audit.events[-1]["resource_type"] == "dataset" and audit.events[-1]["details"] == {"project_id": str(_PROJECT), "upload_session_id": str(first.id), "version": 1, "purpose": "training"})
        second = _finalize(session, _create(session, declared_sha256=SHA_B))
        check(CID, "the next upload of the same name is version 2", second.version == 2)
        other = _finalize(session, _create(session), dataset_name="other")
        check(CID, "a different name starts at version 1", other.version == 1)
        other_project = _finalize(session, _create(session, project_id=_OTHER_PROJECT), dataset_name="support")
        check(CID, "the same name in another project starts at version 1", other_project.version == 1)
        third = _finalize(session, _create(session), dataset_name="support")
        check(CID, "versions keep counting per (project, name)", third.version == 3)
        held = _finalize(session, _create(session, purpose="held_out_evaluation"), dataset_name="held")
        check(CID, "a held-out upload creates a held-out dataset", held.purpose == "held_out_evaluation")
        at_expiry = _create(session, ttl=timedelta(hours=1))
        edge = _finalize(session, at_expiry, dataset_name="edge", now=at_expiry.expires_at)
        check(CID, "finalizing exactly at expires_at is allowed", edge.version == 1)
        for bad in ("", " ", " x", "x ", "x" * 201):
            check(CID, f"dataset name {bad[:5]!r} is refused", _raises(lambda b=bad: _finalize(session, _create(session), dataset_name=b), ValueError))
        ok_name = _finalize(session, _create(session), dataset_name="n" * 200)
        check(CID, "a 200-character name is allowed", len(ok_name.name) == 200)
        for field, value in (("verified_size_bytes", True), ("verified_sha256", "Z" * 64), ("verified_content_type", " ")):
            check(CID, f"a malformed {field} is refused", _raises(lambda f=field, v=value: _finalize(session, _create(session), **{f: v}), ValueError))
        check(CID, "naive finalize time is refused", _raises(lambda: _finalize(session, _create(session), now=datetime(2026, 1, 1)), ValueError))
        stamped = _finalize(session, _create(session), dataset_name="stamp", now=None)
        check(CID, "without `now` finalize uses the current time", abs((stamped.created_at - datetime.now(UTC)).total_seconds()) < 60)


def test_s4_finalize_refusals_write_nothing() -> None:
    with _store_env() as (session, audit, world, _statements):
        def snapshot() -> tuple[int, int, int]:
            return len(_sessions(session)), len(_datasets(session)), len(audit.events)

        for label, over, names in (
            ("size", {"verified_size_bytes": 1001}, ("size_bytes",)),
            ("smaller size", {"verified_size_bytes": 999}, ("size_bytes",)),
            ("hash", {"verified_sha256": SHA_B}, ("sha256",)),
            ("content type", {"verified_content_type": "text/csv"}, ("content_type",)),
            ("all three", {"verified_size_bytes": 1, "verified_sha256": SHA_B, "verified_content_type": "x/y"}, ("size_bytes", "sha256", "content_type")),
        ):
            record = _create(session)
            before = snapshot()
            try:
                _finalize(session, record, **over)
                raised: BaseException | None = None
            except Exception as exc:  # noqa: BLE001
                raised = exc
            check(CID, f"a {label} mismatch raises UploadVerificationError naming exactly {names}", isinstance(raised, store.UploadVerificationError) and raised.mismatched == names)
            check(CID, f"a {label} mismatch wrote nothing and left the session open", snapshot() == before and record.status == "open" and record.verified_sha256 is None)

        record = _create(session, ttl=timedelta(hours=1))
        before = snapshot()
        check(CID, "finalizing one second past expiry is refused", _raises(lambda: _finalize(session, record, now=record.expires_at + timedelta(seconds=1)), store.UploadExpiredError))
        check(CID, "an expired finalize wrote nothing", snapshot() == before and record.status == "open")

        done = _create(session)
        _finalize(session, done)
        before = snapshot()
        check(CID, "finalizing a verified session again is refused", _raises(lambda: _finalize(session, done), store.UploadStateError))
        check(CID, "the repeat wrote nothing", snapshot() == before)

        revoked = _create(session)
        world.status = "revoked"
        before = snapshot()
        check(CID, "a source revoked after the session opened creates no dataset", _raises(lambda: _finalize(session, revoked), DatasetSourceAuthorizationError))
        check(CID, "the revoked finalize wrote nothing", snapshot() == before and revoked.status == "open")
        world.status = "active"
        world.purposes = ["validation"]
        check(CID, "a purpose narrowed after the session opened creates no dataset", _raises(lambda: _finalize(session, revoked), DatasetSourceAuthorizationError))
        world.purposes = ["training", "held_out_evaluation"]
        check(CID, "positive control: with the grant restored the same session finalizes (as version 2 of 'support')", _finalize(session, revoked).version == 2)

        check(CID, "an unknown session id is not found", _raises(lambda: _finalize(session, SimpleNamespace(project_id=_PROJECT, id=uuid.uuid4(), declared_size_bytes=1, declared_sha256=SHA_A, declared_content_type="a/b")), store.UploadSessionNotFoundError))
        foreign = _create(session)
        check(CID, "a session is not reachable through another project id", _raises(lambda: _finalize(session, SimpleNamespace(project_id=_OTHER_PROJECT, id=foreign.id, declared_size_bytes=1000, declared_sha256=SHA_A, declared_content_type="application/jsonl")), store.UploadSessionNotFoundError))
        check(CID, "non-uuid ids are not found, not crashes", _raises(lambda: store.get_upload_session(session, _CTX, "x", "y"), store.UploadSessionNotFoundError))  # type: ignore[arg-type]
        check(CID, "a non-uuid project id with a real session id is not found", _raises(lambda: store.get_upload_session(session, _CTX, "x", foreign.id), store.UploadSessionNotFoundError))  # type: ignore[arg-type]
        check(CID, "a non-uuid session id with a real project id is not found", _raises(lambda: store.get_upload_session(session, _CTX, _PROJECT, "y"), store.UploadSessionNotFoundError))  # type: ignore[arg-type]


def test_s5_version_conflict_mapping_is_specific() -> None:
    with _store_env() as (session, _audit, _world, _statements):
        record = _create(session)
        real_flush = session.flush
        calls = {"n": 0}

        def flaky(constraint: str):
            def flush(*args: Any, **kwargs: Any) -> None:
                calls["n"] += 1
                if any(isinstance(obj, store.Dataset) for obj in session.new):
                    raise IntegrityError("INSERT INTO datasets", {}, Exception(f'duplicate key value violates unique constraint "{constraint}"'))
                real_flush(*args, **kwargs)

            return flush

        calls["n"] = 0
        session.flush = flaky("uq_datasets_project_name_version")  # type: ignore[method-assign]
        check(CID, "a duplicate (project, name, version) maps to DatasetVersionConflictError", _raises(lambda: _finalize(session, record), store.DatasetVersionConflictError))
        session.rollback()
        record2 = _create(session)
        calls["n"] = 0
        session.flush = flaky("fk_datasets_verified_upload_session")  # type: ignore[method-assign]
        check(CID, "any other integrity error is re-raised, never relabelled as a version conflict", _raises(lambda: _finalize(session, record2), IntegrityError))


def test_s6_reject_expire_and_state_machine() -> None:
    with _store_env() as (session, audit, _world, _statements):
        record = _create(session)
        rejected = store.record_upload_rejected(session, _CTX, project_id=_PROJECT, upload_session_id=record.id, reason="hash_mismatch", now=_NOW + timedelta(minutes=1), actor=_ACTOR)
        check(CID, "an open session can be rejected with a stable code", rejected.status == "rejected" and rejected.rejection_reason == "hash_mismatch" and rejected.finalized_at == _NOW + timedelta(minutes=1) and rejected.updated_at == _NOW + timedelta(minutes=1))
        check(CID, "rejection is audited with the reason and never object content", audit.events[-1]["action"] == "dataset_upload_session.reject" and audit.events[-1]["details"] == {"project_id": str(_PROJECT), "reason": "hash_mismatch"} and audit.events[-1]["resource_id"] == str(record.id))
        check(CID, "a rejected session cannot be rejected again", _raises(lambda: store.record_upload_rejected(session, _CTX, project_id=_PROJECT, upload_session_id=record.id, reason="again", actor=_ACTOR), store.UploadStateError))
        check(CID, "a rejected session cannot create a dataset", _raises(lambda: _finalize(session, record), store.UploadStateError))
        verified = _create(session)
        _finalize(session, verified)
        check(CID, "a verified session cannot be rejected", _raises(lambda: store.record_upload_rejected(session, _CTX, project_id=_PROJECT, upload_session_id=verified.id, reason="late", actor=_ACTOR), store.UploadStateError) and verified.status == "verified" and verified.rejection_reason is None)
        for bad in ("", "Has Space", "UPPER", "a" * 65, "1abc", "a-b", None, 5):
            probe = _create(session)
            check(CID, f"reason {bad!r} is refused", _raises(lambda b=bad, p=probe: store.record_upload_rejected(session, _CTX, project_id=_PROJECT, upload_session_id=p.id, reason=b, actor=_ACTOR), ValueError))  # type: ignore[arg-type]
            check(CID, f"a refused reason {bad!r} leaves the session open", probe.status == "open")
        ok_reason = _create(session)
        store.record_upload_rejected(session, _CTX, project_id=_PROJECT, upload_session_id=ok_reason.id, reason="a" * 64, actor=_ACTOR)
        check(CID, "a 64-character lowercase reason is allowed", ok_reason.rejection_reason == "a" * 64)
        stamped = _create(session)
        store.record_upload_rejected(session, _CTX, project_id=_PROJECT, upload_session_id=stamped.id, reason="x", actor=_ACTOR)
        check(CID, "without `now` a rejection is stamped with the current time", abs((stamped.finalized_at - datetime.now(UTC)).total_seconds()) < 60)

    with _store_env() as (session, audit, _world, _statements):
        stale = _create(session, ttl=timedelta(hours=1))
        exact = _create(session, ttl=timedelta(hours=2))
        fresh = _create(session, ttl=timedelta(hours=10))
        done = _create(session, ttl=timedelta(hours=1))
        _finalize(session, done, now=_NOW + timedelta(minutes=1))
        gone = _create(session, ttl=timedelta(hours=1))
        store.record_upload_rejected(session, _CTX, project_id=_PROJECT, upload_session_id=gone.id, reason="x", actor=_ACTOR)
        audit.events.clear()
        cutoff = _NOW + timedelta(hours=2)
        count = store.expire_open_sessions(session, _CTX, now=cutoff, actor=_ACTOR)
        check(CID, "expire_open_sessions moves exactly the open sessions with expires_at <= now", count == 2 and stale.status == "expired" and exact.status == "expired" and fresh.status == "open")
        check(CID, "expiry never touches verified or rejected sessions", done.status == "verified" and gone.status == "rejected")
        check(CID, "an expired session records the cutoff as its finalize time", stale.finalized_at == cutoff and stale.updated_at == cutoff)
        check(CID, "each expiry is audited once", audit.actions() == ["dataset_upload_session.expire"] * 2 and {e["resource_id"] for e in audit.events} == {str(stale.id), str(exact.id)})
        check(CID, "a second sweep expires nothing more", store.expire_open_sessions(session, _CTX, now=cutoff, actor=_ACTOR) == 0)
        check(CID, "an expired session cannot create a dataset", _raises(lambda: _finalize(session, stale), store.UploadStateError))
        check(CID, "a naive sweep time is refused", _raises(lambda: store.expire_open_sessions(session, _CTX, now=datetime(2026, 1, 1), actor=_ACTOR), ValueError))
        long_past = _create(session, now=datetime(2020, 1, 1, tzinfo=UTC), ttl=timedelta(hours=1))
        far_future = _create(session, now=datetime(2099, 1, 1, tzinfo=UTC), ttl=timedelta(hours=1))
        store.expire_open_sessions(session, _CTX, actor=_ACTOR)
        check(CID, "without `now` the sweep uses the current time", long_past.status == "expired" and far_future.status == "open")


def test_s7_reads_hide_held_out_and_paginate() -> None:
    with _store_env() as (session, _audit, world, _statements):
        a = _finalize(session, _create(session), dataset_name="a")
        b = _finalize(session, _create(session, purpose="held_out_evaluation"), dataset_name="b")
        c = _finalize(session, _create(session), dataset_name="c")
        other = _finalize(session, _create(session, project_id=_OTHER_PROJECT), dataset_name="z")
        ids = sorted([a.id, b.id, c.id], key=str)
        everything = store.list_datasets(session, _CTX, project_id=_PROJECT, include_held_out=True, limit=200)
        check(CID, "a privileged list returns this project's datasets ordered by id", [d.id for d in everything] == ids and other.id not in {d.id for d in everything})
        visible = store.list_datasets(session, _CTX, project_id=_PROJECT, include_held_out=False, limit=200)
        check(CID, "an unprivileged list omits held_out_evaluation datasets", b.id not in {d.id for d in visible} and len(visible) == 2)
        page = store.list_datasets(session, _CTX, project_id=_PROJECT, include_held_out=True, limit=2)
        rest = store.list_datasets(session, _CTX, project_id=_PROJECT, include_held_out=True, limit=2, after=page[-1].id)
        check(CID, "limit and after page without overlap or gaps", [d.id for d in page] == ids[:2] and [d.id for d in rest] == ids[2:])
        check(CID, "limit=1 returns exactly one", len(store.list_datasets(session, _CTX, project_id=_PROJECT, include_held_out=True, limit=1)) == 1)
        for bad in (0, 201, -1, True, "5", None):
            check(CID, f"limit {bad!r} is refused", _raises(lambda b=bad: store.list_datasets(session, _CTX, project_id=_PROJECT, include_held_out=True, limit=b), ValueError))  # type: ignore[arg-type]
        check(CID, "after must be a uuid", _raises(lambda: store.list_datasets(session, _CTX, project_id=_PROJECT, include_held_out=True, limit=5, after="x"), ValueError))  # type: ignore[arg-type]
        check(CID, "a missing project lists as ProjectNotFoundError", _raises(lambda: store.list_datasets(session, _CTX, project_id=uuid.uuid4(), include_held_out=True, limit=5), store.ProjectNotFoundError))
        check(CID, "get_dataset returns a visible dataset", store.get_dataset(session, _CTX, project_id=_PROJECT, dataset_id=a.id, include_held_out=False).id == a.id)
        check(CID, "get_dataset hides a held-out dataset from an unprivileged caller", _raises(lambda: store.get_dataset(session, _CTX, project_id=_PROJECT, dataset_id=b.id, include_held_out=False), store.DatasetNotFoundError))
        check(CID, "get_dataset shows a held-out dataset to a privileged caller", store.get_dataset(session, _CTX, project_id=_PROJECT, dataset_id=b.id, include_held_out=True).id == b.id)
        check(CID, "get_dataset is project-scoped", _raises(lambda: store.get_dataset(session, _CTX, project_id=_OTHER_PROJECT, dataset_id=a.id, include_held_out=True), store.DatasetNotFoundError))
        check(CID, "get_dataset of a random id is not found", _raises(lambda: store.get_dataset(session, _CTX, project_id=_PROJECT, dataset_id=uuid.uuid4(), include_held_out=True), store.DatasetNotFoundError))
        check(CID, "get_dataset with non-uuid ids is not found", _raises(lambda: store.get_dataset(session, _CTX, project_id=_PROJECT, dataset_id="x", include_held_out=True), store.DatasetNotFoundError))  # type: ignore[arg-type]
        check(CID, "get_dataset with a non-uuid project id is not found", _raises(lambda: store.get_dataset(session, _CTX, project_id="x", dataset_id=a.id, include_held_out=True), store.DatasetNotFoundError))  # type: ignore[arg-type]
        del world


def test_s8_module_constants_and_no_storage_surface() -> None:
    check(CID, "the upload cap is 10 GiB", store.MAX_UPLOAD_BYTES == 10 * GIB)
    check(CID, "the upload lifetime cap is 24 hours", store.MAX_UPLOAD_TTL == timedelta(hours=24))
    check(CID, "the page cap is 200 and the name cap 200", store.MAX_PAGE_SIZE == 200 and store.DATASET_NAME_MAX_LENGTH == 200)
    check(CID, "held-out purpose name is exact", store.HELD_OUT_PURPOSE == "held_out_evaluation")
    check(CID, "the version-conflict constraint name matches the migration", store._VERSION_CONSTRAINT == "uq_datasets_project_name_version" and "uq_datasets_project_name_version" in _migration())
    source = Path(store.__file__).read_text()
    for forbidden in ("boto3", "botocore", "requests", "urllib", "httpx", "s3://", "socket"):
        check(CID, f"the store never touches object storage or the network ({forbidden})", forbidden not in source)
    check(CID, "the store has no delete path", "delete(" not in source and "session.delete" not in source)
    columns = {column.name for column in store.Dataset.__table__.columns}
    check(CID, "the Dataset model exposes no storage field", not ({"object_key", "bucket", "artifact_uri"} & columns))
    check(CID, "the session model's columns match the migration's", all(f'"{column.name}"' in _migration() for column in store.DatasetUploadSession.__table__.columns))
    check(CID, "the dataset model's columns match the migration's", all(f'"{column.name}"' in _migration() for column in store.Dataset.__table__.columns))


# -- R: routes -----------------------------------------------------------------

_ORG_ID = uuid.uuid4()
_PRINCIPALS = {
    name: AuthenticatedPrincipal(issuer="https://issuer.example", subject=name)
    for name in ("ml", "admin", "orgadmin", "viewer", "evaluator", "approver")
}
_ROLES = {
    "ml": Role.ML_ENGINEER,
    "admin": Role.WORKSPACE_ADMIN,
    "orgadmin": Role.ORGANIZATION_ADMIN,
    "viewer": Role.VIEWER,
    "evaluator": Role.EVALUATOR,
    "approver": Role.APPROVER,
}


def _principal_resolver(request: Any) -> AuthenticatedPrincipal | None:
    return _PRINCIPALS.get(request.headers.get("x-test-principal", ""))


def _role_lookup(issuer: str, subject: str, organization_id: uuid.UUID) -> Role | None:
    return _ROLES.get(subject) if organization_id == _ORG_ID else None


def _free_port() -> int:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


@contextlib.contextmanager
def _running_app(router: Any = None):
    app = FastAPI()
    app.include_router(
        router
        or routes.build_dataset_router(lambda: None, principal_resolver=_principal_resolver, role_lookup=_role_lookup)
    )
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


def _get(base: str, path: str, principal: str | None = "ml") -> _Http:
    headers = {"X-Test-Principal": principal} if principal else {}
    parsed = urllib.parse.urlparse(base)
    conn = http.client.HTTPConnection(parsed.hostname or "127.0.0.1", parsed.port or 80, timeout=10)
    try:
        conn.request("GET", path, headers=headers)
        response = conn.getresponse()
        return _Http(response.status, response.read())
    finally:
        conn.close()


_PROJECT_ID = uuid.uuid4()


def _collection(project: Any = _PROJECT_ID) -> str:
    return f"/api/v1/organizations/{_ORG_ID}/projects/{project}/datasets"


def _dataset_ns(*, purpose: str = "training", name: str = "support", version: int = 1, did: uuid.UUID | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        id=did or uuid.uuid4(),
        organization_id=_ORG_ID,
        project_id=_PROJECT_ID,
        name=name,
        version=version,
        purpose=purpose,
        upload_session_id=uuid.uuid4(),
        upload_status="verified",
        verified_sha256=SHA_A,
        verified_size_bytes=1234,
        created_by="user-1",
        created_at=_NOW,
        object_key="tenants/secret/key",
    )


@contextlib.contextmanager
def _session_cm(session_factory: Any, tenant_context: Any):
    yield object()


class _Env:
    def __init__(self) -> None:
        self.rows: list[SimpleNamespace] = []
        self.list_calls: list[dict[str, Any]] = []
        self.get_calls: list[dict[str, Any]] = []
        self.get_result: SimpleNamespace | None = None
        self.project_missing = False

    def list_datasets(self, session: Any, ctx: Any, **kwargs: Any) -> Any:
        self.list_calls.append(kwargs)
        if self.project_missing:
            raise store.ProjectNotFoundError("nope")
        return self.rows[: kwargs["limit"]]

    def get_dataset(self, session: Any, ctx: Any, **kwargs: Any) -> Any:
        self.get_calls.append(kwargs)
        if self.get_result is None:
            raise store.DatasetNotFoundError("nope")
        return self.get_result


@contextlib.contextmanager
def _patched(env: _Env):
    names = {
        "tenant_scoped_session_for": _session_cm,
        "list_datasets": env.list_datasets,
        "get_dataset": env.get_dataset,
    }
    originals = {name: getattr(routes, name) for name in names}
    for name, value in names.items():
        setattr(routes, name, value)
    try:
        yield
    finally:
        for name, value in originals.items():
            setattr(routes, name, value)


_EXPECTED_KEYS = {
    "id", "organization_id", "project_id", "name", "version", "purpose",
    "upload_session_id", "verified_sha256", "verified_size_bytes", "created_by", "created_at",
}


def test_r1_roles_and_authentication() -> None:
    env = _Env()
    env.rows = [_dataset_ns()]
    env.get_result = env.rows[0]
    with _patched(env), _running_app() as base:
        check(CID, "an unauthenticated list is 401", _get(base, _collection(), None).status == 401)
        check(CID, "an unauthenticated get is 401", _get(base, f"{_collection()}/{uuid.uuid4()}", None).status == 401)
        for role in ("ml", "admin", "orgadmin"):
            check(CID, f"{role} may list (200)", _get(base, _collection(), role).status == 200)
            check(CID, f"{role} may get (200)", _get(base, f"{_collection()}/{uuid.uuid4()}", role).status == 200)
        for role in ("viewer", "evaluator", "approver"):
            check(CID, f"{role} is denied the list (403)", _get(base, _collection(), role).status == 403)
            check(CID, f"{role} is denied the get (403)", _get(base, f"{_collection()}/{uuid.uuid4()}", role).status == 403)
        check(CID, "a malformed project id is a validation error, never a 200", _get(base, f"/api/v1/organizations/{_ORG_ID}/projects/not-a-uuid/datasets").status == 422)


def test_r2_response_shape_and_no_storage_fields() -> None:
    env = _Env()
    row = _dataset_ns()
    env.rows = [row]
    env.get_result = row
    with _patched(env), _running_app() as base:
        listing = _get(base, _collection())
        check(CID, "the list is 200 with items and next_after", listing.status == 200 and set(listing.json()) == {"items", "next_after"})
        item = listing.json()["items"][0]
        check(CID, "an item carries exactly the contract's keys", set(item) == _EXPECTED_KEYS)
        check(
            CID,
            "an item's values are exact",
            item["id"] == str(row.id)
            and item["organization_id"] == str(_ORG_ID)
            and item["project_id"] == str(_PROJECT_ID)
            and item["name"] == "support"
            and item["version"] == 1
            and item["purpose"] == "training"
            and item["upload_session_id"] == str(row.upload_session_id)
            and item["verified_sha256"] == SHA_A
            and item["verified_size_bytes"] == 1234
            and item["created_by"] == "user-1"
            and item["created_at"] == _NOW.isoformat(),
        )
        single = _get(base, f"{_collection()}/{row.id}")
        check(CID, "get returns the same shape", single.status == 200 and single.json() == item)
        for body in (listing.body.decode(), single.body.decode()):
            check(CID, "no response carries an object key, bucket or storage URI", not any(t in body for t in ("object_key", "tenants/", "artifact_uri", "bucket", "s3://", "presign", "upload_status")))
        check(CID, "a single page of one item has no cursor", listing.json()["next_after"] is None)


def test_r3_held_out_visibility_flags() -> None:
    env = _Env()
    env.get_result = _dataset_ns(purpose="held_out_evaluation")
    with _patched(env), _running_app() as base:
        for role, expected in (("ml", False), ("admin", True), ("orgadmin", True)):
            env.list_calls.clear()
            env.get_calls.clear()
            _get(base, _collection(), role)
            _get(base, f"{_collection()}/{uuid.uuid4()}", role)
            check(CID, f"list include_held_out is {expected} for {role}", [c["include_held_out"] for c in env.list_calls] == [expected])
            check(CID, f"get include_held_out is {expected} for {role}", [c["include_held_out"] for c in env.get_calls] == [expected])
        check(CID, "the store receives the path's project id", env.list_calls[-1]["project_id"] == _PROJECT_ID)
        want = uuid.uuid4()
        _get(base, f"{_collection()}/{want}", "admin")
        check(CID, "the store receives the path's dataset id and project id", env.get_calls[-1]["dataset_id"] == want and env.get_calls[-1]["project_id"] == _PROJECT_ID)


def test_r4_not_found_bodies_are_identical() -> None:
    env = _Env()
    with _patched(env), _running_app() as base:
        missing = _get(base, f"{_collection()}/{uuid.uuid4()}")
        other = _get(base, f"{_collection()}/{uuid.uuid4()}")
        check(CID, "a missing dataset is 404 {detail: not_found}", missing.status == 404 and missing.json() == {"detail": "not_found"} and missing.body == other.body)
        env.project_missing = True
        gone = _get(base, _collection())
        check(CID, "a missing/foreign project lists as 404 {detail: not_found}", gone.status == 404 and gone.json() == {"detail": "not_found"})


def test_r5_limit_after_and_pagination() -> None:
    env = _Env()
    env.rows = [_dataset_ns(name=f"d{i}") for i in range(5)]
    with _patched(env), _running_app() as base:
        _get(base, _collection())
        check(CID, "the default limit is 50", env.list_calls[-1]["limit"] == 50 and env.list_calls[-1]["after"] is None)
        for value, ok in (("1", True), ("200", True), ("0", False), ("201", False), ("-1", False), ("abc", False), ("", False), ("1.5", False)):
            response = _get(base, f"{_collection()}?limit={value}")
            if ok:
                check(CID, f"limit={value} is accepted", response.status == 200 and env.list_calls[-1]["limit"] == int(value))
            else:
                check(CID, f"limit={value!r} is 400 invalid_limit", response.status == 400 and response.json() == {"detail": "invalid_limit"})
        cursor = uuid.uuid4()
        _get(base, f"{_collection()}?after={cursor}")
        check(CID, "a valid after cursor reaches the store as a UUID", env.list_calls[-1]["after"] == cursor)
        for value in ("nope", "", "123"):
            response = _get(base, f"{_collection()}?after={value}")
            check(CID, f"after={value!r} is 400 invalid_after", response.status == 400 and response.json() == {"detail": "invalid_after"})
        bad_both = _get(base, f"{_collection()}?limit=0&after=nope")
        check(CID, "an invalid limit is reported before an invalid cursor", bad_both.json() == {"detail": "invalid_limit"})
        env.list_calls.clear()
        _get(base, f"{_collection()}?limit=nope")
        check(CID, "an invalid limit never reaches the store", env.list_calls == [])
        full = _get(base, f"{_collection()}?limit=5").json()
        check(CID, "a full page carries next_after = the last item's id", len(full["items"]) == 5 and full["next_after"] == full["items"][-1]["id"])
        short = _get(base, f"{_collection()}?limit=6").json()
        check(CID, "a short page has no cursor", len(short["items"]) == 5 and short["next_after"] is None)
        two = _get(base, f"{_collection()}?limit=2").json()
        check(CID, "limit=2 returns two items and the cursor of the second", len(two["items"]) == 2 and two["next_after"] == two["items"][1]["id"])
        env.rows = []
        empty = _get(base, f"{_collection()}?limit=1").json()
        check(CID, "an empty page is {items: [], next_after: null}", empty == {"items": [], "next_after": None})


def test_r6_module_constants() -> None:
    check(CID, "the default page size is 50", routes._DEFAULT_LIMIT == 50)
    check(CID, "only ml_engineer is blind to held-out data", routes._HELD_OUT_BLIND_ROLES == frozenset({Role.ML_ENGINEER}))
    check(CID, "the collection path is exact", routes._COLLECTION_PATH == "/api/v1/organizations/{organization_id}/projects/{project_id}/datasets")
    check(CID, "the item path is exact", routes._ITEM_PATH == routes._COLLECTION_PATH + "/{dataset_id}")
    check(CID, "the routes module adds no write verb", not re.search(r"@router\.(post|put|patch|delete)", Path(routes.__file__).read_text()))
    check(CID, "the routes module never mentions an object key", "object_key" not in Path(routes.__file__).read_text().split('"""', 2)[2])


# -- C: contract and wiring -----------------------------------------------------


def test_c1_interface_contract() -> None:
    spec = yaml.safe_load(INTERFACE_PATH.read_text())
    paths = spec["paths"]
    collection = "/api/v1/organizations/{organization_id}/projects/{project_id}/datasets"
    item = collection + "/{dataset_id}"
    check(CID, "05-interface.yaml declares the list and get paths", collection in paths and item in paths)
    check(CID, "the list path is GET only", set(paths[collection]) - {"parameters"} == {"get"})
    check(CID, "the item path is GET only", set(paths[item]) - {"parameters"} == {"get"})
    check(CID, "no upload or finalize path exists yet (FTF-0501 owns those)", not any("upload" in p or "finalize" in p for p in paths))
    schemas = spec["components"]["schemas"]
    dataset = schemas["Dataset"]
    check(CID, "the Dataset schema's properties match the route's keys exactly", set(dataset["properties"]) == _EXPECTED_KEYS and set(dataset["required"]) == _EXPECTED_KEYS)
    check(CID, "the Dataset schema is closed", dataset["additionalProperties"] is False)
    check(CID, "the Dataset schema bounds name, version and the hash shape", dataset["properties"]["name"]["maxLength"] == 200 and dataset["properties"]["version"]["minimum"] == 1 and dataset["properties"]["verified_sha256"]["pattern"] == "^[0-9a-f]{64}$")
    check(CID, "DatasetPage is items + next_after, closed", set(schemas["DatasetPage"]["properties"]) == {"items", "next_after"} and schemas["DatasetPage"]["additionalProperties"] is False)
    limit = next(p for p in paths[collection]["get"]["parameters"] if p["name"] == "limit")
    check(CID, "the limit parameter is 1..200 with default 50", limit["schema"]["minimum"] == 1 and limit["schema"]["maximum"] == 200 and limit["schema"]["default"] == 50)
    check(CID, "the list documents 400/401/403/404", {"200", "400", "401", "403", "404"} <= set(paths[collection]["get"]["responses"]))
    check(CID, "the get documents 401/403/404", {"200", "401", "403", "404"} <= set(paths[item]["get"]["responses"]))
    for name in ("UploadSessionCreateRequest", "UploadSession", "UploadFinalizeRequest", "UploadSessionStatus"):
        check(CID, f"the frozen component {name} exists", name in schemas)
    check(CID, "UploadSession never carries the object key", "object_key" not in schemas["UploadSession"]["properties"] and not any("object_key" in json.dumps(schemas[n]) or "bucket" in json.dumps(schemas[n]) for n in ("Dataset", "DatasetPage", "UploadSession")))
    check(CID, "UploadSessionStatus is exactly the four database states", schemas["UploadSessionStatus"]["enum"] == ["open", "verified", "rejected", "expired"])
    check(CID, "the create request caps the declared size at 10 GiB", schemas["UploadSessionCreateRequest"]["properties"]["declared_size_bytes"]["maximum"] == 10 * GIB)
    check(CID, "the request components are closed", all(schemas[n]["additionalProperties"] is False for n in ("UploadSessionCreateRequest", "UploadSession", "UploadFinalizeRequest")))


def test_c2_router_is_wired_into_the_app() -> None:
    main = MAIN_PATH.read_text()
    check(CID, "main.py imports build_dataset_router", "from app.datasets.dataset_routes import build_dataset_router" in main)
    check(
        CID,
        "main.py includes the router with the shared principal resolver and role lookup",
        re.search(r"app\.include_router\(\s*build_dataset_router\(\s*session_factory, principal_resolver=principal_resolver, role_lookup=role_lookup\s*\)\s*\)", main) is not None,
    )
    env = _full_app_env()
    added = set(env) - set(os.environ)
    old = {k: os.environ[k] for k in env if k in os.environ}
    os.environ.update(env)
    try:
        import app.main as main_module

        app = main_module.create_app()
    finally:
        for key in added:
            os.environ.pop(key, None)
        os.environ.update(old)
    check(CID, "create_app returns a real app", app is not None)
    with _running_app_for(app) as base:
        check(CID, "GET .../datasets is mounted on the real app (401, not 404)", _get(base, _collection(), None).status == 401)
        check(CID, "GET .../datasets/{id} is mounted on the real app (401, not 404)", _get(base, f"{_collection()}/{uuid.uuid4()}", None).status == 401)
        check(CID, "no write verb is mounted on the dataset collection", _post(base, _collection()) in (404, 405))


def _post(base: str, path: str) -> int:
    parsed = urllib.parse.urlparse(base)
    conn = http.client.HTTPConnection(parsed.hostname or "127.0.0.1", parsed.port or 80, timeout=10)
    try:
        conn.request("POST", path, body=b"{}", headers={"Content-Type": "application/json"})
        return conn.getresponse().status
    finally:
        conn.close()


@contextlib.contextmanager
def _running_app_for(app: FastAPI):
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


def _full_app_env() -> dict[str, str]:
    return {
        "SOVRA_ENVIRONMENT": "staging",
        "SOVRA_DATABASE_URL": "postgresql+psycopg://sovra_app:s3cr3t@db.internal:5432/sovra",
        "SOVRA_LITELLM_GATEWAY_URL": "https://litellm.internal.example",
        "SOVRA_PRIVACY_POLICY": "standard",
        "SOVRA_TLS_VERIFY": "true",
        "SOVRA_MAX_TRAINING_BUDGET_USD": "500.00",
        "SOVRA_LITELLM_API_KEY_SECRET_REF": "vault://sovra/litellm-api-key",
        "SOVRA_DATABASE_PASSWORD_SECRET_REF": "openbao://sovra/db-password",
        "SOVRA_OIDC_ISSUER": "http://idp.internal.example",
        "SOVRA_OIDC_CLIENT_ID": "sovra-factory",
        "SOVRA_OIDC_CLIENT_SECRET_REF": "vault://sovra/oidc-client-secret",
        "SOVRA_OIDC_REDIRECT_URI": "http://factory.internal.example/auth/callback",
        "SOVRA_OIDC_AUTHORIZATION_ENDPOINT": "http://idp.internal.example/authorize",
        "SOVRA_OIDC_TOKEN_ENDPOINT": "http://idp.internal.example/token",
        "SOVRA_OIDC_JWKS_URI": "http://idp.internal.example/jwks",
        "SOVRA_SESSION_SIGNING_KEY_SECRET_REF": "vault://sovra/session-signing-key",
        "SOVRA_OIDC_ALLOWED_ORIGIN": "http://factory.internal.example",
        "SOVRA_OIDC_CLIENT_SECRET_VALUE": "not-a-real-secret",
        "SOVRA_SESSION_SIGNING_KEY_VALUE": "not-a-real-signing-key",
    }


def main() -> None:
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
            except Exception as exc:  # noqa: BLE001 - a crash is a failed test, reported by name
                FAILURES.append(f"FAIL [{CID}] {name} raised {type(exc).__name__}: {exc}")

    if FAILURES:
        print(f"FTF-0501-P1 dataset upload tests: {len(FAILURES)} failed")
        for failure in FAILURES:
            print(f"  {failure}")
        sys.exit(1)

    print("FTF-0501-P1 dataset upload tests: all checks passed")
    sys.exit(0)


if __name__ == "__main__":
    main()
