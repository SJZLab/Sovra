"""JSON importer (FTF-0503; FINE_TUNING_FACTORY_RELEASE_1.md Section 7, 8.1, 9, 67.2, 67.3).

Round 3: a true streaming scanner. Round 2 held the whole document as one text
buffer (peak 3.2 / 25 / 200 MiB for 1 / 8 / 64 MiB) and let several structural
errors through: `[G, 1"]", G]` completed and silently dropped the third
element, a stray `:` or `x` between elements became a row, malformed /
duplicate-key / very deep / 5 MiB values under an ignored top-level key were
accepted, a UTF-8 BOM was refused, one oversize element ended the whole
import, and every fatal locator was `object:0`.

Turns an already-authorised, size/hash-bound byte stream
(`import_common.ChunkReader`) into P1 import events, the same shape CSV and
JSONL produce.

## Where the records are: the `json` MappingSpec block

  - `container: ""` -- the document's own top-level value is the records. A
    top-level *array* is a container of *N* candidate records, one per
    element. A top-level *object* is itself the one record.
  - `container: "/rows"` -- the array (or single-record object) lives at that
    exact key of a top-level object. Every other top-level key must be in
    `ignored_top_level_keys` (else fatal `unmapped_top_level_key`, at the key).
    A container pointer deeper than one token is `MappingUnsupportedError`
    (`mapping_unsupported`) before a byte is read.

## One pass over the chunks, nothing buffered beyond one value

The scanner is a byte-level state machine fed chunk by chunk, so every result
is independent of where the chunks are cut. It tracks strings (and their
escapes) and, for every open `{` / `[` of the value being read, what that
container expects next (a key, a colon, a value, a comma or its closer), as
well as the position in the document grammar (after `[`, after a comma, after
a key, ...). That is the whole JSON grammar except the spelling of a bare
literal (`01`, `tru`) and of an escape (`\\q`), which `strict_loads` judges. Only the
bytes of the *current* value are kept, up to `limits.max_record_bytes`; a
record element over that is discarded as it arrives (its structure is still
tracked) and becomes one `record_too_large` row while the import continues.
Memory therefore does not depend on the document size.

A value is parsed by P1's `strict_loads` alone (duplicate keys, NaN,
4300-digit ints, lone surrogates, nesting depth), so a bad *value* costs one
row. A bad *structure* -- a value starting with a character that cannot start
a JSON value, a missing or doubled comma or colon, a `]` closing a `{`, an
unclosed string or bracket, a trailing comma, anything after the document's
end, a scalar where the container should be -- is fatal `json_malformed`, located at
the byte offset where it was found (`object:<offset>` in the source,
counting a UTF-8 BOM). A value under an ignored top-level key is parsed too
(strictly) and must fit in `max_record_bytes` (`field_too_large`), because
"ignored" must not mean "unchecked".

## Encoding

A UTF-8 BOM at byte 0 is accepted and skipped. A UTF-16 / UTF-32 BOM, or a NUL
among the first four bytes, is fatal `unsupported_encoding`. The whole stream
is checked as strict UTF-8 as it arrives (`invalid_encoding`, located at the
bad byte), including inside skipped oversize records.

Only a verified content type of `application/json` is imported
(`content_type_not_supported_by_importer`), and a reader declared for another
hash than the caller expects ends `failed` / `source_changed`; both before a
byte is read.

Scope: pure and synchronous, no database and no network. No import route
calls it yet; `proving_symbol` stays blank, as for the sibling importers.
"""

from __future__ import annotations

import codecs
import hashlib
import hmac
import json
import re
from collections.abc import Callable, Generator, Iterator, Mapping
from dataclasses import dataclass
from typing import Final

from workers.data.canonical_conversation_normalizer import ToolArgumentValidator
from workers.data.import_common import (
    DEFAULT_LIMITS,
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
    Slot,
    StrictJsonError,
    apply_mapping,
    normalize_mapped_record,
    parse_pointer,
    strict_loads,
    to_dataset_record_candidate,
)

