#!/usr/bin/env python3
"""FTF-0501-P2 -- real Postgres proof for the upload-session lifecycle
hardening migration (0025_dataset_upload_hardening).

Proves against a live PostgreSQL server (never SQLite) every clause of the
QUEUE.md FTF-0501-P2 contract, as the restricted `sovra_app` role and, for
the "every role" claim, as the migration owner:

- a rejected / expired / aborted-style closed session cannot be moved back to
  `open` (and a verified one still cannot be touched); the only accepted
  status change is open -> verified | rejected | expired;
- a session cannot be inserted as verified (or rejected / expired) -- each
  attempt carries a row that satisfies every CHECK constraint, so the refusal
  can only come from the new guard;
- a forward-dated (or backdated) `created_at` is refused, and `created_at`
  within the 60 second window is accepted (boundary controls on both sides);
- the normal store path (create -> verify + dataset, create -> reject,
  expire sweep) still works on the hardened database;
- the same attacks SUCCEED on a database where the protection is removed:
  after `alembic downgrade -1` (migration 0024 only) and under each of the
  three injected faults (insert trigger dropped, transition check dropped,
  insert check dropped) -- so every protection is shown able to fail.

Every negative has a positive control right next to it, so a green run cannot
come from a fixture that refuses everything.

Requires SOVRA_DATABASE_URL (migration-owner role) and SOVRA_APP_DB_PASSWORD.
Run: python3 tests/integration/test_ftf_0501_p2_dataset_upload_hardening.py
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

ROOT = Path(__file__).resolve().parent.parent.parent
API_ROOT = ROOT / "apps" / "api"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(API_ROOT))

from sqlalchemy import Engine, create_engine, text  # noqa: E402
from sqlalchemy.exc import DBAPIError  # noqa: E402
from sqlalchemy.orm import Session, sessionmaker  # noqa: E402

from app.auth.organization_workspace_schema import (  # noqa: E402
    APP_ROLE_NAME,
    Organization,
    Workspace,
)
from app.datasets import dataset_upload_store as store  # noqa: E402
from app.db.tenant_context import TenantContext, tenant_scoped_session_for  # noqa: E402
from app.projects.project_schema import Project  # noqa: E402

FAILURES: list[str] = []
CID = "FTF-0501-P2"
MIGRATION_PATH = API_ROOT / "alembic" / "versions" / "0025_dataset_upload_hardening.py"

REOPEN = "a closed upload session cannot be changed or reopened"
VERIFIED_FROZEN = "a verified upload session is immutable"
NOT_OPEN_INSERT = "a new upload session must be open"
BAD_CLOCK = "created_at must be within 60 seconds of the database clock"


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


def _sha(seed: str) -> str:
    return (seed * 64)[:64]


def _load_migration() -> Any:
    spec = importlib.util.spec_from_file_location("migration_0025", MIGRATION_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _finish() -> None:
    if FAILURES:
        print(f"FTF-0501-P2 dataset upload hardening real proof: {len(FAILURES)} failed")
        for failure in FAILURES:
            print(f"  {failure}")
        sys.exit(1)
    print("FTF-0501-P2 dataset upload hardening real proof: all checks passed")
    sys.exit(0)


def _migrate(migration_url: str, args: list[str], label: str) -> None:
    result = _alembic(args, migration_url)
    check(
        f"{label} runs cleanly (exit {result.returncode})\n{result.stdout}\n{result.stderr}",
        result.returncode == 0,
    )
    if result.returncode != 0:
        _finish()


def main() -> None:
    migration_url = _required_env("SOVRA_DATABASE_URL")
    app_password = _required_env("SOVRA_APP_DB_PASSWORD")
    migration = _load_migration()

    for args, label in (
        (["upgrade", "head"], "alembic upgrade head"),
        (["downgrade", "-1"], "alembic downgrade -1"),
        (["upgrade", "head"], "alembic upgrade head after downgrade -1"),
    ):
        _migrate(migration_url, args, label)
    heads = _alembic(["heads"], migration_url)
    check(f"exactly one Alembic head: {heads.stdout!r}", heads.stdout.count("(head)") == 1)

    owner_engine = create_engine(migration_url)
    app_engine = create_engine(_app_role_url(migration_url, app_password))
    check("the app engine is the restricted sovra_app role", app_engine.url.username == "sovra_app")
    factory = sessionmaker(bind=app_engine, expire_on_commit=False)

    # ---- seed: one org / project / upload connection ---------------------------
    with Session(owner_engine) as session:
        org = Organization(name="Acme 0501p2", slug=f"acme-0501p2-{uuid.uuid4().hex[:8]}")
        session.add(org)
        session.flush()
        ws = Workspace(organization_id=org.id, name="Main", slug="main")
        session.add(ws)
        session.flush()
        proj = Project(
            organization_id=org.id,
            workspace_id=ws.id,
            name="p1",
            project_type="generic_model_tuning",
            status="active",
            security_profile="standard",
            row_version=1,
            created_by="seed",
        )
        session.add(proj)
        session.commit()
        org_id, project_id = org.id, proj.id
    connection_id = uuid.uuid4()
    with owner_engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO dataset_source_connections (id, organization_id, project_id, "
                "source_type, display_name, allowed_purposes_json, security_classification, "
                "connection_status, created_by) VALUES (:id, :org, :project, 'upload', 'up', "
                "CAST(:purposes AS jsonb), 'internal', 'active', 'seed')"
            ),
            {
                "id": connection_id,
                "org": org_id,
                "project": project_id,
                "purposes": json.dumps(["training"]),
            },
        )

    def raw(engine: Engine, sql: str, **params: Any) -> tuple[bool, str]:
        try:
            with engine.begin() as conn:
                conn.execute(
                    text("SELECT set_config('app.current_organization_id', :org, true)"),
                    {"org": str(org_id)},
                )
                conn.execute(text(sql), params)
            return True, ""
        except DBAPIError as exc:
            return False, str(exc.orig)

    def insert_session(
        engine: Engine = app_engine,
        *,
        status: str = "open",
        created_offset: timedelta = timedelta(0),
        ttl: timedelta = timedelta(hours=1),
    ) -> tuple[uuid.UUID, bool, str]:
        """Inserts a row that satisfies every CHECK for its status, so the only
        thing that can refuse it is the P2 guard."""
        session_id = uuid.uuid4()
        created = datetime.now(UTC) + created_offset
        closed = status != "open"
        verified = status == "verified"
        ok, message = raw(
            engine,
            "INSERT INTO dataset_upload_sessions (id, organization_id, project_id, "
            "dataset_source_connection_id, purpose, object_key, declared_size_bytes, max_bytes, "
            "declared_sha256, declared_content_type, status, verified_size_bytes, "
            "verified_sha256, verified_content_type, rejection_reason, created_by, created_at, "
            "expires_at, finalized_at, updated_at) VALUES (:id, :org, :project, :conn, "
            "'training', :key, 500, 1000, :sha, 'application/jsonl', :status, :vsize, :vsha, "
            ":vctype, :reason, 'raw', :created, :expires, :finalized, :created)",
            id=session_id,
            org=org_id,
            project=project_id,
            conn=connection_id,
            key=f"tenants/{org_id}/projects/{project_id}/uploads/{session_id}/source",
            sha=_sha("9"),
            status=status,
            vsize=500 if verified else None,
            vsha=_sha("9") if verified else None,
            vctype="application/jsonl" if verified else None,
            reason="rejected_by_test" if status == "rejected" else None,
            created=created,
            expires=created + ttl,
            finalized=created if closed else None,
        )
        return session_id, ok, message

    def status_of(session_id: uuid.UUID) -> str | None:
        with owner_engine.connect() as conn:
            return conn.execute(
                text("SELECT status FROM dataset_upload_sessions WHERE id = :id"),
                {"id": session_id},
            ).scalar()

    def count_sessions() -> int:
        with owner_engine.connect() as conn:
            return int(
                conn.execute(text("SELECT count(*) FROM dataset_upload_sessions")).scalar() or 0
            )

    def expect_refused(description: str, outcome: tuple[bool, str], needle: str) -> None:
        ok, message = outcome
        check(f"{description} (got ok={ok}: {message[:160]!r})", (not ok) and needle in message)

    def expect_allowed(description: str, outcome: tuple[bool, str]) -> None:
        ok, message = outcome
        check(f"{description} (got ok={ok}: {message[:160]!r})", ok)

    def move(engine: Engine, session_id: uuid.UUID, sql_set: str) -> tuple[bool, str]:
        return raw(
            engine,
            f"UPDATE dataset_upload_sessions SET {sql_set}, updated_at = now() WHERE id = :id",
            id=session_id,
        )

    def closed_session(status: str) -> uuid.UUID:
        session_id, ok, message = insert_session()
        check(f"positive control: an open session inserts ({message[:100]})", ok)
        if status == "rejected":
            sql_set = "status='rejected', rejection_reason='x', finalized_at=now()"
        elif status == "expired":
            sql_set = "status='expired', finalized_at=now()"
        else:
            sql_set = (
                "status='verified', verified_size_bytes=500, "
                f"verified_sha256='{_sha('9')}', verified_content_type='application/jsonl', "
                "finalized_at=now()"
            )
        expect_allowed(
            f"positive control: open -> {status} is accepted", move(app_engine, session_id, sql_set)
        )
        check(f"the session is now {status}", status_of(session_id) == status)
        return session_id

    def reopen_sql() -> str:
        return "status='open', rejection_reason=NULL, finalized_at=NULL"

    # ---- positive controls: the allowed lifecycle ------------------------------
    open_id, ok, message = insert_session()
    check(f"positive control: a well-formed open session inserts ({message[:100]})", ok)
    expect_allowed(
        "positive control: a change that keeps an open session open is accepted",
        move(app_engine, open_id, "status='open'"),
    )
    for edge in (timedelta(seconds=30), timedelta(seconds=-30)):
        _, ok, message = insert_session(created_offset=edge)
        check(f"boundary control: created_at {edge} from now() is accepted ({message[:100]})", ok)

    # ---- attack 1: reopen / re-transition a closed session ---------------------
    attack_cases: list[tuple[str, uuid.UUID, str, str]] = []
    for closed in ("rejected", "expired"):
        sid = closed_session(closed)
        attack_cases.append((f"reopen a {closed} session", sid, reopen_sql(), closed))
        other = "expired" if closed == "rejected" else "rejected"
        move_sql = (
            "status='expired', rejection_reason=NULL"
            if other == "expired"
            else "status='rejected', rejection_reason='y'"
        )
        attack_cases.append((f"re-close a {closed} session as {other}", sid, move_sql, closed))
        attack_cases.append(
            (f"edit a column of a {closed} session in place", sid, "finalized_at=now()", closed)
        )
    verified_id = closed_session("verified")
    attack_cases.append(("reopen a verified session", verified_id, reopen_sql(), "verified"))
    for description, sid, sql_set, still in attack_cases:
        needle = VERIFIED_FROZEN if still == "verified" else REOPEN
        expect_refused(
            f"as sovra_app, cannot {description}", move(app_engine, sid, sql_set), needle
        )
        check(
            f"after the refused attempt to {description} the status is unchanged",
            status_of(sid) == still,
        )
    expect_refused(
        "as the migration owner, a rejected session cannot be reopened either",
        move(owner_engine, attack_cases[0][1], reopen_sql()),
        REOPEN,
    )

    # ---- attack 2: insert a session in a non-open status -----------------------
    before = count_sessions()
    for status in ("verified", "rejected", "expired"):
        expect_refused(
            f"as sovra_app, cannot insert a session already {status}",
            insert_session(status=status)[1:],
            NOT_OPEN_INSERT,
        )
        expect_refused(
            f"as the migration owner, cannot insert a session already {status}",
            insert_session(owner_engine, status=status)[1:],
            NOT_OPEN_INSERT,
        )
    check("no non-open session was inserted by the attempts", count_sessions() == before)

    # ---- attack 3: a created_at that is not the database's now() ---------------
    for label, offset in (
        ("forward-dated 10 minutes", timedelta(minutes=10)),
        ("forward-dated 61 seconds (just past the window)", timedelta(seconds=61 + 2)),
        ("forward-dated 23 hours (stretching a 24h expiry)", timedelta(hours=23)),
        ("backdated 10 minutes", timedelta(minutes=-10)),
        ("backdated 63 seconds (just past the window)", timedelta(seconds=-63)),
    ):
        expect_refused(
            f"as sovra_app, a {label} created_at is refused",
            insert_session(created_offset=offset)[1:],
            BAD_CLOCK,
        )
    expect_refused(
        "as the migration owner, a forward-dated created_at is refused",
        insert_session(owner_engine, created_offset=timedelta(minutes=10))[1:],
        BAD_CLOCK,
    )
    check("no out-of-window session was inserted by the attempts", count_sessions() == before)

    # ---- the normal store path still works -------------------------------------
    ctx = TenantContext(organization_id=org_id)
    actor = store.UploadActor(actor_type="user", actor_id="user-p2", request_id="req-p2")

    def run_store(fn: Callable[[Session], Any]) -> tuple[Any, BaseException | None]:
        try:
            with tenant_scoped_session_for(factory, ctx) as session:
                return fn(session), None
        except BaseException as exc:  # noqa: BLE001 -- the test inspects the type
            return None, exc

    def open_via_store(ttl: timedelta = timedelta(hours=1)) -> Any:
        record, error = run_store(
            lambda s: store.create_upload_session(
                s,
                ctx,
                project_id=project_id,
                connection_id=connection_id,
                purpose="training",
                declared_size_bytes=1000,
                declared_sha256=_sha("a"),
                declared_content_type="application/jsonl",
                max_bytes=2000,
                ttl=ttl,
                actor=actor,
            )
        )
        check(f"the store opens a session on the hardened database ({error!r})", error is None)
        return record

    to_verify = open_via_store()
    dataset, error = run_store(
        lambda s: store.record_upload_verified_and_create_dataset(
            s,
            ctx,
            project_id=project_id,
            upload_session_id=to_verify.id,
            dataset_name="p2-dataset",
            verified_size_bytes=1000,
            verified_sha256=_sha("a"),
            verified_content_type="application/jsonl",
            actor=actor,
        )
    )
    check(
        f"the store verifies a session and creates its dataset version ({error!r})",
        error is None and dataset is not None and dataset.version == 1,
    )
    check("that session is verified", status_of(to_verify.id) == "verified")
    to_reject = open_via_store()
    _, error = run_store(
        lambda s: store.record_upload_rejected(
            s,
            ctx,
            project_id=project_id,
            upload_session_id=to_reject.id,
            reason="size_mismatch",
            actor=actor,
        )
    )
    check(f"the store rejects an open session ({error!r})", error is None)
    check("that session is rejected", status_of(to_reject.id) == "rejected")
    to_expire = open_via_store(ttl=timedelta(minutes=1))
    swept, error = run_store(
        lambda s: store.expire_open_sessions(
            s, ctx, now=datetime.now(UTC) + timedelta(minutes=5), actor=actor
        )
    )
    check(f"the store's expiry sweep runs ({error!r})", error is None and (swept or 0) >= 1)
    check("the swept session is expired", status_of(to_expire.id) == "expired")

    # ---- Law 1: every protection shown able to fail ----------------------------
    reopen_target = closed_session("rejected")
    freeze_0024 = migration._FREEZE_FUNCTION_0024
    freeze_0025 = migration._FREEZE_FUNCTION_0025
    insert_guard = migration._INSERT_GUARD_FUNCTION

    def owner_sql(sql: str) -> None:
        with owner_engine.begin() as conn:
            conn.execute(text(sql))

    def attacks_succeed(label: str, *, reopen: bool, insert_verified: bool, forward: bool) -> None:
        if reopen:
            sid = closed_session("rejected")
            expect_allowed(
                f"[{label}] the reopen attack SUCCEEDS (so the test can fail)",
                move(app_engine, sid, reopen_sql()),
            )
        if insert_verified:
            expect_allowed(
                f"[{label}] inserting a session already verified SUCCEEDS (so the test can fail)",
                insert_session(status="verified")[1:],
            )
        if forward:
            expect_allowed(
                f"[{label}] a forward-dated created_at SUCCEEDS (so the test can fail)",
                insert_session(created_offset=timedelta(minutes=10))[1:],
            )

    # Fault A: the BEFORE INSERT trigger dropped.
    owner_sql("DROP TRIGGER tr_dataset_upload_sessions_insert_guard ON dataset_upload_sessions")
    attacks_succeed("insert trigger dropped", reopen=False, insert_verified=True, forward=True)
    reopen_refused = move(app_engine, reopen_target, reopen_sql())
    expect_refused(
        "[insert trigger dropped] the transition guard still refuses a reopen",
        reopen_refused,
        REOPEN,
    )
    owner_sql(
        "CREATE TRIGGER tr_dataset_upload_sessions_insert_guard BEFORE INSERT ON "
        "dataset_upload_sessions FOR EACH ROW EXECUTE FUNCTION "
        "dataset_upload_sessions_insert_guard()"
    )
    expect_refused(
        "[restored] a forward-dated created_at is refused again",
        insert_session(created_offset=timedelta(minutes=10))[1:],
        BAD_CLOCK,
    )

    # Fault B: the transition check removed (the 0024 function body).
    owner_sql(freeze_0024)
    attacks_succeed("transition check removed", reopen=True, insert_verified=False, forward=False)
    owner_sql(freeze_0025)
    expect_refused(
        "[restored] a rejected session cannot be reopened again",
        move(app_engine, closed_session("rejected"), reopen_sql()),
        REOPEN,
    )

    # Fault C1/C2: each branch of the insert check removed on its own.
    owner_sql(
        insert_guard.replace("CREATE FUNCTION", "CREATE OR REPLACE FUNCTION").replace(
            "IF NEW.status <> 'open' THEN", "IF false THEN"
        )
    )
    attacks_succeed(
        "insert status check removed", reopen=False, insert_verified=True, forward=False
    )
    expect_refused(
        "[insert status check removed] the created_at check still refuses a forward date",
        insert_session(created_offset=timedelta(minutes=10))[1:],
        BAD_CLOCK,
    )
    owner_sql(
        insert_guard.replace("CREATE FUNCTION", "CREATE OR REPLACE FUNCTION").replace(
            "NEW.created_at > now() + interval '60 seconds'", "false"
        )
    )
    attacks_succeed("forward-date check removed", reopen=False, insert_verified=False, forward=True)
    owner_sql(insert_guard.replace("CREATE FUNCTION", "CREATE OR REPLACE FUNCTION"))
    expect_refused(
        "[restored] a forward-dated created_at is refused once more",
        insert_session(created_offset=timedelta(minutes=10))[1:],
        BAD_CLOCK,
    )

    # Migration 0024 alone (downgrade -1) has none of the three protections.
    _migrate(
        migration_url,
        ["downgrade", "0024_dataset_uploads"],
        "alembic downgrade 0024_dataset_uploads (0024 only, whatever head is)",
    )
    attacks_succeed("at migration 0024", reopen=True, insert_verified=True, forward=True)
    _migrate(migration_url, ["upgrade", "head"], "alembic upgrade head (back to head)")
    expect_refused(
        "[re-upgraded] a rejected session cannot be reopened",
        move(app_engine, closed_session("rejected"), reopen_sql()),
        REOPEN,
    )
    expect_refused(
        "[re-upgraded] inserting a session already verified is refused",
        insert_session(status="verified")[1:],
        NOT_OPEN_INSERT,
    )
    expect_refused(
        "[re-upgraded] a forward-dated created_at is refused",
        insert_session(created_offset=timedelta(minutes=10))[1:],
        BAD_CLOCK,
    )

    _finish()


if __name__ == "__main__":
    main()
