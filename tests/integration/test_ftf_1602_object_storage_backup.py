#!/usr/bin/env python3
"""FTF-1602 -- real object-storage backup integration proof.

Boots the same live SeaweedFS S3 fixture as FTF-R017 / FTF-0500B
(`infra/docker/docker-compose.yml`'s `s3` service) and runs
`scripts/object_storage_backup.py` against it with the real stdlib SigV4 client:

  A1 a dataset, a two-shard model and a result are backed up from a versioned source bucket
     into an object-lock target bucket; every blob is re-read from the real server, hashes
     equal the manifest, and every blob and the record carry real COMPLIANCE retention;
  N1 a retained backup blob version cannot be deleted from the real target;
  N1 an overwritten (stale) version, a deleted key, a missing shard and a lost encryption key
     reference each fail completeness and leave the real target bucket empty;
  N1 a blob replaced on the real target after the backup is caught by verify_backup.

Requires a Docker daemon with container-registry egress (GitHub Actions has it; the
interactive dev sandbox does not -- same documented limit as FTF-0004 / FTF-R017). CI job:
`object-storage-backup-integration`.

Run directly: python3 tests/integration/test_ftf_1602_object_storage_backup.py
"""

from __future__ import annotations

import hashlib
import http.client
import os
import pathlib
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
from datetime import UTC, datetime

ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "apps" / "api"))

import yaml  # noqa: E402
from scripts.object_storage_backup import (  # noqa: E402
    BackupIncompleteError,
    BlobRef,
    ObjectStorageBackupError,
    S3ObjectStore,
    create_backup,
    verify_backup,
)

from app.services.storage_semantics_client import (  # noqa: E402
    S3Client,
    S3Credentials,
    S3Endpoint,
    S3Error,
)

COMPOSE_FILE = "infra/docker/docker-compose.yml"
PROFILE_PATH = ROOT / "infra" / "internal" / "storage-profile.yaml"
FAILURES: list[str] = []


def check(criterion_id: str, description: str, condition: bool) -> None:
    if not condition:
        FAILURES.append(f"FAIL [{criterion_id}] {description}")


def run(*args: str, check_exit: bool = True) -> subprocess.CompletedProcess[str]:
    print("+", " ".join(args), flush=True)
    return subprocess.run(args, check=check_exit, capture_output=True, text=True)


def compose(*args: str, check_exit: bool = True) -> subprocess.CompletedProcess[str]:
    return run("docker", "compose", "-f", COMPOSE_FILE, *args, check_exit=check_exit)


def _wait_for_tcp(host: str, port: int, timeout_seconds: float = 60.0) -> None:
    import socket

    deadline = time.monotonic() + timeout_seconds
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((host, port), timeout=2.0):
                return
        except OSError as exc:  # noqa: PERF203
            last_error = exc
            time.sleep(1.0)
    raise TimeoutError(f"S3 endpoint {host}:{port} never became reachable: {last_error}")


def _wait_for_s3_http_ready(endpoint: S3Endpoint, timeout_seconds: float = 90.0) -> None:
    deadline = time.monotonic() + timeout_seconds
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            urllib.request.urlopen(  # noqa: S310
                urllib.request.Request(f"{endpoint.base_url}/", method="GET"), timeout=3.0
            )
            return
        except urllib.error.HTTPError:
            return
        except (urllib.error.URLError, http.client.HTTPException, OSError) as exc:
            last_error = exc
            time.sleep(1.0)
    raise TimeoutError(f"S3 endpoint never returned a real HTTP response: {last_error}")


def _load_endpoint_and_credentials() -> tuple[S3Endpoint, S3Credentials]:
    profile = yaml.safe_load(PROFILE_PATH.read_text(encoding="utf-8"))
    env = profile["endpoint"]
    defaults = env["local_fixture_defaults"]

    def _get(var_key: str, default_key: str) -> str:
        return os.environ.get(env[var_key], str(defaults[default_key]))

    endpoint = S3Endpoint(
        scheme=_get("scheme_env_var", "scheme"),
        host=_get("host_env_var", "host"),
        port=int(_get("port_env_var", "port")),
        region=_get("region_env_var", "region"),
    )
    credentials = S3Credentials(
        access_key=_get("access_key_env_var", "access_key"),
        secret_key=_get("secret_key_env_var", "secret_key"),
    )
    return endpoint, credentials


def _bucket(label: str) -> str:
    return f"sovra-ftf1602-{label}-{uuid.uuid4().hex[:12]}"


