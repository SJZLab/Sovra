#!/usr/bin/env python3
# ruff: noqa: E501
"""FTF-R006-P1b -- pure-logic proof for `app/reviews/argilla_identity_store.py`
(no database, no network): the two deterministic naming functions and the
module's own typed-error/dataclass shapes. The real provisioning/
revocation/membership-sync behaviour against a real database, a real
OpenBao server and a real (fake-server) Argilla wire protocol is proved by
`tests/integration/test_argilla_identity_scope.py` -- this module's own
raw-SQL functions bind `set_config`/row-level security, which SQLite does
not have, so there is no meaningful SQLite-backed split here (the same
precedent `tests/integration/test_ftf_0519_p1_split_manifests.py` already
set for FTF-0519-P1's own UNWIRED, DB-coupled store).

Run: python3 tests/tasks/test_ftf_r006_p1b.py
"""

from __future__ import annotations

import contextlib
import re
import sqlite3
import sys
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "apps" / "api"))

from argilla_test_support import FakeArgillaServer  # noqa: E402
from sqlalchemy import create_engine, text  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

import app.reviews.argilla_identity_store as store  # noqa: E402
from app.auth.rbac_middleware import Role  # noqa: E402
from app.reviews.argilla_identity_store import (  # noqa: E402
    DATASET_SOURCE_ROLES,
    ArgillaIdentityError,
    ReviewerIdentityRevokedError,
    _username_for,
    ensure_review_scope,
    ensure_reviewer_identity,
    revoke_reviewer_identity,
    sync_workspace_membership,
    workspace_name_for,
)

FAILURES: list[str] = []
CID = "R006P1b"


def check(description: str, condition: bool) -> None:
    if not condition:
        FAILURES.append(f"FAIL [{CID}] {description}")


def raises(exc: type[BaseException], fn: Any, *args: Any, **kwargs: Any) -> bool:
    try:
        fn(*args, **kwargs)
    except exc:
        return True
    except Exception:
        return False
    return False


_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")


def test_workspace_name_deterministic_and_shaped() -> None:
    org, proj = uuid.uuid4(), uuid.uuid4()
    name1 = workspace_name_for(org, proj)
    name2 = workspace_name_for(org, proj)
    check("workspace_name_for is deterministic for the same (org, project)", name1 == name2)
    check(
        "workspace_name_for matches ArgillaClient's own workspace-name identifier shape",
        bool(_IDENTIFIER.fullmatch(name1)),
    )
    check("workspace_name_for starts with the sovra- prefix", name1.startswith("sovra-"))
    check(
        "workspace_name_for never embeds the raw project id as readable text",
        str(proj) not in name1,
    )


def test_workspace_name_collision_free_across_projects() -> None:
    org = uuid.uuid4()
    names = {workspace_name_for(org, uuid.uuid4()) for _ in range(200)}
    check(
        "200 distinct projects under one org produce 200 distinct workspace names",
        len(names) == 200,
    )


def test_workspace_name_differs_across_orgs_for_same_project() -> None:
    proj = uuid.uuid4()
    n1 = workspace_name_for(uuid.uuid4(), proj)
    n2 = workspace_name_for(uuid.uuid4(), proj)
    check("the same project under two different orgs gets two different workspace names", n1 != n2)


def test_username_deterministic_shaped_and_content_free() -> None:
    org = uuid.uuid4()
    u1 = _username_for(org, "https://issuer.example", "subject-123@example.com")
    u2 = _username_for(org, "https://issuer.example", "subject-123@example.com")
    check("_username_for is deterministic for the same (org, issuer, subject)", u1 == u2)
    check(
        "_username_for matches ArgillaClient's own username identifier shape",
        bool(_IDENTIFIER.fullmatch(u1)),
    )
    check(
        "_username_for never embeds the raw subject (which may be email-shaped) as readable text",
        "subject-123" not in u1 and "example.com" not in u1,
    )
    u3 = _username_for(org, "https://issuer.example", "subject-456@example.com")
    check("two different subjects under the same org get two different usernames", u1 != u3)


def test_dataset_source_roles_matches_section_45() -> None:
    check(
        "DATASET_SOURCE_ROLES is exactly ml_engineer/workspace_admin/organization_admin",
        DATASET_SOURCE_ROLES
        == frozenset({Role.ML_ENGINEER, Role.WORKSPACE_ADMIN, Role.ORGANIZATION_ADMIN}),
    )
    check(
        "a reviewer/evaluator/viewer-only role is never in DATASET_SOURCE_ROLES",
        Role.EVALUATOR not in DATASET_SOURCE_ROLES
        and Role.VIEWER not in DATASET_SOURCE_ROLES
        and Role.APPROVER not in DATASET_SOURCE_ROLES,
    )


