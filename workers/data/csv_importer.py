"""CSV importer (FTF-0504; FINE_TUNING_FACTORY_RELEASE_1.md Section 7, 8.1, 9, 67.2, 67.3).

Turns an already-authorised, size/hash-bound byte stream (FTF-0502-P1's
`ChunkReader`) into FTF-0502-P1 import events, one CSV record at a time.

Everything about the file is declared, nothing is guessed:

- `encoding` is one of `CSV_ENCODINGS` and `delimiter` one of `CSV_DELIMITERS`,
  both read from the MappingSpec `csv` block. There is no `csv.Sniffer` and no
  fallback encoding. Decoding is strict and incremental, so a multi-byte
  character split across two chunks is decoded, not refused.
- Header cells are matched by exact text: no strip, casefold or NFC. A repeated
  header cell is fatal (`duplicate_header`), so a mapped header always names one
  column. A mapped column that is absent is fatal (`mapping_column_missing`).
- A record whose field count differs from the header's is rejected
  (`column_count_mismatch`); it is never padded or cut.
- Every field stays the exact decoded `str`: no int/float/bool conversion, no
  strip, and embedded CR / LF / CRLF inside a quoted field are kept as they
  were. Formula-looking text (`=`, `+`, `-`, `@`, leading TAB / CR, ...) is data:
  nothing here evaluates, escapes or quotes it. Neutralising it for a
  spreadsheet export is the export task's job (Section 67.3).

Fatal versus rejected. A problem the reader cannot resynchronise after (bad
encoding, an unterminated quote, a stray quote, an oversize field, too many
columns, a BOM under `utf-8`, a missing or repeated header) ends the import:
the report's `state` is `failed` and `failure_code` is the code. The physical
line it happened on (`line:<n>`) is the report's `fatal_locator` (a `LocatedImportReport`,
inside the report digest). A
problem confined to one record becomes a `RowRejection` and the import goes on.

Disclosed choices (each pinned by a test):

- Locators are `line:<n>`, the 1-based physical line a record starts on (what a
  person opening the file needs; a multiline quoted record is located at its
  first line). A fatal problem is located at the physical line of the offending
  character (`invalid_encoding`: the line of the first bad byte), or for an
  unterminated quote at the line its record started on; the line never depends
  on how the source was cut into chunks. The logical ordinal of an accepted
  record is its candidate's `record_index`; the frozen locator grammar has no slot for both.
- `header_row` counts non-blank records before the header (0 means the first
  non-blank record). Records before it are an explicit preamble the owner
  declared, and are skipped without being counted as records or rejections.
  With `has_header=false` it must be 0 and the first non-blank record fixes the
  expected width.
- The records are parsed by this module's own resumable state machine (RFC 4180
  quoting, `""` for a quote, strict: a character after a closing quote other
  than a delimiter or line break is `csv_structure_invalid`). It never touches
  the `csv` module, so no process-global (`csv.field_size_limit`) is read or
  written and concurrent imports with different limits cannot affect each other.
  One field longer than `limits.max_record_bytes` characters, or one logical
  record (all its physical lines, quotes, delimiters and its line break; the LF
  of a CRLF is not counted) longer than twice that plus two, is fatal
  `field_too_large`, detected at exactly the first character over, so the
  memory one record can take is bounded whatever the chunking.
  A record whose mapped fields add up to more than `max_record_bytes` UTF-8
  bytes is rejected `record_too_large`.
- A NUL character is refused only in a mapped field (`nul_character`), since
  only mapped fields reach the canonical content; the parser itself accepts NUL.
- Only a verified content type of `text/csv` is imported. Any other value is a
  `failed` report, `content_type_not_supported_by_importer`, before a byte of
  the source is read.
- Tabular sources carry no object keys and no spreadsheet column letters: a
  `key` or `letters` slot, or the `messages` shape (a JSON pointer into an
  object), is a `MappingInvalidError`. Tool calls travel in a `messages_json`
  column.

Scope: pure and synchronous, no database and no network (the packet's
`database_changes`: "No independent schema change. Consume frozen contract").
No import route exists yet to call it; `proving_symbol` stays blank, as for the
sibling importers.
"""

from __future__ import annotations

import codecs
import copy
import hashlib
import hmac
import json
import re
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass
from typing import Final

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

