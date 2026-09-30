#!/usr/bin/env python3
"""FTF-0302 -- Hugging Face artifact resolver (no-DB unit/fault tests).

Runnable without any database and without real egress to the public
internet -- this suite builds and drives a real local HTTPS server itself,
matching the no-DB/no-external-egress local-server pattern
`tests/tasks/test_ftf_0203.py`/`tests/tasks/test_ftf_0202.py` already
established for exactly this class of "real external system" requirement:
a real self-signed CA this suite generates and installs into the
sandbox's own system trust store for the duration of the run (removed
again in `atexit`), so every request this suite makes goes through
`packages/security/outbound_guard.py`'s own real, completely unmodified
`ssl.create_default_context()` certificate validation and real DNS/socket
connection path -- never a mocked `guarded_fetch`, never a relaxed TLS
context. The server implements the exact subset of the real Hugging Face
Hub REST API `hugging_face_artifact_resolver.py` calls (the
`/api/models/{repo}/revision/{revision}` revision-resolution endpoint and
the `/{repo}/resolve/{sha}/{filename}` file-download endpoint, including a
real HTTP redirect for one file exactly as Hugging Face's own LFS-backed
files real-world redirect to a CDN host) -- this sandboxed CI/dev
environment has no route to the public internet at all (confirmed
directly: a plain `curl` to `huggingface.co` from this sandbox fails at
the outbound proxy), so a real local double is this repository's own
established way to prove the real request/response/verification logic
without depending on a live third-party service's availability inside an
automated gate (the same reasoning already applied to FTF-0202's real
local OpenBao and FTF-0101's real local `idpy-oidc` instead of a live
cloud secret manager or live Google/Okta OIDC provider). The client code
under test makes no assumption beyond the documented public API shape
(endpoint paths, JSON field names) that would not hold against the real
service.

Covers this task's own negative_acceptance ("Floating revision,
unavailable license or remote code execution is blocked/explicitly
reviewed; no sensitive training data present during acquisition") as four
independently-triggered real checks, plus real file-digest verification
against a genuine sha256 mismatch, a real destination-denied policy
check, and two disclosed fault injections (Law 1) proving the
digest-mismatch and floating-revision checks are load-bearing.

Run directly: python3 tests/tasks/test_ftf_0302.py
"""

from __future__ import annotations

import atexit
import hashlib
import importlib
import ipaddress
import shutil
import subprocess
import sys
import tempfile
import threading
import uuid
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
API_ROOT = ROOT / "apps" / "api"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(API_ROOT))

from packages.security.url_policy import ConnectionPolicy, OutboundPolicyError  # noqa: E402

import app.artifacts.hugging_face_artifact_resolver as hf_resolver  # noqa: E402
from app.artifacts.artifact_schema import TrainableArtifact  # noqa: E402
from app.artifacts.license_acceptance_workflow import LicenseNotAcceptedError  # noqa: E402

FAILURES: list[str] = []


def check(criterion_id: str, description: str, condition: bool) -> None:
    if not condition:
        FAILURES.append(f"FAIL [{criterion_id}] {description}")


def _make_artifact(*, license_accepted: bool = True, **overrides: object) -> TrainableArtifact:
    now = datetime.now(UTC)
    fields: dict[str, object] = {
        "organization_id": uuid.uuid4(),
        "display_name": f"hf-artifact-{uuid.uuid4().hex[:8]}",
        "source_type": "huggingface",
        "source_uri": "acme/tiny-model",
        "model_family": "tiny",
        "base_model_revision": "a" * 40,
        "license_id": "apache-2.0",
        "trust_remote_code_allowed": False,
        "status": "registered",
    }
    fields.update(overrides)
    artifact = TrainableArtifact(**fields)
    artifact.id = uuid.uuid4()
    artifact.created_at = now
    artifact.updated_at = now
    if license_accepted:
        artifact.license_accepted_by = "reviewer-1"
        artifact.license_accepted_at = now
    return artifact


# -- Real local HTTPS Hugging-Face-Hub-shaped fixture ------------------------

