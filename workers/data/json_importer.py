"""JSON importer (FTF-0503; FINE_TUNING_FACTORY_RELEASE_1.md Section 7, 8.1, 9, 67.2, 67.3).

Round 2: rebuilt on FTF-0502-P1 (`workers/data/import_common.py`), replacing the
round-1 module that called `json.loads` on the whole document and handed the
raw parsed object straight to FTF-0506. That design is this task's own
reported defect class: a 1 MiB document peaked at 63 MiB (62x, not linear); a
4301-digit int, a duplicate key, NaN/Infinity/1e999 and a lone surrogate were
all silently accepted; there was no content-type gate, no `ChunkReader`
input, no `ImportReport`/Law 17 states and no deterministic `record_id`
(FTF-0506's `normalize_conversation` mints a fresh one per call); quarantine
messages carried FTF-0506's own exception text verbatim (a META-LEAK); and a
BOM or a UTF-16/32 document was not refused as `unsupported_encoding`.

Turns an already-authorised, size/hash-bound byte stream
(`import_common.ChunkReader`) into P1 import events, the same shape CSV
(`csv_importer.run_csv_import`) and JSONL produce -- `run_json_import` is the
third sibling, not a fourth independently invented report/event pipeline.

## Where the records are: the `json` MappingSpec block

A single JSON document cannot be split into independent lines the way JSONL
can, and unlike a CSV/XLSX sheet it has no fixed tabular shape -- so where the
list of records lives inside the document is itself something the mapping
must declare, never guessed. The MappingSpec's own `json` block (already
validated by `import_common.mapping_from_object`: `container` a JSON Pointer,
`ignored_top_level_keys` a list of strings, both required once the block is
present) is read here for the first time by any importer:

  - `container: ""` -- **`$`**: the document's own top-level value is the
    records. A top-level *array* is a container of *N* candidate records, one
    per element, in document order. A top-level *object* is itself the one
    record (**`$record`** at the root) -- `row_index` 0, the direct analogue
    of round 1's "conversation" shape.
  - `container: "/rows"` -- **`/<key>`**: the array or single-record object
    lives one level into the root object, at that exact key. Every other
    top-level key must be in `ignored_top_level_keys` or the import is fatal
    `unmapped_top_level_key` -- a key the mapping did not account for is never
    silently ignored. Disclosed scope limit: `container` supports depth 0 or
    1 only (every named example is one of these two); a deeper pointer is
    `MappingInvalidError` ("bad_pointer") at mapping-validation time, before
    any byte is read. A real need for deeper nesting is a new, separately
    reviewed round, not a silent guess here.

Each record element, once located, is mapped by the *same* `apply_mapping` /
`normalize_mapped_record` pipeline CSV uses -- a JSON element is already a
`Mapping`, so a `messages`/`messages_json`/`prompt_completion` shape with
`key`-kind slots applies to it unchanged. This is what gives this round its
deterministic `record_id` (a uuid5 of `dataset_sha256:mapping_sha256:index`,
never re-derived or re-randomised) and its content-free rejections
(`RowMappingError` carries a code and a slot name, never source text) --
both inherited directly from P1, not reimplemented.

## The linear container scanner: the real fix for the reported memory defect

Section 67.3's "without loading unbounded file" cannot mean true per-byte
streaming for JSON the way JSONL's per-line read achieves it: a JSON document
is one balanced value, and nothing before its closing bracket is
syntactically verifiable, so the whole document must be held as one buffer
(bounded by `ImportLimits.max_source_bytes` via `ChunkReader`, the same
shared bound every importer uses -- this is a disclosed scope limit, not a
new one). What round 1 got wrong was not holding the buffer; it was handing
the *whole* buffer to `json.loads` in one call, which builds every element's
full nested Python object graph simultaneously before this importer (or its
caller) can even look at one record -- the real source of "not linear":
holding *N* fully-materialised records live at once costs far more than *N*
times one record's own bytes, a cost that only grows with *N*, not with the
container's own scanning.

`_skip_value` / `_scan_array` / `_scan_object` are a single linear (one pass,
no backtracking) byte-position scanner: string/escape-aware bracket depth
tracking, the same technique `import_common._scan_depth` already uses for
its own pre-`json.loads` nesting check, extended here to also record where
each top-level array element (or root-level object member) *starts* and
*ends* -- never building a Python value for anything it skips past. Once an
element's span is known, `strict_loads` parses *that one element alone* (P1's
own one real JSON reader: duplicate keys, NaN/Infinity, the 4300-digit int
cap and lone surrogates are refused there, per element, with a stable code --
exactly the round-1 defects, now caught per record instead of crashing or
silently passing for the whole document). The accepted/rejected candidate is
yielded immediately and the caller (the shared `ImportAccumulator`, same as
CSV/JSONL) consumes and discards it before the next element is even located
-- at most one parsed record (plus the one scanner-held text buffer) is ever
live at a time, independent of how many records the container holds. This is
proven directly, not asserted: `test_memory_is_linear_not_quadratic_in_record_count`
(tracemalloc) and `test_scanner_matches_json_loads_on_every_valid_document`
(a differential test: for generated valid documents, every element this
scanner extracts and `strict_loads`-parses is value-equal to the same index
of a single whole-document `json.loads`, proving the split is semantically
exact, not an approximation) in `tests/tasks/test_ftf_0503.py`.

A span the scanner cannot close before the end of text (an unterminated
string or an unbalanced bracket -- including a trailing comma, which is not
valid JSON) is fatal `json_malformed`: once one boundary is wrong, every
later comma/bracket the scanner would find is unreliable too, so this ends
the whole import rather than mis-locating later elements. A boundary that
*is* found but is semantically invalid (a bad number, a duplicate key, ...)
is caught by `strict_loads` on that one element alone and only rejects that
one record -- the scanner's own job is finding spans, never validating them.

One element whose raw span exceeds `2 * limits.max_record_bytes + 2` (the
same generous multiplier `csv_importer` uses for one field) is fatal
`field_too_large`, caught by span length before it is parsed at all; between
that bound and `limits.max_record_bytes` it is `RowRejection record_too_large`,
also unparsed. Both are Section 67.3's "canonical single-record max 2MiB",
applied to the *one* element currently being read rather than the document as
a whole -- the round-1 defect ("an over-cap document loses every element") no
longer applies: the document-level bound is `ImportLimits.max_source_bytes`
(shared, already enforced by `ChunkReader` before a byte reaches this module),
and one oversize element inside an otherwise-fine document only costs that
one element, never the rest.

## Content type and encoding (round-1 defects: "no content-type gate";
## "BOM refused, UTF-16/32 not unsupported_encoding")

Only a verified content type of `application/json` is imported
(`content_type_not_supported_by_importer`, checked before a byte is read from
`reader`, the same ordering `csv_importer` uses). RFC 8259 requires UTF-8 and
forbids a byte-order mark; a UTF-8 BOM is fatal `unexpected_bom` and a UTF-16
or UTF-32 BOM (LE or BE) is fatal `unsupported_encoding`, both detected by
their leading bytes before any decode is attempted (longest signature first,
since the UTF-16LE and UTF-32LE marks share a two-byte prefix). Bytes that
remain after that check are decoded strict UTF-8; a document that is not
valid UTF-8 at all (no recognised BOM, just bad bytes) is fatal
`invalid_encoding` -- the same code `csv_importer` uses for the identical
underlying failure on its own declared encoding.

Scope: pure and synchronous, no database and no network (the packet's
`database_changes`: "No independent schema change. Consume frozen contract").
No import route exists yet to call it; `proving_symbol` stays blank, the same
disclosed precedent as every sibling importer.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Iterator, Mapping
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
        "field_too_large",
        "invalid_encoding",
        "json_malformed",
        "mapping_column_missing",
        "unexpected_bom",
        "unmapped_top_level_key",
        "unsupported_encoding",
    }
)

IMPORTER_NAME: Final = "json"
IMPORTER_VERSION: Final = "1"
SOURCE_TYPE: Final = "upload"

# Longest signature first: the UTF-16LE and UTF-32LE marks share a prefix.
_BOM_SIGNATURES: Final = (
    (b"\xff\xfe\x00\x00", "unsupported_encoding"),  # UTF-32 LE
    (b"\x00\x00\xfe\xff", "unsupported_encoding"),  # UTF-32 BE
    (b"\xef\xbb\xbf", "unexpected_bom"),  # UTF-8 BOM (RFC 8259 forbids it)
    (b"\xff\xfe", "unsupported_encoding"),  # UTF-16 LE
    (b"\xfe\xff", "unsupported_encoding"),  # UTF-16 BE
)

_WHITESPACE: Final = " \t\r\n"
_TERMINATORS: Final = ",]}\t\r\n "


class JsonAborted(ImportAborted):
    """A fatal JSON problem: the import ends `failed` with `failure_code`,
    and `locator` names where (`object:0` for every document-level failure
    -- there is no finer position to blame once the scanner itself cannot
    trust later offsets; `record:<n>` for one oversize element, found by its
    span length alone, before it is even parsed)."""

    def __init__(self, failure_code: str, locator: str) -> None:
        super().__init__("failed", failure_code)
        self.locator = locator


class _Unterminated(Exception):  # noqa: N818 - internal control flow, turned into JsonAborted
    """The scanner could not find where a value/object/array ends before the
    end of text (an unclosed string/bracket, or a trailing comma)."""


@dataclass(frozen=True)
class JsonImportOutcome:
    report: ImportReport
    fatal_locator: str | None


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
# The linear scanner: span-finding only, never value-building.
# ---------------------------------------------------------------------------


def _skip_ws(text: str, pos: int) -> int:
    length = len(text)
    while pos < length and text[pos] in _WHITESPACE:
        pos += 1
    return pos


def _skip_value(text: str, pos: int) -> int:
    """`pos` indexes the first (non-whitespace) character of a value. Returns
    the index just past the value's own extent -- never parses or allocates
    the value itself. Raises `_Unterminated` when that extent cannot be
    determined before the end of `text`."""
    length = len(text)
    # Every structural character below is compared straight off `text[...]`
    # (never cached into a plain variable first): a single already-isolated
    # punctuation character can never actually differ from its own
    # `.strip().lower()` form, which would otherwise leave an unkillable
    # equivalent-mutant surface behind (the same reasoning round 1's own
    # `_max_nesting_depth` already documented for this exact mutation class).
    if text[pos] == "{" or text[pos] == "[":
        depth = 1
        i = pos + 1
        in_string = False
        escape = False
        while i < length:
            if in_string:
                if escape:
                    escape = False
                elif text[i] == "\\":
                    escape = True
                elif text[i] == '"':
                    in_string = False
            elif text[i] == '"':
                in_string = True
            elif text[i] == "{" or text[i] == "[":
                depth += 1
            elif text[i] == "}" or text[i] == "]":
                depth -= 1
                if depth == 0:
                    return i + 1
            i += 1
        raise _Unterminated
    if text[pos] == '"':
        i = pos + 1
        escape = False
        while i < length:
            if escape:
                escape = False
            elif text[i] == "\\":
                escape = True
            elif text[i] == '"':
                return i + 1
            i += 1
        raise _Unterminated
    # A bare literal (number / true / false / null, or garbage this value's
    # own eventual strict_loads call will reject): scan to the next
    # structural terminator. Never consuming anything is itself malformed.
    i = pos
    while i < length and text[i] not in _TERMINATORS:
        i += 1
    if i == pos:
        raise _Unterminated
    return i


def _scan_array(text: str, open_pos: int) -> tuple[list[tuple[int, int]], int]:
    """`open_pos` indexes the `[` character. Returns each element's own
    `(start, end)` span in order, and the index just past the matching `]`.
    Raises `_Unterminated` on an unclosed array or a trailing comma."""
    length = len(text)
    pos = _skip_ws(text, open_pos + 1)
    spans: list[tuple[int, int]] = []
    if pos < length and text[pos] == "]":
        return spans, pos + 1
    while True:
        if pos >= length:
            raise _Unterminated
        start = pos
        end = _skip_value(text, pos)
        spans.append((start, end))
        pos = _skip_ws(text, end)
        if pos >= length:
            raise _Unterminated
        if text[pos] == ",":
            pos = _skip_ws(text, pos + 1)
            continue
        if text[pos] == "]":
            return spans, pos + 1
        raise _Unterminated


def _scan_object(text: str, open_pos: int) -> tuple[list[tuple[str, int, int]], int]:
    """`open_pos` indexes the `{` character. Returns each member's own
    `(key, value_start, value_end)` in order, and the index just past the
    matching `}`. A member's key text is decoded (it is always a short JSON
    string, cheap to materialise) so a caller can match it by name; its value
    is only spanned, never parsed. Raises `_Unterminated` the same way
    `_scan_array` does, plus on a key that is not itself a well-formed string."""
    length = len(text)
    pos = _skip_ws(text, open_pos + 1)
    members: list[tuple[str, int, int]] = []
    if pos < length and text[pos] == "}":
        return members, pos + 1
    while True:
        if pos >= length or text[pos] != '"':
            raise _Unterminated
        key_start = pos
        key_end = _skip_value(text, pos)
        try:
            key = json.loads(text[key_start:key_end])
        except json.JSONDecodeError:
            raise _Unterminated from None
        pos = _skip_ws(text, key_end)
        if pos >= length or text[pos] != ":":
            raise _Unterminated
        pos = _skip_ws(text, pos + 1)
        if pos >= length:
            raise _Unterminated
        value_start = pos
        value_end = _skip_value(text, pos)
        members.append((key, value_start, value_end))
        pos = _skip_ws(text, value_end)
        if pos >= length:
            raise _Unterminated
        if text[pos] == ",":
            pos = _skip_ws(text, pos + 1)
            continue
        if text[pos] == "}":
            return members, pos + 1
        raise _Unterminated


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

    def _locate_container(self, text: str) -> tuple[int, int]:
        """Returns the container's own `(start, end)` span. Raises
        `JsonAborted` for every document-level failure."""
        pos = _skip_ws(text, 0)
        if pos >= len(text):
            raise JsonAborted("json_malformed", "object:0")
        tokens = self._options.container_tokens
        if not tokens:
            try:
                end = _skip_value(text, pos)
            except _Unterminated:
                raise JsonAborted("json_malformed", "object:0") from None
            if _skip_ws(text, end) != len(text):
                raise JsonAborted("json_malformed", "object:0")
            return pos, end

        if text[pos] != "{":
            raise JsonAborted("json_malformed", "object:0")
        try:
            members, end = _scan_object(text, pos)
        except _Unterminated:
            raise JsonAborted("json_malformed", "object:0") from None
        if _skip_ws(text, end) != len(text):
            raise JsonAborted("json_malformed", "object:0")

        key = tokens[0]
        matches = [(s, e) for k, s, e in members if k == key]
        self.source_schema_sha256 = self._schema_sha256([k for k, _s, _e in members])
        if len(matches) != 1:
            raise JsonAborted("mapping_column_missing", "object:0")
        allowed = self._options.ignored_top_level_keys | {key}
        for member_key, _s, _e in members:
            if member_key not in allowed:
                raise JsonAborted("unmapped_top_level_key", "object:0")
        return matches[0]

    def _emit_record(self, text: str, start: int, end: int, index: int) -> Iterator[ImportEvent]:
        locator = f"record:{index}"
        raw_bytes = len(text[start:end].encode("utf-8"))
        generous = 2 * self._limits.max_record_bytes + 2
        if raw_bytes > generous:
            raise JsonAborted("field_too_large", locator)
        if raw_bytes > self._limits.max_record_bytes:
            yield RowRejection(locator, "record_too_large")
            return
        try:
            parsed = strict_loads(text[start:end], max_depth=self._limits.max_depth)
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

    def _generate(self) -> Iterator[ImportEvent]:
        if (
            type(self._verified_content_type) is not str
            or self._verified_content_type not in JSON_CONTENT_TYPES
        ):
            raise ImportAborted("failed", "content_type_not_supported_by_importer")

        buf = bytearray()
        for chunk in self._reader:
            buf.extend(chunk)
        content = bytes(buf)

        for signature, code in _BOM_SIGNATURES:
            if content.startswith(signature):
                raise JsonAborted(code, "object:0")
        try:
            text = content.decode("utf-8", errors="strict")
        except UnicodeDecodeError:
            raise JsonAborted("invalid_encoding", "object:0") from None

        container_start, container_end = self._locate_container(text)
        if text[container_start] == "[":
            try:
                spans, _end = _scan_array(text, container_start)
            except _Unterminated:
                raise JsonAborted("json_malformed", "object:0") from None
            for index, (start, end) in enumerate(spans):
                yield from self._emit_record(text, start, end, index)
        elif text[container_start] == "{":
            yield from self._emit_record(text, container_start, container_end, 0)
        else:
            raise JsonAborted("json_malformed", "object:0")


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
    )
    return JsonImportOutcome(report, aborted.locator if isinstance(aborted, JsonAborted) else None)