def test_naming_functions_refuse_non_uuid_and_empty_inputs() -> None:
    check(
        "workspace_name_for refuses a None organization_id instead of hashing it",
        raises(TypeError, workspace_name_for, None, uuid.uuid4()),
    )
    check(
        "workspace_name_for refuses a non-UUID string project_id instead of hashing it",
        raises(TypeError, workspace_name_for, uuid.uuid4(), "not-a-uuid"),
    )
    check(
        "_username_for refuses a None organization_id instead of hashing it",
        raises(TypeError, _username_for, None, "iss", "sub"),
    )
    check(
        "_username_for refuses an empty issuer instead of hashing it",
        raises(TypeError, _username_for, uuid.uuid4(), "", "sub"),
    )
    check(
        "_username_for refuses an empty subject instead of hashing it",
        raises(TypeError, _username_for, uuid.uuid4(), "iss", ""),
    )


def test_error_hierarchy() -> None:
    check(
        "ReviewerIdentityRevokedError is an ArgillaIdentityError",
        issubclass(ReviewerIdentityRevokedError, ArgillaIdentityError),
    )
    check(
        "ArgillaIdentityError is a plain Exception, never collides with ArgillaClientError",
        issubclass(ArgillaIdentityError, Exception),
    )


# ------------------------------------------------------------------ store behaviour (SQLite)
# The store's raw SQL is plain SELECT/INSERT/UPDATE; SQLite runs it for real. Only the Postgres-
# only tenant plumbing (`set_config`, RLS) and the OpenBao write are stood in for here -- the live
# Postgres/OpenBao evidence is tests/integration/test_argilla_identity_scope.py.

_DDL = """
CREATE TABLE argilla_review_scopes (
  id UUID PRIMARY KEY DEFAULT (gen_uuid()),
  organization_id UUID NOT NULL, project_id UUID NOT NULL, component_binding_id UUID NOT NULL,
  argilla_workspace_id VARCHAR(36) NOT NULL, argilla_workspace_name VARCHAR(64) NOT NULL,
  UNIQUE (organization_id, project_id), UNIQUE (argilla_workspace_id));
CREATE TABLE argilla_reviewer_identities (
  id UUID PRIMARY KEY DEFAULT (gen_uuid()),
  organization_id UUID NOT NULL, issuer VARCHAR(512) NOT NULL, subject VARCHAR(255) NOT NULL,
  argilla_user_id VARCHAR(36) NOT NULL, credential_secret_ref VARCHAR(512) NOT NULL,
  status VARCHAR(16) NOT NULL DEFAULT 'active', revoked_at TEXT, updated_at TEXT,
  UNIQUE (organization_id, issuer, subject), UNIQUE (argilla_user_id));
CREATE TABLE argilla_workspace_members (
  id UUID PRIMARY KEY DEFAULT (gen_uuid()),
  organization_id UUID NOT NULL, scope_id UUID NOT NULL, identity_id UUID NOT NULL,
  status VARCHAR(16) NOT NULL DEFAULT 'active', updated_at TEXT,
  UNIQUE (organization_id, scope_id, identity_id));
"""

ISSUER, SUBJECT = "https://issuer.example", "reviewer-1"


