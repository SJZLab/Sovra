"""Real S3-compatible storage client for FTF-R017 storage-semantics
qualification.

Section 80.4 requires qualifying "the organization's maintained S3 service
or the selected SeaweedFS-based offline fixture against the actual
multipart, checksum, versioning, immutable-publication, identity,
retention/deletion, encryption, backup and restore contract." Proving that
requires a real, signed S3 API client, not a canned stub -- boto3 (or any
S3 SDK) is a new runtime dependency CLAUDE.md gates ask-first, so this is a
stdlib-only implementation of AWS Signature Version 4 request signing
(`hashlib`/`hmac`/`urllib`/`datetime`/`base64` only), targeted narrowly at
the operations `tests/integration/test_storage_semantics.py` actually
exercises -- not a general-purpose S3 SDK.

The client is endpoint-configurable (`S3Endpoint`/`S3Credentials` are
plain constructor arguments, never a hardcoded host) so the exact same
code path qualifies either the local SeaweedFS fixture or the
organization's real external S3 endpoint later, without a migration --
satisfying this task's own acceptance clause 2 ("maintain compatible old
external S3 source connection without silent migration").
"""

from __future__ import annotations

import hashlib
import hmac
import urllib.error
import urllib.request
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from urllib.parse import quote
from xml.etree import ElementTree as ET

_ALGORITHM = "AWS4-HMAC-SHA256"
_AWS4_REQUEST = "aws4_request"
_SERVICE = "s3"


class S3Error(Exception):
    """A real, non-2xx S3 API response -- never silently swallowed."""

    def __init__(self, status_code: int, code: str, message: str, body: str = "") -> None:
        self.status_code = status_code
        self.code = code
        self.message = message
        self.body = body
        super().__init__(f"S3Error({status_code}, {code!r}, {message!r})")


@dataclass(frozen=True)
class S3Credentials:
    access_key: str
    secret_key: str


@dataclass(frozen=True)
class S3Endpoint:
    """Path-style S3 endpoint. Never hardcoded to one host in the client
    itself -- callers (config/env, tests) supply the real target, whether
    that is the local SeaweedFS fixture or an organization's external S3."""

    scheme: str
    host: str
    port: int
    region: str = "us-east-1"

    @property
    def base_url(self) -> str:
        return f"{self.scheme}://{self.host}:{self.port}"

    @property
    def host_header(self) -> str:
        default_port = {"http": 80, "https": 443}.get(self.scheme)
        if self.port == default_port:
            return self.host
        return f"{self.host}:{self.port}"


def _sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _hmac_digest(key: bytes, msg: str) -> bytes:
    return hmac.new(key, msg.encode("utf-8"), hashlib.sha256).digest()


def _signing_key(secret_key: str, date_stamp: str, region: str, service: str) -> bytes:
    k_date = _hmac_digest(("AWS4" + secret_key).encode("utf-8"), date_stamp)
    k_region = _hmac_digest(k_date, region)
    k_service = _hmac_digest(k_region, service)
    return _hmac_digest(k_service, _AWS4_REQUEST)


def _uri_encode_path(path: str) -> str:
    return "/".join(quote(segment, safe="") for segment in path.split("/"))


def _canonical_query_string(query: Mapping[str, str]) -> str:
    if not query:
        return ""
    items = sorted(query.items())
    return "&".join(f"{quote(k, safe='')}={quote(v, safe='')}" for k, v in items)


@dataclass(frozen=True)
class SignedRequest:
    method: str
    url: str
    headers: dict[str, str]
    body: bytes


