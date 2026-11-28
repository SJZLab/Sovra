"""FTF-H027 -- metadata-only workflow payloads and sanitized failures (§12, §65.2, §77.6).

Temporal stores workflow arguments, results and failures in its history, and
workflow IDs, task queues and search attributes are visible to anyone who can
read that history or its UI. §12: "jobs pass opaque references, not raw
examples ... sanitized failure conversion; keep workflow IDs, task queues,
search attributes and metric labels content-free."

This module is the one codec for what goes into that history:

* `PayloadSchema` is a closed, declared field set. Each field has one kind:
  `uuid` (canonical lowercase), `sha256` (64 lowercase hex), `count` (int
  0..10**12), `bool`, or `enum` (a value from that field's own declared set).
  There is no free-text, id-shaped, URL or bytes kind at all. A content-free
  id pattern such as FTF-0903's `id` still admits a short canary or secret
  ("CANARY-DO-NOT-USE-..."), so it is deliberately not a payload kind here.
* `encode_workflow_payload` refuses an unknown or missing field and any value
  outside its kind, then returns canonical JSON bytes. Types are exact: a
  `str`/`int` subclass or a `bool` posing as a count is refused.
  `decode_workflow_payload` re-validates bytes read back from a history, so a
  tampered or foreign payload is refused, not trusted.
* `sanitize_workflow_failure` turns any exception into
  `{"code", "correlation_id"}`. The code is the exception class's own stable
  `code` attribute when it is code-shaped, otherwise `internal_error`. The
  exception message, args, traceback and cause are never included.
* `validate_workflow_identity` admits a workflow id `<kind>-<uuid>` and a task
  queue drawn only from the caller's declared set.

Errors carry a stable `code`, and their messages never echo a value.

Disclosed: a `uuid`/`sha256` field still carries 122/256 caller-chosen bits.
A malicious worker could hide a fragment of data in it. The capacity is fixed
and bounded, and FTF-H027's history inspection scans for encoded and
fragmented canaries. This module removes every free-text channel, not every
covert one.

UNWIRED: no Temporal worker exists yet. The real history inspection over a
running Temporal server is FTF-H027-P2.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

__all__ = [
    "FIELD_KINDS",
    "WorkflowPayloadError",
    "InvalidPayloadSchemaError",
    "PayloadRefusedError",
    "InvalidWorkflowIdentityError",
    "PayloadField",
    "PayloadSchema",
    "encode_workflow_payload",
    "decode_workflow_payload",
    "sanitize_workflow_failure",
    "validate_workflow_identity",
]

# Kind -> whether it declares its own value set.
_DECLARES_VALUES: Mapping[str, bool] = MappingProxyType(
    {"uuid": False, "sha256": False, "count": False, "bool": False, "enum": True}
)
FIELD_KINDS: tuple[str, ...] = tuple(_DECLARES_VALUES)

_CODE_RE = re.compile(r"[a-z][a-z0-9_]{0,63}")
_QUEUE_RE = re.compile(r"[a-z][a-z0-9-]{0,62}")
_SHA256_RE = re.compile(r"[0-9a-f]{64}")
_UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
_WORKFLOW_ID_RE = re.compile(
    r"([a-z][a-z0-9_]{0,40})-([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})"
)
_MAX_COUNT = 10**12
_MAX_FIELDS = 32
_MAX_ENUM_VALUES = 64
_MAX_PAYLOAD_BYTES = 16_384
_PAYLOAD_VERSION = 1


class WorkflowPayloadError(Exception):
    """Base. Every subclass carries a stable `code`, and messages never echo a value."""

    code = "workflow_payload_error"


class InvalidPayloadSchemaError(WorkflowPayloadError):
    code = "workflow_payload_schema_invalid"


class PayloadRefusedError(WorkflowPayloadError):
    """A value is not one of its field's permitted, content-free shapes."""

    code = "workflow_payload_refused"


class InvalidWorkflowIdentityError(WorkflowPayloadError):
    code = "workflow_identity_invalid"


@dataclass(frozen=True)
class PayloadField:
    kind: str
    required: bool = True
    values: tuple[str, ...] = ()


@dataclass(frozen=True)
class PayloadSchema:
    name: str
    fields: Mapping[str, PayloadField]

    def __post_init__(self) -> None:
        if type(self.name) is not str or not _CODE_RE.fullmatch(self.name):
            raise InvalidPayloadSchemaError("schema name must be code-shaped")
        if not isinstance(self.fields, Mapping) or not 0 < len(self.fields) <= _MAX_FIELDS:
            raise InvalidPayloadSchemaError("schema must declare 1..32 fields")
        frozen: dict[str, PayloadField] = {}
        for key, spec in self.fields.items():
            if type(key) is not str or not _CODE_RE.fullmatch(key):
                raise InvalidPayloadSchemaError("field names must be code-shaped")
            if type(spec) is not PayloadField:
                raise InvalidPayloadSchemaError("every field must be a PayloadField")
            if type(spec.kind) is not str or spec.kind not in FIELD_KINDS:
                raise InvalidPayloadSchemaError("field kind is not a permitted kind")
            if type(spec.required) is not bool:
                raise InvalidPayloadSchemaError("required must be a bool")
            if type(spec.values) is not tuple:
                raise InvalidPayloadSchemaError("enum values must be a tuple")
            if _DECLARES_VALUES[spec.kind]:
                if not 0 < len(spec.values) <= _MAX_ENUM_VALUES:
                    raise InvalidPayloadSchemaError("an enum field needs 1..64 values")
                if len(set(spec.values)) != len(spec.values) or any(
                    type(v) is not str or not _CODE_RE.fullmatch(v) for v in spec.values
                ):
                    raise InvalidPayloadSchemaError("enum values must be unique codes")
            elif spec.values:
                raise InvalidPayloadSchemaError("only an enum field declares values")
            frozen[key] = PayloadField(spec.kind, spec.required, spec.values)
        object.__setattr__(self, "fields", MappingProxyType(frozen))


