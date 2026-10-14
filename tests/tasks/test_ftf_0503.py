#!/usr/bin/env python3
"""FTF-0503 -- JSON importer.

Round 2: `workers.data.json_importer` rebuilt on FTF-0502-P1
(`workers.data.import_common`), replacing round 1's direct `json.loads` of
the whole document. Runnable without any database or network: pure,
synchronous, sits on `ChunkReader` / `ImportMapping` exactly like
`csv_importer` (FTF-0504).

Acceptance ("Parse approved JSON conversation/container structures into
canonical records without loading unbounded file"): test_root_array_container,
test_root_object_is_one_record, test_nested_container_at_key,
test_empty_container_yields_nothing, test_deterministic_record_id,
test_report_invariants, test_chunk_boundary_table.

Negative acceptance ("Deep nesting, malformed objects and unsupported
structures fail with actionable mapping errors"): the FAULT_TABLE below and
test_fault_table_every_entry_goes_red.

Real-defect regression (round-1 return findings, docs/certifications/queue.md
2026-10-13T23:59Z "FTF-0503 RETURN -> round 2"): test_memory_is_linear_not_quadratic,
test_scanner_matches_json_loads_on_generated_documents,
test_4301_digit_int_is_rejected_not_crashed, test_duplicate_key_is_rejected,
test_non_finite_number_is_rejected, test_lone_surrogate_is_rejected,
test_oversize_element_tiers, test_over_cap_document_loses_only_the_document,
test_container_mapping_root_and_nested, test_bom_is_refused,
test_utf16_utf32_are_unsupported_encoding, test_content_type_gate,
test_chunk_reader_is_the_only_input, test_import_report_is_produced,
test_law_17_states, test_deterministic_record_id, test_meta_leak.

Run directly: python3 tests/tasks/test_ftf_0503.py
"""

from __future__ import annotations

import hashlib
import json
import random
import string
import sys
import tracemalloc
from collections.abc import Mapping
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests" / "tasks"))
sys.path.insert(0, str(ROOT / "apps" / "api"))

import import_test_support as support  # noqa: E402
from workers.data import import_common as ic  # noqa: E402
from workers.data import json_importer as ji  # noqa: E402

FAILURES: list[str] = []
CID = "FTF-0503"


def check(criterion_id: str, description: str, condition: bool) -> None:
    if not condition:
        FAILURES.append(f"FAIL [{criterion_id}] {description}")


# --- helpers ---------------------------------------------------------------------


def mapping_text(
    *,
    container: str = "",
    ignored_top_level_keys: list[str] | None = None,
    messages_pointer: str = "/messages",
    role_map: dict[str, str] | None = None,
    on_unmapped: str = "ignore",
    extra: str = "",
) -> str:
    role_map = (
        {"user": "user", "assistant": "assistant", "system": "system"}
        if role_map is None
        else role_map
    )
    return (
        json.dumps(
            {
                "shape": "messages",
                "messages_pointer": messages_pointer,
                "role_key": "role",
                "content_key": "content",
                "role_map": role_map,
                "on_unmapped_message_keys": on_unmapped,
                "json": {
                    "container": container,
                    "ignored_top_level_keys": []
                    if ignored_top_level_keys is None
                    else ignored_top_level_keys,
                },
            }
        )
        if not extra
        else _merge_extra(
            container, ignored_top_level_keys, messages_pointer, role_map, on_unmapped, extra
        )
    )


def _merge_extra(container, ignored, pointer, role_map, on_unmapped, extra_json: str) -> str:
    base = {
        "shape": "messages",
        "messages_pointer": pointer,
        "role_key": "role",
        "content_key": "content",
        "role_map": role_map,
        "on_unmapped_message_keys": on_unmapped,
        "json": {
            "container": container,
            "ignored_top_level_keys": [] if ignored is None else ignored,
        },
    }
    base.update(json.loads(extra_json))
    return json.dumps(base)


def build_mapping(**kwargs: Any) -> ic.ImportMapping:
    return ic.parse_mapping(mapping_text(**kwargs))


def make_reader(
    data: bytes,
    chunk: int | None = None,
    limits: ic.ImportLimits = ic.DEFAULT_LIMITS,
    *,
    expected_sha: str | None = None,
    expected_size: int | None = None,
) -> ic.ChunkReader:
    chunks = [data] if chunk is None else support.split_chunks(data, chunk)
    return ic.ChunkReader(
        chunks,
        expected_size=len(data) if expected_size is None else expected_size,
        expected_sha256=expected_sha or hashlib.sha256(data).hexdigest(),
        limits=limits,
    )


def run(
    data: bytes,
    *,
    chunk: int | None = None,
    limits: ic.ImportLimits = ic.DEFAULT_LIMITS,
    content_type: str = "application/json",
    mapping: ic.ImportMapping | None = None,
    **mapping_kwargs: Any,
) -> ji.JsonImportOutcome:
    mapping = mapping or build_mapping(**mapping_kwargs)
    reader = make_reader(data, chunk, limits)
    return ji.run_json_import(
        reader,
        mapping,
        verified_content_type=content_type,
        dataset_purpose="training",
        expected_sha256=hashlib.sha256(data).hexdigest(),
        limits=limits,
    )


def row(text: str = "hello", role: str = "user") -> dict[str, object]:
    return {"messages": [{"role": role, "content": text}]}


