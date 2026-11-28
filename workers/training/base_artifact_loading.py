"""Base artifact loading inside the training worker (FTF-0706; FINE_TUNING_FACTORY_RELEASE_1.md
Section 16 steps 4-5 and 10, Section 17 "Prefer safetensors ... do not deserialize arbitrary
uploaded pickle", Section 68.1 paragraph 3, Section 68.4, Section 77.3).

Refined from the packet path `services/training-worker/trainers/base_artifact_loading.py` to
`workers/training/` (Control-plane ruling 1): that is where the repository keeps training-worker
code (FTF-0701's `training_config_schema.py` lives here).

`load_base_artifact` takes three things the worker did not invent:

1. the job's `ResolvedTrainingConfig` (FTF-0701) -- its hash is re-verified first, and it names the
   exact artifact id, `weights_digest` and base/tokenizer revision the job was approved against;
2. the approved `TrainableArtifact` record (FTF-0301 row, digest resolved by FTF-0306), which must
   still be `ready`, license-accepted, carry the same digest and revision, and not allow remote
   code;
3. the object-store connection (`PrivateS3ConnectionConfig`, FTF-0303) the deployment gives the
   worker -- SeaweedFS in the integration jobs.

Artifact layout (no schema change). A `private_s3` base artifact's `source_uri` names its manifest
object, `<prefix>/sovra-artifact-manifest.json`, and `base_model_revision` is that object's exact S3
`versionId`. FTF-0303/FTF-0306 already define `weights_digest` for a `private_s3` artifact as the
sha256 of that one pinned object, so the manifest is authenticated by the digest the registry
already stores. The manifest lists every file of the artifact with its exact `versionId`, byte size
and sha256:

    {"schema": "sovra.base_artifact_manifest.v1",
     "files": [{"path": "config.json", "version_id": "...", "size_bytes": 123, "sha256": "..."}]}

Order (each step refuses with a typed `BaseArtifactLoadError` before the next one runs):

  1. config hash (`config_integrity_failed`), then job <-> record agreement: artifact id
     (`artifact_mismatch`), status `ready` (`artifact_not_approved`), license (FTF-0305 gate,
     `license_not_accepted`), digest present and equal (`digest_unknown`, `digest_mismatch`),
     revision and tokenizer revision equal (`revision_not_approved`), `private_s3` only
     (`unsupported_source_type`), `trust_remote_code_allowed` false (`remote_code_not_allowed`).
  2. FTF-0306 `verify_or_compute_weights_digest` re-fetches the manifest at its exact version and
     re-compares the digest ("compare hashes on every load"). The worker parses the one response
     body that comparison hashed (exactly one fetch is required), so the manifest it reads is the
     one the approved digest covers.
  3. strict manifest parse (`manifest_invalid`), then the FTF-H010-P3a exact file allow-list:
     pickle-capable files are `unsafe_weight_format`, anything else off the list is
     `artifact_file_unexpected`, a required file absent is `artifact_file_missing`. `tokenizer.json`
     is required (the worker loads a fast tokenizer only).
  4. the artifact prefix is listed; a current object the manifest does not name is
     `artifact_file_unexpected`, so the prefix holds exactly the approved set.
  5. every file is fetched at its pinned version through FTF-0303 (bucket and tenant prefix
     checks, exact size) and its sha256 compared (`artifact_file_missing`,
     `artifact_file_size_mismatch`, `artifact_file_digest_mismatch`).
  6. with every digest verified, and before anything is written or loaded: strict
     `config.json` / `tokenizer_config.json` (FTF-H010-P3a; `auto_map` or `trust_remote_code` in
     either is `remote_code_not_allowed`), every safetensors header (`weights_header_invalid`), and
     the shard index if present (`weights_index_inconsistent`).
  7. the files are written into a new private job directory (`workspace_unavailable` if it exists),
     the tokenizer is loaded from `tokenizer.json` by the Rust `tokenizers` library (pure data, no
     code), and checked against the model config and special tokens
     (`tokenizer_invalid`, `tokenizer_model_inconsistent`).

Fail closed (Law 17): an unreachable store, a refused or unexpected HTTP status, or a listing that
cannot be read is state `blocked`, code `object_store_unavailable`; every other refusal is state
`failed`. Nothing is ever returned as loaded unless every step passed, and a failed load removes
the job's partially written base directory.

Errors carry a stable code and a static reason only: no path, key, digest, field value or the text
of an underlying exception reaches the message or the exception chain (META-LEAK).

Scope disclosed: tensors are verified (safe format, exact header/size coverage, digests) but not
materialized into framework tensors -- that is the Axolotl load in FTF-R003/FTF-0708. Each object is
read whole by FTF-H006 `guarded_fetch`, so the worker's memory bounds the artifact size until a
streaming fetch exists.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import re
import shutil
import time
import xml.etree.ElementTree as ET
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

from packages.security.outbound_guard import GuardedResponse, OutboundGuardError, guarded_fetch
from packages.security.url_policy import ConnectionPolicy
from tokenizers import Tokenizer

from app.artifacts.artifact_schema import TrainableArtifact
from app.artifacts.base_model_revision_digest_resolver import (
    DigestMismatchError,
    PrivateS3ResolutionInputs,
    canonical_weights_digest,
    verify_or_compute_weights_digest,
)
from app.artifacts.license_acceptance_workflow import (
    LicenseNotAcceptedError,
    assert_license_accepted_for_use,
)
from app.artifacts.private_s3_artifact_resolver import (
    BucketMismatchError,
    CrossTenantPrefixError,
    ObjectVersionUnavailableError,
    PartialUploadError,
    PrivateS3ConnectionConfig,
    PrivateS3ResolutionError,
    default_private_s3_resolver_policy,
    resolve_private_s3_object,
)
from app.model_versions.import_artifact_validators import (
    KIND_WEIGHTS,
    MAX_SAFETENSORS_HEADER_BYTES,
    ImportArtifactValidationError,
    ModelConfig,
    SafetensorsHeader,
    TokenizerConfig,
    check_file_names,
    parse_safetensors_header,
    validate_config_json,
    validate_tokenizer_config_json,
)
from app.services.storage_semantics_client import sign_request
from workers.training.training_config_schema import ResolvedTrainingConfig, verify_config_sha256

__all__ = [
    "ERROR_CODES",
    "MANIFEST_NAME",
    "MANIFEST_SCHEMA",
    "STATE_BLOCKED",
    "STATE_FAILED",
    "BaseArtifactLoadError",
    "LoadedBaseArtifact",
    "load_base_artifact",
]

MANIFEST_NAME: Final = "sovra-artifact-manifest.json"
MANIFEST_SCHEMA: Final = "sovra.base_artifact_manifest.v1"
MANIFEST_MAX_BYTES: Final = 1 << 20
MANIFEST_MAX_FILES: Final = 256
LIST_MAX_PAGES: Final = 64
LIST_MAX_KEYS: Final = 4096

STATE_FAILED: Final = "failed"
STATE_BLOCKED: Final = "blocked"

ERROR_CODES: Final = (
    "artifact_file_digest_mismatch",
    "artifact_file_missing",
    "artifact_file_size_mismatch",
    "artifact_file_unexpected",
    "artifact_location_denied",
    "artifact_mismatch",
    "artifact_not_approved",
    "config_integrity_failed",
    "config_invalid",
    "digest_mismatch",
    "digest_unknown",
    "license_not_accepted",
    "manifest_invalid",
    "object_store_unavailable",
    "quantization_unverified",
    "remote_code_not_allowed",
    "revision_not_approved",
    "tokenizer_config_invalid",
    "tokenizer_invalid",
    "tokenizer_model_inconsistent",
    "unsafe_weight_format",
    "unsupported_source_type",
    "weights_header_invalid",
    "weights_index_inconsistent",
    "workspace_unavailable",
)

_SHA256_HEX = re.compile(r"[0-9a-f]{64}")
_VERSION_ID = re.compile(r"[A-Za-z0-9._\-+=/]{1,1024}")
_MANIFEST_FILE_KEYS = frozenset({"path", "version_id", "size_bytes", "sha256"})
_MAX_FILE_BYTES = 1 << 40
_TIMEOUT_SECONDS = 60.0
_S3_NS = "{http://s3.amazonaws.com/doc/2006-03-01/}"
_REMOTE_CODE_KEYS = ("auto_map", "trust_remote_code")
_FILE_CODES: Final[Mapping[str, str]] = {
    "unsafe_weight_format": "unsafe_weight_format",
    "unexpected_file": "artifact_file_unexpected",
    "required_file_missing": "artifact_file_missing",
}

_log = logging.getLogger("sovra.training.base_artifact")

FetchFn = Callable[..., GuardedResponse]


class BaseArtifactLoadError(Exception):
    """The base artifact was not loaded. `code` is one of `ERROR_CODES`; `state` is `failed` for a
    refusal and `blocked` when the object store could not give a definite answer."""

    def __init__(self, code: str, reason: str, *, state: str = STATE_FAILED) -> None:
        super().__init__(f"{code}: {reason}")
        self.code = code
        self.reason = reason
        self.state = state


def _fail(code: str, reason: str) -> BaseArtifactLoadError:
    return BaseArtifactLoadError(code, reason, state=STATE_FAILED)


def _blocked(reason: str) -> BaseArtifactLoadError:
    return BaseArtifactLoadError("object_store_unavailable", reason, state=STATE_BLOCKED)


@dataclass(frozen=True)
class ManifestFile:
    path: str
    version_id: str
    size_bytes: int
    sha256: str


@dataclass(frozen=True)
class LoadedBaseArtifact:
    """What the worker loaded. Only ever built after every step passed."""

    artifact_id: str
    base_revision: str
    weights_digest: str
    directory: Path
    files: tuple[ManifestFile, ...]
    model_config: ModelConfig
    tokenizer_config: TokenizerConfig
    tokenizer: Tokenizer
    weights: tuple[tuple[str, SafetensorsHeader], ...]


# -- step 1: job <-> approved record ---------------------------------------------------------


def _check_job_against_record(config: ResolvedTrainingConfig, artifact: TrainableArtifact) -> str:
    """Returns the bare sha256 hex the job and the record agree on."""
    try:
        intact = verify_config_sha256(config)
        doc = config.doc
        job_artifact_id = doc["base"]["trainable_artifact_id"]
        job_digest = doc["server"]["weights_digest"]
        job_revision = doc["server"]["base_revision"]
        tokenizer_revision = doc["tokenizer"]["revision"]
    except Exception:  # noqa: BLE001  # any malformed config is one coded refusal
        raise _fail("config_integrity_failed", "config_unreadable") from None
    if intact is not True:
        raise _fail("config_integrity_failed", "config_sha256_mismatch")
    if type(job_artifact_id) is not str or job_artifact_id != str(artifact.id):
        raise _fail("artifact_mismatch", "job_names_another_artifact")
    if artifact.status != "ready":
        raise _fail("artifact_not_approved", "artifact_not_ready")
    try:
        assert_license_accepted_for_use(artifact)
    except LicenseNotAcceptedError:
        raise _fail("license_not_accepted", "license_gate_refused") from None
    stored = artifact.weights_digest
    if type(stored) is not str or not stored.startswith("sha256:"):
        raise _fail("digest_unknown", "record_has_no_resolved_digest")
    stored_hex = stored[len("sha256:") :]
    if _SHA256_HEX.fullmatch(stored_hex) is None:
        raise _fail("digest_unknown", "record_digest_malformed")
    if type(job_digest) is not str or not hmac.compare_digest(job_digest, stored_hex):
        raise _fail("digest_mismatch", "job_digest_differs_from_record")
    revision = artifact.base_model_revision
    if (
        type(revision) is not str
        or type(job_revision) is not str
        or type(tokenizer_revision) is not str
        or job_revision != revision
        or tokenizer_revision != revision
    ):
        raise _fail("revision_not_approved", "revision_differs_from_record")
    if artifact.source_type != "private_s3":
        raise _fail("unsupported_source_type", "worker_loads_private_s3_only")
    if artifact.trust_remote_code_allowed is not False:
        raise _fail("remote_code_not_allowed", "record_allows_remote_code")
    return stored_hex


# -- object store ----------------------------------------------------------------------------


def _split_source_uri(source_uri: str) -> tuple[str, str]:
    if not source_uri.startswith("s3://"):
        raise _fail("artifact_location_denied", "source_uri_not_s3")
    bucket, _, key = source_uri[len("s3://") :].partition("/")
    prefix, _, name = key.rpartition("/")
    if not bucket or not prefix or name != MANIFEST_NAME:
        raise _fail("artifact_location_denied", "source_uri_is_not_a_manifest")
    return bucket, prefix + "/"


def _fetch_object(
    source_uri: str,
    version_id: str,
    *,
    connection: PrivateS3ConnectionConfig,
    expected_size: int | None,
    policy: ConnectionPolicy,
    fetch: FetchFn,
    missing_code: str,
) -> bytes:
    """One pinned object version through FTF-0303, returning the exact bytes it hashed."""
    captured: list[GuardedResponse] = []

    def capture(*args: Any, **kwargs: Any) -> GuardedResponse:
        response = fetch(*args, **kwargs)
        captured.append(response)
        return response

    try:
        result = resolve_private_s3_object(
            source_uri,
            version_id,
            connection=connection,
            expected_size_bytes=expected_size,
            policy=policy,
            fetch=capture,
            timeout=_TIMEOUT_SECONDS,
        )
    except (BucketMismatchError, CrossTenantPrefixError):
        raise _fail("artifact_location_denied", "outside_the_connection_scope") from None
    except ObjectVersionUnavailableError:
        raise _fail(missing_code, "object_version_absent") from None
    except PartialUploadError:
        raise _fail("artifact_file_size_mismatch", "size_differs_from_manifest") from None
    except PrivateS3ResolutionError:
        raise _blocked("object_fetch_failed") from None
    if len(captured) != 1:
        raise _blocked("object_fetch_failed")
    body = captured[0].body
    if hashlib.sha256(body).hexdigest() != result.sha256_hex:
        raise _blocked("object_fetch_failed")
    return body


def _list_current_keys(
    bucket: str,
    prefix: str,
    *,
    connection: PrivateS3ConnectionConfig,
    policy: ConnectionPolicy,
    fetch: FetchFn,
) -> set[str]:
    """Every current object key under `prefix` (ListObjectsV2, paginated, bounded)."""
    keys: set[str] = set()
    token: str | None = None
    for _page in range(LIST_MAX_PAGES):
        query = {"list-type": "2", "prefix": prefix}
        if token is not None:
            query["continuation-token"] = token
        signed = sign_request(
            method="GET",
            endpoint=connection.endpoint,
            credentials=connection.credentials,
            canonical_uri=f"/{bucket}",
            query=query,
        )
        try:
            response = fetch(
                signed.url, policy, method="GET", headers=signed.headers, timeout=_TIMEOUT_SECONDS
            )
        except OutboundGuardError:
            raise _blocked("listing_unreachable") from None
        if response.status != 200:
            raise _blocked("listing_refused")
        try:
            root = ET.fromstring(response.body)
            for contents in root.findall(f"{_S3_NS}Contents"):
                key = contents.findtext(f"{_S3_NS}Key")
                if key is None:
                    raise ValueError
                keys.add(key)
            truncated = root.findtext(f"{_S3_NS}IsTruncated") == "true"
            token = root.findtext(f"{_S3_NS}NextContinuationToken")
        except (ET.ParseError, ValueError):
            raise _blocked("listing_unreadable") from None
        if len(keys) > LIST_MAX_KEYS:
            raise _fail("artifact_file_unexpected", "prefix_holds_too_many_objects")
        if not truncated:
            return keys
        if not token:
            raise _blocked("listing_unreadable")
    raise _blocked("listing_unbounded")


# -- manifest --------------------------------------------------------------------------------


def _no_duplicates(pairs: list[tuple[str, object]]) -> dict[str, object]:
    out: dict[str, object] = {}
    for key, value in pairs:
        if key in out:
            raise ValueError
        out[key] = value
    return out


def _reject_constant(_name: str) -> object:
    raise ValueError


def _parse_manifest(body: bytes) -> tuple[ManifestFile, ...]:
    if len(body) > MANIFEST_MAX_BYTES:
        raise _fail("manifest_invalid", "manifest_too_large")
    try:
        doc = json.loads(
            body.decode("utf-8"),
            object_pairs_hook=_no_duplicates,
            parse_constant=_reject_constant,
        )
    except (UnicodeDecodeError, ValueError, RecursionError):
        raise _fail("manifest_invalid", "manifest_not_strict_json") from None
    if type(doc) is not dict or set(doc) != {"schema", "files"}:
        raise _fail("manifest_invalid", "manifest_keys")
    if doc["schema"] != MANIFEST_SCHEMA:
        raise _fail("manifest_invalid", "manifest_schema")
    entries = doc["files"]
    if type(entries) is not list or not 0 < len(entries) <= MANIFEST_MAX_FILES:
        raise _fail("manifest_invalid", "manifest_files")
    files: list[ManifestFile] = []
    for entry in entries:
        if type(entry) is not dict or set(entry) != _MANIFEST_FILE_KEYS:
            raise _fail("manifest_invalid", "manifest_entry_keys")
        path, version_id = entry["path"], entry["version_id"]
        size, sha256 = entry["size_bytes"], entry["sha256"]
        if (
            type(path) is not str
            or type(version_id) is not str
            or _VERSION_ID.fullmatch(version_id) is None
            or type(size) is not int
            or not 0 <= size <= _MAX_FILE_BYTES
            or type(sha256) is not str
            or _SHA256_HEX.fullmatch(sha256) is None
        ):
            raise _fail("manifest_invalid", "manifest_entry_values")
        files.append(ManifestFile(path=path, version_id=version_id, size_bytes=size, sha256=sha256))
    try:
        check_file_names([f.path for f in files], kind=KIND_WEIGHTS)
    except ImportArtifactValidationError as exc:
        code = _FILE_CODES.get(exc.code, "manifest_invalid")
        raise _fail(code, "file_set_not_allowed") from None
    if "tokenizer.json" not in {f.path for f in files}:
        raise _fail("artifact_file_missing", "tokenizer_json_required")
    return tuple(files)


# -- content checks (all digests already verified) -------------------------------------------


def _json_object(body: bytes) -> dict[str, object] | None:
    try:
        value = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError, RecursionError):
        return None
    return value if type(value) is dict else None


def _refuse_remote_code(name: str, body: bytes) -> None:
    obj = _json_object(body)
    if obj is not None and any(key in obj for key in _REMOTE_CODE_KEYS):
        raise _fail("remote_code_not_allowed", f"{name.split('.')[0]}_declares_custom_code")


def _validated_contents(
    contents: Mapping[str, bytes],
) -> tuple[ModelConfig, TokenizerConfig, tuple[tuple[str, SafetensorsHeader], ...]]:
    _refuse_remote_code("config.json", contents["config.json"])
    _refuse_remote_code("tokenizer_config.json", contents["tokenizer_config.json"])
    try:
        model_config = validate_config_json(contents["config.json"])
        tokenizer_config = validate_tokenizer_config_json(contents["tokenizer_config.json"])
    except ImportArtifactValidationError as exc:
        raise _fail(exc.code, "metadata_rejected") from None
    weights: list[tuple[str, SafetensorsHeader]] = []
    for name in sorted(n for n in contents if n.endswith(".safetensors")):
        body = contents[name]
        try:
            header = parse_safetensors_header(
                body[: 8 + MAX_SAFETENSORS_HEADER_BYTES], file_size=len(body)
            )
        except ImportArtifactValidationError:
            raise _fail("weights_header_invalid", "safetensors_header_rejected") from None
        weights.append((name, header))
    index = contents.get("model.safetensors.index.json")
    if index is not None:
        _check_index(index, weights)
    return model_config, tokenizer_config, tuple(weights)


def _check_index(index: bytes, weights: list[tuple[str, SafetensorsHeader]]) -> None:
    """The index's weight_map must name exactly the tensors each shard really holds."""
    obj = _json_object(index)
    weight_map = obj.get("weight_map") if obj is not None else None
    if type(weight_map) is not dict:
        raise _fail("weights_index_inconsistent", "weight_map_missing")
    actual = {tensor: shard for shard, header in weights for tensor, _info in header.tensors}
    if weight_map != actual:
        raise _fail("weights_index_inconsistent", "weight_map_differs_from_shards")


