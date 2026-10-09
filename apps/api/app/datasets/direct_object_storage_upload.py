"""Direct object-storage upload: presigned multipart + finalize verification
(FTF-0501, Section 8.1 steps 1-4, 65.2, 67.3, 67.5).

The browser uploads parts straight to private object storage with short-lived
presigned URLs; the API never proxies object bytes. This module is the storage
half. The database half (`dataset_upload_store`, FTF-0501-P1/P2) owns the
upload-session lifecycle and is the only way a Dataset version comes to exist.

Rules this module enforces:

- The object key is the session's own server-generated key; no caller string
  ever reaches the storage layer (Section 65.2). A presigned URL binds the
  method, the key, the part number, the multipart upload id and the exact part
  `content-length`, and lives at most five minutes and never past the
  session's own expiry.
- Finalize trusts nothing the client says. It lists the parts storage actually
  holds, requires exactly the planned part sizes, completes the multipart
  upload, then requires the stored size, the server-computed SHA-256 and the
  content-sniffed type to equal what the session declared. Any difference
  deletes the object and raises `UploadRejection` with a stable reason code;
  the caller then records the rejection, so no Dataset version exists.
- An upload that is merely not finished yet (parts missing) raises
  `UploadIncomplete` and changes nothing: the session stays open and resumable
  until its own expiry.
- A storage outage, bad credential or unexpected storage answer raises
  `UploadStorageUnavailableError`; it is never turned into a rejection or a
  success (Law 17), and the session stays open.
- The file type is detected from content, not from the name or the declared
  type alone (Section 8.1 step 3); an unsupported or ambiguous file is rejected.

The module never touches the database.
"""

from __future__ import annotations

import codecs
import hashlib
import io
import os
import re
import struct
import urllib.error
import zipfile
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import IO, TypeVar, cast
from urllib.parse import urlsplit
from xml.etree import ElementTree
from xml.parsers import expat

from app.services.storage_semantics_client import (
    PRESIGN_MAX_EXPIRES_SECONDS,
    S3Client,
    S3Credentials,
    S3Endpoint,
    S3Error,
    presign_url,
)

__all__ = [
    "DEFAULT_PART_BYTES",
    "MAX_PARTS",
    "MAX_PART_URLS_PER_REQUEST",
    "MIN_PART_BYTES",
    "SNIFF_BYTES",
    "SUPPORTED_CONTENT_TYPES",
    "PartGrant",
    "PartPlan",
    "UploadExpired",
    "UploadIncomplete",
    "UploadRejection",
    "UploadStorageSettings",
    "UploadStorageUnavailableError",
    "UploadTarget",
    "UploadedPart",
    "VerifiedObject",
    "abort_upload",
    "find_upload_id",
    "list_uploaded_parts",
    "normalize_content_type",
    "plan_parts",
    "presign_part_urls",
    "sniff_content",
    "inspect_xlsx",
    "start_multipart",
    "verify_and_complete",
]

MIN_PART_BYTES = 5 * 1024**2  # S3's minimum for every part but the last.
DEFAULT_PART_BYTES = 64 * 1024**2
MAX_PARTS = 10_000  # S3's maximum part count.
MAX_PART_URLS_PER_REQUEST = 100
SNIFF_BYTES = 64 * 1024
_HASH_CHUNK_BYTES = 1024 * 1024

XLSX_CONTENT_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
SUPPORTED_CONTENT_TYPES = frozenset(
    {
        "application/json",
        "application/jsonl",
        "application/x-ndjson",
        "text/csv",
        XLSX_CONTENT_TYPE,
    }
)