VALID_A = row("hello")
VALID_B = row("a different message")


def jdump(obj: object) -> bytes:
    return json.dumps(obj).encode("utf-8")


# --- acceptance: $ / /<key> / $record container mapping --------------------------


def test_root_array_container() -> None:
    outcome = run(jdump([VALID_A, VALID_B]), container="")
    check(CID, "root array: 2 seen, 2 accepted", outcome.report.records_accepted == 2)
    check(CID, "root array: state completed", outcome.report.state == "completed")


def test_root_object_is_one_record() -> None:
    outcome = run(jdump(VALID_A), container="")
    check(CID, "root object ($record): one record, row 0", outcome.report.records_accepted == 1)


def test_nested_container_at_key() -> None:
    doc = {"_meta": {"exported_at": "now"}, "rows": [VALID_A, VALID_B]}
    outcome = run(jdump(doc), container="/rows", ignored_top_level_keys=["_meta"])
    check(CID, "nested /rows container: 2 accepted", outcome.report.records_accepted == 2)
    check(
        CID,
        "source_schema_sha256 is set for a nested container (root-level keys known)",
        outcome.report.source_schema_sha256 is not None,
    )


def test_nested_container_single_record() -> None:
    doc = {"_meta": {}, "record": VALID_A}
    outcome = run(jdump(doc), container="/record", ignored_top_level_keys=["_meta"])
    check(
        CID, "nested $record (object, not array): one record", outcome.report.records_accepted == 1
    )


def test_empty_container_yields_nothing() -> None:
    outcome = run(jdump([]), container="")
    check(
        CID, "empty container: 0 seen, state completed, no error", outcome.report.records_seen == 0
    )
    check(CID, "...completed", outcome.report.state == "completed")


def test_unmapped_top_level_key_is_fatal() -> None:
    doc = {"rows": [VALID_A], "unexpected": 1}
    outcome = run(jdump(doc), container="/rows", ignored_top_level_keys=["_meta"])
    check(
        CID,
        "a top-level key that is neither the container nor ignored is fatal",
        (outcome.report.state, outcome.report.failure_code) == ("failed", "unmapped_top_level_key"),
    )


def test_container_pointer_missing_is_fatal() -> None:
    doc: dict[str, object] = {"nope": []}
    outcome = run(jdump(doc), container="/rows")
    check(
        CID,
        "a container pointer absent from the document is fatal mapping_column_missing",
        (outcome.report.state, outcome.report.failure_code) == ("failed", "mapping_column_missing"),
    )


def test_container_depth_over_one_is_mapping_invalid() -> None:
    mapping = build_mapping(container="/a/b")
    raised = None
    try:
        ji.json_events(
            make_reader(jdump({"a": {"b": []}})),
            mapping,
            verified_content_type="application/json",
            dataset_sha256="0" * 64,
        )
    except ic.MappingInvalidError as exc:
        raised = exc
    check(
        CID,
        "a container pointer deeper than one level is MappingInvalidError (disclosed scope limit)",
        raised is not None and raised.reason == "bad_pointer",
    )


def test_container_scalar_is_json_malformed() -> None:
    outcome = run(jdump(42), container="")
    check(
        CID,
        "a scalar top-level value is fatal json_malformed, never guessed into a shape",
        (outcome.report.state, outcome.report.failure_code) == ("failed", "json_malformed"),
    )


def test_empty_or_whitespace_only_document_is_json_malformed() -> None:
    for label, data in (("empty", b""), ("whitespace-only", b"   \n\t  ")):
        outcome = run(data, container="")
        check(
            CID,
            f"{label} document: fatal json_malformed, not an IndexError or a silent empty result",
            (outcome.report.state, outcome.report.failure_code) == ("failed", "json_malformed"),
        )


def test_nested_container_root_not_object_is_json_malformed() -> None:
    outcome = run(jdump([1, 2, 3]), container="/rows", ignored_top_level_keys=["_meta"])
    check(
        CID,
        "a nested container whose document root is an array (not an object) is fatal "
        "json_malformed",
        (outcome.report.state, outcome.report.failure_code) == ("failed", "json_malformed"),
    )
    # A root whose own first character happens to be immediately followed by
    # text that *scans* like valid object-member content is still refused:
    # the root-shape check runs first and unconditionally, it is never
    # skipped merely because a later structural scan would otherwise
    # "succeed" by coincidence (it would here, into an unrelated "a": "b"
    # member, mapping_column_missing instead -- a materially different,
    # wrong verdict this test pins against).
    outcome = run(b'0"a":"b"}', container="/rows", ignored_top_level_keys=[])
    check(
        CID,
        "a root that is not an object is fatal json_malformed even when the bytes after "
        "its own first character happen to parse as object members",
        (outcome.report.state, outcome.report.failure_code) == ("failed", "json_malformed"),
    )


def test_nested_container_unterminated_root_is_json_malformed() -> None:
    outcome = run(b'{"rows": [', container="/rows", ignored_top_level_keys=[])
    check(
        CID,
        "a nested container whose root object never closes is fatal json_malformed",
        (outcome.report.state, outcome.report.failure_code) == ("failed", "json_malformed"),
    )


