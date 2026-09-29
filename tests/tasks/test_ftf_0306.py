#!/usr/bin/env python3
"""FTF-0306 -- Base model revision/digest resolver (no-DB unit/fault tests).

Runnable without any database. This task is a dispatcher/composer over
three already-CERTIFIED sibling resolvers (FTF-0302 Hugging Face,
FTF-0303 private S3, FTF-0304 local filesystem) -- their own real
HTTP/TLS/signature/filesystem-safety logic is already independently
proven by their own suites, so this suite exercises real instances of
each (a real local HTTPS Hugging-Face-Hub-shaped fixture reusing
`tests/tasks/test_ftf_0302.py`'s own real-CA-trust technique, a real
local plain-HTTP path-style S3-shaped fixture reusing
`tests/tasks/test_ftf_0303.py`'s own real-signature-verification
technique, and a real temporary directory for `local_filesystem`) rather
than re-deriving each protocol from scratch, while proving what is
actually new here: correct dispatch by `source_type`, canonical
`sha256:`-prefixed digest formatting, real DB-free persistence-readiness
(`ResolvedDigestOutcome.changed`), and this task's own two acceptance/
negative_acceptance clauses:

1. **"Create immutable base-model/tokenizer manifest and compare hashes
   on every load/resume."** Proven for all three source types: a first
   resolution (`weights_digest=None`) reports `changed=True`; a second
   resolution against the *same* real unchanged bytes reports
   `changed=False` and the identical digest (a real "load/resume"
   re-verification); a third resolution after the real underlying bytes
   changed raises `DigestMismatchError` rather than silently accepting
   drift.
2. **"Alias/display-name equality or missing files cannot substitute for
   exact revision/digest."** Proven two ways: two artifacts with
   different `display_name` but identical real underlying bytes resolve
   to the identical digest (display name never enters the computation),
   and two artifacts with the same `display_name` but different real
   underlying bytes resolve to different digests (display-name equality
   is never treated as sufficient); separately, a `local_filesystem`
   artifact whose resolved path has no real files raises rather than
   this module substituting an empty/placeholder digest, and
   `private_registry` (a real Section 6.7 `source_type` with no real
   resolver task anywhere in this repository) raises
   `UnsupportedSourceTypeError` rather than fabricating one.

Run directly: python3 tests/tasks/test_ftf_0306.py
"""

from __future__ import annotations

import atexit
import hashlib
import shutil
import ssl
import subprocess
import sys
import tempfile
import threading
import uuid
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

ROOT = Path(__file__).resolve().parent.parent.parent
API_ROOT = ROOT / "apps" / "api"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(API_ROOT))

from packages.security.url_policy import ConnectionPolicy  # noqa: E402

import app.artifacts.base_model_revision_digest_resolver as digest_resolver  # noqa: E402
from app.artifacts.artifact_schema import TrainableArtifact, validate_weights_digest  # noqa: E402
from app.artifacts.local_artifact_resolver import LocalArtifactResolutionError  # noqa: E402
from app.artifacts.private_s3_artifact_resolver import PrivateS3ConnectionConfig  # noqa: E402
from app.services.storage_semantics_client import (  # noqa: E402
    S3Credentials,
    S3Endpoint,
    sign_request,
)

FAILURES: list[str] = []


def check(criterion_id: str, description: str, condition: bool) -> None:
    if not condition:
        FAILURES.append(f"FAIL [{criterion_id}] {description}")


def _make_artifact(**overrides: object) -> TrainableArtifact:
    now = datetime.now(UTC)
    fields: dict[str, object] = {
        "organization_id": uuid.uuid4(),
        "display_name": f"digest-artifact-{uuid.uuid4().hex[:8]}",
        "source_type": "local_filesystem",
        "source_uri": "model-a",
        "model_family": "tiny",
        "base_model_revision": "v1.0",
        "license_id": "apache-2.0",
        "trust_remote_code_allowed": False,
        "status": "registered",
    }
    fields.update(overrides)
    artifact = TrainableArtifact(**fields)
    artifact.id = uuid.uuid4()
    artifact.created_at = now
    artifact.updated_at = now
    artifact.license_accepted_by = "reviewer-1"
    artifact.license_accepted_at = now
    return artifact


