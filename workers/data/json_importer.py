"""JSON importer (FTF-0503; FINE_TUNING_FACTORY_RELEASE_1.md Section 7, 8.1, 9, 67.2, 67.3).

Round 3: a true single-pass streaming scanner, replacing round 2's
"span-based but whole-document-buffered" design. Round 2 fixed round 1's
"json.loads the whole document" memory blowup for *parsing* (per-element
`strict_loads`, never one document-wide object graph) but still read the
*entire* source into one `bytearray`, then one `bytes`, then one decoded
`str`, before scanning it -- three live full-document copies, the real
reason a 1 MiB document peaked at 62x its own size and a 64 MiB one peaked
at over 3x the sibling CSV/JSONL importers' 1.5x contract. Round 2 also ran
two independent passes over the same bytes that could silently disagree:
`_locate_container`'s own whole-document `_skip_value` call (used only to
check nothing trails the root value) versus the real per-element
`_scan_array`/`_scan_object` call that actually emitted records -- a
crafted document (a bare numeric literal immediately followed by a `"`,
with no separator) made the two scans compute two *different* end
positions, and the code trusted the second one without ever checking they
agreed: the array "completed" having silently dropped everything after
the first scan's own wrong boundary, with no fatal error at all
(docs/certifications/queue.md 2026-10-14 "FTF-0503 RETURN -> round 3",
finding 3, reproduced in this round's own regression test).

## The streaming fix: one pass, a prunable cursor, no second scan

`_Cursor` wraps `ChunkReader`'s chunks behind an incremental UTF-8 decoder
(`codecs.getincrementaldecoder`, which already handles a multi-byte
character split across a chunk boundary correctly) and exposes `text[i]` /
`text[a:b]` exactly like a plain `str` -- `IndexError` past the end, same
as a real string -- so `_skip_value` / `_scan_array` / `_scan_object` below
are unmodified in their *interface* and still directly unit-tested against
plain `str` literals; `_generate` is the only caller that ever passes a
`_Cursor` instead. The cursor only grows (pulls another chunk) when a scan
genuinely needs a position it does not have yet, and `_generate` prunes
(discards) everything behind the end of the record it just emitted --
nothing before the *current* record's own start is ever resident, so peak
memory is proportional to one record (bounded by `max_record_bytes`), not
to the document (`test_memory_is_linear_not_quadratic`: tracemalloc peak at
64 MiB stays within the sibling-importer 1.5x contract, not round 2's
measured 3x+).

There is exactly one structural pass now: `_scan_root` walks the root
value once, and for the nested-container case (`container: "/<key>"`)
`_stream_object_for_container` walks the root object's members once,
switching into element-by-element array streaming *inline* when it reaches
the target key, then resuming member validation afterward for the schema
hash and `unmapped_top_level_key` -- there is no second, independent
re-scan of the array to disagree with the first.

## Two real scanner bugs fixed (not merely papered over)

`_skip_value`'s bracket branch used a plain depth *counter* for both `{`/`}`
and `[`/`]` ("any open bracket is +1, any close bracket is -1"), so a
mismatched pair like `[1, 2}` was accepted as one complete, well-formed
value -- round 3 replaces the counter with a stack of the actual opening
characters, so a close bracket that does not match the stack's own top
opener raises `_Unterminated`. The bare-literal branch did not stop at a
raw `"`: a bare token immediately followed by a string with no separator
(`1"]"`) was scanned straight through the quote as "more literal text",
stopping only at whichever real terminator character came next -- which,
crafted adversarially, can be a bracket that belongs to an embedded string
rather than the true structural boundary (exactly round 2's silent-drop
defect above). `"` is now a terminator for a bare literal too: no legitimate
JSON literal (a number, `true`, `false`, `null`) ever legitimately contains
or abuts a raw quote, so this changes no valid document's outcome, only an
already-invalid one's -- `test_scan_object_truncation_table` /
`test_scan_array_truncation_table` (both already-passing tables) and the new
`test_adjacent_literal_and_string_is_fatal_not_dropped` / mismatched-bracket
tests pin both fixes.

## Where the records are: the `json` MappingSpec block

(unchanged from round 2) A single JSON document cannot be split into
independent lines the way JSONL can, and unlike a CSV/XLSX sheet it has no
fixed tabular shape -- so where the list of records lives inside the
document is itself something the mapping must declare, never guessed. The
MappingSpec's own `json` block (already validated by
`import_common.mapping_from_object`: `container` a JSON Pointer,
`ignored_top_level_keys` a list of strings, both required once the block is
present) is read here for the first time by any importer:

  - `container: ""` -- **`$`**: the document's own top-level value is the
    records. A top-level *array* is a container of *N* candidate records,
    one per element, in document order. A top-level *object* is itself the
    one record (**`$record`** at the root) -- `row_index` 0.
  - `container: "/rows"` -- **`/<key>`**: the array or single-record object
    lives one level into the root object, at that exact key. Every other
    top-level key must be in `ignored_top_level_keys` or the import is
    fatal `unmapped_top_level_key`. Disclosed scope limit: `container`
    supports depth 0 or 1 only; a deeper pointer is `MappingInvalidError`
    ("bad_pointer") at mapping-validation time, before any byte is read.

Each record element, once located, is mapped by the *same* `apply_mapping` /
`normalize_mapped_record` pipeline CSV uses, giving this importer its
deterministic `record_id` (a uuid5 of `dataset_sha256:mapping_sha256:index`)
and its content-free rejections (`RowMappingError`), both inherited
directly from P1.

## Oversize elements never end the import (round 3; round 2 return finding 5)

Round 2 treated an element whose raw span exceeded `2 * max_record_bytes +
2` as fatal `field_too_large`, ending the whole import and losing every
later element -- a real defect for JSON specifically: unlike a CSV/JSONL
field (whose end is not known until it is actually scanned, so an
unbounded field is a genuine unbounded-read risk without a hard escape
hatch), this scanner already finds an element's exact span cheaply via
bracket/quote tracking *before* ever materialising its text, so there is no
cost reason to abort the whole document over one oversize element. Round 3
removes the fatal tier entirely: any element whose span exceeds
`max_record_bytes` -- checked first by the cheap character-count lower
bound (UTF-8 byte count is never less than character count, so a
character count alone already past the limit proves the real size is too,
without ever slicing or encoding a possibly huge span) and only then, for a
span close enough to the limit to need it, by the exact encoded byte count
-- is `RowRejection record_too_large`, and the import continues to the next
element. `JSON_FATAL_CODES` no longer includes `field_too_large`.

## A UTF-8 BOM is accepted and stripped (round 3; round 2 return finding 4)

Round 2 followed RFC 8259's letter ("a BOM is forbidden") and refused a
leading UTF-8 BOM as fatal `unexpected_bom`. Round 3 instead strips it, the
same discipline `jsonl_importer` already uses for the same byte sequence:
real JSON sources exported by common tools do carry one, and refusing them
outright is stricter than the sibling importers, not a security property.
UTF-16 / UTF-32 (a BOM, or a NUL byte among the first four) are still fatal
`unsupported_encoding`, detected by leading bytes before any decode is
attempted (longest signature first, since the UTF-16LE and UTF-32LE marks
share a two-byte prefix) -- exactly `jsonl_importer._first`'s own check.
`JSON_FATAL_CODES` no longer includes `unexpected_bom`.

## A caller's `expected_sha256` is checked before a byte is read (round 3;
## round 2 return finding 2, "EXPECTED-SHA-MISMATCH-COMPLETES")

A caller's `expected_sha256` (the hash every accepted record and the final
report are tagged with) is checked against the `ChunkReader`'s own declared
`expected_sha256` *before* a byte is read: whoever built the reader and
whoever calls `run_json_import`/`json_events` can in principle name
different hashes, and only the reader's own end-of-stream check would
otherwise notice -- after already tagging every accepted record with the
caller's possibly-different value. A mismatch is `failed`/`source_changed`,
the same code a reader whose bytes do not match its own declared hash ends
with, and the same fix `jsonl_importer` round 3 already applies.

## `fatal_locator` lives in the shared `ImportReport` field (round 3; round
## 2 return finding "FATAL-LOCATOR-OUTSIDE-IMPORTREPORT")

`JsonImportOutcome` no longer carries its own, second `fatal_locator`
field: `run_json_import` now passes it to `ImportAccumulator.report(...,
fatal_locator=...)`, which returns the shared `import_common.
LocatedImportReport` (inside the report's own digest) when there is one --
`JsonImportOutcome.fatal_locator` is a read-only property delegating to
`report.fatal_locator`, never a second, independently tracked copy of it,
exactly `jsonl_importer.JsonlImportOutcome`'s own shape.

A structural failure's locator is now a real byte offset (`object:<n>`,
the position the scan was at when it found the problem) rather than always
the fixed placeholder `object:0` round 2 used for every document-level
failure. Content-type, encoding and top-level-key failures (which have no
more precise single position to blame than the document itself) still use
`object:0`.

## Content type and encoding

Only a verified content type of `application/json` is imported
(`content_type_not_supported_by_importer`, checked before a byte is read
from `reader`).

Scope: pure and synchronous, no database and no network (the packet's
`database_changes`: "No independent schema change. Consume frozen
contract"). No import route exists yet to call it; `proving_symbol` stays
blank, the same disclosed precedent as every sibling importer.
"""