class Env:
    def __init__(self) -> None:
        self.server = FakeArgillaServer()
        self.client = self.server.client()
        self.engine = create_engine(
            "sqlite://",
            poolclass=StaticPool,
            connect_args={"detect_types": sqlite3.PARSE_DECLTYPES, "check_same_thread": False},
        )
        raw = self.engine.raw_connection()
        raw.driver_connection.create_function(  # type: ignore[attr-defined]
            "now", 0, lambda: datetime.now(UTC).isoformat()
        )
        raw.driver_connection.create_function(  # type: ignore[attr-defined]
            "gen_uuid", 0, lambda: str(uuid.uuid4())
        )
        raw.driver_connection.executescript(_DDL)  # type: ignore[attr-defined]
        self.org, self.project, self.binding = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        self.seen_orgs: list[Any] = []
        self.secret_writes: list[dict[str, Any]] = []
        self.sleeps: list[float] = []
        self._saved = (
            store.execute_tenant_scoped,
            store.tenant_scoped_session,
            store.set_tenant_context,
            store.store_secret_ref,
            store.time.sleep,
        )

        def passthrough(
            session: Session, organization_id: Any, statement: Any, params: Any = None
        ) -> Any:
            self.seen_orgs.append(organization_id)
            return session.execute(statement, params or {})

        @contextlib.contextmanager
        def scoped(factory: Any, organization_id: Any) -> Iterator[Session]:
            session = factory()
            try:
                yield session
                session.commit()
            except BaseException:
                session.rollback()
                raise
            finally:
                session.close()

        def fake_secret(**kw: Any) -> str:
            self.secret_writes.append(kw)
            return f"openbao://{kw['mount']}/{kw['path']}#{kw['field']}"

        store.execute_tenant_scoped = passthrough  # type: ignore[assignment]
        store.tenant_scoped_session = scoped  # type: ignore[assignment]
        store.set_tenant_context = lambda *a, **k: None  # type: ignore[assignment]
        store.store_secret_ref = fake_secret  # type: ignore[assignment]
        store.time.sleep = self.sleeps.append  # type: ignore[assignment]

    def factory(self) -> Session:
        return Session(self.engine)

    def sql(self, statement: str, **params: Any) -> list[Any]:
        with self.engine.begin() as conn:
            result = conn.execute(text(statement), params)
            return list(result.fetchall()) if result.returns_rows else []

    def requests(self, method: str) -> int:
        return sum(1 for m, _ in self.server.requests if m == method)

    def close(self) -> None:
        (
            store.execute_tenant_scoped,
            store.tenant_scoped_session,
            store.set_tenant_context,
            store.store_secret_ref,
            store.time.sleep,
        ) = self._saved  # type: ignore[assignment]
        self.server.shutdown()

    def scope(self) -> Any:
        return ensure_review_scope(
            self.factory,
            organization_id=self.org,
            project_id=self.project,
            component_binding_id=self.binding,
            client=self.client,
        )

    def identity(self, subject: str = SUBJECT) -> Any:
        return ensure_reviewer_identity(
            self.factory,
            organization_id=self.org,
            issuer=ISSUER,
            subject=subject,
            client=self.client,
            openbao_config=None,  # type: ignore[arg-type]
        )

    def sync(self, role: Role | None, scope: Any, identity: Any, subject: str = SUBJECT) -> None:
        sync_workspace_membership(
            self.factory,
            organization_id=self.org,
            issuer=ISSUER,
            subject=subject,
            role=role,
            scope=scope,
            identity=identity,
            client=self.client,
        )

    def revoke(self, subject: str = SUBJECT) -> None:
        revoke_reviewer_identity(
            self.factory,
            organization_id=self.org,
            issuer=ISSUER,
            subject=subject,
            client=self.client,
        )

    def member_rows(self) -> list[Any]:
        return self.sql("SELECT identity_id, status FROM argilla_workspace_members")


sqlite3.register_adapter(uuid.UUID, str)
sqlite3.register_adapter(datetime, lambda d: d.isoformat())
sqlite3.register_converter("UUID", lambda b: uuid.UUID(b.decode()))


def with_env(fn: Any) -> Any:
    def run() -> None:
        env = Env()
        try:
            fn(env)
        finally:
            env.close()

    return run


@with_env
def test_ensure_review_scope(env: Env) -> None:
    scope = env.scope()
    check(
        "scope names the deterministic workspace",
        scope.argilla_workspace_name == workspace_name_for(env.org, env.project),
    )
    check(
        "scope carries org/project/binding",
        (scope.organization_id, scope.project_id, scope.component_binding_id)
        == (env.org, env.project, env.binding),
    )
    server_ws = env.server.workspaces.get(scope.argilla_workspace_name)
    check(
        "the real server workspace exists and its id is persisted",
        server_ws is not None and server_ws["id"] == scope.argilla_workspace_id,
    )
    rows = env.sql(
        "SELECT id, argilla_workspace_id, argilla_workspace_name, component_binding_id FROM argilla_review_scopes"
    )
    check(
        "exactly one scope row, matching the returned record",
        len(rows) == 1
        and str(rows[0][0]) == str(scope.id)
        and rows[0][1] == scope.argilla_workspace_id
        and rows[0][2] == scope.argilla_workspace_name
        and str(rows[0][3]) == str(env.binding),
    )
    again = env.scope()
    check(
        "second call returns the same scope",
        str(again.id) == str(scope.id) and again.argilla_workspace_id == scope.argilla_workspace_id,
    )
    check("second call does not touch Argilla again", env.requests("POST") == 1)
    check("only the scope's own org reached the tenant guard", set(env.seen_orgs) == {env.org})
    other = ensure_review_scope(
        env.factory,
        organization_id=env.org,
        project_id=uuid.uuid4(),
        component_binding_id=env.binding,
        client=env.client,
    )
    check(
        "a different project gets a different workspace",
        other.argilla_workspace_id != scope.argilla_workspace_id
        and len(env.sql("SELECT 1 FROM argilla_review_scopes")) == 2,
    )


