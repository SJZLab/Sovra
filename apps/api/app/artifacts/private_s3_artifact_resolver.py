"""Private S3 artifact resolver (FTF-0303, Section 6.7/13/34/68/77/81).

`FTF-0301`'s own `TrainableArtifact` schema stores a `private_s3`
`source_uri` as a bare, unresolved `s3://bucket/key` identifier and never
dials a real upstream (its own module docstring names this exact task,
FTF-0303, as the real caller). This module is that caller: given an
already-registered `private_s3` artifact, it fetches the exact pinned
object version from a real, SigV4-signed, S3-compatible endpoint and
verifies the download for real -- never trusting a server-declared value
in place of an independently computed one.

Every real HTTP request this module makes goes through
`packages/security/outbound_guard.py`'s `guarded_fetch` (FTF-H006) -- the
one module in this repository allowed to open an outbound socket --
`packages/security/url_policy.py`'s own module docstring already names
this task (`FTF-0303`) as one of its intended future callers. Request
signing reuses `apps/api/app/services/storage_semantics_client.py`'s own
real AWS Signature Version 4 implementation (`sign_request`, FTF-R017,
CERTIFIED) rather than re-deriving SigV4 math a second time; this module
only ever *signs* a request with it, it does not use that module's own
raw-`urllib` `S3Client` transport (which predates FTF-H006's shared-guard
requirement and is scoped to FTF-R017's own storage-semantics
qualification, not this task's write set).

This task's own negative_acceptance ("Bucket mismatch, cross-tenant
prefix, partial upload or ETag-as-hash assumption fails load") is
enforced as four independent, disclosed checks:

  1. **Bucket mismatch** -- the `source_uri`'s own bucket must equal the
     caller's `PrivateS3ConnectionConfig.expected_bucket` (the bucket this
     organization's own real S3 credentials/connection is actually scoped
     to) -- checked before any network request, the same
     "reject-before-any-request" discipline `FTF-0302`'s own floating-
     revision/license gates already established.
  2. **Cross-tenant prefix** -- the parsed object key must start with
     `PrivateS3ConnectionConfig.tenant_key_prefix` (this organization's own
     namespace inside a possibly shared bucket) -- also checked before any
     network request. Neither this module nor `TrainableArtifact` (FTF-
     0301, frozen) stores this connection/prefix binding -- Section 6.7's
     schema has no S3-connection entity, and adding one here would be the
     "independent schema change" this task's own contract forbids
     (`database_changes: "No independent schema change. Consume frozen
     contract."`). The bucket/prefix binding is therefore a real, explicit
     caller-supplied trust boundary (`PrivateS3ConnectionConfig`), the same
     shape `resolve_huggingface_snapshot`'s own overridable `base_url`
     already established for a structurally identical "test/production
     endpoint is a caller concern, not a stored field" reason.
  3. **Partial upload** -- a genuine mid-transfer truncation (the server
     declares a `Content-Length` it then does not actually deliver) is
     never silently accepted as a complete file: `guarded_fetch`'s own
     underlying `http.client.HTTPResponse.read()` already raises
     `http.client.IncompleteRead` in that case, and this module does not
     catch or wrap it into a false success anywhere -- it propagates
     uncaught, exactly like every other real, unexpected transport failure
     this module does not specifically expect (confirmed directly against
     a real short-lived connection in this task's own test suite, not
     assumed from `http.client`'s documentation alone). A self-consistent
     but still-incomplete object (one whose own declared `Content-Length`
     matches what was actually sent, but which is not the *complete*
     artifact the caller independently already knows the size of) is
     caught by the optional `expected_size_bytes` parameter below, an
     independent size expectation from *outside* this request/response
     cycle entirely -- the only source that can catch this class of
     defect, since nothing in the response itself distinguishes a
     genuinely complete object from a self-consistent-but-short one.
  4. **ETag-as-hash assumption** -- this module never uses the response's
     `ETag` header as if it were a content digest anywhere in its own
     code: `sha256_hex` below is *always* computed directly from the real
     downloaded bytes (`hashlib.sha256(response.body)`), never from `ETag`
     in any form. This matters because S3's own ETag is the content MD5
     *only* for a single-part upload; a multipart upload's ETag is
     `"<hex>-<part_count>"`, which is neither a valid hash of the object's
     content nor even the right hash algorithm -- treating it as one would
     silently "verify" a wrong artifact. `etag_looks_like_content_hash`
     below is disclosed, informational-only metadata (true only for a
     plain 32-hex-character ETag) -- it is never consulted by this
     module's own verification path, precisely so that assumption can
     never be made here even by a future accidental edit; this task's own
     fault-injection test (see `tests/tasks/test_ftf_0303.py`) proves that
     a version of this module which *did* make that assumption produces a
     wrong digest against a real multipart-shaped `ETag`.

`base_model_revision` (Section 6.7, shared across every `source_type`) is
read here as the exact, required S3 object `versionId` for this artifact
-- an explicit version identity, never "the current object", matching
Section 18's reproducibility requirement. An empty/blank value is refused
before any network request; this module does not additionally re-apply
`FTF-0301`'s own `is_pinned_revision` mutable-alias check, since that
check's own alias vocabulary (`"latest"`, `"main"`, ...) targets
VCS-style refs and has no real S3 equivalent -- a real S3 `versionId` is
never one of those literal strings in practice, and this task's own
`dependencies` list (`FTF-0202`, `FTF-0301`, `FTF-H006`) does not include
`FTF-0305`'s license workflow, so this module deliberately does not gate
on license acceptance either -- that stays FTF-0302's own concern for a
`huggingface` artifact, not a dependency this task was given.

`FTF-0202`'s own `resolve_secret_ref` (OpenBao, CERTIFIED) is the real
credential source for a production `PrivateS3ConnectionConfig` -- this
module accepts already-resolved `S3Credentials` rather than a
`secret_ref` string itself, so it never needs to import an OpenBao client
configuration of its own; the caller (a future connection-management task,
out of this task's own write set) resolves the two secret refs an S3
access-key/secret-key pair requires and constructs `S3Credentials` once,
the same "caller resolves the secret, this module only ever sees real
already-resolved credentials" shape `FTF-0201`'s own LiteLLM connector
already established for `secret_ref`.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import dataclass
from urllib.parse import urlsplit

from packages.security.outbound_guard import GuardedResponse, OutboundGuardError, guarded_fetch
from packages.security.url_policy import ConnectionPolicy

from app.artifacts.artifact_schema import TrainableArtifact, validate_source_uri
from app.services.storage_semantics_client import S3Credentials, S3Endpoint, sign_request

__all__ = [
    "BucketMismatchError",
    "CrossTenantPrefixError",
    "ObjectVersionUnavailableError",
    "PartialUploadError",
    "PrivateS3ConnectionConfig",
    "PrivateS3ResolutionError",
    "PrivateS3ResolutionResult",
    "default_private_s3_resolver_policy",
    "resolve_and_verify_private_s3_artifact",
    "resolve_private_s3_object",
]

_DEFAULT_TIMEOUT_SECONDS = 30.0

GuardedFetchFn = Callable[..., GuardedResponse]


class PrivateS3ResolutionError(ValueError):
    """Base class for every real defect this module raises resolving a
    `private_s3` artifact -- never caught and silently converted to a
    fabricated success anywhere in this module."""


class BucketMismatchError(PrivateS3ResolutionError):
    """The artifact's own `source_uri` bucket does not equal the caller's
    `PrivateS3ConnectionConfig.expected_bucket` -- refused before any
    network request is made."""


class CrossTenantPrefixError(PrivateS3ResolutionError):
    """The artifact's own `source_uri` key does not start with the
    caller's `PrivateS3ConnectionConfig.tenant_key_prefix` -- refused
    before any network request is made."""


class ObjectVersionUnavailableError(PrivateS3ResolutionError):
    """`base_model_revision` is blank, or the real endpoint reports that
    the exact requested object version does not exist (HTTP 404)."""


class PartialUploadError(PrivateS3ResolutionError):
    """The real number of bytes received does not match a caller-supplied
    `expected_size_bytes` from outside this request/response cycle -- an
    independent size expectation, since a self-consistent-but-incomplete
    response looks otherwise identical to a complete one. A genuine
    mid-transfer truncation is a separate, unrelated failure this module
    never catches -- see this module's own docstring, negative_acceptance
    item 3."""


@dataclass(frozen=True)
class PrivateS3ConnectionConfig:
    """The real, explicit trust boundary a caller supplies for one
    resolution -- deliberately not a `TrainableArtifact` field nor a new
    database entity (see this module's own docstring, negative_acceptance
    item 2)."""

    endpoint: S3Endpoint
    credentials: S3Credentials
    expected_bucket: str
    tenant_key_prefix: str


@dataclass(frozen=True)
class PrivateS3ResolutionResult:
    bucket: str
    key: str
    version_id: str
    size_bytes: int
    etag_raw: str
    etag_looks_like_content_hash: bool
    sha256_hex: str


def default_private_s3_resolver_policy() -> ConnectionPolicy:
    """Production default: no redirects (a real path-style S3 `GetObject`
    to a fixed, already-known-good endpoint has no legitimate redirect to
    follow, unlike `FTF-0302`'s own public Hugging Face CDN case), and no
    private-address allowlist -- a real internal/private S3 endpoint's own
    policy must explicitly allowlist it via
    `explicit_private_allowlist`/`allowed_schemes` (Section 65.4), which
    this default deliberately leaves for the caller to supply rather than
    silently widening it here."""
    return ConnectionPolicy(name="private_s3_artifact_resolver")


def _parse_bucket_and_key(source_uri: str) -> tuple[str, str]:
    # Shape safety is reused from FTF-0301, never re-implemented here.
    validate_source_uri("private_s3", source_uri)
    parsed = urlsplit(source_uri)
    bucket = parsed.hostname or ""
    key = parsed.path.lstrip("/")
    return bucket, key


def _looks_like_plain_md5_etag(etag: str) -> bool:
    """True only for a plain 32-hex-character ETag -- the shape a
    single-part upload's own MD5-derived ETag actually has. A multipart
    upload's ETag (`"<hex>-<part_count>"`) is never true here. Disclosed,
    informational only -- see this module's own docstring, negative_
    acceptance item 4."""
    return len(etag) == 32 and all(c in "0123456789abcdef" for c in etag.lower())


def resolve_private_s3_object(
    source_uri: str,
    version_id: str,
    *,
    connection: PrivateS3ConnectionConfig,
    expected_size_bytes: int | None = None,
    policy: ConnectionPolicy | None = None,
    fetch: GuardedFetchFn = guarded_fetch,
    timeout: float = _DEFAULT_TIMEOUT_SECONDS,
) -> PrivateS3ResolutionResult:
    """Resolves and verifies exactly one pinned object version from a real
    S3-compatible endpoint. Raises one of this module's own error classes
    for every negative_acceptance case; never returns a partial or
    fabricated result."""
    bucket, key = _parse_bucket_and_key(source_uri)

    if bucket != connection.expected_bucket:
        raise BucketMismatchError(
            f"source_uri bucket {bucket!r} does not match this connection's own expected "
            f"bucket {connection.expected_bucket!r}"
        )
    if not key.startswith(connection.tenant_key_prefix):
        raise CrossTenantPrefixError(
            f"source_uri key {key!r} does not start with this connection's own tenant key "
            f"prefix {connection.tenant_key_prefix!r}"
        )
    if not version_id or not version_id.strip():
        raise ObjectVersionUnavailableError(
            "base_model_revision must name a real, non-blank S3 object versionId"
        )

    canonical_uri = f"/{bucket}/{key}"
    signed = sign_request(
        method="GET",
        endpoint=connection.endpoint,
        credentials=connection.credentials,
        canonical_uri=canonical_uri,
        query={"versionId": version_id},
    )

    active_policy = policy or default_private_s3_resolver_policy()
    try:
        response: GuardedResponse = fetch(
            signed.url, active_policy, method="GET", headers=signed.headers, timeout=timeout
        )
    except OutboundGuardError as exc:
        raise PrivateS3ResolutionError(
            f"could not reach the private S3 endpoint for {bucket}/{key}: {exc}"
        ) from exc

    if response.status == 404:
        raise ObjectVersionUnavailableError(
            f"{bucket}/{key} has no object at version {version_id!r}"
        )
    if response.status != 200:
        raise PrivateS3ResolutionError(
            f"unexpected HTTP {response.status} fetching {bucket}/{key}@{version_id}"
        )

    actual_size = len(response.body)
    if expected_size_bytes is not None and actual_size != expected_size_bytes:
        raise PartialUploadError(
            f"{bucket}/{key}@{version_id} was expected to be {expected_size_bytes} bytes but "
            f"{actual_size} bytes were actually received -- the upload may be incomplete"
        )

    # `GuardedResponse.headers` preserves the exact case a server sent
    # (FTF-H006's own `guarded_fetch` does not lowercase them) -- HTTP
    # header names are case-insensitive on the wire (RFC 7230 3.2), so a
    # real S3-compatible server sending the canonical "ETag" spelling
    # would silently miss a lowercase-only lookup. Lowercase once here,
    # the same defensive precedent `storage_semantics_client.py` (FTF-
    # R017) already established for this exact class of gap.
    headers_lower = {name.lower(): value for name, value in response.headers.items()}
    etag_raw = headers_lower.get("etag", "").strip('"')
    sha256_hex = hashlib.sha256(response.body).hexdigest()  # never derived from etag_raw

    return PrivateS3ResolutionResult(
        bucket=bucket,
        key=key,
        version_id=version_id,
        size_bytes=actual_size,
        etag_raw=etag_raw,
        etag_looks_like_content_hash=bool(etag_raw) and _looks_like_plain_md5_etag(etag_raw),
        sha256_hex=sha256_hex,
    )


def resolve_and_verify_private_s3_artifact(
    artifact: TrainableArtifact,
    *,
    connection: PrivateS3ConnectionConfig,
    expected_size_bytes: int | None = None,
    policy: ConnectionPolicy | None = None,
    fetch: GuardedFetchFn = guarded_fetch,
    timeout: float = _DEFAULT_TIMEOUT_SECONDS,
) -> PrivateS3ResolutionResult:
    """The approved-artifact entry point, mirroring
    `resolve_and_verify_huggingface_artifact`'s own shape (FTF-0302).
    Never writes `weights_digest` or any other field back to `artifact` or
    the database -- that composition is `FTF-0306`'s own separately
    dependency-gated job, the same disclosed boundary FTF-0302 already
    established for the identical reason."""
    if artifact.source_type != "private_s3":
        raise PrivateS3ResolutionError(
            f"artifact {artifact.id} has source_type {artifact.source_type!r}, not 'private_s3'"
        )
    return resolve_private_s3_object(
        artifact.source_uri,
        artifact.base_model_revision,
        connection=connection,
        expected_size_bytes=expected_size_bytes,
        policy=policy,
        fetch=fetch,
        timeout=timeout,
    )