_CONFIG_BYTES = b'{"model_type": "tiny"}'
_TOKENIZER_BYTES = b'{"tok": true}'
_WEIGHTS_BYTES = b"FAKE-SAFETENSORS-BYTES-FOR-FTF-0302" * 4
_WEIGHTS_SHA256 = hashlib.sha256(_WEIGHTS_BYTES).hexdigest()
_WRONG_WEIGHTS_SHA256 = hashlib.sha256(b"different-bytes-entirely").hexdigest()

REQUEST_LOG: list[str] = []

_PINNED_REVISION = "a" * 40


def _revision_json(
    *,
    sha: str = _PINNED_REVISION,
    gated: bool = False,
    siblings: list[dict[str, object]] | None = None,
    config: dict[str, object] | None = None,
) -> dict[str, object]:
    return {
        "sha": sha,
        "gated": gated,
        "siblings": siblings if siblings is not None else [],
        "config": config or {},
    }


_ROUTES: dict[str, dict[str, object]] = {
    "/api/models/acme/tiny-model/revision/" + _PINNED_REVISION: {
        "status": 200,
        "json": _revision_json(
            siblings=[
                {"rfilename": "config.json"},
                {"rfilename": "tokenizer.json"},
                {"rfilename": "model.safetensors", "lfs": {"sha256": _WEIGHTS_SHA256}},
                {"rfilename": "data/train.parquet"},
                {"rfilename": "notes.rst"},
            ]
        ),
    },
    "/acme/tiny-model/resolve/" + _PINNED_REVISION + "/config.json": {
        "status": 200,
        "body": _CONFIG_BYTES,
    },
    "/acme/tiny-model/resolve/" + _PINNED_REVISION + "/tokenizer.json": {
        "status": 200,
        "body": _TOKENIZER_BYTES,
    },
    "/acme/tiny-model/resolve/" + _PINNED_REVISION + "/model.safetensors": {
        "status": 302,
        "location": "/cdn/tiny-model/model.safetensors",
    },
    "/cdn/tiny-model/model.safetensors": {
        "status": 200,
        "body": _WEIGHTS_BYTES,
    },
    "/api/models/acme/gated-model/revision/" + _PINNED_REVISION: {
        "status": 403,
        "json": {"error": "gated"},
    },
    "/api/models/acme/custom-code-model/revision/" + _PINNED_REVISION: {
        "status": 200,
        "json": _revision_json(
            siblings=[
                {"rfilename": "config.json"},
                {"rfilename": "model.safetensors", "lfs": {"sha256": _WEIGHTS_SHA256}},
                {"rfilename": "modeling_custom.py"},
            ],
            config={"auto_map": {"AutoModel": "modeling_custom.MyModel"}},
        ),
    },
    "/acme/custom-code-model/resolve/" + _PINNED_REVISION + "/config.json": {
        "status": 200,
        "body": _CONFIG_BYTES,
    },
    "/acme/custom-code-model/resolve/" + _PINNED_REVISION + "/model.safetensors": {
        "status": 200,
        "body": _WEIGHTS_BYTES,
    },
    "/api/models/acme/bad-digest-model/revision/" + _PINNED_REVISION: {
        "status": 200,
        "json": _revision_json(
            siblings=[
                {"rfilename": "model.safetensors", "lfs": {"sha256": _WRONG_WEIGHTS_SHA256}},
            ]
        ),
    },
    "/acme/bad-digest-model/resolve/" + _PINNED_REVISION + "/model.safetensors": {
        "status": 200,
        "body": _WEIGHTS_BYTES,
    },
    "/api/models/acme/dataset-only-model/revision/" + _PINNED_REVISION: {
        "status": 200,
        "json": _revision_json(
            siblings=[
                {"rfilename": "data/train.parquet"},
                {"rfilename": "notes.rst"},
            ]
        ),
    },
}


