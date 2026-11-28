#!/usr/bin/env python3
"""FTF-0705 object-store access against the REAL SeaweedFS S3 API (no doubles).

Boots `infra/docker/docker-compose.yml`'s `s3` service when it is not already reachable (the same
pinned image and identity file every S3 integration job uses) and proves the negative clause
"no object-store access stays blocked" on the real server, plus the passing probe:

I1  right identity + existing bucket: the probe round-trips, and the probe object is really gone
    afterwards (an independent HEAD answers 404, the bucket lists no probe key).
I2  wrong secret key                      -> object_store_denied
I3  unknown access key                    -> object_store_denied
I4  bucket that does not exist            -> object_store_bucket_missing (and it is not created)
I5  a closed port                         -> object_store_unreachable
I6  `validate_nvidia_environment` on this real runner with the REAL store: the object_store probe
    passes or fails exactly as above, and the result is BLOCKED whatever the store says, because
    this runner has no NVIDIA GPU (never SUPPORTED, Law 17).
I7  the worker entry `python -m workers.training validate-environment` as a real process against
    the real store: exit 3, object_store `ok` with the right identity and `object_store_denied`
    with a wrong secret, which is never printed.

Run: `uv run python3 tests/integration/test_ftf_0705_object_store_s3.py`
"""

# ruff: noqa: E501
from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "apps" / "api"))

from workers.training import nvidia_environment_validation as nv  # noqa: E402
from workers.training import object_store_probe as osp  # noqa: E402

from app.services.storage_semantics_client import (  # noqa: E402
    S3Client,
    S3Credentials,
    S3Endpoint,
    S3Error,
)

CID = "FTF-0705"
COMPOSE_FILE = "infra/docker/docker-compose.yml"
HOST, PORT = "localhost", 8333
ACCESS_KEY = "sovra-local-test-access-key"
SECRET_KEY = "sovra_local_dev_only_secret_key_1234"
FAILURES: list[str] = []
CHECKS = [0]


def check(tag: str, description: str, condition: object) -> None:
    CHECKS[0] += 1
    if not condition:
        FAILURES.append(f"FAIL [{CID}/{tag}] {description}")


def compose(*args: str, check_exit: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["docker", "compose", "-f", COMPOSE_FILE, *args],
        cwd=ROOT,
        check=check_exit,
        capture_output=True,
        text=True,
    )


def _reachable() -> bool:
    try:
        with socket.create_connection((HOST, PORT), timeout=2.0):
            return True
    except OSError:
        return False


def _wait_ready(timeout: float) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            urllib.request.urlopen(f"http://{HOST}:{PORT}/", timeout=3.0)  # noqa: S310
            return
        except urllib.error.HTTPError:
            return
        except OSError:
            time.sleep(1.0)
    raise TimeoutError("SeaweedFS S3 never answered")


def env_for(
    bucket: str, *, access: str = ACCESS_KEY, secret: str = SECRET_KEY, port: int = PORT
) -> dict[str, str]:
    return {
        "SOVRA_STORAGE_S3_SCHEME": "http",
        "SOVRA_STORAGE_S3_HOST": HOST,
        "SOVRA_STORAGE_S3_PORT": str(port),
        "SOVRA_STORAGE_S3_REGION": "us-east-1",
        "SOVRA_STORAGE_S3_ACCESS_KEY": access,
        "SOVRA_STORAGE_S3_SECRET_KEY": secret,
        "SOVRA_TRAINING_OBJECT_STORE_BUCKET": bucket,
    }


def code_of(probe: osp.S3RoundTripProbe | None, key: str) -> str:
    assert probe is not None
    try:
        probe.round_trip(key, b"sovra ftf-0705 probe " + key.encode())
    except nv.ObjectStoreFailure as exc:
        return exc.code
    return "ok"


