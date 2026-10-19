#!/usr/bin/env python3
"""FTF-0512 -- Normalized duplicate detector.

Runnable without any database and without any external network egress:
`detect_normalized_duplicates` and `compute_normalized_text_hash` are pure,
synchronous, no-I/O functions (this task's own `database_changes` clause --
"No independent schema change. Consume frozen contract.").

This task's own acceptance clause ("Canonicalized hash matching preserves
meaningful tool/ID semantics while detecting formatting duplicates") is
covered by `test_case_only_difference_is_a_duplicate`,
`test_whitespace_only_difference_is_a_duplicate`,
`test_punctuation_only_difference_is_a_duplicate`,
`test_generated_uuid_difference_is_a_duplicate`,
`test_generated_timestamp_difference_is_a_duplicate`,
`test_tool_call_id_name_preserved_verbatim_still_matches_when_identical`,
`test_normalized_duplicate_always_blocks_even_same_purpose`, and
`test_deterministic_group_ordering`.

This task's own negative_acceptance ("Normalization cannot collapse
distinct customer outcomes or strip relevant security constraints") is
covered by `test_different_tool_call_arguments_is_not_a_duplicate`,
`test_different_json_content_value_is_not_a_duplicate`,
`test_different_security_word_is_not_a_duplicate`,
`test_different_business_id_in_text_is_not_a_duplicate`, and
`test_metadata_excluded_but_content_still_distinguishes_records`.

Run directly: python3 tests/tasks/test_ftf_0512.py
"""

from __future__ import annotations

import sys
from collections.abc import Mapping
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from workers.data.normalized_duplicate_detector import (  # noqa: E402
    SUPPORTED_PURPOSES,
    NormalizedDuplicateDetectionError,
    compute_normalized_text_hash,
    detect_normalized_duplicates,
)

FAILURES: list[str] = []


def check(criterion_id: str, description: str, condition: bool) -> None:
    if not condition:
        FAILURES.append(f"FAIL [{criterion_id}] {description}")


def _text_content(*, role: str, text: str) -> dict[str, object]:
    return {"role": role, "content": [{"type": "text", "text": text}]}


def _conversation(*texts: str) -> dict[str, object]:
    roles = ["user", "assistant"]
    messages = [_text_content(role=roles[index % 2], text=text) for index, text in enumerate(texts)]
    return {"messages": messages, "metadata": {}}


def _conversation_with_tool_call(text: str, tool_call: Mapping[str, object]) -> dict[str, object]:
    messages: list[dict[str, object]] = [
        {"role": "user", "content": [{"type": "text", "text": text}]},
        {"role": "assistant", "content": [], "tool_calls": [tool_call]},
    ]
    return {"messages": messages, "metadata": {}}


def _record(**overrides: object) -> dict[str, object]:
    record: dict[str, object] = {
        "record_index": 0,
        "purpose": "training",
        "content": _conversation("Hello, how can I help you today?"),
    }
    record.update(overrides)
    return record


def _issue_codes(report: object, batch_position: int) -> set[str]:
    return {
        issue.code
        for issue in report.issues  # type: ignore[attr-defined]
        if issue.batch_position == batch_position
    }


def test_case_only_difference_is_a_duplicate() -> None:
    report = detect_normalized_duplicates(
        [
            _record(record_index=0, content=_conversation("Your order has shipped.")),
            _record(record_index=1, content=_conversation("YOUR ORDER HAS SHIPPED.")),
        ]
    )
    check(
        "FTF-0512",
        "acceptance: a case-only formatting difference is detected as a duplicate",
        len(report.duplicate_groups) == 1,
    )


def test_whitespace_only_difference_is_a_duplicate() -> None:
    report = detect_normalized_duplicates(
        [
            _record(record_index=0, content=_conversation("Your order has shipped.")),
            _record(record_index=1, content=_conversation("Your   order  has\tshipped.")),
        ]
    )
    check(
        "FTF-0512",
        "acceptance: a whitespace-only formatting difference is detected as a duplicate",
        len(report.duplicate_groups) == 1,
    )


def test_punctuation_only_difference_is_a_duplicate() -> None:
    report = detect_normalized_duplicates(
        [
            _record(record_index=0, content=_conversation("Your order has shipped.")),
            _record(record_index=1, content=_conversation("Your order has shipped!!!")),
        ]
    )
    check(
        "FTF-0512",
        "acceptance: a punctuation-only formatting difference is detected as a duplicate",
        len(report.duplicate_groups) == 1,
    )