def sign_request(
    *,
    method: str,
    endpoint: S3Endpoint,
    credentials: S3Credentials,
    canonical_uri: str,
    query: Mapping[str, str] | None = None,
    headers: Mapping[str, str] | None = None,
    body: bytes = b"",
    now: datetime | None = None,
    service: str = _SERVICE,
) -> SignedRequest:
    """AWS Signature Version 4, implemented directly against the published
    algorithm (canonical request -> string to sign -> derived signing key
    -> signature), not delegated to any SDK. `now` is injectable for
    deterministic unit tests; production callers never pass it."""
    query = dict(query or {})
    moment = now if now is not None else datetime.now(UTC)
    amz_date = moment.strftime("%Y%m%dT%H%M%SZ")
    date_stamp = moment.strftime("%Y%m%d")

    payload_hash = _sha256_hex(body)

    all_headers: dict[str, str] = {k.lower(): v.strip() for k, v in (headers or {}).items()}
    all_headers["host"] = endpoint.host_header
    all_headers["x-amz-date"] = amz_date
    all_headers["x-amz-content-sha256"] = payload_hash

    signed_header_names = sorted(all_headers.keys())
    canonical_headers = "".join(f"{name}:{all_headers[name]}\n" for name in signed_header_names)
    signed_headers = ";".join(signed_header_names)
    canonical_uri_encoded = _uri_encode_path(canonical_uri) or "/"

    canonical_request = "\n".join(
        [
            method.upper(),
            canonical_uri_encoded,
            _canonical_query_string(query),
            canonical_headers,
            signed_headers,
            payload_hash,
        ]
    )

    credential_scope = f"{date_stamp}/{endpoint.region}/{service}/{_AWS4_REQUEST}"
    string_to_sign = "\n".join(
        [
            _ALGORITHM,
            amz_date,
            credential_scope,
            _sha256_hex(canonical_request.encode("utf-8")),
        ]
    )

    signing_key = _signing_key(credentials.secret_key, date_stamp, endpoint.region, service)
    signature = hmac.new(signing_key, string_to_sign.encode("utf-8"), hashlib.sha256).hexdigest()

    authorization = (
        f"{_ALGORITHM} Credential={credentials.access_key}/{credential_scope}, "
        f"SignedHeaders={signed_headers}, Signature={signature}"
    )

    final_headers = dict(all_headers)
    final_headers["authorization"] = authorization

    query_string = _canonical_query_string(query)
    url = f"{endpoint.base_url}{canonical_uri}"
    if query_string:
        url += f"?{query_string}"

    return SignedRequest(method=method.upper(), url=url, headers=final_headers, body=body)


PRESIGN_MAX_EXPIRES_SECONDS = 300  # Section 65.2: a pre-signed grant lives <= 5 minutes.
_UNSIGNED_PAYLOAD = "UNSIGNED-PAYLOAD"


