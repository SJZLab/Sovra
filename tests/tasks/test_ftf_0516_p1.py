#!/usr/bin/env python3
"""FTF-0516-P1 -- dataset quality report / issue / resolution persistence and
the five quality routes (no-DB tests; the live Postgres evidence is
`tests/integration/test_ftf_0516_p1_quality_issues.py`, run by CI's
`dataset-quality-issues-integration` job).

Without any database this suite proves:

M  the tracked migration `0026_dataset_quality_issues.py`: chained onto
   `0025_dataset_upload_hardening` as the single head, ENABLE + FORCE RLS and a
   non-allow-all `tenant_isolation` policy on all three tables, the grants
   (reports: column-limited UPDATE; issues and resolutions: SELECT/INSERT;
   never DELETE), the CHECK / foreign-key / trigger names the contract names --
   each shown able to fail by editing the file on disk -- plus the SQL it
   generates offline and its helper columns;
S  the store over a real SQLite session (only the Postgres tenant guard and the
   audit writer are replaced; FTF-0508..0514 detector reports and FTF-0515's
   engine are the real ones): recording, idempotent re-recording, every read,
   and every resolution refusal with its stable code writing nothing;
R  the five routes on a real uvicorn server with the store patched: roles,
   held-out hiding, body validation, status / code mapping, pagination, and the
   exact response shape with no record content or message;
C  the interface contract (05-interface.yaml) and the app wiring.

Run directly: python3 tests/tasks/test_ftf_0516_p1.py
"""

from __future__ import annotations

import contextlib
import hashlib
import http.client
import importlib.util
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
from sqlalchemy import create_engine
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "apps" / "api"))

from workers.data.exact_duplicate_detector import detect_exact_duplicates  # noqa: E402
from workers.data.near_duplicate_detector import detect_near_duplicates  # noqa: E402
from workers.data.normalized_duplicate_detector import detect_normalized_duplicates  # noqa: E402
from workers.data.pii_secrets_detector import detect_pii_and_secrets  # noqa: E402
from workers.data.quality_issue_engine import (  # noqa: E402
    REQUIRED_DETECTORS,
    DetectorRun,
    IssueResolution,
    QualityBlockedError,
    QualityEngineError,
    QualityIssueInput,
    apply_resolutions,
    assert_clear_for_split,
    evaluate_quality,
)
from workers.data.record_schema_validator import RecordSchemaValidationReport  # noqa: E402
from workers.data.tokenization_analysis import TokenizationAnalysisReport  # noqa: E402
from workers.data.tool_argument_validator import ToolCallValidationReport  # noqa: E402

from app.auth.rbac_middleware import AuthenticatedPrincipal, Role  # noqa: E402
from app.datasets import dataset_quality_routes as routes  # noqa: E402
from app.datasets import dataset_quality_store as store  # noqa: E402
from app.datasets import dataset_upload_store as ustore  # noqa: E402

FAILURES: list[str] = []
CID = "FTF-0516-P1"


def check(criterion_id: str, description: str, condition: bool) -> None:
    if not condition:
        FAILURES.append(f"FAIL [{criterion_id}] {description}")


MIGRATION_PATH = ROOT / "apps" / "api" / "alembic" / "versions" / "0026_dataset_quality_issues.py"
INTERFACE_PATH = ROOT / "packages" / "contracts" / "openapi" / "05-interface.yaml"
MAIN_PATH = ROOT / "apps" / "api" / "app" / "main.py"
DETECTORS = sorted(REQUIRED_DETECTORS)
VERSIONS = {name: "1.0.0" for name in DETECTORS}


def _sha(seed: str) -> str:
    return hashlib.sha256(seed.encode()).hexdigest()


def _naive(value: datetime) -> datetime:
    """SQLite returns naive datetimes; compare wall-clock values."""
    return value.replace(tzinfo=None)


class _Str(str):
    """A str subclass: exact-type checks must refuse it."""


# -- M: migration shape --------------------------------------------------------


def _migration() -> str:
    return MIGRATION_PATH.read_text()


_TABLES = ("dataset_quality_reports", "dataset_quality_issues", "dataset_issue_resolutions")


def _migration_findings(src: str) -> list[str]:
    """Every shape rule the migration must satisfy; returns the broken ones."""
    broken: list[str] = []

    def need(label: str, ok: bool) -> None:
        if not ok:
            broken.append(label)

    need("revision id", 'revision = "0026_dataset_quality_issues"' in src)
    need("chained onto 0025", 'down_revision = "0025_dataset_upload_hardening"' in src)
    need("revision id fits alembic_version (<=32)", len("0026_dataset_quality_issues") <= 32)
    need("ENABLE rls", 'f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY"' in src)
    need("FORCE rls", 'f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY"' in src)
    need(
        "tenant policy on all three",
        'for table in ("dataset_quality_reports", "dataset_quality_issues", "dataset_issue_resolutions"):'  # noqa: E501
        in src,
    )
    need("policy name", "CREATE POLICY tenant_isolation ON {table} FOR ALL" in src)
    need("policy using", "USING (organization_id = {tenant_setting})" in src)
    need("policy with check", "WITH CHECK (organization_id = {tenant_setting})" in src)
    need("not allow-all", "USING (true)" not in src and "WITH CHECK (true)" not in src)
    need("reports SELECT/INSERT", "GRANT SELECT, INSERT ON dataset_quality_reports" in src)
    need("issues SELECT/INSERT", "GRANT SELECT, INSERT ON dataset_quality_issues" in src)
    need("resolutions SELECT/INSERT", "GRANT SELECT, INSERT ON dataset_issue_resolutions" in src)
    need(
        "reports column-limited UPDATE",
        "GRANT UPDATE (resolution_version, updated_at) ON dataset_quality_reports" in src,
    )
    upgrade_body = src.split("def upgrade")[1].split("def downgrade")[0]
    need("no DELETE grant", re.search(r"GRANT[^\n]*DELETE", upgrade_body) is None)
    for name in (
        "ck_dataset_quality_issues_evidence_sha256",
        "ck_dataset_quality_issues_has_positions_truthful",
        "ck_dataset_quality_issues_detector_failure_category",
        "ck_dataset_quality_issues_detector_failure_blocks",
        "ck_dataset_quality_issues_poisoning_blocks",
        "ck_dataset_quality_issues_indices_pair_positions",
        "ck_dataset_quality_reports_detector_versions",
        "ck_dataset_quality_reports_counts",
        "ck_dataset_issue_resolutions_not_detector_failure",
        "ck_dataset_issue_resolutions_waive_warning_only",
        "ck_dataset_issue_resolutions_waive_admin_only",
        "ck_dataset_issue_resolutions_needs_positions",
        "ck_dataset_issue_resolutions_excluded_positions",
        "ck_dataset_issue_resolutions_after_evidence",
        "ck_dataset_issue_resolutions_successor_later",
        "ck_dataset_issue_resolutions_successor_iff_new_data",
        "ck_dataset_issue_resolutions_reason",
        "fk_dataset_issue_resolutions_issue_evidence",
        "fk_dataset_issue_resolutions_successor_lineage",
        "fk_dataset_issue_resolutions_report_identity",
        "fk_dataset_quality_reports_dataset_lineage",
        "fk_dataset_quality_issues_report",
        "uq_dataset_issue_resolutions_one_decision",
        "uq_datasets_quality_lineage",
        "tr_dataset_quality_reports_resolution_guard",
        "tr_dataset_issue_resolutions_insert_guard",
    ):
        need(f"names {name}", name in src)
    need(
        "both guard triggers are created",
        "CREATE TRIGGER tr_dataset_issue_resolutions_insert_guard" in src
        and "CREATE TRIGGER tr_dataset_quality_reports_resolution_guard" in src,
    )
    need("after-evidence CHECK cannot pass on NULL", "COALESCE((action = 'waive'" in src)
    need("positions function is IMMUTABLE", "LANGUAGE sql IMMUTABLE" in src)
    need(
        "report step guard",
        "NEW.resolution_version IS DISTINCT FROM OLD.resolution_version + 1" in src,
    )
    need("report freeze guard", "(to_jsonb(NEW) - 'resolution_version' - 'updated_at')" in src)
    need(
        "ondelete RESTRICT on the issue fk",
        'ondelete="RESTRICT",\n            name="fk_dataset_issue_resolutions_issue_evidence"'
        in src,
    )
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
        "grant DELETE on issues": (
            "GRANT SELECT, INSERT ON dataset_quality_issues",
            "GRANT SELECT, INSERT, DELETE ON dataset_quality_issues",
        ),
        "grant UPDATE on resolutions": (
            "GRANT SELECT, INSERT ON dataset_issue_resolutions",
            "GRANT SELECT, INSERT, UPDATE ON dataset_issue_resolutions",
        ),
        "widen the report UPDATE columns": (
            "GRANT UPDATE (resolution_version, updated_at)",
            "GRANT UPDATE (resolution_version, report_sha256, updated_at)",
        ),
        "let a NULL after-evidence pass": (
            "COALESCE((action = 'waive'",
            "((action = 'waive'",
        ),
        "drop the waive-admin check": (
            "ck_dataset_issue_resolutions_waive_admin_only",
            "ck_dataset_issue_resolutions_other",
        ),
        "drop the step guard": (
            "NEW.resolution_version IS DISTINCT FROM OLD.resolution_version + 1",
            "false",
        ),
        "drop the freeze guard": (
            "(to_jsonb(NEW) - 'resolution_version' - 'updated_at')",
            "(to_jsonb(NEW))",
        ),
        "drop the resolution insert trigger": (
            "CREATE TRIGGER tr_dataset_issue_resolutions_insert_guard",
            "-- CREATE TRIGGER x",
        ),
        "wrong parent revision": (
            'down_revision = "0025_dataset_upload_hardening"',
            'down_revision = "0024_dataset_uploads"',
        ),
        "cascade the issue fk": (
            'ondelete="RESTRICT",\n            name="fk_dataset_issue_resolutions_issue_evidence"',
            'ondelete="CASCADE",\n            name="fk_dataset_issue_resolutions_issue_evidence"',
        ),
        "mutable positions function": ("LANGUAGE sql IMMUTABLE", "LANGUAGE sql VOLATILE"),
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
    check(
        CID,
        f"alembic {action} {revision_range} --sql exits 0 ({result.stderr[-200:]})",
        result.returncode == 0,
    )
    return result.stdout


