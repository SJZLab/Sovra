"""JSON importer (FTF-0503, Section 7/8/67/77/81).

Section 8.1's import pipeline step 5 ("Parse in isolated worker") needs one
real, load-bearing place where the JSON dialect (Section 67.1's own
`Upload | ... | ... JSONL/JSON/CSV/XLSX parsing` row) turns an already
type-detected raw byte document into Section 7's own canonical conversation
records -- this module is that place for JSON specifically, the sibling of
FTF-0502's `import_jsonl`. It is a pure, synchronous, no-DB function (this
task's own `database_changes` clause: "No independent schema change.
Consume frozen contract."): it never opens a file, a socket or a database
itself, and it never calls across the `workers/data` <-> `apps/api`
deployable-unit boundary (same discipline FTF-0502/FTF-0506/FTF-0508 already
established) -- a caller (a later, separately dependency-gated upload/import
route) supplies the already-authorized whole-document bytes and reuses
FTF-0506's already-CERTIFIED `normalize_conversation` for every record this
module reads. `ImportedRecord`/`QuarantinedRow` are reused directly from
FTF-0502's `jsonl_importer` (reuse-first, Section 77.1) rather than a second,
structurally identical pair of dataclasses.

A single JSON document cannot be split into independent lines the way JSONL
can: a well-formed document is exactly one balanced top-level value, and
nothing before that closing bracket/brace is syntactically verifiable. This
importer therefore accepts two disclosed top-level shapes, read literally
from this task's own acceptance clause ("Parse approved JSON
**conversation/container** structures"), rather than guessing a wrapping
field name (for example `"records"` or `"data"`) that nothing in this
repository's contracts names:

  - **conversation** -- a top-level JSON *object* is one single conversation
    record (the whole document is one row, `row_index=0`), handed to
    FTF-0506's `normalize_conversation` exactly like one JSONL line.
  - **container** -- a top-level JSON *array* holds zero or more candidate
    records, one per element, in order (`row_index` is the element's
    0-based position) -- the direct JSON analogue of one JSONL line per
    array element.

Any other top-level JSON type (a string, number, boolean or null) is not a
"conversation" or "container" structure at all; it is quarantined as a
single row 0 with an actionable message, never guessed into either shape.

This task's own acceptance clause ("... without loading unbounded file") is
met by two checks performed *before* any decode or parse is attempted, the
same "cheapest possible refusal point first" discipline FTF-0502 already
applies to its own per-line size bound:

  - **document size**: `len(content)` (a plain byte-length check on the
    already-received buffer, never a second read) is bound by
    `max_total_bytes` (default `MAX_JSON_DOCUMENT_BYTES`). A full JSON
    document -- unlike JSONL -- must be held in memory whole to be parsed at
    all (there is no stdlib incremental JSON-array parser this task's own
    `files` clause licenses adding), so "without loading unbounded file"
    is met here by refusing to even attempt `json.loads` on a document over
    this bound, not by true per-element streaming. This is a disclosed
    scope limit, not a defect: Section 67.3's own 10GiB multipart upload
    ceiling (`dataset_upload_store.MAX_UPLOAD_BYTES`) governs what may be
    *uploaded*; a single in-process `json.loads` call over a document that
    large would never be safe regardless of this importer's own bound, so
    `MAX_JSON_DOCUMENT_BYTES` is deliberately far smaller and independently
    configurable per caller/deployment capability evidence (Section 67.3:
    "Larger allowed limits require capability/load evidence, not disabling
    protection").
  - **nesting depth**: before calling `json.loads` at all, a single
    byte-level pass (`_max_nesting_depth`) counts the deepest `{`/`[`
    bracket nesting actually reached, correctly ignoring any `{`/`[`/`}`/`]`
    that appears inside a JSON string literal (escape-aware). A document
    whose nesting exceeds `max_nesting_depth` (default `MAX_NESTING_DEPTH`)
    is quarantined `nesting_too_deep` *before* the recursive-descent parser
    ever sees it -- this is this task's own "deep nesting ... fail with
    actionable mapping errors" negative-acceptance clause, made
    deterministic and independently testable (an exact depth boundary),
    rather than relying on an uncontrolled `RecursionError` whose threshold
    depends on the interpreter's own ambient recursion limit. `json.loads`
    is still wrapped in a defensive `except RecursionError` as a backstop
    (it should never fire once the pre-check above holds), never left to
    crash the caller.

Negative acceptance ("Deep nesting, malformed objects and unsupported
structures fail with actionable mapping errors"), the remaining cases:

  - **malformed objects**: invalid JSON syntax (`json.JSONDecodeError`) is
    quarantined `malformed_json` with the real decoder message, never
    fatal. A container element that is not itself a JSON object (for
    example a bare number or a nested array inside the container array) is
    quarantined `not_an_object` individually -- one bad element never
    affects any other element in the same container, the same
    "nothing silently dropped" discipline FTF-0502 established.
  - **unsupported structures**: a top-level scalar (string/number/
    boolean/null) is quarantined `unsupported_top_level_type` with the
    real observed type name, never coerced into either accepted shape.
  - **malformed UTF-8**: `content` is decoded strictly (`errors="strict"`)
    before any parse is attempted; a `UnicodeDecodeError` is quarantined
    `malformed_utf8`, the same code FTF-0502 already uses for the identical
    underlying failure.

This module's own central invariant, the JSON analogue of FTF-0502's
"every line produces exactly one item": a **container** (top-level array)
of length *N* yields exactly *N* items, one per element, in order; a
**conversation** (top-level object) or any document-level failure (size,
nesting, decode, parse, or an unsupported top-level type) yields exactly
one item, `row_index=0` -- there is no row to attach document-level
evidence to beyond the document itself.

Scope decision, same disclosed precedent as FTF-0502/FTF-0506/FTF-0507/
FTF-0508 ("no live caller yet"): this task's own packet `files` list names
only this module and its test, not a route or a DB write. No dataset-import
pipeline route exists yet in this repository to call `import_json`.
`proving_symbol` stays blank; `product_truth_check.py`'s product-true count
is deliberately unchanged.

Path refinement recorded before READY (Section 84.3), same precedent as
FTF-0502/FTF-0506/FTF-0507/FTF-0508/FTF-0517: the packet names
`services/training-worker/data/json_importer.py`, but this repo's real
`SOURCE_ROOTS` (FTF-0001, PR #10) name `workers/data` as the real data/
review worker root -- there is no `services/` tree in this repository.
Placed alongside FTF-0502's/FTF-0506's/FTF-0507's/FTF-0508's own modules
instead of creating a second, non-existent tree.
"""

