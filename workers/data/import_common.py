"""Dataset import common contract (FTF-0502-P1; FINE_TUNING_FACTORY_RELEASE_1.md
Section 7, Section 8.1 steps 1-7, Section 9, Section 67.2, Section 67.3).

The one place the four format importers (JSONL, JSON, CSV, XLSX) share, so a
rule is written once and a fault in it is caught once. Pure and stdlib-only
apart from FTF-0506 (`normalize_conversation`) and FTF-0508
(`MAX_CANONICAL_RECORD_BYTES`), which are reused, never re-declared. Nothing
here touches a network, a database or the filesystem.

Rules this module enforces:

- Limits are lower-only. `ImportLimits` accepts any value up to its default and
  refuses a higher one: Section 67.3 says larger limits need capability / load
  evidence, which a caller cannot supply by passing a bigger number.
- `strict_loads` is the one JSON reader: duplicate keys, NaN / Infinity / values
  that overflow to infinity, integers over CPython's digit limit, lone
  surrogates and nesting over `max_depth` are refused with a stable code. The
  depth check is a linear scan outside strings that runs *before* `json.loads`,
  so a 100,000-deep document never reaches the recursive parser. (The same rules
  as FTF-0607 `parse_strict_json`, which is not imported: it lives in a separate
  deployable, `workers/evaluation`.)
- `parse_mapping` / `mapping_from_object` are the single MappingSpec parser
  (FTF-H011 must reuse it): exact types, an exact allow-list per shape, no
  defaults for anything the owner has to decide (`on_unmapped_message_keys`,
  `quality_label`), and a `mapping_sha256` over canonical JSON.
- A role is mapped only by an exact `role_map` lookup. No casefold, strip or
  Unicode normalisation is applied to either side.
- Nothing is read from the data to decide a record's dataset `purpose` or
  `split`; both belong to the Dataset row.
- A rejection names a *where* (a locator and a slot from this module's own
  grammar) and a *why* (a code from a closed catalog). It never carries source
  text, a key from the data, or an FTF-0506 message.
- `ChunkReader` turns every failure of the source into `blocked` /
  `source_unavailable` (Law 17: an outage is never `completed` or `failed`), a
  size or hash difference into `failed` / `source_changed`, and checks the
  deadline between chunks.
- `ImportReport.records_seen == records_accepted + records_rejected`, and
  `report_sha256` covers every other field.
"""

from __future__ import annotations

import copy
import hashlib
import hmac
import json
import re
import time
import uuid
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from datetime import datetime
from types import MappingProxyType
from typing import Final

from workers.data.canonical_conversation_normalizer import (
    ALLOWED_ROLES,
    SCHEMA_VERSION,
    CanonicalConversationRecord,
    ConversationNormalizationError,
    ToolArgumentValidator,
    normalize_conversation,
)
from workers.data.record_schema_validator import MAX_CANONICAL_RECORD_BYTES

__all__ = [
    "DEFAULT_LIMITS",
    "FAILURE_CODES",
    "MAPPING_INVALID_REASONS",
    "REJECTION_CODES",
    "AcceptedRecord",
    "BlankLine",
    "ChunkReader",
    "ImportAborted",
    "ImportAccumulator",
    "ImportLimits",
    "ImportMapping",
    "ImportReport",
    "MappedRow",
    "MappingInvalidError",
    "MappingUnsupportedError",
    "PreviewResult",
    "RowMappingError",
    "RowRejection",
    "Slot",
    "StrictJsonError",
    "apply_mapping",
    "mapping_from_object",
    "normalize_mapped_record",
    "parse_mapping",
    "parse_pointer",
    "preview",
    "resolve_pointer",
    "run_import",
    "strict_loads",
    "to_dataset_record_candidate",
]

IMPORTER_RECORD_ID_NAMESPACE: Final = uuid.UUID("5f0d6b1e-7a64-5b0a-9f6e-0c4f0f6a2b11")

MAX_JSON_INT_DIGITS: Final = 4300
MAX_MAPPING_BYTES: Final = 64 * 1024
MAX_MAPPING_DEPTH: Final = 8
MAX_ROLE_MAP_ENTRIES: Final = 64
MAX_NAME_LENGTH: Final = 256
MAX_POINTER_TOKENS: Final = 16
MAX_POINTER_INDEX_DIGITS: Final = 9

# ---------------------------------------------------------------------------
# Closed catalogs
# ---------------------------------------------------------------------------

STRICT_JSON_CODES: Final = frozenset(
    {
        "invalid_json",
        "duplicate_key",
        "non_finite_number",
        "number_out_of_range",
        "invalid_unicode",
        "nesting_too_deep",
    }
)

REJECTION_CODES: Final = frozenset(
    {
        "canonical_schema_invalid",
        "column_count_mismatch",
        "invalid_event_timestamp",
        "mapping_type_mismatch",
        "nul_character",
        "record_too_large",
        "required_field_empty",
        "required_field_missing",
        "unmapped_message_key",
        "unmapped_role",
        "invalid_utf8",
        "record_not_object",
        "formula_cell",
        "cell_error_value",
    }
    | STRICT_JSON_CODES
)

FAILURE_CODES: Final = frozenset(
    {
        "content_type_not_supported_by_importer",
        "csv_structure_invalid",
        "duplicate_header",
        "duplicate_sheet_name",
        "external_link_present",
        "field_too_large",
        "invalid_encoding",
        "json_malformed",
        "limit_exceeded",
        "mapping_column_missing",
        "parse_timeout",
        "sheet_out_of_bounds",
        "source_changed",
        "source_too_large",
        "source_unavailable",
        "stream_not_consumed",
        "too_many_cells",
        "too_many_columns",
        "too_many_rejections",
        "unexpected_bom",
        "unmapped_top_level_key",
        "unsafe_archive",
        "unsafe_workbook",
        "unsupported_encoding",
        "unsupported_workbook",
        "xlsx_malformed",
    }
)

