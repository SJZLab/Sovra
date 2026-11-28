"""The real S3-compatible object-store probe for NVIDIA environment validation (FTF-0705).

`S3RoundTripProbe.round_trip` writes a small probe object, reads it back, checks it is
byte-identical, deletes it and confirms the delete with a HEAD that must answer 404. Every failure
becomes one stable `ObjectStoreFailure` code; nothing is retried into a success (Law 17):

- `object_store_denied`            the store refused the caller's identity (401/403);
- `object_store_bucket_missing`    the bucket does not exist (404 `NoSuchBucket`);
- `object_store_unreachable`       no HTTP answer (connection refused, DNS, timeout, reset);
- `object_store_readback_mismatch` the object read back differs from what was written;
- `object_store_delete_unverified` the probe object is still present after its delete;
- `object_store_error`             any other S3 error response.

`S3ProbeClient` is a deliberately tiny, stdlib-only AWS Signature Version 4 client (PUT, GET,
DELETE, HEAD of one object, path-style). The worker does not import the API package, and an S3 SDK
would be a new runtime dependency (CLAUDE.md ask-first), so it follows the same published
algorithm as the certified `apps/api/app/services/storage_semantics_client.py` (FTF-R017);
tests/tasks/test_ftf_0705.py pins that both produce the identical signed request.

`probe_from_environment` builds the probe from the `SOVRA_STORAGE_S3_*` variables the storage
profile names (infra/internal/storage-profile.yaml) plus `SOVRA_TRAINING_OBJECT_STORE_BUCKET`. A
missing or malformed variable returns None, which the validator reports as
`object_store_unconfigured`; there are no built-in defaults on this path.
"""

from __future__ import annotations

import hashlib
import hmac
import http.client
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from urllib.parse import quote
from xml.etree import ElementTree as ET

from workers.training.nvidia_environment_validation import ObjectStoreFailure

__all__ = [
    "ENV_VARS",
    "S3ProbeClient",
    "S3ProbeError",
    "S3RoundTripProbe",
    "probe_from_environment",
]

ENV_VARS: Mapping[str, str] = {
    "scheme": "SOVRA_STORAGE_S3_SCHEME",
    "host": "SOVRA_STORAGE_S3_HOST",
    "port": "SOVRA_STORAGE_S3_PORT",
    "region": "SOVRA_STORAGE_S3_REGION",
    "access_key": "SOVRA_STORAGE_S3_ACCESS_KEY",
    "secret_key": "SOVRA_STORAGE_S3_SECRET_KEY",
    "bucket": "SOVRA_TRAINING_OBJECT_STORE_BUCKET",
}

_NETWORK_ERRORS = (urllib.error.URLError, http.client.HTTPException, OSError, TimeoutError)
_MAX_ERROR_BODY = 65536


class S3ProbeError(Exception):
    """A real non-2xx S3 response: status and the XML `Code` (or `Unknown`)."""

    def __init__(self, status_code: int, code: str) -> None:
        self.status_code = status_code
        self.code = code
        super().__init__(f"{status_code} {code}")


def _hmac(key: bytes, msg: str) -> bytes:
    return hmac.new(key, msg.encode("utf-8"), hashlib.sha256).digest()


