"""JSONL importer (FTF-0502, Section 7/8/67/77/81).

Section 8.1's import pipeline step 5 ("Parse in isolated worker") needs one
real, load-bearing place where the JSONL dialect (Section 67.1's own
`Upload | ... | ... JSONL/JSON/CSV/XLSX parsing` row) turns an already
type-detected raw byte stream into Section 7's own canonical conversation
records -- this module is that place for JSONL specifically. It is a pure,
synchronous, no-DB function (this task's own `database_changes` clause: "No
independent schema change. Consume frozen contract."): it never opens a
file, a socket or a database itself, and it never calls across the
`workers/data` <-> `apps/api` deployable-unit boundary (same discipline
FTF-0506/FTF-0508 already established) -- a caller (a later, separately
dependency-gated upload/import route) supplies the already-authorized byte
stream and reuses FTF-0506's already-CERTIFIED `normalize_conversation` for
every record this module reads.

This task's own acceptance clause ("Stream JSONL per record and report
exact row/schema failures with bounded memory") is met by `import_jsonl`:

  - **stream per record, bounded memory**: `import_jsonl` is a generator.
    It holds at most one raw line and one parsed/normalized record in
    memory at a time -- never the whole source, and never a growing list
    of results. A caller that writes each yielded item immediately (to a
    DB row, an import-report file, ...) uses memory independent of source
    size; if a caller instead collects every item into a list, that is the
    caller's own choice, not a bound this module imposes.
  - **report exact row/schema failures**: every yielded item is either an
    `ImportedRecord` (row index + the real `CanonicalConversationRecord`
    FTF-0506 produced) or a `QuarantinedRow` (row index + a stable `code` +
    the real underlying error text) -- never a bare boolean or a count.
    **Every input line in `lines` produces exactly one yielded item.**
    This is the module's own central invariant, not merely a convention:
    `sum(1 for _ in import_jsonl(lines, ...)) == len(list(lines))` always
    holds. A line is never silently absorbed into another line's result,
    and never dropped without a `QuarantinedRow` of its own -- the real,
    executable form of this task's own negative_acceptance ("...produce
    quarantine/errors, not silent loss").

Negative acceptance ("Malformed UTF-8/line, huge record and mixed critical
schemas produce quarantine/errors, not silent loss"), read against the real
dependency this module has (FTF-0506, not a second, independently invented
schema checker):

  - **malformed UTF-8/line**: `lines` is an iterable of raw `bytes` lines
    (never pre-decoded `str` -- a caller that already decoded has already
    destroyed the one signal this check exists to catch). Each line is
    decoded strictly (`errors="strict"`); a `UnicodeDecodeError` quarantines
    that row alone (`code="malformed_utf8"`) and the stream continues with
    the next line. A line that is not `bytes`/`bytearray` at all (a caller
    bug, or a source that mixed decoded and undecoded lines) quarantines
    the same way (`code="not_bytes_line"`) rather than raising and losing
    every row after it.
  - **huge record**: Section 67.3's own "canonical single-record max 2MiB"
    (`record_schema_validator.MAX_CANONICAL_RECORD_BYTES`, reused directly,
    not re-declared) bounds the raw line itself, checked by byte length
    before any decode or parse is attempted -- the oversize case is refused
    at the cheapest possible point, not after building a huge Python object
    first. `code="line_too_large"`.
  - **mixed critical schemas**: this task's own packet names no second,
    independently invented "schema" concept for this importer to check --
    FTF-0506's `normalize_conversation` is the one real schema check this
    repository has for a parsed record's shape, and it already refuses
    every record whose shape it cannot losslessly and unambiguously
    normalize (an unmapped role, an unsupported content shape, a record
    missing `messages` entirely, ...). A JSONL source that mixes rows of
    genuinely different shapes -- for example some rows following the
    Section 7 `messages` array and others following a `prompt`/`completion`
    pair, or rows whose tool calls reference tools no `tool_schemas`
    mapping for this import recognizes -- is exactly a "mixed ... schemas"
    source: each differently-shaped row fails `normalize_conversation` on
    its own exact reason, is quarantined individually (`code=
    "schema_invalid"`, carrying that real `ConversationNormalizationError`
    text verbatim), and every other row in the same source is still
    processed. Nothing is silently coerced into a guessed common shape, and
    nothing about one row's failure drops or re-labels another row.
  - **disclosed, not invented**: this module forwards every parsed JSON
    object straight through as FTF-0506's own `raw_record` and lets it
    auto-assign a fresh `record_id` and a solo `group_id` when the record
    carries none -- Section 9's own cross-record grouping keys (shared
    conversation/template/customer-case identity) are a later pipeline
    concern once a specific source's own field for that identity is known;
    this importer does not guess which raw field is a group key, the same
    "refuse to guess" discipline FTF-0506 itself applies to role aliases.

Scope decision, same disclosed precedent as FTF-0506/FTF-0507/FTF-0508
("no live caller yet"): this task's own packet `files` list names only this
module and its test, not a route or a DB write. No dataset-import pipeline
route exists yet in this repository to call `import_jsonl` (Section 67.1's
actual upload/import routes are separate, later, dependency-gated tasks).
`proving_symbol` stays blank; `product_truth_check.py`'s product-true count
is deliberately unchanged.

Path refinement recorded before READY (Section 84.3), same precedent as
FTF-0506/FTF-0507/FTF-0508/FTF-0517: the packet names
`services/training-worker/data/jsonl_importer.py`, but this repo's real
`SOURCE_ROOTS` (FTF-0001, PR #10) name `workers/data` as the real data/
review worker root -- there is no `services/` tree in this repository.
Placed alongside FTF-0506's/FTF-0507's/FTF-0508's/FTF-0517's own modules
instead of creating a second, non-existent tree.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass
from types import MappingProxyType

from workers.data.canonical_conversation_normalizer import (
    CanonicalConversationRecord,
    ConversationNormalizationError,
    ToolArgumentValidator,
    normalize_conversation,
)
from workers.data.record_schema_validator import MAX_CANONICAL_RECORD_BYTES, SUPPORTED_SOURCE_TYPES

__all__ = [
    "MAX_RAW_LINE_BYTES",
    "ImportedRecord",
    "JsonlImportError",
    "QuarantinedRow",
    "import_jsonl",
]

# Section 67.3: "canonical single-record max 2MiB" -- reused directly from
# FTF-0508's already-CERTIFIED constant, never re-declared independently.
MAX_RAW_LINE_BYTES = MAX_CANONICAL_RECORD_BYTES

_EMPTY_TOOL_SCHEMAS: MappingProxyType[str, Mapping[str, object]] = MappingProxyType({})


class JsonlImportError(ValueError):
    """The `import_jsonl` call itself is malformed (`lines` is not a real
    iterable of lines, or `source_type` is not a recognized dialect) -- not
    a per-row failure, so there is no row to attach quarantine evidence to.
    Never raised once streaming has started; every row-level problem is a
    `QuarantinedRow` instead."""


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


def _quarantine(row_index: int, code: str, message: str) -> QuarantinedRow:
    return QuarantinedRow(row_index=row_index, code=code, message=message)


def _stream(
    lines: Iterable[bytes],
    *,
    source_type: str,
    tool_schemas: Mapping[str, Mapping[str, object]],
    tool_argument_validator: ToolArgumentValidator | None,
    max_line_bytes: int,
) -> Iterator[ImportedRecord | QuarantinedRow]:
    for row_index, raw_line in enumerate(lines):
        if not isinstance(raw_line, bytes | bytearray):
            yield _quarantine(
                row_index,
                "not_bytes_line",
                f"line must be bytes, got {type(raw_line).__name__}",
            )
            continue

        line = bytes(raw_line).rstrip(b"\r\n")

        if len(line) > max_line_bytes:
            yield _quarantine(
                row_index,
                "line_too_large",
                f"line is {len(line)} bytes, exceeding the Section 67.3 canonical "
                f"single-record max of {max_line_bytes} bytes",
            )
            continue

        if not line or line.isspace():
            yield _quarantine(row_index, "blank_line", "line is empty or whitespace-only")
            continue

        try:
            decoded = line.decode("utf-8", errors="strict")
        except UnicodeDecodeError as exc:
            yield _quarantine(row_index, "malformed_utf8", f"line is not valid UTF-8: {exc}")
            continue

        try:
            parsed = json.loads(decoded)
        except json.JSONDecodeError as exc:
            yield _quarantine(row_index, "malformed_json", f"line is not valid JSON: {exc}")
            continue

        if not isinstance(parsed, dict):
            yield _quarantine(
                row_index,
                "not_an_object",
                f"line must parse to a JSON object, got {type(parsed).__name__}",
            )
            continue

        try:
            record = normalize_conversation(
                source_type=source_type,
                source_record_index=row_index,
                raw_record=parsed,
                tool_schemas=tool_schemas,
                tool_argument_validator=tool_argument_validator,
            )
        except ConversationNormalizationError as exc:
            yield _quarantine(row_index, "schema_invalid", str(exc))
            continue

        yield ImportedRecord(row_index=row_index, record=record)


def import_jsonl(
    lines: Iterable[bytes],
    *,
    source_type: str,
    tool_schemas: Mapping[str, Mapping[str, object]] = _EMPTY_TOOL_SCHEMAS,
    tool_argument_validator: ToolArgumentValidator | None = None,
    max_line_bytes: int = MAX_RAW_LINE_BYTES,
) -> Iterator[ImportedRecord | QuarantinedRow]:
    """Streams `lines` (an iterable of raw, not-yet-decoded `bytes` lines --
    a file opened in binary mode is the expected caller shape) into Section
    7's own canonical conversation records, one line at a time and with no
    memory bound beyond the single line/record currently being processed.

    Every line in `lines` produces exactly one yielded item -- an
    `ImportedRecord` or a `QuarantinedRow` -- in input order. Never raises
    once iteration begins; every per-row problem is reported, not raised.

    Raises `JsonlImportError` immediately, before any line is read, when
    the call itself cannot possibly succeed: `source_type` is not one of
    `record_schema_validator.SUPPORTED_SOURCE_TYPES` (Section 6.8F's own
    frozen dialect enum), or `lines` is not a real iterable of lines (a
    bare `bytes`/`bytearray`/`str` is itself iterable over individual
    bytes/characters, never over lines, so it is refused outright rather
    than silently misread one byte/character at a time)."""
    if source_type not in SUPPORTED_SOURCE_TYPES:
        raise JsonlImportError(
            f"source_type must be one of {sorted(SUPPORTED_SOURCE_TYPES)}, got {source_type!r}"
        )
    if isinstance(lines, bytes | bytearray | str):
        raise JsonlImportError(
            "lines must be an iterable of bytes lines, not a single "
            f"{type(lines).__name__} (which iterates over its own "
            "bytes/characters, never lines)"
        )
    if max_line_bytes < 1:
        raise JsonlImportError(f"max_line_bytes must be a positive int, got {max_line_bytes!r}")
    try:
        iter(lines)
    except TypeError as exc:
        raise JsonlImportError(f"lines must be iterable: {exc}") from exc

    return _stream(
        lines,
        source_type=source_type,
        tool_schemas=tool_schemas,
        tool_argument_validator=tool_argument_validator,
        max_line_bytes=max_line_bytes,
    )
