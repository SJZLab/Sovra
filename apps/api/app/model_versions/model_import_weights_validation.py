"""Full-weight and adapter import validation (FTF-H010-P3, Section 6.7, 6.17, 17 "Prefer
safetensors ... do not deserialize arbitrary uploaded pickle", 34, 58A rows 2-3, 68.1 paragraph 3,
68.4).

Two plain functions a durable dispatcher calls after `claim_model_import_job`, one job at a time:

- `validate_weights_import(...)`  origin `imported_weights`
- `validate_adapter_import(...)`  origin `imported_adapter`

Both run the P2 claim/lease/fence unchanged (the dispatcher passes the matching origin to
`claim_model_import_job`) and write exactly one terminal outcome through the same epoch-conditional
UPDATE, so a holder whose lease was taken over matches 0 rows and nothing is written (`fenced_out`).
Inputs come from database rows only (the job's `trainable_artifact_id` and, for an adapter, its
`parent_model_version_id`); the approved local root is a server-side argument, never request input.

Steps (each refusal is its own failed code; numbers follow QUEUE.md FTF-H010-P3):

1. the artifact is `ready` with a resolved digest            -> `artifact_not_ready`
2. FTF-0305 license gate on the artifact and, for an adapter, on the base's artifact
                                                              -> `license_not_accepted`,
                                                                 `base_license_not_accepted`
3. FTF-0306 `verify_or_compute_weights_digest` recomputes the digest from real bytes
                                                              -> `digest_mismatch`
   an unreachable source -> `blocked_external` `artifact_source_unavailable`; `private_s3` and
   `huggingface` artifacts -> `blocked_external` `metadata_files_unavailable` (no multi-file
   resolver manifest exists for them yet)
4. exact file allow-list over the FTF-0304 manifest (P3a `check_file_names`)
5. every parsed file is read once, size-bounded, hashed and equal to its manifest sha256
                                                    -> `artifact_changed_during_validation`
6. strict `config.json` / `tokenizer_config.json` / `adapter_config.json` parsing (P3a)
7. every `*.safetensors` header (P3a `parse_safetensors_header`, stdlib `struct` + `json`)
8. adapter only: the parent must be a same-project imported full-weight version whose own import
   job succeeded (`base_not_validated`); FTF-1002 `build_artifact_lineage_manifest` with the
   resolved base (`adapter_base_mismatch`); the lora tensors against the base's real shard headers
   (`adapter_base_incompatible`); the adapter directory's tokenizer files byte-equal to the base's
   (`tokenizer_mismatch`). `base_model_name_or_path` never resolves the base.
9. success inserts the ModelVersion in the terminal transaction, epoch-fenced. Lineage is
   `partial` and never `complete` (only a factory run could prove training and data). No existing
   `model_versions` row is ever updated.

Nothing here imports torch, safetensors or transformers, and no artifact file is ever executed or
imported: JSON is parsed by the stdlib, and a safetensors header by `struct` and `json`.

Every refusal carries a stable code and never caller text: a file name, a path, a field value or
the text of a resolver exception never reaches a failure code, an audit row or an exception chain
(every raise happens outside the `except` block that caught the original).

Disclosed: UNWIRED (no dispatcher calls these yet, FTF-0801/H016); `factory_trained` parents are
refused `base_not_validated` because no factory-run entity exists yet to prove them; the
`model.safetensors.index.json` file is allow-listed and hashed but not interpreted; shard bytes
beyond each header are covered by the two full-hash passes (FTF-0306 digest and the manifest
re-resolve), not by a third read.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from sqlalchemy import select, text, update
from sqlalchemy.orm import Session

from app.artifacts.artifact_crud import ArtifactNotFoundError, get_artifact
from app.artifacts.artifact_lineage_manifest import (
    AdapterBaseMismatchError,
    ArtifactLineageError,
    build_artifact_lineage_manifest,
)
from app.artifacts.artifact_schema import (
    TrainableArtifact,
    TrainableArtifactValidationError,
    validate_weights_digest,
)
from app.artifacts.base_model_revision_digest_resolver import (
    DigestMismatchError,
    LocalFilesystemResolutionInputs,
    canonical_weights_digest,
    verify_or_compute_weights_digest,
)
from app.artifacts.license_acceptance_workflow import (
    LicenseNotAcceptedError,
    assert_license_accepted_for_use,
)
from app.artifacts.local_artifact_resolver import (
    LocalArtifactFileEntry,
    LocalArtifactManifest,
    LocalArtifactResolutionError,
    resolve_local_artifact,
)
from app.auth.audit_event_writer import write_audit_event
from app.auth.tenant_isolation_query_guards import execute_tenant_scoped
from app.db.tenant_context import TenantContext, tenant_scoped_session_for
from app.model_versions.import_artifact_validators import (
    KIND_ADAPTER,
    KIND_WEIGHTS,
    MAX_SAFETENSORS_HEADER_BYTES,
    ImportArtifactValidationError,
    SafetensorsHeader,
    base_shapes_from_headers,
    check_adapter_base_compatibility,
    check_file_names,
    parse_safetensors_header,
    validate_adapter_config_json,
    validate_config_json,
    validate_tokenizer_config_json,
)
from app.model_versions.model_import_schema import (
    ORIGIN_IMPORTED_ADAPTER,
    ORIGIN_IMPORTED_WEIGHTS,
    ModelImportJob,
)
from app.model_versions.model_import_validation import (
    STATE_BLOCKED_EXTERNAL,
    STATE_FAILED,
    STATE_SUCCEEDED,
    ClaimToken,
    ImportOutcome,
    ModelImportActivityError,
    _Fenced,
    _load_job,
    _require_aware,
)
from app.model_versions.model_version_crud import _next_version_number
from app.model_versions.model_version_schema import ModelVersion

__all__ = [
    "validate_adapter_import",
    "validate_weights_import",
]

CODE_ARTIFACT_NOT_READY = "artifact_not_ready"
CODE_LICENSE_NOT_ACCEPTED = "license_not_accepted"
CODE_BASE_LICENSE_NOT_ACCEPTED = "base_license_not_accepted"
CODE_DIGEST_MISMATCH = "digest_mismatch"
CODE_SOURCE_UNAVAILABLE = "artifact_source_unavailable"
CODE_SOURCE_INVALID = "artifact_source_invalid"
CODE_METADATA_FILES_UNAVAILABLE = "metadata_files_unavailable"
CODE_CHANGED_DURING_VALIDATION = "artifact_changed_during_validation"
CODE_BASE_NOT_VALIDATED = "base_not_validated"
CODE_REQUIRED_FILE_MISSING = "required_file_missing"
CODE_ADAPTER_BASE_MISMATCH = "adapter_base_mismatch"
CODE_TOKENIZER_MISMATCH = "tokenizer_mismatch"

_CONFIG_FILE = "config.json"
_TOKENIZER_CONFIG_FILE = "tokenizer_config.json"
_ADAPTER_CONFIG_FILE = "adapter_config.json"
_ADAPTER_WEIGHTS_FILE = "adapter_model.safetensors"
_SAFETENSORS_SUFFIX = ".safetensors"
_TOKENIZER_FILES = frozenset(
    {
        "tokenizer_config.json",
        "tokenizer.json",
        "tokenizer.model",
        "special_tokens_map.json",
        "added_tokens.json",
        "vocab.json",
        "merges.txt",
        "chat_template.jinja",
    }
)
_CONFIG_MAX_BYTES = 1 << 20
_TOKENIZER_CONFIG_MAX_BYTES = 4 << 20
_HEADER_LENGTH_BYTES = 8


class _Refusal(Exception):
    """Internal control flow only: one terminal outcome. Carries a stable code and a state,
    never text from the artifact."""

    def __init__(self, code: str, state: str = STATE_FAILED) -> None:
        super().__init__(code)
        self.code = code
        self.state = state


@dataclass(frozen=True)
class _ParentSnapshot:
    model_version_id: uuid.UUID
    origin: str
    import_job_succeeded: bool
    artifact: TrainableArtifact | None


@dataclass(frozen=True)
class _Inputs:
    created_by: str
    artifact: TrainableArtifact
    parent_model_version_id: uuid.UUID | None
    parent: _ParentSnapshot | None


@dataclass(frozen=True)
class _Facts:
    weights_digest: str
    base_model_revision: str
    chat_template_sha256: str | None


# ---------------------------------------------------------------------------
# Database reads (each its own tenant-scoped transaction; filesystem work runs outside)
# ---------------------------------------------------------------------------


def _read_inputs(session_factory: Callable[[], Session], token: ClaimToken, origin: str) -> _Inputs:
    context = TenantContext(organization_id=token.organization_id)
    with tenant_scoped_session_for(session_factory, context) as session:
        job = _load_job(session, token.organization_id, token.project_id, token.job_id)
        if job is None:
            raise ModelImportActivityError("job_not_found")
        if job.state != "validating" or job.claim_epoch != token.epoch:
            raise ModelImportActivityError("fenced_out")
        if job.requested_origin not in (origin,) or job.trainable_artifact_id is None:
            raise ModelImportActivityError("origin_not_claimable")
        if origin is ORIGIN_IMPORTED_ADAPTER and job.parent_model_version_id is None:
            raise ModelImportActivityError("job_invalid")
        try:
            artifact = get_artifact(session, context, job.trainable_artifact_id)
        except ArtifactNotFoundError:
            raise ModelImportActivityError("job_invalid") from None
        session.expunge(artifact)
        parent = None
        if origin is ORIGIN_IMPORTED_ADAPTER:
            parent = _read_parent(session, context, token, job)
        return _Inputs(
            created_by=job.created_by,
            artifact=artifact,
            parent_model_version_id=job.parent_model_version_id,
            parent=parent,
        )


def _read_parent(
    session: Session, context: TenantContext, token: ClaimToken, job: ModelImportJob
) -> _ParentSnapshot | None:
    statement = select(ModelVersion).where(
        ModelVersion.id == job.parent_model_version_id,
        ModelVersion.organization_id == token.organization_id,
        ModelVersion.project_id == token.project_id,
    )
    version = execute_tenant_scoped(session, token.organization_id, statement).scalars().first()
    if version is None:
        return None
    succeeded = (
        execute_tenant_scoped(
            session,
            token.organization_id,
            select(ModelImportJob.id).where(
                ModelImportJob.organization_id == token.organization_id,
                ModelImportJob.project_id == token.project_id,
                ModelImportJob.result_model_version_id == version.id,
                ModelImportJob.state == "succeeded",
            ),
        )
        .scalars()
        .first()
        is not None
    )
    artifact = None
    if version.trainable_artifact_id is not None:
        try:
            artifact = get_artifact(session, context, version.trainable_artifact_id)
        except ArtifactNotFoundError:
            artifact = None
        else:
            session.expunge(artifact)
    return _ParentSnapshot(
        model_version_id=version.id,
        origin=version.origin,
        import_job_succeeded=succeeded,
        artifact=artifact,
    )


# ---------------------------------------------------------------------------
# Filesystem steps (no database transaction is open while these run)
# ---------------------------------------------------------------------------


def _guard(
    fn: Callable[[], object], mapping: tuple[tuple[type[Exception], str, str], ...]
) -> object:
    """Runs `fn`; a mapped exception becomes a `_Refusal` raised AFTER the except block, so the
    refusal's `__context__` never carries the original text (a path, a field value)."""
    refusal: _Refusal | None = None
    result: object = None
    try:
        result = fn()
    except Exception as exc:  # noqa: BLE001 - re-raised below unless mapped
        for kind, code, state in mapping:
            if isinstance(exc, kind):
                refusal = _Refusal(code, state)
                break
        else:
            raise
    if refusal is not None:
        raise refusal
    return result