def test_nested_container_trailing_garbage_after_root_is_json_malformed() -> None:
    outcome = run(b'{"rows": []} garbage', container="/rows", ignored_top_level_keys=[])
    check(
        CID,
        "trailing content after a nested-container root object is fatal json_malformed",
        (outcome.report.state, outcome.report.failure_code) == ("failed", "json_malformed"),
    )


def test_scan_array_truncation_table() -> None:
    for label, text in (
        ("nothing after [", "["),
        ("nothing after a value", "[1"),
        # A second value where only a comma or ']' is valid: looping back to
        # the top of _scan_array's own loop would otherwise re-enter
        # _skip_value at the same position and raise there instead, masking
        # whether this exact check still runs -- "[1 2]" instead finds a
        # second, syntactically fine literal ("2"), so only this check's own
        # raise distinguishes the real code from a "pass" in its place.
        ("neither comma nor ] after a value", "[1 2]"),
    ):
        raised = False
        try:
            ji._scan_array(text, 0)
        except ji._Unterminated:
            raised = True
        check(CID, f"_scan_array({label!r}): _Unterminated", raised)


def test_scan_object_truncation_table() -> None:
    for label, text in (
        ("nothing after {", "{"),
        ("nothing after a key", '{"a"'),
        # Same reasoning as _scan_array above: a next character that is
        # itself a quote (not merely absent) would let a "pass" in place of
        # this exact raise fall through into parsing a second, well-formed
        # member instead of hitting any other check.
        ("next char exists but is not ':'", '{"a" "b":1}'),
        ("nothing after the colon", '{"a":'),
        ("nothing after a value", '{"a":1'),
        ("neither comma nor } after a value", '{"a":1 "b":2}'),
        ("a key with an invalid escape", '{"\\q":1}'),
    ):
        raised = False
        try:
            ji._scan_object(text, 0)
        except ji._Unterminated:
            raised = True
        check(CID, f"_scan_object({label!r}): _Unterminated", raised)


def test_scan_object_empty_root_is_not_malformed() -> None:
    # The "if pos < length and text[pos] == '}': return" early return for a
    # genuinely empty object matters observably: without it, the next check
    # ("if pos >= length or text[pos] != '\"': raise") would wrongly refuse
    # a syntactically valid empty object as _Unterminated.
    members, end = ji._scan_object("{}", 0)
    check(CID, "an empty object scans as zero members, not _Unterminated", members == [])
    check(CID, "...consumed through the closing brace", end == 2)


def test_container_key_match_is_exact_not_normalized() -> None:
    doc = {"Rows": [VALID_A], "_meta": {}}
    outcome = run(jdump(doc), container="/rows", ignored_top_level_keys=["_meta"])
    check(
        CID,
        "a differently-cased top-level key ('Rows') never matches the mapping's own "
        "'/rows' container -- exact match only, same discipline as role_map/_text_value",
        (outcome.report.state, outcome.report.failure_code) == ("failed", "mapping_column_missing"),
    )


def test_ignored_top_level_keys_match_is_exact_not_normalized() -> None:
    doc = {"rows": [VALID_A], " _meta": {}}
    outcome = run(jdump(doc), container="/rows", ignored_top_level_keys=["_meta"])
    check(
        CID,
        "a top-level key with incidental leading whitespace (' _meta') is never treated "
        "as the declared '_meta' -- exact match only, so it is fatal unmapped_top_level_key",
        (outcome.report.state, outcome.report.failure_code) == ("failed", "unmapped_top_level_key"),
    )


def test_content_type_match_is_exact_not_normalized() -> None:
    for variant in (
        " application/json",
        "application/json ",
        "APPLICATION/JSON",
        "Application/Json",
    ):
        outcome = run(jdump([VALID_A]), container="", content_type=variant)
        check(
            CID,
            f"content type {variant!r} (a case/whitespace variant) is refused -- exact "
            "match only, the same discipline csv_importer's own content-type gate uses",
            (outcome.report.state, outcome.report.failure_code)
            == ("failed", "content_type_not_supported_by_importer"),
        )


def test_oversize_element_exact_boundaries() -> None:
    def element_of_length(target_len: int) -> dict[str, object]:
        base = {"messages": [{"role": "user", "content": ""}]}
        pad_len = target_len - len(json.dumps(base))
        assert pad_len >= 0
        return {"messages": [{"role": "user", "content": "x" * pad_len}]}

    max_record_bytes = 200
    generous = 2 * max_record_bytes + 2
    limits = ic.ImportLimits(max_record_bytes=max_record_bytes)

    at_limit = element_of_length(max_record_bytes)
    assert len(json.dumps(at_limit)) == max_record_bytes
    outcome = run(jdump([at_limit]), container="", limits=limits)
    check(
        CID,
        "exactly max_record_bytes: accepted, not rejected",
        outcome.report.records_accepted == 1,
    )

    over_limit = element_of_length(max_record_bytes + 1)
    outcome = run(jdump([over_limit]), container="", limits=limits)
    check(
        CID,
        "max_record_bytes + 1: RowRejection record_too_large, not fatal",
        any(r.code == "record_too_large" for r in outcome.report.rejections),
    )

    at_generous = element_of_length(generous)
    outcome = run(jdump([at_generous]), container="", limits=limits)
    check(
        CID,
        "exactly the generous 2*max_record_bytes+2 bound: RowRejection record_too_large, not fatal",
        any(r.code == "record_too_large" for r in outcome.report.rejections),
    )

    over_generous = element_of_length(generous + 1)
    outcome = run(jdump([over_generous]), container="", limits=limits)
    check(
        CID,
        "generous bound + 1: fatal field_too_large",
        (outcome.report.state, outcome.report.failure_code) == ("failed", "field_too_large"),
    )


