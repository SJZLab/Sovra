"""JSONL importer (FTF-0502; FINE_TUNING_FACTORY_RELEASE_1.md Section 7, 8.1, 67.2, 67.3).

Turns an already-authorised, size/hash-bound byte stream (FTF-0502-P1's
`ChunkReader`) into FTF-0502-P1 import events, one JSON Lines record at a time.

Line rules (each pinned by a test):

- Only LF delimits a line; exactly one CR directly before the LF is removed.
  U+2028 / U+2029 inside a string are data. A lone CR is part of the line (JSON
  whitespace), so a CR-only file is one line.
- A UTF-8 BOM is accepted only as the first three bytes of the source and is
  removed from the byte stream before any line is cut. A BOM anywhere else is
  part of its line and fails `invalid_json`. UTF-16 / UTF-32 (a BOM, or a NUL
  among the first four bytes) end the import `failed` / `unsupported_encoding`,
  located at `line:1` (`JsonlImportOutcome.fatal_locator`), before a record is
  read.
- A line that is empty or only ASCII whitespace is a blank line: counted in
  `blank_lines`, never a record. The final line needs no LF.
- Every line is decoded as strict UTF-8 on its own (`invalid_utf8`; the next
  line still imports) and must be exactly one JSON object through P1
  `strict_loads` (`invalid_json`, `duplicate_key`, `non_finite_number`,
  `number_out_of_range`, `nesting_too_deep`, `invalid_unicode`;
  `record_not_object` for any other JSON value).
- A line longer than `limits.max_record_bytes` bytes (its LF and one CR before
  it are not counted) is rejected `record_too_large`. Its bytes are discarded
  as they arrive up to the next LF, never buffered, and the following lines
  import. The line buffer holds at most `max_record_bytes + 1` bytes and each
  chunk is searched for its LF once, so memory does not depend on the chunking
  and time is linear in the source size.
- A line that fits but whose canonical content (what FTF-0508 measures: sorted,
  ASCII-escaped JSON) exceeds `MAX_CANONICAL_RECORD_BYTES` is also rejected
  `record_too_large`, so an accepted candidate always passes
  `validate_record_batch`.

Locators are `line:<n>`, the 1-based physical line (blank lines count). The
`record_index` of an accepted candidate is the 0-based ordinal of non-blank
lines, rejected ones included, so `records_seen == records_accepted +
records_rejected` always holds and no line disappears without a code.

A mapping must describe a JSONL source: slots are object keys (`{"key": ...}`),
and a `csv`, `xlsx` or `json` block is refused (`MappingInvalidError`) rather
than silently ignored. `source_schema_sha256` stays `None`: a JSONL source has
no header.

Only a verified content type of `application/jsonl` or `application/x-ndjson`
is imported; any other value is a `failed` report,
`content_type_not_supported_by_importer`, before a byte is read.

A caller's `expected_sha256` (the hash the record/report content is tagged
with) is checked against the `ChunkReader`'s own declared `expected_sha256`
(the hash its bytes are verified against) before a byte is read: whoever
built the reader and whoever calls `run_jsonl_import`/`jsonl_events` can name
different hashes, and only the reader's own end-of-stream check would
otherwise notice -- after already tagging every accepted record with the
caller's possibly-different value. A mismatch is `failed`/`source_changed`,
same as a reader whose bytes do not match its own declared hash.

Scope: pure and synchronous, no database and no network (the packet's
`database_changes`: "No independent schema change. Consume frozen contract").
No import route exists yet to call it; `proving_symbol` stays blank, as for the
sibling importers. `ImportedRecord` / `QuarantinedRow` below are the round-1
row types that `workers/data/json_importer.py` still imports; they go when
FTF-0503 is rebuilt on FTF-0502-P1.
"""

from __future__ import annotations

import hmac
import json
from collections.abc import Callable, Iterable, Iterator, Mapping
from dataclasses import dataclass
from typing import Final

from workers.data.canonical_conversation_normalizer import (
    CanonicalConversationRecord,
    ToolArgumentValidator,
)
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
    StrictJsonError,
    apply_mapping,
    normalize_mapped_record,
    strict_loads,
    to_dataset_record_candidate,
)
from workers.data.record_schema_validator import (
    MAX_CANONICAL_RECORD_BYTES,
    SUPPORTED_SOURCE_TYPES,
)