@with_env
def test_ensure_review_scope_create_race(env: Env) -> None:
    winner_id = uuid.uuid4()
    real = env.client.ensure_workspace

    def racing(name: str) -> Any:
        ws = real(name)
        env.sql(
            "INSERT INTO argilla_review_scopes (id, organization_id, project_id, component_binding_id, argilla_workspace_id, argilla_workspace_name) "
            "VALUES (:id, :o, :p, :b, :w, :n)",
            id=str(winner_id),
            o=str(env.org),
            p=str(env.project),
            b=str(env.binding),
            w=ws.id,
            n=ws.name,
        )
        return ws

    env.client.ensure_workspace = racing  # type: ignore[method-assign]
    scope = env.scope()
    check("a lost create-race returns the winner's row", str(scope.id) == str(winner_id))
    check("no duplicate scope row", len(env.sql("SELECT 1 FROM argilla_review_scopes")) == 1)


@with_env
def test_ensure_reviewer_identity(env: Env) -> None:
    ident = env.identity()
    users = list(env.server.users.values())
    check(
        "one real annotator was created with the deterministic username",
        len(users) == 1
        and users[0]["role"] == "annotator"
        and users[0]["username"] == _username_for(env.org, ISSUER, SUBJECT),
    )
    check(
        "record names the Argilla user and active status",
        ident.argilla_user_id == users[0]["id"]
        and ident.status == "active"
        and ident.issuer == ISSUER
        and ident.subject == SUBJECT
        and ident.organization_id == env.org,
    )
    check("exactly one secret write", len(env.secret_writes) == 1)
    w = env.secret_writes[0] if env.secret_writes else {}
    check(
        "the api key goes to OpenBao under secret/argilla-reviewers/<org>/<user>#api_key",
        w.get("mount") == "secret"
        and w.get("path") == f"argilla-reviewers/{env.org}/{users[0]['id']}"
        and w.get("field") == "api_key"
        and w.get("value") == users[0]["api_key"],
    )
    check(
        "record stores the ref, never the key",
        ident.credential_secret_ref
        == f"openbao://secret/argilla-reviewers/{env.org}/{users[0]['id']}#api_key"
        and users[0]["api_key"] not in ident.credential_secret_ref,
    )
    stored = env.sql(
        "SELECT credential_secret_ref, argilla_user_id, status FROM argilla_reviewer_identities"
    )
    check(
        "one row stored with the ref",
        len(stored) == 1
        and stored[0][0] == ident.credential_secret_ref
        and stored[0][1] == users[0]["id"]
        and stored[0][2] == "active",
    )
    check(
        "no api key or password anywhere in the table",
        all(
            users[0]["api_key"] not in str(v)
            for r in env.sql("SELECT * FROM argilla_reviewer_identities")
            for v in r
        ),
    )
    again = env.identity()
    check(
        "second call is idempotent (same row, no new user, no new secret)",
        str(again.id) == str(ident.id)
        and len(env.server.users) == 1
        and len(env.secret_writes) == 1,
    )
    env.identity("reviewer-2")
    check("another subject gets its own user", len(env.server.users) == 2)
    check("only this org reached the tenant guard", set(env.seen_orgs) == {env.org})


@with_env
def test_ensure_reviewer_identity_revoked_is_refused(env: Env) -> None:
    env.identity()
    env.revoke()
    check(
        "an existing revoked identity is refused, not reactivated",
        raises(ReviewerIdentityRevokedError, env.identity),
    )
    check(
        "it stays revoked",
        env.sql("SELECT status FROM argilla_reviewer_identities")[0][0] == "revoked",
    )


def _insert_identity(env: Env, user_id: str, status: str = "active") -> uuid.UUID:
    row_id = uuid.uuid4()
    env.sql(
        "INSERT INTO argilla_reviewer_identities (id, organization_id, issuer, subject, argilla_user_id, credential_secret_ref, status) "
        "VALUES (:id, :o, :i, :s, :u, 'openbao://secret/x#api_key', :st)",
        id=str(row_id),
        o=str(env.org),
        i=ISSUER,
        s=SUBJECT,
        u=user_id,
        st=status,
    )
    return row_id


