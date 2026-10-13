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
line it happened on is returned beside the report (`CsvImportOutcome.
fatal_locator`, `line:<n>`), because `ImportReport` has no field for it. A
problem confined to one record becomes a `RowRejection` and the import goes on.

Disclosed choices (each pinned by a test):

- Locators are `line:<n>`, the 1-based physical line a record starts on (what a
  person opening the file needs; a multiline quoted record is located at its
  first line). The logical ordinal of an accepted record is its candidate's
  `record_index`; the frozen locator grammar has no slot for both.
- `header_row` counts non-blank records before the header (0 means the first
  non-blank record). Records before it are an explicit preamble the owner
  declared, and are skipped without being counted as records or rejections.
  With `has_header=false` it must be 0 and the first non-blank record fixes the
  expected width.
- The CSV `field_size_limit` is `limits.max_record_bytes` characters, set around
  every `next()` of the reader and restored after it (the limit is global to the
  `csv` module). A single physical line longer than twice that, plus two, is
  fatal `field_too_large`: it is what bounds the memory one line can take.
  A record whose mapped fields add up to more than `max_record_bytes` UTF-8
  bytes is rejected `record_too_large`.
- A NUL character is refused only in a mapped field (`nul_character`), since
  only mapped fields reach the canonical content. CPython's `csv` module accepts
  NUL, so this importer checks it.
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
import csv
import hashlib
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
CSV_MAX_COLUMNS: Final = 16_384
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

_EOL: Final = re.compile(r"\r\n|\r|\n")
_BOM_CHECKED_ENCODINGS: Final = ("utf-8", "utf-16-le", "utf-16-be")
_FIELD_LIMIT_MESSAGE: Final = "field larger than field limit"


class CsvAborted(ImportAborted):
    """A fatal CSV problem: the import ends `failed` with `failure_code`, and
    `locator` names the physical line (`line:<n>`) it happened on."""

    def __init__(self, failure_code: str, line: int) -> None:
        if failure_code not in CSV_FATAL_CODES:
            raise ValueError("unknown CSV fatal code")
        super().__init__("failed", failure_code)
        self.locator = f"line:{max(line, 1)}"


@dataclass(frozen=True)
class CsvImportOutcome:
    report: ImportReport
    fatal_locator: str | None


@dataclass(frozen=True)
class _Options:
    encoding: str
    delimiter: str
    has_header: bool
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
    """The `csv` block and the slots of a MappingSpec, refused up front when
    they cannot describe a CSV. Raises `MappingInvalidError`."""
    block = mapping.document.get("csv")
    if type(block) is not dict:
        raise MappingInvalidError("missing_key")
    encoding, delimiter = block["encoding"], block["delimiter"]
    has_header, header_row = block["has_header"], block["header_row"]
    if encoding not in CSV_ENCODINGS or delimiter not in CSV_DELIMITERS:
        raise MappingInvalidError("bad_value")
    if type(has_header) is not bool or type(header_row) is not int:
        raise MappingInvalidError("wrong_type")
    if not has_header and header_row != 0:
        raise MappingInvalidError("bad_value")
    if mapping.messages_pointer is not None:
        raise MappingInvalidError("bad_shape")
    slots = _mapped_slots(mapping)
    for _name, slot in slots:
        if slot.kind not in ("header", "index") or (slot.kind == "header" and not has_header):
            raise MappingInvalidError("bad_slot")
    return _Options(str(encoding), str(delimiter), has_header, header_row, slots)


def _schema_sha256(headers: list[str]) -> str:
    text = json.dumps(headers, ensure_ascii=True, separators=(",", ":"))
    return hashlib.sha256(text.encode("ascii")).hexdigest()