__all__ = [
    "JSON_CONTENT_TYPES",
    "JSON_FATAL_CODES",
    "JsonAborted",
    "JsonEventStream",
    "JsonImportOutcome",
    "json_events",
    "run_json_import",
]

JSON_CONTENT_TYPES: Final = frozenset({"application/json"})
JSON_FATAL_CODES: Final = frozenset(
    {
        "content_type_not_supported_by_importer",
        "field_too_large",
        "invalid_encoding",
        "json_malformed",
        "mapping_column_missing",
        "source_changed",
        "unmapped_top_level_key",
        "unsupported_encoding",
    }
)

IMPORTER_NAME: Final = "json"
IMPORTER_VERSION: Final = "1"
SOURCE_TYPE: Final = "upload"

# Longest signature first: the UTF-16LE and UTF-32LE marks share a prefix.
_UTF_UNSUPPORTED_BOMS: Final = (b"\xff\xfe\x00\x00", b"\x00\x00\xfe\xff", b"\xff\xfe", b"\xfe\xff")
_UTF8_BOM: Final = b"\xef\xbb\xbf"
_HEAD_BYTES: Final = 4

# A top-level key longer than this cannot be a name the mapping declared.
_KEY_CAP: Final = 4096
# Bracket types are tracked exactly down to this depth; deeper values are
# counted only (their row is rejected `nesting_too_deep` long before this).
_STACK_CAP: Final = 1024

_WS: Final = re.compile(rb"[ \t\r\n]*")
_STRING_RUN: Final = re.compile(rb'[^"\\]*')
_LITERAL_RUN: Final = re.compile(rb"[A-Za-z0-9+\-.]*")
_VALUE_LITERAL_START: Final = frozenset(b"-0123456789tfnNI")

_QUOTE: Final = 0x22
_BACKSLASH: Final = 0x5C
_LBRACE, _RBRACE = 0x7B, 0x7D
_LBRACKET, _RBRACKET = 0x5B, 0x5D
_COMMA, _COLON = 0x2C, 0x3A

# document grammar
_START, _ARR_FIRST, _ARR_AFTER, _ARR_NEXT = 0, 1, 2, 3
_OBJ_FIRST, _OBJ_NEXT, _OBJ_COLON, _OBJ_VALUE, _OBJ_AFTER, _END = 4, 5, 6, 7, 8, 9
_VALUE = 10  # reading one value; `_ctx` says whose

# what the value being read is
_CTX_KEY, _CTX_IGNORED, _CTX_ELEMENT, _CTX_SINGLE = 0, 1, 2, 3
# value shape
_V_NONE, _V_NEST, _V_STRING, _V_LITERAL = 0, 1, 2, 3
# what the innermost open container expects next (one byte per open container)
_A_FIRST, _A_AFTER, _A_NEXT = 1, 2, 3  # array: value or `]` / `,` or `]` / a value
_O_FIRST, _O_COLON, _O_VALUE, _O_AFTER, _O_NEXT = 4, 5, 6, 7, 8  # object
_VALUE_STATES: Final = frozenset({_A_FIRST, _A_NEXT, _O_VALUE})
_KEY_STATES: Final = frozenset({_O_FIRST, _O_NEXT})
_STATE_AFTER_VALUE: Final = {_A_FIRST: _A_AFTER, _A_NEXT: _A_AFTER, _O_VALUE: _O_AFTER}


class JsonAborted(ImportAborted):
    """A fatal JSON problem: the import ends `failed` with `failure_code`, and
    `locator` is `object:<byte offset in the source>` where it was found."""

    def __init__(self, failure_code: str, offset: int) -> None:
        super().__init__("failed", failure_code)
        self.locator = f"object:{max(offset, 0)}"


@dataclass(frozen=True)
class JsonImportOutcome:
    report: ImportReport