MAPPING_INVALID_REASONS: Final = frozenset(
    {
        "not_a_string",
        "too_large",
        "not_json",
        "not_an_object",
        "missing_key",
        "unknown_key",
        "wrong_type",
        "bad_value",
        "bad_pointer",
        "bad_role_map",
        "bad_slot",
        "bad_shape",
    }
)

# Recognised but deliberately not implemented: reported as `mapping_unsupported`
# so a caller cannot mistake "we cannot do this" for "you wrote this wrong".
UNSUPPORTED_SHAPES: Final = frozenset({"grouped_rows", "grouped_multi_row"})

_METADATA_KEYS: Final = (
    "language",
    "task_category",
    "consent",
    "quality_label",
    "source_record_id",
    "event_timestamp",
)
_LOCATOR_PATTERN: Final = re.compile(r"(line|row|record|object):[0-9]{1,18}")
_SLOT_NAME_PATTERN: Final = re.compile(r"[a-z_]{1,32}(\.[a-z_]{1,32})?(\[[0-9]{1,9}\])?")
_MESSAGE_PREFIX: Final = re.compile(r"messages\[([0-9]{1,9})\]")
_SHA256_PATTERN: Final = re.compile(r"[0-9a-f]{64}")
_PURPOSE_PATTERN: Final = re.compile(r"[a-z][a-z_]{0,31}")
_NAME_PATTERN: Final = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.\-]{0,63}")
_COLUMN_LETTERS: Final = re.compile(r"[A-Z]{1,3}")

# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class StrictJsonError(ValueError):
    """`strict_loads` refused its input. Carries a code from `STRICT_JSON_CODES`
    and nothing from the input."""

    def __init__(self, code: str) -> None:
        if code not in STRICT_JSON_CODES:
            raise ValueError("unknown strict-JSON code")
        super().__init__(code)
        self.code = code


class MappingInvalidError(ValueError):
    """The MappingSpec is not acceptable. `code` is always `mapping_invalid`;
    `reason` is from `MAPPING_INVALID_REASONS` and names no value."""

    code = "mapping_invalid"

    def __init__(self, reason: str) -> None:
        if reason not in MAPPING_INVALID_REASONS:
            raise ValueError("unknown mapping-invalid reason")
        super().__init__(f"mapping_invalid: {reason}")
        self.reason = reason


class MappingUnsupportedError(ValueError):
    """The MappingSpec asks for something this release does not do."""

    code = "mapping_unsupported"

    def __init__(self) -> None:
        super().__init__("mapping_unsupported")


class RowMappingError(ValueError):
    """One source row could not be mapped. `code` is from `REJECTION_CODES`;
    `slot` is from this module's own slot grammar, never from the data."""

    def __init__(self, code: str, slot: str | None = None) -> None:
        if code not in REJECTION_CODES:
            raise ValueError("unknown rejection code")
        if slot is not None and not _SLOT_NAME_PATTERN.fullmatch(slot):
            raise ValueError("slot is not from the slot grammar")
        super().__init__(code if slot is None else f"{code}@{slot}")
        self.code = code
        self.slot = slot


class ImportAborted(Exception):  # noqa: N818 - a verdict about the whole import, not an error class
    """The import cannot complete. `state` is `failed` or `blocked`."""

    def __init__(self, state: str, failure_code: str) -> None:
        if state not in ("failed", "blocked") or failure_code not in FAILURE_CODES:
            raise ValueError("unknown abort verdict")
        super().__init__(f"{state}:{failure_code}")
        self.state = state
        self.failure_code = failure_code


# ---------------------------------------------------------------------------
# Limits
# ---------------------------------------------------------------------------


def _is_int(value: object) -> bool:
    return type(value) is int


_MAX_DEADLINE_SECONDS: Final = 3600.0


@dataclass(frozen=True)
class ImportLimits:
    """Section 67.3 limits. A caller may lower any of them, never raise one."""

    max_source_bytes: int = 10 * 1024**3
    max_record_bytes: int = MAX_CANONICAL_RECORD_BYTES
    max_depth: int = 64
    max_rejections: int = 10_000
    preview_max_records: int = 20
    deadline_seconds: float = _MAX_DEADLINE_SECONDS
    # FTF-0505's own bounds (the xlsx importer's cell count and shared-strings
    # byte cap): additive fields under the catalog rule -- a limit a single
    # importer needs becomes one field here, never a module-local dataclass,
    # so a future importer's own limit is added the same way instead of
    # re-inventing a second limits object.
    max_xlsx_cells: int = 50_000_000
    max_xlsx_shared_strings_bytes: int = 256 * 1024**2

    def __post_init__(self) -> None:
        defaults = ImportLimits.__dataclass_fields__
        for name in (
            "max_source_bytes",
            "max_record_bytes",
            "max_depth",
            "max_rejections",
            "preview_max_records",
            "max_xlsx_cells",
            "max_xlsx_shared_strings_bytes",
        ):
            value = getattr(self, name)
            if not _is_int(value) or value < 1:
                raise ValueError(f"{name} must be a positive int")
            if value > defaults[name].default:
                raise ValueError(f"{name} cannot exceed its default without load evidence")
        seconds = self.deadline_seconds
        if type(seconds) not in (int, float) or not 0 < seconds <= _MAX_DEADLINE_SECONDS:
            raise ValueError("deadline_seconds must be positive and at most its default")


DEFAULT_LIMITS: Final = ImportLimits()


# ---------------------------------------------------------------------------
# Strict JSON
# ---------------------------------------------------------------------------


def _scan_depth(text: str, max_depth: int) -> None:
    """Linear, non-recursive: the deepest container nesting outside strings."""
    depth = 0
    in_string = False
    escaped = False
    for ch in text:
        if in_string:
            if escaped:
                escaped = False
                continue
            match ch:
                case "\\":
                    escaped = True
                case '"':
                    in_string = False
            continue
        match ch:
            case '"':
                in_string = True
            case "[" | "{":
                depth += 1
                if depth > max_depth:
                    raise StrictJsonError("nesting_too_deep")
            case "]" | "}":
                depth -= 1


