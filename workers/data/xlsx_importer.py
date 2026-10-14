"""XLSX importer (FTF-0505; FINE_TUNING_FACTORY_RELEASE_1.md Section 7, 8.1, 9, 67.2, 67.3).

Turns an already-authorised, size/hash-bound byte stream (FTF-0502-P1's
`ChunkReader`) into FTF-0502-P1 import events, one worksheet row at a time.

Values only. Nothing is evaluated, nothing is retrieved, nothing is trusted that
a spreadsheet application would compute:

- A cell with a formula (`<f>`, including a shared `<f t="shared" si=".."/>` with
  no text and an array formula) or of type `str` (a formula's string result) in a
  mapped column rejects its row (`formula_cell`). The cached `<v>` is never used.
  A cell of type `e` in a mapped column rejects it (`cell_error_value`).
- Macro carriers (`xl/vbaProject.bin`, `activeX`, `embeddings`, macro sheets or a
  macro-enabled content type) are `unsafe_workbook`; external links (`xl/
  externalLinks/`, `xl/connections.xml`, `xl/queryTables/` or any relationship with
  `TargetMode="External"`) are `external_link_present`. Neither is opened or fetched.
- The archive is bounded before it is read (entries, directory size, end-of-archive
  totals read before `zipfile` allocates for them, declared and counted decompressed
  bytes, traversal / absolute / backslash / casefold-duplicate names) and every XML
  part is parsed incrementally by expat with any DOCTYPE refused, so there is no
  entity, no external subset and no billion-laughs expansion (`workers/data/
  ooxml_safety.py`, ported from FTF-0501's upload-time checks).
- An OLE/CFB container (an encrypted / password-protected workbook), a non-zip, strict
  OOXML, a template, an encrypted zip entry or a compression method other than
  stored / deflate is `unsupported_workbook`.

Everything about the sheet is declared, nothing is guessed. The mapping's `xlsx`
block names the sheet (`sheet_name`, matched exactly: no casefold, no strip; a name
that does not exist is fatal `mapping_column_missing` and never falls back to
another sheet, hidden or visible) and the header row (`header_row`: the number of
non-blank rows before the header, as for CSV; 0 is the first non-blank row). Header
cells are matched by exact text; a repeated non-empty header is fatal
`duplicate_header`; a mapped column that is absent is fatal `mapping_column_missing`.

Every cell stays the exact text it was stored as: shared strings, inline strings and
rich-text runs are concatenated exactly (phonetic `rPh` runs excluded), ECMA-376
`_xHHHH_` escapes are decoded once (`_x005F_` is `_`, and the result is never
decoded again), numbers / booleans / dates keep their literal `<v>` text (no float
conversion, no date-serial interpretation: disclosed). Formula-looking plain text,
leading zeros in text cells, Arabic and right-to-left text are data and survive.
Merged ranges hold their value in the top-left cell only, so a covered cell is an
empty cell (`required_field_empty` when the slot is required).

Fatal versus rejected. A problem the reader cannot resynchronise after (a malformed
or out-of-order cell reference, an index into the shared strings that does not exist,
an oversize cell, too many cells, a row or column beyond the sheet bounds, a missing
or repeated header) ends the import: the report's `state` is `failed` and
`failure_code` is the code. The sheet row it happened on is returned beside the
report (`XlsxImportOutcome.fatal_locator`, `row:<n>`), or None for a problem with the
package as a whole. A problem confined to one row becomes a `RowRejection`.

Disclosed choices (each pinned by a test):

- Locators are `row:<n>`, the 1-based sheet row number (what a person opening the file
  sees). The logical ordinal of a data row is its candidate's `record_index`.
- Rows and cells must strictly increase by `r` (a missing `r` is the previous one plus
  one). A duplicate, decreasing or non-canonical reference is fatal `xlsx_malformed`.
  The sheet's own `<dimension>` is ignored (untrusted).
- Bounds are refused, never truncated: a row past 1,048,576 or a column past XFD is
  `sheet_out_of_bounds`; more than `max_cells` cells (every `<c>`, mapped or not) is
  `too_many_cells`; shared strings that decompress past `max_shared_strings_bytes`
  (default 256 MiB, the job memory quota) are `limit_exceeded`; any one cell or string
  longer than `limits.max_record_bytes` characters is `field_too_large`. A caller may
  lower any bound, never raise one.
- A formula or error cell in the header row is fatal `xlsx_malformed`: a header is
  never taken from a cached result. Only the sheet's first `header_row` rows are
  skipped; cells to the right of the header are ignored (they are unmapped data).
- A lone surrogate left by a `_xD800_` style escape in a mapped field rejects the row
  (`invalid_unicode`); a NUL in a mapped field is `nul_character`.
- The source is spooled to one anonymous `tempfile.TemporaryFile` (mode 0600, no
  directory entry, deleted on close) while `ChunkReader` hashes it; `zipfile` opens it
  only after the byte count and sha256 matched.
- Only a verified content type of the XLSX content type is imported. Any other value is
  a `failed` report, `content_type_not_supported_by_importer`, before a byte is read.
- Tabular sources carry no object keys: a `key` slot or the `messages` shape (a JSON
  pointer into an object) is a `MappingInvalidError`. Tool calls travel in a
  `messages_json` column.

Scope: pure and synchronous, no database and no network (the packet's
`database_changes`: "No independent schema change. Consume frozen contract"). No import
route exists yet to call it; `proving_symbol` stays blank, as for the sibling importers.
"""

from __future__ import annotations

