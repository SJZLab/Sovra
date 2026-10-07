#!/usr/bin/env python3
# mypy: disable-error-code="arg-type,attr-defined,return-value,assignment,call-arg"
# ruff: noqa: E501
"""FTF-0308-P1 -- ModelCatalogEntry persistence + training-artifact mapping
(no-DB unit/fault tests).

Runnable without Postgres or network egress. The real live-database evidence
(row-level security, grants, composite foreign keys, the migration round trip,
cross-tenant denial) is
`tests/integration/test_ftf_0308_p1_model_catalog_mapping.py`, run in CI's
`model-catalog-mapping-integration` job.

Criteria (QUEUE Backlog, FTF-0308-P1):
  M1  migration 0022 shape: both tables ENABLE+FORCE RLS with a non-allow-all
      tenant policy, entries SELECT/INSERT/UPDATE, changes SELECT/INSERT only
      (append-only), composite FKs, version CHECKs, UQ (connection, name).
  S1  store: first mapping -> v1 + one change row (0 -> 1) + one audit event.
  S2  store: remap A->B -> v2, one more change row and audit event.
  S3  store: same artifact is a no-op (no version, change row or audit).
  S4  store: stale/early `expected_catalog_version` -> conflict, nothing written.
  S5  store: artifact not ready / foreign -> refused, nothing written.
  S6  store: `require_current_mapping` rejects a version that was superseded.
  S7  store: unmap bumps the version and clears `mapped_by`.
  R1  routes: no alias->artifact heuristic; the caller's artifact id is used.
  R2  routes: a fresh discovery runs on every POST; a non-`discovered` state is
      503 `gateway_unavailable` and an unlisted alias is 409
      `model_not_accessible`, both with no write (Law 17).
  R3  routes: 400 for any unknown body field (`source_uri`, `model_family`...),
      404 `not_found` for a missing/foreign connection or artifact, 409 codes,
      viewer 403 on POST, read open to viewer.
  C1  03-interface.yaml declares the three routes and closed request bodies.

Run directly: python3 tests/tasks/test_ftf_0308_p1.py
"""

from __future__ import annotations

import contextlib
import http.client
import json
import re
import socket
import sys
import threading
import time
import urllib.parse
import uuid
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import uvicorn
import yaml
from fastapi import FastAPI
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "apps" / "api"))

from packages.litellm_client.v1_models_discovery import (  # noqa: E402
    DiscoveredModel,
    DiscoveryState,
    ModelDiscoveryResult,
    ModelMetadataStatus,
)

from app.artifacts.artifact_crud import ArtifactNotFoundError  # noqa: E402
from app.auth.rbac_middleware import AuthenticatedPrincipal, Role  # noqa: E402
from app.litellm import model_catalog_mapping_routes as mcmr  # noqa: E402
from app.litellm import model_catalog_mapping_store as store  # noqa: E402
from app.litellm.connection_crud import ConnectionNotFoundError  # noqa: E402

FAILURES: list[str] = []
CID = "FTF-0308-P1"


def check(criterion_id: str, description: str, condition: bool) -> None:
    if not condition:
        FAILURES.append(f"FAIL [{criterion_id}] {description}")


MIGRATION_PATH = ROOT / "apps" / "api" / "alembic" / "versions" / "0022_model_catalog_entries.py"
INTERFACE_PATH = ROOT / "packages" / "contracts" / "openapi" / "03-interface.yaml"
MAIN_PATH = ROOT / "apps" / "api" / "app" / "main.py"
_NOW = datetime(2026, 10, 7, 12, 0, 0, tzinfo=UTC)


# -- M1: migration shape -----------------------------------------------------


def _migration() -> str:
    return MIGRATION_PATH.read_text()


def test_m1_migration_rls_grants_and_policy() -> None:
    src = _migration()
    check(CID, "revision chains onto 0021_memberships", 'down_revision = "0021_memberships"' in src)
    check(CID, "revision id fits alembic_version (<= 32)", len("0022_model_catalog_entries") <= 32)
    check(
        CID,
        "both tables are looped for ENABLE and FORCE row level security",
        'for table in ("model_catalog_entries", "model_catalog_mapping_changes"):' in src
        and "ENABLE ROW LEVEL SECURITY" in src
        and "FORCE ROW LEVEL SECURITY" in src,
    )
    policy = re.search(
        r"CREATE POLICY tenant_isolation ON \{table\} FOR ALL[^\n]*\n[^\n]*\n[^\n]*", src
    )
    check(
        CID,
        "tenant policy is scoped to app.current_organization_id on USING and WITH CHECK",
        policy is not None
        and "USING (organization_id = {tenant_setting})" in policy.group(0)
        and "WITH CHECK (organization_id = {tenant_setting})" in policy.group(0)
        and "current_setting('app.current_organization_id', true)" in src
        and "USING (true)" not in src
        and "WITH CHECK (true)" not in src,
    )
    check(
        CID,
        "entries get SELECT, INSERT, UPDATE and never DELETE",
        "GRANT SELECT, INSERT, UPDATE ON model_catalog_entries" in src,
    )
    check(
        CID,
        "the change log is append-only: SELECT, INSERT only",
        "GRANT SELECT, INSERT ON model_catalog_mapping_changes" in src
        and "UPDATE ON model_catalog_mapping_changes" not in src
        and "DELETE ON model_catalog_mapping_changes" not in src,
    )
    check(
        CID,
        "no DELETE grant anywhere",
        re.search(r"GRANT[^\n]*DELETE", src) is None and "GRANT ALL" not in src,
    )


