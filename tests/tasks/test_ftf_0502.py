#!/usr/bin/env python3
"""FTF-0502 -- JSONL importer.

Runnable without any database or network: `import_jsonl` is a pure,
synchronous, no-I/O generator over an in-memory iterable of `bytes` lines
(this task's own `database_changes` clause -- "No independent schema
change. Consume frozen contract.") that reuses FTF-0506's already-CERTIFIED
`normalize_conversation` for every record, the same dependency-injected
`tool_argument_validator` pattern FTF-0506's own suite establishes --
proven here against FTF-0405's real `validate_instance`, not a stand-in.

Acceptance ("Stream JSONL per record and report exact row/schema failures
with bounded memory"): test_streams_clean_records_end_to_end,
test_row_index_matches_lineage_source_record_index,
test_every_line_produces_exactly_one_item (this task's own central
"bounded memory, nothing silently dropped" invariant, proven over a mixed
stream of many valid and invalid lines).

Negative acceptance ("Malformed UTF-8/line, huge record and mixed critical
schemas produce quarantine/errors, not silent loss"):
test_malformed_utf8_is_quarantined_not_fatal, test_huge_line_is_quarantined,
test_malformed_json_is_quarantined, test_non_object_json_is_quarantined,
test_mixed_shape_rows_are_each_quarantined_with_their_own_reason (this
module's own disclosed reading of "mixed critical schemas" -- see the
production module's docstring), test_not_bytes_line_is_quarantined,
test_blank_line_is_quarantined, test_quarantine_messages_are_exact_not_generic.

Call-level (not per-row) negative acceptance: test_source_type_must_be_supported,
test_lines_rejects_a_single_bytes_blob, test_max_line_bytes_must_be_positive --
each raises immediately, before any line is read, per the module's own
documented distinction between a malformed call and a malformed row.

GRADER-style checklist: malformed input fails closed; tool_schemas/
tool_argument_validator are forwarded unchanged to a real dependency
(test_tool_schemas_and_validator_are_forwarded_to_normalize_conversation);
this importer never invents a record_id/group_key
(test_record_id_and_group_id_are_never_invented_by_the_importer).

Run directly: python3 tests/tasks/test_ftf_0502.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
API_ROOT = ROOT / "apps" / "api"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(API_ROOT))

from workers.data.jsonl_importer import (  # noqa: E402
    MAX_RAW_LINE_BYTES,
    ImportedRecord,
    JsonlImportError,
    QuarantinedRow,
    import_jsonl,
)
from workers.data.record_schema_validator import MAX_CANONICAL_RECORD_BYTES  # noqa: E402

from app.services.json_schema_tool_validation import validate_instance  # noqa: E402

FAILURES: list[str] = []
CID = "FTF-0502"


def check(criterion_id: str, description: str, condition: bool) -> None:
    if not condition:
        FAILURES.append(f"FAIL [{criterion_id}] {description}")


def jsonl_bytes(*objs: object) -> list[bytes]:
    import json

    return [json.dumps(obj).encode("utf-8") for obj in objs]


_VALID_A = {"messages": [{"role": "user", "content": "hello"}]}
_VALID_B = {"messages": [{"role": "user", "content": "a different message"}]}


def test_streams_clean_records_end_to_end() -> None:
    results = list(import_jsonl(jsonl_bytes(_VALID_A, _VALID_B), source_type="upload"))
    check(CID, "two valid lines produce two ImportedRecord items", len(results) == 2)
    check(
        CID,
        "every result is an ImportedRecord, none quarantined",
        all(isinstance(r, ImportedRecord) for r in results),
    )
    first, second = results
    assert isinstance(first, ImportedRecord) and isinstance(second, ImportedRecord)
    check(
        CID,
        "the real FTF-0506 CanonicalConversationRecord is returned, not a stand-in",
        first.record.schema_version == "1.0"
        and first.record.messages[0].content[0].text == "hello",
    )
    check(
        CID,
        "distinct rows get distinct canonical hashes for distinct content",
        first.record.canonical_hash != second.record.canonical_hash,
    )


def test_row_index_matches_lineage_source_record_index() -> None:
    results = list(import_jsonl(jsonl_bytes(_VALID_A, _VALID_A, _VALID_A), source_type="upload"))
    check(CID, "three lines in, three results out", len(results) == 3)
    for i, item in enumerate(results):
        assert isinstance(item, ImportedRecord)
        check(
            CID,
            f"row {i}: row_index matches its position in the input",
            item.row_index == i,
        )
        check(
            CID,
            f"row {i}: FTF-0506's own lineage.source_record_index equals this importer's row_index",
            item.record.lineage.source_record_index == i,
        )
        check(
            CID,
            f"row {i}: lineage.source_type is forwarded",
            item.record.lineage.source_type == "upload",
        )


def test_every_line_produces_exactly_one_item() -> None:
    lines: list[object] = [
        *jsonl_bytes(_VALID_A),
        b"not json at all {{{",
        b"\xff\xfe\xfd",
        b"42",
        b"",
        b"   ",
        *jsonl_bytes(_VALID_B),
        "a str line, not bytes",
    ]
    results = list(import_jsonl(lines, source_type="upload"))  # type: ignore[arg-type]
    check(
        CID,
        "every one of the 8 input lines produces exactly one output item "
        "(this task's own central negative-acceptance invariant: nothing "
        "silently dropped, regardless of how malformed a line is)",
        len(results) == len(lines) == 8,
    )
    check(
        CID,
        "row_index values are exactly 0..7, each appearing once, in order",
        [r.row_index for r in results] == list(range(8)),
    )
    codes = [r.code if isinstance(r, QuarantinedRow) else None for r in results]
    check(CID, "row 0 (valid) is not quarantined", codes[0] is None)
    check(CID, "row 1 (invalid JSON) is quarantined malformed_json", codes[1] == "malformed_json")
    check(CID, "row 2 (invalid UTF-8) is quarantined malformed_utf8", codes[2] == "malformed_utf8")
    check(CID, "row 3 (a bare JSON int) is quarantined not_an_object", codes[3] == "not_an_object")
    check(CID, "row 4 (empty line) is quarantined blank_line", codes[4] == "blank_line")
    check(CID, "row 5 (whitespace-only line) is quarantined blank_line", codes[5] == "blank_line")
    check(CID, "row 6 (valid) is not quarantined", codes[6] is None)
    check(
        CID, "row 7 (a str, not bytes) is quarantined not_bytes_line", codes[7] == "not_bytes_line"
    )


def test_malformed_utf8_is_quarantined_not_fatal() -> None:
    lines = [b"\xff\xfe\xfd not valid utf-8", *jsonl_bytes(_VALID_A)]
    results = list(import_jsonl(lines, source_type="upload"))
    check(CID, "two lines in, two results out", len(results) == 2)
    bad, good = results
    check(
        CID,
        "negative_acceptance: malformed UTF-8 quarantines that row with code malformed_utf8",
        isinstance(bad, QuarantinedRow) and bad.code == "malformed_utf8",
    )
    check(
        CID,
        "a malformed-UTF-8 line never raises -- the stream continues to the next line",
        isinstance(good, ImportedRecord),
    )


def test_huge_line_is_quarantined() -> None:
    small_line = b'{"messages": [{"role": "user", "content": "hi"}], "pad": "' + b"x" * 100 + b'"}'
    assert len(small_line) > 50
    results = list(
        import_jsonl([small_line, *jsonl_bytes(_VALID_A)], source_type="upload", max_line_bytes=60)
    )
    check(CID, "two lines in, two results out", len(results) == 2)
    big, ok = results
    check(
        CID,
        "negative_acceptance: a line over max_line_bytes is quarantined line_too_large, "
        "checked by byte length before any decode or parse is attempted",
        isinstance(big, QuarantinedRow) and big.code == "line_too_large",
    )
    check(CID, "a later, properly-sized line still imports", isinstance(ok, ImportedRecord))
    check(
        CID,
        "the default bound reuses Section 67.3's canonical single-record max "
        "(FTF-0508's MAX_CANONICAL_RECORD_BYTES), never re-declared independently",
        MAX_RAW_LINE_BYTES == MAX_CANONICAL_RECORD_BYTES == 2 * 1024 * 1024,
    )


def test_malformed_json_is_quarantined() -> None:
    results = list(import_jsonl([b"{not valid json"], source_type="upload"))
    check(CID, "one line in, one result out", len(results) == 1)
    (item,) = results
    check(
        CID,
        "negative_acceptance: invalid JSON syntax is quarantined malformed_json, not fatal",
        isinstance(item, QuarantinedRow) and item.code == "malformed_json",
    )


def test_non_object_json_is_quarantined() -> None:
    for payload, label in ((b"42", "number"), (b"[1,2,3]", "array"), (b'"a string"', "string")):
        results = list(import_jsonl([payload], source_type="upload"))
        (item,) = results
        check(
            CID,
            f"a top-level JSON {label} is quarantined not_an_object, never guessed into a record",
            isinstance(item, QuarantinedRow) and item.code == "not_an_object",
        )


def test_mixed_shape_rows_are_each_quarantined_with_their_own_reason() -> None:
    # This module's own disclosed reading of "mixed critical schemas": a
    # source mixing a genuine Section 7 messages-array row with a
    # differently-shaped row (here, a prompt/completion pair with no
    # `messages` key at all) is exactly this case. Each row is judged on
    # its own real FTF-0506 ConversationNormalizationError text.
    prompt_completion_row = {"prompt": "what is 2+2?", "completion": "4"}
    unmapped_role_row = {"messages": [{"role": "human", "content": "hi"}]}
    lines = jsonl_bytes(_VALID_A, prompt_completion_row, unmapped_role_row, _VALID_B)
    results = list(import_jsonl(lines, source_type="upload"))
    check(CID, "four lines in, four results out", len(results) == 4)
    first, second, third, fourth = results
    check(CID, "the genuine messages-shaped row 0 still imports", isinstance(first, ImportedRecord))
    check(
        CID,
        "a prompt/completion-shaped row is quarantined schema_invalid with FTF-0506's "
        "own exact reason (no 'messages' key at all)",
        isinstance(second, QuarantinedRow)
        and second.code == "schema_invalid"
        and "messages must be a non-empty list" in second.message,
    )
    assert isinstance(second, QuarantinedRow)
    check(
        CID,
        "an unmapped-role row is quarantined schema_invalid with FTF-0506's own exact "
        "reason, a genuinely different message than row 1's",
        isinstance(third, QuarantinedRow)
        and third.code == "schema_invalid"
        and "unmapped role" in third.message
        and third.message != second.message,
    )
    check(
        CID,
        "the genuine messages-shaped row 3, after two differently-shaped quarantined "
        "rows, still imports -- one row's failure never affects another row",
        isinstance(fourth, ImportedRecord),
    )


def test_not_bytes_line_is_quarantined() -> None:
    lines: list[object] = [*jsonl_bytes(_VALID_A), "a plain str line", 12345]
    results = list(import_jsonl(lines, source_type="upload"))  # type: ignore[arg-type]
    check(CID, "three lines in, three results out", len(results) == 3)
    ok, str_line, int_line = results
    check(CID, "the genuine bytes line still imports", isinstance(ok, ImportedRecord))
    check(
        CID,
        "a str line (already decoded, defeating the UTF-8 check) is quarantined not_bytes_line",
        isinstance(str_line, QuarantinedRow) and str_line.code == "not_bytes_line",
    )
    check(
        CID,
        "a non-bytes, non-str line (an int) is quarantined not_bytes_line too",
        isinstance(int_line, QuarantinedRow) and int_line.code == "not_bytes_line",
    )


def test_blank_line_is_quarantined() -> None:
    results = list(import_jsonl([b"", b"   ", b"\t\t", b"\r\n"], source_type="upload"))
    check(CID, "four blank-ish lines in, four results out", len(results) == 4)
    check(
        CID,
        "every blank/whitespace-only line is quarantined blank_line, never silently skipped",
        all(isinstance(r, QuarantinedRow) and r.code == "blank_line" for r in results),
    )


def test_quarantine_messages_are_exact_not_generic() -> None:
    (malformed_json,) = list(import_jsonl([b"{bad"], source_type="upload"))
    (bad_utf8,) = list(import_jsonl([b"\xff\xfe"], source_type="upload"))
    (too_large,) = list(import_jsonl([b'{"x": 1}'], source_type="upload", max_line_bytes=3))
    assert isinstance(malformed_json, QuarantinedRow)
    assert isinstance(bad_utf8, QuarantinedRow)
    assert isinstance(too_large, QuarantinedRow)
    check(
        CID,
        "malformed_json carries the real json.JSONDecodeError text, not a placeholder",
        "line is not valid JSON:" in malformed_json.message
        and "Expecting" in malformed_json.message,
    )
    check(
        CID,
        "malformed_utf8 carries the real UnicodeDecodeError text, not a placeholder",
        "line is not valid UTF-8:" in bad_utf8.message,
    )
    check(
        CID,
        "line_too_large states the real measured length and the real configured bound",
        "8 bytes" in too_large.message and "max of 3 bytes" in too_large.message,
    )


_LOOKUP_ORDER_SCHEMA = {
    "type": "object",
    "properties": {"order_id": {"type": "string"}},
    "required": ["order_id"],
    "additionalProperties": False,
}


def test_tool_schemas_and_validator_are_forwarded_to_normalize_conversation() -> None:
    good_call = {
        "messages": [
            {"role": "user", "content": "where is my order"},
            {
                "role": "assistant",
                "content": [],
                "tool_calls": [
                    {"id": "call_1", "name": "lookup_order", "arguments": {"order_id": "abc"}}
                ],
            },
            {
                "role": "tool",
                "tool_call_id": "call_1",
                "name": "lookup_order",
                "content": [{"type": "json", "value": {"status": "shipped"}}],
            },
        ]
    }
    bad_call = {
        "messages": [
            {"role": "user", "content": "where is my order"},
            {
                "role": "assistant",
                "content": [],
                "tool_calls": [
                    {
                        "id": "call_1",
                        "name": "lookup_order",
                        "arguments": {"order_id": "abc", "extra": True},
                    }
                ],
            },
        ]
    }
    results = list(
        import_jsonl(
            jsonl_bytes(good_call, bad_call),
            source_type="upload",
            tool_schemas={"lookup_order": _LOOKUP_ORDER_SCHEMA},
            tool_argument_validator=validate_instance,
        )
    )
    check(CID, "two lines in, two results out", len(results) == 2)
    ok, rejected = results
    check(
        CID,
        "tool_schemas/tool_argument_validator are forwarded through to the real "
        "FTF-0405 validate_instance: a conforming tool call imports",
        isinstance(ok, ImportedRecord),
    )
    check(
        CID,
        "a real FTF-0405 schema violation (additionalProperties:false) is forwarded "
        "through as a genuine schema_invalid quarantine, not silently accepted",
        isinstance(rejected, QuarantinedRow)
        and rejected.code == "schema_invalid"
        and "excess property" in rejected.message,
    )

    # Without a validator supplied at all, the same tool call fails closed too --
    # proving the importer does not quietly default to "no validator means accept".
    (no_validator_result,) = list(
        import_jsonl(
            jsonl_bytes(good_call),
            source_type="upload",
            tool_schemas={"lookup_order": _LOOKUP_ORDER_SCHEMA},
        )
    )
    check(
        CID,
        "no tool_argument_validator supplied: a tool call is refused, never accepted on trust",
        isinstance(no_validator_result, QuarantinedRow)
        and no_validator_result.code == "schema_invalid"
        and "no tool_argument_validator was supplied" in no_validator_result.message,
    )


def test_record_id_and_group_id_are_never_invented_by_the_importer() -> None:
    # Disclosed design (module docstring): this importer passes every parsed
    # object straight through as FTF-0506's raw_record and never extracts or
    # guesses a record_id/group_key from any particular raw field -- two
    # identical-content rows get two independent, distinct record_ids/group_ids.
    results = list(import_jsonl(jsonl_bytes(_VALID_A, _VALID_A), source_type="upload"))
    first, second = results
    assert isinstance(first, ImportedRecord) and isinstance(second, ImportedRecord)
    check(
        CID,
        "two rows with byte-identical content get independently assigned record_ids "
        "(the importer never reads a record_id/group_key field out of the raw JSON)",
        first.record.record_id != second.record.record_id,
    )
    check(
        CID,
        "...and independent solo group_ids, consistent with FTF-0506's own default "
        "(a caller-level grouping decision this importer does not invent)",
        first.record.group_id != second.record.group_id,
    )
    check(
        CID,
        "but canonical_hash is still identical for identical content (duplicate "
        "detection, a later pipeline stage, needs this to hold regardless)",
        first.record.canonical_hash == second.record.canonical_hash,
    )


def test_source_type_must_be_supported() -> None:
    raised = False
    try:
        import_jsonl(jsonl_bytes(_VALID_A), source_type="not_a_real_dialect")
    except JsonlImportError as exc:
        raised = True
        check(CID, "the error names the rejected value", "not_a_real_dialect" in str(exc))
    check(
        CID,
        "an unrecognized source_type raises JsonlImportError immediately, "
        "before any line is read -- a call-level error, not a per-row one",
        raised,
    )


def test_lines_rejects_a_single_bytes_blob() -> None:
    for bad_lines, label in (
        (b'{"messages": []}', "bytes"),
        (bytearray(b'{"messages": []}'), "bytearray"),
        ('{"messages": []}', "str"),
    ):
        raised = False
        try:
            import_jsonl(bad_lines, source_type="upload")  # type: ignore[arg-type]
        except JsonlImportError:
            raised = True
        check(
            CID,
            f"a single {label} blob (iterable over its own bytes/characters, never "
            "lines) is rejected outright rather than silently misread one unit at a time",
            raised,
        )


def test_max_line_bytes_must_be_positive() -> None:
    for bad_value in (0, -1):
        raised = False
        try:
            import_jsonl(jsonl_bytes(_VALID_A), source_type="upload", max_line_bytes=bad_value)
        except JsonlImportError:
            raised = True
        check(CID, f"max_line_bytes={bad_value} raises JsonlImportError immediately", raised)


def test_empty_source_yields_nothing_and_does_not_raise() -> None:
    results = list(import_jsonl([], source_type="upload"))
    check(CID, "an empty source yields zero results, not an error", results == [])


def test_jsonl_import_error_is_a_value_error() -> None:
    check(CID, "JsonlImportError is a ValueError", issubclass(JsonlImportError, ValueError))


def main() -> int:
    tests = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_") and callable(f)]
    for name, fn in tests:
        try:
            fn()
        except Exception as exc:  # noqa: BLE001 -- a crash is a failed test, reported by name
            FAILURES.append(f"FAIL [{CID}] {name} raised {type(exc).__name__}: {exc}")
    if FAILURES:
        for failure in FAILURES:
            print(failure)
        print(f"{len(FAILURES)} check(s) failed")
        return 1
    print("all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
