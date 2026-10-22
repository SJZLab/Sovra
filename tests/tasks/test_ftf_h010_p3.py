#!/usr/bin/env python3
# mypy: disable-error-code="arg-type,attr-defined,return-value,assignment,call-arg,index,misc,no-untyped-def,var-annotated,union-attr,operator,no-any-return"
# ruff: noqa: E501
"""FTF-H010-P3 -- full-weight and adapter import validation (no database server, no network).

The activity runs against an in-memory SQLite store with the tenant-session, audit and artifact
lookup seams replaced (the same split `test_ftf_h010_p2.py` uses) over REAL files in a real
approved-root directory: tiny safetensors fixtures are written with stdlib `struct` + `json`, the
FTF-0304 manifest and the FTF-0306 digest are the real resolvers'. The real PostgreSQL evidence
(RLS, the fence trigger, the FKs, the real route to a job) is
`tests/integration/test_ftf_h010_p3_weights_import.py`.

Criterion ids:
  A  weights: the success shape, and every step's failure code and terminal state (truth table)
  B  the safetensors header and file steps: allow-list look-alikes, hostile length, size drift
  C  adapters: exact parent, license, lineage, tensor shapes, tokenizer files, name-vs-parent
  D  argument and job validation (strict types, no coercion)
  E  fencing: a taken-over epoch writes nothing; the terminal transaction re-reads the artifact
  F  no DB session is open while the filesystem is read
  G  META-LEAK: markers in file names, directories and field values never reach a failure code, an
     audit row, an outcome or an exception chain
  H  the pickle attack is refused on the real default code and nothing is ever deserialized
  I  every fault in FAULTS turns the suite RED (fault-injected module copies)

Run directly: python3 tests/tasks/test_ftf_h010_p3.py
"""

from __future__ import annotations

import contextlib
import hashlib
import importlib.util
import json
import os
import struct
import sys
import tempfile
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "apps" / "api"))

from sqlalchemy import create_engine, event, select, text  # noqa: E402
from sqlalchemy.dialects.postgresql import JSONB  # noqa: E402
from sqlalchemy.ext.compiler import compiles  # noqa: E402
from sqlalchemy.orm import Session, attributes  # noqa: E402


@compiles(JSONB, "sqlite")
def _jsonb_as_json(element: Any, compiler: Any, **kw: Any) -> str:
    return "JSON"


import app.model_versions.model_import_validation as p2  # noqa: E402
import app.model_versions.model_version_crud as version_crud  # noqa: E402
from app.artifacts import artifact_schema  # noqa: E402
from app.artifacts.artifact_crud import ArtifactNotFoundError  # noqa: E402
from app.artifacts.base_model_revision_digest_resolver import canonical_weights_digest  # noqa: E402
from app.artifacts.local_artifact_resolver import resolve_local_artifact  # noqa: E402
from app.model_versions import model_import_schema as job_schema  # noqa: E402
from app.model_versions import model_version_schema as version_schema  # noqa: E402

CID = "FTF-H010-P3"
MODULE_PATH = (
    ROOT / "apps" / "api" / "app" / "model_versions" / "model_import_weights_validation.py"
)

# Under scripts/mutation_gate.py the production file is already mutated, so re-patching it with the
# FAULTS texts would count a mutant "killed" by a patch-text mismatch instead of by behaviour: the
# gate's kill signal must be the behavioural suite alone (same mode FTF-0904 / FTF-H010-P3a use).
UNDER_MUTATION_GATE = "mutation-gate-pyc" in os.environ.get("PYTHONPYCACHEPREFIX", "")
BEHAVIOUR_ONLY = os.environ.get("FTF_H010_P3_BEHAVIOUR_ONLY") == "1" or UNDER_MUTATION_GATE

_failures: list[str] = []
CHECK_COUNT = [0]


def check(criterion_id: str, description: str, condition: bool) -> None:
    CHECK_COUNT[0] += 1
    if not condition:
        _failures.append(f"FAIL [{criterion_id}] {description}")


def load_module(path: Path, name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


# -- fixtures ----------------------------------------------------------------------------------

NOW = datetime(2026, 10, 22, 12, 0, tzinfo=UTC)
ORG = uuid.UUID("aaaaaaaa-1111-4111-8111-11111111111a")
OTHER_ORG = uuid.UUID("bbbbbbbb-2222-4222-8222-22222222222b")
PROJECT = uuid.UUID("cccccccc-3333-4333-8333-33333333333c")
OTHER_PROJECT = uuid.UUID("dddddddd-4444-4444-8444-44444444444d")
WORKER = "worker-1"
REVISION = "a1b2c3d4e5f6"
MARK = "LEAK" + "MARK"

DTYPE_SIZE = {"F32": 4, "F16": 2, "I8": 1}
WEIGHTS = "weights"
ADAPTER = "imported_adapter"
FULL = "imported_weights"


@event.listens_for(job_schema.ModelImportJob, "load")
@event.listens_for(job_schema.ModelImportJob, "refresh")
def _aware(target: Any, *args: Any) -> None:
    value = target.lease_expires_at
    if value is not None and value.tzinfo is None:
        attributes.set_committed_value(target, "lease_expires_at", value.replace(tzinfo=UTC))


def safetensors_bytes(
    tensors: list[tuple[str, str, tuple[int, ...]]], metadata: dict[str, str] | None = None
) -> bytes:
    header: dict[str, Any] = {}
    offset = 0
    for name, dtype, shape in tensors:
        count = 1
        for dim in shape:
            count *= dim
        size = DTYPE_SIZE[dtype] * count
        header[name] = {
            "dtype": dtype,
            "shape": list(shape),
            "data_offsets": [offset, offset + size],
        }
        offset += size
    if metadata is not None:
        header["__metadata__"] = metadata
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
TOKENIZER_CONFIG = {"chat_template": "{{ messages }}", "eos_token": "</s>"}
BASE_TENSORS = [
    ("model.layers.0.q_proj.weight", "F32", (8, 4)),
    ("model.layers.0.v_proj.weight", "F32", (8, 4)),
]
ADAPTER_TENSORS = [
    ("base_model.model.model.layers.0.q_proj.lora_A.weight", "F32", (2, 4)),
    ("base_model.model.model.layers.0.q_proj.lora_B.weight", "F32", (8, 2)),
]
ADAPTER_CONFIG = {
    "peft_type": "LORA",
    "r": 2,
    "target_modules": ["q_proj"],
    "base_model_name_or_path": "any-name",
}


def jbytes(value: Any) -> bytes:
    return json.dumps(value).encode()


def write_files(root: Path, directory: str, files: dict[str, bytes]) -> None:
    base = root / directory
    base.mkdir(parents=True, exist_ok=True)
    for name, data in files.items():
        target = base / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)


def weights_files(**over: Any) -> dict[str, bytes]:
    files = {
        "config.json": jbytes(CONFIG),
        "tokenizer_config.json": jbytes(TOKENIZER_CONFIG),
        "tokenizer.json": b'{"version":"1"}',
        "model.safetensors": safetensors_bytes(BASE_TENSORS),
    }
    for name, data in over.items():
        key = name.replace("__", ".")
        if data is None:
            files.pop(key, None)
        else:
            files[key] = data
    return files


def adapter_files(**over: Any) -> dict[str, bytes]:
    files = {
        "adapter_config.json": jbytes(ADAPTER_CONFIG),
        "adapter_model.safetensors": safetensors_bytes(ADAPTER_TENSORS),
    }
    for name, data in over.items():
        key = name.replace("__", ".")
        if data is None:
            files.pop(key, None)
        else:
            files[key] = data
    return files


@dataclass
class Fixture:
    job_id: uuid.UUID
    token: Any
    artifact_id: uuid.UUID
    parent_id: uuid.UUID | None = None
    base_artifact_id: uuid.UUID | None = None


class Audit:
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    def __call__(self, session: Session, **kwargs: Any) -> None:
        self.events.append(kwargs)