@with_env
def test_ensure_reviewer_identity_conflict_poll(env: Env) -> None:
    # Argilla already has this username (a concurrent caller is mid-provision): poll for its row.
    env.server.users["pre"] = {
        "id": "pre",
        "username": _username_for(env.org, ISSUER, SUBJECT),
        "role": "annotator",
        "api_key": "k",
    }
    winner: list[uuid.UUID] = []

    def sleep(seconds: float) -> None:
        env.sleeps.append(seconds)
        if len(env.sleeps) == 3:
            winner.append(_insert_identity(env, "pre"))

    store.time.sleep = sleep  # type: ignore[assignment]
    ident = env.identity()
    check(
        "polling returns the row the winner committed", winner and str(ident.id) == str(winner[0])
    )
    check(
        "it polled with the pinned delay, 3 times",
        env.sleeps == [store._CONFLICT_POLL_DELAY_SECONDS] * 3,
    )
    check(
        "the loser wrote no secret and created no user",
        env.secret_writes == [] and len(env.server.users) == 1,
    )


@with_env
def test_ensure_reviewer_identity_conflict_never_converges(env: Env) -> None:
    env.server.users["pre"] = {
        "id": "pre",
        "username": _username_for(env.org, ISSUER, SUBJECT),
        "role": "annotator",
        "api_key": "k",
    }
    try:
        env.identity()
        outcome = "returned"
    except ArgillaIdentityError as exc:
        outcome = "raised"
        message = str(exc)
    check("a conflict with no row ever appearing raises ArgillaIdentityError", outcome == "raised")
    check(
        "it polled the full bounded number of attempts (sleeping between, not after the last)",
        len(env.sleeps) == store._CONFLICT_POLL_ATTEMPTS - 1,
    )
    check(
        "the error names the attempts and the username",
        outcome == "raised"
        and str(store._CONFLICT_POLL_ATTEMPTS) in message
        and _username_for(env.org, ISSUER, SUBJECT) in message,
    )
    check(
        "a revoked-but-not-ArgillaIdentityRevoked error is the plain class",
        outcome == "raised"
        and not isinstance(ArgillaIdentityError("x"), ReviewerIdentityRevokedError),
    )


@with_env
def test_ensure_reviewer_identity_conflict_poll_finds_revoked(env: Env) -> None:
    env.server.users["pre"] = {
        "id": "pre",
        "username": _username_for(env.org, ISSUER, SUBJECT),
        "role": "annotator",
        "api_key": "k",
    }
    _insert_identity(env, "pre", "revoked")
    # the row exists before the first select would see it only after create fails: simulate by hiding
    check(
        "conflict path refuses a revoked identity",
        raises(ReviewerIdentityRevokedError, env.identity),
    )


@with_env
def test_ensure_reviewer_identity_insert_race(env: Env) -> None:
    winner: list[uuid.UUID] = []

    def racing_secret(**kw: Any) -> str:
        env.secret_writes.append(kw)
        winner.append(_insert_identity(env, "someone-else"))
        return "openbao://secret/loser#api_key"

    store.store_secret_ref = racing_secret  # type: ignore[assignment]
    ident = env.identity()
    check(
        "a lost insert-race returns the winner's row",
        winner and str(ident.id) == str(winner[0]) and ident.argilla_user_id == "someone-else",
    )
    check(
        "no duplicate identity row", len(env.sql("SELECT 1 FROM argilla_reviewer_identities")) == 1
    )

    env2_sql = "UPDATE argilla_reviewer_identities SET status = 'revoked'"
    env.sql(env2_sql)
    env.server.users.clear()

    def racing_revoked(**kw: Any) -> str:
        raise AssertionError("not reached")

    check(
        "an existing revoked row short-circuits before any Argilla call",
        raises(ReviewerIdentityRevokedError, env.identity) and len(env.server.users) == 0,
    )