def _bool_value(spec: PayloadField, value: object) -> object:
    if type(value) is not bool:
        raise PayloadRefusedError("value is not a bool")
    return value


def _count_value(spec: PayloadField, value: object) -> object:
    if type(value) is not int or not 0 <= value <= _MAX_COUNT:
        raise PayloadRefusedError("value is not a bounded count")
    return value


def _pattern_value(pattern: re.Pattern[str], value: object) -> object:
    if type(value) is not str or not pattern.fullmatch(value):
        raise PayloadRefusedError("value does not have the field's shape")
    return value


def _enum_value(spec: PayloadField, value: object) -> object:
    if type(value) is not str or value not in spec.values:
        raise PayloadRefusedError("value is not one of the field's declared values")
    return value


_CHECKS = MappingProxyType(
    {
        "uuid": lambda spec, value: _pattern_value(_UUID_RE, value),
        "sha256": lambda spec, value: _pattern_value(_SHA256_RE, value),
        "count": _count_value,
        "bool": _bool_value,
        "enum": _enum_value,
    }
)


def _value(spec: PayloadField, value: object) -> object:
    return _CHECKS[spec.kind](spec, value)


def _checked_schema(schema: object) -> PayloadSchema:
    if type(schema) is not PayloadSchema:
        raise InvalidPayloadSchemaError("schema must be a PayloadSchema")
    return schema


def _validated(schema: PayloadSchema, fields: object) -> dict[str, object]:
    if type(fields) is not dict:
        raise PayloadRefusedError("fields must be a dict")
    out: dict[str, object] = {}
    for key, value in fields.items():
        if type(key) is not str or key not in schema.fields:
            raise PayloadRefusedError("field is not declared by the schema")
        out[key] = _value(schema.fields[key], value)
    required = {key for key, spec in schema.fields.items() if spec.required}
    if not required.issubset(out):
        raise PayloadRefusedError("a required field is missing")
    return out


def encode_workflow_payload(schema: PayloadSchema, fields: Mapping[str, object]) -> bytes:
    """Canonical JSON bytes holding only declared, content-free values."""
    checked = _checked_schema(schema)
    values = _validated(checked, fields)
    document = {"schema": checked.name, "v": _PAYLOAD_VERSION, "fields": values}
    return json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode(
        "ascii"
    )


def decode_workflow_payload(schema: PayloadSchema, data: bytes) -> Mapping[str, object]:
    """Re-validate a payload read back from a history. Fail closed."""
    checked = _checked_schema(schema)
    if type(data) is not bytes or not data or len(data) > _MAX_PAYLOAD_BYTES:
        raise PayloadRefusedError("payload must be bounded bytes")
    try:
        document = json.loads(data.decode("ascii"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise PayloadRefusedError("payload is not canonical JSON") from exc
    if type(document) is not dict or "fields" not in document:
        raise PayloadRefusedError("payload envelope is malformed")
    values = _validated(checked, document["fields"])
    # Re-encoding binds the schema name, version and envelope keys: another
    # schema, another version, an extra key or other spacing all differ.
    if encode_workflow_payload(checked, values) != data:
        raise PayloadRefusedError("payload is not canonical for this schema")
    return MappingProxyType(values)


def _uuid_text(value: object, what: str) -> str:
    if type(value) is not str or not _UUID_RE.fullmatch(value):
        raise InvalidWorkflowIdentityError(f"{what} must be a canonical uuid")
    return value


def sanitize_workflow_failure(error: BaseException, *, correlation_id: str) -> bytes:
    """The only failure shape a workflow history ever holds: a stable code and a
    correlation id. Message, args, traceback and cause are dropped."""
    if not isinstance(error, BaseException):
        raise PayloadRefusedError("error must be an exception")
    correlation = _uuid_text(correlation_id, "correlation_id")
    code = getattr(type(error), "code", None)
    if type(code) is not str or not _CODE_RE.fullmatch(code):
        code = "internal_error"
    document = {"code": code, "correlation_id": correlation}
    return json.dumps(document, sort_keys=True, separators=(",", ":")).encode("ascii")


def validate_workflow_identity(
    workflow_id: object, task_queue: object, allowed_task_queues: frozenset[str]
) -> tuple[str, str]:
    """`<kind>-<uuid>` workflow id and a task queue from a declared set only."""
    if (
        type(allowed_task_queues) is not frozenset
        or not allowed_task_queues
        or any(type(q) is not str or not _QUEUE_RE.fullmatch(q) for q in allowed_task_queues)
    ):
        raise InvalidWorkflowIdentityError("allowed task queues must be declared codes")
    if type(workflow_id) is not str or not _WORKFLOW_ID_RE.fullmatch(workflow_id):
        raise InvalidWorkflowIdentityError("workflow id must be <kind>-<uuid>")
    if type(task_queue) is not str or task_queue not in allowed_task_queues:
        raise InvalidWorkflowIdentityError("task queue is not a declared queue")
    return workflow_id, task_queue
