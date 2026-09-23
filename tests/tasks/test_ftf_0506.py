#!/usr/bin/env python3
"""FTF-0506 -- Canonical conversation normalizer.

Runnable without any database and without any external network egress:
`normalize_conversation` is a pure, synchronous, no-I/O function (this
task's own `database_changes` clause -- "No independent schema change.
Consume frozen contract.") that takes tool-argument validation as a
dependency-injected `tool_argument_validator` callable rather than
importing across the `workers/data` <-> `apps/api` deployable-unit
boundary (see the module's own docstring). This suite wires in FTF-0405's
own already-CERTIFIED `validate_instance` directly, unmodified, as that
callable -- proven here against a real schema built through that same
module's own `validate_json_schema_document`
(`test_tool_arguments_validate_against_a_real_ftf_0405_schema`), not a
hand-rolled stand-in.

This task's own acceptance clause ("Normalize roles/content/tool calls
into canonical schema with lineage/group IDs and deterministic hash") is
covered by `test_full_section_7_example_normalizes_end_to_end`,
`test_canonical_hash_is_deterministic_and_content_derived`,
`test_group_id_is_stable_for_a_shared_explicit_group_key`,
`test_group_id_defaults_to_a_solo_group_per_record_id` and
`test_lineage_records_source_and_raw_hash`.

This task's own negative_acceptance ("Missing/unmapped role or coerced
information loss is rejected, not silently guessed") is covered by every
`test_rejects_*` function below -- each names the exact Section 7
"Required validator" or negative_acceptance clause it proves.

Run directly: python3 tests/tasks/test_ftf_0506.py
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
    normalize_conversation,
)

from app.services.json_schema_tool_validation import (  # noqa: E402
    validate_instance,
    validate_json_schema_document,
)

FAILURES: list[str] = []


def check(criterion_id: str, description: str, condition: bool) -> None:
    if not condition:
        FAILURES.append(f"FAIL [{criterion_id}] {description}")


def _rejects(**kwargs: object) -> str | None:
    kwargs.setdefault("source_type", "unit_test")
    kwargs.setdefault("source_record_index", 0)
    try:
        normalize_conversation(**kwargs)  # type: ignore[arg-type]
    except ConversationNormalizationError as exc:
        return str(exc)
    return None


_SECTION_7_EXAMPLE = {
    "messages": [
        {"role": "system", "content": "system instruction"},
        {"role": "user", "content": "request"},
        {
            "role": "assistant",
            "content": [],
            "tool_calls": [
                {"id": "call_1", "name": "lookup_order", "arguments": {"order_id": "123"}}
            ],
        },
        {
            "role": "tool",
            "tool_call_id": "call_1",
            "name": "lookup_order",
            "content": [{"type": "json", "value": {"status": "shipped"}}],
        },
        {"role": "assistant", "content": "The order has shipped."},
    ],
    "metadata": {"source": "production_trace", "language": "en", "quality_label": "approved"},
}

_LOOKUP_ORDER_SCHEMA = {
    "type": "object",
    "properties": {"order_id": {"type": "string"}},
    "required": ["order_id"],
    "additionalProperties": False,
}


def test_full_section_7_example_normalizes_end_to_end() -> None:
    result = normalize_conversation(
        source_type="production_trace",
        source_record_index=0,
        raw_record=_SECTION_7_EXAMPLE,
        tool_schemas={"lookup_order": _LOOKUP_ORDER_SCHEMA},
        tool_argument_validator=validate_instance,
    )
    check("FTF-0506", "schema_version is Section 7's pinned '1.0'", result.schema_version == "1.0")
    check("FTF-0506", "exactly five messages survive normalization", len(result.messages) == 5)
    roles = [m.role for m in result.messages]
    check(
        "FTF-0506",
        "role order matches Section 7's own worked example exactly",
        roles == ["system", "user", "assistant", "tool", "assistant"],
    )
    system_msg, user_msg, assistant_call, tool_msg, assistant_final = result.messages
    check(
        "FTF-0506",
        "a bare string content is losslessly wrapped as a single text part",
        len(system_msg.content) == 1
        and system_msg.content[0].type == "text"
        and system_msg.content[0].text == "system instruction",
    )
    check(
        "FTF-0506",
        "the tool-issuing assistant message carries exactly one recorded tool call",
        len(assistant_call.tool_calls) == 1
        and assistant_call.tool_calls[0].id == "call_1"
        and assistant_call.tool_calls[0].name == "lookup_order"
        and dict(assistant_call.tool_calls[0].arguments) == {"order_id": "123"},
    )
    check(
        "FTF-0506",
        "the tool result message keeps its tool_call_id/name and json content part",
        tool_msg.tool_call_id == "call_1"
        and tool_msg.name == "lookup_order"
        and tool_msg.content[0].type == "json"
        and tool_msg.content[0].value == {"status": "shipped"},
    )
    check(
        "FTF-0506",
        "the concluding assistant message carries the final text",
        assistant_final.content[0].type == "text"
        and assistant_final.content[0].text == "The order has shipped.",
    )
    check(
        "FTF-0506",
        "metadata is carried through unchanged",
        dict(result.metadata) == _SECTION_7_EXAMPLE["metadata"],
    )
    check(
        "FTF-0506",
        "record_id is a non-empty string",
        isinstance(result.record_id, str) and bool(result.record_id),
    )
    check(
        "FTF-0506",
        "canonical_hash is a 64-character hex SHA-256 digest",
        isinstance(result.canonical_hash, str)
        and len(result.canonical_hash) == 64
        and all(c in "0123456789abcdef" for c in result.canonical_hash),
    )


def test_canonical_hash_is_deterministic_and_content_derived() -> None:
    first = normalize_conversation(
        source_type="jsonl",
        source_record_index=0,
        raw_record=_SECTION_7_EXAMPLE,
        tool_schemas={"lookup_order": _LOOKUP_ORDER_SCHEMA},
        tool_argument_validator=validate_instance,
    )
    second = normalize_conversation(
        source_type="jsonl",
        source_record_index=1,
        raw_record=_SECTION_7_EXAMPLE,
        tool_schemas={"lookup_order": _LOOKUP_ORDER_SCHEMA},
        tool_argument_validator=validate_instance,
    )
    check(
        "FTF-0506",
        "identical conversation content hashes identically regardless of record_id "
        "(Section 8.1 step 10, exact duplicate detection, needs this)",
        first.canonical_hash == second.canonical_hash,
    )
    check(
        "FTF-0506",
        "two normalize_conversation calls assign distinct random record_ids by default",
        first.record_id != second.record_id,
    )

    different_content = {
        "messages": [{"role": "user", "content": "a completely different request"}],
        "metadata": {"source": "production_trace"},
    }
    third = normalize_conversation(
        source_type="jsonl", source_record_index=2, raw_record=different_content
    )
    check(
        "FTF-0506",
        "different conversation content hashes differently",
        third.canonical_hash != first.canonical_hash,
    )


def test_group_id_is_stable_for_a_shared_explicit_group_key() -> None:
    case_a = {"messages": [{"role": "user", "content": "first turn of case 42"}]}
    case_b = {
        "messages": [{"role": "user", "content": "second turn of case 42, different content"}]
    }
    first = normalize_conversation(
        source_type="jsonl", source_record_index=0, raw_record=case_a, group_key="case-42"
    )
    second = normalize_conversation(
        source_type="jsonl", source_record_index=1, raw_record=case_b, group_key="case-42"
    )
    other = normalize_conversation(
        source_type="jsonl", source_record_index=2, raw_record=case_a, group_key="case-99"
    )
    check(
        "FTF-0506",
        "records sharing an explicit group_key get the same group_id despite "
        "different content (Section 9: keep entire conversations/customer cases in one split)",
        first.group_id == second.group_id,
    )
    check(
        "FTF-0506",
        "a different explicit group_key produces a different group_id",
        first.group_id != other.group_id,
    )


def test_group_id_defaults_to_a_solo_group_per_record_id() -> None:
    raw = {"messages": [{"role": "user", "content": "standalone"}]}
    first = normalize_conversation(
        source_type="jsonl", source_record_index=0, raw_record=raw, record_id="rec-a"
    )
    second = normalize_conversation(
        source_type="jsonl", source_record_index=1, raw_record=raw, record_id="rec-b"
    )
    same_id_twice = normalize_conversation(
        source_type="jsonl", source_record_index=2, raw_record=raw, record_id="rec-a"
    )
    check(
        "FTF-0506",
        "without an explicit group_key, two different records get different group_ids",
        first.group_id != second.group_id,
    )
    check(
        "FTF-0506",
        "the solo group_id is a deterministic function of record_id, not random",
        first.group_id == same_id_twice.group_id,
    )
    check(
        "FTF-0506",
        "an explicit-group_key group_id never collides with a solo group_id's own namespace",
        first.group_id
        != normalize_conversation(
            source_type="jsonl", source_record_index=3, raw_record=raw, group_key="rec-a"
        ).group_id,
    )


def test_lineage_records_source_and_raw_hash() -> None:
    raw_one = {"messages": [{"role": "user", "content": "hello"}]}
    raw_two = {"messages": [{"role": "user", "content": "hello!"}]}
    first = normalize_conversation(source_type="csv", source_record_index=7, raw_record=raw_one)
    second = normalize_conversation(source_type="csv", source_record_index=7, raw_record=raw_two)
    check("FTF-0506", "lineage.source_type is passed through", first.lineage.source_type == "csv")
    check(
        "FTF-0506",
        "lineage.source_record_index is passed through",
        first.lineage.source_record_index == 7,
    )
    check(
        "FTF-0506",
        "lineage.raw_sha256 is a real hash of the exact pre-normalization input, "
        "distinct for distinct raw records",
        first.lineage.raw_sha256 != second.lineage.raw_sha256,
    )
    check(
        "FTF-0506",
        "lineage.raw_sha256 is stable for the byte-identical raw record",
        first.lineage.raw_sha256
        == normalize_conversation(
            source_type="csv", source_record_index=7, raw_record=raw_one
        ).lineage.raw_sha256,
    )


def test_tool_arguments_validate_against_a_real_ftf_0405_schema() -> None:
    # Real integration with FTF-0405 (CERTIFIED): the schema below is itself
    # validated through that task's own `validate_json_schema_document`
    # before being handed to this task's `normalize_conversation` --
    # genuine composition, not a hand-rolled stand-in schema shape.
    validated_schema = validate_json_schema_document(
        _LOOKUP_ORDER_SCHEMA, "tool_schemas.lookup_order"
    )
    result = normalize_conversation(
        source_type="jsonl",
        source_record_index=0,
        raw_record={
            "messages": [
                {"role": "user", "content": "where is my order"},
                {
                    "role": "assistant",
                    "content": [],
                    "tool_calls": [
                        {"id": "call_9", "name": "lookup_order", "arguments": {"order_id": "abc"}}
                    ],
                },
                {
                    "role": "tool",
                    "tool_call_id": "call_9",
                    "name": "lookup_order",
                    "content": [{"type": "json", "value": {"status": "processing"}}],
                },
            ]
        },
        tool_schemas={"lookup_order": validated_schema},
        tool_argument_validator=validate_instance,
    )
    check(
        "FTF-0506",
        "a real FTF-0405-validated schema accepts a conforming tool call",
        dict(result.messages[1].tool_calls[0].arguments) == {"order_id": "abc"},
    )

    reason = _rejects(
        raw_record={
            "messages": [
                {"role": "user", "content": "where is my order"},
                {
                    "role": "assistant",
                    "content": [],
                    "tool_calls": [
                        {
                            "id": "call_9",
                            "name": "lookup_order",
                            "arguments": {"order_id": "abc", "unexpected_extra": True},
                        }
                    ],
                },
            ]
        },
        tool_schemas={"lookup_order": validated_schema},
        tool_argument_validator=validate_instance,
    )
    check(
        "FTF-0506",
        "tool arguments validate for real against the project ToolDefinition schema "
        "(Section 7 required validator) -- an excess argument under "
        "additionalProperties:false is genuinely rejected by FTF-0405's own validator, "
        "not a stand-in",
        reason is not None and "excess property" in reason,
    )


def test_rejects_unmapped_role() -> None:
    reason = _rejects(raw_record={"messages": [{"role": "human", "content": "hi"}]})
    check(
        "FTF-0506",
        "negative_acceptance: an unmapped role ('human') is rejected outright, "
        "never silently aliased to 'user'",
        reason is not None and "unmapped role" in reason,
    )


def test_rejects_unsupported_content_shape() -> None:
    reason = _rejects(raw_record={"messages": [{"role": "user", "content": 42}]})
    check(
        "FTF-0506",
        "negative_acceptance: a numeric content value is refused, not guessed into text",
        reason is not None and "unsupported content shape" in reason,
    )
    reason_dict = _rejects(raw_record={"messages": [{"role": "user", "content": {"free": "form"}}]})
    check(
        "FTF-0506",
        "negative_acceptance: a bare JSON object as content is refused for a non-tool "
        "role, not silently wrapped as a json content part",
        reason_dict is not None and "unsupported content shape" in reason_dict,
    )


def test_rejects_unsupported_content_part_type() -> None:
    reason = _rejects(
        raw_record={"messages": [{"role": "user", "content": [{"type": "image", "url": "x"}]}]}
    )
    check(
        "FTF-0506",
        "Section 7 required validator: no unsupported content type",
        reason is not None and "unsupported content type" in reason,
    )


def test_rejects_null_content() -> None:
    reason = _rejects(raw_record={"messages": [{"role": "user", "content": None}]})
    check(
        "FTF-0506",
        "Section 7 required validator: no null role/content ambiguity",
        reason is not None and "must not be null" in reason,
    )


def test_rejects_consecutive_user_messages() -> None:
    reason = _rejects(
        raw_record={
            "messages": [
                {"role": "user", "content": "first"},
                {"role": "user", "content": "second"},
            ]
        }
    )
    check(
        "FTF-0506",
        "Section 7 required validator: valid role ordering -- two consecutive 'user' "
        "messages is invalid",
        reason is not None and "cannot follow" in reason,
    )


def test_rejects_non_leading_system_message() -> None:
    reason = _rejects(
        raw_record={
            "messages": [
                {"role": "user", "content": "hi"},
                {"role": "assistant", "content": "hello"},
                {"role": "system", "content": "late system message"},
            ]
        }
    )
    check(
        "FTF-0506",
        "valid role ordering: 'system' messages must form a leading contiguous block",
        reason is not None and "leading contiguous block" in reason,
    )


def test_rejects_tool_message_with_no_pending_call() -> None:
    # prev_role is 'assistant' (a valid tool predecessor) but that assistant
    # message issued no tool_calls -- pending_calls is empty, so the
    # specific "no pending tool call to answer" branch fires rather than
    # the more general "must directly follow the issuing assistant" check.
    reason = _rejects(
        raw_record={
            "messages": [
                {"role": "user", "content": "hi"},
                {"role": "assistant", "content": "let me think"},
                {
                    "role": "tool",
                    "tool_call_id": "call_ghost",
                    "name": "anything",
                    "content": [{"type": "text", "text": "x"}],
                },
            ]
        }
    )
    check(
        "FTF-0506",
        "valid role ordering: a 'tool' message cannot appear with no pending tool call",
        reason is not None and "no pending tool call" in reason,
    )

    reason_after_user = _rejects(
        raw_record={
            "messages": [
                {"role": "user", "content": "hi"},
                {
                    "role": "tool",
                    "tool_call_id": "call_ghost",
                    "name": "anything",
                    "content": [{"type": "text", "text": "x"}],
                },
            ]
        }
    )
    check(
        "FTF-0506",
        "valid role ordering: a 'tool' message can never directly follow a 'user' message",
        reason_after_user is not None
        and "must directly follow the assistant message" in reason_after_user,
    )


def test_rejects_tool_result_referencing_unknown_call_id() -> None:
    reason = _rejects(
        raw_record={
            "messages": [
                {"role": "user", "content": "hi"},
                {
                    "role": "assistant",
                    "content": [],
                    "tool_calls": [
                        {"id": "call_1", "name": "lookup_order", "arguments": {"order_id": "1"}}
                    ],
                },
                {
                    "role": "tool",
                    "tool_call_id": "call_does_not_exist",
                    "name": "lookup_order",
                    "content": [{"type": "text", "text": "x"}],
                },
            ]
        },
        tool_schemas={"lookup_order": _LOOKUP_ORDER_SCHEMA},
        tool_argument_validator=validate_instance,
    )
    check(
        "FTF-0506",
        "Section 7 required validator: every tool result references an existing tool call",
        reason is not None and "does not reference an existing pending tool call" in reason,
    )


def test_rejects_tool_result_name_mismatch() -> None:
    reason = _rejects(
        raw_record={
            "messages": [
                {"role": "user", "content": "hi"},
                {
                    "role": "assistant",
                    "content": [],
                    "tool_calls": [
                        {"id": "call_1", "name": "lookup_order", "arguments": {"order_id": "1"}}
                    ],
                },
                {
                    "role": "tool",
                    "tool_call_id": "call_1",
                    "name": "a_different_tool_name",
                    "content": [{"type": "text", "text": "x"}],
                },
            ]
        },
        tool_schemas={"lookup_order": _LOOKUP_ORDER_SCHEMA},
        tool_argument_validator=validate_instance,
    )
    check(
        "FTF-0506",
        "negative_acceptance: a tool result naming a different tool than its own "
        "issuing call is rejected, not silently accepted",
        reason is not None and "does not match the issuing call's name" in reason,
    )


def test_rejects_duplicate_tool_call_id() -> None:
    reason = _rejects(
        raw_record={
            "messages": [
                {"role": "user", "content": "hi"},
                {
                    "role": "assistant",
                    "content": [],
                    "tool_calls": [
                        {"id": "call_1", "name": "lookup_order", "arguments": {"order_id": "1"}},
                        {"id": "call_1", "name": "lookup_order", "arguments": {"order_id": "2"}},
                    ],
                },
            ]
        },
        tool_schemas={"lookup_order": _LOOKUP_ORDER_SCHEMA},
        tool_argument_validator=validate_instance,
    )
    check(
        "FTF-0506",
        "Section 7 required validator: tool call IDs unique within record",
        reason is not None and "not unique within this record" in reason,
    )


def test_rejects_record_ending_with_unanswered_tool_call() -> None:
    reason = _rejects(
        raw_record={
            "messages": [
                {"role": "user", "content": "hi"},
                {
                    "role": "assistant",
                    "content": [],
                    "tool_calls": [
                        {"id": "call_1", "name": "lookup_order", "arguments": {"order_id": "1"}}
                    ],
                },
            ]
        },
        tool_schemas={"lookup_order": _LOOKUP_ORDER_SCHEMA},
        tool_argument_validator=validate_instance,
    )
    check(
        "FTF-0506",
        "a record ending with an unanswered tool call is rejected, not silently "
        "accepted as complete",
        reason is not None and "unanswered tool call" in reason,
    )


def test_rejects_unmapped_tool_name() -> None:
    reason = _rejects(
        raw_record={
            "messages": [
                {"role": "user", "content": "hi"},
                {
                    "role": "assistant",
                    "content": [],
                    "tool_calls": [{"id": "call_1", "name": "no_such_tool", "arguments": {}}],
                },
            ]
        },
        tool_schemas={"lookup_order": _LOOKUP_ORDER_SCHEMA},
        tool_argument_validator=validate_instance,
    )
    check(
        "FTF-0506",
        "negative_acceptance: a tool call naming a tool absent from tool_schemas is "
        "rejected as unmapped, never validated against no schema at all",
        reason is not None and "not a mapped project tool" in reason,
    )


def test_rejects_tool_call_when_no_validator_supplied() -> None:
    # A mapped tool with a real schema present, but no tool_argument_validator
    # given at all -- must fail closed rather than accept the arguments on trust.
    reason = _rejects(
        raw_record={
            "messages": [
                {"role": "user", "content": "hi"},
                {
                    "role": "assistant",
                    "content": [],
                    "tool_calls": [
                        {"id": "call_1", "name": "lookup_order", "arguments": {"order_id": "1"}}
                    ],
                },
            ]
        },
        tool_schemas={"lookup_order": _LOOKUP_ORDER_SCHEMA},
    )
    check(
        "FTF-0506",
        "negative_acceptance: a tool call is never accepted on trust when no "
        "tool_argument_validator was supplied to prove its arguments",
        reason is not None and "no tool_argument_validator was supplied" in reason,
    )


def test_rejects_unrecognized_extra_field() -> None:
    reason = _rejects(
        raw_record={"messages": [{"role": "user", "content": "hi", "unexpected": "field"}]}
    )
    check(
        "FTF-0506",
        "negative_acceptance: an unrecognized field on a message is rejected, not "
        "silently dropped (coerced information loss)",
        reason is not None and "unsupported fields" in reason,
    )


def test_rejects_empty_messages_list() -> None:
    reason = _rejects(raw_record={"messages": []})
    check(
        "FTF-0506",
        "an empty messages list is rejected",
        reason is not None and "non-empty" in reason,
    )


def test_rejects_non_mapping_raw_record() -> None:
    reason = _rejects(raw_record="not-a-mapping")
    check(
        "FTF-0506",
        "a non-mapping raw_record is rejected",
        reason is not None and "must be a mapping" in reason,
    )


def test_rejects_non_serializable_metadata() -> None:
    reason = _rejects(
        raw_record={"messages": [{"role": "user", "content": "hi"}], "metadata": {"bad": object()}}
    )
    check(
        "FTF-0506",
        "metadata that is not JSON-serializable is rejected rather than silently dropped",
        reason is not None and "not JSON-serializable" in reason,
    )


def main() -> None:
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()

    if FAILURES:
        print(f"FTF-0506 canonical conversation normalizer: {len(FAILURES)} failed")
        for failure in FAILURES:
            print(f"  {failure}")
        sys.exit(1)

    print("FTF-0506 canonical conversation normalizer: all checks passed")
    sys.exit(0)


if __name__ == "__main__":
    main()
