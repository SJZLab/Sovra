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
- Age comes from data the backend actually gives. The upload session's own
  `expires_at` (the database row named by the key's session id, at most 24 h
  after creation, Section 67.5) is authoritative when an `expiry_lookup` is
  supplied; otherwise the storage listing's `Initiated` time is used. SeaweedFS
  3.99 returns no `Initiated`, so on a real deployment the lookup is required.
  An upload whose age can be derived from neither is never aborted and is
  counted as `undated`; `main()` then exits non-zero so a scheduler shows it.
- Bounded, but not by the first page: the listing is paged with key/upload-id
  markers under a per-run budget (`max_pages`). If the budget ends before the
  listing does, `more_may_remain` is true and `main()` exits 3; aborted
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

import os
import re
import sys
import urllib.error
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from functools import partial

from app.datasets.direct_object_storage_upload import (
    UploadStorageSettings,
    UploadStorageUnavailableError,
    _guard,
)
from app.services.storage_semantics_client import S3Client, S3Error

__all__ = [
    "ExpiryLookup",
    "MAX_UPLOAD_AGE",
    "SweepResult",
    "main",
    "sweep_abandoned_uploads",
]

MAX_UPLOAD_AGE = timedelta(hours=24)  # Section 67.5
LISTING_PAGE_SIZE = 1000  # S3's own ceiling for one ListMultipartUploads page
DEFAULT_MAX_PAGES = 100  # per-run budget: at most 100,000 uploads examined
_UUID = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
_SESSION_KEY = re.compile(
    rf"tenants/(?P<org>{_UUID})/projects/{_UUID}/uploads/(?P<session>{_UUID})/source"
)

# (organization id, upload session id) -> the session's `expires_at`, or None
# when no such session exists. A lookup that cannot answer must raise.
ExpiryLookup = Callable[[uuid.UUID, uuid.UUID], datetime | None]


@dataclass(frozen=True)
class SweepResult:
    listed: int
    aborted: int
    kept: int  # younger than max_age
    undated: int  # age cannot be derived from the session or the listing: never aborted
    foreign: int  # not one of the Factory's own session keys: never touched
    more_may_remain: bool
    pages: int = 1


def _initiated(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        moment = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return moment if moment.tzinfo is not None else None


def _deadline(
    match: re.Match[str],
    upload: dict[str, str],
    max_age: timedelta,
    expiry_lookup: ExpiryLookup | None,
) -> datetime | None:
    """The moment after which the upload may be aborted, or None if unknown."""
    if expiry_lookup is not None:
        expires_at = expiry_lookup(uuid.UUID(match["org"]), uuid.UUID(match["session"]))
        if isinstance(expires_at, datetime) and expires_at.tzinfo is not None:
            return expires_at
    started = _initiated(upload.get("Initiated"))
    return None if started is None else started + max_age


def sweep_abandoned_uploads(
    client: S3Client,
    settings: UploadStorageSettings,
    *,
    now: datetime | None = None,
    max_age: timedelta = MAX_UPLOAD_AGE,
    expiry_lookup: ExpiryLookup | None = None,
    max_pages: int = DEFAULT_MAX_PAGES,
    page_size: int = LISTING_PAGE_SIZE,
) -> SweepResult:
    moment = now if now is not None else datetime.now(UTC)
    if moment.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    if not timedelta(0) < max_age <= MAX_UPLOAD_AGE:
        raise ValueError("max_age must be positive and at most 24 hours")
    if not 1 <= max_pages:
        raise ValueError("max_pages must be at least 1")
    if not 1 <= page_size <= LISTING_PAGE_SIZE:
        raise ValueError(f"page_size must be between 1 and {LISTING_PAGE_SIZE}")
    listed = aborted = kept = undated = foreign = pages = 0
    markers: tuple[str, str] | None = ("", "")
    while markers is not None and pages < max_pages:
        key_marker, upload_id_marker = markers
        uploads, markers = _guard(
            "list_multipart_uploads",
            partial(
                client.list_multipart_uploads_page,
                settings.bucket,
                prefix="tenants/",
                key_marker=key_marker,
                upload_id_marker=upload_id_marker,
                max_uploads=page_size,
            ),
        )
        pages += 1
        listed += len(uploads)
        for upload in uploads:
            key, upload_id = upload.get("Key"), upload.get("UploadId")
            match = _SESSION_KEY.fullmatch(key) if isinstance(key, str) else None
            if match is None or not isinstance(upload_id, str) or not upload_id:
                foreign += 1
                continue
            deadline = _deadline(match, upload, max_age, expiry_lookup)
            if deadline is None:
                undated += 1
                continue
            # Exactly at the deadline is kept: aborted only once it has passed.
            if moment <= deadline:
                kept += 1
                continue
            try:
                client.abort_multipart_upload(settings.bucket, str(key), upload_id)
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
        listed=listed,
        aborted=aborted,
        kept=kept,
        undated=undated,
        foreign=foreign,
        more_may_remain=markers is not None,
        pages=pages,
    )