def test_m1_migration_constraints() -> None:
    src = _migration()
    check(
        CID,
        "unique (connection, public_model_name)",
        'name="uq_model_catalog_entries_connection_id_public_model_name"' in src,
    )
    check(
        CID,
        "composite FK to litellm_connections inside the organization",
        '["organization_id", "litellm_connection_id"]' in src
        and '"litellm_connections.organization_id", "litellm_connections.id"' in src,
    )
    check(
        CID,
        "composite FK to trainable_artifacts inside the organization",
        '["organization_id", "trainable_artifact_id"]' in src
        and '"trainable_artifacts.organization_id", "trainable_artifacts.id"' in src,
    )
    check(
        CID,
        "composite FK from a change to its entry inside the organization",
        '"model_catalog_entries.organization_id", "model_catalog_entries.id"' in src,
    )
    check(
        CID,
        "new version must equal previous + 1",
        '"new_catalog_version = previous_catalog_version + 1"' in src,
    )
    check(CID, "catalog_version >= 1", '"catalog_version >= 1"' in src)
    check(CID, "blank model name refused", "\"public_model_name <> ''\"" in src)
    columns = set(re.findall(r'sa\.Column\(\s*"(\w+)"', src))
    check(
        CID,
        "no metadata, cost or supports_* column is stored (Law 11)",
        bool(columns)
        and not any(
            c.startswith("supports_") or "cost" in c or "metadata" in c or c.endswith("_json")
            for c in columns
        ),
    )
    check(
        CID,
        "the connection UQ the composite FK needs is created",
        '"uq_litellm_connections_organization_id_id"' in src,
    )


def _run_migration_with(mutated: str) -> bool:
    """True when the shape tests (M1) would catch `mutated`."""
    original = MIGRATION_PATH.read_text()
    MIGRATION_PATH.write_text(mutated)
    try:
        before = len(FAILURES)
        test_m1_migration_rls_grants_and_policy()
        test_m1_migration_constraints()
        caught = len(FAILURES) > before
        del FAILURES[before:]
        return caught
    finally:
        MIGRATION_PATH.write_text(original)


def test_m1_fault_injection_against_the_file_on_disk() -> None:
    src = MIGRATION_PATH.read_text()
    check(
        CID,
        "dropping FORCE row level security is caught",
        _run_migration_with(
            src.replace('f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY"', '"SELECT 1"')
        ),
    )
    check(
        CID,
        "widening the change log grant to UPDATE is caught",
        _run_migration_with(
            src.replace(
                "GRANT SELECT, INSERT ON model_catalog_mapping_changes",
                "GRANT SELECT, INSERT, UPDATE ON model_catalog_mapping_changes",
            )
        ),
    )
    check(
        CID,
        "an allow-all tenant policy is caught",
        _run_migration_with(
            src.replace("USING (organization_id = {tenant_setting})", "USING (true)")
        ),
    )
    check(
        CID,
        "dropping the +1 version check is caught",
        _run_migration_with(
            src.replace("new_catalog_version = previous_catalog_version + 1", "true")
        ),
    )


# -- S*: store against SQLite, with the Postgres-only seams replaced ---------


class _Audit:
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    def __call__(self, session: Session, **kwargs: Any) -> None:
        self.events.append(kwargs)


def _passthrough_execute(session: Session, organization_id: uuid.UUID, statement: Any) -> Any:
    return session.execute(statement)


@contextlib.contextmanager
def _store_env(artifacts: dict[uuid.UUID, str]):
    """A real SQLite session over the real ORM tables; only the Postgres
    tenant guard, the audit writer and the artifact lookup are replaced."""
    engine = create_engine("sqlite://")
    store.ModelCatalogEntryRecord.__table__.create(engine)
    store.ModelCatalogMappingChangeRecord.__table__.create(engine)
    with engine.begin() as conn:
        conn.execute(
            text(
                "CREATE UNIQUE INDEX uq_entry ON model_catalog_entries "
                "(litellm_connection_id, public_model_name)"
            )
        )
    audit = _Audit()

    def fake_get_artifact(session: Any, context: Any, artifact_id: uuid.UUID) -> Any:
        if artifact_id not in artifacts:
            raise ArtifactNotFoundError("not found")
        return SimpleNamespace(id=artifact_id, status=artifacts[artifact_id])

    saved = (store.execute_tenant_scoped, store.write_audit_event, store.get_artifact)
    store.execute_tenant_scoped = _passthrough_execute
    store.write_audit_event = audit
    store.get_artifact = fake_get_artifact
    try:
        with Session(engine) as session:
            yield session, audit
    finally:
        store.execute_tenant_scoped, store.write_audit_event, store.get_artifact = saved
        engine.dispose()


_ORG = uuid.uuid4()
_CONN = uuid.uuid4()
_CTX: Any = SimpleNamespace(organization_id=_ORG)
_ACTOR = store.MappingActor(actor_type="user", actor_id="user-1", request_id="req-1")


def _map(
    session: Session,
    artifact: uuid.UUID | None,
    *,
    name: str = "gpt-x",
    expected: int | None = None,
    actor: store.MappingActor = _ACTOR,
) -> store.MappingOutcome:
    return store.set_training_artifact_mapping(
        session,
        _CTX,
        connection_id=_CONN,
        public_model_name=name,
        trainable_artifact_id=artifact,
        expected_catalog_version=expected,
        actor=actor,
    )


def _changes(session: Session) -> list[store.ModelCatalogMappingChangeRecord]:
    return list(session.query(store.ModelCatalogMappingChangeRecord).all())


def test_s1_first_mapping_creates_v1_change_row_and_audit() -> None:
    a = uuid.uuid4()
    with _store_env({a: "ready"}) as (session, audit):
        out = _map(session, a, expected=0)
        check(CID, "first mapping reports changed", out.changed and out.entry is not None)
        entry = out.entry
        assert entry is not None
        check(
            CID,
            "entry starts at version 1 mapped to the artifact by the actor",
            entry.catalog_version == 1
            and entry.trainable_artifact_id == a
            and entry.mapped_by == "user-1"
            and entry.public_model_name == "gpt-x"
            and entry.litellm_connection_id == _CONN
            and entry.organization_id == _ORG,
        )
        rows = _changes(session)
        check(
            CID,
            "exactly one change row 0 -> 1 with no previous artifact",
            len(rows) == 1
            and rows[0].previous_catalog_version == 0
            and rows[0].new_catalog_version == 1
            and rows[0].previous_trainable_artifact_id is None
            and rows[0].new_trainable_artifact_id == a
            and rows[0].actor == "user-1"
            and rows[0].request_id == "req-1"
            and rows[0].model_catalog_entry_id == entry.id,
        )
        check(CID, "exactly one audit event", len(audit.events) == 1)
        event = audit.events[0]
        check(
            CID,
            "audit event names the mapping change action and resource",
            event["action"] == "model_catalog_entry.training_artifact_mapping_change"
            and event["resource_type"] == "model_catalog_entry"
            and event["resource_id"] == str(entry.id)
            and event["organization_id"] == _ORG
            and event["actor_id"] == "user-1"
            and event["request_id"] == "req-1",
        )
        details = event["details"]
        check(
            CID,
            "audit details carry the previous/new artifact and version",
            details["previous_trainable_artifact_id"] is None
            and details["new_trainable_artifact_id"] == str(a)
            and details["previous_catalog_version"] == 0
            and details["new_catalog_version"] == 1
            and details["litellm_connection_id"] == str(_CONN),
        )