def _check_artifact(artifact: TrainableArtifact, *, base: bool) -> None:
    ready = artifact.status == "ready"
    digest_ok = True
    try:
        validate_weights_digest(artifact.weights_digest)
    except TrainableArtifactValidationError:
        digest_ok = False
    if not ready or not digest_ok:
        raise _Refusal(CODE_ARTIFACT_NOT_READY if not base else CODE_BASE_NOT_VALIDATED)
    _guard(
        lambda: assert_license_accepted_for_use(artifact),
        (
            (
                LicenseNotAcceptedError,
                CODE_BASE_LICENSE_NOT_ACCEPTED if base else CODE_LICENSE_NOT_ACCEPTED,
                STATE_FAILED,
            ),
        ),
    )


def _resolve_manifest(artifact: TrainableArtifact, approved_root: Path) -> LocalArtifactManifest:
    """Step 3: the FTF-0306 digest gate over real bytes, then the FTF-0304 manifest, which must
    resolve to the very digest just verified (a file rewritten between the two passes)."""
    if artifact.source_type != "local_filesystem":
        raise _Refusal(CODE_METADATA_FILES_UNAVAILABLE, STATE_BLOCKED_EXTERNAL)
    mapping: tuple[tuple[type[Exception], str, str], ...] = (
        (DigestMismatchError, CODE_DIGEST_MISMATCH, STATE_FAILED),
        (LocalArtifactResolutionError, CODE_SOURCE_INVALID, STATE_FAILED),
        (TrainableArtifactValidationError, CODE_SOURCE_INVALID, STATE_FAILED),
        (OSError, CODE_SOURCE_UNAVAILABLE, STATE_BLOCKED_EXTERNAL),
    )
    _guard(
        lambda: verify_or_compute_weights_digest(
            artifact, LocalFilesystemResolutionInputs(approved_root=approved_root)
        ),
        mapping,
    )
    manifest = _guard(lambda: resolve_local_artifact(approved_root, artifact.source_uri), mapping)
    assert isinstance(manifest, LocalArtifactManifest)
    if not _same_hash(canonical_weights_digest(manifest.manifest_sha256), artifact.weights_digest):
        raise _Refusal(CODE_CHANGED_DURING_VALIDATION)
    return manifest


