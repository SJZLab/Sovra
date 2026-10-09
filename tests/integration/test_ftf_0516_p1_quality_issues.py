#!/usr/bin/env python3
"""FTF-0516-P1 -- real Postgres proof for dataset quality report, issue and
resolution persistence and the five quality routes (migration 0026).

Proves against a live PostgreSQL server (never SQLite -- it has no row-level
security) every clause of the QUEUE.md FTF-0516-P1 contract, with the real
FTF-0508..0514 detectors producing the reports:

- the migration upgrades, downgrades and re-upgrades; all three tables ENABLE +
  FORCE row-level security with a non-allow-all `tenant_isolation` policy; the
  app role has no DELETE anywhere, no UPDATE on issues or resolutions, and may
  update only `(resolution_version, updated_at)` of a report;
- `record_quality_report` stores only engine-produced issues, never a detector
  message, pins the evidence hash, and is idempotent for the latest findings;
- raw `sovra_app` SQL that bypasses the store is refused by the database
  itself: a wrong evidence hash or flag, a waiver of a blocking issue, a
  resolution of a detector failure, a waiver by a role that may not waive, a
  before-evidence mismatch, a successor of another purpose or not later,
  unsorted or foreign exclusion positions, a second decision on one issue,
  `resolution_version` +2, any other column change on a report, and a raw
  cross-tenant INSERT;
- the store and the five routes on a real uvicorn server: every refusal code,
  idempotent replay, all-or-nothing, a held-out dataset hidden from
  ml_engineer, cross-tenant 404s identical to a random id, exactly one audit
  row per resolution, and the datasets table untouched by every action;
- `load_resolved_quality_report` keeps FTF-0515's `assert_clear_for_split`
  unchanged: unresolved blocking still raises and resolving clears it.

Every negative has a positive control next to it, so a green run cannot come
from a fixture that refuses everything.

Requires SOVRA_DATABASE_URL (migration-owner role) and SOVRA_APP_DB_PASSWORD.
Run: python3 tests/integration/test_ftf_0516_p1_quality_issues.py
"""

from __future__ import annotations

import contextlib
import hashlib
import http.client
import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.parse
import uuid
from collections.abc import Callable, Iterator
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
from tokenizers import Tokenizer, models, pre_tokenizers, processors, trainers  # noqa: E402
from workers.data.canonical_conversation_normalizer import SCHEMA_VERSION  # noqa: E402
from workers.data.exact_duplicate_detector import detect_exact_duplicates  # noqa: E402
from workers.data.near_duplicate_detector import detect_near_duplicates  # noqa: E402
from workers.data.normalized_duplicate_detector import detect_normalized_duplicates  # noqa: E402
from workers.data.pii_secrets_detector import detect_pii_and_secrets  # noqa: E402
from workers.data.quality_issue_engine import (  # noqa: E402
    REQUIRED_DETECTORS,
    DetectorRun,
    QualityBlockedError,
    QualityIssueInput,
    assert_clear_for_split,
    assert_clear_for_training,
)
from workers.data.record_schema_validator import validate_record_batch  # noqa: E402
from workers.data.tokenization_analysis import (  # noqa: E402
    ChatTemplateFastTokenizer,
    analyze_tokenization,
)
from workers.data.tool_argument_validator import validate_tool_calls  # noqa: E402

from app.auth.organization_workspace_schema import (  # noqa: E402
    APP_ROLE_NAME,
    Organization,
    Workspace,
)
from app.auth.rbac_middleware import AuthenticatedPrincipal, Role  # noqa: E402
from app.datasets import dataset_quality_store as qstore  # noqa: E402
from app.datasets import dataset_upload_store as ustore  # noqa: E402
from app.datasets.dataset_quality_routes import build_dataset_quality_router  # noqa: E402
from app.db.tenant_context import TenantContext, tenant_scoped_session_for  # noqa: E402
from app.projects.project_schema import Project  # noqa: E402

FAILURES: list[str] = []
CID = "FTF-0516-P1"
_ISSUER = "https://issuer.example"
MESSAGE_MARKER = "email found at"  # the detector message text; must never be stored
VERSIONS = {name: "1.0.0" for name in sorted(REQUIRED_DETECTORS)}


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


def _http(
    base_url: str, method: str, path: str, principal: str | None, body: Any = None
) -> _Response:
    headers = {"X-Test-Principal": principal} if principal else {}
    payload = None
    if body is not None:
        payload = json.dumps(body).encode() if not isinstance(body, bytes) else body
        headers["Content-Type"] = "application/json"
    parsed = urllib.parse.urlparse(base_url)
    conn = http.client.HTTPConnection(parsed.hostname or "127.0.0.1", parsed.port or 80, timeout=20)
    try:
        conn.request(method, path, body=payload, headers=headers)
        response = conn.getresponse()
        return _Response(response.status, response.read())
    finally:
        conn.close()


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
    return hashlib.sha256(seed.encode()).hexdigest()


def _record(index: int, text_value: str, raw: str | None = None) -> dict[str, object]:
    return {
        "record_index": index,
        "purpose": "training",
        "schema_version": SCHEMA_VERSION,
        "source_type": "upload",
        "quality_status": "pending",
        "pii_status": "unscanned",
        "tool_schema_status": "not_applicable",
        "record_sha256": _sha(raw if raw is not None else f"raw-{index}-{text_value}"),
        "normalized_sha256": _sha(f"norm-{index}-{text_value}"),
        "content": {
            "messages": [{"role": "user", "content": [{"type": "text", "text": text_value}]}],
            "metadata": {},
        },
    }


def _build_exact_tokenizer() -> ChatTemplateFastTokenizer:
    """A real, exact `ChatTemplateFastTokenizer`: a real `tokenizers.Tokenizer`
    trained offline/in-memory on a tiny fixed corpus, plus a real chat
    template, both pinned by their own real sha256 digests -- not a hand-built
    report. Same technique as FTF-0510's own test suite
    (`tests/tasks/test_ftf_0510.py`); no network, no pretrained model."""
    tokenizer = Tokenizer(models.BPE(unk_token="[UNK]"))
    tokenizer.pre_tokenizer = pre_tokenizers.Whitespace()
    corpus = [
        "alpha routers beta billing contact help gamma delta",
        "order status shipped pending cancelled",
    ] * 20
    trainer = trainers.BpeTrainer(vocab_size=200, special_tokens=["[UNK]"])
    tokenizer.train_from_iterator(corpus, trainer)
    tokenizer.add_special_tokens(["<s>", "</s>"])
    tokenizer.post_processor = processors.TemplateProcessing(
        single="<s> $A", special_tokens=[("<s>", tokenizer.token_to_id("<s>"))]
    )
    scratch_dir = tempfile.mkdtemp(prefix="ftf_0516_p1_integration_")
    tokenizer_json_path = os.path.join(scratch_dir, "tokenizer.json")
    tokenizer.save(tokenizer_json_path)
    tokenizer_json_sha256 = hashlib.sha256(Path(tokenizer_json_path).read_bytes()).hexdigest()
    template = (
        '{% for message in messages %}<|{{ message["role"] }}|>{{ message["content"] }}'
        "{{ eos_token }}\n{% endfor %}"
    )
    template_sha256 = hashlib.sha256(template.encode("utf-8")).hexdigest()
    return ChatTemplateFastTokenizer.from_resolved_files(
        tokenizer_json_path,
        {"chat_template": template, "bos_token": "<s>", "eos_token": "</s>"},
        expected_tokenizer_json_sha256=tokenizer_json_sha256,
        expected_chat_template_sha256=template_sha256,
    )


_REAL_TOKENIZER = _build_exact_tokenizer()


def _full_runs(clean: bool = False, drop: tuple[str, ...] = ()) -> list[DetectorRun]:
    """Real detector reports. Not clean: record 12 carries an email (blocking
    pii at position 2) and records 10/11 share a raw hash (a duplicate warning
    over positions 0 and 1)."""
    batch = (
        [_record(10, "alpha routers"), _record(11, "beta billing")]
        if clean
        else [
            _record(10, "alpha routers", raw="dup"),
            _record(11, "beta billing", raw="dup"),
            _record(12, "contact jane.doe@example.com for help"),
            _record(13, "gamma"),
            _record(14, "delta"),
        ]
    )
    record_schema = validate_record_batch(batch)
    assert (
        record_schema.is_clean
    ), f"test fixture itself failed real FTF-0508 validation: {record_schema.issues!r}"
    tool_arguments = validate_tool_calls(
        [], locked_manifest={}, current_tool_versions={}, instance_validator=None
    )
    tokenization = analyze_tokenization(batch, tokenizer=_REAL_TOKENIZER, max_sequence_length=4096)
    assert (
        tokenization.certifies_training_fit
    ), f"test fixture itself failed real FTF-0510 tokenization: {tokenization.issues!r}"
    reports: dict[str, object] = {
        "record_schema": record_schema,
        "tool_arguments": tool_arguments,
        "tokenization": tokenization,
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
        message=f"{MESSAGE_MARKER} {reason}",
        batch_positions=positions,
        record_indices=tuple(100 + p for p in positions),
        evidence_digest=_sha(reason),
    )


