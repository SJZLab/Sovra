#!/usr/bin/env python3
"""FTF-0503 -- JSON importer.

Runnable without any database or network: `import_json` is a pure,
synchronous, no-I/O generator over an in-memory whole JSON document
(`bytes`) (this task's own `database_changes` clause -- "No independent
schema change. Consume frozen contract.") that reuses FTF-0506's
already-CERTIFIED `normalize_conversation` for every record, and reuses
FTF-0502's `ImportedRecord`/`QuarantinedRow` dataclasses directly rather
than a second, structurally identical pair (reuse-first, Section 77.1).

Acceptance ("Parse approved JSON conversation/container structures into
canonical records without loading unbounded file"):
test_conversation_top_level_object_imports_as_one_row,
test_container_top_level_array_imports_each_element,
test_row_index_matches_lineage_source_record_index,
test_empty_container_yields_nothing_and_does_not_raise,
test_document_size_is_checked_before_any_decode_or_parse,
test_nesting_depth_is_checked_before_json_loads.

Negative acceptance ("Deep nesting, malformed objects and unsupported
structures fail with actionable mapping errors"):
test_document_too_large_is_quarantined, test_nesting_too_deep_is_quarantined,
test_malformed_json_is_quarantined, test_malformed_utf8_is_quarantined_not_fatal,
test_unsupported_top_level_type_is_quarantined,
test_container_element_not_object_is_quarantined,
test_mixed_shape_elements_are_each_quarantined_with_their_own_reason,
test_quarantine_messages_are_exact_not_generic.

Call-level (not per-row/document) negative acceptance:
test_source_type_must_be_supported, test_content_rejects_non_bytes_types,
test_max_total_bytes_must_be_positive, test_max_nesting_depth_must_be_positive --
each raises immediately, before any byte is read.

GRADER-style checklist: malformed input fails closed; tool_schemas/
tool_argument_validator are forwarded unchanged to a real dependency
(test_tool_schemas_and_validator_are_forwarded_to_normalize_conversation);
this importer never invents a record_id/group_key
(test_record_id_and_group_id_are_never_invented_by_the_importer); brackets
inside JSON string literals never inflate the measured nesting depth
(test_string_literal_brackets_do_not_count_toward_nesting_depth,
test_escaped_quote_inside_string_does_not_end_it_early).

Run directly: python3 tests/tasks/test_ftf_0503.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
API_ROOT = ROOT / "apps" / "api"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(API_ROOT))

from workers.data.json_importer import (  # noqa: E402
    MAX_JSON_DOCUMENT_BYTES,
    MAX_NESTING_DEPTH,
    ImportedRecord,
    JsonImportError,
    QuarantinedRow,
    _max_nesting_depth,
    import_json,
)
from workers.data.jsonl_importer import ImportedRecord as _JsonlImportedRecord  # noqa: E402
from workers.data.jsonl_importer import QuarantinedRow as _JsonlQuarantinedRow  # noqa: E402

from app.services.json_schema_tool_validation import validate_instance  # noqa: E402

FAILURES: list[str] = []
CID = "FTF-0503"


def check(criterion_id: str, description: str, condition: bool) -> None:
    if not condition:
        FAILURES.append(f"FAIL [{criterion_id}] {description}")


def json_bytes(obj: object) -> bytes:
    return json.dumps(obj).encode("utf-8")


_VALID_A = {"messages": [{"role": "user", "content": "hello"}]}
_VALID_B = {"messages": [{"role": "user", "content": "a different message"}]}


def test_types_are_reused_from_jsonl_importer_not_redeclared() -> None:
    check(
        CID,
        "ImportedRecord is the exact same class object as FTF-0502's, not a "
        "second, structurally identical dataclass",
        ImportedRecord is _JsonlImportedRecord,
    )
    check(
        CID,
        "QuarantinedRow is the exact same class object as FTF-0502's",
        QuarantinedRow is _JsonlQuarantinedRow,
    )


def test_conversation_top_level_object_imports_as_one_row() -> None:
    results = list(import_json(json_bytes(_VALID_A), source_type="upload"))
    check(CID, "a top-level object produces exactly one result", len(results) == 1)
    (item,) = results
    check(CID, "it is an ImportedRecord, not quarantined", isinstance(item, ImportedRecord))
    assert isinstance(item, ImportedRecord)
    check(CID, "row_index is 0 for a single-conversation document", item.row_index == 0)
    check(
        CID,
        "the real FTF-0506 CanonicalConversationRecord is returned, not a stand-in",
        item.record.schema_version == "1.0" and item.record.messages[0].content[0].text == "hello",
    )


def test_container_top_level_array_imports_each_element() -> None:
    results = list(import_json(json_bytes([_VALID_A, _VALID_B]), source_type="upload"))
    check(CID, "two elements in, two results out", len(results) == 2)
    check(
        CID,
        "every result is an ImportedRecord, none quarantined",
        all(isinstance(r, ImportedRecord) for r in results),
    )
    first, second = results
    assert isinstance(first, ImportedRecord) and isinstance(second, ImportedRecord)
    check(
        CID,
        "distinct elements get distinct canonical hashes for distinct content",
        first.record.canonical_hash != second.record.canonical_hash,
    )


def test_row_index_matches_lineage_source_record_index() -> None:
    results = list(import_json(json_bytes([_VALID_A, _VALID_A, _VALID_A]), source_type="upload"))
    check(CID, "three elements in, three results out", len(results) == 3)
    for i, item in enumerate(results):
        assert isinstance(item, ImportedRecord)
        check(CID, f"element {i}: row_index matches its position", item.row_index == i)
        check(
            CID,
            f"element {i}: FTF-0506's own lineage.source_record_index equals row_index",
            item.record.lineage.source_record_index == i,
        )
        check(
            CID,
            f"element {i}: lineage.source_type is forwarded",
            item.record.lineage.source_type == "upload",
        )


def test_empty_container_yields_nothing_and_does_not_raise() -> None:
    results = list(import_json(json_bytes([]), source_type="upload"))
    check(CID, "an empty container yields zero results, not an error", results == [])


def test_document_size_is_checked_before_any_decode_or_parse() -> None:
    # A document that is not even valid UTF-8/JSON still gets the size
    # verdict first, proving the byte-length check runs before decode/parse
    # are ever attempted (the module's own documented ordering).
    oversized_garbage = b"\xff" * 100
    (item,) = list(import_json(oversized_garbage, source_type="upload", max_total_bytes=50))
    check(
        CID,
        "an oversized document is quarantined document_too_large even though its "
        "bytes are not valid UTF-8 -- the size check runs first",
        isinstance(item, QuarantinedRow) and item.code == "document_too_large",
    )


def test_document_too_large_is_quarantined() -> None:
    small = json_bytes(_VALID_A)
    (item,) = list(import_json(small, source_type="upload", max_total_bytes=len(small) - 1))
    check(
        CID,
        "negative_acceptance: a document over max_total_bytes is quarantined "
        "document_too_large, checked by byte length before any decode or parse",
        isinstance(item, QuarantinedRow) and item.code == "document_too_large",
    )
    check(
        CID,
        "the default bound is a disclosed, independently configurable constant",
        MAX_JSON_DOCUMENT_BYTES == 64 * 1024 * 1024,
    )


def test_document_size_bound_is_strictly_greater_than() -> None:
    small = json_bytes(_VALID_A)
    (exact,) = list(import_json(small, source_type="upload", max_total_bytes=len(small)))
    check(
        CID,
        "a document exactly max_total_bytes long is accepted ('exceeding' means strictly over)",
        isinstance(exact, ImportedRecord),
    )
    (over,) = list(import_json(small, source_type="upload", max_total_bytes=len(small) - 1))
    check(
        CID,
        "one byte over max_total_bytes is quarantined",
        isinstance(over, QuarantinedRow) and over.code == "document_too_large",
    )


def test_nesting_too_deep_is_quarantined() -> None:
    # "[" * n + "]" * n is a document whose deepest nesting is exactly n.
    content = (b"[" * 5) + (b"]" * 5)
    check(CID, "sanity: the probe document is well-formed JSON", json.loads(content) is not None)
    (too_deep,) = list(import_json(content, source_type="upload", max_nesting_depth=4))
    check(
        CID,
        "negative_acceptance: nesting over max_nesting_depth is quarantined "
        "nesting_too_deep, checked before json.loads is ever called",
        isinstance(too_deep, QuarantinedRow) and too_deep.code == "nesting_too_deep",
    )
    check(
        CID,
        "the default bound is a disclosed, independently configurable constant",
        MAX_NESTING_DEPTH == 64,
    )


def test_nesting_depth_bound_is_strictly_greater_than() -> None:
    content = (b"[" * 5) + (b"]" * 5)
    (at_bound,) = list(import_json(content, source_type="upload", max_nesting_depth=5))
    check(
        CID,
        "a document exactly max_nesting_depth deep is accepted at the document-level "
        "stage ('exceeding' means strictly over) -- it proceeds to element dispatch",
        not (isinstance(at_bound, QuarantinedRow) and at_bound.code == "nesting_too_deep"),
    )
    (over_bound,) = list(import_json(content, source_type="upload", max_nesting_depth=4))
    check(
        CID,
        "one level over max_nesting_depth is quarantined nesting_too_deep",
        isinstance(over_bound, QuarantinedRow) and over_bound.code == "nesting_too_deep",
    )


def test_string_literal_brackets_do_not_count_toward_nesting_depth() -> None:
    # A string value packed with brace/bracket characters must never inflate
    # the measured depth -- only real structural nesting counts.
    payload = {"messages": [{"role": "user", "content": "{" * 200 + "[" * 200}]}
    content = json_bytes(payload)
    depth = _max_nesting_depth(content.decode("utf-8"))
    check(
        CID,
        "brackets inside a JSON string literal are never counted as structural "
        "nesting -- the real depth here is shallow (messages -> array -> object)",
        depth <= 3,
    )
    # And the importer itself accepts it even with a tight bound, proving the
    # same thing end-to-end, not just via the private helper.
    (item,) = list(import_json(content, source_type="upload", max_nesting_depth=3))
    check(
        CID,
        "end-to-end: the document is accepted past the depth check despite the "
        "string's own hundreds of brace/bracket characters",
        not (isinstance(item, QuarantinedRow) and item.code == "nesting_too_deep"),
    )


def test_escaped_quote_inside_string_does_not_end_it_early() -> None:
    # json.dumps properly backslash-escapes an embedded quote. If the depth
    # scanner mistakenly treated that escaped quote as ending the string, the
    # literal "[bracket]" text immediately after it would be miscounted as
    # real structural nesting.
    payload = {"x": 'text with "quotes" and [brackets] {braces} inside'}
    content = json_bytes(payload)
    check(
        CID, "sanity: the dumped text contains an escaped quote", '\\"' in content.decode("utf-8")
    )
    depth = _max_nesting_depth(content.decode("utf-8"))
    check(
        CID,
        "the escaped quote never ends the string early, so the trailing "
        "[brackets]/{braces} inside it are never counted as real nesting "
        "(the document's real depth is 1: just the outer object)",
        depth == 1,
    )


def test_boring_characters_outside_brackets_never_change_depth() -> None:
    # Mutation: the closing-bracket check `ch == "}" or ch == "]"` -> `ch !=
    # "}" or ch == "]"`. That change makes depth decrement on almost every
    # non-"}" character, including plain digits/commas that appear outside
    # any string or bracket. A document with several such "boring" chars
    # *before* real nesting would, under the mutation, push `depth` deeply
    # negative first -- so the real nesting's own increments never climb
    # back above the initial `max_depth` of 0, and the returned value drops
    # from the true 3 down to 0.
    check(
        CID,
        "digits/commas outside any bracket/string never affect measured depth",
        _max_nesting_depth("1,2,[[[]]]") == 3,
    )


def test_depth_resets_correctly_between_sibling_bracket_groups() -> None:
    # Mutation: `ch == "}" or ch == "]"` -> `ch == "}" and ch == "]"` (always
    # False for a single character) -- closing brackets would never
    # decrement `depth` at all. A shallow bracket group followed by a
    # deeper, independent sibling group would then have the second group's
    # depth wrongly measured relative to the first group's leftover depth
    # instead of starting back at 0.
    check(
        CID,
        "depth correctly returns to 0 after a closed sibling group, so a "
        "later group's own nesting is measured from a clean baseline",
        _max_nesting_depth("[]" + "[[[]]]") == 3,
    )


def test_backslash_detection_inside_a_string_is_exact() -> None:
    # Mutation: `ch == "\\"` -> `ch != "\\"` inside the in-string branch.
    # That flips which characters start an escape sequence: almost every
    # non-backslash character would wrongly arm `escape_next`, which then
    # consumes (and effectively un-escapes) the *next* character -- including
    # a real closing quote, which would never end the string, so a bracket
    # right after it is wrongly swallowed as "still inside the string"
    # instead of counted as real structural nesting.
    check(
        CID,
        "a plain (no-backslash) string followed by a real bracket: the "
        "closing quote is detected correctly and the bracket counts",
        _max_nesting_depth('"ab"[') == 1,
    )


def test_malformed_json_is_quarantined() -> None:
    (item,) = list(import_json(b"{not valid json", source_type="upload"))
    check(
        CID,
        "negative_acceptance: invalid JSON syntax is quarantined malformed_json, not fatal",
        isinstance(item, QuarantinedRow) and item.code == "malformed_json",
    )


def test_malformed_utf8_is_quarantined_not_fatal() -> None:
    (item,) = list(import_json(b"\xff\xfe\xfd", source_type="upload"))
    check(
        CID,
        "negative_acceptance: malformed UTF-8 is quarantined malformed_utf8, not fatal",
        isinstance(item, QuarantinedRow) and item.code == "malformed_utf8",
    )


def test_unsupported_top_level_type_is_quarantined() -> None:
    for payload, label in (
        (b"42", "number"),
        (b'"a string"', "string"),
        (b"true", "boolean"),
        (b"null", "null"),
    ):
        (item,) = list(import_json(payload, source_type="upload"))
        check(
            CID,
            f"a top-level JSON {label} is quarantined unsupported_top_level_type, "
            "never guessed into either accepted shape",
            isinstance(item, QuarantinedRow) and item.code == "unsupported_top_level_type",
        )


def test_container_element_not_object_is_quarantined() -> None:
    results = list(
        import_json(json_bytes([_VALID_A, 42, "a string", [1, 2], _VALID_B]), source_type="upload")
    )
    check(CID, "five elements in, five results out", len(results) == 5)
    codes = [r.code if isinstance(r, QuarantinedRow) else None for r in results]
    check(CID, "element 0 (valid) is not quarantined", codes[0] is None)
    check(CID, "element 1 (a number) is quarantined not_an_object", codes[1] == "not_an_object")
    check(CID, "element 2 (a string) is quarantined not_an_object", codes[2] == "not_an_object")
    check(
        CID, "element 3 (a nested array) is quarantined not_an_object", codes[3] == "not_an_object"
    )
    check(CID, "element 4 (valid) is not quarantined", codes[4] is None)
    # Mutation: `row_index=row_index` -> a hard-coded `0` inside `_quarantine`.
    # Each result's own real position must be preserved, not collapsed to 0.
    check(
        CID,
        "every result (quarantined or not) carries its own real position, not a hard-coded 0",
        [r.row_index for r in results] == [0, 1, 2, 3, 4],
    )


def test_mixed_shape_elements_are_each_quarantined_with_their_own_reason() -> None:
    prompt_completion = {"prompt": "what is 2+2?", "completion": "4"}
    unmapped_role = {"messages": [{"role": "human", "content": "hi"}]}
    results = list(
        import_json(
            json_bytes([_VALID_A, prompt_completion, unmapped_role, _VALID_B]), source_type="upload"
        )
    )
    check(CID, "four elements in, four results out", len(results) == 4)
    first, second, third, fourth = results
    check(
        CID,
        "the genuine messages-shaped element 0 still imports",
        isinstance(first, ImportedRecord),
    )
    assert isinstance(second, QuarantinedRow) and isinstance(third, QuarantinedRow)
    check(
        CID,
        "a prompt/completion-shaped element is quarantined schema_invalid with "
        "FTF-0506's own exact reason",
        second.code == "schema_invalid" and "messages must be a non-empty list" in second.message,
    )
    check(
        CID,
        "an unmapped-role element is quarantined schema_invalid with a genuinely "
        "different reason than element 1's",
        third.code == "schema_invalid"
        and "unmapped role" in third.message
        and third.message != second.message,
    )
    check(
        CID,
        "the genuine messages-shaped element 3, after two bad elements, still imports",
        isinstance(fourth, ImportedRecord),
    )


def test_quarantine_messages_are_exact_not_generic() -> None:
    (malformed_json,) = list(import_json(b"{bad", source_type="upload"))
    (bad_utf8,) = list(import_json(b"\xff\xfe", source_type="upload"))
    (too_large,) = list(import_json(b'{"x": 1}', source_type="upload", max_total_bytes=3))
    assert isinstance(malformed_json, QuarantinedRow)
    assert isinstance(bad_utf8, QuarantinedRow)
    assert isinstance(too_large, QuarantinedRow)
    check(
        CID,
        "malformed_json carries the real json.JSONDecodeError text, not a placeholder",
        "document is not valid JSON:" in malformed_json.message
        and "Expecting" in malformed_json.message,
    )
    check(
        CID,
        "malformed_utf8 carries the real UnicodeDecodeError text, not a placeholder",
        "document is not valid UTF-8:" in bad_utf8.message,
    )
    check(
        CID,
        "document_too_large states the real measured length and the real configured bound",
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
        import_json(
            json_bytes([good_call, bad_call]),
            source_type="upload",
            tool_schemas={"lookup_order": _LOOKUP_ORDER_SCHEMA},
            tool_argument_validator=validate_instance,
        )
    )
    check(CID, "two elements in, two results out", len(results) == 2)
    ok, rejected = results
    check(
        CID,
        "tool_schemas/tool_argument_validator are forwarded through to the real "
        "FTF-0405 validate_instance: a conforming tool call imports",
        isinstance(ok, ImportedRecord),
    )
    assert isinstance(rejected, QuarantinedRow)
    check(
        CID,
        "a real FTF-0405 schema violation is forwarded through as a genuine "
        "schema_invalid quarantine, not silently accepted",
        rejected.code == "schema_invalid" and "excess property" in rejected.message,
    )


def test_record_id_and_group_id_are_never_invented_by_the_importer() -> None:
    results = list(import_json(json_bytes([_VALID_A, _VALID_A]), source_type="upload"))
    first, second = results
    assert isinstance(first, ImportedRecord) and isinstance(second, ImportedRecord)
    check(
        CID,
        "two elements with byte-identical content get independently assigned "
        "record_ids (the importer never reads a record_id/group_key out of raw JSON)",
        first.record.record_id != second.record.record_id,
    )
    check(
        CID,
        "...and independent solo group_ids",
        first.record.group_id != second.record.group_id,
    )
    check(
        CID,
        "but canonical_hash is still identical for identical content",
        first.record.canonical_hash == second.record.canonical_hash,
    )


def test_source_type_must_be_supported() -> None:
    raised = False
    try:
        import_json(json_bytes(_VALID_A), source_type="not_a_real_dialect")
    except JsonImportError as exc:
        raised = True
        check(CID, "the error names the rejected value", "not_a_real_dialect" in str(exc))
    check(
        CID,
        "an unrecognized source_type raises JsonImportError immediately, before "
        "any byte is read -- a call-level error, not a document/row-level one",
        raised,
    )


def test_source_type_matching_is_exact_not_normalized() -> None:
    for bad in ("Upload", "UPLOAD", " upload", "upload "):
        raised = False
        try:
            import_json(json_bytes(_VALID_A), source_type=bad)
        except JsonImportError:
            raised = True
        check(
            CID,
            f"source_type={bad!r} (a case/whitespace variant) is refused, exact-match only",
            raised,
        )


def test_content_rejects_non_bytes_types() -> None:
    for bad_content, label in (
        ('{"messages": []}', "str"),
        (42, "int"),
        (None, "None"),
        (3.14, "float"),
        ([1, 2], "list"),
    ):
        raised = False
        try:
            import_json(bad_content, source_type="upload")  # type: ignore[arg-type]
        except JsonImportError:
            raised = True
        check(
            CID,
            f"content={label} (not bytes/bytearray) raises JsonImportError immediately, "
            "not a bare TypeError, and never silently decoded",
            raised,
        )


def test_content_accepts_bytearray() -> None:
    (item,) = list(import_json(bytearray(json_bytes(_VALID_A)), source_type="upload"))
    check(
        CID, "a bytearray document is accepted the same as bytes", isinstance(item, ImportedRecord)
    )


def test_max_total_bytes_must_be_positive() -> None:
    for bad_value in (0, -1):
        raised = False
        try:
            import_json(json_bytes(_VALID_A), source_type="upload", max_total_bytes=bad_value)
        except JsonImportError:
            raised = True
        check(CID, f"max_total_bytes={bad_value} raises JsonImportError immediately", raised)
    raised_at_one = False
    try:
        import_json(json_bytes(_VALID_A), source_type="upload", max_total_bytes=1)
    except JsonImportError:
        raised_at_one = True
    check(CID, "max_total_bytes=1 is accepted at the call level (1 is positive)", not raised_at_one)


def test_max_nesting_depth_must_be_positive() -> None:
    for bad_value in (0, -1):
        raised = False
        try:
            import_json(json_bytes(_VALID_A), source_type="upload", max_nesting_depth=bad_value)
        except JsonImportError:
            raised = True
        check(CID, f"max_nesting_depth={bad_value} raises JsonImportError immediately", raised)
    raised_at_one = False
    try:
        import_json(json_bytes(_VALID_A), source_type="upload", max_nesting_depth=1)
    except JsonImportError:
        raised_at_one = True
    check(
        CID, "max_nesting_depth=1 is accepted at the call level (1 is positive)", not raised_at_one
    )


def test_json_import_error_is_a_value_error() -> None:
    check(CID, "JsonImportError is a ValueError", issubclass(JsonImportError, ValueError))


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