def _same_hash(left: object, right: object) -> bool:
    """Exact comparison of two hash strings (anything that is not a str is not equal)."""
    if not isinstance(left, str) or not isinstance(right, str):
        return False
    return hmac.compare_digest(left.encode(), right.encode())


def _entry(manifest: LocalArtifactManifest, name: str) -> LocalArtifactFileEntry:
    found = {e.relative_path: e for e in manifest.entries}.get(name)
    if found is None:
        raise _Refusal(CODE_REQUIRED_FILE_MISSING)
    return found


def _artifact_dir(approved_root: Path, artifact: TrainableArtifact) -> Path:
    node = approved_root.resolve(strict=True) / artifact.source_uri
    return node if node.is_dir() else node.parent


def _open_no_follow(path: Path) -> int:
    return os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))


def _read_parsed_file(
    directory: Path, manifest: LocalArtifactManifest, name: str, max_bytes: int
) -> bytes:
    """Reads one file once, at most `max_bytes + 1` bytes, and checks it against its manifest
    sha256. A file over the bound is returned truncated so the strict parser refuses it by size
    under its own code (the file is never read in full)."""
    entry = _entry(manifest, name)
    refusal: str | None = None
    data = b""
    try:
        with os.fdopen(_open_no_follow(directory / name), "rb") as handle:
            data = handle.read(max_bytes + 1)
    except OSError:
        refusal = CODE_SOURCE_UNAVAILABLE
    if refusal is not None:
        raise _Refusal(refusal, STATE_BLOCKED_EXTERNAL)
    if len(data) <= max_bytes and (
        len(data) != entry.size_bytes or hashlib.sha256(data).hexdigest() != entry.sha256
    ):
        raise _Refusal(CODE_CHANGED_DURING_VALIDATION)
    return data