from __future__ import annotations

import codecs
import hashlib
import hmac
import json
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

_UTF8_BOM: Final = b"\xef\xbb\xbf"
_UTF16_BOMS: Final = (b"\xff\xfe", b"\xfe\xff")
_HEAD_BYTES: Final = 4

_WHITESPACE: Final = frozenset(" \t\r\n")
_TERMINATORS: Final = frozenset({",", "]", "}", "\t", "\r", "\n", " ", '"'})
_OPENERS: Final = frozenset({"{", "["})
_CLOSERS: Final = {"}": "{", "]": "["}


class JsonAborted(ImportAborted):
    """A fatal JSON problem: the import ends `failed` with `failure_code`,
    and `locator` names where (`object:<byte offset>` for a document-level
    structural failure the scan found a real position for, `object:0` when
    there is no more precise position to blame; `record:<n>` is never used
    here -- an oversize or malformed *element* is a `RowRejection`, never
    fatal)."""

    def __init__(self, failure_code: str, locator: str) -> None:
        super().__init__("failed", failure_code)
        self.locator = locator


class _Unterminated(Exception):  # noqa: N818 - internal control flow, turned into JsonAborted
    """The scanner could not find where a value/object/array ends before
    the end of available text (an unclosed string/bracket, a mismatched
    bracket pair, or a trailing comma)."""


