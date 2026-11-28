"""The real S3-compatible object-store probe for NVIDIA environment validation (FTF-0705).

`S3RoundTripProbe.round_trip` writes a small probe object, reads it back, checks it is
byte-identical, deletes it and confirms the delete with a HEAD that must answer 404. It uses the
repository's certified stdlib SigV4 client (`apps/api/app/services/storage_semantics_client.py`,
FTF-R017) instead of a second signer and adds no runtime dependency. Every failure becomes one
stable `ObjectStoreFailure` code; nothing is retried into a success (Law 17):

- `object_store_denied`         the store refused the caller's identity (401/403);
- `object_store_bucket_missing` the bucket does not exist (404 `NoSuchBucket`);
- `object_store_unreachable`    no HTTP answer (connection refused, DNS, timeout, reset);
- `object_store_readback_mismatch` the object read back differs from what was written;
- `object_store_delete_unverified` the probe object is still present after its delete;
- `object_store_error`          any other S3 error response.

`probe_from_environment` builds the probe from the same `SOVRA_STORAGE_S3_*` variables the storage
profile names (infra/internal/storage-profile.yaml) plus `SOVRA_TRAINING_OBJECT_STORE_BUCKET`. A
missing variable returns None, which the validator reports as `object_store_unconfigured`; there are
no built-in defaults on this path.
"""

from __future__ import annotations

import http.client
import urllib.error
from collections.abc import Mapping
from dataclasses import dataclass

from apps.api.app.services.storage_semantics_client import (
    S3Client,
    S3Credentials,
    S3Endpoint,
    S3Error,
)

from workers.training.nvidia_environment_validation import ObjectStoreFailure

__all__ = ["ENV_VARS", "S3RoundTripProbe", "probe_from_environment"]

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


def _failure_for(exc: S3Error) -> ObjectStoreFailure:
    if exc.status_code in (401, 403):
        return ObjectStoreFailure("object_store_denied", f"{exc.status_code} {exc.code}")
    if exc.status_code == 404 and exc.code == "NoSuchBucket":
        return ObjectStoreFailure("object_store_bucket_missing", "404 NoSuchBucket")
    return ObjectStoreFailure("object_store_error", f"{exc.status_code} {exc.code}")


@dataclass(frozen=True)
class S3RoundTripProbe:
    client: S3Client
    bucket: str

    def round_trip(self, key: str, body: bytes) -> None:
        try:
            self.client.put_object(self.bucket, key, body)
            read_back, _ = self.client.get_object(self.bucket, key)
            if read_back != body:
                raise ObjectStoreFailure(
                    "object_store_readback_mismatch", "the probe object read back differs"
                )
            self.client.delete_object(self.bucket, key)
        except S3Error as exc:
            raise _failure_for(exc) from exc
        except _NETWORK_ERRORS as exc:
            raise ObjectStoreFailure("object_store_unreachable", type(exc).__name__) from exc
        try:
            self.client.head_object(self.bucket, key)
        except S3Error as exc:
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
    client = S3Client(
        S3Endpoint(
            scheme=values["scheme"],
            host=values["host"],
            port=port,
            region=values["region"],
        ),
        S3Credentials(access_key=values["access_key"], secret_key=values["secret_key"]),
        timeout_seconds=timeout_seconds,
    )
    return S3RoundTripProbe(client=client, bucket=values["bucket"])