import enum
import hashlib
import hmac
import json
import re
import tempfile
import zipfile
from collections import deque
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass
from typing import IO, Final
from xml.parsers import expat

from workers.data.canonical_conversation_normalizer import ToolArgumentValidator
from workers.data.import_common import (
    DEFAULT_LIMITS,
    AcceptedRecord,
    BlankLine,
    ChunkReader,
    ImportAborted,
    ImportAccumulator,
    ImportEvent,
    ImportLimits,
    ImportMapping,
    ImportReport,
    MappingInvalidError,
    RowMappingError,
    RowRejection,
    Slot,
    apply_mapping,
    normalize_mapped_record,
    to_dataset_record_candidate,
)
from workers.data.ooxml_safety import (
    MACRO_PART_PREFIXES,
    MAX_CONTENT_TYPES_BYTES,
    MAX_DIRECTORY_BYTES,
    MAX_ENTRIES,
    MAX_SMALL_PART_BYTES,
    MAX_TOKEN_BYTES,
    MAX_UNCOMPRESSED_BYTES,
    MAX_XML_DEPTH,
    OLE_MAGIC,
    XLSX_CONTENT_TYPE,
    OoxmlRefused,
    PartBudget,
    check_part_names,
    end_record_totals,
    mentions_declaration,
    new_xml_parser,
    read_part_blocks,
    read_tail,
)

__all__ = [
    "DEFAULT_XLSX_LIMITS",
    "XLSX_CONTENT_TYPES",
    "XLSX_FATAL_CODES",
    "XLSX_MAX_COLUMN",
    "XLSX_MAX_ROW",
    "XlsxAborted",
    "XlsxEventStream",
    "XlsxImportOutcome",
    "XlsxLimits",
    "decode_xml_escapes",
    "run_xlsx_import",
    "xlsx_events",
]

XLSX_CONTENT_TYPES: Final = frozenset({XLSX_CONTENT_TYPE})
XLSX_MAX_ROW: Final = 1_048_576
XLSX_MAX_COLUMN: Final = 16_384  # XFD is the 16,384th column
XLSX_FATAL_CODES: Final = frozenset(
    {
        "duplicate_header",
        "duplicate_sheet_name",
        "external_link_present",
        "field_too_large",
        "limit_exceeded",
        "mapping_column_missing",
        "sheet_out_of_bounds",
        "too_many_cells",
        "unsafe_archive",
        "unsafe_workbook",
        "unsupported_workbook",
        "xlsx_malformed",
    }
)

IMPORTER_NAME: Final = "xlsx"
IMPORTER_VERSION: Final = "1"
SOURCE_TYPE: Final = "upload"

_MAIN_NS: Final = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
_STRICT_NS: Final = "http://purl.oclc.org/ooxml/spreadsheetml/main"
# Namespaces are matched exactly (they are case-sensitive URIs); read through this table.
_NAMESPACES: Final = {"main": _MAIN_NS, "strict": _STRICT_NS}
_DOC_REL_NS: Final = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_PKG_REL_NS: Final = "http://schemas.openxmlformats.org/package/2006/relationships"
_CT_NS: Final = "http://schemas.openxmlformats.org/package/2006/content-types"
_REL_WORKSHEET: Final = _DOC_REL_NS + "/worksheet"
_REL_SHARED_STRINGS: Final = _DOC_REL_NS + "/sharedStrings"
_WORKBOOK_PART: Final = "xl/workbook.xml"
_CONTENT_TYPES_KEY: Final = "[content_types].xml"  # CONTENT_TYPES_PART, casefolded
_WORKBOOK_RELS_KEY: Final = "xl/_rels/workbook.xml.rels"
_EXTERNAL_PREFIXES: Final = ("xl/externallinks/", "xl/querytables/")
_EXTERNAL_PARTS: Final = frozenset({"xl/connections.xml"})
_CELL_REF: Final = re.compile(r"([A-Z]{1,3})([1-9][0-9]*)")
_ROW_NUMBER: Final = re.compile(r"[1-9][0-9]*")
_SHARED_INDEX: Final = re.compile(r"[0-9]{1,9}")
_XML_ESCAPE: Final = re.compile(r"_x([0-9A-Fa-f]{4})_")
_SURROGATE: Final = re.compile("[\ud800-\udfff]")


_XLSX_LIMIT_DEFAULTS: Final = {
    "max_cells": 50_000_000,
    "max_shared_strings_bytes": 256 * 1024**2,
    "max_total_uncompressed_bytes": MAX_UNCOMPRESSED_BYTES,
}


@dataclass(frozen=True)
class XlsxLimits:
    """The XLSX-specific bounds. A caller may lower any of them, never raise one."""

    max_cells: int = _XLSX_LIMIT_DEFAULTS["max_cells"]
    max_shared_strings_bytes: int = _XLSX_LIMIT_DEFAULTS["max_shared_strings_bytes"]
    max_total_uncompressed_bytes: int = _XLSX_LIMIT_DEFAULTS["max_total_uncompressed_bytes"]

    def __post_init__(self) -> None:
        for name, default in _XLSX_LIMIT_DEFAULTS.items():
            value = getattr(self, name)
            if type(value) is not int or value < 1:
                raise ValueError(f"{name} must be a positive int")
            if value > default:
                raise ValueError(f"{name} cannot exceed its default without load evidence")


DEFAULT_XLSX_LIMITS: Final = XlsxLimits()