@dataclass(frozen=True)
class JsonImportOutcome:
    """`fatal_locator` lives in `report.fatal_locator` (the one shared field
    every format importer feeds, FTF-0502 r3): this is a read-only
    convenience, never a second, independently tracked copy of it."""

    report: ImportReport

    @property
    def fatal_locator(self) -> str | None:
        return self.report.fatal_locator


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
    they cannot describe this importer's own scope. Raises `MappingInvalidError`."""
    block = mapping.document.get("json")
    if type(block) is not dict:
        raise MappingInvalidError("missing_key")
    tokens = parse_pointer(block["container"])
    if len(tokens) > 1:
        # Disclosed scope limit (module docstring): depth 0 or 1 only.
        raise MappingInvalidError("bad_pointer")
    for _name, slot in _mapped_slots(mapping):
        if slot.kind != "key":
            raise MappingInvalidError("bad_slot")
    return _Options(tokens, frozenset(block["ignored_top_level_keys"]))


# ---------------------------------------------------------------------------
# The streaming cursor: a `str`-like view, fed on demand, prunable.
# ---------------------------------------------------------------------------


class _Cursor:
    """A forward-only, prunable `str`-like view over `chunks`, decoded
    incrementally (UTF-8, chunk-boundary safe). `cursor[i]` / `cursor[a:b]`
    behave exactly like a plain `str`'s (an `IndexError` past the end of
    what the source has, never a sentinel), so `_skip_value` /
    `_scan_array` / `_scan_object` below work unmodified on either a plain
    `str` (the direct unit tests) or a `_Cursor` (`_generate`'s own real,
    bounded-memory path): neither one knows or cares which it was given.

    `prune(pos)` marks everything strictly before `pos` as no longer
    needed. The actual `str` slice that drops it is deferred until at
    least `_PRUNE_THRESHOLD` characters are discardable (re-slicing a
    large `str` to drop a handful of leading characters is itself an
    O(len(buffer)) copy in CPython -- doing that on every single small
    record, as a naive "prune immediately" would, turns pruning itself
    into the quadratic cost this class exists to avoid). Memory held is
    therefore bounded by one lookahead chunk plus at most
    `_PRUNE_THRESHOLD` already-consumed characters, independent of how
    much of the document has been consumed -- not by the current record's
    span alone, but still independent of total document size."""

    __slots__ = ("_chunks", "_decoder", "_buf", "_base", "_discardable", "_done")

    _PRUNE_THRESHOLD: Final = 1 << 20  # 1 MiB of staged-but-unsliced prefix

    def __init__(self, chunks: Iterator[bytes]) -> None:
        self._chunks = chunks
        self._decoder = codecs.getincrementaldecoder("utf-8")(errors="strict")
        self._buf = ""
        self._base = 0
        self._discardable = 0
        self._done = False

    def _grow(self) -> bool:
        if self._done:
            return False
        try:
            chunk = next(self._chunks)
        except StopIteration:
            tail = self._decoder.decode(b"", final=True)
            self._done = True
            if not tail:
                return False
            self._buf += tail
            return True
        decoded = self._decoder.decode(chunk)
        if not decoded:
            return self._grow() if not self._done else False
        self._buf += decoded
        return True

    def __getitem__(self, key: int | slice) -> str:
        if isinstance(key, slice):
            start = 0 if key.start is None else key.start
            stop = key.stop
            if stop is not None:
                while self._base + len(self._buf) < stop and self._grow():
                    pass
            return self._buf[start - self._base : None if stop is None else stop - self._base]
        pos = key
        while self._base + len(self._buf) <= pos:
            if not self._grow():
                raise IndexError("_Cursor index out of range")
        return self._buf[pos - self._base]

    def prune(self, pos: int) -> None:
        rel = pos - self._base
        if rel > self._discardable:
            self._discardable = rel
        if self._discardable >= self._PRUNE_THRESHOLD:
            self._buf = self._buf[self._discardable :]
            self._base += self._discardable
            self._discardable = 0