def _check_tokenizer(
    tokenizer: Tokenizer, model_config: ModelConfig, tokenizer_config: TokenizerConfig
) -> None:
    if tokenizer.get_vocab_size(with_added_tokens=True) > model_config.vocab_size:
        raise _fail("tokenizer_model_inconsistent", "tokenizer_larger_than_model_vocab")
    declared = [content for _key, content in tokenizer_config.special_tokens]
    declared += list(tokenizer_config.additional_special_tokens)
    for content in declared:
        token_id = tokenizer.token_to_id(content)
        if token_id is None or token_id >= model_config.vocab_size:
            raise _fail("tokenizer_model_inconsistent", "special_token_not_in_vocabulary")


def _write_private(directory: Path, contents: Mapping[str, bytes]) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    for name, body in contents.items():
        fd = os.open(directory / name, flags, 0o600)
        with os.fdopen(fd, "wb") as handle:
            handle.write(body)
            handle.flush()
            os.fsync(handle.fileno())


# -- entry point -----------------------------------------------------------------------------


def load_base_artifact(
    config: ResolvedTrainingConfig,
    artifact: TrainableArtifact,
    *,
    connection: PrivateS3ConnectionConfig,
    directory: Path,
    policy: ConnectionPolicy | None = None,
    fetch: FetchFn = guarded_fetch,
) -> LoadedBaseArtifact:
    """Loads the exact approved base model and tokenizer revision for `config` into `directory`
    (which must not exist yet) and returns it, or raises `BaseArtifactLoadError`. Nothing from
    the artifact is written or loaded until every digest has been verified."""
    started = time.monotonic()
    active_policy = policy or default_private_s3_resolver_policy()
    try:
        loaded = _load(config, artifact, connection, directory, active_policy, fetch)
    except BaseArtifactLoadError as exc:
        _log.warning(
            "base_artifact_load_refused",
            extra={
                "artifact_id": str(artifact.id),
                "config_sha256": config.config_sha256,
                "code": exc.code,
                "state": exc.state,
                "elapsed_ms": int((time.monotonic() - started) * 1000),
            },
        )
        raise
    _log.info(
        "base_artifact_loaded",
        extra={
            "artifact_id": loaded.artifact_id,
            "config_sha256": config.config_sha256,
            "file_count": len(loaded.files),
            "elapsed_ms": int((time.monotonic() - started) * 1000),
        },
    )
    return loaded