_BINARY_MAGIC = (b"PK\x03\x04", b"%PDF", b"\x1f\x8b", b"\x89PNG", b"GIF8", b"\x7fELF", b"MZ")
_UTF8_BOM = codecs.BOM_UTF8
_DEFAULT_PORT_BY_SCHEME = {"http": 80, "https": 443}
_ZIP_MAGIC_BY_TYPE = {XLSX_CONTENT_TYPE: b"PK\x03\x04"}
# Section 67.3 archive bounds for an XLSX container (values-only, no macros).
MAX_XLSX_ENTRIES = 10_000
MAX_XLSX_UNCOMPRESSED_BYTES = 10 * 1024**3  # the 10 GiB source-object default
MAX_XLSX_CONTENT_TYPES_BYTES = 1024**2
_XLSX_CONTENT_TYPES_PART = "[Content_Types].xml"
_XLSX_WORKBOOK_PART = "xl/workbook.xml"
_XLSX_RANGE_READ_BYTES = 64 * 1024
# The zip central directory of a legitimate workbook is a few KiB; 10,000 entries
# of ordinary part names fit well inside this. The end-of-archive record is read
# first and anything larger is refused before zipfile allocates for it.
MAX_XLSX_DIRECTORY_BYTES = 4 * 1024**2
# No single ranged GET (and no single read from the stored object) exceeds this.
_XLSX_MAX_RANGE_BYTES = 1024**2
_ZIP_EOCD = b"PK\x05\x06"
_ZIP64_LOCATOR = b"PK\x06\x07"
_ZIP64_EOCD = b"PK\x06\x06"
_ZIP_EOCD_SIZE = 22
_ZIP64_LOCATOR_SIZE = 20
_ZIP64_EOCD_SIZE = 56
_ZIP_MAX_COMMENT = 0xFFFF
# Closed lookup tables rather than comparisons: a type is matched exactly, with
# no tolerance for case or whitespace once it has been normalized.
_SUPPORTED_BY_NAME = {name: name for name in SUPPORTED_CONTENT_TYPES}
_MUST_START_WITH = {
    "application/jsonl": frozenset({b"{"}),
    "application/x-ndjson": frozenset({b"{"}),
    "application/json": frozenset({b"{", b"["}),
}
_MUST_NOT_START_WITH = {"text/csv": frozenset({b"{", b"["})}
_LEADING_WHITESPACE_THEN_BYTE = re.compile(rb"\s*(\S)")


_T = TypeVar("_T")


class UploadRejection(Exception):  # noqa: N818 - a domain verdict, not an error class
    """The stored object must not become a dataset. `reason` is a stable
    lowercase code (never object content); the object has been deleted."""

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


class UploadIncomplete(Exception):  # noqa: N818
    """Parts are still missing; nothing was changed and the session stays open."""

    def __init__(self, missing_parts: tuple[int, ...]) -> None:
        self.missing_parts = missing_parts
        super().__init__(f"{len(missing_parts)} part(s) missing")


class UploadExpired(Exception):  # noqa: N818
    """The session's own lifetime has ended; no URL is issued and nothing is
    completed."""


class UploadStorageUnavailableError(RuntimeError):
    """Object storage is not configured, not reachable or answered in an
    unexpected way. Content-free: only an operation name and a status/code."""


@dataclass(frozen=True)
class UploadStorageSettings:
    endpoint: S3Endpoint
    credentials: S3Credentials
    bucket: str

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> UploadStorageSettings | None:
        """`None` when upload storage is not configured at all (the routes then
        answer 503 `storage_not_configured`); raises `ValueError` when it is
        configured inconsistently."""
        source = os.environ if env is None else env
        names = (
            "SOVRA_UPLOAD_S3_ENDPOINT",
            "SOVRA_UPLOAD_S3_BUCKET",
            "SOVRA_UPLOAD_S3_ACCESS_KEY",
            "SOVRA_UPLOAD_S3_SECRET_KEY",
        )
        values = {name: source.get(name, "").strip() for name in names}
        if not any(values.values()):
            return None
        missing = [name for name, value in values.items() if not value]
        if missing:
            raise ValueError(f"upload storage is partly configured; missing {sorted(missing)}")
        parts = urlsplit(values["SOVRA_UPLOAD_S3_ENDPOINT"])
        default_port = _DEFAULT_PORT_BY_SCHEME.get(parts.scheme)
        if default_port is None or not parts.hostname:
            raise ValueError("SOVRA_UPLOAD_S3_ENDPOINT must be an http(s) URL with a host")
        port = parts.port or default_port
        region = source.get("SOVRA_UPLOAD_S3_REGION", "").strip() or "us-east-1"
        return cls(
            endpoint=S3Endpoint(parts.scheme, parts.hostname, port, region),
            credentials=S3Credentials(
                values["SOVRA_UPLOAD_S3_ACCESS_KEY"], values["SOVRA_UPLOAD_S3_SECRET_KEY"]
            ),
            bucket=values["SOVRA_UPLOAD_S3_BUCKET"],
        )

    def client(self) -> S3Client:
        return S3Client(self.endpoint, self.credentials, timeout_seconds=30.0)


