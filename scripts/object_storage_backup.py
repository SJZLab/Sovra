#!/usr/bin/env python3
"""Object-storage backup of dataset/model/result manifests and their exact
immutable blobs (FTF-1602, Section 48 / 71.2).

A stdlib-only tool (it reuses `apps/api/app/services/storage_semantics_client.py`'s
SigV4 `S3Client`; `boto3` stays ask-first) that backs up every blob a set of
manifests references, exactly as recorded:

- each blob is fetched by its recorded `(key, version_id)`, never "whatever is
  latest", and its size and SHA-256 are verified against the manifest;
- the backup is written content-addressed (`<prefix>/<backup_id>/blobs/<sha256>`)
  to a target bucket, immediately read back and re-hashed, and given a
  COMPLIANCE retention that is read back too (Section 71.2: a backup that was
  never verified does not count);
- the backup record is written last, so an interrupted run leaves blobs but no
  record and is never reported as a backup.

Completeness is fail-closed (negative_acceptance): a missing shard, a stale
object version (the recorded version is not the key's current one, was deleted
or never existed) or a lost encryption key reference (an encrypted blob whose
key reference is absent from the caller's recoverable set) raises
`BackupIncompleteError` naming every issue, before anything is written. Issue
text never contains object content or key material.

The record is self-sufficient (Section 48: a backup that has never been restored does
not count): it carries every manifest entry (key, version_id, kind, artifact_id, shard
layout, encryption key reference, tenant, object metadata), so `restore_backup` can
rebuild the objects into a clean bucket from the backup alone and prove bytes, sha256,
key/version mapping and metadata. A corrupted, partial or same-size-swapped backup is
refused before anything is written (Law 17).

Tenant scoping: every `BlobRef` names its `organization_id`; one backup holds exactly one
tenant, every key must live under `tenants/<organization_id>/`, backups are stored under
`<prefix>/<organization_id>/<backup_id>`, and verify/restore refuse a record of another
tenant and never write outside the tenant's own key prefix.

Scope, disclosed: the BackupRecord/RestoreRecord JSON Schemas in
`packages/contracts/jsonschema/16-domain.schema.json` are FTF-1601's and frozen
(`shared_lock_required` is empty here), so this task does not extend them; the
records below are this module's own typed output. Restore *automation* (scheduling,
runbooks) is FTF-1603.

CLI:
    python scripts/object_storage_backup.py backup --manifest m.json \\
        --source-bucket src --target-bucket dst --known-key-ref kms/a ...
    python scripts/object_storage_backup.py verify --target-bucket dst --backup-id <id> \\
        --organization-id <org>
    python scripts/object_storage_backup.py restore --target-bucket dst --restore-bucket clean \\
        --backup-id <id> --organization-id <org>
Endpoint and credentials come from the SOVRA_STORAGE_S3_* variables named in
infra/internal/storage-profile.yaml.
"""

from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
import os
import re
import sys
import uuid
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Protocol