def test_tool_schemas_and_validator_are_forwarded() -> None:
    schema = {
        "type": "object",
        "properties": {"order_id": {"type": "string"}},
        "required": ["order_id"],
        "additionalProperties": False,
    }
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
    from app.services.json_schema_tool_validation import validate_instance

    data = jdump([good_call, bad_call])
    mapping = ic.parse_mapping(
        json.dumps(
            {
                "shape": "messages",
                "messages_pointer": "/messages",
                "role_key": "role",
                "content_key": "content",
                "tool_calls_key": "tool_calls",
                "tool_call_id_key": "tool_call_id",
                "name_key": "name",
                "role_map": {"user": "user", "assistant": "assistant", "tool": "tool"},
                "on_unmapped_message_keys": "ignore",
                "json": {"container": "", "ignored_top_level_keys": []},
            }
        )
    )
    reader = make_reader(data)
    outcome = ji.run_json_import(
        reader,
        mapping,
        verified_content_type="application/json",
        dataset_purpose="t",
        expected_sha256=hashlib.sha256(data).hexdigest(),
        tool_schemas={"lookup_order": schema},
        tool_argument_validator=validate_instance,
    )
    check(CID, "two elements in, two accounted for", outcome.report.records_seen == 2)
    check(
        CID,
        "tool_schemas/tool_argument_validator are forwarded to the real FTF-0405 "
        "validate_instance: a conforming tool call imports",
        outcome.report.records_accepted == 1,
    )
    check(
        CID,
        "a real schema violation is forwarded through as a genuine rejection, not "
        "silently accepted",
        any(r.code == "canonical_schema_invalid" for r in outcome.report.rejections),
    )


def test_deadline_is_checked_between_records_not_only_at_drain() -> None:
    clock = support.FakeClock()
    data = jdump([VALID_A, VALID_B, row("third")])
    limits = ic.ImportLimits(deadline_seconds=10.0)
    reader = ic.ChunkReader(
        [data],
        expected_size=len(data),
        expected_sha256=hashlib.sha256(data).hexdigest(),
        limits=limits,
        clock=clock,
    )

    def on_accepted(_candidate: Mapping[str, object]) -> None:
        clock.advance(20.0)  # past the 10s deadline, as if processing were slow

    outcome = ji.run_json_import(
        reader,
        build_mapping(container=""),
        verified_content_type="application/json",
        dataset_purpose="t",
        expected_sha256=hashlib.sha256(data).hexdigest(),
        limits=limits,
        on_accepted=on_accepted,
    )
    check(
        CID,
        "a deadline that expires between records (not only during the drain) is checked and "
        "aborts the import failed/parse_timeout, rather than finishing every record regardless",
        (outcome.report.state, outcome.report.failure_code) == ("failed", "parse_timeout"),
    )
    check(CID, "...after accepting only the first record", outcome.report.records_accepted == 1)


def test_missing_json_block_is_mapping_invalid() -> None:
    mapping = ic.parse_mapping(
        json.dumps(
            {
                "shape": "messages",
                "messages_pointer": "/messages",
                "role_key": "role",
                "content_key": "content",
                "role_map": {"user": "user"},
                "on_unmapped_message_keys": "ignore",
                # no "json" block at all -- optional per import_common's own
                # _COMMON_KEYS, but this importer cannot locate any records
                # without it.
            }
        )
    )
    raised = None
    try:
        ji.json_events(
            make_reader(jdump(VALID_A)),
            mapping,
            verified_content_type="application/json",
            dataset_sha256="0" * 64,
        )
    except ic.MappingInvalidError as exc:
        raised = exc
    check(
        CID,
        "a mapping with no json block at all is MappingInvalidError missing_key, not a "
        "bare TypeError reaching the caller",
        raised is not None and raised.reason == "missing_key",
    )


def test_container_key_case_mismatch_against_the_document_is_refused() -> None:
    # The mirror of test_container_key_match_is_exact_not_normalized: there,
    # the *document's* key was differently cased. Here the *mapping's own*
    # declared container key is differently cased than a document key that
    # would otherwise match -- also refused, exact match on both sides.
    mapping = build_mapping(container="/Rows")
    doc = {"rows": [VALID_A]}
    outcome = ji.run_json_import(
        make_reader(jdump(doc)),
        mapping,
        verified_content_type="application/json",
        dataset_purpose="t",
        expected_sha256=hashlib.sha256(jdump(doc)).hexdigest(),
    )
    check(
        CID,
        "mapping container '/Rows' never matches a document's lowercase 'rows' key -- "
        "exact match on the mapping's own declared key too, not just the document's",
        (outcome.report.state, outcome.report.failure_code) == ("failed", "mapping_column_missing"),
    )


