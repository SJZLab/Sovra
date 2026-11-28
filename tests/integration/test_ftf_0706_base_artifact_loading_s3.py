#!/usr/bin/env python3
"""FTF-0706 -- base artifact loading against the live SeaweedFS S3 API.

Real path, no mocks: `infra/docker/docker-compose.yml`'s `s3` service (SeaweedFS, real SigV4
identity), a real versioned bucket, a small real base artifact built here (real `tokenizers`
tokenizer, real config files, a real safetensors file), uploaded file by file with the versionIds
the server really returned, then its manifest. FTF-0306 `verify_or_compute_weights_digest` computes
the approved `weights_digest` from the live manifest object, as the registry does, and FTF-0701
resolves the job's config against that record. `prepare_training_job` (the worker stage) then runs
with the default `guarded_fetch` transport -- every request goes over the network to SeaweedFS.

Proves against the live server:
1. acceptance: the exact approved revision loads, every file's digest equals the approved one, the
   tokenizer encodes, and datasets are mounted only after the verified base files exist;
2. a newer, unapproved version of a file uploaded after approval is not what loads;
3. negatives, each refused before any dataset mount and leaving no job directory: an extra object
   under the artifact prefix, a custom code file and pickle weights in the manifest, a manifest
   pinning a deleted version, a file whose bytes differ from the manifest digest, a record whose
   digest no longer matches the live manifest;
4. fail closed: wrong credentials (a real 403) and an unreachable endpoint are `blocked`,
   `object_store_unavailable`, never loaded.

Run: uv run python3 tests/integration/test_ftf_0706_base_artifact_loading_s3.py
(boots the compose `s3` service if it is not already reachable).
"""

from __future__ import annotations

import hashlib
import http.client
import importlib.util
import ipaddress
import json
import logging
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "apps" / "api"))

import yaml  # noqa: E402
from packages.security.url_policy import ConnectionPolicy  # noqa: E402
from workers.training import base_artifact_loading as bal  # noqa: E402
from workers.training.training_job_preparation import prepare_training_job  # noqa: E402

from app.artifacts.base_model_revision_digest_resolver import (  # noqa: E402
    PrivateS3ResolutionInputs,
    verify_or_compute_weights_digest,
)
from app.artifacts.private_s3_artifact_resolver import PrivateS3ConnectionConfig  # noqa: E402
from app.services.storage_semantics_client import (  # noqa: E402
    S3Client,
    S3Credentials,
    S3Endpoint,
)

_spec = importlib.util.spec_from_file_location(
    "ftf0706_task", ROOT / "tests/tasks/test_ftf_0706.py"
)
assert _spec is not None and _spec.loader is not None
fx: Any = importlib.util.module_from_spec(_spec)
sys.modules["ftf0706_task"] = fx
_spec.loader.exec_module(fx)

COMPOSE_FILE = "infra/docker/docker-compose.yml"
PROFILE_PATH = ROOT / "infra" / "internal" / "storage-profile.yaml"
CID = "FTF-0706"
TENANT = "org-7"
FAILURES: list[str] = []


def check(description: str, condition: bool) -> None:
    print(("ok   " if condition else "FAIL ") + description, flush=True)
    if not condition:
        FAILURES.append(f"FAIL [{CID}] {description}")


def compose(*args: str, check_exit: bool = True) -> subprocess.CompletedProcess[str]:
    print("+ docker compose", " ".join(args), flush=True)
    return subprocess.run(
        ["docker", "compose", "-f", COMPOSE_FILE, *args],
        check=check_exit,
        capture_output=True,
        text=True,
    )


def _wait_for_tcp(host: str, port: int, timeout_seconds: float) -> None:
    deadline = time.monotonic() + timeout_seconds
    while True:
        try:
            with socket.create_connection((host, port), timeout=2.0):
                return
        except OSError:
            if time.monotonic() > deadline:
                raise TimeoutError(f"{host}:{port} not reachable") from None
            time.sleep(1.0)


def _wait_for_http(endpoint: S3Endpoint, timeout_seconds: float = 90.0) -> None:
    deadline = time.monotonic() + timeout_seconds
    while True:
        try:
            urllib.request.urlopen(f"{endpoint.base_url}/", timeout=3.0)  # noqa: S310
            return
        except urllib.error.HTTPError:
            return
        except (urllib.error.URLError, http.client.HTTPException, OSError):
            if time.monotonic() > deadline:
                raise TimeoutError("S3 gateway never answered") from None
            time.sleep(1.0)


def _endpoint_and_credentials() -> tuple[S3Endpoint, S3Credentials]:
    env = yaml.safe_load(PROFILE_PATH.read_text(encoding="utf-8"))["endpoint"]
    defaults = env["local_fixture_defaults"]

    def get(var_key: str, default_key: str) -> str:
        return os.environ.get(env[var_key], str(defaults[default_key]))

    endpoint = S3Endpoint(
        scheme=get("scheme_env_var", "scheme"),
        host=get("host_env_var", "host"),
        port=int(get("port_env_var", "port")),
        region=get("region_env_var", "region"),
    )
    credentials = S3Credentials(
        access_key=get("access_key_env_var", "access_key"),
        secret_key=get("secret_key_env_var", "secret_key"),
    )
    return endpoint, credentials


