"""Abort abandoned multipart uploads (FTF-0501, Section 67.5).

A browser that never finalizes leaves an unfinished multipart upload (and its
stored parts) in the upload bucket. Section 67.5 gives such an upload at most
24 hours. Nothing in the bucket configuration does this, so this sweeper does:
it lists the in-progress multipart uploads under the Factory's own key layout,
and aborts every one that was initiated more than `max_age` ago.

Rules:

- Only keys shaped exactly like a session's own object key
  (`tenants/<uuid>/projects/<uuid>/uploads/<uuid>/source`) are ever aborted, so
  a shared bucket's other uploads are never touched.
- An upload without a parseable initiation time is never aborted (it is
  counted as `undated`): the sweeper does not guess an age.
- Bounded: one listing page (at most 1000 uploads) per call. If a full page
  came back, `more_may_remain` is true and the caller sweeps again; aborted
  uploads leave the listing, so repeated sweeps make progress.
- A storage failure raises `UploadStorageUnavailableError`; a sweep that could
  not list or abort is never reported as done (Law 17). An upload that vanished
  between the listing and its abort (404) counts as already gone.

Deploy dependency: the sweeper needs the same `SOVRA_UPLOAD_S3_ENDPOINT`,
`SOVRA_UPLOAD_S3_BUCKET`, `SOVRA_UPLOAD_S3_ACCESS_KEY`, `SOVRA_UPLOAD_S3_SECRET_KEY`
(and optional `SOVRA_UPLOAD_S3_REGION`) environment as the API, and something
must run `python -m app.datasets.upload_sweeper` at least hourly (see
docs/operations/direct-upload-storage.md). Until that is scheduled in a
deployment the sweeper is implemented and tested but UNWIRED there.
"""

from __future__ import annotations

import re
import sys
import urllib.error
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from app.datasets.direct_object_storage_upload import (
    UploadStorageSettings,
    UploadStorageUnavailableError,
    _guard,
)
from app.services.storage_semantics_client import S3Client, S3Error

__all__ = ["MAX_UPLOAD_AGE", "SweepResult", "main", "sweep_abandoned_uploads"]

MAX_UPLOAD_AGE = timedelta(hours=24)  # Section 67.5
LISTING_PAGE_SIZE = 1000  # the client lists at most this many uploads per call
_UUID = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
_SESSION_KEY = re.compile(rf"tenants/{_UUID}/projects/{_UUID}/uploads/{_UUID}/source")


@dataclass(frozen=True)
class SweepResult:
    listed: int
    aborted: int
    kept: int  # younger than max_age
    undated: int  # no parseable initiation time: never aborted
    foreign: int  # not one of the Factory's own session keys: never touched
    more_may_remain: bool


def _initiated(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        moment = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return moment if moment.tzinfo is not None else None


def sweep_abandoned_uploads(
    client: S3Client,
    settings: UploadStorageSettings,
    *,
    now: datetime | None = None,
    max_age: timedelta = MAX_UPLOAD_AGE,
) -> SweepResult:
    moment = now if now is not None else datetime.now(UTC)
    if moment.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    if not timedelta(0) < max_age <= MAX_UPLOAD_AGE:
        raise ValueError("max_age must be positive and at most 24 hours")
    uploads = _guard(
        "list_multipart_uploads",
        lambda: client.list_multipart_uploads(settings.bucket, prefix="tenants/"),
    )
    aborted = kept = undated = foreign = 0
    for upload in uploads:
        key, upload_id = upload.get("Key"), upload.get("UploadId")
        if (
            not isinstance(key, str)
            or not isinstance(upload_id, str)
            or not upload_id
            or _SESSION_KEY.fullmatch(key) is None
        ):
            foreign += 1
            continue
        started = _initiated(upload.get("Initiated"))
        if started is None:
            undated += 1
            continue
        if moment - started <= max_age:
            kept += 1
            continue
        try:
            client.abort_multipart_upload(settings.bucket, key, upload_id)
        except S3Error as exc:
            if exc.status_code != 404:
                raise UploadStorageUnavailableError(
                    f"abort_multipart_upload: storage answered {exc.status_code} {exc.code}"
                ) from exc
        except (OSError, urllib.error.URLError, TimeoutError) as exc:
            raise UploadStorageUnavailableError(
                f"abort_multipart_upload: storage unreachable ({type(exc).__name__})"
            ) from exc
        aborted += 1
    return SweepResult(
        listed=len(uploads),
        aborted=aborted,
        kept=kept,
        undated=undated,
        foreign=foreign,
        more_may_remain=len(uploads) >= LISTING_PAGE_SIZE,
    )


def main() -> int:
    """Sweeps once using the API's own `SOVRA_UPLOAD_S3_*` environment. Exit 0
    after a completed sweep, 2 when storage is not configured, 1 when storage
    failed (so a scheduler shows the failure)."""
    settings = UploadStorageSettings.from_env()
    if settings is None:
        print("upload sweeper: SOVRA_UPLOAD_S3_* is not configured", file=sys.stderr)
        return 2
    try:
        result = sweep_abandoned_uploads(settings.client(), settings)
    except UploadStorageUnavailableError as exc:
        print(f"upload sweeper: {exc}", file=sys.stderr)
        return 1
    print(
        f"upload sweeper: listed={result.listed} aborted={result.aborted} kept={result.kept} "
        f"undated={result.undated} foreign={result.foreign} "
        f"more_may_remain={result.more_may_remain}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