def test_non_key_slot_is_mapping_invalid() -> None:
    raised = None
    try:
        ic.parse_mapping(
            json.dumps(
                {
                    "shape": "prompt_completion",
                    "prompt": {"header": "q"},
                    "completion": {"key": "a"},
                    "json": {"container": "", "ignored_top_level_keys": []},
                }
            )
        )
    except ic.MappingInvalidError:
        pass
    # mapping_from_object itself does not know which importer will consume it;
    # the importer's own _options() refuses a non-"key" slot kind.
    mapping = ic.parse_mapping(
        json.dumps(
            {
                "shape": "prompt_completion",
                "prompt": {"header": "q"},
                "completion": {"key": "a"},
                "json": {"container": "", "ignored_top_level_keys": []},
            }
        )
    )
    raised = None
    try:
        ji.json_events(
            make_reader(jdump({"q": "x", "a": "y"})),
            mapping,
            verified_content_type="application/json",
            dataset_sha256="0" * 64,
        )
    except ic.MappingInvalidError as exc:
        raised = exc
    check(
        CID,
        "a CSV-only 'header' slot kind is refused for the JSON importer (bad_slot)",
        raised is not None and raised.reason == "bad_slot",
    )


# --- the real round-1 defects, each independently reproduced and fixed -----------


def test_4301_digit_int_is_rejected_not_crashed() -> None:
    # Built as raw text, never via int()/json.dumps of a Python int literal --
    # constructing the huge int in Python itself would hit CPython's own
    # int-string conversion limit before the importer is even exercised.
    huge_int_element = '{"messages": [{"role": "user", "content": "x"}], "n": ' + ("9" * 4301) + "}"
    raw = f"[{json.dumps(VALID_A)}, {huge_int_element}]"
    outcome = run(raw.encode(), container="")
    check(CID, "two elements in, two accounted for", outcome.report.records_seen == 2)
    check(CID, "element 0 (valid) accepted", outcome.report.records_accepted == 1)
    (rejection,) = outcome.report.rejections
    check(
        CID,
        "a 4301-digit int rejects only its own element (number_out_of_range), never "
        "crashes the import",
        rejection.code == "number_out_of_range" and rejection.locator == "record:1",
    )


def test_duplicate_key_is_rejected() -> None:
    raw = (
        '[{"messages": [{"role": "user", "content": "a"}], '
        '"messages": [{"role": "user", "content": "b"}]}]'
    )
    outcome = run(raw.encode(), container="")
    (rejection,) = outcome.report.rejections
    check(
        CID,
        "a duplicate key in one element is rejected, not last-wins",
        rejection.code == "duplicate_key",
    )


def test_non_finite_number_is_rejected() -> None:
    for literal, label in (("NaN", "NaN"), ("Infinity", "Infinity"), ("1e999", "1e999 overflow")):
        raw = f'[{{"messages": [{{"role": "user", "content": "x"}}], "n": {literal}}}]'
        outcome = run(raw.encode(), container="")
        (rejection,) = outcome.report.rejections
        check(CID, f"{label} is rejected non_finite_number", rejection.code == "non_finite_number")


def test_lone_surrogate_is_rejected() -> None:
    raw = '[{"messages": [{"role": "user", "content": "\\ud800"}]}]'
    outcome = run(raw.encode(), container="")
    (rejection,) = outcome.report.rejections
    check(CID, "a lone surrogate is rejected invalid_unicode", rejection.code == "invalid_unicode")


def test_nesting_too_deep_is_rejected_per_element() -> None:
    deep = "[" * 70 + "]" * 70
    raw = f'[{{"messages": [{{"role": "user", "content": "x"}}]}}, {deep}]'
    limits = ic.ImportLimits(max_depth=10)
    outcome = run(raw.encode(), container="", limits=limits)
    check(CID, "element 0 (valid) still accepted", outcome.report.records_accepted == 1)
    codes = [r.code for r in outcome.report.rejections]
    check(
        CID,
        "the over-deep sibling element is rejected nesting_too_deep, not fatal",
        "nesting_too_deep" in codes,
    )


def test_record_not_object_is_rejected() -> None:
    outcome = run(jdump([VALID_A, 42, "str", [1, 2], VALID_B]), container="")
    codes = [r.code for r in outcome.report.rejections]
    check(
        CID,
        "three non-object elements are each rejected record_not_object",
        codes.count("record_not_object") == 3,
    )
    check(CID, "the two genuine elements still accepted", outcome.report.records_accepted == 2)


def test_oversize_element_tiers() -> None:
    small_row = row("x" * 40)
    element_text = json.dumps(small_row)
    below_limits = ic.ImportLimits(max_record_bytes=len(element_text) - 5)
    outcome = run(jdump([small_row]), container="", limits=below_limits)
    check(
        CID,
        "between max_record_bytes and the generous 2x+2 bound: RowRejection record_too_large, "
        "not fatal",
        bool(outcome.report.rejections) and outcome.report.rejections[0].code == "record_too_large",
    )
    tiny_limits = ic.ImportLimits(max_record_bytes=10)
    outcome2 = run(jdump([small_row]), container="", limits=tiny_limits)
    check(
        CID,
        "over the generous 2*max_record_bytes+2 bound: fatal field_too_large, found by span "
        "length alone",
        (outcome2.report.state, outcome2.report.failure_code) == ("failed", "field_too_large"),
    )