from __future__ import annotations

import json
from collections.abc import Iterator, Mapping
from types import MappingProxyType

from workers.data.canonical_conversation_normalizer import (
    ConversationNormalizationError,
    ToolArgumentValidator,
    normalize_conversation,
)
from workers.data.jsonl_importer import ImportedRecord, QuarantinedRow
from workers.data.record_schema_validator import SUPPORTED_SOURCE_TYPES

__all__ = [
    "MAX_JSON_DOCUMENT_BYTES",
    "MAX_NESTING_DEPTH",
    "ImportedRecord",
    "JsonImportError",
    "QuarantinedRow",
    "import_json",
]

# Disclosed scope limit (module docstring, "document size"): deliberately far
# smaller than Section 67.3's 10GiB multipart upload ceiling -- a single
# in-process `json.loads` call over a document that large would never be
# safe. 64 MiB comfortably holds many thousands of canonical records while
# keeping a single parse call's memory bound predictable.
MAX_JSON_DOCUMENT_BYTES = 64 * 1024 * 1024

# Disclosed scope limit (module docstring, "nesting depth"): a finite,
# independently testable bound checked before `json.loads` ever runs.
MAX_NESTING_DEPTH = 64

_EMPTY_TOOL_SCHEMAS: MappingProxyType[str, Mapping[str, object]] = MappingProxyType({})


class JsonImportError(ValueError):
    """The `import_json` call itself is malformed (`content` is not real
    bytes, or `source_type` is not a recognized dialect) -- not a
    document-level or row-level failure, so there is no row to attach
    quarantine evidence to. Never raised once parsing has started; every
    document-level or row-level problem is a `QuarantinedRow` instead."""


def _max_nesting_depth(text: str) -> int:
    """Counts the deepest `{`/`[` nesting actually reached in `text`,
    ignoring any bracket characters that appear inside a JSON string
    literal. Escape-aware: a `\\"` inside a string never ends it early, and
    the character immediately after any `\\` is always treated as part of
    the string, never as a structural character."""
    depth = 0
    max_depth = 0
    in_string = False
    escape_next = False
    for ch in text:
        if in_string:
            if escape_next:
                escape_next = False
            elif ch == "\\":
                escape_next = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == "{" or ch == "[":
            depth += 1
            if depth > max_depth:
                max_depth = depth
        elif ch == "}" or ch == "]":
            depth -= 1
    return max_depth


def _quarantine(row_index: int, code: str, message: str) -> QuarantinedRow:
    return QuarantinedRow(row_index=row_index, code=code, message=message)


def _normalize_one(
    row_index: int,
    raw_record: object,
    *,
    source_type: str,
    tool_schemas: Mapping[str, Mapping[str, object]],
    tool_argument_validator: ToolArgumentValidator | None,
) -> ImportedRecord | QuarantinedRow:
    if not isinstance(raw_record, dict):
        return _quarantine(
            row_index,
            "not_an_object",
            f"element must be a JSON object, got {type(raw_record).__name__}",
        )
    try:
        record = normalize_conversation(
            source_type=source_type,
            source_record_index=row_index,
            raw_record=raw_record,
            tool_schemas=tool_schemas,
            tool_argument_validator=tool_argument_validator,
        )
    except ConversationNormalizationError as exc:
        return _quarantine(row_index, "schema_invalid", str(exc))
    return ImportedRecord(row_index=row_index, record=record)


