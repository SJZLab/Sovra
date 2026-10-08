"""FTF-0903 -- Content-free telemetry (Section 11-12, 65-66, 70, 77, 81).

Workflow, log and span telemetry describe *that* something happened, never
*what* the content was. Every event kind has a frozen, closed field schema
(`EVENT_SCHEMAS`): a field not in that schema is refused outright, and a
field that is in the schema is still refused unless its value matches that
field's own bounded shape -- a short id/code pattern, a closed enum, a
non-negative bounded number, a timezone-aware timestamp, a bool, or an
opaque `ContentRef` pointing at encrypted private content kept elsewhere
(Section 11.4/12 "encrypted private content stored by reference").

There is no "message" or "detail" free-text field anywhere in these
schemas. Section 12's own test is "a malicious worker cannot send arbitrary
payloads to an otherwise permitted collector", so no field, however named,
accepts unbounded text: every string-shaped field kind (`id`, `code`,
`enum`) is matched with `fullmatch` against a short, single-line, bounded
pattern, so a source snippet, an exception traceback or a raw prompt is
refused by the *shape* check alone, never by name-sniffing for "that looks
like a prompt". A value ending in a single trailing newline (the classic
`$`-anchor bypass for `re.match`) is refused the same way a multi-line
string is, because every pattern here is matched with `fullmatch`, never
`match`.

`emit_event` is fail-closed: an unknown event kind, a non-mapping `fields`
argument, a field name absent from the schema, a missing required field, or
any present field whose value fails its own kind's check all raise one of
this module's own coded errors -- never a silently dropped field, a
coerced value, or a partially-built event. Error messages name the
rejected field, never its value (Section 65.4 "no secrets ... in log").

This module is a pure library with no live caller yet: the producers
(Temporal workflows, worker logs, serving spans) are separate,
dependency-gated tasks that will call `emit_event`. That gap is disclosed
in docs/spec-matrix.yaml, the same blank-`proving_symbol` precedent
FTF-0901/FTF-0902 carry.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from types import MappingProxyType

__all__ = [
    "EVENT_KINDS",
    "WORKFLOW_STATES",
    "LOG_LEVELS",
    "SPAN_STATUSES",
    "ContentFreeTelemetryError",
    "InvalidEventError",
    "DisallowedFieldError",
    "ContentRefusedError",
    "ContentRef",
    "TelemetryEvent",
    "EVENT_SCHEMAS",
    "emit_event",
]

EVENT_KINDS: tuple[str, ...] = ("workflow", "log", "span")

WORKFLOW_STATES: tuple[str, ...] = (
    "queued",
    "running",
    "succeeded",
    "failed",
    "cancelled",
    "reconciliation_required",
)
LOG_LEVELS: tuple[str, ...] = ("debug", "info", "warning", "error")
SPAN_STATUSES: tuple[str, ...] = ("ok", "error")

# Bounded, single-line shapes. `fullmatch` (never `match`) so a trailing
# newline -- the classic `$`-anchor bypass -- cannot sneak a second line
# of free text past the end-of-pattern check.
_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,98}")
_CODE_RE = re.compile(r"[a-z][a-z0-9_]{0,63}")
_MAX_NUMBER = 1_000_000_000


class ContentFreeTelemetryError(Exception):
    """Base; every subclass has a stable `code` for the API error body."""

    code = "content_free_telemetry_error"


class InvalidEventError(ContentFreeTelemetryError):
    """Malformed call shape: unknown kind, or `fields` not a mapping."""

    code = "content_free_telemetry_invalid_event"


class DisallowedFieldError(ContentFreeTelemetryError):
    """A field name outside the event kind's closed schema, or a required
    field that is missing."""

    code = "content_free_telemetry_disallowed_field"


class ContentRefusedError(ContentFreeTelemetryError):
    """A field is permitted, but its value fails that field's own bounded
    shape -- the negative acceptance made executable: no snippet,
    traceback or prompt gets through, whatever field it is attached to."""

    code = "content_free_telemetry_content_refused"


@dataclass(frozen=True)
class ContentRef:
    """An opaque pointer to encrypted private content kept elsewhere.
    Never carries the content itself -- only two bounded ids."""

    ref_id: str
    encryption_key_id: str

    def __post_init__(self) -> None:
        _id_value(self.ref_id, "content ref id")
        _id_value(self.encryption_key_id, "encryption key id")


@dataclass(frozen=True)
class TelemetryEvent:
    kind: str
    fields: Mapping[str, object]


def _id_value(value: object, what: str) -> str:
    if not isinstance(value, str) or not _ID_RE.fullmatch(value):
        raise ContentRefusedError(f"{what} is not a bounded id")
    return value


def _code_value(value: object, what: str) -> str:
    if not isinstance(value, str) or not _CODE_RE.fullmatch(value):
        raise ContentRefusedError(f"{what} is not a bounded code")
    return value


def _enum_value(value: object, allowed: tuple[str, ...], what: str) -> str:
    # Type check before membership: an unhashable value must raise our
    # own error, never a bare TypeError from `in`.
    if not isinstance(value, str) or value not in allowed:
        raise ContentRefusedError(f"{what} is not one of the closed set {allowed}")
    return value


def _count_value(value: object, what: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0 or value > _MAX_NUMBER:
        raise ContentRefusedError(f"{what} is not a bounded non-negative count")
    return value


def _finite(value: float) -> bool:
    return value == value and value not in (float("inf"), float("-inf"))


def _number_value(value: object, what: str) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, int | float)
        or not _finite(float(value))
        or value < 0
        or value > _MAX_NUMBER
    ):
        raise ContentRefusedError(f"{what} is not a bounded non-negative number")
    return float(value)


def _timestamp_value(value: object, what: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ContentRefusedError(f"{what} must be a timezone-aware datetime")
    return value


def _bool_value(value: object, what: str) -> bool:
    if not isinstance(value, bool):
        raise ContentRefusedError(f"{what} must be a bool")
    return value


def _content_ref_value(value: object, what: str) -> ContentRef:
    if not isinstance(value, ContentRef):
        raise ContentRefusedError(f"{what} must be a ContentRef, never inline content")
    return value


@dataclass(frozen=True)
class _FieldSpec:
    kind: str
    required: bool = False
    enum: tuple[str, ...] | None = None


def _validate_field(name: str, spec: _FieldSpec, value: object) -> object:
    if spec.kind == "id":
        return _id_value(value, name)
    if spec.kind == "code":
        return _code_value(value, name)
    if spec.kind == "enum":
        assert spec.enum is not None
        return _enum_value(value, spec.enum, name)
    if spec.kind == "count":
        return _count_value(value, name)
    if spec.kind == "number":
        return _number_value(value, name)
    if spec.kind == "timestamp":
        return _timestamp_value(value, name)
    if spec.kind == "bool":
        return _bool_value(value, name)
    if spec.kind == "content_ref":
        return _content_ref_value(value, name)
    raise InvalidEventError(f"unknown field kind {spec.kind!r}")  # pragma: no cover


_WORKFLOW_SCHEMA: Mapping[str, _FieldSpec] = MappingProxyType(
    {
        "workflow_id": _FieldSpec("id", required=True),
        "task_id": _FieldSpec("id", required=True),
        "job_id": _FieldSpec("id"),
        "request_id": _FieldSpec("id"),
        "state": _FieldSpec("enum", required=True, enum=WORKFLOW_STATES),
        "timing_ms": _FieldSpec("number"),
        "cost_usd": _FieldSpec("number"),
        "error_code": _FieldSpec("code"),
        "failure_ref": _FieldSpec("content_ref"),
    }
)

_LOG_SCHEMA: Mapping[str, _FieldSpec] = MappingProxyType(
    {
        "workflow_id": _FieldSpec("id"),
        "task_id": _FieldSpec("id"),
        "job_id": _FieldSpec("id"),
        "request_id": _FieldSpec("id"),
        "level": _FieldSpec("enum", required=True, enum=LOG_LEVELS),
        "message_code": _FieldSpec("code", required=True),
        "detail_ref": _FieldSpec("content_ref"),
    }
)

_SPAN_SCHEMA: Mapping[str, _FieldSpec] = MappingProxyType(
    {
        "span_id": _FieldSpec("id", required=True),
        "parent_span_id": _FieldSpec("id"),
        "workflow_id": _FieldSpec("id"),
        "name": _FieldSpec("code", required=True),
        "duration_ms": _FieldSpec("number"),
        "status": _FieldSpec("enum", required=True, enum=SPAN_STATUSES),
        "error_code": _FieldSpec("code"),
    }
)

EVENT_SCHEMAS: Mapping[str, Mapping[str, _FieldSpec]] = MappingProxyType(
    {
        "workflow": _WORKFLOW_SCHEMA,
        "log": _LOG_SCHEMA,
        "span": _SPAN_SCHEMA,
    }
)


def emit_event(kind: object, fields: object) -> TelemetryEvent:
    """Validate `fields` against `kind`'s closed schema and return an
    immutable, content-free `TelemetryEvent`. Fail closed on anything
    unrecognised, missing or out of bound -- never drop or coerce a field.
    """
    if not isinstance(kind, str) or kind not in EVENT_SCHEMAS:
        raise InvalidEventError(f"unknown event kind {kind!r}; must be one of {EVENT_KINDS}")
    if not isinstance(fields, Mapping):
        raise InvalidEventError("fields must be a mapping")
    schema = EVENT_SCHEMAS[kind]
    validated: dict[str, object] = {}
    for name, value in fields.items():
        if not isinstance(name, str) or name not in schema:
            raise DisallowedFieldError(f"field {name!r} is not permitted on a {kind!r} event")
        validated[name] = _validate_field(name, schema[name], value)
    for name, spec in schema.items():
        if spec.required and name not in validated:
            raise DisallowedFieldError(f"required field {name!r} is missing on a {kind!r} event")
    return TelemetryEvent(kind, MappingProxyType(validated))
