"""OOXML archive and XML safety primitives (FTF-0505; FINE_TUNING_FACTORY_RELEASE_1.md
Section 8.1, 67.2, 67.3).

Pure and synchronous: no database, no network, nothing here evaluates a cell, a
formula or a macro. Every function either returns or raises `OoxmlRefused`, whose
`code` is one of the importer failure codes (`unsafe_archive`, `unsafe_workbook`,
`unsupported_workbook`, `external_link_present`, `xlsx_malformed`,
`limit_exceeded`).

These are the checks FTF-0501's `inspect_xlsx` already makes on the stored
object (apps/api/app/datasets/direct_object_storage_upload.py), ported so the
worker applies them again to the bytes it actually reads: the end-of-archive
totals before `zipfile` allocates for them, the entry and directory bounds,
traversal / absolute / backslash names, case-insensitive duplicate names, and a
DOCTYPE refused at the expat parser in every encoding. `tests/tasks/
test_ftf_0505.py` imports both modules and pins the shared bounds equal, so the
two cannot drift apart.

Decompressed bytes are always counted from what is actually read, in 64 KiB
blocks, per part and in total (`PartBudget`); the sizes in the zip directory
are never the only thing trusted.
"""

from __future__ import annotations

import struct
import zipfile
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from typing import Final
from xml.parsers import expat

__all__ = [
    "CONTENT_TYPES_PART",
    "MACRO_PART_PREFIXES",
    "MAX_CONTENT_TYPES_BYTES",
    "MAX_DIRECTORY_BYTES",
    "MAX_ENTRIES",
    "MAX_SMALL_PART_BYTES",
    "MAX_TOKEN_BYTES",
    "MAX_UNCOMPRESSED_BYTES",
    "MAX_XML_DEPTH",
    "OLE_MAGIC",
    "READ_BLOCK_BYTES",
    "XLSX_CONTENT_TYPE",
    "OoxmlRefused",
    "PartBudget",
    "check_part_names",
    "end_record_totals",
    "mentions_declaration",
    "new_xml_parser",
    "read_part_blocks",
    "read_tail",
    "tail_size",
]

XLSX_CONTENT_TYPE: Final = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

# The FTF-0501 bounds, pinned equal by a drift test.
MAX_ENTRIES: Final = 10_000
MAX_DIRECTORY_BYTES: Final = 4 * 1024**2
MAX_UNCOMPRESSED_BYTES: Final = 10 * 1024**3
MAX_CONTENT_TYPES_BYTES: Final = 1024**2
CONTENT_TYPES_PART: Final = "[Content_Types].xml"

# Parts read whole (workbook, relationships, content types) are small by nature.
MAX_SMALL_PART_BYTES: Final = 4 * 1024**2
READ_BLOCK_BYTES: Final = 64 * 1024
# A start tag, comment or text run longer than this without one parser event is
# not a spreadsheet: expat would otherwise buffer it whole.
MAX_TOKEN_BYTES: Final = 1024**2
MAX_XML_DEPTH: Final = 64

OLE_MAGIC: Final = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"

# Parts whose mere presence makes a package a macro / active-content carrier.
MACRO_PART_PREFIXES: Final = (
    "xl/vbaproject.bin",
    "xl/activex/",
    "xl/embeddings/",
    "xl/macrosheets/",
)

_ZIP_EOCD: Final = b"PK\x05\x06"
_ZIP64_LOCATOR: Final = b"PK\x06\x07"
_ZIP64_EOCD: Final = b"PK\x06\x06"
_ZIP_EOCD_SIZE: Final = 22
_ZIP64_LOCATOR_SIZE: Final = 20
_ZIP64_EOCD_SIZE: Final = 56
_ZIP_MAX_COMMENT: Final = 0xFFFF

_CODES: Final = frozenset(
    {
        "duplicate_sheet_name",
        "external_link_present",
        "limit_exceeded",
        "parse_timeout",
        "unsafe_archive",
        "unsafe_workbook",
        "unsupported_workbook",
        "xlsx_malformed",
    }
)


class OoxmlRefused(Exception):  # noqa: N818 - a verdict about the whole package, not an error class
    """The package or part cannot be read safely. `code` is a failure code."""

    def __init__(self, code: str) -> None:
        if code not in _CODES:
            raise ValueError("unknown refusal code")
        super().__init__(code)
        self.code = code


# -- the end of the archive ---------------------------------------------------


def tail_size(total_size: int) -> int:
    """How many trailing bytes hold every end-of-archive record zipfile will read."""
    return min(
        total_size, _ZIP_MAX_COMMENT + _ZIP_EOCD_SIZE + _ZIP64_LOCATOR_SIZE + _ZIP64_EOCD_SIZE
    )


