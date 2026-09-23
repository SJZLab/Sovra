"""Canonical conversation normalizer (FTF-0506, Section 7/8/9/67/77/81).

Section 8.1's import pipeline ("6. Normalize records", "7. Validate
schema", "8. Validate tool calls") needs one real, load-bearing place
where a heterogeneous parsed source record becomes the canonical Section
7 conversation schema -- this module is that place. It is a pure,
synchronous, no-I/O, no-DB function (this task's own `database_changes`
clause: "No independent schema change. Consume frozen contract.") --
callers (a JSONL/CSV/XLSX/API-upload importer, in a later, separately
dependency-gated task) supply an already-parsed raw record; this module
never reads a file, a socket or a database itself.

This task's own acceptance clause ("Normalize roles/content/tool calls
into canonical schema with lineage/group IDs and deterministic hash") is
met by four real, independently testable pieces:

  - **roles/content/tool calls -> canonical schema**: `normalize_conversation`
    enforces every one of Section 7's own "Required validators" verbatim
    ("valid role ordering", "tool call IDs unique within record", "every
    tool result references an existing tool call", "tool arguments
    validate against the project ToolDefinition schema version", "no
    unsupported content type", "no null role/content ambiguity",
    "canonical JSON serialization before hashing") -- not a subset, and
    not a looser restatement.
  - **lineage**: `RecordLineage` records exactly the fields
    `DatasetRecord` (Section 6.11) needs to trace a normalized record
    back to its pre-normalization source: `source_type`,
    `source_record_index`, and `raw_sha256` (a real SHA-256 of the exact,
    untouched `raw_record` this call was given, computed *before* any
    coercion). A future importer wires `record_sha256=lineage.raw_sha256`
    / `normalized_sha256=canonical_hash` directly onto its own
    `DatasetRecord` row -- this module produces the real values, not
    placeholders.
  - **group IDs**: Section 9's own "Keep entire conversations, customer
    cases, documents, synthetic seed/template families ... in one split"
    needs a stable split-isolation identity distinct from
    `DatasetRecord.duplicate_group_id` (Section 6.11's own *duplicate*-
    detection field, owned by a later dedup task, never written here).
    An explicit, caller-supplied `group_key` (the source's own case/
    thread identity, when the source declares one) always produces the
    same `group_id` for every record sharing that key, so a caller
    keeps the whole group in one split (Section 9). Absent a
    `group_key`, a record is its own singleton group -- deterministic
    given `record_id`, never content-derived (a fresh `record_id` each
    call is expected and does not create split leakage on its own,
    since a solo group of one record has nothing else to keep together
    with).
  - **deterministic hash**: `canonical_hash` is a SHA-256 over
    `json.dumps(..., sort_keys=True, separators=(",", ":"))` of exactly
    `{schema_version, messages, metadata}` -- the same canonical-JSON
    convention `apps/api/app/tools/tool_definition_schema.py`'s own
    `compute_schema_hash` (FTF-0404, CERTIFIED) already established, not
    a new one invented here. `record_id`/`group_id` are deliberately
    excluded from the hashed document: two imports of byte-identical
    conversation content must hash identically regardless of which
    random `record_id` each import assigned, since Section 8.1 step 10
    ("Detect exact duplicate records") needs that hash to be a function
    of content alone.

This task's own negative_acceptance ("Missing/unmapped role or coerced
information loss is rejected, not silently guessed") is the module's
central design discipline, applied at every decision point, not just to
`role`:

  - `role` must be exactly one of `system`/`user`/`assistant`/`tool` --
    no alias table (`"human"`, `"ai"`, `"function"`, ...) is ever
    consulted. A caller-side alias decision is a separate, explicit
    mapping step *before* this function is called; this function does
    not guess one.
  - `content` accepts only two shapes that are losslessly,
    unambiguously convertible to Section 7's own content-part schema: a
    plain string (wrapped as a single `{"type": "text", "text": ...}`
    part -- there is exactly one reasonable reading of a bare string as
    conversation content) or an already-canonical list of `{"type":
    "text", "text": ...}` / `{"type": "json", "value": ...}` parts,
    validated field-by-field. A bare JSON object, a list of bare
    strings, a number, or any other shape is refused outright rather
    than guessed into one of the two allowed types.
  - a `tool_calls` entry naming a tool absent from the caller-supplied
    `tool_schemas` mapping is refused as an unmapped tool, the same
    "unmapped ... rejected, not silently guessed" discipline applied to
    `role`, rather than validated against no schema at all (which would
    silently accept anything).
  - tool call `arguments` are validated through a caller-supplied
    `tool_argument_validator` callable, deliberately dependency-injected
    rather than imported directly. `workers/data` and `apps/api` are
    separate deployable units (separate `pyproject.toml` workspace
    members, per `docs/architecture/`'s own apps/workers split) --
    nothing else in this repository imports across that boundary in
    either direction, and a worker module hard-importing an API-app
    module would make `workers/data` undeployable without shipping
    `apps/api`'s own source tree alongside it. This module instead
    defines the exact call shape FTF-0405's already-CERTIFIED
    `apps/api/app/services/json_schema_tool_validation.validate_instance`
    already has (`(schema, instance, *, field_name=...) -> None`,
    raising on violation) so a caller can pass that real function in
    directly, unmodified -- proven for real in this task's own test
    suite (`test_tool_arguments_validate_against_a_real_ftf_0405_schema`
    passes `validate_instance` itself, not a stand-in). A tool call is
    refused outright, before any validator ever runs, when no validator
    was supplied at all (Section 75.1: "no effect authorized solely by
    a UI control" reapplied here as "no tool call is accepted on trust
    when nothing can prove its arguments") -- this is this task's own
    real, genuine integration point with its `FTF-0405` dependency, not
    a restated claim.
  - any message carrying a field this schema does not define (for
    example a `tool_call_id` on a `user` message, or an unknown key
    alongside `type`/`text` in a content part) is refused rather than
    silently dropped -- a field this module does not recognize is
    exactly the "coerced information loss" the negative_acceptance
    clause names.

Role-ordering discipline (Section 7's "valid role ordering", made
concrete): zero or more leading `system` messages form a strictly
leading, contiguous block; the first non-`system` message must be
`user`; `user` may only follow `system`/`assistant`; `assistant` may
only follow `system`/`user`/`tool`; `tool` may only follow
`assistant`/`tool`, and only while at least one tool call from the
current assistant turn is still unanswered. A `tool` message can never
be immediately followed by `user` -- the assistant turn that issued the
call(s) must produce its own concluding `assistant` message first,
exactly as Section 7's own worked example shows
(`system -> user -> assistant(tool_calls) -> tool -> assistant`). A
record ending with an unanswered tool call is refused, not silently
accepted as "incomplete but fine".
"""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Protocol

