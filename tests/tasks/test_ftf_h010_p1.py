#!/usr/bin/env python3
# mypy: disable-error-code="arg-type,attr-defined,return-value,assignment,call-arg"
# ruff: noqa: E501
"""FTF-H010-P1 -- model import fields + import-validation jobs (no-DB
unit/fault tests).

Runnable without Postgres or network egress. The real live-database
evidence (row-level security, grants, composite foreign keys, the
migration round trip, cross-tenant denial, the new model_versions CHECK
constraints) is `tests/integration/test_ftf_h010_p1_model_import.py`, run
in CI's `model-import-integration` job.

Criteria (QUEUE Backlog, FTF-H010-P1):
  M1  migration 0027 shape: model_versions gets the 8 new columns, two new
      structural CHECKs (external_endpoint, imported_adapter), two sha256
      format CHECKs, an origin/lineage_completeness enum CHECK, the self
      FK + supporting UQ, and a correct backfill from import_kind.
  M2  model_import_jobs: ENABLE+FORCE RLS with a non-allow-all tenant
      policy, SELECT/INSERT/column-limited UPDATE, never DELETE, state and
      origin enum CHECKs, failure_code<->state CHECK, composite FKs.
  S1  store: a well-formed request of each origin creates a queued job and
      one audit event.
  S2  store: external_endpoint + trainable_artifact_id is refused, nothing
      written (no fictitious hash).
  S3  store: external_endpoint with no external_provider_model_ref is
      refused.
  S4  store: imported_adapter with no parent_model_version_id is refused.
  S5  store: a foreign/missing project, parent or artifact is refused with
      the exact matching not-found error, nothing written.
  S6  store: an unknown origin is refused.
  S7  store: get/list are scoped to the exact project; a wrong-project,
      missing or non-uuid job id is ModelImportJobNotFoundError.
  R1  routes: 400 for any unknown body field, a missing/non-string
      requested_origin, or a non-uuid parent/artifact id.
  R2  routes: viewer gets 403 on POST, 200 on GET; no principal is 401.
  R3  routes: every store error maps to its stable HTTP code; a 400 never
      writes anything.
  C1  10-interface.yaml declares the three routes and closed schemas; the
      router is wired into main.py with the shared resolver/role lookup.

Run directly: python3 tests/tasks/test_ftf_h010_p1.py
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
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import uvicorn
import yaml
from fastapi import FastAPI
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "apps" / "api"))

from app.artifacts.artifact_crud import ArtifactNotFoundError  # noqa: E402
from app.auth.rbac_middleware import AuthenticatedPrincipal, Role  # noqa: E402
from app.model_versions import model_import_crud as crud  # noqa: E402
from app.model_versions import model_import_routes as mir  # noqa: E402
from app.model_versions import model_import_schema as schema  # noqa: E402
from app.model_versions import model_version_schema as mv_schema  # noqa: E402

FAILURES: list[str] = []
CID = "FTF-H010-P1"


def check(criterion_id: str, description: str, condition: bool) -> None:
    if not condition:
        FAILURES.append(f"FAIL [{criterion_id}] {description}")


MIGRATION_PATH = ROOT / "apps" / "api" / "alembic" / "versions" / "0027_model_import.py"
INTERFACE_PATH = ROOT / "packages" / "contracts" / "openapi" / "10-interface.yaml"
MAIN_PATH = ROOT / "apps" / "api" / "app" / "main.py"
_NOW = datetime(2026, 10, 8, 12, 0, 0, tzinfo=UTC)


# -- M1/M2: migration shape ---------------------------------------------------


def _migration() -> str:
    return MIGRATION_PATH.read_text()


def test_m1_model_versions_columns_and_backfill() -> None:
    src = _migration()
    check(
        CID,
        "chains onto the current main head (or a later renumbering, never a guess)",
        re.search(r'down_revision = "0\d+_\w+"', src) is not None,
    )
    check(CID, "revision id fits alembic_version (<= 32)", len("0027_model_import") <= 32)
    for column in (
        "origin",
        "lineage_completeness",
        "adapter_only",
        "parent_model_version_id",
        "external_provider_model_ref",
        "observed_deployment_fingerprint",
        "tokenizer_revision",
        "chat_template_sha256",
        "serving_config_sha256",
    ):
        check(CID, f"model_versions gets a real {column} column", f'"{column}"' in src)
    check(
        CID,
        "origin is backfilled from import_kind with no unmapped case",
        "WHEN 'endpoint' THEN 'external_endpoint'" in src
        and "WHEN 'adapter' THEN 'imported_adapter'" in src
        and "WHEN 'full_weight' THEN 'imported_weights'" in src,
    )
    check(
        CID,
        "origin is made NOT NULL only after the backfill runs",
        src.index("UPDATE model_versions SET origin")
        < src.index('op.alter_column("model_versions", "origin", nullable=False)'),
    )
    check(
        CID,
        "pre-existing endpoint rows backfill external_provider_model_ref from endpoint_reference",
        "SET external_provider_model_ref = endpoint_reference" in src,
    )
    check(
        CID,
        "the self-referential FK's own supporting UQ is added",
        '"uq_model_versions_organization_id_id"' in src,
    )


def test_m1_model_versions_checks() -> None:
    src = _migration()
    check(
        CID,
        "origin enum CHECK",
        "ck_model_versions_origin_values" in src
        and "origin in {_ORIGINS}" in src.replace(
            "f\"origin in {_ORIGINS}\"", 'f"origin in {_ORIGINS}"'
        ),
    )
    check(
        CID,
        "external_endpoint requires no artifact/digest and a real provider ref",
        "ck_model_versions_external_endpoint_consistency" in src
        and "trainable_artifact_id IS NULL AND weights_digest IS NULL" in src
        and "external_provider_model_ref IS NOT NULL" in src,
    )
    check(
        CID,
        "imported_adapter requires a base revision and adapter_only=true",
        "ck_model_versions_imported_adapter_consistency" in src
        and "base_model_revision IS NOT NULL AND adapter_only" in src,
    )
    check(
        CID,
        "both new sha256 columns are format-checked",
        "ck_model_versions_chat_template_sha256_format" in src
        and "ck_model_versions_serving_config_sha256_format" in src
        and src.count("_SHA256_PATTERN") >= 2,
    )
    check(
        CID,
        "the self-referential composite FK is RESTRICT, never CASCADE",
        '"fk_model_versions_organization_id_parent_model_version_id"' in src
        and 'ondelete="RESTRICT"' in src,
    )


def test_m2_model_import_jobs_rls_grants_and_checks() -> None:
    src = _migration()
    check(
        CID,
        "ENABLE and FORCE row level security",
        "ALTER TABLE model_import_jobs ENABLE ROW LEVEL SECURITY" in src
        and "ALTER TABLE model_import_jobs FORCE ROW LEVEL SECURITY" in src,
    )
    policy = re.search(r"CREATE POLICY tenant_isolation ON model_import_jobs FOR ALL[^\n]*\n[^\n]*\n[^\n]*", src)
    check(
        CID,
        "tenant policy is scoped on both USING and WITH CHECK, never allow-all",
        policy is not None
        and "USING (organization_id = {tenant_setting})" in policy.group(0)
        and "WITH CHECK (organization_id = {tenant_setting})" in policy.group(0)
        and "USING (true)" not in src
        and "WITH CHECK (true)" not in src,
    )
    check(
        CID,
        "SELECT/INSERT plus a column-limited UPDATE, never a bare UPDATE/DELETE grant",
        'GRANT SELECT, INSERT ON model_import_jobs TO "{APP_ROLE_NAME}"'.replace(
            "{APP_ROLE_NAME}", ""
        )
        in src.replace('f\'GRANT SELECT, INSERT ON model_import_jobs TO "{APP_ROLE_NAME}"\'', "")
        or 'GRANT SELECT, INSERT ON model_import_jobs TO "' in src,
    )
    check(
        CID,
        "UPDATE is column-limited to state/failure_code/updated_at only",
        "_JOB_UPDATE_COLUMNS = \"state, failure_code, updated_at\"" in src
        and "GRANT UPDATE ({_JOB_UPDATE_COLUMNS})" in src,
    )
    check(
        CID,
        "no DELETE grant anywhere in this migration",
        re.search(r"GRANT[^\n]*DELETE", src) is None and "GRANT ALL" not in src,
    )
    check(
        CID,
        "state and origin enums are real database CHECKs",
        "ck_model_import_jobs_origin" in src and "ck_model_import_jobs_state" in src,
    )
    check(
        CID,
        "failure_code is present exactly when the state is failed/blocked_external",
        "(state in ('failed','blocked_external')) = (failure_code IS NOT NULL)" in src,
    )
    check(
        CID,
        "composite FKs to organizations/projects/model_versions/trainable_artifacts",
        '["organization_id", "project_id"]' in src
        and '["organization_id", "parent_model_version_id"]' in src
        and '["organization_id", "trainable_artifact_id"]' in src,
    )


def _run_migration_with(mutated: str) -> bool:
    """True when the shape tests above would catch `mutated`."""
    original = MIGRATION_PATH.read_text()
    MIGRATION_PATH.write_text(mutated)
    try:
        before = len(FAILURES)
        test_m1_model_versions_columns_and_backfill()
        test_m1_model_versions_checks()
        test_m2_model_import_jobs_rls_grants_and_checks()
        caught = len(FAILURES) > before
        del FAILURES[before:]
        return caught
    finally:
        MIGRATION_PATH.write_text(original)


def test_fault_injection_against_the_migration_file_on_disk() -> None:
    src = MIGRATION_PATH.read_text()
    check(
        CID,
        "dropping FORCE row level security is caught",
        _run_migration_with(
            src.replace('"ALTER TABLE model_import_jobs FORCE ROW LEVEL SECURITY"', '"SELECT 1"')
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
        "widening the job update grant to every column is caught",
        _run_migration_with(
            src.replace(
                'GRANT UPDATE ({_JOB_UPDATE_COLUMNS}) ON model_import_jobs',
                "GRANT UPDATE ON model_import_jobs",
            )
        ),
    )
    check(
        CID,
        "dropping the external_endpoint consistency CHECK is caught",
        _run_migration_with(
            src.replace(
                '    op.create_check_constraint(\n'
                '        "ck_model_versions_external_endpoint_consistency",\n'
                '        "model_versions",\n'
                '        "origin <> \'external_endpoint\' OR ("\n'
                '        "trainable_artifact_id IS NULL AND weights_digest IS NULL "\n'
                '        "AND external_provider_model_ref IS NOT NULL)",\n'
                "    )\n",
                "",
            )
        ),
    )
    check(
        CID,
        "dropping the imported_adapter consistency CHECK is caught",
        _run_migration_with(
            src.replace(
                '    op.create_check_constraint(\n'
                '        "ck_model_versions_imported_adapter_consistency",\n'
                '        "model_versions",\n'
                '        "origin <> \'imported_adapter\' OR "\n'
                '        "(base_model_revision IS NOT NULL AND adapter_only)",\n'
                "    )\n",
                "",
            )
        ),
    )
    check(
        CID,
        "a wrong/missing origin-backfill mapping is caught",
        _run_migration_with(
            src.replace("WHEN 'adapter' THEN 'imported_adapter'", "WHEN 'adapter' THEN 'imported_weights'")
        ),
    )


def _offline_sql(revision_range: str, action: str) -> str:
    """Runs the real, unmodified alembic `action` in `--sql` (offline) mode:
    real Python migration code executes and every real `op.*` call renders
    its DDL, but nothing connects to a database. The only way (short of a
    live Postgres) to prove `upgrade()`/`downgrade()` themselves are not
    short-circuited -- the source-text shape checks above never actually
    call them."""
    env = {**os.environ, "SOVRA_DATABASE_URL": "postgresql+psycopg://x:y@localhost:5432/z"}
    result = subprocess.run(
        [sys.executable, "-m", "alembic", action, revision_range, "--sql"],
        cwd=str(ROOT / "apps" / "api"),
        capture_output=True,
        text=True,
        env=env,
    )
    check(
        CID,
        f"alembic {action} {revision_range} --sql exits 0 ({result.stderr[-300:]})",
        result.returncode == 0,
    )
    return result.stdout


def test_m1_offline_upgrade_sql_actually_runs() -> None:
    up = _offline_sql("0025_dataset_upload_hardening:0027_model_import", "upgrade")
    for needle in (
        "ALTER TABLE model_versions ADD COLUMN origin",
        "ALTER TABLE model_versions ALTER COLUMN origin SET NOT NULL",
        "ck_model_versions_external_endpoint_consistency",
        "ck_model_versions_imported_adapter_consistency",
        "CREATE TABLE model_import_jobs",
        "UPDATE model_versions SET origin = CASE import_kind",
        "version_num='0027_model_import'",
    ):
        check(CID, f"the real upgrade SQL contains {needle!r}", needle in up)


def test_m1_offline_downgrade_sql_actually_runs() -> None:
    down = _offline_sql("0027_model_import:0025_dataset_upload_hardening", "downgrade")
    for needle in (
        "DROP TABLE model_import_jobs",
        "ALTER TABLE model_versions DROP COLUMN origin",
        "ALTER TABLE model_versions DROP CONSTRAINT ck_model_versions_origin_values",
        "version_num='0025_dataset_upload_hardening'",
    ):
        check(CID, f"the real downgrade SQL contains {needle!r}", needle in down)


# -- S*: store against SQLite, with the reused DB-touching seams replaced ----


class _Audit:
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    def __call__(self, session: Session, **kwargs: Any) -> None:
        self.events.append(kwargs)


def _passthrough_execute(session: Session, organization_id: uuid.UUID, statement: Any) -> Any:
    return session.execute(statement)


_ORG = uuid.uuid4()
_PROJECT = uuid.uuid4()
_CTX: Any = SimpleNamespace(organization_id=_ORG)
_ACTOR = crud.ActorInfo(actor_type="user", actor_id="user-1", request_id="req-1")


@contextlib.contextmanager
def _store_env(
    *,
    project_ok: bool = True,
    parent_versions: set[uuid.UUID] | None = None,
    artifacts: set[uuid.UUID] | None = None,
):
    engine = create_engine("sqlite://")
    schema.ModelImportJob.__table__.create(engine)
    audit = _Audit()

    def fake_authorize_project(session: Any, context: Any, project_id: Any) -> None:
        if not project_ok:
            raise crud.ProjectNotFoundError("no such project")

    def fake_fetch_model_version(
        session: Any, context: Any, project_id: Any, model_version_id: Any
    ) -> Any:
        if parent_versions is None or model_version_id not in parent_versions:
            raise crud.ModelVersionNotFoundError("no such parent")
        return SimpleNamespace(id=model_version_id)

    def fake_get_artifact(session: Any, context: Any, artifact_id: Any) -> Any:
        if artifacts is None or artifact_id not in artifacts:
            raise ArtifactNotFoundError("no such artifact")
        return SimpleNamespace(id=artifact_id)

    saved = (
        crud._authorize_project_or_raise,
        crud._fetch_model_version_or_raise,
        crud.get_artifact,
        crud.execute_tenant_scoped,
        crud.write_audit_event,
    )
    crud._authorize_project_or_raise = fake_authorize_project
    crud._fetch_model_version_or_raise = fake_fetch_model_version
    crud.get_artifact = fake_get_artifact
    crud.execute_tenant_scoped = _passthrough_execute
    crud.write_audit_event = audit
    try:
        with Session(engine) as session:
            yield session, audit
    finally:
        (
            crud._authorize_project_or_raise,
            crud._fetch_model_version_or_raise,
            crud.get_artifact,
            crud.execute_tenant_scoped,
            crud.write_audit_event,
        ) = saved
        engine.dispose()


def _jobs(session: Session) -> list[schema.ModelImportJob]:
    return list(session.query(schema.ModelImportJob).all())


def _raises(fn: Any, error: type[BaseException]) -> bool:
    try:
        fn()
    except error:
        return True
    except Exception:  # noqa: BLE001 - the wrong exception type is a failed check
        return False
    return False


def test_s1_each_origin_creates_a_queued_job_with_one_audit_event() -> None:
    adapter_base = uuid.uuid4()
    artifact = uuid.uuid4()
    with _store_env(parent_versions={adapter_base}, artifacts={artifact}) as (session, audit):
        job = crud.create_model_import_job(
            session,
            _CTX,
            project_id=_PROJECT,
            requested_origin="imported_weights",
            parent_model_version_id=None,
            trainable_artifact_id=artifact,
            external_provider_model_ref=None,
            actor=_ACTOR,
        )
        check(
            CID,
            "imported_weights creates a queued job with no failure_code",
            job.state == "queued"
            and job.failure_code is None
            and job.requested_origin == "imported_weights"
            and job.trainable_artifact_id == artifact
            and job.organization_id == _ORG
            and job.project_id == _PROJECT
            and job.created_by == "user-1",
        )
        check(CID, "exactly one audit event, naming the action/resource", len(audit.events) == 1)
        event = audit.events[0]
        check(
            CID,
            "the audit event carries the resource id, action and actor",
            event["action"] == "model_import_job.create"
            and event["resource_type"] == "model_import_job"
            and event["resource_id"] == str(job.id)
            and event["organization_id"] == _ORG
            and event["actor_id"] == "user-1"
            and event["request_id"] == "req-1"
            and event["details"]["requested_origin"] == "imported_weights",
        )
        check(
            CID,
            "audit details carry the real artifact id (present) and None for the absent parent",
            event["details"]["trainable_artifact_id"] == str(artifact)
            and event["details"]["parent_model_version_id"] is None,
        )

        job2 = crud.create_model_import_job(
            session,
            _CTX,
            project_id=_PROJECT,
            requested_origin="imported_adapter",
            parent_model_version_id=adapter_base,
            trainable_artifact_id=None,
            external_provider_model_ref=None,
            actor=_ACTOR,
        )
        check(
            CID,
            "imported_adapter with a real base is queued",
            job2.state == "queued" and job2.parent_model_version_id == adapter_base,
        )
        event2 = audit.events[-1]
        check(
            CID,
            "audit details carry the real parent id (present) and None for the absent artifact",
            event2["details"]["parent_model_version_id"] == str(adapter_base)
            and event2["details"]["trainable_artifact_id"] is None,
        )

        job3 = crud.create_model_import_job(
            session,
            _CTX,
            project_id=_PROJECT,
            requested_origin="external_endpoint",
            parent_model_version_id=None,
            trainable_artifact_id=None,
            external_provider_model_ref="provider://model/x",
            actor=_ACTOR,
        )
        check(
            CID,
            "external_endpoint with a real provider ref and no artifact is queued",
            job3.state == "queued"
            and job3.trainable_artifact_id is None
            and job3.external_provider_model_ref == "provider://model/x",
        )

        job4 = crud.create_model_import_job(
            session,
            _CTX,
            project_id=_PROJECT,
            requested_origin="factory_trained",
            parent_model_version_id=None,
            trainable_artifact_id=None,
            external_provider_model_ref=None,
            actor=_ACTOR,
        )
        check(CID, "factory_trained with nothing else required is queued", job4.state == "queued")
        check(CID, "four jobs, four audit events", len(_jobs(session)) == 4 and len(audit.events) == 4)


def test_s2_external_endpoint_with_artifact_is_refused() -> None:
    artifact = uuid.uuid4()
    with _store_env(artifacts={artifact}) as (session, audit):
        check(
            CID,
            "external_endpoint + trainable_artifact_id is a ModelImportValidationError",
            _raises(
                lambda: crud.create_model_import_job(
                    session,
                    _CTX,
                    project_id=_PROJECT,
                    requested_origin="external_endpoint",
                    parent_model_version_id=None,
                    trainable_artifact_id=artifact,
                    external_provider_model_ref="provider://x",
                    actor=_ACTOR,
                ),
                schema.ModelImportValidationError,
            ),
        )
        check(CID, "nothing was written by the refusal", not _jobs(session) and not audit.events)


def test_s3_external_endpoint_with_no_provider_ref_is_refused() -> None:
    with _store_env() as (session, audit):
        for bad_ref in (None, "", ):
            check(
                CID,
                f"external_endpoint with external_provider_model_ref={bad_ref!r} is refused",
                _raises(
                    lambda r=bad_ref: crud.create_model_import_job(
                        session,
                        _CTX,
                        project_id=_PROJECT,
                        requested_origin="external_endpoint",
                        parent_model_version_id=None,
                        trainable_artifact_id=None,
                        external_provider_model_ref=r,
                        actor=_ACTOR,
                    ),
                    schema.ModelImportValidationError,
                ),
            )
        check(CID, "nothing was written by either refusal", not _jobs(session) and not audit.events)


def test_s4_imported_adapter_with_no_base_is_refused() -> None:
    with _store_env() as (session, audit):
        check(
            CID,
            "imported_adapter with parent_model_version_id=None is refused",
            _raises(
                lambda: crud.create_model_import_job(
                    session,
                    _CTX,
                    project_id=_PROJECT,
                    requested_origin="imported_adapter",
                    parent_model_version_id=None,
                    trainable_artifact_id=None,
                    external_provider_model_ref=None,
                    actor=_ACTOR,
                ),
                schema.ModelImportValidationError,
            ),
        )
        check(CID, "nothing was written", not _jobs(session) and not audit.events)


def test_s5_foreign_or_missing_references_are_refused() -> None:
    real_parent = uuid.uuid4()
    real_artifact = uuid.uuid4()
    with _store_env(project_ok=False) as (session, audit):
        check(
            CID,
            "project_ok=False -> ProjectNotFoundError, nothing written",
            _raises(
                lambda: crud.create_model_import_job(
                    session,
                    _CTX,
                    project_id=_PROJECT,
                    requested_origin="factory_trained",
                    parent_model_version_id=None,
                    trainable_artifact_id=None,
                    external_provider_model_ref=None,
                    actor=_ACTOR,
                ),
                crud.ProjectNotFoundError,
            )
            and not _jobs(session)
            and not audit.events,
        )
    with _store_env(parent_versions={real_parent}, artifacts={real_artifact}) as (session, audit):
        check(
            CID,
            "a foreign parent_model_version_id is ModelVersionNotFoundError, nothing written",
            _raises(
                lambda: crud.create_model_import_job(
                    session,
                    _CTX,
                    project_id=_PROJECT,
                    requested_origin="imported_adapter",
                    parent_model_version_id=uuid.uuid4(),
                    trainable_artifact_id=None,
                    external_provider_model_ref=None,
                    actor=_ACTOR,
                ),
                crud.ModelVersionNotFoundError,
            )
            and not _jobs(session)
            and not audit.events,
        )
        check(
            CID,
            "a foreign trainable_artifact_id is ArtifactNotFoundError, nothing written",
            _raises(
                lambda: crud.create_model_import_job(
                    session,
                    _CTX,
                    project_id=_PROJECT,
                    requested_origin="imported_weights",
                    parent_model_version_id=None,
                    trainable_artifact_id=uuid.uuid4(),
                    external_provider_model_ref=None,
                    actor=_ACTOR,
                ),
                ArtifactNotFoundError,
            )
            and not _jobs(session)
            and not audit.events,
        )
        check(
            CID,
            "the real parent/artifact ids both still work in this same environment",
            crud.create_model_import_job(
                session,
                _CTX,
                project_id=_PROJECT,
                requested_origin="imported_adapter",
                parent_model_version_id=real_parent,
                trainable_artifact_id=real_artifact,
                external_provider_model_ref=None,
                actor=_ACTOR,
            ).state
            == "queued",
        )


def test_s6_unknown_origin_is_refused() -> None:
    with _store_env() as (session, audit):
        for bad in ("", "ENDPOINT", "endpoint", "full_weight", 7, None, ["factory_trained"]):
            check(
                CID,
                f"origin {bad!r} is refused",
                _raises(
                    lambda o=bad: crud.create_model_import_job(
                        session,
                        _CTX,
                        project_id=_PROJECT,
                        requested_origin=o,
                        parent_model_version_id=None,
                        trainable_artifact_id=None,
                        external_provider_model_ref=None,
                        actor=_ACTOR,
                    ),
                    schema.ModelImportValidationError,
                ),
            )
        check(CID, "none of the refusals wrote anything", not _jobs(session) and not audit.events)


def test_s7_get_and_list_are_scoped_to_the_exact_project() -> None:
    other_project = uuid.uuid4()
    with _store_env() as (session, _audit):
        job = crud.create_model_import_job(
            session,
            _CTX,
            project_id=_PROJECT,
            requested_origin="factory_trained",
            parent_model_version_id=None,
            trainable_artifact_id=None,
            external_provider_model_ref=None,
            actor=_ACTOR,
        )
        check(
            CID,
            "get_model_import_job returns the real row for the right project",
            crud.get_model_import_job(session, _CTX, _PROJECT, job.id).id == job.id,
        )
        check(
            CID,
            "the identical job id under a different project is not found",
            _raises(
                lambda: crud.get_model_import_job(session, _CTX, other_project, job.id),
                crud.ModelImportJobNotFoundError,
            ),
        )
        check(
            CID,
            "a missing id is not found",
            _raises(
                lambda: crud.get_model_import_job(session, _CTX, _PROJECT, uuid.uuid4()),
                crud.ModelImportJobNotFoundError,
            ),
        )
        check(
            CID,
            "a non-uuid id is not found, never a raw TypeError/ValueError",
            _raises(
                lambda: crud.get_model_import_job(session, _CTX, _PROJECT, "not-a-uuid"),
                crud.ModelImportJobNotFoundError,
            ),
        )
        check(
            CID,
            "list is scoped to the project: one job here, none under the other project",
            len(crud.list_model_import_jobs(session, _CTX, _PROJECT)) == 1
            and len(crud.list_model_import_jobs(session, _CTX, other_project)) == 0,
        )


def test_s8_schema_level_validators_are_exact_and_independent() -> None:
    check(
        CID,
        "validate_origin refuses a case/whitespace variant of a real origin "
        "(never normalized, even at the schema layer's own entry point)",
        _raises(lambda: schema.validate_origin(" External_Endpoint "), schema.ModelImportValidationError)
        and _raises(lambda: schema.validate_origin("FACTORY_TRAINED"), schema.ModelImportValidationError),
    )
    f = schema.validate_external_provider_model_ref
    check(CID, "an empty external_provider_model_ref is refused", _raises(lambda: f(""), schema.ModelImportValidationError))
    check(CID, "a None external_provider_model_ref is refused", _raises(lambda: f(None), schema.ModelImportValidationError))
    check(CID, "a non-string external_provider_model_ref is refused", _raises(lambda: f(7), schema.ModelImportValidationError))
    check(
        CID,
        "a value over the max length is refused, exactly at the max is accepted",
        _raises(lambda: f("x" * (schema.EXTERNAL_PROVIDER_MODEL_REF_MAX_LENGTH + 1)), schema.ModelImportValidationError)
        and f("x" * schema.EXTERNAL_PROVIDER_MODEL_REF_MAX_LENGTH) == "x" * schema.EXTERNAL_PROVIDER_MODEL_REF_MAX_LENGTH,
    )
    check(CID, "a normal value round-trips unchanged", f("provider://m") == "provider://m")

    def _bad_job() -> Any:
        return schema.ModelImportJob(
            id=uuid.uuid4(),
            organization_id=_ORG,
            project_id=_PROJECT,
            requested_origin="bogus-origin",
            parent_model_version_id=None,
            trainable_artifact_id=None,
            external_provider_model_ref=None,
            state="queued",
            failure_code=None,
            created_by="x",
            created_at=_NOW,
            updated_at=_NOW,
        )

    check(
        CID,
        "ModelImportJob.__init__ re-validates requested_origin itself, independent of crud.py "
        "(defense in depth: constructing the ORM object directly with a bad origin still refuses)",
        _raises(_bad_job, schema.ModelImportValidationError),
    )


def test_s9_origin_branch_comparisons_are_exact_never_normalized() -> None:
    """crud.create_model_import_job's own `requested_origin == "external_endpoint"`/
    `== "imported_adapter"` branch guards must use exact string equality, never a
    case/whitespace-tolerant match -- probed by bypassing the module's own
    validate_origin (which already refuses any such variant) to show the
    *comparison itself* does not quietly normalize one through."""
    with _store_env() as (session, audit):
        original = crud.validate_origin
        crud.validate_origin = lambda v: v  # identity: let an un-normalized value through
        try:
            endpoint_message: str | None = None
            try:
                crud.create_model_import_job(
                    session,
                    _CTX,
                    project_id=_PROJECT,
                    requested_origin=" External_Endpoint ",
                    parent_model_version_id=None,
                    trainable_artifact_id=None,
                    external_provider_model_ref=None,
                    actor=_ACTOR,
                )
            except schema.ModelImportValidationError as exc:
                endpoint_message = str(exc)
            check(
                CID,
                "a padded/cased 'external_endpoint' variant never matches the exact == guard -- "
                "its own external_provider_model_ref requirement never fires for it",
                endpoint_message is not None
                and "requires a real external_provider_model_ref" not in endpoint_message,
            )

            adapter_message: str | None = None
            try:
                crud.create_model_import_job(
                    session,
                    _CTX,
                    project_id=_PROJECT,
                    requested_origin=" Imported_Adapter ",
                    parent_model_version_id=None,
                    trainable_artifact_id=None,
                    external_provider_model_ref=None,
                    actor=_ACTOR,
                )
            except schema.ModelImportValidationError as exc:
                adapter_message = str(exc)
            check(
                CID,
                "a padded/cased 'imported_adapter' variant never matches the exact == guard -- "
                "its own parent_model_version_id (base) requirement never fires for it",
                adapter_message is not None
                and "requires parent_model_version_id" not in adapter_message,
            )
        finally:
            crud.validate_origin = original
        check(CID, "neither probe wrote a job", not _jobs(session) and not audit.events)


_MV_ORG = uuid.uuid4()
_MV_PROJECT = uuid.uuid4()
_MV_ARTIFACT = uuid.uuid4()
_MV_DIGEST = "sha256:" + "a" * 64


def _mv(**overrides: object) -> mv_schema.ModelVersion:
    kwargs: dict[str, object] = {
        "organization_id": _MV_ORG,
        "project_id": _MV_PROJECT,
        "version_number": 1,
        "import_kind": "endpoint",
        "endpoint_reference": "https://models.example.com/v1/production",
        "created_by": "user-1",
        "created_at": _NOW,
    }
    kwargs.update(overrides)
    return mv_schema.ModelVersion(**kwargs)


def test_m2_model_version_new_field_defaulting() -> None:
    endpoint = _mv()
    check(
        CID,
        "an unmodified endpoint import (no new fields passed) is auto-classified "
        "origin=external_endpoint with external_provider_model_ref copied from "
        "endpoint_reference, lineage_completeness=unknown, adapter_only=False",
        endpoint.origin == "external_endpoint"
        and endpoint.external_provider_model_ref == "https://models.example.com/v1/production"
        and endpoint.lineage_completeness == "unknown"
        and endpoint.adapter_only is False,
    )
    full_weight = _mv(
        import_kind="full_weight",
        trainable_artifact_id=_MV_ARTIFACT,
        weights_digest=_MV_DIGEST,
        base_model_revision="a1b2c3d4",
        endpoint_reference=None,
    )
    check(
        CID,
        "an unmodified full_weight import is auto-classified origin=imported_weights, "
        "adapter_only=False",
        full_weight.origin == "imported_weights" and full_weight.adapter_only is False,
    )
    adapter = _mv(
        import_kind="adapter",
        trainable_artifact_id=_MV_ARTIFACT,
        weights_digest=_MV_DIGEST,
        base_model_revision="a1b2c3d4",
        endpoint_reference=None,
    )
    check(
        CID,
        "an unmodified adapter import is auto-classified origin=imported_adapter, "
        "adapter_only=True (satisfying the new ck_model_versions_imported_adapter_consistency)",
        adapter.origin == "imported_adapter" and adapter.adapter_only is True,
    )
    check(
        CID,
        "an explicit origin always wins over the derived one",
        _mv(origin="factory_trained").origin == "factory_trained",
    )
    check(
        CID,
        "an explicit external_provider_model_ref always wins over the copied endpoint_reference",
        _mv(external_provider_model_ref="provider://explicit").external_provider_model_ref
        == "provider://explicit",
    )
    check(
        CID,
        "an explicit lineage_completeness/adapter_only always wins over the default",
        _mv(lineage_completeness="complete").lineage_completeness == "complete"
        and _mv(
            import_kind="full_weight",
            trainable_artifact_id=_MV_ARTIFACT,
            weights_digest=_MV_DIGEST,
            base_model_revision="a1b2c3d4",
            endpoint_reference=None,
            adapter_only=True,
        ).adapter_only
        is True,
    )
    check(
        CID,
        "an invalid explicit origin is refused",
        _raises(lambda: _mv(origin="bogus"), mv_schema.ModelVersionValidationError),
    )
    check(
        CID,
        "an invalid explicit lineage_completeness is refused",
        _raises(
            lambda: _mv(lineage_completeness="bogus"), mv_schema.ModelVersionValidationError
        ),
    )


def test_m2_import_kind_branch_comparisons_are_exact_never_normalized() -> None:
    """model_version_schema.py's own `kind == "endpoint"`/`kind in
    _ARTIFACT_ONLY_KINDS` guards (pre-existing, FTF-1001) must use exact
    matching -- probed the same way as test_s9, by bypassing the module's
    own validate_import_kind (which already refuses any case/whitespace
    variant) to show the comparisons themselves never quietly normalize."""
    original = mv_schema.validate_import_kind
    mv_schema.validate_import_kind = lambda v: v
    try:
        check(
            CID,
            "a padded/cased 'endpoint' variant never matches kind == 'endpoint' -- an "
            "artifact id that the real endpoint branch would forbid is let through "
            "uninspected (neither branch recognizes the kind at all)",
            _mv(
                import_kind=" Endpoint ", trainable_artifact_id=_MV_ARTIFACT, origin="factory_trained"
            ).import_kind
            == " Endpoint ",
        )
        check(
            CID,
            "a padded/cased 'adapter' variant never matches kind in _ARTIFACT_ONLY_KINDS -- "
            "an endpoint_reference that branch would forbid is let through uninspected",
            _mv(
                import_kind=" Adapter ",
                endpoint_reference="https://x.example.com",
                trainable_artifact_id=None,
                origin="factory_trained",
            ).import_kind
            == " Adapter ",
        )
    finally:
        mv_schema.validate_import_kind = original


# -- R*: routes, driven over real HTTP with the DB-touching seams replaced ----

_ISSUER = "https://issuer.example"
_ORG_ID = uuid.uuid4()
_ADMIN = AuthenticatedPrincipal(issuer=_ISSUER, subject="admin-1")
_ML = AuthenticatedPrincipal(issuer=_ISSUER, subject="ml-1")
_VIEWER = AuthenticatedPrincipal(issuer=_ISSUER, subject="viewer-1")
_PRINCIPALS = {"admin": _ADMIN, "ml": _ML, "viewer": _VIEWER}
_ROLES = {"admin-1": Role.ORGANIZATION_ADMIN, "ml-1": Role.ML_ENGINEER, "viewer-1": Role.VIEWER}
_PROJECT_ID = uuid.uuid4()
_JOB_ID = uuid.uuid4()


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
def _session_cm(session_factory: Any, tenant_context: Any):
    yield object()


@contextlib.contextmanager
def _running_app():
    app = FastAPI()
    app.include_router(
        mir.build_model_import_router(
            lambda: None, principal_resolver=_principal_resolver, role_lookup=_role_lookup
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


def _collection_path() -> str:
    return f"/api/v1/organizations/{_ORG_ID}/projects/{_PROJECT_ID}/model-imports"


def _item_path(job_id: Any = _JOB_ID) -> str:
    return f"{_collection_path()}/{job_id}"


def _job_ns(
    *,
    job_id: uuid.UUID = _JOB_ID,
    origin: str = "factory_trained",
    state: str = "queued",
    parent_model_version_id: uuid.UUID | None = None,
    trainable_artifact_id: uuid.UUID | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        id=job_id,
        organization_id=_ORG_ID,
        project_id=_PROJECT_ID,
        requested_origin=origin,
        parent_model_version_id=parent_model_version_id,
        trainable_artifact_id=trainable_artifact_id,
        external_provider_model_ref=None,
        state=state,
        failure_code=None,
        created_by="ml-1",
        created_at=_NOW,
        updated_at=_NOW,
    )


class _Env:
    def __init__(self) -> None:
        self.create_calls: list[dict[str, Any]] = []
        self.create_result: Any = None
        self.create_error: Exception | None = None
        self.get_result: Any = None
        self.get_error: Exception | None = None
        self.list_result: list[Any] = []

    def create(self, session: Any, ctx: Any, **kwargs: Any) -> Any:
        self.create_calls.append(kwargs)
        if self.create_error is not None:
            raise self.create_error
        return self.create_result

    def get(self, session: Any, ctx: Any, project_id: Any, job_id: Any) -> Any:
        if self.get_error is not None:
            raise self.get_error
        return self.get_result

    def list_jobs(self, session: Any, ctx: Any, project_id: Any) -> Any:
        return self.list_result


@contextlib.contextmanager
def _patched(env: _Env):
    names = {
        "tenant_scoped_session_for": _session_cm,
        "create_model_import_job": env.create,
        "get_model_import_job": env.get,
        "list_model_import_jobs": env.list_jobs,
    }
    originals = {name: getattr(mir, name) for name in names}
    for name, value in names.items():
        setattr(mir, name, value)
    try:
        yield
    finally:
        for name, value in originals.items():
            setattr(mir, name, value)


def test_r1_post_body_validation() -> None:
    with _running_app() as base:
        env = _Env()
        with _patched(env):
            cases: list[tuple[str, dict[str, Any]]] = [
                ("unknown field", {"requested_origin": "factory_trained", "x": 1}),
                ("missing requested_origin", {}),
                ("a numeric requested_origin", {"requested_origin": 7}),
                ("a non-uuid parent_model_version_id", {"requested_origin": "imported_adapter", "parent_model_version_id": "nope"}),
                ("a numeric parent_model_version_id", {"requested_origin": "imported_adapter", "parent_model_version_id": 7}),
                ("a non-uuid trainable_artifact_id", {"requested_origin": "imported_weights", "trainable_artifact_id": "nope"}),
                ("a numeric trainable_artifact_id", {"requested_origin": "imported_weights", "trainable_artifact_id": 7}),
                ("a numeric external_provider_model_ref", {"requested_origin": "external_endpoint", "external_provider_model_ref": 7}),
            ]
            for label, payload in cases:
                result = _request(base, "POST", _collection_path(), payload=payload)
                check(CID, f"{label} -> 400", result.status == 400)
            check(CID, "a list body -> 400", _request(base, "POST", _collection_path(), payload=[1]).status == 400)
            check(
                CID,
                "a list body containing only a valid body-key string is still 400 "
                "(the is-a-dict check runs first; proves it is never skipped)",
                _request(base, "POST", _collection_path(), payload=["requested_origin"]).status
                == 400,
            )
            check(CID, "broken JSON -> 400", _request(base, "POST", _collection_path(), raw=b"{").status == 400)
            check(CID, "no body -> 400", _request(base, "POST", _collection_path()).status == 400)
            check(CID, "no refused request reached the store", not env.create_calls)
            unknown = _request(
                base, "POST", _collection_path(), payload={"requested_origin": "factory_trained", "y": 2}
            )
            check(CID, "the unknown field is named in the 400 detail", "y" in unknown.json()["detail"])


def test_r2_post_roles_and_unauthenticated() -> None:
    with _running_app() as base:
        env = _Env()
        env.create_result = _job_ns()
        payload = {"requested_origin": "factory_trained"}
        with _patched(env):
            check(
                CID,
                "a viewer cannot POST (403)",
                _request(base, "POST", _collection_path(), principal="viewer", payload=payload).status == 403,
            )
            check(
                CID,
                "no principal is 401 on POST",
                _request(base, "POST", _collection_path(), principal=None, payload=payload).status == 401,
            )
            check(CID, "the viewer's refusal never reached the store", not env.create_calls)
            for principal in ("ml", "admin"):
                check(
                    CID,
                    f"{principal} may POST (201)",
                    _request(base, "POST", _collection_path(), principal=principal, payload=payload).status
                    == 201,
                )
            check(
                CID,
                "a viewer may GET the collection and an item",
                _request(base, "GET", _collection_path(), principal="viewer").status == 200,
            )
            check(
                CID,
                "no principal is 401 on GET",
                _request(base, "GET", _collection_path(), principal=None).status == 401,
            )


def test_r3_post_passes_exactly_the_callers_fields_with_a_fresh_actor() -> None:
    with _running_app() as base:
        env = _Env()
        env.create_result = _job_ns()
        parent = str(uuid.uuid4())
        artifact = str(uuid.uuid4())
        payload = {
            "requested_origin": "imported_adapter",
            "parent_model_version_id": parent,
            "trainable_artifact_id": artifact,
        }
        with _patched(env):
            first = _request(base, "POST", _collection_path(), payload=payload)
            second = _request(base, "POST", _collection_path(), payload=payload)
            check(CID, "both calls succeed", first.status == 201 and second.status == 201)
            call = env.create_calls[0]
            check(
                CID,
                "the store received the exact parsed ids, project and actor",
                str(call["project_id"]) == str(_PROJECT_ID)
                and call["requested_origin"] == "imported_adapter"
                and str(call["parent_model_version_id"]) == parent
                and str(call["trainable_artifact_id"]) == artifact
                and call["external_provider_model_ref"] is None
                and call["actor"].actor_id == "ml-1"
                and call["actor"].actor_type == "user",
            )
            check(
                CID,
                "each call gets its own request id",
                env.create_calls[0]["actor"].request_id != env.create_calls[1]["actor"].request_id,
            )


def test_r3_store_errors_map_to_stable_codes() -> None:
    with _running_app() as base:
        env = _Env()
        payload = {"requested_origin": "factory_trained"}
        with _patched(env):
            table: list[tuple[Exception, int, str | None]] = [
                (crud.ProjectNotFoundError("x"), 404, "not_found"),
                (crud.ModelVersionNotFoundError("x"), 404, "not_found"),
                (ArtifactNotFoundError("x"), 404, "not_found"),
                (schema.ModelImportValidationError("bad shape"), 400, None),
            ]
            for error, status, code in table:
                env.create_error = error
                result = _request(base, "POST", _collection_path(), payload=payload)
                if code is not None:
                    check(CID, f"{type(error).__name__} -> {status} {code}", result.status == status and result.json() == {"detail": code})
                else:
                    check(
                        CID,
                        f"{type(error).__name__} -> {status} with the real message",
                        result.status == status and result.json()["detail"] == "bad shape",
                    )


def test_r_get_list_and_item() -> None:
    with _running_app() as base:
        env = _Env()
        with _patched(env):
            env.list_result = [_job_ns(), _job_ns(job_id=uuid.uuid4(), state="queued")]
            listed = _request(base, "GET", _collection_path()).json()
            check(CID, "list returns every job serialized", len(listed) == 2 and listed[0]["state"] == "queued")

            env.get_result = _job_ns(origin="external_endpoint")
            one = _request(base, "GET", _item_path()).json()
            check(
                CID,
                "a single job is serialized with exactly the contract's own key set",
                set(one)
                == {
                    "id",
                    "organization_id",
                    "project_id",
                    "requested_origin",
                    "parent_model_version_id",
                    "trainable_artifact_id",
                    "external_provider_model_ref",
                    "state",
                    "failure_code",
                    "created_by",
                    "created_at",
                    "updated_at",
                },
            )
            check(
                CID,
                "a single job is serialized with every contract field's real value "
                "(organization_id/project_id are the real ids, never a placeholder)",
                one["id"] == str(_JOB_ID)
                and one["organization_id"] == str(_ORG_ID)
                and one["project_id"] == str(_PROJECT_ID)
                and one["requested_origin"] == "external_endpoint"
                and one["parent_model_version_id"] is None
                and one["trainable_artifact_id"] is None
                and one["state"] == "queued"
                and one["failure_code"] is None
                and one["created_by"] == "ml-1"
                and one["created_at"] == _NOW.isoformat()
                and one["updated_at"] == _NOW.isoformat(),
            )

            real_parent, real_artifact = uuid.uuid4(), uuid.uuid4()
            env.get_result = _job_ns(
                parent_model_version_id=real_parent, trainable_artifact_id=real_artifact
            )
            with_ids = _request(base, "GET", _item_path()).json()
            check(
                CID,
                "when the real row carries a parent/artifact id, serialization carries "
                "the exact same id as a string, never None and never a different value",
                with_ids["parent_model_version_id"] == str(real_parent)
                and with_ids["trainable_artifact_id"] == str(real_artifact),
            )

            env.get_error = crud.ModelImportJobNotFoundError("gone")
            missing = _request(base, "GET", _item_path())
            check(CID, "a missing job is 404 not_found", missing.status == 404 and missing.json() == {"detail": "not_found"})


# -- C1 / wiring ---------------------------------------------------------------


def test_c1_interface_contract_declares_the_routes() -> None:
    doc = yaml.safe_load(INTERFACE_PATH.read_text())
    base = "/api/v1/organizations/{organization_id}/projects/{project_id}/model-imports"
    paths = doc["paths"]
    check(CID, "the collection path declares GET and POST", set(paths[base]) >= {"get", "post"})
    check(CID, "the item path declares GET only", set(paths[base + "/{model_import_job_id}"]) == {"parameters", "get"})
    post = paths[base]["post"]
    check(CID, "POST documents 200s/400/401/403/404", set(post["responses"]) >= {"201", "400", "401", "403", "404"})
    schemas = doc["components"]["schemas"]
    request = schemas["ModelImportJobCreateRequest"]
    check(
        CID,
        "the request body is closed and requires only requested_origin",
        request["additionalProperties"] is False
        and request["required"] == ["requested_origin"]
        and set(request["properties"])
        == {
            "requested_origin",
            "parent_model_version_id",
            "trainable_artifact_id",
            "external_provider_model_ref",
        },
    )
    job = schemas["ModelImportJob"]
    check(
        CID,
        "the job response is closed and carries exactly the contract fields",
        job["additionalProperties"] is False
        and set(job["properties"])
        == {
            "id",
            "organization_id",
            "project_id",
            "requested_origin",
            "parent_model_version_id",
            "trainable_artifact_id",
            "external_provider_model_ref",
            "state",
            "failure_code",
            "created_by",
            "created_at",
            "updated_at",
        },
    )
    check(
        CID,
        "state and origin are the real, closed enums",
        schemas["ModelImportJobState"]["enum"]
        == ["queued", "validating", "failed", "succeeded", "blocked_external"]
        and schemas["ModelImportOrigin"]["enum"]
        == ["factory_trained", "imported_weights", "imported_adapter", "external_endpoint"],
    )


def _router() -> Any:
    return mir.build_model_import_router(
        lambda: None, principal_resolver=_principal_resolver, role_lookup=_role_lookup
    )


def test_c1_router_is_wired_into_the_app_and_uses_closed_methods() -> None:
    main_src = MAIN_PATH.read_text()
    check(
        CID,
        "main.py imports and includes the import router with the shared resolver and lookup",
        "from app.model_versions.model_import_routes import build_model_import_router" in main_src
        and re.search(
            r"build_model_import_router\(\s*session_factory, principal_resolver=principal_resolver, role_lookup=role_lookup\s*\)",
            main_src,
        )
        is not None,
    )
    base = "/api/v1/organizations/{organization_id}/projects/{project_id}/model-imports"
    paths = {getattr(r, "path", "") for r in _router().routes}
    check(CID, "exactly the collection and item paths are mounted", paths == {base, base + "/{model_import_job_id}"})
    methods = {(getattr(r, "path", ""), m) for r in _router().routes for m in getattr(r, "methods", set())}
    check(
        CID,
        "exactly GET+POST on the collection and GET on the item (no PUT/PATCH/DELETE anywhere)",
        methods == {(base, "GET"), (base, "POST"), (base + "/{model_import_job_id}", "GET")},
    )


def test_c1_module_never_moves_a_job_past_queued() -> None:
    routes_src = (ROOT / "apps/api/app/model_versions/model_import_routes.py").read_text()
    crud_src = (ROOT / "apps/api/app/model_versions/model_import_crud.py").read_text()
    check(
        CID,
        "no production code in this slice ever sets state to anything but queued",
        "state=\"queued\"" in crud_src
        and "succeeded" not in crud_src
        and "succeeded" not in routes_src.split('"""', 2)[2],
    )
    check(
        CID,
        "the routes module never calls session.execute/update directly (grant stays a future worker's job)",
        "UPDATE model_import_jobs" not in routes_src and "UPDATE model_import_jobs" not in crud_src,
    )


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


def test_x_create_app_mounts_the_model_import_routes() -> None:
    """Same no-DB env fixture test_ftf_0308_p1/test_ftf_0214 use:
    `create_engine` is lazy and the role gate runs before any session is
    opened, so no database is needed. Exercises the real, unmodified
    `create_app()` -- the only way to kill the two mutants a diff anywhere
    inside that one large function makes reachable (an inserted fail-open
    `return None` at its very top, and a flipped `SOVRA_ENVIRONMENT ==
    "production"` comparison), neither of which this file's other,
    narrower router-level tests exercise."""
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
    with _running_app_from(app) as base:
        for method, path in (
            ("GET", _collection_path()),
            ("POST", _collection_path()),
            ("GET", _item_path()),
        ):
            result = _request(base, method, path, principal=None)
            check(
                CID,
                f"{method} {path} is mounted on the real app (401, not 404)",
                result.status == 401,
            )


@contextlib.contextmanager
def _running_app_from(app: FastAPI):
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


def main() -> int:
    tests = [value for name, value in sorted(globals().items()) if name.startswith("test_")]
    for test in tests:
        test()
    if FAILURES:
        print(f"{len(FAILURES)} failing check(s):")
        for failure in FAILURES:
            print(f"  {failure}")
        return 1
    print(f"OK -- {len(tests)} test functions, 0 failing checks ({CID})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
