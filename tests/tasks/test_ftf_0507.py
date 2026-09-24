#!/usr/bin/env python3
"""FTF-0507 -- Tool-trace normalizer.

Runnable without any database and without any external network egress:
`normalize_tool_trace` is a pure, synchronous, no-I/O function composing
FTF-0506's already-CERTIFIED `normalize_conversation` (this task's own
`database_changes` clause -- "No independent schema change. Consume
frozen contract.").

This task's own acceptance clause ("Convert approved trace messages, tool
IDs/results and outcomes preserving ordering and actual permissions") is
covered by `test_end_to_end_trace_normalizes_out_of_order_events`
(ordering restored from `sequence`, not arrival order),
`test_trace_raw_sha256_is_sensitive_to_arrival_order` (the exact
as-received batch stays independently verifiable even after reordering),
and `test_tool_call_authorizations_are_collected_and_preserved` (actual
per-call permission preserved into `metadata`, never guessed).

This task's own negative_acceptance ("Orphan/duplicate call IDs, malformed
tool result and mixed-session traces cannot be accepted") is covered by
`test_rejects_mixed_session_trace`,
`test_rejects_duplicate_tool_call_id_across_trace`,
`test_rejects_orphan_tool_call_id_via_ftf_0506`, and
`test_rejects_malformed_tool_result_via_ftf_0506` -- the last two proving
this module's own real, wired-through delegation to FTF-0506's already-
CERTIFIED guards, not re-asserting FTF-0506's own suite a second time.

Run directly: python3 tests/tasks/test_ftf_0507.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
API_ROOT = ROOT / "apps" / "api"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(API_ROOT))

from workers.data.canonical_conversation_normalizer import (  # noqa: E402
    ConversationNormalizationError,
)
from workers.data.tool_trace_normalizer import (  # noqa: E402
    RESERVED_METADATA_KEYS,
    normalize_tool_trace,
)

from app.services.json_schema_tool_validation import validate_instance  # noqa: E402

FAILURES: list[str] = []


def check(criterion_id: str, description: str, condition: bool) -> None:
    if not condition:
        FAILURES.append(f"FAIL [{criterion_id}] {description}")


def _rejects(**kwargs: object) -> str | None:
    kwargs.setdefault("source_type", "agent_traces")
    kwargs.setdefault("source_record_index", 0)
    kwargs.setdefault("session_id", "session-1")
    try:
        normalize_tool_trace(**kwargs)  # type: ignore[arg-type]
    except ConversationNormalizationError as exc:
        return str(exc)
    return None


_LOOKUP_ORDER_SCHEMA = {
    "type": "object",
    "properties": {"order_id": {"type": "string"}},
    "required": ["order_id"],
    "additionalProperties": False,
}


def _golden_events() -> list[dict[str, object]]:
    # Deliberately out of chronological order -- proves ordering is
    # restored from `sequence`, not from list position.
    return [
        {
            "session_id": "session-1",
            "sequence": 30,
            "role": "tool",
            "tool_call_id": "call_1",
            "name": "lookup_order",
            "content": [{"type": "json", "value": {"status": "shipped"}}],
        },
        {
            "session_id": "session-1",
            "sequence": 10,
            "role": "user",
            "content": "Where is my order?",
        },
        {
            "session_id": "session-1",
            "sequence": 40,
            "role": "assistant",
            "content": "Your order has shipped.",
        },
        {
            "session_id": "session-1",
            "sequence": 20,
            "role": "assistant",
            "content": [],
            "tool_calls": [
                {
                    "id": "call_1",
                    "name": "lookup_order",
                    "arguments": {"order_id": "123"},
                    "authorized": True,
                }
            ],
        },
    ]


def test_end_to_end_trace_normalizes_out_of_order_events() -> None:
    result = normalize_tool_trace(
        source_type="agent_traces",
        source_record_index=0,
        session_id="session-1",
        events=_golden_events(),
        tool_schemas={"lookup_order": _LOOKUP_ORDER_SCHEMA},
        tool_argument_validator=validate_instance,
        metadata={"source": "production_trace", "language": "en"},
    )
    roles = [m.role for m in result.messages]
    check(
        "FTF-0507",
        "role order is restored from 'sequence', not list arrival order",
        roles == ["user", "assistant", "tool", "assistant"],
    )
    check(
        "FTF-0507",
        "the reordered assistant message still carries its one real tool call",
        len(result.messages[1].tool_calls) == 1
        and result.messages[1].tool_calls[0].id == "call_1"
        and dict(result.messages[1].tool_calls[0].arguments) == {"order_id": "123"},
    )
    check(
        "FTF-0507",
        "the tool result content is preserved losslessly",
        result.messages[2].content[0].type == "json"
        and result.messages[2].content[0].value == {"status": "shipped"},
    )
    check(
        "FTF-0507",
        "caller-declared metadata survives alongside this module's own additions",
        result.metadata["source"] == "production_trace" and result.metadata["language"] == "en",
    )
    check(
        "FTF-0507",
        "group_id is derived from session_id (Section 9 split-isolation identity)",
        isinstance(result.group_id, str) and len(result.group_id) == 64,
    )


def test_tool_call_authorizations_are_collected_and_preserved() -> None:
    result = normalize_tool_trace(
        source_type="agent_traces",
        source_record_index=0,
        session_id="session-1",
        events=_golden_events(),
        tool_schemas={"lookup_order": _LOOKUP_ORDER_SCHEMA},
        tool_argument_validator=validate_instance,
    )
    check(
        "FTF-0507",
        "acceptance: actual per-call permission is preserved, not dropped",
        result.metadata.get("tool_call_authorizations") == {"call_1": True},
    )


def test_tool_call_authorizations_absent_when_no_tools_used() -> None:
    result = normalize_tool_trace(
        source_type="agent_traces",
        source_record_index=0,
        session_id="session-1",
        events=[
            {"session_id": "session-1", "sequence": 1, "role": "user", "content": "hi"},
            {"session_id": "session-1", "sequence": 2, "role": "assistant", "content": "hello"},
        ],
    )
    check(
        "FTF-0507",
        "no fabricated authorizations key when the trace used no tools at all",
        "tool_call_authorizations" not in result.metadata,
    )


def test_trace_raw_sha256_is_sensitive_to_arrival_order() -> None:
    events = _golden_events()
    reversed_events = list(reversed(events))
    result_a = normalize_tool_trace(
        source_type="agent_traces",
        source_record_index=0,
        session_id="session-1",
        events=events,
        tool_schemas={"lookup_order": _LOOKUP_ORDER_SCHEMA},
        tool_argument_validator=validate_instance,
    )
    result_b = normalize_tool_trace(
        source_type="agent_traces",
        source_record_index=0,
        session_id="session-1",
        events=reversed_events,
        tool_schemas={"lookup_order": _LOOKUP_ORDER_SCHEMA},
        tool_argument_validator=validate_instance,
    )
    check(
        "FTF-0507",
        "the same events in a different arrival order stay independently "
        "verifiable via a different trace_raw_sha256",
        result_a.metadata["trace_raw_sha256"] != result_b.metadata["trace_raw_sha256"],
    )
    check(
        "FTF-0507",
        "yet the resulting canonical conversation messages themselves are identical "
        "regardless of arrival order (ordering was genuinely restored)",
        [m.to_canonical() for m in result_a.messages]
        == [m.to_canonical() for m in result_b.messages],
    )
    result_c = normalize_tool_trace(
        source_type="agent_traces",
        source_record_index=0,
        session_id="session-1",
        events=events,
        tool_schemas={"lookup_order": _LOOKUP_ORDER_SCHEMA},
        tool_argument_validator=validate_instance,
    )
    check(
        "FTF-0507",
        "trace_raw_sha256 is deterministic for the exact same input batch",
        result_a.metadata["trace_raw_sha256"] == result_c.metadata["trace_raw_sha256"],
    )


def test_rejects_mixed_session_trace() -> None:
    events = _golden_events()
    events[1] = dict(events[1])
    events[1]["session_id"] = "session-2"
    reason = _rejects(
        events=events,
        tool_schemas={"lookup_order": _LOOKUP_ORDER_SCHEMA},
        tool_argument_validator=validate_instance,
    )
    check(
        "FTF-0507",
        "negative_acceptance: a mixed-session trace is refused, not silently merged",
        reason is not None and "mixed-session" in reason,
    )


def test_rejects_missing_authorized_flag() -> None:
    events = [
        {"session_id": "session-1", "sequence": 1, "role": "user", "content": "hi"},
        {
            "session_id": "session-1",
            "sequence": 2,
            "role": "assistant",
            "content": [],
            "tool_calls": [
                {"id": "call_1", "name": "lookup_order", "arguments": {"order_id": "1"}}
            ],
        },
    ]
    reason = _rejects(
        events=events,
        tool_schemas={"lookup_order": _LOOKUP_ORDER_SCHEMA},
        tool_argument_validator=validate_instance,
    )
    check(
        "FTF-0507",
        "negative_acceptance: a tool call missing an explicit 'authorized' flag is "
        "refused, not assumed permitted",
        reason is not None and "authorized" in reason,
    )


def test_rejects_non_bool_authorized_flag() -> None:
    events = [
        {"session_id": "session-1", "sequence": 1, "role": "user", "content": "hi"},
        {
            "session_id": "session-1",
            "sequence": 2,
            "role": "assistant",
            "content": [],
            "tool_calls": [
                {
                    "id": "call_1",
                    "name": "lookup_order",
                    "arguments": {"order_id": "1"},
                    "authorized": "yes",
                }
            ],
        },
    ]
    reason = _rejects(
        events=events,
        tool_schemas={"lookup_order": _LOOKUP_ORDER_SCHEMA},
        tool_argument_validator=validate_instance,
    )
    check(
        "FTF-0507",
        "a non-boolean 'authorized' value (e.g. a string) is refused, not coerced",
        reason is not None and "authorized" in reason,
    )


def test_rejects_duplicate_tool_call_id_across_trace() -> None:
    events = [
        {"session_id": "session-1", "sequence": 1, "role": "user", "content": "hi"},
        {
            "session_id": "session-1",
            "sequence": 2,
            "role": "assistant",
            "content": [],
            "tool_calls": [
                {
                    "id": "call_1",
                    "name": "lookup_order",
                    "arguments": {"order_id": "1"},
                    "authorized": True,
                }
            ],
        },
        {
            "session_id": "session-1",
            "sequence": 3,
            "role": "tool",
            "tool_call_id": "call_1",
            "name": "lookup_order",
            "content": [{"type": "json", "value": {"status": "shipped"}}],
        },
        {
            "session_id": "session-1",
            "sequence": 4,
            "role": "assistant",
            "content": "ok",
        },
        {
            "session_id": "session-1",
            "sequence": 5,
            "role": "user",
            "content": "and again?",
        },
        {
            "session_id": "session-1",
            "sequence": 6,
            "role": "assistant",
            "content": [],
            "tool_calls": [
                {
                    "id": "call_1",
                    "name": "lookup_order",
                    "arguments": {"order_id": "2"},
                    "authorized": True,
                }
            ],
        },
    ]
    reason = _rejects(
        events=events,
        tool_schemas={"lookup_order": _LOOKUP_ORDER_SCHEMA},
        tool_argument_validator=validate_instance,
    )
    check(
        "FTF-0507",
        "negative_acceptance: a duplicate tool call id across the trace is refused "
        "by this module's own trace-level pass",
        reason is not None and "duplicate tool call id" in reason,
    )


def test_rejects_orphan_tool_call_id_via_ftf_0506() -> None:
    events = [
        {"session_id": "session-1", "sequence": 1, "role": "user", "content": "hi"},
        {
            "session_id": "session-1",
            "sequence": 2,
            "role": "assistant",
            "content": [],
            "tool_calls": [
                {
                    "id": "call_1",
                    "name": "lookup_order",
                    "arguments": {"order_id": "1"},
                    "authorized": True,
                }
            ],
        },
        {
            "session_id": "session-1",
            "sequence": 3,
            "role": "tool",
            "tool_call_id": "call_never_issued",
            "name": "lookup_order",
            "content": [{"type": "json", "value": {"status": "shipped"}}],
        },
    ]
    reason = _rejects(
        events=events,
        tool_schemas={"lookup_order": _LOOKUP_ORDER_SCHEMA},
        tool_argument_validator=validate_instance,
    )
    check(
        "FTF-0507",
        "negative_acceptance: an orphan tool_call_id is refused via FTF-0506's own "
        "already-CERTIFIED guard, genuinely wired through this module",
        reason is not None and "does not reference an existing pending tool call" in reason,
    )


def test_rejects_malformed_tool_result_via_ftf_0506() -> None:
    events = [
        {"session_id": "session-1", "sequence": 1, "role": "user", "content": "hi"},
        {
            "session_id": "session-1",
            "sequence": 2,
            "role": "assistant",
            "content": [],
            "tool_calls": [
                {
                    "id": "call_1",
                    "name": "lookup_order",
                    "arguments": {"order_id": "1"},
                    "authorized": True,
                }
            ],
        },
        {
            "session_id": "session-1",
            "sequence": 3,
            "role": "tool",
            "tool_call_id": "call_1",
            "name": "a_different_tool",
            "content": [{"type": "json", "value": {"status": "shipped"}}],
        },
    ]
    reason = _rejects(
        events=events,
        tool_schemas={"lookup_order": _LOOKUP_ORDER_SCHEMA},
        tool_argument_validator=validate_instance,
    )
    check(
        "FTF-0507",
        "negative_acceptance: a malformed tool result naming the wrong tool is "
        "refused via FTF-0506's own already-CERTIFIED guard",
        reason is not None and "does not match the" in reason,
    )


def test_rejects_duplicate_sequence() -> None:
    events = [
        {"session_id": "session-1", "sequence": 1, "role": "user", "content": "hi"},
        {"session_id": "session-1", "sequence": 1, "role": "assistant", "content": "hello"},
    ]
    reason = _rejects(events=events)
    check(
        "FTF-0507",
        "a duplicate sequence number is refused as ambiguous ordering",
        reason is not None and "duplicate sequence" in reason,
    )


def test_rejects_non_int_sequence() -> None:
    events = [{"session_id": "session-1", "sequence": "1", "role": "user", "content": "hi"}]
    reason = _rejects(events=events)
    check(
        "FTF-0507",
        "a non-int sequence value is refused rather than coerced",
        reason is not None and "sequence must be an int" in reason,
    )


def test_rejects_bool_sequence() -> None:
    events = [{"session_id": "session-1", "sequence": True, "role": "user", "content": "hi"}]
    reason = _rejects(events=events)
    check(
        "FTF-0507",
        "a bool sequence value is refused (bool is an int subclass but not a real ordinal)",
        reason is not None and "sequence must be an int" in reason,
    )


def test_rejects_empty_events() -> None:
    reason = _rejects(events=[])
    check(
        "FTF-0507", "an empty events list is rejected", reason is not None and "non-empty" in reason
    )


def test_rejects_non_mapping_event() -> None:
    reason = _rejects(events=["not-a-mapping"])
    check(
        "FTF-0507",
        "a non-mapping trace event is rejected",
        reason is not None and "must be an object" in reason,
    )


def test_rejects_empty_session_id() -> None:
    reason = _rejects(
        session_id="", events=[{"session_id": "", "sequence": 1, "role": "user", "content": "hi"}]
    )
    check(
        "FTF-0507", "an empty session_id is rejected", reason is not None and "session_id" in reason
    )


def test_rejects_reserved_metadata_key_collision() -> None:
    reason = _rejects(
        events=[{"session_id": "session-1", "sequence": 1, "role": "user", "content": "hi"}],
        metadata={"trace_raw_sha256": "not-really-a-hash"},
    )
    check(
        "FTF-0507",
        "a caller-supplied metadata key colliding with this module's own reserved "
        "key is refused, not silently overwritten",
        reason is not None and "reserved key" in reason,
    )


def test_reserved_metadata_keys_constant_matches_actual_written_keys() -> None:
    result = normalize_tool_trace(
        source_type="agent_traces",
        source_record_index=0,
        session_id="session-1",
        events=_golden_events(),
        tool_schemas={"lookup_order": _LOOKUP_ORDER_SCHEMA},
        tool_argument_validator=validate_instance,
    )
    written_keys = set(result.metadata) - {"source", "language"}
    check(
        "FTF-0507",
        "every metadata key this module writes is declared in RESERVED_METADATA_KEYS",
        written_keys <= RESERVED_METADATA_KEYS,
    )


def main() -> None:
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()

    if FAILURES:
        print(f"FTF-0507 tool-trace normalizer: {len(FAILURES)} failed")
        for failure in FAILURES:
            print(f"  {failure}")
        sys.exit(1)

    print("FTF-0507 tool-trace normalizer: all checks passed")
    sys.exit(0)


if __name__ == "__main__":
    main()