def _policy(port: int) -> ConnectionPolicy:
    return ConnectionPolicy(
        name="ftf-0706-base-artifact-loading-integration",
        allowed_schemes=frozenset({"http"}),
        allowed_ports=frozenset({port}),
        explicit_private_allowlist=(
            ipaddress.ip_network("127.0.0.0/8"),
            ipaddress.ip_network("::1/128"),
        ),
    )


class Live:
    def __init__(self, client: S3Client, endpoint: S3Endpoint, credentials: S3Credentials) -> None:
        self.client = client
        self.endpoint = endpoint
        self.credentials = credentials
        self.policy = _policy(endpoint.port)

    def connection(self, bucket: str, credentials: S3Credentials | None = None) -> Any:
        return PrivateS3ConnectionConfig(
            endpoint=self.endpoint,
            credentials=credentials or self.credentials,
            expected_bucket=bucket,
            tenant_key_prefix=TENANT,
        )

    def publish(
        self,
        files: dict[str, bytes],
        *,
        entry_over: dict[str, dict[str, Any]] | None = None,
        extra: dict[str, bytes] | None = None,
    ) -> tuple[str, str, Any, dict[str, str]]:
        """Uploads `files` to a fresh versioned bucket, then the manifest; returns (bucket,
        prefix, approved TrainableArtifact with its FTF-0306 digest, file versionIds)."""
        bucket = f"sovra-ftf0706-{uuid.uuid4().hex[:12]}"
        prefix = f"{TENANT}/models/tiny-{uuid.uuid4().hex[:6]}/"
        self.client.create_bucket(bucket)
        self.client.put_bucket_versioning(bucket, enabled=True)
        entries = []
        versions: dict[str, str] = {}
        for name, body in sorted(files.items()):
            version = self.client.put_object(bucket, prefix + name, body)["version_id"]
            versions[name] = version
            entry: dict[str, Any] = {
                "path": name,
                "version_id": version,
                "size_bytes": len(body),
                "sha256": hashlib.sha256(body).hexdigest(),
            }
            entry.update((entry_over or {}).get(name, {}))
            entries.append(entry)
        manifest = json.dumps({"schema": bal.MANIFEST_SCHEMA, "files": entries}).encode()
        manifest_version = self.client.put_object(bucket, prefix + bal.MANIFEST_NAME, manifest)[
            "version_id"
        ]
        for key, body in (extra or {}).items():
            self.client.put_object(bucket, prefix + key, body)
        artifact = fx.make_artifact(
            manifest_version,
            None,
            status="registered",
            source_uri=f"s3://{bucket}/{prefix}{bal.MANIFEST_NAME}",
        )
        outcome = verify_or_compute_weights_digest(
            artifact,
            PrivateS3ResolutionInputs(connection=self.connection(bucket), policy=self.policy),
        )
        artifact.weights_digest = outcome.weights_digest
        artifact.status = "ready"
        return bucket, prefix, artifact, versions

    def prepare(
        self, bucket: str, artifact: Any, *, connection: Any = None, config: Any = None
    ) -> Any:
        out = fx.Outcome()
        root = Path(tempfile.mkdtemp(prefix="ftf0706-live-"))
        cfg = config or fx.make_config(
            str(artifact.id), artifact.weights_digest[7:], artifact.base_model_revision
        )

        def mount_datasets(data_dir: Path) -> str:
            base = data_dir.parent / "base"
            out.mount_calls.append(
                {
                    "base_tokenizer_present": (base / "tokenizer.json").is_file(),
                    "base_weights_present": (base / "model.safetensors").is_file(),
                }
            )
            data_dir.mkdir(mode=0o700)
            return "mounted"

        try:
            out.prepared = prepare_training_job(
                cfg,
                artifact,
                connection=connection or self.connection(bucket),
                workspace_root=root,
                mount_datasets=mount_datasets,
                policy=self.policy,
            )
        except bal.BaseArtifactLoadError as exc:
            out.error = exc
        out.job_dir_exists_after = (root / cfg.config_sha256).exists()
        return out


def expect_refused(label: str, out: Any, code: str, state: str = "failed") -> None:
    err = out.error
    check(
        f"{label}: refused {code}/{state} (got {getattr(err, 'code', None)}/"
        f"{getattr(err, 'state', None)})",
        err is not None and err.code == code and err.state == state,
    )
    check(f"{label}: datasets never mounted", out.mount_calls == [])
    check(f"{label}: no job directory left", out.job_dir_exists_after is False)