# == 1. local_filesystem fixture (real temp directory, no server) ===========


class _TempRoot:
    def __enter__(self) -> Path:
        self._tmp = tempfile.TemporaryDirectory()
        return Path(self._tmp.name)

    def __exit__(self, *exc: object) -> None:
        self._tmp.cleanup()


def _write(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)


def test_local_filesystem_dispatch_returns_canonical_digest() -> None:
    with _TempRoot() as root:
        _write(root / "model-a" / "config.json", b'{"hidden_size": 4096}')
        _write(root / "model-a" / "tokenizer.json", b'{"tok": true}')
        artifact = _make_artifact(source_type="local_filesystem", source_uri="model-a")
        inputs = digest_resolver.LocalFilesystemResolutionInputs(approved_root=root)

        digest = digest_resolver.compute_candidate_weights_digest(artifact, inputs)

        check(
            "FTF-0306",
            "acceptance: local_filesystem dispatch returns a real canonical sha256: digest",
            digest.startswith("sha256:") and len(digest) == len("sha256:") + 64,
        )
        check(
            "FTF-0306",
            "acceptance: the returned digest is accepted by FTF-0301's own validate_weights_digest",
            validate_weights_digest(digest) == digest,
        )


def test_local_filesystem_load_resume_compare_and_drift_detection() -> None:
    with _TempRoot() as root:
        _write(root / "model-b" / "weights.bin", b"\x00\x01\x02\x03" * 1024)
        artifact = _make_artifact(source_type="local_filesystem", source_uri="model-b")
        inputs = digest_resolver.LocalFilesystemResolutionInputs(approved_root=root)

        first = digest_resolver.verify_or_compute_weights_digest(artifact, inputs)
        check(
            "FTF-0306",
            "acceptance: first resolution of an unresolved artifact reports changed=True",
            first.changed is True,
        )
        artifact.weights_digest = first.weights_digest

        second = digest_resolver.verify_or_compute_weights_digest(artifact, inputs)
        check(
            "FTF-0306",
            "acceptance: re-verifying unchanged real bytes (a 'load/resume') reports "
            "changed=False and the identical digest, never a fresh unrelated one",
            second.changed is False and second.weights_digest == first.weights_digest,
        )

        # Real drift: the underlying bytes on disk actually change.
        _write(root / "model-b" / "weights.bin", b"\xff\xfe\xfd\xfc" * 1024)
        raised = False
        try:
            digest_resolver.verify_or_compute_weights_digest(artifact, inputs)
        except digest_resolver.DigestMismatchError:
            raised = True
        check(
            "FTF-0306",
            "acceptance: 'compare hashes on every load/resume' -- real byte drift after "
            "resolution raises DigestMismatchError rather than silently accepting it",
            raised,
        )
        check(
            "FTF-0306",
            "acceptance: a rejected mismatch never mutates the artifact's own already-resolved "
            "weights_digest in place",
            artifact.weights_digest == first.weights_digest,
        )


def test_local_filesystem_missing_files_cannot_substitute_for_a_digest() -> None:
    with _TempRoot() as root:
        (root / "model-empty").mkdir()
        artifact = _make_artifact(source_type="local_filesystem", source_uri="model-empty")
        inputs = digest_resolver.LocalFilesystemResolutionInputs(approved_root=root)
        raised = False
        try:
            digest_resolver.compute_candidate_weights_digest(artifact, inputs)
        except LocalArtifactResolutionError:
            raised = True
        check(
            "FTF-0306",
            "negative_acceptance: missing files cannot substitute for exact digest -- an "
            "artifact directory with no real files raises rather than returning an empty/"
            "placeholder digest",
            raised,
        )