@dataclass(frozen=True)
class UploadTarget:
    """What storage needs to know about one upload session. Built from the
    session row; `object_key` is the server-generated key, never user input."""

    object_key: str
    declared_size_bytes: int
    declared_sha256: str
    declared_content_type: str
    expires_at: datetime


@dataclass(frozen=True)
class PartPlan:
    part_size_bytes: int
    part_count: int
    last_part_size_bytes: int

    def expected_size(self, part_number: int) -> int:
        if not 1 <= part_number <= self.part_count:
            raise ValueError(f"part_number must be 1..{self.part_count}")
        return self.part_size_bytes if part_number < self.part_count else self.last_part_size_bytes


@dataclass(frozen=True)
class UploadedPart:
    part_number: int
    size_bytes: int
    etag: str


@dataclass(frozen=True)
class PartGrant:
    part_number: int
    method: str
    url: str
    content_length: int
    expires_at: datetime


@dataclass(frozen=True)
class VerifiedObject:
    size_bytes: int
    sha256: str
    content_type: str


def plan_parts(declared_size_bytes: int, *, part_size_bytes: int | None = None) -> PartPlan:
    """The deterministic part layout for a declared size: every part but the
    last is `part_size_bytes` (default `DEFAULT_PART_BYTES`, read at call time);
    the last holds the remainder."""
    if part_size_bytes is None:
        part_size_bytes = DEFAULT_PART_BYTES
    if isinstance(declared_size_bytes, bool) or not isinstance(declared_size_bytes, int):
        raise ValueError("declared_size_bytes must be an int")
    if isinstance(part_size_bytes, bool) or not isinstance(part_size_bytes, int):
        raise ValueError("part_size_bytes must be an int")
    if declared_size_bytes <= 0:
        raise ValueError("declared_size_bytes must be positive")
    if part_size_bytes < MIN_PART_BYTES:
        raise ValueError(f"part_size_bytes must be at least {MIN_PART_BYTES}")
    count = -(-declared_size_bytes // part_size_bytes)
    if count > MAX_PARTS:
        raise ValueError(f"declared size needs more than {MAX_PARTS} parts")
    return PartPlan(
        part_size_bytes=part_size_bytes,
        part_count=count,
        last_part_size_bytes=declared_size_bytes - (count - 1) * part_size_bytes,
    )


def normalize_content_type(value: object) -> str | None:
    """The media type without parameters, lower-cased, when it is one of the
    supported import types; otherwise `None`."""
    if not isinstance(value, str):
        return None
    return _SUPPORTED_BY_NAME.get(value.split(";", 1)[0].strip().lower())


def _decodes_as_utf8(head: bytes, *, complete: bool) -> bool:
    decoder = codecs.getincrementaldecoder("utf-8")()
    try:
        decoder.decode(head, final=complete)
    except UnicodeDecodeError:
        return False
    return True


def _first_significant_byte(text: bytes) -> bytes:
    """The first byte that is not ASCII whitespace, or `b""` when there is none."""
    found = _LEADING_WHITESPACE_THEN_BYTE.match(text)
    return found.group(1) if found else b""


def sniff_content(declared_content_type: str, head: bytes, *, complete: bool) -> str | None:
    """Checks the leading bytes of an object against its declared type
    (Section 8.1 step 3). Returns `None` when they agree, else a stable reason:
    `unsupported_content_type` or `content_type_mismatch`. `complete` says the
    bytes are the whole object (so a truncated UTF-8 tail is an error)."""
    base = normalize_content_type(declared_content_type)
    if base is None:
        return "unsupported_content_type"
    zip_magic = _ZIP_MAGIC_BY_TYPE.get(base)
    if zip_magic is not None:
        return None if head.startswith(zip_magic) else "content_type_mismatch"
    if any(head.startswith(magic) for magic in _BINARY_MAGIC) or b"\x00" in head:
        return "content_type_mismatch"
    if not _decodes_as_utf8(head, complete=complete):
        return "content_type_mismatch"
    text = head[len(_UTF8_BOM) :] if head.startswith(_UTF8_BOM) else head
    first = _first_significant_byte(text)
    if not first:
        return "content_type_mismatch"
    starts = _MUST_START_WITH.get(base)
    if starts is not None:
        return None if starts.issuperset((first,)) else "content_type_mismatch"
    # text/csv: a leading JSON structure is ambiguous (Section 8.1 step 4).
    return None if _MUST_NOT_START_WITH[base].isdisjoint((first,)) else "content_type_mismatch"


# -- storage calls -------------------------------------------------------------


def _storage_error(operation: str, exc: BaseException) -> UploadStorageUnavailableError:
    if isinstance(exc, S3Error):
        return UploadStorageUnavailableError(
            f"{operation}: storage answered {exc.status_code} {exc.code}"
        )
    return UploadStorageUnavailableError(f"{operation}: storage unreachable ({type(exc).__name__})")


def _guard(operation: str, call: Callable[[], _T]) -> _T:
    try:
        return call()
    except S3Error as exc:
        raise _storage_error(operation, exc) from exc
    except (OSError, urllib.error.URLError, TimeoutError, ValueError) as exc:
        raise _storage_error(operation, exc) from exc


class _UnsafeArchiveError(Exception):
    """An archive that asks for more than the bounds allow (internal verdict)."""


def _mentions_declaration(document: bytes) -> bool:
    for codec in ("latin-1", "utf-16", "utf-16-le", "utf-16-be"):
        text = document.decode(codec, errors="ignore").lower()
        if "<!doctype" in text or "<!entity" in text:
            return True
    return False


def _refuse_declarations(document: bytes) -> None:
    """Parses `document` with expat and refuses any DOCTYPE (the only place an
    entity, notation or external subset can be declared), whatever the encoding:
    a byte search for `<!doctype` misses UTF-16. Raises `_UnsafeArchiveError`; a
    malformed document raises `expat.ExpatError` for the caller to classify."""

    def refuse(*_args: object) -> None:
        raise _UnsafeArchiveError("DOCTYPE or entity declaration")

    parser = expat.ParserCreate()
    parser.StartDoctypeDeclHandler = refuse
    parser.Parse(document, True)


def _end_record_totals(tail: bytes) -> tuple[int, int, int] | None:
    """(entries, directory size, directory offset) as `zipfile` itself would read
    them from the end of the archive (the end-of-central-directory record, or the
    zip64 one when its locator is present), or None when no record is found."""
    if len(tail) >= _ZIP_EOCD_SIZE and tail[-_ZIP_EOCD_SIZE:-18] == _ZIP_EOCD:
        position = len(tail) - _ZIP_EOCD_SIZE
        if struct.unpack("<H", tail[-2:])[0] != 0:
            position = tail.rfind(_ZIP_EOCD)
    else:
        position = tail.rfind(_ZIP_EOCD)
    if position < 0 or len(tail) - position < _ZIP_EOCD_SIZE:
        return None
    fields = struct.unpack("<4s4H2LH", tail[position : position + _ZIP_EOCD_SIZE])
    entries, size, offset = fields[4], fields[5], fields[6]
    record_at = position - _ZIP64_LOCATOR_SIZE - _ZIP64_EOCD_SIZE
    locator_at = position - _ZIP64_LOCATOR_SIZE
    # record_at >= 0 also guarantees locator_at >= 0 (no negative slice indexes)
    if (
        record_at >= 0
        and tail[locator_at : locator_at + 4] == _ZIP64_LOCATOR
        and tail[record_at : record_at + 4] == _ZIP64_EOCD
    ):
        record = struct.unpack("<4sQ2H2L4Q", tail[record_at : record_at + _ZIP64_EOCD_SIZE])
        entries, size, offset = record[7], record[8], record[9]
    return entries, size, offset


class _StoredObjectFile(io.RawIOBase):
    """A read-only seekable view of a stored object, backed by ranged GETs, so
    the zip central directory can be read without downloading the object. A
    storage failure raises `UploadStorageUnavailableError` (never a verdict)."""

    def __init__(self, client: S3Client, settings: UploadStorageSettings, key: str, size: int):
        self._client, self._settings, self._key, self._size = client, settings, key, size
        self._pos = 0
        self._window_start = 0
        self._window = b""

    def readable(self) -> bool:
        if self.closed:
            raise ValueError("I/O operation on closed file")
        return True

    def seekable(self) -> bool:
        if self.closed:
            raise ValueError("I/O operation on closed file")
        return True

    def seek(self, offset: int, whence: int = os.SEEK_SET) -> int:
        base = {os.SEEK_SET: 0, os.SEEK_CUR: self._pos, os.SEEK_END: self._size}[whence]
        self._pos = max(0, base + offset)
        return self._pos

    def _fetch(self, first: int, last: int) -> bytes:
        return _guard(
            "get_object",
            lambda: self._client.get_object_range(self._settings.bucket, self._key, first, last),
        )

    def read(self, size: int = -1) -> bytes:
        end = self._size if size is None or size < 0 else min(self._size, self._pos + size)
        if end - self._pos > MAX_XLSX_DIRECTORY_BYTES:
            # zipfile never legitimately asks for more than the bounded directory in one read
            raise _UnsafeArchiveError("a single read larger than the directory bound")
        out = bytearray()
        while self._pos < end:
            offset = self._pos - self._window_start
            if not 0 <= offset < len(self._window):
                wanted = min(max(end - self._pos, _XLSX_RANGE_READ_BYTES), _XLSX_MAX_RANGE_BYTES)
                last = min(self._size, self._pos + wanted) - 1
                self._window_start = self._pos
                self._window = self._fetch(self._pos, last)
                offset = 0
                if not self._window:
                    break
            piece = self._window[offset : offset + (end - self._pos)]
            out += piece
            self._pos += len(piece)
        return bytes(out)


def inspect_xlsx(
    client: S3Client, settings: UploadStorageSettings, target: UploadTarget
) -> str | None:
    """Checks that a stored object is an XLSX container (Section 8.1 steps 3-4,
    67.3) by reading only its zip directory and `[Content_Types].xml` with
    ranged GETs. Returns `None` for a spreadsheet package, `content_type_mismatch`
    for anything else (a docx, a plain zip, junk, a truncated archive) and
    `unsafe_archive` for a zip bomb, path traversal, duplicate part names, a
    macro-enabled workbook or entity declarations. Nothing is evaluated."""
    stored = _StoredObjectFile(client, settings, target.object_key, target.declared_size_bytes)
    try:
        # The end-of-archive record is read first, so a record that claims a huge
        # directory or entry count is refused before zipfile allocates for it.
        tail_size = min(
            stored._size,
            _ZIP_MAX_COMMENT + _ZIP_EOCD_SIZE + _ZIP64_LOCATOR_SIZE + _ZIP64_EOCD_SIZE,
        )
        totals = (
            _end_record_totals(stored._fetch(stored._size - tail_size, stored._size - 1))
            if tail_size > 0
            else None
        )
        if totals is not None and (
            totals[0] > MAX_XLSX_ENTRIES or totals[1] > MAX_XLSX_DIRECTORY_BYTES
        ):
            return "unsafe_archive"
        with zipfile.ZipFile(cast("IO[bytes]", stored)) as archive:
            infos = archive.infolist()
            if len(infos) > MAX_XLSX_ENTRIES:
                return "unsafe_archive"
            names = [info.filename for info in infos]
            if len(set(names)) != len(names):
                return "unsafe_archive"
            if any(
                name.startswith("/") or "\\" in name or ".." in name.split("/") for name in names
            ):
                return "unsafe_archive"
            if sum(info.file_size for info in infos) > MAX_XLSX_UNCOMPRESSED_BYTES:
                return "unsafe_archive"
            if "[Content_Types].xml" not in names or "xl/workbook.xml" not in names:
                return "content_type_mismatch"
            with archive.open(_XLSX_CONTENT_TYPES_PART) as handle:
                document = handle.read(MAX_XLSX_CONTENT_TYPES_BYTES + 1)
    except UploadStorageUnavailableError:
        raise
    except _UnsafeArchiveError:
        return "unsafe_archive"
    except (
        zipfile.BadZipFile,
        zipfile.LargeZipFile,
        EOFError,
        NotImplementedError,
        OverflowError,
        RuntimeError,
        ValueError,
        struct.error,
    ):
        return "content_type_mismatch"
    if len(document) > MAX_XLSX_CONTENT_TYPES_BYTES:
        return "unsafe_archive"
    try:
        _refuse_declarations(document)
        root = ElementTree.fromstring(document)  # noqa: S314 - no DOCTYPE/ENTITY, refused above
    except _UnsafeArchiveError:
        return "unsafe_archive"
    except (ElementTree.ParseError, expat.ExpatError):
        # Not well-formed XML: a document that still mentions a declaration (in any
        # case or encoding) is an attempt, not an innocent mismatch.
        return "unsafe_archive" if _mentions_declaration(document) else "content_type_mismatch"
    for element in root:
        if element.tag.rpartition("}")[2] == "Override" and element.get("PartName") == (
            "/" + _XLSX_WORKBOOK_PART
        ):
            content_type = element.get("ContentType")
            if content_type == (
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"
            ):
                return None
            if content_type and "macroenabled" in content_type.lower():
                return "unsafe_archive"
            return "content_type_mismatch"
    return "content_type_mismatch"


def start_multipart(client: S3Client, settings: UploadStorageSettings, target: UploadTarget) -> str:
    """Opens the multipart upload for the session's own key, stamping the
    declared content type, and returns its upload id."""
    return _guard(
        "create_multipart_upload",
        lambda: client.create_multipart_upload(
            settings.bucket, target.object_key, content_type=target.declared_content_type
        ),
    )


def find_upload_id(
    client: S3Client, settings: UploadStorageSettings, target: UploadTarget
) -> str | None:
    """The in-progress multipart upload for exactly this key, or `None`. The
    id is never stored in the database: the key is unique per session, so
    storage itself is the record."""
    uploads = _guard(
        "list_multipart_uploads",
        lambda: client.list_multipart_uploads(settings.bucket, prefix=target.object_key),
    )
    matches = [u for u in uploads if u.get("Key") == target.object_key and u.get("UploadId")]
    if len(matches) > 1:
        raise UploadStorageUnavailableError(
            "list_multipart_uploads: more than one upload for a key"
        )
    return matches[0]["UploadId"] if matches else None


def list_uploaded_parts(
    client: S3Client, settings: UploadStorageSettings, target: UploadTarget, upload_id: str
) -> list[UploadedPart]:
    raw = _guard(
        "list_parts", lambda: client.list_parts(settings.bucket, target.object_key, upload_id)
    )
    parts: list[UploadedPart] = []
    for item in raw:
        try:
            parts.append(
                UploadedPart(
                    part_number=int(item["PartNumber"]),
                    size_bytes=int(item["Size"]),
                    etag=str(item["ETag"]).strip('"'),
                )
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise UploadStorageUnavailableError("list_parts: malformed part entry") from exc
    parts.sort(key=lambda part: part.part_number)
    return parts


def presign_part_urls(
    settings: UploadStorageSettings,
    target: UploadTarget,
    upload_id: str,
    part_numbers: list[int],
    *,
    now: datetime | None = None,
) -> list[PartGrant]:
    """Short-lived PUT URLs, one per requested part. Raises `UploadExpired`
    when the session has no lifetime left, `ValueError` for a bad part list."""
    moment = now if now is not None else datetime.now(UTC)
    plan = plan_parts(target.declared_size_bytes)
    if (
        not isinstance(part_numbers, list)
        or not part_numbers
        or len(part_numbers) > MAX_PART_URLS_PER_REQUEST
        or any(isinstance(n, bool) or not isinstance(n, int) for n in part_numbers)
        or len(set(part_numbers)) != len(part_numbers)
        or any(not 1 <= n <= plan.part_count for n in part_numbers)
    ):
        raise ValueError(
            f"part_numbers must be 1..{MAX_PART_URLS_PER_REQUEST} distinct ints "
            f"in 1..{plan.part_count}"
        )
    remaining = int((target.expires_at - moment).total_seconds())
    if remaining < 1:
        raise UploadExpired
    lifetime = min(PRESIGN_MAX_EXPIRES_SECONDS, remaining)
    grants: list[PartGrant] = []
    for number in part_numbers:
        length = plan.expected_size(number)
        url = presign_url(
            method="PUT",
            endpoint=settings.endpoint,
            credentials=settings.credentials,
            canonical_uri=f"/{settings.bucket}/{target.object_key}",
            query={"partNumber": str(number), "uploadId": upload_id},
            signed_headers={"content-length": str(length)},
            expires_seconds=lifetime,
            now=moment,
        )
        grants.append(
            PartGrant(
                part_number=number,
                method="PUT",
                url=url,
                content_length=length,
                expires_at=moment + timedelta(seconds=lifetime),
            )
        )
    return grants


def abort_upload(client: S3Client, settings: UploadStorageSettings, target: UploadTarget) -> None:
    """Aborts the in-progress multipart upload (if any) and deletes any object
    at the session's key. Idempotent."""
    upload_id = find_upload_id(client, settings, target)
    if upload_id is not None:
        _guard(
            "abort_multipart_upload",
            lambda: client.abort_multipart_upload(settings.bucket, target.object_key, upload_id),
        )
    _delete_object(client, settings, target)


def _delete_object(client: S3Client, settings: UploadStorageSettings, target: UploadTarget) -> None:
    try:
        client.delete_object(settings.bucket, target.object_key)
    except S3Error as exc:
        if exc.status_code != 404:
            raise _storage_error("delete_object", exc) from exc
    except (OSError, urllib.error.URLError, TimeoutError) as exc:
        raise _storage_error("delete_object", exc) from exc


def _reject(
    client: S3Client, settings: UploadStorageSettings, target: UploadTarget, reason: str
) -> UploadRejection:
    """Removes whatever storage holds for the session, then returns the verdict
    for the caller to raise. If cleanup itself fails the outage wins: the
    session stays open rather than recording a rejection over live bytes."""
    abort_upload(client, settings, target)
    return UploadRejection(reason)


def verify_and_complete(
    client: S3Client,
    settings: UploadStorageSettings,
    target: UploadTarget,
    *,
    now: datetime | None = None,
) -> VerifiedObject:
    """Completes the multipart upload and verifies the stored object against
    the session's declared size, SHA-256 and content type. Returns the values
    storage actually holds; never the declared ones."""
    moment = now if now is not None else datetime.now(UTC)
    if moment > target.expires_at:
        raise UploadExpired
    plan = plan_parts(target.declared_size_bytes)
    if normalize_content_type(target.declared_content_type) is None:
        raise _reject(client, settings, target, "unsupported_content_type")

    upload_id = find_upload_id(client, settings, target)
    if upload_id is not None:
        parts = list_uploaded_parts(client, settings, target, upload_id)
        numbers = [part.part_number for part in parts]
        if len(set(numbers)) != len(numbers) or any(not 1 <= n <= plan.part_count for n in numbers):
            raise _reject(client, settings, target, "unexpected_part")
        for part in parts:
            if part.size_bytes != plan.expected_size(part.part_number):
                raise _reject(client, settings, target, "part_size_mismatch")
        missing = tuple(n for n in range(1, plan.part_count + 1) if n not in set(numbers))
        if missing:
            raise UploadIncomplete(missing)
        _guard(
            "complete_multipart_upload",
            lambda: client.complete_multipart_upload(
                settings.bucket,
                target.object_key,
                upload_id,
                [(part.part_number, part.etag) for part in parts],
            ),
        )
    # With no upload in progress an earlier finalize may already have completed
    # it (a crash before the database write); the object is verified from
    # scratch below either way, and a missing object is simply "nothing there".
    try:
        head = client.head_object(settings.bucket, target.object_key)
    except S3Error as exc:
        if exc.status_code == 404:
            raise UploadIncomplete(tuple(range(1, plan.part_count + 1))) from exc
        raise _storage_error("head_object", exc) from exc
    except (OSError, urllib.error.URLError, TimeoutError) as exc:
        raise _storage_error("head_object", exc) from exc

    try:
        stored_size = int(head.get("content-length", ""))
    except ValueError as exc:
        raise _storage_error("head_object", exc) from exc
    if stored_size != target.declared_size_bytes:
        raise _reject(client, settings, target, "size_mismatch")

    digest = hashlib.sha256()
    first = b""
    seen = 0
    try:
        for chunk in client.stream_object(settings.bucket, target.object_key):
            first += chunk[: SNIFF_BYTES - len(first)]
            digest.update(chunk)
            seen += len(chunk)
            if seen > target.declared_size_bytes:
                raise _reject(client, settings, target, "size_mismatch")
    except S3Error as exc:
        raise _storage_error("get_object", exc) from exc
    except (OSError, urllib.error.URLError, TimeoutError) as exc:
        raise _storage_error("get_object", exc) from exc
    if seen != target.declared_size_bytes:
        raise _reject(client, settings, target, "size_mismatch")
    if digest.hexdigest() != target.declared_sha256:
        raise _reject(client, settings, target, "sha256_mismatch")
    reason = sniff_content(
        target.declared_content_type, first, complete=target.declared_size_bytes <= SNIFF_BYTES
    )
    if (
        reason is None
        and normalize_content_type(target.declared_content_type) in _ZIP_MAGIC_BY_TYPE
    ):
        reason = inspect_xlsx(client, settings, target)
    if reason is not None:
        raise _reject(client, settings, target, reason)
    return VerifiedObject(
        size_bytes=seen, sha256=digest.hexdigest(), content_type=target.declared_content_type
    )