class Env:
    """An in-memory store plus the replaced seams, bound to one (possibly fault-injected) module."""

    def __init__(self, m: Any) -> None:
        self.m = m
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.engine = create_engine("sqlite://")
        job_schema.ModelImportJob.__table__.create(self.engine)
        for table in (
            version_schema.ModelVersion.__table__,
            artifact_schema.TrainableArtifact.__table__,
        ):
            columns = list(table.c)
            defaults = [c.server_default for c in columns]
            for c in columns:
                c.server_default = None
            try:
                table.create(self.engine)
            finally:
                for c, d in zip(columns, defaults, strict=True):
                    c.server_default = d
        self.audit = Audit()
        self.factory = lambda: Session(self.engine)
        self.open_sessions = 0
        self.sessions_during_fs: list[int] = []
        self.counter = 0

    # -- seams -------------------------------------------------------------------------------

    @contextlib.contextmanager
    def tenant_session(self, session_factory: Any, context: Any):
        session = session_factory()
        self.open_sessions += 1
        try:
            yield session
            session.commit()
        except BaseException:
            session.rollback()
            raise
        finally:
            session.close()
            self.open_sessions -= 1

    def execute_scoped(
        self, session: Any, organization_id: Any, statement: Any, params: Any = None
    ) -> Any:
        return session.execute(statement)

    def get_artifact(self, session: Any, context: Any, artifact_id: Any) -> Any:
        row = session.get(artifact_schema.TrainableArtifact, artifact_id)
        if row is None or row.organization_id != context.organization_id:
            raise ArtifactNotFoundError("missing")
        return row

    @contextlib.contextmanager
    def patched(self):
        m = self.m
        names = {
            "tenant_scoped_session_for": self.tenant_session,
            "execute_tenant_scoped": self.execute_scoped,
            "write_audit_event": self.audit,
            "get_artifact": self.get_artifact,
        }
        saved = {name: getattr(m, name) for name in names}
        saved_p2 = (p2.execute_tenant_scoped, p2.tenant_scoped_session_for)
        saved_crud = version_crud.execute_tenant_scoped
        for name, value in names.items():
            setattr(m, name, value)
        p2.execute_tenant_scoped = self.execute_scoped
        p2.tenant_scoped_session_for = self.tenant_session
        version_crud.execute_tenant_scoped = self.execute_scoped
        try:
            yield self
        finally:
            for name, value in saved.items():
                setattr(m, name, value)
            p2.execute_tenant_scoped, p2.tenant_scoped_session_for = saved_p2
            version_crud.execute_tenant_scoped = saved_crud
            self.engine.dispose()
            self.tmp.cleanup()

    # -- data --------------------------------------------------------------------------------

    def add_artifact(
        self,
        directory: str,
        files: dict[str, bytes],
        *,
        status: str = "ready",
        accepted_by: str | None = "owner",
        accepted: bool = True,
        digest: str | None = None,
        family: str = "llama",
        revision: str = REVISION,
        source_type: str = "local_filesystem",
        source_uri: str | None = None,
        org: uuid.UUID = ORG,
        bypass_checks: bool = False,
    ) -> uuid.UUID:
        if files:
            write_files(self.root, directory, files)
        if digest is None and source_type == "local_filesystem" and files:
            manifest = resolve_local_artifact(self.root, directory)
            digest = canonical_weights_digest(manifest.manifest_sha256)
        elif digest is None:
            digest = "sha256:" + "d" * 64
        self.counter += 1
        artifact_id = uuid.uuid4()
        with Session(self.engine) as session:
            if bypass_checks:
                session.execute(text("PRAGMA ignore_check_constraints = ON"))
            session.add(
                artifact_schema.TrainableArtifact(
                    id=artifact_id,
                    organization_id=org,
                    display_name=f"artifact-{self.counter}",
                    source_type=source_type,
                    source_uri=source_uri if source_uri is not None else directory,
                    model_family=family,
                    base_model_revision=revision,
                    weights_digest=digest,
                    license_id="apache-2.0",
                    license_accepted_by=accepted_by if accepted else None,
                    license_accepted_at=NOW if accepted else None,
                    trust_remote_code_allowed=False,
                    required_python_packages_json=[],
                    minimum_gpu_profile_json={},
                    status=status,
                    created_at=NOW,
                    updated_at=NOW,
                )
            )
            session.commit()
            session.execute(text("PRAGMA ignore_check_constraints = OFF"))
        return artifact_id

    def add_job(
        self,
        origin: str,
        artifact_id: uuid.UUID | None,
        *,
        parent: uuid.UUID | None = None,
        state: str = "validating",
        epoch: int = 1,
        project: uuid.UUID = PROJECT,
    ) -> uuid.UUID:
        job_id = uuid.uuid4()
        with Session(self.engine) as session:
            session.add(
                job_schema.ModelImportJob(
                    id=job_id,
                    organization_id=ORG,
                    project_id=project,
                    requested_origin=origin,
                    parent_model_version_id=parent,
                    trainable_artifact_id=artifact_id,
                    state=state,
                    claim_epoch=epoch,
                    lease_expires_at=NOW + timedelta(hours=1) if state == "validating" else None,
                    result_model_version_id=None,
                    created_by="ml-1",
                    created_at=NOW,
                    updated_at=NOW,
                )
            )
            session.commit()
        return job_id

    def add_parent(
        self,
        artifact_id: uuid.UUID,
        *,
        origin: str = FULL,
        job_state: str | None = "succeeded",
        project: uuid.UUID = PROJECT,
    ) -> uuid.UUID:
        """A previously validated full-weight ModelVersion plus the succeeded job that made it."""
        with Session(self.engine) as session:
            artifact = session.get(artifact_schema.TrainableArtifact, artifact_id)
            version_id = uuid.uuid4()
            session.add(
                version_schema.ModelVersion(
                    id=version_id,
                    created_at=NOW,
                    organization_id=ORG,
                    project_id=project,
                    version_number=1,
                    import_kind="full_weight",
                    origin=origin,
                    trainable_artifact_id=artifact_id,
                    weights_digest=artifact.weights_digest,
                    base_model_revision=artifact.base_model_revision,
                    lineage_completeness="partial",
                    created_by="ml-1",
                )
            )
            session.commit()
        if job_state is not None:
            with Session(self.engine) as session:
                session.add(
                    job_schema.ModelImportJob(
                        id=uuid.uuid4(),
                        organization_id=ORG,
                        project_id=project,
                        requested_origin=FULL,
                        trainable_artifact_id=artifact_id,
                        state=job_state,
                        claim_epoch=1,
                        lease_expires_at=None,
                        failure_code="base_failure"
                        if job_state in ("failed", "blocked_external")
                        else None,
                        result_model_version_id=version_id if job_state == "succeeded" else None,
                        created_by="ml-1",
                        created_at=NOW,
                        updated_at=NOW,
                    )
                )
                session.commit()
        return version_id

    def weights_job(
        self, directory: str, files: dict[str, bytes], **artifact_kwargs: Any
    ) -> Fixture:
        artifact_id = self.add_artifact(directory, files, **artifact_kwargs)
        job_id = self.add_job(FULL, artifact_id)
        return Fixture(job_id, self.token(job_id), artifact_id)

    def adapter_job(
        self,
        files: dict[str, bytes] | None = None,
        *,
        base_files: dict[str, bytes] | None = None,
        base_kwargs: dict[str, Any] | None = None,
        adapter_kwargs: dict[str, Any] | None = None,
        parent_origin: str = FULL,
        parent_job_state: str | None = "succeeded",
        parent_project: uuid.UUID = PROJECT,
        with_parent: bool = True,
    ) -> Fixture:
        base_id = self.add_artifact(
            "base", base_files if base_files is not None else weights_files(), **(base_kwargs or {})
        )
        parent_id = self.add_parent(
            base_id, origin=parent_origin, job_state=parent_job_state, project=parent_project
        )
        akw = {"digest": None}
        akw.update(adapter_kwargs or {})
        artifact_id = self.add_artifact(
            "adapter", files if files is not None else adapter_files(), **akw
        )
        job_id = self.add_job(ADAPTER, artifact_id, parent=parent_id if with_parent else None)
        return Fixture(job_id, self.token(job_id), artifact_id, parent_id, base_id)

    def token(self, job_id: uuid.UUID, epoch: int = 1) -> Any:
        return p2.ClaimToken(ORG, PROJECT, job_id, epoch, WORKER)

    def job(self, job_id: uuid.UUID) -> Any:
        with Session(self.engine) as session:
            row = session.execute(
                select(job_schema.ModelImportJob).where(job_schema.ModelImportJob.id == job_id)
            ).scalar_one()
            session.expunge(row)
            return row

    def versions(self) -> list[Any]:
        with Session(self.engine) as session:
            rows = list(session.execute(select(version_schema.ModelVersion)).scalars())
            for row in rows:
                session.expunge(row)
            return rows

    def run(self, kind: str, fx: Fixture, **kw: Any) -> Any:
        fn = self.m.validate_weights_import if kind == FULL else self.m.validate_adapter_import
        kw.setdefault("approved_root", self.root)
        kw.setdefault("now", NOW)
        return fn(self.factory, kw.pop("token", fx.token), **kw)


def activity_error(fn: Any) -> str | None:
    try:
        fn()
    except p2.ModelImportActivityError as exc:
        return exc.code
    except BaseException as exc:  # noqa: BLE001
        return f"unexpected {type(exc).__name__}"
    return None


def outcome_pair(o: Any) -> tuple[str, str | None]:
    return o.state, o.failure_code


def expect_outcome(
    env: Env, kind: str, fx: Fixture, label: str, state: str, code: str | None
) -> Any:
    outcome = env.run(kind, fx)
    check(
        "A",
        f"{label}: ({state}, {code}) got {outcome_pair(outcome)}",
        outcome_pair(outcome) == (state, code),
    )
    job = env.job(fx.job_id)
    check(
        "A", f"{label}: job row ends {state}/{code}", (job.state, job.failure_code) == (state, code)
    )
    check("A", f"{label}: lease cleared", job.lease_expires_at is None)
    if state != "succeeded":
        check(
            "A",
            f"{label}: no model version written",
            env.versions() == [] or all(v.id == fx.parent_id for v in env.versions()),
        )
        check(
            "A",
            f"{label}: result id empty",
            outcome.model_version_id is None and job.result_model_version_id is None,
        )
    return outcome


# -- groups ----------------------------------------------------------------------------------------