def test_generated_uuid_difference_is_a_duplicate() -> None:
    report = detect_normalized_duplicates(
        [
            _record(
                record_index=0,
                content=_conversation(
                    "Ticket 3f2e1a10-9b4c-4d3e-8f1a-2b3c4d5e6f70 has been resolved."
                ),
            ),
            _record(
                record_index=1,
                content=_conversation(
                    "Ticket a1b2c3d4-e5f6-4789-abcd-ef0123456789 has been resolved."
                ),
            ),
        ]
    )
    check(
        "FTF-0512",
        "acceptance: two records differing only in an embedded generated UUID are "
        "detected as a duplicate (Section 10 method 3 'schema-aware' generated-ID rule)",
        len(report.duplicate_groups) == 1,
    )


def test_generated_timestamp_difference_is_a_duplicate() -> None:
    report = detect_normalized_duplicates(
        [
            _record(
                record_index=0,
                content=_conversation("Event recorded at 2026-01-01T12:00:00Z successfully."),
            ),
            _record(
                record_index=1,
                content=_conversation("Event recorded at 2026-06-15T08:30:45Z successfully."),
            ),
        ]
    )
    check(
        "FTF-0512",
        "acceptance: two records differing only in an embedded generated ISO-8601 "
        "timestamp are detected as a duplicate",
        len(report.duplicate_groups) == 1,
    )


def test_tool_call_id_name_preserved_verbatim_still_matches_when_identical() -> None:
    tool_call = {"id": "call_1", "name": "lookup_order", "arguments": {"order_id": "9"}}
    content_a = _conversation_with_tool_call("Hi there!", tool_call)
    content_b = _conversation_with_tool_call("HI THERE.", tool_call)
    report = detect_normalized_duplicates(
        [
            _record(record_index=0, content=content_a),
            _record(record_index=1, content=content_b),
        ]
    )
    check(
        "FTF-0512",
        "acceptance: identical tool calls plus a formatting-only text difference "
        "still match as a duplicate",
        len(report.duplicate_groups) == 1,
    )


def test_normalized_duplicate_always_blocks_even_same_purpose() -> None:
    report = detect_normalized_duplicates(
        [
            _record(record_index=0, purpose="training", content=_conversation("Hello there.")),
            _record(record_index=1, purpose="training", content=_conversation("HELLO THERE!")),
        ]
    )
    group = report.duplicate_groups[0]
    check(
        "FTF-0512",
        "acceptance: Section 10 'normalized match: always block' -- a same-purpose "
        "text-normalized duplicate is still blocking",
        not group.is_cross_purpose and group in report.blocking_groups,
    )


def test_cross_purpose_normalized_duplicate_is_also_blocking() -> None:
    report = detect_normalized_duplicates(
        [
            _record(record_index=0, purpose="training", content=_conversation("Hello there.")),
            _record(
                record_index=1,
                purpose="held_out_evaluation",
                content=_conversation("HELLO THERE!"),
            ),
        ]
    )
    group = report.duplicate_groups[0]
    check(
        "FTF-0512",
        "a cross-purpose text-normalized duplicate is blocking too",
        group.is_cross_purpose and group in report.blocking_groups,
    )


def test_deterministic_group_ordering() -> None:
    records = [
        _record(record_index=0, content=_conversation("Alpha message.")),
        _record(record_index=1, content=_conversation("ALPHA MESSAGE!")),
        _record(record_index=2, content=_conversation("Beta message.")),
        _record(record_index=3, content=_conversation("BETA MESSAGE!")),
    ]
    first = detect_normalized_duplicates(records)
    second = detect_normalized_duplicates(records)
    check(
        "FTF-0512",
        "acceptance: repeated calls on the same input produce identical, "
        "digest-sorted group ordering",
        first.duplicate_groups == second.duplicate_groups and len(first.duplicate_groups) == 2,
    )


def test_different_tool_call_arguments_is_not_a_duplicate() -> None:
    content_a = _conversation_with_tool_call(
        "Processing your request.",
        {"id": "call_1", "name": "issue_refund", "arguments": {"amount": 10}},
    )
    content_b = _conversation_with_tool_call(
        "Processing your request.",
        {"id": "call_1", "name": "issue_refund", "arguments": {"amount": 10000}},
    )
    report = detect_normalized_duplicates(
        [
            _record(record_index=0, content=content_a),
            _record(record_index=1, content=content_b),
        ]
    )
    check(
        "FTF-0512",
        "negative_acceptance: two records differing only in a tool call's "
        "arguments (a distinct customer outcome -- a different refund amount) "
        "are never collapsed into one duplicate group",
        len(report.duplicate_groups) == 0,
    )