def test_s2_remap_bumps_version_by_one_with_history() -> None:
    a, b = uuid.uuid4(), uuid.uuid4()
    with _store_env({a: "ready", b: "ready"}) as (session, audit):
        first = _map(session, a)
        second = _map(session, b, expected=1)
        entry = second.entry
        assert entry is not None and first.entry is not None
        check(
            CID,
            "remap keeps one entry and moves it to version 2 on artifact B",
            second.changed
            and entry.id == first.entry.id
            and entry.catalog_version == 2
            and entry.trainable_artifact_id == b,
        )
        rows = sorted(_changes(session), key=lambda r: r.new_catalog_version)
        check(
            CID,
            "two change rows chain 0->1 then 1->2 A->B",
            [(r.previous_catalog_version, r.new_catalog_version) for r in rows] == [(0, 1), (1, 2)]
            and rows[1].previous_trainable_artifact_id == a
            and rows[1].new_trainable_artifact_id == b,
        )
        check(
            CID,
            "two audit events, the second 1 -> 2",
            len(audit.events) == 2
            and audit.events[1]["details"]["previous_catalog_version"] == 1
            and audit.events[1]["details"]["new_catalog_version"] == 2
            and audit.events[1]["details"]["previous_trainable_artifact_id"] == str(a)
            and audit.events[1]["details"]["new_trainable_artifact_id"] == str(b),
        )
        listed = store.list_mapping_changes(session, _CTX, entry.id, limit=1)
        check(
            CID,
            "history lists newest first and honours the limit",
            len(listed) == 1 and listed[0].new_catalog_version == 2,
        )
        both = store.list_mapping_changes(session, _CTX, entry.id, limit=100)
        check(
            CID,
            "history lists both rows newest first",
            [r.new_catalog_version for r in both] == [2, 1],
        )


def test_s3_same_artifact_is_a_noop() -> None:
    a = uuid.uuid4()
    with _store_env({a: "ready"}) as (session, audit):
        _map(session, a)
        before = (len(_changes(session)), len(audit.events))
        again = _map(session, a, expected=1)
        entry = again.entry
        assert entry is not None
        check(
            CID,
            "same artifact: unchanged, version still 1, no new change row or audit event",
            not again.changed
            and entry.catalog_version == 1
            and (len(_changes(session)), len(audit.events)) == before,
        )
        check(
            CID,
            "unmapping an alias that has no entry creates nothing",
            not _map(session, None, name="never-mapped").changed
            and session.query(store.ModelCatalogEntryRecord).count() == 1,
        )
        check(
            CID,
            "a no-op with a stale expected version is still a conflict",
            _raises(lambda: _map(session, a, expected=5), store.CatalogVersionConflictError),
        )


def _raises(fn: Any, error: type[BaseException]) -> bool:
    try:
        fn()
    except error:
        return True
    except Exception:  # noqa: BLE001 - the wrong exception type is a failed check
        return False
    return False


def test_s4_expected_version_must_match() -> None:
    a, b = uuid.uuid4(), uuid.uuid4()
    with _store_env({a: "ready", b: "ready"}) as (session, audit):
        check(
            CID,
            "an alias with no entry expects version 0, so 1 is a conflict and writes nothing",
            _raises(lambda: _map(session, a, expected=1), store.CatalogVersionConflictError)
            and session.query(store.ModelCatalogEntryRecord).count() == 0
            and not audit.events,
        )
        _map(session, a)
        check(
            CID,
            "stale expected version is a conflict and the mapping stays on A",
            _raises(lambda: _map(session, b, expected=0), store.CatalogVersionConflictError)
            and session.query(store.ModelCatalogEntryRecord).one().trainable_artifact_id == a
            and len(_changes(session)) == 1
            and len(audit.events) == 1,
        )
        check(
            CID,
            "a future expected version is a conflict too",
            _raises(lambda: _map(session, b, expected=2), store.CatalogVersionConflictError),
        )
        check(
            CID,
            "the matching expected version succeeds",
            _map(session, b, expected=1).changed,
        )


def test_s4b_concurrent_first_mapping_is_a_conflict_not_a_database_error() -> None:
    a = uuid.uuid4()
    with _store_env({a: "ready"}) as (session, audit):
        _map(session, a)
        session.commit()
        real_get_entry = store.get_entry
        store.get_entry = lambda *args, **kwargs: None
        try:
            raced = _raises(lambda: _map(session, a), store.CatalogVersionConflictError)
        finally:
            store.get_entry = real_get_entry
        session.rollback()
        check(CID, "a lost first-mapping race is a conflict", raced)
        check(
            CID,
            "the loser wrote no second change row or audit event",
            len(_changes(session)) == 1 and len(audit.events) == 1,
        )


def test_s5_artifact_must_be_ready_and_owned() -> None:
    ready, registered, archived, foreign = uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    with _store_env({ready: "ready", registered: "registered", archived: "archived"}) as (
        session,
        audit,
    ):
        for artifact, label in ((registered, "registered"), (archived, "archived")):
            check(
                CID,
                f"a {label} artifact is refused as not ready",
                _raises(lambda a=artifact: _map(session, a), store.ArtifactNotReadyError),
            )
        check(
            CID,
            "a foreign/missing artifact is ArtifactNotFoundError",
            _raises(lambda: _map(session, foreign), ArtifactNotFoundError),
        )
        check(
            CID,
            "nothing was written by any refusal",
            session.query(store.ModelCatalogEntryRecord).count() == 0
            and not _changes(session)
            and not audit.events,
        )
        _map(session, ready)
        check(
            CID,
            "a refused remap leaves the existing mapping and version alone",
            _raises(lambda: _map(session, registered, expected=1), store.ArtifactNotReadyError)
            and session.query(store.ModelCatalogEntryRecord).one().catalog_version == 1,
        )


