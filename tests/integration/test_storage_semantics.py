#!/usr/bin/env python3
"""FTF-R017 -- real storage-semantics integration proof.

Proves Section 80.4's eight named semantics (multipart, checksum,
versioning, immutable-publication, identity, retention/deletion,
encryption, backup and restore) against a real, live SeaweedFS S3-API
container -- never a canned/mocked response. Boots
`infra/docker/docker-compose.yml`'s `s3` service for real via `docker
compose`, using the real, stdlib-only SigV4 client
(apps/api/app/services/storage_semantics_client.py), the same way
`scripts/verify_docker_local_stack.py` boots the whole stack for FTF-0004.

Requires a real Docker daemon with container-registry egress. This
repo's interactive dev sandbox does not have that (confirmed again this
task with a real `docker pull` attempt against this exact pinned SeaweedFS
digest -- same reproducible 403 FTF-0004's own evidence already
documented); GitHub Actions' hosted runner does, so this runs there
(.github/workflows/ci.yml, job `storage-semantics-integration`).

Run directly (against an already-running or bootable local stack):
    python3 tests/integration/test_storage_semantics.py
"""

from __future__ import annotations

import base64
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

ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "apps" / "api"))

import yaml  # noqa: E402

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
    """A real TCP connect can succeed before SeaweedFS's S3 gateway
    goroutine is actually ready to serve requests (its `weed server`
    process starts master/volume/filer/S3 in sequence) -- observed for
    real in this task's own first CI run: every request failed with
    `http.client.RemoteDisconnected` immediately after `docker compose up
    -d` returned, even though the TCP port was already accepting
    connections. This retries a real, unsigned HTTP GET until the server
    returns an actual HTTP response (any status code, including an auth
    error) instead of dropping the connection -- application-level
    readiness, not just a TCP accept."""
    deadline = time.monotonic() + timeout_seconds
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            urllib.request.urlopen(  # noqa: S310
                urllib.request.Request(f"{endpoint.base_url}/", method="GET"), timeout=3.0
            )
            return
        except urllib.error.HTTPError:
            return  # a real HTTP response (even an error status) means the server is up
        except (urllib.error.URLError, http.client.HTTPException, OSError) as exc:
            last_error = exc
            time.sleep(1.0)
    raise TimeoutError(
        f"S3 endpoint {endpoint.base_url} never returned a real HTTP response: {last_error}"
    )


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


def _unique_bucket(label: str) -> str:
    return f"sovra-test-{label}-{uuid.uuid4().hex[:12]}"


def _sse_c_headers(key: bytes) -> dict[str, str]:
    return {
        "x-amz-server-side-encryption-customer-algorithm": "AES256",
        "x-amz-server-side-encryption-customer-key": base64.b64encode(key).decode(),
        "x-amz-server-side-encryption-customer-key-MD5": base64.b64encode(
            hashlib.md5(key, usedforsecurity=False).digest()
        ).decode(),
    }


def test_multipart(client: S3Client) -> None:
    bucket = _unique_bucket("multipart")
    client.create_bucket(bucket)
    key = "large-object.bin"
    part1 = b"A" * (5 * 1024 * 1024)  # S3's minimum non-final part size
    part2 = b"B" * 1024
    upload_id = client.create_multipart_upload(bucket, key)
    etag1 = client.upload_part(bucket, key, upload_id, 1, part1)
    etag2 = client.upload_part(bucket, key, upload_id, 2, part2)
    client.complete_multipart_upload(bucket, key, upload_id, [(1, etag1), (2, etag2)])

    body, _ = client.get_object(bucket, key)
    check("FTF-R017", "multipart: reconstructed object is byte-identical", body == part1 + part2)


def test_multipart_abort(client: S3Client) -> None:
    bucket = _unique_bucket("multipart-abort")
    client.create_bucket(bucket)
    key = "aborted.bin"
    upload_id = client.create_multipart_upload(bucket, key)
    client.upload_part(bucket, key, upload_id, 1, b"C" * (5 * 1024 * 1024))
    client.abort_multipart_upload(bucket, key, upload_id)
    try:
        client.get_object(bucket, key)
        check("FTF-R017", "multipart: an aborted upload never publishes a partial object", False)
    except S3Error as exc:
        check(
            "FTF-R017",
            "multipart: an aborted upload never publishes a partial object",
            exc.status_code == 404,
        )


