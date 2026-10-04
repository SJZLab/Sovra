"""S3/MinIO source validation + metadata preview (FTF-0500B, Section
7A.10/8/9/45/65/67/77/81).

Section 67.1's own connector table names the real behavior this task
implements for an `s3`/`minio` `DatasetSourceConnection` (FTF-0500,
CERTIFIED): "Paginated bounded list, exact version manifest, ETag not
treated as universal SHA-256." This module is that real caller -- given an
already-authorized connection's own resolved endpoint/credentials/bucket/
tenant prefix, it lists the real object versions under that prefix from a
real, SigV4-signed S3-compatible endpoint (AWS S3 or a real MinIO/SeaweedFS
S3-compatible server), and returns a bounded, safe *metadata* preview (key/
version/size/ETag/last-modified -- never file content) with an immutable
manifest hash. Section 8's own file-content import pipeline (parsing
JSONL/CSV rows, schema validation, PII scanning) is a materially separate,
not-yet-dependency-gated concern this task does not touch -- this module's
own suggested filename ("metadata_preview", not "content_preview" or
"import") already names that boundary.

Every real HTTP request this module makes goes through
`packages/security/outbound_guard.py`'s `guarded_fetch` (FTF-H006) --
`packages/security/url_policy.py`'s own module docstring already names
this exact task (`FTF-0500B`) as one of its intended future callers.
Request signing reuses `apps/api/app/services/storage_semantics_client.py`'s
own real AWS Signature Version 4 implementation (`sign_request`, FTF-R017,
CERTIFIED) rather than re-deriving SigV4 math a second time, the same
precedent `FTF-0303`'s own `private_s3_artifact_resolver.py` already
established -- this module likewise never uses that module's own
`S3Client` transport (which predates FTF-H006's shared-guard requirement).

This task's own negative_acceptance ("S3 ETag is not assumed SHA; source
changes, cross-tenant prefix and overly broad grants are denied") is
enforced as four independent, disclosed checks:

  1. **Overly broad grant** -- a connection whose own `tenant_key_prefix`
     is blank or resolves to bucket root (`""`/`"/"`) is refused before
     any network request: a scoped dataset-source connection must declare
     a real, non-root namespace inside its bucket, never unrestricted
     access to the entire bucket (`OverlyBroadGrantError`).
  2. **Cross-tenant prefix** -- every real key this module's own request
     actually receives back is independently re-checked against the
     connection's own `tenant_key_prefix` at a real `/` path-segment
     boundary (`_key_within_tenant_prefix`, the same boundary-safe logic
     `FTF-0303`'s own already-CERTIFIED `_key_within_tenant_prefix`
     established for the identical `tenant_key_prefix="org-4"` vs
     `key="org-42/..."` collision) -- never trusted merely because the
     request's own `prefix` query parameter was honored by a compliant
     server; a non-compliant or compromised S3/MinIO endpoint that returns
     a key outside the requested prefix is refused, not silently accepted
     (`CrossTenantPrefixError`).
  3. **Source changes during listing** -- Section 67.1's own
     "Changes during pagination cannot silently mix versions... if
     consistency cannot be guaranteed, show `SNAPSHOT_UNSTABLE` and block
     production data use" is made real: this module tracks, across every
     page of a paginated listing, the single `IsLatest` version id it has
     already seen for each real key; if a later page reports a
     *different* `IsLatest` version id for a key already recorded, the
     underlying source was mutated between two real page fetches of the
     same listing -- refused as `SourceChangedDuringListingError`, never
     silently resolved by keeping the first or the last answer.
  4. **ETag-as-hash assumption** -- this module never derives a content
     digest from `ETag` anywhere in its own code. `etag_looks_like_
     content_hash` (true only for a plain 32-hex-character ETag) is
     disclosed, informational metadata only, exactly mirroring
     `FTF-0303`'s own established discipline -- this task's own scope is a
     *metadata* preview (no object bytes are ever downloaded here), so
     there is no real downloaded-content digest to prefer over ETag in the
     first place; the point is that this module's own manifest never
     *claims* ETag is one.
  5. **Versioning disabled on the source bucket** -- a real S3/MinIO-
     compatible `ListObjectVersions` response reports the literal string
     `"null"` as `VersionId` for every entry of a bucket that never had
     object versioning enabled (and for any entry created while
     versioning was suspended). This task's own "exact version manifest"
     acceptance clause cannot be made real against a source that cannot
     actually distinguish object versions, so a real `"null"` VersionId
     anywhere in the listing is refused rather than silently accepted and
     hashed as if it pinned anything (`VersioningDisabledError`).

The returned `manifest_hash` (sha256 over the canonical JSON serialization
of the exact bounded `(key, version_id, size_bytes, etag_raw)` tuples
returned) is this task's own "immutable manifest" acceptance clause made
real -- a caller can detect whether a later re-preview of the same
connection/prefix returned an identical listing without re-comparing every
field by hand, the same "one hash covers the whole manifest" shape
Section 9's own split-manifest convention already established for
comparable content elsewhere in this specification.

`role`-based (STS) access (Section 67.1's own table also names "role" as
configuration alongside endpoint/region/bucket/prefix) is out of this
task's own scope: this module accepts already-resolved, static
`S3Credentials` (access key + secret key) exactly like `FTF-0303`'s own
established "caller resolves the secret, this module only ever sees
already-resolved credentials" shape -- a future task may add real STS
role-assumption without changing this module's own call contract, since it
would only change how `S3Credentials` gets constructed upstream, not what
this module does with them.

This module is a pure connector: it does not read or write
`DatasetSourceConnection` rows, resolve `credential_secret_ref`/
`connection_config_encrypted_ref` via OpenBao, or expose an HTTP route --
those are a separately dependency-gated composition task, the same
disclosed boundary `FTF-0303`'s own docstring already established for the
identical "resolver only, caller supplies already-resolved config" shape.
No new database field or entity is added here (`database_changes: "No
independent schema change"`).
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from xml.etree import ElementTree as ET

from packages.security.outbound_guard import GuardedResponse, OutboundGuardError, guarded_fetch
from packages.security.url_policy import ConnectionPolicy

from app.services.storage_semantics_client import S3Credentials, S3Endpoint, sign_request

__all__ = [
    "CrossTenantPrefixError",
    "OverlyBroadGrantError",
    "S3MinioSourceConfig",
    "S3MinioSourceValidationError",
    "S3ObjectVersionMetadata",
    "S3MinioSourceValidationResult",
    "SourceChangedDuringListingError",
    "SourceUnavailableError",
    "VersioningDisabledError",
    "default_s3_minio_preview_policy",
    "validate_and_preview_s3_minio_source",
]

# A real S3/MinIO `ListObjectVersions` response's own literal sentinel for
# "this bucket never had object versioning enabled" -- never a real version
# id.
_UNVERSIONED_SENTINEL = "null"

_DEFAULT_TIMEOUT_SECONDS = 30.0

# Section 67.2: "preview at most 20 records inside the authorized private
# boundary."
DEFAULT_MAX_PREVIEW_RECORDS = 20
# The real S3 ListObjectVersions API's own default/maximum `max-keys`.
DEFAULT_PAGE_SIZE = 1000

GuardedFetchFn = Callable[..., GuardedResponse]


class S3MinioSourceValidationError(ValueError):
    """Base class for every real defect this module raises validating/
    previewing an `s3`/`minio` dataset source -- never caught and silently
    converted to a fabricated success anywhere in this module."""


class OverlyBroadGrantError(S3MinioSourceValidationError):
    """The connection's own `tenant_key_prefix` is blank or resolves to
    bucket root -- refused before any network request is made."""


class CrossTenantPrefixError(S3MinioSourceValidationError):
    """A real key returned by the endpoint does not fall within the
    connection's own `tenant_key_prefix` at a real path-segment
    boundary."""


class SourceChangedDuringListingError(S3MinioSourceValidationError):
    """A real key's own `IsLatest` version id differs between two pages of
    the same paginated listing -- the source was mutated mid-listing."""


class SourceUnavailableError(S3MinioSourceValidationError):
    """The real endpoint could not be reached, or returned an unexpected
    (non-200) status or an unparsable response body."""


class VersioningDisabledError(S3MinioSourceValidationError):
    """A real entry in the listing reports the literal `"null"` VersionId
    -- object versioning was never enabled (or was suspended) for this
    source, so no real "exact version manifest" can be produced from it."""


@dataclass(frozen=True)
class S3MinioSourceConfig:
    """The real, explicit trust boundary a caller supplies for one
    validation/preview call -- mirrors `FTF-0303`'s own
    `PrivateS3ConnectionConfig` shape and, like it, is deliberately not a
    `DatasetSourceConnection` field nor a new database entity."""

    endpoint: S3Endpoint
    credentials: S3Credentials
    bucket: str
    tenant_key_prefix: str
    max_preview_records: int = DEFAULT_MAX_PREVIEW_RECORDS
    page_size: int = DEFAULT_PAGE_SIZE


@dataclass(frozen=True)
class S3ObjectVersionMetadata:
    key: str
    version_id: str
    size_bytes: int
    etag_raw: str
    etag_looks_like_content_hash: bool
    last_modified: str
    is_latest: bool
    is_delete_marker: bool


@dataclass(frozen=True)
class S3MinioSourceValidationResult:
    bucket: str
    tenant_key_prefix: str
    objects: tuple[S3ObjectVersionMetadata, ...]
    truncated: bool
    manifest_hash: str
    captured_at: str


def default_s3_minio_preview_policy() -> ConnectionPolicy:
    """Production default: no redirects (a real path-style
    `ListObjectVersions` call to a fixed, already-known-good endpoint has
    no legitimate redirect to follow), and no private-address allowlist --
    a real internal/private S3/MinIO endpoint's own policy must explicitly
    allowlist it via `explicit_private_allowlist`, the same deliberate
    "caller supplies it" precedent `FTF-0303`'s own
    `default_private_s3_resolver_policy` already established."""
    return ConnectionPolicy(name="s3_minio_source_validation_metadata_preview")


def _looks_like_plain_md5_etag(etag: str) -> bool:
    """True only for a plain 32-hex-character ETag -- see this module's
    own docstring, negative_acceptance item 4. Disclosed, informational
    only; never consulted by this module's own validation path."""
    return len(etag) == 32 and all(c in "0123456789abcdef" for c in etag.lower())