def group_a(m: Any) -> None:
    """Weights: success shape and the failure table."""
    with Env(m).patched() as env:
        fx = env.weights_job("w", weights_files())
        outcome = expect_outcome(env, FULL, fx, "A1 success", "succeeded", None)
        versions = env.versions()
        check("A", "A1: exactly one ModelVersion", len(versions) == 1)
        v = versions[0]
        artifact = None
        with Session(env.engine) as s:
            artifact = s.get(artifact_schema.TrainableArtifact, fx.artifact_id)
            digest, revision = artifact.weights_digest, artifact.base_model_revision
        check("A", "A1: result id is the version", outcome.model_version_id == v.id)
        check(
            "A",
            "A1: import_kind full_weight, origin imported_weights",
            (v.import_kind, v.origin) == ("full_weight", FULL),
        )
        check("A", "A1: artifact id bound", v.trainable_artifact_id == fx.artifact_id)
        check("A", "A1: digest is the re-verified one", v.weights_digest == digest)
        check("A", "A1: base_model_revision from the artifact", v.base_model_revision == revision)
        check(
            "A",
            "A1: tokenizer_revision = the artifact's base revision",
            v.tokenizer_revision == revision,
        )
        check(
            "A",
            "A1: chat_template_sha256 = sha256 of the exact UTF-8 template",
            v.chat_template_sha256
            == hashlib.sha256(TOKENIZER_CONFIG["chat_template"].encode()).hexdigest(),
        )
        check("A", "A1: serving_config_sha256 NULL until FTF-1003", v.serving_config_sha256 is None)
        check("A", "A1: not adapter_only", v.adapter_only is False)
        check("A", "A1: lineage partial, never complete", v.lineage_completeness == "partial")
        check("A", "A1: no declared parent", v.parent_model_version_id is None)
        check("A", "A1: created_by is the job's creator", v.created_by == "ml-1")
        check(
            "A", "A1: project and organization", (v.project_id, v.organization_id) == (PROJECT, ORG)
        )
        check("A", "A1: version number 1", v.version_number == 1)
        job = env.job(fx.job_id)
        check("A", "A1: job result id", job.result_model_version_id == v.id)
        check("A", "A1: exactly one audit row", len(env.audit.events) == 1)
        event_ = env.audit.events[0]
        check(
            "A",
            "A1: audit action, resource and details",
            event_["action"] == "model_import.validate"
            and event_["resource_id"] == str(fx.job_id)
            and event_["details"]
            == {"job_id": str(fx.job_id), "origin": FULL, "outcome": "succeeded"}
            and event_["actor_type"] == "system"
            and event_["actor_id"] == WORKER
            and event_["organization_id"] == ORG,
        )

    # a declared parent on a weights job is kept as declared
    with Env(m).patched() as env:
        parent_artifact = env.add_artifact(
            "p", weights_files(config__json=jbytes({**CONFIG, "vocab_size": 17}))
        )
        parent_id = env.add_parent(parent_artifact)
        artifact_id = env.add_artifact("w", weights_files())
        job_id = env.add_job(FULL, artifact_id, parent=parent_id)
        fx = Fixture(job_id, env.token(job_id), artifact_id)
        env.run(FULL, fx)
        newest = [v for v in env.versions() if v.id != parent_id]
        check(
            "A",
            "A2: the declared parent is kept for a weights import",
            len(newest) == 1 and newest[0].parent_model_version_id == parent_id,
        )
        check("A", "A2: and it is the second version number", newest[0].version_number == 2)

    # step 1: artifact ready, resolved digest
    for label, kwargs in (
        ("A3 registered artifact", {"status": "registered"}),
        ("A3 archived artifact", {"status": "archived"}),
    ):
        with Env(m).patched() as env:
            fx = env.weights_job("w", weights_files(), **kwargs)
            expect_outcome(env, FULL, fx, label, "failed", "artifact_not_ready")
    with Env(m).patched() as env:
        artifact_id = env.add_artifact("w", weights_files())
        with Session(env.engine) as s:
            s.get(artifact_schema.TrainableArtifact, artifact_id).weights_digest = None
            s.commit()
        job_id = env.add_job(FULL, artifact_id)
        expect_outcome(
            env,
            FULL,
            Fixture(job_id, env.token(job_id), artifact_id),
            "A3 ready but no digest",
            "failed",
            "artifact_not_ready",
        )
    with Env(m).patched() as env:
        artifact_id = env.add_artifact("w", weights_files())
        with Session(env.engine) as s:
            s.get(artifact_schema.TrainableArtifact, artifact_id).weights_digest = (
                "sha256:" + "A" * 64
            )
            s.commit()
        job_id = env.add_job(FULL, artifact_id)
        expect_outcome(
            env,
            FULL,
            Fixture(job_id, env.token(job_id), artifact_id),
            "A3 malformed digest text",
            "failed",
            "artifact_not_ready",
        )

    # step 2: license
    with Env(m).patched() as env:
        fx = env.weights_job("w", weights_files(), accepted_by=None)
        expect_outcome(env, FULL, fx, "A4 license not accepted", "failed", "license_not_accepted")
    with Env(m).patched() as env:
        fx = env.weights_job("w", weights_files(), accepted=False, status="registered")
        expect_outcome(
            env,
            FULL,
            fx,
            "A4 unaccepted registered stays artifact_not_ready first",
            "failed",
            "artifact_not_ready",
        )

    # step 3: sources that cannot be read here
    with Env(m).patched() as env:
        fx = env.weights_job("w", {}, source_type="private_s3", source_uri="s3://bucket/key")
        expect_outcome(
            env, FULL, fx, "A5 private_s3", "blocked_external", "metadata_files_unavailable"
        )
    with Env(m).patched() as env:
        fx = env.weights_job("w", {}, source_type="huggingface", source_uri="acme/model")
        expect_outcome(
            env, FULL, fx, "A5 huggingface", "blocked_external", "metadata_files_unavailable"
        )
    with Env(m).patched() as env:
        fx = env.weights_job("w", weights_files())
        outcome = env.run(FULL, fx, approved_root=env.root / "does-not-exist")
        check(
            "A",
            f"A6 approved root missing -> blocked artifact_source_unavailable, got {outcome_pair(outcome)}",
            outcome_pair(outcome) == ("blocked_external", "artifact_source_unavailable"),
        )
    with Env(m).patched() as env:
        fx = env.weights_job("w", weights_files())
        (env.root / "w" / "extra.txt").write_text("late")
        expect_outcome(
            env,
            FULL,
            fx,
            "A7 digest drift (file added after the artifact was resolved)",
            "failed",
            "digest_mismatch",
        )
    with Env(m).patched() as env:
        fx = env.weights_job("w", weights_files())
        (env.root / "w" / "config.json").write_bytes(jbytes({**CONFIG, "hidden_size": 5}))
        expect_outcome(
            env, FULL, fx, "A7 digest drift (a byte changed)", "failed", "digest_mismatch"
        )
    with Env(m).patched() as env:
        fx = env.weights_job("w", weights_files(), digest="sha256:" + "0" * 64)
        expect_outcome(
            env, FULL, fx, "A7 the stored digest is never trusted", "failed", "digest_mismatch"
        )
    with Env(m).patched() as env:
        fx = env.weights_job("w", weights_files())
        (env.root / "w" / "model.safetensors").chmod(0o755)
        expect_outcome(
            env,
            FULL,
            fx,
            "A8 an executable file is refused by the resolver",
            "failed",
            "artifact_source_invalid",
        )
    with Env(m).patched() as env:
        fx = env.weights_job("w", weights_files())
        outside = env.root / "outside.json"
        outside.write_bytes(b"{}")
        os.symlink(outside, env.root / "w" / "link.json")
        expect_outcome(
            env,
            FULL,
            fx,
            "A8 a symlink inside the artifact is refused",
            "failed",
            "artifact_source_invalid",
        )
    with Env(m).patched() as env:
        fx = env.weights_job("w", weights_files())
        with Session(env.engine) as s:
            s.get(artifact_schema.TrainableArtifact, fx.artifact_id).source_uri = "../w"
            s.commit()
        expect_outcome(
            env,
            FULL,
            fx,
            "A8 a traversal source_uri is refused",
            "failed",
            "artifact_source_invalid",
        )

    # step 4: allow-list (exact, never a deny-list)
    allow_cases = [
        ("notes.txt", "unexpected_file"),
        (".env", "unexpected_file"),
        ("Config.JSON", "unexpected_file"),
        ("config.json ", "unexpected_file"),
        ("model.SAFETENSORS", "unexpected_file"),
        ("model.safetensors.bak", "unexpected_file"),
        ("a.b.safetensors", "unexpected_file"),
        ("sub/model-2.safetensors", "unexpected_file"),
        ("weights.bin", "unsafe_weight_format"),
        ("weights.PT", "unsafe_weight_format"),
        ("weights.PKL", "unsafe_weight_format"),
        ("model.joblib", "unsafe_weight_format"),
        ("model.npy", "unsafe_weight_format"),
        ("pytorch_model.bin", "unsafe_weight_format"),
    ]
    for name, code in allow_cases:
        with Env(m).patched() as env:
            files = weights_files()
            files[name] = b"x"
            fx = env.weights_job("w", files)
            expect_outcome(env, FULL, fx, f"A9 extra {name!r}", "failed", code)
    for name in ("config.json", "tokenizer_config.json", "model.safetensors"):
        with Env(m).patched() as env:
            fx = env.weights_job("w", weights_files(**{name.replace(".", "__"): None}))
            expect_outcome(env, FULL, fx, f"A10 missing {name}", "failed", "required_file_missing")
    with Env(m).patched() as env:
        fx = env.weights_job("w", weights_files(tokenizer__json=None))
        expect_outcome(
            env,
            FULL,
            fx,
            "A10 neither tokenizer.json nor tokenizer.model",
            "failed",
            "required_file_missing",
        )
    with Env(m).patched() as env:
        files = weights_files(tokenizer__json=None)
        files["tokenizer.model"] = b"spm"
        files["model.safetensors.index.json"] = b"{}"
        files["generation_config.json"] = b"{}"
        files["README.md"] = b"hello"
        fx = env.weights_job("w", files)
        expect_outcome(
            env,
            FULL,
            fx,
            "A11 tokenizer.model, the index and named optional files are allowed",
            "succeeded",
            None,
        )
    with Env(m).patched() as env:
        files = weights_files()
        files["model-00001-of-00002.safetensors"] = files.pop("model.safetensors")
        files.pop("model-00001-of-00002.safetensors")
        files["shard_1.safetensors"] = safetensors_bytes([BASE_TENSORS[0]])
        files["shard_2.safetensors"] = safetensors_bytes([BASE_TENSORS[1]])
        fx = env.weights_job("w", files)
        expect_outcome(env, FULL, fx, "A11 two shards", "succeeded", None)

    # step 6: strict parsing
    parse_cases = [
        ("config_invalid", weights_files(config__json=jbytes({**CONFIG, "hidden_size": 0}))),
        ("config_invalid", weights_files(config__json=jbytes({**CONFIG, "hidden_size": True}))),
        ("config_invalid", weights_files(config__json=b'{"model_type":"a","model_type":"b"}')),
        ("config_invalid", weights_files(config__json=b'{"model_type": NaN}')),
        ("config_invalid", weights_files(config__json=b"[1]")),
        ("config_invalid", weights_files(config__json=b"\xff\xfe")),
        ("config_invalid", weights_files(config__json=b" " * ((1 << 20) + 1))),
        (
            "quantization_unverified",
            weights_files(config__json=jbytes({**CONFIG, "quantization_config": {"bits": 4}})),
        ),
        (
            "tokenizer_config_invalid",
            weights_files(tokenizer_config__json=jbytes({"chat_template": 5})),
        ),
        (
            "tokenizer_config_invalid",
            weights_files(tokenizer_config__json=jbytes({"eos_token": 7})),
        ),
        ("tokenizer_config_invalid", weights_files(tokenizer_config__json=b" " * ((4 << 20) + 1))),
    ]
    for index, (code, files) in enumerate(parse_cases):
        with Env(m).patched() as env:
            fx = env.weights_job("w", files)
            expect_outcome(env, FULL, fx, f"A12.{index} {code}", "failed", code)
    with Env(m).patched() as env:
        fx = env.weights_job(
            "w", weights_files(tokenizer_config__json=jbytes({"eos_token": "</s>"}))
        )
        outcome = env.run(FULL, fx)
        v = env.versions()
        check(
            "A",
            "A13: no chat template -> chat_template_sha256 NULL",
            outcome.state == "succeeded" and v[0].chat_template_sha256 is None,
        )