class XlsxAborted(ImportAborted):
    """A fatal XLSX problem: the import ends `failed` with `failure_code`, and
    `locator` names the sheet row (`row:<n>`) it happened on, or is None when the
    package as a whole is at fault."""

    def __init__(self, failure_code: str, row: int | None = None) -> None:
        if XLSX_FATAL_CODES.isdisjoint((failure_code,)):
            raise ValueError("not an XLSX fatal code")
        super().__init__("failed", failure_code)
        self.locator = None if row is None else f"row:{max(row, 1)}"


@dataclass(frozen=True)
class XlsxImportOutcome:
    report: ImportReport
    fatal_locator: str | None


@dataclass(frozen=True)
class _Options:
    sheet_name: str
    header_row: int
    slots: tuple[tuple[str, Slot], ...]


def _mapped_slots(mapping: ImportMapping) -> tuple[tuple[str, Slot], ...]:
    named: list[tuple[str, Slot | None]] = [
        ("prompt", mapping.prompt),
        ("completion", mapping.completion),
        ("system", mapping.system),
        ("messages", mapping.messages_slot),
        ("group_key", mapping.group_key),
    ]
    named.extend((f"metadata.{name}", slot) for name, slot in mapping.metadata_slots)
    return tuple((name, slot) for name, slot in named if slot is not None)


def _options(mapping: ImportMapping) -> _Options:
    """The `xlsx` block and the slots of a MappingSpec, refused up front when they
    cannot describe a worksheet. Raises `MappingInvalidError`."""
    block = mapping.document.get("xlsx")
    if type(block) is not dict:
        raise MappingInvalidError("missing_key")
    if mapping.messages_pointer is not None:
        raise MappingInvalidError("bad_shape")
    slots = _mapped_slots(mapping)
    for _name, slot in slots:
        if slot.kind not in ("header", "index", "letters"):
            raise MappingInvalidError("bad_slot")
    return _Options(str(block["sheet_name"]), int(block["header_row"]), slots)


def _schema_sha256(sheet_index: int, headers: list[str]) -> str:
    text = json.dumps([sheet_index, headers], ensure_ascii=True, separators=(",", ":"))
    return hashlib.sha256(text.encode("ascii")).hexdigest()


def decode_xml_escapes(text: str) -> str:
    """ECMA-376 `_xHHHH_` escapes (Part 1, 22.4.2.4), decoded in one left-to-right
    pass: `_x005F_` is `_`, and what it produces is never scanned again, so
    `_x005F_x0041_` is the literal text `_x0041_`. UTF-16 surrogate escapes that
    form a pair are joined; a lone one stays a lone surrogate for the caller to
    refuse."""
    out = _XML_ESCAPE.sub(lambda match: chr(int(match.group(1), 16)), text)
    if _SURROGATE.search(out):
        out = out.encode("utf-16-le", "surrogatepass").decode("utf-16-le", "surrogatepass")
    return out


# -- XML scanners -----------------------------------------------------------------


def _local(name: str) -> str | None:
    """The local name of an element in the SpreadsheetML main namespace, else None."""
    namespace, _sep, local = name.rpartition(" ")
    return local if namespace == _NAMESPACES["main"] else None


def _ends(path: list[str | None], *suffix: str) -> bool:
    return len(path) >= len(suffix) and path[-len(suffix) :] == list(suffix)


def _root_refusal(name: str) -> OoxmlRefused:
    return OoxmlRefused(
        "unsupported_workbook"
        if name.rpartition(" ")[0] == _NAMESPACES["strict"]
        else "xlsx_malformed"
    )


class _Scanner:
    """Common expat plumbing: the element path, depth bound and event counter."""

    def __init__(self) -> None:
        self.path: list[str | None] = []
        self.events = 0

    def _enter(self, name: str) -> None:
        self.events += 1
        if len(self.path) >= MAX_XML_DEPTH:
            raise OoxmlRefused("unsafe_archive")
        self.path.append(_local(name))

    def tick(self, *_args: object) -> None:
        """A comment or processing instruction: proof the parser is making progress."""
        self.events += 1


class _SharedStringsScanner(_Scanner):
    def __init__(self, max_string: int) -> None:
        super().__init__()
        self.strings: list[str] = []
        self._max_string = max_string
        self._runs: list[str] = []
        self._piece: list[str] = []
        self._size = 0
        self._in_text = False

    def start(self, name: str, _attrs: object) -> None:
        if not self.path and _local(name) != "sst":
            raise _root_refusal(name)
        self._enter(name)
        if self._in_text:
            raise OoxmlRefused("xlsx_malformed")
        if _ends(self.path, "sst", "si"):
            self._runs, self._size = [], 0
        elif _ends(self.path, "sst", "si", "t") or _ends(self.path, "sst", "si", "r", "t"):
            self._in_text, self._piece = True, []

    def chars(self, data: str) -> None:
        self.events += 1
        if not self._in_text:
            return
        self._size += len(data)
        if self._size > self._max_string:
            raise XlsxAborted("field_too_large")
        self._piece.append(data)

    def end(self, _name: str) -> None:
        self.events += 1
        if self._in_text:
            self._runs.append("".join(self._piece))
            self._in_text = False
        elif _ends(self.path, "sst", "si"):
            self.strings.append(decode_xml_escapes("".join(self._runs)))
        self.path.pop()


class _Kind(enum.Enum):
    """What a cell holds: its stored text, a formula (result never used) or an error."""

    VALUE = enum.auto()
    FORMULA = enum.auto()
    ERROR = enum.auto()