@dataclass(frozen=True)
class S3ProbeClient:
    scheme: str
    host: str
    port: int
    region: str
    access_key: str
    secret_key: str
    timeout_seconds: float = 10.0

    def signed_headers(
        self, method: str, path: str, body: bytes, now: datetime
    ) -> tuple[str, dict[str, str]]:
        """(url, headers) for a path-style request signed with AWS Signature Version 4."""
        amz_date = now.strftime("%Y%m%dT%H%M%SZ")
        date_stamp = now.strftime("%Y%m%d")
        payload_hash = hashlib.sha256(body).hexdigest()
        default_port = {"http": 80, "https": 443}[self.scheme]
        host_header = self.host if self.port == default_port else f"{self.host}:{self.port}"
        headers = {
            "host": host_header,
            "x-amz-content-sha256": payload_hash,
            "x-amz-date": amz_date,
        }
        names = sorted(headers)
        canonical_uri = "/".join(quote(segment, safe="") for segment in path.split("/"))
        canonical_request = "\n".join(
            [
                method,
                canonical_uri,
                "",
                "".join(f"{name}:{headers[name]}\n" for name in names),
                ";".join(names),
                payload_hash,
            ]
        )
        scope = f"{date_stamp}/{self.region}/s3/aws4_request"
        string_to_sign = "\n".join(
            [
                "AWS4-HMAC-SHA256",
                amz_date,
                scope,
                hashlib.sha256(canonical_request.encode("utf-8")).hexdigest(),
            ]
        )
        key = _hmac(("AWS4" + self.secret_key).encode("utf-8"), date_stamp)
        for part in (self.region, "s3", "aws4_request"):
            key = _hmac(key, part)
        signature = hmac.new(key, string_to_sign.encode("utf-8"), hashlib.sha256).hexdigest()
        headers["authorization"] = (
            f"AWS4-HMAC-SHA256 Credential={self.access_key}/{scope}, "
            f"SignedHeaders={';'.join(names)}, Signature={signature}"
        )
        return f"{self.scheme}://{self.host}:{self.port}{path}", headers

    def request(
        self,
        method: str,
        bucket: str,
        key: str,
        body: bytes = b"",
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> bytes:
        now = clock() if clock is not None else datetime.now(UTC)
        url, headers = self.signed_headers(method, f"/{bucket}/{key}", body, now)
        req = urllib.request.Request(  # noqa: S310 - scheme is checked to be http(s)
            url, data=body if body else None, headers=headers, method=method
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout_seconds) as resp:  # noqa: S310
                return bytes(resp.read())
        except urllib.error.HTTPError as exc:
            raw = exc.read()[:_MAX_ERROR_BODY]
            code = "Unknown"
            try:
                for element in ET.fromstring(raw).iter():  # noqa: S314 - bounded S3 error body
                    if element.tag.rsplit("}", 1)[-1] == "Code" and element.text:
                        code = element.text.strip()[:64]
                        break
            except ET.ParseError:
                pass
            raise S3ProbeError(exc.code, code) from exc


def _failure_for(exc: S3ProbeError) -> ObjectStoreFailure:
    if exc.status_code in (401, 403):
        return ObjectStoreFailure("object_store_denied", f"{exc.status_code} {exc.code}")
    if exc.status_code == 404 and exc.code == "NoSuchBucket":
        return ObjectStoreFailure("object_store_bucket_missing", "404 NoSuchBucket")
    return ObjectStoreFailure("object_store_error", f"{exc.status_code} {exc.code}")


@dataclass(frozen=True)
class S3RoundTripProbe:
    client: S3ProbeClient
    bucket: str

    def round_trip(self, key: str, body: bytes) -> None:
        try:
            self.client.request("PUT", self.bucket, key, body)
            read_back = self.client.request("GET", self.bucket, key)
            if read_back != body:
                raise ObjectStoreFailure(
                    "object_store_readback_mismatch", "the probe object read back differs"
                )
            self.client.request("DELETE", self.bucket, key)
        except S3ProbeError as exc:
            raise _failure_for(exc) from exc
        except _NETWORK_ERRORS as exc:
            raise ObjectStoreFailure("object_store_unreachable", type(exc).__name__) from exc
        try:
            self.client.request("HEAD", self.bucket, key)
        except S3ProbeError as exc:
            if exc.status_code == 404:
                return
            raise _failure_for(exc) from exc
        except _NETWORK_ERRORS as exc:
            raise ObjectStoreFailure("object_store_unreachable", type(exc).__name__) from exc
        raise ObjectStoreFailure(
            "object_store_delete_unverified", "the probe object is still present after delete"
        )


def probe_from_environment(
    env: Mapping[str, str], *, timeout_seconds: float = 10.0
) -> S3RoundTripProbe | None:
    values = {name: env.get(var, "").strip() for name, var in ENV_VARS.items()}
    if any(not value for value in values.values()):
        return None
    if values["scheme"] not in ("http", "https") or not values["port"].isdigit():
        return None
    port = int(values["port"])
    if not 1 <= port <= 65535:
        return None
    client = S3ProbeClient(
        scheme=values["scheme"],
        host=values["host"],
        port=port,
        region=values["region"],
        access_key=values["access_key"],
        secret_key=values["secret_key"],
        timeout_seconds=timeout_seconds,
    )
    return S3RoundTripProbe(client=client, bucket=values["bucket"])