def read_tail(spool: object) -> bytes:
    """The last `tail_size` bytes of a seekable binary file (position left at 0)."""
    seek, read = spool.seek, spool.read  # type: ignore[attr-defined]
    size = seek(0, 2)
    wanted = tail_size(size)
    seek(size - wanted)
    data: bytes = read(wanted)
    seek(0)
    return data


def end_record_totals(tail: bytes) -> tuple[int, int, int] | None:
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


# -- names --------------------------------------------------------------------


def check_part_names(infos: list[zipfile.ZipInfo], *, max_total_bytes: int) -> None:
    """The directory-level checks, none of which reads a part. Raises
    `OoxmlRefused("unsafe_archive")` for too many entries, an absolute / backslash
    / traversal name, two names equal after casefold (OPC part names are
    case-insensitive), or declared sizes that add up past `max_total_bytes`;
    `unsupported_workbook` for an encrypted entry or a compression method other
    than stored / deflate."""
    if len(infos) > MAX_ENTRIES:
        raise OoxmlRefused("unsafe_archive")
    seen: set[str] = set()
    for info in infos:
        name = info.filename
        if name.startswith("/") or "\\" in name or ".." in name.split("/"):
            raise OoxmlRefused("unsafe_archive")
        folded = name.casefold()
        if folded in seen:
            raise OoxmlRefused("unsafe_archive")
        seen.add(folded)
    if sum(info.file_size for info in infos) > max_total_bytes:
        raise OoxmlRefused("unsafe_archive")
    for info in infos:
        if info.flag_bits & 0x1 or info.compress_type not in (
            zipfile.ZIP_STORED,
            zipfile.ZIP_DEFLATED,
        ):
            raise OoxmlRefused("unsupported_workbook")


# -- bounded reads ------------------------------------------------------------


@dataclass
class PartBudget:
    """Counts the decompressed bytes actually read, across all parts."""

    remaining: int

    def spend(self, size: int) -> None:
        self.remaining -= size
        if self.remaining < 0:
            raise OoxmlRefused("unsafe_archive")


def read_part_blocks(
    archive: zipfile.ZipFile,
    info: zipfile.ZipInfo,
    *,
    part_cap: int,
    budget: PartBudget,
    over_cap_code: str = "unsafe_archive",
    tick: Callable[[], None] | None = None,
) -> Iterator[bytes]:
    """The decompressed bytes of one part, in blocks of at most `READ_BLOCK_BYTES`.
    Bytes are counted as they are produced: past `part_cap` for this part raises
    `over_cap_code`, past the archive-wide `budget` raises `unsafe_archive`. A
    damaged entry (bad CRC, truncated stream) is `xlsx_malformed`. `tick` runs
    before every block (the caller's deadline)."""
    produced = 0
    try:
        with archive.open(info) as handle:
            while True:
                if tick is not None:
                    tick()
                block = handle.read(READ_BLOCK_BYTES)
                if not block:
                    return
                produced += len(block)
                if produced > part_cap:
                    raise OoxmlRefused(over_cap_code)
                budget.spend(len(block))
                yield block
    except OoxmlRefused:
        raise
    except (
        zipfile.BadZipFile,
        zipfile.LargeZipFile,
        EOFError,
        NotImplementedError,
        OverflowError,
        RuntimeError,
        ValueError,
        OSError,
        struct.error,
    ):
        raise OoxmlRefused("xlsx_malformed") from None


# -- XML ----------------------------------------------------------------------


def mentions_declaration(document: bytes) -> bool:
    for codec in ("latin-1", "utf-16", "utf-16-le", "utf-16-be"):
        text = document.decode(codec, errors="ignore").lower()
        if "<!doctype" in text or "<!entity" in text:
            return True
    return False


def new_xml_parser(*, namespaces: bool = True) -> expat.XMLParserType:
    """A fresh expat parser that refuses any DOCTYPE (the only place an entity,
    notation or external subset can be declared), whatever the encoding: a byte
    search for `<!doctype` misses UTF-16. Parameter entities are never parsed and
    no external entity handler is installed."""

    def refuse(*_args: object) -> None:
        raise OoxmlRefused("unsafe_archive")

    parser = expat.ParserCreate(namespace_separator=" ") if namespaces else expat.ParserCreate()
    parser.StartDoctypeDeclHandler = refuse
    parser.SetParamEntityParsing(expat.XML_PARAM_ENTITY_PARSING_NEVER)
    return parser