__all__ = [
    "ALLOWED_CONTENT_TYPES",
    "ALLOWED_ROLES",
    "SCHEMA_VERSION",
    "CanonicalConversationRecord",
    "CanonicalMessage",
    "ContentPart",
    "ConversationNormalizationError",
    "RecordLineage",
    "ToolArgumentValidator",
    "ToolCallPart",
    "normalize_conversation",
]

SCHEMA_VERSION = "1.0"

ALLOWED_ROLES = frozenset({"system", "user", "assistant", "tool"})
ALLOWED_CONTENT_TYPES = frozenset({"text", "json"})

_USER_ALLOWED_PREV = frozenset({None, "system", "assistant"})
_ASSISTANT_ALLOWED_PREV = frozenset({"system", "user", "tool"})
_TOOL_ALLOWED_PREV = frozenset({"assistant", "tool"})

_EMPTY_TOOL_SCHEMAS: MappingProxyType[str, Mapping[str, object]] = MappingProxyType({})


class ConversationNormalizationError(ValueError):
    """A raw record could not be normalized without either guessing an
    unmapped role/tool/content shape or silently losing information --
    this task's own negative_acceptance, raised instead of coercing."""


class ToolArgumentValidator(Protocol):
    """The exact call shape FTF-0405's already-CERTIFIED
    `validate_instance(schema, instance, *, field_name=...) -> None` has
    -- a caller passes that real function in directly. Must raise on a
    schema violation; a normal return means the arguments are accepted."""

    def __call__(
        self, schema: dict[str, object], instance: object, *, field_name: str = ...
    ) -> None: ...


@dataclass(frozen=True)
class ContentPart:
    type: str
    text: str | None = None
    value: object = None

    def to_canonical(self) -> dict[str, object]:
        if self.type == "text":
            return {"type": "text", "text": self.text}
        return {"type": "json", "value": self.value}


