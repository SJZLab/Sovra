#!/usr/bin/env python3
"""FTF-0303 -- Private S3 artifact resolver (no-DB unit/fault tests).

Runnable without any database. Drives a real local, plain-HTTP,
path-style S3-shaped fixture server that independently re-derives the
expected AWS Signature Version 4 `Authorization` header (reusing
`sign_request`, the same real signer under test, exactly the way a real
S3-compatible backend independently re-derives a signature from its own
knowledge of the secret key) and rejects a request whose signature does
not match -- this is not a stub that accepts any request; a wrong secret
genuinely fails against it (see `test_wrong_credentials_are_rejected`).
Plain HTTP (not HTTPS) matches this repository's own established
SeaweedFS-fixture precedent for a *private* S3-compatible endpoint
(`tests/integration/test_storage_semantics.py`, FTF-R017, CERTIFIED) --
unlike FTF-0302's own public Hugging Face Hub case, a private endpoint's
own TLS posture is an operator/network decision, not something this
fixture needs to prove.

Covers this task's own negative_acceptance ("Bucket mismatch, cross-
tenant prefix, partial upload or ETag-as-hash assumption fails load") as
four independently-triggered real checks, plus a real destination-denied
policy check, a real wrong-credentials rejection, a real mid-transfer
truncation (proving `http.client.IncompleteRead` is never silently
swallowed), and two disclosed fault injections (Law 1) proving the
expected-size and independent-digest-computation checks are load-bearing.

Run directly: python3 tests/tasks/test_ftf_0303.py
"""

from __future__ import annotations

import atexit
import hashlib
import importlib
import ipaddress
import sys
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

from packages.security.url_policy import ConnectionPolicy, OutboundPolicyError  # noqa: E402

import app.artifacts.private_s3_artifact_resolver as s3_resolver  # noqa: E402
from app.artifacts.artifact_schema import TrainableArtifact  # noqa: E402
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
        "display_name": f"s3-artifact-{uuid.uuid4().hex[:8]}",
        "source_type": "private_s3",
        "source_uri": "s3://acme-bucket/org-42/models/tiny.safetensors",
        "model_family": "tiny",
        "base_model_revision": "v1.0-real-version-id",
        "license_id": "apache-2.0",
        "trust_remote_code_allowed": False,
        "status": "registered",
    }
    fields.update(overrides)
    artifact = TrainableArtifact(**fields)
    artifact.id = uuid.uuid4()
    artifact.created_at = now
    artifact.updated_at = now
    return artifact


# -- Real local plain-HTTP path-style S3-shaped fixture ----------------------

_ACCESS_KEY = "AKIA_TEST_ACCESS_KEY"
_SECRET_KEY = "test-secret-key-do-not-use-in-production"
_CREDENTIALS = S3Credentials(access_key=_ACCESS_KEY, secret_key=_SECRET_KEY)
_WRONG_CREDENTIALS = S3Credentials(access_key=_ACCESS_KEY, secret_key="wrong-secret-key")
_REGION = "us-east-1"

_OBJECT_BYTES = b"REAL-S3-OBJECT-BYTES-FOR-FTF-0303" * 8
_OBJECT_SHA256 = hashlib.sha256(_OBJECT_BYTES).hexdigest()
_SINGLE_PART_ETAG = hashlib.md5(_OBJECT_BYTES, usedforsecurity=False).hexdigest()  # 32 hex chars
_MULTIPART_ETAG = f"{_SINGLE_PART_ETAG}-3"  # never a valid hash of anything

REQUEST_LOG: list[str] = []

# path -> versionId -> response spec
_ROUTES: dict[str, dict[str, dict[str, object]]] = {
    "/acme-bucket/org-42/models/tiny.safetensors": {
        "v1.0-real-version-id": {
            "status": 200,
            "body": _OBJECT_BYTES,
            "etag": _SINGLE_PART_ETAG,
        },
        "v-multipart-etag": {
            "status": 200,
            "body": _OBJECT_BYTES,
            "etag": _MULTIPART_ETAG,
        },
        "v-truncate-mid-transfer": {
            "status": 200,
            "body": _OBJECT_BYTES,
            "etag": _SINGLE_PART_ETAG,
            "truncate_after_bytes": 5,
        },
    },
}