__all__ = [
    "CSV_CONTENT_TYPES",
    "CSV_DELIMITERS",
    "CSV_ENCODINGS",
    "CSV_FATAL_CODES",
    "CSV_MAX_COLUMNS",
    "CsvAborted",
    "CsvEventStream",
    "CsvImportOutcome",
    "csv_events",
    "run_csv_import",
]

CSV_ENCODINGS: Final = ("utf-8", "utf-8-sig", "utf-16-le", "utf-16-be", "windows-1256")
CSV_DELIMITERS: Final = (",", ";", "\t", "|")
CSV_CONTENT_TYPES: Final = frozenset({"text/csv"})
CSV_MAX_COLUMNS: Final = 16_384
_SHA256_PATTERN: Final = re.compile(r"[0-9a-f]{64}")
CSV_FATAL_CODES: Final = frozenset(
    {
        "csv_structure_invalid",
        "duplicate_header",
        "field_too_large",
        "invalid_encoding",
        "mapping_column_missing",
        "too_many_columns",
        "unexpected_bom",
    }
)

IMPORTER_NAME: Final = "csv"
IMPORTER_VERSION: Final = "1"
SOURCE_TYPE: Final = "upload"

# Indexes into CSV_ENCODINGS: utf-8, utf-16-le, utf-16-be (utf-8-sig consumes its BOM).
_BOM_CHECKED_ENCODINGS: Final = (0, 2, 3)

# Parser states.
_START_RECORD: Final = 0
_START_FIELD: Final = 1
_IN_FIELD: Final = 2
_QUOTED: Final = 3
_QUOTE_IN_QUOTED: Final = 4
_AFTER_CR: Final = 5  # a record ended with CR as the last character of a chunk


class CsvAborted(ImportAborted):
    """A fatal CSV problem: the import ends `failed` with `failure_code`, and
    `locator` names the physical line (`line:<n>`) it happened on."""

    def __init__(self, failure_code: str, line: int) -> None:
        super().__init__("failed", failure_code)
        self.locator = f"line:{max(line, 1)}"


class _BadBytes(Exception):  # noqa: N818 - internal control flow, turned into CsvAborted
    """The source holds bytes the declared encoding cannot decode."""


@dataclass(frozen=True)
class CsvImportOutcome:
    report: ImportReport


@dataclass(frozen=True)
class _Options:
    encoding: str
    delimiter: str
    has_header: bool
    header_row: int
    slots: tuple[tuple[str, Slot], ...]
    bom_forbidden: bool


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
    """The `csv` block and the slots of a MappingSpec, refused up front when
    they cannot describe a CSV. Raises `MappingInvalidError`."""
    block = mapping.document.get("csv")
    if type(block) is not dict:
        raise MappingInvalidError("missing_key")
    encoding, delimiter = block["encoding"], block["delimiter"]
    has_header, header_row = block["has_header"], block["header_row"]
    if encoding not in CSV_ENCODINGS or delimiter not in CSV_DELIMITERS:
        raise MappingInvalidError("bad_value")
    if not has_header and header_row != 0:
        raise MappingInvalidError("bad_value")
    if mapping.messages_pointer is not None:
        raise MappingInvalidError("bad_shape")
    slots = _mapped_slots(mapping)
    for _name, slot in slots:
        if slot.kind not in ("header", "index") or (isinstance(slot.value, str) and not has_header):
            raise MappingInvalidError("bad_slot")
    bom_forbidden = CSV_ENCODINGS.index(encoding) in _BOM_CHECKED_ENCODINGS
    return _Options(str(encoding), str(delimiter), has_header, header_row, slots, bom_forbidden)


def _schema_sha256(headers: list[str]) -> str:
    text = json.dumps(headers, ensure_ascii=True, separators=(",", ":"))
    return hashlib.sha256(text.encode("ascii")).hexdigest()