def group_b(m: Any) -> None:
    """The safetensors header and the file-read steps."""
    st = safetensors_bytes
    bad_headers: list[tuple[str, bytes]] = []
    good = st(BASE_TENSORS)
    n = struct.unpack("<Q", good[:8])[0]
    bad_headers.append(("N = 0", struct.pack("<Q", 0) + good[8:]))
    bad_headers.append(("N one past the file", struct.pack("<Q", len(good) - 7) + good[8:]))
    bad_headers.append(("N = 100,000,001", struct.pack("<Q", 100_000_001) + good[8:]))
    bad_headers.append(("N huge", struct.pack("<Q", 2**63) + good[8:]))
    bad_headers.append(("truncated below 8 bytes", good[:5]))
    bad_headers.append(("not an object", struct.pack("<Q", 2) + b"[]" + b""))
    bad_headers.append(("truncated data", good[:-1]))
    bad_headers.append(("trailing data", good + b"\0"))
    raw = good[8 : 8 + n].decode()
    for label, text_ in (
        ("lower-case dtype", raw.replace('"F32"', '"f32"', 1)),
        ("unknown dtype", raw.replace('"F32"', '"F33"', 1)),
        ("bool shape entry", raw.replace("[8,4]", "[true,4]", 1)),
        ("negative shape entry", raw.replace("[8,4]", "[-8,4]", 1)),
        ("float shape entry", raw.replace("[8,4]", "[8.0,4]", 1)),
        ("end before begin", raw.replace("[0,128]", "[128,0]", 1)),
        ("gap in the offsets", raw.replace("[128,256]", "[130,258]", 1)),
        ("overlapping offsets", raw.replace("[128,256]", "[64,192]", 1)),
        ("non-str metadata", raw[:-1] + ',"__metadata__":{"k":1}}'),
    ):
        body = text_.encode()
        bad_headers.append((label, struct.pack("<Q", len(body)) + body + good[8 + n :]))
    dup = b'{"a":{"dtype":"F32","shape":[1],"data_offsets":[0,4]},"a":{"dtype":"F32","shape":[1],"data_offsets":[4,8]}}'
    bad_headers.append(
        (
            "duplicate tensor names (duplicate JSON keys)",
            struct.pack("<Q", len(dup)) + dup + bytes(8),
        )
    )
    for label, blob in bad_headers:
        with Env(m).patched() as env:
            fx = env.weights_job("w", weights_files(model__safetensors=blob))
            expect_outcome(env, FULL, fx, f"B1 {label}", "failed", "weights_header_invalid")

    # a header whose declared length is huge is never read in full
    with Env(m).patched() as env:
        fx = env.weights_job(
            "w", weights_files(model__safetensors=struct.pack("<Q", 99_000_000) + b"{" + bytes(64))
        )
        reads: list[int] = []
        real_fdopen = os.fdopen

        class Spy:
            def __init__(self, handle: Any) -> None:
                self.handle = handle

            def __enter__(self) -> Any:
                self.handle.__enter__()
                return self

            def __exit__(self, *exc: Any) -> Any:
                return self.handle.__exit__(*exc)

            def read(self, count: int = -1) -> bytes:
                reads.append(count)
                return self.handle.read(count)

            def fileno(self) -> int:
                return self.handle.fileno()

            def __getattr__(self, name: str) -> Any:
                return getattr(self.handle, name)

        os.fdopen = lambda *a, **k: Spy(real_fdopen(*a, **k))
        try:
            outcome = env.run(FULL, fx)
        finally:
            os.fdopen = real_fdopen
        check(
            "B",
            f"B2 an oversize declared length is refused ({outcome_pair(outcome)})",
            outcome_pair(outcome) == ("failed", "weights_header_invalid"),
        )
        check(
            "B",
            f"B2 ... without ever reading it (largest read {max(reads, default=0)}, {len(reads)} reads)",
            0 < max(reads, default=0) <= (4 << 20) + 1,
        )

    # the file changes between two passes
    def swap_after(
        env: Env,
        name: str,
        rewrite: Any,
        label: str,
        when: str,
        expected: tuple[str, str] = ("failed", "artifact_changed_during_validation"),
    ) -> None:
        fx = env.weights_job("w", weights_files())
        real = getattr(env.m, name)

        def wrapper(*a: Any, **k: Any) -> Any:
            result = real(*a, **k)
            rewrite(env.root / "w")
            return result

        setattr(env.m, name, wrapper)
        try:
            outcome = env.run(FULL, fx)
        finally:
            setattr(env.m, name, real)
        check("B", f"{label}: ({outcome_pair(outcome)})", outcome_pair(outcome) == expected)
        check("B", f"{label}: no version", env.versions() == [])

    with Env(m).patched() as env:
        swap_after(
            env,
            "verify_or_compute_weights_digest",
            lambda d: (d / "tokenizer.json").write_bytes(b'{"version":"2"}'),
            "B3 rewritten between the digest pass and the manifest pass",
            "digest",
        )
    with Env(m).patched() as env:
        swap_after(
            env,
            "resolve_local_artifact",
            lambda d: (d / "config.json").write_bytes(jbytes({**CONFIG, "hidden_size": 6})),
            "B3 config.json rewritten (valid, same size) after the manifest",
            "manifest",
        )
    with Env(m).patched() as env:
        swap_after(
            env,
            "resolve_local_artifact",
            lambda d: (d / "tokenizer_config.json").write_bytes(jbytes({"eos_token": "x"})),
            "B3 tokenizer_config.json rewritten after the manifest",
            "manifest",
        )
    with Env(m).patched() as env:
        swap_after(
            env,
            "resolve_local_artifact",
            lambda d: (d / "model.safetensors").write_bytes(
                safetensors_bytes(BASE_TENSORS) + b"\0" * 8
            ),
            "B3 a shard changes size after the manifest",
            "manifest",
        )
    with Env(m).patched() as env:
        swap_after(
            env,
            "resolve_local_artifact",
            lambda d: (d / "model.safetensors").unlink(),
            "B3 a shard vanishes after the manifest (blocked as unreadable, never a crash)",
            "manifest",
            ("blocked_external", "artifact_source_unavailable"),
        )

    # a file replaced with a symlink after the manifest is never followed
    with Env(m).patched() as env:
        fx = env.weights_job("w", weights_files())
        real = env.m.resolve_local_artifact
        target = env.root / "elsewhere.json"
        target.write_bytes(jbytes(CONFIG))

        def link_it(*a: Any, **k: Any) -> Any:
            result = real(*a, **k)
            (env.root / "w" / "config.json").unlink()
            os.symlink(target, env.root / "w" / "config.json")
            return result

        env.m.resolve_local_artifact = link_it
        try:
            outcome = env.run(FULL, fx)
        finally:
            env.m.resolve_local_artifact = real
        check(
            "B",
            f"B4 a symlink swapped in after the manifest is not followed ({outcome_pair(outcome)})",
            outcome.state != "succeeded" and env.versions() == [],
        )