__all__ = [
    "JSONL_CONTENT_TYPES",
    "ImportedRecord",
    "JsonlAborted",
    "JsonlEventStream",
    "JsonlImportOutcome",
    "QuarantinedRow",
    "import_jsonl",
    "jsonl_events",
    "run_jsonl_import",
]

JSONL_CONTENT_TYPES: Final = frozenset({"application/jsonl", "application/x-ndjson"})

IMPORTER_NAME: Final = "jsonl"
IMPORTER_VERSION: Final = "1"
DEFAULT_SOURCE_TYPE: Final = "upload"

_UTF8_BOM: Final = b"\xef\xbb\xbf"
_UTF16_BOMS: Final = (b"\xff\xfe", b"\xfe\xff")
_HEAD_BYTES: Final = 4
_TABULAR_BLOCKS: Final = frozenset({"csv", "xlsx", "json"})


def _canonical_size(content: object) -> int:
    """The size FTF-0508 measures a record's content by (`record_schema_validator`
    pins the same formula; a test compares the two at the limit)."""
    return len(json.dumps(content, sort_keys=True, separators=(",", ":")).encode("utf-8"))


class JsonlAborted(ImportAborted):
    """A fatal JSONL problem: the import ends `failed` with `failure_code`, and
    `locator` names the physical line (`line:<n>`) it happened on."""

    def __init__(self, failure_code: str, line: int) -> None:
        super().__init__("failed", failure_code)
        self.locator = f"line:{max(line, 1)}"


@dataclass(frozen=True)
class JsonlImportOutcome:
    """`fatal_locator` lives in `report.fatal_locator` (the one shared field every
    format importer feeds, FTF-0502 r3): this is a read-only convenience, never a
    second, independently tracked copy of it."""

    report: ImportReport

    @property
    def fatal_locator(self) -> str | None:
        return self.report.fatal_locator


def _check_mapping(mapping: ImportMapping) -> None:
    """A MappingSpec that cannot describe a JSONL source is refused up front.
    Raises `MappingInvalidError`."""
    if not _TABULAR_BLOCKS.isdisjoint(mapping.document):
        raise MappingInvalidError("bad_shape")
    slots = [
        mapping.messages_slot,
        mapping.prompt,
        mapping.completion,
        mapping.system,
        mapping.group_key,
        *(slot for _name, slot in mapping.metadata_slots),
    ]
    if any(slot is not None and slot.kind != "key" for slot in slots):
        raise MappingInvalidError("bad_slot")