def test_checksum(client: S3Client) -> None:
    bucket = _unique_bucket("checksum")
    client.create_bucket(bucket)
    key = "checked.txt"
    body = b"sovra checksum semantics fixture"
    correct_md5_b64 = base64.b64encode(hashlib.md5(body, usedforsecurity=False).digest()).decode()

    result = client.put_object(bucket, key, body, sse_c_headers={"Content-MD5": correct_md5_b64})
    local_md5_hex = hashlib.md5(body, usedforsecurity=False).hexdigest()
    check(
        "FTF-R017",
        "checksum: server ETag matches locally computed MD5 for a correct upload",
        result["etag"] == local_md5_hex,
    )

    wrong_md5_b64 = base64.b64encode(
        hashlib.md5(b"different content", usedforsecurity=False).digest()
    ).decode()
    try:
        client.put_object(bucket, key, body, sse_c_headers={"Content-MD5": wrong_md5_b64})
        check(
            "FTF-R017",
            "checksum: a Content-MD5 that does not match the uploaded body is rejected",
            False,
        )
    except S3Error as exc:
        check(
            "FTF-R017",
            "checksum: a Content-MD5 that does not match the uploaded body is rejected",
            exc.status_code == 400,
        )


def test_versioning(client: S3Client) -> None:
    bucket = _unique_bucket("versioning")
    client.create_bucket(bucket)
    client.put_bucket_versioning(bucket, enabled=True)
    check(
        "FTF-R017",
        "versioning: GetBucketVersioning reports Enabled after PutBucketVersioning",
        client.get_bucket_versioning(bucket) == "Enabled",
    )

    key = "versioned.txt"
    result_v1 = client.put_object(bucket, key, b"version one")
    result_v2 = client.put_object(bucket, key, b"version two")
    check(
        "FTF-R017",
        "versioning: two overwrites of the same key produce two distinct version IDs",
        bool(result_v1["version_id"]) and result_v1["version_id"] != result_v2["version_id"],
    )

    body_v1, _ = client.get_object(bucket, key, version_id=result_v1["version_id"])
    body_latest, _ = client.get_object(bucket, key)
    check(
        "FTF-R017",
        "versioning: the old version is still independently retrievable by version ID",
        body_v1 == b"version one" and body_latest == b"version two",
    )

    versions = client.list_object_versions(bucket, prefix=key)
    check(
        "FTF-R017",
        "versioning: ListObjectVersions reports both versions of the object",
        len({v.get("VersionId") for v in versions if isinstance(v, dict)}) >= 2,
    )