def main() -> None:
    migration_url = _required_env("SOVRA_DATABASE_URL")
    app_password = _required_env("SOVRA_APP_DB_PASSWORD")

    for args, label in (
        (["upgrade", "head"], "alembic upgrade head"),
        (["downgrade", "-1"], "alembic downgrade -1"),
        (["upgrade", "head"], "alembic upgrade head after downgrade -1"),
    ):
        migrated = _alembic(args, migration_url)
        check(
            f"{label} runs cleanly (exit {migrated.returncode})\n{migrated.stdout}\n{migrated.stderr}",  # noqa: E501
            migrated.returncode == 0,
        )
        if migrated.returncode != 0:
            _finish()
    heads = _alembic(["heads"], migration_url)
    check(f"exactly one Alembic head: {heads.stdout!r}", heads.stdout.count("(head)") == 1)

    owner_engine = create_engine(migration_url)
    app_engine = create_engine(_app_role_url(migration_url, app_password))
    check("the app engine is the restricted sovra_app role", app_engine.url.username == "sovra_app")
    factory = sessionmaker(bind=app_engine, expire_on_commit=False)

    _check_catalog(owner_engine)

    # ---- seed: two orgs; org1 has a source connection -------------------------
    with Session(owner_engine) as session:
        org1 = Organization(name="Acme 0516", slug=f"acme-0516-{uuid.uuid4().hex[:8]}")
        org2 = Organization(name="Globex 0516", slug=f"globex-0516-{uuid.uuid4().hex[:8]}")
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

        p1, p2 = project(org1, ws1, "p1"), project(org2, ws2, "p2")
        session.add_all([p1, p2])
        session.commit()
        org1_id, org2_id, p1_id, p2_id = org1.id, org2.id, p1.id, p2.id

    def seed_connection(org: uuid.UUID, project_id: uuid.UUID) -> uuid.UUID:
        connection_id = uuid.uuid4()
        with owner_engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO dataset_source_connections (id, organization_id, project_id, "
                    "source_type, display_name, allowed_purposes_json, security_classification, "
                    "connection_status, created_by) VALUES (:id, :org, :project, 'upload', "
                    "'upload-src', CAST(:purposes AS jsonb), 'internal', 'active', 'seed')"
                ),
                {
                    "id": connection_id,
                    "org": org,
                    "project": project_id,
                    "purposes": json.dumps(["training", "held_out_evaluation"]),
                },
            )
        return connection_id

    conn1, conn2 = seed_connection(org1_id, p1_id), seed_connection(org2_id, p2_id)  # noqa: F841
    ctx1, ctx2 = TenantContext(organization_id=org1_id), TenantContext(organization_id=org2_id)  # noqa: F841
    actor_ml = qstore.QualityActor("user", "user-org1-ml_engineer", "ml_engineer", "req-ml")
    actor_admin = qstore.QualityActor("user", "user-org1-workspace_admin", "workspace_admin", "r-a")
    upload_actor = ustore.UploadActor("user", "seed-user", "req-upload")

    def run_store(
        ctx: TenantContext, fn: Callable[[Session], Any]
    ) -> tuple[Any, BaseException | None]:
        try:
            with tenant_scoped_session_for(factory, ctx) as session:
                return fn(session), None
        except BaseException as exc:  # noqa: BLE001 -- the test inspects the type
            return None, exc

    def make_dataset(
        name: str,
        purpose: str = "training",
        *,
        ctx: TenantContext = ctx1,
        project_id: uuid.UUID = p1_id,
        connection: uuid.UUID = conn1,
    ) -> ustore.Dataset:
        sha = _sha(f"{name}-{uuid.uuid4()}")

        def build(session: Session) -> ustore.Dataset:
            opened = ustore.create_upload_session(
                session,
                ctx,
                project_id=project_id,
                connection_id=connection,
                purpose=purpose,
                declared_size_bytes=100,
                declared_sha256=sha,
                declared_content_type="application/jsonl",
                max_bytes=100,
                actor=upload_actor,
            )
            return ustore.record_upload_verified_and_create_dataset(
                session,
                ctx,
                project_id=project_id,
                upload_session_id=opened.id,
                dataset_name=name,
                verified_size_bytes=100,
                verified_sha256=sha,
                verified_content_type="application/jsonl",
                actor=upload_actor,
            )

        dataset, error = run_store(ctx, build)
        if error is not None:
            raise error
        return dataset

    def record(
        dataset: ustore.Dataset,
        runs: list[DetectorRun] | None = None,
        *,
        extra: tuple[QualityIssueInput, ...] = (),
        ctx: TenantContext = ctx1,
        versions: dict[str, str] | None = None,
    ) -> qstore.DatasetQualityReport:
        report, error = run_store(
            ctx,
            lambda s: qstore.record_quality_report(
                s,
                ctx,
                project_id=dataset.project_id,
                dataset_id=dataset.id,
                runs=runs if runs is not None else _full_runs(),
                extra_issues=extra,
                detector_versions=versions or VERSIONS,
                actor=actor_ml,
            ),
        )
        if error is not None:
            raise error
        return report

    def scalar(sql: str, **params: Any) -> Any:
        with owner_engine.connect() as conn:
            return conn.execute(text(sql), params).scalar()

    def counts() -> tuple[int, int, int, int, int]:
        return (
            int(scalar("SELECT count(*) FROM dataset_quality_reports") or 0),
            int(scalar("SELECT count(*) FROM dataset_quality_issues") or 0),
            int(scalar("SELECT count(*) FROM dataset_issue_resolutions") or 0),
            int(scalar("SELECT count(*) FROM audit_events") or 0),
            int(scalar("SELECT count(*) FROM datasets") or 0),
        )

    def datasets_fingerprint() -> str:
        return str(scalar("SELECT md5(string_agg(d::text, '|' ORDER BY d.id)) FROM datasets d"))

    def audit_count(action: str, resource_id: str) -> int:
        return int(
            scalar(
                "SELECT count(*) FROM audit_events WHERE action=:a AND resource_id=:r",
                a=action,
                r=resource_id,
            )
            or 0
        )

    def issue_of(report: qstore.DatasetQualityReport, reason: str) -> qstore.DatasetQualityIssue:
        rows, error = run_store(
            ctx1,
            lambda s: list(
                s.query(qstore.DatasetQualityIssue).filter_by(
                    report_id=report.id, reason_code=reason
                )
            ),
        )
        if error is not None or len(rows) != 1:
            raise AssertionError(f"expected one issue {reason}: {rows!r} {error!r}")
        return rows[0]

    def req(
        issue: qstore.DatasetQualityIssue, action: str, **over: Any
    ) -> qstore.ResolutionRequest:
        args: dict[str, Any] = {
            "issue_id": issue.issue_id,
            "action": action,
            "reason": "reviewed by the data owner",
            "expected_before_evidence_sha256": issue.evidence_sha256,
        }
        if action == "exclude":
            args["excluded_batch_positions"] = tuple(issue.batch_positions[:1])
        args.update(over)
        return qstore.ResolutionRequest(**args)

    def resolve(
        dataset: ustore.Dataset,
        report: qstore.DatasetQualityReport,
        items: list[qstore.ResolutionRequest],
        *,
        version: int | None = None,
        key: str | None = None,
        actor: qstore.QualityActor = actor_ml,
        held_out: bool = False,
        ctx: TenantContext = ctx1,
    ) -> tuple[Any, BaseException | None]:
        return run_store(
            ctx,
            lambda s: qstore.submit_resolutions(
                s,
                ctx,
                project_id=dataset.project_id,
                dataset_id=dataset.id,
                quality_report_id=report.id,
                expected_resolution_version=report_version(report) if version is None else version,
                idempotency_key=key or uuid.uuid4().hex,
                resolutions=items,
                include_held_out=held_out,
                actor=actor,
            ),
        )

    def report_version(report: qstore.DatasetQualityReport) -> int:
        return int(
            scalar(
                "SELECT resolution_version FROM dataset_quality_reports WHERE id=:i", i=report.id
            )
            or 0
        )

    def refused(error: BaseException | None, code: str) -> bool:
        return isinstance(error, qstore.QualityStoreError) and error.code == code

    # =================================================================== record
    d1 = make_dataset("support")
    before = counts()
    r1 = record(
        d1, extra=(_extra("seeded_poison", (3,), severity="warning", category="poisoning"),)
    )
    after = counts()
    check(
        "recording writes one report, three issues (pii blocking, duplicate warning, "
        "poisoning forced blocking) and one audit event",
        after[0] == before[0] + 1
        and after[1] == before[1] + 3
        and after[3] == before[3] + 1
        and audit_count("dataset_quality_report.record", str(r1.id)) == 1,
    )
    check(
        "report copies the dataset identity and starts at seq 1 / resolution_version 0",
        r1.report_seq == 1
        and r1.resolution_version == 0
        and r1.dataset_sha256 == d1.verified_sha256
        and r1.dataset_version == 1
        and r1.dataset_name == "support"
        and r1.blocking_count == 2
        and r1.warning_count == 1
        and sorted(r1.detectors_reported) == sorted(REQUIRED_DETECTORS),
    )
    pii = issue_of(r1, "pii_secret_email")
    dup = issue_of(r1, "raw_duplicate_group")
    poison = issue_of(r1, "seeded_poison")
    check(
        "issue severities and positions come from the real detectors / engine",
        pii.severity == "blocking"
        and list(pii.batch_positions) == [2]
        and dup.severity == "warning"
        and list(dup.batch_positions) == [0, 1]
        and poison.severity == "blocking"
        and poison.category == "poisoning",
    )
    check(
        "evidence_sha256 is sha256(issue_id:dataset_sha256)",
        all(
            i.evidence_sha256 == _sha(f"{i.issue_id}:{d1.verified_sha256}")
            for i in (pii, dup, poison)
        ),
    )
    stored_text = str(
        scalar(
            "SELECT string_agg(i::text, '|') FROM dataset_quality_issues i WHERE report_id=:r",
            r=r1.id,
        )
    )
    check(
        "no detector message text is stored in any issue column", MESSAGE_MARKER not in stored_text
    )
    check(
        "the message is stored only as its SHA-256",
        pii.message_sha256 == _sha("email found at content[0].text"),
    )

    before = counts()
    again = record(
        d1, extra=(_extra("seeded_poison", (3,), severity="warning", category="poisoning"),)
    )
    check(
        "recording identical findings returns the latest report and writes nothing",
        again.id == r1.id and counts() == before,
    )

    before = counts()
    _, error = run_store(
        ctx1,
        lambda s: qstore.record_quality_report(
            s,
            ctx1,
            project_id=p1_id,
            dataset_id=d1.id,
            runs=_full_runs(),
            detector_versions={"record_schema": "1"},
            actor=actor_ml,
        ),
    )
    check("detector_versions missing a required detector is refused", isinstance(error, ValueError))
    _, error = run_store(
        ctx1,
        lambda s: qstore.record_quality_report(
            s,
            ctx1,
            project_id=p1_id,
            dataset_id=uuid.uuid4(),
            runs=_full_runs(),
            detector_versions=VERSIONS,
            actor=actor_ml,
        ),
    )
    check(
        "an unknown dataset id is DatasetNotFoundError",
        isinstance(error, qstore.DatasetNotFoundError),
    )
    check("record refusals wrote nothing", counts() == before)

    # =================================================== blocks until resolved
    engine_report, error = run_store(
        ctx1,
        lambda s: qstore.load_resolved_quality_report(s, ctx1, project_id=p1_id, dataset_id=d1.id),
    )
    check(f"load_resolved_quality_report works ({error!r})", error is None)
    blocked_split = blocked_training = False
    try:
        assert_clear_for_split(engine_report)
    except QualityBlockedError:
        blocked_split = True
    try:
        assert_clear_for_training(engine_report)
    except QualityBlockedError:
        blocked_training = True
    check(
        "unresolved blocking issues still raise QualityBlockedError for split and training",
        blocked_split and blocked_training and engine_report.blocks_split,
    )

    # ------------------------------------------------ a successor chain for d1
    d2 = make_dataset("support")  # v2, later than d1
    check("d2 is support v2", d2.version == 2)
    r_d2_partial = record(d2, _full_runs(clean=True, drop=("near_duplicate",)))
    check(
        "a report missing a detector records a detector_not_run failure issue",
        r_d2_partial.blocking_count == 1
        and "near_duplicate" not in r_d2_partial.detectors_reported,
    )

    d_other = make_dataset("other-name")
    d_other_report = record(d_other, _full_runs(clean=True))  # noqa: F841

    pii_req = req(pii, "exclude")  # positions (2,)  # noqa: F841
    snapshot = datasets_fingerprint()
    # -- refusals: each writes nothing
    before = counts()
    failure_cases: list[tuple[str, list[qstore.ResolutionRequest], str, dict[str, Any]]] = [
        (
            "wrong before-evidence hash",
            [req(pii, "exclude", expected_before_evidence_sha256=_sha("nope"))],
            "evidence_mismatch",
            {},
        ),
        (
            "exclude positions not in the issue",
            [req(pii, "exclude", excluded_batch_positions=(2, 4))],
            "positions_not_in_issue",
            {},
        ),
        (
            "exclude position from another issue",
            [req(pii, "exclude", excluded_batch_positions=(0,))],
            "positions_not_in_issue",
            {},
        ),
        (
            "waiver of a blocking issue by an admin",
            [req(pii, "waive")],
            "waiver_not_allowed_for_blocking",
            {"actor": actor_admin},
        ),
        (
            "waiver by ml_engineer",
            [req(dup, "waive")],
            "waive_not_permitted",
            {},
        ),
        (
            "correct without a successor",
            [req(pii, "correct")],
            "invalid_body",
            {},
        ),
        (
            "exclude with an empty position list",
            [req(pii, "exclude", excluded_batch_positions=())],
            "invalid_body",
            {},
        ),
        (
            "unsorted exclusion positions",
            [req(dup, "exclude", excluded_batch_positions=(1, 0))],
            "invalid_body",
            {},
        ),
        (
            "successor in another name (lineage)",
            [req(pii, "new_version", successor_dataset_id=d_other.id)],
            "invalid_body",
            {},
        ),
        (
            "successor whose latest report lacks a detector (not revalidated)",
            [req(pii, "new_version", successor_dataset_id=d2.id)],
            "successor_not_revalidated",
            {},
        ),
        (
            "stale expected_resolution_version",
            [req(pii, "exclude")],
            "report_version_conflict",
            {"version": 7},
        ),
        (
            "unknown issue id",
            [req(pii, "exclude", issue_id=_sha("unknown"))],
            "not_found",
            {},
        ),
    ]
    for description, items, code, extra_args in failure_cases:
        _, error = resolve(d1, r1, items, **extra_args)
        check(f"refused with {code}: {description} (got {error!r})", refused(error, code))
    _, error = resolve(d1, r1, [req(pii, "exclude")], held_out=False, version=0, key=" padded ")
    check(
        "an idempotency key with surrounding whitespace is invalid_body",
        refused(error, "invalid_body"),
    )
    check("every resolution refusal wrote nothing", counts() == before)
    check(
        "every resolution refusal left the datasets table untouched",
        datasets_fingerprint() == snapshot,
    )

    # -- all-or-nothing: second item invalid => first not written either
    before = counts()
    _, error = resolve(
        d1,
        r1,
        [req(pii, "exclude"), req(dup, "exclude", expected_before_evidence_sha256=_sha("bad"))],
    )
    check("a request with one bad item is refused", refused(error, "evidence_mismatch"))
    check("... and writes none of its other decisions", counts() == before)

    # -- success: exclude the pii position, new_version the poisoning issue
    # d2 must be revalidated first: a clean full report on a fresh v2? d2 already has a
    # partial report; record the full report as its latest.
    r_d2_full = record(d2, _full_runs(clean=True))
    check("d2 now has a latest report with every required detector", r_d2_full.report_seq == 2)
    before = counts()
    outcome, error = resolve(
        d1,
        r1,
        [req(pii, "exclude"), req(poison, "new_version", successor_dataset_id=d2.id)],
        key="key-1",
    )
    check(f"exclude + new_version succeeds ({error!r})", error is None)
    after = counts()
    check(
        f"two decisions add exactly two resolution rows ({before} -> {after})",
        after[2] == before[2] + 2,
    )
    check(
        f"... and exactly two audit events ({before} -> {after})",
        after[3] == before[3] + 2
        and all(
            audit_count("dataset_quality_issue.resolve", str(row.id)) == 1
            for row in outcome.resolutions
        ),
    )
    check(
        "... and no dataset row is added or changed",
        after[4] == before[4] and datasets_fingerprint() == snapshot,
    )
    check(
        "resolution_version rose by exactly 1 for the whole request",
        report_version(r1) == 1 and outcome is not None and outcome.report.resolution_version == 1,
    )
    pii_row = next(r for r in outcome.resolutions if r.issue_id == pii.issue_id)
    new_row = next(r for r in outcome.resolutions if r.issue_id == poison.issue_id)
    check(
        "exclude row binds sorted positions and the hashed after-evidence",
        list(pii_row.excluded_batch_positions) == [2]
        and pii_row.after_evidence_sha256 == _sha(f"{d1.verified_sha256}:exclude:2")
        and pii_row.before_evidence_sha256 == pii.evidence_sha256
        and pii_row.engine_decision == "resolved",
    )
    check(
        "new_version row binds the successor and its verified hash",
        new_row.successor_dataset_id == d2.id
        and new_row.successor_version == 2
        and new_row.after_evidence_sha256 == d2.verified_sha256,
    )

    # -- idempotent replay
    before = counts()
    replay, error = resolve(
        d1,
        r1,
        [req(pii, "exclude"), req(poison, "new_version", successor_dataset_id=d2.id)],
        version=0,
        key="key-1",
    )
    check(
        "same key + same body replays the stored decisions",
        error is None and replay is not None and replay.replayed and len(replay.resolutions) == 2,
    )
    check("... and adds no rows and no audit events", counts() == before)
    _, error = resolve(
        d1, r1, [req(pii, "exclude", reason="a different reason")], version=0, key="key-1"
    )
    check(
        "same key + different body is idempotency_key_reused",
        refused(error, "idempotency_key_reused"),
    )
    _, error = resolve(d1, r1, [req(dup, "exclude", excluded_batch_positions=(0,))], version=0)
    check(
        "a stale version after a decision is report_version_conflict",
        refused(error, "report_version_conflict"),
    )
    _, error = resolve(d1, r1, [req(pii, "exclude")], version=1)
    check(
        "deciding an already-decided issue is issue_already_decided",
        refused(error, "issue_already_decided"),
    )
    check("replay + stale + decided refusals wrote nothing", counts() == before)

    # -- still blocked? both blocking issues resolved; the warning is open
    engine_report, error = run_store(
        ctx1,
        lambda s: qstore.load_resolved_quality_report(s, ctx1, project_id=p1_id, dataset_id=d1.id),
    )
    clear = True
    try:
        assert_clear_for_split(engine_report)
        assert_clear_for_training(engine_report)
    except QualityBlockedError:
        clear = False
    check("resolving both blocking issues clears split and training (warning stays open)", clear)
    states = {i.issue_id: i.resolution_state for i in engine_report.issues}
    check(
        "engine states: blocking resolved, warning open",
        states[pii.issue_id] == "resolved"
        and states[poison.issue_id] == "resolved"
        and states[dup.issue_id] == "open",
    )
    positions, error = run_store(
        ctx1,
        lambda s: qstore.excluded_batch_positions(s, ctx1, project_id=p1_id, dataset_id=d1.id),
    )
    check("excluded_batch_positions returns the exclusion set", error is None and positions == (2,))

    # -- waive: admin may, on a warning
    _, error = resolve(d1, r1, [req(dup, "waive")], actor=actor_ml)
    check("ml_engineer cannot waive (store)", refused(error, "waive_not_permitted"))
    waived, error = resolve(d1, r1, [req(dup, "waive")], actor=actor_admin)
    check(f"workspace_admin can waive a warning ({error!r})", error is None)
    check(
        "waive row: engine decision waived, no positions, no after-evidence",
        waived is not None
        and waived.resolutions[0].engine_decision == "waived"
        and waived.resolutions[0].after_evidence_sha256 is None
        and list(waived.resolutions[0].excluded_batch_positions) == [],
    )

    # ---- detector failure / superseded / revalidation chain -----------------
    d3 = make_dataset("chain")
    r3 = record(d3, _full_runs(drop=("near_duplicate",)), extra=(_extra("w_a", (0, 1)),))
    failure = issue_of(r3, "detector_not_run")
    _, error = resolve(d3, r3, [req(failure, "exclude")])
    check(
        "a detector failure cannot be resolved", refused(error, "detector_failure_requires_rerun")
    )
    warn = issue_of(r3, "w_a")
    r3b = record(d3, _full_runs(), extra=(_extra("w_a", (0, 1)),))
    check("a re-run with different findings becomes report 2", r3b.report_seq == 2)
    _, error = resolve(d3, r3, [req(warn, "exclude")])
    check("deciding a superseded report is report_superseded", refused(error, "report_superseded"))
    check(
        "the superseded report's chain hash differs from the first report",
        r3b.report_sha256 != r3.report_sha256,
    )

    # successor chain: c2 (no report), c3 (partial report), c4 (full + open issue)
    c1 = make_dataset("lineage")
    c1_report = record(c1, extra=(_extra("c1_w", (0, 1)),))
    c2 = make_dataset("lineage")
    c3 = make_dataset("lineage")
    record(c3, _full_runs(clean=True, drop=("pii_secrets",)))
    c4 = make_dataset("lineage")
    c4_report = record(c4, _full_runs(clean=True), extra=(_extra("c4_w", (0, 1)),))
    c1_issue = issue_of(c1_report, "c1_w")
    _, error = resolve(c1, c1_report, [req(c1_issue, "correct", successor_dataset_id=c2.id)])
    check(
        "a successor with no report is successor_not_revalidated",
        refused(error, "successor_not_revalidated"),
    )
    _, error = resolve(c1, c1_report, [req(c1_issue, "correct", successor_dataset_id=c3.id)])
    check(
        "a successor missing a detector is successor_not_revalidated",
        refused(error, "successor_not_revalidated"),
    )
    c4_issue = issue_of(c4_report, "c4_w")
    _, error = resolve(c4, c4_report, [req(c4_issue, "correct", successor_dataset_id=c1.id)])
    check(
        "a successor that is not a later version is successor_not_later_version",
        refused(error, "successor_not_later_version"),
    )
    ok_outcome, error = resolve(
        c1, c1_report, [req(c1_issue, "correct", successor_dataset_id=c4.id)]
    )
    check(f"a revalidated later successor is accepted ({error!r})", error is None)
    check(
        "a correct resolution stores the successor's verified hash as after-evidence",
        ok_outcome is not None
        and ok_outcome.resolutions[0].after_evidence_sha256 == c4.verified_sha256,
    )

    # held-out successor of the same name
    h_a = make_dataset("held-chain")
    h_a_report = record(h_a, extra=(_extra("h_w", (0, 1)),))
    h_b = make_dataset("held-chain", purpose="held_out_evaluation")
    record(h_b, _full_runs(clean=True))
    h_item = req(issue_of(h_a_report, "h_w"), "new_version", successor_dataset_id=h_b.id)
    _, error = resolve(h_a, h_a_report, [h_item], held_out=True, actor=actor_admin)
    check(
        "a successor of another purpose (training -> held_out) is refused",
        refused(error, "invalid_body"),
    )
    _, error = resolve(h_a, h_a_report, [h_item])
    check(
        "a held-out successor is invisible (not_found) to a caller that may not see held-out",
        refused(error, "not_found"),
    )

    # =================================================== database-level attacks
    attack_ds = make_dataset("attack")
    attack = record(
        attack_ds,
        _full_runs(clean=True),
        extra=(
            _extra("a_w1", (0, 1)),
            _extra("a_w2", (0, 1)),
            _extra("a_w3", (0, 1)),
            _extra("a_w4", (0, 1)),
            _extra("a_w5", (0, 1)),
            _extra("a_w6", (0, 1)),
            _extra("a_w7", (0, 1)),
            _extra("a_w8", (0, 1)),
            _extra("a_b1", (0, 1), severity="blocking"),
            _extra("a_b2", (0, 1), severity="blocking"),
            _extra("a_nopos", (), severity="blocking"),
        ),
    )
    attack_failure_ds = make_dataset("attack-failure")
    attack_failure = record(attack_failure_ds, _full_runs(clean=True, drop=("tokenization",)))
    attack_failure_issue = issue_of(attack_failure, "detector_not_run")

    def raw(org: uuid.UUID, sql: str, /, **params: Any) -> tuple[bool, str]:
        try:
            with app_engine.begin() as conn:
                conn.execute(
                    text("SELECT set_config('app.current_organization_id', :o, true)"),
                    {"o": str(org)},
                )
                conn.execute(text(sql), params)
            return True, ""
        except DBAPIError as exc:
            return False, str(exc.orig)

    def expect(description: str, outcome: tuple[bool, str], fragment: str) -> None:
        ok, message = outcome
        check(f"{description} (got {message[:140]!r})", not ok and fragment in message)

    def aissue(reason: str) -> qstore.DatasetQualityIssue:
        return issue_of(attack, reason)

    _res_columns = (
        "id, organization_id, quality_report_id, issue_id, project_id, dataset_id, dataset_name, "
        "dataset_purpose, dataset_version, dataset_sha256, severity, is_detector_failure, "
        "has_positions, before_evidence_sha256, action, engine_decision, excluded_batch_positions, "
        "successor_dataset_id, successor_version, successor_sha256, after_evidence_sha256, reason, "
        "actor_id, actor_role, request_id, idempotency_key, request_sha256, "
        "resolution_version_after"
    )
    _res_values = (
        "gen_random_uuid(), :org, :report, :issue, :project, :dataset, :dname, 'training', :dver, "
        ":dsha, :sev, :isdf, :haspos, :before, :action, :decision, CAST(:excl AS integer[]), "
        ":succ, :sver, :ssha, :after, :reason, 'raw-actor', :role, 'raw-req', :key, :rsha, 1"
    )

    _WAIVE: dict[str, Any] = {
        "action": "waive",
        "decision": "waived",
        "excl": "{}",
        "after": None,
        "role": "workspace_admin",
    }

    def raw_resolution(
        issue: qstore.DatasetQualityIssue,
        report: qstore.DatasetQualityReport = attack,
        dataset: ustore.Dataset = attack_ds,
        **over: Any,
    ) -> tuple[bool, str]:
        positions = list(issue.batch_positions[:1])
        params: dict[str, Any] = {
            "org": org1_id,
            "report": report.id,
            "issue": issue.issue_id,
            "project": p1_id,
            "dataset": dataset.id,
            "dname": dataset.name,
            "dver": dataset.version,
            "dsha": dataset.verified_sha256,
            "sev": issue.severity,
            "isdf": issue.is_detector_failure,
            "haspos": issue.has_positions,
            "before": issue.evidence_sha256,
            "action": "exclude",
            "decision": "resolved",
            "excl": "{" + ",".join(str(p) for p in positions) + "}",
            "succ": None,
            "sver": None,
            "ssha": None,
            "after": _sha(
                f"{dataset.verified_sha256}:exclude:{','.join(str(p) for p in positions)}"
            ),
            "reason": "raw reason",
            "role": "ml_engineer",
            "key": uuid.uuid4().hex,
            "rsha": _sha("raw"),
        }
        params.update(over)
        return raw(
            org1_id,
            f"INSERT INTO dataset_issue_resolutions ({_res_columns}) VALUES ({_res_values})",
            **params,
        )

    expect(
        "raw waiver of a blocking issue is refused by the database",
        raw_resolution(aissue("a_b1"), **_WAIVE),
        "ck_dataset_issue_resolutions_waive_warning_only",
    )
    expect(
        "raw resolution of a detector failure is refused",
        raw_resolution(
            attack_failure_issue, report=attack_failure, dataset=attack_failure_ds, **_WAIVE
        ),
        "ck_dataset_issue_resolutions_not_detector_failure",
    )
    expect(
        "a lying detector-failure flag fails the issue foreign key",
        raw_resolution(
            attack_failure_issue,
            report=attack_failure,
            dataset=attack_failure_ds,
            isdf=False,
            sev="warning",
            **_WAIVE,
        ),
        "fk_dataset_issue_resolutions_issue_evidence",
    )
    expect(
        "a before-evidence mismatch fails the issue foreign key",
        raw_resolution(aissue("a_w1"), before=_sha("forged")),
        "fk_dataset_issue_resolutions_issue_evidence",
    )
    expect(
        "a lying has_positions flag fails the issue foreign key",
        raw_resolution(aissue("a_nopos"), haspos=True, sev="warning", **_WAIVE),
        "fk_dataset_issue_resolutions_issue_evidence",
    )
    expect(
        "a lying severity fails the issue foreign key",
        raw_resolution(aissue("a_b2"), sev="warning", **_WAIVE),
        "fk_dataset_issue_resolutions_issue_evidence",
    )
    expect(
        "waiver by ml_engineer is refused by the database",
        raw_resolution(aissue("a_w2"), **{**_WAIVE, "role": "ml_engineer"}),
        "ck_dataset_issue_resolutions_waive_admin_only",
    )
    expect(
        "a waive action with a resolved engine decision is refused",
        raw_resolution(aissue("a_w2"), **{**_WAIVE, "decision": "resolved"}),
        "ck_dataset_issue_resolutions_engine_decision",
    )
    expect(
        "exclude without positions on an issue with none is refused",
        raw_resolution(aissue("a_nopos"), excl="{}", haspos=False, after=_sha("x")),
        "ck_dataset_issue_resolutions",
    )
    expect(
        "unsorted exclusion positions are refused",
        raw_resolution(
            aissue("a_w3"),
            excl="{1,0}",
            after=_sha(f"{attack_ds.verified_sha256}:exclude:1,0"),
        ),
        "ck_dataset_issue_resolutions_excluded_positions",
    )
    expect(
        "exclusion positions outside the issue are refused by the insert guard",
        raw_resolution(
            aissue("a_w3"),
            excl="{9}",
            after=_sha(f"{attack_ds.verified_sha256}:exclude:9"),
        ),
        "excluded positions must all belong to the issue",
    )
    expect(
        "a wrong after-evidence hash on exclude is refused",
        raw_resolution(aissue("a_w3"), after=_sha("wrong")),
        "ck_dataset_issue_resolutions_after_evidence",
    )
    expect(
        "correct without a successor is refused",
        raw_resolution(aissue("a_w3"), action="correct", excl="{}", after=None),
        "ck_dataset_issue_resolutions",
    )
    expect(
        "a blank-padded reason is refused",
        raw_resolution(aissue("a_w3"), reason=" padded "),
        "ck_dataset_issue_resolutions_reason",
    )
    expect(
        "a 2001-character reason is refused",
        raw_resolution(aissue("a_w3"), reason="x" * 2001),
        "ck_dataset_issue_resolutions_reason",
    )
    expect(
        "an unknown role is refused",
        raw_resolution(aissue("a_w3"), role="viewer"),
        "ck_dataset_issue_resolutions_actor_role",
    )
    # successor lineage: held-out purpose successor of the same name; not later
    attack_v2 = make_dataset("attack")
    attack_held = make_dataset("attack", purpose="held_out_evaluation")
    expect(
        "a successor of another purpose fails the successor foreign key",
        raw_resolution(
            aissue("a_w4"),
            action="new_version",
            excl="{}",
            succ=attack_held.id,
            sver=attack_held.version,
            ssha=attack_held.verified_sha256,
            after=attack_held.verified_sha256,
        ),
        "fk_dataset_issue_resolutions_successor_lineage",
    )
    expect(
        "a successor that is not a later version is refused",
        raw_resolution(
            aissue("a_w4"),
            action="new_version",
            excl="{}",
            succ=attack_ds.id,
            sver=attack_ds.version,
            ssha=attack_ds.verified_sha256,
            after=attack_ds.verified_sha256,
        ),
        "ck_dataset_issue_resolutions_successor_later",
    )
    expect(
        "a lying successor hash fails the successor foreign key",
        raw_resolution(
            aissue("a_w4"),
            action="new_version",
            excl="{}",
            succ=attack_v2.id,
            sver=attack_v2.version,
            ssha=_sha("forged"),
            after=_sha("forged"),
        ),
        "fk_dataset_issue_resolutions_successor_lineage",
    )
    expect(
        "new_version with a successor but a NULL after-evidence is refused (NULL cannot pass the CHECK)",  # noqa: E501
        raw_resolution(
            aissue("a_w4"),
            action="new_version",
            excl="{}",
            succ=attack_v2.id,
            sver=attack_v2.version,
            ssha=attack_v2.verified_sha256,
            after=None,
        ),
        "ck_dataset_issue_resolutions_after_evidence",
    )
    expect(
        "exclude with a NULL after-evidence is refused",
        raw_resolution(aissue("a_w4"), after=None),
        "ck_dataset_issue_resolutions_after_evidence",
    )
    # positive controls: the same shapes, valid, are accepted (consumes the issue)
    ok, message = raw_resolution(aissue("a_w5"))
    check(f"positive control: a valid raw exclude resolution is accepted ({message[:120]})", ok)
    ok, message = raw_resolution(
        aissue("a_w6"),
        action="new_version",
        excl="{}",
        succ=attack_v2.id,
        sver=attack_v2.version,
        ssha=attack_v2.verified_sha256,
        after=attack_v2.verified_sha256,
    )
    check(f"positive control: a valid raw new_version resolution is accepted ({message[:120]})", ok)
    ok, message = raw_resolution(aissue("a_w7"), **_WAIVE)
    check(
        f"positive control: a valid raw admin waiver of a warning is accepted ({message[:120]})", ok
    )
    expect(
        "a second decision on the same issue is refused (one decision per issue)",
        raw_resolution(aissue("a_w5")),
        "uq_dataset_issue_resolutions_one_decision",
    )

    # issues table
    issue_columns = (
        "id, organization_id, report_id, dataset_sha256, issue_id, detector, category, severity, "
        "reason_code, message_sha256, batch_positions, record_indices, evidence_digest, "
        "is_detector_failure, has_positions, evidence_sha256"
    )

    def raw_issue(**over: Any) -> tuple[bool, str]:
        issue_id = _sha(uuid.uuid4().hex)
        params: dict[str, Any] = {
            "org": org1_id,
            "report": attack.id,
            "dsha": attack_ds.verified_sha256,
            "issue": issue_id,
            "detector": "exact_duplicate",
            "category": "exact_duplicate",
            "severity": "warning",
            "reason": "raw_issue",
            "msg": _sha("m"),
            "pos": "{0}",
            "idx": "{5}",
            "digest": None,
            "isdf": False,
            "haspos": True,
        }
        params.update(over)
        params.setdefault("evidence", _sha(f"{params['issue']}:{params['dsha']}"))
        return raw(
            org1_id,
            f"INSERT INTO dataset_quality_issues ({issue_columns}) VALUES (gen_random_uuid(), :org, "  # noqa: E501
            ":report, :dsha, :issue, :detector, :category, :severity, :reason, :msg, "
            "CAST(:pos AS integer[]), CAST(:idx AS integer[]), :digest, :isdf, :haspos, :evidence)",
            **params,
        )

    ok, message = raw_issue()
    check(f"positive control: a valid raw issue row is accepted ({message[:120]})", ok)
    expect(
        "a wrong evidence_sha256 is refused",
        raw_issue(evidence=_sha("wrong")),
        "ck_dataset_quality_issues_evidence_sha256",
    )
    expect(
        "a lying has_positions is refused",
        raw_issue(haspos=False),
        "ck_dataset_quality_issues_has_positions_truthful",
    )
    expect(
        "an unknown category is refused",
        raw_issue(category="made_up"),
        "ck_dataset_quality_issues_category",
    )
    expect(
        "record indices that do not pair with positions are refused",
        raw_issue(idx="{1,2}"),
        "ck_dataset_quality_issues_indices_pair_positions",
    )
    expect(
        "a detector failure carrying positions is refused",
        raw_issue(category="detector_failure", severity="blocking", isdf=True),
        "ck_dataset_quality_issues_detector_failure_blocks",
    )
    expect(
        "a category detector_failure without the flag is refused",
        raw_issue(
            category="detector_failure",
            severity="blocking",
            isdf=False,
            pos="{}",
            idx="{}",
            haspos=False,
        ),
        "ck_dataset_quality_issues_detector_failure_category",
    )
    expect(
        "a poisoning warning is refused",
        raw_issue(category="poisoning", severity="warning"),
        "ck_dataset_quality_issues_poisoning_blocks",
    )
    expect(
        "a negative position is refused",
        raw_issue(pos="{-1}", idx="{}"),
        "ck_dataset_quality_issues_positions_valid",
    )
    expect(
        "a non-hex evidence digest is refused",
        raw_issue(digest="NOT-HEX"),
        "ck_dataset_quality_issues_evidence_digest",
    )
    expect(
        "a bad reason code is refused",
        raw_issue(reason="Bad Reason"),
        "ck_dataset_quality_issues_reason_code",
    )
    expect(
        "a wrong dataset hash fails the report foreign key",
        raw_issue(dsha=_sha("other-sha")),
        "fk_dataset_quality_issues_report",
    )

    # reports table
    def report_update(org: uuid.UUID, sql: str, **params: Any) -> tuple[bool, str]:
        return raw(org, sql, **params)

    expect(
        "resolution_version +2 is refused by the trigger",
        report_update(
            org1_id,
            "UPDATE dataset_quality_reports SET resolution_version = resolution_version + 2 WHERE id=:r",  # noqa: E501
            r=attack.id,
        ),
        "resolution_version must rise by exactly 1",
    )
    expect(
        "resolution_version going backwards is refused",
        report_update(
            org1_id,
            "UPDATE dataset_quality_reports SET resolution_version = resolution_version - 1 WHERE id=:r",  # noqa: E501
            r=r1.id,
        ),
        "resolution_version must rise by exactly 1",
    )
    expect(
        "the app role cannot UPDATE another report column",
        report_update(
            org1_id,
            "UPDATE dataset_quality_reports SET report_sha256=:s WHERE id=:r",
            s=_sha("x"),
            r=attack.id,
        ),
        "permission denied",
    )
    ok, message = report_update(
        org1_id,
        "UPDATE dataset_quality_reports SET resolution_version = resolution_version + 1, updated_at = now() WHERE id=:r",  # noqa: E501
        r=attack.id,
    )
    check(f"positive control: resolution_version +1 is accepted ({message[:120]})", ok)
    with owner_engine.begin() as conn:
        conn.execute(
            text("SELECT set_config('app.current_organization_id', :o, true)"), {"o": str(org1_id)}
        )
        try:
            conn.execute(
                text(
                    "UPDATE dataset_quality_reports SET report_sha256=:s, "
                    "resolution_version = resolution_version + 1 WHERE id=:r"
                ),
                {"s": _sha("tamper"), "r": attack.id},
            )
            owner_changed = True
        except DBAPIError as exc:
            owner_changed = "immutable except its resolution_version" not in str(exc.orig)
    check("even the migration owner cannot change a report column (trigger)", not owner_changed)
    for table in ("dataset_quality_reports", "dataset_quality_issues", "dataset_issue_resolutions"):
        expect(
            f"the app role cannot DELETE from {table}",
            raw(org1_id, f"DELETE FROM {table}"),
            "permission denied",
        )
    expect(
        "the app role cannot UPDATE an issue",
        raw(org1_id, "UPDATE dataset_quality_issues SET severity='warning' WHERE true"),
        "permission denied",
    )
    expect(
        "the app role cannot UPDATE a resolution",
        raw(org1_id, "UPDATE dataset_issue_resolutions SET reason='edited' WHERE true"),
        "permission denied",
    )

    def report_insert(
        report_seq: int, detectors_reported_sql: str, detector_versions: dict
    ) -> tuple[bool, str]:  # noqa: E501
        return raw(
            org1_id,
            "INSERT INTO dataset_quality_reports (id, organization_id, project_id, dataset_id, "
            "dataset_name, dataset_purpose, dataset_version, dataset_sha256, report_seq, "
            "report_sha256, detector_versions, detectors_reported, issue_count, "
            "blocking_count, warning_count, created_by) "
            f"VALUES (gen_random_uuid(), :o1, :p, :d, 'attack', 'training', 1, :sha, "
            f"{report_seq}, :rsha, CAST(:dv AS jsonb), {detectors_reported_sql}, 0, 0, 0, 'x')",
            o1=org1_id,
            p=p1_id,
            d=attack_ds.id,
            sha=attack_ds.verified_sha256,
            rsha=_sha(f"check-{report_seq}"),
            dv=json.dumps(detector_versions),
        )

    # V11: detectors_reported must be a subset of the 7 known detector names.
    expect(
        "an unknown detector name in detectors_reported is refused by the CHECK",
        report_insert(20, "ARRAY['bogus_detector']::text[]", VERSIONS),
        "ck_dataset_quality_reports_detectors_reported",
    )
    ok, message = report_insert(21, "ARRAY['record_schema']::text[]", VERSIONS)
    check(f"positive control: a real detector name is accepted ({message[:120]})", ok)

    # V10: detector_versions must name every one of the 7 required detectors.
    incomplete_versions = dict(VERSIONS)
    del incomplete_versions[next(iter(sorted(incomplete_versions)))]
    expect(
        "detector_versions missing one of the 7 required detectors is refused by the CHECK",
        report_insert(22, "ARRAY[]::text[]", incomplete_versions),
        "ck_dataset_quality_reports_detector_versions",
    )
    ok, message = report_insert(23, "ARRAY[]::text[]", VERSIONS)
    check(f"positive control: all 7 required detectors is accepted ({message[:120]})", ok)

    # cross-tenant raw access
    cross_ok, cross_msg = raw(
        org2_id,
        "INSERT INTO dataset_quality_reports (id, organization_id, project_id, dataset_id, dataset_name, "  # noqa: E501
        "dataset_purpose, dataset_version, dataset_sha256, report_seq, report_sha256, detector_versions, "  # noqa: E501
        "detectors_reported, issue_count, blocking_count, warning_count, created_by) "
        "VALUES (gen_random_uuid(), :o1, :p, :d, 'attack', 'training', 1, :sha, 9, :rsha, "
        "CAST(:dv AS jsonb), ARRAY[]::text[], 0, 0, 0, 'x')",
        o1=org1_id,
        p=p1_id,
        d=attack_ds.id,
        sha=attack_ds.verified_sha256,
        rsha=_sha("cross"),
        dv=json.dumps(VERSIONS),
    )
    check(
        f"org2 context cannot INSERT a report for org1 (RLS; got {cross_msg[:120]!r})",
        not cross_ok and "row-level security" in cross_msg,
    )
    with app_engine.begin() as conn:
        conn.execute(
            text("SELECT set_config('app.current_organization_id', :o, true)"), {"o": str(org2_id)}
        )
        seen = [
            conn.execute(
                text(f"SELECT count(*) FROM {t} WHERE organization_id=:o1"), {"o1": org1_id}
            ).scalar()
            for t in (
                "dataset_quality_reports",
                "dataset_quality_issues",
                "dataset_issue_resolutions",
            )
        ]
    check("org2 context sees none of org1's quality rows", seen == [0, 0, 0])
    with app_engine.begin() as conn:
        unset = [
            conn.execute(text(f"SELECT count(*) FROM {t}")).scalar()
            for t in (
                "dataset_quality_reports",
                "dataset_quality_issues",
                "dataset_issue_resolutions",
            )
        ]
    check("with no tenant context the app role sees no quality rows", unset == [0, 0, 0])
    # ================================================== concurrency: the report row lock
    lock_ds = make_dataset("lock")
    lock_report = record(
        lock_ds,
        _full_runs(clean=True),
        extra=(_extra("lk_a", (0, 1)), _extra("lk_b", (0, 1))),
    )
    lock_a, lock_b = issue_of(lock_report, "lk_a"), issue_of(lock_report, "lk_b")
    racer: dict[str, Any] = {}

    def race() -> None:
        racer["outcome"] = resolve(lock_ds, lock_report, [req(lock_b, "exclude")], version=0)

    with tenant_scoped_session_for(factory, ctx1) as holder:
        held = qstore.submit_resolutions(
            holder,
            ctx1,
            project_id=p1_id,
            dataset_id=lock_ds.id,
            quality_report_id=lock_report.id,
            expected_resolution_version=0,
            idempotency_key=uuid.uuid4().hex,
            resolutions=[req(lock_a, "exclude")],
            include_held_out=False,
            actor=actor_ml,
        )
        thread = threading.Thread(target=race, daemon=True)
        thread.start()
        time.sleep(1.5)
        still_waiting = thread.is_alive()
    thread.join(15)
    check(
        "a second request for the same report waits on the row lock while the first is open",
        still_waiting and held.report.resolution_version == 1,
    )
    _, raced_error = racer.get("outcome", (None, None))
    check(
        f"... then sees the new version and is report_version_conflict, not a 500 ({raced_error!r})",  # noqa: E501
        refused(raced_error, "report_version_conflict"),
    )
    check(
        "exactly one of the two concurrent decisions was recorded",
        int(
            scalar(
                "SELECT count(*) FROM dataset_issue_resolutions WHERE quality_report_id=:r",
                r=lock_report.id,
            )
            or 0
        )
        == 1
        and report_version(lock_report) == 1,
    )

    # ===================================================================== routes
    org_ids = {"org1": org1_id, "org2": org2_id}
    fixture = _Fixture(org_ids)
    app = FastAPI()
    app.include_router(
        build_dataset_quality_router(
            factory,
            principal_resolver=fixture.principal_resolver,
            role_lookup=fixture.role_lookup,
        )
    )
    ml = fixture.header_for("org1", Role.ML_ENGINEER)
    admin = fixture.header_for("org1", Role.WORKSPACE_ADMIN)
    viewer = fixture.header_for("org1", Role.VIEWER)
    ml2 = fixture.header_for("org2", Role.ML_ENGINEER)

    def base(org: uuid.UUID, project_id: uuid.UUID, dataset_id: uuid.UUID) -> str:
        return f"/api/v1/organizations/{org}/projects/{project_id}/datasets/{dataset_id}"

    route_ds = make_dataset("routes")
    route_report = record(
        route_ds,
        extra=(_extra("rt_warn", (0, 1)), _extra("rt_warn2", (0, 1)), _extra("rt_warn3", (0, 1))),
    )
    route_pii = issue_of(route_report, "pii_secret_email")
    route_warn = issue_of(route_report, "rt_warn")
    route_warn2 = issue_of(route_report, "rt_warn2")
    held_ds = make_dataset("routes-held", purpose="held_out_evaluation")
    held_report = record(held_ds, extra=(_extra("held_warn", (0, 1)),))
    held_issue = issue_of(held_report, "held_warn")

    with _running_app(app) as url:
        b = base(org1_id, p1_id, route_ds.id)
        # authentication / authorization
        check("no principal => 401", _http(url, "GET", b + "/quality-report", None).status == 401)
        check("viewer => 403", _http(url, "GET", b + "/quality-report", viewer).status == 403)
        check(
            "viewer cannot POST resolve-issue",
            _http(url, "POST", b + "/resolve-issue", viewer, {}).status == 403,
        )
        # reads
        response = _http(url, "GET", b + "/quality-report", ml)
        body = response.json()
        check(f"quality-report 200 ({response.status})", response.status == 200)
        check(
            "quality-report: counts, flags and disclosure",
            body["id"] == str(route_report.id)
            and body["report_seq"] == 1
            and body["resolution_version"] == 0
            and body["blocks_split"] is True
            and body["blocks_training"] is True
            and body["is_clean"] is False
            and body["issue_count"] == 5
            and body["blocking_count"] == 1
            and "not proof" in body["residual_risk_disclosure"],
        )
        response = _http(url, "GET", b + "/issues?limit=3", ml)
        page = response.json()
        check(
            "issues: ordered by issue_id, limit honoured, next_after set",
            response.status == 200
            and len(page["items"]) == 3
            and [i["issue_id"] for i in page["items"]]
            == sorted(i["issue_id"] for i in page["items"])
            and page["next_after"] == page["items"][-1]["issue_id"],
        )
        check(
            "issues: no message text and no record content anywhere in the body",
            MESSAGE_MARKER not in response.body.decode()
            and "jane.doe" not in response.body.decode(),
        )
        page2 = _http(url, "GET", b + f"/issues?limit=3&after={page['next_after']}", ml).json()
        all_ids = [i["issue_id"] for i in page["items"] + page2["items"]]
        check(
            "issues: the next page continues without overlap and ends with a null cursor",
            len(set(all_ids)) == 5 and len(page2["items"]) == 2 and page2["next_after"] is None,
        )
        warnings = _http(url, "GET", b + "/issues?severity=warning&state=open", ml).json()["items"]
        check(
            "issues: severity + state filters",
            len(warnings) == 4 and all(i["severity"] == "warning" for i in warnings),
        )
        pii_only = _http(url, "GET", b + "/issues?detector=pii_secrets", ml).json()["items"]
        check(
            "issues: detector filter",
            len(pii_only) == 1 and pii_only[0]["reason_code"] == "pii_secret_email",
        )
        check(
            "issues: category filter",
            len(_http(url, "GET", b + "/issues?category=exact_duplicate", ml).json()["items"]) == 4,
        )
        for query in ("limit=0", "limit=201", "limit=abc"):
            check(
                f"issues: {query} => 400 invalid_limit",
                _http(url, "GET", b + f"/issues?{query}", ml).json() == {"detail": "invalid_limit"},
            )
        check(
            "issues: a non-hex after => 400 invalid_after",
            _http(url, "GET", b + "/issues?after=zz", ml).json() == {"detail": "invalid_after"},
        )
        check(
            "issues: an unknown filter value => 400",
            _http(url, "GET", b + "/issues?severity=bogus", ml).status == 400,
        )
        one = _http(url, "GET", b + f"/issues/{route_pii.issue_id}", ml)
        check(
            "issue detail: exact shape, state open, resolution null",
            one.status == 200
            and one.json()["issue_id"] == route_pii.issue_id
            and one.json()["state"] == "open"
            and one.json()["resolution"] is None
            and one.json()["evidence_sha256"] == route_pii.evidence_sha256
            and set(one.json())
            == {
                "issue_id",
                "report_id",
                "detector",
                "category",
                "severity",
                "reason_code",
                "message_sha256",
                "batch_positions",
                "record_indices",
                "evidence_digest",
                "is_detector_failure",
                "evidence_sha256",
                "state",
                "resolution",
            },
        )
        check(
            "issue detail: unknown id => 404 not_found",
            _http(url, "GET", b + f"/issues/{_sha('none')}", ml).json() == {"detail": "not_found"},
        )
        check(
            "issue detail: non-hex id => 404 not_found",
            _http(url, "GET", b + "/issues/not-hex", ml).status == 404,
        )

        # held-out visibility
        hb = base(org1_id, p1_id, held_ds.id)
        random_ds = base(org1_id, p1_id, uuid.uuid4())
        for suffix in (
            "/quality-report",
            "/issues",
            f"/issues/{held_issue.issue_id}",
            "/issue-resolutions",
        ):
            denied = _http(url, "GET", hb + suffix, ml)
            missing = _http(
                url, "GET", random_ds + suffix.replace(held_issue.issue_id, held_issue.issue_id), ml
            )
            check(
                f"held-out {suffix} is a 404 to ml_engineer identical to a random dataset id",
                denied.status == 404 and denied.body == missing.body,
            )
            check(
                f"held-out {suffix} is visible to workspace_admin",
                _http(url, "GET", hb + suffix, admin).status == 200,
            )
        held_body = {
            "quality_report_id": str(held_report.id),
            "expected_resolution_version": 0,
            "idempotency_key": "held-key",
            "resolutions": [
                {
                    "issue_id": held_issue.issue_id,
                    "action": "exclude",
                    "reason": "held",
                    "expected_before_evidence_sha256": held_issue.evidence_sha256,
                    "excluded_batch_positions": [0],
                }
            ],
        }
        denied = _http(url, "POST", hb + "/resolve-issue", ml, held_body)
        check(
            "held-out resolve-issue is a 404 to ml_engineer",
            denied.status == 404 and denied.json() == {"detail": "not_found"},
        )

        # cross tenant
        cross = _http(url, "GET", b + "/quality-report", ml2)
        check(
            "org2 principal on org1's path => 403 or 404, never data",
            cross.status in (403, 404) and b"report_seq" not in cross.body,
        )
        org2_path = base(org2_id, p2_id, route_ds.id)
        foreign = _http(url, "GET", org2_path + "/quality-report", ml2)
        random_org2 = _http(url, "GET", base(org2_id, p2_id, uuid.uuid4()) + "/quality-report", ml2)
        check(
            "org2 reading org1's dataset id under its own org => 404 identical to a random id",
            foreign.status == 404 and foreign.body == random_org2.body,
        )

        # resolve-issue
        def post(items: list[dict[str, Any]], **over: Any) -> _Response:
            payload = {
                "quality_report_id": str(route_report.id),
                "expected_resolution_version": 0,
                "idempotency_key": uuid.uuid4().hex,
                "resolutions": items,
            }
            payload.update(over)
            return _http(url, "POST", b + "/resolve-issue", ml, payload)

        def item(issue: qstore.DatasetQualityIssue, action: str, **extra: Any) -> dict[str, Any]:
            value: dict[str, Any] = {
                "issue_id": issue.issue_id,
                "action": action,
                "reason": "route review",
                "expected_before_evidence_sha256": issue.evidence_sha256,
            }
            if action == "exclude":
                value["excluded_batch_positions"] = list(issue.batch_positions[:1])
            value.update(extra)
            return value

        before = counts()
        for description, payload_bytes in (
            ("not JSON", b"{nope"),
            ("a JSON array", b"[]"),
            ("empty object", b"{}"),
        ):
            result = _http(url, "POST", b + "/resolve-issue", ml, payload_bytes)
            check(
                f"resolve-issue: {description} => 400 invalid_body",
                result.status == 400 and result.json() == {"detail": "invalid_body"},
            )
        for description, mutate in (
            ("an unknown top-level field", lambda p: p.update(extra=1)),
            ("a missing field", lambda p: p.pop("idempotency_key")),
            ("zero resolutions", lambda p: p.update(resolutions=[])),
            (
                "101 resolutions",
                lambda p: p.update(resolutions=[item(route_warn, "exclude")] * 101),
            ),
            ("a bad report id", lambda p: p.update(quality_report_id="nope")),
            ("a string version", lambda p: p.update(expected_resolution_version="0")),
            ("a bool version", lambda p: p.update(expected_resolution_version=True)),
            ("a negative version", lambda p: p.update(expected_resolution_version=-1)),
            ("an unknown item field", lambda p: p["resolutions"][0].update(rogue=1)),
            ("an unknown action", lambda p: p["resolutions"][0].update(action="delete")),
            (
                "a non-list positions",
                lambda p: p["resolutions"][0].update(excluded_batch_positions="0"),
            ),
            (
                "bool positions",
                lambda p: p["resolutions"][0].update(excluded_batch_positions=[True]),
            ),
            (
                "a bad successor uuid",
                lambda p: p["resolutions"][0].update(successor_dataset_id="nope"),
            ),
            ("a client-built issue payload", lambda p: p.update(issues=[{"issue_id": "x"}])),
        ):
            payload = {
                "quality_report_id": str(route_report.id),
                "expected_resolution_version": 0,
                "idempotency_key": uuid.uuid4().hex,
                "resolutions": [item(route_warn, "exclude")],
            }
            mutate(payload)
            result = _http(url, "POST", b + "/resolve-issue", ml, payload)
            check(
                f"resolve-issue: {description} => 400 invalid_body (got {result.status})",
                result.status == 400 and result.json()["detail"] == "invalid_body",
            )
        check("resolve-issue: malformed requests wrote nothing", counts() == before)

        waive_by_ml = post([item(route_warn, "waive")])
        check(
            "route: ml_engineer waive => 403 waive_not_permitted",
            waive_by_ml.status == 403 and waive_by_ml.json() == {"detail": "waive_not_permitted"},
        )
        blocking_waive = _http(
            url,
            "POST",
            b + "/resolve-issue",
            admin,
            {
                "quality_report_id": str(route_report.id),
                "expected_resolution_version": 0,
                "idempotency_key": "wb-1",
                "resolutions": [item(route_pii, "waive")],
            },
        )
        check(
            "route: waiving a blocking issue => 409",
            blocking_waive.status == 409
            and blocking_waive.json() == {"detail": "waiver_not_allowed_for_blocking"},
        )
        wrong_ev = post([item(route_warn, "exclude", expected_before_evidence_sha256=_sha("x"))])
        check(
            "route: wrong evidence => 409 evidence_mismatch",
            wrong_ev.status == 409 and wrong_ev.json() == {"detail": "evidence_mismatch"},
        )
        stale = post([item(route_warn, "exclude")], expected_resolution_version=3)
        check(
            "route: stale version => 409 report_version_conflict",
            stale.status == 409 and stale.json() == {"detail": "report_version_conflict"},
        )
        bad_pos = post([item(route_warn, "exclude", excluded_batch_positions=[7])])
        check(
            "route: foreign positions => 400 positions_not_in_issue",
            bad_pos.status == 400 and bad_pos.json() == {"detail": "positions_not_in_issue"},
        )
        missing_report = _http(
            url,
            "POST",
            b + "/resolve-issue",
            ml,
            {
                "quality_report_id": str(uuid.uuid4()),
                "expected_resolution_version": 0,
                "idempotency_key": "mr-1",
                "resolutions": [item(route_warn, "exclude")],
            },
        )
        check(
            "route: unknown report => 404 quality_report_not_found",
            missing_report.status == 404
            and missing_report.json() == {"detail": "quality_report_not_found"},
        )
        check("route: refused resolve requests wrote nothing", counts() == before)

        created = post(
            [item(route_warn, "exclude"), item(route_pii, "exclude")], idempotency_key="route-key"
        )
        check(
            f"route: exclude x2 => 201 ({created.status} {created.body[:120]!r})",
            created.status == 201,
        )
        created_body = created.json()
        check(
            "route: response carries the new version, replayed=false and both decisions",
            created_body["resolution_version"] == 1
            and created_body["replayed"] is False
            and len(created_body["items"]) == 2
            and all(
                set(i) >= {"before_evidence_sha256", "after_evidence_sha256", "action"}
                for i in created_body["items"]
            ),
        )
        replay = _http(
            url,
            "POST",
            b + "/resolve-issue",
            ml,
            {
                "quality_report_id": str(route_report.id),
                "expected_resolution_version": 0,
                "idempotency_key": "route-key",
                "resolutions": [item(route_warn, "exclude"), item(route_pii, "exclude")],
            },
        )
        check(
            "route: identical replay => 200 replayed=true",
            replay.status == 200 and replay.json()["replayed"] is True,
        )
        reused = _http(
            url,
            "POST",
            b + "/resolve-issue",
            ml,
            {
                "quality_report_id": str(route_report.id),
                "expected_resolution_version": 0,
                "idempotency_key": "route-key",
                "resolutions": [item(route_warn, "exclude", reason="different")],
            },
        )
        check(
            "route: same key, different body => 409 idempotency_key_reused",
            reused.status == 409 and reused.json() == {"detail": "idempotency_key_reused"},
        )
        decided = post([item(route_warn, "exclude")], expected_resolution_version=1)
        check(
            "route: deciding again => 409 issue_already_decided",
            decided.status == 409 and decided.json() == {"detail": "issue_already_decided"},
        )
        waive_ok = _http(
            url,
            "POST",
            b + "/resolve-issue",
            admin,
            {
                "quality_report_id": str(route_report.id),
                "expected_resolution_version": 1,
                "idempotency_key": "route-waive",
                "resolutions": [item(route_warn2, "waive")],
            },
        )
        check(
            f"route: workspace_admin waives a warning => 201 ({waive_ok.status})",
            waive_ok.status == 201,
        )
        listed = _http(url, "GET", b + "/issue-resolutions?limit=10", ml).json()["items"]
        check(
            "issue-resolutions: newest first, every decision, no duplicates",
            len(listed) == 3 and [r["action"] for r in listed][0] == "waive",
        )
        check(
            "issue-resolutions: limit bounds",
            _http(url, "GET", b + "/issue-resolutions?limit=0", ml).status == 400
            and _http(url, "GET", b + "/issue-resolutions?limit=1", ml).json()["items"].__len__()
            == 1,
        )
        after_report = _http(url, "GET", b + "/quality-report", ml).json()
        check(
            "quality-report reflects the decisions: version 2, still blocked by nothing blocking left",  # noqa: E501
            after_report["resolution_version"] == 2 and after_report["blocks_split"] is False,
        )
        states = {
            i["issue_id"]: i["state"] for i in _http(url, "GET", b + "/issues", ml).json()["items"]
        }
        check(
            "issue states follow the decisions",
            states[route_warn.issue_id] == "resolved"
            and states[route_pii.issue_id] == "resolved"
            and states[route_warn2.issue_id] == "waived",
        )
        open_items = _http(url, "GET", b + "/issues?state=open", ml).json()["items"]
        check("state=open lists only the undecided issues", len(open_items) == 2)
        check(
            "state=waived filter",
            len(_http(url, "GET", b + "/issues?state=waived", ml).json()["items"]) == 1,
        )

    _finish()