def _read_header(directory: Path, manifest: LocalArtifactManifest, name: str) -> SafetensorsHeader:
    entry = _entry(manifest, name)
    refusal: str | None = None
    prefix = b""
    size = -1
    try:
        with os.fdopen(_open_no_follow(directory / name), "rb") as handle:
            size = os.fstat(handle.fileno()).st_size
            prefix = handle.read(_HEADER_LENGTH_BYTES)
            length = int.from_bytes(prefix, "little")
            # The parser judges the declared length; only a plausible number of bytes is ever
            # read (never past the file, never past the format's own bound), so a hostile header
            # cannot force a huge read.
            body_cap = min(
                length, max(size - _HEADER_LENGTH_BYTES, 0), MAX_SAFETENSORS_HEADER_BYTES
            )
            prefix += handle.read(body_cap)
    except OSError:
        refusal = CODE_SOURCE_UNAVAILABLE
    if refusal is not None:
        raise _Refusal(refusal, STATE_BLOCKED_EXTERNAL)
    if size != entry.size_bytes:
        raise _Refusal(CODE_CHANGED_DURING_VALIDATION)
    return _parse(lambda: parse_safetensors_header(prefix, file_size=size))  # type: ignore[return-value]


def _parse(fn: Callable[[], object]) -> object:
    """Maps a P3a validator refusal to its own failure code, raised outside the except."""
    code: str | None = None
    result: object = None
    try:
        result = fn()
    except ImportArtifactValidationError as exc:
        code = exc.code
    if code is not None:
        raise _Refusal(code)
    return result