def _closed_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def main() -> None:
    booted = False
    if not _reachable():
        if shutil.which("docker") is None:
            print(
                "BLOCKED_EXTERNAL: no S3 fixture reachable and no docker to boot it; this proof does not skip.",
                file=sys.stderr,
            )
            sys.exit(1)
        print("S3 fixture not reachable -- booting it via docker compose", flush=True)
        compose("up", "-d", "s3")
        booted = True
    _wait_ready(90.0)
    admin = S3Client(S3Endpoint("http", HOST, PORT), S3Credentials(ACCESS_KEY, SECRET_KEY))
    bucket = f"ftf0705-{uuid.uuid4().hex[:12]}"
    for attempt in range(30):
        try:
            admin.create_bucket(bucket)
            break
        except (S3Error, OSError):
            if attempt == 29:
                raise
            time.sleep(2.0)

    # I1
    good = osp.probe_from_environment(env_for(bucket))
    key = f"sovra-env-probe/{uuid.uuid4().hex}"
    check("I1", "right identity round-trips", code_of(good, key) == "ok")
    try:
        admin.head_object(bucket, key)
        check("I1", "probe object deleted (independent HEAD)", False)
    except S3Error as exc:
        check("I1", "probe object deleted (independent HEAD 404)", exc.status_code == 404)
    status, _, body = admin._request(
        "GET", bucket, query={"list-type": "2", "prefix": "sovra-env-probe/"}
    )
    check("I1", "bucket lists no probe key", status == 200 and b"<Key>" not in body)

    # I2, I3
    check(
        "I2",
        "wrong secret -> denied",
        code_of(
            osp.probe_from_environment(env_for(bucket, secret="wrong-secret-ftf0705")),
            f"k-{uuid.uuid4().hex}",
        )
        == "object_store_denied",
    )
    check(
        "I3",
        "unknown access key -> denied",
        code_of(
            osp.probe_from_environment(env_for(bucket, access="nobody-ftf0705")),
            f"k-{uuid.uuid4().hex}",
        )
        == "object_store_denied",
    )

    # I4
    missing = f"ftf0705-missing-{uuid.uuid4().hex[:12]}"
    check(
        "I4",
        "missing bucket -> bucket_missing",
        code_of(osp.probe_from_environment(env_for(missing)), f"k-{uuid.uuid4().hex}")
        == "object_store_bucket_missing",
    )
    try:
        admin._request("HEAD", missing)
        check("I4", "missing bucket not created by the probe", False)
    except S3Error as exc:
        check("I4", "missing bucket not created by the probe", exc.status_code == 404)

    # I5
    check(
        "I5",
        "closed port -> unreachable",
        code_of(osp.probe_from_environment(env_for(bucket, port=_closed_port())), "k")
        == "object_store_unreachable",
    )

    # I6 the real validator on this real runner
    no_gpu = shutil.which("nvidia-smi") is None
    with tempfile.TemporaryDirectory() as tmp:
        for tag, env, want in (
            ("I6 good", env_for(bucket), "ok"),
            ("I6 denied", env_for(bucket, secret="wrong-secret-ftf0705"), "object_store_denied"),
            ("I6 missing", env_for(missing), "object_store_bucket_missing"),
        ):
            result = nv.validate_nvidia_environment(
                nv.EnvironmentRequirements(
                    min_gpu_vram_gb=8,
                    require_bf16=True,
                    storage_path=tmp,
                    min_free_storage_bytes=1,
                    object_store=osp.probe_from_environment(env),
                )
            )
            obj = next(p for p in result.probes if p.probe == "object_store")
            check(tag, f"object_store probe {want} (got {obj.code})", obj.code == want)
            if no_gpu:
                check(
                    tag,
                    "BLOCKED on a runner without a GPU",
                    isinstance(result, nv.BlockedEnvironment)
                    and result.failed_probe == "nvidia_smi",
                )
            if want != "ok":
                check(
                    tag,
                    "never SUPPORTED with no object-store access",
                    isinstance(result, nv.BlockedEnvironment)
                    and "object_store" in result.failed_probes,
                )

        # I7 the worker entry as a real process
        argv = [
            sys.executable,
            "-m",
            "workers.training",
            "validate-environment",
            "--min-gpu-vram-gb",
            "8",
            "--precision",
            "bf16",
            "--min-free-storage-gb",
            "1",
        ]
        for tag, secret, want in (
            ("I7 good", SECRET_KEY, "ok"),
            ("I7 denied", "wrong-secret-ftf0705-canary", "object_store_denied"),
        ):
            env = {**os.environ, **env_for(bucket, secret=secret), "SOVRA_TRAINING_WORK_DIR": tmp}
            done = subprocess.run(
                argv, cwd=ROOT, env=env, capture_output=True, text=True, timeout=300, check=False
            )
            doc = json.loads(done.stdout)
            entry_obj = next(p for p in doc["probes"] if p["probe"] == "object_store")
            check(
                tag,
                f"entry object_store {want} (got {entry_obj['code']})",
                entry_obj["code"] == want,
            )
            if no_gpu:
                check(
                    tag, "entry exit 3 BLOCKED", done.returncode == 3 and doc["status"] == "BLOCKED"
                )
            check(
                tag, "secret never printed", secret not in done.stdout and secret not in done.stderr
            )

    if booted and FAILURES:
        print(compose("logs", "--no-color", "s3", check_exit=False).stdout[-4000:])
    if FAILURES:
        for failure in FAILURES:
            print(failure, file=sys.stderr)
        print(f"{len(FAILURES)} failure(s) of {CHECKS[0]} checks", file=sys.stderr)
        sys.exit(1)
    print(f"OK {CID} object store (real SeaweedFS): {CHECKS[0]} checks")


if __name__ == "__main__":
    main()
