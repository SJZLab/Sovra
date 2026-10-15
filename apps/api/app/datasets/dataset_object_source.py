"""Read-only access to a Dataset version's verified bytes (FTF-0502-P1,
Section 8.1 steps 1-7, Section 9, Section 65.2, Section 69.1).

The importers (FTF-0502..0505) read the bytes of a Dataset version that
FTF-0501 already verified. This module is the only way they get them:

- The Dataset is loaded through FTF-0501-P1 `get_dataset`, so a foreign
  organization's dataset, a foreign project's dataset, a missing id and a
  `held_out_evaluation` dataset a caller may not see all raise the same
  `DatasetNotFoundError` (Section 9, Section 69.1).
- `include_held_out` is keyword-only with no default: every caller states
  whether it may read held-out data.
- The object key comes from the dataset's own upload-session row, read as
  `sovra_app` under the tenant context. No caller string reaches storage, there
  is no listing call, and the key is never returned or put in an error.
- The returned `chunks` iterator reads the object in bounded Range windows
  (`_WINDOW_BYTES`), each pinned to the first window's ETag and size, so a
  slowly consumed dataset is not ended by the server and a window of another
  object version is never spliced on: that is `DatasetSourceChangedError`,
  which `ChunkReader` turns into `failed` / `source_changed`. A storage failure
  surfaces from the iterator as `UploadStorageUnavailableError` (content-free,
  `from None`: the original exception can carry the signed URL, the key or, for
  `http.client.IncompleteRead`, object bytes). Every exception the stream raises
  is converted, not only the ones a caller expects. A stream
  that ends before the verified size is the same outage, not a changed object:
  the object is immutable, and a dropped connection reads as a clean end.

This module never writes.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Protocol

from sqlalchemy.orm import Session

from app.datasets.dataset_upload_store import (
    DatasetNotFoundError,
    UploadSessionNotFoundError,
    get_dataset,
    get_upload_session,
)
from app.datasets.direct_object_storage_upload import UploadStorageUnavailableError
from app.db.tenant_context import TenantContext
from app.services.storage_semantics_client import S3Error, S3ObjectChanged

__all__ = [
    "SOURCE_TYPE",
    "DatasetObjectSource",
    "DatasetObjectStorage",
    "DatasetSourceChangedError",
    "DatasetSourceInconsistentError",
    "open_dataset_source",
]

# Every Dataset version is created from a verified upload session, and an
# upload session can only be opened on an `upload` connection.
SOURCE_TYPE = "upload"
_CHUNK_SIZE = 1024 * 1024
_WINDOW_BYTES = 8 * 1024 * 1024


class _StreamingClient(Protocol):
    def stream_object_windows(
        self, bucket: str, key: str, *, window_bytes: int = ..., chunk_size: int = ...
    ) -> Iterator[bytes]: ...


@dataclass(frozen=True)
class DatasetObjectStorage:
    """The client and bucket the dataset bytes live in."""

    client: _StreamingClient
    bucket: str


class DatasetSourceInconsistentError(RuntimeError):
    """The dataset row and its upload session disagree. The database refuses
    to create such a pair, so this means the data was changed underneath it.
    Content-free."""


class DatasetSourceChangedError(RuntimeError):
    """The object changed while it was being read. Content-free.
    `source_changed` is the marker `ChunkReader` maps to `failed` /
    `source_changed` (Law 17: neither an outage nor spliced bytes)."""

    source_changed = True


@dataclass(frozen=True)
class DatasetObjectSource:
    dataset_id: uuid.UUID
    purpose: str
    source_type: str
    verified_content_type: str
    expected_sha256: str
    expected_size: int
    chunks: Iterator[bytes]


def _stream(storage: DatasetObjectStorage, key: str, expected_size: int) -> Iterator[bytes]:
    received = 0
    try:
        for chunk in storage.client.stream_object_windows(
            storage.bucket, key, window_bytes=_WINDOW_BYTES, chunk_size=_CHUNK_SIZE
        ):
            received += len(chunk)
            yield chunk
    except S3ObjectChanged:
        raise DatasetSourceChangedError("stream_object_windows: object changed") from None
    except S3Error as exc:
        raise UploadStorageUnavailableError(
            f"stream_object_windows: storage answered {exc.status_code} {exc.code}"
        ) from None
    except (UploadStorageUnavailableError, DatasetSourceChangedError):
        raise
    except Exception as exc:  # noqa: BLE001 - any storage-side failure is an outage; the original can carry object bytes
        # http.client.IncompleteRead carries the partial object bytes and
        # BadStatusLine the raw status line: neither may leave this module.
        raise UploadStorageUnavailableError(
            f"stream_object_windows: storage failed ({type(exc).__name__})"
        ) from None
    if received < expected_size:
        # An immutable, verified object cannot get shorter: the connection ended
        # early and `http.client` reports that as a clean end of stream.
        raise UploadStorageUnavailableError("stream_object_windows: storage ended the stream early")


def open_dataset_source(
    session: Session,
    context: TenantContext,
    *,
    project_id: uuid.UUID,
    dataset_id: uuid.UUID,
    include_held_out: bool,
    storage: DatasetObjectStorage,
) -> DatasetObjectSource:
    """Reads the dataset and its upload session now; the object is read lazily
    when `chunks` is iterated. Raises `DatasetNotFoundError` for a foreign,
    missing or hidden held-out dataset (indistinguishably)."""
    if type(include_held_out) is not bool:
        raise TypeError("include_held_out must be a bool")
    dataset = get_dataset(
        session,
        context,
        project_id=project_id,
        dataset_id=dataset_id,
        include_held_out=include_held_out,
    )
    try:
        upload = get_upload_session(
            session, context, project_id=project_id, upload_session_id=dataset.upload_session_id
        )
    except UploadSessionNotFoundError:
        raise DatasetNotFoundError(f"dataset {dataset_id} not found") from None
    if (
        upload.status != "verified"
        or upload.verified_sha256 != dataset.verified_sha256
        or upload.verified_size_bytes != dataset.verified_size_bytes
        or upload.verified_content_type is None
    ):
        raise DatasetSourceInconsistentError("dataset and upload session disagree")
    return DatasetObjectSource(
        dataset_id=dataset.id,
        purpose=dataset.purpose,
        source_type=SOURCE_TYPE,
        verified_content_type=upload.verified_content_type,
        expected_sha256=dataset.verified_sha256,
        expected_size=dataset.verified_size_bytes,
        chunks=_stream(storage, upload.object_key, dataset.verified_size_bytes),
    )