class _HuggingFaceHandler(BaseHTTPRequestHandler):
    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        pass

    def do_GET(self) -> None:  # noqa: N802
        REQUEST_LOG.append(self.path)
        route = _ROUTES.get(self.path)
        if route is None:
            self._respond(404, b'{"error": "not found"}')
            return
        status_value = route["status"]
        assert isinstance(status_value, int)
        status = status_value
        if status in (301, 302, 303, 307, 308):
            body = b""
            self.send_response(status)
            self.send_header("Location", str(route["location"]))
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if "json" in route:
            import json as _json

            body = _json.dumps(route["json"]).encode("utf-8")
        else:
            body = route["body"]  # type: ignore[assignment]
        self._respond(status, body)

    def _respond(self, status: int, body: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if body:
            self.wfile.write(body)


_TLS_TMP = Path(tempfile.mkdtemp(prefix="ftf-0302-tls-"))
_CA_TRUST_TARGET = Path("/usr/local/share/ca-certificates/ftf-0302-test-ca.crt")
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
            "/CN=FTF-0302 Test CA",
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


import ssl  # noqa: E402


def _start_https_server(certfile: Path, keyfile: Path) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _HuggingFaceHandler)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(certfile=str(certfile), keyfile=str(keyfile))
    server.socket = context.wrap_socket(server.socket, server_side=True)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    atexit.register(server.shutdown)
    return server


_SERVER: ThreadingHTTPServer | None = None
_BASE_URL: str | None = None
if _UPDATE_CA_AVAILABLE:
    _leaf, _key = _generate_trusted_server_cert()
    _SERVER = _start_https_server(_leaf, _key)
    _PORT = _SERVER.server_address[1]
    _BASE_URL = f"https://127.0.0.1:{_PORT}"
else:
    print(
        "FTF-0302: this sandbox has no openssl/update-ca-certificates/passwordless sudo -- the "
        "live TLS-backed checks are skipped and disclosed here; GitHub Actions' ubuntu-latest "
        "runner has all three."
    )


def _policy() -> ConnectionPolicy:
    assert _PORT is not None
    return ConnectionPolicy(
        name="test-huggingface",
        allowed_schemes=frozenset({"https"}),
        allowed_ports=frozenset({_PORT}),
        explicit_private_allowlist=(ipaddress.ip_network("127.0.0.0/8"),),
        allow_redirects=True,
        max_redirects=5,
    )


# -- Tests --------------------------------------------------------------


def test_happy_path_resolves_and_verifies_real_files() -> None:
    if _BASE_URL is None:
        return
    result = hf_resolver.resolve_huggingface_snapshot(
        "acme/tiny-model",
        _PINNED_REVISION,
        trust_remote_code_allowed=False,
        base_url=_BASE_URL,
        policy=_policy(),
    )
    names = sorted(f.filename for f in result.files)
    check(
        "FTF-0302",
        "acceptance: only recognized model-artifact files are acquired, nested/unrecognized "
        f"files excluded ({names})",
        names == ["config.json", "model.safetensors", "tokenizer.json"],
    )
    weights_file = next(f for f in result.files if f.filename == "model.safetensors")
    check(
        "FTF-0302",
        "acceptance: the redirected LFS file's real downloaded bytes match Hugging Face Hub's "
        "own declared sha256",
        weights_file.sha256_hex == _WEIGHTS_SHA256
        and weights_file.declared_sha256_hex == _WEIGHTS_SHA256,
    )
    check(
        "FTF-0302",
        "acceptance: files with no declared hash (small non-LFS config files) are still "
        "recorded with their own real computed sha256",
        all(
            f.declared_sha256_hex is None for f in result.files if f.filename != "model.safetensors"
        )
        and all(len(f.sha256_hex) == 64 for f in result.files),
    )
    check(
        "FTF-0302",
        "acceptance: the resolved commit sha is the real value Hugging Face Hub's own response "
        "declared",
        result.resolved_commit_sha == _PINNED_REVISION,
    )
    check(
        "FTF-0302",
        "combined_sha256_hex is deterministic and derived from the real per-file digests",
        len(result.combined_sha256_hex) == 64,
    )
    # Re-running must produce the identical combined digest -- deterministic,
    # never a value that depends on request order or wall-clock time.
    result_again = hf_resolver.resolve_huggingface_snapshot(
        "acme/tiny-model",
        _PINNED_REVISION,
        trust_remote_code_allowed=False,
        base_url=_BASE_URL,
        policy=_policy(),
    )
    check(
        "FTF-0302",
        "combined_sha256_hex is deterministic across repeated real resolutions",
        result.combined_sha256_hex == result_again.combined_sha256_hex,
    )