def _reject_duplicates(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise StrictJsonError("duplicate_key")
        result[key] = value
    return result


def _reject_constant(_name: str) -> object:
    raise StrictJsonError("non_finite_number")


def _parse_float(text: str) -> float:
    value = float(text)
    if value != value or value in (float("inf"), float("-inf")):
        raise StrictJsonError("non_finite_number")
    return value


def _parse_int(text: str) -> int:
    digits = text[1:] if text.startswith("-") else text
    if len(digits) > MAX_JSON_INT_DIGITS:
        raise StrictJsonError("number_out_of_range")
    try:
        return int(text)
    except ValueError:
        raise StrictJsonError("number_out_of_range") from None


def _has_lone_surrogate(text: str) -> bool:
    return any(0xD800 <= ord(ch) <= 0xDFFF for ch in text)


def _check_surrogates(root: object) -> None:
    stack: list[object] = [root]
    while stack:
        node = stack.pop()
        if isinstance(node, str):
            if _has_lone_surrogate(node):
                raise StrictJsonError("invalid_unicode")
        elif isinstance(node, dict):
            for key, value in node.items():
                if _has_lone_surrogate(key):
                    raise StrictJsonError("invalid_unicode")
                stack.append(value)
        elif isinstance(node, list):
            stack.extend(node)


def strict_loads(data: bytes | str, *, max_depth: int = 64) -> object:
    """The one JSON reader for imports. Raises `StrictJsonError` with a stable,
    content-free code."""
    if not _is_int(max_depth) or max_depth < 1:
        raise ValueError("max_depth must be a positive int")
    if isinstance(data, bytes):
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            raise StrictJsonError("invalid_unicode") from None
    elif isinstance(data, str):
        text = data
    else:
        raise TypeError("data must be bytes or str")
    _scan_depth(text, max_depth)
    try:
        value = json.loads(
            text,
            object_pairs_hook=_reject_duplicates,
            parse_constant=_reject_constant,
            parse_float=_parse_float,
            parse_int=_parse_int,
        )
    except StrictJsonError:
        raise
    except (ValueError, RecursionError):
        raise StrictJsonError("invalid_json") from None
    _check_surrogates(value)
    return value


# ---------------------------------------------------------------------------
# MappingSpec
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Slot:
    """Where a value lives in a source row: `key` (an exact object key),
    `header` (exact header text of a tabular source), `index` (0-based column)
    or `letters` (spreadsheet column letters, `A`..`ZZZ`)."""

    kind: str
    value: str | int


@dataclass(frozen=True)
class _ShapeKeys:
    required: tuple[str, ...]
    optional: tuple[str, ...]
    message_based: bool
    pointer_based: bool


_COMMON_KEYS: Final = ("group_key", "metadata", "csv", "xlsx", "json")
_MESSAGE_KEYS: Final = ("role_key", "content_key", "role_map", "on_unmapped_message_keys")
_MESSAGE_OPTIONAL: Final = ("tool_calls_key", "tool_call_id_key", "name_key")
_SHAPE_KEYS: Final = {
    "messages": _ShapeKeys(
        ("shape", "messages_pointer", *_MESSAGE_KEYS),
        (*_COMMON_KEYS, *_MESSAGE_OPTIONAL),
        True,
        True,
    ),
    "messages_json": _ShapeKeys(
        ("shape", "messages", *_MESSAGE_KEYS),
        (*_COMMON_KEYS, *_MESSAGE_OPTIONAL),
        True,
        False,
    ),
    "prompt_completion": _ShapeKeys(
        ("shape", "prompt", "completion"), (*_COMMON_KEYS, "system"), False, False
    ),
}


@dataclass(frozen=True)
class MessageKeys:
    role_key: str
    content_key: str
    tool_calls_key: str | None
    tool_call_id_key: str | None
    name_key: str | None


@dataclass(frozen=True)
class ImportMapping:
    shape: str
    mapping_sha256: str
    document: Mapping[str, object] = field(repr=False)
    messages_pointer: tuple[str, ...] | None = None
    messages_slot: Slot | None = None
    message_keys: MessageKeys | None = None
    role_map: Mapping[str, str] = field(default_factory=lambda: MappingProxyType({}))
    on_unmapped_message_keys: str | None = None
    prompt: Slot | None = None
    completion: Slot | None = None
    system: Slot | None = None
    group_key: Slot | None = None
    metadata_slots: tuple[tuple[str, Slot], ...] = ()
    # FTF-H011-P1's own additive field (IMPORTER ORDER catalog rule): a caller-supplied tap
    # called first by `apply_mapping`, before any validation, with a *copy* of the row and
    # headers so a mapping preview can show the exact raw row a rejection or candidate came
    # from. Never set by `mapping_from_object`/`parse_mapping` (so no parsed MappingSpec ever
    # carries one) and never read by `document`/`mapping_sha256` (`compare=False`,
    # `repr=False`: two mappings that differ only by observer are the same mapping). A tap
    # that mutates its own copy can never reach the importer's own row/headers, which stay
    # the original objects.
    row_observer: Callable[[Row, Sequence[str] | None], None] | None = field(
        default=None, compare=False, repr=False
    )


def _bad(reason: str) -> MappingInvalidError:
    return MappingInvalidError(reason)


def _obj(value: object, required: Iterable[str], optional: Iterable[str]) -> dict[str, object]:
    if type(value) is not dict:
        raise _bad("not_an_object")
    required_set, optional_set = frozenset(required), frozenset(optional)
    for key in value:
        if key not in required_set and key not in optional_set:
            raise _bad("unknown_key")
    if required_set - value.keys():
        raise _bad("missing_key")
    return value


def _name(value: object) -> str:
    if type(value) is not str:
        raise _bad("wrong_type")
    if not value or len(value) > MAX_NAME_LENGTH or _has_lone_surrogate(value):
        raise _bad("bad_value")
    return value


def _bool(value: object) -> bool:
    if type(value) is not bool:
        raise _bad("wrong_type")
    return value


def _count(value: object, maximum: int) -> int:
    if type(value) is not int:
        raise _bad("wrong_type")
    if not 0 <= value <= maximum:
        raise _bad("bad_value")
    return value


def _slot(value: object) -> Slot:
    if type(value) is not dict or len(value) != 1:
        raise _bad("bad_slot" if type(value) is dict else "wrong_type")
    ((kind, raw),) = value.items()
    if kind in ("key", "header"):
        return Slot(kind, _name(raw))
    if kind == "index":
        return Slot(kind, _count(raw, 16_383))
    if kind == "letters":
        letters = _name(raw)
        if not _COLUMN_LETTERS.fullmatch(letters):
            raise _bad("bad_slot")
        return Slot(kind, letters)
    raise _bad("bad_slot")


def parse_pointer(value: object) -> tuple[str, ...]:
    """Parses a JSON-Pointer-shaped MappingSpec string (`""` or `/a/b/0`) into
    its unescaped tokens. Raises `MappingInvalidError` (`bad_pointer`,
    `wrong_type`). Exported so a format importer that reads its own pointer
    field straight from `mapping.document` (the way `csv_importer` reads its
    own `csv` block) can resolve it without a second, independently
    reimplemented parser (FTF-0503's `json` block `container` field)."""
    if type(value) is not str:
        raise _bad("wrong_type")
    if value == "":
        return ()
    if not value.startswith("/") or len(value) > MAX_NAME_LENGTH or _has_lone_surrogate(value):
        raise _bad("bad_pointer")
    tokens: list[str] = []
    for raw in value[1:].split("/"):
        if raw == "" or raw == "-":
            raise _bad("bad_pointer")
        if re.search(r"~(?![01])", raw) or raw.endswith("~"):
            raise _bad("bad_pointer")
        token = raw.replace("~1", "/").replace("~0", "~")
        if (
            token.isascii()
            and token.isdigit()
            and ((len(token) > 1 and token[0] == "0") or len(token) > MAX_POINTER_INDEX_DIGITS)
        ):
            raise _bad("bad_pointer")
        tokens.append(token)
    if len(tokens) > MAX_POINTER_TOKENS:
        raise _bad("bad_pointer")
    return tuple(tokens)


def resolve_pointer(document: object, tokens: Sequence[str]) -> tuple[bool, object]:
    """Walks `tokens` (already unescaped) with exact key equality. Returns
    `(found, value)`; a numeric token indexes a list only."""
    node = document
    for token in tokens:
        if type(node) is dict:
            if token not in node:
                return False, None
            node = node[token]
        elif type(node) is list and token.isascii() and token.isdigit():
            index = int(token)
            if index >= len(node):
                return False, None
            node = node[index]
        else:
            return False, None
    return True, node


def _message_keys(obj: dict[str, object]) -> MessageKeys:
    def optional(key: str) -> str | None:
        value = obj.get(key, _MISSING)
        return None if value is _MISSING else _name(value)

    return MessageKeys(
        role_key=_name(obj["role_key"]),
        content_key=_name(obj["content_key"]),
        tool_calls_key=optional("tool_calls_key"),
        tool_call_id_key=optional("tool_call_id_key"),
        name_key=optional("name_key"),
    )


def _role_map(value: object) -> Mapping[str, str]:
    if type(value) is not dict or not value or len(value) > MAX_ROLE_MAP_ENTRIES:
        raise _bad("bad_role_map")
    for raw, role in value.items():
        if type(raw) is not str or not raw or len(raw) > MAX_NAME_LENGTH:
            raise _bad("bad_role_map")
        if _has_lone_surrogate(raw) or type(role) is not str or role not in ALLOWED_ROLES:
            raise _bad("bad_role_map")
    return MappingProxyType(dict(value))


def _validate_formats(obj: dict[str, object]) -> None:
    if "csv" in obj:
        csv_block = _obj(obj["csv"], ("encoding", "delimiter", "has_header", "header_row"), ())
        _name(csv_block["encoding"])
        if len(_name(csv_block["delimiter"])) != 1:
            raise _bad("bad_value")
        _bool(csv_block["has_header"])
        _count(csv_block["header_row"], 1_000_000)
    if "xlsx" in obj:
        xlsx_block = _obj(obj["xlsx"], ("sheet_name", "header_row"), ())
        _name(xlsx_block["sheet_name"])
        _count(xlsx_block["header_row"], 1_000_000)
    if "json" in obj:
        json_block = _obj(obj["json"], ("container", "ignored_top_level_keys"), ())
        parse_pointer(json_block["container"])
        ignored = json_block["ignored_top_level_keys"]
        if type(ignored) is not list or len(ignored) > MAX_ROLE_MAP_ENTRIES:
            raise _bad("wrong_type")
        for entry in ignored:
            _name(entry)


def _canonical_sha256(document: object) -> str:
    text = json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(text.encode("ascii")).hexdigest()


def mapping_from_object(obj: object) -> ImportMapping:
    """Validates an already-parsed MappingSpec. Raises `MappingInvalidError`
    (`mapping_invalid`) or `MappingUnsupportedError` (`mapping_unsupported`)."""
    if type(obj) is not dict:
        raise _bad("not_an_object")
    shape = obj.get("shape")
    if type(shape) is not str:
        raise _bad("wrong_type")
    if shape in UNSUPPORTED_SHAPES:
        raise MappingUnsupportedError
    spec = _SHAPE_KEYS.get(shape)
    if spec is None:
        raise _bad("bad_shape")
    body = _obj(obj, spec.required, spec.optional)

    group_key = _slot(body["group_key"]) if "group_key" in body else None
    metadata_slots: list[tuple[str, Slot]] = []
    if "metadata" in body:
        metadata = _obj(body["metadata"], (), _METADATA_KEYS)
        metadata_slots = [(key, _slot(raw)) for key, raw in sorted(metadata.items())]
    _validate_formats(body)

    pointer: tuple[str, ...] | None = None
    messages_slot: Slot | None = None
    keys: MessageKeys | None = None
    prompt: Slot | None = None
    completion: Slot | None = None
    system: Slot | None = None
    on_unmapped: str | None = None
    role_map: Mapping[str, str] = MappingProxyType({})
    if spec.message_based:
        keys = _message_keys(body)
        role_map = _role_map(body["role_map"])
        policy = body["on_unmapped_message_keys"]
        if type(policy) is not str or policy not in ("reject", "ignore"):
            raise _bad("bad_value")
        on_unmapped = policy
        if spec.pointer_based:
            pointer = parse_pointer(body["messages_pointer"])
        else:
            messages_slot = _slot(body["messages"])
    else:
        prompt, completion = _slot(body["prompt"]), _slot(body["completion"])
        system = _slot(body["system"]) if "system" in body else None

    return ImportMapping(
        shape=shape,
        mapping_sha256=_canonical_sha256(body),
        document=MappingProxyType(dict(body)),
        messages_pointer=pointer,
        messages_slot=messages_slot,
        message_keys=keys,
        role_map=role_map,
        on_unmapped_message_keys=on_unmapped,
        prompt=prompt,
        completion=completion,
        system=system,
        group_key=group_key,
        metadata_slots=tuple(metadata_slots),
    )


def parse_mapping(document: str) -> ImportMapping:
    """Parses MappingSpec JSON text. Duplicate keys, NaN and out-of-range
    numbers are refused; the only error types are `MappingInvalidError` and
    `MappingUnsupportedError`."""
    if type(document) is not str:
        raise _bad("not_a_string")
    if len(document.encode("utf-8", "surrogatepass")) > MAX_MAPPING_BYTES:
        raise _bad("too_large")
    try:
        obj = strict_loads(document, max_depth=MAX_MAPPING_DEPTH)
    except StrictJsonError:
        raise _bad("not_json") from None
    return mapping_from_object(obj)


# ---------------------------------------------------------------------------
# Applying a mapping to one row
# ---------------------------------------------------------------------------

Row = Mapping[str, object] | Sequence[object]
_MISSING: Final = object()


def _column_index(letters: str) -> int:
    index = 0
    for ch in letters:
        index = index * 26 + (ord(ch) - ord("A") + 1)
    return index - 1


def _read_slot(slot: Slot, row: Row, headers: Sequence[str] | None) -> object:
    if isinstance(row, Mapping):
        if slot.kind in ("key", "header"):
            return row.get(slot.value, _MISSING)  # type: ignore[arg-type]
        return _MISSING
    if isinstance(row, str | bytes):
        return _MISSING
    if slot.kind == "index":
        position = int(slot.value)
    elif slot.kind == "letters":
        position = _column_index(str(slot.value))
    elif slot.kind == "header" and headers is not None:
        matches = [i for i, header in enumerate(headers) if header == slot.value]
        if len(matches) != 1:
            return _MISSING
        position = matches[0]
    else:
        return _MISSING
    return row[position] if 0 <= position < len(row) else _MISSING


def _text_value(
    slot: Slot | None, row: Row, headers: Sequence[str] | None, name: str, *, required: bool
) -> str | None:
    """A str from `slot`. Missing -> `required_field_missing` (required) or
    None (optional); an empty tabular cell is `required_field_empty` (required)
    or omitted (optional)."""
    tabular = not isinstance(row, Mapping)
    value = _MISSING if slot is None else _read_slot(slot, row, headers)
    if value is _MISSING or value is None:
        if required:
            raise RowMappingError("required_field_missing", name)
        return None
    if type(value) is not str:
        raise RowMappingError("mapping_type_mismatch", name)
    if value == "" and tabular:
        if required:
            raise RowMappingError("required_field_empty", name)
        return None
    return value


@dataclass(frozen=True)
class MappedRow:
    raw_record: dict[str, object]
    group_key: str | None


def _map_message(mapping: ImportMapping, raw: object, index: int) -> dict[str, object]:
    keys = mapping.message_keys
    assert keys is not None
    slot = f"messages[{index}]"
    if type(raw) is not dict:
        raise RowMappingError("mapping_type_mismatch", slot)
    known = {keys.role_key, keys.content_key}
    known.update(k for k in (keys.tool_calls_key, keys.tool_call_id_key, keys.name_key) if k)
    if mapping.on_unmapped_message_keys == "reject" and any(key not in known for key in raw):
        raise RowMappingError("unmapped_message_key", slot)
    if keys.role_key not in raw or keys.content_key not in raw:
        raise RowMappingError("required_field_missing", slot)
    raw_role = raw[keys.role_key]
    if type(raw_role) is not str:
        raise RowMappingError("mapping_type_mismatch", slot)
    role = mapping.role_map.get(raw_role)
    if role is None:
        raise RowMappingError("unmapped_role", slot)
    message: dict[str, object] = {"role": role, "content": raw[keys.content_key]}
    for target, source in (
        ("tool_calls", keys.tool_calls_key),
        ("tool_call_id", keys.tool_call_id_key),
        ("name", keys.name_key),
    ):
        if source is not None and source in raw:
            message[target] = raw[source]
    return message


def _parse_timestamp(value: str) -> None:
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        raise RowMappingError("invalid_event_timestamp", "metadata.event_timestamp") from None
    if parsed.utcoffset() is None:
        raise RowMappingError("invalid_event_timestamp", "metadata.event_timestamp")


_METADATA_CHECKS: Final = {"event_timestamp": _parse_timestamp}


def apply_mapping(
    mapping: ImportMapping, row: Row, *, headers: Sequence[str] | None = None
) -> MappedRow:
    """Builds the FTF-0506 raw record from one source row. A mapping never
    defaults a value: an empty optional slot is omitted."""
    if mapping.row_observer is not None:
        # OBSERVER-COPY: a *deep* copy, unconditionally -- a shallow
        # `dict()`/`list()` still shares every nested mutable value with
        # `row` itself, so an observer that mutates a nested list/dict in
        # place would silently corrupt the very row this function is about
        # to build the real candidate and hash from. `copy.deepcopy` is
        # correct and safe for every `Row` shape uniformly (it is a no-op,
        # returning the identical object, for the immutable `str`/`bytes`
        # case -- unlike the old type-dispatched `dict()`/`list()` split,
        # there is no longer a type check to get wrong here).
        copied_row = copy.deepcopy(row)
        copied_headers = None if headers is None else tuple(headers)
        mapping.row_observer(copied_row, copied_headers)
    messages: list[dict[str, object]] = []
    if mapping.prompt is not None:
        system = _text_value(mapping.system, row, headers, "system", required=False)
        prompt = _text_value(mapping.prompt, row, headers, "prompt", required=True)
        completion = _text_value(mapping.completion, row, headers, "completion", required=True)
        if system is not None:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        messages.append({"role": "assistant", "content": completion})
    else:
        if mapping.messages_slot is None:
            if not isinstance(row, Mapping):
                raise RowMappingError("mapping_type_mismatch", "messages")
            found, array = resolve_pointer(dict(row), mapping.messages_pointer or ())
            if not found:
                raise RowMappingError("required_field_missing", "messages")
        else:
            assert mapping.messages_slot is not None
            cell = _read_slot(mapping.messages_slot, row, headers)
            if cell is _MISSING or cell is None:
                raise RowMappingError("required_field_missing", "messages")
            if type(cell) is str:
                if not cell and not isinstance(row, Mapping):
                    raise RowMappingError("required_field_empty", "messages")
                try:
                    array = strict_loads(cell, max_depth=DEFAULT_LIMITS.max_depth)
                except StrictJsonError as exc:
                    raise RowMappingError(exc.code, "messages") from None
            else:
                array = cell
        if type(array) is not list or not array:
            raise RowMappingError("mapping_type_mismatch", "messages")
        messages = [_map_message(mapping, raw, i) for i, raw in enumerate(array)]

    metadata: dict[str, object] = {}
    for name, slot in mapping.metadata_slots:
        text = _text_value(slot, row, headers, f"metadata.{name}", required=False)
        if text is None:
            continue
        check = _METADATA_CHECKS.get(name)
        if check is not None:
            check(text)
        metadata[name] = text
    group_key = None
    if mapping.group_key is not None:
        group_key = _text_value(mapping.group_key, row, headers, "group_key", required=False)
    return MappedRow({"messages": messages, "metadata": metadata}, group_key)


def normalize_mapped_record(
    mapping: ImportMapping,
    mapped: MappedRow,
    *,
    source_record_index: int,
    dataset_sha256: str,
    source_type: str,
    tool_schemas: Mapping[str, Mapping[str, object]] | None = None,
    tool_argument_validator: ToolArgumentValidator | None = None,
) -> CanonicalConversationRecord:
    """FTF-0506 over a mapped row. `record_id` is a uuid5 of
    `dataset_sha256:mapping_sha256:index`: deterministic and content-free. A
    normalisation failure becomes `canonical_schema_invalid`, naming at most
    the message index from the structural `messages[N]` prefix."""
    if not _is_int(source_record_index) or source_record_index < 0:
        raise ValueError("source_record_index must be a non-negative int")
    if type(dataset_sha256) is not str or not _SHA256_PATTERN.fullmatch(dataset_sha256):
        raise ValueError("dataset_sha256 must be 64 lowercase hex characters")
    record_id = str(
        uuid.uuid5(
            IMPORTER_RECORD_ID_NAMESPACE,
            f"{dataset_sha256}:{mapping.mapping_sha256}:{source_record_index}",
        )
    )
    try:
        return normalize_conversation(
            source_type=source_type,
            source_record_index=source_record_index,
            raw_record=mapped.raw_record,
            tool_schemas=tool_schemas if tool_schemas is not None else MappingProxyType({}),
            tool_argument_validator=tool_argument_validator,
            group_key=mapped.group_key,
            record_id=record_id,
        )
    except ConversationNormalizationError as exc:
        found = _MESSAGE_PREFIX.match(str(exc))
        slot = f"messages[{found.group(1)}]" if found else None
        raise RowMappingError("canonical_schema_invalid", slot) from None


def to_dataset_record_candidate(
    record: CanonicalConversationRecord,
    *,
    record_index: int,
    tool_schema_status: str,
) -> dict[str, object]:
    """The Section 6.11 candidate exactly as FTF-0508 accepts it."""
    if not _is_int(record_index) or record_index < 0:
        raise ValueError("record_index must be a non-negative int")
    if tool_schema_status not in ("not_applicable", "valid"):
        raise ValueError("tool_schema_status must be not_applicable or valid")
    return {
        "record_index": record_index,
        "schema_version": SCHEMA_VERSION,
        "source_type": record.lineage.source_type,
        "content": {
            "messages": [message.to_canonical() for message in record.messages],
            "metadata": dict(record.metadata),
        },
        "record_sha256": record.lineage.raw_sha256,
        "normalized_sha256": record.canonical_hash,
        "quality_status": "pending",
        "pii_status": "unscanned",
        "tool_schema_status": tool_schema_status,
    }


# ---------------------------------------------------------------------------
# Events, report, accumulator
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RowRejection:
    locator: str
    code: str
    slot: str | None = None

    def __post_init__(self) -> None:
        if type(self.locator) is not str or not _LOCATOR_PATTERN.fullmatch(self.locator):
            raise ValueError("locator is not from the locator grammar")
        if self.code not in REJECTION_CODES:
            raise ValueError("unknown rejection code")
        if self.slot is not None and not _SLOT_NAME_PATTERN.fullmatch(self.slot):
            raise ValueError("slot is not from the slot grammar")


@dataclass(frozen=True)
class AcceptedRecord:
    candidate: Mapping[str, object] = field(repr=False)


@dataclass(frozen=True)
class BlankLine:
    pass


ImportEvent = AcceptedRecord | RowRejection | BlankLine


_STATE_NEEDS_FAILURE_CODE: Final = {"completed": False, "failed": True, "blocked": True}


@dataclass(frozen=True)
class ImportReport:
    state: str
    failure_code: str | None
    importer: str
    importer_version: str
    mapping_sha256: str
    dataset_purpose: str
    expected_sha256: str
    source_sha256: str
    source_bytes: int
    source_schema_sha256: str | None
    records_seen: int
    records_accepted: int
    records_rejected: int
    blank_lines: int
    rejection_counts: tuple[tuple[str, int], ...]
    rejections: tuple[RowRejection, ...]
    report_sha256: str
    # Where a fatal problem happened (`line:<n>` / `row:<n>` ...). Additive and optional: None
    # here, and a `LocatedImportReport` (below) when there is one, so a report without a
    # locator keeps exactly the fields, the body and the digest it had before.
    fatal_locator = None  # deliberately unannotated: not a dataclass field

    def body(self) -> dict[str, object]:
        """Every field except `report_sha256`, as canonical-JSON-ready data."""
        values = asdict(self)
        del values["report_sha256"]
        values["rejection_counts"] = dict(self.rejection_counts)
        return values

    def __post_init__(self) -> None:
        needs_failure_code = _STATE_NEEDS_FAILURE_CODE.get(self.state)
        if needs_failure_code != (self.failure_code is not None):
            raise ValueError("state must be known and failure_code set exactly when not completed")
        if self.failure_code is not None and self.failure_code not in FAILURE_CODES:
            raise ValueError("unknown failure code")
        if self.records_seen != self.records_accepted + self.records_rejected:
            raise ValueError("records_seen must equal records_accepted + records_rejected")
        if self.report_sha256 != _canonical_sha256(self.body()):
            raise ValueError("report_sha256 does not cover this report")


@dataclass(frozen=True)
class LocatedImportReport(ImportReport):
    """An `ImportReport` that ends at a fatal problem whose location is known; the
    locator is inside the report digest."""

    fatal_locator: str = ""  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if (
            not _STATE_NEEDS_FAILURE_CODE.get(self.state)
            or type(self.fatal_locator) is not str
            or not _LOCATOR_PATTERN.fullmatch(self.fatal_locator)
        ):
            raise ValueError("fatal_locator must be a structural locator on a non-completed report")
        super().__post_init__()


@dataclass(frozen=True)
class PreviewResult:
    accepted: tuple[Mapping[str, object], ...]
    rejections: tuple[RowRejection, ...]
    complete: bool


class Deadline:
    def __init__(self, seconds: float, clock: Callable[[], float]) -> None:
        self._clock = clock
        self._limit = clock() + seconds

    def check(self) -> None:
        if self._clock() > self._limit:
            raise ImportAborted("failed", "parse_timeout")


class ChunkReader(Iterator[bytes]):
    """Wraps an `Iterable[bytes]` source: counts and hashes what it yields and
    holds it to the declared size and sha256.

    Any exception from the source is `blocked` / `source_unavailable`; a size
    or hash that differs from the declaration is `failed` / `source_changed`;
    the deadline is checked before every chunk. After an abort the same
    `ImportAborted` is raised on every later read."""

    def __init__(
        self,
        source: Iterable[bytes],
        *,
        expected_size: int,
        expected_sha256: str,
        limits: ImportLimits = DEFAULT_LIMITS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if not _is_int(expected_size) or expected_size < 0:
            raise ValueError("expected_size must be a non-negative int")
        if type(expected_sha256) is not str or not _SHA256_PATTERN.fullmatch(expected_sha256):
            raise ValueError("expected_sha256 must be 64 lowercase hex characters")
        self._iterator: Iterator[bytes] = iter(source)
        self._expected_size = expected_size
        self._expected_sha256 = expected_sha256
        self._limits = limits
        self.deadline = Deadline(limits.deadline_seconds, clock)
        self._hash = hashlib.sha256()
        self.bytes_read = 0
        self._aborted: ImportAborted | None = None

    @property
    def sha256(self) -> str:
        return self._hash.hexdigest()

    def _abort(self, state: str, code: str) -> ImportAborted:
        self._aborted = ImportAborted(state, code)
        close = getattr(self._iterator, "close", None)
        if callable(close):
            try:
                close()
            except Exception:  # noqa: BLE001 - the verdict is already decided
                pass
        return self._aborted

    def __next__(self) -> bytes:
        if self._aborted is not None:
            raise self._aborted
        if self._expected_size > self._limits.max_source_bytes:
            raise self._abort("failed", "source_too_large")
        try:
            self.deadline.check()
        except ImportAborted as exc:
            raise self._abort(exc.state, exc.failure_code) from None
        while True:
            try:
                chunk = next(self._iterator)
            except StopIteration:
                break
            except Exception as exc:  # noqa: BLE001 - any source failure is an outage, never a verdict
                if getattr(exc, "source_changed", False) is True:
                    # The source itself says the verified object changed under
                    # the read (a version difference between two windows).
                    raise self._abort("failed", "source_changed") from None
                raise self._abort("blocked", "source_unavailable") from None
            if type(chunk) is not bytes:
                raise self._abort("blocked", "source_unavailable")
            if not chunk:
                continue
            self.bytes_read += len(chunk)
            self._hash.update(chunk)
            if self.bytes_read > self._expected_size:
                raise self._abort("failed", "source_changed")
            return chunk
        if self.bytes_read != self._expected_size or not hmac.compare_digest(
            self.sha256, self._expected_sha256
        ):
            raise self._abort("failed", "source_changed")
        raise StopIteration


class ImportAccumulator:
    """Counts events into an `ImportReport`. The one code path for a full
    import and a preview."""

    def __init__(
        self,
        *,
        importer: str,
        importer_version: str,
        mapping_sha256: str,
        dataset_purpose: str,
        expected_sha256: str,
        limits: ImportLimits = DEFAULT_LIMITS,
        stop_after_accepted: int | None = None,
        on_accepted: Callable[[Mapping[str, object]], None] | None = None,
    ) -> None:
        for label, value in (("importer", importer), ("importer_version", importer_version)):
            if type(value) is not str or not _NAME_PATTERN.fullmatch(value):
                raise ValueError(f"{label} is not a plain name")
        if type(mapping_sha256) is not str or not _SHA256_PATTERN.fullmatch(mapping_sha256):
            raise ValueError("mapping_sha256 must be 64 lowercase hex characters")
        if type(expected_sha256) is not str or not _SHA256_PATTERN.fullmatch(expected_sha256):
            raise ValueError("expected_sha256 must be 64 lowercase hex characters")
        if type(dataset_purpose) is not str or not _PURPOSE_PATTERN.fullmatch(dataset_purpose):
            raise ValueError("dataset_purpose is not a plain name")
        if stop_after_accepted is not None and (
            not _is_int(stop_after_accepted)
            or not 1 <= stop_after_accepted <= limits.preview_max_records
        ):
            raise ValueError("stop_after_accepted must be 1..preview_max_records")
        self._meta = (importer, importer_version, mapping_sha256, dataset_purpose, expected_sha256)
        self._limits = limits
        self._stop_after = stop_after_accepted
        self._on_accepted = on_accepted
        self._accepted = 0
        self._rejected = 0
        self._blank = 0
        self._counts: dict[str, int] = {}
        self._rejections: list[RowRejection] = []
        self.source_schema_sha256: str | None = None
        self.stopped_early = False

    def add(self, event: ImportEvent) -> bool:
        """Records one event. Returns False once a preview has what it asked
        for. Raises `ImportAborted` when the rejection budget is spent."""
        if isinstance(event, AcceptedRecord):
            self._accepted += 1
            if self._on_accepted is not None:
                self._on_accepted(event.candidate)
        elif isinstance(event, RowRejection):
            if self._rejected >= self._limits.max_rejections:
                raise ImportAborted("failed", "too_many_rejections")
            self._rejected += 1
            self._counts[event.code] = self._counts.get(event.code, 0) + 1
            self._rejections.append(event)
        elif isinstance(event, BlankLine):
            self._blank += 1
        else:
            raise TypeError("unknown import event")
        if self._stop_after is not None and self._accepted >= self._stop_after:
            self.stopped_early = True
            return False
        return True

    def report(
        self,
        *,
        source_sha256: str,
        source_bytes: int,
        aborted: ImportAborted | None = None,
        fatal_locator: str | None = None,
    ) -> ImportReport:
        importer, version, mapping_sha256, purpose, expected = self._meta
        fields: dict[str, object] = {
            "state": aborted.state if aborted else "completed",
            "failure_code": aborted.failure_code if aborted else None,
            "importer": importer,
            "importer_version": version,
            "mapping_sha256": mapping_sha256,
            "dataset_purpose": purpose,
            "expected_sha256": expected,
            "source_sha256": source_sha256,
            "source_bytes": source_bytes,
            "source_schema_sha256": self.source_schema_sha256,
            "records_seen": self._accepted + self._rejected,
            "records_accepted": self._accepted,
            "records_rejected": self._rejected,
            "blank_lines": self._blank,
            "rejection_counts": tuple(sorted(self._counts.items())),
            "rejections": tuple(self._rejections),
        }
        unsealed = {
            **fields,
            "rejection_counts": dict(self._counts),
            "rejections": [asdict(rejection) for rejection in self._rejections],
        }
        if fatal_locator is not None:
            unsealed["fatal_locator"] = fatal_locator
            return LocatedImportReport(
                **fields,  # type: ignore[arg-type]
                fatal_locator=fatal_locator,
                report_sha256=_canonical_sha256(unsealed),
            )
        return ImportReport(**fields, report_sha256=_canonical_sha256(unsealed))  # type: ignore[arg-type]


def run_import(
    events: Iterable[ImportEvent],
    reader: ChunkReader,
    *,
    importer: str,
    importer_version: str,
    mapping: ImportMapping,
    dataset_purpose: str,
    expected_sha256: str,
    limits: ImportLimits = DEFAULT_LIMITS,
    source_schema_sha256: str | None = None,
    on_accepted: Callable[[Mapping[str, object]], None] | None = None,
) -> ImportReport:
    """Drains `events` (which must pull their bytes from `reader`) into a
    report. An abort anywhere is the report's state, never an exception; a
    source that stopped early or never reached its end of stream cannot be
    `completed`."""
    accumulator = ImportAccumulator(
        importer=importer,
        importer_version=importer_version,
        mapping_sha256=mapping.mapping_sha256,
        dataset_purpose=dataset_purpose,
        expected_sha256=expected_sha256,
        limits=limits,
        on_accepted=on_accepted,
    )
    accumulator.source_schema_sha256 = source_schema_sha256
    aborted: ImportAborted | None = None
    try:
        for event in events:
            reader.deadline.check()
            accumulator.add(event)
        for _ in reader:  # the importer must have consumed the stream to its end
            raise ImportAborted("failed", "stream_not_consumed")
    except ImportAborted as exc:
        aborted = exc
    return accumulator.report(
        source_sha256=reader.sha256, source_bytes=reader.bytes_read, aborted=aborted
    )


def preview(
    events: Iterable[ImportEvent],
    limit: int,
    *,
    limits: ImportLimits = DEFAULT_LIMITS,
) -> PreviewResult:
    """The first `limit` accepted records and the rejections seen on the way,
    through the same accumulator a full import uses."""
    if not _is_int(limit) or not 1 <= limit <= limits.preview_max_records:
        raise ValueError("limit must be between 1 and the preview maximum")
    accepted: list[Mapping[str, object]] = []
    accumulator = ImportAccumulator(
        importer="preview",
        importer_version="1",
        mapping_sha256="0" * 64,
        dataset_purpose="preview",
        expected_sha256="0" * 64,
        limits=limits,
        stop_after_accepted=limit,
        on_accepted=accepted.append,
    )
    complete = True
    try:
        for event in events:
            if not accumulator.add(event):
                break
    except ImportAborted:
        complete = False
    report = accumulator.report(source_sha256="0" * 64, source_bytes=0)
    return PreviewResult(
        tuple(accepted), report.rejections, complete and not accumulator.stopped_early
    )