class _Parser:
    """A resumable CSV record parser: `feed(text)` for each decoded piece of the
    source in order, then `finish()`. Each parsed record is yielded as
    `(start_line, fields)`; a record with no characters at all (a blank line) is
    `(line, [])`. `line` is the 1-based physical line of the next character."""

    def __init__(self, delimiter: str, max_field: int, max_record: int) -> None:
        self._delimiter = delimiter
        self._unquoted = re.compile(f"[^{re.escape(delimiter)}\\r\\n]*")
        self._max_field = max_field
        self._max_record = max_record
        self.line = 1
        self.start_line = 1
        self._state = _START_RECORD
        self._fields: list[str] = []
        self._pieces: list[str] = []
        self._field_len = 0
        self._record_len = 0
        self._quoted_cr = False  # the last quoted piece ended in a CR

    # -- accounting ----------------------------------------------------------

    def _count_breaks(self, piece: str) -> None:
        if not piece:
            return
        breaks = piece.count("\n") + piece.count("\r") - piece.count("\r\n")
        if self._quoted_cr and piece[0] == "\n":
            breaks -= 1  # the LF of a CRLF whose CR ended the previous piece
        self._quoted_cr = piece[-1] == "\r"
        self.line += breaks

    def _bump(self, count: int) -> None:
        self._record_len += count
        if self._record_len > self._max_record:
            raise CsvAborted("field_too_large", self.line)

    def _content(self, text: str, start: int, end: int, *, quoted: bool) -> None:
        size = end - start
        room = min(self._max_field - self._field_len, self._max_record - self._record_len)
        if size > room:
            if quoted:
                self._count_breaks(text[start : start + room])
            raise CsvAborted("field_too_large", self.line)
        self._pieces.append(text[start:end])
        self._field_len += size
        self._record_len += size
        if quoted:
            self._count_breaks(text[start:end])

    def _save_field(self) -> None:
        self._fields.append("".join(self._pieces))
        self._pieces = []
        self._field_len = 0
        if len(self._fields) > CSV_MAX_COLUMNS:
            raise CsvAborted("too_many_columns", self.start_line)

    def _terminate(self, text: str, position: int) -> int:
        """Consume the record's line break at `position`; returns the next one."""
        char = text[position]
        self._bump(1)
        position += 1
        self.line += 1
        self._state = _START_RECORD
        if char == "\r":
            if position == len(text):
                self._state = _AFTER_CR
            elif text[position] == "\n":
                position += 1
        return position

    def _record(self) -> tuple[int, list[str]]:
        row, self._fields = self._fields, []
        self._record_len = 0
        return self.start_line, row

    # -- states --------------------------------------------------------------

    def feed(self, text: str) -> Iterator[tuple[int, list[str]]]:
        position, length = 0, len(text)
        delimiter = self._delimiter
        while position < length:
            state = self._state
            if state == _IN_FIELD:
                end = self._unquoted.match(text, position).end()  # type: ignore[union-attr]
                self._content(text, position, end, quoted=False)
                position = end
                if position == length:
                    break
                self._save_field()
                if text[position] == delimiter:
                    self._bump(1)
                    position += 1
                    self._state = _START_FIELD
                else:
                    position = self._terminate(text, position)
                    yield self._record()
            elif state == _START_FIELD:
                if text[position] == '"':
                    self._bump(1)
                    position += 1
                    self._quoted_cr = False
                    self._state = _QUOTED
                else:  # an empty field, a delimiter or a line break is _IN_FIELD's job
                    self._state = _IN_FIELD
            elif state == _QUOTED:
                end = text.find('"', position)
                end = length if end < 0 else end
                self._content(text, position, end, quoted=True)
                position = end
                if position < length:
                    self._bump(1)
                    position += 1
                    self._quoted_cr = False
                    self._state = _QUOTE_IN_QUOTED
            elif state == _QUOTE_IN_QUOTED:
                if text[position] == '"':
                    self._content('"', 0, 1, quoted=False)
                    position += 1
                    self._state = _QUOTED
                elif text[position] == delimiter:
                    self._save_field()
                    self._bump(1)
                    position += 1
                    self._state = _START_FIELD
                elif text[position] in "\r\n":
                    self._save_field()
                    position = self._terminate(text, position)
                    yield self._record()
                else:
                    raise CsvAborted("csv_structure_invalid", self.line)
            elif state == _START_RECORD:
                self.start_line = self.line
                if text[position] in "\r\n":
                    position = self._terminate(text, position)
                    yield self._record()
                else:
                    self._state = _START_FIELD
            else:  # _AFTER_CR
                if text[position] == "\n":
                    position += 1
                self._state = _START_RECORD

    def finish(self) -> Iterator[tuple[int, list[str]]]:
        """The source ended: emit the last record (it needs no line break), or
        fail when it ends inside a quoted field."""
        if self._state == _QUOTED:
            raise CsvAborted("csv_structure_invalid", self.start_line)
        if self._state in (_START_FIELD, _IN_FIELD, _QUOTE_IN_QUOTED):
            self._save_field()
            yield self._record()