def _validate_names(manifest: LocalArtifactManifest, kind: str) -> None:
    _parse(lambda: check_file_names([e.relative_path for e in manifest.entries], kind=kind))


def _weights_pipeline(artifact: TrainableArtifact, approved_root: Path) -> _Facts:
    _check_artifact(artifact, base=False)
    manifest = _resolve_manifest(artifact, approved_root)
    _validate_names(manifest, KIND_WEIGHTS)
    directory = _artifact_dir(approved_root, artifact)
    config_bytes = _read_parsed_file(directory, manifest, _CONFIG_FILE, _CONFIG_MAX_BYTES)
    tokenizer_bytes = _read_parsed_file(
        directory, manifest, _TOKENIZER_CONFIG_FILE, _TOKENIZER_CONFIG_MAX_BYTES
    )
    _parse(lambda: validate_config_json(config_bytes))
    tokenizer = _parse(lambda: validate_tokenizer_config_json(tokenizer_bytes))
    for entry in manifest.entries:
        if entry.relative_path.endswith(_SAFETENSORS_SUFFIX):
            _read_header(directory, manifest, entry.relative_path)
    assert artifact.weights_digest is not None
    return _Facts(
        weights_digest=artifact.weights_digest,
        base_model_revision=artifact.base_model_revision,
        chat_template_sha256=tokenizer.chat_template_sha256,  # type: ignore[attr-defined]
    )


def _tokenizer_hashes(manifest: LocalArtifactManifest) -> dict[str, str]:
    return {
        e.relative_path: e.sha256 for e in manifest.entries if e.relative_path in _TOKENIZER_FILES
    }


def _adapter_pipeline(
    artifact: TrainableArtifact,
    parent: _ParentSnapshot | None,
    parent_version_id: uuid.UUID,
    organization_id: uuid.UUID,
    project_id: uuid.UUID,
    created_by: str,
    approved_root: Path,
) -> _Facts:
    _check_artifact(artifact, base=False)
    base_artifact = parent.artifact if parent is not None else None
    if base_artifact is not None:
        _check_artifact_license_only(base_artifact)
    manifest = _resolve_manifest(artifact, approved_root)
    _validate_names(manifest, KIND_ADAPTER)
    directory = _artifact_dir(approved_root, artifact)
    config_bytes = _read_parsed_file(directory, manifest, _ADAPTER_CONFIG_FILE, _CONFIG_MAX_BYTES)
    adapter_config = _parse(lambda: validate_adapter_config_json(config_bytes))
    adapter_header = _read_header(directory, manifest, _ADAPTER_WEIGHTS_FILE)

    if (
        parent is None
        or parent.origin not in (ORIGIN_IMPORTED_WEIGHTS,)
        or not parent.import_job_succeeded
        or base_artifact is None
    ):
        raise _Refusal(CODE_BASE_NOT_VALIDATED)
    _check_artifact(base_artifact, base=True)
    _lineage_exact_base(
        artifact, base_artifact, parent_version_id, organization_id, project_id, created_by
    )
    base_manifest = _resolve_manifest(base_artifact, approved_root)
    base_directory = _artifact_dir(approved_root, base_artifact)
    headers = [
        _read_header(base_directory, base_manifest, e.relative_path)
        for e in base_manifest.entries
        if e.relative_path.endswith(_SAFETENSORS_SUFFIX)
    ]
    base_shapes = _parse(lambda: base_shapes_from_headers(headers))
    _parse(lambda: check_adapter_base_compatibility(adapter_header, adapter_config, base_shapes))
    base_hashes = _tokenizer_hashes(base_manifest)
    for name, digest in _tokenizer_hashes(manifest).items():
        if not _same_hash(base_hashes.get(name), digest):
            raise _Refusal(CODE_TOKENIZER_MISMATCH)
    assert artifact.weights_digest is not None
    return _Facts(
        weights_digest=artifact.weights_digest,
        base_model_revision=artifact.base_model_revision,
        chat_template_sha256=None,
    )