_ROOT = Path(__file__).resolve().parent.parent
for _path in (str(_ROOT), str(_ROOT / "apps" / "api")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from app.services.storage_semantics_client import (  # noqa: E402
    S3Client,
    S3Credentials,
    S3Endpoint,
    S3Error,
)

__all__ = [
    "BackupIncompleteError",
    "BackupRecord",
    "BlobRef",
    "ObjectStorageBackupError",
    "ObjectStore",
    "RestoreRecord",
    "S3ObjectStore",
    "VersionInfo",
    "create_backup",
    "restore_backup",
    "verify_backup",
]

KINDS = frozenset({"dataset", "model", "result"})
MAX_RETENTION_DAYS = 3650
_SHA256 = re.compile(r"[0-9a-fA-F]{64}")
_TEXT = re.compile(r"\S(?:.*\S)?", re.DOTALL)
RECORD_NAME = "backup-record.json"
_USER_METADATA = "x-amz-meta-"


class ObjectStorageBackupError(RuntimeError):
    """The backup could not produce verifiable evidence (never a partial success)."""


class BackupIncompleteError(ObjectStorageBackupError):
    """One or more recorded blobs could not be backed up exactly; `issues` names each."""

    def __init__(self, issues: Sequence[str]) -> None:
        self.issues = tuple(issues)
        super().__init__("backup incomplete: " + "; ".join(self.issues))


@dataclasses.dataclass(frozen=True)
class BlobRef:
    """One blob exactly as a dataset/model/result manifest recorded it."""

    kind: str
    key: str
    version_id: str
    sha256: str
    size_bytes: int
    artifact_id: str
    organization_id: str
    shard_index: int = 0
    shard_count: int = 1
    encrypted: bool = False
    encryption_key_ref: str | None = None


@dataclasses.dataclass(frozen=True)
class VersionInfo:
    version_id: str
    is_latest: bool
    is_delete_marker: bool


class ObjectStore(Protocol):
    def list_versions(self, key: str) -> list[VersionInfo]: ...

    def get_version(self, key: str, version_id: str) -> bytes: ...

    def version_metadata(self, key: str, version_id: str) -> dict[str, str]: ...

    def put_retained(self, key: str, body: bytes, *, retain_until: datetime) -> str: ...

    def put_restored(self, key: str, body: bytes, *, metadata: Mapping[str, str]) -> str: ...

    def get_latest(self, key: str) -> bytes: ...

    def retention_until(self, key: str) -> datetime | None: ...


@dataclasses.dataclass(frozen=True)
class BackupEntry:
    """One manifest entry exactly as backed up, with the source object's metadata."""

    kind: str
    key: str
    version_id: str
    sha256: str
    size_bytes: int
    artifact_id: str
    organization_id: str
    shard_index: int
    shard_count: int
    encrypted: bool
    encryption_key_ref: str | None
    metadata: tuple[tuple[str, str], ...]  # sorted (name, value)


@dataclasses.dataclass(frozen=True)
class BackupRecord:
    backup_id: str
    organization_id: str
    created_at: str
    retain_until: str
    manifest_sha256: str
    object_count: int
    blob_count: int
    total_bytes: int
    blobs: tuple[tuple[str, int], ...]  # (sha256, size_bytes), sorted
    entries: tuple[BackupEntry, ...]  # sorted by (key, version_id)

    def to_json(self) -> str:
        return json.dumps(dataclasses.asdict(self), sort_keys=True, separators=(",", ":"))


@dataclasses.dataclass(frozen=True)
class RestoreRecord:
    """Proof of a restore into a clean bucket: `restored` maps every backed-up
    `(key, version_id)` to the version id the restored object now has."""

    backup_id: str
    organization_id: str
    manifest_sha256: str
    object_count: int
    total_bytes: int
    restored: tuple[tuple[str, str, str], ...]  # (key, backed-up version, restored version)

    def to_json(self) -> str:
        return json.dumps(dataclasses.asdict(self), sort_keys=True, separators=(",", ":"))


def _storage_failure(what: str, exc: Exception) -> ObjectStorageBackupError:
    """A storage read/write failure as this module's own error (never a raw TimeoutError,
    OSError, HTTP or S3 exception, and never the server's message)."""
    detail = f"S3 status {exc.status_code}" if isinstance(exc, S3Error) else type(exc).__name__
    return ObjectStorageBackupError(f"{what}: storage request failed ({detail})")


def _io(what: str, fn: Any, *args: Any, **kwargs: Any) -> Any:
    try:
        return fn(*args, **kwargs)
    except ObjectStorageBackupError:
        raise
    except Exception as exc:
        raise _storage_failure(what, exc) from exc


def _digest(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def _is_text(value: object) -> bool:
    # Exact `str` only: a subclass could override startswith/split/__eq__/__hash__ and slip a
    # tenant-prefix or identity check (tenant-guard bypass).
    return type(value) is str and _TEXT.fullmatch(value) is not None


def _is_sha256(value: object) -> bool:
    return type(value) is str and _SHA256.fullmatch(value) is not None


def _is_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _tenant_prefix(organization_id: str) -> str:
    return f"tenants/{organization_id}/"


def _validate_ref(ref: object) -> BlobRef:
    if type(ref) is not BlobRef:
        raise ObjectStorageBackupError("manifest entries must be BlobRef")
    if type(ref.kind) is not str or ref.kind not in KINDS:
        raise ObjectStorageBackupError("blob kind must be dataset, model or result")
    for name in ("key", "version_id", "artifact_id", "organization_id"):
        if not _is_text(getattr(ref, name)):
            raise ObjectStorageBackupError(f"blob {name} must be a non-empty trimmed string")
    if "/" in ref.organization_id:
        raise ObjectStorageBackupError("blob organization_id must be a plain path segment")
    if any(part in ("", ".", "..") for part in ref.key.split("/")):
        raise ObjectStorageBackupError("blob key must not contain empty or dot path segments")
    if not ref.key.startswith(_tenant_prefix(ref.organization_id)):
        raise ObjectStorageBackupError("blob key is outside its organization's tenant prefix")
    if not _is_sha256(ref.sha256):
        raise ObjectStorageBackupError("blob sha256 must be 64 hex characters")
    if not _is_int(ref.size_bytes) or ref.size_bytes < 0:
        raise ObjectStorageBackupError("blob size_bytes must be a non-negative integer")
    if not _is_int(ref.shard_count) or ref.shard_count < 1:
        raise ObjectStorageBackupError("blob shard_count must be an integer >= 1")
    if not _is_int(ref.shard_index) or not 0 <= ref.shard_index < ref.shard_count:
        raise ObjectStorageBackupError("blob shard_index must be within 0..shard_count-1")
    if not isinstance(ref.encrypted, bool):
        raise ObjectStorageBackupError("blob encrypted must be a bool")
    if ref.encryption_key_ref is not None and not _is_text(ref.encryption_key_ref):
        raise ObjectStorageBackupError("blob encryption_key_ref must be a non-empty string or None")
    return ref


def _validate_manifest(refs: object) -> tuple[BlobRef, ...]:
    if not isinstance(refs, Sequence) or isinstance(refs, str | bytes):
        raise ObjectStorageBackupError("manifest must be a sequence of BlobRef")
    checked = tuple(_validate_ref(ref) for ref in refs)
    if not checked:
        raise ObjectStorageBackupError("manifest must reference at least one blob")
    if len({ref.organization_id for ref in checked}) != 1:
        raise ObjectStorageBackupError("a backup must hold exactly one organization's blobs")
    seen: set[tuple[str, str]] = set()
    for ref in checked:
        identity = (ref.key, ref.version_id)
        if identity in seen:
            raise ObjectStorageBackupError("manifest lists the same key and version twice")
        seen.add(identity)
    return checked


def _manifest_sha256(entries: Sequence[BackupEntry]) -> str:
    canonical = json.dumps(
        sorted((dataclasses.asdict(e) for e in entries), key=lambda d: (d["key"], d["version_id"])),
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


def _shard_issues(refs: Sequence[BlobRef]) -> list[str]:
    issues: list[str] = []
    groups: dict[tuple[str, str], list[BlobRef]] = {}
    for ref in refs:
        groups.setdefault((ref.kind, ref.artifact_id), []).append(ref)
    for (kind, artifact_id), group in sorted(groups.items()):
        counts = {r.shard_count for r in group}
        if len(counts) != 1:
            issues.append(f"{kind} {artifact_id}: shards disagree on shard_count")
            continue
        expected = next(iter(counts))
        present = {r.shard_index for r in group}
        missing = sorted(set(range(expected)) - present)
        if missing:
            issues.append(f"{kind} {artifact_id}: missing shards {missing} of {expected}")
        if len(present) != len(group):
            issues.append(f"{kind} {artifact_id}: duplicate shard index")
    return issues


def _key_issues(refs: Sequence[BlobRef], known_key_refs: frozenset[str]) -> list[str]:
    issues: list[str] = []
    for ref in refs:
        if ref.encrypted and ref.encryption_key_ref is None:
            issues.append(f"{ref.key}@{ref.version_id}: encrypted blob has no key reference")
        elif ref.encrypted and ref.encryption_key_ref not in known_key_refs:
            issues.append(
                f"{ref.key}@{ref.version_id}: encryption key reference is not recoverable"
            )
    return issues


def _version_issue(ref: BlobRef, versions: Sequence[VersionInfo]) -> str | None:
    match = [v for v in versions if v.version_id == ref.version_id]
    if not match:
        return f"{ref.key}@{ref.version_id}: recorded version does not exist"
    if match[0].is_delete_marker:
        return f"{ref.key}@{ref.version_id}: recorded version is a delete marker"
    latest = [v for v in versions if v.is_latest]
    if len(latest) != 1 or latest[0].version_id != ref.version_id:
        return f"{ref.key}@{ref.version_id}: stale, a newer version or delete marker exists"
    return None


def create_backup(
    manifest: Sequence[BlobRef],
    *,
    source: ObjectStore,
    target: ObjectStore,
    known_key_refs: frozenset[str],
    retain_days: int,
    now: datetime | None = None,
    backup_id: str | None = None,
    prefix: str = "backups",
) -> BackupRecord:
    """Back up every blob `manifest` references; raise unless complete and verified."""
    refs = _validate_manifest(manifest)
    if type(known_key_refs) is not frozenset or not all(_is_text(k) for k in known_key_refs):
        raise ObjectStorageBackupError("known_key_refs must be a frozenset of non-empty strings")
    if not _is_int(retain_days) or not 1 <= retain_days <= MAX_RETENTION_DAYS:
        raise ObjectStorageBackupError(f"retain_days must be an integer in 1..{MAX_RETENTION_DAYS}")
    created = now if now is not None else datetime.now(UTC)
    if created.tzinfo is None:
        raise ObjectStorageBackupError("now must be timezone-aware")
    identity = backup_id if backup_id is not None else str(uuid.uuid4())
    if not _is_text(identity) or "/" in identity or not _is_text(prefix) or prefix.endswith("/"):
        raise ObjectStorageBackupError("backup_id and prefix must be plain path segments")

    issues = [*_shard_issues(refs), *_key_issues(refs, known_key_refs)]
    fetched: dict[str, bytes] = {}
    entries: list[BackupEntry] = []
    for ref in refs:
        issue = _version_issue(ref, source.list_versions(ref.key))
        if issue is not None:
            issues.append(issue)
            continue
        body = source.get_version(ref.key, ref.version_id)
        if len(body) != ref.size_bytes:
            issues.append(f"{ref.key}@{ref.version_id}: size differs from the manifest")
        elif _digest(body) != ref.sha256:
            issues.append(f"{ref.key}@{ref.version_id}: sha256 differs from the manifest")
        else:
            fetched[_digest(body)] = body
            entries.append(_entry(ref, source.version_metadata(ref.key, ref.version_id)))
    if issues:
        raise BackupIncompleteError(issues)

    organization_id = refs[0].organization_id
    retain_until = created + timedelta(days=retain_days)
    base = f"{prefix}/{organization_id}/{identity}"
    for digest, body in sorted(fetched.items()):
        _put_verified(target, f"{base}/blobs/{digest}", body, retain_until)
    ordered = tuple(sorted(entries, key=lambda e: (e.key, e.version_id)))
    record = BackupRecord(
        backup_id=identity,
        organization_id=organization_id,
        created_at=created.strftime("%Y-%m-%dT%H:%M:%SZ"),
        retain_until=retain_until.strftime("%Y-%m-%dT%H:%M:%SZ"),
        manifest_sha256=_manifest_sha256(ordered),
        object_count=len(ordered),
        blob_count=len(fetched),
        total_bytes=sum(len(b) for b in fetched.values()),
        blobs=tuple(sorted((d, len(b)) for d, b in fetched.items())),
        entries=ordered,
    )
    _put_verified(target, f"{base}/{RECORD_NAME}", record.to_json().encode(), retain_until)
    return record


def _entry(ref: BlobRef, metadata: Mapping[str, str]) -> BackupEntry:
    if not isinstance(metadata, Mapping) or not all(
        isinstance(k, str) and isinstance(v, str) for k, v in metadata.items()
    ):
        raise ObjectStorageBackupError("object metadata must be a mapping of strings")
    return BackupEntry(
        kind=ref.kind,
        key=ref.key,
        version_id=ref.version_id,
        sha256=ref.sha256.lower(),
        size_bytes=ref.size_bytes,
        artifact_id=ref.artifact_id,
        organization_id=ref.organization_id,
        shard_index=ref.shard_index,
        shard_count=ref.shard_count,
        encrypted=ref.encrypted,
        encryption_key_ref=ref.encryption_key_ref,
        metadata=tuple(sorted(metadata.items())),
    )


def _put_verified(target: ObjectStore, key: str, body: bytes, retain_until: datetime) -> None:
    target.put_retained(key, body, retain_until=retain_until)
    if target.get_latest(key) != body:
        raise ObjectStorageBackupError(f"{key}: read-back differs from what was written")
    held = target.retention_until(key)
    if held is None or held < retain_until.replace(microsecond=0):
        raise ObjectStorageBackupError(f"{key}: retention was not applied")


_TIME_FORMAT = "%Y-%m-%dT%H:%M:%SZ"
_RECORD_FIELDS = frozenset(f.name for f in dataclasses.fields(BackupRecord))
_ENTRY_FIELDS = frozenset(f.name for f in dataclasses.fields(BackupEntry))


def _malformed(where: str) -> ObjectStorageBackupError:
    return ObjectStorageBackupError(f"backup record is malformed: {where}")


def _pair(value: object, second: Any, where: str) -> tuple[str, Any]:
    if (
        type(value) is not list
        or len(value) != 2
        or type(value[0]) is not str
        or not second(value[1])
    ):
        raise _malformed(where)
    return value[0], value[1]


def _parse_entry(item: object, index: int) -> BackupEntry:
    where = f"entries[{index}]"
    if type(item) is not dict or set(item) != _ENTRY_FIELDS:
        raise _malformed(where)
    # Exact types only here; value rules (trimmed, tenant prefix, hex) are `_record_issues`'.
    for name in ("kind", "key", "version_id", "artifact_id", "organization_id", "sha256"):
        if type(item[name]) is not str:
            raise _malformed(f"{where}.{name}")
    for name in ("size_bytes", "shard_index", "shard_count"):
        if not _is_int(item[name]):
            raise _malformed(f"{where}.{name}")
    if type(item["encrypted"]) is not bool:
        raise _malformed(f"{where}.encrypted")
    if item["encryption_key_ref"] is not None and type(item["encryption_key_ref"]) is not str:
        raise _malformed(f"{where}.encryption_key_ref")
    if type(item["metadata"]) is not list:
        raise _malformed(f"{where}.metadata")
    metadata = tuple(
        _pair(pair, lambda v: type(v) is str, f"{where}.metadata") for pair in item["metadata"]
    )
    return BackupEntry(**{**item, "metadata": metadata})


def _parse_record(raw: bytes) -> BackupRecord:
    try:
        data = json.loads(raw)
    except (ValueError, TypeError) as exc:
        raise ObjectStorageBackupError("backup record is missing or unreadable") from exc
    if type(data) is not dict or set(data) != _RECORD_FIELDS:
        raise _malformed("fields")
    for name in ("backup_id", "organization_id", "manifest_sha256"):
        if type(data[name]) is not str:
            raise _malformed(name)
    for name in ("created_at", "retain_until"):
        try:
            datetime.strptime(data[name], _TIME_FORMAT)
        except (ValueError, TypeError) as exc:
            raise _malformed(name) from exc
    for name in ("object_count", "blob_count", "total_bytes"):
        if not _is_int(data[name]):
            raise _malformed(name)
    if type(data["blobs"]) is not list or type(data["entries"]) is not list:
        raise _malformed("blobs and entries must be lists")
    blobs = tuple(_pair(pair, _is_int, "blobs") for pair in data["blobs"])
    entries = tuple(_parse_entry(item, i) for i, item in enumerate(data["entries"]))
    return BackupRecord(**{**data, "blobs": blobs, "entries": entries})


def _load_record(
    target: ObjectStore, organization_id: str, backup_id: str, prefix: str, now: datetime
) -> tuple[BackupRecord, str]:
    if not _is_text(backup_id) or "/" in backup_id:
        raise ObjectStorageBackupError("backup_id must be a plain path segment")
    if not _is_text(organization_id) or "/" in organization_id:
        raise ObjectStorageBackupError("organization_id must be a plain path segment")
    base = f"{prefix}/{organization_id}/{backup_id}"
    record_key = f"{base}/{RECORD_NAME}"
    # Pin the record: it is the one object the whole backup is trusted through, so it must
    # be a single, undeleted, still-retained version. A newer, re-hashed record version
    # (a shrunk backup with consistent totals) is refused, not read as "the latest".
    versions = _io("backup record", target.list_versions, record_key)
    if not versions:
        raise ObjectStorageBackupError("backup record is missing or unreadable")
    if len(versions) != 1:
        raise ObjectStorageBackupError("backup record has more than one version")
    if versions[0].is_delete_marker or not versions[0].is_latest:
        raise ObjectStorageBackupError("backup record version is a delete marker or not latest")
    try:
        raw = target.get_latest(record_key)
    except S3Error as exc:
        raise ObjectStorageBackupError("backup record is missing or unreadable") from exc
    except ObjectStorageBackupError:
        raise
    except Exception as exc:
        raise _storage_failure("backup record", exc) from exc
    record = _parse_record(raw)
    if record.backup_id != backup_id:
        raise ObjectStorageBackupError("backup record belongs to a different backup")
    if record.organization_id != organization_id:
        raise ObjectStorageBackupError("backup record belongs to a different organization")
    held = _io("backup record retention", target.retention_until, record_key)
    declared = datetime.strptime(record.retain_until, _TIME_FORMAT).replace(tzinfo=UTC)
    if held is None or held < declared or held <= now:
        raise BackupIncompleteError(["backup record is not under retention as its record declares"])
    return record, base


def _record_issues(record: BackupRecord) -> list[str]:
    """Internal-consistency checks: the record's counts, bytes, blob list and manifest hash."""
    if not all(isinstance(d, str) and _SHA256.fullmatch(d) and s >= 0 for d, s in record.blobs):
        raise ObjectStorageBackupError("backup record lists a malformed blob")
    issues: list[str] = []
    for entry in record.entries:
        if entry.organization_id != record.organization_id or not entry.key.startswith(
            _tenant_prefix(record.organization_id)
        ):
            issues.append(f"{entry.key}@{entry.version_id}: entry is outside the record's tenant")
        if any(part in ("", ".", "..") for part in entry.key.split("/")):
            issues.append(f"{entry.key}@{entry.version_id}: entry key has dot path segments")
    blob_sizes = dict(record.blobs)
    if len(blob_sizes) != len(record.blobs):
        issues.append("record lists the same blob twice")
    entry_blobs = {e.sha256: e.size_bytes for e in record.entries}
    if entry_blobs != blob_sizes:
        issues.append("record blob list disagrees with its manifest entries")
    if len(record.blobs) != record.blob_count:
        issues.append("record blob_count disagrees with its blob list")
    if len(record.entries) != record.object_count:
        issues.append("record object_count disagrees with its manifest entries")
    if sum(size for _, size in record.blobs) != record.total_bytes:
        issues.append("record total_bytes disagrees with its blob list")
    if _manifest_sha256(record.entries) != record.manifest_sha256:
        issues.append("record manifest_sha256 disagrees with its manifest entries")
    return issues


def _blob_issue(body: bytes, digest: str, size: int) -> str | None:
    if len(body) != size or _digest(body) != digest:
        return f"{digest}: blob no longer matches its recorded hash"
    return None


def verify_backup(
    target: ObjectStore,
    backup_id: str,
    *,
    organization_id: str,
    expected_manifest_sha256: str | None = None,
    prefix: str = "backups",
    now: datetime | None = None,
) -> BackupRecord:
    """Re-read a backup and prove its record is consistent and every blob is present,
    hashed as recorded and still retained."""
    current = now if now is not None else datetime.now(UTC)
    record, base = _load_record(target, organization_id, backup_id, prefix, current)
    issues = _record_issues(record)
    if expected_manifest_sha256 is not None and record.manifest_sha256 != expected_manifest_sha256:
        issues.append("record manifest_sha256 differs from the expected manifest")
    for digest, size in record.blobs:
        key = f"{base}/blobs/{digest}"
        # Existence is decided by listing: a GET of an absent key can hang until the client
        # times out on a real server, which would hide "missing" behind a storage failure.
        if not _io(f"{digest}: blob", target.list_versions, key):
            issues.append(f"{digest}: blob is missing")
            continue
        try:
            body = target.get_latest(key)
        except S3Error:
            issues.append(f"{digest}: blob is missing")
            continue
        except ObjectStorageBackupError:
            raise
        except Exception as exc:
            raise _storage_failure(f"{digest}: blob", exc) from exc
        mismatch = _blob_issue(body, digest, size)
        if mismatch is not None:
            issues.append(mismatch)
        held = _io(f"{digest}: blob retention", target.retention_until, key)
        if held is None or held <= current:
            issues.append(f"{digest}: blob is not under retention")
    if issues:
        raise BackupIncompleteError(issues)
    return record


def restore_backup(
    backup: ObjectStore,
    destination: ObjectStore,
    backup_id: str,
    *,
    organization_id: str,
    expected_manifest_sha256: str | None = None,
    prefix: str = "backups",
    now: datetime | None = None,
) -> RestoreRecord:
    """Rebuild every backed-up object into a clean `destination` from the backup alone.

    The whole backup is verified before the first write (nothing is restored from a
    corrupted, partial or swapped backup), the destination must hold none of the keys, each
    blob is re-checked as it is read, and each restored object is read back and compared on
    bytes, sha256 and metadata. A blob swapped mid-restore aborts it (objects already
    written stay, visibly; there is no silent success)."""
    record = verify_backup(
        backup,
        backup_id,
        organization_id=organization_id,
        expected_manifest_sha256=expected_manifest_sha256,
        prefix=prefix,
        now=now,
    )
    base = f"{prefix}/{organization_id}/{backup_id}"
    dirty = [
        f"{entry.key}: destination is not clean"
        for entry in record.entries
        if _io(f"{entry.key}: destination", destination.list_versions, entry.key)
    ]
    if dirty:
        raise BackupIncompleteError(dirty)

    restored: list[tuple[str, str, str]] = []
    for entry in record.entries:
        # Re-read each blob just before its write: a blob swapped after verification is
        # refused here (blobs are COMPLIANCE-retained, so this is a defence in depth).
        body = _io(f"{entry.key}: backup blob", backup.get_latest, f"{base}/blobs/{entry.sha256}")
        if _blob_issue(body, entry.sha256, entry.size_bytes) is not None:
            raise ObjectStorageBackupError(
                f"{entry.key}: backup blob changed between verification and restore"
            )
        new_version = _io(
            f"{entry.key}: restore write",
            destination.put_restored,
            entry.key,
            body,
            metadata=dict(entry.metadata),
        )
        if not _is_text(new_version):
            raise ObjectStorageBackupError(f"{entry.key}: destination returned no version id")
        stored = _io(
            f"{entry.key}: restore read-back", destination.get_version, entry.key, new_version
        )
        if _blob_issue(stored, entry.sha256, entry.size_bytes) is not None:
            raise ObjectStorageBackupError(f"{entry.key}: restored bytes differ from the backup")
        restored_meta = _io(
            f"{entry.key}: restore metadata", destination.version_metadata, entry.key, new_version
        )
        if restored_meta != dict(entry.metadata):
            raise ObjectStorageBackupError(f"{entry.key}: restored metadata differs from backup")
        restored.append((entry.key, entry.version_id, new_version))
    return RestoreRecord(
        backup_id=backup_id,
        organization_id=organization_id,
        manifest_sha256=record.manifest_sha256,
        object_count=record.object_count,
        total_bytes=sum(e.size_bytes for e in record.entries),
        restored=tuple(restored),
    )


class S3ObjectStore:
    """`ObjectStore` over one bucket of a real S3-compatible server."""

    def __init__(self, client: S3Client, bucket: str) -> None:
        self._client = client
        self._bucket = bucket

    def list_versions(self, key: str) -> list[VersionInfo]:
        found: list[VersionInfo] = []
        for entry in self._client.list_object_versions(self._bucket, prefix=key):
            if not isinstance(entry, dict) or entry.get("Key") != key:
                continue
            found.append(
                VersionInfo(
                    version_id=str(entry.get("VersionId", "")),
                    is_latest=str(entry.get("IsLatest", "")).lower() == "true",
                    is_delete_marker=bool(entry.get("IsDeleteMarker", False)),
                )
            )
        return found

    def get_version(self, key: str, version_id: str) -> bytes:
        body, _ = self._client.get_object(self._bucket, key, version_id=version_id)
        return body

    def version_metadata(self, key: str, version_id: str) -> dict[str, str]:
        """Content type and user metadata (`x-amz-meta-*`) of one object version."""
        _, headers = self._client.get_object(self._bucket, key, version_id=version_id)
        kept = {
            name: value
            for name, value in headers.items()
            if name == "content-type" or name.startswith(_USER_METADATA)
        }
        return dict(sorted(kept.items()))

    def get_latest(self, key: str) -> bytes:
        body, _ = self._client.get_object(self._bucket, key)
        return body

    def put_restored(self, key: str, body: bytes, *, metadata: Mapping[str, str]) -> str:
        allowed = {
            name: value
            for name, value in metadata.items()
            if name == "content-type" or name.startswith(_USER_METADATA)
        }
        if allowed.keys() != metadata.keys():
            raise ObjectStorageBackupError(f"{key}: metadata has headers that cannot be restored")
        # `retention_headers` is the client's generic extra-request-header mapping.
        result = self._client.put_object(self._bucket, key, body, retention_headers=allowed)
        return result["version_id"]

    def put_retained(self, key: str, body: bytes, *, retain_until: datetime) -> str:
        result = self._client.put_object(self._bucket, key, body)
        self._client.put_object_retention(
            self._bucket,
            key,
            mode="COMPLIANCE",
            retain_until_date=retain_until.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        )
        return result["version_id"]

    def retention_until(self, key: str) -> datetime | None:
        try:
            info = self._client.get_object_retention(self._bucket, key)
        except S3Error as exc:
            if exc.status_code == 404:  # no retention configured on this object version
                return None
            raise
        raw = info.get("RetainUntilDate")
        if info.get("Mode") != "COMPLIANCE" or not isinstance(raw, str):
            return None
        try:
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            return None
        return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def _client_from_env() -> S3Client:
    def need(name: str) -> str:
        value = os.environ.get(name)
        if not value:
            raise ObjectStorageBackupError(f"{name} must be set")
        return value

    endpoint = S3Endpoint(
        scheme=need("SOVRA_STORAGE_S3_SCHEME"),
        host=need("SOVRA_STORAGE_S3_HOST"),
        port=int(need("SOVRA_STORAGE_S3_PORT")),
        region=need("SOVRA_STORAGE_S3_REGION"),
    )
    credentials = S3Credentials(
        access_key=need("SOVRA_STORAGE_S3_ACCESS_KEY"),
        secret_key=need("SOVRA_STORAGE_S3_SECRET_KEY"),
    )
    return S3Client(endpoint, credentials)


def _manifest_from_file(path: Path) -> tuple[BlobRef, ...]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list) or not all(isinstance(item, Mapping) for item in data):
        raise ObjectStorageBackupError("manifest file must be a JSON list of objects")
    try:
        return tuple(BlobRef(**item) for item in data)
    except TypeError as exc:
        raise ObjectStorageBackupError("manifest entry has unknown or missing fields") from exc


def _run_backup(args: argparse.Namespace, client: S3Client) -> BackupRecord:
    return create_backup(
        _manifest_from_file(args.manifest),
        source=S3ObjectStore(client, args.source_bucket),
        target=S3ObjectStore(client, args.target_bucket),
        known_key_refs=frozenset(args.known_key_ref),
        retain_days=args.retain_days,
    )


def _run_verify(args: argparse.Namespace, client: S3Client) -> BackupRecord:
    return verify_backup(
        S3ObjectStore(client, args.target_bucket),
        args.backup_id,
        organization_id=args.organization_id,
        expected_manifest_sha256=args.expected_manifest_sha256,
    )


def _run_restore(args: argparse.Namespace, client: S3Client) -> RestoreRecord:
    return restore_backup(
        S3ObjectStore(client, args.target_bucket),
        S3ObjectStore(client, args.restore_bucket),
        args.backup_id,
        organization_id=args.organization_id,
        expected_manifest_sha256=args.expected_manifest_sha256,
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0] if __doc__ else "")
    sub = parser.add_subparsers(required=True)
    backup = sub.add_parser("backup")
    backup.set_defaults(run=_run_backup)
    backup.add_argument("--manifest", type=Path, required=True)
    backup.add_argument("--source-bucket", required=True)
    backup.add_argument("--target-bucket", required=True)
    backup.add_argument("--known-key-ref", action="append", default=[])
    backup.add_argument("--retain-days", type=int, default=30)
    verify = sub.add_parser("verify")
    verify.set_defaults(run=_run_verify)
    verify.add_argument("--target-bucket", required=True)
    verify.add_argument("--backup-id", required=True)
    verify.add_argument("--organization-id", required=True)
    verify.add_argument("--expected-manifest-sha256", default=None)
    restore = sub.add_parser("restore")
    restore.set_defaults(run=_run_restore)
    restore.add_argument("--target-bucket", required=True)
    restore.add_argument("--restore-bucket", required=True)
    restore.add_argument("--backup-id", required=True)
    restore.add_argument("--organization-id", required=True)
    restore.add_argument("--expected-manifest-sha256", default=None)
    args = parser.parse_args(argv)
    try:
        record = args.run(args, _client_from_env())
    except ObjectStorageBackupError as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1
    print(record.to_json())
    return 0


if __name__ == "__main__":
    sys.exit(main())