def presign_url(
    *,
    method: str,
    endpoint: S3Endpoint,
    credentials: S3Credentials,
    canonical_uri: str,
    expires_seconds: int,
    query: Mapping[str, str] | None = None,
    signed_headers: Mapping[str, str] | None = None,
    now: datetime | None = None,
    max_expires_seconds: int = PRESIGN_MAX_EXPIRES_SECONDS,
    service: str = _SERVICE,
) -> str:
    """AWS Signature Version 4 *query-string* presigned URL (the published
    "Authenticating Requests: Using Query Parameters" algorithm). The method,
    path, query and every header in `signed_headers` are bound into the
    signature, so the holder cannot change the object key, the part number
    or the signed `content-length`. The payload is `UNSIGNED-PAYLOAD`: the
    object's digest is verified by the server after upload, not by the URL.

    `expires_seconds` must be 1..`max_expires_seconds`; the default ceiling is
    five minutes (Section 65.2) and a caller can only lower it in practice --
    raising the ceiling is only for reproducing the AWS documentation vector.
    """
    if (
        isinstance(expires_seconds, bool)
        or not isinstance(expires_seconds, int)
        or isinstance(max_expires_seconds, bool)
        or not isinstance(max_expires_seconds, int)
        or max_expires_seconds > 604800
        or not 0 < expires_seconds <= max_expires_seconds
    ):
        raise ValueError(f"expires_seconds must be an int in 1..{max_expires_seconds}")
    moment = now if now is not None else datetime.now(UTC)
    amz_date = moment.strftime("%Y%m%dT%H%M%SZ")
    date_stamp = moment.strftime("%Y%m%d")
    credential_scope = f"{date_stamp}/{endpoint.region}/{service}/{_AWS4_REQUEST}"

    headers: dict[str, str] = {k.lower(): v.strip() for k, v in (signed_headers or {}).items()}
    headers["host"] = endpoint.host_header
    header_names = sorted(headers)
    signed_header_list = ";".join(header_names)

    full_query = dict(query or {})
    full_query.update(
        {
            "X-Amz-Algorithm": _ALGORITHM,
            "X-Amz-Credential": f"{credentials.access_key}/{credential_scope}",
            "X-Amz-Date": amz_date,
            "X-Amz-Expires": str(expires_seconds),
            "X-Amz-SignedHeaders": signed_header_list,
        }
    )
    canonical_query = _canonical_query_string(full_query)
    canonical_headers = "".join(f"{name}:{headers[name]}\n" for name in header_names)
    canonical_request = "\n".join(
        [
            method.upper(),
            _uri_encode_path(canonical_uri) or "/",
            canonical_query,
            canonical_headers,
            signed_header_list,
            _UNSIGNED_PAYLOAD,
        ]
    )
    string_to_sign = "\n".join(
        [
            _ALGORITHM,
            amz_date,
            credential_scope,
            _sha256_hex(canonical_request.encode("utf-8")),
        ]
    )
    signing_key = _signing_key(credentials.secret_key, date_stamp, endpoint.region, service)
    signature = hmac.new(signing_key, string_to_sign.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"{endpoint.base_url}{canonical_uri}?{canonical_query}&X-Amz-Signature={signature}"


def _strip_ns(tag: str) -> str:
    return tag.split("}", 1)[1] if "}" in tag else tag


def _xml_to_dict(element: ET.Element) -> Any:
    children = list(element)
    if not children:
        return element.text or ""
    result: dict[str, Any] = {}
    for child in children:
        name = _strip_ns(child.tag)
        value = _xml_to_dict(child)
        if name in result:
            existing = result[name]
            if isinstance(existing, list):
                existing.append(value)
            else:
                result[name] = [existing, value]
        else:
            result[name] = value
    return result


class S3Client:
    """Minimal, real SigV4-signed S3-compatible client -- exactly the
    operations FTF-R017's storage-semantics evidence needs, not a general
    SDK (Section 84.4: "implement the smallest coherent change")."""

    def __init__(
        self,
        endpoint: S3Endpoint,
        credentials: S3Credentials,
        timeout_seconds: float = 10.0,
        *,
        debug: bool = False,
    ) -> None:
        self._endpoint = endpoint
        self._credentials = credentials
        self._timeout = timeout_seconds
        # FTF-R017 diagnosis aid only (default off): this task's CI hit a
        # real, unexplained "9/9 fail with S3-level 500/501" result where
        # every test's own exception is caught generically at the test-loop
        # level (tests/integration/test_storage_semantics.py's `main()`),
        # which cannot tell which specific S3 operation inside a given test
        # actually failed. Never prints credentials/signature material --
        # only the request shape (method/path/query-keys/header-names/body
        # length) and the response status, which is exactly what's needed
        # to pin an InternalError/NotImplemented to one real call.
        self._debug = debug

    def _request(
        self,
        method: str,
        bucket: str,
        key: str = "",
        *,
        query: Mapping[str, str] | None = None,
        headers: Mapping[str, str] | None = None,
        body: bytes = b"",
    ) -> tuple[int, dict[str, str], bytes]:
        canonical_uri = f"/{bucket}/{key}" if key else f"/{bucket}"
        if self._debug:
            print(
                f"S3 request: {method} {canonical_uri} "
                f"query={sorted((query or {}).keys())} "
                f"headers={sorted((headers or {}).keys())} "
                f"body_len={len(body)}",
                flush=True,
            )
        signed = sign_request(
            method=method,
            endpoint=self._endpoint,
            credentials=self._credentials,
            canonical_uri=canonical_uri,
            query=query,
            headers=headers,
            body=body,
        )
        request = urllib.request.Request(  # noqa: S310
            signed.url, data=body if body else None, headers=signed.headers, method=method
        )
        try:
            with urllib.request.urlopen(request, timeout=self._timeout) as resp:  # noqa: S310
                response_body = resp.read()
                if self._debug:
                    print(f"S3 response: {resp.status} (ok)", flush=True)
                # HTTP header names are case-insensitive (RFC 7230 3.2), but
                # `resp.headers.items()` preserves the exact case the server
                # sent. SeaweedFS's Go S3 gateway sends most headers through
                # `Header().Set(...)`, which canonicalizes to Title-Case on
                # the wire (e.g. "x-amz-version-id" -> "X-Amz-Version-Id"),
                # while a couple (ETag) are written directly into the header
                # map and sent verbatim. A plain-dict, exact-case lookup
                # silently misses the canonicalized ones -- lowercase every
                # key here once so every caller can look up a stable,
                # case-insensitive key instead of guessing wire casing.
                return (
                    resp.status,
                    {name.lower(): value for name, value in resp.headers.items()},
                    response_body,
                )
        except urllib.error.HTTPError as exc:
            response_body = exc.read()
            code = "Unknown"
            message = response_body.decode("utf-8", errors="replace")
            try:
                root = ET.fromstring(response_body)  # noqa: S314
                parsed = _xml_to_dict(root)
                if isinstance(parsed, dict):
                    code = parsed.get("Code", code)
                    message = parsed.get("Message", message)
            except ET.ParseError:
                pass
            if self._debug:
                print(f"S3 response: {exc.code} {code} {message!r}", flush=True)
            raise S3Error(exc.code, code, message, message) from exc

    # -- Buckets ----------------------------------------------------------

    def create_bucket(self, bucket: str, *, object_lock_enabled: bool = False) -> None:
        headers = {}
        if object_lock_enabled:
            headers["x-amz-bucket-object-lock-enabled"] = "true"
        self._request("PUT", bucket, headers=headers)

    def put_bucket_versioning(self, bucket: str, *, enabled: bool) -> None:
        status = "Enabled" if enabled else "Suspended"
        body = (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<VersioningConfiguration xmlns="http://s3.amazonaws.com/doc/2006-03-01/">'
            f"<Status>{status}</Status></VersioningConfiguration>"
        ).encode()
        self._request("PUT", bucket, query={"versioning": ""}, body=body)

    def get_bucket_versioning(self, bucket: str) -> str:
        _, _, body = self._request("GET", bucket, query={"versioning": ""})
        root = ET.fromstring(body)  # noqa: S314
        parsed = _xml_to_dict(root)
        return parsed.get("Status", "") if isinstance(parsed, dict) else ""

    def list_object_versions(self, bucket: str, *, prefix: str = "") -> list[dict]:
        query = {"versions": ""}
        if prefix:
            query["prefix"] = prefix
        _, _, body = self._request("GET", bucket, query=query)
        root = ET.fromstring(body)  # noqa: S314
        parsed = _xml_to_dict(root)
        if not isinstance(parsed, dict):
            return []
        versions = parsed.get("Version", [])
        if isinstance(versions, dict):
            versions = [versions]
        markers = parsed.get("DeleteMarker", [])
        if isinstance(markers, dict):
            markers = [markers]
        for marker in markers:
            marker["IsDeleteMarker"] = True
        return [*versions, *markers]

    # -- Objects ------------------------------------------------------------

    def put_object(
        self,
        bucket: str,
        key: str,
        body: bytes,
        *,
        sse_c_headers: Mapping[str, str] | None = None,
        retention_headers: Mapping[str, str] | None = None,
    ) -> dict[str, str]:
        headers: dict[str, str] = {}
        headers.update(sse_c_headers or {})
        headers.update(retention_headers or {})
        status, response_headers, _ = self._request("PUT", bucket, key, headers=headers, body=body)
        return {
            "etag": response_headers.get("etag", "").strip('"'),
            "version_id": response_headers.get("x-amz-version-id", ""),
            "status": str(status),
        }

    def get_object(
        self,
        bucket: str,
        key: str,
        *,
        version_id: str | None = None,
        sse_c_headers: Mapping[str, str] | None = None,
    ) -> tuple[bytes, dict[str, str]]:
        query = {"versionId": version_id} if version_id else None
        _, headers, body = self._request(
            "GET", bucket, key, query=query, headers=sse_c_headers or {}
        )
        return body, headers

    def delete_object(
        self, bucket: str, key: str, *, version_id: str | None = None
    ) -> dict[str, str]:
        query = {"versionId": version_id} if version_id else None
        status, headers, _ = self._request("DELETE", bucket, key, query=query)
        return {
            "delete_marker": headers.get("x-amz-delete-marker", "false"),
            "version_id": headers.get("x-amz-version-id", ""),
            "status": str(status),
        }

    def put_object_retention(
        self, bucket: str, key: str, *, mode: str, retain_until_date: str, bypass: bool = False
    ) -> None:
        body = (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<Retention xmlns="http://s3.amazonaws.com/doc/2006-03-01/">'
            f"<Mode>{mode}</Mode><RetainUntilDate>{retain_until_date}</RetainUntilDate>"
            "</Retention>"
        ).encode()
        headers = {}
        if bypass:
            headers["x-amz-bypass-governance-retention"] = "true"
        self._request("PUT", bucket, key, query={"retention": ""}, headers=headers, body=body)

    def get_object_retention(self, bucket: str, key: str) -> dict[str, str]:
        _, _, body = self._request("GET", bucket, key, query={"retention": ""})
        root = ET.fromstring(body)  # noqa: S314
        parsed = _xml_to_dict(root)
        return parsed if isinstance(parsed, dict) else {}

    # -- Multipart upload -----------------------------------------------------

    def create_multipart_upload(
        self, bucket: str, key: str, *, content_type: str | None = None
    ) -> str:
        headers = {"content-type": content_type} if content_type else None
        _, _, body = self._request("POST", bucket, key, query={"uploads": ""}, headers=headers)
        root = ET.fromstring(body)  # noqa: S314
        parsed = _xml_to_dict(root)
        if not isinstance(parsed, dict) or "UploadId" not in parsed:
            raise S3Error(0, "MalformedResponse", "no UploadId in InitiateMultipartUpload result")
        return parsed["UploadId"]

    def upload_part(
        self, bucket: str, key: str, upload_id: str, part_number: int, body: bytes
    ) -> str:
        _, headers, _ = self._request(
            "PUT",
            bucket,
            key,
            query={"partNumber": str(part_number), "uploadId": upload_id},
            body=body,
        )
        return headers.get("etag", "").strip('"')

    def complete_multipart_upload(
        self, bucket: str, key: str, upload_id: str, parts: list[tuple[int, str]]
    ) -> str:
        parts_xml = "".join(
            f"<Part><PartNumber>{number}</PartNumber><ETag>{etag}</ETag></Part>"
            for number, etag in parts
        )
        body = (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<CompleteMultipartUpload xmlns="http://s3.amazonaws.com/doc/2006-03-01/">'
            f"{parts_xml}</CompleteMultipartUpload>"
        ).encode()
        _, _, response_body = self._request(
            "POST", bucket, key, query={"uploadId": upload_id}, body=body
        )
        root = ET.fromstring(response_body)  # noqa: S314
        parsed = _xml_to_dict(root)
        return parsed.get("ETag", "").strip('"') if isinstance(parsed, dict) else ""

    def abort_multipart_upload(self, bucket: str, key: str, upload_id: str) -> None:
        self._request("DELETE", bucket, key, query={"uploadId": upload_id})

    def head_object(self, bucket: str, key: str) -> dict[str, str]:
        """Lower-cased response headers of a HEAD (`content-length`,
        `content-type`, `etag`, ...). A missing object raises `S3Error` 404."""
        _, headers, _ = self._request("HEAD", bucket, key)
        return headers

    def get_object_range(self, bucket: str, key: str, first: int, last: int) -> bytes:
        """The inclusive byte range `first..last` of an object."""
        if not 0 <= first <= last:
            raise ValueError("require 0 <= first <= last")
        _, _, body = self._request("GET", bucket, key, headers={"range": f"bytes={first}-{last}"})
        return body

    def stream_object(self, bucket: str, key: str, *, chunk_size: int = 1024 * 1024):
        """Yields the object's bytes in `chunk_size` pieces without holding the
        whole object in memory (uploads can be 10 GiB)."""
        if chunk_size <= 0:
            raise ValueError("chunk_size must be positive")
        signed = sign_request(
            method="GET",
            endpoint=self._endpoint,
            credentials=self._credentials,
            canonical_uri=f"/{bucket}/{key}",
        )
        request = urllib.request.Request(  # noqa: S310
            signed.url, headers=signed.headers, method="GET"
        )
        try:
            with urllib.request.urlopen(request, timeout=self._timeout) as resp:  # noqa: S310
                while True:
                    chunk = resp.read(chunk_size)
                    if not chunk:
                        return
                    yield chunk
        except urllib.error.HTTPError as exc:
            raise S3Error(exc.code, "Unknown", exc.reason or "", "") from exc

    def list_multipart_uploads(self, bucket: str, *, prefix: str) -> list[dict[str, str]]:
        """In-progress multipart uploads whose key starts with `prefix`."""
        _, _, body = self._request(
            "GET", bucket, query={"uploads": "", "prefix": prefix, "max-uploads": "1000"}
        )
        parsed = _xml_to_dict(ET.fromstring(body))  # noqa: S314
        if not isinstance(parsed, dict):
            return []
        uploads = parsed.get("Upload", [])
        if isinstance(uploads, dict):
            uploads = [uploads]
        return [u for u in uploads if isinstance(u, dict)]

    def list_multipart_uploads_page(
        self,
        bucket: str,
        *,
        prefix: str,
        key_marker: str = "",
        upload_id_marker: str = "",
        max_uploads: int = 1000,
    ) -> tuple[list[dict[str, str]], tuple[str, str] | None]:
        """One page of in-progress uploads and the (key, upload id) markers of
        the next page, or `None` when the listing is complete. A server that
        says the listing is truncated without advancing the markers is refused
        (it would page forever)."""
        query = {"uploads": "", "prefix": prefix, "max-uploads": str(max_uploads)}
        if key_marker:
            query["key-marker"] = key_marker
        if upload_id_marker:
            query["upload-id-marker"] = upload_id_marker
        _, _, body = self._request("GET", bucket, query=query)
        parsed = _xml_to_dict(ET.fromstring(body))  # noqa: S314
        if not isinstance(parsed, dict):
            return [], None
        uploads = parsed.get("Upload", [])
        if isinstance(uploads, dict):
            uploads = [uploads]
        page = [u for u in uploads if isinstance(u, dict)]
        if str(parsed.get("IsTruncated", "false")).lower() != "true":
            return page, None
        next_key = parsed.get("NextKeyMarker")
        next_upload = parsed.get("NextUploadIdMarker")
        if isinstance(next_key, str) and next_key and isinstance(next_upload, str):
            pass  # AWS S3: the pair names the position
        elif not next_key and isinstance(next_upload, str) and next_upload:
            # SeaweedFS 3.99 lists in upload-id order and pages by `upload-id-marker` alone (it
            # answers a key-marker with an empty page): carry the upload id and no key.
            next_key = ""
        else:
            # Neither style: S3's rule is that the next marker is the last entry returned.
            last = page[-1] if page else {}
            next_key, next_upload = last.get("Key"), last.get("UploadId")
            if not (isinstance(next_key, str) and next_key and isinstance(next_upload, str)):
                raise S3Error(0, "MalformedResponse", "ListMultipartUploads gave no usable markers")
        if (next_key, next_upload) == (key_marker, upload_id_marker):
            raise S3Error(
                0,
                "MalformedResponse",
                f"ListMultipartUploads did not advance (markers {next_key!r}/{next_upload!r})",
            )
        return page, (next_key, next_upload)

    def list_parts(self, bucket: str, key: str, upload_id: str) -> list[dict[str, str]]:
        """Every part uploaded so far (paged through `part-number-marker`)."""
        parts: list[dict[str, str]] = []
        marker = 0
        while True:
            _, _, body = self._request(
                "GET",
                bucket,
                key,
                query={
                    "uploadId": upload_id,
                    "part-number-marker": str(marker),
                    "max-parts": "1000",
                },
            )
            parsed = _xml_to_dict(ET.fromstring(body))  # noqa: S314
            if not isinstance(parsed, dict):
                return parts
            page = parsed.get("Part", [])
            if isinstance(page, dict):
                page = [page]
            parts.extend(p for p in page if isinstance(p, dict))
            if str(parsed.get("IsTruncated", "false")).lower() != "true":
                return parts
            # The marker is a part number: it must strictly advance, or a storage
            # server that repeats (or rewinds) it would keep this loop paging forever.
            try:
                next_marker = int(str(parsed.get("NextPartNumberMarker", "")))
            except ValueError as exc:
                raise S3Error(0, "MalformedResponse", "ListParts gave no usable marker") from exc
            if next_marker <= marker:
                raise S3Error(0, "MalformedResponse", "ListParts did not advance")
            marker = next_marker