def test_s6_require_current_mapping_rejects_superseded_version() -> None:
    a, b = uuid.uuid4(), uuid.uuid4()
    with _store_env({a: "ready", b: "ready"}) as (session, _audit):
        entry = _map(session, a).entry
        assert entry is not None
        got = store.require_current_mapping(session, _CTX, entry.id, 1)
        check(CID, "the current version is accepted and returns the entry", got.id == entry.id)
        _map(session, b, expected=1)
        try:
            store.require_current_mapping(session, _CTX, entry.id, 1)
            raised = None
        except store.MappingChangedError as exc:
            raised = exc
        check(
            CID,
            "the superseded version raises MappingChangedError naming both versions",
            raised is not None
            and raised.entry_id == entry.id
            and raised.observed_catalog_version == 1
            and raised.current_catalog_version == 2,
        )
        check(
            CID,
            "the new version is accepted",
            store.require_current_mapping(session, _CTX, entry.id, 2).trainable_artifact_id == b,
        )
        check(
            CID,
            "an unknown or non-uuid entry id is not found",
            _raises(
                lambda: store.require_current_mapping(session, _CTX, uuid.uuid4(), 1),
                store.ModelCatalogEntryNotFoundError,
            )
            and _raises(
                lambda: store.require_current_mapping(session, _CTX, "x", 1),
                store.ModelCatalogEntryNotFoundError,
            ),
        )


def test_s7_unmap_bumps_version_and_clears_mapped_by() -> None:
    a = uuid.uuid4()
    with _store_env({a: "ready"}) as (session, audit):
        _map(session, a)
        out = _map(session, None, expected=1)
        entry = out.entry
        assert entry is not None
        check(
            CID,
            "unmap moves to version 2 with no artifact and no mapped_by",
            out.changed
            and entry.catalog_version == 2
            and entry.trainable_artifact_id is None
            and entry.mapped_by is None,
        )
        last = sorted(_changes(session), key=lambda r: r.new_catalog_version)[-1]
        check(
            CID,
            "the unmap is recorded A -> none",
            last.previous_trainable_artifact_id == a
            and last.new_trainable_artifact_id is None
            and len(audit.events) == 2
            and audit.events[1]["details"]["new_trainable_artifact_id"] is None,
        )


def test_s8_input_validation() -> None:
    a = uuid.uuid4()
    with _store_env({a: "ready"}) as (session, _audit):
        check(
            CID, "empty model name refused", _raises(lambda: _map(session, a, name=""), ValueError)
        )
        check(
            CID,
            "a 256 character name is accepted and 257 refused",
            _map(session, a, name="n" * 256).changed
            and _raises(lambda: _map(session, a, name="n" * 257), ValueError),
        )
        check(
            CID,
            "a non-uuid artifact id is refused",
            _raises(lambda: _map(session, "not-a-uuid"), ValueError),
        )
        entry = session.query(store.ModelCatalogEntryRecord).one()
        for bad in (0, 101, True, "5", -1):
            check(
                CID,
                f"limit {bad!r} is refused",
                _raises(
                    lambda b=bad: store.list_mapping_changes(session, _CTX, entry.id, limit=b),
                    ValueError,
                ),
            )
        check(
            CID,
            "limits 1 and 100 are accepted",
            len(store.list_mapping_changes(session, _CTX, entry.id, limit=1)) == 1
            and len(store.list_mapping_changes(session, _CTX, entry.id, limit=100)) == 1,
        )
        check(
            CID,
            "get_entry is keyed by connection and exact name",
            store.get_entry(session, _CTX, connection_id=_CONN, public_model_name="n" * 256)
            is not None
            and store.get_entry(session, _CTX, connection_id=_CONN, public_model_name="N" * 256)
            is None
            and store.get_entry(
                session, _CTX, connection_id=uuid.uuid4(), public_model_name="n" * 256
            )
            is None,
        )


def test_s9_entries_are_per_connection_and_per_name() -> None:
    a = uuid.uuid4()
    other = uuid.uuid4()
    with _store_env({a: "ready"}) as (session, _audit):
        _map(session, a, name="m1")
        _map(session, a, name="m2")
        store.set_training_artifact_mapping(
            session,
            _CTX,
            connection_id=other,
            public_model_name="m1",
            trainable_artifact_id=a,
            expected_catalog_version=None,
            actor=_ACTOR,
        )
        check(
            CID,
            "the same artifact can back three independent aliases, each at version 1",
            session.query(store.ModelCatalogEntryRecord).count() == 3
            and {e.catalog_version for e in session.query(store.ModelCatalogEntryRecord)} == {1},
        )
        check(
            CID,
            "alias names are not trimmed or case-folded",
            _map(session, a, name="M1").changed
            and session.query(store.ModelCatalogEntryRecord).count() == 4,
        )


# -- R*: routes, driven over real HTTP with the DB/gateway seams replaced -----

_ISSUER = "https://issuer.example"
_ORG_ID = uuid.uuid4()
_ADMIN = AuthenticatedPrincipal(issuer=_ISSUER, subject="admin-1")
_ML = AuthenticatedPrincipal(issuer=_ISSUER, subject="ml-1")
_VIEWER = AuthenticatedPrincipal(issuer=_ISSUER, subject="viewer-1")
_PRINCIPALS = {"admin": _ADMIN, "ml": _ML, "viewer": _VIEWER}
_ROLES = {"admin-1": Role.ORGANIZATION_ADMIN, "ml-1": Role.ML_ENGINEER, "viewer-1": Role.VIEWER}


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