class CsvEventStream(Iterator[ImportEvent]):
    """The import events of one CSV source. `source_schema_sha256` is set once
    the header has been read (sha256 over the exact header cells, in order)."""

    def __init__(
        self,
        reader: ChunkReader,
        mapping: ImportMapping,
        *,
        verified_content_type: str,
        dataset_sha256: str,
        limits: ImportLimits = DEFAULT_LIMITS,
        tool_schemas: Mapping[str, Mapping[str, object]] | None = None,
        tool_argument_validator: ToolArgumentValidator | None = None,
    ) -> None:
        self._options = _options(mapping)
        self._reader = reader
        self._mapping = mapping
        self._verified_content_type = verified_content_type
        self._dataset_sha256 = dataset_sha256
        self._limits = limits
        self._tool_schemas = tool_schemas
        self._tool_argument_validator = tool_argument_validator
        self.source_schema_sha256: str | None = None
        self._parser = _Parser(
            self._options.delimiter, limits.max_record_bytes, 2 * limits.max_record_bytes + 2
        )
        self._events = self._generate()

    def __next__(self) -> ImportEvent:
        return next(self._events)

    # -- bytes -> text -------------------------------------------------------

    def _texts(self) -> Iterator[str]:
        """Decoded text, strictly. On bytes the encoding cannot decode, the text
        before the first bad byte is yielded first and `_BadBytes` follows, so
        the failure is located at the same physical line for every chunking and
        an earlier structure error still wins."""
        decoder = codecs.getincrementaldecoder(self._options.encoding)(errors="strict")
        first = True
        for chunk in self._reader:
            snapshot = copy.copy(decoder)
            try:
                text = decoder.decode(chunk)
            except UnicodeDecodeError:
                valid = self._valid_prefix(snapshot, chunk)
                if valid:
                    self._check_bom(valid, first)
                    yield valid
                raise _BadBytes from None
            if text:
                self._check_bom(text, first)
                first = False
                yield text
        if self._options.encoding == "utf-8-sig" and first and 0 < self._reader.bytes_read < 3:
            # utf-8-sig's own incremental decoder buffers a strict prefix of the 3-byte BOM
            # (`\xef` or `\xef\xbb`) and silently decodes it to "" at end-of-stream instead of
            # raising: `first` still true plus fewer than 3 bytes read means every byte so far
            # decoded to nothing, which utf-8-sig only ever does for a truncated BOM (a plain
            # byte decodes immediately to itself). Never let a corrupt, cut-off source look like
            # a trivially empty, valid CSV.
            raise _BadBytes
        try:
            # Every codec in CSV_ENCODINGS returns "" here or raises (fuzzed over all five), so
            # this only detects a sequence cut off at the end of the source.
            decoder.decode(b"", final=True)
        except UnicodeDecodeError:
            raise _BadBytes from None

    def _check_bom(self, text: str, first: bool) -> None:
        if first and self._options.bom_forbidden and text[0] == "\ufeff":
            raise CsvAborted("unexpected_bom", 1)

    @staticmethod
    def _valid_prefix(snapshot: codecs.IncrementalDecoder, chunk: bytes) -> str:
        """The text of `chunk` before its first bad byte, given the decoder as it was
        before the chunk. The error offset a codec reports is not a reliable cut (a
        `utf-8-sig` decoder counts it after the BOM it strips), so the longest prefix
        that decodes is searched for: if a prefix decodes, so does every shorter one."""
        good, bad = 0, len(chunk)  # chunk[:good] decodes; chunk[:bad] does not
        while bad - good > 1:
            middle = (good + bad) // 2
            try:
                copy.copy(snapshot).decode(chunk[:middle])
            except UnicodeDecodeError:
                bad = middle
            else:
                good = middle
        return copy.copy(snapshot).decode(chunk[:good])

    def _rows(self) -> Iterator[tuple[int, list[str]]]:
        parser = self._parser
        try:
            for text in self._texts():
                yield from parser.feed(text)
        except _BadBytes:
            raise CsvAborted("invalid_encoding", parser.line) from None
        yield from parser.finish()

    # -- rows -> events ------------------------------------------------------

    def _positions(self, headers: list[str] | None, width: int, line: int) -> list[tuple[str, int]]:
        positions: list[tuple[str, int]] = []
        for name, slot in self._options.slots:
            if isinstance(slot.value, str):
                assert headers is not None
                if slot.value not in headers:
                    raise CsvAborted("mapping_column_missing", line)
                position = headers.index(str(slot.value))
            else:
                position = int(slot.value)
                if not 0 <= position < width:
                    raise CsvAborted("mapping_column_missing", line)
            positions.append((name, position))
        return positions

    def _generate(self) -> Iterator[ImportEvent]:
        if (
            type(self._verified_content_type) is not str
            or self._verified_content_type not in CSV_CONTENT_TYPES
        ):
            raise ImportAborted("failed", "content_type_not_supported_by_importer")
        expected = self._dataset_sha256
        if (
            type(expected) is not str
            or not _SHA256_PATTERN.fullmatch(expected)
            or not hmac.compare_digest(
                # the hash the source was declared with, known before a byte is read
                self._reader._expected_sha256,
                expected,
            )
        ):
            raise ImportAborted("failed", "source_changed")
        options = self._options
        headers: list[str] | None = None
        width: int | None = None
        positions: list[tuple[str, int]] = []
        non_blank = 0
        record_index = 0
        last_line = 1
        for start_line, row in self._rows():
            last_line = start_line
            if not row:
                yield BlankLine()
                continue
            if options.has_header and headers is None:
                non_blank += 1
                if non_blank - 1 < options.header_row:
                    continue
                if len(set(row)) != len(row):
                    raise CsvAborted("duplicate_header", start_line)
                headers, width = row, len(row)
                self.source_schema_sha256 = _schema_sha256(row)
                positions = self._positions(headers, width, start_line)
                continue
            if width is None:
                width = len(row)
                positions = self._positions(None, width, start_line)
            locator = f"line:{start_line}"
            index, record_index = record_index, record_index + 1
            if len(row) != width:
                yield RowRejection(locator, "column_count_mismatch")
                continue
            yield self._map_row(row, headers, positions, locator, index)
        if options.has_header and headers is None:
            raise CsvAborted("mapping_column_missing", last_line)

    def _map_row(
        self,
        row: list[str],
        headers: list[str] | None,
        positions: list[tuple[str, int]],
        locator: str,
        index: int,
    ) -> ImportEvent:
        total = 0
        for name, position in positions:
            if "\x00" in row[position]:
                return RowRejection(locator, "nul_character", name)
            total += len(row[position].encode("utf-8"))
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


