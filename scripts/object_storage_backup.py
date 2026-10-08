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

Scope, disclosed: the BackupRecord/RestoreRecord JSON Schemas in
`packages/contracts/jsonschema/16-domain.schema.json` are FTF-1601's and frozen
(`shared_lock_required` is empty here), so this task does not extend them; the
record below is this module's own typed output. Restore automation is FTF-1603.

CLI:
    python scripts/object_storage_backup.py backup --manifest m.json \\
        --source-bucket src --target-bucket dst --known-key-ref kms/a ...
    python scripts/object_storage_backup.py verify --target-bucket dst --backup-id <id>
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
from typing import Protocol

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
    "S3ObjectStore",
    "VersionInfo",
    "create_backup",
    "verify_backup",
]

KINDS = frozenset({"dataset", "model", "result"})
MAX_RETENTION_DAYS = 3650
_SHA256 = re.compile(r"[0-9a-fA-F]{64}")
_TEXT = re.compile(r"\S(?:.*\S)?", re.DOTALL)
RECORD_NAME = "backup-record.json"


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

    def put_retained(self, key: str, body: bytes, *, retain_until: datetime) -> str: ...

    def get_latest(self, key: str) -> bytes: ...

    def retention_until(self, key: str) -> datetime | None: ...


@dataclasses.dataclass(frozen=True)
class BackupRecord:
    backup_id: str
    created_at: str
    retain_until: str
    manifest_sha256: str
    object_count: int
    blob_count: int
    total_bytes: int
    blobs: tuple[tuple[str, int], ...]  # (sha256, size_bytes), sorted

    def to_json(self) -> str:
        return json.dumps(dataclasses.asdict(self), sort_keys=True, separators=(",", ":"))


def _digest(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def _is_text(value: object) -> bool:
    return isinstance(value, str) and _TEXT.fullmatch(value) is not None


def _is_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _validate_ref(ref: object) -> BlobRef:
    if not isinstance(ref, BlobRef):
        raise ObjectStorageBackupError("manifest entries must be BlobRef")
    if ref.kind not in KINDS:
        raise ObjectStorageBackupError("blob kind must be dataset, model or result")
    for name in ("key", "version_id", "artifact_id"):
        if not _is_text(getattr(ref, name)):
            raise ObjectStorageBackupError(f"blob {name} must be a non-empty trimmed string")
    if not isinstance(ref.sha256, str) or _SHA256.fullmatch(ref.sha256) is None:
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
    seen: set[tuple[str, str]] = set()
    for ref in checked:
        identity = (ref.key, ref.version_id)
        if identity in seen:
            raise ObjectStorageBackupError("manifest lists the same key and version twice")
        seen.add(identity)
    return checked


def _manifest_sha256(refs: Sequence[BlobRef]) -> str:
    canonical = json.dumps(
        sorted((dataclasses.asdict(r) for r in refs), key=lambda d: (d["key"], d["version_id"])),
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
    if not isinstance(known_key_refs, frozenset) or not all(_is_text(k) for k in known_key_refs):
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
    if issues:
        raise BackupIncompleteError(issues)

    retain_until = created + timedelta(days=retain_days)
    base = f"{prefix}/{identity}"
    for digest, body in sorted(fetched.items()):
        _put_verified(target, f"{base}/blobs/{digest}", body, retain_until)
    record = BackupRecord(
        backup_id=identity,
        created_at=created.strftime("%Y-%m-%dT%H:%M:%SZ"),
        retain_until=retain_until.strftime("%Y-%m-%dT%H:%M:%SZ"),
        manifest_sha256=_manifest_sha256(refs),
        object_count=len(refs),
        blob_count=len(fetched),
        total_bytes=sum(len(b) for b in fetched.values()),
        blobs=tuple(sorted((d, len(b)) for d, b in fetched.items())),
    )
    _put_verified(target, f"{base}/{RECORD_NAME}", record.to_json().encode(), retain_until)
    return record


def _put_verified(target: ObjectStore, key: str, body: bytes, retain_until: datetime) -> None:
    target.put_retained(key, body, retain_until=retain_until)
    if target.get_latest(key) != body:
        raise ObjectStorageBackupError(f"{key}: read-back differs from what was written")
    held = target.retention_until(key)
    if held is None or held < retain_until.replace(microsecond=0):
        raise ObjectStorageBackupError(f"{key}: retention was not applied")


def verify_backup(
    target: ObjectStore,
    backup_id: str,
    *,
    prefix: str = "backups",
    now: datetime | None = None,
) -> BackupRecord:
    """Re-read a backup and prove every blob is present, hashed as recorded and still retained."""
    if not _is_text(backup_id) or "/" in backup_id:
        raise ObjectStorageBackupError("backup_id must be a plain path segment")
    base = f"{prefix}/{backup_id}"
    try:
        raw = target.get_latest(f"{base}/{RECORD_NAME}")
        data = json.loads(raw)
        record = BackupRecord(
            **{**data, "blobs": tuple((str(d), int(s)) for d, s in data["blobs"])}
        )
    except (S3Error, ValueError, TypeError, KeyError) as exc:
        raise ObjectStorageBackupError("backup record is missing or unreadable") from exc
    if not all(isinstance(d, str) and _SHA256.fullmatch(d) and s >= 0 for d, s in record.blobs):
        raise ObjectStorageBackupError("backup record lists a malformed blob")
    if record.backup_id != backup_id:
        raise ObjectStorageBackupError("backup record belongs to a different backup")
    current = now if now is not None else datetime.now(UTC)
    issues: list[str] = []
    for digest, size in record.blobs:
        key = f"{base}/blobs/{digest}"
        try:
            body = target.get_latest(key)
        except S3Error:
            issues.append(f"{digest}: blob is missing")
            continue
        if len(body) != size or hashlib.sha256(body).hexdigest() != digest:
            issues.append(f"{digest}: blob no longer matches its recorded hash")
        held = target.retention_until(key)
        if held is None or held <= current:
            issues.append(f"{digest}: blob is not under retention")
    if len(record.blobs) != record.blob_count:
        issues.append("record blob_count disagrees with its blob list")
    if issues:
        raise BackupIncompleteError(issues)
    return record


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

    def get_latest(self, key: str) -> bytes:
        body, _ = self._client.get_object(self._bucket, key)
        return body

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
    return verify_backup(S3ObjectStore(client, args.target_bucket), args.backup_id)


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