def group_c(m: Any) -> None:
    """Adapters."""
    with Env(m).patched() as env:
        fx = env.adapter_job()
        expect_outcome(env, ADAPTER, fx, "C1 success", "succeeded", None)
        versions = [v for v in env.versions() if v.id != fx.parent_id and v.origin == ADAPTER]
        check("C", "C1: one adapter ModelVersion", len(versions) == 1)
        v = versions[0]
        with Session(env.engine) as s:
            artifact = s.get(artifact_schema.TrainableArtifact, fx.artifact_id)
            digest = artifact.weights_digest
        check(
            "C",
            "C1: import_kind adapter / origin imported_adapter / adapter_only",
            (v.import_kind, v.origin, v.adapter_only) == ("adapter", ADAPTER, True),
        )
        check("C", "C1: parent is the declared base", v.parent_model_version_id == fx.parent_id)
        check(
            "C",
            "C1: digest, revision, tokenizer revision",
            (v.weights_digest, v.base_model_revision, v.tokenizer_revision)
            == (digest, REVISION, REVISION),
        )
        check(
            "C",
            "C1: no chat template, no serving config",
            (v.chat_template_sha256, v.serving_config_sha256) == (None, None),
        )
        check("C", "C1: lineage partial", v.lineage_completeness == "partial")
        check(
            "C",
            "C1: the parent row is untouched",
            [x for x in env.versions() if x.id == fx.parent_id][0].lineage_completeness
            == "partial",
        )
        check("C", "C1: audit origin", env.audit.events[-1]["details"]["origin"] == ADAPTER)

    # base resolution
    cases: list[tuple[str, dict[str, Any], str, str]] = [
        ("C2 no parent on the job", {"with_parent": False}, "failed", "job_invalid"),
        (
            "C2 parent produced by a factory run",
            {"parent_origin": "factory_trained"},
            "failed",
            "base_not_validated",
        ),
        (
            "C2 parent never ran an import job",
            {"parent_job_state": None},
            "failed",
            "base_not_validated",
        ),
        (
            "C2 parent's import job failed",
            {"parent_job_state": "failed"},
            "failed",
            "base_not_validated",
        ),
        (
            "C2 parent in another project",
            {"parent_project": OTHER_PROJECT},
            "failed",
            "base_not_validated",
        ),
        (
            "C3 base license revoked",
            {"base_kwargs": {"accepted_by": None}},
            "failed",
            "base_license_not_accepted",
        ),
        (
            "C3 adapter license not accepted",
            {"adapter_kwargs": {"accepted_by": None}},
            "failed",
            "license_not_accepted",
        ),
        (
            "C4 base revision differs (never a same-family guess)",
            {"adapter_kwargs": {"revision": "ffffffffffff"}},
            "failed",
            "adapter_base_mismatch",
        ),
        (
            "C4 base revision case variant",
            {"adapter_kwargs": {"revision": REVISION.upper()}},
            "failed",
            "adapter_base_mismatch",
        ),
        (
            "C4 base revision whitespace variant",
            {"adapter_kwargs": {"revision": REVISION + " "}},
            "failed",
            "adapter_base_mismatch",
        ),
        (
            "C4 base revision zero-width variant",
            {"adapter_kwargs": {"revision": REVISION + "​"}},
            "failed",
            "adapter_base_mismatch",
        ),
        (
            "C4 different model family",
            {"adapter_kwargs": {"family": "mistral"}},
            "failed",
            "adapter_base_mismatch",
        ),
    ]
    for label, kwargs, state, code in cases:
        with Env(m).patched() as env:
            if code == "job_invalid":
                fx = env.adapter_job(**kwargs)
                got = activity_error(lambda env=env, fx=fx: env.run(ADAPTER, fx))
                check("C", f"{label}: error {got}", got == "job_invalid")
                check(
                    "C",
                    f"{label}: nothing written",
                    [v.id for v in env.versions()] == [fx.parent_id]
                    and env.audit.events == []
                    and env.job(fx.job_id).state == "validating",
                )
            else:
                fx = env.adapter_job(**kwargs)
                expect_outcome(env, ADAPTER, fx, label, state, code)

    # the base artifact drifted on disk
    with Env(m).patched() as env:
        fx = env.adapter_job()
        (env.root / "base" / "extra.txt").write_text("late")
        expect_outcome(
            env,
            ADAPTER,
            fx,
            "C5 base files changed after the base was resolved",
            "failed",
            "digest_mismatch",
        )
    with Env(m).patched() as env:
        fx = env.adapter_job(
            base_kwargs={"status": "registered", "accepted": False, "accepted_by": None}
        )
        expect_outcome(
            env,
            ADAPTER,
            fx,
            "C5 base artifact not ready (and unlicensed)",
            "failed",
            "base_license_not_accepted",
        )

    # tensors against the real base headers
    adapter_cases: list[tuple[str, dict[str, bytes], dict[str, bytes] | None, str]] = [
        (
            "C6 r differs from the config",
            adapter_files(adapter_config__json=jbytes({**ADAPTER_CONFIG, "r": 3})),
            None,
            "adapter_base_incompatible",
        ),
        (
            "C6 in/out swapped",
            adapter_files(
                adapter_model__safetensors=safetensors_bytes(
                    [
                        ("base_model.model.model.layers.0.q_proj.lora_A.weight", "F32", (2, 8)),
                        ("base_model.model.model.layers.0.q_proj.lora_B.weight", "F32", (4, 2)),
                    ]
                )
            ),
            None,
            "adapter_base_incompatible",
        ),
        (
            "C6 the base lacks the module",
            adapter_files(
                adapter_model__safetensors=safetensors_bytes(
                    [
                        ("base_model.model.model.layers.1.q_proj.lora_A.weight", "F32", (2, 4)),
                        ("base_model.model.model.layers.1.q_proj.lora_B.weight", "F32", (8, 2)),
                    ]
                )
            ),
            None,
            "adapter_base_incompatible",
        ),
        (
            "C6 a target module without tensors",
            adapter_files(
                adapter_config__json=jbytes(
                    {**ADAPTER_CONFIG, "target_modules": ["q_proj", "v_proj"]}
                )
            ),
            None,
            "adapter_base_incompatible",
        ),
        (
            "C6 a module that is not targeted",
            adapter_files(
                adapter_config__json=jbytes({**ADAPTER_CONFIG, "target_modules": ["v_proj"]})
            ),
            None,
            "adapter_base_incompatible",
        ),
        (
            "C6 a non-lora tensor",
            adapter_files(
                adapter_model__safetensors=safetensors_bytes([("extra.weight", "F32", (1,))])
            ),
            None,
            "adapter_base_incompatible",
        ),
        (
            "C7 peft_type is not LORA",
            adapter_files(adapter_config__json=jbytes({**ADAPTER_CONFIG, "peft_type": "lora"})),
            None,
            "adapter_config_invalid",
        ),
        (
            "C7 missing adapter_config",
            adapter_files(adapter_config__json=None),
            None,
            "required_file_missing",
        ),
        (
            "C7 missing adapter weights",
            adapter_files(adapter_model__safetensors=None),
            None,
            "required_file_missing",
        ),
        (
            "C7 a pickle adapter",
            {**adapter_files(), "adapter_model.bin": b"x"},
            None,
            "unsafe_weight_format",
        ),
        (
            "C7 an adapter with a full weights file",
            {**adapter_files(), "model.safetensors": b"x"},
            None,
            "unexpected_file",
        ),
        (
            "C7 corrupt adapter header",
            adapter_files(adapter_model__safetensors=b"\x01\x02"),
            None,
            "weights_header_invalid",
        ),
        (
            "C8 adapter tokenizer differs from the base's",
            {**adapter_files(), "tokenizer.json": b'{"version":"9"}'},
            None,
            "tokenizer_mismatch",
        ),
        (
            "C8 adapter ships a tokenizer file the base lacks",
            {**adapter_files(), "vocab.json": b"{}"},
            None,
            "tokenizer_mismatch",
        ),
    ]
    for label, files, base_files, code in adapter_cases:
        with Env(m).patched() as env:
            fx = env.adapter_job(files, base_files=base_files)
            expect_outcome(env, ADAPTER, fx, label, "failed", code)
    with Env(m).patched() as env:
        fx = env.adapter_job(
            {**adapter_files(), "tokenizer.json": b'{"version":"1"}', "README.md": b"x"}
        )
        expect_outcome(
            env, ADAPTER, fx, "C8 identical tokenizer files are accepted", "succeeded", None
        )

    # the name in the adapter config never resolves the base
    with Env(m).patched() as env:
        fx = env.adapter_job(
            adapter_files(
                adapter_config__json=jbytes(
                    {**ADAPTER_CONFIG, "base_model_name_or_path": "totally/other-model"}
                )
            )
        )
        expect_outcome(
            env,
            ADAPTER,
            fx,
            "C9 a config naming another model while the true parent is compatible is accepted",
            "succeeded",
            None,
        )
    with Env(m).patched() as env:
        incompatible_base = weights_files(
            model__safetensors=safetensors_bytes([("model.layers.0.q_proj.weight", "F32", (9, 4))])
        )
        fx = env.adapter_job(
            adapter_files(
                adapter_config__json=jbytes({**ADAPTER_CONFIG, "base_model_name_or_path": "base"})
            ),
            base_files=incompatible_base,
        )
        expect_outcome(
            env,
            ADAPTER,
            fx,
            "C9 a config naming the right model while the true parent is incompatible is refused",
            "failed",
            "adapter_base_incompatible",
        )

    # an adapter job against the weights entry point (and the reverse) is the wrong origin
    with Env(m).patched() as env:
        fx = env.adapter_job()
        got = activity_error(lambda: env.run(FULL, fx))
        check(
            "C",
            f"C10 an adapter job through validate_weights_import: {got}",
            got == "origin_not_claimable",
        )
    with Env(m).patched() as env:
        fx = env.weights_job("w", weights_files())
        got = activity_error(lambda: env.run(ADAPTER, fx))
        check(
            "C",
            f"C10 a weights job through validate_adapter_import: {got}",
            got == "origin_not_claimable",
        )


def group_d(m: Any) -> None:
    with Env(m).patched() as env:
        fx = env.weights_job("w", weights_files())
        for label, kw in (
            ("token is not a ClaimToken", {"token": object()}),
            ("token is None", {"token": None}),
            ("approved_root is bytes", {"approved_root": b"/tmp"}),
            ("approved_root is empty", {"approved_root": ""}),
            ("approved_root is None", {"approved_root": None}),
            ("now is naive", {"now": datetime(2026, 1, 1)}),
            ("now is a string", {"now": "2026-01-01"}),
            ("now is None", {"now": None}),
        ):
            got = activity_error(lambda kw=kw: env.run(FULL, fx, **kw))
            check("D", f"D1 {label}: {got}", got == "invalid_argument")
        check(
            "D",
            "D1: nothing written by any invalid call",
            env.job(fx.job_id).state == "validating"
            and env.audit.events == []
            and env.versions() == [],
        )
        outcome = env.run(FULL, fx, approved_root=str(env.root))
        check("D", "D2 a str approved_root is accepted", outcome.state == "succeeded")
    with Env(m).patched() as env:
        got = activity_error(
            lambda: env.m.validate_weights_import(
                env.factory, env.token(uuid.uuid4()), approved_root=env.root, now=NOW
            )
        )
        check("D", f"D3 unknown job: {got}", got == "job_not_found")
    with Env(m).patched() as env:
        job_id = env.add_job(FULL, None)
        got = activity_error(
            lambda: env.m.validate_weights_import(
                env.factory, env.token(job_id), approved_root=env.root, now=NOW
            )
        )
        check("D", f"D4 a weights job with no artifact: {got}", got == "origin_not_claimable")
    with Env(m).patched() as env:
        job_id = env.add_job(FULL, uuid.uuid4())
        got = activity_error(
            lambda: env.m.validate_weights_import(
                env.factory, env.token(job_id), approved_root=env.root, now=NOW
            )
        )
        check("D", f"D5 an artifact that does not exist: {got}", got == "job_invalid")
    with Env(m).patched() as env:
        foreign = env.add_artifact("w", weights_files(), org=OTHER_ORG)
        job_id = env.add_job(FULL, foreign)
        got = activity_error(
            lambda: env.m.validate_weights_import(
                env.factory, env.token(job_id), approved_root=env.root, now=NOW
            )
        )
        check(
            "D",
            f"D6 another organization's artifact is identical to a missing one: {got}",
            got == "job_invalid",
        )
    for state in ("queued", "succeeded", "failed", "blocked_external"):
        with Env(m).patched() as env:
            fx = env.weights_job("w", weights_files())
            with Session(env.engine) as s:
                job = s.get(job_schema.ModelImportJob, fx.job_id)
                s.execute(text("PRAGMA ignore_check_constraints = ON"))
                job.state = state
                job.lease_expires_at = None
                if state == "queued":
                    job.claim_epoch = 0
                s.commit()
                s.execute(text("PRAGMA ignore_check_constraints = OFF"))
            got = activity_error(lambda env=env, fx=fx: env.run(FULL, fx))
            check("D", f"D7 a job in state {state} is fenced out: {got}", got == "fenced_out")
            check(
                "D", f"D7 {state}: nothing written", env.versions() == [] and env.audit.events == []
            )
    with Env(m).patched() as env:
        fx = env.weights_job("w", weights_files())
        got = activity_error(lambda: env.run(FULL, fx, token=env.token(fx.job_id, epoch=2)))
        check(
            "D",
            f"D8 a token from a different epoch is fenced out before any work: {got}",
            got == "fenced_out",
        )
    with Env(m).patched() as env:
        fx = env.weights_job("w", weights_files())
        other = p2.ClaimToken(ORG, OTHER_PROJECT, fx.job_id, 1, WORKER)
        got = activity_error(lambda: env.run(FULL, fx, token=other))
        check(
            "D",
            f"D9 the wrong project is identical to a missing job: {got}",
            got == "job_not_found",
        )