class _Type(enum.Enum):
    """The cell `t` attribute, as far as this importer treats it differently."""

    SHARED = enum.auto()  # s: an index into the shared strings
    INLINE = enum.auto()  # inlineStr
    FORMULA_RESULT = enum.auto()  # str: a formula's string result
    ERROR = enum.auto()  # e
    LITERAL = enum.auto()  # n, b, d (and no t): the literal <v> text


_CELL_TYPES: Final = {
    "s": _Type.SHARED,
    "inlineStr": _Type.INLINE,
    "str": _Type.FORMULA_RESULT,
    "e": _Type.ERROR,
    "n": _Type.LITERAL,
    "b": _Type.LITERAL,
    "d": _Type.LITERAL,
}


@dataclass
class _RawRow:
    number: int
    cells: dict[int, tuple[_Kind, str]]
    nonblank: bool


class _SheetScanner(_Scanner):
    """Turns the SpreadsheetML of one worksheet into `_RawRow`s as expat feeds it.

    `wanted` is the set of columns whose cells are kept (None: all); a cell outside
    it is still validated and counted, only its text is not stored."""

    def __init__(self, shared: list[str], max_cells: int, max_cell_chars: int) -> None:
        super().__init__()
        self.rows: deque[_RawRow] = deque()
        self.wanted: frozenset[int] | None = None
        self.row_number = 0
        self._shared = shared
        self._max_cells = max_cells
        self._max_cell_chars = max_cell_chars
        self._cells = 0
        self._last_row = 0
        self._last_col = -1
        self._row_cells: dict[int, tuple[_Kind, str]] = {}
        self._nonblank = False
        self._col = 0
        self._type = _Type.LITERAL
        self._has_f = False
        self._has_v = False
        self._has_is = False
        self._v_text = ""
        self._inline: list[str] = []
        self._piece: list[str] = []
        self._cell_chars = 0
        self._in_v = False
        self._in_t = False

    def _fatal(self, code: str) -> XlsxAborted:
        return XlsxAborted(code, self.row_number or None)

    # -- handlers -----------------------------------------------------------------

    def start(self, name: str, attrs: Mapping[str, str]) -> None:
        if not self.path and _local(name) != "worksheet":
            raise _root_refusal(name)
        self._enter(name)
        path = self.path
        if self._in_v or self._in_t:
            raise self._fatal("xlsx_malformed")
        if _ends(path, "worksheet", "sheetData", "row"):
            self._start_row(attrs)
        elif _ends(path, "worksheet", "sheetData", "row", "c"):
            self._start_cell(attrs)
        elif _ends(path, "worksheet", "sheetData", "row", "c", "v"):
            if self._has_v:
                raise self._fatal("xlsx_malformed")
            self._has_v, self._in_v, self._piece = True, True, []
        elif _ends(path, "worksheet", "sheetData", "row", "c", "f"):
            self._has_f = True
        elif _ends(path, "worksheet", "sheetData", "row", "c", "is"):
            if self._has_is:
                raise self._fatal("xlsx_malformed")
            self._has_is = True
        elif _ends(path, "worksheet", "sheetData", "row", "c", "is", "t") or _ends(
            path, "worksheet", "sheetData", "row", "c", "is", "r", "t"
        ):
            self._in_t, self._piece = True, []

    def _start_row(self, attrs: Mapping[str, str]) -> None:
        given = attrs.get("r")
        if given is None:
            number = self._last_row + 1
        else:
            if not _ROW_NUMBER.fullmatch(given):
                raise XlsxAborted("xlsx_malformed", self._last_row or None)
            number = _row_number(given)
        self.row_number = number
        if number > XLSX_MAX_ROW:
            raise self._fatal("sheet_out_of_bounds")
        if number <= self._last_row:
            raise self._fatal("xlsx_malformed")
        self._last_row, self._last_col = number, -1
        self._row_cells, self._nonblank = {}, False

    def _start_cell(self, attrs: Mapping[str, str]) -> None:
        cell_type = _Type.LITERAL
        if "t" in attrs:
            if attrs["t"] not in _CELL_TYPES:
                raise self._fatal("xlsx_malformed")
            cell_type = _CELL_TYPES[attrs["t"]]
        reference = attrs.get("r")
        if reference is None:
            column = self._last_col + 1
        else:
            found = _CELL_REF.fullmatch(reference)
            if found is None:
                raise self._fatal("xlsx_malformed")
            letters, digits = found.groups()
            column = _column_index(letters)
            if _row_number(digits) > XLSX_MAX_ROW:
                raise self._fatal("sheet_out_of_bounds")
            if _row_number(digits) != self.row_number:
                raise self._fatal("xlsx_malformed")
        if column >= XLSX_MAX_COLUMN:
            raise self._fatal("sheet_out_of_bounds")
        if column <= self._last_col:
            raise self._fatal("xlsx_malformed")
        self._cells += 1
        if self._cells > self._max_cells:
            raise self._fatal("too_many_cells")
        self._last_col, self._col = column, column
        self._type = cell_type
        self._has_f = self._has_v = self._has_is = False
        self._v_text = ""
        self._inline = []
        self._cell_chars = 0

    def chars(self, data: str) -> None:
        self.events += 1
        if not (self._in_v or self._in_t):
            return
        self._cell_chars += len(data)
        if self._cell_chars > self._max_cell_chars:
            raise self._fatal("field_too_large")
        self._piece.append(data)

    def end(self, _name: str) -> None:
        self.events += 1
        path = self.path
        if self._in_v:
            self._v_text = "".join(self._piece)
            self._in_v = False
        elif self._in_t:
            self._inline.append("".join(self._piece))
            self._in_t = False
        elif _ends(path, "worksheet", "sheetData", "row", "c"):
            self._finish_cell()
        elif _ends(path, "worksheet", "sheetData", "row"):
            self.rows.append(_RawRow(self.row_number, self._row_cells, self._nonblank))
            self._row_cells = {}
        path.pop()

    def _finish_cell(self) -> None:
        kind, text = _Kind.VALUE, ""
        if self._has_f or self._type is _Type.FORMULA_RESULT:
            kind = _Kind.FORMULA
        elif self._type is _Type.ERROR:
            kind = _Kind.ERROR
        elif self._type is _Type.SHARED:
            if not self._has_v or not _SHARED_INDEX.fullmatch(self._v_text):
                raise self._fatal("xlsx_malformed")
            index = int(self._v_text)
            if index >= len(self._shared):
                raise self._fatal("xlsx_malformed")
            text = self._shared[index]
        elif self._type is _Type.INLINE:
            text = decode_xml_escapes("".join(self._inline))
        else:  # n, b, d: the literal <v> text
            text = self._v_text
        if kind is not _Kind.VALUE or text != "":
            self._nonblank = True
        if self.wanted is None or self._col in self.wanted:
            self._row_cells[self._col] = (kind, text)