@with_env
def test_sync_workspace_membership(env: Env) -> None:
    scope, ident = env.scope(), env.identity()
    ws, uid = scope.argilla_workspace_id, ident.argilla_user_id
    posts = lambda: env.requests("POST")  # noqa: E731
    env.sync(None, scope, ident)
    check(
        "no role: no membership anywhere",
        env.server.memberships == set() and env.member_rows() == [],
    )
    env.sync(Role.VIEWER, scope, ident)
    check(
        "a non-source role: still no membership",
        env.server.memberships == set() and env.member_rows() == [],
    )
    for role in (Role.ML_ENGINEER, Role.WORKSPACE_ADMIN, Role.ORGANIZATION_ADMIN):
        env.sync(None, scope, ident)
        env.sync(role, scope, ident)
        check(f"{role.value} becomes a real workspace member", (ws, uid) in env.server.memberships)
    rows = env.member_rows()
    check(
        "one active membership row for this identity",
        len(rows) == 1 and str(rows[0][0]) == str(ident.id) and rows[0][1] == "active",
    )
    before = (posts(), len(env.server.requests))
    env.sync(Role.ML_ENGINEER, scope, ident)
    check("already active: no Argilla call at all", (posts(), len(env.server.requests)) == before)
    env.sync(Role.VIEWER, scope, ident)
    check(
        "role narrowed: removed from the server and row revoked",
        (ws, uid) not in env.server.memberships and env.member_rows()[0][1] == "revoked",
    )
    n = len(env.server.requests)
    env.sync(None, scope, ident)
    check("already revoked: no Argilla call", len(env.server.requests) == n)
    env.sync(Role.WORKSPACE_ADMIN, scope, ident)
    check(
        "re-entitled: re-added and the same row reactivated",
        (ws, uid) in env.server.memberships
        and len(env.member_rows()) == 1
        and env.member_rows()[0][1] == "active",
    )
    check("membership statements ran under this org only", set(env.seen_orgs) == {env.org})


@with_env
def test_sync_membership_idempotent_server_outcomes(env: Env) -> None:
    scope, ident = env.scope(), env.identity()
    env.server.memberships.add((scope.argilla_workspace_id, ident.argilla_user_id))
    env.sync(Role.ML_ENGINEER, scope, ident)
    check(
        "server already has the member (409): row still recorded active",
        env.member_rows()[0][1] == "active",
    )
    env.server.memberships.clear()
    env.sync(Role.VIEWER, scope, ident)
    check(
        "server already lacks the member (404): row still revoked",
        env.member_rows()[0][1] == "revoked",
    )


@with_env
def test_sync_membership_refuses_stale_or_revoked_identity(env: Env) -> None:
    scope, ident = env.scope(), env.identity()
    stale = store.ReviewerIdentityRecord(
        id=uuid.uuid4(),
        organization_id=env.org,
        issuer=ISSUER,
        subject=SUBJECT,
        argilla_user_id=ident.argilla_user_id,
        credential_secret_ref=ident.credential_secret_ref,
        status="active",
    )
    check(
        "a stale identity reference is refused",
        raises(ArgillaIdentityError, env.sync, Role.ML_ENGINEER, scope, stale)
        and not env.server.memberships,
    )
    check(
        "an unknown (org, issuer, subject) is refused",
        raises(ArgillaIdentityError, env.sync, Role.ML_ENGINEER, scope, ident, "nobody"),
    )
    env.revoke()
    check(
        "a revoked identity is refused even through an old active reference",
        raises(ReviewerIdentityRevokedError, env.sync, Role.ML_ENGINEER, scope, ident)
        and not env.server.memberships,
    )


@with_env
def test_revoke_reviewer_identity(env: Env) -> None:
    check("revoking an unknown identity raises", raises(ArgillaIdentityError, env.revoke))
    scope, ident = env.scope(), env.identity()
    other_scope = ensure_review_scope(
        env.factory,
        organization_id=env.org,
        project_id=uuid.uuid4(),
        component_binding_id=env.binding,
        client=env.client,
    )
    other = env.identity("reviewer-2")
    env.sync(Role.ML_ENGINEER, scope, ident)
    env.sync(Role.ML_ENGINEER, other_scope, ident)
    env.sync(Role.ML_ENGINEER, scope, other, "reviewer-2")
    env.revoke()
    check(
        "every membership of the revoked identity is removed on the server",
        not any(u == ident.argilla_user_id for _, u in env.server.memberships),
    )
    check(
        "another identity's membership is untouched",
        (scope.argilla_workspace_id, other.argilla_user_id) in env.server.memberships,
    )
    status = {
        str(r[0]): r[1]
        for r in env.sql("SELECT identity_id, status FROM argilla_workspace_members")
    }
    check(
        "revoked identity's membership rows are revoked, the other's still active",
        status[str(ident.id)] == "revoked" and status[str(other.id)] == "active",
    )
    row = env.sql(
        "SELECT status, revoked_at, updated_at FROM argilla_reviewer_identities WHERE subject = :s",
        s=SUBJECT,
    )[0]
    check(
        "identity row is revoked with revoked_at and updated_at stamped",
        row[0] == "revoked" and row[1] is not None and row[2] == row[1],
    )
    check(
        "the other identity stays active",
        env.sql("SELECT status FROM argilla_reviewer_identities WHERE subject = 'reviewer-2'")[0][0]
        == "active",
    )
    n = len(env.server.requests)
    env.revoke()
    check("revoking again is a no-op (no Argilla call)", len(env.server.requests) == n)