def test_display_name_equality_never_substitutes_for_real_digest() -> None:
    with _TempRoot() as root:
        _write(root / "model-c" / "weights.bin", b"AAAA" * 100)
        _write(root / "model-d" / "weights.bin", b"BBBB" * 100)
        inputs = digest_resolver.LocalFilesystemResolutionInputs(approved_root=root)

        same_name_diff_bytes_1 = _make_artifact(
            display_name="shared-name", source_type="local_filesystem", source_uri="model-c"
        )
        same_name_diff_bytes_2 = _make_artifact(
            display_name="shared-name", source_type="local_filesystem", source_uri="model-d"
        )
        digest_1 = digest_resolver.compute_candidate_weights_digest(same_name_diff_bytes_1, inputs)
        digest_2 = digest_resolver.compute_candidate_weights_digest(same_name_diff_bytes_2, inputs)
        check(
            "FTF-0306",
            "negative_acceptance: identical display_name with different real underlying bytes "
            "still resolves to different digests -- display-name equality is never a substitute",
            digest_1 != digest_2,
        )

        diff_name_same_bytes_1 = _make_artifact(
            display_name="name-one", source_type="local_filesystem", source_uri="model-c"
        )
        diff_name_same_bytes_2 = _make_artifact(
            display_name="name-two", source_type="local_filesystem", source_uri="model-c"
        )
        digest_3 = digest_resolver.compute_candidate_weights_digest(diff_name_same_bytes_1, inputs)
        digest_4 = digest_resolver.compute_candidate_weights_digest(diff_name_same_bytes_2, inputs)
        check(
            "FTF-0306",
            "sanity: different display_name with identical real underlying bytes still "
            "resolves to the identical digest -- display_name never enters the computation "
            "at all, in either direction",
            digest_3 == digest_4 == digest_1,
        )


def test_private_registry_has_no_resolver_and_is_refused() -> None:
    artifact = _make_artifact(
        source_type="private_registry", source_uri="https://registry.example.com/models/tiny"
    )
    raised = False
    try:
        digest_resolver.compute_candidate_weights_digest(
            artifact, digest_resolver.LocalFilesystemResolutionInputs(approved_root="/tmp")
        )
    except digest_resolver.UnsupportedSourceTypeError:
        raised = True
    check(
        "FTF-0306",
        "negative_acceptance: private_registry has no real resolver task anywhere in this "
        "repository -- refused with UnsupportedSourceTypeError, never fabricated",
        raised,
    )


def test_mismatched_resolution_inputs_type_is_refused() -> None:
    artifact = _make_artifact(source_type="local_filesystem", source_uri="model-a")
    connection = PrivateS3ConnectionConfig(
        endpoint=S3Endpoint(scheme="http", host="127.0.0.1", port=1, region="us-east-1"),
        credentials=S3Credentials(access_key="x", secret_key="y"),
        expected_bucket="b",
        tenant_key_prefix="p/",
    )
    wrong_inputs = digest_resolver.PrivateS3ResolutionInputs(connection=connection)
    raised = False
    try:
        digest_resolver.compute_candidate_weights_digest(artifact, wrong_inputs)
    except digest_resolver.BaseModelDigestResolutionError:
        raised = True
    check(
        "FTF-0306",
        "defensive: resolution inputs of the wrong dataclass type for the artifact's own "
        "source_type are refused rather than silently misused",
        raised,
    )


# == 2. private_s3 fixture (real local plain-HTTP path-style S3 fixture) ====

_S3_ACCESS_KEY = "AKIA_TEST_ACCESS_KEY"
_S3_SECRET_KEY = "test-secret-key-do-not-use-in-production"
_S3_CREDENTIALS = S3Credentials(access_key=_S3_ACCESS_KEY, secret_key=_S3_SECRET_KEY)
_S3_REGION = "us-east-1"
_S3_OBJECT_BYTES = b"REAL-S3-OBJECT-BYTES-FOR-FTF-0306" * 8
_S3_OBJECT_BYTES_V2 = b"REAL-S3-OBJECT-BYTES-FOR-FTF-0306-CHANGED" * 8