def test_nested_and_unrecognized_files_are_never_requested() -> None:
    if _BASE_URL is None:
        return
    before = len(REQUEST_LOG)
    hf_resolver.resolve_huggingface_snapshot(
        "acme/tiny-model",
        _PINNED_REVISION,
        trust_remote_code_allowed=False,
        base_url=_BASE_URL,
        policy=_policy(),
    )
    requested = REQUEST_LOG[before:]
    check(
        "FTF-0302",
        "negative_acceptance: no sensitive training data present during acquisition -- a "
        f"nested/dataset-shaped path is never requested ({requested})",
        not any("train.parquet" in path or "notes.rst" in path for path in requested),
    )


def test_floating_revision_is_rejected_before_any_network_call() -> None:
    before = len(REQUEST_LOG)
    raised = False
    try:
        hf_resolver.resolve_huggingface_snapshot(
            "acme/tiny-model",
            "main",
            trust_remote_code_allowed=False,
            base_url=_BASE_URL or "https://127.0.0.1:1",
            policy=_policy() if _BASE_URL else hf_resolver.default_huggingface_resolver_policy(),
        )
    except hf_resolver.FloatingRevisionError:
        raised = True
    check(
        "FTF-0302",
        "negative_acceptance: a floating revision alias ('main') is refused",
        raised,
    )
    check(
        "FTF-0302",
        "the floating-revision refusal happens before any network request is made",
        len(REQUEST_LOG) == before,
    )


def test_gated_repository_is_rejected() -> None:
    if _BASE_URL is None:
        return
    raised = False
    try:
        hf_resolver.resolve_huggingface_snapshot(
            "acme/gated-model",
            _PINNED_REVISION,
            trust_remote_code_allowed=False,
            base_url=_BASE_URL,
            policy=_policy(),
        )
    except hf_resolver.GatedRepositoryUnavailableError:
        raised = True
    check(
        "FTF-0302",
        "negative_acceptance: a gated repository the real API denies (HTTP 403) is refused, "
        "never silently treated as available",
        raised,
    )


def test_remote_code_without_review_is_rejected() -> None:
    if _BASE_URL is None:
        return
    before = len(REQUEST_LOG)
    raised = False
    try:
        hf_resolver.resolve_huggingface_snapshot(
            "acme/custom-code-model",
            _PINNED_REVISION,
            trust_remote_code_allowed=False,
            base_url=_BASE_URL,
            policy=_policy(),
        )
    except hf_resolver.RemoteCodeNotAllowedError:
        raised = True
    requested = REQUEST_LOG[before:]
    check(
        "FTF-0302",
        "negative_acceptance: remote code execution is blocked without explicit review "
        "(trust_remote_code_allowed=False)",
        raised,
    )
    check(
        "FTF-0302",
        "no file is downloaded once the remote-code gate refuses the repository",
        not any("/resolve/" in path for path in requested),
    )


def test_remote_code_with_explicit_review_is_allowed() -> None:
    if _BASE_URL is None:
        return
    result = hf_resolver.resolve_huggingface_snapshot(
        "acme/custom-code-model",
        _PINNED_REVISION,
        trust_remote_code_allowed=True,
        base_url=_BASE_URL,
        policy=_policy(),
    )
    names = sorted(f.filename for f in result.files)
    check(
        "FTF-0302",
        "negative_acceptance: explicit review (trust_remote_code_allowed=True) allows "
        "acquisition to proceed, but the .py file itself is still never downloaded "
        f"(only recognized model-artifact files are acquired: {names})",
        result.requires_remote_code is True and names == ["config.json", "model.safetensors"],
    )


def test_digest_mismatch_is_detected() -> None:
    if _BASE_URL is None:
        return
    raised = False
    try:
        hf_resolver.resolve_huggingface_snapshot(
            "acme/bad-digest-model",
            _PINNED_REVISION,
            trust_remote_code_allowed=False,
            base_url=_BASE_URL,
            policy=_policy(),
        )
    except hf_resolver.ResolvedFileDigestMismatchError:
        raised = True
    check(
        "FTF-0302",
        "acceptance: a downloaded file whose real sha256 does not match Hugging Face Hub's own "
        "declared sha256 is refused, never accepted as verified",
        raised,
    )