def _row_number(digits: str) -> int:
    """A row number written as `digits`; anything of 8 digits or more is past the
    1,048,576 rows of a sheet and is not turned into an int."""
    return XLSX_MAX_ROW + 1 if len(digits) > len(str(XLSX_MAX_ROW)) else int(digits)


def _header_position(value: str | int, headers: list[str]) -> int:
    """The column whose header cell is exactly `value`, or -1 (an empty name selects nothing:
    the gaps of a header row are not columns)."""
    text = str(value)
    return headers.index(text) if text and text in headers else -1


def _letters_position(value: str | int, _headers: list[str]) -> int:
    return _column_index(str(value))


def _index_position(value: str | int, _headers: list[str]) -> int:
    return int(value)


_COLUMN_RESOLVERS: Final = {
    "header": _header_position,
    "letters": _letters_position,
    "index": _index_position,
}


def _column_index(letters: str) -> int:
    index = 0
    for ch in letters:
        index = index * 26 + (ord(ch) - ord("A") + 1)
    return index - 1


# -- the package --------------------------------------------------------------------


@dataclass
class _Package:
    archive: zipfile.ZipFile
    sheet: zipfile.ZipInfo
    sheet_index: int
    shared: list[str]
    budget: PartBudget


def _parse_small(
    data: bytes,
    start: Callable[[str, dict[str, str]], None],
    end: Callable[[str], None] | None = None,
) -> None:
    """Parses a small XML part whole. A malformed document is `xlsx_malformed`, but
    one that still mentions a DOCTYPE / ENTITY (in any case or encoding) is an
    attempt, not an innocent defect: `unsafe_archive`."""
    parser = new_xml_parser()
    parser.StartElementHandler = start
    if end is not None:
        parser.EndElementHandler = end
    try:
        parser.Parse(data, True)
    except expat.ExpatError:
        raise OoxmlRefused(
            "unsafe_archive" if mentions_declaration(data) else "xlsx_malformed"
        ) from None


def _read_small(
    archive: zipfile.ZipFile,
    info: zipfile.ZipInfo,
    budget: PartBudget,
    tick: Callable[[], None],
    cap: int = MAX_SMALL_PART_BYTES,
) -> bytes:
    return b"".join(read_part_blocks(archive, info, part_cap=cap, budget=budget, tick=tick))


def _resolve_target(target: str) -> str:
    """A relationship target, relative to `xl/`, as a part name. An absolute path, a
    backslash, a `..` segment or a URL is `unsafe_archive`: the only thing followed is a
    relative path inside the package. Whether such a part exists is the caller's check."""
    if target.startswith("/") or "\\" in target or ":" in target:
        raise OoxmlRefused("unsafe_archive")
    segments = target.split("/")
    if ".." in segments:
        raise OoxmlRefused("unsafe_archive")
    return "xl/" + "/".join(segment for segment in segments if segment != ".")


def _content_types(document: bytes) -> tuple[dict[str, str], list[str]]:
    overrides: dict[str, str] = {}
    declared: list[str] = []

    def start(name: str, attrs: dict[str, str]) -> None:
        if name == f"{_CT_NS} Override":
            if "PartName" not in attrs or "ContentType" not in attrs:
                raise OoxmlRefused("xlsx_malformed")
            overrides[attrs["PartName"]] = attrs["ContentType"]
            declared.append(attrs["ContentType"])
        elif name == f"{_CT_NS} Default":
            if "ContentType" not in attrs:
                raise OoxmlRefused("xlsx_malformed")
            declared.append(attrs["ContentType"])

    _parse_small(document, start)
    return overrides, declared


def _relationships(document: bytes) -> list[dict[str, str]]:
    found: list[dict[str, str]] = []

    def start(name: str, attrs: dict[str, str]) -> None:
        if name == f"{_PKG_REL_NS} Relationship":
            found.append(dict(attrs))

    _parse_small(document, start)
    return found


def _workbook_sheets(document: bytes) -> list[tuple[str, str]]:
    """[(sheet name, relationship id)] in workbook order. The root must be a
    transitional-namespace `workbook`."""
    sheets: list[tuple[str, str]] = []
    path: list[str | None] = []

    def start(name: str, attrs: dict[str, str]) -> None:
        local = _local(name)
        if not path and local != "workbook":
            raise _root_refusal(name)
        path.append(local)
        if _ends(path, "workbook", "sheets", "sheet"):
            sheet_name, rid = attrs.get("name"), attrs.get(f"{_DOC_REL_NS} id")
            if sheet_name is None or rid is None:
                raise OoxmlRefused("xlsx_malformed")
            sheets.append((sheet_name, rid))

    def end(_name: str) -> None:
        path.pop()

    _parse_small(document, start, end)
    return sheets