def test_different_json_content_value_is_not_a_duplicate() -> None:
    content_a = {
        "messages": [
            {"role": "user", "content": [{"type": "text", "text": "Status?"}]},
            {
                "role": "tool",
                "content": [{"type": "json", "value": {"status": "shipped"}}],
                "tool_call_id": "call_1",
                "name": "lookup_order",
            },
        ],
        "metadata": {},
    }
    content_b = {
        "messages": [
            {"role": "user", "content": [{"type": "text", "text": "Status?"}]},
            {
                "role": "tool",
                "content": [{"type": "json", "value": {"status": "cancelled"}}],
                "tool_call_id": "call_1",
                "name": "lookup_order",
            },
        ],
        "metadata": {},
    }
    report = detect_normalized_duplicates(
        [
            _record(record_index=0, content=content_a),
            _record(record_index=1, content=content_b),
        ]
    )
    check(
        "FTF-0512",
        "negative_acceptance: two records differing only in a json tool-result "
        "value (a distinct customer outcome -- shipped vs. cancelled) are never "
        "collapsed into one duplicate group",
        len(report.duplicate_groups) == 0,
    )


def test_different_security_word_is_not_a_duplicate() -> None:
    report = detect_normalized_duplicates(
        [
            _record(
                record_index=0,
                content=_conversation("Refunds are forbidden for this account."),
            ),
            _record(
                record_index=1,
                content=_conversation("Refunds are allowed for this account."),
            ),
        ]
    )
    check(
        "FTF-0512",
        "negative_acceptance: normalization cannot strip a relevant security "
        "constraint -- 'forbidden' vs. 'allowed' is a real word difference, "
        "not formatting, and must never collapse",
        len(report.duplicate_groups) == 0,
    )


def test_different_business_id_in_text_is_not_a_duplicate() -> None:
    report = detect_normalized_duplicates(
        [
            _record(record_index=0, content=_conversation("Order 10023 has shipped.")),
            _record(record_index=1, content=_conversation("Order 88471 has shipped.")),
        ]
    )
    check(
        "FTF-0512",
        "negative_acceptance: a plain business identifier embedded in text (not "
        "a UUID/timestamp-shaped generated value) is never redacted, so two "
        "records naming different orders never collapse",
        len(report.duplicate_groups) == 0,
    )


def test_metadata_excluded_but_content_still_distinguishes_records() -> None:
    content_a = _conversation("Thanks for reaching out.")
    content_a["metadata"] = {"source": "zendesk"}
    content_b = _conversation("Thanks for reaching out!!!")
    content_b["metadata"] = {"source": "intercom"}
    report = detect_normalized_duplicates(
        [
            _record(record_index=0, content=content_a),
            _record(record_index=1, content=content_b),
        ]
    )
    check(
        "FTF-0512",
        "metadata (which describes the import, not the conversation) is excluded "
        "from the hash -- two records with different metadata but formatting-only "
        "text differences still match",
        len(report.duplicate_groups) == 1,
    )


def test_unknown_content_surfaces_issue_not_silently_dropped() -> None:
    report = detect_normalized_duplicates([_record(record_index=0, content={"not": "canonical"})])
    check(
        "FTF-0512",
        "negative_acceptance: a malformed content document is surfaced as an "
        "unknown_content issue, not silently dropped from the report",
        "unknown_content" in _issue_codes(report, 0),
    )


def test_invalid_purpose_surfaces_issue() -> None:
    report = detect_normalized_duplicates([_record(record_index=0, purpose="unapproved_purpose")])
    check(
        "FTF-0512",
        "an unrecognized purpose is surfaced as an issue, not silently accepted",
        "invalid_purpose" in _issue_codes(report, 0),
    )


def test_raises_on_non_sequence_batch() -> None:
    try:
        detect_normalized_duplicates("not-a-sequence")  # type: ignore[arg-type]
    except NormalizedDuplicateDetectionError:
        pass
    else:
        check(
            "FTF-0512",
            "a non-sequence batch raises, there is no row to attach evidence to",
            False,
        )


def test_raises_on_empty_batch() -> None:
    try:
        detect_normalized_duplicates([])
    except NormalizedDuplicateDetectionError:
        pass
    else:
        check("FTF-0512", "an empty batch raises rather than silently reporting nothing", False)