@dataclass(frozen=True)
class _Options:
    container_tokens: tuple[str, ...]
    ignored_top_level_keys: frozenset[str]


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
    """The `json` block and the slots of a MappingSpec, refused up front when
    they cannot describe this importer's own scope. Raises `MappingInvalidError`
    (malformed) or `MappingUnsupportedError` (a container deeper than one key)."""
    block = mapping.document.get("json")
    if type(block) is not dict:
        raise MappingInvalidError("missing_key")
    tokens = parse_pointer(block["container"])
    if len(tokens) > 1:
        raise MappingUnsupportedError
    for _name, slot in _mapped_slots(mapping):
        if slot.kind != "key":
            raise MappingInvalidError("bad_slot")
    return _Options(tokens, frozenset(block["ignored_top_level_keys"]))


class JsonEventStream(Iterator[ImportEvent]):
    """The import events of one JSON source. `source_schema_sha256` is set once
    the document's top-level keys are known (nested container only)."""

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
        self._nested = bool(self._options.container_tokens)
        self._container_key = self._options.container_tokens[0] if self._nested else None
        self._decoder = codecs.getincrementaldecoder("utf-8")("strict")
        self._pos = 0  # source offset of the first byte of the next chunk
        self._phase = _START
        self._after_container = _OBJ_AFTER if self._nested else _END
        self._ctx = _CTX_ELEMENT
        self._shape = _V_NONE
        self._value_start = 0
        self._buf: bytearray | None = bytearray()  # None once the value outgrew `_cap`
        self._cap = limits.max_record_bytes
        self._stack = bytearray()
        self._extra_depth = 0
        self._in_string = False
        self._in_literal = False
        self._escape = False
        self._index = 0
        self._keys: list[str] = []
        self._ignored_seen: set[str] = set()
        self._container_seen = False
        self._current_is_container = False
        self._events = self._generate()

    def __next__(self) -> ImportEvent:
        return next(self._events)

    # -- bytes in --------------------------------------------------------------------------

    def _generate(self) -> Iterator[ImportEvent]:
        if (
            type(self._verified_content_type) is not str
            or self._verified_content_type not in JSON_CONTENT_TYPES
        ):
            raise ImportAborted("failed", "content_type_not_supported_by_importer")
        if not hmac.compare_digest(
            # the hash the source was declared with, known before a byte is read
            self._reader._expected_sha256,
            self._dataset_sha256,
        ):
            raise ImportAborted("failed", "source_changed")
        head = bytearray()
        checked = False
        for chunk in self._reader:
            if not checked:
                head += chunk
                if len(head) < _HEAD_BYTES:
                    continue
                checked = True
                chunk = self._first(bytes(head))
                head.clear()
            if chunk:
                yield from self._process(chunk)
        if not checked:
            rest = self._first(bytes(head))
            if rest:
                yield from self._process(rest)
        self._finish()

    def _first(self, head: bytes) -> bytes:
        """The first bytes of the source, judged before anything is read."""
        if head.startswith(_UTF_UNSUPPORTED_BOMS) or b"\x00" in head[:_HEAD_BYTES]:
            raise JsonAborted("unsupported_encoding", 0)
        if head.startswith(_UTF8_BOM):
            self._pos = len(_UTF8_BOM)
            return head[len(_UTF8_BOM) :]
        return head

    def _process(self, data: bytes) -> Iterator[ImportEvent]:
        pending = len(self._decoder.getstate()[0])
        if pending or not data.isascii():
            try:
                self._decoder.decode(data)
            except UnicodeDecodeError as exc:
                raise JsonAborted("invalid_encoding", self._pos - pending + exc.start) from None
        yield from self._feed(data)
        self._pos += len(data)

    def _finish(self) -> None:
        pending = len(self._decoder.getstate()[0])
        if pending:
            raise JsonAborted("invalid_encoding", self._pos - pending)
        if self._phase != _END:
            raise JsonAborted("json_malformed", self._pos)

    # -- the grammar -----------------------------------------------------------------------

    def _feed(self, chunk: bytes) -> Iterator[ImportEvent]:
        size = len(chunk)
        base = self._pos
        i = 0
        while i < size:
            if self._phase == _VALUE:
                i = yield from self._scan_value(chunk, i, base)
                continue
            i = _WS.match(chunk, i).end()  # type: ignore[union-attr]
            if i >= size:
                break
            c = chunk[i]
            phase = self._phase
            if phase == _START:
                if c == _LBRACKET and not self._nested:
                    self._phase = _ARR_FIRST
                elif c == _LBRACE and not self._nested:
                    self._begin_value(_CTX_SINGLE)
                    continue
                elif c == _LBRACE:
                    self._phase = _OBJ_FIRST
                else:
                    raise JsonAborted("json_malformed", base + i)
                i += 1
            elif phase == _ARR_FIRST:
                if c == _RBRACKET:
                    self._phase = self._after_container
                    i += 1
                else:
                    self._begin_value(_CTX_ELEMENT)
            elif phase == _ARR_AFTER:
                if c == _COMMA:
                    self._phase = _ARR_NEXT
                elif c == _RBRACKET:
                    self._phase = self._after_container
                else:
                    raise JsonAborted("json_malformed", base + i)
                i += 1
            elif phase == _ARR_NEXT:
                self._begin_value(_CTX_ELEMENT)
            elif phase in (_OBJ_FIRST, _OBJ_NEXT):
                if c == _QUOTE:
                    self._begin_value(_CTX_KEY)
                elif c == _RBRACE and phase == _OBJ_FIRST:
                    i += 1
                    self._close_root_object(base + i - 1)
                else:
                    raise JsonAborted("json_malformed", base + i)
            elif phase == _OBJ_COLON:
                if c != _COLON:
                    raise JsonAborted("json_malformed", base + i)
                self._phase = _OBJ_VALUE
                i += 1
            elif phase == _OBJ_VALUE:
                if not self._current_is_container:
                    self._begin_value(_CTX_IGNORED)
                elif c == _LBRACKET:
                    self._phase = _ARR_FIRST
                    i += 1
                elif c == _LBRACE:
                    self._begin_value(_CTX_SINGLE)
                else:
                    raise JsonAborted("json_malformed", base + i)
            elif phase == _OBJ_AFTER:
                if c == _COMMA:
                    self._phase = _OBJ_NEXT
                    i += 1
                elif c == _RBRACE:
                    i += 1
                    self._close_root_object(base + i - 1)
                else:
                    raise JsonAborted("json_malformed", base + i)
            else:  # _END: only whitespace may follow the document
                raise JsonAborted("json_malformed", base + i)

    def _close_root_object(self, offset: int) -> None:
        if self._nested:
            keys = sorted(self._keys)
            text = json.dumps(keys, ensure_ascii=True, separators=(",", ":"))
            self.source_schema_sha256 = hashlib.sha256(text.encode("ascii")).hexdigest()
            if not self._container_seen:
                raise JsonAborted("mapping_column_missing", offset)
        self._phase = _END

    # -- one value -------------------------------------------------------------------------

    def _begin_value(self, ctx: int) -> None:
        self._phase = _VALUE
        self._ctx = ctx
        self._shape = _V_NONE
        self._buf = bytearray()
        self._stack.clear()
        self._extra_depth = 0
        self._in_string = False
        self._in_literal = False
        self._escape = False
        self._cap = _KEY_CAP if ctx == _CTX_KEY else self._limits.max_record_bytes

    def _retain(self, chunk: bytes, start: int, end: int) -> None:
        buf = self._buf
        if buf is None:
            return
        if len(buf) + (end - start) > self._cap:
            self._buf = None
            if self._ctx == _CTX_KEY:
                raise JsonAborted("unmapped_top_level_key", self._value_start)
            if self._ctx == _CTX_IGNORED:
                raise JsonAborted("field_too_large", self._value_start)
            return
        buf += memoryview(chunk)[start:end]

    def _value_bytes(self) -> bytes:
        assert self._buf is not None
        return bytes(self._buf)

    def _skip_string(self, chunk: bytes, i: int) -> tuple[int, bool]:
        """Advances over string content; True when the closing quote was consumed."""
        size = len(chunk)
        while i < size:
            if self._escape:
                self._escape = False
                i += 1
                continue
            i = _STRING_RUN.match(chunk, i).end()  # type: ignore[union-attr]
            if i >= size:
                return i, False
            if chunk[i] == _QUOTE:
                return i + 1, True
            self._escape = True
            i += 1
        return i, False

    def _scan_value(self, chunk: bytes, i: int, base: int) -> Generator[ImportEvent, None, int]:
        size = len(chunk)
        start = i
        done = False
        if self._shape == _V_NONE:
            c = chunk[i]
            self._value_start = base + i
            if c == _LBRACE or c == _LBRACKET:
                self._shape = _V_NEST
                self._stack.append(_A_FIRST if c == _LBRACKET else _O_FIRST)
                i += 1
            elif c == _QUOTE:
                self._shape = _V_STRING
                i += 1
            elif c in _VALUE_LITERAL_START and self._ctx != _CTX_KEY:
                self._shape = _V_LITERAL
            else:
                raise JsonAborted("json_malformed", base + i)
        while i < size and not done:
            shape = self._shape
            if shape == _V_STRING:
                i, done = self._skip_string(chunk, i)
            elif shape == _V_LITERAL:
                i = _LITERAL_RUN.match(chunk, i).end()  # type: ignore[union-attr]
                done = i < size
            elif self._in_string:
                i, closed = self._skip_string(chunk, i)
                self._in_string = not closed
            elif self._in_literal:
                i = _LITERAL_RUN.match(chunk, i).end()  # type: ignore[union-attr]
                self._in_literal = i >= size
            else:
                i = _WS.match(chunk, i).end()  # type: ignore[union-attr]
                if i >= size:
                    break
                done = self._token(chunk[i], base + i)
                i += 1
        self._retain(chunk, start, i)
        if done:
            yield from self._complete()
        return i

    def _token(self, c: int, offset: int) -> bool:
        """One structural byte inside a `{...}` / `[...]` value, checked against what the
        innermost container expects next. True when the outermost container closed."""
        stack = self._stack
        if self._extra_depth:  # deeper than the exact tracking: count brackets and strings only
            if c == _QUOTE:
                self._in_string = True
            elif c == _LBRACE or c == _LBRACKET:
                self._extra_depth += 1
            elif c == _RBRACE or c == _RBRACKET:
                self._extra_depth -= 1
            return False
        state = stack[-1]
        if c == _QUOTE:
            if state in _VALUE_STATES:
                stack[-1] = _STATE_AFTER_VALUE[state]
            elif state in _KEY_STATES:
                stack[-1] = _O_COLON
            else:
                raise JsonAborted("json_malformed", offset)
            self._in_string = True
        elif c == _LBRACE or c == _LBRACKET:
            if state not in _VALUE_STATES:
                raise JsonAborted("json_malformed", offset)
            stack[-1] = _STATE_AFTER_VALUE[state]
            if len(stack) >= _STACK_CAP:
                self._extra_depth = 1
            else:
                stack.append(_A_FIRST if c == _LBRACKET else _O_FIRST)
        elif c in _VALUE_LITERAL_START:
            if state not in _VALUE_STATES:
                raise JsonAborted("json_malformed", offset)
            stack[-1] = _STATE_AFTER_VALUE[state]
            self._in_literal = True
        elif c == _COMMA and state in (_A_AFTER, _O_AFTER):
            stack[-1] = _A_NEXT if state == _A_AFTER else _O_NEXT
        elif c == _COLON and state == _O_COLON:
            stack[-1] = _O_VALUE
        elif (c == _RBRACKET and state in (_A_FIRST, _A_AFTER)) or (
            c == _RBRACE and state in (_O_FIRST, _O_AFTER)
        ):
            stack.pop()
            return not stack
        else:
            raise JsonAborted("json_malformed", offset)
        return False

    def _complete(self) -> Iterator[ImportEvent]:
        ctx = self._ctx
        if ctx == _CTX_KEY:
            self._accept_key()
            return
        if ctx == _CTX_IGNORED:
            self._check_ignored()
            self._phase = _OBJ_AFTER
            return
        index = self._index
        self._index += 1
        yield from self._emit_record(index)
        self._phase = _ARR_AFTER if ctx == _CTX_ELEMENT else self._after_container
        return

    def _accept_key(self) -> None:
        offset = self._value_start
        try:
            key = json.loads(self._value_bytes())
        except ValueError:
            raise JsonAborted("json_malformed", offset) from None
        if key == self._container_key:
            if self._container_seen:
                raise JsonAborted("mapping_column_missing", offset)
            self._container_seen = True
            self._current_is_container = True
        elif key in self._options.ignored_top_level_keys:
            if key in self._ignored_seen:
                raise JsonAborted("json_malformed", offset)
            self._ignored_seen.add(key)
            self._current_is_container = False
        else:
            raise JsonAborted("unmapped_top_level_key", offset)
        self._keys.append(key)
        self._phase = _OBJ_COLON

    def _check_ignored(self) -> None:
        try:
            strict_loads(self._value_bytes(), max_depth=self._limits.max_depth)
        except StrictJsonError:
            raise JsonAborted("json_malformed", self._value_start) from None

    def _emit_record(self, index: int) -> Iterator[ImportEvent]:
        locator = f"record:{index}"
        if self._buf is None:
            yield RowRejection(locator, "record_too_large")
            return
        try:
            parsed = strict_loads(self._value_bytes(), max_depth=self._limits.max_depth)
        except StrictJsonError as exc:
            yield RowRejection(locator, exc.code)
            return
        if type(parsed) is not dict:
            yield RowRejection(locator, "record_not_object")
            return
        try:
            mapped = apply_mapping(self._mapping, parsed)
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
            yield RowRejection(locator, exc.code, exc.slot)
            return
        has_tool_calls = any(message.tool_calls for message in record.messages)
        yield AcceptedRecord(
            to_dataset_record_candidate(
                record,
                record_index=index,
                tool_schema_status="valid" if has_tool_calls else "not_applicable",
            )
        )