def group_e(m: Any) -> None:
    """Fencing and the terminal re-read."""
    with Env(m).patched() as env:
        fx = env.weights_job("w", weights_files())
        real = env.m.verify_or_compute_weights_digest

        def take_over(*a: Any, **k: Any) -> Any:
            result = real(*a, **k)
            with Session(env.engine) as s:
                job = s.get(job_schema.ModelImportJob, fx.job_id)
                job.claim_epoch = 2
                s.commit()
            return result

        env.m.verify_or_compute_weights_digest = take_over
        try:
            got = activity_error(lambda: env.run(FULL, fx))
        finally:
            env.m.verify_or_compute_weights_digest = real
        job = env.job(fx.job_id)
        check("E", f"E1 a taken-over epoch gets fenced_out: {got}", got == "fenced_out")
        check("E", "E1: no ModelVersion", env.versions() == [])
        check("E", "E1: no audit row", env.audit.events == [])
        check(
            "E",
            "E1: the new holder's job row is untouched",
            (job.state, job.claim_epoch, job.failure_code) == ("validating", 2, None),
        )
    # the same for a failure outcome
    with Env(m).patched() as env:
        fx = env.weights_job("w", weights_files(config__json=jbytes({**CONFIG, "hidden_size": 0})))
        real = env.m.resolve_local_artifact

        def take_over(*a: Any, **k: Any) -> Any:
            result = real(*a, **k)
            with Session(env.engine) as s:
                s.get(job_schema.ModelImportJob, fx.job_id).claim_epoch = 5
                s.commit()
            return result

        env.m.resolve_local_artifact = take_over
        try:
            got = activity_error(lambda: env.run(FULL, fx))
        finally:
            env.m.resolve_local_artifact = real
        check(
            "E",
            f"E2 a failure by a stale holder is fenced too: {got}",
            got == "fenced_out"
            and env.job(fx.job_id).failure_code is None
            and env.audit.events == [],
        )

    # the artifact changes under the activity: the terminal transaction re-reads it
    def mutate_artifact(label: str, change: Any, bypass: bool = False) -> None:
        with Env(m).patched() as env:
            fx = env.weights_job("w", weights_files())
            real = env.m.resolve_local_artifact

            def hook(*a: Any, **k: Any) -> Any:
                result = real(*a, **k)
                with Session(env.engine) as s:
                    if bypass:
                        s.execute(text("PRAGMA ignore_check_constraints = ON"))
                    change(s.get(artifact_schema.TrainableArtifact, fx.artifact_id))
                    s.commit()
                    s.execute(text("PRAGMA ignore_check_constraints = OFF"))
                return result

            env.m.resolve_local_artifact = hook
            try:
                outcome = env.run(FULL, fx)
            finally:
                env.m.resolve_local_artifact = real
            check(
                "E",
                f"{label}: ({outcome_pair(outcome)})",
                outcome_pair(outcome) == ("failed", "artifact_changed_during_validation"),
            )
            check("E", f"{label}: no ModelVersion", env.versions() == [])

    def revoke(a: Any) -> None:
        a.license_accepted_by = None
        a.license_accepted_at = None
        a.status = "registered"

    def archive(a: Any) -> None:
        a.status = "archived"

    def lose_acceptance_time(a: Any) -> None:
        a.license_accepted_at = None

    def new_digest(a: Any) -> None:
        a.weights_digest = "sha256:" + "e" * 64

    def new_revision(a: Any) -> None:
        a.base_model_revision = "000000000000"

    mutate_artifact("E3 license revoked while validating", revoke)
    mutate_artifact("E3 archived while validating", archive)
    mutate_artifact("E3 license time cleared while validating", lose_acceptance_time, bypass=True)
    mutate_artifact("E3 digest replaced while validating", new_digest)
    mutate_artifact("E3 revision replaced while validating", new_revision)

    # a succeeded outcome is one transaction: a failing ModelVersion insert leaves the job untouched
    with Env(m).patched() as env:
        fx = env.weights_job("w", weights_files())
        real_next = env.m._next_version_number

        def boom(*a: Any, **k: Any) -> int:
            raise RuntimeError("insert failed")

        env.m._next_version_number = boom
        try:
            got = activity_error(lambda: env.run(FULL, fx))
        finally:
            env.m._next_version_number = real_next
        check(
            "E",
            f"E4 a failing insert propagates and rolls back: {got}",
            got == "unexpected RuntimeError",
        )
        job = env.job(fx.job_id)
        check(
            "E",
            "E4: the job stays validating with its lease",
            job.state == "validating" and job.lease_expires_at is not None,
        )
        check("E", "E4: no audit row, no version", env.audit.events == [] and env.versions() == [])


def group_f(m: Any) -> None:
    """No DB session while the filesystem is read."""
    for kind, build in (
        (FULL, lambda env: env.weights_job("w", weights_files())),
        (ADAPTER, lambda env: env.adapter_job()),
    ):
        with Env(m).patched() as env:
            fx = build(env)
            seen: list[tuple[str, int]] = []
            hooks = (
                "verify_or_compute_weights_digest",
                "resolve_local_artifact",
                "check_file_names",
                "parse_safetensors_header",
                "validate_config_json",
            )
            real = {name: getattr(env.m, name) for name in hooks}

            def make(name: str, env: Env, seen: list[tuple[str, int]], real: dict[str, Any]) -> Any:
                def wrapped(*a: Any, **k: Any) -> Any:
                    seen.append((name, env.open_sessions))
                    return real[name](*a, **k)

                return wrapped

            for name in hooks:
                setattr(env.m, name, make(name, env, seen, real))
            try:
                env.run(kind, fx)
            finally:
                for name in hooks:
                    setattr(env.m, name, real[name])
            check("F", f"F1 {kind}: the hooks ran ({len(seen)})", len(seen) >= 4)
            check(
                "F",
                f"F1 {kind}: no session open during any filesystem step: {sorted(set(seen))[:6]}",
                all(count == 0 for _name, count in seen),
            )


def group_g(m: Any) -> None:
    """META-LEAK."""
    secrets = (MARK, MARK.lower())

    def clean(text_: str) -> bool:
        return not any(s in text_ for s in secrets)

    with Env(m).patched() as env:
        files = weights_files()
        files[f"{MARK}.txt"] = b"x"
        fx = env.weights_job(f"dir-{MARK}", files)
        outcome = env.run(FULL, fx)
        job = env.job(fx.job_id)
        blob = json.dumps(
            [outcome.state, outcome.failure_code, job.failure_code, env.audit.events], default=str
        )
        check(
            "G",
            f"G1 a marker in a file name stays out of the outcome, job row and audit ({outcome.failure_code})",
            outcome.failure_code == "unexpected_file" and clean(blob),
        )
    with Env(m).patched() as env:
        files = weights_files()
        files["bad.json"] = b"x"
        fx = env.weights_job(f"dir-{MARK}", weights_files())
        (env.root / f"dir-{MARK}" / "model.safetensors").chmod(0o755)
        outcome = env.run(FULL, fx)
        blob = json.dumps([outcome.state, outcome.failure_code, env.audit.events], default=str)
        check(
            "G",
            f"G2 a marker in a directory name stays out when the resolver refuses ({outcome.failure_code})",
            outcome.failure_code == "artifact_source_invalid" and clean(blob),
        )
    with Env(m).patched() as env:
        fx = env.weights_job(
            "w",
            weights_files(
                config__json=jbytes(
                    {"model_type": MARK, "architectures": [MARK], "hidden_size": MARK}
                )
            ),
        )
        outcome = env.run(FULL, fx)
        blob = json.dumps([outcome.state, outcome.failure_code, env.audit.events], default=str)
        check(
            "G",
            f"G3 a marker in a field value stays out ({outcome.failure_code})",
            outcome.failure_code == "config_invalid" and clean(blob),
        )
    with Env(m).patched() as env:
        fx = env.weights_job("w", weights_files())
        env.run(FULL, fx, approved_root=env.root / f"missing-{MARK}")
        check(
            "G",
            "G4 a marker in the approved root stays out of the audit",
            clean(json.dumps(env.audit.events, default=str))
            and clean(env.job(fx.job_id).failure_code or ""),
        )
    # no exception that escapes an activity call carries a marker or a chained one
    with Env(m).patched() as env:
        fx = env.weights_job(f"dir-{MARK}", weights_files())
        try:
            env.run(FULL, fx, token=env.token(fx.job_id, epoch=9))
        except p2.ModelImportActivityError as exc:
            chain = []
            cur: BaseException | None = exc
            while cur is not None:
                chain.append(str(cur))
                cur = cur.__cause__ or cur.__context__
            check(
                "G", f"G5 the fenced exception chain is code only: {chain}", chain == ["fenced_out"]
            )
    # refusals never raise: the original resolver text is not a __context__ of anything stored
    with Env(m).patched() as env:
        fx = env.weights_job(f"dir-{MARK}", weights_files())
        (env.root / f"dir-{MARK}" / "model.safetensors").chmod(0o755)
        captured: list[BaseException] = []
        real = env.m._Refusal.__init__

        def spy(self: Any, *a: Any, **k: Any) -> None:
            real(self, *a, **k)
            captured.append(self)

        env.m._Refusal.__init__ = spy
        try:
            env.run(FULL, fx)
        finally:
            env.m._Refusal.__init__ = real
        raised = [e for e in captured]
        check(
            "G",
            "G6 the internal refusal has no chained exception text",
            bool(raised) and all(e.__context__ is None and e.__cause__ is None for e in raised),
        )