def _sha(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def _setup(client: S3Client) -> tuple[str, str, S3ObjectStore, S3ObjectStore]:
    src, dst = _bucket("src"), _bucket("dst")
    client.create_bucket(src)
    client.put_bucket_versioning(src, enabled=True)
    client.create_bucket(dst, object_lock_enabled=True)
    client.put_bucket_versioning(dst, enabled=True)
    return src, dst, S3ObjectStore(client, src), S3ObjectStore(client, dst)


def _seed(client: S3Client, bucket: str) -> list[BlobRef]:
    refs: list[BlobRef] = []
    for kind, key, body, artifact, index, count in (
        ("dataset", "tenants/o/datasets/d1.jsonl", b'{"a":1}\n{"a":2}\n', "d1", 0, 1),
        ("model", "tenants/o/models/m1/shard-0", b"weights-shard-zero" * 100, "m1", 0, 2),
        ("model", "tenants/o/models/m1/shard-1", b"weights-shard-one" * 100, "m1", 1, 2),
        ("result", "tenants/o/results/r1.json", b'{"pass":true}', "r1", 0, 1),
    ):
        put = client.put_object(bucket, key, body)
        refs.append(
            BlobRef(
                kind=kind,
                key=key,
                version_id=put["version_id"],
                sha256=_sha(body),
                size_bytes=len(body),
                artifact_id=artifact,
                shard_index=index,
                shard_count=count,
            )
        )
    return refs


def _empty(client: S3Client, bucket: str) -> bool:
    return client.list_object_versions(bucket, prefix="backups/") == []


def _backup(src: S3ObjectStore, dst: S3ObjectStore, refs: list[BlobRef], **kw: object):
    args: dict[str, object] = {
        "source": src,
        "target": dst,
        "known_key_refs": frozenset({"kms/recoverable"}),
        "retain_days": 1,
    }
    args.update(kw)
    return create_backup(refs, **args)  # type: ignore[arg-type]


def test_complete_backup_is_hash_verified_and_retained(client: S3Client) -> None:
    source_bucket, target_bucket, src, dst = _setup(client)
    refs = _seed(client, source_bucket)
    check(
        "FTF-1602-A1",
        "setup: the source bucket holds real, distinct version ids",
        len({r.version_id for r in refs}) == 4 and all(r.version_id for r in refs),
    )
    record = _backup(src, dst, refs)
    check("FTF-1602-A1", "the record counts all four objects", record.object_count == 4)
    for ref in refs:
        stored, _ = client.get_object(
            target_bucket, f"backups/{record.backup_id}/blobs/{ref.sha256}"
        )
        check(
            "FTF-1602-A1",
            f"{ref.key}: the real target holds bytes with the manifest's sha256",
            _sha(stored) == ref.sha256 and len(stored) == ref.size_bytes,
        )
        held = client.get_object_retention(
            target_bucket, f"backups/{record.backup_id}/blobs/{ref.sha256}"
        )
        check(
            "FTF-1602-A1",
            f"{ref.key}: the real server reports COMPLIANCE retention on the blob",
            held.get("Mode") == "COMPLIANCE",
        )
    record_held = client.get_object_retention(
        target_bucket, f"backups/{record.backup_id}/backup-record.json"
    )
    check("FTF-1602-A1", "the record is retained too", record_held.get("Mode") == "COMPLIANCE")
    until = datetime.fromisoformat(record_held["RetainUntilDate"].replace("Z", "+00:00"))
    check("FTF-1602-A1", "retention ends in the future", until > datetime.now(UTC))
    check(
        "FTF-1602-A1",
        "verify_backup accepts the real backup",
        verify_backup(dst, record.backup_id) == record,
    )

    first_blob = f"backups/{record.backup_id}/blobs/{record.blobs[0][0]}"
    versions = [
        v
        for v in client.list_object_versions(target_bucket, prefix=first_blob)
        if v.get("Key") == first_blob
    ]
    try:
        client.delete_object(target_bucket, first_blob, version_id=versions[0]["VersionId"])
        deleted = True
    except S3Error as exc:
        deleted = exc.status_code not in (403, 409)
    check("FTF-1602-N1", "a retained backup blob version cannot be deleted", not deleted)


def test_stale_version_fails_and_writes_nothing(client: S3Client) -> None:
    source_bucket, target_bucket, src, dst = _setup(client)
    refs = _seed(client, source_bucket)
    client.put_object(source_bucket, refs[0].key, b"overwritten after the manifest was cut")
    err = None
    try:
        _backup(src, dst, refs)
    except BackupIncompleteError as exc:
        err = exc
    check(
        "FTF-1602-N1",
        "an overwritten version fails completeness as stale",
        err is not None and "stale" in str(err),
    )
    check("FTF-1602-N1", "nothing was written to the real target", _empty(client, target_bucket))


def test_deleted_key_fails(client: S3Client) -> None:
    source_bucket, target_bucket, src, dst = _setup(client)
    refs = _seed(client, source_bucket)
    client.delete_object(source_bucket, refs[3].key)
    err = None
    try:
        _backup(src, dst, refs)
    except BackupIncompleteError as exc:
        err = exc
    check(
        "FTF-1602-N1",
        "a key deleted after the manifest fails completeness",
        err is not None and "stale" in str(err),
    )
    check("FTF-1602-N1", "nothing was written to the real target", _empty(client, target_bucket))


def test_missing_shard_fails(client: S3Client) -> None:
    source_bucket, target_bucket, src, dst = _setup(client)
    refs = _seed(client, source_bucket)
    err = None
    try:
        _backup(src, dst, [r for r in refs if r.key != "tenants/o/models/m1/shard-1"])
    except BackupIncompleteError as exc:
        err = exc
    check(
        "FTF-1602-N1",
        "a missing model shard fails completeness",
        err is not None and "missing shards" in str(err),
    )
    check("FTF-1602-N1", "nothing was written to the real target", _empty(client, target_bucket))


def test_lost_encryption_key_reference_fails(client: S3Client) -> None:
    import dataclasses

    source_bucket, target_bucket, src, dst = _setup(client)
    refs = _seed(client, source_bucket)
    lost = [dataclasses.replace(refs[0], encrypted=True, encryption_key_ref="kms/lost"), *refs[1:]]
    err = None
    try:
        _backup(src, dst, lost)
    except BackupIncompleteError as exc:
        err = exc
    check(
        "FTF-1602-N1",
        "a lost encryption key reference fails completeness",
        err is not None and "not recoverable" in str(err),
    )
    check("FTF-1602-N1", "nothing was written to the real target", _empty(client, target_bucket))
    kept = [
        dataclasses.replace(refs[0], encrypted=True, encryption_key_ref="kms/recoverable"),
        *refs[1:],
    ]
    check(
        "FTF-1602-A1",
        "the same blob with a recoverable key reference backs up",
        _backup(src, dst, kept).blob_count == 4,
    )


def test_replaced_backup_blob_is_caught_by_verify(client: S3Client) -> None:
    source_bucket, target_bucket, src, dst = _setup(client)
    record = _backup(src, dst, _seed(client, source_bucket))
    blob_key = f"backups/{record.backup_id}/blobs/{record.blobs[0][0]}"
    client.put_object(target_bucket, blob_key, b"replaced after the backup")
    err: Exception | None = None
    try:
        verify_backup(dst, record.backup_id)
    except ObjectStorageBackupError as exc:
        err = exc
    check(
        "FTF-1602-N1",
        "verify_backup catches a replaced blob on the real target",
        err is not None and "no longer matches" in str(err),
    )


def main() -> None:
    endpoint, credentials = _load_endpoint_and_credentials()
    booted_here = False
    try:
        _wait_for_tcp(endpoint.host, endpoint.port, timeout_seconds=3.0)
    except TimeoutError:
        print("S3 fixture not yet reachable -- booting it via docker compose", flush=True)
        compose("up", "-d", "s3")
        booted_here = True
        _wait_for_tcp(endpoint.host, endpoint.port, timeout_seconds=90.0)
    _wait_for_s3_http_ready(endpoint, timeout_seconds=90.0)

    client = S3Client(endpoint, credentials)
    for name, fn in sorted(globals().items()):
        if not (name.startswith("test_") and callable(fn)):
            continue
        print(f"-- running {name}", flush=True)
        try:
            fn(client)
        except Exception as exc:  # noqa: BLE001
            FAILURES.append(f"FAIL [{name}] unexpected exception: {exc!r}")

    if FAILURES:
        print(f"FTF-1602 object storage backup integration: {len(FAILURES)} failed")
        for failure in FAILURES:
            print(f"  {failure}")
        logs = compose("logs", "--no-color", "s3", check_exit=False)
        print(logs.stdout[-4000:])
        print(logs.stderr[-1000:])
        if booted_here:
            compose("stop", "s3", check_exit=False)
        print(f"FTF-1602 object storage backup integration: {len(FAILURES)} failed (summary)")
        for failure in FAILURES:
            print(f"  {failure}")
        sys.exit(1)
    if booted_here:
        compose("stop", "s3", check_exit=False)
    print("FTF-1602 object storage backup integration: all checks passed")
    sys.exit(0)


if __name__ == "__main__":
    main()