def csv_events(
    reader: ChunkReader,
    mapping: ImportMapping,
    *,
    verified_content_type: str,
    dataset_sha256: str,
    limits: ImportLimits = DEFAULT_LIMITS,
    tool_schemas: Mapping[str, Mapping[str, object]] | None = None,
    tool_argument_validator: ToolArgumentValidator | None = None,
) -> CsvEventStream:
    """The events of a CSV source, for `preview` or a caller's own drain. Raises
    `MappingInvalidError` before reading a byte when the mapping cannot
    describe a CSV; a `verified_content_type` other than `text/csv` aborts the
    first `next()` with `content_type_not_supported_by_importer`, also before a
    byte is read."""
    return CsvEventStream(
        reader,
        mapping,
        verified_content_type=verified_content_type,
        dataset_sha256=dataset_sha256,
        limits=limits,
        tool_schemas=tool_schemas,
        tool_argument_validator=tool_argument_validator,
    )


def run_csv_import(
    reader: ChunkReader,
    mapping: ImportMapping,
    *,
    verified_content_type: str,
    dataset_purpose: str,
    expected_sha256: str,
    limits: ImportLimits = DEFAULT_LIMITS,
    tool_schemas: Mapping[str, Mapping[str, object]] | None = None,
    tool_argument_validator: ToolArgumentValidator | None = None,
    on_accepted: Callable[[Mapping[str, object]], None] | None = None,
) -> CsvImportOutcome:
    """Drains a CSV source into an `ImportReport`. An abort anywhere (a fatal
    CSV problem, a source outage, a deadline, a rejection budget) is the
    report's `state`, never an exception; a source that was not read to its end
    cannot be `completed`."""
    stream = csv_events(
        reader,
        mapping,
        verified_content_type=verified_content_type,
        dataset_sha256=expected_sha256,
        limits=limits,
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
            reader.deadline.check()
            accumulator.add(event)
    except ImportAborted as exc:
        aborted = exc
    accumulator.source_schema_sha256 = stream.source_schema_sha256
    report = accumulator.report(
        source_sha256=reader.sha256,
        source_bytes=reader.bytes_read,
        aborted=aborted,
        fatal_locator=aborted.locator if isinstance(aborted, CsvAborted) else None,
    )
    return CsvImportOutcome(report)
