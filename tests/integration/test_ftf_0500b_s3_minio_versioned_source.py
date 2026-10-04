#!/usr/bin/env python3
"""FTF-0500B -- real versioned S3/MinIO source integration proof.

Closes the 2026-10-04 Control-plane L2 review's DoD item 4 for this task
("the DoD item 'real versioned SeaweedFS/MinIO integration, or
BLOCKED_EXTERNAL' is unmet, though CI already runs SeaweedFS"). Boots
`infra/docker/docker-compose.yml`'s `s3` service (the same real, live
SeaweedFS S3-API container `tests/integration/test_storage_semantics.py`
already qualifies for FTF-R017), the same way that task's own test does,
and runs this task's own real connector
(`app.datasets.s3_minio_source_validation_metadata_preview.
validate_and_preview_s3_minio_source`) against it -- never a synthetic
plain-HTTP fixture (that is `tests/tasks/test_ftf_0500b.py`'s own, separate,
no-DB job).

Proves, against a real server (not a hand-built response):
  - a real versioned bucket's two real overwrites of the same key produce
    two real, distinct `VersionId`s, both present in this module's own
    bounded preview, with a real, non-empty `manifest_hash`;
  - a real *unversioned* bucket's real `ListObjectVersions` response
    reports the real literal `VersionId` `"null"` SeaweedFS (and AWS S3)
    themselves use for this case, and this module's own
    `VersioningDisabledError` negative_acceptance check fires against that
    real response -- not merely against a hand-crafted test fixture.

Requires a real Docker daemon with container-registry egress. This
repo's interactive dev sandbox does not have that (same reproducible 403
FTF-0004's/FTF-R017's own evidence already documents); GitHub Actions'
hosted runner does, so this runs there (.github/workflows/ci.yml, job
`s3-minio-versioned-source-integration`).

Run directly (against an already-running or bootable local stack):
    python3 tests/integration/test_ftf_0500b_s3_minio_versioned_source.py
"""

from __future__ import annotations

import http.client
import ipaddress
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
from packages.security.url_policy import ConnectionPolicy  # noqa: E402