def _load(
    config: ResolvedTrainingConfig,
    artifact: TrainableArtifact,
    connection: PrivateS3ConnectionConfig,
    directory: Path,
    policy: ConnectionPolicy,
    fetch: FetchFn,
) -> LoadedBaseArtifact:
    digest_hex = _check_job_against_record(config, artifact)
    bucket, prefix = _split_source_uri(artifact.source_uri)

    # Step 2: FTF-0306 re-verification of the pinned manifest, capturing the exact bytes it hashed.
    manifest_bodies: list[bytes] = []

    def manifest_fetch(*args: Any, **kwargs: Any) -> GuardedResponse:
        response = fetch(*args, **kwargs)
        manifest_bodies.append(response.body)
        return response

    try:
        verify_or_compute_weights_digest(
            artifact,
            PrivateS3ResolutionInputs(
                connection=connection,
                policy=policy,
                fetch=manifest_fetch,
                timeout=_TIMEOUT_SECONDS,
            ),
        )
    except DigestMismatchError:
        raise _fail("digest_mismatch", "manifest_differs_from_approved_digest") from None
    except (BucketMismatchError, CrossTenantPrefixError):
        raise _fail("artifact_location_denied", "outside_the_connection_scope") from None
    except ObjectVersionUnavailableError:
        raise _fail("artifact_file_missing", "manifest_version_absent") from None
    except PrivateS3ResolutionError:
        raise _blocked("manifest_fetch_failed") from None
    if len(manifest_bodies) != 1:
        raise _blocked("manifest_fetch_failed")
    # FTF-0306 hashed exactly this one response body and found it equal to the approved digest.
    manifest_body = manifest_bodies[0]

    # Step 3-4: the manifest, then the store holds exactly the approved set.
    files = _parse_manifest(manifest_body)
    expected_keys = {prefix + f.path for f in files} | {prefix + MANIFEST_NAME}
    current_keys = _list_current_keys(
        bucket, prefix, connection=connection, policy=policy, fetch=fetch
    )
    if current_keys - expected_keys:
        raise _fail("artifact_file_unexpected", "store_holds_unlisted_object")

    # Step 5: every file at its pinned version, size and digest verified before any use.
    contents: dict[str, bytes] = {}
    for entry in files:
        body = _fetch_object(
            f"s3://{bucket}/{prefix}{entry.path}",
            entry.version_id,
            connection=connection,
            expected_size=entry.size_bytes,
            policy=policy,
            fetch=fetch,
            missing_code="artifact_file_missing",
        )
        if not hmac.compare_digest(hashlib.sha256(body).hexdigest(), entry.sha256):
            raise _fail("artifact_file_digest_mismatch", "file_differs_from_manifest")
        contents[entry.path] = body

    # Step 6: content checks on verified bytes only.
    model_config, tokenizer_config, weights = _validated_contents(contents)

    # Step 7: write into a new private directory, then load the tokenizer from it.
    try:
        directory.mkdir(mode=0o700, parents=False, exist_ok=False)
    except OSError:
        raise _fail("workspace_unavailable", "base_directory_not_creatable") from None
    try:
        try:
            _write_private(directory, contents)
        except OSError:
            raise _fail("workspace_unavailable", "base_file_not_writable") from None
        try:
            tokenizer = Tokenizer.from_file(str(directory / "tokenizer.json"))
        except Exception:  # noqa: BLE001  # the Rust parser raises a bare Exception
            raise _fail("tokenizer_invalid", "tokenizer_json_unloadable") from None
        _check_tokenizer(tokenizer, model_config, tokenizer_config)
    except BaseException:
        shutil.rmtree(directory, ignore_errors=True)
        raise
    return LoadedBaseArtifact(
        artifact_id=str(artifact.id),
        base_revision=artifact.base_model_revision,
        weights_digest=canonical_weights_digest(digest_hex),
        directory=directory,
        files=files,
        model_config=model_config,
        tokenizer_config=tokenizer_config,
        tokenizer=tokenizer,
        weights=weights,
    )