def test_immutable_publication(client: S3Client) -> None:
    bucket = _unique_bucket("immutable-pub")
    client.create_bucket(bucket, object_lock_enabled=True)
    key = "published.txt"
    result = client.put_object(bucket, key, b"immutable published dataset version")
    from datetime import UTC, datetime, timedelta

    retain_until = (datetime.now(UTC) + timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    client.put_object_retention(bucket, key, mode="COMPLIANCE", retain_until_date=retain_until)

    try:
        client.delete_object(bucket, key, version_id=result["version_id"])
        check(
            "FTF-R017",
            "immutable_publication: a COMPLIANCE-retained object version cannot be deleted",
            False,
        )
    except S3Error as exc:
        check(
            "FTF-R017",
            "immutable_publication: a COMPLIANCE-retained object version cannot be deleted",
            exc.status_code in (403, 409),
        )


def test_identity(client: S3Client, endpoint: S3Endpoint) -> None:
    bucket = _unique_bucket("identity")
    client.create_bucket(bucket)
    client.put_object(bucket, "probe.txt", b"identity probe")

    wrong_client = S3Client(
        endpoint, S3Credentials(access_key="not-a-real-key", secret_key="not-a-real-secret")
    )
    try:
        wrong_client.get_object(bucket, "probe.txt")
        check(
            "FTF-R017",
            "identity: a request signed with an unconfigured access key is rejected",
            False,
        )
    except S3Error as exc:
        check(
            "FTF-R017",
            "identity: a request signed with an unconfigured access key is rejected",
            exc.status_code in (403, 401),
        )

    body, _ = client.get_object(bucket, "probe.txt")
    check(
        "FTF-R017",
        "identity: the same request signed with the real configured identity succeeds",
        body == b"identity probe",
    )


def test_retention_deletion(client: S3Client) -> None:
    bucket = _unique_bucket("retention-del")
    client.create_bucket(bucket)
    client.put_bucket_versioning(bucket, enabled=True)
    key = "deletable.txt"
    put_result = client.put_object(bucket, key, b"not retained")

    delete_result = client.delete_object(bucket, key)
    check(
        "FTF-R017",
        "retention_deletion: deleting an object in a versioned bucket creates a delete marker",
        delete_result["delete_marker"] == "true",
    )

    body, _ = client.get_object(bucket, key, version_id=put_result["version_id"])
    check(
        "FTF-R017",
        "retention_deletion: the object is still recoverable by its prior version ID",
        body == b"not retained",
    )


def test_encryption(client: S3Client) -> None:
    bucket = _unique_bucket("encryption")
    client.create_bucket(bucket)
    key = "secret.bin"
    plaintext = b"sovra sse-c encryption semantics fixture"
    sse_key = os.urandom(32)

    client.put_object(bucket, key, plaintext, sse_c_headers=_sse_c_headers(sse_key))

    body_with_key, _ = client.get_object(bucket, key, sse_c_headers=_sse_c_headers(sse_key))
    check(
        "FTF-R017",
        "encryption: GET with the correct SSE-C key returns the original plaintext",
        body_with_key == plaintext,
    )

    try:
        client.get_object(bucket, key)
        check(
            "FTF-R017",
            "encryption: GET without the SSE-C key is rejected, not silently served",
            False,
        )
    except S3Error as exc:
        check(
            "FTF-R017",
            "encryption: GET without the SSE-C key is rejected, not silently served",
            exc.status_code == 400,
        )

    wrong_key = os.urandom(32)
    try:
        client.get_object(bucket, key, sse_c_headers=_sse_c_headers(wrong_key))
        check(
            "FTF-R017",
            "encryption: GET with the wrong SSE-C key is rejected, not silently served",
            False,
        )
    except S3Error as exc:
        check(
            "FTF-R017",
            "encryption: GET with the wrong SSE-C key is rejected, not silently served",
            exc.status_code == 400,
        )


def test_backup_and_restore(client: S3Client) -> None:
    source_bucket = _unique_bucket("backup-src")
    backup_bucket = _unique_bucket("backup-dst")
    client.create_bucket(source_bucket)
    client.create_bucket(backup_bucket)
    key = "restorable.txt"
    original = b"sovra backup and restore semantics fixture" * 100
    original_hash = hashlib.sha256(original).hexdigest()

    client.put_object(source_bucket, key, original)
    body, _ = client.get_object(source_bucket, key)
    client.put_object(backup_bucket, key, body)  # real object-storage-to-object-storage copy

    client.delete_object(source_bucket, key)
    try:
        client.get_object(source_bucket, key)
        deleted_for_real = False
    except S3Error:
        deleted_for_real = True
    check(
        "FTF-R017",
        "backup_and_restore: the original is genuinely gone before restore",
        deleted_for_real,
    )

    restored, _ = client.get_object(backup_bucket, key)
    client.put_object(source_bucket, key, restored)
    restored_from_source, _ = client.get_object(source_bucket, key)
    restored_hash = hashlib.sha256(restored_from_source).hexdigest()
    check(
        "FTF-R017",
        "backup_and_restore: the restored object is byte-identical and hash-identical",
        restored_from_source == original and restored_hash == original_hash,
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

    # A real TCP accept is not the same as the S3 gateway actually being
    # ready to serve requests -- see _wait_for_s3_http_ready's own
    # docstring for the real failure this caught in this task's own first
    # CI run. Always wait for it, whether or not this process booted the
    # fixture itself.
    _wait_for_s3_http_ready(endpoint, timeout_seconds=90.0)

    client = S3Client(endpoint, credentials)

    for name, fn in sorted(globals().items()):
        if not (name.startswith("test_") and callable(fn)):
            continue
        print(f"-- running {name}", flush=True)
        try:
            if name == "test_identity":
                fn(client, endpoint)
            else:
                fn(client)
        except S3Error as exc:
            FAILURES.append(f"FAIL [{name}] unexpected S3Error: {exc}")
        except Exception as exc:  # noqa: BLE001
            FAILURES.append(f"FAIL [{name}] unexpected exception: {exc!r}")

    if FAILURES:
        print(f"FTF-R017 storage semantics: {len(FAILURES)} failed")
        for failure in FAILURES:
            print(f"  {failure}")
        print("-- s3 container logs (diagnosis, not a canned pass) --", flush=True)
        logs = compose("logs", "--no-color", "s3", check_exit=False)
        print(logs.stdout)
        print(logs.stderr)
        if booted_here:
            compose("stop", "s3", check_exit=False)
        sys.exit(1)

    if booted_here:
        compose("stop", "s3", check_exit=False)

    print("FTF-R017 storage semantics: all checks passed")
    sys.exit(0)


if __name__ == "__main__":
    main()