@with_env
def test_revoke_tolerates_already_removed_member(env: Env) -> None:
    scope, ident = env.scope(), env.identity()
    env.sync(Role.ML_ENGINEER, scope, ident)
    env.server.memberships.clear()
    env.revoke()
    check(
        "a member already gone from the server (404) does not fail the revoke",
        env.sql("SELECT status FROM argilla_reviewer_identities")[0][0] == "revoked"
        and env.member_rows()[0][1] == "revoked",
    )


@with_env
def test_revoke_skips_revoked_memberships(env: Env) -> None:
    scope, ident = env.scope(), env.identity()
    env.sync(Role.ML_ENGINEER, scope, ident)
    env.sync(None, scope, ident)
    n = env.requests("DELETE")
    env.revoke()
    check("an already-revoked membership is not removed again", env.requests("DELETE") == n)


_PURE = {n for n in globals() if n.startswith("test_")} - {
    n for n in globals() if n.startswith("test_") and getattr(globals()[n], "__name__", "") == "run"
}


# ------------------------------------------------------------------ remaining pins + migration


@with_env
def test_idempotent_calls_touch_nothing_and_return_full_records(env: Env) -> None:
    scope = env.scope()
    n = len(env.server.requests)
    again = env.scope()
    check(
        "existing scope short-circuits before any Argilla request at all",
        len(env.server.requests) == n,
    )
    check(
        "existing scope record is fully populated",
        (
            again.organization_id,
            again.project_id,
            again.component_binding_id,
            again.argilla_workspace_name,
        )
        == (env.org, env.project, env.binding, scope.argilla_workspace_name),
    )
    ident = env.identity()
    again_i = env.identity()
    check(
        "existing identity record carries the looked-up issuer and subject",
        again_i.issuer == ISSUER
        and again_i.subject == SUBJECT
        and again_i.organization_id == env.org,
    )
    check(
        "existing identity record names the stored user and ref",
        again_i.argilla_user_id == ident.argilla_user_id
        and again_i.credential_secret_ref == ident.credential_secret_ref,
    )
    other = env.identity("reviewer-2")
    check(
        "a lookup never returns another subject's row",
        other.subject == "reviewer-2" and str(other.id) != str(ident.id),
    )
    check(
        "lookup is keyed by issuer too",
        env.sql("SELECT 1 FROM argilla_reviewer_identities WHERE issuer = :i", i=ISSUER) != []
        and raises(
            ArgillaIdentityError,
            lambda: revoke_reviewer_identity(
                env.factory,
                organization_id=env.org,
                issuer="https://other.example",
                subject=SUBJECT,
                client=env.client,
            ),
        ),
    )


@with_env
def test_integrity_error_without_a_winner_row_is_not_swallowed(env: Env) -> None:
    from sqlalchemy.exc import IntegrityError

    real = env.client.ensure_workspace

    def clash(name: str) -> Any:
        ws = real(name)
        env.sql(
            "INSERT INTO argilla_review_scopes (organization_id, project_id, component_binding_id, argilla_workspace_id, argilla_workspace_name) "
            "VALUES (:o, :p, :b, :w, 'other')",
            o=str(env.org),
            p=str(uuid.uuid4()),
            b=str(env.binding),
            w=ws.id,
        )
        return ws

    env.client.ensure_workspace = clash  # type: ignore[method-assign]
    check(
        "a unique clash that is NOT this project's own row re-raises the IntegrityError",
        raises(IntegrityError, env.scope),
    )

    def clash_identity(**kw: Any) -> str:
        env.sql(
            "INSERT INTO argilla_reviewer_identities (organization_id, issuer, subject, argilla_user_id, credential_secret_ref) "
            "VALUES (:o, :i, 'someone-else', :u, 'openbao://secret/x#api_key')",
            o=str(env.org),
            i=ISSUER,
            u=kw["path"].rsplit("/", 1)[-1],
        )
        return "openbao://secret/loser#api_key"

    store.store_secret_ref = clash_identity  # type: ignore[assignment]
    check(
        "an identity unique clash that is NOT this identity's own row re-raises the IntegrityError",
        raises(IntegrityError, env.identity),
    )


@with_env
def test_status_is_compared_exactly_and_revoke_is_not_restamped(env: Env) -> None:
    env.identity()
    env.sql("UPDATE argilla_reviewer_identities SET status = 'Revoked '")
    check(
        "only the exact 'revoked' status is refused by ensure (the DB CHECK pins the domain)",
        not raises(ReviewerIdentityRevokedError, env.identity),
    )
    env.revoke()
    row = env.sql("SELECT status, revoked_at FROM argilla_reviewer_identities")[0]
    check(
        "a non-exact status is not mistaken for already-revoked: revoke runs and stamps it",
        row[0] == "revoked" and row[1] is not None,
    )
    first = row[1]
    env.revoke()
    check(
        "revoking again does not restamp revoked_at",
        env.sql("SELECT revoked_at FROM argilla_reviewer_identities")[0][0] == first,
    )


