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
coerced value, or a partially-built event. Error messages are constant
strings: they never echo caller-supplied text -- not a value, not an event
kind and not a field name (Section 65.4 "no secrets ... in log"); a caller
can tell refusals apart by the error class and its stable `code`.

Types are exact. A `str`/`int`/`float`/`datetime` subclass is refused
(`type(x) is str`, never `isinstance`): a subclass can lie in `__eq__`
(passing an enum check it should fail) or in `__str__`/`__repr__` (storing
one value and serialising another). `ContentRef` is re-validated at
`emit_event` and stored as a fresh copy, so a ref forged with
`object.__new__`, one whose `__post_init__` was skipped by a subclass, or
one mutated with `object.__setattr__` is refused, and a caller mutating its
own ref afterwards cannot change an emitted event.

Disclosed (shape-only): id and code values are checked for shape and bound,
not scanned for secret or PII look-alikes -- a well-formed short id that
happens to be a secret still passes.

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
_ABSENT = object()


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
    if type(value) is not str or not _ID_RE.fullmatch(value):
        raise ContentRefusedError(f"{what} is not a bounded id")
    return value


def _code_value(value: object, what: str) -> str:
    if type(value) is not str or not _CODE_RE.fullmatch(value):
        raise ContentRefusedError(f"{what} is not a bounded code")
    return value


def _enum_value(value: object, allowed: tuple[str, ...], what: str) -> str:
    # Exact-type check before membership: an unhashable value must raise
    # our own error (never a bare TypeError from `in`), and a str subclass
    # with a lying __eq__ must not pass the membership test.
    if type(value) is not str or value not in allowed:
        raise ContentRefusedError(f"{what} is not one of the closed set {allowed}")
    return value


def _count_value(value: object, what: str) -> int:
    if type(value) is not int or value < 0 or value > _MAX_NUMBER:
        raise ContentRefusedError(f"{what} is not a bounded non-negative count")
    return value


def _number_value(value: object, what: str) -> float:
    # Exact int/float only. The bound is checked on the value itself, before
    # any float() conversion: float(10**400) raises a bare OverflowError.
    # NaN fails every comparison, so `not (0 <= value <= max)` refuses it,
    # and +/-inf fall outside the bound.
    if type(value) not in (int, float) or not (0 <= value <= _MAX_NUMBER):  # type: ignore[operator]
        raise ContentRefusedError(f"{what} is not a bounded non-negative number")
    # `+ 0.0` normalises -0.0 to +0.0 (accepted, stored as +0.0).
    return float(value) + 0.0  # type: ignore[arg-type]


def _timestamp_value(value: object, what: str) -> datetime:
    if type(value) is not datetime or value.tzinfo is None or value.utcoffset() is None:
        raise ContentRefusedError(f"{what} must be a timezone-aware datetime")
    return value


def _bool_value(value: object, what: str) -> bool:
    if type(value) is not bool:
        raise ContentRefusedError(f"{what} must be a bool")
    return value


def _content_ref_value(value: object, what: str) -> ContentRef:
    if type(value) is not ContentRef:
        raise ContentRefusedError(f"{what} must be a ContentRef, never inline content")
    # Re-validate both ids: `__post_init__` only ran at construction, and a
    # frozen dataclass can still be forged (object.__new__) or mutated
    # (object.__setattr__). A fresh copy is stored, so the caller's own ref
    # cannot be mutated into the emitted event afterwards.
    ref_id = getattr(value, "ref_id", None)
    key_id = getattr(value, "encryption_key_id", None)
    return ContentRef(_id_value(ref_id, "content ref id"), _id_value(key_id, "encryption key id"))


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
    raise InvalidEventError("unknown field kind")  # pragma: no cover


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
    if type(kind) is not str or kind not in EVENT_SCHEMAS:
        raise InvalidEventError("event kind is not one of the permitted kinds")
    if not isinstance(fields, Mapping):
        raise InvalidEventError("fields must be a mapping")
    schema = EVENT_SCHEMAS[kind]
    given: dict[str, object] = {}
    for name, value in fields.items():
        if type(name) is not str or name not in schema:
            raise DisallowedFieldError("field is not permitted on this event kind")
        given[name] = value
    for name, spec in schema.items():
        if spec.required and name not in given:
            raise DisallowedFieldError(f"required field {name!r} is missing on this event kind")
    # Schema order, whatever the caller's order: same input, same event.
    validated = {
        name: _validate_field(name, spec, value)
        for name, spec in schema.items()
        if (value := given.get(name, _ABSENT)) is not _ABSENT
    }
    return TelemetryEvent(kind, MappingProxyType(validated))