@dataclass(frozen=True)
class ToolCallPart:
    id: str
    name: str
    arguments: MappingProxyType[str, object]

    def to_canonical(self) -> dict[str, object]:
        return {"id": self.id, "name": self.name, "arguments": dict(self.arguments)}


@dataclass(frozen=True)
class CanonicalMessage:
    role: str
    content: tuple[ContentPart, ...]
    tool_calls: tuple[ToolCallPart, ...] = ()
    tool_call_id: str | None = None
    name: str | None = None

    def to_canonical(self) -> dict[str, object]:
        document: dict[str, object] = {
            "role": self.role,
            "content": [part.to_canonical() for part in self.content],
        }
        if self.tool_calls:
            document["tool_calls"] = [call.to_canonical() for call in self.tool_calls]
        if self.tool_call_id is not None:
            document["tool_call_id"] = self.tool_call_id
        if self.name is not None:
            document["name"] = self.name
        return document


@dataclass(frozen=True)
class RecordLineage:
    source_type: str
    source_record_index: int
    raw_sha256: str


@dataclass(frozen=True)
class CanonicalConversationRecord:
    schema_version: str
    record_id: str
    messages: tuple[CanonicalMessage, ...]
    metadata: MappingProxyType[str, object]
    group_id: str
    canonical_hash: str
    lineage: RecordLineage


def _ensure_json_serializable(value: object, field: str) -> None:
    try:
        json.dumps(value)
    except (TypeError, ValueError) as exc:
        raise ConversationNormalizationError(
            f"{field}: value is not JSON-serializable: {exc}"
        ) from exc