def group_h(m: Any) -> None:
    """The pickle attack on the real default code."""
    import pickle

    class Boom:
        def __reduce__(self) -> Any:
            return (os.system, ("touch " + str(Path(tempfile.gettempdir()) / "ftf-h010-p3-pwned"),))

    payload = pickle.dumps(Boom())
    marker = Path(tempfile.gettempdir()) / "ftf-h010-p3-pwned"
    marker.unlink(missing_ok=True)
    with Env(m).patched() as env:
        fx = env.weights_job("w", weights_files(model__safetensors=payload))
        expect_outcome(
            env,
            FULL,
            fx,
            "H1 a pickle renamed model.safetensors",
            "failed",
            "weights_header_invalid",
        )
        check("H", "H1: nothing was deserialized or executed", not marker.exists())
    with Env(m).patched() as env:
        files = weights_files()
        files["pytorch_model.bin"] = payload
        fx = env.weights_job("w", files)
        expect_outcome(
            env, FULL, fx, "H2 a real pickle weights file", "failed", "unsafe_weight_format"
        )
        check("H", "H2: nothing was deserialized or executed", not marker.exists())
    with Env(m).patched() as env:
        fx = env.adapter_job({**adapter_files(), "adapter_model.bin": payload})
        expect_outcome(
            env, ADAPTER, fx, "H3 a pickle beside the adapter", "failed", "unsafe_weight_format"
        )
        check("H", "H3: nothing was deserialized or executed", not marker.exists())
    check(
        "H",
        "H4: the module imports no deserializer or ML runtime",
        not any(
            token in MODULE_PATH.read_text()
            for token in (
                "import pickle",
                "import torch",
                "import safetensors",
                "import transformers",
                "import numpy",
                "import marshal",
                "import shelve",
                "import dill",
                "import joblib",
            )
        ),
    )


def group_j(m: Any) -> None:
    """Exact comparisons and error paths pinned by the mutation gate."""

    def raw(env: Env, sql: str, **params: Any) -> None:
        with Session(env.engine) as s:
            s.execute(text("PRAGMA ignore_check_constraints = ON"))
            s.execute(text(sql), params)
            s.commit()
            s.execute(text("PRAGMA ignore_check_constraints = OFF"))

    # J1: a job whose stored state / origin is a case or whitespace variant is not the exact one
    for variant in ("Validating", " validating", "validating "):
        with Env(m).patched() as env:
            fx = env.weights_job("w", weights_files())
            raw(env, "UPDATE model_import_jobs SET state = :s", s=variant)
            got = activity_error(lambda env=env, fx=fx: env.run(FULL, fx))
            check("J", f"J1 state {variant!r} is fenced out: {got}", got == "fenced_out")
    for variant in ("Imported_Weights", "imported_weights ", " imported_weights"):
        with Env(m).patched() as env:
            fx = env.weights_job("w", weights_files())
            raw(env, "UPDATE model_import_jobs SET requested_origin = :s", s=variant)
            got = activity_error(lambda env=env, fx=fx: env.run(FULL, fx))
            check(
                "J",
                f"J2 stored origin {variant!r} is not claimable: {got}",
                got == "origin_not_claimable",
            )
    with Env(m).patched() as env:
        fx = env.weights_job("w", weights_files())
        for variant in ("Imported_Weights", "imported_weights ", " imported_weights"):
            got = activity_error(
                lambda env=env, fx=fx, variant=variant: env.m._read_inputs(
                    env.factory, fx.token, variant
                )
            )
            check(
                "J",
                f"J3 a variant origin argument {variant!r} never matches the stored one: {got}",
                got == "origin_not_claimable",
            )

    # J4: a stored source_type / parent origin variant is not the exact value
    with Env(m).patched() as env:
        fx = env.weights_job("w", weights_files())
        raw(env, "UPDATE trainable_artifacts SET source_type = 'Local_Filesystem'")
        expect_outcome(
            env,
            FULL,
            fx,
            "J4 source_type variant",
            "blocked_external",
            "metadata_files_unavailable",
        )
    for variant in ("Imported_Weights", "imported_weights ", " imported_weights"):
        with Env(m).patched() as env:
            fx = env.adapter_job()
            raw(env, "UPDATE model_versions SET origin = :o", o=variant)
            expect_outcome(
                env, ADAPTER, fx, f"J5 parent origin {variant!r}", "failed", "base_not_validated"
            )

    # J6: an unmapped exception is never swallowed and writes nothing
    with Env(m).patched() as env:
        fx = env.weights_job("w", weights_files())
        real = env.m.verify_or_compute_weights_digest

        def boom(*a: Any, **k: Any) -> Any:
            raise RuntimeError("unexpected")

        env.m.verify_or_compute_weights_digest = boom
        try:
            got = activity_error(lambda: env.run(FULL, fx))
        finally:
            env.m.verify_or_compute_weights_digest = real
        check("J", f"J6 an unmapped exception propagates: {got}", got == "unexpected RuntimeError")
        check(
            "J",
            "J6 and nothing is written",
            env.versions() == []
            and env.audit.events == []
            and env.job(fx.job_id).state == "validating",
        )

    # J7: a parsed file that vanishes after the manifest is blocked as unreadable
    with Env(m).patched() as env:
        fx = env.weights_job("w", weights_files())
        real = env.m.resolve_local_artifact

        def vanish(*a: Any, **k: Any) -> Any:
            result = real(*a, **k)
            (env.root / "w" / "config.json").unlink()
            return result

        env.m.resolve_local_artifact = vanish
        try:
            outcome = env.run(FULL, fx)
        finally:
            env.m.resolve_local_artifact = real
        check(
            "J",
            f"J7 config.json vanishing is blocked, not a crash ({outcome_pair(outcome)})",
            outcome_pair(outcome) == ("blocked_external", "artifact_source_unavailable"),
        )

    # J8: a parsed file of exactly the size bound is still hashed
    for name, bound, other in (
        ("config.json", 1 << 20, {**CONFIG, "hidden_size": 5}),
        ("tokenizer_config.json", 4 << 20, {"eos_token": "x"}),
    ):

        def padded(value: Any, bound: int = bound) -> bytes:
            body = json.dumps(value).encode()
            return body + b" " * (bound - len(body))

        original = CONFIG if name == "config.json" else TOKENIZER_CONFIG
        with Env(m).patched() as env:
            fx = env.weights_job("w", weights_files(**{name.replace(".", "__"): padded(original)}))
            outcome = env.run(FULL, fx)
            check(
                "J",
                f"J8 an exactly-bounded {name} that is unchanged succeeds ({outcome_pair(outcome)})",
                outcome.state == "succeeded",
            )
        with Env(m).patched() as env:
            fx = env.weights_job("w", weights_files(**{name.replace(".", "__"): padded(original)}))
            real = env.m.resolve_local_artifact

            def rewrite(
                *a: Any,
                name: str = name,
                other: Any = other,
                env: Env = env,
                real: Any = real,
                padded: Any = padded,
                **k: Any,
            ) -> Any:
                result = real(*a, **k)
                (env.root / "w" / name).write_bytes(padded(other))
                return result

            env.m.resolve_local_artifact = rewrite
            try:
                outcome = env.run(FULL, fx)
            finally:
                env.m.resolve_local_artifact = real
            check(
                "J",
                f"J8 an exactly-bounded {name} rewritten at the same size is caught ({outcome_pair(outcome)})",
                outcome_pair(outcome) == ("failed", "artifact_changed_during_validation"),
            )

    # J9: the internal file lookup is exact and a missing entry is a refusal
    with Env(m).patched() as env:
        fx = env.weights_job("w", weights_files())
        manifest = resolve_local_artifact(env.root, "w")
        refusal = None
        try:
            env.m._read_parsed_file(env.root / "w", manifest, "absent.json", 100)
        except env.m._Refusal as exc:
            refusal = exc.code
        check(
            "J",
            f"J9 a name outside the manifest is a refusal: {refusal}",
            refusal == "required_file_missing",
        )
        names = {e.relative_path for e in manifest.entries}
        check(
            "J",
            "J9 the lookup is exact for every name",
            all(env.m._entry(manifest, n).relative_path == n for n in names),
        )

    # J10: a base that is not ready (but licensed) is `base_not_validated`, never trusted
    with Env(m).patched() as env:
        fx = env.adapter_job(base_kwargs={"status": "registered"})
        expect_outcome(
            env, ADAPTER, fx, "J10 base registered but licensed", "failed", "base_not_validated"
        )
    with Env(m).patched() as env:
        fx = env.adapter_job()
        raw(
            env,
            "UPDATE trainable_artifacts SET weights_digest = NULL WHERE id = :i",
            i=fx.base_artifact_id.hex,
        )
        expect_outcome(
            env, ADAPTER, fx, "J10 base without a resolved digest", "failed", "base_not_validated"
        )

    # J11: a stored artifact status that is a look-alike of `ready` is not ready
    for variant in ("Ready", " ready", "ready "):
        with Env(m).patched() as env:
            fx = env.weights_job("w", weights_files())
            raw(env, "UPDATE trainable_artifacts SET status = :s", s=variant)
            expect_outcome(env, FULL, fx, f"J11 status {variant!r}", "failed", "artifact_not_ready")