def run_suite(live: Live) -> None:
    files = fx.base_files()
    bucket, prefix, artifact, _ = live.publish(files)
    out = live.prepare(bucket, artifact)
    check(
        f"acceptance: live load succeeds (error={getattr(out.error, 'code', None)})",
        out.error is None,
    )
    if out.prepared is not None:
        loaded = out.prepared.base
        check(
            "acceptance: exact approved revision and FTF-0306 digest",
            loaded.base_revision == artifact.base_model_revision
            and loaded.weights_digest == artifact.weights_digest,
        )
        check(
            "acceptance: every written file equals its approved sha256",
            all(
                hashlib.sha256((loaded.directory / f.path).read_bytes()).hexdigest() == f.sha256
                for f in loaded.files
            ),
        )
        check("acceptance: tokenizer encodes", loaded.tokenizer.encode("hello world").ids == [3, 4])
        check(
            "order: datasets mounted once, after the verified base files exist",
            out.mount_calls == [{"base_tokenizer_present": True, "base_weights_present": True}],
        )

    live.client.put_object(bucket, prefix + "tokenizer.json", fx.tokenizer_json({"[UNK]": 0}))
    out = live.prepare(bucket, artifact)
    check(
        "pinned version: a newer unapproved tokenizer.json version is not what loads",
        out.error is None and out.prepared.base.tokenizer.encode("hello").ids == [3],
    )

    b, _, art, _ = live.publish(files, extra={"notes.txt": b"unlisted"})
    expect_refused(
        "extra object under the prefix", live.prepare(b, art), "artifact_file_unexpected"
    )

    with_code = dict(files)
    with_code["modeling_custom.py"] = b"import os\n"
    b, _, art, _ = live.publish(with_code)
    expect_refused("custom code file", live.prepare(b, art), "artifact_file_unexpected")

    with_pickle = dict(files)
    with_pickle["pytorch_model.bin"] = b"\x80\x04pickle"
    b, _, art, _ = live.publish(with_pickle)
    expect_refused("pickle weights", live.prepare(b, art), "unsafe_weight_format")

    remote = dict(files)
    remote["config.json"] = fx.config_json(auto_map={"AutoModel": "x.Y"})
    b, _, art, _ = live.publish(remote)
    expect_refused("config auto_map", live.prepare(b, art), "remote_code_not_allowed")

    b, p, art, versions = live.publish(files)
    live.client.delete_object(b, p + "tokenizer.json", version_id=versions["tokenizer.json"])
    expect_refused(
        "pinned tokenizer.json version permanently deleted",
        live.prepare(b, art),
        "artifact_file_missing",
    )

    other = hashlib.sha256(b"something else").hexdigest()
    b, _, art, _ = live.publish(files, entry_over={"config.json": {"sha256": other}})
    expect_refused(
        "file bytes differ from the manifest", live.prepare(b, art), "artifact_file_digest_mismatch"
    )

    b, _, art, _ = live.publish(files)
    art.weights_digest = "sha256:" + "d" * 64
    cfg = fx.make_config(str(art.id), "d" * 64, art.base_model_revision)
    expect_refused(
        "record digest differs from the live manifest",
        live.prepare(b, art, config=cfg),
        "digest_mismatch",
    )

    b, _, art, _ = live.publish(files)
    wrong = live.connection(
        b, S3Credentials(access_key=live.credentials.access_key, secret_key="wrong")
    )
    expect_refused(
        "wrong credentials (real 403)",
        live.prepare(b, art, connection=wrong),
        "object_store_unavailable",
        "blocked",
    )

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        closed_port = s.getsockname()[1]
    dead = PrivateS3ConnectionConfig(
        endpoint=S3Endpoint(scheme="http", host="127.0.0.1", port=closed_port),
        credentials=live.credentials,
        expected_bucket=b,
        tenant_key_prefix=TENANT,
    )
    saved = live.policy
    live.policy = _policy(closed_port)
    expect_refused(
        "unreachable endpoint",
        live.prepare(b, art, connection=dead),
        "object_store_unavailable",
        "blocked",
    )
    live.policy = saved


def main() -> None:
    logging.disable(logging.CRITICAL)
    endpoint, credentials = _endpoint_and_credentials()
    booted_here = False
    try:
        _wait_for_tcp(endpoint.host, endpoint.port, 3.0)
    except TimeoutError:
        compose("up", "-d", "s3")
        booted_here = True
        _wait_for_tcp(endpoint.host, endpoint.port, 90.0)
    _wait_for_http(endpoint)
    live = Live(S3Client(endpoint, credentials), endpoint, credentials)
    try:
        run_suite(live)
    except Exception as exc:  # noqa: BLE001
        FAILURES.append(f"FAIL [{CID}] unexpected exception: {exc!r}")
    if FAILURES:
        print("\n".join(FAILURES))
        logs = compose("logs", "--no-color", "s3", check_exit=False)
        print(logs.stdout[-4000:])
    if booted_here:
        compose("stop", "s3", check_exit=False)
    if FAILURES:
        print(f"FTF-0706 live base artifact loading: {len(FAILURES)} failed")
        sys.exit(1)
    print("FTF-0706 live base artifact loading: all checks passed")


if __name__ == "__main__":
    main()