def test_compute_normalized_text_hash_rejects_non_canonical_shape() -> None:
    try:
        compute_normalized_text_hash({"messages": []})
    except NormalizedDuplicateDetectionError:
        pass
    else:
        check(
            "FTF-0512",
            "compute_normalized_text_hash rejects a document missing the "
            "canonical 'metadata' key rather than guessing one",
            False,
        )


def test_compute_normalized_text_hash_rejects_extra_key() -> None:
    try:
        compute_normalized_text_hash(
            {"messages": [{"role": "user", "content": []}], "metadata": {}, "extra": 1}
        )
    except NormalizedDuplicateDetectionError:
        pass
    else:
        check(
            "FTF-0512",
            "compute_normalized_text_hash rejects a document with an extra "
            "top-level key, not only one with a missing key",
            False,
        )


def test_compute_normalized_text_hash_rejects_non_mapping_content_even_if_keys_match() -> None:
    try:
        # not a Mapping, but set(content) happens to equal the required key
        # set -- the two guard sub-conditions must be OR'd, not AND'd.
        compute_normalized_text_hash(["messages", "metadata"])
    except NormalizedDuplicateDetectionError:
        pass
    else:
        check(
            "FTF-0512",
            "compute_normalized_text_hash rejects a non-Mapping content even "
            "when its elements happen to match the required key names",
            False,
        )


def test_compute_normalized_text_hash_rejects_empty_messages_list() -> None:
    try:
        compute_normalized_text_hash({"messages": [], "metadata": {}})
    except NormalizedDuplicateDetectionError:
        pass
    else:
        check(
            "FTF-0512",
            "compute_normalized_text_hash rejects an empty messages list "
            "rather than hashing a vacuous document",
            False,
        )


def test_supported_purposes_matches_ftf_0500_vocabulary() -> None:
    check(
        "FTF-0512",
        "SUPPORTED_PURPOSES mirrors FTF-0500's own closed allowed_purposes vocabulary "
        "verbatim, not a second invented set",
        SUPPORTED_PURPOSES
        == frozenset(
            {
                "training",
                "validation",
                "held_out_evaluation",
                "candidate_development",
                "baseline_only",
            }
        ),
    )


def test_requalification_role_json_value_and_tool_ids_stay_distinct() -> None:
    """FTF-0512-F1 round 3 item 4 (Law 15): role structure, JSON tool-result
    values (case, width) and tool-call identifiers are meaningful and survive
    byte-for-byte; none is a formatting variant."""

    def roles(first: str, second: str) -> dict[str, object]:
        return {
            "messages": [
                _text_content(role=first, text="hi"),
                _text_content(role=second, text="ok"),
            ],
            "metadata": {},
        }

    check(
        "FTF-0512",
        "swapping roles with identical text is a different conversation",
        compute_normalized_text_hash(roles("user", "assistant"))
        != compute_normalized_text_hash(roles("assistant", "user")),
    )
    check(
        "FTF-0512",
        "a user/assistant pair is not a user/user pair",
        compute_normalized_text_hash(roles("user", "assistant"))
        != compute_normalized_text_hash(roles("user", "user")),
    )

    def json_value(value: object) -> dict[str, object]:
        return {
            "messages": [
                _text_content(role="user", text="go"),
                {"role": "tool", "content": [{"type": "json", "value": value}]},
            ],
            "metadata": {},
        }

    for left, right in [("Approved", "approved"), ("ok", "ｏｋ"), ({"A": "B"}, {"a": "b"})]:
        check(
            "FTF-0512",
            f"json tool-result value {left!r} vs {right!r} are distinct outcomes",
            compute_normalized_text_hash(json_value(left))
            != compute_normalized_text_hash(json_value(right)),
        )

    def call_id(identifier: str) -> dict[str, object]:
        return _conversation_with_tool_call(
            "go", {"id": identifier, "name": "lookup", "arguments": {"k": "v"}}
        )

    for variant in ["CALL_1", "ｃａｌｌ_1", "call\u200b_1"]:
        check(
            "FTF-0512",
            f"tool call id {variant!r} is a different identifier from 'call_1'",
            compute_normalized_text_hash(call_id("call_1"))
            != compute_normalized_text_hash(call_id(variant)),
        )


def main() -> None:
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()

    if FAILURES:
        print(f"FTF-0512 normalized duplicate detector: {len(FAILURES)} failed")
        for failure in FAILURES:
            print(f"  {failure}")
        sys.exit(1)

    print("FTF-0512 normalized duplicate detector: all checks passed")
    sys.exit(0)


if __name__ == "__main__":
    main()
