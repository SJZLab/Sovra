#!/usr/bin/env python3
"""FTF-1602 -- Object storage backup (no-DB unit/fault tests).

Acceptance:
  A1 dataset/model/result manifests and their exact immutable blobs are backed up with hash
     verification and retention
  A2 the real S3 integration is tests/integration/test_ftf_1602_object_storage_backup.py
Negative acceptance:
  N1 missing shards, stale object versions and lost encryption key references fail backup
     completeness, and nothing is written for a failed backup

The store here is an in-memory stand-in used only for isolated unit/fault tests; the real
SeaweedFS S3 qualification is the integration file above.

Run directly: python3 tests/tasks/test_ftf_1602.py
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import sys
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "apps" / "api"))

from scripts import object_storage_backup as osb  # noqa: E402
from scripts.object_storage_backup import (  # noqa: E402
    BackupIncompleteError,
    BlobRef,
    ObjectStorageBackupError,
    S3ObjectStore,
    VersionInfo,
    create_backup,
    verify_backup,
)

from app.services.storage_semantics_client import S3Error  # noqa: E402

FAILURES: list[str] = []
NOW = datetime(2026, 10, 8, 12, 0, 0, tzinfo=UTC)


def check(criterion_id: str, description: str, condition: bool) -> None:
    if not condition:
        FAILURES.append(f"FAIL [{criterion_id}] {description}")


def raises(
    exc_type: type[BaseException], fn, *args: object, **kwargs: object
) -> BaseException | None:
    try:
        fn(*args, **kwargs)
    except exc_type as exc:
        return exc
    return None


class MemStore:
    def __init__(
        self, *, corrupt_readback: bool = False, skip_retention: bool = False, short_by_s: int = 0
    ) -> None:
        self.versions: dict[str, list[tuple[str, bytes | None]]] = {}
        self.retention: dict[str, datetime] = {}
        self.metadata: dict[tuple[str, str], dict[str, str]] = {}
        self.corrupt_readback = corrupt_readback
        self.skip_retention = skip_retention
        self.short_by_s = short_by_s
        self._n = 0

    def add(self, key: str, body: bytes | None, version_id: str | None = None) -> str:
        self._n += 1
        version_id = version_id if version_id is not None else f"v{self._n}"
        self.versions.setdefault(key, []).append((version_id, body))
        return version_id

    def replace_only(self, key: str, body: bytes) -> None:
        """Swap the content of the key's single version (a forged record that is still the
        only version -- the stored retention is kept, as COMPLIANCE would)."""
        self.versions[key] = [("v-forged", body)]

    def list_versions(self, key: str) -> list[VersionInfo]:
        entries = self.versions.get(key, [])
        return [
            VersionInfo(vid, i == len(entries) - 1, body is None)
            for i, (vid, body) in enumerate(entries)
        ]

    def get_version(self, key: str, version_id: str) -> bytes:
        for vid, body in self.versions.get(key, []):
            if vid == version_id and body is not None:
                return body
        raise S3Error(404, "NoSuchVersion", "no such version")

    def version_metadata(self, key: str, version_id: str) -> dict[str, str]:
        return dict(self.metadata.get((key, version_id), {}))

    def put_restored(self, key: str, body: bytes, *, metadata: Any) -> str:
        version_id = self.add(key, body)
        self.metadata[(key, version_id)] = dict(metadata)
        return version_id

    def get_latest(self, key: str) -> bytes:
        entries = self.versions.get(key, [])
        if not entries or entries[-1][1] is None:
            raise S3Error(404, "NoSuchKey", "no such key")
        body = entries[-1][1]
        return body + b"!" if self.corrupt_readback else body

    def put_retained(self, key: str, body: bytes, *, retain_until: datetime) -> str:
        version_id = self.add(key, body)
        if not self.skip_retention:
            self.retention[key] = retain_until - timedelta(seconds=self.short_by_s)
        return version_id

    def retention_until(self, key: str) -> datetime | None:
        return self.retention.get(key)


def bad(ref: BlobRef, **changes: Any) -> BlobRef:
    """A BlobRef with deliberately wrong-typed fields, as a hostile caller could build."""
    return dataclasses.replace(ref, **changes)


def sha(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def fixture(**overrides: object) -> tuple[MemStore, list[BlobRef]]:
    source = MemStore()
    refs: list[BlobRef] = []
    for kind, key, body, artifact, index, count in (
        ("dataset", "tenants/o/datasets/d1.jsonl", b"dataset-bytes", "d1", 0, 1),
        ("model", "tenants/o/models/m1/shard-0", b"model-shard-zero", "m1", 0, 2),
        ("model", "tenants/o/models/m1/shard-1", b"model-shard-one", "m1", 1, 2),
        ("result", "tenants/o/results/r1.json", b'{"score":1}', "r1", 0, 1),
    ):
        version_id = source.add(key, body)
        refs.append(
            BlobRef(
                kind=kind,
                key=key,
                version_id=version_id,
                sha256=sha(body),
                size_bytes=len(body),
                artifact_id=artifact,
                organization_id="o",
                shard_index=index,
                shard_count=count,
            )
        )
    return source, refs


def run(source: MemStore, refs: Any, target: MemStore | None = None, **kw: object):
    target = target if target is not None else MemStore()
    args: dict[str, object] = {
        "source": source,
        "target": target,
        "known_key_refs": frozenset({"kms/a"}),
        "retain_days": 30,
        "now": NOW,
        "backup_id": "b1",
    }
    args.update(kw)
    return create_backup(refs, **args)  # type: ignore[arg-type]


def invalid(fn, *args: object, **kwargs: object) -> bool:
    """True only for an up-front input refusal, never a completeness failure further down."""
    exc = raises(ObjectStorageBackupError, fn, *args, **kwargs)
    return exc is not None and not isinstance(exc, BackupIncompleteError)


def refused(message: str, fn, *args: object, **kwargs: object) -> bool:
    """True only for an up-front refusal whose message names the guard that fired."""
    exc = raises(ObjectStorageBackupError, fn, *args, **kwargs)
    return exc is not None and not isinstance(exc, BackupIncompleteError) and message in str(exc)


def nothing_written(target: MemStore) -> bool:
    return target.versions == {} and target.retention == {}


def test_complete_backup_is_verified_and_retained() -> None:
    source, refs = fixture()
    target = MemStore()
    record = run(source, refs, target)
    check(
        "FTF-1602-A1",
        "record counts every object and blob",
        (record.object_count, record.blob_count) == (4, 4),
    )
    check(
        "FTF-1602-A1",
        "total bytes is the sum of the verified blobs",
        record.total_bytes == sum(r.size_bytes for r in refs),
    )
    check(
        "FTF-1602-A1",
        "retention date is created_at + retain_days",
        record.retain_until == "2026-11-07T12:00:00Z",
    )
    for ref in refs:
        key = f"backups/o/b1/blobs/{ref.sha256}"
        check(
            "FTF-1602-A1",
            f"{ref.key}: blob stored under its sha256",
            target.get_latest(key) is not None,
        )
        check(
            "FTF-1602-A1",
            f"{ref.key}: blob is under retention until the record date",
            target.retention.get(key) == NOW + timedelta(days=30),
        )
    stored = json.loads(target.get_latest("backups/o/b1/backup-record.json"))
    check(
        "FTF-1602-A1",
        "the record is written and matches the returned one",
        stored["backup_id"] == "b1" and stored["manifest_sha256"] == record.manifest_sha256,
    )
    check(
        "FTF-1602-A1",
        "the record itself is retained",
        "backups/o/b1/backup-record.json" in target.retention,
    )
    verified = verify_backup(target, "b1", organization_id="o", now=NOW)
    check("FTF-1602-A1", "verify_backup accepts an intact backup", verified == record)


def test_exact_version_is_backed_up_not_latest() -> None:
    source = MemStore()
    old = source.add("tenants/o/k", b"old-bytes")
    ref = BlobRef("dataset", "tenants/o/k", old, sha(b"old-bytes"), 9, "d", "o")
    source.add("tenants/o/k", b"new-bytes")
    err = raises(BackupIncompleteError, run, source, [ref])
    check(
        "FTF-1602-N1",
        "a recorded version that is no longer the latest is stale",
        err is not None and "stale" in str(err),
    )
    only_latest = MemStore()
    v = only_latest.add("tenants/o/k", b"new-bytes")
    ok = run(only_latest, [BlobRef("dataset", "tenants/o/k", v, sha(b"new-bytes"), 9, "d", "o")])
    check("FTF-1602-A1", "the latest recorded version backs up", ok.blob_count == 1)


def test_negative_completeness() -> None:
    source, refs = fixture()
    target = MemStore()
    err = raises(
        BackupIncompleteError, run, source, [r for r in refs if r.key != refs[2].key], target
    )
    check(
        "FTF-1602-N1",
        "a missing shard fails completeness naming the shard",
        err is not None and "missing shards [1] of 2" in str(err),
    )
    check(
        "FTF-1602-N1",
        "a failed backup writes nothing, not even the record",
        nothing_written(target),
    )

    source, refs = fixture()
    source.add(refs[0].key, b"overwritten")
    target = MemStore()
    err = raises(BackupIncompleteError, run, source, refs, target)
    check(
        "FTF-1602-N1",
        "an overwritten (stale) version fails completeness",
        err is not None and "stale" in str(err) and nothing_written(target),
    )

    source, refs = fixture()
    source.add(refs[0].key, None)
    err = raises(BackupIncompleteError, run, source, refs)
    check(
        "FTF-1602-N1",
        "a delete marker after the recorded version is stale",
        err is not None and "stale" in str(err),
    )
    source, refs = fixture()
    ghost = bad(refs[0], version_id="v999")
    err = raises(BackupIncompleteError, run, source, [ghost, *refs[1:]])
    check(
        "FTF-1602-N1",
        "a version that never existed fails completeness",
        err is not None and "does not exist" in str(err),
    )
    source = MemStore()
    marker_version = source.add("tenants/o/k", None)
    err = raises(
        BackupIncompleteError,
        run,
        source,
        [BlobRef("dataset", "tenants/o/k", marker_version, sha(b""), 0, "d", "o")],
    )
    check(
        "FTF-1602-N1",
        "a recorded delete marker is refused as a blob",
        err is not None and "delete marker" in str(err),
    )
    err = raises(
        BackupIncompleteError,
        run,
        MemStore(),
        [BlobRef("dataset", "tenants/o/absent", "v1", sha(b""), 0, "d", "o")],
    )
    check("FTF-1602-N1", "a key absent from the source fails completeness", err is not None)

    source, refs = fixture()
    enc = bad(refs[0], encrypted=True, encryption_key_ref="kms/lost")
    target = MemStore()
    err = raises(BackupIncompleteError, run, source, [enc, *refs[1:]], target)
    check(
        "FTF-1602-N1",
        "an encrypted blob whose key reference is not recoverable fails",
        err is not None and "not recoverable" in str(err) and nothing_written(target),
    )
    noref = bad(refs[0], encrypted=True, encryption_key_ref=None)
    err = raises(BackupIncompleteError, run, source, [noref, *refs[1:]])
    check(
        "FTF-1602-N1",
        "an encrypted blob with no key reference at all fails",
        err is not None and "no key reference" in str(err),
    )
    known = bad(refs[0], encrypted=True, encryption_key_ref="kms/a")
    check(
        "FTF-1602-A1",
        "an encrypted blob with a recoverable key reference backs up",
        run(source, [known, *refs[1:]]).blob_count == 4,
    )
    plain_with_ref = bad(refs[0], encrypted=False, encryption_key_ref="kms/lost")
    check(
        "FTF-1602-A1",
        "an unencrypted blob needs no recoverable key",
        run(source, [plain_with_ref, *refs[1:]]).blob_count == 4,
    )

    bad_hash = bad(refs[0], sha256="0" * 64)
    err = raises(BackupIncompleteError, run, source, [bad_hash, *refs[1:]])
    check(
        "FTF-1602-N1",
        "a sha256 differing from the manifest fails",
        err is not None and "sha256 differs" in str(err),
    )
    bad_size = bad(refs[0], size_bytes=refs[0].size_bytes + 1)
    err = raises(BackupIncompleteError, run, source, [bad_size, *refs[1:]])
    check(
        "FTF-1602-N1",
        "a size differing from the manifest fails",
        err is not None and "size differs" in str(err),
    )
    disagree = bad(refs[2], shard_count=3)
    err = raises(BackupIncompleteError, run, source, [*refs[:2], disagree, refs[3]])
    check(
        "FTF-1602-N1",
        "shards disagreeing on shard_count fail",
        err is not None and "disagree" in str(err),
    )
    dup_index = bad(refs[2], shard_index=0)
    err = raises(BackupIncompleteError, run, source, [*refs[:2], dup_index, refs[3]])
    check(
        "FTF-1602-N1",
        "a duplicated shard index fails",
        err is not None and "duplicate shard" in str(err),
    )
    err = raises(
        BackupIncompleteError,
        run,
        source,
        [
            bad(refs[0], encrypted=True),
            bad(refs[1], sha256="1" * 64),
            refs[2],
            bad(refs[3], size_bytes=999),
        ],
    )
    check(
        "FTF-1602-N1",
        "every issue is reported, not only the first",
        len(getattr(err, "issues", ())) >= 3,
    )
    check(
        "FTF-1602-N1",
        "issues never carry blob content",
        err is not None and "dataset-bytes" not in str(err),
    )


def test_dedupe_and_ordering() -> None:
    source = MemStore()
    v1 = source.add("tenants/o/a", b"same")
    v2 = source.add("tenants/o/b", b"same")
    refs = [
        BlobRef("dataset", "tenants/o/a", v1, sha(b"same"), 4, "x", "o"),
        BlobRef("result", "tenants/o/b", v2, sha(b"same"), 4, "y", "o"),
    ]
    record = run(source, refs)
    check(
        "FTF-1602-A1",
        "identical content is stored once but both objects are counted",
        (record.object_count, record.blob_count) == (2, 1),
    )
    other = run(source, list(reversed(refs)))
    check(
        "FTF-1602-A1",
        "manifest hash does not depend on entry order",
        other.manifest_sha256 == record.manifest_sha256,
    )
    changed = run(source, [refs[0], bad(refs[1], kind="model")])
    check(
        "FTF-1602-A1",
        "manifest hash changes when an entry's kind changes",
        changed.manifest_sha256 != record.manifest_sha256,
    )


def test_input_validation() -> None:
    source, refs = fixture()
    good = refs[0]
    bad_refs: list[tuple[str, object]] = [
        ("empty manifest", []),
        ("string manifest", "abc"),
        ("non-BlobRef entry", [{"key": "x"}]),
        ("unknown kind", [bad(good, kind="log")]),
        ("empty key", [bad(good, key="")]),
        ("padded key", [bad(good, key=" k")]),
        ("non-str version", [bad(good, version_id=1)]),
        ("empty artifact", [bad(good, artifact_id="")]),
        ("non-hex sha", [bad(good, sha256="g" * 64)]),
        ("short sha", [bad(good, sha256="a" * 63)]),
        ("bool size", [bad(good, size_bytes=True)]),
        ("negative size", [bad(good, size_bytes=-1)]),
        ("zero shard_count", [bad(good, shard_count=0)]),
        ("bool shard_count", [bad(good, shard_count=True)]),
        ("shard_index at count", [bad(good, shard_index=1, shard_count=1)]),
        ("negative shard_index", [bad(good, shard_index=-1, shard_count=2)]),
        ("non-bool encrypted", [bad(good, encrypted=1)]),
        ("blank key ref", [bad(good, encryption_key_ref="")]),
        ("duplicate identity", [good, good]),
        ("unhashable key", [bad(good, key=[])]),
    ]
    for label, manifest in bad_refs:
        check(
            "FTF-1602-N1",
            f"{label} raises ObjectStorageBackupError",
            invalid(run, source, manifest),
        )
    other_kwargs: list[tuple[str, dict[str, object]]] = [
        ("retain_days 0", {"retain_days": 0}),
        ("retain_days bool", {"retain_days": True}),
        ("retain_days too long", {"retain_days": osb.MAX_RETENTION_DAYS + 1}),
        ("naive now", {"now": datetime(2026, 1, 1)}),
        ("key refs as list", {"known_key_refs": ["kms/a"]}),
        ("key refs with non-str", {"known_key_refs": frozenset({1})}),
        ("slash in backup id", {"backup_id": "a/b"}),
        ("blank prefix", {"prefix": ""}),
        ("trailing slash prefix", {"prefix": "p/"}),
    ]
    for label, kw in other_kwargs:
        check(
            "FTF-1602-N1",
            f"{label} raises ObjectStorageBackupError",
            invalid(run, source, refs, **kw),
        )
    check(
        "FTF-1602-A1",
        "retain_days at the maximum is accepted",
        run(source, refs, retain_days=osb.MAX_RETENTION_DAYS).blob_count == 4,
    )
    check(
        "FTF-1602-A1",
        "retain_days of 1 is accepted",
        run(source, refs, retain_days=1).retain_until == "2026-10-09T12:00:00Z",
    )


def test_exactness_and_boundaries() -> None:
    source = MemStore()
    source.add("tenants/o/k", b"x", "abc")
    ref = BlobRef("dataset", "tenants/o/k", "abc", sha(b"x"), 1, "d", "o")
    check("FTF-1602-A1", "an exact version id backs up", run(source, [ref]).blob_count == 1)
    err = raises(BackupIncompleteError, run, source, [bad(ref, version_id="ABC")])
    check(
        "FTF-1602-N1",
        "version ids are case-exact",
        err is not None and "does not exist" in str(err),
    )
    mixed = MemStore()
    mixed.add("tenants/o/k", b"x", "ABC")
    check(
        "FTF-1602-A1",
        "an upper-case version id is matched exactly",
        run(mixed, [bad(ref, version_id="ABC")]).blob_count == 1,
    )
    twin = MemStore()
    twin.add("tenants/o/k", b"x", "ABC")
    twin.add("tenants/o/k", b"x", "abc")
    err = raises(BackupIncompleteError, run, twin, [bad(ref, version_id="ABC")])
    check(
        "FTF-1602-N1",
        "a case-twin latest version does not make ABC current",
        err is not None and "stale" in str(err),
    )
    for bad_kind in ("Dataset", " dataset", "dataset "):
        check(
            "FTF-1602-N1",
            f"kind {bad_kind!r} is refused",
            invalid(run, source, [bad(ref, kind=bad_kind)]),
        )
    for bad_key in ("k ", " k"):
        check(
            "FTF-1602-N1",
            f"key {bad_key!r} is refused",
            invalid(run, source, [bad(ref, key=bad_key)]),
        )
    for bad_version in ("abc ", " abc"):
        check(
            "FTF-1602-N1",
            f"version {bad_version!r} is refused",
            invalid(run, source, [bad(ref, version_id=bad_version)]),
        )
    for bad_artifact in ("d ", " d"):
        check(
            "FTF-1602-N1",
            f"artifact {bad_artifact!r} is refused",
            invalid(run, source, [bad(ref, artifact_id=bad_artifact)]),
        )
    for bad_sha in (" " + sha(b"x")[1:], sha(b"x") + "0"):
        check(
            "FTF-1602-N1",
            f"sha256 {bad_sha[:8]!r} form is refused up front",
            invalid(run, source, [bad(ref, sha256=bad_sha)]),
        )
    check(
        "FTF-1602-N1",
        "a non-string sha256 is refused up front",
        invalid(run, source, [bad(ref, sha256=123)]),
    )
    check(
        "FTF-1602-N1",
        "a non-int size is refused up front",
        invalid(run, source, [bad(ref, size_bytes="1")]),
    )
    check(
        "FTF-1602-N1",
        "a negative size is refused up front",
        invalid(run, source, [bad(ref, size_bytes=-1)]),
    )
    check(
        "FTF-1602-N1",
        "a non-int shard index is refused up front",
        invalid(run, source, [bad(ref, shard_index="0")]),
    )
    check(
        "FTF-1602-N1",
        "a shard index equal to the count is refused up front",
        invalid(run, source, [bad(ref, shard_index=2, shard_count=2)]),
    )
    check(
        "FTF-1602-N1",
        "a non-bool encrypted flag is refused up front",
        invalid(run, source, [bad(ref, encrypted="yes")]),
    )
    check(
        "FTF-1602-N1",
        "a non-str key ref is refused up front",
        invalid(run, source, [bad(ref, encryption_key_ref=5)]),
    )
    check(
        "FTF-1602-N1",
        "a tuple manifest is accepted but bytes are not",
        invalid(run, source, b"abc") and run(source, (ref,)).blob_count == 1,
    )
    check(
        "FTF-1602-N1",
        "a repeated key and version is refused up front",
        invalid(run, source, [ref, bad(ref, kind="result", artifact_id="e")]),
    )
    keyed = bad(ref, encrypted=True, encryption_key_ref="KMS/A")
    check(
        "FTF-1602-A1",
        "key references match exactly when equal",
        run(source, [keyed], known_key_refs=frozenset({"KMS/A"})).blob_count == 1,
    )
    err = raises(BackupIncompleteError, run, source, [keyed], known_key_refs=frozenset({"kms/a"}))
    check(
        "FTF-1602-N1",
        "key references are case-exact",
        err is not None and "not recoverable" in str(err),
    )
    check(
        "FTF-1602-N1",
        "a padded key reference is refused up front",
        invalid(run, source, [bad(keyed, encryption_key_ref="kms/a ")]),
    )

    target = MemStore()
    record = run(source, [ref], target, backup_id="Bk-1")
    check(
        "FTF-1602-A1",
        "a mixed-case backup id verifies under the same id",
        verify_backup(target, "Bk-1", organization_id="o", now=NOW) == record,
    )
    keys = list(json.loads(record.to_json()))
    check("FTF-1602-A1", "the record JSON is canonically key-sorted", keys == sorted(keys))
    for bad_manifest in ("abc", b"abc", None, 5):
        check(
            "FTF-1602-N1",
            f"manifest {bad_manifest!r} is refused as not a sequence",
            refused("manifest must be a sequence", run, source, bad_manifest),
        )
    for bad_id in ("", " Bk-1", "a/b"):
        check(
            "FTF-1602-N1",
            f"verify refuses backup id {bad_id!r} by its guard",
            refused(
                "backup_id must be a plain path segment",
                verify_backup,
                target,
                bad_id,
                organization_id="o",
            ),
        )
    check(
        "FTF-1602-N1",
        "verify refuses a padded backup id",
        invalid(verify_backup, target, " Bk-1", organization_id="o"),
    )
    at_expiry = NOW + timedelta(days=30)
    check(
        "FTF-1602-N1",
        "retention that ends exactly now is no longer retention",
        raises(
            BackupIncompleteError, verify_backup, target, "Bk-1", organization_id="o", now=at_expiry
        )
        is not None,
    )
    check(
        "FTF-1602-A1",
        "retention one second from ending still counts",
        verify_backup(target, "Bk-1", organization_id="o", now=at_expiry - timedelta(seconds=1))
        == record,
    )
    same_len = MemStore()
    same_len.versions = {k: list(v) for k, v in target.versions.items()}
    same_len.retention = dict(target.retention)
    blob_key = f"backups/o/Bk-1/blobs/{record.blobs[0][0]}"
    same_len.add(blob_key, b"y")
    err = raises(
        BackupIncompleteError, verify_backup, same_len, "Bk-1", organization_id="o", now=NOW
    )
    check(
        "FTF-1602-N1",
        "verify catches a same-length blob with a different hash",
        err is not None and "no longer matches" in str(err),
    )
    shorter = MemStore()
    shorter.versions = {k: list(v) for k, v in target.versions.items()}
    shorter.retention = dict(target.retention)
    shorter.add(blob_key, b"")
    err = raises(
        BackupIncompleteError, verify_backup, shorter, "Bk-1", organization_id="o", now=NOW
    )
    check(
        "FTF-1602-N1",
        "verify catches a truncated blob",
        err is not None and "no longer matches" in str(err),
    )


def test_hex_case_is_compared_exactly() -> None:
    source = MemStore()
    version = source.add("tenants/o/k", b"x")
    upper = bad(
        BlobRef("dataset", "tenants/o/k", version, sha(b"x"), 1, "d", "o"), sha256=sha(b"x").upper()
    )
    err = raises(BackupIncompleteError, run, source, [upper])
    check(
        "FTF-1602-N1",
        "an upper-case manifest sha256 is not silently equal to the real digest",
        err is not None and "sha256 differs" in str(err),
    )
    target = MemStore()
    record = run(
        source, [BlobRef("dataset", "tenants/o/k", version, sha(b"x"), 1, "d", "o")], target
    )
    data = json.loads(target.get_latest("backups/o/b1/backup-record.json"))
    upper_digest = record.blobs[0][0].upper()
    data["blobs"] = [[upper_digest, 1]]
    forged = MemStore()
    forged.replace_only("backups/o/b1/backup-record.json", json.dumps(data).encode())
    forged.add(f"backups/o/b1/blobs/{upper_digest}", b"x")
    forged.retention = {k: v for k, v in target.retention.items()}
    forged.retention[f"backups/o/b1/blobs/{upper_digest}"] = NOW + timedelta(days=30)
    err = raises(BackupIncompleteError, verify_backup, forged, "b1", organization_id="o", now=NOW)
    check(
        "FTF-1602-N1",
        "a record digest in the wrong case does not verify against the real digest",
        err is not None and "no longer matches" in str(err),
    )


def test_empty_blob_round_trips() -> None:
    source = MemStore()
    version = source.add("tenants/o/empty", b"")
    target = MemStore()
    record = run(
        source, [BlobRef("result", "tenants/o/empty", version, sha(b""), 0, "r", "o")], target
    )
    check(
        "FTF-1602-A1",
        "a zero-byte blob backs up and verifies",
        verify_backup(target, "b1", organization_id="o", now=NOW) == record
        and record.blobs == ((sha(b""), 0),),
    )


def test_write_side_verification() -> None:
    source, refs = fixture()
    err = raises(ObjectStorageBackupError, run, source, refs, MemStore(corrupt_readback=True))
    check(
        "FTF-1602-N1",
        "a read-back that differs from the write fails the backup",
        err is not None and "read-back" in str(err),
    )
    err = raises(ObjectStorageBackupError, run, source, refs, MemStore(skip_retention=True))
    check(
        "FTF-1602-N1",
        "retention that was not applied fails the backup",
        err is not None and "retention" in str(err),
    )
    err = raises(ObjectStorageBackupError, run, source, refs, MemStore(short_by_s=1))
    check("FTF-1602-N1", "retention shorter than requested fails the backup", err is not None)
    target = MemStore(corrupt_readback=True)
    raises(ObjectStorageBackupError, run, source, refs, target)
    check(
        "FTF-1602-N1",
        "no record is written when a blob failed verification",
        "backups/o/b1/backup-record.json" not in target.versions,
    )


def test_verify_backup() -> None:
    source, refs = fixture()
    target = MemStore()
    record = run(source, refs, target)
    digest = record.blobs[0][0]
    key = f"backups/o/b1/blobs/{digest}"

    tampered = MemStore()
    tampered.versions = {k: list(v) for k, v in target.versions.items()}
    tampered.retention = dict(target.retention)
    tampered.add(key, b"tampered")
    err = raises(BackupIncompleteError, verify_backup, tampered, "b1", organization_id="o", now=NOW)
    check(
        "FTF-1602-N1",
        "verify detects a blob that no longer matches its hash",
        err is not None and "no longer matches" in str(err),
    )

    missing = MemStore()
    missing.versions = {k: list(v) for k, v in target.versions.items() if k != key}
    missing.retention = dict(target.retention)
    err = raises(BackupIncompleteError, verify_backup, missing, "b1", organization_id="o", now=NOW)
    check("FTF-1602-N1", "verify detects a missing blob", err is not None and "missing" in str(err))

    err = raises(
        BackupIncompleteError,
        verify_backup,
        target,
        "b1",
        organization_id="o",
        now=NOW + timedelta(days=31),
    )
    check(
        "FTF-1602-N1",
        "verify detects retention that has lapsed",
        err is not None and "retention" in str(err),
    )
    unretained = MemStore()
    unretained.versions = {k: list(v) for k, v in target.versions.items()}
    err = raises(
        BackupIncompleteError, verify_backup, unretained, "b1", organization_id="o", now=NOW
    )
    check("FTF-1602-N1", "verify detects blobs that were never retained", err is not None)

    err = raises(
        ObjectStorageBackupError, verify_backup, MemStore(), "b1", organization_id="o", now=NOW
    )
    check(
        "FTF-1602-N1",
        "verify refuses a backup without a record",
        err is not None and "record" in str(err),
    )
    garbled = MemStore()
    garbled.replace_only("backups/o/b1/backup-record.json", b"not json")
    check(
        "FTF-1602-N1",
        "verify refuses an unreadable record",
        raises(ObjectStorageBackupError, verify_backup, garbled, "b1", organization_id="o")
        is not None,
    )
    wrong_id = MemStore()
    wrong_id.versions = {k: list(v) for k, v in target.versions.items()}
    wrong_id.versions["backups/o/b2/backup-record.json"] = wrong_id.versions.pop(
        "backups/o/b1/backup-record.json"
    )
    err = raises(
        ObjectStorageBackupError, verify_backup, wrong_id, "b2", organization_id="o", now=NOW
    )
    check(
        "FTF-1602-N1",
        "verify refuses a record that belongs to another backup",
        err is not None and "different backup" in str(err),
    )
    check(
        "FTF-1602-N1",
        "verify refuses a path-like backup id",
        raises(ObjectStorageBackupError, verify_backup, target, "../x", organization_id="o")
        is not None,
    )
    for bad_blob in (["ABCDEF", 3], [record.blobs[0][0], -1], ["x" * 64, 1]):
        malformed = clone(target)
        data = json.loads(target.get_latest("backups/o/b1/backup-record.json"))
        data["blobs"] = [bad_blob]
        data["blob_count"] = 1
        malformed.replace_only("backups/o/b1/backup-record.json", json.dumps(data).encode())
        check(
            "FTF-1602-N1",
            f"verify refuses a record listing blob {bad_blob!r}",
            refused("malformed blob", verify_backup, malformed, "b1", organization_id="o", now=NOW),
        )
    miscounted = MemStore()
    data = json.loads(target.get_latest("backups/o/b1/backup-record.json"))
    data["blob_count"] = 99
    miscounted.versions = {k: list(v) for k, v in target.versions.items()}
    miscounted.replace_only("backups/o/b1/backup-record.json", json.dumps(data).encode())
    miscounted.retention = dict(target.retention)
    err = raises(
        BackupIncompleteError, verify_backup, miscounted, "b1", organization_id="o", now=NOW
    )
    check(
        "FTF-1602-N1",
        "verify detects a record whose blob_count disagrees with its list",
        err is not None and "blob_count" in str(err),
    )


class StubS3Client:
    def __init__(self) -> None:
        self.entries: list[Any] = []
        self.retention: dict[str, dict[str, str]] = {}
        self.calls: list[tuple[str, str]] = []
        self.failing: dict[str, int] = {}
        self.headers: dict[str, str] = {}
        self.put_headers: dict[str, str] = {}

    def list_object_versions(self, bucket: str, *, prefix: str = "") -> list[dict]:
        return [
            e
            for e in self.entries
            if not isinstance(e, dict) or e.get("Key", "").startswith(prefix)
        ]

    def get_object(self, bucket: str, key: str, *, version_id: str | None = None):
        self.calls.append(("get", version_id or ""))
        return b"body", dict(self.headers)

    def put_object(
        self, bucket: str, key: str, body: bytes, *, retention_headers: Any = None
    ) -> dict[str, str]:
        self.calls.append(("put", key))
        self.put_headers = dict(retention_headers or {})
        return {"version_id": "vv"}

    def put_object_retention(
        self, bucket: str, key: str, *, mode: str, retain_until_date: str, bypass: bool = False
    ) -> None:
        self.retention[key] = {"Mode": mode, "RetainUntilDate": retain_until_date}

    def get_object_retention(self, bucket: str, key: str) -> dict[str, str]:
        if key in self.failing:
            raise S3Error(self.failing[key], "Any", "forced")
        return self.retention.get(key, {})


def test_s3_adapter_shape() -> None:
    stub = StubS3Client()
    stub.entries = [
        {"Key": "a", "VersionId": "1", "IsLatest": "false"},
        {"Key": "a", "VersionId": "2", "IsLatest": "true"},
        {"Key": "ab", "VersionId": "9", "IsLatest": "true"},
        {"Key": "a", "VersionId": "3", "IsLatest": "false", "IsDeleteMarker": True},
        "junk",
    ]
    store = S3ObjectStore(stub, "bkt")  # type: ignore[arg-type]
    versions = store.list_versions("a")
    check(
        "FTF-1602-A1",
        "adapter lists only the exact key, never a prefix sibling",
        [v.version_id for v in versions] == ["1", "2", "3"],
    )
    check(
        "FTF-1602-A1",
        "adapter reads IsLatest as a boolean, not a truthy string",
        [v.is_latest for v in versions] == [False, True, False],
    )
    check(
        "FTF-1602-A1",
        "adapter flags delete markers",
        [v.is_delete_marker for v in versions] == [False, False, True],
    )
    stub.entries.append({"Key": "Key-A", "VersionId": "7", "IsLatest": "true"})
    check(
        "FTF-1602-A1",
        "adapter matches a mixed-case key exactly",
        [v.version_id for v in store.list_versions("Key-A")] == ["7"],
    )
    check(
        "FTF-1602-A1",
        "adapter get_latest returns the object body",
        store.get_latest("a") == b"body",
    )
    store.get_version("a", "1")
    check("FTF-1602-A1", "adapter fetches the exact version id", ("get", "1") in stub.calls)
    until = datetime(2026, 11, 1, 0, 0, 0, tzinfo=UTC)
    check(
        "FTF-1602-A1",
        "put_retained returns the version id",
        store.put_retained("k", b"x", retain_until=until) == "vv",
    )
    check(
        "FTF-1602-A1",
        "retention is COMPLIANCE with a UTC date",
        stub.retention["k"] == {"Mode": "COMPLIANCE", "RetainUntilDate": "2026-11-01T00:00:00Z"},
    )
    check(
        "FTF-1602-A1", "retention_until round-trips the date", store.retention_until("k") == until
    )
    stub.retention["g"] = {"Mode": "GOVERNANCE", "RetainUntilDate": "2026-11-01T00:00:00Z"}
    check(
        "FTF-1602-N1",
        "GOVERNANCE retention is not accepted as immutable",
        store.retention_until("g") is None,
    )
    stub.retention["bad"] = {"Mode": "COMPLIANCE", "RetainUntilDate": "soon"}
    check(
        "FTF-1602-N1",
        "an unparsable retention date is no retention",
        store.retention_until("bad") is None,
    )
    check("FTF-1602-N1", "no retention is None", store.retention_until("none") is None)
    stub.failing["nolock"] = 404
    check(
        "FTF-1602-N1",
        "a 404 from the retention read means no retention, not a crash",
        store.retention_until("nolock") is None,
    )
    stub.failing["boom"] = 500
    check(
        "FTF-1602-N1",
        "any other S3 error on the retention read propagates",
        raises(S3Error, store.retention_until, "boom") is not None,
    )
    offset = {"Mode": "COMPLIANCE", "RetainUntilDate": "2026-11-01T02:00:00+02:00"}
    stub.retention["tz"] = offset
    check(
        "FTF-1602-A1",
        "an offset date is normalised to the same instant",
        store.retention_until("tz") == until,
    )


def test_cli_commands() -> None:
    import contextlib
    import io

    source, refs = fixture()
    stores = {"src": source, "dst": MemStore()}
    patches: list[Any] = [
        mock.patch.object(osb, "S3ObjectStore", lambda client, bucket: stores[bucket]),
        mock.patch.object(osb, "_client_from_env", lambda: None),
    ]
    for patch in patches:
        patch.start()
    try:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "m.json"
            path.write_text(json.dumps([dataclasses.asdict(r) for r in refs]))
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                code = osb.main(
                    [
                        "backup",
                        "--manifest",
                        str(path),
                        "--source-bucket",
                        "src",
                        "--target-bucket",
                        "dst",
                    ]
                )
            printed = json.loads(out.getvalue())
            check(
                "FTF-1602-A1",
                "the backup command succeeds and prints the record",
                code == 0 and printed["blob_count"] == 4,
            )
            check(
                "FTF-1602-A1",
                "the backup command wrote to the target bucket only",
                any(k.endswith("backup-record.json") for k in stores["dst"].versions)
                and "backups" not in "".join(source.versions),
            )
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                vcode = osb.main(
                    [
                        "verify",
                        "--target-bucket",
                        "dst",
                        "--backup-id",
                        printed["backup_id"],
                        "--organization-id",
                        "o",
                    ]
                )
            check(
                "FTF-1602-A1",
                "the verify command succeeds on an intact backup",
                vcode == 0 and json.loads(out.getvalue()) == printed,
            )
            stores["dst"].add(
                f"backups/o/{printed['backup_id']}/blobs/{printed['blobs'][0][0]}", b"tampered"
            )
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                bad = osb.main(
                    [
                        "verify",
                        "--target-bucket",
                        "dst",
                        "--backup-id",
                        printed["backup_id"],
                        "--organization-id",
                        "o",
                    ]
                )
            check(
                "FTF-1602-N1",
                "the verify command exits 1 on a tampered backup",
                bad == 1 and "FAIL" in err.getvalue(),
            )
            stores["src"].add(refs[0].key, b"newer")
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                stale = osb.main(
                    [
                        "backup",
                        "--manifest",
                        str(path),
                        "--source-bucket",
                        "src",
                        "--target-bucket",
                        "dst",
                        "--retain-days",
                        "5",
                    ]
                )
            check(
                "FTF-1602-N1",
                "the backup command exits 1 on a stale manifest",
                stale == 1 and "stale" in err.getvalue(),
            )
    finally:
        for patch in patches:
            patch.stop()


def test_cli_guards() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "m.json"
        path.write_text(json.dumps([{"kind": "dataset", "bogus": 1}]))
        check(
            "FTF-1602-N1",
            "a manifest file with unknown fields is refused",
            refused("unknown or missing fields", osb._manifest_from_file, path),
        )
        path.write_text(json.dumps([1, 2]))
        check(
            "FTF-1602-N1",
            "a manifest file list of non-objects is refused as not objects",
            refused("must be a JSON list of objects", osb._manifest_from_file, path),
        )
        path.write_text(json.dumps({"kind": "dataset"}))
        check(
            "FTF-1602-N1",
            "a manifest file that is not a list is refused",
            refused("must be a JSON list", osb._manifest_from_file, path),
        )
        path.write_text(
            json.dumps(
                [
                    {
                        "kind": "dataset",
                        "key": "tenants/o/k",
                        "organization_id": "o",
                        "version_id": "1",
                        "sha256": "a" * 64,
                        "size_bytes": 1,
                        "artifact_id": "d",
                    }
                ]
            )
        )
        loaded = osb._manifest_from_file(path)
        check(
            "FTF-1602-A1",
            "a well-formed manifest file loads into BlobRefs",
            len(loaded) == 1 and loaded[0].key == "tenants/o/k",
        )
        import os

        saved = {k: v for k, v in os.environ.items() if k.startswith("SOVRA_STORAGE_S3_")}
        for name in saved:
            del os.environ[name]
        try:
            code = osb.main(
                ["verify", "--target-bucket", "b", "--backup-id", "x", "--organization-id", "o"]
            )
        finally:
            os.environ.update(saved)
        check("FTF-1602-N1", "the CLI fails closed without storage credentials", code == 1)


# ---------------------------------------------------------------------------
# Round 2: restore proof, tenant scoping, verify re-checks
# ---------------------------------------------------------------------------

META = {"content-type": "application/jsonl", "x-amz-meta-owner": "team-a"}


def meta_fixture() -> tuple[MemStore, list[BlobRef]]:
    source, refs = fixture()
    for ref in refs:
        source.metadata[(ref.key, ref.version_id)] = dict(META)
    return source, refs


def backed_up(**kw: object) -> tuple[MemStore, MemStore, list[BlobRef], Any]:
    source, refs = meta_fixture()
    target = MemStore()
    record = run(source, refs, target, backup_id="b1", **kw)
    return source, target, refs, record


def clone(store: MemStore) -> MemStore:
    twin = MemStore()
    twin.versions = {k: list(v) for k, v in store.versions.items()}
    twin.retention = dict(store.retention)
    twin.metadata = {k: dict(v) for k, v in store.metadata.items()}
    return twin


def with_record(target: MemStore, change: Any, org: str = "o") -> MemStore:
    """A copy of `target` whose stored record JSON was edited by `change(data)`."""
    twin = clone(target)
    key = f"backups/{org}/b1/backup-record.json"
    data = json.loads(target.get_latest(key))
    change(data)
    twin.replace_only(key, json.dumps(data).encode())
    return twin


def restore(target: MemStore, destination: MemStore | None = None, **kw: object) -> Any:
    destination = destination if destination is not None else MemStore()
    args: dict[str, object] = {"organization_id": "o", "now": NOW}
    args.update(kw)
    return osb.restore_backup(target, destination, "b1", **args)  # type: ignore[arg-type]


def refuses_restore(target: MemStore, message: str, **kw: object) -> bool:
    destination = MemStore()
    exc = raises(ObjectStorageBackupError, restore, target, destination, **kw)
    return exc is not None and message in str(exc) and nothing_written(destination)


def test_restore_round_trip_is_proven() -> None:
    source, target, refs, record = backed_up()
    check(
        "FTF-1602-A3",
        "the record carries every manifest entry with its metadata",
        len(record.entries) == 4
        and all(dict(e.metadata) == META for e in record.entries)
        and {(e.key, e.version_id) for e in record.entries} == {(r.key, r.version_id) for r in refs}
        and {e.artifact_id for e in record.entries} == {"d1", "m1", "r1"}
        and sorted((e.shard_index, e.shard_count) for e in record.entries if e.kind == "model")
        == [(0, 2), (1, 2)],
    )
    destination = MemStore()
    destination._n = 100  # restored version ids must be the destination's own
    restored = restore(target, destination)
    check(
        "FTF-1602-A3",
        "restore maps every backed-up key/version to a new restored version",
        restored.object_count == 4
        and {(k, v) for k, v, _ in restored.restored} == {(r.key, r.version_id) for r in refs}
        and all(new and new != old for _, old, new in restored.restored),
    )
    for ref, (key, _, new) in zip(
        sorted(refs, key=lambda r: (r.key, r.version_id)), restored.restored, strict=True
    ):
        body = destination.get_version(key, new)
        check(
            "FTF-1602-A3",
            f"{key}: restored bytes equal the manifest size and sha256",
            key == ref.key and len(body) == ref.size_bytes and sha(body) == ref.sha256,
        )
        check(
            "FTF-1602-A3",
            f"{key}: restored object metadata equals the source's",
            destination.version_metadata(key, new) == META,
        )
    check(
        "FTF-1602-A3",
        "the restore record binds the backup's manifest hash and total bytes",
        restored.manifest_sha256 == record.manifest_sha256
        and restored.total_bytes == sum(r.size_bytes for r in refs)
        and restored.organization_id == "o",
    )
    check(
        "FTF-1602-A3",
        "restore needs only the backup bucket (the source is not consulted)",
        restore(clone(target)).object_count == 4,
    )


def test_restore_refuses_bad_backups_and_writes_nothing() -> None:
    _, target, _, record = backed_up()
    digest = record.blobs[0][0]
    key = f"backups/o/b1/blobs/{digest}"
    size = record.blobs[0][1]

    swapped = clone(target)
    swapped.add(key, b"x" * size)
    check(
        "FTF-1602-N2",
        "a same-size swapped blob is refused and nothing is written",
        refuses_restore(swapped, "no longer matches"),
    )
    corrupted = clone(target)
    corrupted.add(key, b"\0" + corrupted.get_latest(key)[1:])
    check(
        "FTF-1602-N2",
        "a corrupted blob is refused and nothing is written",
        refuses_restore(corrupted, "no longer matches"),
    )
    partial = clone(target)
    partial.add(key, partial.get_latest(key)[: size // 2])
    check(
        "FTF-1602-N2",
        "a truncated blob is refused and nothing is written",
        refuses_restore(partial, "no longer matches"),
    )
    gone = clone(target)
    del gone.versions[key]
    check(
        "FTF-1602-N2",
        "a missing blob is refused and nothing is written",
        refuses_restore(gone, "blob is missing"),
    )
    no_record = clone(target)
    del no_record.versions["backups/o/b1/backup-record.json"]
    check(
        "FTF-1602-N2",
        "a backup without its record is refused",
        refuses_restore(no_record, "record is missing"),
    )
    unretained = clone(target)
    unretained.retention.clear()
    check(
        "FTF-1602-N2",
        "a backup whose retention is gone is refused",
        refuses_restore(unretained, "not under retention"),
    )
    forged = with_record(target, lambda d: d["entries"][0].update(sha256="f" * 64))
    check(
        "FTF-1602-N2",
        "a record whose entry hash was forged is refused",
        refuses_restore(forged, "disagrees"),
    )
    shrunk = with_record(target, lambda d: d.update(entries=d["entries"][:-1]))
    check(
        "FTF-1602-N2",
        "a record with a dropped manifest entry is refused",
        refuses_restore(shrunk, "disagrees"),
    )


def test_restore_destination_must_be_clean() -> None:
    _, target, refs, _ = backed_up()
    dirty = MemStore()
    dirty.add(refs[0].key, b"already here")
    exc = raises(BackupIncompleteError, restore, target, dirty)
    check(
        "FTF-1602-N2",
        "a destination that already holds a key is refused and keeps only its own object",
        exc is not None
        and "not clean" in str(exc)
        and list(dirty.versions) == [refs[0].key]
        and len(dirty.versions[refs[0].key]) == 1,
    )
    marker = MemStore()
    marker.add(refs[1].key, None)
    check(
        "FTF-1602-N2",
        "a destination key that only has a delete marker is not clean either",
        raises(BackupIncompleteError, restore, target, marker) is not None,
    )


def test_restore_read_back_is_verified() -> None:
    _, target, _, _ = backed_up()

    class FlippingDestination(MemStore):
        def get_version(self, key: str, version_id: str) -> bytes:
            return super().get_version(key, version_id)[:-1] + b"?"

    exc = raises(ObjectStorageBackupError, restore, target, FlippingDestination())
    check(
        "FTF-1602-N2",
        "restored bytes that differ on read-back fail the restore",
        exc is not None and "restored bytes differ" in str(exc),
    )

    class DroppingDestination(MemStore):
        def put_restored(self, key: str, body: bytes, *, metadata: Any) -> str:
            return super().put_restored(key, body, metadata={})

    exc = raises(ObjectStorageBackupError, restore, target, DroppingDestination())
    check(
        "FTF-1602-N2",
        "restored metadata that differs from the source's fails the restore",
        exc is not None and "metadata differs" in str(exc),
    )

    class VersionlessDestination(MemStore):
        def put_restored(self, key: str, body: bytes, *, metadata: Any) -> str:
            super().put_restored(key, body, metadata=metadata)
            return ""

    exc = raises(ObjectStorageBackupError, restore, target, VersionlessDestination())
    check(
        "FTF-1602-N2",
        "a destination that returns no version id fails the restore",
        exc is not None and "no version id" in str(exc),
    )


def test_verify_rechecks_record_totals_and_manifest_hash() -> None:
    _, target, _, record = backed_up()
    cases = (
        ("object_count", lambda d: d.update(object_count=d["object_count"] + 1), "object_count"),
        ("blob_count", lambda d: d.update(blob_count=d["blob_count"] - 1), "blob_count"),
        ("total_bytes", lambda d: d.update(total_bytes=d["total_bytes"] + 1), "total_bytes"),
        ("manifest_sha256", lambda d: d.update(manifest_sha256="0" * 64), "manifest_sha256"),
        (
            "entry metadata",
            lambda d: d["entries"][0].update(metadata=[["content-type", "text/evil"]]),
            "manifest_sha256",
        ),
        (
            "entry key",
            lambda d: d["entries"][0].update(key="tenants/o/other"),
            "manifest_sha256",
        ),
    )
    for label, change, word in cases:
        err = raises(
            BackupIncompleteError,
            verify_backup,
            with_record(target, change),
            "b1",
            organization_id="o",
            now=NOW,
        )
        check(
            "FTF-1602-N2",
            f"verify catches a record whose {label} was changed",
            err is not None and word in str(err),
        )
    err = raises(
        BackupIncompleteError,
        verify_backup,
        target,
        "b1",
        organization_id="o",
        expected_manifest_sha256="1" * 64,
        now=NOW,
    )
    check(
        "FTF-1602-N2",
        "verify refuses a record that differs from the manifest hash the caller holds",
        err is not None and "expected manifest" in str(err),
    )
    check(
        "FTF-1602-A3",
        "verify accepts the matching expected manifest hash",
        verify_backup(
            target,
            "b1",
            organization_id="o",
            expected_manifest_sha256=record.manifest_sha256,
            now=NOW,
        )
        == record,
    )
    dup = with_record(target, lambda d: d["blobs"].append(d["blobs"][0]))
    err = raises(BackupIncompleteError, verify_backup, dup, "b1", organization_id="o", now=NOW)
    check("FTF-1602-N2", "verify catches a blob listed twice", err is not None)


def test_tenant_scoping() -> None:
    source, refs = meta_fixture()
    other = source.add("tenants/p/datasets/x", b"other-tenant")
    foreign = BlobRef("dataset", "tenants/p/datasets/x", other, sha(b"other-tenant"), 12, "x", "p")
    target = MemStore()
    check(
        "FTF-1602-N3",
        "a backup mixing two tenants is refused before anything is written",
        refused("exactly one organization", run, source, [*refs, foreign], target)
        and nothing_written(target),
    )
    outside = bad(refs[0], key="tenants/p/datasets/d1.jsonl")
    check(
        "FTF-1602-N3",
        "a key outside the blob's own tenant prefix is refused",
        refused("outside its organization", run, source, [outside, *refs[1:]], target)
        and nothing_written(target),
    )
    sibling = bad(refs[0], organization_id="o2")
    check(
        "FTF-1602-N3",
        "tenant ids that are only a string prefix of each other do not match",
        refused("outside its organization", run, source, [sibling], target),
    )
    for key in ("tenants/o/../p/x", "tenants/o//x", "tenants/o/./x"):
        check(
            "FTF-1602-N3",
            f"key {key!r} with dot or empty segments is refused",
            refused("dot path segments", run, source, [bad(refs[0], key=key)], target),
        )
    for org in ("", " o", "o/p", "o "):
        check(
            "FTF-1602-N3",
            f"organization id {org!r} is refused",
            invalid(run, source, [bad(refs[0], organization_id=org)], target),
        )

    _, good, _, record = backed_up()
    check(
        "FTF-1602-N3",
        "the backup is stored under its tenant's own prefix, nowhere else",
        all(k.startswith("backups/o/b1/") for k in good.versions) and record.organization_id == "o",
    )
    check(
        "FTF-1602-N3",
        "restoring as another organization finds no backup",
        refuses_restore(good, "record is missing", organization_id="p"),
    )
    moved = clone(good)
    for key in list(moved.versions):
        moved.versions[key.replace("backups/o/", "backups/p/")] = moved.versions.pop(key)
    moved.retention = {k.replace("backups/o/", "backups/p/"): v for k, v in good.retention.items()}
    check(
        "FTF-1602-N3",
        "a record copied under another tenant's prefix is refused",
        refuses_restore(moved, "different organization", organization_id="p"),
    )
    escaping = with_record(
        good,
        lambda d: (
            d["entries"][0].update(key="tenants/p/escape"),
            d.update(manifest_sha256=osb._manifest_sha256(_entries_from(d))),
        ),
    )
    check(
        "FTF-1602-N3",
        "restore never writes outside the tenant's key prefix, even for a re-hashed record",
        refuses_restore(escaping, "outside the record's tenant"),
    )
    dotted = with_record(
        good,
        lambda d: (
            d["entries"][0].update(key="tenants/o/../p/x"),
            d.update(manifest_sha256=osb._manifest_sha256(_entries_from(d))),
        ),
    )
    check(
        "FTF-1602-N3",
        "restore refuses a re-hashed record whose key escapes through dot segments",
        refuses_restore(dotted, "dot path segments"),
    )
    for bad_org in ("", "o/p", " o"):
        check(
            "FTF-1602-N3",
            f"verify refuses organization id {bad_org!r}",
            invalid(verify_backup, good, "b1", organization_id=bad_org),
        )


def _entries_from(data: Any) -> list[Any]:
    return [
        osb.BackupEntry(**{**e, "metadata": tuple((str(k), str(v)) for k, v in e["metadata"])})
        for e in data["entries"]
    ]


def test_s3_adapter_metadata_and_restore_write() -> None:
    stub = StubS3Client()
    store = S3ObjectStore(stub, "bkt")  # type: ignore[arg-type]
    stub.headers = {
        "content-type": "application/jsonl",
        "x-amz-meta-owner": "team-a",
        "etag": '"abc"',
        "x-amz-version-id": "7",
        "server": "seaweed",
    }
    check(
        "FTF-1602-A3",
        "version_metadata keeps content type and user metadata only",
        store.version_metadata("k", "1")
        == {"content-type": "application/jsonl", "x-amz-meta-owner": "team-a"},
    )
    version = store.put_restored("k", b"x", metadata=META)
    check(
        "FTF-1602-A3",
        "put_restored sends the metadata and returns the new version id",
        version == "vv" and stub.put_headers == META,
    )
    exc = raises(ObjectStorageBackupError, store.put_restored, "k", b"x", metadata={"x-evil": "1"})
    check(
        "FTF-1602-N2",
        "put_restored refuses headers that are not content type or user metadata",
        exc is not None and ("put", "k") == stub.calls[-1],
    )
    check(
        "FTF-1602-N2",
        "put_restored sends nothing for a refused header",
        stub.calls.count(("put", "k")) == 1,
    )
    cases = [{"a": 1}, {1: "a"}, "x"]
    for meta in cases:
        source, refs = fixture()

        class Odd(MemStore):
            def version_metadata(self, key: str, version_id: str, m: Any = meta) -> Any:
                return m

        odd = Odd()
        odd.versions = source.versions
        check(
            "FTF-1602-N2",
            f"non-string object metadata {meta!r} is refused",
            refused("metadata must be a mapping of strings", run, odd, refs),
        )


def test_restore_cli() -> None:
    import contextlib
    import io

    source, refs = meta_fixture()
    stores = {"src": source, "dst": MemStore(), "clean": MemStore()}
    patches: list[Any] = [
        mock.patch.object(osb, "S3ObjectStore", lambda client, bucket: stores[bucket]),
        mock.patch.object(osb, "_client_from_env", lambda: None),
    ]
    for patch in patches:
        patch.start()
    try:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "m.json"
            path.write_text(json.dumps([dataclasses.asdict(r) for r in refs]))
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                code = osb.main(
                    [
                        "backup",
                        "--manifest",
                        str(path),
                        "--source-bucket",
                        "src",
                        "--target-bucket",
                        "dst",
                    ]
                )
            printed = json.loads(out.getvalue())
            check("FTF-1602-A3", "the backup command succeeds", code == 0)
            argv = [
                "restore",
                "--target-bucket",
                "dst",
                "--restore-bucket",
                "clean",
                "--backup-id",
                printed["backup_id"],
                "--organization-id",
                "o",
            ]
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                code = osb.main(argv)
            done = json.loads(out.getvalue())
            check(
                "FTF-1602-A3",
                "the restore command rebuilds every object into the clean bucket",
                code == 0 and done["object_count"] == 4 and len(stores["clean"].versions) == 4,
            )
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                again = osb.main(argv)
            check(
                "FTF-1602-N2",
                "restoring a second time into the now-dirty bucket exits 1",
                again == 1 and "FAIL" in err.getvalue(),
            )
    finally:
        for patch in patches:
            patch.stop()


# ---------------------------------------------------------------------------
# Round 2 mutation survivors: exactness of ids, hashes and header names
# ---------------------------------------------------------------------------


def rehash(data: Any) -> None:
    data["manifest_sha256"] = osb._manifest_sha256(_entries_from(data))


def edit_entry(data: Any, **changes: Any) -> None:
    data["entries"][0].update(changes)
    rehash(data)


def test_empty_manifest_and_org_segment_guards() -> None:
    source, refs = meta_fixture()
    check(
        "FTF-1602-N3",
        "an empty manifest is refused",
        refused("at least one blob", run, source, []),
    )
    slashed = bad(refs[0], organization_id="o/p", key="tenants/o/p/datasets/x")
    check(
        "FTF-1602-N3",
        "an organization id with a slash is refused by its own guard",
        refused("organization_id must be a plain path segment", run, source, [slashed]),
    )
    _, good, _, _ = backed_up()
    for org in ("", " o", "o ", "o/p"):
        check(
            "FTF-1602-N3",
            f"verify names the organization id guard for {org!r}",
            refused(
                "organization_id must be a plain path segment",
                verify_backup,
                good,
                "b1",
                organization_id=org,
            ),
        )


def test_organization_identity_is_compared_exactly() -> None:
    _, good, _, _ = backed_up()
    # A record stored under a differently-cased tenant path is another tenant's.
    cased = clone(good)
    for key in list(cased.versions):
        cased.versions[key.replace("backups/o/", "backups/O/")] = cased.versions.pop(key)
    cased.retention = {k.replace("backups/o/", "backups/O/"): v for k, v in good.retention.items()}
    check(
        "FTF-1602-N3",
        "a record under backups/O/ is not accepted for organization 'O'",
        refuses_restore(cased, "different organization", organization_id="O"),
    )
    for variant in ("o ", "O", " o"):
        forged = with_record(good, lambda d, v=variant: d.update(organization_id=v))
        check(
            "FTF-1602-N3",
            f"a record claiming organization {variant!r} is not tenant 'o'",
            refuses_restore(forged, "different organization"),
        )
    for variant in ("O", "o ", " o"):
        wrong_entry = with_record(
            good,
            lambda d, v=variant: edit_entry(d, organization_id=v),
        )
        check(
            "FTF-1602-N3",
            f"a re-hashed entry claiming organization {variant!r} is refused",
            refuses_restore(wrong_entry, "outside the record's tenant"),
        )


def test_mixed_case_organization_round_trip() -> None:
    source = MemStore()
    version = source.add("tenants/Acme/datasets/d", b"acme-data")
    ref = BlobRef("dataset", "tenants/Acme/datasets/d", version, sha(b"acme-data"), 9, "d", "Acme")
    target = MemStore()
    record = run(source, [ref], target, backup_id="b1")
    destination = MemStore()
    done = osb.restore_backup(target, destination, "b1", organization_id="Acme", now=NOW)
    check(
        "FTF-1602-A3",
        "a mixed-case organization id backs up, verifies and restores exactly",
        record.organization_id == "Acme"
        and done.organization_id == "Acme"
        and verify_backup(target, "b1", organization_id="Acme", now=NOW) == record
        and list(destination.versions) == ["tenants/Acme/datasets/d"],
    )
    check(
        "FTF-1602-N3",
        "a differently-cased organization id cannot reach that tenant's backup",
        refuses_restore(target, "record is missing", organization_id="acme"),
    )
    upper_entry = with_record(target, lambda d: edit_entry(d, organization_id="acme"), org="Acme")
    check(
        "FTF-1602-N3",
        "an entry whose organization only differs in case is refused",
        refuses_restore(upper_entry, "outside the record's tenant", organization_id="Acme"),
    )


def test_dot_segment_guard_is_exact() -> None:
    source = MemStore()
    key = "tenants/o/ ../datasets/spaced"
    version = source.add(key, b"spaced")
    ref = BlobRef("dataset", key, version, sha(b"spaced"), 6, "d", "o")
    target = MemStore()
    run(source, [ref], target, backup_id="b1")
    destination = MemStore()
    done = osb.restore_backup(target, destination, "b1", organization_id="o", now=NOW)
    check(
        "FTF-1602-A3",
        "a key segment that merely contains dots and a space is not a dot segment",
        done.object_count == 1 and key in destination.versions,
    )


def test_manifest_hash_is_compared_exactly() -> None:
    _, target, _, record = backed_up()
    upper = with_record(target, lambda d: d.update(manifest_sha256=d["manifest_sha256"].upper()))
    err = raises(BackupIncompleteError, verify_backup, upper, "b1", organization_id="o", now=NOW)
    check(
        "FTF-1602-N2",
        "an upper-case manifest hash in the record is not the computed hash",
        err is not None and "manifest_sha256 disagrees" in str(err),
    )
    err = raises(
        BackupIncompleteError,
        verify_backup,
        upper,
        "b1",
        organization_id="o",
        expected_manifest_sha256=record.manifest_sha256,
        now=NOW,
    )
    check(
        "FTF-1602-N2",
        "an upper-case record hash also differs from the caller's expected hash",
        err is not None and "expected manifest" in str(err),
    )
    err = raises(
        BackupIncompleteError,
        verify_backup,
        target,
        "b1",
        organization_id="o",
        expected_manifest_sha256=record.manifest_sha256.upper(),
        now=NOW,
    )
    check(
        "FTF-1602-N2",
        "an upper-case expected hash is not the record's lower-case hash",
        err is not None and "expected manifest" in str(err),
    )


def test_blob_digest_is_compared_exactly() -> None:
    _, target, _, record = backed_up()
    upper_digest = record.blobs[0][0].upper()
    old_key = f"backups/o/b1/blobs/{record.blobs[0][0]}"

    def to_upper(data: Any) -> None:
        low = record.blobs[0][0]
        data["blobs"] = [[d.upper() if d == low else d, n] for d, n in data["blobs"]]
        for entry in data["entries"]:
            if entry["sha256"] == low:
                entry["sha256"] = low.upper()
        rehash(data)

    forged = with_record(target, to_upper)
    forged.versions[f"backups/o/b1/blobs/{upper_digest}"] = list(forged.versions[old_key])
    forged.retention[f"backups/o/b1/blobs/{upper_digest}"] = forged.retention[old_key]
    err = raises(BackupIncompleteError, verify_backup, forged, "b1", organization_id="o", now=NOW)
    check(
        "FTF-1602-N2",
        "a blob recorded under an upper-case digest does not match its lower-case hash",
        err is not None and "no longer matches" in str(err),
    )


def test_restore_rechecks_each_blob_as_it_is_read() -> None:
    _, target, _, record = backed_up()
    blob_key = f"backups/o/b1/blobs/{record.blobs[0][0]}"
    size = record.blobs[0][1]

    class SwapAfterVerify(MemStore):
        def __init__(self, replacement: bytes) -> None:
            super().__init__()
            self.replacement = replacement
            self.reads = 0

        def get_latest(self, key: str) -> bytes:
            body = super().get_latest(key)
            if key == blob_key:
                self.reads += 1
                if self.reads >= 2:  # the first read is verify_backup's
                    return self.replacement
            return body

    for label, replacement in (("same-size", b"z" * size), ("shorter", b"z")):
        swapping = SwapAfterVerify(replacement)
        swapping.versions = {k: list(v) for k, v in target.versions.items()}
        swapping.retention = dict(target.retention)
        destination = MemStore()
        exc = raises(ObjectStorageBackupError, restore, swapping, destination)
        check(
            "FTF-1602-N2",
            f"a {label} blob swapped between verification and restore aborts the restore",
            exc is not None
            and "changed between verification and restore" in str(exc)
            and swapping.reads >= 2,
        )


def test_restore_record_names_its_backup() -> None:
    _, target, _, _ = backed_up()
    done = restore(target)
    check(
        "FTF-1602-A3",
        "the restore record names the backup it restored",
        done.backup_id == "b1" and json.loads(done.to_json())["backup_id"] == "b1",
    )


def test_s3_header_names_are_exact() -> None:
    stub = StubS3Client()
    store = S3ObjectStore(stub, "bkt")  # type: ignore[arg-type]
    stub.headers = {
        "Content-Type": "text/evil",
        "X-Amz-Meta-Owner": "x",
        " content-type": "text/evil",
        "content-type": "application/jsonl",
    }
    check(
        "FTF-1602-N2",
        "only exact lower-case header names are captured as metadata",
        store.version_metadata("k", "1") == {"content-type": "application/jsonl"},
    )
    for name in ("Content-Type", "X-Amz-Meta-Owner", " content-type"):
        stub.calls.clear()
        exc = raises(ObjectStorageBackupError, store.put_restored, "k", b"x", metadata={name: "v"})
        check(
            "FTF-1602-N2",
            f"put_restored refuses the non-canonical header name {name!r}",
            exc is not None and ("put", "k") not in stub.calls,
        )


def main() -> int:
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
    if FAILURES:
        print("\n".join(FAILURES))
        print(f"{len(FAILURES)} check(s) failed")
        return 1
    print("all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