def test_no_recognized_artifact_files_raises() -> None:
    if _BASE_URL is None:
        return
    raised = False
    try:
        hf_resolver.resolve_huggingface_snapshot(
            "acme/dataset-only-model",
            _PINNED_REVISION,
            trust_remote_code_allowed=False,
            base_url=_BASE_URL,
            policy=_policy(),
        )
    except hf_resolver.NoRecognizedArtifactFilesError:
        raised = True
    check(
        "FTF-0302",
        "a repository with no recognized model-artifact file shape is refused rather than "
        "silently acquiring nothing as a fabricated success",
        raised,
    )


def test_license_gate_blocks_before_any_network_call() -> None:
    before = len(REQUEST_LOG)
    artifact = _make_artifact(license_accepted=False)
    raised = False
    try:
        hf_resolver.resolve_and_verify_huggingface_artifact(
            artifact,
            base_url=_BASE_URL or "https://127.0.0.1:1",
            policy=_policy() if _BASE_URL else hf_resolver.default_huggingface_resolver_policy(),
        )
    except LicenseNotAcceptedError:
        raised = True
    check(
        "FTF-0302",
        "negative_acceptance: unavailable (unaccepted) license blocks acquisition, reusing "
        "FTF-0305's own assert_license_accepted_for_use",
        raised,
    )
    check(
        "FTF-0302",
        "the license gate is checked before any network request is made",
        len(REQUEST_LOG) == before,
    )


def test_license_accepted_artifact_resolves_end_to_end() -> None:
    if _BASE_URL is None:
        return
    artifact = _make_artifact(license_accepted=True)
    result = hf_resolver.resolve_and_verify_huggingface_artifact(
        artifact, base_url=_BASE_URL, policy=_policy()
    )
    check(
        "FTF-0302",
        "an artifact with an accepted license resolves end-to-end through the real approved-"
        "artifact entry point",
        result.resolved_commit_sha == _PINNED_REVISION and len(result.files) == 3,
    )


def test_wrong_source_type_is_rejected() -> None:
    artifact = _make_artifact(
        source_type="private_s3", source_uri="s3://acme-bucket/models/x", license_accepted=True
    )
    raised = False
    try:
        hf_resolver.resolve_and_verify_huggingface_artifact(
            artifact, base_url=_BASE_URL or "https://x"
        )
    except hf_resolver.HuggingFaceResolutionError:
        raised = True
    check(
        "FTF-0302",
        "an artifact whose source_type is not 'huggingface' is refused, never silently resolved",
        raised,
    )


def test_destination_denied_is_a_real_policy_check() -> None:
    if _BASE_URL is None:
        return
    # The production default policy carries no private-address allowlist --
    # a real, unmodified policy denial against this suite's own loopback
    # test server, proving the destination check is genuinely live.
    denied = False
    try:
        hf_resolver.resolve_huggingface_snapshot(
            "acme/tiny-model",
            _PINNED_REVISION,
            trust_remote_code_allowed=False,
            base_url=_BASE_URL,
            policy=hf_resolver.default_huggingface_resolver_policy(),
        )
    except OutboundPolicyError:
        denied = True
    check(
        "FTF-0302",
        "a destination not permitted by policy is denied by a real, live check "
        "(OutboundPolicyError propagates unwrapped, matching FTF-H006's own convention)",
        denied,
    )


# -- Fault injection (Law 1): the digest-mismatch and floating-revision -----
# checks are load-bearing, not merely present in source.

_MODULE_PATH = ROOT / "apps" / "api" / "app" / "artifacts" / "hugging_face_artifact_resolver.py"