def _key_within_tenant_prefix(key: str, tenant_key_prefix: str) -> bool:
    """Path-segment-aware containment, not a bare `str.startswith` -- see
    this module's own docstring, negative_acceptance item 2. Identical
    logic to `FTF-0303`'s own already-CERTIFIED `_key_within_tenant_prefix`
    (re-implemented here, not imported, since this task's own write set is
    a separate module with its own independent test coverage)."""
    if key == tenant_key_prefix:
        return True
    boundary = tenant_key_prefix if tenant_key_prefix.endswith("/") else tenant_key_prefix + "/"
    return key.startswith(boundary)


def _local_tag(tag: str) -> str:
    return tag.split("}", 1)[1] if "}" in tag else tag


def _parse_list_versions_xml(
    body: bytes,
) -> tuple[list[dict[str, object]], bool, str | None, str | None]:
    """Parses a real `ListObjectVersions` XML response into a list of
    entry dicts (each a `Version` or `DeleteMarker`, in real document
    order) plus the real `IsTruncated`/`NextKeyMarker`/
    `NextVersionIdMarker` pagination fields. A narrow, purpose-built parser
    (not a generic XML-to-dict transform) -- this task's own contract
    needs exactly these fields, not a general S3 SDK (Section 84.4:
    "implement the smallest coherent change")."""
    try:
        root = ET.fromstring(body)  # noqa: S314
    except ET.ParseError as exc:
        raise SourceUnavailableError(
            f"the S3/MinIO endpoint returned an unparsable ListObjectVersions response: {exc}"
        ) from exc

    entries: list[dict[str, object]] = []
    is_truncated = False
    next_key_marker: str | None = None
    next_version_id_marker: str | None = None

    for child in root:
        name = _local_tag(child.tag)
        if name == "IsTruncated":
            is_truncated = (child.text or "").strip().lower() == "true"
        elif name == "NextKeyMarker":
            next_key_marker = child.text
        elif name == "NextVersionIdMarker":
            next_version_id_marker = child.text
        elif name in ("Version", "DeleteMarker"):
            fields: dict[str, str] = {}
            for sub in child:
                fields[_local_tag(sub.tag)] = sub.text or ""
            size_text = fields.get("Size", "0") or "0"
            entries.append(
                {
                    "key": fields.get("Key", ""),
                    "version_id": fields.get("VersionId", ""),
                    "is_latest": fields.get("IsLatest", "").strip().lower() == "true",
                    "last_modified": fields.get("LastModified", ""),
                    "etag": fields.get("ETag", "").strip('"'),
                    "size": int(size_text),
                    "is_delete_marker": name == "DeleteMarker",
                }
            )
    return entries, is_truncated, next_key_marker, next_version_id_marker