class JsonlEventStream(Iterator[ImportEvent]):
    """The import events of one JSONL source."""

    def __init__(
        self,
        reader: ChunkReader,
        mapping: ImportMapping,
        *,
        verified_content_type: str,
        dataset_sha256: str,
        source_type: str = DEFAULT_SOURCE_TYPE,
        limits: ImportLimits = DEFAULT_LIMITS,
        tool_schemas: Mapping[str, Mapping[str, object]] | None = None,
        tool_argument_validator: ToolArgumentValidator | None = None,
    ) -> None:
        _check_mapping(mapping)
        if type(source_type) is not str or source_type not in SUPPORTED_SOURCE_TYPES:
            raise ValueError("source_type is not a supported source type")
        self._reader = reader
        self._mapping = mapping
        self._verified_content_type = verified_content_type
        self._dataset_sha256 = dataset_sha256
        self._source_type = source_type
        self._limits = limits
        self._tool_schemas = tool_schemas
        self._tool_argument_validator = tool_argument_validator
        self.source_schema_sha256: str | None = None
        self._events = self._generate()

    def __next__(self) -> ImportEvent:
        return next(self._events)

    # -- bytes -> lines ------------------------------------------------------

    def _chunks(self) -> Iterator[bytes]:
        """The source bytes with a leading UTF-8 BOM removed. The first four
        bytes are held back to detect UTF-16 / UTF-32 before any line is cut."""
        head = bytearray()
        checked = False
        for chunk in self._reader:
            if checked:
                yield chunk
                continue
            head += chunk
            if len(head) >= _HEAD_BYTES:
                checked = True
                rest = self._first(bytes(head))
                if rest:
                    yield rest
        if not checked and head:
            rest = self._first(bytes(head))
            if rest:
                yield rest

    @staticmethod
    def _first(head: bytes) -> bytes:
        if head[:2] in _UTF16_BOMS or b"\x00" in head[:_HEAD_BYTES]:
            raise JsonlAborted("unsupported_encoding", 1)
        if head.startswith(_UTF8_BOM):
            return head[len(_UTF8_BOM) :]
        return head

    def _lines(self) -> Iterator[tuple[int, bytes | None]]:
        """`(line number, bytes)` for every LF-delimited line, one trailing CR
        removed; `None` for a line longer than `max_record_bytes`."""
        limit = self._limits.max_record_bytes
        cap = limit + 1  # one more byte than the limit: the CR of a CRLF is not counted
        buffer = bytearray()
        oversize = False
        number = 1
        for chunk in self._chunks():
            view = memoryview(chunk)
            position = 0
            while True:
                newline = chunk.find(b"\n", position)
                end = len(chunk) if newline < 0 else newline
                if not oversize:
                    if len(buffer) + (end - position) > cap:
                        oversize = True
                        buffer.clear()
                    else:
                        buffer += view[position:end]
                if newline < 0:
                    break
                yield number, self._finish_line(buffer, oversize, limit)
                buffer.clear()
                oversize = False
                number += 1
                position = newline + 1
        if oversize or buffer:
            yield number, self._finish_line(buffer, oversize, limit)

    @staticmethod
    def _finish_line(buffer: bytearray, oversize: bool, limit: int) -> bytes | None:
        if oversize:
            return None
        line = bytes(buffer)
        if line.endswith(b"\r"):
            line = line[:-1]
        return None if len(line) > limit else line

    # -- lines -> events -----------------------------------------------------

    def _generate(self) -> Iterator[ImportEvent]:
        if (
            type(self._verified_content_type) is not str
            or self._verified_content_type not in JSONL_CONTENT_TYPES
        ):
            raise ImportAborted("failed", "content_type_not_supported_by_importer")
        if not hmac.compare_digest(
            # the hash the source was declared with, known before a byte is read
            self._reader._expected_sha256,
            self._dataset_sha256,
        ):
            raise ImportAborted("failed", "source_changed")
        record_index = 0
        for number, line in self._lines():
            if line is not None and (not line or line.isspace()):
                yield BlankLine()
                continue
            locator = f"line:{number}"
            index, record_index = record_index, record_index + 1
            if line is None:
                yield RowRejection(locator, "record_too_large")
                continue
            yield self._record(locator, line, index)

    def _record(self, locator: str, line: bytes, index: int) -> ImportEvent:
        try:
            text = line.decode("utf-8")
        except UnicodeDecodeError:
            return RowRejection(locator, "invalid_utf8")
        try:
            value = strict_loads(text, max_depth=self._limits.max_depth)
        except StrictJsonError as exc:
            return RowRejection(locator, exc.code)
        if type(value) is not dict:
            return RowRejection(locator, "record_not_object")
        try:
            mapped = apply_mapping(self._mapping, value)
            record = normalize_mapped_record(
                self._mapping,
                mapped,
                source_record_index=index,
                dataset_sha256=self._dataset_sha256,
                source_type=self._source_type,
                tool_schemas=self._tool_schemas,
                tool_argument_validator=self._tool_argument_validator,
            )
        except RowMappingError as exc:
            return RowRejection(locator, exc.code, exc.slot)
        has_tool_calls = any(message.tool_calls for message in record.messages)
        candidate = to_dataset_record_candidate(
            record,
            record_index=index,
            tool_schema_status="valid" if has_tool_calls else "not_applicable",
        )
        if _canonical_size(candidate["content"]) > MAX_CANONICAL_RECORD_BYTES:
            return RowRejection(locator, "record_too_large")
        return AcceptedRecord(candidate)


def jsonl_events(
    reader: ChunkReader,
    mapping: ImportMapping,
    *,
    verified_content_type: str,
    dataset_sha256: str,
    source_type: str = DEFAULT_SOURCE_TYPE,
    limits: ImportLimits = DEFAULT_LIMITS,
    tool_schemas: Mapping[str, Mapping[str, object]] | None = None,
    tool_argument_validator: ToolArgumentValidator | None = None,
) -> JsonlEventStream:
    """The events of a JSONL source, for `preview` or a caller's own drain.
    Raises `MappingInvalidError` before reading a byte when the mapping cannot
    describe a JSONL source; a `verified_content_type` that is not JSONL aborts
    the first `next()` with `content_type_not_supported_by_importer`, also
    before a byte is read."""
    return JsonlEventStream(
        reader,
        mapping,
        verified_content_type=verified_content_type,
        dataset_sha256=dataset_sha256,
        source_type=source_type,
        limits=limits,
        tool_schemas=tool_schemas,
        tool_argument_validator=tool_argument_validator,
    )