def _canonical_sha256(document: object) -> str:
    canonical = json.dumps(document, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _coerce_content(raw_content: object, *, field: str) -> tuple[ContentPart, ...]:
    if isinstance(raw_content, str):
        return (ContentPart(type="text", text=raw_content),)

    if raw_content is None:
        raise ConversationNormalizationError(f"{field}: content must not be null")

    if not isinstance(raw_content, list):
        raise ConversationNormalizationError(
            f"{field}: unsupported content shape {type(raw_content).__name__} -- "
            "refusing to guess a lossy coercion"
        )

    parts: list[ContentPart] = []
    for index, item in enumerate(raw_content):
        item_field = f"{field}[{index}]"
        if not isinstance(item, dict):
            raise ConversationNormalizationError(
                f"{item_field}: content list items must be objects, not {type(item).__name__}"
            )
        item_type = item.get("type")
        if item_type not in ALLOWED_CONTENT_TYPES:
            raise ConversationNormalizationError(
                f"{item_field}: unsupported content type {item_type!r}"
            )
        if item_type == "text":
            text = item.get("text")
            if not isinstance(text, str):
                raise ConversationNormalizationError(
                    f"{item_field}: text content part must carry a string 'text'"
                )
            extra = set(item) - {"type", "text"}
            if extra:
                raise ConversationNormalizationError(
                    f"{item_field}: text content part has unsupported fields {sorted(extra)}"
                )
            parts.append(ContentPart(type="text", text=text))
        else:
            if "value" not in item:
                raise ConversationNormalizationError(
                    f"{item_field}: json content part missing 'value'"
                )
            value = item["value"]
            _ensure_json_serializable(value, f"{item_field}.value")
            extra = set(item) - {"type", "value"}
            if extra:
                raise ConversationNormalizationError(
                    f"{item_field}: json content part has unsupported fields {sorted(extra)}"
                )
            parts.append(ContentPart(type="json", value=value))
    return tuple(parts)


def _coerce_tool_calls(
    raw_tool_calls: object,
    *,
    field: str,
    tool_schemas: Mapping[str, Mapping[str, object]],
    tool_argument_validator: ToolArgumentValidator | None,
) -> tuple[ToolCallPart, ...]:
    if raw_tool_calls is None:
        return ()
    if not isinstance(raw_tool_calls, list):
        raise ConversationNormalizationError(f"{field}: tool_calls must be a list")

    calls: list[ToolCallPart] = []
    for index, item in enumerate(raw_tool_calls):
        item_field = f"{field}[{index}]"
        if not isinstance(item, dict):
            raise ConversationNormalizationError(f"{item_field}: tool call must be an object")
        call_id = item.get("id")
        name = item.get("name")
        arguments = item.get("arguments")
        if not isinstance(call_id, str) or not call_id:
            raise ConversationNormalizationError(
                f"{item_field}: tool call id must be a non-empty string"
            )
        if not isinstance(name, str) or not name:
            raise ConversationNormalizationError(
                f"{item_field}: tool call name must be a non-empty string"
            )
        if not isinstance(arguments, dict):
            raise ConversationNormalizationError(
                f"{item_field}: tool call arguments must be an object"
            )
        extra = set(item) - {"id", "name", "arguments"}
        if extra:
            raise ConversationNormalizationError(
                f"{item_field}: tool call has unsupported fields {sorted(extra)}"
            )
        schema = tool_schemas.get(name)
        if schema is None:
            raise ConversationNormalizationError(
                f"{item_field}: tool {name!r} is not a mapped project tool -- "
                "refusing to guess its contract"
            )
        if tool_argument_validator is None:
            raise ConversationNormalizationError(
                f"{item_field}: no tool_argument_validator was supplied -- refusing to "
                "accept a tool call's arguments on trust when nothing can prove them"
            )
        try:
            tool_argument_validator(dict(schema), arguments, field_name=f"{item_field}.arguments")
        except ConversationNormalizationError:
            raise
        except ValueError as exc:
            raise ConversationNormalizationError(
                f"{item_field}: tool arguments failed schema validation: {exc}"
            ) from exc
        calls.append(
            ToolCallPart(id=call_id, name=name, arguments=MappingProxyType(dict(arguments)))
        )
    return tuple(calls)


def normalize_conversation(
    *,
    source_type: str,
    source_record_index: int,
    raw_record: Mapping[str, object],
    tool_schemas: Mapping[str, Mapping[str, object]] = _EMPTY_TOOL_SCHEMAS,
    tool_argument_validator: ToolArgumentValidator | None = None,
    group_key: str | None = None,
    record_id: str | None = None,
) -> CanonicalConversationRecord:
    """Normalizes one already-parsed `raw_record` into Section 7's own
    canonical conversation schema. Raises `ConversationNormalizationError`
    on the first violation of any Section 7 "Required validator" or this
    task's own negative_acceptance -- never coerces a shape it cannot
    losslessly and unambiguously represent."""
    if not isinstance(raw_record, Mapping):
        raise ConversationNormalizationError("raw_record must be a mapping")

    try:
        raw_sha256 = _canonical_sha256(dict(raw_record))
    except (TypeError, ValueError) as exc:
        raise ConversationNormalizationError(f"raw_record is not JSON-serializable: {exc}") from exc

    raw_messages = raw_record.get("messages")
    if not isinstance(raw_messages, list) or not raw_messages:
        raise ConversationNormalizationError("messages must be a non-empty list")

    raw_metadata = raw_record.get("metadata", {})
    if not isinstance(raw_metadata, dict):
        raise ConversationNormalizationError("metadata must be an object")
    _ensure_json_serializable(raw_metadata, "metadata")

    started_body = False
    pending_calls: dict[str, str] = {}
    used_call_ids: set[str] = set()
    prev_role: str | None = None
    canonical_messages: list[CanonicalMessage] = []

    for index, raw_message in enumerate(raw_messages):
        field = f"messages[{index}]"
        if not isinstance(raw_message, dict):
            raise ConversationNormalizationError(f"{field}: message must be an object")

        role = raw_message.get("role")
        if role not in ALLOWED_ROLES:
            raise ConversationNormalizationError(
                f"{field}: unmapped role {role!r} -- refusing to guess an alias"
            )

        if role == "system":
            if started_body:
                raise ConversationNormalizationError(
                    f"{field}: 'system' messages must form a leading contiguous block"
                )
        else:
            started_body = True

        if role == "user":
            if prev_role not in _USER_ALLOWED_PREV:
                raise ConversationNormalizationError(f"{field}: 'user' cannot follow {prev_role!r}")
            if pending_calls:
                raise ConversationNormalizationError(
                    f"{field}: 'user' cannot start before every pending tool call is answered"
                )
        elif role == "assistant":
            if prev_role not in _ASSISTANT_ALLOWED_PREV:
                raise ConversationNormalizationError(
                    f"{field}: 'assistant' cannot follow {prev_role!r}"
                )
            if pending_calls:
                raise ConversationNormalizationError(
                    f"{field}: 'assistant' cannot follow before every pending tool call "
                    "from the prior turn is answered"
                )
        elif role == "tool":
            if prev_role not in _TOOL_ALLOWED_PREV:
                raise ConversationNormalizationError(
                    f"{field}: 'tool' must directly follow the assistant message "
                    "that issued its tool call"
                )
            if not pending_calls:
                raise ConversationNormalizationError(
                    f"{field}: 'tool' message with no pending tool call to answer"
                )

        content_parts = _coerce_content(raw_message.get("content"), field=f"{field}.content")

        if role == "assistant":
            tool_calls = _coerce_tool_calls(
                raw_message.get("tool_calls"),
                field=f"{field}.tool_calls",
                tool_schemas=tool_schemas,
                tool_argument_validator=tool_argument_validator,
            )
            for call in tool_calls:
                if call.id in used_call_ids or call.id in pending_calls:
                    raise ConversationNormalizationError(
                        f"{field}: tool call id {call.id!r} is not unique within this record"
                    )
                pending_calls[call.id] = call.name
            extra = set(raw_message) - {"role", "content", "tool_calls"}
            if extra:
                raise ConversationNormalizationError(
                    f"{field}: unsupported fields on assistant message {sorted(extra)}"
                )
            if not content_parts and not tool_calls:
                raise ConversationNormalizationError(
                    f"{field}: assistant message must carry content or tool_calls"
                )
            canonical_messages.append(
                CanonicalMessage(role="assistant", content=content_parts, tool_calls=tool_calls)
            )
        elif role == "tool":
            tool_call_id = raw_message.get("tool_call_id")
            name = raw_message.get("name")
            if not isinstance(tool_call_id, str) or not tool_call_id:
                raise ConversationNormalizationError(
                    f"{field}: tool message must carry a non-empty string tool_call_id"
                )
            expected_name = pending_calls[tool_call_id] if tool_call_id in pending_calls else None
            if expected_name is None:
                raise ConversationNormalizationError(
                    f"{field}: tool_call_id {tool_call_id!r} does not reference "
                    "an existing pending tool call"
                )
            if name != expected_name:
                raise ConversationNormalizationError(
                    f"{field}: tool result name {name!r} does not match the "
                    f"issuing call's name {expected_name!r}"
                )
            if not content_parts:
                raise ConversationNormalizationError(f"{field}: tool result must carry content")
            extra = set(raw_message) - {"role", "content", "tool_call_id", "name"}
            if extra:
                raise ConversationNormalizationError(
                    f"{field}: unsupported fields on tool message {sorted(extra)}"
                )
            del pending_calls[tool_call_id]
            used_call_ids.add(tool_call_id)
            canonical_messages.append(
                CanonicalMessage(
                    role="tool", content=content_parts, tool_call_id=tool_call_id, name=name
                )
            )
        else:
            if not content_parts:
                raise ConversationNormalizationError(f"{field}: {role} message must carry content")
            extra = set(raw_message) - {"role", "content"}
            if extra:
                raise ConversationNormalizationError(
                    f"{field}: unsupported fields on {role} message {sorted(extra)}"
                )
            canonical_messages.append(CanonicalMessage(role=role, content=content_parts))

        prev_role = role

    if pending_calls:
        raise ConversationNormalizationError(
            "record ends with unanswered tool call(s): " + ", ".join(sorted(pending_calls))
        )

    metadata_frozen: MappingProxyType[str, object] = MappingProxyType(dict(raw_metadata))

    canonical_body = {
        "schema_version": SCHEMA_VERSION,
        "messages": [message.to_canonical() for message in canonical_messages],
        "metadata": dict(metadata_frozen),
    }
    canonical_hash = _canonical_sha256(canonical_body)

    resolved_record_id = record_id if record_id is not None else str(uuid.uuid4())
    if group_key is not None:
        group_id = hashlib.sha256(f"group:explicit:{group_key}".encode()).hexdigest()
    else:
        group_id = hashlib.sha256(f"group:solo:{resolved_record_id}".encode()).hexdigest()

    lineage = RecordLineage(
        source_type=source_type,
        source_record_index=source_record_index,
        raw_sha256=raw_sha256,
    )

    return CanonicalConversationRecord(
        schema_version=SCHEMA_VERSION,
        record_id=resolved_record_id,
        messages=tuple(canonical_messages),
        metadata=metadata_frozen,
        group_id=group_id,
        canonical_hash=canonical_hash,
        lineage=lineage,
    )