def test_fault_injection_digest_mismatch_check_is_load_bearing() -> None:
    if _BASE_URL is None:
        return
    original = _MODULE_PATH.read_bytes()
    target = b"if declared is not None and declared != digest:"
    check(
        "FTF-0302",
        "fault injection target snippet is present in the real source",
        target in original,
    )
    try:
        mutated = original.replace(
            target, b"if False and declared is not None and declared != digest:", 1
        )
        check("FTF-0302", "fault injection actually changed the file", mutated != original)
        _MODULE_PATH.write_bytes(mutated)
        importlib.reload(hf_resolver)
        no_longer_raises = False
        try:
            hf_resolver.resolve_huggingface_snapshot(
                "acme/bad-digest-model",
                _PINNED_REVISION,
                trust_remote_code_allowed=False,
                base_url=_BASE_URL,
                policy=_policy(),
            )
            no_longer_raises = True
        except hf_resolver.ResolvedFileDigestMismatchError:
            no_longer_raises = False
        check(
            "FTF-0302",
            "fault injection: disabling the digest-mismatch check genuinely lets a corrupted/"
            "mismatched file through as a fabricated success",
            no_longer_raises,
        )
    finally:
        _MODULE_PATH.write_bytes(original)
        importlib.reload(hf_resolver)
        check(
            "FTF-0302",
            "the module file is restored byte-identical after fault injection",
            _MODULE_PATH.read_bytes() == original,
        )
        # Re-run confirms the real check is back in force.
        restored_raises = False
        try:
            hf_resolver.resolve_huggingface_snapshot(
                "acme/bad-digest-model",
                _PINNED_REVISION,
                trust_remote_code_allowed=False,
                base_url=_BASE_URL,
                policy=_policy(),
            )
        except hf_resolver.ResolvedFileDigestMismatchError:
            restored_raises = True
        check(
            "FTF-0302",
            "after restoring the file, the digest-mismatch check genuinely fires again",
            restored_raises,
        )


def test_fault_injection_floating_revision_check_is_load_bearing() -> None:
    original = _MODULE_PATH.read_bytes()
    target = b"    if not is_pinned_revision(revision):\n"
    check(
        "FTF-0302",
        "fault injection target snippet is present in the real source",
        target in original,
    )
    try:
        mutated = original.replace(
            target, b"    if False and not is_pinned_revision(revision):\n", 1
        )
        check("FTF-0302", "fault injection actually changed the file", mutated != original)
        _MODULE_PATH.write_bytes(mutated)
        importlib.reload(hf_resolver)
        before = len(REQUEST_LOG)
        no_longer_raises = False
        try:
            hf_resolver.resolve_huggingface_snapshot(
                "acme/tiny-model",
                "main",
                trust_remote_code_allowed=False,
                base_url=_BASE_URL or "https://127.0.0.1:1",
                policy=_policy()
                if _BASE_URL
                else hf_resolver.default_huggingface_resolver_policy(),
            )
            no_longer_raises = True
        except hf_resolver.FloatingRevisionError:
            no_longer_raises = False
        except Exception:  # noqa: BLE001 -- any other real outcome still proves the guard is gone
            no_longer_raises = True
        check(
            "FTF-0302",
            "fault injection: disabling the floating-revision check genuinely lets a mutable "
            "alias reach a real network request instead of being refused up front",
            no_longer_raises and (len(REQUEST_LOG) > before or _BASE_URL is None),
        )
    finally:
        _MODULE_PATH.write_bytes(original)
        importlib.reload(hf_resolver)
        check(
            "FTF-0302",
            "the module file is restored byte-identical after fault injection",
            _MODULE_PATH.read_bytes() == original,
        )
        restored_raises = False
        try:
            hf_resolver.resolve_huggingface_snapshot(
                "acme/tiny-model",
                "main",
                trust_remote_code_allowed=False,
                base_url=_BASE_URL or "https://127.0.0.1:1",
                policy=_policy()
                if _BASE_URL
                else hf_resolver.default_huggingface_resolver_policy(),
            )
        except hf_resolver.FloatingRevisionError:
            restored_raises = True
        check(
            "FTF-0302",
            "after restoring the file, the floating-revision check genuinely fires again",
            restored_raises,
        )


def main() -> None:
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()

    if FAILURES:
        print(f"FTF-0302 Hugging Face artifact resolver: {len(FAILURES)} failed")
        for failure in FAILURES:
            print(f"  {failure}")
        sys.exit(1)

    print("FTF-0302 Hugging Face artifact resolver: all checks passed")
    sys.exit(0)


if __name__ == "__main__":
    main()