def open_package(
    spool: IO[bytes],
    sheet_name: str,
    *,
    limits: XlsxLimits,
    max_cell_chars: int,
    tick: Callable[[], None],
) -> _Package:
    """Everything that must hold before a single cell is read. Raises
    `OoxmlRefused` (mapped to a fatal code by the caller) or, for a shared-strings
    cell that is too large, `XlsxAborted`."""
    spool.seek(0)
    head = spool.read(8)
    spool.seek(0)
    if head.startswith(OLE_MAGIC) or not head.startswith(b"PK\x03\x04"):
        raise OoxmlRefused("unsupported_workbook")
    totals = end_record_totals(read_tail(spool))
    if totals is None:
        raise OoxmlRefused("unsupported_workbook")
    if totals[0] > MAX_ENTRIES or totals[1] > MAX_DIRECTORY_BYTES:
        raise OoxmlRefused("unsafe_archive")
    try:
        archive = zipfile.ZipFile(spool)
    except (zipfile.BadZipFile, zipfile.LargeZipFile, EOFError, OverflowError, ValueError):
        raise OoxmlRefused("unsupported_workbook") from None
    infos = archive.infolist()
    check_part_names(infos, max_total_bytes=limits.max_total_uncompressed_bytes)
    parts = {info.filename.casefold(): info for info in infos}
    for folded in parts:
        if folded.startswith(MACRO_PART_PREFIXES):
            raise OoxmlRefused("unsafe_workbook")
    for folded in parts:
        if folded.startswith(_EXTERNAL_PREFIXES) or folded in _EXTERNAL_PARTS:
            raise OoxmlRefused("external_link_present")
    if not {_CONTENT_TYPES_KEY, _WORKBOOK_PART}.issubset(parts):
        raise OoxmlRefused("unsupported_workbook")
    budget = PartBudget(limits.max_total_uncompressed_bytes)

    overrides, declared = _content_types(
        _read_small(archive, parts[_CONTENT_TYPES_KEY], budget, tick, cap=MAX_CONTENT_TYPES_BYTES)
    )
    if any("macroenabled" in kind.lower() or "vbaproject" in kind.lower() for kind in declared):
        raise OoxmlRefused("unsafe_workbook")
    if overrides.get("/" + _WORKBOOK_PART) != (
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"
    ):
        raise OoxmlRefused("unsupported_workbook")

    rels: dict[str, list[dict[str, str]]] = {}
    for folded, info in parts.items():
        if folded.endswith(".rels"):
            rels[folded] = _relationships(_read_small(archive, info, budget, tick))
            if any(r.get("TargetMode", "").lower() == "external" for r in rels[folded]):
                raise OoxmlRefused("external_link_present")

    sheets = _workbook_sheets(_read_small(archive, parts[_WORKBOOK_PART], budget, tick))
    folded_names = [name.casefold() for name, _rid in sheets]
    if len(set(folded_names)) != len(folded_names):
        raise OoxmlRefused("duplicate_sheet_name")
    workbook_rels = rels.get(_WORKBOOK_RELS_KEY)
    if workbook_rels is None:
        raise OoxmlRefused("xlsx_malformed")
    by_id = {r["Id"]: r for r in workbook_rels if "Id" in r}
    matches = [i for i, (name, _rid) in enumerate(sheets) if name == sheet_name]
    if not matches:
        raise XlsxAborted("mapping_column_missing")
    sheet_index = matches[0]
    sheet_rel = by_id.get(sheets[sheet_index][1])
    if sheet_rel is None or "Target" not in sheet_rel:
        raise OoxmlRefused("xlsx_malformed")
    sheet_part = _resolve_target(sheet_rel["Target"]).casefold()
    if sheet_rel.get("Type") != _REL_WORKSHEET:
        raise OoxmlRefused("unsupported_workbook")
    if sheet_part not in parts:
        raise OoxmlRefused("xlsx_malformed")

    shared: list[str] = []
    for item in by_id.values():
        if item.get("Type") == _REL_SHARED_STRINGS and "Target" in item:
            part = _resolve_target(item["Target"]).casefold()
            if part not in parts:
                raise OoxmlRefused("xlsx_malformed")
            shared = _load_shared_strings(
                archive, parts[part], budget, tick, limits.max_shared_strings_bytes, max_cell_chars
            )
            break
    return _Package(archive, parts[sheet_part], sheet_index, shared, budget)


def _load_shared_strings(
    archive: zipfile.ZipFile,
    info: zipfile.ZipInfo,
    budget: PartBudget,
    tick: Callable[[], None],
    cap: int,
    max_string_chars: int,
) -> list[str]:
    scanner = _SharedStringsScanner(max_string_chars)
    parser = new_xml_parser()
    parser.StartElementHandler = scanner.start
    parser.EndElementHandler = scanner.end
    parser.CharacterDataHandler = scanner.chars
    parser.CommentHandler = scanner.tick
    parser.ProcessingInstructionHandler = scanner.tick
    silent = 0
    try:
        for block in read_part_blocks(
            archive, info, part_cap=cap, budget=budget, over_cap_code="limit_exceeded", tick=tick
        ):
            before = scanner.events
            parser.Parse(block, False)
            silent = _check_progress(silent, before, scanner.events, len(block))
        parser.Parse(b"", True)
    except expat.ExpatError:
        raise OoxmlRefused("xlsx_malformed") from None
    return scanner.strings