def validate_and_preview_s3_minio_source(
    config: S3MinioSourceConfig,
    *,
    policy: ConnectionPolicy | None = None,
    fetch: GuardedFetchFn = guarded_fetch,
    timeout: float = _DEFAULT_TIMEOUT_SECONDS,
) -> S3MinioSourceValidationResult:
    """Validates and previews exactly one real, versioned S3/MinIO source
    scoped to `config.tenant_key_prefix`. Raises one of this module's own
    error classes for every negative_acceptance case; never returns a
    partial or fabricated result."""
    if not isinstance(config.bucket, str) or not config.bucket:
        raise S3MinioSourceValidationError("bucket must be a non-empty string")

    prefix = config.tenant_key_prefix
    if not isinstance(prefix, str) or not prefix.strip("/"):
        raise OverlyBroadGrantError(
            f"tenant_key_prefix {prefix!r} must be a real, non-empty, non-root scoped "
            "prefix -- an empty or root prefix would grant this connection access to the "
            "entire bucket, not a tenant-scoped namespace within it"
        )

    active_policy = policy or default_s3_minio_preview_policy()
    collected: list[S3ObjectVersionMetadata] = []
    latest_version_by_key: dict[str, str] = {}
    total_examined = 0
    key_marker = ""
    version_id_marker = ""
    is_truncated = False

    while True:
        query: dict[str, str] = {
            "versions": "",
            "prefix": prefix,
            "max-keys": str(config.page_size),
        }
        if key_marker:
            query["key-marker"] = key_marker
        if version_id_marker:
            query["version-id-marker"] = version_id_marker

        signed = sign_request(
            method="GET",
            endpoint=config.endpoint,
            credentials=config.credentials,
            canonical_uri=f"/{config.bucket}",
            query=query,
        )
        try:
            response: GuardedResponse = fetch(
                signed.url, active_policy, method="GET", headers=signed.headers, timeout=timeout
            )
        except OutboundGuardError as exc:
            raise SourceUnavailableError(
                f"could not reach the S3/MinIO endpoint for bucket {config.bucket!r}: {exc}"
            ) from exc
        if response.status != 200:
            raise SourceUnavailableError(
                f"unexpected HTTP {response.status} listing bucket {config.bucket!r} "
                f"(prefix={prefix!r})"
            )

        entries, is_truncated, next_key_marker, next_version_id_marker = _parse_list_versions_xml(
            response.body
        )

        for entry in entries:
            total_examined += 1
            key = entry["key"]
            assert isinstance(key, str)
            if not _key_within_tenant_prefix(key, prefix):
                raise CrossTenantPrefixError(
                    f"the real listing returned key {key!r}, which is outside this "
                    f"connection's own tenant_key_prefix {prefix!r} at a real path-segment "
                    "boundary -- refused rather than trusted from the response"
                )
            version_id = entry["version_id"]
            assert isinstance(version_id, str)
            if version_id == _UNVERSIONED_SENTINEL:
                raise VersioningDisabledError(
                    f"key {key!r} reports the real literal VersionId {_UNVERSIONED_SENTINEL!r} "
                    "-- object versioning is disabled (or suspended) on this source, so no "
                    "exact version manifest can be produced from it"
                )
            if entry["is_latest"] and not entry["is_delete_marker"]:
                previously_seen = latest_version_by_key.get(key)
                if previously_seen is not None and previously_seen != version_id:
                    raise SourceChangedDuringListingError(
                        f"key {key!r} was already listed as latest version "
                        f"{previously_seen!r} on an earlier page of this same listing, but "
                        f"a later page reports a different latest version {version_id!r} -- "
                        "the source changed mid-listing (SNAPSHOT_UNSTABLE); refusing to "
                        "silently mix versions across pages"
                    )
                latest_version_by_key[key] = version_id

            if len(collected) < config.max_preview_records:
                etag = entry["etag"]
                assert isinstance(etag, str)
                size_bytes = entry["size"]
                assert isinstance(size_bytes, int)
                last_modified = entry["last_modified"]
                assert isinstance(last_modified, str)
                collected.append(
                    S3ObjectVersionMetadata(
                        key=key,
                        version_id=version_id,
                        size_bytes=size_bytes,
                        etag_raw=etag,
                        etag_looks_like_content_hash=bool(etag)
                        and _looks_like_plain_md5_etag(etag),
                        last_modified=last_modified,
                        is_latest=bool(entry["is_latest"]),
                        is_delete_marker=bool(entry["is_delete_marker"]),
                    )
                )

        if len(collected) >= config.max_preview_records:
            break
        if not is_truncated:
            break
        key_marker = next_key_marker or ""
        version_id_marker = next_version_id_marker or ""

    truncated = is_truncated or total_examined > len(collected)

    manifest_source = [
        {
            "key": entry.key,
            "version_id": entry.version_id,
            "size_bytes": entry.size_bytes,
            "etag_raw": entry.etag_raw,
        }
        for entry in collected
    ]
    manifest_hash = hashlib.sha256(
        json.dumps(manifest_source, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()

    return S3MinioSourceValidationResult(
        bucket=config.bucket,
        tenant_key_prefix=prefix,
        objects=tuple(collected),
        truncated=truncated,
        manifest_hash=manifest_hash,
        captured_at=datetime.now(UTC).isoformat(),
    )