import app.datasets.s3_minio_source_validation_metadata_preview as s3_preview  # noqa: E402
from app.services.storage_semantics_client import (  # noqa: E402
    S3Client,
    S3Credentials,
    S3Endpoint,
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
    """Same real application-level readiness check as
    `test_storage_semantics.py` (FTF-R017) -- a real TCP accept can
    succeed before SeaweedFS's S3 gateway is actually ready to serve
    requests; a real HTTP response (even an error status) is required."""
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
    raise TimeoutError(
        f"S3 endpoint {endpoint.base_url} never returned a real HTTP response: {last_error}"
    )


def _load_endpoint_and_credentials() -> tuple[S3Endpoint, S3Credentials]:
    profile = yaml.safe_load(PROFILE_PATH.read_text(encoding="utf-8"))
    env = profile["endpoint"]
    defaults = env["local_fixture_defaults"]

    def _get(var_key: str, default_key: str) -> str:
        import os

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
    return f"sovra-ftf0500b-{label}-{uuid.uuid4().hex[:12]}"


def _policy(endpoint: S3Endpoint) -> ConnectionPolicy:
    return ConnectionPolicy(
        name="ftf-0500b-versioned-source-integration",
        allowed_schemes=frozenset({endpoint.scheme}),
        allowed_ports=frozenset({endpoint.port}),
        explicit_private_allowlist=(
            ipaddress.ip_network("127.0.0.0/8"),
            ipaddress.ip_network("::1/128"),
        ),
    )


def test_real_versioned_bucket_produces_an_exact_two_version_manifest(
    client: S3Client, endpoint: S3Endpoint, credentials: S3Credentials
) -> None:
    bucket = _unique_bucket("versioned")
    client.create_bucket(bucket)
    client.put_bucket_versioning(bucket, enabled=True)
    check(
        "FTF-0500B",
        "setup: the real bucket genuinely reports versioning Enabled before this task's own "
        "connector is exercised against it",
        client.get_bucket_versioning(bucket) == "Enabled",
    )

    prefix = "org-42/"
    key = f"{prefix}dataset.jsonl"
    result_v1 = client.put_object(bucket, key, b"first real version")
    result_v2 = client.put_object(bucket, key, b"second real version")
    check(
        "FTF-0500B",
        "setup: two real overwrites of the same key produced two distinct real VersionIds",
        bool(result_v1["version_id"])
        and bool(result_v2["version_id"])
        and result_v1["version_id"] != result_v2["version_id"],
    )

    config = s3_preview.S3MinioSourceConfig(
        endpoint=endpoint,
        credentials=credentials,
        bucket=bucket,
        tenant_key_prefix=prefix,
    )
    preview = s3_preview.validate_and_preview_s3_minio_source(config, policy=_policy(endpoint))

    returned_version_ids = {o.version_id for o in preview.objects if o.key == key}
    check(
        "FTF-0500B",
        "acceptance: this task's own connector, run against a real versioned S3-compatible "
        "server, returns both real VersionIds for the same key -- a genuine exact version "
        "manifest, not a synthetic one",
        returned_version_ids == {result_v1["version_id"], result_v2["version_id"]},
    )
    check(
        "FTF-0500B",
        "acceptance: the connector produces a real, non-empty immutable manifest_hash against "
        "a real server response",
        isinstance(preview.manifest_hash, str) and len(preview.manifest_hash) == 64,
    )
    check(
        "FTF-0500B",
        "acceptance: no entry's VersionId is ever the real unversioned sentinel on a bucket "
        "that genuinely has versioning enabled",
        all(o.version_id.strip().lower() != "null" for o in preview.objects),
    )


def test_real_unversioned_bucket_is_genuinely_refused(
    client: S3Client, endpoint: S3Endpoint, credentials: S3Credentials
) -> None:
    """Never enables versioning on this bucket -- a real S3-compatible
    server's own real `ListObjectVersions` response for it must report the
    real literal VersionId `"null"`, and this task's own connector must
    refuse it for real, not merely against a hand-built fixture."""
    bucket = _unique_bucket("unversioned")
    client.create_bucket(bucket)

    prefix = "org-7/"
    key = f"{prefix}dataset.jsonl"
    client.put_object(bucket, key, b"only ever one real, unversioned object")

    config = s3_preview.S3MinioSourceConfig(
        endpoint=endpoint,
        credentials=credentials,
        bucket=bucket,
        tenant_key_prefix=prefix,
    )
    raised = False
    try:
        s3_preview.validate_and_preview_s3_minio_source(config, policy=_policy(endpoint))
    except s3_preview.VersioningDisabledError:
        raised = True
    check(
        "FTF-0500B",
        "negative_acceptance: a real, genuinely never-versioned bucket's real "
        "ListObjectVersions response (literal VersionId 'null') is refused by this task's "
        "own connector against a real live server, not only a synthetic fixture",
        raised,
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

    client = S3Client(endpoint, credentials, debug=True)

    for name, fn in sorted(globals().items()):
        if not (name.startswith("test_") and callable(fn)):
            continue
        print(f"-- running {name}", flush=True)
        try:
            fn(client, endpoint, credentials)
        except s3_preview.S3MinioSourceValidationError as exc:
            FAILURES.append(f"FAIL [{name}] unexpected {type(exc).__name__}: {exc}")
        except Exception as exc:  # noqa: BLE001
            FAILURES.append(f"FAIL [{name}] unexpected exception: {exc!r}")

    if FAILURES:
        print(f"FTF-0500B versioned S3/MinIO source integration: {len(FAILURES)} failed")
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

    print("FTF-0500B versioned S3/MinIO source integration: all checks passed")
    sys.exit(0)


if __name__ == "__main__":
    main()