def group_k(m: Any) -> None:
    """The shard header is bound to the manifest hash; non-regular files; the exact header cap."""
    import signal

    benign = safetensors_bytes(BASE_TENSORS)
    hostile = benign.replace(b'"F32"', b'"F33"', 1)
    check(
        "K",
        "K0 fixture: the hostile shard has the benign shard's size",
        len(hostile) == len(benign) and hostile != benign,
    )

    # K1: registered over a hostile shard; a same-size benign shard is swapped in after the manifest
    with Env(m).patched() as env:
        fx = env.weights_job("w", weights_files(model__safetensors=hostile))
        real = env.m.resolve_local_artifact

        def swap(*a: Any, **k: Any) -> Any:
            result = real(*a, **k)
            (env.root / "w" / "model.safetensors").write_bytes(benign)
            return result

        env.m.resolve_local_artifact = swap
        try:
            outcome = env.run(FULL, fx)
        finally:
            env.m.resolve_local_artifact = real
        check(
            "K",
            f"K1 a same-size shard swapped after the manifest is not judged as the digested file ({outcome_pair(outcome)})",
            outcome_pair(outcome) == ("failed", "artifact_changed_during_validation")
            and env.versions() == [],
        )

    # K2: the same for the base in the adapter path
    with Env(m).patched() as env:
        fx = env.adapter_job(base_files=weights_files(model__safetensors=hostile))
        real = env.m.resolve_local_artifact

        def swap_base(*a: Any, **k: Any) -> Any:
            result = real(*a, **k)
            if a[1] == "base":
                (env.root / "base" / "model.safetensors").write_bytes(benign)
            return result

        env.m.resolve_local_artifact = swap_base
        try:
            outcome = env.run(ADAPTER, fx)
        finally:
            env.m.resolve_local_artifact = real
        check(
            "K",
            f"K2 a same-size base shard swapped after its manifest pass is not judged ({outcome_pair(outcome)})",
            outcome_pair(outcome) == ("failed", "artifact_changed_during_validation"),
        )

    # K3: a non-regular file swapped in after the manifest is a changed file, never a hang
    def timed(env: Env, fx: Fixture) -> Any:
        def alarm(*_a: Any) -> None:
            raise TimeoutError("hang")

        old = signal.signal(signal.SIGALRM, alarm)
        signal.setitimer(signal.ITIMER_REAL, 15)
        try:
            return env.run(FULL, fx)
        except TimeoutError:
            return "hang"
        finally:
            signal.setitimer(signal.ITIMER_REAL, 0)
            signal.signal(signal.SIGALRM, old)

    for name in ("config.json", "tokenizer_config.json", "model.safetensors"):
        for kind in ("fifo", "directory"):
            with Env(m).patched() as env:
                fx = env.weights_job("w", weights_files())
                real = env.m.resolve_local_artifact

                def replace(
                    *a: Any,
                    name: str = name,
                    kind: str = kind,
                    env: Env = env,
                    real: Any = real,
                    **k: Any,
                ) -> Any:
                    result = real(*a, **k)
                    target = env.root / "w" / name
                    target.unlink()
                    if kind == "fifo":
                        os.mkfifo(target)
                    else:
                        target.mkdir()
                    return result

                env.m.resolve_local_artifact = replace
                try:
                    outcome = timed(env, fx)
                finally:
                    env.m.resolve_local_artifact = real
                got = outcome if isinstance(outcome, str) else outcome_pair(outcome)
                check(
                    "K",
                    f"K3 {name} swapped for a {kind} after the manifest: {got}",
                    got == ("failed", "artifact_changed_during_validation")
                    and env.versions() == [],
                )

    # K4: a header of exactly the format's own bound (100,000,000 bytes) is accepted
    n = 100_000_000
    body = json.dumps(
        {
            "model.layers.0.q_proj.weight": {
                "dtype": "F32",
                "shape": [8, 4],
                "data_offsets": [0, 128],
            }
        },
        separators=(",", ":"),
    ).encode()
    big = struct.pack("<Q", n) + body + b" " * (n - len(body)) + bytes(128)
    with Env(m).patched() as env:
        fx = env.weights_job("w", weights_files(model__safetensors=big))
        outcome = env.run(FULL, fx)
        check(
            "K",
            f"K4 a header of exactly 100,000,000 bytes is accepted ({outcome_pair(outcome)})",
            outcome_pair(outcome) == ("succeeded", None),
        )
    del big


# -- faults ----------------------------------------------------------------------------------------

FAULTS: list[tuple[str, str, str, int]] = [
    (
        "license gate skipped",
        "lambda: assert_license_accepted_for_use(artifact),",
        "lambda: None,",
        1,
    ),
    (
        "base license gate skipped",
        "lambda: assert_license_accepted_for_use(base_artifact),",
        "lambda: None,",
        1,
    ),
    (
        "digest trusted from the stored value (FTF-0306 call dropped)",
        "lambda: verify_or_compute_weights_digest(\n            artifact, LocalFilesystemResolutionInputs(approved_root=approved_root)\n        ),",
        "lambda: None,",
        1,
    ),
    (
        "manifest pass not bound to the verified digest",
        "if not _same_hash(canonical_weights_digest(manifest.manifest_sha256), artifact.weights_digest):",
        "if False:",
        1,
    ),
    (
        "allow-list dropped (any file accepted)",
        "_parse(lambda: check_file_names([e.relative_path for e in manifest.entries], kind=kind))",
        "pass",
        1,
    ),
    ("artifact readiness not required", "if not ready or not digest_ok:", "if False:", 1),
    (
        "parsed-file hash check dropped",
        "len(data) != entry.size_bytes\n        or not _same_hash(hashlib.sha256(data).hexdigest(), entry.sha256)",
        "False",
        1,
    ),
    (
        "shard header not bound to the manifest hash",
        "if size != entry.size_bytes or not _same_hash(digest.hexdigest(), entry.sha256):",
        "if size != entry.size_bytes:",
        1,
    ),
    (
        "non-regular files read (no S_ISREG check)",
        "if not stat.S_ISREG(os.fstat(fd).st_mode):",
        "if False:",
        1,
    ),
    (
        "blocking open (O_NONBLOCK dropped)",
        '| getattr(os, "O_NONBLOCK", 0)',
        "",
        1,
    ),
    (
        "header read cap one short",
        "MAX_SAFETENSORS_HEADER_BYTES\n            )",
        "MAX_SAFETENSORS_HEADER_BYTES - 1\n            )",
        1,
    ),
    (
        "symlinks followed when reading",
        'os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)',
        "os.O_RDONLY",
        1,
    ),
    (
        "header read unbounded",
        "body_cap = min(\n                length, max(size - _HEADER_LENGTH_BYTES, 0), MAX_SAFETENSORS_HEADER_BYTES\n            )",
        "body_cap = length",
        1,
    ),
    (
        "base resolved without the import job succeeding",
        "or not parent.import_job_succeeded",
        "or False",
        1,
    ),
    (
        "base resolved from any origin",
        "or parent.origin not in (ORIGIN_IMPORTED_WEIGHTS,)",
        "or False",
        1,
    ),
    (
        "lineage exact-base check dropped",
        "lambda: build_artifact_lineage_manifest(\n            candidate, artifact, resolved_base_artifact=base_artifact\n        ),",
        "lambda: None,",
        1,
    ),
    (
        "adapter/base shape check dropped",
        "_parse(lambda: check_adapter_base_compatibility(adapter_header, adapter_config, base_shapes))",
        "pass",
        1,
    ),
    (
        "tokenizer mismatch ignored",
        "if not _same_hash(base_hashes.get(name), digest):",
        "if False:",
        1,
    ),
    (
        "lineage written as complete",
        'lineage_completeness="partial",',
        'lineage_completeness="complete",',
        1,
    ),
    (
        "chat template hash dropped",
        "chat_template_sha256=facts.chat_template_sha256,",
        "chat_template_sha256=None,",
        1,
    ),
    ("adapter flag dropped", "adapter_only=adapter,", "adapter_only=False,", 1),
    (
        "declared parent dropped",
        "parent_model_version_id=inputs.parent_model_version_id,",
        "parent_model_version_id=None,",
        1,
    ),
    (
        "terminal artifact re-read dropped",
        "if _artifact_unchanged(session, context, inputs.artifact):",
        "if True:",
        1,
    ),
    (
        "epoch fence dropped",
        "if result.rowcount != 1:\n                raise _Fenced",
        "if False:\n                raise _Fenced",
        1,
    ),
    (
        "unmapped exceptions swallowed",
        "        else:\n            raise\n",
        "        else:\n            pass\n",
        1,
    ),
]


def suite(m: Any) -> list[str]:
    _failures.clear()
    group_a(m)
    group_b(m)
    group_c(m)
    group_d(m)
    group_e(m)
    group_f(m)
    group_g(m)
    group_h(m)
    group_j(m)
    group_k(m)
    return list(_failures)


def run_fault(source: str, name: str, old: str, new: str, count: int) -> tuple[bool, str]:
    if source.count(old) != count:
        return False, f"patch text occurs {source.count(old)} time(s), expected {count}"
    mutated = source.replace(old, new)
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "mutant.py"
        path.write_text(mutated)
        try:
            mm = load_module(path, f"model_import_weights_validation_fault_{abs(hash(name))}")
        except BaseException as exc:  # noqa: BLE001
            return False, f"mutant does not import ({type(exc).__name__}: {exc})"
        try:
            red = suite(mm)
        except BaseException as exc:  # noqa: BLE001 - a crashing suite is also RED
            red = [f"crash {type(exc).__name__}: {exc}"]
    if not red:
        return False, "suite stayed GREEN"
    return True, f"{len(red)} failing check(s), e.g. {red[0][:110]}"


def main() -> int:
    if not MODULE_PATH.exists():
        print(f"FAIL [{CID}] module missing: {MODULE_PATH}")
        return 1
    source = MODULE_PATH.read_text()
    m = load_module(MODULE_PATH, "model_import_weights_validation_real")
    failures = suite(m)
    if failures:
        print(f"{len(failures)} failing check(s) on the real module:")
        for failure in failures[:80]:
            print(f"  {failure}")
        return 1
    if BEHAVIOUR_ONLY:
        print(
            f"OK -- {CHECK_COUNT[0]} checks in 1 suite run (behaviour only, no fault copies) ({CID})"
        )
        return 0
    names = [f[0] for f in FAULTS]
    if len(set(names)) != len(names):
        failures.append("FAIL [I1] duplicate fault names")
    ran = 0
    for name, old, new, count in FAULTS:
        ok, detail = run_fault(source, name, old, new, count)
        ran += 1
        if ok:
            print(f"  fault {name}: RED ({detail})")
        else:
            failures.append(f"FAIL [I1] fault {name}: {detail}")
    if failures:
        print(f"{len(failures)} failing check(s):")
        for failure in failures[:80]:
            print(f"  {failure}")
        return 1
    print(
        f"OK -- {CHECK_COUNT[0]} checks in {ran + 1} suite runs, {ran} faults RED, 0 failing ({CID})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