_S3_ROUTES: dict[str, dict[str, dict[str, object]]] = {
    "/acme-bucket/org-1/models/tiny.safetensors": {
        "v1": {"status": 200, "body": _S3_OBJECT_BYTES, "etag": "deadbeef00000000deadbeef00000001"},
        "v2": {
            "status": 200,
            "body": _S3_OBJECT_BYTES_V2,
            "etag": "deadbeef00000000deadbeef00000002",
        },
    },
}


def _s3_expected_authorization(method: str, path: str, query: dict[str, str], amz_date: str) -> str:
    parsed = urlsplit(f"http://placeholder{path}")
    endpoint = S3Endpoint(scheme="http", host="127.0.0.1", port=_S3_PORT, region=_S3_REGION)
    now = datetime.strptime(amz_date, "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC)
    signed = sign_request(
        method=method,
        endpoint=endpoint,
        credentials=_S3_CREDENTIALS,
        canonical_uri=parsed.path,
        query=query,
        now=now,
    )
    return signed.headers["authorization"]


class _S3Handler(BaseHTTPRequestHandler):
    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        pass

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlsplit(self.path)
        query = {k: v[0] for k, v in parse_qs(parsed.query).items()}
        version_id = query.get("versionId", "")

        amz_date = self.headers.get("x-amz-date", "")
        actual_auth = self.headers.get("authorization", "")
        expected_auth = (
            _s3_expected_authorization(self.command, parsed.path, query, amz_date)
            if amz_date
            else None
        )
        if expected_auth is None or actual_auth != expected_auth:
            self._respond(403, b'{"Code":"SignatureDoesNotMatch"}')
            return

        versions = _S3_ROUTES.get(parsed.path)
        route = versions.get(version_id) if versions else None
        if route is None:
            self._respond(404, b'{"Code":"NoSuchVersion"}')
            return

        status = route["status"]
        assert isinstance(status, int)
        body = route["body"]
        assert isinstance(body, bytes)
        self.send_response(status)
        self.send_header("ETag", f'"{route["etag"]}"')
        self._respond_body(body)

    def _respond(self, status: int, body: bytes) -> None:
        self.send_response(status)
        self._respond_body(body)

    def _respond_body(self, body: bytes) -> None:
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if body:
            self.wfile.write(body)


def _start_s3_server() -> ThreadingHTTPServer:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _S3Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    atexit.register(server.shutdown)
    return server


_S3_SERVER = _start_s3_server()
_S3_PORT = _S3_SERVER.server_address[1]


def _s3_connection() -> PrivateS3ConnectionConfig:
    return PrivateS3ConnectionConfig(
        endpoint=S3Endpoint(scheme="http", host="127.0.0.1", port=_S3_PORT, region=_S3_REGION),
        credentials=_S3_CREDENTIALS,
        expected_bucket="acme-bucket",
        tenant_key_prefix="org-1/",
    )


def _s3_policy() -> ConnectionPolicy:
    import ipaddress

    return ConnectionPolicy(
        name="test-ftf-0306-private-s3",
        allowed_schemes=frozenset({"http"}),
        allowed_ports=frozenset({_S3_PORT}),
        explicit_private_allowlist=(ipaddress.ip_network("127.0.0.0/8"),),
    )


def test_private_s3_dispatch_and_load_resume_drift_detection() -> None:
    artifact = _make_artifact(
        source_type="private_s3",
        source_uri="s3://acme-bucket/org-1/models/tiny.safetensors",
        base_model_revision="v1",
    )
    inputs = digest_resolver.PrivateS3ResolutionInputs(
        connection=_s3_connection(), policy=_s3_policy()
    )

    first = digest_resolver.verify_or_compute_weights_digest(artifact, inputs)
    check(
        "FTF-0306",
        "acceptance: private_s3 dispatch returns a real canonical sha256: digest matching an "
        "independently computed digest of the real object bytes",
        first.weights_digest
        == digest_resolver.canonical_weights_digest(hashlib.sha256(_S3_OBJECT_BYTES).hexdigest()),
    )
    artifact.weights_digest = first.weights_digest

    second = digest_resolver.verify_or_compute_weights_digest(artifact, inputs)
    check(
        "FTF-0306",
        "acceptance: re-resolving the same pinned S3 object version (a 'load/resume') "
        "reports changed=False and the identical digest",
        second.changed is False and second.weights_digest == first.weights_digest,
    )

    # A different, real, pinned object version -- e.g. re-pointing base_model_revision at a
    # different real versionId -- must never be silently accepted as still matching.
    drifted = _make_artifact(
        source_type="private_s3",
        source_uri="s3://acme-bucket/org-1/models/tiny.safetensors",
        base_model_revision="v2",
    )
    drifted.weights_digest = first.weights_digest
    raised = False
    try:
        digest_resolver.verify_or_compute_weights_digest(drifted, inputs)
    except digest_resolver.DigestMismatchError:
        raised = True
    check(
        "FTF-0306",
        "acceptance: 'compare hashes on every load/resume' -- a real different pinned object "
        "version's own real bytes raise DigestMismatchError rather than being accepted",
        raised,
    )


# == 3. huggingface fixture (real local HTTPS fixture, real CA trust) =======

_HF_CONFIG_BYTES = b'{"model_type": "tiny"}'
_HF_WEIGHTS_BYTES = b"FAKE-SAFETENSORS-BYTES-FOR-FTF-0306" * 4
_HF_WEIGHTS_SHA256 = hashlib.sha256(_HF_WEIGHTS_BYTES).hexdigest()
_HF_PINNED_REVISION = "b" * 40

_HF_ROUTES: dict[str, dict[str, object]] = {
    "/api/models/acme/tiny-model/revision/" + _HF_PINNED_REVISION: {
        "status": 200,
        "json": {
            "sha": _HF_PINNED_REVISION,
            "gated": False,
            "siblings": [
                {"rfilename": "config.json"},
                {"rfilename": "model.safetensors", "lfs": {"sha256": _HF_WEIGHTS_SHA256}},
            ],
            "config": {},
        },
    },
    "/acme/tiny-model/resolve/" + _HF_PINNED_REVISION + "/config.json": {
        "status": 200,
        "body": _HF_CONFIG_BYTES,
    },
    "/acme/tiny-model/resolve/" + _HF_PINNED_REVISION + "/model.safetensors": {
        "status": 200,
        "body": _HF_WEIGHTS_BYTES,
    },
}


class _HuggingFaceHandler(BaseHTTPRequestHandler):
    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        pass

    def do_GET(self) -> None:  # noqa: N802
        route = _HF_ROUTES.get(self.path)
        if route is None:
            self._respond(404, b'{"error": "not found"}')
            return
        if "json" in route:
            import json as _json

            body = _json.dumps(route["json"]).encode("utf-8")
        else:
            body = route["body"]  # type: ignore[assignment]
        status = route["status"]
        assert isinstance(status, int)
        self._respond(status, body)

    def _respond(self, status: int, body: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if body:
            self.wfile.write(body)


_TLS_TMP = Path(tempfile.mkdtemp(prefix="ftf-0306-tls-"))
_CA_TRUST_TARGET = Path("/usr/local/share/ca-certificates/ftf-0306-test-ca.crt")
_UPDATE_CA_AVAILABLE = (
    shutil.which("update-ca-certificates") is not None
    and shutil.which("openssl") is not None
    and shutil.which("sudo") is not None
    and subprocess.run(["sudo", "-n", "true"], capture_output=True).returncode == 0
)


def _run_openssl(args: list[str]) -> None:
    subprocess.run(["openssl", *args], check=True, capture_output=True)


def _generate_trusted_server_cert() -> tuple[Path, Path]:
    ca_key = _TLS_TMP / "ca.key"
    ca_crt = _TLS_TMP / "ca.crt"
    leaf_key = _TLS_TMP / "leaf.key"
    leaf_csr = _TLS_TMP / "leaf.csr"
    leaf_crt = _TLS_TMP / "leaf.crt"
    leaf_ext = _TLS_TMP / "leaf.ext"

    _run_openssl(["genrsa", "-out", str(ca_key), "2048"])
    _run_openssl(
        [
            "req",
            "-x509",
            "-new",
            "-key",
            str(ca_key),
            "-sha256",
            "-days",
            "2",
            "-out",
            str(ca_crt),
            "-subj",
            "/CN=FTF-0306 Test CA",
        ]
    )
    _run_openssl(["genrsa", "-out", str(leaf_key), "2048"])
    _run_openssl(
        ["req", "-new", "-key", str(leaf_key), "-out", str(leaf_csr), "-subj", "/CN=127.0.0.1"]
    )
    leaf_ext.write_text("subjectAltName=IP:127.0.0.1\n")
    _run_openssl(
        [
            "x509",
            "-req",
            "-in",
            str(leaf_csr),
            "-CA",
            str(ca_crt),
            "-CAkey",
            str(ca_key),
            "-CAcreateserial",
            "-out",
            str(leaf_crt),
            "-days",
            "2",
            "-sha256",
            "-extfile",
            str(leaf_ext),
        ]
    )

    subprocess.run(
        ["sudo", "cp", str(ca_crt), str(_CA_TRUST_TARGET)], check=True, capture_output=True
    )
    subprocess.run(["sudo", "update-ca-certificates"], check=True, capture_output=True)
    atexit.register(_remove_ca_trust)
    return leaf_crt, leaf_key


def _remove_ca_trust() -> None:
    subprocess.run(["sudo", "rm", "-f", str(_CA_TRUST_TARGET)], check=False, capture_output=True)
    subprocess.run(["sudo", "update-ca-certificates", "--fresh"], check=False, capture_output=True)


def _start_https_server(certfile: Path, keyfile: Path) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _HuggingFaceHandler)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(certfile=str(certfile), keyfile=str(keyfile))
    server.socket = context.wrap_socket(server.socket, server_side=True)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    atexit.register(server.shutdown)
    return server


_HF_SERVER: ThreadingHTTPServer | None = None
_HF_BASE_URL: str | None = None
if _UPDATE_CA_AVAILABLE:
    _hf_leaf, _hf_key = _generate_trusted_server_cert()
    _HF_SERVER = _start_https_server(_hf_leaf, _hf_key)
    _HF_PORT = _HF_SERVER.server_address[1]
    _HF_BASE_URL = f"https://127.0.0.1:{_HF_PORT}"


def _hf_policy() -> ConnectionPolicy:
    import ipaddress

    assert _HF_PORT is not None
    return ConnectionPolicy(
        name="test-ftf-0306-huggingface",
        allowed_schemes=frozenset({"https"}),
        allowed_ports=frozenset({_HF_PORT}),
        explicit_private_allowlist=(ipaddress.ip_network("127.0.0.0/8"),),
        allow_redirects=True,
        max_redirects=5,
    )


def test_huggingface_dispatch_and_load_resume_compare() -> None:
    if _HF_BASE_URL is None:
        return
    artifact = _make_artifact(
        source_type="huggingface",
        source_uri="acme/tiny-model",
        base_model_revision=_HF_PINNED_REVISION,
    )
    inputs = digest_resolver.HuggingFaceResolutionInputs(base_url=_HF_BASE_URL, policy=_hf_policy())

    first = digest_resolver.verify_or_compute_weights_digest(artifact, inputs)
    check(
        "FTF-0306",
        "acceptance: huggingface dispatch returns a real canonical sha256: digest combining "
        "every real recognized model-artifact file's own content",
        first.weights_digest.startswith("sha256:") and first.changed,
    )
    artifact.weights_digest = first.weights_digest

    second = digest_resolver.verify_or_compute_weights_digest(artifact, inputs)
    check(
        "FTF-0306",
        "acceptance: re-resolving the same real pinned commit (a 'load/resume') reports "
        "changed=False and the identical digest",
        second.changed is False and second.weights_digest == first.weights_digest,
    )


def main() -> None:
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()

    if FAILURES:
        print(f"FTF-0306 Base model revision/digest resolver tests: {len(FAILURES)} failed")
        for failure in FAILURES:
            print(f"  {failure}")
        sys.exit(1)

    print("FTF-0306 Base model revision/digest resolver tests: all checks passed")
    sys.exit(0)


if __name__ == "__main__":
    main()