def _peek(text: object, pos: int) -> str | None:
    try:
        return text[pos]  # type: ignore[index]
    except IndexError:
        return None


def _skip_ws(text: object, pos: int) -> int:
    while True:
        c = _peek(text, pos)
        if c is None or c not in _WHITESPACE:
            return pos
        pos += 1


# ---------------------------------------------------------------------------
# The linear scanner: span-finding only, never value-building. Works on a
# plain `str` (direct unit tests below) or a `_Cursor` (the real path).
# ---------------------------------------------------------------------------


def _skip_value(text: object, pos: int) -> int:
    """`pos` indexes the first (non-whitespace) character of a value.
    Returns the index just past the value's own extent -- never parses or
    allocates the value itself. Raises `_Unterminated` when that extent
    cannot be determined (an unclosed string/bracket, a bracket that closes
    a different bracket type than the one it opened, or a bare literal
    directly abutting a stray quote)."""
    ch = text[pos]  # type: ignore[index]  # the caller always confirms a value starts here
    if ch in _OPENERS:
        stack = [ch]
        i = pos + 1
        in_string = False
        escape = False
        while stack:
            c = _peek(text, i)
            if c is None:
                raise _Unterminated
            if in_string:
                if escape:
                    escape = False
                elif c == "\\":
                    escape = True
                elif c == '"':
                    in_string = False
            elif c == '"':
                in_string = True
            elif c in _OPENERS:
                stack.append(c)
            elif c in _CLOSERS:
                if stack[-1] != _CLOSERS[c]:
                    raise _Unterminated
                stack.pop()
            i += 1
        return i
    if ch == '"':
        i = pos + 1
        escape = False
        while True:
            c = _peek(text, i)
            if c is None:
                raise _Unterminated
            if escape:
                escape = False
            elif c == "\\":
                escape = True
            elif c == '"':
                return i + 1
            i += 1
    # A bare literal (number / true / false / null, or garbage this value's
    # own eventual strict_loads call will reject): scan to the next
    # structural terminator -- including a raw `"`, since no legitimate
    # literal ever abuts a quote with no separator (round 3: round 2 let
    # a bare token swallow a following quote as "more literal text",
    # which could walk straight past the real array boundary into an
    # embedded string's own content and silently mis-close the array).
    i = pos
    while True:
        c = _peek(text, i)
        if c is None or c in _TERMINATORS:
            break
        i += 1
    if i == pos:
        raise _Unterminated
    return i