def _drive(
    reader: ChunkReader,
    mapping: ImportMapping,
    *,
    verified_content_type: str,
    dataset_purpose: str,
    expected_sha256: str,
    source_type: str,
    limits: ImportLimits,
    tool_schemas: Mapping[str, Mapping[str, object]] | None,
    tool_argument_validator: ToolArgumentValidator | None,
    on_accepted: Callable[[Mapping[str, object]], None] | None,
) -> Iterator[ImportEvent | JsonlImportOutcome]:
    stream = jsonl_events(
        reader,
        mapping,
        verified_content_type=verified_content_type,
        dataset_sha256=expected_sha256,
        source_type=source_type,
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
            yield event
    except ImportAborted as exc:
        aborted = exc
    report = accumulator.report(
        source_sha256=reader.sha256,
        source_bytes=reader.bytes_read,
        aborted=aborted,
        fatal_locator=aborted.locator if isinstance(aborted, JsonlAborted) else None,
    )
    yield JsonlImportOutcome(report)


def run_jsonl_import(
    reader: ChunkReader,
    mapping: ImportMapping,
    *,
    verified_content_type: str,
    dataset_purpose: str,
    expected_sha256: str,
    source_type: str = DEFAULT_SOURCE_TYPE,
    limits: ImportLimits = DEFAULT_LIMITS,
    tool_schemas: Mapping[str, Mapping[str, object]] | None = None,
    tool_argument_validator: ToolArgumentValidator | None = None,
    on_accepted: Callable[[Mapping[str, object]], None] | None = None,
) -> JsonlImportOutcome:
    """Drains a JSONL source into an `ImportReport`. An abort anywhere (a fatal
    problem, a source outage, a deadline, a rejection budget) is the report's
    `state`, never an exception; a source that was not read to its end cannot be
    `completed`."""
    outcome: JsonlImportOutcome | None = None
    for item in _drive(
        reader,
        mapping,
        verified_content_type=verified_content_type,
        dataset_purpose=dataset_purpose,
        expected_sha256=expected_sha256,
        source_type=source_type,
        limits=limits,
        tool_schemas=tool_schemas,
        tool_argument_validator=tool_argument_validator,
        on_accepted=on_accepted,
    ):
        if isinstance(item, JsonlImportOutcome):
            outcome = item
    assert outcome is not None
    return outcome


def import_jsonl(
    chunks: Iterable[bytes],
    *,
    mapping: ImportMapping,
    expected_sha256: str,
    expected_size: int,
    verified_content_type: str,
    dataset_purpose: str,
    source_type: str = DEFAULT_SOURCE_TYPE,
    limits: ImportLimits = DEFAULT_LIMITS,
    tool_schemas: Mapping[str, Mapping[str, object]] | None = None,
    tool_argument_validator: ToolArgumentValidator | None = None,
) -> Iterator[ImportEvent | ImportReport]:
    """Streams a JSONL source: every accepted record, rejection and blank line
    as it is read, then one final `ImportReport` (never an exception for a bad
    or unavailable source)."""
    reader = ChunkReader(
        chunks, expected_size=expected_size, expected_sha256=expected_sha256, limits=limits
    )
    for item in _drive(
        reader,
        mapping,
        verified_content_type=verified_content_type,
        dataset_purpose=dataset_purpose,
        expected_sha256=expected_sha256,
        source_type=source_type,
        limits=limits,
        tool_schemas=tool_schemas,
        tool_argument_validator=tool_argument_validator,
        on_accepted=None,
    ):
        yield item.report if isinstance(item, JsonlImportOutcome) else item


# ---------------------------------------------------------------------------
# Round-1 row types, still imported by workers/data/json_importer.py
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ImportedRecord:
    """One line that normalized cleanly. `row_index` is this line's
    0-based position in the `lines` iterable this call was given --
    `CanonicalConversationRecord.lineage.source_record_index` carries the
    identical value, so the two are never out of step."""

    row_index: int
    record: CanonicalConversationRecord


@dataclass(frozen=True)
class QuarantinedRow:
    """One line that did not normalize -- malformed UTF-8, an oversize
    line, invalid JSON, a non-object top level, or a real
    `ConversationNormalizationError` from FTF-0506 (which is also this
    module's own "mixed critical schemas" case; see the module docstring).
    `message` always carries the real underlying error text, never a
    generic placeholder."""

    row_index: int
    code: str
    message: str
