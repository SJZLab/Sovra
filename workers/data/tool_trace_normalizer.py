"""Tool-trace normalizer (FTF-0507, Section 7/8-10/67/77/81).

Section 67.1's "Agent traces / production conversations" connector row and
Section 8's "canonical agent trace format" v1 source type both name traces
as a real, distinct import source -- but neither the frozen `05-domain`
contract (`packages/contracts/jsonschema/05-domain.schema.json`, which only
fixes the enum value `agent_traces`/`production_conversations`) nor Section
7 (which defines the canonical *conversation* schema, not a trace *wire*
schema) pins an exact trace wire format: Section 67.1's own row says a
trace source's schema is *declared per source*, resolved through the
Section 67.2 field-mapping wizard. This module is the real, load-bearing
place that wizard output lands: a `session_id`-scoped, `sequence`-ordered
list of already-field-mapped trace events becomes one Section 7 canonical
`CanonicalConversationRecord` -- composing FTF-0506's already-CERTIFIED
`normalize_conversation` for every Section 7 "Required validator" it
already enforces (role ordering, tool call ID uniqueness, orphan tool
results, unmapped tools, unsupported content/fields, JSON-serializable
metadata) rather than reimplementing any of it, per this repo's own
reuse-first discipline (77.1: "Reuse these engines ... rather than
building competing ... engines" applies just as much to a sibling
already-CERTIFIED module as to the seven external components).

This task's own acceptance clause ("Convert approved trace messages, tool
IDs/results and outcomes preserving ordering and actual permissions") adds
exactly two things FTF-0506 has no concept of, and this module owns both
for real:

  - **ordering**: a trace batch is not guaranteed to arrive in
    conversation order (Section 67.1: "changes during pagination cannot
    silently mix versions" -- the same discipline applies to event
    arrival order over an async/networked trace export). Every event
    declares its own `sequence` (a non-bool `int`); this module sorts by
    it before building the Section 7 `messages` array, rejects a
    duplicate `sequence` value as ambiguous ordering, and separately
    records `trace_raw_sha256` -- a real SHA-256 over the exact
    caller-given `events` list *before* sorting -- into the output
    record's `metadata`, so the literal as-received batch stays
    independently verifiable even after this module reorders it.
  - **actual permissions**: Section 7A.6's "tool side-effect/confirmation
    policy" and Section 84.4's "no effect authorized solely by a UI
    control" both name tool-call authorization as a real fact that must
    never be assumed. A trace's own assistant `tool_calls` entries must
    each declare an explicit boolean `authorized` (whether that call was
    actually allowed to execute, as observed in the source system) --
    never guessed, never defaulted to `True`. `authorized` is stripped
    before the call is handed to FTF-0506 (whose own canonical
    `ToolCallPart` has no such field, and correctly rejects unrecognized
    ones) and instead collected into `metadata["tool_call_authorizations"]`
    (`{call_id: bool}`), a real, disclosed, JSON-serializable side channel
    -- not lost, not silently dropped onto the floor.

This task's own negative_acceptance ("Orphan/duplicate call IDs, malformed
tool result and mixed-session traces cannot be accepted") is met two ways:

  - **orphan/duplicate call IDs, malformed tool result**: delegated
    directly to FTF-0506's own already-proven guards (an orphan
    `tool_call_id` fails "does not reference an existing pending tool
    call"; a duplicate `tool_call_id` fails "not unique within this
    record"; a tool result naming the wrong tool or missing required
    fields fails FTF-0506's own tool-message validation) -- proven here
    end-to-end through this module's own real event-shaped input, not
    re-asserted against FTF-0506 directly a second time. A trace-level
    duplicate `tool_call_id` is additionally caught by this module's own
    earlier, trace-specific pass (before FTF-0506 ever sees it) so the
    rejection reason names the trace's own event, not FTF-0506's internal
    per-record bookkeeping.
  - **mixed-session traces**: FTF-0506 has no notion of "session" at all
    (its `group_key` is caller-supplied and untyped). This module owns
    this check for real: every event must declare the caller's own
    `session_id` exactly; an event declaring a different one is refused
    outright as a mixed-session trace, rather than silently merged into
    one conversation that never actually happened as a single session
    (a real correctness/tenant-adjacent hazard, not a cosmetic one --
    Section 84.4's "tenant/purpose/privacy/least-privilege checks precede
    each effect" reapplied to session identity for trace ingestion).

Scope decision, same disclosed precedent as FTF-0506/FTF-1302/FTF-1303/
FTF-1304 ("no live caller yet"): this task's own packet `files` list names
only this module and its test, not an importer/route. No trace-source
importer exists yet in this repository to call `normalize_tool_trace`
(Section 67.1's actual REST/trace connector is a separate, later,
dependency-gated task). `proving_symbol` stays blank; `product_truth_check.py`'s
product-true count is deliberately unchanged.

Path refinement recorded before READY (Section 84.3), same as FTF-0506:
the packet names `services/training-worker/data/tool_trace_normalizer.py`,
but this repo's real `SOURCE_ROOTS` (FTF-0001, PR #10) name `workers/data`
as the real data/review worker root -- there is no `services/` tree in
this repository. Placed alongside FTF-0506's own module instead of
creating a second, non-existent tree.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from typing import Any

from workers.data.canonical_conversation_normalizer import (
    CanonicalConversationRecord,
    ConversationNormalizationError,
    ToolArgumentValidator,
    normalize_conversation,
)

__all__ = [
    "RESERVED_METADATA_KEYS",
    "ToolTraceNormalizationError",
    "normalize_tool_trace",
]

_EMPTY_TOOL_SCHEMAS: Mapping[str, Mapping[str, object]] = {}

_AUTHORIZATIONS_KEY = "tool_call_authorizations"
_TRACE_HASH_KEY = "trace_raw_sha256"
RESERVED_METADATA_KEYS = frozenset({_AUTHORIZATIONS_KEY, _TRACE_HASH_KEY})


class ToolTraceNormalizationError(ConversationNormalizationError):
    """A raw trace event batch could not be normalized without either
    guessing session membership/ordering/permission or silently losing
    information -- this task's own negative_acceptance, raised instead of
    coercing. Subclasses `ConversationNormalizationError` so a caller
    catching FTF-0506's own exception type still catches this one; a
    caller that wants to distinguish this task's own trace-specific
    rejections from FTF-0506's delegated ones can catch this subclass
    directly."""


def _canonical_sha256(document: object) -> str:
    canonical = json.dumps(document, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _ensure_json_serializable(value: object, field: str) -> None:
    try:
        json.dumps(value)
    except (TypeError, ValueError) as exc:
        raise ToolTraceNormalizationError(
            f"{field}: value is not JSON-serializable: {exc}"
        ) from exc


def normalize_tool_trace(
    *,
    source_type: str,
    source_record_index: int,
    session_id: str,
    events: Sequence[Mapping[str, object]],
    tool_schemas: Mapping[str, Mapping[str, object]] = _EMPTY_TOOL_SCHEMAS,
    tool_argument_validator: ToolArgumentValidator | None = None,
    metadata: Mapping[str, object] | None = None,
    record_id: str | None = None,
) -> CanonicalConversationRecord:
    """Normalizes one `session_id`-scoped batch of already-field-mapped
    raw trace `events` into a single Section 7 canonical
    `CanonicalConversationRecord`, via FTF-0506's already-CERTIFIED
    `normalize_conversation`. Raises `ToolTraceNormalizationError` (a
    `ConversationNormalizationError` subclass) on any violation of this
    task's own negative_acceptance; propagates FTF-0506's own
    `ConversationNormalizationError` unchanged for any Section 7
    "Required validator" violation this module delegates to it."""
    if not isinstance(session_id, str) or not session_id:
        raise ToolTraceNormalizationError("session_id must be a non-empty string")

    if not isinstance(events, Sequence) or isinstance(events, str | bytes) or not events:
        raise ToolTraceNormalizationError("events must be a non-empty sequence")

    trace_raw_sha256 = _canonical_sha256(
        [dict(event) if isinstance(event, Mapping) else event for event in events]
    )

    if metadata is not None and not isinstance(metadata, Mapping):
        raise ToolTraceNormalizationError("metadata must be an object")
    reserved_collision = RESERVED_METADATA_KEYS & set(metadata or {})
    if reserved_collision:
        raise ToolTraceNormalizationError(
            f"metadata uses reserved key(s) {sorted(reserved_collision)} -- "
            "this module writes them itself and refuses to silently overwrite a "
            "caller-supplied value under the same name"
        )
    _ensure_json_serializable(dict(metadata or {}), "metadata")

    seen_sequences: set[int] = set()
    authorizations: dict[str, bool] = {}
    indexed_events: list[tuple[int, Mapping[str, object]]] = []

    for raw_index, event in enumerate(events):
        field = f"events[{raw_index}]"
        if not isinstance(event, Mapping):
            raise ToolTraceNormalizationError(f"{field}: trace event must be an object")

        event_session_id = event.get("session_id")
        if event_session_id != session_id:
            raise ToolTraceNormalizationError(
                f"{field}: declares session_id {event_session_id!r}, expected "
                f"{session_id!r} -- mixed-session traces cannot be accepted"
            )

        raw_sequence = event.get("sequence")
        if not isinstance(raw_sequence, int) or isinstance(raw_sequence, bool):
            raise ToolTraceNormalizationError(
                f"{field}: sequence must be an int, not {type(raw_sequence).__name__} -- "
                "refusing to guess arrival order"
            )
        sequence: int = raw_sequence
        if sequence in seen_sequences:
            raise ToolTraceNormalizationError(
                f"{field}: duplicate sequence {sequence} -- ordering is ambiguous"
            )
        seen_sequences.add(sequence)

        message: dict[str, Any] = {
            k: v for k, v in event.items() if k not in ("session_id", "sequence")
        }

        if message.get("role") == "assistant" and message.get("tool_calls") is not None:
            raw_calls = message["tool_calls"]
            if not isinstance(raw_calls, list):
                raise ToolTraceNormalizationError(f"{field}.tool_calls: must be a list")
            cleaned_calls: list[object] = []
            for call_index, raw_call in enumerate(raw_calls):
                call_field = f"{field}.tool_calls[{call_index}]"
                if not isinstance(raw_call, Mapping):
                    raise ToolTraceNormalizationError(f"{call_field}: tool call must be an object")
                call = dict(raw_call)
                authorized = call.pop("authorized", None)
                if not isinstance(authorized, bool):
                    call_id_repr = call.get("id", "<unknown>")
                    raise ToolTraceNormalizationError(
                        f"{call_field}: tool call {call_id_repr!r} does not declare an "
                        "explicit boolean 'authorized' permission -- refusing to guess "
                        "whether it was actually allowed to execute"
                    )
                call_id = call.get("id")
                if isinstance(call_id, str) and call_id:
                    if call_id in authorizations:
                        raise ToolTraceNormalizationError(
                            f"{call_field}: duplicate tool call id {call_id!r} across "
                            "this trace -- cannot be accepted"
                        )
                    authorizations[call_id] = authorized
                cleaned_calls.append(call)
            message["tool_calls"] = cleaned_calls

        indexed_events.append((sequence, message))

    indexed_events.sort(key=lambda pair: pair[0])
    ordered_messages = [message for _, message in indexed_events]

    combined_metadata: dict[str, object] = dict(metadata or {})
    combined_metadata[_TRACE_HASH_KEY] = trace_raw_sha256
    if authorizations:
        combined_metadata[_AUTHORIZATIONS_KEY] = authorizations

    raw_record = {"messages": ordered_messages, "metadata": combined_metadata}

    return normalize_conversation(
        source_type=source_type,
        source_record_index=source_record_index,
        raw_record=raw_record,
        tool_schemas=tool_schemas,
        tool_argument_validator=tool_argument_validator,
        group_key=session_id,
        record_id=record_id,
    )
