"""JSON importer (FTF-0503, round 2; FINE_TUNING_FACTORY_RELEASE_1.md Section 7, 8.1, 67.2, 67.3).

Turns an already-authorised, size/hash-bound byte stream (FTF-0502-P1's `ChunkReader`) holding
one JSON document into FTF-0502-P1 import events, one record at a time, without ever holding
the document: a linear byte scanner finds the records and hands each one, alone, to P1's
`strict_loads`.

Approved containers are named in the mapping (`json.container`), nothing is guessed:

- `"$"`          the root is an array of records;
- `"/<key>"`     the root is an object; exactly that key (an exact, case-sensitive match on the
                 decoded key; `~1` / `~0` unescape as in a JSON pointer) holds the array. Every
                 other root key must be listed in `json.ignored_top_level_keys`, else the import
                 ends `failed` / `unmapped_top_level_key`. An ignored value is scanned (strings,
                 escapes, bracket kinds, depth) and bounded by `max_record_bytes`, never kept;
- `"$record"`    the root is an object that is one record (at most `max_record_bytes`).

Anything deeper or different (`/a/b`, an empty pointer) is `MappingUnsupportedError`
(`mapping_unsupported`), and so is a mapping with no `json` block, a `csv` / `xlsx` block, or a
slot that is not an object key (`MappingInvalidError`); all before a byte is read.

Records: each element is at most `limits.max_record_bytes` bytes (the whitespace around it is
not counted); it is cut out by the scanner and parsed alone by `strict_loads`, so duplicate keys,
NaN / Infinity, 1e999, a 4,301-digit integer and a lone surrogate are that element's rejection
(`duplicate_key`, `non_finite_number`, `number_out_of_range`, `invalid_unicode`, `invalid_utf8`,
`invalid_json`, `record_not_object`, then the mapping's own codes), and the next element still
imports. An element over the limit is skipped by scanning without being buffered and rejected
`record_too_large`; so is one whose canonical content (what FTF-0508 measures) is over
`MAX_CANONICAL_RECORD_BYTES`. Locators are `record:<n>`, the 1-based ordinal of the element
(rejected ones included), so `records_seen == records_accepted + records_rejected`.

Structure is not a row. These end the import `failed`, located at the byte offset of the
offending byte (`JsonImportOutcome.fatal_locator`, `offset:<n>`) with the mapping's own container
pointer (`fatal_container`), never any document content:

- `json_structure_invalid`   trailing / leading / doubled comma, missing comma, a token that is
                             not a value between elements, mismatched or unbalanced brackets, a
                             non-string or missing key, a missing colon, a scalar root, an empty
                             document, a truncated document, data after the root, a byte that
                             is not JSON whitespace between tokens (VT, FF, NBSP ...), a BOM
                             anywhere but byte 0;
- `container_not_found`      the mapped key is absent, or the root is not an object holding it;
- `container_not_array`      the mapped key (or a `$` root) holds something other than an array;
- `duplicate_root_key`       a root key written twice (compared after decoding escapes);
- `unmapped_top_level_key`   a root key that is neither the container nor ignored;
- `limit_exceeded`           nesting deeper than `limits.max_depth` (the root counts as depth 1),
                             or an ignored value over `max_record_bytes`;
- `unsupported_encoding`     UTF-16 / UTF-32 (a BOM, or a NUL among the first four bytes).

UTF-8 only; a BOM is accepted at byte 0 only (offsets count its three bytes). Whitespace is the
four JSON characters. An empty container is `completed` with 0 records.

Memory is bounded by the largest chunk plus `max_record_bytes`, and time is linear: the scanner
jumps between interesting bytes with compiled regular expressions and never rescans a chunk, so
neither depends on the chunking.

Only a verified content type of exactly `application/json` is imported; any other value is a
`failed` report, `content_type_not_supported_by_importer`, before a byte is read.

Scope: pure and synchronous, no database and no network. No import route exists yet to call it;
`proving_symbol` stays blank, as for the sibling importers. `source_schema_sha256` stays `None`.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Iterable, Iterator, Mapping
from dataclasses import dataclass
from typing import Final

from workers.data.canonical_conversation_normalizer import ToolArgumentValidator
from workers.data.import_common import (
    DEFAULT_LIMITS,
    JSON_ROOT_CONTAINERS,
    AcceptedRecord,
    ChunkReader,
    ImportAborted,
    ImportAccumulator,
    ImportEvent,
    ImportLimits,
    ImportMapping,
    ImportReport,
    MappingInvalidError,
    MappingUnsupportedError,
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
    "JSON_CONTENT_TYPES",
    "JsonAborted",
    "JsonContainerScanner",
    "JsonEventStream",
    "JsonImportOutcome",
    "ScannedElement",
    "import_json",
    "json_events",
    "run_json_import",
]

JSON_CONTENT_TYPES: Final = frozenset({"application/json"})

IMPORTER_NAME: Final = "json"
IMPORTER_VERSION: Final = "1"
DEFAULT_SOURCE_TYPE: Final = "upload"

_UTF8_BOM: Final = b"\xef\xbb\xbf"
_UTF16_BOMS: Final = (b"\xff\xfe", b"\xfe\xff")
_HEAD_BYTES: Final = 4
# A root key is a mapping name (at most 256 characters); even all `\uXXXX\uXXXX` escapes
# (12 bytes per astral character) fit here, so a longer key cannot equal any mapped name.
_MAX_KEY_BYTES: Final = 256 * 12

_QUOTE: Final = 0x22
_BACKSLASH: Final = 0x5C
_COMMA: Final = 0x2C
_COLON: Final = 0x3A
_LBRACKET: Final = 0x5B
_RBRACKET: Final = 0x5D
_LBRACE: Final = 0x7B
_RBRACE: Final = 0x7D
_NOT_A_VALUE_START: Final = frozenset({_COMMA, _COLON, _RBRACKET, _RBRACE})

_MODE_ARRAY: Final = 0  # json.container "$"
_MODE_RECORD: Final = 1  # json.container "$record"
_MODE_KEY: Final = 2  # json.container "/<key>"
_ROOT_MODES: Final = dict(zip(JSON_ROOT_CONTAINERS, (_MODE_ARRAY, _MODE_RECORD), strict=True))

_NOT_WS: Final = re.compile(rb"[^ \t\n\r]")
# A closing quote, or a backslash with the byte it escapes (absent at the end of a chunk).
_STRING_EVENT: Final = re.compile(rb'"|\\.?', re.DOTALL)
_STRUCTURE: Final = re.compile(rb'["\[\]{}]')
_TOKEN_END: Final = re.compile(rb"[ \t\n\r,\]}]")


# ---------------------------------------------------------------------------
# Fatal structure problems
# ---------------------------------------------------------------------------


class JsonAborted(ImportAborted):
    """A fatal JSON problem: the import ends `failed` with `failure_code`, and `locator` names
    the byte offset (`offset:<n>`) of the offending byte."""

    def __init__(self, failure_code: str, offset: int) -> None:
        super().__init__("failed", failure_code)
        self.offset = offset
        self.locator = f"offset:{offset}"


@dataclass(frozen=True)
class ScannedElement:
    """One element of the container. `data` is its exact bytes, or `None` when it was longer
    than `max_record_bytes` and was skipped without being kept. `offset` is where it starts."""

    ordinal: int
    data: bytes | None
    offset: int


def container_key(container: object) -> tuple[int, str]:
    """`(mode, key)` for a `json.container`: `_MODE_ARRAY` / `_MODE_RECORD` with an empty key
    for `"$"` / `"$record"`, `_MODE_KEY` with the decoded key for `"/<key>"`. Raises
    `MappingUnsupportedError` for any other spelling (a deeper pointer, an empty one, a
    non-string)."""
    if type(container) is not str:
        raise MappingUnsupportedError
    root_mode = _ROOT_MODES.get(container)
    if root_mode is not None:
        return root_mode, ""
    if not container.startswith("/") or "/" in container[1:]:
        raise MappingUnsupportedError
    return _MODE_KEY, container[1:].replace("~1", "/").replace("~0", "~")


class _Cursor:
    """A position in a stream of chunks. `base` is the stream offset of `buf[0]`."""

    __slots__ = ("_chunks", "base", "buf", "pos")

    def __init__(self, chunks: Iterable[bytes]) -> None:
        self._chunks = iter(chunks)
        self.buf = b""
        self.pos = 0
        self.base = 0

    @property
    def offset(self) -> int:
        return self.base + self.pos

    def more(self) -> bool:
        """Makes `buf[pos]` readable; False at the end of the stream."""
        while self.pos >= len(self.buf):
            chunk = next(self._chunks, None)
            if chunk is None:
                return False
            self.base += len(self.buf)
            self.buf = chunk
            self.pos = 0
        return True

    def hold(self, count: int) -> bytes:
        """The next `count` bytes (fewer at the end of the stream), not consumed."""
        while len(self.buf) - self.pos < count:
            chunk = next(self._chunks, None)
            if chunk is None:
                break
            self.base += self.pos
            self.buf = self.buf[self.pos :] + chunk
            self.pos = 0
        return self.buf[self.pos : self.pos + count]

    def skip_whitespace(self) -> bool:
        """Moves to the next non-whitespace byte; False at the end of the stream."""
        while self.more():
            found = _NOT_WS.search(self.buf, self.pos)
            if found is not None:
                self.pos = found.start()
                return True
            self.pos = len(self.buf)
        return False

    @property
    def byte(self) -> int:
        return self.buf[self.pos]


class JsonContainerScanner:
    """Splits the elements of a mapped container out of a JSON document, one at a time.

    `elements()` yields `ScannedElement`s and raises `JsonAborted` for a structure problem."""

    def __init__(
        self,
        chunks: Iterable[bytes],
        *,
        container: str,
        ignored_top_level_keys: frozenset[str],
        max_depth: int,
        max_record_bytes: int,
    ) -> None:
        self._mode, self._key_name = container_key(container)
        self._ignored = ignored_top_level_keys
        self._max_depth = max_depth
        self._cap = max_record_bytes
        self._c = _Cursor(chunks)
        self._ordinal = 0

    # -- fatal verdicts ---------------------------------------------------------------

    def _structure(self, offset: int) -> JsonAborted:
        return JsonAborted("json_structure_invalid", offset)

    def _truncated(self) -> JsonAborted:
        return JsonAborted("json_structure_invalid", self._c.offset)

    # -- the document ------------------------------------------------------------------

    def elements(self) -> Iterator[ScannedElement]:
        c = self._c
        head = c.hold(_HEAD_BYTES)
        if head[:2] in _UTF16_BOMS or b"\x00" in head:
            raise JsonAborted("unsupported_encoding", 0)
        if head.startswith(_UTF8_BOM):
            c.pos += len(_UTF8_BOM)
        if not c.skip_whitespace():
            raise self._truncated()
        first = c.byte
        if self._mode == _MODE_ARRAY:
            if first == _LBRACE:
                raise JsonAborted("container_not_array", c.offset)
            if first != _LBRACKET:
                raise self._structure(c.offset)
            c.pos += 1
            yield from self._items(1)
        elif self._mode == _MODE_RECORD:
            if first == _LBRACKET:
                raise JsonAborted("container_not_found", c.offset)
            if first != _LBRACE:
                raise self._structure(c.offset)
            start = c.offset
            data = self._value(0, True)
            self._ordinal += 1
            yield ScannedElement(self._ordinal, data, start)
        else:
            if first == _LBRACKET:
                raise JsonAborted("container_not_found", c.offset)
            if first != _LBRACE:
                raise self._structure(c.offset)
            c.pos += 1
            yield from self._members()
        if c.skip_whitespace():
            raise self._structure(c.offset)

    def _items(self, base: int) -> Iterator[ScannedElement]:
        """The elements of an array whose `[` was just consumed; `base` containers enclose
        the elements."""
        c = self._c
        if not c.skip_whitespace():
            raise self._truncated()
        if c.byte == _RBRACKET:
            c.pos += 1
            return
        while True:
            if not c.skip_whitespace():
                raise self._truncated()
            if c.byte in _NOT_A_VALUE_START:
                raise self._structure(c.offset)
            start = c.offset
            data = self._value(base, True)
            self._ordinal += 1
            yield ScannedElement(self._ordinal, data, start)
            if not c.skip_whitespace():
                raise self._truncated()
            if c.byte == _COMMA:
                c.pos += 1
            elif c.byte == _RBRACKET:
                c.pos += 1
                return
            else:
                raise self._structure(c.offset)

    def _members(self) -> Iterator[ScannedElement]:
        """The members of a root object whose `{` was just consumed."""
        c = self._c
        seen: set[str] = set()
        found = False
        if not c.skip_whitespace():
            raise self._truncated()
        if c.byte == _RBRACE:
            raise JsonAborted("container_not_found", c.offset)
        while True:
            if not c.skip_whitespace():
                raise self._truncated()
            if c.byte != _QUOTE:
                raise self._structure(c.offset)
            key_offset = c.offset
            key = self._key(key_offset)
            if not c.skip_whitespace():
                raise self._truncated()
            if c.byte != _COLON:
                raise self._structure(c.offset)
            c.pos += 1
            if not c.skip_whitespace():
                raise self._truncated()
            if key in seen:
                raise JsonAborted("duplicate_root_key", key_offset)
            seen.add(key)
            if key == self._key_name:
                if c.byte != _LBRACKET:
                    raise JsonAborted("container_not_array", c.offset)
                c.pos += 1
                found = True
                yield from self._items(2)
            elif key in self._ignored:
                if c.byte in _NOT_A_VALUE_START:
                    raise self._structure(c.offset)
                start = c.offset
                if self._value(1, False) is None:
                    raise JsonAborted("limit_exceeded", start)
            else:
                raise JsonAborted("unmapped_top_level_key", key_offset)
            if not c.skip_whitespace():
                raise self._truncated()
            if c.byte == _COMMA:
                c.pos += 1
            elif c.byte == _RBRACE:
                break
            else:
                raise self._structure(c.offset)
        if not found:
            raise JsonAborted("container_not_found", c.offset)
        c.pos += 1

    def _key(self, offset: int) -> str:
        """Reads the string key at the cursor. One too long to equal any mapped name is
        `unmapped_top_level_key` at `offset`; one that is not a valid JSON string is a
        structure error there."""
        c = self._c
        c.pos += 1
        raw = bytearray()
        escaped = False
        while True:
            if not c.more():
                raise self._truncated()
            buf, pos = c.buf, c.pos
            start = pos
            closed = False
            while pos < len(buf):
                if escaped:
                    pos += 1
                    escaped = False
                    continue
                found = _STRING_EVENT.search(buf, pos)
                if found is None:
                    pos = len(buf)
                    break
                pos = found.end()
                if buf[found.start()] == _QUOTE:
                    closed = True
                    break
                if found.end() - found.start() == 1:
                    escaped = True
            raw += buf[start : pos - 1 if closed else pos]
            if len(raw) > _MAX_KEY_BYTES:
                raise JsonAborted("unmapped_top_level_key", offset)
            c.pos = pos
            if closed:
                break
        try:
            key = strict_loads(b'"' + bytes(raw) + b'"')
        except StrictJsonError:
            raise self._structure(offset) from None
        assert type(key) is str
        return key

    def _value(self, base: int, capture: bool) -> bytes | None:
        """Scans one JSON value starting at the cursor (a non-whitespace byte that is not a
        separator or closer). Returns its bytes (empty when `capture` is not set), or `None`
        when it is longer than `max_record_bytes`. `base` is the number of containers already
        open around it."""
        c = self._c
        cap = self._cap
        limit = self._max_depth
        kept = bytearray()
        size = 0
        over = False
        stack = bytearray()
        in_string = False
        escaped = False
        in_token = False
        while True:
            if not c.more():
                if in_token:
                    break
                raise self._truncated()
            buf, pos = c.buf, c.pos
            start = pos
            ended = False
            while pos < len(buf):
                if escaped:
                    pos += 1
                    escaped = False
                    continue
                if in_string:
                    found = _STRING_EVENT.search(buf, pos)
                    if found is None:
                        pos = len(buf)
                        break
                    pos = found.end()
                    if buf[found.start()] == _QUOTE:
                        in_string = False
                        if not stack:
                            ended = True
                            break
                    elif found.end() - found.start() == 1:
                        escaped = True
                    continue
                if in_token:
                    found = _TOKEN_END.search(buf, pos)
                    if found is None:
                        pos = len(buf)
                    else:
                        pos = found.start()
                        ended = True
                    break
                if stack:
                    found = _STRUCTURE.search(buf, pos)
                    if found is None:
                        pos = len(buf)
                        break
                    at = found.start()
                    ch = buf[at]
                    pos = found.end()
                    if ch == _QUOTE:
                        in_string = True
                    elif ch == _LBRACKET or ch == _LBRACE:
                        if base + len(stack) + 1 > limit:
                            raise JsonAborted("limit_exceeded", c.base + at)
                        stack.append(_RBRACKET if ch == _LBRACKET else _RBRACE)
                    else:
                        if stack[-1] != ch:
                            raise self._structure(c.base + at)
                        stack.pop()
                        if not stack:
                            ended = True
                            break
                    continue
                ch = buf[pos]
                if ch == _QUOTE:
                    in_string = True
                    pos += 1
                elif ch == _LBRACKET or ch == _LBRACE:
                    if base + 1 > limit:
                        raise JsonAborted("limit_exceeded", c.base + pos)
                    stack.append(_RBRACKET if ch == _LBRACKET else _RBRACE)
                    pos += 1
                else:
                    in_token = True
            size += pos - start
            if capture and not over:
                kept += buf[start:pos]
            if size > cap:
                over = True
                kept.clear()
            c.pos = pos
            if ended:
                break
        return None if over else bytes(kept)


# ---------------------------------------------------------------------------
# The importer
# ---------------------------------------------------------------------------


def _canonical_size(content: object) -> int:
    """The size FTF-0508 measures a record's content by (`record_schema_validator` pins the
    same formula; a test compares the two at the limit)."""
    return len(json.dumps(content, sort_keys=True, separators=(",", ":")).encode("utf-8"))


@dataclass(frozen=True)
class JsonImportOutcome:
    report: ImportReport
    fatal_locator: str | None
    fatal_container: str | None


_TABULAR_BLOCKS: Final = frozenset({"csv", "xlsx"})


def _check_mapping(mapping: ImportMapping) -> tuple[str, frozenset[str]]:
    """A MappingSpec that cannot describe a JSON source is refused up front. Raises
    `MappingInvalidError` or `MappingUnsupportedError`; returns the container as written and
    the ignored root keys."""
    block = mapping.document.get("json")
    if type(block) is not dict or not _TABULAR_BLOCKS.isdisjoint(mapping.document):
        raise MappingInvalidError("bad_shape")
    slots = [
        mapping.messages_slot,
        mapping.prompt,
        mapping.completion,
        mapping.system,
        mapping.group_key,
        *(slot for _name, slot in mapping.metadata_slots),
    ]
    if {slot.kind for slot in slots if slot is not None} - {"key"}:
        raise MappingInvalidError("bad_slot")
    container = block["container"]
    container_key(container)
    return container, frozenset(block["ignored_top_level_keys"])


class JsonEventStream(Iterator[ImportEvent]):
    """The import events of one JSON source."""

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
        self.container, self._ignored = _check_mapping(mapping)
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

    def _generate(self) -> Iterator[ImportEvent]:
        if (
            type(self._verified_content_type) is not str
            or self._verified_content_type not in JSON_CONTENT_TYPES
        ):
            raise ImportAborted("failed", "content_type_not_supported_by_importer")
        scanner = JsonContainerScanner(
            self._reader,
            container=self.container,
            ignored_top_level_keys=self._ignored,
            max_depth=self._limits.max_depth,
            max_record_bytes=self._limits.max_record_bytes,
        )
        for element in scanner.elements():
            locator = f"record:{element.ordinal}"
            if element.data is None:
                yield RowRejection(locator, "record_too_large")
                continue
            yield self._record(locator, element.data, element.ordinal - 1)

    def _record(self, locator: str, data: bytes, index: int) -> ImportEvent:
        try:
            text = data.decode("utf-8")
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


def json_events(
    reader: ChunkReader,
    mapping: ImportMapping,
    *,
    verified_content_type: str,
    dataset_sha256: str,
    source_type: str = DEFAULT_SOURCE_TYPE,
    limits: ImportLimits = DEFAULT_LIMITS,
    tool_schemas: Mapping[str, Mapping[str, object]] | None = None,
    tool_argument_validator: ToolArgumentValidator | None = None,
) -> JsonEventStream:
    """The events of a JSON source, for `preview` or a caller's own drain. Raises
    `MappingInvalidError` / `MappingUnsupportedError` before reading a byte when the mapping
    cannot describe a JSON source; a `verified_content_type` that is not `application/json`
    aborts the first `next()` with `content_type_not_supported_by_importer`, also before a byte
    is read."""
    return JsonEventStream(
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
) -> Iterator[ImportEvent | JsonImportOutcome]:
    stream = json_events(
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
        source_sha256=reader.sha256, source_bytes=reader.bytes_read, aborted=aborted
    )
    if isinstance(aborted, JsonAborted):
        yield JsonImportOutcome(report, aborted.locator, stream.container)
    else:
        yield JsonImportOutcome(report, None, None)


def run_json_import(
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
) -> JsonImportOutcome:
    """Drains a JSON source into an `ImportReport`. An abort anywhere (a fatal problem, a
    source outage, a deadline, a rejection budget) is the report's `state`, never an
    exception; a source that was not read to its end cannot be `completed`."""
    outcome: JsonImportOutcome | None = None
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
        if isinstance(item, JsonImportOutcome):
            outcome = item
    assert outcome is not None
    return outcome


def import_json(
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
    """Streams a JSON source: every accepted record and rejection as it is read, then one
    final `ImportReport` (never an exception for a bad or unavailable source)."""
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
        yield item.report if isinstance(item, JsonImportOutcome) else item