class _Discovery:
    """Records every call; returns whatever `result` is set to."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.result = _discovered("gpt-x", "openai/gpt-4o")

    def __call__(self, **kwargs: Any) -> ModelDiscoveryResult:
        self.calls.append(kwargs)
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


_DISCOVERY = _Discovery()


@contextlib.contextmanager
def _running_app():
    app = FastAPI()
    app.include_router(
        mcmr.build_model_catalog_mapping_router(
            lambda: None,
            principal_resolver=_principal_resolver,
            role_lookup=_role_lookup,
            secret_manager_config_factory=lambda: None,
            validation_policy_factory=lambda: SimpleNamespace(),
            discovery_function=_DISCOVERY,
        )
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


def _request(
    base: str,
    method: str,
    path: str,
    *,
    principal: str | None = "ml",
    payload: Any = None,
    raw: bytes | None = None,
) -> _Http:
    headers: dict[str, str] = {}
    if principal is not None:
        headers["X-Test-Principal"] = principal
    body: bytes | None = raw
    if raw is None and payload is not None:
        body = json.dumps(payload).encode("utf-8")
    if body is not None:
        headers["Content-Type"] = "application/json"
    parsed = urllib.parse.urlparse(base)
    conn = http.client.HTTPConnection(parsed.hostname or "127.0.0.1", parsed.port or 80, timeout=10)
    try:
        conn.request(method, path, body=body, headers=headers)
        response = conn.getresponse()
        return _Http(response.status, response.read())
    finally:
        conn.close()


_CONN_ID = uuid.uuid4()
_ART_ID = uuid.uuid4()


def _path(model: str = "gpt-x", suffix: str = "", connection: Any = _CONN_ID) -> str:
    return (
        f"/api/v1/organizations/{_ORG_ID}/connections/litellm/{connection}"
        f"/models/{model}/training-artifact{suffix}"
    )


@contextlib.contextmanager
def _session_cm(session_factory: Any, tenant_context: Any):
    yield object()


def _artifact_ns(artifact_id: uuid.UUID = _ART_ID) -> SimpleNamespace:
    return SimpleNamespace(
        id=artifact_id,
        organization_id=_ORG_ID,
        display_name="Llama",
        source_type="huggingface",
        source_uri="org/repo",
        model_family="llama",
        base_model_revision="a" * 40,
        weights_digest="sha256:" + "b" * 64,
        license_id="mit",
        license_accepted_by="u",
        license_accepted_at=_NOW,
        trust_remote_code_allowed=False,
        required_python_packages_json=[],
        minimum_gpu_profile_json={},
        status="ready",
        created_at=_NOW,
        updated_at=_NOW,
        archived_at=None,
    )


def _entry_ns(*, artifact: uuid.UUID | None, version: int = 1) -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid.uuid4(),
        trainable_artifact_id=artifact,
        catalog_version=version,
        last_verified_accessible_at=_NOW,
    )


class _Env:
    """Replaces every DB-touching name the routes module imported."""

    def __init__(self) -> None:
        self.connection = SimpleNamespace(
            status="active", base_url="https://gw.example", secret_ref="openbao://secret/k"
        )
        self.connection_statuses: list[str] | None = None
        self.connection_missing = False
        self.entry: SimpleNamespace | None = None
        self.changes: list[Any] = []
        self.set_calls: list[dict[str, Any]] = []
        self.set_result: Any = None
        self.set_error: Exception | None = None
        self.changes_limit: list[int] = []

    def get_connection(self, session: Any, ctx: Any, connection_id: Any) -> Any:
        if self.connection_missing:
            raise ConnectionNotFoundError("nope")
        if self.connection_statuses:
            return SimpleNamespace(
                status=self.connection_statuses.pop(0),
                base_url=self.connection.base_url,
                secret_ref=self.connection.secret_ref,
            )
        return self.connection

    def get_entry(self, session: Any, ctx: Any, **kwargs: Any) -> Any:
        return self.entry

    def list_changes(self, session: Any, ctx: Any, entry_id: Any, *, limit: int) -> Any:
        self.changes_limit.append(limit)
        return self.changes[:limit]

    def set_mapping(self, session: Any, ctx: Any, **kwargs: Any) -> Any:
        self.set_calls.append(kwargs)
        if self.set_error is not None:
            raise self.set_error
        return self.set_result

    def get_artifact(self, session: Any, ctx: Any, artifact_id: Any) -> Any:
        return _artifact_ns(artifact_id)


@contextlib.contextmanager
def _patched(env: _Env):
    names = {
        "tenant_scoped_session_for": _session_cm,
        "get_connection": env.get_connection,
        "get_entry": env.get_entry,
        "list_mapping_changes": env.list_changes,
        "set_training_artifact_mapping": env.set_mapping,
        "get_artifact": env.get_artifact,
    }
    originals = {name: getattr(mcmr, name) for name in names}
    for name, value in names.items():
        setattr(mcmr, name, value)
    _DISCOVERY.calls.clear()
    _DISCOVERY.result = _discovered("gpt-x", "openai/gpt-4o")
    try:
        yield
    finally:
        for name, value in originals.items():
            setattr(mcmr, name, value)


def _change_ns(version: int) -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid.uuid4(),
        model_catalog_entry_id=uuid.uuid4(),
        previous_trainable_artifact_id=None,
        new_trainable_artifact_id=_ART_ID,
        previous_catalog_version=version - 1,
        new_catalog_version=version,
        actor="ml-1",
        request_id="r",
        created_at=_NOW,
    )


def test_r_get_reads_the_explicit_mapping() -> None:
    with _running_app() as base:
        env = _Env()
        with _patched(env):
            unmapped = _request(base, "GET", _path())
            body = unmapped.json()
            check(
                CID,
                "GET with no entry is 200 unmapped with version 0 and no ids",
                unmapped.status == 200
                and body["mapping_state"] == "unmapped"
                and body["model_catalog_entry_id"] is None
                and body["trainable_artifact_id"] is None
                and body["catalog_version"] == 0
                and body["artifact"] is None
                and body["last_changed_at"] is None
                and body["last_verified_accessible_at"] is None
                and body["public_model_name"] == "gpt-x"
                and body["litellm_connection_id"] == str(_CONN_ID),
            )
            check(CID, "GET never runs a discovery", not _DISCOVERY.calls)
            env.entry = _entry_ns(artifact=_ART_ID, version=3)
            env.changes = [_change_ns(3)]
            mapped = _request(base, "GET", _path("openai/gpt-4o"), principal="viewer")
            body = mapped.json()
            check(
                CID,
                "GET of a mapped alias returns the artifact, version and timestamps (viewer may read; '/' names route)",
                mapped.status == 200
                and body["mapping_state"] == "mapped"
                and body["model_catalog_entry_id"] == str(env.entry.id)
                and body["trainable_artifact_id"] == str(_ART_ID)
                and body["catalog_version"] == 3
                and body["artifact"]["id"] == str(_ART_ID)
                and body["artifact"]["status"] == "ready"
                and body["last_changed_at"] == _NOW.isoformat()
                and body["last_verified_accessible_at"] == _NOW.isoformat()
                and body["public_model_name"] == "openai/gpt-4o",
            )
            env.entry = _entry_ns(artifact=None, version=2)
            env.changes = []
            cleared = _request(base, "GET", _path()).json()
            check(
                CID,
                "an unmapped entry reads as unmapped at its stored version",
                cleared["mapping_state"] == "unmapped"
                and cleared["trainable_artifact_id"] is None
                and cleared["catalog_version"] == 2
                and cleared["artifact"] is None,
            )
            env.connection_missing = True
            missing = _request(base, "GET", _path())
            check(
                CID,
                "a missing/foreign connection is 404 not_found",
                missing.status == 404 and missing.json() == {"detail": "not_found"},
            )
            check(
                CID,
                "no principal is 401",
                _request(base, "GET", _path(), principal=None).status == 401,
            )
            check(
                CID,
                "an over-long model name is 400",
                _request(base, "GET", _path("n" * 257)).status == 400,
            )


def test_r_post_validates_the_body_before_any_discovery() -> None:
    with _running_app() as base:
        env = _Env()
        with _patched(env):
            cases: list[tuple[str, dict[str, Any]]] = [
                (
                    "source_uri is refused",
                    {"trainable_artifact_id": str(_ART_ID), "source_uri": "x"},
                ),
                ("model_family is refused", {"trainable_artifact_id": None, "model_family": "x"}),
                ("trainable_artifact_id is required", {"expected_catalog_version": 1}),
                ("a non-uuid artifact id", {"trainable_artifact_id": "nope"}),
                ("a numeric artifact id", {"trainable_artifact_id": 7}),
                (
                    "a negative expected version",
                    {"trainable_artifact_id": None, "expected_catalog_version": -1},
                ),
                (
                    "a boolean expected version",
                    {"trainable_artifact_id": None, "expected_catalog_version": True},
                ),
                (
                    "a string expected version",
                    {"trainable_artifact_id": None, "expected_catalog_version": "1"},
                ),
                (
                    "a float expected version",
                    {"trainable_artifact_id": None, "expected_catalog_version": 1.5},
                ),
            ]
            for label, payload in cases:
                result = _request(base, "POST", _path(), payload=payload)
                check(CID, f"{label} -> 400", result.status == 400)
            check(
                CID,
                "a list body -> 400",
                _request(base, "POST", _path(), payload=[1]).status == 400,
            )
            check(
                CID, "broken JSON -> 400", _request(base, "POST", _path(), raw=b"{").status == 400
            )
            check(CID, "no body -> 400", _request(base, "POST", _path()).status == 400)
            check(
                CID,
                "an over-long model name -> 400",
                _request(
                    base, "POST", _path("n" * 257), payload={"trainable_artifact_id": None}
                ).status
                == 400,
            )
            check(
                CID,
                "no refused request ran a discovery or touched the store",
                not _DISCOVERY.calls and not env.set_calls,
            )
            unknown = _request(
                base, "POST", _path(), payload={"trainable_artifact_id": None, "x": 1}
            )
            check(
                CID, "the unknown field is named in the 400 detail", "x" in unknown.json()["detail"]
            )


def test_r_post_roles() -> None:
    with _running_app() as base:
        env = _Env()
        env.set_result = store.MappingOutcome(entry=_entry_ns(artifact=_ART_ID), changed=True)
        with _patched(env):
            payload = {"trainable_artifact_id": str(_ART_ID)}
            viewer = _request(base, "POST", _path(), principal="viewer", payload=payload)
            check(CID, "a viewer cannot POST (403)", viewer.status == 403)
            check(
                CID,
                "no principal is 401",
                _request(base, "POST", _path(), principal=None, payload=payload).status == 401,
            )
            check(
                CID,
                "the viewer's refusal ran no discovery and no write",
                not _DISCOVERY.calls and not env.set_calls,
            )
            for principal in ("ml", "admin"):
                check(
                    CID,
                    f"{principal} may POST",
                    _request(base, "POST", _path(), principal=principal, payload=payload).status
                    == 200,
                )


def test_r_post_runs_a_fresh_discovery_every_call_before_writing() -> None:
    with _running_app() as base:
        env = _Env()
        env.set_result = store.MappingOutcome(entry=_entry_ns(artifact=_ART_ID), changed=True)
        with _patched(env):
            payload = {"trainable_artifact_id": str(_ART_ID), "expected_catalog_version": 0}
            first = _request(base, "POST", _path("openai/gpt-4o"), payload=payload)
            second = _request(base, "POST", _path("openai/gpt-4o"), payload=payload)
            check(CID, "both calls succeed", first.status == 200 and second.status == 200)
            check(
                CID,
                "a discovery ran on every call with the connection's own URL and secret ref",
                len(_DISCOVERY.calls) == 2
                and all(
                    c["base_url"] == "https://gw.example"
                    and c["secret_ref"] == "openbao://secret/k"
                    for c in _DISCOVERY.calls
                ),
            )
            call = env.set_calls[0]
            check(
                CID,
                "the store got the caller's artifact id, the exact alias, the expected version and the actor",
                call["trainable_artifact_id"] == _ART_ID
                and call["public_model_name"] == "openai/gpt-4o"
                and call["connection_id"] == _CONN_ID
                and call["expected_catalog_version"] == 0
                and call["actor"].actor_id == "ml-1"
                and call["actor"].actor_type == "user"
                and len(call["actor"].request_id) == 32,
            )
            body = first.json()
            check(
                CID,
                "the response reports changed and the mapped artifact",
                body["changed"] is True
                and body["mapping_state"] == "mapped"
                and body["trainable_artifact_id"] == str(_ART_ID)
                and body["artifact"]["id"] == str(_ART_ID),
            )
            check(
                CID,
                "each call gets its own request id",
                env.set_calls[0]["actor"].request_id != env.set_calls[1]["actor"].request_id,
            )


def test_r_post_never_infers_an_artifact_from_the_alias() -> None:
    with _running_app() as base:
        env = _Env()
        env.set_result = store.MappingOutcome(
            entry=_entry_ns(artifact=None, version=2), changed=True
        )
        with _patched(env):
            result = _request(base, "POST", _path(), payload={"trainable_artifact_id": None})
            check(CID, "an explicit null unmaps", result.status == 200)
            check(
                CID,
                "the store received no artifact for an alias named like a model family",
                env.set_calls[0]["trainable_artifact_id"] is None
                and env.set_calls[0]["expected_catalog_version"] is None,
            )
            body = result.json()
            check(
                CID,
                "the unmap response is unmapped with no artifact",
                body["mapping_state"] == "unmapped"
                and body["artifact"] is None
                and body["changed"],
            )


def test_r_post_gateway_failures_write_nothing() -> None:
    with _running_app() as base:
        env = _Env()
        env.set_result = store.MappingOutcome(entry=_entry_ns(artifact=_ART_ID), changed=True)
        payload = {"trainable_artifact_id": str(_ART_ID)}
        with _patched(env):
            for state in DiscoveryState:
                if state is DiscoveryState.DISCOVERED:
                    continue
                _DISCOVERY.result = _failed(state)
                result = _request(base, "POST", _path(), payload=payload)
                check(
                    CID,
                    f"discovery state {state.value} is 503 gateway_unavailable",
                    result.status == 503 and result.json() == {"detail": "gateway_unavailable"},
                )
            check(CID, "no failed discovery wrote anything", not env.set_calls)
            _DISCOVERY.result = _discovered("other-model")
            absent = _request(base, "POST", _path(), payload=payload)
            check(
                CID,
                "an alias the gateway does not list is 409 model_not_accessible",
                absent.status == 409 and absent.json() == {"detail": "model_not_accessible"},
            )
            _DISCOVERY.result = _discovered("GPT-X", "gpt-x ", "gpt-xx")
            near = _request(base, "POST", _path(), payload=payload)
            check(
                CID,
                "near-miss spellings (case, whitespace, prefix) do not count as accessible",
                near.status == 409,
            )
            _DISCOVERY.result = _discovered()
            check(
                CID,
                "an empty model list is not accessible",
                _request(base, "POST", _path(), payload=payload).status == 409,
            )
            check(CID, "no refusal wrote anything", not env.set_calls)


def test_r_post_revoked_connection_is_refused() -> None:
    with _running_app() as base:
        env = _Env()
        env.set_result = store.MappingOutcome(entry=_entry_ns(artifact=_ART_ID), changed=True)
        payload = {"trainable_artifact_id": str(_ART_ID)}
        with _patched(env):
            env.connection = SimpleNamespace(
                status="revoked", base_url="https://gw.example", secret_ref="s"
            )
            result = _request(base, "POST", _path(), payload=payload)
            check(
                CID,
                "a revoked connection is 409 connection_revoked before any network call",
                result.status == 409
                and result.json() == {"detail": "connection_revoked"}
                and not _DISCOVERY.calls
                and not env.set_calls,
            )
            env.connection = SimpleNamespace(
                status="active", base_url="https://gw.example", secret_ref="s"
            )
            env.connection_statuses = ["active", "revoked"]
            raced = _request(base, "POST", _path(), payload=payload)
            check(
                CID,
                "a revoke that lands during discovery is caught inside the write transaction",
                raced.status == 409
                and raced.json() == {"detail": "connection_revoked"}
                and len(_DISCOVERY.calls) == 1
                and not env.set_calls,
            )
            env.connection_statuses = None
            env.connection_missing = True
            check(
                CID,
                "a missing/foreign connection is 404 not_found with no discovery",
                _request(base, "POST", _path(), payload=payload).status == 404
                and len(_DISCOVERY.calls) == 1,
            )


def test_r_post_maps_store_errors_to_stable_codes() -> None:
    with _running_app() as base:
        env = _Env()
        payload = {"trainable_artifact_id": str(_ART_ID)}
        with _patched(env):
            table: list[tuple[Exception, int, str]] = [
                (ArtifactNotFoundError("x"), 404, "not_found"),
                (store.ArtifactNotReadyError("x"), 409, "artifact_not_ready"),
                (store.CatalogVersionConflictError("x"), 409, "catalog_version_conflict"),
            ]
            for error, status, code in table:
                env.set_error = error
                result = _request(base, "POST", _path(), payload=payload)
                check(
                    CID,
                    f"{type(error).__name__} -> {status} {code}",
                    result.status == status and result.json() == {"detail": code},
                )


def test_r_changes_route() -> None:
    with _running_app() as base:
        env = _Env()
        with _patched(env):
            empty = _request(base, "GET", _path(suffix="/changes"), principal="viewer")
            check(
                CID,
                "no entry -> 200 with an empty list",
                empty.status == 200 and empty.json() == {"changes": []},
            )
            env.entry = _entry_ns(artifact=_ART_ID, version=3)
            env.changes = [_change_ns(3), _change_ns(2), _change_ns(1)]
            full = _request(base, "GET", _path(suffix="/changes")).json()["changes"]
            check(
                CID,
                "changes are serialized in the store's order with versions and actor",
                [c["new_catalog_version"] for c in full] == [3, 2, 1]
                and full[0]["previous_catalog_version"] == 2
                and full[0]["actor"] == "ml-1"
                and full[0]["new_trainable_artifact_id"] == str(_ART_ID)
                and full[0]["previous_trainable_artifact_id"] is None
                and full[0]["created_at"] == _NOW.isoformat(),
            )
            check(CID, "the default limit is 50", env.changes_limit[-1] == 50)
            two = _request(base, "GET", _path(suffix="/changes?limit=2")).json()["changes"]
            check(CID, "limit=2 is honoured", len(two) == 2 and env.changes_limit[-1] == 2)
            check(
                CID,
                "limit=100 is accepted",
                _request(base, "GET", _path(suffix="/changes?limit=100")).status == 200,
            )
            for bad in ("0", "101", "abc", "-1", "1.5"):
                check(
                    CID,
                    f"limit={bad} is 400",
                    _request(base, "GET", _path(suffix=f"/changes?limit={bad}")).status == 400,
                )
            env.connection_missing = True
            check(
                CID,
                "a missing/foreign connection is 404",
                _request(base, "GET", _path(suffix="/changes")).status == 404,
            )
            check(
                CID,
                "no principal is 401",
                _request(base, "GET", _path(suffix="/changes"), principal=None).status == 401,
            )


# -- C1 / wiring ---------------------------------------------------------------


def test_c1_interface_contract_declares_the_routes() -> None:
    doc = yaml.safe_load(INTERFACE_PATH.read_text())
    base = "/api/v1/organizations/{organization_id}/connections/litellm/{connection_id}/models/{model_name}/training-artifact"
    paths = doc["paths"]
    check(CID, "the mapping path declares GET and POST", set(paths[base]) >= {"get", "post"})
    check(CID, "the changes path declares GET", "get" in paths[base + "/changes"])
    post = paths[base]["post"]
    codes = set(post["responses"])
    check(
        CID,
        "POST documents 200/400/401/403/404/409/503",
        codes >= {"200", "400", "401", "403", "404", "409", "503"},
    )
    schemas = doc["components"]["schemas"]
    request = schemas["ModelTrainingArtifactMappingRequest"]
    check(
        CID,
        "the request body is closed and requires trainable_artifact_id",
        request["additionalProperties"] is False
        and request["required"] == ["trainable_artifact_id"]
        and set(request["properties"]) == {"trainable_artifact_id", "expected_catalog_version"},
    )
    mapping = schemas["ModelTrainingArtifactMapping"]
    check(
        CID,
        "the mapping response carries exactly the contract fields",
        set(mapping["properties"])
        == {
            "mapping_state",
            "model_catalog_entry_id",
            "litellm_connection_id",
            "public_model_name",
            "trainable_artifact_id",
            "catalog_version",
            "last_verified_accessible_at",
            "last_changed_at",
            "artifact",
        }
        and mapping["properties"]["mapping_state"]["enum"] == ["unmapped", "mapped"]
        and mapping["additionalProperties"] is False,
    )
    limit = next(p for p in paths[base + "/changes"]["parameters"] if p["name"] == "limit")
    check(
        CID,
        "changes limit is 1..100",
        limit["schema"]["minimum"] == 1 and limit["schema"]["maximum"] == 100,
    )
    forbidden = {"cost", "supports_tools", "metadata", "max_tokens", "source_uri", "model_family"}
    check(
        CID,
        "no metadata/cost/capability field is promised by the mapping contract",
        not (set(mapping["properties"]) & forbidden),
    )


def test_c1_router_is_wired_into_the_app_and_uses_a_closed_path() -> None:
    main_src = MAIN_PATH.read_text()
    check(
        CID,
        "main.py imports and includes the mapping router with the shared resolver and lookup",
        "from app.litellm.model_catalog_mapping_routes import build_model_catalog_mapping_router"
        in main_src
        and re.search(
            r"build_model_catalog_mapping_router\(\s*session_factory, principal_resolver=principal_resolver, role_lookup=role_lookup\s*\)",
            main_src,
        )
        is not None,
    )
    paths = {getattr(r, "path", "") for r in _router().routes}
    base = (
        "/api/v1/organizations/{organization_id}/connections/litellm/{connection_id}"
        "/models/{model_name:path}/training-artifact"
    )
    check(
        CID,
        "exactly the mapping path and its changes path are mounted",
        paths == {base, base + "/changes"},
    )
    methods = {
        (getattr(r, "path", ""), m) for r in _router().routes for m in getattr(r, "methods", set())
    }
    check(
        CID,
        "exactly GET+POST on the mapping path and GET on changes (no DELETE/PUT/PATCH)",
        methods == {(base, "GET"), (base, "POST"), (base + "/changes", "GET")},
    )


def _router() -> Any:
    return mcmr.build_model_catalog_mapping_router(
        lambda: None, principal_resolver=_principal_resolver, role_lookup=_role_lookup
    )


def test_c1_the_module_has_no_alias_heuristic_or_management_api_call() -> None:
    routes_src = (ROOT / "apps/api/app/litellm/model_catalog_mapping_routes.py").read_text()
    store_src = (ROOT / "apps/api/app/litellm/model_catalog_mapping_store.py").read_text()
    for needle in (
        "model/info",
        "model_group",
        "/model/new",
        "/model/update",
        "startswith(",
        "split(",
    ):
        check(
            CID,
            f"routes never use {needle!r} to derive anything from the alias",
            needle not in routes_src.split('"""', 2)[2],
        )
    check(
        CID,
        "the store never calls the network",
        "requests" not in store_src and "httpx" not in store_src and "urllib" not in store_src,
    )
    check(
        CID,
        "the store and routes do not edit model_catalog_routes or the classifier",
        "model_catalog_routes import" not in routes_src
        and "training_capability_classifier" not in routes_src + store_src,
    )


def main() -> None:
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
            except Exception as exc:  # noqa: BLE001 - a crash is a failed test, reported by name
                FAILURES.append(f"FAIL [{CID}] {name} raised {type(exc).__name__}: {exc}")

    if FAILURES:
        print(f"FTF-0308-P1 model catalog mapping tests: {len(FAILURES)} failed")
        for failure in FAILURES:
            print(f"  {failure}")
        sys.exit(1)

    print("FTF-0308-P1 model catalog mapping tests: all checks passed")
    sys.exit(0)


if __name__ == "__main__":
    main()