def _check_artifact_license_only(base_artifact: TrainableArtifact) -> None:
    _guard(
        lambda: assert_license_accepted_for_use(base_artifact),
        ((LicenseNotAcceptedError, CODE_BASE_LICENSE_NOT_ACCEPTED, STATE_FAILED),),
    )


def _lineage_exact_base(
    artifact: TrainableArtifact,
    base_artifact: TrainableArtifact,
    parent_version_id: uuid.UUID,
    organization_id: uuid.UUID,
    project_id: uuid.UUID,
    created_by: str,
) -> None:
    """FTF-1002: the exact base, never a same-family guess. The version is built only to be
    judged (in memory, never added to a session)."""
    candidate = ModelVersion(
        id=uuid.uuid4(),
        organization_id=organization_id,
        project_id=project_id,
        version_number=1,
        import_kind="adapter",
        origin=ORIGIN_IMPORTED_ADAPTER,
        trainable_artifact_id=artifact.id,
        weights_digest=artifact.weights_digest,
        base_model_revision=artifact.base_model_revision,
        adapter_only=True,
        parent_model_version_id=parent_version_id,
        created_by=created_by,
    )
    _guard(
        lambda: build_artifact_lineage_manifest(
            candidate, artifact, resolved_base_artifact=base_artifact
        ),
        (
            (AdapterBaseMismatchError, CODE_ADAPTER_BASE_MISMATCH, STATE_FAILED),
            (ArtifactLineageError, CODE_ADAPTER_BASE_MISMATCH, STATE_FAILED),
        ),
    )


# ---------------------------------------------------------------------------
# Terminal write: one transaction, epoch-fenced
# ---------------------------------------------------------------------------


def _artifact_unchanged(
    session: Session, context: TenantContext, artifact: TrainableArtifact
) -> bool:
    """Re-reads the artifact inside the terminal transaction, share-locked: a concurrent revoke
    or archive either commits first (seen here) or waits for this transaction."""
    current = (
        execute_tenant_scoped(
            session,
            context.organization_id,
            select(TrainableArtifact)
            .where(
                TrainableArtifact.id == artifact.id,
                TrainableArtifact.organization_id == context.organization_id,
            )
            .with_for_update(read=True),
        )
        .scalars()
        .first()
    )
    return (
        current is not None
        and current.license_accepted_at is not None
        and (current.status, current.weights_digest, current.base_model_revision)
        == ("ready", artifact.weights_digest, artifact.base_model_revision)
    )