def _check_progress(silent: int, before: int, after: int, size: int) -> int:
    """Bytes fed since the parser last reported anything. expat buffers a start tag,
    a comment or a processing instruction whole, so a run of bytes with no event
    past `MAX_TOKEN_BYTES` is an attempt to make it allocate without bound."""
    silent = silent + size if after == before else 0
    if silent > MAX_TOKEN_BYTES:
        raise OoxmlRefused("unsafe_archive")
    return silent


# -- events -----------------------------------------------------------------------


class XlsxEventStream(Iterator[ImportEvent]):
    """The import events of one XLSX source. `source_schema_sha256` is set once the
    header has been read (sha256 over the sheet index plus the exact header cells)."""

    def __init__(
        self,
        reader: ChunkReader,
        mapping: ImportMapping,
        *,
        verified_content_type: str,
        dataset_sha256: str,
        limits: ImportLimits = DEFAULT_LIMITS,
        xlsx_limits: XlsxLimits = DEFAULT_XLSX_LIMITS,
        tool_schemas: Mapping[str, Mapping[str, object]] | None = None,
        tool_argument_validator: ToolArgumentValidator | None = None,
    ) -> None:
        self._options = _options(mapping)
        self._reader = reader
        self._mapping = mapping
        self._verified_content_type = verified_content_type
        self._dataset_sha256 = dataset_sha256
        self._limits = limits
        self._xlsx_limits = xlsx_limits
        self._tool_schemas = tool_schemas
        self._tool_argument_validator = tool_argument_validator
        self.source_schema_sha256: str | None = None
        self._events = self._generate()

    def __next__(self) -> ImportEvent:
        return next(self._events)

    def close(self) -> None:
        """Stops early (a preview) and closes the spool at once."""
        self._events.close()  # type: ignore[attr-defined]

    # -- bytes -> rows ------------------------------------------------------------

    def _spool(self, spool: IO[bytes]) -> None:
        """Copies the whole source into `spool`. `ChunkReader` has verified its byte
        count and its own declared sha256 by the time it stops; the sha256 the caller
        expects (the dataset's verified hash, which also names every record) must be the
        same bytes, or nothing is imported."""
        try:
            for chunk in self._reader:
                spool.write(chunk)
            spool.flush()
        except OSError:
            raise ImportAborted("blocked", "source_unavailable") from None
        if not hmac.compare_digest(self._reader.sha256, self._dataset_sha256):
            raise ImportAborted("failed", "source_changed")

    def _rows(self, package: _Package, scanner: _SheetScanner) -> Iterator[_RawRow]:
        parser = new_xml_parser()
        parser.StartElementHandler = scanner.start
        parser.EndElementHandler = scanner.end
        parser.CharacterDataHandler = scanner.chars
        parser.CommentHandler = scanner.tick
        parser.ProcessingInstructionHandler = scanner.tick
        silent = 0
        try:
            for block in read_part_blocks(
                package.archive,
                package.sheet,
                part_cap=package.budget.remaining,
                budget=package.budget,
                tick=self._reader.deadline.check,
            ):
                before = scanner.events
                parser.Parse(block, False)
                silent = _check_progress(silent, before, scanner.events, len(block))
                while scanner.rows:
                    yield scanner.rows.popleft()
            parser.Parse(b"", True)
        except expat.ExpatError:
            raise XlsxAborted("xlsx_malformed", scanner.row_number or None) from None
        while scanner.rows:
            yield scanner.rows.popleft()

    # -- rows -> events -----------------------------------------------------------

    def _positions(self, headers: list[str], row: int) -> list[tuple[str, int]]:
        width = len(headers)
        positions: list[tuple[str, int]] = []
        for name, slot in self._options.slots:
            position = _COLUMN_RESOLVERS[slot.kind](slot.value, headers)
            if not 0 <= position < width:
                raise XlsxAborted("mapping_column_missing", row)
            positions.append((name, position))
        return positions

    def _generate(self) -> Iterator[ImportEvent]:
        if (
            type(self._verified_content_type) is not str
            or self._verified_content_type not in XLSX_CONTENT_TYPES
        ):
            raise ImportAborted("failed", "content_type_not_supported_by_importer")
        options = self._options
        try:
            with tempfile.TemporaryFile() as spool:
                self._spool(spool)
                package = open_package(
                    spool,
                    options.sheet_name,
                    limits=self._xlsx_limits,
                    max_cell_chars=self._limits.max_record_bytes,
                    tick=self._reader.deadline.check,
                )
                try:
                    yield from self._sheet_events(package)
                finally:
                    package.archive.close()
        except OoxmlRefused as exc:
            raise XlsxAborted(exc.code) from None

    def _sheet_events(self, package: _Package) -> Iterator[ImportEvent]:
        options = self._options
        scanner = _SheetScanner(
            package.shared, self._xlsx_limits.max_cells, self._limits.max_record_bytes
        )
        headers: list[str] | None = None
        positions: list[tuple[str, int]] = []
        non_blank = 0
        record_index = 0
        last_row = 1
        for raw in self._rows(package, scanner):
            last_row = raw.number
            self._reader.deadline.check()
            if not raw.nonblank:
                yield BlankLine()
                continue
            if headers is None:
                non_blank += 1
                if non_blank - 1 < options.header_row:
                    continue
                headers = self._header_cells(raw)
                self.source_schema_sha256 = _schema_sha256(package.sheet_index, headers)
                positions = self._positions(headers, raw.number)
                scanner.wanted = frozenset(position for _name, position in positions)
                continue
            locator = f"row:{raw.number}"
            index, record_index = record_index, record_index + 1
            yield self._map_row(raw, len(headers), headers, positions, locator, index)
        if headers is None:
            raise XlsxAborted("mapping_column_missing", last_row)

    @staticmethod
    def _header_cells(raw: _RawRow) -> list[str]:
        width = max(raw.cells) + 1
        headers = [""] * width
        for column, (kind, text) in raw.cells.items():
            if kind is not _Kind.VALUE:
                raise XlsxAborted("xlsx_malformed", raw.number)
            headers[column] = text
        named = [header for header in headers if header]
        if len(set(named)) != len(named):
            raise XlsxAborted("duplicate_header", raw.number)
        return headers

    def _map_row(
        self,
        raw: _RawRow,
        width: int,
        headers: list[str],
        positions: list[tuple[str, int]],
        locator: str,
        index: int,
    ) -> ImportEvent:
        row = [""] * width
        total = 0
        for name, position in positions:
            kind, text = raw.cells.get(position, (_Kind.VALUE, ""))
            if kind is _Kind.FORMULA:
                return RowRejection(locator, "formula_cell", name)
            if kind is _Kind.ERROR:
                return RowRejection(locator, "cell_error_value", name)
            if "\x00" in text:
                return RowRejection(locator, "nul_character", name)
            if _SURROGATE.search(text):
                return RowRejection(locator, "invalid_unicode", name)
            row[position] = text
            total += len(text.encode("utf-8"))
        if total > self._limits.max_record_bytes:
            return RowRejection(locator, "record_too_large")
        try:
            mapped = apply_mapping(self._mapping, row, headers=headers)
            record = normalize_mapped_record(
                self._mapping,
                mapped,
                source_record_index=index,
                dataset_sha256=self._dataset_sha256,
                source_type=SOURCE_TYPE,
                tool_schemas=self._tool_schemas,
                tool_argument_validator=self._tool_argument_validator,
            )
        except RowMappingError as exc:
            return RowRejection(locator, exc.code, exc.slot)
        has_tool_calls = any(message.tool_calls for message in record.messages)
        return AcceptedRecord(
            to_dataset_record_candidate(
                record,
                record_index=index,
                tool_schema_status="valid" if has_tool_calls else "not_applicable",
            )
        )