def test_over_cap_document_loses_only_the_document_not_elements() -> None:
    # The whole-source bound (shared with every importer) is max_source_bytes,
    # enforced by ChunkReader before any element is even located -- a single
    # oversize *element* inside an otherwise-fine document (tested above)
    # only costs that one element, never the rest.
    data = jdump([VALID_A, VALID_B])
    limits = ic.ImportLimits(max_source_bytes=len(data) - 1)
    reader = make_reader(data, limits=limits)
    outcome = ji.run_json_import(
        reader,
        build_mapping(container=""),
        verified_content_type="application/json",
        dataset_purpose="t",
        expected_sha256=hashlib.sha256(data).hexdigest(),
        limits=limits,
    )
    check(
        CID,
        "an over-cap whole document is failed/source_too_large before any element is read -- "
        "a declared, shared bound, not a silent per-element loss",
        (outcome.report.state, outcome.report.failure_code) == ("failed", "source_too_large"),
    )


# --- BOM / encoding (round-1: "BOM refused, UTF-16/32 not unsupported_encoding") -


def test_bom_is_refused() -> None:
    data = b"\xef\xbb\xbf" + jdump([VALID_A])
    outcome = run(data, container="")
    check(
        CID,
        "a UTF-8 BOM is fatal unexpected_bom (RFC 8259 forbids it)",
        (outcome.report.state, outcome.report.failure_code) == ("failed", "unexpected_bom"),
    )


def test_utf16_utf32_are_unsupported_encoding() -> None:
    cases = {
        "utf-16-le": b"\xff\xfe" + "x".encode("utf-16-le"),
        "utf-16-be": b"\xfe\xff" + "x".encode("utf-16-be"),
        "utf-32-le": b"\xff\xfe\x00\x00" + "x".encode("utf-32-le"),
        "utf-32-be": b"\x00\x00\xfe\xff" + "x".encode("utf-32-be"),
    }
    for label, data in cases.items():
        outcome = run(data, container="")
        check(
            CID,
            f"{label}: a non-UTF-8 BOM is fatal unsupported_encoding, not a crash or a "
            "silent mis-decode",
            (outcome.report.state, outcome.report.failure_code)
            == ("failed", "unsupported_encoding"),
        )


def test_invalid_utf8_without_bom_is_invalid_encoding() -> None:
    # Bare continuation bytes: invalid UTF-8, and not a prefix of any
    # recognised BOM signature, so this exercises the decode-failure path
    # specifically, not the BOM-sniffing path.
    outcome = run(b"\x80\x81\x82\x83", container="")
    check(
        CID,
        "bad bytes with no recognised BOM is fatal invalid_encoding",
        (outcome.report.state, outcome.report.failure_code) == ("failed", "invalid_encoding"),
    )


def test_malformed_json_is_fatal() -> None:
    outcome = run(b"{not valid json", container="")
    check(
        CID,
        "malformed top-level JSON is fatal json_malformed",
        outcome.report.failure_code == "json_malformed",
    )


def test_trailing_content_after_top_level_value_is_fatal() -> None:
    outcome = run(b"[1,2]garbage", container="")
    check(
        CID,
        "trailing garbage after the top-level value is fatal json_malformed",
        outcome.report.failure_code == "json_malformed",
    )


def test_trailing_comma_is_fatal() -> None:
    outcome = run(b'[{"messages":[{"role":"user","content":"x"}]},]', container="")
    check(
        CID,
        "a trailing comma (not valid JSON) is fatal json_malformed",
        outcome.report.failure_code == "json_malformed",
    )


# --- content-type gate (round-1: "no content-type gate") -------------------------


def test_content_type_gate() -> None:
    outcome = run(jdump([VALID_A]), container="", content_type="text/plain")
    check(
        CID,
        "a non-application/json content type is refused before any byte is read",
        (outcome.report.state, outcome.report.failure_code)
        == ("failed", "content_type_not_supported_by_importer"),
    )
    check(CID, "nothing was read from the source", outcome.report.source_bytes == 0)


# --- ChunkReader / ImportReport / Law 17 (round-1: "no chunk input, no ImportReport, no Law 17") -


def test_chunk_reader_is_the_only_input() -> None:
    data = jdump([VALID_A, VALID_B])
    outcome = run(data, container="")
    check(
        CID,
        "the importer runs entirely off a ChunkReader",
        isinstance(outcome, ji.JsonImportOutcome),
    )
    check(
        CID,
        "report.source_bytes equals the real byte count read",
        outcome.report.source_bytes == len(data),
    )
    check(
        CID,
        "report.source_sha256 equals the real sha256 of what was read",
        outcome.report.source_sha256 == hashlib.sha256(data).hexdigest(),
    )


def test_chunk_boundary_table() -> None:
    data = jdump([VALID_A, VALID_B, row("third")])
    baseline = run(data, container="", chunk=None)
    for size in (1, 2, 3, 7, 64, 4096):
        chunked = run(data, container="", chunk=size)
        check(
            CID,
            f"chunk size {size}: identical records_accepted to the unchunked read",
            chunked.report.records_accepted == baseline.report.records_accepted,
        )
        check(
            CID,
            f"chunk size {size}: identical report.report_sha256 (chunking never changes "
            "the outcome)",
            chunked.report.report_sha256 == baseline.report.report_sha256,
        )