class CsvEventStream(Iterator[ImportEvent]):
    """The import events of one CSV source. `source_schema_sha256` is set once
    the header has been read (sha256 over the exact header cells, in order)."""

    def __init__(
        self,
        reader: ChunkReader,
        mapping: ImportMapping,
        *,
        dataset_sha256: str,
        limits: ImportLimits = DEFAULT_LIMITS,
        tool_schemas: Mapping[str, Mapping[str, object]] | None = None,
        tool_argument_validator: ToolArgumentValidator | None = None,
    ) -> None:
        self._options = _options(mapping)
        self._reader = reader
        self._mapping = mapping
        self._dataset_sha256 = dataset_sha256
        self._limits = limits
        self._tool_schemas = tool_schemas
        self._tool_argument_validator = tool_argument_validator
        self.source_schema_sha256: str | None = None
        self._lines_emitted = 0
        self._events = self._generate()

    def __next__(self) -> ImportEvent:
        return next(self._events)

    # -- bytes -> text lines -------------------------------------------------

    def _texts(self) -> Iterator[str]:
        decoder = codecs.getincrementaldecoder(self._options.encoding)(errors="strict")
        first = True
        for chunk in self._reader:
            try:
                text = decoder.decode(chunk)
            except UnicodeDecodeError:
                raise CsvAborted("invalid_encoding", self._lines_emitted + 1) from None
            if first and text:
                first = False
                if self._options.encoding in _BOM_CHECKED_ENCODINGS and text[0] == "﻿":
                    raise CsvAborted("unexpected_bom", 1)
            if text:
                yield text
        try:
            tail = decoder.decode(b"", final=True)
        except UnicodeDecodeError:
            raise CsvAborted("invalid_encoding", self._lines_emitted + 1) from None
        if tail:
            yield tail

    def _lines(self) -> Iterator[str]:
        """Text split at CR, LF or CRLF with the terminator kept, so the csv
        reader sees embedded line breaks exactly as written."""
        max_line = 2 * self._limits.max_record_bytes + 2
        buffer: list[str] = []
        buffered = 0
        held_cr = False  # the buffer ends in a CR that a following LF would join

        def take() -> str:
            nonlocal buffer, buffered
            line = "".join(buffer)
            buffer, buffered = [], 0
            self._lines_emitted += 1
            return line

        def hold(piece: str) -> None:
            nonlocal buffered
            buffered += len(piece)
            if buffered > max_line:
                raise CsvAborted("field_too_large", self._lines_emitted + 1)
            buffer.append(piece)

        for text in self._texts():
            position = 0
            if held_cr:
                held_cr = False
                if text[0] == "\n":
                    hold("\n")
                    position = 1
                yield take()
            for match in _EOL.finditer(text, position):
                end = match.end()
                hold(text[position:end])
                position = end
                if match.group() == "\r" and end == len(text):
                    held_cr = True
                    break
                yield take()
            if position < len(text):
                hold(text[position:])
        if buffer:
            yield take()

    # -- text lines -> rows --------------------------------------------------

    def _next_row(self, rows: Iterator[list[str]]) -> list[str] | None:
        previous = csv.field_size_limit(self._limits.max_record_bytes)
        try:
            return next(rows)
        except StopIteration:
            return None
        except csv.Error as exc:
            code = (
                "field_too_large"
                if str(exc).startswith(_FIELD_LIMIT_MESSAGE)
                else "csv_structure_invalid"
            )
            raise CsvAborted(code, self._lines_emitted) from None
        finally:
            csv.field_size_limit(previous)

    def _positions(self, headers: list[str] | None, width: int) -> list[tuple[str, int]]:
        positions: list[tuple[str, int]] = []
        for name, slot in self._options.slots:
            if slot.kind == "header":
                assert headers is not None
                if slot.value not in headers:
                    raise CsvAborted("mapping_column_missing", self._lines_emitted)
                position = headers.index(str(slot.value))
            else:
                position = int(slot.value)
                if not 0 <= position < width:
                    raise CsvAborted("mapping_column_missing", self._lines_emitted)
            positions.append((name, position))
        return positions

    def _generate(self) -> Iterator[ImportEvent]:
        options = self._options
        rows = csv.reader(self._lines(), delimiter=options.delimiter, quotechar='"', strict=True)
        headers: list[str] | None = None
        width: int | None = None
        positions: list[tuple[str, int]] = []
        non_blank = 0
        record_index = 0
        while True:
            start_line = rows.line_num + 1
            row = self._next_row(rows)
            if row is None:
                break
            if not row:
                yield BlankLine()
                continue
            if len(row) > CSV_MAX_COLUMNS:
                raise CsvAborted("too_many_columns", start_line)
            if options.has_header and headers is None:
                non_blank += 1
                if non_blank - 1 < options.header_row:
                    continue
                if len(set(row)) != len(row):
                    raise CsvAborted("duplicate_header", start_line)
                headers, width = row, len(row)
                self.source_schema_sha256 = _schema_sha256(row)
                positions = self._positions(headers, width)
                continue
            if width is None:
                width = len(row)
                positions = self._positions(None, width)
            locator = f"line:{start_line}"
            index, record_index = record_index, record_index + 1
            if len(row) != width:
                yield RowRejection(locator, "column_count_mismatch")
                continue
            yield self._map_row(row, headers, positions, locator, index)
        if options.has_header and headers is None:
            raise CsvAborted("mapping_column_missing", rows.line_num)

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
    dataset_sha256: str,
    limits: ImportLimits = DEFAULT_LIMITS,
    tool_schemas: Mapping[str, Mapping[str, object]] | None = None,
    tool_argument_validator: ToolArgumentValidator | None = None,
) -> CsvEventStream:
    """The events of a CSV source, for `preview` or a caller's own drain. Raises
    `MappingInvalidError` before reading a byte when the mapping cannot
    describe a CSV."""
    return CsvEventStream(
        reader,
        mapping,
        dataset_sha256=dataset_sha256,
        limits=limits,
        tool_schemas=tool_schemas,
        tool_argument_validator=tool_argument_validator,
    )


def run_csv_import(
    reader: ChunkReader,
    mapping: ImportMapping,
    *,
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
        for _ in reader:
            raise ImportAborted("failed", "stream_not_consumed")
    except ImportAborted as exc:
        aborted = exc
    accumulator.source_schema_sha256 = stream.source_schema_sha256
    report = accumulator.report(
        source_sha256=reader.sha256, source_bytes=reader.bytes_read, aborted=aborted
    )
    return CsvImportOutcome(report, aborted.locator if isinstance(aborted, CsvAborted) else None)