def json_events(
    reader: ChunkReader,
    mapping: ImportMapping,
    *,
    verified_content_type: str,
    dataset_sha256: str,
    limits: ImportLimits = DEFAULT_LIMITS,
    tool_schemas: Mapping[str, Mapping[str, object]] | None = None,
    tool_argument_validator: ToolArgumentValidator | None = None,
) -> JsonEventStream:
    """The events of a JSON source, for `preview` or a caller's own drain.
    Raises `MappingInvalidError` / `MappingUnsupportedError` before reading a
    byte when the mapping cannot describe this importer's scope; a
    `verified_content_type` other than `application/json`, or a reader declared
    for another hash than `dataset_sha256`, aborts the first `next()`, also
    before a byte is read."""
    return JsonEventStream(
        reader,
        mapping,
        verified_content_type=verified_content_type,
        dataset_sha256=dataset_sha256,
        limits=limits,
        tool_schemas=tool_schemas,
        tool_argument_validator=tool_argument_validator,
    )


def run_json_import(
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
) -> JsonImportOutcome:
    """Drains a JSON source into an `ImportReport`. An abort anywhere (a fatal
    JSON problem, a source outage, a deadline, a rejection budget) is the
    report's `state`, never an exception."""
    stream = json_events(
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
        fatal_locator=aborted.locator if isinstance(aborted, JsonAborted) else None,
    )
    return JsonImportOutcome(report)