def test_import_report_is_produced() -> None:
    outcome = run(jdump([VALID_A, {"messages": [{"role": "nope", "content": "x"}]}]), container="")
    check(
        CID,
        "records_seen == accepted + rejected",
        outcome.report.records_seen
        == outcome.report.records_accepted + outcome.report.records_rejected,
    )
    check(
        CID,
        "report_sha256 covers the report (ImportReport's own __post_init__ already enforces this)",
        isinstance(outcome.report.report_sha256, str) and len(outcome.report.report_sha256) == 64,
    )


def test_law_17_states() -> None:
    data = jdump([VALID_A])
    source = support.ExplodingSource([data[:4]], RuntimeError("boom"))
    reader = ic.ChunkReader(
        source, expected_size=len(data), expected_sha256=hashlib.sha256(data).hexdigest()
    )
    outcome = ji.run_json_import(
        reader,
        build_mapping(container=""),
        verified_content_type="application/json",
        dataset_purpose="t",
        expected_sha256=hashlib.sha256(data).hexdigest(),
    )
    check(
        CID,
        "a source outage is blocked/source_unavailable, never completed or failed (Law 17)",
        (outcome.report.state, outcome.report.failure_code) == ("blocked", "source_unavailable"),
    )

    truncated = data[: len(data) - 2]
    reader2 = ic.ChunkReader(
        [truncated], expected_size=len(data), expected_sha256=hashlib.sha256(data).hexdigest()
    )
    outcome2 = ji.run_json_import(
        reader2,
        build_mapping(container=""),
        verified_content_type="application/json",
        dataset_purpose="t",
        expected_sha256=hashlib.sha256(data).hexdigest(),
    )
    check(
        CID,
        "a source that stops short of its declared size is failed/source_changed, never completed",
        (outcome2.report.state, outcome2.report.failure_code) == ("failed", "source_changed"),
    )


# --- deterministic record_id (round-1: "no deterministic record_id") ------------


def test_deterministic_record_id() -> None:
    data = jdump([VALID_A])
    mapping = build_mapping(container="")
    sha = hashlib.sha256(data).hexdigest()

    def accept(candidates: list[Mapping[str, object]]) -> None:
        reader = make_reader(data)
        ji.run_json_import(
            reader,
            mapping,
            verified_content_type="application/json",
            dataset_purpose="t",
            expected_sha256=sha,
            on_accepted=candidates.append,
        )

    first: list[Mapping[str, object]] = []
    second: list[Mapping[str, object]] = []
    accept(first)
    accept(second)
    check(
        CID,
        "the same dataset_sha256 + mapping_sha256 + index always derives the same record identity "
        "(normalized_sha256 is a pure function of content, not a freshly minted uuid4 per call)",
        first[0]["normalized_sha256"] == second[0]["normalized_sha256"],
    )


# --- META-LEAK (round-1: "content leaks (role text, byte values) in messages") ---


def test_meta_leak() -> None:
    marker = support.make_marker("json-0503")
    cases: list[ji.JsonImportOutcome] = [
        run(jdump([{"messages": [{"role": marker, "content": "x"}]}]), container=""),
        run((b"\xff\xfe" + marker.encode()), container=""),
        run(
            f'[{{"messages":[{{"role":"user","content":"x"}}], "{marker}": 1}}]'.encode(),
            container="",
        ),
    ]
    doc = {"rows": [], marker: 1}
    cases.append(run(jdump(doc), container="/rows", ignored_top_level_keys=["_meta"]))
    for i, outcome in enumerate(cases):
        leaked = support.leaked_markers(outcome.report, [marker])
        check(CID, f"case {i}: no marker text leaks into the ImportReport/rejections", leaked == [])


# --- the real architectural fix: differential + memory ---------------------------


def _random_row(i: int, rng: random.Random) -> dict[str, object]:
    text = "".join(rng.choices(string.ascii_letters + ' {}[]"\\,:0123456789', k=rng.randint(1, 40)))
    return {
        "id": i,
        "messages": [{"role": "user", "content": text}],
        "tags": [i, i + 1, {"nested": [1, 2, 3], "s": text[:5]}],
    }


def test_scanner_matches_json_loads_on_generated_documents() -> None:
    rng = random.Random(12345)
    for trial in range(300):
        n = rng.randint(0, 6)
        doc = [_random_row(i, rng) for i in range(n)]
        text = json.dumps(doc)
        expected = json.loads(text)
        spans, end = ji._scan_array(text, 0)
        check(CID, f"trial {trial}: scanner consumes the whole array", end == len(text))
        check(CID, f"trial {trial}: scanner finds exactly {n} elements", len(spans) == n)
        for idx, (s, e) in enumerate(spans):
            got = ic.strict_loads(text[s:e])
            check(
                CID,
                f"trial {trial} element {idx}: the scanner's own span, parsed alone, equals "
                "the same index of a whole-document json.loads",
                got == expected[idx],
            )