def _check_catalog(owner_engine: Any) -> None:
    tables = ("dataset_quality_reports", "dataset_quality_issues", "dataset_issue_resolutions")
    with owner_engine.connect() as conn:
        for table in tables:
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
                f"sovra_app has no table-level UPDATE on {table}",
                not conn.execute(
                    text("SELECT has_table_privilege('sovra_app', :t, 'UPDATE')"), {"t": table}
                ).scalar(),
            )
        columns = {
            row[0]
            for row in conn.execute(
                text(
                    "SELECT column_name FROM information_schema.column_privileges "
                    "WHERE grantee='sovra_app' AND table_name='dataset_quality_reports' "
                    "AND privilege_type='UPDATE'"
                )
            )
        }
        check(
            f"sovra_app may UPDATE only (resolution_version, updated_at) of a report: {columns}",
            columns == {"resolution_version", "updated_at"},
        )
        check(
            "sovra_app has no UPDATE on any column of issues or resolutions",
            conn.execute(
                text(
                    "SELECT count(*) FROM information_schema.column_privileges WHERE grantee='sovra_app' "  # noqa: E501
                    "AND privilege_type='UPDATE' AND table_name IN "
                    "('dataset_quality_issues','dataset_issue_resolutions')"
                )
            ).scalar()
            == 0,
        )
        check(
            "sovra_app has no UPDATE on datasets (a quality action can never rewrite a version)",
            not conn.execute(
                text("SELECT has_table_privilege('sovra_app','datasets','UPDATE')")
            ).scalar(),
        )


def _finish() -> None:
    if FAILURES:
        print(f"FTF-0516-P1 quality issue real proof: {len(FAILURES)} failed")
        for failure in FAILURES:
            print(f"  {failure}")
        sys.exit(1)
    print("FTF-0516-P1 quality issue real proof: all checks passed")
    sys.exit(0)


if __name__ == "__main__":
    main()