def _iter_array_spans(text: object, open_pos: int) -> Generator[tuple[int, int], None, int]:
    pos = _skip_ws(text, open_pos + 1)
    if _peek(text, pos) == "]":
        return pos + 1
    while True:
        start = pos
        if _peek(text, start) is None:
            raise _Unterminated
        end = _skip_value(text, pos)
        yield (start, end)
        pos = _skip_ws(text, end)
        c = _peek(text, pos)
        if c == ",":
            pos = _skip_ws(text, pos + 1)
            continue
        if c == "]":
            return pos + 1
        raise _Unterminated


def _scan_array(text: object, open_pos: int) -> tuple[list[tuple[int, int]], int]:
    """`open_pos` indexes the `[` character. Returns each element's own
    `(start, end)` span in order, and the index just past the matching `]`.
    Raises `_Unterminated` on an unclosed array or a trailing comma."""
    spans: list[tuple[int, int]] = []
    gen = _iter_array_spans(text, open_pos)
    try:
        while True:
            spans.append(next(gen))
    except StopIteration as stop:
        return spans, stop.value


def _iter_object_members(
    text: object, open_pos: int
) -> Generator[tuple[str, int, int], None, int]:
    pos = _skip_ws(text, open_pos + 1)
    if _peek(text, pos) == "}":
        return pos + 1
    while True:
        if _peek(text, pos) != '"':
            raise _Unterminated
        key_start = pos
        key_end = _skip_value(text, pos)
        try:
            key = json.loads(text[key_start:key_end])  # type: ignore[index]
        except json.JSONDecodeError:
            raise _Unterminated from None
        pos = _skip_ws(text, key_end)
        if _peek(text, pos) != ":":
            raise _Unterminated
        pos = _skip_ws(text, pos + 1)
        if _peek(text, pos) is None:
            raise _Unterminated
        value_start = pos
        value_end = _skip_value(text, pos)
        yield (key, value_start, value_end)
        pos = _skip_ws(text, value_end)
        c = _peek(text, pos)
        if c == ",":
            pos = _skip_ws(text, pos + 1)
            continue
        if c == "}":
            return pos + 1
        raise _Unterminated


def _scan_object(text: object, open_pos: int) -> tuple[list[tuple[str, int, int]], int]:
    """`open_pos` indexes the `{` character. Returns each member's own
    `(key, value_start, value_end)` in order, and the index just past the
    matching `}`. A member's key text is decoded (it is always a short JSON
    string, cheap to materialise) so a caller can match it by name; its
    value is only spanned, never parsed. Raises `_Unterminated` the same
    way `_scan_array` does, plus on a key that is not itself a well-formed
    string."""
    members: list[tuple[str, int, int]] = []
    gen = _iter_object_members(text, open_pos)
    try:
        while True:
            members.append(next(gen))
    except StopIteration as stop:
        return members, stop.value


# ---------------------------------------------------------------------------
# Events
# ---------------------------------------------------------------------------