def xlsx_events(
    reader: ChunkReader,
    mapping: ImportMapping,
    *,
    verified_content_type: str,
    dataset_sha256: str,
    limits: ImportLimits = DEFAULT_LIMITS,
    xlsx_limits: XlsxLimits = DEFAULT_XLSX_LIMITS,
    tool_schemas: Mapping[str, Mapping[str, object]] | None = None,
    tool_argument_validator: ToolArgumentValidator | None = None,
) -> XlsxEventStream:
    """The events of an XLSX source, for `preview` or a caller's own drain. Raises
    `MappingInvalidError` before reading a byte when the mapping cannot describe a
    worksheet; a `verified_content_type` other than the XLSX content type aborts the
    first `next()` with `content_type_not_supported_by_importer`, also before a byte
    is read."""
    return XlsxEventStream(
        reader,
        mapping,
        verified_content_type=verified_content_type,
        dataset_sha256=dataset_sha256,
        limits=limits,
        xlsx_limits=xlsx_limits,
        tool_schemas=tool_schemas,
        tool_argument_validator=tool_argument_validator,
    )


def run_xlsx_import(
    reader: ChunkReader,
    mapping: ImportMapping,
    *,
    verified_content_type: str,
    dataset_purpose: str,
    expected_sha256: str,
    limits: ImportLimits = DEFAULT_LIMITS,
    xlsx_limits: XlsxLimits = DEFAULT_XLSX_LIMITS,
    tool_schemas: Mapping[str, Mapping[str, object]] | None = None,
    tool_argument_validator: ToolArgumentValidator | None = None,
    on_accepted: Callable[[Mapping[str, object]], None] | None = None,
) -> XlsxImportOutcome:
    """Drains an XLSX source into an `ImportReport`. An abort anywhere (a fatal
    XLSX problem, a source outage, a deadline, a rejection budget) is the report's
    `state`, never an exception; a source that was not read to its end cannot be
    `completed`."""
    stream = xlsx_events(
        reader,
        mapping,
        verified_content_type=verified_content_type,
        dataset_sha256=expected_sha256,
        limits=limits,
        xlsx_limits=xlsx_limits,
        tool_schemas=tool_schemas,
        tool_argument_validator=tool_argument_validator,
    )
    accumulator = ImportAccumulator(
        importer=IMPORTER_NAME,
        importer_version=IMPORTER_VERSION,
        mapping_sha256=mapping.mapping_sha256,
        dataset_purpose=dataset_purpose,
        expected_sha256=expected_sha256,
        limits=limits,
        on_accepted=on_accepted,
    )
    aborted: ImportAborted | None = None
    try:
        for event in stream:
            accumulator.add(event)
    except ImportAborted as exc:
        aborted = exc
    finally:
        stream.close()
    accumulator.source_schema_sha256 = stream.source_schema_sha256
    report = accumulator.report(
        source_sha256=reader.sha256, source_bytes=reader.bytes_read, aborted=aborted
    )
    return XlsxImportOutcome(report, aborted.locator if isinstance(aborted, XlsxAborted) else None)