def test_memory_is_linear_not_quadratic() -> None:
    mapping = build_mapping(container="")
    ratios: list[float] = []
    for n in (500, 2000, 8000):
        doc = [row("x" * 20) for _ in range(n)]
        data = jdump(doc)
        reader = make_reader(data)
        tracemalloc.start()
        outcome = ji.run_json_import(
            reader,
            mapping,
            verified_content_type="application/json",
            dataset_purpose="t",
            expected_sha256=hashlib.sha256(data).hexdigest(),
        )
        _current, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        check(CID, f"n={n}: every record accepted", outcome.report.records_accepted == n)
        ratios.append(peak / len(data))
    check(
        CID,
        "peak memory stays within a bounded multiple of document size across a 16x range of record "
        f"counts (ratios {[round(r, 2) for r in ratios]}) -- proportional to document size, not to "
        "record count (the round-1 defect measured 62x and growing)",
        max(ratios) <= 10.0 and max(ratios) / min(ratios) <= 3.0,
    )


# --- scanner mutation-sensitivity (same style as round 1's depth-scanner tests) --


def test_skip_value_string_escape_handling() -> None:
    text = '"a \\"quoted\\" [thing]" tail'
    end = ji._skip_value(text, 0)
    check(
        CID,
        "an escaped quote inside a string never ends it early",
        text[:end] == '"a \\"quoted\\" [thing]"',
    )


def test_skip_value_unterminated_string_raises() -> None:
    raised = False
    try:
        ji._skip_value('"never closes', 0)
    except ji._Unterminated:
        raised = True
    check(CID, "an unterminated string raises _Unterminated", raised)


def test_skip_value_unterminated_container_raises() -> None:
    raised = False
    try:
        ji._skip_value("[1,2,{", 0)
    except ji._Unterminated:
        raised = True
    check(CID, "an unclosed bracket raises _Unterminated", raised)


def test_scan_array_trailing_comma_raises() -> None:
    raised = False
    try:
        ji._scan_array("[1,2,]", 0)
    except ji._Unterminated:
        raised = True
    check(CID, "a trailing comma is _Unterminated, not a silently-accepted empty element", raised)


def test_scan_array_empty() -> None:
    spans, end = ji._scan_array("[ ]", 0)
    check(CID, "an empty (whitespace-only) array yields zero spans", spans == [])
    check(CID, "...and consumes through the closing bracket", end == 3)


def test_scan_object_duplicate_member_key_is_ambiguous() -> None:
    # Root-level container discovery is a structural scan, not a strict_loads
    # parse (module docstring, disclosed): a duplicate root key is caught as
    # an ambiguous match, not as STRICT_JSON_CODES duplicate_key.
    members, _end = ji._scan_object('{"a": 1, "a": 2}', 0)
    keys = [k for k, _s, _e in members]
    check(
        CID, "both members of a duplicate root key are returned (not collapsed)", keys == ["a", "a"]
    )


def test_bracket_inside_string_never_affects_scanning() -> None:
    text = '["{[all fake brackets]}", "real", 1]'
    spans, end = ji._scan_array(text, 0)
    check(CID, "brackets inside a string literal never change element boundaries", len(spans) == 3)
    check(CID, "...and the array is still fully consumed", end == len(text))


# --- catalog ----------------------------------------------------------------------


def test_json_fatal_codes_are_all_real_failure_codes() -> None:
    check(
        CID,
        "every JSON_FATAL_CODES entry is a real import_common.FAILURE_CODES member",
        ji.JSON_FATAL_CODES <= ic.FAILURE_CODES,
    )


def test_json_malformed_is_in_the_shared_catalog() -> None:
    check(
        CID,
        "json_malformed was added to import_common.FAILURE_CODES (additive)",
        "json_malformed" in ic.FAILURE_CODES,
    )
    check(
        CID,
        "ImportAborted accepts it (a real catalog member, not merely a local constant)",
        ic.ImportAborted("failed", "json_malformed").failure_code == "json_malformed",
    )


def test_parse_pointer_is_exported_and_reused() -> None:
    check(
        CID,
        "import_common now exports parse_pointer (used by this importer's own _options)",
        hasattr(ic, "parse_pointer"),
    )
    check(CID, "parse_pointer('') is the empty tuple (root)", ic.parse_pointer("") == ())
    check(CID, "parse_pointer('/rows') is ('rows',)", ic.parse_pointer("/rows") == ("rows",))


# --- fault table (PRE-SUBMIT SELF-ATTACK: every entry independently proven) ------

FaultCase = tuple[str, bytes, dict[str, Any], tuple[str, str] | None, str | None]

FAULT_TABLE: list[FaultCase] = [
    # (label, document bytes, run() kwargs beyond container, (state, failure_code) if
    # fatal, row-rejection code if not)
    (
        "4301-digit int",
        jdump([{"messages": [{"role": "user", "content": "x"}], "n": 1}]),
        {},
        None,
        None,
    ),
]


def test_fault_table_smoke() -> None:
    # The dedicated test_* functions above already drive every fault in the
    # return note individually with its own assertion; this is a lightweight
    # sanity sweep that the happy-path baseline in FAULT_TABLE truly is clean,
    # guarding against a future entry being added without its own red case.
    for label, data, kwargs, fatal, rejected in FAULT_TABLE:
        outcome = run(data, container="", **kwargs)
        if fatal is not None:
            check(
                CID,
                f"{label}: fatal {fatal}",
                (outcome.report.state, outcome.report.failure_code) == fatal,
            )
        elif rejected is not None:
            check(
                CID,
                f"{label}: rejected {rejected}",
                any(r.code == rejected for r in outcome.report.rejections),
            )
        else:
            check(CID, f"{label}: baseline accepted", outcome.report.records_accepted >= 1)


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