class JsonEventStream(Iterator[ImportEvent]):
    """The import events of one JSON source. `source_schema_sha256` is set
    once the document's top-level keys are known (only meaningful when
    `container` names a nested key; `None` for a root container, which has
    no separate "schema" beyond the records themselves)."""

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
        self._events = self._generate()

    def __next__(self) -> ImportEvent:
        return next(self._events)

    def _schema_sha256(self, keys: list[str]) -> str:
        text = json.dumps(sorted(keys), ensure_ascii=True, separators=(",", ":"))
        return hashlib.sha256(text.encode("ascii")).hexdigest()

    def _emit_record(
        self, cursor: _Cursor, start: int, end: int, index: int
    ) -> Iterator[ImportEvent]:
        locator = f"record:{index}"
        if end - start > self._limits.max_record_bytes:
            # A character count is never less than the UTF-8 byte count it
            # decodes from, so a character count alone past the limit
            # already proves the real byte size is too -- skip slicing or
            # encoding a possibly huge span to confirm it.
            yield RowRejection(locator, "record_too_large")
            return
        raw = cursor[start:end]
        if len(raw.encode("utf-8")) > self._limits.max_record_bytes:
            yield RowRejection(locator, "record_too_large")
            return
        try:
            parsed = strict_loads(raw, max_depth=self._limits.max_depth)
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

    def _stream_array(self, cursor: _Cursor, open_pos: int) -> Generator[ImportEvent, None, int]:
        """Streams `AcceptedRecord`/`RowRejection` events for each element of
        the array opening at `open_pos`, pruning `cursor` after each one.
        Returns the index just past the matching `]`. Raises `JsonAborted`
        (never `_Unterminated`, which is this function's own internal-only
        signal) on a structural problem, located at the real byte offset
        the scan was at."""
        pos = _skip_ws(cursor, open_pos + 1)
        if _peek(cursor, pos) == "]":
            return pos + 1
        index = 0
        while True:
            start = pos
            if _peek(cursor, start) is None:
                raise JsonAborted("json_malformed", f"object:{start}")
            try:
                end = _skip_value(cursor, pos)
            except _Unterminated:
                raise JsonAborted("json_malformed", f"object:{start}") from None
            yield from self._emit_record(cursor, start, end, index)
            cursor.prune(end)
            index += 1
            pos = _skip_ws(cursor, end)
            c = _peek(cursor, pos)
            if c == ",":
                pos = _skip_ws(cursor, pos + 1)
                continue
            if c == "]":
                return pos + 1
            raise JsonAborted("json_malformed", f"object:{pos}")

    def _stream_object_for_container(
        self, cursor: _Cursor, open_pos: int, target_key: str
    ) -> Generator[ImportEvent, None, int]:
        """Streams the records of the one member of the root object named
        `target_key`, while validating every other member's key against
        `ignored_top_level_keys` and collecting the full key list for
        `source_schema_sha256` -- one forward pass, the array (when the
        target is one) streamed inline rather than re-scanned afterward.
        Returns the index just past the root object's own matching `}`."""
        allowed = self._options.ignored_top_level_keys | {target_key}
        keys: list[str] = []
        found = 0
        has_unmapped_key = False
        pos = _skip_ws(cursor, open_pos + 1)
        if _peek(cursor, pos) == "}":
            end = pos + 1
        else:
            while True:
                if _peek(cursor, pos) != '"':
                    raise JsonAborted("json_malformed", f"object:{pos}")
                key_start = pos
                try:
                    key_end = _skip_value(cursor, pos)
                except _Unterminated:
                    raise JsonAborted("json_malformed", f"object:{key_start}") from None
                try:
                    key = json.loads(cursor[key_start:key_end])
                except json.JSONDecodeError:
                    raise JsonAborted("json_malformed", f"object:{key_start}") from None
                keys.append(key)
                # A key this importer does not recognise is only reported once
                # the target key's own presence (exactly once, never zero or
                # ambiguous) is settled below -- mapping_column_missing takes
                # priority over unmapped_top_level_key, the same precedence
                # round 2's own (non-streaming) member-list check used.
                if key not in allowed:
                    has_unmapped_key = True
                pos = _skip_ws(cursor, key_end)
                if _peek(cursor, pos) != ":":
                    raise JsonAborted("json_malformed", f"object:{pos}")
                pos = _skip_ws(cursor, pos + 1)
                if _peek(cursor, pos) is None:
                    raise JsonAborted("json_malformed", f"object:{pos}")
                value_start = pos
                if key == target_key:
                    found += 1
                    if found > 1:
                        raise JsonAborted("mapping_column_missing", "object:0")
                    value_end = yield from self._stream_container_value(cursor, value_start)
                else:
                    try:
                        value_end = _skip_value(cursor, value_start)
                    except _Unterminated:
                        raise JsonAborted("json_malformed", f"object:{value_start}") from None
                cursor.prune(value_end)
                pos = _skip_ws(cursor, value_end)
                c = _peek(cursor, pos)
                if c == ",":
                    pos = _skip_ws(cursor, pos + 1)
                    continue
                if c == "}":
                    end = pos + 1
                    break
                raise JsonAborted("json_malformed", f"object:{pos}")

        if found == 0:
            raise JsonAborted("mapping_column_missing", "object:0")
        if has_unmapped_key:
            raise JsonAborted("unmapped_top_level_key", "object:0")
        self.source_schema_sha256 = self._schema_sha256(keys)
        return end

    def _stream_container_value(
        self, cursor: _Cursor, value_start: int
    ) -> Generator[ImportEvent, None, int]:
        """The container's own value: an array streamed element by element,
        or a single `$record` object. Returns the index just past it."""
        ch = _peek(cursor, value_start)
        if ch == "[":
            return (yield from self._stream_array(cursor, value_start))
        if ch == "{":
            try:
                value_end = _skip_value(cursor, value_start)
            except _Unterminated:
                raise JsonAborted("json_malformed", f"object:{value_start}") from None
            yield from self._emit_record(cursor, value_start, value_end, 0)
            return value_end
        raise JsonAborted("json_malformed", f"object:{value_start}")

    def _scan_root(self, cursor: _Cursor) -> Iterator[ImportEvent]:
        pos = _skip_ws(cursor, 0)
        if _peek(cursor, pos) is None:
            raise JsonAborted("json_malformed", f"object:{pos}")

        tokens = self._options.container_tokens
        if tokens:
            if cursor[pos] != "{":
                raise JsonAborted("json_malformed", f"object:{pos}")
            end = yield from self._stream_object_for_container(cursor, pos, tokens[0])
        else:
            ch = cursor[pos]
            if ch == "[":
                end = yield from self._stream_array(cursor, pos)
            elif ch == "{":
                try:
                    end = _skip_value(cursor, pos)
                except _Unterminated:
                    raise JsonAborted("json_malformed", f"object:{pos}") from None
                yield from self._emit_record(cursor, pos, end, 0)
            else:
                raise JsonAborted("json_malformed", f"object:{pos}")

        tail = _skip_ws(cursor, end)
        if _peek(cursor, tail) is not None:
            raise JsonAborted("json_malformed", f"object:{end}")

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

        raw_iter = iter(self._reader)
        head = bytearray()
        for chunk in raw_iter:
            head += chunk
            if len(head) >= _HEAD_BYTES:
                break
        head_bytes = bytes(head)
        if head_bytes[:2] in _UTF16_BOMS or b"\x00" in head_bytes[:_HEAD_BYTES]:
            raise JsonAborted("unsupported_encoding", "object:0")
        if head_bytes.startswith(_UTF8_BOM):
            head_bytes = head_bytes[len(_UTF8_BOM) :]

        def _rechunked() -> Iterator[bytes]:
            if head_bytes:
                yield head_bytes
            yield from raw_iter

        cursor = _Cursor(_rechunked())
        try:
            yield from self._scan_root(cursor)
        except UnicodeDecodeError:
            raise JsonAborted("invalid_encoding", "object:0") from None


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
    Raises `MappingInvalidError` before reading a byte when the mapping
    cannot describe this importer's scope; a `verified_content_type` other
    than `application/json` aborts the first `next()`, also before a byte
    is read."""
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
    """Drains a JSON source into an `ImportReport`. An abort anywhere (a
    fatal JSON problem, a source outage, a deadline, a rejection budget) is
    the report's `state`, never an exception."""
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
