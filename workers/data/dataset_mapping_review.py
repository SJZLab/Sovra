"""Dataset mapping probe and preview (FTF-H011-P1; FINE_TUNING_FACTORY_RELEASE_1.md
Section 7, Section 7A.10, Section 8-8.2, Section 9, Section 22 Screen F, Section 67.1-67.4,
Section 72.1-72.3/72.5, Section 77.6, Section 81; AF-15).

The wizard needs to show a source's real raw rows before a mapping is built (`run_mapping_probe`)
and, once a mapping is drafted, every row's raw value next to what it becomes
(`run_mapping_preview`)
-- "preview normalized records and rejected rows with machine-readable reasons" (Section 67.2).
Both reuse FTF-0502-P1's own importer event streams (`jsonl_events`/`json_events`/`csv_events`)
and `ImportMapping`'s own additive `row_observer` tap (added by this task, the one serialized edit
the IMPORTER ORDER catalog rule allows `workers/data/import_common.py`): the tap sees the exact raw
row an event came from, in the same order the events themselves arrive, so this module pairs them
one by one without re-parsing or re-implementing a single importer's own byte-level rules.

XLSX is not dispatched here: Section 67.3's own values-only reading is FTF-0505 (FTF-H011-P6, "only
after FTF-0505 is CERTIFIED"); a verified content type this module does not recognise -- XLSX
included -- is `MappingPreviewUnavailableError` ("importer_unavailable"), never a silent fallback.

Masking (Section 67.2 "sensitive values are masked unless the user has preview permission"):
permission is an authorization decision this pure module cannot make (it has no identity, no DB),
so the caller (the service layer, which does hold the authenticated principal and the connection's
classification) passes `reveal: bool` and this module masks accordingly. Disclosed reading, since
no section fixes an exact masking shape: a header or object key is schema, not data, and is always
shown in clear (the wizard cannot map a column it cannot see the name of); every *value* reachable
from a raw row, recursively through lists and objects, is masked when `reveal` is False, replaced
by a type-and-size-bucket marker that carries none of the original text -- this is "masking
skipped" and "one field left in clear" both caught by the same rule (mask every value or mask
none, never a per-field exception), per this task's own fault list.

Scope decision, same disclosed precedent as every other `workers/data` module reused by FTF-H011
(FTF-0506/FTF-0507/FTF-0508 &c.): pure and synchronous here, no database and no network -- this
task's own `database_changes` clause ("No independent schema change") and its lock
(`module:import_common`) fix that shape. The DB/S3 composition (opening the real dataset source,
authorizing the connection's purpose, masking by the caller's real permission) is
`apps/api/app/datasets/dataset_mapping_service.py`, FTF-H011-P1's other file. `proving_symbol`
stays blank for both: FTF-H011-P3 is the first live route.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Final

from workers.data.csv_importer import csv_events
from workers.data.import_common import (
    AcceptedRecord,
    BlankLine,
    ChunkReader,
    ImportAborted,
    ImportLimits,
    ImportMapping,
    Row,
    RowRejection,
    Slot,
)
from workers.data.json_importer import json_events
from workers.data.jsonl_importer import jsonl_events

__all__ = [
    "JSONL_PREVIEW_CONTENT_TYPES",
    "PREVIEW_CONTENT_TYPES",
    "PREVIEW_LIMITS",
    "ComparisonCell",
    "MappingPreviewResult",
    "MappingPreviewRow",
    "MappingPreviewUnavailableError",
    "ProbeResult",
    "compute_tool_definitions_sha256",
    "run_mapping_preview",
    "run_mapping_probe",
]

JSONL_PREVIEW_CONTENT_TYPES: Final = frozenset({"application/jsonl", "application/x-ndjson"})
PREVIEW_CONTENT_TYPES: Final = JSONL_PREVIEW_CONTENT_TYPES | frozenset(
    {"application/json", "text/csv"}
)

# Section 67.3's own preview bound (max 20) and a short deadline: a preview is one bounded,
# interactive read, never the full-import limits (`ImportLimits` is itself lower-only, so this
# can only ever request *less* than the defaults, never more).
PREVIEW_LIMITS: Final = ImportLimits(max_rejections=200, deadline_seconds=30.0)

_MASK_PREFIX: Final = "⁣masked⁣"  # a word-joiner-delimited marker, never real content
_SIZE_BUCKETS: Final = (0, 1, 9, 33, 129, 513, 2049)


class MappingPreviewUnavailableError(ValueError):
    """The verified content type has no preview importer yet (XLSX, or anything this release
    does not recognise). `code` is always `importer_unavailable`."""

    code = "importer_unavailable"

    def __init__(self, verified_content_type: str) -> None:
        super().__init__(f"importer_unavailable: {verified_content_type!r}")
        self.verified_content_type = verified_content_type


@dataclass(frozen=True)
class ComparisonCell:
    slot: str
    raw_type: str
    canonical_type: str
    identical: bool


@dataclass(frozen=True)
class MappingPreviewRow:
    locator: str
    outcome: str  # "accepted" | "rejected"
    code: str | None
    slot: str | None
    raw_row: Mapping[str, object] | tuple[object, ...] | None
    not_mapped: tuple[str, ...]
    candidate: Mapping[str, object] | None
    comparison: tuple[ComparisonCell, ...]

    def __post_init__(self) -> None:
        if self.outcome not in ("accepted", "rejected"):
            raise ValueError("outcome must be accepted or rejected")
        if (self.outcome == "accepted") != (self.candidate is not None):
            raise ValueError("candidate is set exactly when outcome is accepted")
        if (self.outcome == "rejected") != (self.code is not None):
            raise ValueError("code is set exactly when outcome is rejected")


@dataclass(frozen=True)
class MappingPreviewResult:
    state: str  # "completed" | "failed" | "blocked"
    failure_code: str | None
    rows: tuple[MappingPreviewRow, ...]
    observed_role_values: tuple[str, ...]
    counts: Mapping[str, int] = field(repr=False)
    complete: bool
    tool_definitions_sha256: str
    source_schema_sha256: str | None
    preview_sha256: str


@dataclass(frozen=True)
class ProbeResult:
    headers: tuple[str, ...] | None
    raw_rows: tuple[Mapping[str, object] | tuple[object, ...], ...]
    complete: bool


def _size_bucket(length: int) -> str:
    bucket = 0
    for threshold in _SIZE_BUCKETS:
        if length >= threshold:
            bucket = threshold
    return f">={bucket}"


def _mask_value(value: object) -> object:
    """None/bool are never masked (never sensitive text); a str gets its own size-bucketed
    marker (`_size_bucket`); every other scalar (int, float, and anything else this module
    does not otherwise recognise) shares one plain type-name marker -- there is nothing a
    number's own value could leak that its type name does not already say, so it needs no
    bucket of its own."""
    if value is None or type(value) is bool:
        return value
    if type(value) is str:
        return f"{_MASK_PREFIX}:str:{_size_bucket(len(value))}"
    if type(value) is list:
        return [_mask_value(v) for v in value]
    if type(value) is dict:
        return {key: _mask_value(v) for key, v in value.items()}
    return f"{_MASK_PREFIX}:{type(value).__name__}"


def _mask_row(
    row: Row | None, headers: Sequence[str] | None
) -> Mapping[str, object] | tuple[object, ...] | None:
    """Masks every *value* reachable from a raw row; a header/object key is schema and is
    never masked. A tabular row (a plain sequence) has no keys of its own, so pairs with
    `headers` positionally when they are the same length; otherwise (a short/overlong row,
    already its own distinct rejection elsewhere) it is masked positionally without names."""
    if row is None:
        return None
    if isinstance(row, Mapping):
        return {key: _mask_value(value) for key, value in row.items()}
    if headers is not None and len(headers) == len(row):
        return {name: _mask_value(value) for name, value in zip(headers, row, strict=True)}
    return tuple(_mask_value(value) for value in row)


def compute_tool_definitions_sha256(
    tool_schemas: Mapping[str, Mapping[str, object]] | None,
) -> str:
    """A stable digest over the exact tool definitions a preview/import run was given,
    canonical-JSON the same way `import_common`'s own `mapping_sha256`/`report_sha256` are
    computed (sorted keys, ASCII-escaped, no whitespace). `None` (no tool schemas supplied) and
    an empty `{}` both canonicalize to the same empty document and so the same digest -- neither
    one is a "fewer tools" variant of the other, both just mean no tool is defined."""
    document = (
        {} if tool_schemas is None else {name: dict(tool_schemas[name]) for name in tool_schemas}
    )
    text = json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(text.encode("ascii")).hexdigest()


PreviewEvent = AcceptedRecord | RowRejection | BlankLine

# One shared sentinel for "this value is not present" across every helper below -- two
# independently constructed `object()` instances are never `is`-equal to each other, so a
# per-function `_MISSING = object()` would silently make every "is it present" check this
# module does True, by comparing against the wrong instance.
_MISSING: Final = object()

_EVENTS_BY_CONTENT_TYPE: Final[dict[str, Callable[..., Iterator[PreviewEvent]]]] = {
    "application/jsonl": jsonl_events,
    "application/x-ndjson": jsonl_events,
    "application/json": json_events,
    "text/csv": csv_events,
}


def _top_level_slots(mapping: ImportMapping) -> tuple[Slot, ...]:
    """Slots that name a position in the *raw row itself* -- never
    `message_keys` (those name a key inside each already-split message, not a column of the
    row), and never a bare one-token `messages_pointer` already folded into `not_mapped` by its
    caller."""
    slots = [
        mapping.prompt,
        mapping.completion,
        mapping.system,
        mapping.messages_slot,
        mapping.group_key,
    ]
    slots.extend(slot for _name, slot in mapping.metadata_slots)
    return tuple(slot for slot in slots if slot is not None)


def _mapped_names(mapping: ImportMapping, headers: Sequence[str] | None) -> frozenset[str]:
    names: set[str] = set()
    for slot in _top_level_slots(mapping):
        if slot.kind in ("key", "header") and isinstance(slot.value, str):
            names.add(slot.value)
        elif slot.kind == "index" and headers is not None and 0 <= int(slot.value) < len(headers):
            names.add(headers[int(slot.value)])
    if (
        mapping.messages_pointer is not None
        and len(mapping.messages_pointer) == 1
        and headers is None
    ):
        names.add(mapping.messages_pointer[0])
    return frozenset(names)


def _not_mapped(mapping: ImportMapping, row: Row, headers: Sequence[str] | None) -> tuple[str, ...]:
    mapped = _mapped_names(mapping, headers)
    if isinstance(row, Mapping):
        return tuple(sorted(str(key) for key in row if str(key) not in mapped))
    if headers is not None and len(headers) == len(row):
        return tuple(name for name in headers if name not in mapped)
    return ()


_PY_TYPE_NAMES: Final[dict[type, str]] = {
    str: "str",
    bool: "bool",
    int: "int",
    float: "float",
    list: "list",
    dict: "object",
    type(None): "null",
}


def _type_name(value: object) -> str:
    return _PY_TYPE_NAMES.get(type(value), type(value).__name__)


def _read_top_level(slot: Slot, row: Row, headers: Sequence[str] | None) -> object:
    """A best-effort, read-only echo of `import_common._read_slot` for display only: this
    module never uses the result to build a record, only to show the user what one mapped slot
    of the raw row looked like next to its canonical value, so an approximation that cannot
    resolve a `letters`-kind slot (spreadsheet column letters; XLSX is not dispatched by this
    module, Section 67.3) falls back to "not found" rather than re-deriving that one additional
    rule for a display-only path."""
    if isinstance(row, Mapping):
        if slot.kind not in ("key", "header"):
            return _MISSING
        return row.get(str(slot.value), _MISSING)
    if isinstance(row, str | bytes):
        return _MISSING
    if slot.kind == "index":
        position = int(slot.value)
    elif slot.kind == "header" and headers is not None:
        matches = [i for i, header in enumerate(headers) if header == slot.value]
        position = matches[0] if len(matches) == 1 else -1
    else:
        return _MISSING
    return row[position] if 0 <= position < len(row) else _MISSING


def _comparison_cells(
    mapping: ImportMapping, row: Row, headers: Sequence[str] | None, candidate: Mapping[str, object]
) -> tuple[ComparisonCell, ...]:
    content = candidate["content"]
    assert isinstance(content, dict)
    messages = content["messages"]
    metadata = content["metadata"]
    assert isinstance(messages, list) and isinstance(metadata, dict)
    cells: list[ComparisonCell] = []

    def cell(name: str, slot: Slot | None, canonical: object) -> None:
        if slot is None:
            return
        raw = _read_top_level(slot, row, headers)
        raw_present = raw is not _MISSING
        raw_type = _type_name(raw) if raw_present else "missing"
        canonical_type = "missing" if canonical is _MISSING else _type_name(canonical)
        identical = raw_present and canonical is not _MISSING and raw == canonical
        cells.append(ComparisonCell(name, raw_type, canonical_type, identical))

    def message_content(role: str) -> object:
        """The plain text FTF-0506 wrapped for one message of `role`, when there is exactly
        one ("prompt"/"completion"/"system" slots are always a single plain string going in,
        Section 67.2's own typed transform allow-list has no multi-part content transform) --
        the raw content-part list itself otherwise, so a genuine mismatch still shows as one."""
        for message in messages:
            if message.get("role") == role:
                parts = message["content"]
                if (
                    isinstance(parts, list)
                    and len(parts) == 1
                    and isinstance(parts[0], dict)
                    and parts[0].get("type") == "text"
                ):
                    return parts[0].get("text")
                return parts
        return _MISSING

    if mapping.prompt is not None:
        cell("system", mapping.system, message_content("system"))
        cell("prompt", mapping.prompt, message_content("user"))
        cell("completion", mapping.completion, message_content("assistant"))
    elif mapping.messages_slot is not None:
        raw = _read_top_level(mapping.messages_slot, row, headers)
        raw_type = _type_name(raw) if raw is not _MISSING else "missing"
        cells.append(
            ComparisonCell(
                "messages",
                raw_type,
                "list",
                raw is not _MISSING and isinstance(raw, list) and len(raw) == len(messages),
            )
        )
    for name, slot in mapping.metadata_slots:
        cell(f"metadata.{name}", slot, metadata.get(name, _MISSING))
    if mapping.group_key is not None:
        raw = _read_top_level(mapping.group_key, row, headers)
        raw_type = _type_name(raw) if raw is not _MISSING else "missing"
        cells.append(ComparisonCell("group_key", raw_type, "str", raw_type == "str"))
    return tuple(cells)


def _with_row_tap(
    mapping: ImportMapping, captured: list[tuple[Row, Sequence[str] | None]]
) -> ImportMapping:
    """`mapping`, with a tap attached that appends every raw row it sees to `captured`, in the
    same order the importer's own events for those rows are yielded (see the module docstring:
    one `apply_mapping` call per row reaching it, synchronously, before that row's event)."""

    def observe(row: Row, headers: Sequence[str] | None) -> None:
        captured.append((row, headers))

    return dataclasses.replace(mapping, row_observer=observe)


def _observed_role_value(mapping: ImportMapping, row: Row) -> str | None:
    """The raw role string of one accepted/rejected row, for `observed_role_values` -- only
    when the row names exactly one message-shaped object directly (the common `messages_json`/
    `messages` single-record wizard step); a batched array of several messages has several role
    values and is left to the per-record canonical candidate the wizard already shows."""
    keys = mapping.message_keys
    if keys is None or not isinstance(row, Mapping):
        return None
    raw_role = row.get(keys.role_key)
    return raw_role if isinstance(raw_role, str) else None


def run_mapping_probe(
    reader: ChunkReader,
    mapping: ImportMapping,
    *,
    verified_content_type: str,
    dataset_sha256: str,
    reveal: bool,
    limits: ImportLimits = PREVIEW_LIMITS,
) -> ProbeResult:
    """Headers (tabular sources only) and up to `limits.preview_max_records` raw rows of a
    source, before any real mapping exists. `mapping` is one minimal, certified probe mapping
    for the dispatched importer (the caller picks it; see the sibling `*_PROBE_MAPPING`
    constants a service layer builds once) -- every candidate/rejection it produces is
    discarded, only the raw rows the tap observed are kept."""
    events_fn = _EVENTS_BY_CONTENT_TYPE.get(verified_content_type)
    if events_fn is None:
        raise MappingPreviewUnavailableError(verified_content_type)
    captured: list[tuple[Row, Sequence[str] | None]] = []
    observed = _with_row_tap(mapping, captured)
    stream = events_fn(
        reader,
        observed,
        verified_content_type=verified_content_type,
        dataset_sha256=dataset_sha256,
        limits=limits,
    )
    raw_rows: list[Mapping[str, object] | tuple[object, ...]] = []
    headers: Sequence[str] | None = None
    complete = True
    try:
        for event in stream:
            reader.deadline.check()
            if isinstance(event, BlankLine):
                continue
            row, row_headers = captured.pop() if captured else (None, None)
            captured.clear()
            if row_headers is not None:
                headers = row_headers
            if row is not None:
                raw_rows.append(_present_row(row, row_headers, reveal))
            if len(raw_rows) >= limits.preview_max_records:
                break
    except ImportAborted:
        complete = False
    return ProbeResult(None if headers is None else tuple(headers), tuple(raw_rows), complete)


def _present_row(
    row: Row, headers: Sequence[str] | None, reveal: bool
) -> Mapping[str, object] | tuple[object, ...]:
    """The raw row as the caller is allowed to see it: unchanged (but still a fresh copy,
    paired positionally with `headers` when it is a tabular row) when `reveal` is True, every
    value masked otherwise. Keys/headers are schema and are never masked either way."""
    if reveal:
        if isinstance(row, Mapping):
            return dict(row)
        if headers is not None and len(headers) == len(row):
            return dict(zip(headers, row, strict=True))
        return tuple(row)
    masked = _mask_row(row, headers)
    assert masked is not None
    return masked


def run_mapping_preview(
    reader: ChunkReader,
    mapping: ImportMapping,
    *,
    verified_content_type: str,
    dataset_sha256: str,
    tool_schemas: Mapping[str, Mapping[str, object]] | None,
    reveal: bool,
    limits: ImportLimits = PREVIEW_LIMITS,
) -> MappingPreviewResult:
    """Drives the real mapping over the real source, through the dispatched importer's own
    event stream, and pairs every accepted/rejected row with the exact raw row the tap on
    `mapping.row_observer` saw for it (absent for a row that never reached `apply_mapping`,
    e.g. malformed JSON or undecodable bytes: still a row, with no raw row to show). Never
    raises for a storage outage or a changed object (Section 67.3/Law 17): the verdict is
    `state`/`failure_code`, the same convention `import_common.ImportReport` uses."""
    events_fn = _EVENTS_BY_CONTENT_TYPE.get(verified_content_type)
    if events_fn is None:
        raise MappingPreviewUnavailableError(verified_content_type)
    tool_definitions_sha256 = compute_tool_definitions_sha256(tool_schemas)
    captured: list[tuple[Row, Sequence[str] | None]] = []
    observed = _with_row_tap(mapping, captured)
    stream = events_fn(
        reader,
        observed,
        verified_content_type=verified_content_type,
        dataset_sha256=dataset_sha256,
        limits=limits,
        tool_schemas=tool_schemas,
    )
    rows: list[MappingPreviewRow] = []
    role_values: set[str] = set()
    counts: dict[str, int] = {"seen": 0, "accepted": 0, "rejected": 0}
    source_schema_sha256: str | None = None
    aborted: ImportAborted | None = None
    locator_index = 0
    try:
        for event in stream:
            reader.deadline.check()
            schema = getattr(stream, "source_schema_sha256", None)
            if schema is not None:
                source_schema_sha256 = schema
            if isinstance(event, BlankLine):
                continue
            locator_index += 1
            row, row_headers = captured.pop() if captured else (None, None)
            captured.clear()
            if len(rows) >= limits.preview_max_records:
                break
            counts["seen"] += 1
            masked_row = _present_row(row, row_headers, reveal) if row is not None else None
            if isinstance(event, AcceptedRecord):
                counts["accepted"] += 1
                not_mapped = _not_mapped(mapping, row, row_headers) if row is not None else ()
                comparison = (
                    _comparison_cells(mapping, row, row_headers, event.candidate)
                    if row is not None
                    else ()
                )
                if row is not None:
                    value = _observed_role_value(mapping, row)
                    if value is not None:
                        role_values.add(value)
                rows.append(
                    MappingPreviewRow(
                        locator=f"record:{locator_index}",
                        outcome="accepted",
                        code=None,
                        slot=None,
                        raw_row=masked_row,
                        not_mapped=not_mapped,
                        candidate=event.candidate,
                        comparison=comparison,
                    )
                )
            else:
                assert isinstance(event, RowRejection)
                counts["rejected"] += 1
                counts[f"rejected:{event.code}"] = counts.get(f"rejected:{event.code}", 0) + 1
                not_mapped = _not_mapped(mapping, row, row_headers) if row is not None else ()
                rows.append(
                    MappingPreviewRow(
                        locator=f"record:{locator_index}",
                        outcome="rejected",
                        code=event.code,
                        slot=event.slot,
                        raw_row=masked_row,
                        not_mapped=not_mapped,
                        candidate=None,
                        comparison=(),
                    )
                )
    except ImportAborted as exc:
        aborted = exc
    state = aborted.state if aborted is not None else "completed"
    failure_code = aborted.failure_code if aborted is not None else None
    complete = aborted is None and len(rows) < limits.preview_max_records
    digest_source = {
        "dataset_sha256": dataset_sha256,
        "mapping_sha256": mapping.mapping_sha256,
        "tool_definitions_sha256": tool_definitions_sha256,
        "rows": [
            {
                "locator": row.locator,
                "outcome": row.outcome,
                "code": row.code,
                "record_sha256": row.candidate.get("record_sha256") if row.candidate else None,
                "normalized_sha256": (
                    row.candidate.get("normalized_sha256") if row.candidate else None
                ),
            }
            for row in rows
        ],
        "counts": counts,
    }
    preview_sha256 = hashlib.sha256(
        json.dumps(digest_source, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode(
            "ascii"
        )
    ).hexdigest()
    return MappingPreviewResult(
        state=state,
        failure_code=failure_code,
        rows=tuple(rows),
        observed_role_values=tuple(sorted(role_values)),
        counts=counts,
        complete=complete,
        tool_definitions_sha256=tool_definitions_sha256,
        source_schema_sha256=source_schema_sha256,
        preview_sha256=preview_sha256,
    )