def _finish(
    session_factory: Callable[[], Session],
    token: ClaimToken,
    inputs: _Inputs,
    *,
    origin: str,
    state: str,
    failure_code: str | None,
    facts: _Facts | None,
    now: datetime,
) -> ImportOutcome:
    context = TenantContext(organization_id=token.organization_id)
    model_version_id: uuid.UUID | None = None
    try:
        with tenant_scoped_session_for(session_factory, context) as session:
            if facts is not None:
                if session.get_bind().dialect.name.startswith("postgres"):
                    session.execute(
                        text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
                        {"key": f"model_versions:{token.project_id}"},
                    )
                if _artifact_unchanged(session, context, inputs.artifact):
                    adapter = origin is ORIGIN_IMPORTED_ADAPTER
                    version = ModelVersion(
                        id=uuid.uuid4(),
                        created_at=now,
                        organization_id=token.organization_id,
                        project_id=token.project_id,
                        version_number=_next_version_number(session, context, token.project_id),
                        import_kind="adapter" if adapter else "full_weight",
                        origin=origin,
                        trainable_artifact_id=inputs.artifact.id,
                        weights_digest=facts.weights_digest,
                        base_model_revision=facts.base_model_revision,
                        tokenizer_revision=facts.base_model_revision,
                        chat_template_sha256=facts.chat_template_sha256,
                        serving_config_sha256=None,
                        adapter_only=adapter,
                        parent_model_version_id=inputs.parent_model_version_id,
                        lineage_completeness="partial",
                        created_by=inputs.created_by,
                    )
                    session.add(version)
                    session.flush()
                    model_version_id = version.id
                else:
                    state, failure_code = STATE_FAILED, CODE_CHANGED_DURING_VALIDATION

            statement = (
                update(ModelImportJob)
                .where(
                    ModelImportJob.id == token.job_id,
                    ModelImportJob.organization_id == token.organization_id,
                    ModelImportJob.project_id == token.project_id,
                    ModelImportJob.claim_epoch == token.epoch,
                    ModelImportJob.state == "validating",
                )
                .values(
                    state=state,
                    failure_code=failure_code,
                    result_model_version_id=model_version_id,
                    lease_expires_at=None,
                    updated_at=now,
                )
                .execution_options(synchronize_session=False)
            )
            result = execute_tenant_scoped(session, token.organization_id, statement)
            if result.rowcount != 1:
                raise _Fenced
            write_audit_event(
                session,
                organization_id=token.organization_id,
                actor_type="system",
                actor_id=token.worker_id,
                action="model_import.validate",
                resource_type="model_import_job",
                resource_id=str(token.job_id),
                request_id=uuid.uuid4().hex,
                details={
                    "job_id": str(token.job_id),
                    "origin": origin,
                    "outcome": state if failure_code is None else failure_code,
                },
            )
    except _Fenced:
        raise ModelImportActivityError("fenced_out") from None
    return ImportOutcome(state=state, failure_code=failure_code, model_version_id=model_version_id)


# ---------------------------------------------------------------------------
# Entry points
# ---------------------------------------------------------------------------


def _root(approved_root: object) -> Path:
    if not isinstance(approved_root, str | Path) or not str(approved_root):
        raise ModelImportActivityError("invalid_argument")
    return Path(approved_root)


def _run(
    session_factory: Callable[[], Session],
    token: object,
    *,
    origin: str,
    approved_root: object,
    now: object,
) -> ImportOutcome:
    if not isinstance(token, ClaimToken):
        raise ModelImportActivityError("invalid_argument")
    root = _root(approved_root)
    moment = _require_aware(now)
    inputs = _read_inputs(session_factory, token, origin)

    facts: _Facts | None = None
    state, code = STATE_SUCCEEDED, None
    try:
        if origin is ORIGIN_IMPORTED_WEIGHTS:
            facts = _weights_pipeline(inputs.artifact, root)
        else:
            assert inputs.parent_model_version_id is not None
            facts = _adapter_pipeline(
                inputs.artifact,
                inputs.parent,
                inputs.parent_model_version_id,
                token.organization_id,
                token.project_id,
                inputs.created_by,
                root,
            )
    except _Refusal as refusal:
        state, code, facts = refusal.state, refusal.code, None
    return _finish(
        session_factory,
        token,
        inputs,
        origin=origin,
        state=state,
        failure_code=code,
        facts=facts,
        now=moment,
    )


def validate_weights_import(
    session_factory: Callable[[], Session],
    token: ClaimToken,
    *,
    approved_root: str | Path,
    now: datetime,
) -> ImportOutcome:
    """Validates one claimed `imported_weights` job and writes its single terminal outcome.
    Raises `ModelImportActivityError` (`fenced_out` when this token's epoch was taken over, in
    which case nothing was written)."""
    return _run(
        session_factory,
        token,
        origin=ORIGIN_IMPORTED_WEIGHTS,
        approved_root=approved_root,
        now=now,
    )


def validate_adapter_import(
    session_factory: Callable[[], Session],
    token: ClaimToken,
    *,
    approved_root: str | Path,
    now: datetime,
) -> ImportOutcome:
    """Validates one claimed `imported_adapter` job against its exact parent base and writes its
    single terminal outcome. Same error contract as `validate_weights_import`."""
    return _run(
        session_factory,
        token,
        origin=ORIGIN_IMPORTED_ADAPTER,
        approved_root=approved_root,
        now=now,
    )