def _expected_authorization(method: str, path: str, query: dict[str, str], amz_date: str) -> str:
    parsed = urlsplit(f"http://placeholder{path}")
    endpoint = S3Endpoint(scheme="http", host="127.0.0.1", port=_PORT, region=_REGION)
    now = datetime.strptime(amz_date, "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC)
    signed = sign_request(
        method=method,
        endpoint=endpoint,
        credentials=_CREDENTIALS,
        canonical_uri=parsed.path,
        query=query,
        now=now,
    )
    return signed.headers["authorization"]


class _S3Handler(BaseHTTPRequestHandler):
    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        pass

    def do_GET(self) -> None:  # noqa: N802
        REQUEST_LOG.append(self.path)
        parsed = urlsplit(self.path)
        query = {k: v[0] for k, v in parse_qs(parsed.query).items()}
        version_id = query.get("versionId", "")

        # Independently re-derive the expected signature from this
        # fixture's own knowledge of the real secret key -- a genuine
        # signature-verification round trip, not a stub that accepts any
        # request regardless of credentials.
        amz_date = self.headers.get("x-amz-date", "")
        actual_auth = self.headers.get("authorization", "")
        expected_auth = (
            _expected_authorization(self.command, parsed.path, query, amz_date)
            if amz_date
            else None
        )
        if expected_auth is None or actual_auth != expected_auth:
            self._respond(403, b'{"Code":"SignatureDoesNotMatch"}')
            return

        versions = _ROUTES.get(parsed.path)
        route = versions.get(version_id) if versions else None
        if route is None:
            self._respond(404, b'{"Code":"NoSuchVersion"}')
            return

        status = route["status"]
        assert isinstance(status, int)
        body = route["body"]
        assert isinstance(body, bytes)
        truncate_after = route.get("truncate_after_bytes")

        self.send_response(status)
        self.send_header("ETag", f'"{route["etag"]}"')
        if truncate_after is not None:
            assert isinstance(truncate_after, int)
            # Declare the real full length, but only ever send a prefix of
            # it, then close the connection -- a genuine mid-transfer
            # truncation, not a self-consistent short response.
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body[:truncate_after])
            self.close_connection = True
            return
        self._respond(status, body)

    def _respond(self, status: int, body: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if body:
            self.wfile.write(body)


def _start_server() -> ThreadingHTTPServer:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _S3Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    atexit.register(server.shutdown)
    return server


_SERVER = _start_server()
_PORT = _SERVER.server_address[1]


def _endpoint() -> S3Endpoint:
    return S3Endpoint(scheme="http", host="127.0.0.1", port=_PORT, region=_REGION)


def _connection(**overrides: object) -> s3_resolver.PrivateS3ConnectionConfig:
    fields: dict[str, object] = {
        "endpoint": _endpoint(),
        "credentials": _CREDENTIALS,
        "expected_bucket": "acme-bucket",
        "tenant_key_prefix": "org-42/",
    }
    fields.update(overrides)
    return s3_resolver.PrivateS3ConnectionConfig(**fields)  # type: ignore[arg-type]


def _policy() -> ConnectionPolicy:
    return ConnectionPolicy(
        name="test-private-s3",
        allowed_schemes=frozenset({"http"}),
        allowed_ports=frozenset({_PORT}),
        explicit_private_allowlist=(ipaddress.ip_network("127.0.0.0/8"),),
    )


# -- Tests --------------------------------------------------------------


def test_happy_path_resolves_and_verifies_real_object() -> None:
    result = s3_resolver.resolve_private_s3_object(
        "s3://acme-bucket/org-42/models/tiny.safetensors",
        "v1.0-real-version-id",
        connection=_connection(),
        policy=_policy(),
    )
    check(
        "FTF-0303",
        "acceptance: the real downloaded bytes' own sha256 is returned, matching an "
        "independently computed digest of the real object bytes",
        result.sha256_hex == _OBJECT_SHA256,
    )
    check(
        "FTF-0303",
        "acceptance: bucket/key/version are the real resolved values",
        result.bucket == "acme-bucket"
        and result.key == "org-42/models/tiny.safetensors"
        and result.version_id == "v1.0-real-version-id",
    )
    check(
        "FTF-0303",
        "a plain single-part ETag is disclosed as looking like a content hash (informational "
        "only)",
        result.etag_looks_like_content_hash is True,
    )


def test_resolve_and_verify_artifact_entry_point() -> None:
    artifact = _make_artifact()
    result = s3_resolver.resolve_and_verify_private_s3_artifact(
        artifact, connection=_connection(), policy=_policy()
    )
    check(
        "FTF-0303",
        "the approved-artifact entry point resolves end-to-end through the real object fetch",
        result.sha256_hex == _OBJECT_SHA256,
    )


def test_bucket_mismatch_fails_load_before_any_network_request() -> None:
    before = len(REQUEST_LOG)
    raised = False
    try:
        s3_resolver.resolve_private_s3_object(
            "s3://a-totally-different-bucket/org-42/models/tiny.safetensors",
            "v1.0-real-version-id",
            connection=_connection(),
            policy=_policy(),
        )
    except s3_resolver.BucketMismatchError:
        raised = True
    check("FTF-0303", "negative_acceptance: bucket mismatch fails load", raised)
    check(
        "FTF-0303",
        "the bucket-mismatch check is applied before any network request is made",
        len(REQUEST_LOG) == before,
    )


def test_cross_tenant_prefix_fails_load_before_any_network_request() -> None:
    before = len(REQUEST_LOG)
    raised = False
    try:
        s3_resolver.resolve_private_s3_object(
            "s3://acme-bucket/org-99/models/tiny.safetensors",
            "v1.0-real-version-id",
            connection=_connection(),
            policy=_policy(),
        )
    except s3_resolver.CrossTenantPrefixError:
        raised = True
    check("FTF-0303", "negative_acceptance: cross-tenant prefix fails load", raised)
    check(
        "FTF-0303",
        "the cross-tenant-prefix check is applied before any network request is made",
        len(REQUEST_LOG) == before,
    )


def test_cross_tenant_prefix_rejects_sibling_tenant_string_collision() -> None:
    """A bare `key.startswith(tenant_key_prefix)` would let this real,
    different tenant's real object through: `"org-42/..."` genuinely
    starts with the character sequence `"org-4"`, even though `org-4` and
    `org-42` are two different tenant namespaces. This is not a contrived
    edge case -- a connection built from `f"org-{organization_id}"` with
    no trailing separator hits this exact collision for any tenant ID
    that is itself a prefix of another tenant's ID (`org-4`/`org-42`,
    `org-1`/`org-10`, ...). `_key_within_tenant_prefix` must require a
    real `/` path-segment boundary, not merely a matching character
    sequence."""
    before = len(REQUEST_LOG)
    raised = False
    try:
        s3_resolver.resolve_private_s3_object(
            "s3://acme-bucket/org-42/models/tiny.safetensors",
            "v1.0-real-version-id",
            connection=_connection(tenant_key_prefix="org-4"),
            policy=_policy(),
        )
    except s3_resolver.CrossTenantPrefixError:
        raised = True
    check(
        "FTF-0303",
        "negative_acceptance: a tenant-key-prefix string collision with a real different "
        "tenant's key (org-4 vs org-42/...) fails load at a real path-segment boundary, not "
        "a bare string-prefix match",
        raised,
    )
    check(
        "FTF-0303",
        "the sibling-tenant-string-collision check is applied before any network request is "
        "made",
        len(REQUEST_LOG) == before,
    )


def test_blank_version_id_fails_load_before_any_network_request() -> None:
    before = len(REQUEST_LOG)
    raised = False
    try:
        s3_resolver.resolve_private_s3_object(
            "s3://acme-bucket/org-42/models/tiny.safetensors",
            "   ",
            connection=_connection(),
            policy=_policy(),
        )
    except s3_resolver.ObjectVersionUnavailableError:
        raised = True
    check("FTF-0303", "a blank base_model_revision/versionId is refused before any request", raised)
    check(
        "FTF-0303", "no network request is made for a blank version id", len(REQUEST_LOG) == before
    )


def test_unknown_version_is_a_real_404() -> None:
    raised = False
    try:
        s3_resolver.resolve_private_s3_object(
            "s3://acme-bucket/org-42/models/tiny.safetensors",
            "v-does-not-exist",
            connection=_connection(),
            policy=_policy(),
        )
    except s3_resolver.ObjectVersionUnavailableError:
        raised = True
    check(
        "FTF-0303",
        "a real HTTP 404 for an unknown object version raises ObjectVersionUnavailableError",
        raised,
    )


def test_expected_size_mismatch_fails_load() -> None:
    raised = False
    try:
        s3_resolver.resolve_private_s3_object(
            "s3://acme-bucket/org-42/models/tiny.safetensors",
            "v1.0-real-version-id",
            connection=_connection(),
            expected_size_bytes=len(_OBJECT_BYTES) + 1,
            policy=_policy(),
        )
    except s3_resolver.PartialUploadError:
        raised = True
    check(
        "FTF-0303",
        "negative_acceptance: partial upload (independent expected-size mismatch) fails load",
        raised,
    )


def test_expected_size_match_succeeds() -> None:
    result = s3_resolver.resolve_private_s3_object(
        "s3://acme-bucket/org-42/models/tiny.safetensors",
        "v1.0-real-version-id",
        connection=_connection(),
        expected_size_bytes=len(_OBJECT_BYTES),
        policy=_policy(),
    )
    check(
        "FTF-0303",
        "a correct expected_size_bytes does not falsely reject a genuinely complete object",
        result.size_bytes == len(_OBJECT_BYTES),
    )


def test_mid_transfer_truncation_is_never_silently_accepted() -> None:
    raised_incomplete_read = False
    try:
        s3_resolver.resolve_private_s3_object(
            "s3://acme-bucket/org-42/models/tiny.safetensors",
            "v-truncate-mid-transfer",
            connection=_connection(),
            policy=_policy(),
        )
    except Exception as exc:  # noqa: BLE001 -- asserting the exact real exception type below
        raised_incomplete_read = "IncompleteRead" in type(exc).__name__
    check(
        "FTF-0303",
        "negative_acceptance: a genuine mid-transfer truncation (declared Content-Length not "
        "actually delivered) is never silently accepted as a complete object -- it propagates "
        "as a real http.client.IncompleteRead, not a fabricated success",
        raised_incomplete_read,
    )


def test_multipart_etag_never_used_as_a_content_digest() -> None:
    result = s3_resolver.resolve_private_s3_object(
        "s3://acme-bucket/org-42/models/tiny.safetensors",
        "v-multipart-etag",
        connection=_connection(),
        policy=_policy(),
    )
    check(
        "FTF-0303",
        "negative_acceptance: a multipart-shaped ETag is never treated as the content digest -- "
        "the real sha256 of the actual downloaded bytes is returned regardless",
        result.sha256_hex == _OBJECT_SHA256,
    )
    check(
        "FTF-0303",
        "a multipart-shaped ETag is correctly disclosed as NOT looking like a content hash",
        result.etag_looks_like_content_hash is False,
    )
    check(
        "FTF-0303",
        "the real digest is not equal to any hex-only prefix/transform of the multipart ETag",
        result.sha256_hex != _MULTIPART_ETAG.replace("-3", ""),
    )


def test_wrong_source_type_is_rejected() -> None:
    artifact = _make_artifact(source_type="huggingface", source_uri="acme/tiny-model")
    raised = False
    try:
        s3_resolver.resolve_and_verify_private_s3_artifact(artifact, connection=_connection())
    except s3_resolver.PrivateS3ResolutionError:
        raised = True
    check(
        "FTF-0303",
        "an artifact whose source_type is not 'private_s3' is refused, never silently resolved",
        raised,
    )


def test_wrong_credentials_are_rejected() -> None:
    raised = False
    try:
        s3_resolver.resolve_private_s3_object(
            "s3://acme-bucket/org-42/models/tiny.safetensors",
            "v1.0-real-version-id",
            connection=_connection(credentials=_WRONG_CREDENTIALS),
            policy=_policy(),
        )
    except s3_resolver.PrivateS3ResolutionError:
        raised = True
    check(
        "FTF-0303",
        "a real signature computed with the wrong secret key is genuinely rejected by the "
        "fixture's own independent signature re-derivation (proving this is a real signed "
        "request, not an unauthenticated fetch)",
        raised,
    )


def test_destination_denied_is_a_real_policy_check() -> None:
    denied = False
    try:
        s3_resolver.resolve_private_s3_object(
            "s3://acme-bucket/org-42/models/tiny.safetensors",
            "v1.0-real-version-id",
            connection=_connection(),
            policy=s3_resolver.default_private_s3_resolver_policy(),
        )
    except OutboundPolicyError:
        denied = True
    check(
        "FTF-0303",
        "a destination not permitted by policy is denied by a real, live check (production "
        "default carries no private-address allowlist)",
        denied,
    )


# -- Fault injection (Law 1): the expected-size and independent-digest -----
# checks are load-bearing, not merely present in source.

_MODULE_PATH = ROOT / "apps" / "api" / "app" / "artifacts" / "private_s3_artifact_resolver.py"


def test_fault_injection_expected_size_check_is_load_bearing() -> None:
    original = _MODULE_PATH.read_bytes()
    target = b"if expected_size_bytes is not None and actual_size != expected_size_bytes:"
    check(
        "FTF-0303",
        "fault injection target snippet is present in the real source",
        target in original,
    )
    try:
        mutated = original.replace(
            target,
            b"if False and expected_size_bytes is not None and actual_size != expected_size_bytes:",
            1,
        )
        check("FTF-0303", "fault injection actually changed the file", mutated != original)
        _MODULE_PATH.write_bytes(mutated)
        importlib.reload(s3_resolver)
        no_longer_raises = False
        try:
            s3_resolver.resolve_private_s3_object(
                "s3://acme-bucket/org-42/models/tiny.safetensors",
                "v1.0-real-version-id",
                connection=_connection(),
                expected_size_bytes=len(_OBJECT_BYTES) + 1,
                policy=_policy(),
            )
            no_longer_raises = True
        except s3_resolver.PartialUploadError:
            no_longer_raises = False
        check(
            "FTF-0303",
            "fault injection: disabling the expected-size check genuinely lets an "
            "incomplete-looking object through as a fabricated success",
            no_longer_raises,
        )
    finally:
        _MODULE_PATH.write_bytes(original)
        importlib.reload(s3_resolver)
        check(
            "FTF-0303",
            "the module file is restored byte-identical after fault injection",
            _MODULE_PATH.read_bytes() == original,
        )
        restored_raises = False
        try:
            s3_resolver.resolve_private_s3_object(
                "s3://acme-bucket/org-42/models/tiny.safetensors",
                "v1.0-real-version-id",
                connection=_connection(),
                expected_size_bytes=len(_OBJECT_BYTES) + 1,
                policy=_policy(),
            )
        except s3_resolver.PartialUploadError:
            restored_raises = True
        check(
            "FTF-0303",
            "after restoring the file, the expected-size check genuinely fires again",
            restored_raises,
        )


def test_fault_injection_etag_as_hash_assumption_is_load_bearing() -> None:
    original = _MODULE_PATH.read_bytes()
    target = (
        b"sha256_hex = hashlib.sha256(response.body).hexdigest()  # never derived from etag_raw"
    )
    check(
        "FTF-0303",
        "fault injection target snippet is present in the real source",
        target in original,
    )
    try:
        # A version of this module that *did* make the ETag-as-hash
        # assumption: derive the "digest" from the ETag header instead of
        # the real downloaded bytes.
        mutated = original.replace(
            target,
            b'sha256_hex = hashlib.sha256(etag_raw.encode("utf-8")).hexdigest()',
            1,
        )
        check("FTF-0303", "fault injection actually changed the file", mutated != original)
        _MODULE_PATH.write_bytes(mutated)
        importlib.reload(s3_resolver)
        result = s3_resolver.resolve_private_s3_object(
            "s3://acme-bucket/org-42/models/tiny.safetensors",
            "v-multipart-etag",
            connection=_connection(),
            policy=_policy(),
        )
        check(
            "FTF-0303",
            "fault injection: assuming ETag is a content hash genuinely produces a wrong "
            "digest for a real multipart-shaped ETag, silently 'verifying' the wrong bytes",
            result.sha256_hex != _OBJECT_SHA256,
        )
    finally:
        _MODULE_PATH.write_bytes(original)
        importlib.reload(s3_resolver)
        check(
            "FTF-0303",
            "the module file is restored byte-identical after fault injection",
            _MODULE_PATH.read_bytes() == original,
        )
        result = s3_resolver.resolve_private_s3_object(
            "s3://acme-bucket/org-42/models/tiny.safetensors",
            "v-multipart-etag",
            connection=_connection(),
            policy=_policy(),
        )
        check(
            "FTF-0303",
            "after restoring the file, the real independent digest computation is back in force",
            result.sha256_hex == _OBJECT_SHA256,
        )


def main() -> None:
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()

    if FAILURES:
        print(f"FTF-0303 Private S3 artifact resolver: {len(FAILURES)} failed")
        for failure in FAILURES:
            print(f"  {failure}")
        sys.exit(1)

    print("FTF-0303 Private S3 artifact resolver: all checks passed")
    sys.exit(0)


if __name__ == "__main__":
    main()