def _database_expiry_lookup(database_url: str) -> tuple[ExpiryLookup, Callable[[], None]]:
    """Reads each session's `expires_at` through the restricted application
    role under that session's own tenant context (the organization id comes
    from the key's own `tenants/<org>/` segment), so row-level security applies
    exactly as it does for the API. Returns the lookup and a cleanup."""
    from sqlalchemy import create_engine, select
    from sqlalchemy.orm import sessionmaker

    from app.auth.tenant_isolation_query_guards import (
        execute_tenant_scoped,
        tenant_scoped_session,
    )
    from app.datasets.dataset_upload_store import DatasetUploadSession

    engine = create_engine(database_url)
    factory = sessionmaker(bind=engine)

    def lookup(organization_id: uuid.UUID, session_id: uuid.UUID) -> datetime | None:
        with tenant_scoped_session(factory, organization_id) as db:
            statement = select(DatasetUploadSession.expires_at).where(
                DatasetUploadSession.id == session_id
            )
            return execute_tenant_scoped(db, organization_id, statement).scalars().one_or_none()

    return lookup, engine.dispose


def _no_cleanup() -> None:
    return None


def main() -> int:
    """Sweeps once using the API's own `SOVRA_UPLOAD_S3_*` environment and,
    when `SOVRA_DATABASE_URL` is set, the upload sessions' own expiry. Exit 0
    after a sweep that dated and judged every Factory upload it listed; 2 when
    storage is not configured; 1 when storage or the database failed, or when
    any upload's age could not be determined (so a scheduler shows it); 3 when
    the per-run page budget ended before the listing did."""
    settings = UploadStorageSettings.from_env()
    if settings is None:
        print("upload sweeper: SOVRA_UPLOAD_S3_* is not configured", file=sys.stderr)
        return 2
    lookup: ExpiryLookup | None = None
    cleanup: Callable[[], None] = _no_cleanup
    database_url = os.environ.get("SOVRA_DATABASE_URL")
    try:
        if database_url:
            lookup, cleanup = _database_expiry_lookup(database_url)
        result = sweep_abandoned_uploads(
            settings.client(), settings, expiry_lookup=lookup, max_pages=DEFAULT_MAX_PAGES
        )
    except UploadStorageUnavailableError as exc:
        print(f"upload sweeper: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:  # a database failure must be visible, never a clean sweep
        print(f"upload sweeper: session lookup failed ({type(exc).__name__})", file=sys.stderr)
        return 1
    finally:
        cleanup()
    print(
        f"upload sweeper: listed={result.listed} aborted={result.aborted} kept={result.kept} "
        f"undated={result.undated} foreign={result.foreign} pages={result.pages} "
        f"more_may_remain={result.more_may_remain}"
    )
    if result.undated:
        print(
            f"upload sweeper: {result.undated} upload(s) have no determinable age "
            "(set SOVRA_DATABASE_URL so session expiry can be used)",
            file=sys.stderr,
        )
        return 1
    return 3 if result.more_may_remain else 0


if __name__ == "__main__":
    raise SystemExit(main())