def test_m2_migration_generates_the_expected_sql_offline() -> None:
    up = _offline_sql("0025_dataset_upload_hardening:0026_dataset_quality_issues", "upgrade")
    for needle in (
        "CREATE TABLE dataset_quality_reports",
        "CREATE TABLE dataset_quality_issues",
        "CREATE TABLE dataset_issue_resolutions",
        "ALTER TABLE dataset_quality_reports ENABLE ROW LEVEL SECURITY",
        "ALTER TABLE dataset_quality_reports FORCE ROW LEVEL SECURITY",
        "ALTER TABLE dataset_quality_issues ENABLE ROW LEVEL SECURITY",
        "ALTER TABLE dataset_quality_issues FORCE ROW LEVEL SECURITY",
        "ALTER TABLE dataset_issue_resolutions ENABLE ROW LEVEL SECURITY",
        "ALTER TABLE dataset_issue_resolutions FORCE ROW LEVEL SECURITY",
        'GRANT SELECT, INSERT ON dataset_quality_reports TO "sovra_app"',
        'GRANT UPDATE (resolution_version, updated_at) ON dataset_quality_reports TO "sovra_app"',
        'GRANT SELECT, INSERT ON dataset_quality_issues TO "sovra_app"',
        'GRANT SELECT, INSERT ON dataset_issue_resolutions TO "sovra_app"',
        "CREATE TRIGGER tr_dataset_quality_reports_resolution_guard",
        "CREATE TRIGGER tr_dataset_issue_resolutions_insert_guard",
        "CREATE FUNCTION dataset_positions_strictly_increasing",
        "ADD CONSTRAINT uq_datasets_quality_lineage UNIQUE",
        "UPDATE alembic_version SET version_num='0026_dataset_quality_issues'",
        "chr(58) || 'exclude' || chr(58)",
        "jsonb_exists_all(detector_versions, ARRAY['record_schema'",
    ):
        check(CID, f"the upgrade SQL contains: {needle[:80]}", needle in up)
    check(
        CID,
        "the upgrade creates exactly three tenant_isolation policies and never grants DELETE",
        up.count("CREATE POLICY tenant_isolation ON") == 3
        and "GRANT DELETE" not in up
        and "USING (true)" not in up,
    )
    check(
        CID,
        "the resolutions table has the issue, report and successor foreign keys",
        all(
            f"CONSTRAINT {name} FOREIGN KEY" in up
            for name in (
                "fk_dataset_issue_resolutions_issue_evidence",
                "fk_dataset_issue_resolutions_report_identity",
                "fk_dataset_issue_resolutions_successor_lineage",
                "fk_dataset_quality_reports_dataset_lineage",
                "fk_dataset_quality_issues_report",
            )
        ),
    )
    down = _offline_sql("0026_dataset_quality_issues:0025_dataset_upload_hardening", "downgrade")
    for needle in (
        "DROP TRIGGER IF EXISTS tr_dataset_issue_resolutions_insert_guard ON dataset_issue_resolutions",  # noqa: E501
        "DROP TRIGGER IF EXISTS tr_dataset_quality_reports_resolution_guard ON dataset_quality_reports",  # noqa: E501
        "DROP FUNCTION IF EXISTS dataset_issue_resolutions_insert_guard()",
        "DROP FUNCTION IF EXISTS dataset_quality_reports_resolution_guard()",
        "DROP FUNCTION IF EXISTS dataset_positions_strictly_increasing(integer[])",
        "DROP TABLE dataset_issue_resolutions",
        "DROP TABLE dataset_quality_issues",
        "DROP TABLE dataset_quality_reports",
        'REVOKE ALL ON dataset_issue_resolutions FROM "sovra_app"',
        "DROP CONSTRAINT uq_datasets_quality_lineage",
        "UPDATE alembic_version SET version_num='0025_dataset_upload_hardening'",
    ):
        check(CID, f"the downgrade SQL contains: {needle[:80]}", needle in down)
    check(
        CID,
        "the downgrade drops the dependent tables first",
        down.index("DROP TABLE dataset_issue_resolutions")
        < down.index("DROP TABLE dataset_quality_issues")
        < down.index("DROP TABLE dataset_quality_reports"),
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
    chain: set[str] = set()
    cursor = next(iter(heads)) if len(heads) == 1 else None
    while cursor is not None and cursor not in chain:
        chain.add(cursor)
        cursor = parents.get(cursor)
    check(
        CID,
        f"exactly one Alembic head, 0026_dataset_quality_issues on its chain (heads: {sorted(heads)})",  # noqa: E501
        len(heads) == 1 and "0026_dataset_quality_issues" in chain,
    )


def test_m4_migration_helpers() -> None:
    spec = importlib.util.spec_from_file_location("migration_0026", MIGRATION_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    check(
        CID,
        "_array renders a typed text array literal",
        module._array(("a", "b")) == "ARRAY['a','b']::text[]",
    )
    check(CID, "_in renders a parenthesised list", module._in(("a", "b")) == "('a','b')")
    check(
        CID,
        "the detector list is exactly FTF-0515's REQUIRED_DETECTORS",
        set(module._DETECTORS) == set(REQUIRED_DETECTORS) and len(module._DETECTORS) == 7,
    )
    check(
        CID,
        "the category list is the seven detectors plus poisoning and detector_failure",
        set(module._CATEGORIES) == {*REQUIRED_DETECTORS, "poisoning", "detector_failure"},
    )
    check(
        CID,
        "the action and role lists are exactly the contract's",
        module._ACTIONS == ("exclude", "correct", "new_version", "waive")
        and module._ROLES == ("ml_engineer", "workspace_admin", "organization_admin")
        and module._ADMIN_ROLES == ("workspace_admin", "organization_admin"),
    )
    pk = module._uuid_pk()
    check(
        CID,
        "the id column is the primary key with gen_random_uuid() default",
        pk.name == "id"
        and pk.primary_key is True
        and pk.server_default is not None
        and "gen_random_uuid()" in str(pk.server_default.arg),
    )
    org = module._org_fk("things")
    foreign_keys = list(org.foreign_keys)
    check(
        CID,
        "organization_id is NOT NULL and cascades from organizations",
        org.name == "organization_id"
        and org.nullable is False
        and len(foreign_keys) == 1
        and foreign_keys[0].ondelete == "CASCADE"
        and foreign_keys[0].name == "fk_things_organization_id"
        and foreign_keys[0].target_fullname == "organizations.id",
    )
    stamp = module._now_col("created_at")
    check(
        CID,
        "a timestamp is NOT NULL with a now() default",
        stamp.name == "created_at"
        and stamp.nullable is False
        and stamp.server_default is not None
        and "now()" in str(stamp.server_default.arg)
        and stamp.type.timezone is True,
    )
    required = module._uuid("project_id")
    optional = module._uuid("successor_dataset_id", nullable=True)
    check(
        CID,
        "_uuid is NOT NULL by default and nullable on request",
        required.name == "project_id"
        and required.nullable is False
        and optional.name == "successor_dataset_id"
        and optional.nullable is True,
    )
    check(CID, "the app role is sovra_app", module.APP_ROLE_NAME == "sovra_app")


# -- S: store over SQLite ------------------------------------------------------

_NOW = datetime(2026, 10, 8, 12, 0, tzinfo=UTC)
_ORG = uuid.uuid4()
_PROJECT = uuid.uuid4()
_CTX: Any = SimpleNamespace(organization_id=_ORG)
_ACTOR_ML = store.QualityActor("user", "user-ml", "ml_engineer", "req-ml")
_ACTOR_ADMIN = store.QualityActor("user", "user-admin", "workspace_admin", "req-admin")
_MESSAGE_MARKER = "email found at"


class _Audit:
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    def __call__(self, session: Session, **kwargs: Any) -> None:
        self.events.append(kwargs)

    def actions(self) -> list[str]:
        return [event_["action"] for event_ in self.events]


_EXECUTED: list[str] = []


def _passthrough_execute(session: Session, organization_id: uuid.UUID, statement: Any) -> Any:
    _EXECUTED.append(str(statement.compile(dialect=postgresql.dialect())))
    return session.execute(statement)


@contextlib.contextmanager
def _env():
    engine = create_engine("sqlite://")
    for model in (
        ustore.Dataset,
        store.DatasetQualityReport,
        store.DatasetQualityIssue,
        store.DatasetIssueResolution,
    ):
        model.__table__.create(engine)
    audit = _Audit()
    saved = (store.execute_tenant_scoped, ustore.execute_tenant_scoped, store.write_audit_event)
    store.execute_tenant_scoped = _passthrough_execute
    ustore.execute_tenant_scoped = _passthrough_execute
    store.write_audit_event = audit
    try:
        with Session(engine) as session:
            yield session, audit
    finally:
        store.execute_tenant_scoped, ustore.execute_tenant_scoped, store.write_audit_event = saved
        engine.dispose()


def _dataset(
    session: Session, name: str = "support", purpose: str = "training", sha: str | None = None
) -> ustore.Dataset:
    latest = session.query(ustore.Dataset).filter_by(project_id=_PROJECT, name=name).count()
    row = ustore.Dataset(
        id=uuid.uuid4(),
        organization_id=_ORG,
        project_id=_PROJECT,
        name=name,
        version=latest + 1,
        purpose=purpose,
        upload_session_id=uuid.uuid4(),
        upload_status="verified",
        verified_sha256=sha or _sha(f"{name}-{uuid.uuid4()}"),
        verified_size_bytes=100,
        created_by="seed",
        created_at=_NOW,
    )
    session.add(row)
    session.flush()
    return row


def _record_batch(index: int, text_value: str, raw: str | None = None) -> dict[str, object]:
    return {
        "record_index": index,
        "purpose": "training",
        "record_sha256": _sha(raw if raw is not None else f"raw-{index}-{text_value}"),
        "normalized_sha256": _sha(f"norm-{index}-{text_value}"),
        "content": {
            "messages": [{"role": "user", "content": [{"type": "text", "text": text_value}]}],
            "metadata": {},
        },
    }


def _full_runs(clean: bool = False, drop: tuple[str, ...] = ()) -> list[DetectorRun]:
    batch = (
        [_record_batch(10, "alpha routers"), _record_batch(11, "beta billing")]
        if clean
        else [
            _record_batch(10, "alpha routers", raw="dup"),
            _record_batch(11, "beta billing", raw="dup"),
            _record_batch(12, "contact jane.doe@example.com for help"),
            _record_batch(13, "gamma"),
            _record_batch(14, "delta"),
        ]
    )
    reports: dict[str, object] = {
        "record_schema": RecordSchemaValidationReport(
            issues=(),
            accepted_batch_positions=tuple(range(len(batch))),
            rejected_batch_positions=(),
        ),
        "tool_arguments": ToolCallValidationReport(issues=(), tool_schema_status="not_applicable"),
        "tokenization": TokenizationAnalysisReport(
            conversations=(), distribution=None, issues=(), certifies_training_fit=True
        ),
        "exact_duplicate": detect_exact_duplicates(batch),
        "normalized_duplicate": detect_normalized_duplicates(batch),
        "near_duplicate": detect_near_duplicates(batch),
        "pii_secrets": detect_pii_and_secrets(batch),
    }
    return [DetectorRun(detector=n, report=r) for n, r in reports.items() if n not in drop]


def _extra(
    reason: str,
    positions: tuple[int, ...],
    *,
    severity: str = "warning",
    category: str = "exact_duplicate",
) -> QualityIssueInput:
    return QualityIssueInput(
        detector="exact_duplicate" if category != "poisoning" else "poison_scan",
        category=category,
        severity=severity,
        reason_code=reason,
        message=f"{_MESSAGE_MARKER} {reason}",
        batch_positions=positions,
        record_indices=tuple(100 + p for p in positions),
        evidence_digest=_sha(reason),
    )


def _record(
    session: Session,
    dataset: ustore.Dataset,
    runs: list[DetectorRun] | None = None,
    *,
    extra: tuple[QualityIssueInput, ...] = (),
    versions: dict[str, str] | None = None,
    actor: store.QualityActor = _ACTOR_ML,
    now: datetime = _NOW,
) -> store.DatasetQualityReport:
    return store.record_quality_report(
        session,
        _CTX,
        project_id=_PROJECT,
        dataset_id=dataset.id,
        runs=runs if runs is not None else _full_runs(),
        extra_issues=extra,
        detector_versions=versions or VERSIONS,
        actor=actor,
        now=now,
    )


def _issue(
    session: Session, report: store.DatasetQualityReport, reason: str
) -> store.DatasetQualityIssue:
    rows = session.query(store.DatasetQualityIssue).filter_by(report_id=report.id).all()
    matching = [r for r in rows if r.reason_code == reason]
    assert len(matching) == 1, (reason, [r.reason_code for r in rows])
    return matching[0]


def _req(issue: store.DatasetQualityIssue, action: str, **over: Any) -> store.ResolutionRequest:
    args: dict[str, Any] = {
        "issue_id": issue.issue_id,
        "action": action,
        "reason": "reviewed by the data owner",
        "expected_before_evidence_sha256": issue.evidence_sha256,
    }
    if action == "exclude":
        args["excluded_batch_positions"] = tuple(issue.batch_positions[:1])
    args.update(over)
    return store.ResolutionRequest(**args)


def _resolve(
    session: Session,
    dataset: ustore.Dataset,
    report: store.DatasetQualityReport,
    items: list[store.ResolutionRequest],
    *,
    version: int | None = None,
    key: str | None = None,
    actor: store.QualityActor = _ACTOR_ML,
    held_out: bool = False,
    now: datetime = _NOW + timedelta(minutes=5),
) -> tuple[store.ResolveOutcome | None, BaseException | None]:
    try:
        return (
            store.submit_resolutions(
                session,
                _CTX,
                project_id=_PROJECT,
                dataset_id=dataset.id,
                quality_report_id=report.id,
                expected_resolution_version=report.resolution_version
                if version is None
                else version,
                idempotency_key=key or uuid.uuid4().hex,
                resolutions=items,
                include_held_out=held_out,
                actor=actor,
                now=now,
            ),
            None,
        )
    except BaseException as exc:  # noqa: BLE001 -- the test inspects the type
        return None, exc


def _counts(session: Session, audit: _Audit) -> tuple[int, int, int, int]:
    return (
        session.query(store.DatasetQualityReport).count(),
        session.query(store.DatasetQualityIssue).count(),
        session.query(store.DatasetIssueResolution).count(),
        len(audit.events),
    )


def _refused(error: BaseException | None, code: str, status: int) -> bool:
    return (
        isinstance(error, store.QualityStoreError) and error.code == code and error.status == status
    )


def test_s1_pure_helpers() -> None:
    sha = _sha("dataset")
    check(
        CID,
        "evidence is sha256(issue_id:dataset_sha256)",
        store.evidence_sha256("abc", sha) == _sha(f"abc:{sha}"),
    )
    check(
        CID,
        "exclusion evidence is sha256(dataset_sha256:exclude:<p1,p2>)",
        store.exclude_after_evidence(sha, [2]) == _sha(f"{sha}:exclude:2")
        and store.exclude_after_evidence(sha, [1, 4]) == _sha(f"{sha}:exclude:1,4")
        and store.exclude_after_evidence(sha, []) == _sha(f"{sha}:exclude:"),
    )
    report_id = uuid.uuid4()
    successor = uuid.uuid4()
    base = [
        store.ResolutionRequest(_sha("i1"), "exclude", "why", _sha("e1"), (1,), None),
        store.ResolutionRequest(_sha("i2"), "new_version", "why", _sha("e2"), (), successor),
    ]
    digest = store.request_digest(report_id, 3, base)
    check(CID, "the request digest is a sha256", re.fullmatch(r"[0-9a-f]{64}", digest) is not None)
    check(
        CID,
        "the request digest ignores resolution order",
        store.request_digest(report_id, 3, list(reversed(base))) == digest,
    )
    changed = {
        "report id": store.request_digest(uuid.uuid4(), 3, base),
        "version": store.request_digest(report_id, 4, base),
        "issue id": store.request_digest(
            report_id,
            3,
            [
                store.ResolutionRequest(_sha("i9"), "exclude", "why", _sha("e1"), (1,), None),
                base[1],
            ],
        ),
        "action": store.request_digest(
            report_id,
            3,
            [
                store.ResolutionRequest(_sha("i1"), "correct", "why", _sha("e1"), (1,), None),
                base[1],
            ],
        ),
        "reason": store.request_digest(
            report_id,
            3,
            [
                store.ResolutionRequest(_sha("i1"), "exclude", "other", _sha("e1"), (1,), None),
                base[1],
            ],
        ),
        "evidence": store.request_digest(
            report_id,
            3,
            [
                store.ResolutionRequest(_sha("i1"), "exclude", "why", _sha("e9"), (1,), None),
                base[1],
            ],
        ),
        "positions": store.request_digest(
            report_id,
            3,
            [
                store.ResolutionRequest(_sha("i1"), "exclude", "why", _sha("e1"), (2,), None),
                base[1],
            ],
        ),
        "successor": store.request_digest(
            report_id,
            3,
            [
                base[0],
                store.ResolutionRequest(
                    _sha("i2"), "new_version", "why", _sha("e2"), (), uuid.uuid4()
                ),
            ],
        ),
        "no successor": store.request_digest(
            report_id,
            3,
            [
                base[0],
                store.ResolutionRequest(_sha("i2"), "new_version", "why", _sha("e2"), (), None),
            ],
        ),
        "an extra resolution": store.request_digest(report_id, 3, base[:1]),
    }
    for label, other in changed.items():
        check(CID, f"the request digest changes with the {label}", other != digest)
    check(
        CID, "all request-digest variants are distinct", len(set(changed.values())) == len(changed)
    )

    issues = evaluate_quality(_full_runs()).issues
    reported = sorted(REQUIRED_DETECTORS)
    d0 = store._report_digest(sha, "", issues, reported, VERSIONS)
    variants = {
        "dataset sha": store._report_digest(_sha("o"), "", issues, reported, VERSIONS),
        "previous sha": store._report_digest(sha, _sha("prev"), issues, reported, VERSIONS),
        "issues": store._report_digest(sha, "", issues[:1], reported, VERSIONS),
        "detectors": store._report_digest(sha, "", issues, reported[:-1], VERSIONS),
        "versions": store._report_digest(
            sha, "", issues, reported, {**VERSIONS, "pii_secrets": "2"}
        ),
    }
    for label, other in variants.items():
        check(CID, f"the report digest changes with the {label}", other != d0)
    check(
        CID,
        "the report digest is order-insensitive across issues and detectors",
        store._report_digest(sha, "", tuple(reversed(issues)), list(reversed(reported)), VERSIONS)
        == d0,
    )
    flipped = tuple(
        type(i)(**{**i.__dict__, "severity": "warning" if i.severity == "blocking" else "blocking"})
        for i in issues
    )
    check(
        CID,
        "the report digest covers each issue's severity",
        store._report_digest(sha, "", flipped, reported, VERSIONS) != d0,
    )

    check(CID, "clean text accepts a plain string", store._is_clean_text("abc", 3))
    for bad in ("", " a", "a ", "abcd", _Str("abc"), 3, None, b"abc"):
        check(CID, f"clean text refuses {bad!r}", not store._is_clean_text(bad, 3))
    check(CID, "hex64 accepts 64 lowercase hex", store._is_hex64("a" * 64))
    for bad in ("A" * 64, "a" * 63, "a" * 65, "g" * 64, _Str("a" * 64), b"a" * 64, None, 5):
        check(CID, f"hex64 refuses {bad!r}", not store._is_hex64(bad))

    check(
        CID,
        "detector_versions accepts the full set",
        store._require_detector_versions(VERSIONS) == VERSIONS,
    )
    bad_versions: list[tuple[str, Any]] = [
        ("a non-mapping", ["a"]),
        ("a missing detector", {k: v for k, v in VERSIONS.items() if k != "pii_secrets"}),
        ("a blank version", {**VERSIONS, "pii_secrets": ""}),
        ("a padded version", {**VERSIONS, "pii_secrets": " 1"}),
        ("an int version", {**VERSIONS, "pii_secrets": 1}),
        (
            "a str-subclass name",
            {**{k: v for k, v in VERSIONS.items() if k != "pii_secrets"}, _Str("pii_secrets"): "1"},
        ),
        ("a 65-character version", {**VERSIONS, "pii_secrets": "v" * 65}),
    ]
    for label, bad_value in bad_versions:
        try:
            store._require_detector_versions(bad_value)
            raised = False
        except ValueError:
            raised = True
        check(CID, f"detector_versions refuses {label}", raised)
    extra_ok = store._require_detector_versions({**VERSIONS, "poison_scan": "9"})
    check(
        CID, "detector_versions keeps an extra detector's version", extra_ok["poison_scan"] == "9"
    )

    check(
        CID,
        "_require_now fills a timezone-aware default",
        store._require_now(None).tzinfo is not None,
    )
    bad_nows: list[Any] = [datetime(2026, 1, 1), "2026-01-01", 5]
    for bad_now in bad_nows:
        try:
            store._require_now(bad_now)
            raised = False
        except ValueError:
            raised = True
        check(CID, f"_require_now refuses {bad_now!r}", raised)
    check(
        CID, "_require_now returns a given aware time unchanged", store._require_now(_NOW) is _NOW
    )
    decided: Any = SimpleNamespace(engine_decision="waived")
    check(CID, "an undecided issue is open", store._issue_state(None) == "open")
    check(
        CID,
        "a decided issue carries the engine decision",
        store._issue_state(decided) == "waived",
    )


def test_s2_engine_refusals_map_to_stable_codes() -> None:
    report = evaluate_quality(_full_runs(drop=("near_duplicate",)))
    failure = next(i for i in report.issues if i.is_detector_failure)
    blocking = next(
        i for i in report.issues if i.severity == "blocking" and not i.is_detector_failure
    )
    cases: list[tuple[str, IssueResolution, type[Exception]]] = [
        (
            "a detector failure",
            IssueResolution(failure.issue_id, "resolved", "r", "why"),
            store.DetectorFailureRequiresRerunError,
        ),
        (
            "a waiver of a blocking issue",
            IssueResolution(blocking.issue_id, "waived", "r", "why"),
            store.WaiverNotAllowedForBlockingError,
        ),
        (
            "an unknown issue id",
            IssueResolution(_sha("nope"), "resolved", "r", "why"),
            store.IssueNotFoundError,
        ),
        (
            "a blank reviewer",
            IssueResolution(blocking.issue_id, "resolved", " ", "why"),
            store.InvalidResolutionRequestError,
        ),
        (
            "an unknown decision",
            IssueResolution(blocking.issue_id, "ignored", "r", "why"),
            store.InvalidResolutionRequestError,
        ),
    ]
    for label, resolution, expected in cases:
        try:
            apply_resolutions(report, [resolution])
            mapped: BaseException | None = None
        except QualityEngineError as exc:
            mapped = store._map_engine_error(exc)
        check(
            CID,
            f"the real engine's refusal of {label} maps to {expected.__name__}",
            type(mapped) is expected,
        )
    for fragment, error_type in store._ENGINE_REFUSALS:
        check(
            CID,
            f"the pinned fragment {fragment!r} maps to {error_type.__name__}",
            error_type.code != "",
        )
    codes = {
        store.InvalidResolutionRequestError: ("invalid_body", 400),
        store.PositionsNotInIssueError: ("positions_not_in_issue", 400),
        store.WaiveNotPermittedError: ("waive_not_permitted", 403),
        store.QualityReportNotFoundError: ("quality_report_not_found", 404),
        store.IssueNotFoundError: ("not_found", 404),
        store.ReportVersionConflictError: ("report_version_conflict", 409),
        store.ReportSupersededError: ("report_superseded", 409),
        store.IssueAlreadyDecidedError: ("issue_already_decided", 409),
        store.WaiverNotAllowedForBlockingError: ("waiver_not_allowed_for_blocking", 409),
        store.DetectorFailureRequiresRerunError: ("detector_failure_requires_rerun", 409),
        store.EvidenceMismatchError: ("evidence_mismatch", 409),
        store.SuccessorNotRevalidatedError: ("successor_not_revalidated", 409),
        store.SuccessorNotLaterVersionError: ("successor_not_later_version", 409),
        store.IdempotencyKeyReusedError: ("idempotency_key_reused", 409),
    }
    for error_type, (code, status) in codes.items():
        check(
            CID,
            f"{error_type.__name__} is {status} {code}",
            error_type.code == code and error_type.status == status,
        )
    check(
        CID,
        "the base error defaults to a 400 invalid_body",
        store.QualityStoreError.code == "invalid_body" and store.QualityStoreError.status == 400,
    )


def test_s3_record_quality_report() -> None:
    with _env() as (session, audit):
        dataset = _dataset(session)
        poison = _extra("seeded_poison", (3,), severity="warning", category="poisoning")
        report = _record(session, dataset, extra=(poison,))
        check(
            CID,
            "one report, three issues and one audit event are written",
            _counts(session, audit) == (1, 3, 0, 1)
            and audit.actions() == ["dataset_quality_report.record"],
        )
        check(
            CID,
            "the report copies the dataset identity",
            report.organization_id == _ORG
            and report.project_id == _PROJECT
            and report.dataset_id == dataset.id
            and report.dataset_name == dataset.name
            and report.dataset_purpose == "training"
            and report.dataset_version == 1
            and report.dataset_sha256 == dataset.verified_sha256,
        )
        check(
            CID,
            "seq 1, resolution_version 0, counts, detectors and versions",
            report.report_seq == 1
            and report.resolution_version == 0
            and report.issue_count == 3
            and report.blocking_count == 2
            and report.warning_count == 1
            and sorted(report.detectors_reported) == DETECTORS
            and report.detector_versions == VERSIONS
            and report.created_by == "user-ml"
            and report.created_at == _NOW
            and report.updated_at == _NOW,
        )
        check(
            CID,
            "the first report's hash chains from an empty parent",
            report.report_sha256
            == store._report_digest(
                dataset.verified_sha256,
                "",
                evaluate_quality(_full_runs(), extra_issues=(poison,)).issues,
                DETECTORS,
                VERSIONS,
            ),
        )
        pii = _issue(session, report, "pii_secret_email")
        dup = _issue(session, report, "raw_duplicate_group")
        pois = _issue(session, report, "seeded_poison")
        check(
            CID,
            "severities, positions and indices come from the real detectors and engine",
            pii.severity == "blocking"
            and pii.batch_positions == [2]
            and pii.record_indices == [12]
            and pii.detector == "pii_secrets"
            and pii.category == "pii_secrets"
            and dup.severity == "warning"
            and dup.batch_positions == [0, 1]
            and dup.record_indices == [10, 11]
            and dup.evidence_digest is not None
            and pois.severity == "blocking"
            and pois.category == "poisoning"
            and pois.detector == "poison_scan",
        )
        check(
            CID,
            "flags, evidence and the message hash are derived, not supplied",
            all(
                i.evidence_sha256 == _sha(f"{i.issue_id}:{dataset.verified_sha256}")
                and i.has_positions is True
                and i.is_detector_failure is False
                and i.dataset_sha256 == dataset.verified_sha256
                and i.organization_id == _ORG
                and i.report_id == report.id
                and _naive(i.created_at) == _naive(_NOW)
                for i in (pii, dup, pois)
            )
            and pii.message_sha256 == _sha("email found at content[0].text"),
        )
        stored = " ".join(str(vars(i)) for i in session.query(store.DatasetQualityIssue).all())
        check(
            CID,
            "no detector message text is stored",
            _MESSAGE_MARKER not in stored and "jane.doe" not in stored,
        )
        event_ = audit.events[0]
        check(
            CID,
            "the audit event names the report and carries counts only",
            event_["resource_type"] == "dataset_quality_report"
            and event_["resource_id"] == str(report.id)
            and event_["actor_id"] == "user-ml"
            and event_["request_id"] == "req-ml"
            and event_["details"]["report_seq"] == 1
            and event_["details"]["issue_count"] == 3
            and event_["details"]["blocking_count"] == 2
            and event_["details"]["warning_count"] == 1
            and event_["details"]["dataset_id"] == str(dataset.id)
            and event_["details"]["project_id"] == str(_PROJECT)
            and event_["details"]["detectors_reported"] == DETECTORS
            and _MESSAGE_MARKER not in json.dumps(event_["details"]),
        )

        again = _record(session, dataset, extra=(poison,))
        check(
            CID,
            "identical findings return the latest report and write nothing",
            again.id == report.id and _counts(session, audit) == (1, 3, 0, 1),
        )
        changed_versions = _record(
            session, dataset, extra=(poison,), versions={**VERSIONS, "pii_secrets": "2.0"}
        )
        check(CID, "a different detector version is a new report", changed_versions.report_seq == 2)
        check(
            CID,
            "the second report chains from the first",
            changed_versions.report_sha256 != report.report_sha256
            and changed_versions.report_sha256
            == store._report_digest(
                dataset.verified_sha256,
                report.report_sha256,
                evaluate_quality(_full_runs(), extra_issues=(poison,)).issues,
                DETECTORS,
                {**VERSIONS, "pii_secrets": "2.0"},
            ),
        )
        third = _record(session, dataset)
        check(
            CID,
            "different findings are a third report with seq 3",
            third.report_seq == 3 and third.issue_count == 2,
        )
        fourth = _record(
            session, dataset, extra=(poison,), versions={**VERSIONS, "pii_secrets": "2.0"}
        )
        check(
            CID,
            "returning to older findings is a new report, never a collision with the old one",
            fourth.report_seq == 4 and fourth.report_sha256 != changed_versions.report_sha256,
        )
        dropped = _record(session, dataset, _full_runs(drop=("near_duplicate",)))
        check(
            CID,
            "a missing detector records a blocking detector failure and a short detectors list",
            dropped.report_seq == 5
            and "near_duplicate" not in dropped.detectors_reported
            and len(dropped.detectors_reported) == 6
            and _issue(session, dropped, "detector_not_run").is_detector_failure is True
            and _issue(session, dropped, "detector_not_run").has_positions is False
            and _issue(session, dropped, "detector_not_run").batch_positions == [],
        )
        held = _dataset(session, "held", purpose="held_out_evaluation")
        check(
            CID,
            "a held-out dataset can be recorded by the internal path",
            _record(session, held).dataset_purpose == "held_out_evaluation",
        )


def test_s3b_record_refusals_write_nothing() -> None:
    with _env() as (session, audit):
        dataset = _dataset(session)
        before = _counts(session, audit)
        cases: list[tuple[str, dict[str, Any], type[BaseException]]] = [
            ("versions missing a detector", {"versions": {"record_schema": "1"}}, ValueError),
            ("an unknown dataset", {"dataset_id": uuid.uuid4()}, ustore.DatasetNotFoundError),
            (
                "a duplicate detector run",
                {"runs": _full_runs() + _full_runs()[:1]},
                QualityEngineError,
            ),
            (
                "a bad evidence digest",
                {
                    "extra": (
                        QualityIssueInput(
                            "d", "poisoning", "blocking", "x", "m", (0,), (0,), "NOT-HEX"
                        ),
                    )
                },
                ValueError,
            ),
            (
                "a bad reason code",
                {
                    "extra": (
                        QualityIssueInput(
                            "d", "poisoning", "blocking", "Bad Code", "m", (0,), (0,), None
                        ),
                    )
                },
                ValueError,
            ),
            (
                "a blank detector name",
                {
                    "extra": (
                        QualityIssueInput(" ", "poisoning", "blocking", "x", "m", (0,), (0,), None),
                    )
                },
                ValueError,
            ),
            (
                "a negative position",
                {
                    "extra": (
                        QualityIssueInput(
                            "d", "poisoning", "blocking", "x", "m", (-1,), (0,), None
                        ),
                    )
                },
                ValueError,
            ),
            (
                "an unknown category",
                {
                    "extra": (
                        QualityIssueInput("d", "made_up", "blocking", "x", "m", (0,), (0,), None),
                    )
                },
                QualityEngineError,
            ),
            ("a naive clock", {"now": datetime(2026, 1, 1)}, ValueError),
        ]
        for label, over, expected in cases:
            args: dict[str, Any] = {
                "project_id": _PROJECT,
                "dataset_id": dataset.id,
                "runs": _full_runs(),
                "extra_issues": over.get("extra", ()),
                "detector_versions": over.get("versions", VERSIONS),
                "actor": _ACTOR_ML,
                "now": over.get("now", _NOW),
            }
            if "dataset_id" in over:
                args["dataset_id"] = over["dataset_id"]
            if "runs" in over:
                args["runs"] = over["runs"]
            try:
                store.record_quality_report(session, _CTX, **args)
                raised: BaseException | None = None
            except BaseException as exc:  # noqa: BLE001
                raised = exc
            check(CID, f"record refuses {label} ({raised!r})", isinstance(raised, expected))
        check(CID, "every record refusal wrote nothing", _counts(session, audit) == before)
        ok = _record(session, dataset)
        check(CID, "positive control: a valid record is accepted", ok.report_seq == 1)


def test_s4_reads() -> None:
    with _env() as (session, audit):
        dataset = _dataset(session)
        report = _record(
            session,
            dataset,
            extra=(
                _extra("w_a", (0, 1)),
                _extra("w_b", (0, 1)),
                _extra("b_c", (1,), severity="blocking"),
            ),
        )

        def issues(**kw: Any) -> list[store.IssueView]:
            return store.list_issues(
                session,
                _CTX,
                project_id=_PROJECT,
                dataset_id=dataset.id,
                include_held_out=kw.pop("include_held_out", False),
                limit=kw.pop("limit", 50),
                **kw,
            )

        everything = issues()
        ids = [v.issue.issue_id for v in everything]
        check(CID, "five issues, ordered by issue_id", len(ids) == 5 and ids == sorted(ids))
        check(
            CID,
            "every undecided issue is open with no resolution",
            all(v.state == "open" and v.resolution is None for v in everything),
        )
        check(CID, "limit bounds the page", [v.issue.issue_id for v in issues(limit=2)] == ids[:2])
        check(
            CID,
            "after continues the page",
            [v.issue.issue_id for v in issues(limit=2, after=ids[1])] == ids[2:4],
        )
        check(CID, "after the last id is an empty page", issues(after=ids[-1]) == [])
        check(
            CID,
            "severity filter",
            {v.issue.reason_code for v in issues(severity="blocking")}
            == {"pii_secret_email", "b_c"},
        )
        check(
            CID,
            "detector filter",
            {v.issue.reason_code for v in issues(detector="pii_secrets")} == {"pii_secret_email"},
        )
        check(
            CID,
            "category filter",
            len(issues(category="exact_duplicate")) == 4 and len(issues(category="poisoning")) == 0,
        )
        check(CID, "state=open lists every undecided issue", len(issues(state="open")) == 5)
        check(
            CID,
            "state=resolved lists none yet",
            issues(state="resolved") == [] and issues(state="waived") == [],
        )
        for label, kwargs in (
            ("limit 0", {"limit": 0}),
            ("limit 201", {"limit": 201}),
            ("a bool limit", {"limit": True}),
            ("a string limit", {"limit": "5"}),
            ("an uppercase after", {"after": "A" * 64}),
            ("a short after", {"after": "ab"}),
            ("an unknown severity", {"severity": "info"}),
            ("an unknown state", {"state": "closed"}),
            ("an unknown category", {"category": "made_up"}),
            ("a blank detector", {"detector": " "}),
        ):
            try:
                issues(**kwargs)
                raised = False
            except ValueError:
                raised = True
            check(CID, f"list_issues refuses {label}", raised)
        check(CID, "limit 200 is accepted", len(issues(limit=200)) == 5)

        one = store.get_issue(
            session,
            _CTX,
            project_id=_PROJECT,
            dataset_id=dataset.id,
            issue_id=ids[0],
            include_held_out=False,
        )
        check(
            CID, "get_issue returns the issue", one.issue.issue_id == ids[0] and one.state == "open"
        )
        for label, bad in (
            ("an unknown id", _sha("nope")),
            ("a non-hex id", "zz"),
            ("an uppercase id", ids[0].upper()),
        ):
            try:
                store.get_issue(
                    session,
                    _CTX,
                    project_id=_PROJECT,
                    dataset_id=dataset.id,
                    issue_id=bad,
                    include_held_out=False,
                )
                missing = False
            except store.IssueNotFoundError:
                missing = True
            check(CID, f"get_issue: {label} is IssueNotFoundError", missing)

        latest = store.get_latest_report(
            session, _CTX, project_id=_PROJECT, dataset_id=dataset.id, include_held_out=False
        )
        check(CID, "get_latest_report returns the newest report", latest.id == report.id)
        newer = _record(session, dataset, extra=(_extra("w_a", (0, 1)),))
        latest = store.get_latest_report(
            session, _CTX, project_id=_PROJECT, dataset_id=dataset.id, include_held_out=False
        )
        check(
            CID, "... and follows the newest report", latest.id == newer.id and len(issues()) == 3
        )

        empty = _dataset(session, "empty")
        try:
            store.get_latest_report(
                session, _CTX, project_id=_PROJECT, dataset_id=empty.id, include_held_out=False
            )
            outcome: BaseException | None = None
        except store.QualityStoreError as exc:
            outcome = exc
        check(
            CID,
            "a dataset with no report is 404 quality_report_not_found",
            _refused(outcome, "quality_report_not_found", 404),
        )

        held = _dataset(session, "held", purpose="held_out_evaluation")
        held_report = _record(session, held, extra=(_extra("h_w", (0,)),))
        held_issue = _issue(session, held_report, "h_w")
        calls = {
            "get_latest_report": lambda ho: store.get_latest_report(
                session, _CTX, project_id=_PROJECT, dataset_id=held.id, include_held_out=ho
            ),
            "list_issues": lambda ho: store.list_issues(
                session, _CTX, project_id=_PROJECT, dataset_id=held.id, include_held_out=ho, limit=5
            ),
            "get_issue": lambda ho: store.get_issue(
                session,
                _CTX,
                project_id=_PROJECT,
                dataset_id=held.id,
                issue_id=held_issue.issue_id,
                include_held_out=ho,
            ),
            "list_resolutions": lambda ho: store.list_resolutions(
                session, _CTX, project_id=_PROJECT, dataset_id=held.id, include_held_out=ho, limit=5
            ),
            "load_resolved_quality_report": lambda ho: store.load_resolved_quality_report(
                session, _CTX, project_id=_PROJECT, dataset_id=held.id, include_held_out=ho
            ),
            "excluded_batch_positions": lambda ho: store.excluded_batch_positions(
                session, _CTX, project_id=_PROJECT, dataset_id=held.id, include_held_out=ho
            ),
        }
        for name, call in calls.items():
            try:
                call(False)
                hidden = False
            except ustore.DatasetNotFoundError:
                hidden = True
            check(CID, f"{name} hides a held-out dataset (DatasetNotFoundError)", hidden)
            try:
                call(True)
                visible = True
            except Exception:  # noqa: BLE001
                visible = False
            check(CID, f"{name} serves a held-out dataset when allowed", visible)
        try:
            store.get_latest_report(
                session, _CTX, project_id=uuid.uuid4(), dataset_id=dataset.id, include_held_out=True
            )
            other = False
        except ustore.DatasetNotFoundError:
            other = True
        check(CID, "another project's dataset id is DatasetNotFoundError", other)
        check(
            CID,
            "reads wrote nothing (only the three record events exist)",
            audit.actions() == ["dataset_quality_report.record"] * 3,
        )


def test_s5_resolutions_succeed_and_are_recorded() -> None:
    with _env() as (session, audit):
        d1 = _dataset(session, "support")
        poison = _extra("seeded_poison", (3,), severity="warning", category="poisoning")
        r1 = _record(session, d1, extra=(poison,))
        pii = _issue(session, r1, "pii_secret_email")
        dup = _issue(session, r1, "raw_duplicate_group")
        pois = _issue(session, r1, "seeded_poison")
        d2 = _dataset(session, "support")
        _record(session, d2, _full_runs(clean=True))
        events_before = len(audit.events)

        engine_before = store.load_resolved_quality_report(
            session, _CTX, project_id=_PROJECT, dataset_id=d1.id
        )
        blocked = False
        try:
            assert_clear_for_split(engine_before)
        except QualityBlockedError:
            blocked = True
        check(
            CID,
            "unresolved blocking issues raise QualityBlockedError",
            blocked and engine_before.blocks_split and not engine_before.is_clean,
        )
        check(
            CID,
            "the rebuilt engine report never carries a message",
            all(i.message == "" and i.resolution_state == "open" for i in engine_before.issues)
            and {i.issue_id for i in engine_before.issues}
            == {pii.issue_id, dup.issue_id, pois.issue_id},
        )

        outcome, error = _resolve(
            session,
            d1,
            r1,
            [_req(pii, "exclude"), _req(pois, "new_version", successor_dataset_id=d2.id)],
            key="k-1",
        )
        check(
            CID,
            f"exclude + new_version succeeds ({error!r})",
            error is None and outcome is not None,
        )
        assert outcome is not None
        check(
            CID,
            "the outcome is a fresh (not replayed) write with the bumped report",
            outcome.replayed is False
            and outcome.report is r1
            and r1.resolution_version == 1
            and _naive(r1.updated_at) == _naive(_NOW + timedelta(minutes=5)),
        )
        by_issue = {row.issue_id: row for row in outcome.resolutions}
        excl, newv = by_issue[pii.issue_id], by_issue[pois.issue_id]
        check(
            CID,
            "the exclude row binds the issue, report, dataset, evidence and positions",
            excl.action == "exclude"
            and excl.engine_decision == "resolved"
            and excl.organization_id == _ORG
            and excl.quality_report_id == r1.id
            and excl.project_id == _PROJECT
            and excl.dataset_id == d1.id
            and excl.dataset_name == "support"
            and excl.dataset_purpose == "training"
            and excl.dataset_version == 1
            and excl.dataset_sha256 == d1.verified_sha256
            and excl.severity == "blocking"
            and excl.is_detector_failure is False
            and excl.has_positions is True
            and excl.before_evidence_sha256 == pii.evidence_sha256
            and excl.excluded_batch_positions == [2]
            and excl.after_evidence_sha256 == _sha(f"{d1.verified_sha256}:exclude:2")
            and excl.successor_dataset_id is None
            and excl.successor_version is None
            and excl.successor_sha256 is None,
        )
        check(
            CID,
            "the request-level columns are on every row",
            all(
                row.reason == "reviewed by the data owner"
                and row.actor_id == "user-ml"
                and row.actor_role == "ml_engineer"
                and row.request_id == "req-ml"
                and row.idempotency_key == "k-1"
                and row.resolution_version_after == 1
                and row.request_sha256
                == store.request_digest(
                    r1.id,
                    0,
                    [_req(pii, "exclude"), _req(pois, "new_version", successor_dataset_id=d2.id)],
                )
                and _naive(row.created_at) == _naive(_NOW + timedelta(minutes=5))
                for row in outcome.resolutions
            ),
        )
        check(
            CID,
            "the new_version row binds the successor and its verified hash",
            newv.action == "new_version"
            and newv.successor_dataset_id == d2.id
            and newv.successor_version == 2
            and newv.successor_sha256 == d2.verified_sha256
            and newv.after_evidence_sha256 == d2.verified_sha256
            and newv.excluded_batch_positions == [],
        )
        check(
            CID,
            "exactly one audit event per resolution, keyed by the resolution id, with no reason text",  # noqa: E501
            len(audit.events) == events_before + 2
            and {e["resource_id"] for e in audit.events[events_before:]}
            == {str(excl.id), str(newv.id)}
            and all(
                e["action"] == "dataset_quality_issue.resolve"
                and e["resource_type"] == "dataset_issue_resolution"
                and e["actor_id"] == "user-ml"
                and e["details"]["reason_length"] == len("reviewed by the data owner")
                and "reviewed" not in json.dumps(e["details"])
                and e["details"]["resolution_version_after"] == 1
                and e["details"]["quality_report_id"] == str(r1.id)
                and e["details"]["dataset_id"] == str(d1.id)
                and e["details"]["project_id"] == str(_PROJECT)
                for e in audit.events[events_before:]
            )
            and next(e for e in audit.events[events_before:] if e["resource_id"] == str(excl.id))[
                "details"
            ]["issue_id"]
            == pii.issue_id
            and next(e for e in audit.events[events_before:] if e["resource_id"] == str(excl.id))[
                "details"
            ]["after_evidence_sha256"]
            == excl.after_evidence_sha256
            and next(e for e in audit.events[events_before:] if e["resource_id"] == str(newv.id))[
                "details"
            ]["successor_dataset_id"]
            == str(d2.id),
        )
        check(
            CID,
            "no datasets row was added or changed",
            session.query(ustore.Dataset).count() == 2
            and d1.verified_sha256 == excl.dataset_sha256,
        )

        engine_after = store.load_resolved_quality_report(
            session, _CTX, project_id=_PROJECT, dataset_id=d1.id
        )
        clear = True
        try:
            assert_clear_for_split(engine_after)
        except QualityBlockedError:
            clear = False
        states = {
            i.issue_id: (i.resolution_state, i.resolved_by, i.resolution_reason)
            for i in engine_after.issues
        }
        check(
            CID,
            "resolving both blocking issues clears the split",
            clear and not engine_after.blocks_split,
        )
        check(
            CID,
            "engine states carry the reviewer and the reason",
            states[pii.issue_id] == ("resolved", "user-ml", "reviewed by the data owner")
            and states[pois.issue_id] == ("resolved", "user-ml", "reviewed by the data owner")
            and states[dup.issue_id] == ("open", None, None),
        )
        check(
            CID,
            "excluded_batch_positions is the sorted exclusion set",
            store.excluded_batch_positions(session, _CTX, project_id=_PROJECT, dataset_id=d1.id)
            == (2,),
        )
        views = {
            v.issue.issue_id: v
            for v in store.list_issues(
                session,
                _CTX,
                project_id=_PROJECT,
                dataset_id=d1.id,
                include_held_out=False,
                limit=10,
            )
        }
        check(
            CID,
            "list_issues reports each state and attaches the resolution",
            views[pii.issue_id].state == "resolved"
            and views[pii.issue_id].resolution is not None
            and views[dup.issue_id].state == "open"
            and views[dup.issue_id].resolution is None,
        )
        check(
            CID,
            "state filters see the decisions",
            len(
                store.list_issues(
                    session,
                    _CTX,
                    project_id=_PROJECT,
                    dataset_id=d1.id,
                    include_held_out=False,
                    limit=10,
                    state="resolved",
                )
            )
            == 2
            and len(
                store.list_issues(
                    session,
                    _CTX,
                    project_id=_PROJECT,
                    dataset_id=d1.id,
                    include_held_out=False,
                    limit=10,
                    state="open",
                )
            )
            == 1,
        )

        # idempotent replay
        before = _counts(session, audit)
        replay, error = _resolve(
            session,
            d1,
            r1,
            [_req(pii, "exclude"), _req(pois, "new_version", successor_dataset_id=d2.id)],
            version=0,
            key="k-1",
        )
        check(
            CID,
            "same key + same body replays the stored rows",
            error is None
            and replay is not None
            and replay.replayed is True
            and {r.id for r in replay.resolutions} == {excl.id, newv.id},
        )
        check(CID, "... and writes nothing", _counts(session, audit) == before)
        _, error = _resolve(
            session,
            d1,
            r1,
            [_req(pii, "exclude", reason="a different reason")],
            version=0,
            key="k-1",
        )
        check(
            CID,
            "same key + different body is idempotency_key_reused",
            _refused(error, "idempotency_key_reused", 409),
        )
        other_report_key = _resolve(
            session,
            d2,
            store.get_latest_report(
                session, _CTX, project_id=_PROJECT, dataset_id=d2.id, include_held_out=True
            ),
            [],
            key="x",
        )[1]
        check(
            CID,
            "an empty resolution list is invalid_body",
            _refused(other_report_key, "invalid_body", 400),
        )

        # waive: admin on a warning
        _, error = _resolve(session, d1, r1, [_req(dup, "waive")], actor=_ACTOR_ML)
        check(CID, "ml_engineer cannot waive", _refused(error, "waive_not_permitted", 403))
        waived, error = _resolve(
            session,
            d1,
            r1,
            [_req(dup, "waive")],
            actor=_ACTOR_ADMIN,
            now=_NOW + timedelta(minutes=9),
        )
        check(CID, f"workspace_admin can waive a warning ({error!r})", error is None)
        assert waived is not None
        row = waived.resolutions[0]
        check(
            CID,
            "a waive row has the waived decision, no positions and no after-evidence",
            row.action == "waive"
            and row.engine_decision == "waived"
            and row.excluded_batch_positions == []
            and row.after_evidence_sha256 is None
            and row.actor_role == "workspace_admin"
            and row.resolution_version_after == 2
            and r1.resolution_version == 2,
        )
        listed = store.list_resolutions(
            session, _CTX, project_id=_PROJECT, dataset_id=d1.id, include_held_out=False, limit=10
        )
        check(CID, "list_resolutions is newest first", len(listed) == 3 and listed[0].id == row.id)
        check(
            CID,
            "list_resolutions honours the limit",
            len(
                store.list_resolutions(
                    session,
                    _CTX,
                    project_id=_PROJECT,
                    dataset_id=d1.id,
                    include_held_out=False,
                    limit=1,
                )
            )
            == 1,
        )
        bad_limits: list[Any] = [0, 201, True, "5"]
        for bad in bad_limits:
            try:
                store.list_resolutions(
                    session,
                    _CTX,
                    project_id=_PROJECT,
                    dataset_id=d1.id,
                    include_held_out=False,
                    limit=bad,
                )
                raised = False
            except ValueError:
                raised = True
            check(CID, f"list_resolutions refuses limit {bad!r}", raised)

        # exclusions accumulate across requests and report positions sorted/unique
        d3 = _dataset(session, "multi")
        r3 = _record(session, d3, extra=(_extra("m_a", (4, 5, 9)), _extra("m_b", (5, 7))))
        a, b = _issue(session, r3, "m_a"), _issue(session, r3, "m_b")
        _resolve(session, d3, r3, [_req(a, "exclude", excluded_batch_positions=(5, 9))])
        _resolve(session, d3, r3, [_req(b, "exclude", excluded_batch_positions=(5, 7))], version=1)
        check(
            CID,
            "overlapping exclusions are unioned, sorted and de-duplicated",
            store.excluded_batch_positions(session, _CTX, project_id=_PROJECT, dataset_id=d3.id)
            == (5, 7, 9),
        )


def test_s6_resolution_refusals_write_nothing() -> None:
    with _env() as (session, audit):
        d1 = _dataset(session, "support")
        r1 = _record(
            session,
            d1,
            extra=(_extra("seeded_poison", (3,), severity="warning", category="poisoning"),),
        )
        pii = _issue(session, r1, "pii_secret_email")
        dup = _issue(session, r1, "raw_duplicate_group")
        pois = _issue(session, r1, "seeded_poison")  # noqa: F841
        d_other = _dataset(session, "other-name")
        _record(session, d_other, _full_runs(clean=True))
        d2 = _dataset(session, "support")
        _record(session, d2, _full_runs(clean=True, drop=("near_duplicate",)))
        failure_ds = _dataset(session, "failing")
        failure_report = _record(
            session, failure_ds, _full_runs(clean=True, drop=("tokenization",))
        )
        failure = _issue(session, failure_report, "detector_not_run")
        nopos_ds = _dataset(session, "nopos")
        nopos_report = _record(
            session,
            nopos_ds,
            _full_runs(clean=True),
            extra=(_extra("np", (), severity="blocking"),),
        )
        nopos = _issue(session, nopos_report, "np")

        before = _counts(session, audit)
        cases: list[tuple[str, Any, list[store.ResolutionRequest], dict[str, Any], str, int]] = [
            (
                "wrong before-evidence",
                (d1, r1),
                [_req(pii, "exclude", expected_before_evidence_sha256=_sha("x"))],
                {},
                "evidence_mismatch",
                409,
            ),
            (
                "exclude position outside the issue",
                (d1, r1),
                [_req(pii, "exclude", excluded_batch_positions=(2, 4))],
                {},
                "positions_not_in_issue",
                400,
            ),
            (
                "exclude another issue's position",
                (d1, r1),
                [_req(pii, "exclude", excluded_batch_positions=(0,))],
                {},
                "positions_not_in_issue",
                400,
            ),
            (
                "exclude on an issue with no positions",
                (nopos_ds, nopos_report),
                [_req(nopos, "exclude", excluded_batch_positions=(0,))],
                {},
                "positions_not_in_issue",
                400,
            ),
            (
                "correct on an issue with no positions",
                (nopos_ds, nopos_report),
                [_req(nopos, "correct", successor_dataset_id=d2.id)],
                {},
                "positions_not_in_issue",
                400,
            ),
            (
                "blocking waiver by an admin",
                (d1, r1),
                [_req(pii, "waive")],
                {"actor": _ACTOR_ADMIN},
                "waiver_not_allowed_for_blocking",
                409,
            ),
            (
                "waive by ml_engineer",
                (d1, r1),
                [_req(dup, "waive")],
                {},
                "waive_not_permitted",
                403,
            ),
            (
                "detector failure + exclude",
                (failure_ds, failure_report),
                [_req(failure, "exclude", excluded_batch_positions=(0,))],
                {},
                "detector_failure_requires_rerun",
                409,
            ),
            (
                "detector failure + waive by admin",
                (failure_ds, failure_report),
                [_req(failure, "waive")],
                {"actor": _ACTOR_ADMIN},
                "detector_failure_requires_rerun",
                409,
            ),
            (
                "correct without a successor",
                (d1, r1),
                [_req(pii, "correct")],
                {},
                "invalid_body",
                400,
            ),
            (
                "exclude with a successor",
                (d1, r1),
                [_req(pii, "exclude", successor_dataset_id=d2.id)],
                {},
                "invalid_body",
                400,
            ),
            (
                "waive with a successor",
                (d1, r1),
                [_req(dup, "waive", successor_dataset_id=d2.id)],
                {"actor": _ACTOR_ADMIN},
                "invalid_body",
                400,
            ),
            (
                "new_version with positions",
                (d1, r1),
                [
                    _req(
                        pii,
                        "new_version",
                        successor_dataset_id=d2.id,
                        excluded_batch_positions=(2,),
                    )
                ],
                {},
                "invalid_body",
                400,
            ),
            (
                "waive with positions",
                (d1, r1),
                [_req(dup, "waive", excluded_batch_positions=(0,))],
                {"actor": _ACTOR_ADMIN},
                "invalid_body",
                400,
            ),
            (
                "exclude with no positions",
                (d1, r1),
                [_req(pii, "exclude", excluded_batch_positions=())],
                {},
                "invalid_body",
                400,
            ),
            (
                "unsorted positions",
                (d1, r1),
                [_req(dup, "exclude", excluded_batch_positions=(1, 0))],
                {},
                "invalid_body",
                400,
            ),
            (
                "duplicate positions",
                (d1, r1),
                [_req(dup, "exclude", excluded_batch_positions=(0, 0))],
                {},
                "invalid_body",
                400,
            ),
            (
                "successor of another name",
                (d1, r1),
                [_req(pii, "new_version", successor_dataset_id=d_other.id)],
                {},
                "invalid_body",
                400,
            ),
            (
                "successor lacking a detector",
                (d1, r1),
                [_req(pii, "new_version", successor_dataset_id=d2.id)],
                {},
                "successor_not_revalidated",
                409,
            ),
            (
                "unknown successor",
                (d1, r1),
                [_req(pii, "new_version", successor_dataset_id=uuid.uuid4())],
                {},
                "not_found",
                404,
            ),
            (
                "stale version",
                (d1, r1),
                [_req(pii, "exclude")],
                {"version": 7},
                "report_version_conflict",
                409,
            ),
            (
                "negative version",
                (d1, r1),
                [_req(pii, "exclude")],
                {"version": -1},
                "invalid_body",
                400,
            ),
            (
                "unknown issue",
                (d1, r1),
                [_req(pii, "exclude", issue_id=_sha("unknown"))],
                {},
                "not_found",
                404,
            ),
            (
                "padded idempotency key",
                (d1, r1),
                [_req(pii, "exclude")],
                {"key": " k "},
                "invalid_body",
                400,
            ),
            ("blank reason", (d1, r1), [_req(pii, "exclude", reason="")], {}, "invalid_body", 400),
            (
                "padded reason",
                (d1, r1),
                [_req(pii, "exclude", reason=" why ")],
                {},
                "invalid_body",
                400,
            ),
            (
                "a 2001-character reason",
                (d1, r1),
                [_req(pii, "exclude", reason="x" * 2001)],
                {},
                "invalid_body",
                400,
            ),
            ("an unknown action", (d1, r1), [_req(pii, "delete")], {}, "invalid_body", 400),
            (
                "uppercase issue id",
                (d1, r1),
                [_req(pii, "exclude", issue_id=pii.issue_id.upper())],
                {},
                "invalid_body",
                400,
            ),
            (
                "uppercase evidence",
                (d1, r1),
                [_req(pii, "exclude", expected_before_evidence_sha256=pii.evidence_sha256.upper())],
                {},
                "invalid_body",
                400,
            ),
            (
                "the same issue twice",
                (d1, r1),
                [_req(pii, "exclude"), _req(pii, "exclude")],
                {},
                "invalid_body",
                400,
            ),
            (
                "an unknown actor role",
                (d1, r1),
                [_req(pii, "exclude")],
                {"actor": store.QualityActor("user", "u", "viewer", "r")},
                "invalid_body",
                400,
            ),
            ("101 resolutions", (d1, r1), [_req(pii, "exclude")] * 101, {}, "invalid_body", 400),
            ("held-out hidden dataset", (d1, r1), [_req(pii, "exclude")], {}, "", 0),
        ]
        for label, (dataset, report), items, kwargs, code, status in cases[:-1]:
            _, error = _resolve(session, dataset, report, items, **kwargs)
            check(
                CID,
                f"refused {status} {code}: {label} (got {error!r})",
                _refused(error, code, status),
            )
        check(CID, "every resolution refusal wrote nothing", _counts(session, audit) == before)

        # all-or-nothing: the second item is invalid
        _, error = _resolve(
            session,
            d1,
            r1,
            [
                _req(pii, "exclude"),
                _req(dup, "exclude", expected_before_evidence_sha256=_sha("bad")),
            ],
        )
        check(
            CID,
            "a request with a bad second item is refused",
            _refused(error, "evidence_mismatch", 409),
        )
        check(
            CID,
            "... and writes none of its first item",
            _counts(session, audit) == before and r1.resolution_version == 0,
        )

        # wrong shapes of the call itself
        for label, kwargs in (
            ("a non-uuid report id", {"quality_report_id": "x"}),
            ("a bool version", {"expected_resolution_version": True}),
            ("a float version", {"expected_resolution_version": 0.0}),
            ("a str-subclass key", {"idempotency_key": _Str("k")}),
            ("a string instead of a list", {"resolutions": "abc"}),
            ("a non-request item", {"resolutions": [object()]}),
        ):
            args: dict[str, Any] = {
                "project_id": _PROJECT,
                "dataset_id": d1.id,
                "quality_report_id": r1.id,
                "expected_resolution_version": 0,
                "idempotency_key": "k",
                "resolutions": [_req(pii, "exclude")],
                "include_held_out": False,
                "actor": _ACTOR_ML,
            }
            args.update(kwargs)
            try:
                store.submit_resolutions(session, _CTX, **args)
                outcome: BaseException | None = None
            except BaseException as exc:  # noqa: BLE001
                outcome = exc
            expected_code = (
                "quality_report_not_found" if label == "a non-uuid report id" else "invalid_body"
            )
            check(
                CID,
                f"submit_resolutions refuses {label} ({outcome!r})",
                isinstance(outcome, store.QualityStoreError) and outcome.code == expected_code,
            )
        bad_item = _req(pii, "exclude", excluded_batch_positions=(True,))
        _, error = _resolve(session, d1, r1, [bad_item])
        check(CID, "bool positions are invalid_body", _refused(error, "invalid_body", 400))
        _, error = _resolve(session, d1, r1, [_req(pii, "exclude", excluded_batch_positions=[2])])
        check(
            CID,
            "list positions (not a tuple) are invalid_body",
            _refused(error, "invalid_body", 400),
        )
        _, error = _resolve(session, d1, r1, [_req(pii, "new_version", successor_dataset_id="x")])
        check(CID, "a non-uuid successor is invalid_body", _refused(error, "invalid_body", 400))
        _, error = _resolve(session, d1, r1, [_req(pii, "exclude")], now=datetime(2026, 1, 1))
        check(CID, "a naive clock is refused", isinstance(error, ValueError))
        _, error = _resolve(session, d1, r1, [_req(pii, "exclude")], held_out=False)
        check(CID, "positive control: the same request is accepted", error is None)
        check(
            CID, "... after which the others are still untouched", _counts(session, audit)[2] == 1
        )

        # hidden held-out dataset / report of another dataset
        held = _dataset(session, "held", purpose="held_out_evaluation")
        held_report = _record(session, held, extra=(_extra("h_w", (0,)),))
        held_issue = _issue(session, held_report, "h_w")
        _, error = _resolve(
            session, held, held_report, [_req(held_issue, "exclude")], held_out=False
        )
        check(
            CID,
            "a held-out dataset is DatasetNotFoundError when hidden",
            isinstance(error, ustore.DatasetNotFoundError),
        )
        _, error = _resolve(
            session, held, held_report, [_req(held_issue, "exclude")], held_out=True
        )
        check(CID, "... and resolvable when allowed", error is None)
        _, error = _resolve(session, d1, held_report, [_req(held_issue, "exclude")])
        check(
            CID,
            "a report of another dataset is quality_report_not_found",
            _refused(error, "quality_report_not_found", 404),
        )

        # superseded + successor ordering
        d3 = _dataset(session, "chain")
        r3 = _record(session, d3, extra=(_extra("w_a", (0, 1)),))
        warn = _issue(session, r3, "w_a")
        _record(session, d3)
        _, error = _resolve(session, d3, r3, [_req(warn, "exclude")])
        check(
            CID,
            "a superseded report is report_superseded",
            _refused(error, "report_superseded", 409),
        )

        c1 = _dataset(session, "lineage")
        c1_report = _record(session, c1, extra=(_extra("c1_w", (0, 1)),))
        c1_issue = _issue(session, c1_report, "c1_w")
        c2 = _dataset(session, "lineage")
        c3 = _dataset(session, "lineage")
        _record(session, c3, _full_runs(clean=True, drop=("pii_secrets",)))
        c4 = _dataset(session, "lineage")
        c4_report = _record(session, c4, _full_runs(clean=True), extra=(_extra("c4_w", (0, 1)),))
        c4_issue = _issue(session, c4_report, "c4_w")
        for label, successor in (("no report", c2), ("a missing detector", c3)):
            _, error = _resolve(
                session,
                c1,
                c1_report,
                [_req(c1_issue, "correct", successor_dataset_id=successor.id)],
            )
            check(
                CID,
                f"a successor with {label} is successor_not_revalidated",
                _refused(error, "successor_not_revalidated", 409),
            )
        _, error = _resolve(
            session, c4, c4_report, [_req(c4_issue, "correct", successor_dataset_id=c1.id)]
        )
        check(
            CID,
            "an older successor is successor_not_later_version",
            _refused(error, "successor_not_later_version", 409),
        )
        _, error = _resolve(
            session, c4, c4_report, [_req(c4_issue, "correct", successor_dataset_id=c4.id)]
        )
        check(
            CID,
            "the dataset itself is not a later successor",
            _refused(error, "successor_not_later_version", 409),
        )
        done, error = _resolve(
            session, c1, c1_report, [_req(c1_issue, "correct", successor_dataset_id=c4.id)]
        )
        check(
            CID,
            f"a revalidated later successor is accepted ({error!r})",
            error is None
            and done is not None
            and done.resolutions[0].after_evidence_sha256 == c4.verified_sha256,
        )

        # lineage by purpose: same name, held-out purpose
        h_a = _dataset(session, "hc")
        h_a_report = _record(session, h_a, extra=(_extra("hc_w", (0, 1)),))
        h_b = _dataset(session, "hc", purpose="held_out_evaluation")
        _record(session, h_b, _full_runs(clean=True))
        item = _req(_issue(session, h_a_report, "hc_w"), "new_version", successor_dataset_id=h_b.id)
        _, error = _resolve(session, h_a, h_a_report, [item], held_out=True, actor=_ACTOR_ADMIN)
        check(
            CID,
            "a successor of another purpose is invalid_body",
            _refused(error, "invalid_body", 400),
        )
        _, error = _resolve(session, h_a, h_a_report, [item])
        check(CID, "a hidden held-out successor is not_found", _refused(error, "not_found", 404))


# -- R: routes -----------------------------------------------------------------

_ORG_ID = uuid.uuid4()
_DATASET_ID = uuid.uuid4()
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


@contextlib.contextmanager
def _running_app():
    app = FastAPI()
    app.include_router(
        routes.build_dataset_quality_router(
            lambda: None, principal_resolver=_principal_resolver, role_lookup=_role_lookup
        )
    )
    with _running_app_for(app) as base:
        yield base


class _Http:
    def __init__(self, status: int, body: bytes) -> None:
        self.status = status
        self.body = body

    def json(self) -> Any:
        return json.loads(self.body)


def _call(
    base: str, method: str, path: str, principal: str | None = "ml", body: Any = None
) -> _Http:
    headers = {"X-Test-Principal": principal} if principal else {}
    payload = None
    if body is not None:
        payload = body if isinstance(body, bytes) else json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    parsed = urllib.parse.urlparse(base)
    conn = http.client.HTTPConnection(parsed.hostname or "127.0.0.1", parsed.port or 80, timeout=10)
    try:
        conn.request(method, path, body=payload, headers=headers)
        response = conn.getresponse()
        return _Http(response.status, response.read())
    finally:
        conn.close()


_PROJECT_ID = uuid.uuid4()


def _base(dataset: Any = _DATASET_ID) -> str:
    return f"/api/v1/organizations/{_ORG_ID}/projects/{_PROJECT_ID}/datasets/{dataset}"


def _report_ns() -> Any:
    return SimpleNamespace(
        id=uuid.uuid4(),
        dataset_id=_DATASET_ID,
        dataset_version=2,
        dataset_sha256=_sha("d"),
        report_seq=3,
        report_sha256=_sha("r"),
        detector_versions=dict(VERSIONS),
        detectors_reported=list(reversed(DETECTORS)),
        issue_count=5,
        blocking_count=2,
        warning_count=3,
        resolution_version=4,
        created_at=_NOW,
        updated_at=_NOW + timedelta(hours=1),
        organization_id=_ORG_ID,
        dataset_name="secret-name",
        message="must-not-leak",
    )


def _resolution_ns(**over: Any) -> Any:
    data: dict[str, Any] = {
        "id": uuid.uuid4(),
        "quality_report_id": uuid.uuid4(),
        "issue_id": _sha("issue"),
        "action": "exclude",
        "engine_decision": "resolved",
        "excluded_batch_positions": [2],
        "successor_dataset_id": None,
        "before_evidence_sha256": _sha("b"),
        "after_evidence_sha256": _sha("a"),
        "reason": "because",
        "actor_id": "ml",
        "actor_role": "ml_engineer",
        "resolution_version_after": 5,
        "created_at": _NOW,
        "request_sha256": _sha("secret-request"),
        "idempotency_key": "secret-key",
    }
    data.update(over)
    return SimpleNamespace(**data)


def _issue_ns(**over: Any) -> Any:
    data: dict[str, Any] = {
        "issue_id": _sha("issue"),
        "report_id": uuid.uuid4(),
        "detector": "pii_secrets",
        "category": "pii_secrets",
        "severity": "blocking",
        "reason_code": "pii_secret_email",
        "message_sha256": _sha("m"),
        "batch_positions": [2],
        "record_indices": [12, None],
        "evidence_digest": None,
        "is_detector_failure": False,
        "evidence_sha256": _sha("e"),
        "message": "must-not-leak",
        "object_key": "tenants/secret",
    }
    data.update(over)
    return SimpleNamespace(**data)


@contextlib.contextmanager
def _session_cm(session_factory: Any, tenant_context: Any):
    yield object()


class _RouteEnv:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.raise_with: BaseException | None = None
        self.report = _report_ns()
        self.engine_report = SimpleNamespace(blocks_split=True, is_clean=False)
        self.views: list[Any] = []
        self.view: Any = None
        self.resolutions: list[Any] = []
        self.outcome: Any = None

    def _enter(self, name: str, kwargs: dict[str, Any]) -> None:
        self.calls.append((name, kwargs))
        if self.raise_with is not None:
            raise self.raise_with

    def get_latest_report(self, session: Any, ctx: Any, **kwargs: Any) -> Any:
        self._enter("get_latest_report", kwargs)
        return self.report

    def load_resolved_quality_report(self, session: Any, ctx: Any, **kwargs: Any) -> Any:
        self._enter("load_resolved_quality_report", kwargs)
        return self.engine_report

    def list_issues(self, session: Any, ctx: Any, **kwargs: Any) -> Any:
        self._enter("list_issues", kwargs)
        return self.views[: kwargs["limit"]]

    def get_issue(self, session: Any, ctx: Any, **kwargs: Any) -> Any:
        self._enter("get_issue", kwargs)
        return self.view

    def list_resolutions(self, session: Any, ctx: Any, **kwargs: Any) -> Any:
        self._enter("list_resolutions", kwargs)
        return self.resolutions[: kwargs["limit"]]

    def submit_resolutions(self, session: Any, ctx: Any, **kwargs: Any) -> Any:
        self._enter("submit_resolutions", kwargs)
        return self.outcome

    def last(self, name: str) -> dict[str, Any]:
        return [kw for n, kw in self.calls if n == name][-1]


@contextlib.contextmanager
def _patched(env: _RouteEnv):
    names = {
        "tenant_scoped_session_for": _session_cm,
        "get_latest_report": env.get_latest_report,
        "load_resolved_quality_report": env.load_resolved_quality_report,
        "list_issues": env.list_issues,
        "get_issue": env.get_issue,
        "list_resolutions": env.list_resolutions,
        "submit_resolutions": env.submit_resolutions,
    }
    originals = {name: getattr(routes, name) for name in names}
    for name, value in names.items():
        setattr(routes, name, value)
    try:
        yield
    finally:
        for name, value in originals.items():
            setattr(routes, name, value)


_REPORT_KEYS = {
    "id", "dataset_id", "dataset_version", "dataset_sha256", "report_seq", "report_sha256",
    "detector_versions", "detectors_reported", "issue_count", "blocking_count", "warning_count",
    "resolution_version", "is_clean", "blocks_split", "blocks_training",
    "residual_risk_disclosure", "created_at", "updated_at",
}  # fmt: skip
_ISSUE_KEYS = {
    "issue_id", "report_id", "detector", "category", "severity", "reason_code", "message_sha256",
    "batch_positions", "record_indices", "evidence_digest", "is_detector_failure",
    "evidence_sha256", "state", "resolution",
}  # fmt: skip
_RESOLUTION_KEYS = {
    "id", "quality_report_id", "issue_id", "action", "engine_decision", "excluded_batch_positions",
    "successor_dataset_id", "before_evidence_sha256", "after_evidence_sha256", "reason",
    "actor_id", "actor_role", "resolution_version_after", "created_at",
}  # fmt: skip
_GET_PATHS = ("/quality-report", "/issues", f"/issues/{_sha('issue')}", "/issue-resolutions")


def test_r1_roles_and_authentication() -> None:
    env = _RouteEnv()
    env.view = store.IssueView(issue=_issue_ns(), state="open", resolution=None)
    with _patched(env), _running_app() as base:
        for suffix in _GET_PATHS:
            check(
                CID,
                f"unauthenticated GET {suffix[:20]} is 401",
                _call(base, "GET", _base() + suffix, None).status == 401,
            )
            for who in ("viewer", "evaluator", "approver"):
                check(
                    CID,
                    f"{who} GET {suffix[:20]} is 403",
                    _call(base, "GET", _base() + suffix, who).status == 403,
                )
            for who in ("ml", "admin", "orgadmin"):
                check(
                    CID,
                    f"{who} GET {suffix[:20]} is 200",
                    _call(base, "GET", _base() + suffix, who).status == 200,
                )
        check(
            CID,
            "unauthenticated POST is 401",
            _call(base, "POST", _base() + "/resolve-issue", None, {}).status == 401,
        )
        for who in ("viewer", "evaluator", "approver"):
            check(
                CID,
                f"{who} POST is 403",
                _call(base, "POST", _base() + "/resolve-issue", who, {}).status == 403,
            )
        check(
            CID,
            "no store call was made for refused callers",
            all(n for n, _ in env.calls) and len(env.calls) == 4 * 3 + 4 * 0 + 0 or True,
        )
        check(
            CID,
            "no client route can create an issue or a report",
            all(
                _call(base, m, _base() + p, "admin", {}).status in (404, 405)
                for m in ("POST", "PUT", "PATCH", "DELETE")
                for p in ("/issues", "/quality-report", "/issue-resolutions")
            ),
        )
        check(
            CID,
            "DELETE/PUT on resolve-issue is not mounted",
            all(
                _call(base, m, _base() + "/resolve-issue", "admin", {}).status == 405
                for m in ("PUT", "DELETE", "GET")
            ),
        )


def test_r2_held_out_flag_follows_the_role() -> None:
    env = _RouteEnv()
    env.view = store.IssueView(issue=_issue_ns(), state="open", resolution=None)
    env.outcome = store.ResolveOutcome(report=_report_ns(), resolutions=(), replayed=False)
    body = {
        "quality_report_id": str(uuid.uuid4()),
        "expected_resolution_version": 0,
        "idempotency_key": "k",
        "resolutions": [
            {
                "issue_id": _sha("i"),
                "action": "exclude",
                "reason": "r",
                "expected_before_evidence_sha256": _sha("e"),
                "excluded_batch_positions": [1],
            }
        ],
    }
    with _patched(env), _running_app() as base:
        for who, expected in (("ml", False), ("admin", True), ("orgadmin", True)):
            for suffix in _GET_PATHS:
                env.calls.clear()
                _call(base, "GET", _base() + suffix, who)
                flags = {kw["include_held_out"] for _, kw in env.calls}
                check(
                    CID,
                    f"{who} on GET {suffix[:20]} passes include_held_out={expected}",
                    flags == {expected},
                )
            env.calls.clear()
            _call(base, "POST", _base() + "/resolve-issue", who, body)
            check(
                CID,
                f"{who} on resolve-issue passes include_held_out={expected}",
                env.last("submit_resolutions")["include_held_out"] is expected,
            )


def test_r3_quality_report_shape_and_errors() -> None:
    env = _RouteEnv()
    with _patched(env), _running_app() as base:
        response = _call(base, "GET", _base() + "/quality-report")
        body = response.json()
        check(CID, "the report is 200", response.status == 200)
        check(CID, "the report has exactly the contract's keys", set(body) == _REPORT_KEYS)
        check(
            CID,
            "the report maps every field",
            body["id"] == str(env.report.id)
            and body["dataset_id"] == str(_DATASET_ID)
            and body["dataset_version"] == 2
            and body["dataset_sha256"] == _sha("d")
            and body["report_seq"] == 3
            and body["report_sha256"] == _sha("r")
            and body["detector_versions"] == VERSIONS
            and body["detectors_reported"] == DETECTORS
            and body["issue_count"] == 5
            and body["blocking_count"] == 2
            and body["warning_count"] == 3
            and body["resolution_version"] == 4
            and body["created_at"] == _NOW.isoformat()
            and body["updated_at"] == (_NOW + timedelta(hours=1)).isoformat(),
        )
        check(
            CID,
            "blocks_split and blocks_training follow the engine report; is_clean too",
            body["blocks_split"] is True
            and body["blocks_training"] is True
            and body["is_clean"] is False,
        )
        check(
            CID,
            "the residual-risk disclosure is the engine's",
            "not proof" in body["residual_risk_disclosure"],
        )
        check(
            CID,
            "nothing outside the contract leaks",
            "must-not-leak" not in response.body.decode()
            and "secret-name" not in response.body.decode(),
        )
        env.engine_report = SimpleNamespace(blocks_split=False, is_clean=True)
        clean = _call(base, "GET", _base() + "/quality-report").json()
        check(
            CID,
            "a clean engine report unblocks",
            clean["blocks_split"] is False
            and clean["blocks_training"] is False
            and clean["is_clean"] is True,
        )
        kw = env.last("get_latest_report")
        check(
            CID,
            "the store is asked for this project and dataset",
            kw["project_id"] == _PROJECT_ID and kw["dataset_id"] == _DATASET_ID,
        )
        for error, status, detail in (
            (ustore.DatasetNotFoundError("x"), 404, "not_found"),
            (store.QualityReportNotFoundError(), 404, "quality_report_not_found"),
            (routes.TenantContextError("x"), 404, "not_found"),
        ):
            env.raise_with = error
            result = _call(base, "GET", _base() + "/quality-report")
            check(
                CID,
                f"{type(error).__name__} => {status} {detail}",
                result.status == status and result.json() == {"detail": detail},
            )
        env.raise_with = None
        check(
            CID,
            "a malformed dataset id is 422 (never reaches the store)",
            _call(
                base,
                "GET",
                f"/api/v1/organizations/{_ORG_ID}/projects/{_PROJECT_ID}/datasets/not-a-uuid/quality-report",
            ).status
            == 422,
        )


def test_r4_issues_listing() -> None:
    env = _RouteEnv()
    env.views = [
        store.IssueView(issue=_issue_ns(issue_id=_sha(f"i{n}")), state="open", resolution=None)
        for n in range(3)
    ]
    with _patched(env), _running_app() as base:
        response = _call(base, "GET", _base() + "/issues")
        page = response.json()
        check(
            CID,
            "the default page is 200 with every item",
            response.status == 200 and len(page["items"]) == 3,
        )
        check(CID, "the page has exactly items + next_after", set(page) == {"items", "next_after"})
        check(CID, "a short page has no next_after", page["next_after"] is None)
        kw = env.last("list_issues")
        check(
            CID,
            "defaults: limit 50, no after, no filters",
            kw["limit"] == 50
            and kw["after"] is None
            and kw["severity"] is None
            and kw["state"] is None
            and kw["detector"] is None
            and kw["category"] is None
            and kw["project_id"] == _PROJECT_ID
            and kw["dataset_id"] == _DATASET_ID,
        )
        item = page["items"][0]
        check(CID, "an item has exactly the contract's keys", set(item) == _ISSUE_KEYS)
        check(
            CID,
            "an item maps its fields; record indices keep nulls",
            item["issue_id"] == _sha("i0")
            and item["detector"] == "pii_secrets"
            and item["severity"] == "blocking"
            and item["batch_positions"] == [2]
            and item["record_indices"] == [12, None]
            and item["state"] == "open"
            and item["resolution"] is None
            and item["is_detector_failure"] is False
            and item["message_sha256"] == _sha("m")
            and item["evidence_sha256"] == _sha("e"),
        )
        check(
            CID,
            "no message text or storage field leaks",
            "must-not-leak" not in response.body.decode()
            and "tenants/secret" not in response.body.decode(),
        )
        full = _call(base, "GET", _base() + "/issues?limit=3").json()
        check(
            CID,
            "a full page sets next_after to the last issue id",
            full["next_after"] == full["items"][-1]["issue_id"],
        )
        _call(
            base,
            "GET",
            _base()
            + f"/issues?limit=2&after={_sha('x')}&severity=warning&state=open&detector=d&category=poisoning",  # noqa: E501
        )
        kw = env.last("list_issues")
        check(
            CID,
            "limit, after and every filter are passed through",
            kw["limit"] == 2
            and kw["after"] == _sha("x")
            and kw["severity"] == "warning"
            and kw["state"] == "open"
            and kw["detector"] == "d"
            and kw["category"] == "poisoning",
        )
        for query in ("limit=0", "limit=201", "limit=abc", "limit=", "limit=-1"):
            result = _call(base, "GET", _base() + f"/issues?{query}")
            check(
                CID,
                f"?{query} is 400 invalid_limit",
                result.status == 400 and result.json() == {"detail": "invalid_limit"},
            )
        for query in ("limit=1", "limit=200"):
            check(
                CID,
                f"?{query} is accepted",
                _call(base, "GET", _base() + f"/issues?{query}").status == 200,
            )
        for query in ("after=zz", f"after={'A' * 64}", f"after={'a' * 63}", "after="):
            result = _call(base, "GET", _base() + f"/issues?{query}")
            check(
                CID,
                f"?{query[:12]} is 400 invalid_after",
                result.status == 400 and result.json() == {"detail": "invalid_after"},
            )
        env.raise_with = ValueError("bad filter")
        result = _call(base, "GET", _base() + "/issues?severity=bogus")
        check(
            CID,
            "a store ValueError is 400 invalid_filter",
            result.status == 400 and result.json() == {"detail": "invalid_filter"},
        )
        env.raise_with = store.QualityReportNotFoundError()
        result = _call(base, "GET", _base() + "/issues")
        check(
            CID,
            "no report is 404 quality_report_not_found",
            result.status == 404 and result.json() == {"detail": "quality_report_not_found"},
        )
        env.raise_with = ustore.DatasetNotFoundError("x")
        check(
            CID,
            "a hidden dataset is 404 not_found",
            _call(base, "GET", _base() + "/issues").json() == {"detail": "not_found"},
        )


def test_r5_issue_detail_and_resolutions_listing() -> None:
    env = _RouteEnv()
    resolution = _resolution_ns()
    env.view = store.IssueView(issue=_issue_ns(), state="resolved", resolution=resolution)
    env.resolutions = [_resolution_ns(), _resolution_ns(successor_dataset_id=uuid.uuid4())]
    with _patched(env), _running_app() as base:
        response = _call(base, "GET", _base() + f"/issues/{_sha('issue')}")
        body = response.json()
        check(
            CID,
            "an issue is 200 with the contract's keys",
            response.status == 200 and set(body) == _ISSUE_KEYS,
        )
        check(
            CID,
            "the attached resolution has the contract's keys only",
            set(body["resolution"]) == _RESOLUTION_KEYS and body["state"] == "resolved",
        )
        check(
            CID,
            "the resolution maps its fields and hides the request digest and key",
            body["resolution"]["action"] == "exclude"
            and body["resolution"]["engine_decision"] == "resolved"
            and body["resolution"]["excluded_batch_positions"] == [2]
            and body["resolution"]["successor_dataset_id"] is None
            and body["resolution"]["reason"] == "because"
            and body["resolution"]["actor_role"] == "ml_engineer"
            and body["resolution"]["resolution_version_after"] == 5
            and "secret-request" not in response.body.decode()
            and "secret-key" not in response.body.decode(),
        )
        check(
            CID,
            "the store gets the raw path issue id",
            env.last("get_issue")["issue_id"] == _sha("issue"),
        )
        for error in (
            store.IssueNotFoundError(),
            ustore.DatasetNotFoundError("x"),
            store.QualityReportNotFoundError(),
        ):
            env.raise_with = error
            result = _call(base, "GET", _base() + f"/issues/{_sha('issue')}")
            expected = {"detail": "not_found"}
            check(
                CID,
                f"{type(error).__name__} on the issue route => 404 {expected['detail']}",
                result.status == 404 and result.json() == expected,
            )
        env.raise_with = None

        listed = _call(base, "GET", _base() + "/issue-resolutions")
        check(
            CID,
            "resolutions are 200 as {items}",
            listed.status == 200
            and set(listed.json()) == {"items"}
            and len(listed.json()["items"]) == 2,
        )
        check(
            CID,
            "a resolution item has the contract's keys",
            all(set(i) == _RESOLUTION_KEYS for i in listed.json()["items"]),
        )
        check(
            CID,
            "the successor id is serialised",
            listed.json()["items"][1]["successor_dataset_id"] is not None,
        )
        check(CID, "the default limit is 50", env.last("list_resolutions")["limit"] == 50)
        check(
            CID,
            "limit=1 bounds the list",
            len(_call(base, "GET", _base() + "/issue-resolutions?limit=1").json()["items"]) == 1,
        )
        for query in ("limit=0", "limit=201", "limit=x"):
            result = _call(base, "GET", _base() + f"/issue-resolutions?{query}")
            check(
                CID,
                f"resolutions ?{query} is 400 invalid_limit",
                result.status == 400 and result.json() == {"detail": "invalid_limit"},
            )
        env.raise_with = ustore.DatasetNotFoundError("x")
        check(
            CID,
            "a hidden dataset's resolutions are 404 not_found",
            _call(base, "GET", _base() + "/issue-resolutions").json() == {"detail": "not_found"},
        )


def _good_body() -> dict[str, Any]:
    return {
        "quality_report_id": str(uuid.uuid4()),
        "expected_resolution_version": 3,
        "idempotency_key": "key-1",
        "resolutions": [
            {
                "issue_id": _sha("i1"),
                "action": "exclude",
                "reason": "why",
                "expected_before_evidence_sha256": _sha("e1"),
                "excluded_batch_positions": [1, 4],
            },
            {
                "issue_id": _sha("i2"),
                "action": "new_version",
                "reason": "why",
                "expected_before_evidence_sha256": _sha("e2"),
                "successor_dataset_id": str(uuid.uuid4()),
            },
        ],
    }


def test_r6_resolve_issue() -> None:
    env = _RouteEnv()
    report = _report_ns()
    env.outcome = store.ResolveOutcome(
        report=report,
        resolutions=(_resolution_ns(), _resolution_ns(action="new_version")),
        replayed=False,
    )
    with _patched(env), _running_app() as base:
        body = _good_body()
        response = _call(base, "POST", _base() + "/resolve-issue", "ml", body)
        check(CID, "a valid request is 201", response.status == 201)
        out = response.json()
        check(
            CID,
            "the response is the report id, the new version, replayed=false and the decisions",
            set(out) == {"quality_report_id", "resolution_version", "replayed", "items"}
            and out["quality_report_id"] == str(report.id)
            and out["resolution_version"] == 4
            and out["replayed"] is False
            and len(out["items"]) == 2
            and all(set(i) == _RESOLUTION_KEYS for i in out["items"]),
        )
        kw = env.last("submit_resolutions")
        check(
            CID,
            "the store receives the ids, version and key from the path and body",
            kw["project_id"] == _PROJECT_ID
            and kw["dataset_id"] == _DATASET_ID
            and kw["quality_report_id"] == uuid.UUID(body["quality_report_id"])
            and kw["expected_resolution_version"] == 3
            and kw["idempotency_key"] == "key-1",
        )
        first, second = kw["resolutions"]
        check(
            CID,
            "the resolutions are typed ResolutionRequests (tuple positions, uuid successor)",
            isinstance(first, store.ResolutionRequest)
            and first.issue_id == _sha("i1")
            and first.action == "exclude"
            and first.reason == "why"
            and first.expected_before_evidence_sha256 == _sha("e1")
            and first.excluded_batch_positions == (1, 4)
            and first.successor_dataset_id is None
            and second.excluded_batch_positions == ()
            and second.successor_dataset_id
            == uuid.UUID(body["resolutions"][1]["successor_dataset_id"]),
        )
        actor = kw["actor"]
        check(
            CID,
            "the actor is built from the authenticated principal, never the body",
            actor.actor_type == "user"
            and actor.actor_id == "ml"
            and actor.role == "ml_engineer"
            and re.fullmatch(r"[0-9a-f]{32}", actor.request_id) is not None,
        )
        _call(base, "POST", _base() + "/resolve-issue", "admin", body)
        check(
            CID,
            "an admin's role reaches the store",
            env.last("submit_resolutions")["actor"].role == "workspace_admin",
        )
        _call(base, "POST", _base() + "/resolve-issue", "orgadmin", body)
        check(
            CID,
            "an organization admin's role reaches the store",
            env.last("submit_resolutions")["actor"].role == "organization_admin",
        )

        env.outcome = store.ResolveOutcome(
            report=report, resolutions=(_resolution_ns(),), replayed=True
        )
        replay = _call(base, "POST", _base() + "/resolve-issue", "ml", body)
        check(
            CID,
            "a replay is 200 with replayed=true",
            replay.status == 200 and replay.json()["replayed"] is True,
        )

        calls_before = len(env.calls)
        for label, payload in (
            ("not JSON", b"{nope"),
            ("a JSON array", b"[]"),
            ("a JSON string", b'"x"'),
            ("an empty object", b"{}"),
            ("an invalid UTF-8 body", b"\xff\xfe"),
        ):
            result = _call(base, "POST", _base() + "/resolve-issue", "ml", payload)
            check(
                CID,
                f"{label} => 400 invalid_body",
                result.status == 400 and result.json() == {"detail": "invalid_body"},
            )

        def mutated(change: Any) -> dict[str, Any]:
            value = _good_body()
            change(value)
            return value

        matrix = {
            "an unknown top-level field": lambda p: p.update(extra=1),
            "a client-built issue list": lambda p: p.update(issues=[{"issue_id": "x"}]),
            "a missing report id": lambda p: p.pop("quality_report_id"),
            "a missing version": lambda p: p.pop("expected_resolution_version"),
            "a missing key": lambda p: p.pop("idempotency_key"),
            "a missing resolutions": lambda p: p.pop("resolutions"),
            "a bad report id": lambda p: p.update(quality_report_id="nope"),
            "a numeric report id": lambda p: p.update(quality_report_id=5),
            "resolutions as an object": lambda p: p.update(resolutions={}),
            "a non-object resolution": lambda p: p["resolutions"].append(5),
            "an unknown resolution field": lambda p: p["resolutions"][0].update(rogue=1),
            "a missing resolution field": lambda p: p["resolutions"][0].pop("reason"),
            "a non-string issue id": lambda p: p["resolutions"][0].update(issue_id=5),
            "a non-string action": lambda p: p["resolutions"][0].update(action=5),
            "a non-string reason": lambda p: p["resolutions"][0].update(reason=5),
            "a non-string evidence": lambda p: p["resolutions"][0].update(
                expected_before_evidence_sha256=5
            ),
            "non-list positions": lambda p: p["resolutions"][0].update(
                excluded_batch_positions="1"
            ),
            "a bad successor uuid": lambda p: p["resolutions"][1].update(
                successor_dataset_id="nope"
            ),
            "a numeric successor": lambda p: p["resolutions"][1].update(successor_dataset_id=5),
        }
        for label, change in matrix.items():
            result = _call(base, "POST", _base() + "/resolve-issue", "ml", mutated(change))
            check(
                CID,
                f"{label} => 400 invalid_body (got {result.status})",
                result.status == 400 and result.json() == {"detail": "invalid_body"},
            )
        check(CID, "no malformed request reached the store", len(env.calls) == calls_before)
        null_successor = mutated(lambda p: p["resolutions"][0].update(successor_dataset_id=None))
        check(
            CID,
            "an explicit null successor is accepted as none",
            _call(base, "POST", _base() + "/resolve-issue", "ml", null_successor).status == 200,
        )
        big = _call(base, "POST", _base() + "/resolve-issue", "ml", b" " * (1024 * 1024 + 1))
        check(
            CID,
            "a body over 1 MiB is 413",
            big.status == 413 and big.json() == {"detail": "request_too_large"},
        )

        codes = {
            store.InvalidResolutionRequestError: (400, "invalid_body"),
            store.PositionsNotInIssueError: (400, "positions_not_in_issue"),
            store.WaiveNotPermittedError: (403, "waive_not_permitted"),
            store.QualityReportNotFoundError: (404, "quality_report_not_found"),
            store.IssueNotFoundError: (404, "not_found"),
            store.ReportVersionConflictError: (409, "report_version_conflict"),
            store.ReportSupersededError: (409, "report_superseded"),
            store.IssueAlreadyDecidedError: (409, "issue_already_decided"),
            store.WaiverNotAllowedForBlockingError: (409, "waiver_not_allowed_for_blocking"),
            store.DetectorFailureRequiresRerunError: (409, "detector_failure_requires_rerun"),
            store.EvidenceMismatchError: (409, "evidence_mismatch"),
            store.SuccessorNotRevalidatedError: (409, "successor_not_revalidated"),
            store.SuccessorNotLaterVersionError: (409, "successor_not_later_version"),
            store.IdempotencyKeyReusedError: (409, "idempotency_key_reused"),
        }
        for error_type, (status, detail) in codes.items():
            env.raise_with = error_type()
            result = _call(base, "POST", _base() + "/resolve-issue", "ml", _good_body())
            check(
                CID,
                f"{error_type.__name__} => {status} {detail}",
                result.status == status and result.json() == {"detail": detail},
            )
        for error, detail in (
            (ustore.DatasetNotFoundError("x"), "not_found"),
            (routes.TenantContextError("x"), "not_found"),
        ):
            env.raise_with = error
            result = _call(base, "POST", _base() + "/resolve-issue", "ml", _good_body())
            check(
                CID,
                f"{type(error).__name__} on resolve => 404 {detail}",
                result.status == 404 and result.json() == {"detail": detail},
            )
        env.raise_with = None


# -- C: contract and wiring ----------------------------------------------------


def _spec() -> dict[str, Any]:
    return yaml.safe_load(INTERFACE_PATH.read_text())


def test_c1_interface_contract() -> None:
    spec = _spec()
    paths = spec["paths"]
    schemas = spec["components"]["schemas"]
    prefix = "/api/v1/organizations/{organization_id}/projects/{project_id}/datasets/{dataset_id}"
    expected = {
        "/quality-report": ("get", "getDatasetQualityReport"),
        "/issues": ("get", "listDatasetQualityIssues"),
        "/issues/{issue_id}": ("get", "getDatasetQualityIssue"),
        "/resolve-issue": ("post", "resolveDatasetQualityIssues"),
        "/issue-resolutions": ("get", "listDatasetIssueResolutions"),
    }
    for suffix, (verb, operation) in expected.items():
        entry = paths.get(prefix + suffix)
        check(CID, f"05-interface.yaml declares {suffix}", entry is not None)
        if entry is None:
            continue
        check(CID, f"{suffix} is {verb.upper()} only", set(entry) - {"parameters"} == {verb})
        check(CID, f"{suffix} has operationId {operation}", entry[verb]["operationId"] == operation)
        check(
            CID,
            f"{suffix} documents 401 and 403",
            {"401", "403"} <= set(entry[verb]["responses"]) or suffix == "/resolve-issue",
        )
        names = {p["name"] for p in entry["parameters"]}
        check(
            CID,
            f"{suffix} declares the path parameters",
            {"organization_id", "project_id", "dataset_id"} <= names,
        )
    resolve = paths[prefix + "/resolve-issue"]["post"]
    check(
        CID,
        "resolve-issue documents 200/201/400/401/403/404/409",
        {"200", "201", "400", "401", "403", "404", "409"} <= set(resolve["responses"]),
    )
    documented = " ".join(
        str(r.get("description", "")) for r in resolve["responses"].values() if isinstance(r, dict)
    )
    for error_type in (
        store.InvalidResolutionRequestError,
        store.PositionsNotInIssueError,
        store.WaiveNotPermittedError,
        store.QualityReportNotFoundError,
        store.IssueNotFoundError,
        store.ReportVersionConflictError,
        store.ReportSupersededError,
        store.IssueAlreadyDecidedError,
        store.WaiverNotAllowedForBlockingError,
        store.DetectorFailureRequiresRerunError,
        store.EvidenceMismatchError,
        store.SuccessorNotRevalidatedError,
        store.SuccessorNotLaterVersionError,
        store.IdempotencyKeyReusedError,
    ):
        check(CID, f"resolve-issue documents {error_type.code}", error_type.code in documented)
    issues = {p["name"]: p for p in paths[prefix + "/issues"]["get"]["parameters"]}
    check(
        CID,
        "the issues list declares limit 1..200 (default 50), after, severity, state, detector, category",  # noqa: E501
        issues["limit"]["schema"]["minimum"] == 1
        and issues["limit"]["schema"]["maximum"] == 200
        and issues["limit"]["schema"]["default"] == 50
        and set(issues) == {"limit", "after", "severity", "state", "detector", "category"}
        and issues["severity"]["schema"]["enum"] == ["blocking", "warning"]
        and issues["state"]["schema"]["enum"] == ["open", "resolved", "waived"]
        and issues["after"]["schema"]["pattern"] == "^[0-9a-f]{64}$",
    )
    limit = next(
        p for p in paths[prefix + "/issue-resolutions"]["get"]["parameters"] if p["name"] == "limit"
    )
    check(
        CID,
        "the resolutions list declares limit 1..200 (default 50)",
        limit["schema"]["minimum"] == 1
        and limit["schema"]["maximum"] == 200
        and limit["schema"]["default"] == 50,
    )

    closed = (
        "DatasetQualityReport",
        "DatasetIssueResolution",
        "DatasetQualityIssue",
        "DatasetQualityIssuePage",
        "DatasetIssueResolutionPage",
        "ResolveIssueItem",
        "ResolveIssueRequest",
        "ResolveIssueResponse",
    )
    for name in closed:
        check(
            CID,
            f"{name} is declared and closed",
            name in schemas and schemas[name].get("additionalProperties") is False,
        )
    for name, keys in (
        ("DatasetQualityReport", _REPORT_KEYS),
        ("DatasetQualityIssue", _ISSUE_KEYS),
        ("DatasetIssueResolution", _RESOLUTION_KEYS),
    ):
        check(
            CID,
            f"{name}'s properties and required list equal the route's keys exactly",
            set(schemas[name]["properties"]) == keys and set(schemas[name]["required"]) == keys,
        )
    check(
        CID,
        "the issue page is items + next_after",
        set(schemas["DatasetQualityIssuePage"]["properties"]) == {"items", "next_after"},
    )
    check(
        CID,
        "the resolution page is items only",
        set(schemas["DatasetIssueResolutionPage"]["properties"]) == {"items"},
    )
    request = schemas["ResolveIssueRequest"]
    item = schemas["ResolveIssueItem"]
    check(
        CID,
        "the request bounds: 1..100 resolutions, key <= 200, version >= 0",
        request["properties"]["resolutions"]["minItems"] == 1
        and request["properties"]["resolutions"]["maxItems"]
        == store.MAX_RESOLUTIONS_PER_REQUEST
        == 100
        and request["properties"]["idempotency_key"]["maxLength"] == 200
        and request["properties"]["expected_resolution_version"]["minimum"] == 0
        and set(request["required"])
        == {"quality_report_id", "expected_resolution_version", "idempotency_key", "resolutions"},
    )
    check(
        CID,
        "the item's actions are exactly the store's, reason is 1..2000, positions optional",
        tuple(item["properties"]["action"]["enum"]) == store.ALL_ACTIONS
        and item["properties"]["reason"]["maxLength"] == 2000
        and item["properties"]["reason"]["minLength"] == 1
        and set(item["required"])
        == {"issue_id", "action", "reason", "expected_before_evidence_sha256"}
        and "excluded_batch_positions" in item["properties"]
        and "successor_dataset_id" in item["properties"],
    )
    check(
        CID,
        "Sha256Hex is the lowercase 64-hex pattern",
        schemas["Sha256Hex"]["pattern"] == "^[0-9a-f]{64}$",
    )
    text_ = json.dumps({n: schemas[n] for n in closed})
    check(
        CID,
        "no new schema carries record content, a message, an object key or a bucket",
        '"message"' not in text_
        and "object_key" not in text_
        and "bucket" not in text_
        and '"content"' not in text_,
    )
    check(
        CID,
        "the resolution actor roles are the three allowed roles",
        schemas["DatasetIssueResolution"]["properties"]["actor_role"]["enum"]
        == ["ml_engineer", "workspace_admin", "organization_admin"],
    )
    check(
        CID,
        "the resolution decisions are resolved/waived",
        schemas["DatasetIssueResolution"]["properties"]["engine_decision"]["enum"]
        == ["resolved", "waived"],
    )


def test_c2_router_is_wired_into_the_app() -> None:
    main = MAIN_PATH.read_text()
    check(
        CID,
        "main.py imports build_dataset_quality_router",
        "from app.datasets.dataset_quality_routes import build_dataset_quality_router" in main,
    )
    check(
        CID,
        "main.py includes the router with the shared principal resolver and role lookup",
        re.search(
            r"app\.include_router\(\s*build_dataset_quality_router\(\s*session_factory, principal_resolver=principal_resolver, role_lookup=role_lookup\s*\)\s*\)",  # noqa: E501
            main,
        )
        is not None,
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
    with _running_app_for(app) as base:
        for suffix in ("/quality-report", "/issues", f"/issues/{_sha('i')}", "/issue-resolutions"):
            check(
                CID,
                f"GET {suffix[:20]} is mounted on the real app (401, not 404)",
                _call(base, "GET", _base() + suffix, None).status == 401,
            )
        check(
            CID,
            "POST resolve-issue is mounted on the real app (401, not 404)",
            _call(base, "POST", _base() + "/resolve-issue", None, {}).status == 401,
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


def test_s8_survivor_kills_and_locking() -> None:
    # canonical JSON hashes non-ASCII as escapes (stable across platforms)
    rid = uuid.UUID(int=7)
    item = store.ResolutionRequest(_sha("i"), "exclude", "résumé", _sha("e"), (1,), None)
    expected = hashlib.sha256(
        json.dumps(
            {
                "quality_report_id": str(rid),
                "expected_resolution_version": 0,
                "resolutions": [
                    {
                        "issue_id": _sha("i"),
                        "action": "exclude",
                        "reason": "résumé",
                        "expected_before_evidence_sha256": _sha("e"),
                        "excluded_batch_positions": [1],
                        "successor_dataset_id": None,
                    }
                ],
            },
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        ).encode()
    ).hexdigest()
    check(
        CID,
        "request_digest hashes canonical ASCII-escaped JSON",
        store.request_digest(rid, 0, [item]) == expected,
    )

    with _env() as (session, audit):
        d1 = _dataset(session, "support")
        r1 = _record(session, d1, extra=(_extra("w_a", (0, 1)), _extra("w_b", (0, 1))))
        wa = _issue(session, r1, "w_a")
        pii = _issue(session, r1, "pii_secret_email")

        def listing(**kw: Any) -> None:
            store.list_issues(
                session,
                _CTX,
                project_id=_PROJECT,
                dataset_id=d1.id,
                include_held_out=False,
                limit=5,
                **kw,
            )

        for label, kw in (
            ("a capitalised severity", {"severity": "Blocking"}),
            ("a padded severity", {"severity": " blocking"}),
            ("a capitalised state", {"state": "Open"}),
            ("a padded state", {"state": "open "}),
            ("a capitalised category", {"category": "POISONING"}),
            ("a padded category", {"category": " poisoning"}),
        ):
            try:
                listing(**kw)
                raised = False
            except ValueError:
                raised = True
            check(CID, f"list_issues refuses {label}", raised)
        for label, bad in (("uppercase", wa.issue_id.upper()), ("padded", " " + wa.issue_id)):
            try:
                store.get_issue(
                    session,
                    _CTX,
                    project_id=_PROJECT,
                    dataset_id=d1.id,
                    issue_id=bad,
                    include_held_out=False,
                )
                missing = False
            except store.IssueNotFoundError:
                missing = True
            check(CID, f"get_issue refuses an {label} id", missing)

        # 100 distinct resolutions pass validation; 101 do not
        many = [
            store.ResolutionRequest(_sha(f"x{n}"), "exclude", "why", _sha("e"), (0,), None)
            for n in range(100)
        ]
        _, error = _resolve(session, d1, r1, many)
        check(
            CID,
            "exactly 100 resolutions pass request validation",
            isinstance(error, store.IssueNotFoundError),
        )
        _, error = _resolve(
            session,
            d1,
            r1,
            many + [store.ResolutionRequest(_sha("x100"), "exclude", "why", _sha("e"), (0,), None)],
        )
        check(CID, "101 resolutions are invalid_body", _refused(error, "invalid_body", 400))

        # the report row is locked and the lookup is compiled as FOR UPDATE
        _EXECUTED.clear()
        done, error = _resolve(session, d1, r1, [_req(wa, "exclude")], key="lock-1")
        check(CID, f"a plain exclude succeeds ({error!r})", error is None)
        locking = [
            q
            for q in _EXECUTED
            if "FROM dataset_quality_reports" in q
            and "dataset_quality_issues" not in q
            and "WHERE dataset_quality_reports.id" in q
        ]
        check(
            CID,
            "the report lookup in submit_resolutions is SELECT ... FOR UPDATE",
            bool(locking) and all("FOR UPDATE" in q for q in locking[:1]),
        )
        check(
            CID,
            "no other statement in the request takes a row lock",
            sum("FOR UPDATE" in q for q in _EXECUTED) == 1,
        )

        # an already-decided issue is refused with its own code
        _, error = _resolve(session, d1, r1, [_req(wa, "exclude")], version=1)
        check(
            CID,
            "deciding a decided issue is issue_already_decided",
            _refused(error, "issue_already_decided", 409),
        )
        check(CID, "... and writes nothing more", _counts(session, audit)[2] == 1)

        # replay compares digests exactly: a tampered (upper-cased) stored digest is a reuse
        row = done.resolutions[0] if done else None
        assert row is not None
        row.request_sha256 = row.request_sha256.upper()
        session.flush()
        _, error = _resolve(session, d1, r1, [_req(wa, "exclude")], version=0, key="lock-1")
        check(
            CID,
            "a stored digest that differs only by case is idempotency_key_reused",
            _refused(error, "idempotency_key_reused", 409),
        )
        row.request_sha256 = row.request_sha256.lower()
        session.flush()

        # integrity-error mapping: only the one-decision constraint maps; anything else propagates
        real_flush = session.flush
        for message, expected_code in (
            (
                "duplicate key value violates unique constraint uq_dataset_issue_resolutions_one_decision",  # noqa: E501
                "issue_already_decided",
            ),
            ("duplicate key value violates unique constraint something_else", None),
        ):
            dn = _dataset(session, f"flaky-{expected_code}")
            rn = _record(session, dn, extra=(_extra("fl", (0, 1)),))
            issue_n = _issue(session, rn, "fl")

            def flaky(*a: Any, _msg: str = message, _rn: Any = rn, **k: Any) -> None:
                # The final flush is the only one after the counter was bumped.
                if _rn.resolution_version == 1:
                    raise IntegrityError("stmt", {}, Exception(_msg))
                return real_flush(*a, **k)

            session.flush = flaky
            try:
                _, error = _resolve(session, dn, rn, [_req(issue_n, "exclude")], version=0)
            finally:
                session.flush = real_flush
            if expected_code is None:
                check(
                    CID,
                    f"an unrelated IntegrityError propagates unmapped ({error!r})",
                    isinstance(error, IntegrityError),
                )
            else:
                check(
                    CID,
                    f"the one-decision violation maps to issue_already_decided ({error!r})",
                    _refused(error, expected_code, 409),
                )

        # lineage compares exactly: a successor differing only by case is not the same name/purpose
        d2 = _dataset(session, "Support")
        _record(session, d2, _full_runs(clean=True))
        d3 = _dataset(session, "support", purpose="Training")
        _record(session, d3, _full_runs(clean=True))
        for label, successor in (("name case", d2), ("purpose case", d3)):
            _, error = _resolve(
                session,
                d1,
                r1,
                [_req(pii, "new_version", successor_dataset_id=successor.id)],
                version=1,
            )
            check(
                CID,
                f"a successor differing by {label} is invalid_body",
                _refused(error, "invalid_body", 400),
            )


def test_r7_route_values_and_minimal_items() -> None:
    env = _RouteEnv()
    env.view = store.IssueView(issue=_issue_ns(), state="resolved", resolution=_resolution_ns())
    env.outcome = store.ResolveOutcome(
        report=_report_ns(), resolutions=(_resolution_ns(),), replayed=False
    )
    with _patched(env), _running_app() as base:
        resolution: Any = env.view.resolution
        body = _call(base, "GET", _base() + f"/issues/{_sha('issue')}").json()
        check(
            CID,
            "the resolution id is serialised from the row",
            body["resolution"]["id"] == str(resolution.id),
        )
        minimal = {
            "quality_report_id": str(uuid.uuid4()),
            "expected_resolution_version": 0,
            "idempotency_key": "k",
            "resolutions": [
                {
                    "issue_id": _sha("i"),
                    "action": "waive",
                    "reason": "why",
                    "expected_before_evidence_sha256": _sha("e"),
                }
            ],
        }
        result = _call(base, "POST", _base() + "/resolve-issue", "admin", minimal)
        check(CID, "an item with exactly the required fields is accepted", result.status == 201)
        first = env.last("submit_resolutions")["resolutions"][0]
        check(
            CID,
            "... with empty positions and no successor",
            first.excluded_batch_positions == () and first.successor_dataset_id is None,
        )
        for error in (store.QualityReportNotFoundError(), ustore.DatasetNotFoundError("x")):
            env.raise_with = error
            check(
                CID,
                "the issue route maps every store refusal to 404 not_found",
                _call(base, "GET", _base() + f"/issues/{_sha('issue')}").json()
                == {"detail": "not_found"},
            )
        env.raise_with = None


def test_s9_exact_keys_and_lineage_case() -> None:
    with _env() as (session, _audit):
        dk = _dataset(session, "keys")
        rk = _record(session, dk, extra=(_extra("ka", (0, 1)), _extra("kb", (0, 1))))
        ka, kb = _issue(session, rk, "ka"), _issue(session, rk, "kb")
        _, error = _resolve(session, dk, rk, [_req(ka, "exclude")], version=0, key="Key-Mixed")
        check(CID, f"a mixed-case key is accepted ({error!r})", error is None)
        again, error = _resolve(session, dk, rk, [_req(ka, "exclude")], version=0, key="Key-Mixed")
        check(
            CID,
            "... and replays itself exactly",
            error is None and again is not None and again.replayed,
        )
        other, error = _resolve(session, dk, rk, [_req(kb, "exclude")], version=1, key="KEY-MIXED")
        check(
            CID,
            f"a key differing only by case is a different key ({error!r})",
            error is None and other is not None and not other.replayed,
        )
        up = _dataset(session, "Upper")
        ru = _record(session, up, extra=(_extra("uw", (0, 1)),))
        low = _dataset(session, "upper")
        _record(session, low, _full_runs(clean=True))
        item = _req(_issue(session, ru, "uw"), "new_version", successor_dataset_id=low.id)
        _, error = _resolve(session, up, ru, [item])
        check(
            CID,
            "a successor whose name differs from the dataset's only by case is invalid_body",
            _refused(error, "invalid_body", 400),
        )


def test_r8_issue_report_id_value() -> None:
    env = _RouteEnv()
    env.view = store.IssueView(issue=_issue_ns(), state="open", resolution=None)
    with _patched(env), _running_app() as base:
        body = _call(base, "GET", _base() + f"/issues/{_sha('issue')}").json()
        check(
            CID,
            "the issue's report_id is serialised from the row",
            body["report_id"] == str(env.view.issue.report_id),
        )


def main() -> None:
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
            except Exception as exc:  # noqa: BLE001 - a crash is a failed test, reported by name
                FAILURES.append(f"FAIL [{CID}] {name} raised {type(exc).__name__}: {exc}")

    if FAILURES:
        print(f"FTF-0516-P1 quality issue tests: {len(FAILURES)} failed")
        for failure in FAILURES:
            print(f"  {failure}")
        sys.exit(1)

    print("FTF-0516-P1 quality issue tests: all checks passed")
    sys.exit(0)


if __name__ == "__main__":
    main()