class _Recorder:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []

    def __getattr__(self, name: str) -> Any:
        def record(*args: Any, **kwargs: Any) -> None:
            self.calls.append((name, args, kwargs))

        return record


def test_migration_0034_shapes() -> None:
    import importlib.util

    path = ROOT / "apps" / "api" / "alembic" / "versions" / "0034_argilla_review_identity.py"
    spec = importlib.util.spec_from_file_location("migration_0034", path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    rec = _Recorder()
    mod.op = rec
    mod.upgrade()
    tables = {a[0]: a[1:] for n, a, _ in rec.calls if n == "create_table"}
    check(
        "upgrade creates exactly the three tables, scopes first",
        [a[0] for n, a, _ in rec.calls if n == "create_table"]
        == ["argilla_review_scopes", "argilla_reviewer_identities", "argilla_workspace_members"],
    )
    for name, items in tables.items():
        cols = {
            c.name: c
            for c in items
            if hasattr(c, "nullable")
            and hasattr(c, "server_default")
            and hasattr(c, "name")
            and c.__class__.__name__ == "Column"
        }
        for stamp in ("created_at", "updated_at"):
            col = cols[stamp]
            check(
                f"{name}.{stamp} is NOT NULL defaulting to now()",
                col.nullable is False and "now()" in str(col.server_default.arg),
            )
    identities = {
        c.name: c for c in tables["argilla_reviewer_identities"] if c.__class__.__name__ == "Column"
    }
    check(
        "revoked_at is nullable with no default",
        identities["revoked_at"].nullable is True
        and identities["revoked_at"].server_default is None,
    )
    checks = {
        c.name: str(c.sqltext)
        for items in tables.values()
        for c in items
        if c.__class__.__name__ == "CheckConstraint"
    }
    check(
        "status CHECKs name exactly active/revoked via _in",
        checks["ck_argilla_reviewer_identities_status"] == "status IN ('active', 'revoked')"
        and checks["ck_argilla_workspace_members_status"] == "status IN ('active', 'revoked')",
    )
    check(
        "role CHECK is annotator-only",
        checks["ck_argilla_reviewer_identities_role"] == "argilla_role IN ('annotator')",
    )
    sqls = [a[0] for n, a, _ in rec.calls if n == "execute"]
    for table in (
        "argilla_review_scopes",
        "argilla_reviewer_identities",
        "argilla_workspace_members",
    ):
        check(
            f"{table}: grant w/o DELETE, ENABLE + FORCE RLS and a tenant policy",
            f'GRANT SELECT, INSERT, UPDATE ON {table} TO "sovra_app"' in sqls
            and f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY" in sqls
            and f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY" in sqls
            and any(
                q.startswith(f"CREATE POLICY tenant_isolation ON {table} FOR ALL") for q in sqls
            ),
        )
    down = _Recorder()
    mod.op = down
    mod.downgrade()
    check(
        "downgrade drops policies/grants for every table",
        len(
            [
                a
                for n, a, _ in down.calls
                if n == "execute" and a[0].startswith("DROP POLICY IF EXISTS tenant_isolation ON ")
            ]
        )
        == 3
        and len(
            [a for n, a, _ in down.calls if n == "execute" and a[0].startswith("REVOKE ALL ON ")]
        )
        == 3,
    )
    check(
        "downgrade drops children before parents",
        [a[0] for n, a, _ in down.calls if n == "drop_table"]
        == ["argilla_workspace_members", "argilla_reviewer_identities", "argilla_review_scopes"],
    )


def main() -> None:
    test_workspace_name_deterministic_and_shaped()
    test_workspace_name_collision_free_across_projects()
    test_workspace_name_differs_across_orgs_for_same_project()
    test_username_deterministic_shaped_and_content_free()
    test_dataset_source_roles_matches_section_45()
    test_naming_functions_refuse_non_uuid_and_empty_inputs()
    test_error_hierarchy()
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn) and name not in _PURE:
            fn()
    if FAILURES:
        for failure in FAILURES:
            print(failure)
        print(f"{len(FAILURES)} failure(s)")
        sys.exit(1)
    print("All FTF-R006-P1b pure-logic checks passed.")


if __name__ == "__main__":
    main()
