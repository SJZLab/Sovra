#!/usr/bin/env python3
# mypy: disable-error-code="arg-type,attr-defined,return-value,assignment,call-arg,index,misc,no-untyped-def,var-annotated,union-attr,operator,no-any-return"
# ruff: noqa: E501
"""FTF-H010-P3 -- real PostgreSQL proof of full-weight and adapter import validation.

Never SQLite (no RLS, no triggers, no `~` CHECKs). Requires `SOVRA_DATABASE_URL` (a superuser
migration owner, like every sibling integration job) and `SOVRA_APP_DB_PASSWORD`. The files are
real: a real approved-root directory with tiny safetensors fixtures written by stdlib `struct` +
`json`, resolved by the real FTF-0304 resolver and digested by the real FTF-0306 code.

Proves, against the live database as the restricted `sovra_app` role, through the real P1 job
CRUD, the real P2 claim and the P3 activities:

  W   a weights job over real files succeeds and the ModelVersion row passes every database CHECK
      (kind consistency, `chat_template_sha256` format, origin/lineage values) as sovra_app
  A   an adapter job whose parent is that very version succeeds; the parent row is byte-identical
      afterwards and sovra_app holds no UPDATE or DELETE on model_versions
  F   failures write their own code (width <= 64) with no ModelVersion; a pickle beside the
      weights, a bad header, a license revoked, a digest drift, an unknown parent, a different
      base revision and a tokenizer mismatch each end with their own failed code
  Z   fencing on the real trigger: a re-claim after lease expiry takes epoch + 1 and the stale
      holder's validate writes nothing; a concurrent artifact change is caught by the terminal
      re-read; two threads validating one job -> exactly one terminal outcome
  L   no database transaction is open and no job row is locked while the filesystem is read
  T   another tenant cannot read this tenant's job, version or artifact rows, and its own job
      cannot name this tenant's artifact (identical to missing)
  X   META-LEAK: a marker in a file name or directory never reaches a stored failure code, an
      audit row or the logs

Run directly:
    SOVRA_DATABASE_URL=postgresql+psycopg://<superuser>@host/db \\
    SOVRA_APP_DB_PASSWORD=<app-role-password> \\
    python3 tests/integration/test_ftf_h010_p3_weights_import.py
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import struct
import subprocess
import sys
import tempfile
import threading
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

ROOT = Path(__file__).resolve().parent.parent.parent
API_ROOT = ROOT / "apps" / "api"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(API_ROOT))

from sqlalchemy import create_engine, text  # noqa: E402
from sqlalchemy.exc import DBAPIError  # noqa: E402
from sqlalchemy.orm import Session, sessionmaker  # noqa: E402

from app.artifacts.artifact_crud import ArtifactNotFoundError  # noqa: E402
from app.artifacts.artifact_schema import TrainableArtifact  # noqa: E402
from app.artifacts.base_model_revision_digest_resolver import canonical_weights_digest  # noqa: E402
from app.artifacts.local_artifact_resolver import resolve_local_artifact  # noqa: E402
from app.auth.organization_workspace_schema import (  # noqa: E402
    APP_ROLE_NAME,
    Organization,
    Workspace,
)
from app.db.tenant_context import TenantContext, tenant_scoped_session_for  # noqa: E402
from app.model_versions import model_import_validation as claim_module  # noqa: E402
from app.model_versions import model_import_weights_validation as p3  # noqa: E402
from app.model_versions.model_import_crud import ActorInfo, create_model_import_job  # noqa: E402
from app.model_versions.model_version_crud import ModelVersionNotFoundError  # noqa: E402
from app.projects.project_schema import Project  # noqa: E402

CID = "FTF-H010-P3"
FAILURES: list[str] = []
MARK = "LEAK" + "MARK"
REVISION = "a1b2c3d4e5f6"
DTYPE_SIZE = {"F32": 4}


def check(criterion_id: str, description: str, condition: bool) -> None:
    if not condition:
        FAILURES.append(f"FAIL [{criterion_id}] {description}")


def _required_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        print(
            f"BLOCKED_EXTERNAL: {name} is not set -- this task's real evidence requires a live "
            "Postgres (migration owner connection). Not silently skipped: see this file's own "
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


def safetensors_bytes(tensors: list[tuple[str, str, tuple[int, ...]]]) -> bytes:
    header: dict[str, Any] = {}
    offset = 0
    for name, dtype, shape in tensors:
        count = 1
        for dim in shape:
            count *= dim
        size = DTYPE_SIZE[dtype] * count
        header[name] = {"dtype": dtype, "shape": list(shape), "data_offsets": [offset, offset + size]}
        offset += size
    raw = json.dumps(header, separators=(",", ":")).encode()
    return struct.pack("<Q", len(raw)) + raw + bytes(offset)


CONFIG = {
    "model_type": "llama",
    "architectures": ["LlamaForCausalLM"],
    "hidden_size": 4,
    "num_hidden_layers": 1,
    "vocab_size": 16,
    "torch_dtype": "float32",
}
TEMPLATE = "{{ messages }}"
BASE_TENSORS = [("model.layers.0.q_proj.weight", "F32", (8, 4))]
ADAPTER_TENSORS = [
    ("base_model.model.model.layers.0.q_proj.lora_A.weight", "F32", (2, 4)),
    ("base_model.model.model.layers.0.q_proj.lora_B.weight", "F32", (8, 2)),
]
ADAPTER_CONFIG = {"peft_type": "LORA", "r": 2, "target_modules": ["q_proj"]}


def weights_files() -> dict[str, bytes]:
    return {
        "config.json": json.dumps(CONFIG).encode(),
        "tokenizer_config.json": json.dumps({"chat_template": TEMPLATE, "eos_token": "</s>"}).encode(),
        "tokenizer.json": b'{"version":"1"}',
        "model.safetensors": safetensors_bytes(BASE_TENSORS),
    }


def adapter_files() -> dict[str, bytes]:
    return {
        "adapter_config.json": json.dumps(ADAPTER_CONFIG).encode(),
        "adapter_model.safetensors": safetensors_bytes(ADAPTER_TENSORS),
    }


class _LogCapture(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.lines: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.lines.append(record.getMessage())


def main() -> None:
    migration_url = _required_env("SOVRA_DATABASE_URL")
    app_password = _required_env("SOVRA_APP_DB_PASSWORD")
    result = _alembic(["upgrade", "head"], migration_url)
    check(CID, f"M: alembic upgrade head runs cleanly\n{result.stdout}\n{result.stderr}", result.returncode == 0)

    owner_engine = create_engine(migration_url)
    app_engine = create_engine(_app_role_url(migration_url, app_password))
    session_factory = sessionmaker(bind=app_engine)
    check(CID, "everything below runs as the restricted sovra_app role", app_engine.url.username == APP_ROLE_NAME)

    approved_root = Path(tempfile.mkdtemp(prefix="ftf-h010p3-root-"))
    suffix = uuid.uuid4().hex[:8]
    with Session(owner_engine) as session:
        org1 = Organization(name="Acme H010P3", slug=f"acme-h010p3-{suffix}")
        org2 = Organization(name="Globex H010P3", slug=f"globex-h010p3-{suffix}")
        session.add_all([org1, org2])
        session.flush()
        ws1 = Workspace(organization_id=org1.id, name="Acme Main", slug="main")
        ws2 = Workspace(organization_id=org2.id, name="Globex Main", slug="main")
        session.add_all([ws1, ws2])
        session.flush()
        projects = []
        for org, ws, name in ((org1, ws1, "acme-a"), (org1, ws1, "acme-b"), (org2, ws2, "globex-a")):
            project = Project(
                organization_id=org.id,
                workspace_id=ws.id,
                name=name,
                project_type="generic_model_tuning",
                status="active",
                security_profile="standard",
                row_version=1,
                created_by="seed",
            )
            session.add(project)
            projects.append(project)
        session.commit()
        org1_id, org2_id = org1.id, org2.id
        project_a, project_b, project_g = (p.id for p in projects)

    ctx1 = TenantContext(organization_id=org1_id)
    counter = [0]

    def add_artifact(
        directory: str,
        files: dict[str, bytes],
        *,
        org: uuid.UUID = org1_id,
        accepted: bool = True,
        revision: str = REVISION,
        family: str = "llama",
        digest: str | None = None,
    ) -> uuid.UUID:
        base = approved_root / directory
        base.mkdir(parents=True, exist_ok=True)
        for name, data in files.items():
            target = base / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        if digest is None:
            digest = canonical_weights_digest(resolve_local_artifact(approved_root, directory).manifest_sha256)
        counter[0] += 1
        with Session(owner_engine) as session:
            artifact = TrainableArtifact(
                organization_id=org,
                display_name=f"artifact-{suffix}-{counter[0]}",
                source_type="local_filesystem",
                source_uri=directory,
                model_family=family,
                base_model_revision=revision,
                weights_digest=digest,
                license_id="apache-2.0",
                license_accepted_by="owner" if accepted else None,
                license_accepted_at=datetime.now(UTC),
                trust_remote_code_allowed=False,
                required_python_packages_json=[],
                minimum_gpu_profile_json={},
                status="ready",
            )
            session.add(artifact)
            session.commit()
            return artifact.id

    def create_job(origin: str, artifact_id: uuid.UUID, project: uuid.UUID, parent: uuid.UUID | None = None, org: uuid.UUID = org1_id) -> uuid.UUID:
        context = TenantContext(organization_id=org)
        with tenant_scoped_session_for(session_factory, context) as session:
            job = create_model_import_job(
                session,
                context,
                project_id=project,
                requested_origin=origin,
                parent_model_version_id=parent,
                trainable_artifact_id=artifact_id,
                external_provider_model_ref=None,
                actor=ActorInfo(actor_type="user", actor_id="ml-1", request_id=uuid.uuid4().hex),
            )
            return job.id

    def claim(origin: str, job_id: uuid.UUID, project: uuid.UUID, *, now: datetime | None = None, lease: int = 600) -> Any:
        return claim_module.claim_model_import_job(
            session_factory,
            org1_id,
            project,
            job_id,
            origins=(origin,),
            worker_id="worker-1",
            lease_seconds=lease,
            now=now or datetime.now(UTC),
        )

    def run(kind: str, token: Any) -> Any:
        fn = p3.validate_weights_import if kind == "imported_weights" else p3.validate_adapter_import
        return fn(session_factory, token, approved_root=approved_root, now=datetime.now(UTC))

    def job_row(job_id: uuid.UUID) -> Any:
        with Session(owner_engine) as s:
            return s.execute(
                text(
                    "SELECT state, failure_code, claim_epoch, lease_expires_at, result_model_version_id FROM model_import_jobs WHERE id = :id"
                ),
                {"id": str(job_id)},
            ).one()

    def version_row(version_id: uuid.UUID) -> Any:
        with Session(owner_engine) as s:
            return s.execute(
                text(
                    "SELECT import_kind, origin, trainable_artifact_id, weights_digest, base_model_revision, tokenizer_revision, chat_template_sha256, serving_config_sha256, adapter_only, parent_model_version_id, lineage_completeness, version_number, endpoint_reference, external_provider_model_ref, observed_deployment_fingerprint, created_by, project_id FROM model_versions WHERE id = :id"
                ),
                {"id": str(version_id)},
            ).one()

    def version_count() -> int:
        with Session(owner_engine) as s:
            return int(s.execute(text("SELECT count(*) FROM model_versions WHERE organization_id = :o"), {"o": str(org1_id)}).scalar_one())

    def audit_rows(job_id: uuid.UUID) -> list[Any]:
        with Session(owner_engine) as s:
            return list(
                s.execute(
                    text("SELECT actor_id, details_json::text AS details FROM audit_events WHERE resource_id = :id AND action = 'model_import.validate'"),
                    {"id": str(job_id)},
                )
            )

    # ---- W: a weights job over real files ------------------------------------------------------
    base_artifact = add_artifact("base", weights_files())
    base_job = create_job("imported_weights", base_artifact, project_a)
    token = claim("imported_weights", base_job, project_a)
    outcome = run("imported_weights", token)
    check("W", f"W1 weights job succeeds: {outcome}", outcome.state == "succeeded" and outcome.failure_code is None and outcome.model_version_id is not None)
    row = job_row(base_job)
    check("W", "W1 the job row is succeeded with its result and no lease", (row.state, row.failure_code, row.lease_expires_at) == ("succeeded", None, None) and row.result_model_version_id == outcome.model_version_id)
    parent_version = outcome.model_version_id
    v = version_row(parent_version)
    with Session(owner_engine) as s:
        artifact_digest = s.execute(text("SELECT weights_digest FROM trainable_artifacts WHERE id = :i"), {"i": str(base_artifact)}).scalar_one()
    check("W", "W2 import kind / origin / artifact", (v.import_kind, v.origin, v.trainable_artifact_id) == ("full_weight", "imported_weights", base_artifact))
    check("W", "W2 digest re-verified, revision from the artifact", (v.weights_digest, v.base_model_revision, v.tokenizer_revision) == (artifact_digest, REVISION, REVISION))
    check("W", "W2 chat template sha256 is of the exact template bytes (database CHECK format)", v.chat_template_sha256 == hashlib.sha256(TEMPLATE.encode()).hexdigest())
    check("W", "W2 no serving config, not adapter-only, lineage partial, no endpoint columns", (v.serving_config_sha256, v.adapter_only, v.lineage_completeness, v.endpoint_reference, v.external_provider_model_ref, v.observed_deployment_fingerprint) == (None, False, "partial", None, None, None))
    check("W", "W2 created by the job's creator in the job's project", (v.created_by, v.project_id, v.version_number) == ("ml-1", project_a, 1))
    rows = audit_rows(base_job)
    check("W", "W3 exactly one audit row, content-free", len(rows) == 1 and rows[0].actor_id == "worker-1" and json.loads(rows[0].details) == {"job_id": str(base_job), "origin": "imported_weights", "outcome": "succeeded"})

    # ---- A: an adapter on that exact parent ----------------------------------------------------
    with Session(owner_engine) as s:
        parent_snapshot = s.execute(text("SELECT row_to_json(m)::text FROM model_versions m WHERE id = :i"), {"i": str(parent_version)}).scalar_one()
    adapter_artifact = add_artifact("adapter", adapter_files())
    adapter_job = create_job("imported_adapter", adapter_artifact, project_a, parent=parent_version)
    token = claim("imported_adapter", adapter_job, project_a)
    outcome = run("imported_adapter", token)
    check("A", f"A1 adapter job succeeds: {outcome}", outcome.state == "succeeded" and outcome.model_version_id is not None)
    av = version_row(outcome.model_version_id)
    check("A", "A2 adapter ModelVersion shape", (av.import_kind, av.origin, av.adapter_only, av.parent_model_version_id, av.lineage_completeness) == ("adapter", "imported_adapter", True, parent_version, "partial"))
    check("A", "A2 version number follows the parent's; no template, no serving config", (av.version_number, av.chat_template_sha256, av.serving_config_sha256) == (2, None, None))
    with Session(owner_engine) as s:
        after = s.execute(text("SELECT row_to_json(m)::text FROM model_versions m WHERE id = :i"), {"i": str(parent_version)}).scalar_one()
        grants = {r[0] for r in s.execute(text("SELECT privilege_type FROM information_schema.role_table_grants WHERE table_name = 'model_versions' AND grantee = :r"), {"r": APP_ROLE_NAME})}
    check("A", "A3 the parent row is byte-identical afterwards", parent_snapshot == after)
    check("A", f"A3 sovra_app has no UPDATE or DELETE on model_versions: {sorted(grants)}", not ({"UPDATE", "DELETE"} & grants))

    # ---- F: failures write their own code and no version ---------------------------------------
    baseline_versions = version_count()

    def failing(label: str, kind: str, project: uuid.UUID, artifact_id: uuid.UUID, code: str, state: str = "failed", parent: uuid.UUID | None = None, tamper: Any = None) -> uuid.UUID:
        job_id = create_job(kind, artifact_id, project, parent=parent)
        token = claim(kind, job_id, project)
        if tamper is not None:
            tamper()
        outcome = run(kind, token)
        row = job_row(job_id)
        check("F", f"{label}: ({outcome.state}, {outcome.failure_code}) expected ({state}, {code})", (outcome.state, outcome.failure_code) == (state, code))
        check("F", f"{label}: stored row matches, lease cleared, no result", (row.state, row.failure_code, row.lease_expires_at, row.result_model_version_id) == (state, code, None, None))
        check("F", f"{label}: failure code fits the column", len(code) <= 64)
        return job_id

    pickle_files = {**weights_files(), "pytorch_model.bin": b"\x80\x04N."}
    failing("F1 a pickle beside the weights", "imported_weights", project_a, add_artifact("w-pickle", pickle_files), "unsafe_weight_format")
    failing("F2 a corrupt safetensors header", "imported_weights", project_a, add_artifact("w-header", {**weights_files(), "model.safetensors": b"\x05\x00\x00\x00\x00\x00\x00\x00{...."}), "weights_header_invalid")
    failing("F3 a config with a zero size", "imported_weights", project_a, add_artifact("w-config", {**weights_files(), "config.json": json.dumps({**CONFIG, "hidden_size": 0}).encode()}), "config_invalid")
    failing("F4 a quantized checkpoint", "imported_weights", project_a, add_artifact("w-quant", {**weights_files(), "config.json": json.dumps({**CONFIG, "quantization_config": {"bits": 4}}).encode()}), "quantization_unverified")
    failing("F5 the license not accepted", "imported_weights", project_a, add_artifact("w-license", weights_files(), accepted=False), "license_not_accepted")
    drift = add_artifact("w-drift", weights_files())
    failing("F6 a file added after the artifact was resolved", "imported_weights", project_a, drift, "digest_mismatch", tamper=lambda: (approved_root / "w-drift" / "extra.txt").write_text("late"))
    failing("F7 a digest the files never had", "imported_weights", project_a, add_artifact("w-bad-digest", weights_files(), digest="sha256:" + "0" * 64), "digest_mismatch")
    failing("F8 an adapter whose base revision differs", "imported_adapter", project_a, add_artifact("a-rev", adapter_files(), revision="ffffffffffff"), "adapter_base_mismatch", parent=parent_version)
    failing("F9 an adapter of another model family", "imported_adapter", project_a, add_artifact("a-family", adapter_files(), family="mistral"), "adapter_base_mismatch", parent=parent_version)
    failing("F10 an adapter whose rank disagrees with its config", "imported_adapter", project_a, add_artifact("a-rank", {**adapter_files(), "adapter_config.json": json.dumps({**ADAPTER_CONFIG, "r": 3}).encode()}), "adapter_base_incompatible", parent=parent_version)
    failing("F11 an adapter with a different tokenizer", "imported_adapter", project_a, add_artifact("a-tok", {**adapter_files(), "tokenizer.json": b'{"version":"9"}'}), "tokenizer_mismatch", parent=parent_version)
    try:
        create_job("imported_adapter", add_artifact("a-other-project", adapter_files()), project_b, parent=parent_version)
        cross = None
    except ModelVersionNotFoundError:
        cross = "not_found"
    check("F", f"F12 a parent from another project cannot even be enqueued (the P1 job CRUD): {cross}", cross == "not_found")
    failing("F13 a parent that is itself an adapter", "imported_adapter", project_a, add_artifact("a-on-adapter", adapter_files()), "base_not_validated", parent=outcome.model_version_id)
    check("F", "F15 no failure wrote a ModelVersion", version_count() == baseline_versions)

    # ---- Z: fencing -----------------------------------------------------------------------------
    stale_artifact = add_artifact("w-stale", weights_files())
    stale_job = create_job("imported_weights", stale_artifact, project_a)
    first = claim("imported_weights", stale_job, project_a, now=datetime.now(UTC) - timedelta(hours=2), lease=600)
    second = claim("imported_weights", stale_job, project_a)
    check("Z", f"Z1 a re-claim after the lease expired takes epoch + 1 ({first.epoch} -> {second.epoch})", second.epoch == first.epoch + 1)
    try:
        run("imported_weights", first)
        stale_error = None
    except claim_module.ModelImportActivityError as exc:
        stale_error = exc.code
    row = job_row(stale_job)
    check("Z", f"Z2 the stale holder is fenced out: {stale_error}", stale_error == "fenced_out")
    check("Z", "Z2 and wrote nothing: job still validating at the new epoch, no audit row, no version", (row.state, row.claim_epoch) == ("validating", second.epoch) and audit_rows(stale_job) == [] and version_count() == baseline_versions)
    outcome = run("imported_weights", second)
    check("Z", "Z3 the live holder finishes", outcome.state == "succeeded")

    # a stale holder that is only fenced at the terminal write: the epoch moves while it works
    race_artifact = add_artifact("w-race", weights_files())
    race_job = create_job("imported_weights", race_artifact, project_a)
    holder = claim("imported_weights", race_job, project_a, now=datetime.now(UTC) - timedelta(hours=2), lease=600)
    real_resolve = p3.resolve_local_artifact

    def reclaim_midway(*a: Any, **k: Any) -> Any:
        result = real_resolve(*a, **k)
        reclaim_midway.new = claim("imported_weights", race_job, project_a)
        return result

    p3.resolve_local_artifact = reclaim_midway
    try:
        run("imported_weights", holder)
        late_error = None
    except claim_module.ModelImportActivityError as exc:
        late_error = exc.code
    finally:
        p3.resolve_local_artifact = real_resolve
    row = job_row(race_job)
    check("Z", f"Z4 a takeover during the work fences the terminal write: {late_error}", late_error == "fenced_out" and row.state == "validating" and row.result_model_version_id is None)
    check("Z", "Z4 and the successful path left no half write", audit_rows(race_job) == [])

    # the artifact is revoked while the files are read: the terminal re-read catches it
    revoke_artifact = add_artifact("w-revoke", weights_files())
    revoke_job = create_job("imported_weights", revoke_artifact, project_a)
    token = claim("imported_weights", revoke_job, project_a)

    def revoke_midway(*a: Any, **k: Any) -> Any:
        result = real_resolve(*a, **k)
        with Session(owner_engine) as s:
            s.execute(text("UPDATE trainable_artifacts SET license_accepted_by = NULL, license_accepted_at = NULL, status = 'registered' WHERE id = :i"), {"i": str(revoke_artifact)})
            s.commit()
        return result

    before = version_count()
    p3.resolve_local_artifact = revoke_midway
    try:
        outcome = run("imported_weights", token)
    finally:
        p3.resolve_local_artifact = real_resolve
    check("Z", f"Z5 a license revoked mid-validation fails the job: {outcome.state}/{outcome.failure_code}", (outcome.state, outcome.failure_code) == ("failed", "artifact_changed_during_validation") and version_count() == before)

    # two workers validating the same claimed job: one terminal outcome, one version
    dup_artifact = add_artifact("w-dup", weights_files())
    dup_job = create_job("imported_weights", dup_artifact, project_a)
    token = claim("imported_weights", dup_job, project_a)
    results: list[Any] = []

    def worker() -> None:
        try:
            results.append(run("imported_weights", token))
        except claim_module.ModelImportActivityError as exc:
            results.append(exc.code)

    threads = [threading.Thread(target=worker) for _ in range(2)]
    before = version_count()
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    wins = [r for r in results if not isinstance(r, str)]
    check("Z", f"Z6 two concurrent validations of one job: {results}", len(wins) >= 1 and version_count() == before + 1 and len(audit_rows(dup_job)) == 1)

    # ---- L: no open transaction and no job lock while the files are read -----------------------
    lock_artifact = add_artifact("w-lock", weights_files())
    lock_job = create_job("imported_weights", lock_artifact, project_a)
    token = claim("imported_weights", lock_job, project_a)
    findings: list[str] = []

    def probe(*a: Any, **k: Any) -> Any:
        with Session(owner_engine) as s:
            busy = s.execute(
                text("SELECT count(*) FROM pg_stat_activity WHERE usename = :u AND state <> 'idle' AND pid <> pg_backend_pid()"),
                {"u": APP_ROLE_NAME},
            ).scalar_one()
            if busy:
                findings.append(f"{busy} sovra_app session(s) not idle")
            try:
                s.execute(text("SELECT id FROM model_import_jobs WHERE id = :id FOR UPDATE NOWAIT"), {"id": str(lock_job)})
            except DBAPIError:
                findings.append("the job row is locked")
            s.rollback()
        return real_resolve(*a, **k)

    p3.resolve_local_artifact = probe
    try:
        run("imported_weights", token)
    finally:
        p3.resolve_local_artifact = real_resolve
    check("L", f"L1 no transaction open and no lock held during the file reads: {findings}", findings == [])

    # ---- T: tenant isolation --------------------------------------------------------------------
    with Session(app_engine) as s:
        s.execute(text("SELECT set_config('app.current_organization_id', :o, true)"), {"o": str(org2_id)})
        seen_jobs = s.execute(text("SELECT count(*) FROM model_import_jobs")).scalar_one()
        seen_versions = s.execute(text("SELECT count(*) FROM model_versions")).scalar_one()
        seen_artifacts = s.execute(text("SELECT count(*) FROM trainable_artifacts")).scalar_one()
    check("T", f"T1 another tenant sees none of this tenant's rows ({seen_jobs}, {seen_versions}, {seen_artifacts})", (seen_jobs, seen_versions, seen_artifacts) == (0, 0, 0))
    try:
        create_job("imported_weights", base_artifact, project_g, org=org2_id)
        foreign = None
    except ArtifactNotFoundError:
        foreign = "not_found"
    check("T", f"T2 another tenant's job cannot name this tenant's artifact: {foreign}", foreign == "not_found")

    # ---- X: META-LEAK ---------------------------------------------------------------------------
    capture = _LogCapture()
    logging.getLogger().addHandler(capture)
    logging.getLogger().setLevel(logging.DEBUG)
    leak_artifact = add_artifact(f"dir-{MARK}", {**weights_files(), f"{MARK}.txt": b"x"})
    leak_job = create_job("imported_weights", leak_artifact, project_a)
    token = claim("imported_weights", leak_job, project_a)
    outcome = run("imported_weights", token)
    logging.getLogger().removeHandler(capture)
    row = job_row(leak_job)
    blob = json.dumps([outcome.failure_code, row.failure_code, [dict(r._mapping) for r in audit_rows(leak_job)], capture.lines], default=str)
    check("X", f"X1 a marker in a file or directory name stays out of the code, audit and logs ({outcome.failure_code})", outcome.failure_code == "unexpected_file" and MARK not in blob)

    if FAILURES:
        print(f"{len(FAILURES)} failing check(s):")
        for failure in FAILURES:
            print(f"  {failure}")
        sys.exit(1)
    print(f"all checks passed ({CID})")


if __name__ == "__main__":
    main()