def _stream(
    content: bytes,
    *,
    source_type: str,
    tool_schemas: Mapping[str, Mapping[str, object]],
    tool_argument_validator: ToolArgumentValidator | None,
    max_total_bytes: int,
    max_nesting_depth: int,
) -> Iterator[ImportedRecord | QuarantinedRow]:
    if len(content) > max_total_bytes:
        yield _quarantine(
            0,
            "document_too_large",
            f"document is {len(content)} bytes, exceeding the configured "
            f"max of {max_total_bytes} bytes",
        )
        return

    try:
        decoded = content.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        yield _quarantine(0, "malformed_utf8", f"document is not valid UTF-8: {exc}")
        return

    depth = _max_nesting_depth(decoded)
    if depth > max_nesting_depth:
        yield _quarantine(
            0,
            "nesting_too_deep",
            f"document nesting reaches depth {depth}, exceeding the configured "
            f"max of {max_nesting_depth}",
        )
        return

    try:
        parsed = json.loads(decoded)
    except json.JSONDecodeError as exc:
        yield _quarantine(0, "malformed_json", f"document is not valid JSON: {exc}")
        return
    except RecursionError:
        # Defensive backstop only -- the depth pre-check above should always
        # catch this first. Never let a malicious document crash the caller.
        yield _quarantine(
            0,
            "nesting_too_deep",
            "document nesting exceeded the parser's own recursion limit",
        )
        return

    if isinstance(parsed, list):
        for row_index, element in enumerate(parsed):
            yield _normalize_one(
                row_index,
                element,
                source_type=source_type,
                tool_schemas=tool_schemas,
                tool_argument_validator=tool_argument_validator,
            )
        return

    if isinstance(parsed, dict):
        yield _normalize_one(
            0,
            parsed,
            source_type=source_type,
            tool_schemas=tool_schemas,
            tool_argument_validator=tool_argument_validator,
        )
        return

    yield _quarantine(
        0,
        "unsupported_top_level_type",
        "top-level JSON value must be an object (single conversation) or an "
        f"array (container of conversations), got {type(parsed).__name__}",
    )


def import_json(
    content: bytes,
    *,
    source_type: str,
    tool_schemas: Mapping[str, Mapping[str, object]] = _EMPTY_TOOL_SCHEMAS,
    tool_argument_validator: ToolArgumentValidator | None = None,
    max_total_bytes: int = MAX_JSON_DOCUMENT_BYTES,
    max_nesting_depth: int = MAX_NESTING_DEPTH,
) -> Iterator[ImportedRecord | QuarantinedRow]:
    """Parses `content` (the whole, not-yet-decoded raw JSON document as
    `bytes` -- never a pre-decoded `str`, which would already have defeated
    the UTF-8 check below) into Section 7's own canonical conversation
    records.

    A top-level JSON object is one single conversation (`row_index=0`); a
    top-level JSON array is a container of zero or more conversations, one
    per element in order. Any other top-level type, or a document-level
    failure (oversize, malformed UTF-8, too deeply nested, or invalid JSON
    syntax), yields exactly one `QuarantinedRow` at `row_index=0`. A
    container array of length *N* yields exactly *N* items. Never raises
    once parsing has started; every document-level or row-level problem is
    reported, not raised.

    Raises `JsonImportError` immediately, before any byte is read, when the
    call itself cannot possibly succeed: `source_type` is not one of
    `record_schema_validator.SUPPORTED_SOURCE_TYPES`, `content` is not
    `bytes`/`bytearray`, or `max_total_bytes`/`max_nesting_depth` is not a
    positive int."""
    if source_type not in SUPPORTED_SOURCE_TYPES:
        raise JsonImportError(
            f"source_type must be one of {sorted(SUPPORTED_SOURCE_TYPES)}, got {source_type!r}"
        )
    if not isinstance(content, bytes | bytearray):
        raise JsonImportError(
            f"content must be bytes/bytearray, not a pre-decoded {type(content).__name__} "
            "(which would already have defeated the UTF-8 check)"
        )
    if max_total_bytes < 1:
        raise JsonImportError(f"max_total_bytes must be a positive int, got {max_total_bytes!r}")
    if max_nesting_depth < 1:
        raise JsonImportError(
            f"max_nesting_depth must be a positive int, got {max_nesting_depth!r}"
        )

    return _stream(
        bytes(content),
        source_type=source_type,
        tool_schemas=tool_schemas,
        tool_argument_validator=tool_argument_validator,
        max_total_bytes=max_total_bytes,
        max_nesting_depth=max_nesting_depth,
    )
