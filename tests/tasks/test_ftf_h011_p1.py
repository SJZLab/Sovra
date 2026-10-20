#!/usr/bin/env python3
# mypy: disable-error-code="arg-type,attr-defined,assignment,call-arg,index,var-annotated,misc"
"""FTF-H011-P1 -- dataset mapping probe and preview, pure domain.

Runnable without a database or network. `workers/data/dataset_mapping_review.py` is pure;
`apps/api/app/datasets/dataset_mapping_service.py`'s real-DB/S3 composition is exercised by
`tests/integration/test_ftf_h011_p1_mapping_preview_postgres_s3.py`.

Criteria (each test names the clause it pins):

  TAP   `ImportMapping.row_observer`: additive, order-matched to events, a copy never the
        original, never set by `mapping_from_object`, no effect on `mapping_sha256`/equality
  DISP  dispatch by verified content type; XLSX and anything unrecognised ->
        `MappingPreviewUnavailableError`
  MASK  masking: every value masked when not revealed, headers/keys never masked, nested
        structures, `reveal=True` returns the real values
  PAIR  every accepted/rejected row pairs with its own raw row, in order; a row that never
        reaches `apply_mapping` carries no raw row
  CMP   comparison cells: raw/canonical type and equality, per mapped slot
  NMAP  `not_mapped`: raw columns/keys no slot references
  LIM   preview stops at `limits.preview_max_records`; `complete` reflects why it stopped
  OUT   a storage/source outage is `state`/`failure_code`, never a raised exception
  TOOL  `tool_definitions_sha256`: canonical, `None`/`{}` identical, order-independent
  SHA   `preview_sha256` is deterministic and changes when the rows/counts it covers do
  ROLE  `observed_role_values` from a message-shaped row's own raw role string

Run directly: python3 tests/tasks/test_ftf_h011_p1.py
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import sys
from collections.abc import Callable, Iterator, Mapping
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent.parent
API_ROOT = ROOT / "apps" / "api"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(API_ROOT))

import workers.data.dataset_mapping_review as rev  # noqa: E402
from workers.data import import_common as ic  # noqa: E402

from app.services.json_schema_tool_validation import validate_instance  # noqa: E402

FAILURES: list[str] = []


def check(criterion_id: str, description: str, condition: bool) -> None:
    if not condition:
        FAILURES.append(f"FAIL [{criterion_id}] {description}")


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def reader_for(data: bytes, *, limits: ic.ImportLimits = rev.PREVIEW_LIMITS) -> ic.ChunkReader:
    return ic.ChunkReader([data], expected_size=len(data), expected_sha256=sha(data), limits=limits)


PROMPT_COMPLETION = ic.mapping_from_object(
    {"shape": "prompt_completion", "prompt": {"key": "prompt"}, "completion": {"key": "completion"}}
)
CSV_PROMPT_COMPLETION = ic.mapping_from_object(
    {
        "shape": "prompt_completion",
        "csv": {"encoding": "utf-8", "delimiter": ",", "has_header": True, "header_row": 0},
        "prompt": {"header": "q"},
        "completion": {"header": "a"},
    }
)
JSON_PROMPT_COMPLETION = ic.mapping_from_object(
    {
        "shape": "prompt_completion",
        "json": {"container": "", "ignored_top_level_keys": []},
        "prompt": {"key": "prompt"},
        "completion": {"key": "completion"},
    }
)
MESSAGES_JSON = ic.mapping_from_object(
    {
        "shape": "messages_json",
        "messages": {"key": "messages"},
        "role_key": "role",
        "content_key": "content",
        "role_map": {"human": "user", "ai": "assistant"},
        "on_unmapped_message_keys": "ignore",
    }
)


# --- TAP ---------------------------------------------------------------------------------


def test_tap_additive_and_never_set_by_mapping_from_object() -> None:
    check(
        "TAP-1",
        "a freshly parsed MappingSpec has no observer",
        PROMPT_COMPLETION.row_observer is None,
    )
    seen: list[tuple[object, object]] = []
    tapped = dataclasses.replace(PROMPT_COMPLETION, row_observer=lambda r, h: seen.append((r, h)))
    check(
        "TAP-2",
        "attaching an observer does not change mapping_sha256",
        tapped.mapping_sha256 == PROMPT_COMPLETION.mapping_sha256,
    )
    check(
        "TAP-3",
        "attaching an observer does not change the document",
        tapped.document == PROMPT_COMPLETION.document,
    )
    ic.apply_mapping(tapped, {"prompt": "p", "completion": "c"})
    check("TAP-4", "the observer fires exactly once", len(seen) == 1)
    check("TAP-5", "the observer sees the row", seen[0][0] == {"prompt": "p", "completion": "c"})
    check("TAP-6", "the observer sees no headers for a dict row", seen[0][1] is None)


def test_tap_copy_never_the_original() -> None:
    seen: list[tuple[object, object]] = []
    tapped = dataclasses.replace(PROMPT_COMPLETION, row_observer=lambda r, h: seen.append((r, h)))
    row = {"prompt": "p", "completion": "c"}
    ic.apply_mapping(tapped, row)
    captured_row = seen[0][0]
    check("TAP-7", "the observer's row is equal but not the same object", captured_row is not row)
    captured_row["prompt"] = "mutated"
    check(
        "TAP-8",
        "mutating the observer's copy never reaches the original row",
        row["prompt"] == "p",
    )


def test_tap_fires_even_when_the_row_is_then_rejected() -> None:
    seen: list[object] = []
    tapped = dataclasses.replace(PROMPT_COMPLETION, row_observer=lambda r, h: seen.append(r))
    try:
        ic.apply_mapping(tapped, {"prompt": "p"})  # missing completion
    except ic.RowMappingError:
        pass
    check("TAP-9", "the observer fires before the mapping fails", seen == [{"prompt": "p"}])


def test_tap_runs_for_tabular_rows_with_headers() -> None:
    headers = ["q", "a"]
    seen: list[tuple[object, object]] = []
    tapped = dataclasses.replace(
        CSV_PROMPT_COMPLETION, row_observer=lambda r, h: seen.append((r, h))
    )
    ic.apply_mapping(tapped, ["hi", "there"], headers=headers)
    check("TAP-10", "the observer receives the row", seen[0][0] == ["hi", "there"])
    check("TAP-11", "the observer receives the real headers", seen[0][1] == ("q", "a"))
    check(
        "TAP-12",
        "the headers the observer saw are an immutable tuple, not the importer's own list",
        type(seen[0][1]) is tuple,
    )


def test_observer_copy_is_deep_not_shallow() -> None:
    # OBSERVER-COPY: a shallow dict()/list() copy still shares every nested
    # mutable value with the real row -- a nested-mutating observer must
    # never be able to reach the original through it.
    seen: list[object] = []
    tapped = dataclasses.replace(JSON_PROMPT_COMPLETION, row_observer=lambda r, h: seen.append(r))
    row = {"prompt": "p", "completion": "c", "extra_nested": {"a": "b"}, "extra_list": [1, 2]}
    ic.apply_mapping(tapped, row)
    captured = seen[0]
    assert isinstance(captured, dict)
    captured["extra_nested"]["a"] = "MUTATED"
    captured["extra_list"].append("MUTATED")
    check(
        "OBSCOPY-1",
        "mutating a nested dict in the observer's copy never reaches the original row",
        row["extra_nested"]["a"] == "b",
    )
    check(
        "OBSCOPY-2",
        "mutating a nested list in the observer's copy never reaches the original row",
        row["extra_list"] == [1, 2],
    )


def test_observer_nested_mutation_never_changes_the_real_candidate() -> None:
    # Differential (OBSERVER-COPY), direct against `apply_mapping` (never
    # through `run_mapping_preview`/`run_mapping_probe`: both always
    # install their *own* row-pairing tap via `_with_row_tap`'s own
    # `dataclasses.replace`, which -- `row_observer` being a single field,
    # not a chain -- silently discards any observer already on the mapping
    # passed in, so a second, externally-attached observer could never be
    # exercised through them at all). The exact same row, mapped once with
    # no observer and once with a deliberately nested-mutating one, must
    # produce a byte-identical candidate -- across a messages-shaped
    # (nested array) row, a prompt/completion JSON-shaped (nested dict)
    # row, and a flat CSV-shaped (tabular) row, which carries no nested
    # structure of its own, so the mutator is a disclosed no-op there,
    # still proving the format is unaffected by attaching one at all.
    def nested_mutator(row: object, _headers: object) -> None:
        if isinstance(row, dict):
            messages = row.get("messages")
            if isinstance(messages, list) and messages and isinstance(messages[0], dict):
                messages[0]["content"] = "TAMPERED-BY-OBSERVER"
            nested = row.get("nested")
            if isinstance(nested, dict):
                nested["x"] = "TAMPERED-BY-OBSERVER"
        elif isinstance(row, list):
            for value in row:
                if isinstance(value, dict):
                    value["tampered"] = "yes"

    cases = (
        (
            MESSAGES_JSON,
            {"messages": [{"role": "human", "content": "hi"}, {"role": "ai", "content": "there"}]},
            None,
        ),
        (
            JSON_PROMPT_COMPLETION,
            {"prompt": "p1", "completion": "c1", "nested": {"x": "y"}},
            None,
        ),
        (CSV_PROMPT_COMPLETION, ["hello", "world"], ("q", "a")),
    )
    for mapping, row, headers in cases:
        plain_candidate = ic.apply_mapping(mapping, row, headers=headers).raw_record
        tapped_mapping = dataclasses.replace(mapping, row_observer=nested_mutator)
        tapped_candidate = ic.apply_mapping(tapped_mapping, row, headers=headers).raw_record
        check(
            f"OBSCOPY-3-{mapping.shape}",
            f"{mapping.shape}: the real candidate is identical with vs without a "
            "nested-mutating observer",
            plain_candidate == tapped_candidate,
        )


# --- TOOL-VALIDATOR ---------------------------------------------------------------------------


def test_tool_argument_validator_threaded_through_to_the_importer() -> None:
    # TOOL-VALIDATOR: without `tool_argument_validator`, every tool-call
    # row is rejected `canonical_schema_invalid` regardless of whether its
    # own arguments are genuinely valid (the importer's own documented
    # fail-closed default for "no validator supplied") -- making a
    # tool-call-bearing source impossible to preview meaningfully. Passing
    # FTF-0405's real `validate_instance` plus the real schema accepts a
    # genuinely valid call and still rejects a genuinely invalid one.
    tool_mapping = ic.mapping_from_object(
        {
            "shape": "messages_json",
            "messages": {"key": "messages"},
            "role_key": "role",
            "content_key": "content",
            "tool_calls_key": "tool_calls",
            "tool_call_id_key": "tool_call_id",
            "name_key": "name",
            "role_map": {"user": "user", "assistant": "assistant", "tool": "tool"},
            "on_unmapped_message_keys": "ignore",
        }
    )
    schema = {
        "type": "object",
        "properties": {"order_id": {"type": "string"}},
        "required": ["order_id"],
        "additionalProperties": False,
    }
    data = (
        b'{"messages": [{"role": "user", "content": "hi"}, '
        b'{"role": "assistant", "content": "ok", '
        b'"tool_calls": [{"id": "call_1", "name": "lookup_order", '
        b'"arguments": {"order_id": "abc"}}]}, '
        b'{"role": "tool", "content": "found", "tool_call_id": "call_1", '
        b'"name": "lookup_order"}]}\n'
    )
    without_validator = rev.run_mapping_preview(
        reader_for(data),
        tool_mapping,
        verified_content_type="application/jsonl",
        dataset_sha256=sha(data),
        tool_schemas={"lookup_order": schema},
        reveal=True,
    )
    check(
        "TOOLVAL-1",
        "without a tool_argument_validator, a genuinely valid tool call is still rejected "
        "canonical_schema_invalid",
        without_validator.rows[0].outcome == "rejected"
        and without_validator.rows[0].code == "canonical_schema_invalid",
    )
    with_validator = rev.run_mapping_preview(
        reader_for(data),
        tool_mapping,
        verified_content_type="application/jsonl",
        dataset_sha256=sha(data),
        tool_schemas={"lookup_order": schema},
        tool_argument_validator=validate_instance,
        reveal=True,
    )
    check(
        "TOOLVAL-2",
        "with the real validate_instance and the real schema, a genuinely valid tool call "
        "is accepted",
        with_validator.rows[0].outcome == "accepted",
    )
    bad_data = (
        b'{"messages": [{"role": "user", "content": "hi"}, '
        b'{"role": "assistant", "content": "ok", '
        b'"tool_calls": [{"id": "call_1", "name": "lookup_order", '
        b'"arguments": {"order_id": 123}}]}, '
        b'{"role": "tool", "content": "found", "tool_call_id": "call_1", '
        b'"name": "lookup_order"}]}\n'
    )
    bad_result = rev.run_mapping_preview(
        reader_for(bad_data),
        tool_mapping,
        verified_content_type="application/jsonl",
        dataset_sha256=sha(bad_data),
        tool_schemas={"lookup_order": schema},
        tool_argument_validator=validate_instance,
        reveal=True,
    )
    check(
        "TOOLVAL-3",
        "a genuinely invalid tool call (wrong argument type) is still rejected even with a "
        "real validator and schema",
        bad_result.rows[0].outcome == "rejected"
        and bad_result.rows[0].code == "canonical_schema_invalid",
    )


# --- DISP ----------------------------------------------------------------------------------


def test_dispatch_unavailable_for_xlsx_and_unknown() -> None:
    for content_type in (
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "application/octet-stream",
        "",
    ):
        raised = None
        try:
            rev.run_mapping_preview(
                reader_for(b"x"),
                PROMPT_COMPLETION,
                verified_content_type=content_type,
                dataset_sha256=sha(b"x"),
                tool_schemas=None,
                reveal=True,
            )
        except rev.MappingPreviewUnavailableError as exc:
            raised = exc
        check(
            "DISP-1",
            f"{content_type!r} is importer_unavailable",
            raised is not None and raised.code == "importer_unavailable",
        )
        check(
            "DISP-1b",
            f"{content_type!r}'s exception carries the content type, in its message and attribute",
            raised is not None
            and raised.verified_content_type == content_type
            and content_type in str(raised),
        )
        probe_raised = None
        try:
            rev.run_mapping_probe(
                reader_for(b"x"),
                PROMPT_COMPLETION,
                verified_content_type=content_type,
                dataset_sha256=sha(b"x"),
                reveal=True,
            )
        except rev.MappingPreviewUnavailableError as exc:
            probe_raised = exc
        check(
            "DISP-2",
            f"probe({content_type!r}) is also importer_unavailable",
            probe_raised is not None,
        )


def test_dispatch_covers_every_declared_content_type() -> None:
    for content_type in rev.PREVIEW_CONTENT_TYPES:
        check(
            f"DISP-3-{content_type}",
            f"{content_type} has a dispatched importer",
            content_type in rev._EVENTS_BY_CONTENT_TYPE,
        )


# --- MASK ------------------------------------------------------------------------------------


def _is_masked_marker(value: object) -> bool:
    return (
        isinstance(value, Mapping)
        and value.get("masked") is True
        and isinstance(value.get("chars"), int)
    )


def test_mask_every_value_and_every_key_name() -> None:
    # MASK-LEAK: Section 67.2 masks every string leaf *and* every header/
    # key name -- a masked JSON row can no longer be a plain dict keyed by
    # the real name (a dict key must be hashable; the masked marker is a
    # dict, so it can never be one), so it comes back as an ordered tuple
    # of {"key": masked_name, "value": masked_value} pairs instead.
    data = b'{"prompt": "secret-question", "completion": "secret-answer", "pii": "123-45-6789"}\n'
    result = rev.run_mapping_preview(
        reader_for(data),
        PROMPT_COMPLETION,
        verified_content_type="application/jsonl",
        dataset_sha256=sha(data),
        tool_schemas=None,
        reveal=False,
    )
    row = result.rows[0]
    raw = row.raw_row
    assert isinstance(raw, tuple)
    check("MASK-1", "the raw row is a tuple of key/value pairs, never a plain dict", True)
    check("MASK-1b", "every real key name is masked", all(_is_masked_marker(e["key"]) for e in raw))
    check(
        "MASK-2",
        "every real value is masked",
        all(_is_masked_marker(e["value"]) for e in raw),
    )
    check(
        "MASK-2b",
        "the masked chars count matches each real key/value's own real length",
        {e["key"]["chars"] for e in raw} == {len("prompt"), len("completion"), len("pii")}
        and {e["value"]["chars"] for e in raw}
        == {len("secret-question"), len("secret-answer"), len("123-45-6789")},
    )
    check(
        "MASK-3",
        "none of the real text leaks through a masked value or key",
        "secret-question" not in str(raw)
        and "123-45-6789" not in str(raw)
        and "prompt" not in str(raw)
        and "completion" not in str(raw),
    )


def test_mask_reveal_true_shows_the_real_values() -> None:
    data = b'{"prompt": "p1", "completion": "c1"}\n'
    result = rev.run_mapping_preview(
        reader_for(data),
        PROMPT_COMPLETION,
        verified_content_type="application/jsonl",
        dataset_sha256=sha(data),
        tool_schemas=None,
        reveal=True,
    )
    check(
        "MASK-4",
        "reveal=True returns the real row",
        result.rows[0].raw_row == {"prompt": "p1", "completion": "c1"},
    )


def test_mask_nested_structures_and_scalars() -> None:
    masked_list = rev._mask_leaf([1, "x", None, True, {"k": "v"}])
    check("MASK-5", "a list's own values are each masked", isinstance(masked_list, list))
    check("MASK-6", "None is never masked (not a string leaf)", masked_list[2] is None)
    check("MASK-7", "a bool is never masked (not a string leaf)", masked_list[3] is True)
    check("MASK-7b", "an int is never masked (not a string leaf)", masked_list[0] == 1)
    check("MASK-8", "a string is masked", _is_masked_marker(masked_list[1]))
    check(
        "MASK-8b",
        "the masked chars count is the real string's own length",
        masked_list[1]["chars"] == 1,
    )
    nested = masked_list[4]
    check(
        "MASK-9",
        "a nested dict's own key is masked too (MASK-LEAK-NESTED): a pair tuple, no raw key",
        isinstance(nested, tuple)
        and len(nested) == 1
        and "'k'" not in repr(nested)
        and _is_masked_marker(nested[0]["key"]),
    )
    check(
        "MASK-10",
        "a nested dict's own value is masked",
        _is_masked_marker(nested[0]["value"]),
    )


def test_mask_candidate_type_tag_and_identical_flag_stay_visible() -> None:
    # MASK-LEAK's own disclosed exception: a ContentPart's `"type"` tag
    # ("text"/"json") is schema, never raw content, and stays in clear even
    # under full masking; `ComparisonCell.identical`/`raw_type`/
    # `canonical_type` are a separate field entirely, never masked at all.
    data = b'{"prompt": "secret-prompt-text", "completion": "secret-completion-text"}\n'
    result = rev.run_mapping_preview(
        reader_for(data),
        PROMPT_COMPLETION,
        verified_content_type="application/jsonl",
        dataset_sha256=sha(data),
        tool_schemas=None,
        reveal=False,
    )
    row = result.rows[0]
    assert row.candidate is not None
    messages = row.candidate["content"]["messages"]  # type: ignore[index]
    for message in messages:
        for part in message["content"]:
            check(
                "MASK-TYPE-1",
                "the content part's own type tag stays in clear",
                part["type"] in ("text", "json"),
            )
            if part["type"] == "text":
                check(
                    "MASK-TYPE-2",
                    "the content part's own text is masked",
                    _is_masked_marker(part["text"]),
                )
    check(
        "MASK-TYPE-3",
        "none of the real prompt/completion text leaks through the masked candidate",
        "secret-prompt-text" not in str(row.candidate)
        and "secret-completion-text" not in str(row.candidate),
    )
    check(
        "MASK-TYPE-4",
        "comparison cells (raw_type/canonical_type/identical) are never masked",
        all(isinstance(cell.identical, bool) for cell in row.comparison),
    )


def test_mask_rejected_row_candidate_stays_none() -> None:
    # A rejected row's own candidate is None, never built -- masking it
    # must be a safe no-op, never a crash, under reveal=False.
    data = b'{"prompt": "p1"}\n'  # rejected: missing completion
    result = rev.run_mapping_preview(
        reader_for(data),
        PROMPT_COMPLETION,
        verified_content_type="application/jsonl",
        dataset_sha256=sha(data),
        tool_schemas=None,
        reveal=False,
    )
    check(
        "MASK-REJECTED-1",
        "a rejected row is reported as such",
        result.rows[0].outcome == "rejected",
    )
    check(
        "MASK-REJECTED-2",
        "masking a rejected row's own None candidate is a safe no-op, never a crash",
        result.rows[0].candidate is None,
    )


def test_mask_candidate_type_tag_exact_not_case_folded() -> None:
    # EXACT: `_mask_candidate`'s own `key == "type"` comparison must stay
    # exact -- a differently-cased "Type" key (never produced by this
    # module's own fixed candidate schema, but white-box-constructed here
    # the same way every other EXACT test in this file bypasses a real
    # construction path) is never folded into the real "type" tag
    # exception by a `.strip().lower()` normalization.
    # A real ContentPart keeps its "type" tag; every other object -- a differently-cased tag, an
    # extra key, a case-tampered "Type" -- is not a ContentPart, so its "type" is masked too.
    part = rev._mask_candidate({"type": "text", "text": "also-secret"})
    assert part is not None
    check(
        "MASK-EXACT-2",
        "a real ContentPart's exact 'type' tag stays in clear",
        part["type"] == "text",
    )
    check("MASK-EXACT-3", "an ordinary value is masked", _is_masked_marker(part["text"]))
    for tag, body in (
        ("MASK-EXACT-1a", {"Type": "secret-A", "type": "text", "text": "secret-B"}),
        ("MASK-EXACT-1b", {"type": "Text", "text": "secret-C"}),
        ("MASK-EXACT-1c", {"type": "text ", "text": "secret-D"}),
    ):
        masked = rev._mask_candidate(body)
        assert masked is not None
        check(
            tag,
            "an object that is not an exact ContentPart masks its 'type' value as well",
            all(_is_masked_marker(v) for v in masked.values()),
        )


def test_mask_not_mapped_and_observed_role_values_and_probe_headers() -> None:
    # F-MASKNAMES: `_mask_names` actually feeds `not_mapped`/
    # `observed_role_values`/`ProbeResult.headers` -- not merely exercised
    # as a side effect of some other check.
    data = b'{"messages": [{"role": "human", "content": "hi"}], "extra_col": "secret-value"}\n'
    result = rev.run_mapping_preview(
        reader_for(data),
        MESSAGES_JSON,
        verified_content_type="application/jsonl",
        dataset_sha256=sha(data),
        tool_schemas=None,
        reveal=False,
    )
    check(
        "MASK-NAMES-1",
        "not_mapped is non-empty (the fixture names an unmapped column) and every name in "
        "it is masked",
        len(result.rows[0].not_mapped) == 1 and _is_masked_marker(result.rows[0].not_mapped[0]),
    )
    check(
        "MASK-NAMES-2",
        "observed_role_values is non-empty and every raw role string in it is masked",
        len(result.observed_role_values) == 1 and _is_masked_marker(result.observed_role_values[0]),
    )
    check(
        "MASK-NAMES-3",
        "none of the real names leak through",
        "extra_col" not in str(result.rows[0].not_mapped)
        and "human" not in str(result.observed_role_values),
    )
    csv_data = b"q,a\nhello,world\n"
    csv_probe = rev.run_mapping_probe(
        reader_for(csv_data),
        CSV_PROMPT_COMPLETION,
        verified_content_type="text/csv",
        dataset_sha256=sha(csv_data),
        reveal=False,
    )
    check(
        "MASK-NAMES-4",
        "a masked probe's own headers are masked too",
        csv_probe.headers is not None
        and len(csv_probe.headers) == 2
        and all(_is_masked_marker(h) for h in csv_probe.headers),
    )


def test_mask_tabular_row_pairs_with_headers() -> None:
    data = b"q,a\nhello,world\n"
    result = rev.run_mapping_preview(
        reader_for(data),
        CSV_PROMPT_COMPLETION,
        verified_content_type="text/csv",
        dataset_sha256=sha(data),
        tool_schemas=None,
        reveal=False,
    )
    row = result.rows[0]
    raw = row.raw_row
    assert isinstance(raw, tuple)
    check(
        "MASK-11",
        "a tabular raw row's own real header names are masked, never kept in clear",
        all(_is_masked_marker(e["header"]) for e in raw),
    )
    check(
        "MASK-12",
        "its values are masked",
        all(_is_masked_marker(e["value"]) for e in raw),
    )
    check(
        "MASK-13",
        "none of the real header names or values leak through",
        "hello" not in str(raw) and "world" not in str(raw),
    )


# --- PAIR / NMAP / CMP -----------------------------------------------------------------------


def test_pair_accepted_and_rejected_rows_in_order() -> None:
    data = (
        b'{"prompt": "p1", "completion": "c1"}\n'
        b'{"prompt": "p2"}\n'  # rejected: missing completion
        b'{"prompt": "p3", "completion": "c3", "extra": 1}\n'
    )
    result = rev.run_mapping_preview(
        reader_for(data),
        PROMPT_COMPLETION,
        verified_content_type="application/jsonl",
        dataset_sha256=sha(data),
        tool_schemas=None,
        reveal=True,
    )
    check("PAIR-1", "three rows pair with three records", len(result.rows) == 3)
    check(
        "PAIR-2",
        "row 1 accepted with its own raw row",
        result.rows[0].outcome == "accepted" and result.rows[0].raw_row["prompt"] == "p1",  # type: ignore[call-overload]
    )
    check(
        "PAIR-3",
        "row 2 rejected required_field_missing with its own raw row",
        result.rows[1].outcome == "rejected"
        and result.rows[1].code == "required_field_missing"
        and result.rows[1].raw_row == {"prompt": "p2"},
    )
    check(
        "PAIR-4",
        "row 3 accepted with its own raw row",
        result.rows[2].raw_row["extra"] == 1,  # type: ignore[call-overload]
    )
    check("NMAP-1", "row 3's extra key is not_mapped", result.rows[2].not_mapped == ("extra",))
    check("NMAP-2", "row 1 has nothing left over", result.rows[0].not_mapped == ())
    check(
        "CMP-1",
        "row 1's comparison cells show identical raw/canonical text",
        all(cell.identical for cell in result.rows[0].comparison),
    )


def test_pair_row_that_never_reaches_apply_mapping_has_no_raw_row() -> None:
    # Malformed JSON on line 2: fails strict_loads before apply_mapping is ever called.
    data = b'{"prompt": "p1", "completion": "c1"}\nnot json\n'
    result = rev.run_mapping_preview(
        reader_for(data),
        PROMPT_COMPLETION,
        verified_content_type="application/jsonl",
        dataset_sha256=sha(data),
        tool_schemas=None,
        reveal=True,
    )
    check("PAIR-5", "two rows seen", len(result.rows) == 2)
    check("PAIR-6", "row 1 has a raw row", result.rows[0].raw_row is not None)
    check(
        "PAIR-7",
        "row 2 (invalid_json) never reached apply_mapping, so it has no raw row",
        result.rows[1].code == "invalid_json" and result.rows[1].raw_row is None,
    )
    check(
        "PAIR-8",
        "a rejected row with no raw row has no not_mapped columns either (nothing to compute "
        "them from)",
        result.rows[1].not_mapped == (),
    )


def test_nmap_rejected_row_with_a_real_raw_row_still_computes_not_mapped() -> None:
    # A row that reaches apply_mapping, carries extra data, but is still rejected (missing
    # completion) -- pins the REJECTED branch's own `if row is not None` guard separately from
    # the ACCEPTED branch's (NMAP-1 above only exercises the accepted one).
    data = b'{"prompt": "p1", "extra": "unused"}\n'
    result = rev.run_mapping_preview(
        reader_for(data),
        PROMPT_COMPLETION,
        verified_content_type="application/jsonl",
        dataset_sha256=sha(data),
        tool_schemas=None,
        reveal=True,
    )
    check(
        "NMAP-4",
        "a rejected row that did reach apply_mapping still gets its own not_mapped computed",
        result.rows[0].outcome == "rejected" and result.rows[0].not_mapped == ("extra",),
    )


def test_nmap_csv_header_not_referenced_by_any_slot() -> None:
    data = b"q,a,unused\nhello,world,x\n"
    result = rev.run_mapping_preview(
        reader_for(data),
        CSV_PROMPT_COMPLETION,
        verified_content_type="text/csv",
        dataset_sha256=sha(data),
        tool_schemas=None,
        reveal=True,
    )
    check("NMAP-3", "the unused CSV column is not_mapped", result.rows[0].not_mapped == ("unused",))


def test_cmp_identical_false_when_the_raw_value_differs_from_canonical() -> None:
    # A canonical content string is always trimmed of nothing by prompt_completion mapping, so
    # force a mismatch by comparing against a field that is genuinely a different value:
    # metadata.language is passed through unchanged, so pin the *positive* case and the
    # *missing* case (the slot absent from a row it is mapped against).
    mapping = ic.mapping_from_object(
        {
            "shape": "prompt_completion",
            "prompt": {"key": "prompt"},
            "completion": {"key": "completion"},
            "metadata": {"language": {"key": "lang"}},
        }
    )
    with_lang = rev.run_mapping_preview(
        reader_for(b'{"prompt": "p", "completion": "c", "lang": "ar"}\n'),
        mapping,
        verified_content_type="application/jsonl",
        dataset_sha256=sha(b'{"prompt": "p", "completion": "c", "lang": "ar"}\n'),
        tool_schemas=None,
        reveal=True,
    )
    cell = next(c for c in with_lang.rows[0].comparison if c.slot == "metadata.language")
    check(
        "CMP-2",
        "a present metadata slot is identical",
        cell.identical and cell.raw_type == "str" and cell.canonical_type == "str",
    )

    without_lang = rev.run_mapping_preview(
        reader_for(b'{"prompt": "p", "completion": "c"}\n'),
        mapping,
        verified_content_type="application/jsonl",
        dataset_sha256=sha(b'{"prompt": "p", "completion": "c"}\n'),
        tool_schemas=None,
        reveal=True,
    )
    cell2 = next(c for c in without_lang.rows[0].comparison if c.slot == "metadata.language")
    check(
        "CMP-3",
        "a slot absent from both the raw row and the canonical metadata is\n"
        "missing/missing, not falsely identical",
        not cell2.identical and cell2.raw_type == "missing" and cell2.canonical_type == "missing",
    )


# --- LIM -------------------------------------------------------------------------------------


def test_limit_stops_preview_at_preview_max_records() -> None:
    limits = dataclasses.replace(rev.PREVIEW_LIMITS, preview_max_records=2)
    data = b"".join(f'{{"prompt": "p{i}", "completion": "c{i}"}}\n'.encode() for i in range(5))
    result = rev.run_mapping_preview(
        reader_for(data, limits=limits),
        PROMPT_COMPLETION,
        verified_content_type="application/jsonl",
        dataset_sha256=sha(data),
        tool_schemas=None,
        reveal=True,
        limits=limits,
    )
    check("LIM-1", "preview stops at the limit", len(result.rows) == 2)
    check("LIM-2", "complete is False: more rows existed than were shown", result.complete is False)


def test_limit_complete_true_when_the_source_truly_ends() -> None:
    data = b'{"prompt": "p0", "completion": "c0"}\n'
    result = rev.run_mapping_preview(
        reader_for(data),
        PROMPT_COMPLETION,
        verified_content_type="application/jsonl",
        dataset_sha256=sha(data),
        tool_schemas=None,
        reveal=True,
    )
    check("LIM-3", "a short source that ends on its own is complete", result.complete is True)
    check(
        "LIM-4", "state is completed", result.state == "completed" and result.failure_code is None
    )


def test_probe_limits_rows_the_same_way() -> None:
    limits = dataclasses.replace(rev.PREVIEW_LIMITS, preview_max_records=1)
    data = b'{"prompt": "p0", "completion": "c0"}\n{"prompt": "p1", "completion": "c1"}\n'
    probe = rev.run_mapping_probe(
        reader_for(data, limits=limits),
        PROMPT_COMPLETION,
        verified_content_type="application/jsonl",
        dataset_sha256=sha(data),
        reveal=True,
        limits=limits,
    )
    check("LIM-5", "the probe stops at its own limit too", len(probe.raw_rows) == 1)


def test_exactly_the_limit_many_rows_is_complete_not_truncated() -> None:
    # `complete` means the whole source was really seen: a source with
    # *exactly* `preview_max_records` rows, all captured, is complete --
    # never confused with a source that had more and got cut off at the
    # same count (the prior bug: `len(rows) < limits.preview_max_records`
    # could never be True once the limit was actually reached, so an
    # exactly-sized source was always wrongly marked incomplete).
    limits = dataclasses.replace(rev.PREVIEW_LIMITS, preview_max_records=2)
    data = b"".join(f'{{"prompt": "p{i}", "completion": "c{i}"}}\n'.encode() for i in range(2))
    preview = rev.run_mapping_preview(
        reader_for(data, limits=limits),
        PROMPT_COMPLETION,
        verified_content_type="application/jsonl",
        dataset_sha256=sha(data),
        tool_schemas=None,
        reveal=True,
        limits=limits,
    )
    check(
        "LIM-6",
        "a preview source with exactly the limit's own row count is complete",
        len(preview.rows) == 2 and preview.complete is True,
    )
    probe = rev.run_mapping_probe(
        reader_for(data, limits=limits),
        PROMPT_COMPLETION,
        verified_content_type="application/jsonl",
        dataset_sha256=sha(data),
        reveal=True,
        limits=limits,
    )
    check(
        "LIM-7",
        "a probe source with exactly the limit's own row count is complete",
        len(probe.raw_rows) == 2 and probe.complete is True,
    )


def test_one_more_than_the_limit_is_truncated_not_complete() -> None:
    # The flip side, at the exact same limit: one row *past* it must still
    # be incomplete, proving LIM-6/7 are not simply always True.
    limits = dataclasses.replace(rev.PREVIEW_LIMITS, preview_max_records=2)
    data = b"".join(f'{{"prompt": "p{i}", "completion": "c{i}"}}\n'.encode() for i in range(3))
    preview = rev.run_mapping_preview(
        reader_for(data, limits=limits),
        PROMPT_COMPLETION,
        verified_content_type="application/jsonl",
        dataset_sha256=sha(data),
        tool_schemas=None,
        reveal=True,
        limits=limits,
    )
    check(
        "LIM-8",
        "a preview source with one row past the limit is not complete",
        len(preview.rows) == 2 and preview.complete is False,
    )
    probe = rev.run_mapping_probe(
        reader_for(data, limits=limits),
        PROMPT_COMPLETION,
        verified_content_type="application/jsonl",
        dataset_sha256=sha(data),
        reveal=True,
        limits=limits,
    )
    check(
        "LIM-9",
        "a probe source with one row past the limit is not complete",
        len(probe.raw_rows) == 2 and probe.complete is False,
    )


def test_probe_state_is_source_unavailable_on_an_outage() -> None:
    # PROBE-LAW17: a storage outage is a visible state/failure_code on the
    # probe too, never only an opaque complete=False.
    class _ExplodingSource:
        def __iter__(self) -> Iterator[bytes]:
            return self

        def __next__(self) -> bytes:
            raise OSError("storage endpoint unreachable")

    reader = ic.ChunkReader(
        _ExplodingSource(), expected_size=100, expected_sha256="0" * 64, limits=rev.PREVIEW_LIMITS
    )
    probe = rev.run_mapping_probe(
        reader,
        PROMPT_COMPLETION,
        verified_content_type="application/jsonl",
        dataset_sha256="0" * 64,
        reveal=True,
    )
    check(
        "PROBELAW17-1",
        "a real storage outage is blocked/source_unavailable on the probe, never a raised "
        "exception or a bare complete=False",
        probe.state == "blocked" and probe.failure_code == "source_unavailable",
    )
    check("PROBELAW17-2", "complete is False alongside the visible state", probe.complete is False)


def test_probe_state_completed_on_a_normal_run() -> None:
    data = b'{"prompt": "p0", "completion": "c0"}\n'
    probe = rev.run_mapping_probe(
        reader_for(data),
        PROMPT_COMPLETION,
        verified_content_type="application/jsonl",
        dataset_sha256=sha(data),
        reveal=True,
    )
    check(
        "PROBELAW17-3",
        "a normal run is state=completed, failure_code=None",
        probe.state == "completed" and probe.failure_code is None,
    )


def test_source_changed_detected_even_when_truncation_would_have_hidden_it() -> None:
    # SOURCE-CHANGED (Law 17): a preview reads only up to the row limit --
    # fewer bytes than the whole object, by design. The `for event in
    # stream` loop's own iteration protocol already has to pull *one*
    # event past the limit to discover it should stop (which is also how
    # `complete` tells "exactly N rows" from "N+1, truncated" apart) -- so
    # a tampered tail sitting within that single extra pull would already
    # be caught naturally, without this task's own fix. This reproduces
    # the gap that fix actually closes: tampering that sits *further* into
    # the stream than that one-event lookahead ever reaches (chunk3 below,
    # genuinely never touched by the import machinery once this preview's
    # own 1-row limit is satisfied from chunk1 alone) must still be caught
    # -- `ChunkReader`'s own size/sha256 check otherwise fires only at a
    # real end-of-stream, which a preview that stops early never reaches.
    chunk1 = b'{"prompt": "p0", "completion": "c0"}\n'
    chunk2 = b'{"prompt": "p1", "completion": "c1"}\n'
    chunk3 = b'{"prompt": "p2", "completion": "c2"}\n'  # never declared; never pulled without drain
    declared_size = len(chunk1) + len(chunk2)
    limits = dataclasses.replace(rev.PREVIEW_LIMITS, preview_max_records=1)
    reader = ic.ChunkReader(
        [chunk1, chunk2, chunk3],
        expected_size=declared_size,
        expected_sha256=sha(chunk1 + chunk2),
        limits=limits,
    )
    result = rev.run_mapping_preview(
        reader,
        PROMPT_COMPLETION,
        verified_content_type="application/jsonl",
        dataset_sha256=sha(chunk1 + chunk2),
        tool_schemas=None,
        reveal=True,
        limits=limits,
    )
    check(
        "SRCCHANGED-1",
        "the one row the limit allows is still shown",
        len(result.rows) == 1,
    )
    check(
        "SRCCHANGED-2",
        "a tampered chunk sitting past both the row limit's own natural one-event "
        "lookahead and the declared size is still detected, never silently reported as a "
        "clean completed preview",
        result.state == "failed" and result.failure_code == "source_changed",
    )


def test_source_changed_detected_by_probe_too() -> None:
    chunk1 = b'{"prompt": "p0", "completion": "c0"}\n'
    chunk2 = b'{"prompt": "p1", "completion": "c1"}\n'
    chunk3 = b'{"prompt": "p2", "completion": "c2"}\n'
    declared_size = len(chunk1) + len(chunk2)
    limits = dataclasses.replace(rev.PREVIEW_LIMITS, preview_max_records=1)
    reader = ic.ChunkReader(
        [chunk1, chunk2, chunk3],
        expected_size=declared_size,
        expected_sha256=sha(chunk1 + chunk2),
        limits=limits,
    )
    probe = rev.run_mapping_probe(
        reader,
        PROMPT_COMPLETION,
        verified_content_type="application/jsonl",
        dataset_sha256=sha(chunk1 + chunk2),
        reveal=True,
        limits=limits,
    )
    check(
        "SRCCHANGED-3",
        "the probe also detects tampering past its own one-event lookahead",
        probe.state == "failed" and probe.failure_code == "source_changed",
    )


# --- OUT -------------------------------------------------------------------------------------


def test_outage_never_raises_and_is_the_state() -> None:
    # expected_sha256 mismatched to the real bytes' hash -> the ChunkReader itself aborts
    # (failed/source_changed) on its first read, never a raised exception out of preview.
    data = b'{"prompt": "p", "completion": "c"}\n'
    reader = ic.ChunkReader(
        [data], expected_size=len(data), expected_sha256="0" * 64, limits=rev.PREVIEW_LIMITS
    )
    result = rev.run_mapping_preview(
        reader,
        PROMPT_COMPLETION,
        verified_content_type="application/jsonl",
        dataset_sha256=sha(data),
        tool_schemas=None,
        reveal=True,
    )
    check(
        "OUT-1",
        "a hash mismatch is failed/source_changed, not an exception",
        result.state == "failed" and result.failure_code == "source_changed",
    )
    check("OUT-2", "complete is False on an outage", result.complete is False)


def test_outage_mid_stream() -> None:
    def failing_source() -> object:
        yield b'{"prompt": "p0", "completion": "c0"}\n'
        raise RuntimeError("storage dropped")

    data = b'{"prompt": "p0", "completion": "c0"}\n'
    reader = ic.ChunkReader(
        failing_source(), expected_size=len(data) + 100, expected_sha256=sha(data)
    )
    result = rev.run_mapping_preview(
        reader,
        PROMPT_COMPLETION,
        verified_content_type="application/jsonl",
        dataset_sha256=sha(data),
        tool_schemas=None,
        reveal=True,
    )
    check(
        "OUT-3",
        "a mid-stream source failure is blocked/source_unavailable, not an exception",
        result.state == "blocked" and result.failure_code == "source_unavailable",
    )


def test_deadline_checked_per_event_not_only_per_chunk() -> None:
    # Every row below arrives in a *single* in-memory chunk, so the ChunkReader's own internal
    # deadline check (run once, before that one chunk is handed back) cannot by itself catch a
    # deadline that expires while several already-buffered rows are then processed one by one
    # -- proving this module's own explicit per-event `reader.deadline.check()` call (the same
    # defense-in-depth convention `jsonl_importer._drive` already uses) is load-bearing.
    calls = {"n": 0}

    def clock() -> float:
        calls["n"] += 1
        if calls["n"] <= 2:
            return 0.0
        return 1000.0

    data = b"".join(f'{{"prompt": "p{i}", "completion": "c{i}"}}\n'.encode() for i in range(5))
    reader = ic.ChunkReader(
        [data],
        expected_size=len(data),
        expected_sha256=sha(data),
        limits=dataclasses.replace(rev.PREVIEW_LIMITS, deadline_seconds=1.0),
        clock=clock,
    )
    result = rev.run_mapping_preview(
        reader,
        PROMPT_COMPLETION,
        verified_content_type="application/jsonl",
        dataset_sha256=sha(data),
        tool_schemas=None,
        reveal=True,
    )
    check(
        "DEADLINE-1",
        f"a clock that expires between buffered events aborts as a timeout ({result.state}/"
        f"{result.failure_code}, {len(result.rows)} rows seen)",
        result.state == "failed"
        and result.failure_code == "parse_timeout"
        and len(result.rows) < 5,
    )

    calls2 = {"n": 0}

    def clock2() -> float:
        calls2["n"] += 1
        if calls2["n"] <= 2:
            return 0.0
        return 1000.0

    reader2 = ic.ChunkReader(
        [data],
        expected_size=len(data),
        expected_sha256=sha(data),
        limits=dataclasses.replace(rev.PREVIEW_LIMITS, deadline_seconds=1.0),
        clock=clock2,
    )
    probe_result = rev.run_mapping_probe(
        reader2,
        PROMPT_COMPLETION,
        verified_content_type="application/jsonl",
        dataset_sha256=sha(data),
        reveal=True,
    )
    check(
        "DEADLINE-2",
        "the probe's own per-event check aborts the same way, never returning all 5 rows",
        len(probe_result.raw_rows) < 5 and probe_result.complete is False,
    )


# --- TOOL / SHA --------------------------------------------------------------------------------


def test_tool_definitions_sha256_canonical_and_none_equals_empty() -> None:
    a = rev.compute_tool_definitions_sha256(None)
    b = rev.compute_tool_definitions_sha256({})
    check("TOOL-1", "None and {} hash identically", a == b)
    c = rev.compute_tool_definitions_sha256({"x": {"type": "object"}})
    check("TOOL-2", "a non-empty tool set hashes differently", c != a)
    d = rev.compute_tool_definitions_sha256({"x": {"type": "object"}})
    check("TOOL-3", "the digest is deterministic", c == d)
    e = rev.compute_tool_definitions_sha256({"y": {"a": 1}, "x": {"b": 2}})
    f = rev.compute_tool_definitions_sha256({"x": {"b": 2}, "y": {"a": 1}})
    check("TOOL-4", "key order does not change the digest", e == f)


def test_preview_sha256_deterministic_and_sensitive() -> None:
    data = b'{"prompt": "p", "completion": "c"}\n'
    a = rev.run_mapping_preview(
        reader_for(data),
        PROMPT_COMPLETION,
        verified_content_type="application/jsonl",
        dataset_sha256=sha(data),
        tool_schemas=None,
        reveal=True,
    )
    b = rev.run_mapping_preview(
        reader_for(data),
        PROMPT_COMPLETION,
        verified_content_type="application/jsonl",
        dataset_sha256=sha(data),
        tool_schemas=None,
        reveal=True,
    )
    check(
        "SHA-1",
        "two previews of the same input hash identically",
        a.preview_sha256 == b.preview_sha256,
    )
    check(
        "SHA-2",
        "reveal does not change the digest (it is not a hash of the masked text)",
        a.preview_sha256 == b.preview_sha256,
    )

    data2 = b'{"prompt": "p2", "completion": "c2"}\n'
    c = rev.run_mapping_preview(
        reader_for(data2),
        PROMPT_COMPLETION,
        verified_content_type="application/jsonl",
        dataset_sha256=sha(data2),
        tool_schemas=None,
        reveal=True,
    )
    check("SHA-3", "a different dataset hashes differently", a.preview_sha256 != c.preview_sha256)

    d = rev.run_mapping_preview(
        reader_for(data),
        PROMPT_COMPLETION,
        verified_content_type="application/jsonl",
        dataset_sha256=sha(data),
        tool_schemas={"t": {"type": "object"}},
        reveal=True,
    )
    check(
        "SHA-4", "different tool definitions hash differently", a.preview_sha256 != d.preview_sha256
    )


# --- ROLE ------------------------------------------------------------------------------------


def test_observed_role_values_from_message_shaped_rows() -> None:
    data = (
        b'{"role": "human", "content": "hi"}\n'
        b'{"role": "ai", "content": "yo"}\n'
        b'{"role": "human", "content": "again"}\n'
    )
    mapping = ic.mapping_from_object(
        {
            "shape": "prompt_completion",
            "prompt": {"key": "role"},
            "completion": {"key": "content"},
        }
    )
    # prompt_completion has no message_keys, so observed_role_values stays empty for it --
    # pin that, then pin the positive case against a MESSAGES_JSON-shaped row directly.
    result = rev.run_mapping_preview(
        reader_for(data),
        mapping,
        verified_content_type="application/jsonl",
        dataset_sha256=sha(data),
        tool_schemas=None,
        reveal=True,
    )
    check(
        "ROLE-1",
        "prompt_completion mappings never populate observed_role_values",
        result.observed_role_values == (),
    )

    messages_json_row = {
        "messages": [{"role": "human", "content": "hi"}, {"role": "weird", "content": "x"}]
    }
    check(
        "ROLE-2",
        "every raw role string across the row's own resolved messages array is observed, in "
        "order, mapped or not",
        rev._raw_message_roles(MESSAGES_JSON, messages_json_row, None) == ("human", "weird"),
    )
    check(
        "ROLE-3",
        "a non-string role is skipped, never crashes",
        rev._raw_message_roles(MESSAGES_JSON, {"messages": [{"role": 1, "content": "x"}]}, None)
        == (),
    )
    check(
        "ROLE-4",
        "a tabular row (no message_keys apply) is never observed",
        rev._raw_message_roles(PROMPT_COMPLETION, ("a", "b"), None) == (),
    )
    pointer_mapping = ic.mapping_from_object(
        {
            "shape": "messages",
            "messages_pointer": "/conversation",
            "role_key": "role",
            "content_key": "content",
            "role_map": {"u": "user"},
            "on_unmapped_message_keys": "ignore",
        }
    )
    check(
        "ROLE-5",
        "a pointer-based 'messages' shape resolves its own real pointer target too",
        rev._raw_message_roles(
            pointer_mapping, {"conversation": [{"role": "u", "content": "hi"}]}, None
        )
        == ("u",),
    )
    check(
        "ROLE-6",
        "an unresolvable row never raises -- it is a display aid, not a second enforcement path",
        rev._raw_message_roles(MESSAGES_JSON, {"messages": "not valid json"}, None) == ()
        and rev._raw_message_roles(MESSAGES_JSON, {"no_messages_key": True}, None) == (),
    )


def test_raw_message_roles_pointer_branch_guards() -> None:
    pointer_mapping = ic.mapping_from_object(
        {
            "shape": "messages",
            "messages_pointer": "/conversation",
            "role_key": "role",
            "content_key": "content",
            "role_map": {"u": "user"},
            "on_unmapped_message_keys": "ignore",
        }
    )
    check(
        "ROLE-9",
        "a pointer-based shape over a non-Mapping row is refused, not dict()-crashed",
        rev._raw_message_roles(pointer_mapping, ("a", "b"), None) == (),
    )
    check(
        "ROLE-10",
        "a pointer target missing entirely from the row is empty, not a crash",
        rev._raw_message_roles(pointer_mapping, {"other_key": []}, None) == (),
    )


def test_raw_message_roles_rejects_a_non_list_resolved_array() -> None:
    # A tuple (never produced by real JSON/JSONL parsing, but not impossible to construct in
    # memory) must be refused exactly like any other non-list value -- the function's own
    # `type(array) is not list` guard is the only thing standing between an iterable of dicts
    # and a silently-accepted role list.
    check(
        "ROLE-11",
        "a non-list iterable of message-shaped dicts is still refused, not iterated",
        rev._raw_message_roles(
            MESSAGES_JSON, {"messages": ({"role": "human", "content": "hi"},)}, None
        )
        == (),
    )


def test_raw_message_roles_message_keys_guard_is_its_own_safety_net() -> None:
    # `mapping_from_object` never produces `message_keys=None` together with a real
    # `messages_slot`/`messages_pointer` (import_common.py's own `mapping_from_object` sets
    # `keys` and the slot/pointer in the same `if spec.message_based:` branch, together or not
    # at all) -- but `ImportMapping` itself (like `Slot` above) has no construction-time
    # validation of its own enforcing that pairing, so nothing stops a mapping from carrying a
    # real `messages_slot` with `message_keys=None`, the same construction-bypass technique as
    # NMAPLET-2/READTOP-11/READTOP-12. Without the `if keys is None: return ()` guard, this
    # would reach `element[keys.role_key]` with `keys` still `None` and raise `AttributeError`
    # instead of returning `()`.
    bypassed = dataclasses.replace(MESSAGES_JSON, message_keys=None)
    check(
        "ROLE-12",
        "message_keys=None (construction-bypassed, a real messages_slot still attached) "
        "returns empty, never crashes trying to read a role key off a mapping with no "
        "message_keys",
        rev._raw_message_roles(bypassed, {"messages": [{"role": "human", "content": "hi"}]}, None)
        == (),
    )


def test_observed_role_values_end_to_end_includes_rejected_rows() -> None:
    # One row with a role already in role_map (accepted), one with a raw role that is not
    # (rejected, unmapped_role) -- both raw role strings must still show up, since the whole
    # point of this field is to help the user find every raw role that needs a role_map entry,
    # including the ones that got a row rejected.
    data = (
        b'{"messages": [{"role": "human", "content": "hi"}]}\n'
        b'{"messages": [{"role": "robot", "content": "beep"}]}\n'
    )
    result = rev.run_mapping_preview(
        reader_for(data),
        MESSAGES_JSON,
        verified_content_type="application/jsonl",
        dataset_sha256=sha(data),
        tool_schemas=None,
        reveal=True,
    )
    check(
        "ROLE-7",
        f"both the mapped and the unmapped raw role strings are observed "
        f"({result.observed_role_values})",
        set(result.observed_role_values) == {"human", "robot"},
    )
    check(
        "ROLE-8",
        "the second row was in fact rejected for the unmapped role, confirming it is real",
        result.rows[1].outcome == "rejected" and result.rows[1].code == "unmapped_role",
    )


# --- Additional boundary/internal tests (round 1 mutation-gate closure) --------------------


def test_preview_row_post_init_rejects_bad_combinations() -> None:
    def build(**overrides: object) -> rev.MappingPreviewRow:
        base = {
            "locator": "record:1",
            "outcome": "accepted",
            "code": None,
            "slot": None,
            "raw_row": {},
            "not_mapped": (),
            "candidate": {"x": 1},
            "comparison": (),
        }
        base.update(overrides)
        return rev.MappingPreviewRow(**base)  # type: ignore[arg-type]

    check("ROW-1", "a valid accepted row builds", build().outcome == "accepted")
    raised = None
    try:
        build(outcome="maybe")
    except ValueError as exc:
        raised = exc
    check("ROW-2", "an unknown outcome is refused", raised is not None)
    raised = None
    try:
        # Isolates the *first* post_init check: candidate=None and code=None both agree with
        # outcome="maybe" under the other two checks ((False) != (False) is False for each), so
        # only the outcome-membership check itself can catch this one.
        build(outcome="maybe", candidate=None, code=None)
    except ValueError as exc:
        raised = exc
    check(
        "ROW-2b",
        "an unknown outcome is refused even when it would not trip the other two checks",
        raised is not None,
    )

    raised = None
    try:
        # " accepted " is close enough to "accepted" that a careless strip()/lower() on the
        # outcome-membership check alone would silently accept it -- candidate=None, code=None
        # again isolates this from the other two checks the same way ROW-2b does.
        build(outcome=" accepted ", candidate=None, code=None)
    except ValueError as exc:
        raised = exc
    check(
        "ROW-2c",
        "outcome membership is checked verbatim -- whitespace is not stripped before the check",
        raised is not None,
    )

    raised = None
    try:
        build(outcome="accepted", candidate=None)
    except ValueError as exc:
        raised = exc
    check("ROW-3", "accepted with no candidate is refused", raised is not None)

    raised = None
    try:
        build(outcome="rejected", candidate=None, code=None)
    except ValueError as exc:
        raised = exc
    check("ROW-4", "rejected with no code is refused", raised is not None)

    for label, kwargs in (
        ("accepted that also carries a code", {"outcome": "accepted", "code": "c"}),
        ("rejected that also carries a candidate", {"outcome": "rejected", "code": "c"}),
        ("padded accepted with its candidate", {"outcome": " accepted ", "code": None}),
        ("upper-case accepted with its candidate", {"outcome": "ACCEPTED", "code": None}),
        (
            "padded rejected with its code",
            {"outcome": " rejected ", "candidate": None, "code": "c"},
        ),
        (
            "upper-case rejected with its code",
            {"outcome": "Rejected", "candidate": None, "code": "c"},
        ),
    ):
        raised = None
        try:
            build(**kwargs)
        except ValueError as exc:
            raised = exc
        check("ROW-2d", f"{label} is refused verbatim", raised is not None)

    rejected = build(outcome="rejected", candidate=None, code="required_field_missing")
    check("ROW-5", "a valid rejected row builds", rejected.code == "required_field_missing")


def test_mask_marker_carries_the_real_char_count() -> None:
    check(
        "SIZE-1", "an empty string's own marker carries 0 chars", rev._mask_marker("")["chars"] == 0
    )
    check(
        "SIZE-2",
        "a short string's own marker carries its real length",
        rev._mask_marker("hi")["chars"] == 2,
    )
    check(
        "SIZE-3",
        "a long string's own marker carries its real length, not a bucket",
        rev._mask_marker("x" * 500)["chars"] == 500,
    )
    check(
        "SIZE-4",
        "the marker never carries the real text itself",
        "hi" not in str(rev._mask_marker("hi")),
    )


def test_mask_value_distinguishes_str_from_numeric() -> None:
    masked_str = rev._mask_leaf("hi")
    masked_int = rev._mask_leaf(7)
    masked_float = rev._mask_leaf(1.5)
    check(
        "MASKV-1", "a str is masked with the {masked, chars} marker", _is_masked_marker(masked_str)
    )
    check("MASKV-2", "an int is never masked (not a string leaf)", masked_int == 7)
    check("MASKV-3", "a float is never masked (not a string leaf)", masked_float == 1.5)


def test_mask_row_none_passthrough() -> None:
    check(
        "MASKROW-1", "_mask_named_row(None, ...) is None", rev._mask_named_row(None, None) is None
    )
    mismatched = rev._mask_named_row(("a", "b", "c"), ("only-one-header",))
    check(
        "MASKROW-2",
        "a tabular row whose length does not match headers falls back to a plain masked "
        "tuple of masked values, positional, no header pairing attempted",
        isinstance(mismatched, tuple) and all(_is_masked_marker(v) for v in mismatched),
    )


def test_mapped_names_letters_slot_never_names_itself_as_a_header() -> None:
    # A `letters`-kind slot addresses a spreadsheet column by position ("A", "AB", ...), never
    # by a header's own text -- its own `slot.value` must never be added to `_mapped_names`'s
    # result just because it happens to be a string.
    mapping = ic.mapping_from_object(
        {
            "shape": "prompt_completion",
            "csv": {"encoding": "utf-8", "delimiter": ",", "has_header": True, "header_row": 0},
            "prompt": {"letters": "A"},
            "completion": {"index": 1},
        }
    )
    check(
        "NMAPLET-1",
        "a letters-kind slot's own column letter is never treated as a real header name",
        "A" not in rev._mapped_names(mapping, ("A", "B")),
    )
    bypassed = dataclasses.replace(mapping, prompt=ic.Slot(" key ", "A"))
    check(
        "NMAPLET-2",
        "a Slot whose own kind is not exactly 'key' (construction-bypassed) is never added, "
        "even when its value is a plain string that would otherwise qualify",
        "A" not in rev._mapped_names(bypassed, ("A", "B")),
    )


def test_not_mapped_never_crashes_on_a_tabular_row_with_no_headers() -> None:
    check(
        "NMAPNONE-1",
        "a tabular row with headers=None returns no not_mapped columns, never crashes",
        rev._not_mapped(PROMPT_COMPLETION, ("a", "b"), None) == (),
    )


def test_mapped_names_index_boundaries() -> None:
    mapping = ic.mapping_from_object(
        {
            "shape": "prompt_completion",
            "csv": {"encoding": "utf-8", "delimiter": ",", "has_header": True, "header_row": 0},
            "prompt": {"index": 0},
            "completion": {"index": 1},
        }
    )
    headers = ("a", "b")
    check(
        "NMAPIDX-1",
        "an in-range index slot names its real header",
        rev._mapped_names(mapping, headers) == frozenset({"a", "b"}),
    )
    check(
        "NMAPIDX-2",
        "no headers at all means no index-based slot can be named",
        rev._mapped_names(mapping, None) == frozenset(),
    )
    out_of_range = ic.mapping_from_object(
        {
            "shape": "prompt_completion",
            "csv": {"encoding": "utf-8", "delimiter": ",", "has_header": True, "header_row": 0},
            "prompt": {"index": 5},
            "completion": {"index": 1},
        }
    )
    check(
        "NMAPIDX-3",
        "an out-of-range index slot names nothing for it",
        rev._mapped_names(out_of_range, headers) == frozenset({"b"}),
    )
    at_length = ic.mapping_from_object(
        {
            "shape": "prompt_completion",
            "csv": {"encoding": "utf-8", "delimiter": ",", "has_header": True, "header_row": 0},
            "prompt": {"index": 2},
            "completion": {"index": 1},
        }
    )
    check(
        "NMAPIDX-4",
        "an index exactly at the header count (one past the last valid position) is out of range",
        rev._mapped_names(at_length, headers) == frozenset({"b"}),
    )
    # Same construction-bypass technique as NMAPLET-2: `Slot` has no construction-time
    # validation of its own `kind`, so a real in-range index slot whose `kind` carries
    # whitespace/case must still be refused by the `slot.kind == "index"` check -- proving
    # that comparison is exact, not case/whitespace-normalized, the same way NMAPLET-2 already
    # proved it for the `("key", "header")` membership check above.
    bypassed_index = dataclasses.replace(mapping, prompt=ic.Slot(" index ", 0))
    check(
        "NMAPIDX-5",
        "a Slot whose own kind is not exactly 'index' (construction-bypassed) is never added, "
        "even when its value is a real in-range position",
        "a" not in rev._mapped_names(bypassed_index, headers),
    )


def test_mapped_names_messages_pointer_edge_cases() -> None:
    single_token = ic.mapping_from_object(
        {
            "shape": "messages",
            "messages_pointer": "/conversation",
            "role_key": "role",
            "content_key": "content",
            "role_map": {"u": "user"},
            "on_unmapped_message_keys": "ignore",
        }
    )
    check(
        "NMAPPTR-1",
        "a single-token pointer with no headers names that one top-level key",
        rev._mapped_names(single_token, None) == frozenset({"conversation"}),
    )
    check(
        "NMAPPTR-2",
        "the same pointer against a tabular row (headers present) names nothing -- a pointer "
        "only ever addresses a JSON object's own top-level key, never a CSV column",
        rev._mapped_names(single_token, ("a", "b")) == frozenset(),
    )
    root_pointer = ic.mapping_from_object(
        {
            "shape": "messages",
            "messages_pointer": "",
            "role_key": "role",
            "content_key": "content",
            "role_map": {"u": "user"},
            "on_unmapped_message_keys": "ignore",
        }
    )
    check(
        "NMAPPTR-3",
        "a zero-token (root) pointer names nothing -- there is no single top-level key to name",
        rev._mapped_names(root_pointer, None) == frozenset(),
    )


def test_read_top_level_header_edge_cases() -> None:
    slot_a = ic.Slot("header", "a")
    check(
        "READTOP-1",
        "a header slot with no duplicate matches exactly one position",
        rev._read_top_level(slot_a, ("1", "2"), ("a", "b")) == "1",
    )
    check(
        "READTOP-2",
        "a duplicated header name is ambiguous and reads as missing",
        rev._read_top_level(slot_a, ("1", "2"), ("a", "a")) is rev._MISSING,
    )
    index_slot = ic.Slot("index", 1)
    check(
        "READTOP-3",
        "an index exactly at the row's own length is out of range",
        rev._read_top_level(index_slot, ("x",), None) is rev._MISSING,
    )
    check(
        "READTOP-4",
        "an in-range index reads the cell",
        rev._read_top_level(ic.Slot("index", 0), ("x", "y"), None) == "x",
    )
    check(
        "READTOP-5",
        "a non-key/header slot against a Mapping row is missing, even when the dict happens "
        "to have a key equal to str(slot.value) (the guard must fire, not fall through to "
        "a coincidental .get() hit)",
        rev._read_top_level(index_slot, {"1": "wrongly-found"}, None) is rev._MISSING,
    )
    check(
        "READTOP-9",
        "a bare str/bytes row is never indexed as if it were a tabular row",
        rev._read_top_level(ic.Slot("index", 0), "hello", None) is rev._MISSING,
    )
    check(
        "READTOP-10",
        "a letters-kind slot against real headers that happen to equal its own letters "
        "never resolves (this module cannot resolve spreadsheet column letters at all)",
        rev._read_top_level(ic.Slot("letters", "A"), ("x", "y"), ("A", "B")) is rev._MISSING,
    )
    check(
        "READTOP-6",
        "header matching is exact, never case- or whitespace-folded (a real CSV header is "
        "verbatim)",
        rev._read_top_level(ic.Slot("header", "Q"), ("x",), (" q ",)) is rev._MISSING,
    )
    check(
        "READTOP-7",
        "a clean slot name against a real header carrying whitespace/case does not match",
        rev._read_top_level(ic.Slot("header", "q"), ("x",), (" Q ",)) is rev._MISSING,
    )
    check(
        "READTOP-8",
        "a slot name carrying whitespace/case against a clean real header does not match",
        rev._read_top_level(ic.Slot("header", " Q "), ("x",), ("q",)) is rev._MISSING,
    )
    # `Slot` itself has no construction-time validation (that is `import_common._slot()`'s own
    # job, on the parse path); constructing one directly, bypassing that parser, is this
    # project's own established technique for proving a kind-matching check is exact and not
    # silently normalized -- see FTF-0503's own "slot_kind_match_is_exact_no_normalization".
    bypassed_kind_mapping = ic.mapping_from_object(
        {"shape": "prompt_completion", "prompt": {"key": "x"}, "completion": {"key": "y"}}
    )
    bypassed = dataclasses.replace(bypassed_kind_mapping, prompt=ic.Slot(" key ", "x"))
    check(
        "READTOP-11",
        "a Slot whose own kind is not exactly 'key' (construction-bypassed) is refused by the "
        "Mapping-row guard, never normalized into a match",
        rev._read_top_level(bypassed.prompt, {"x": "wrongly-found"}, None) is rev._MISSING,
    )
    bypassed_header = dataclasses.replace(bypassed_kind_mapping, prompt=ic.Slot(" header ", "h"))
    check(
        "READTOP-12",
        "a Slot whose own kind is not exactly 'header' (construction-bypassed) never enters "
        "the header-matching branch, even when a header would otherwise match",
        rev._read_top_level(bypassed_header.prompt, ("v",), ("h",)) is rev._MISSING,
    )
    bypassed_index = dataclasses.replace(bypassed_kind_mapping, prompt=ic.Slot(" index ", 0))
    check(
        "READTOP-13",
        "a Slot whose own kind is not exactly 'index' (construction-bypassed) never enters "
        "the index branch, even when position 0 would otherwise be in range",
        rev._read_top_level(bypassed_index.prompt, ("v0", "v1"), None) is rev._MISSING,
    )


def test_not_mapped_header_names_are_exact() -> None:
    mapping = ic.mapping_from_object(
        {
            "shape": "prompt_completion",
            "csv": {"encoding": "utf-8", "delimiter": ",", "has_header": True, "header_row": 0},
            "prompt": {"header": "q"},
            "completion": {"header": "a"},
        }
    )
    check(
        "NMAPEXACT-1",
        "an exact header match is mapped (not left over)",
        rev._not_mapped(mapping, ("x", "y", "z"), ("q", "a", "extra")) == ("extra",),
    )
    check(
        "NMAPEXACT-2",
        "a differently-cased/whitespaced header is its own distinct column, not folded into "
        "the mapped one",
        rev._not_mapped(mapping, ("x", "y", "z"), (" Q ", "a", "extra")) == (" Q ", "extra"),
    )


def test_comparison_cells_group_key_present_vs_missing() -> None:
    mapping = ic.mapping_from_object(
        {
            "shape": "prompt_completion",
            "prompt": {"key": "prompt"},
            "completion": {"key": "completion"},
            "group_key": {"key": "gid"},
        }
    )
    with_gid = rev.run_mapping_preview(
        reader_for(b'{"prompt": "p", "completion": "c", "gid": "g1"}\n'),
        mapping,
        verified_content_type="application/jsonl",
        dataset_sha256=sha(b'{"prompt": "p", "completion": "c", "gid": "g1"}\n'),
        tool_schemas=None,
        reveal=True,
    )
    cell = next(c for c in with_gid.rows[0].comparison if c.slot == "group_key")
    check(
        "CMPGID-1",
        "a present group_key is a str cell, marked identical",
        cell.raw_type == "str" and cell.identical,
    )

    without_gid = rev.run_mapping_preview(
        reader_for(b'{"prompt": "p", "completion": "c"}\n'),
        mapping,
        verified_content_type="application/jsonl",
        dataset_sha256=sha(b'{"prompt": "p", "completion": "c"}\n'),
        tool_schemas=None,
        reveal=True,
    )
    cell2 = next(c for c in without_gid.rows[0].comparison if c.slot == "group_key")
    check(
        "CMPGID-2",
        "an absent group_key is missing, not falsely str/identical",
        cell2.raw_type == "missing" and not cell2.identical,
    )

    class Weird:  # not a real importer value -- forces _type_name's own fallback branch
        pass

    candidate = {
        "content": {
            "messages": [
                {"role": "user", "content": [{"type": "text", "text": "p"}]},
                {"role": "assistant", "content": [{"type": "text", "text": "c"}]},
            ],
            "metadata": {},
        }
    }
    cells = rev._comparison_cells(
        mapping, {"prompt": "p", "completion": "c", "gid": Weird()}, None, candidate
    )
    weird_cell = next(c for c in cells if c.slot == "group_key")
    check(
        "CMPGID-3",
        f"raw_type carries the real type name verbatim, never case-folded "
        f"({weird_cell.raw_type!r})",
        weird_cell.raw_type == "Weird",
    )

    class STR:  # name case-insensitively equals "str" -- distinguishes the `== "str"` compare
        pass

    cells2 = rev._comparison_cells(
        mapping, {"prompt": "p", "completion": "c", "gid": STR()}, None, candidate
    )
    str_cell = next(c for c in cells2 if c.slot == "group_key")
    check(
        "CMPGID-4",
        "a type literally named 'STR' is never folded into matching the real 'str' literal",
        str_cell.raw_type == "STR" and not str_cell.identical,
    )


def test_run_mapping_probe_populates_the_real_csv_headers() -> None:
    data = b"q,a\nhello,world\n"
    probe = rev.run_mapping_probe(
        reader_for(data),
        CSV_PROMPT_COMPLETION,
        verified_content_type="text/csv",
        dataset_sha256=sha(data),
        reveal=True,
    )
    check(
        "PROBEHDR-2",
        "a CSV source's probe reports its own real headers, not None",
        probe.headers == ("q", "a"),
    )


def test_comparison_cells_identical_requires_exact_equality() -> None:
    mapping = ic.mapping_from_object(
        {
            "shape": "prompt_completion",
            "prompt": {"key": "prompt"},
            "completion": {"key": "completion"},
        }
    )
    data = b'{"prompt": " Hello ", "completion": "c"}\n'
    result = rev.run_mapping_preview(
        reader_for(data),
        mapping,
        verified_content_type="application/jsonl",
        dataset_sha256=sha(data),
        tool_schemas=None,
        reveal=True,
    )
    cell = next(c for c in result.rows[0].comparison if c.slot == "prompt")
    check(
        "CMPEXACT-1",
        "raw/canonical equality is exact -- no implicit strip/casefold applied by the comparator",
        cell.identical,
    )
    # the raw value itself has leading/trailing space and the canonical content preserves it
    # unchanged (FTF-0506 never trims message text) -- a comparator that silently normalized
    # either side before comparing could not be told apart from a correct one using only
    # already-equal values, so this pins the *equal* case at a value a careless `.strip()`
    # would have changed.
    check(
        "CMPEXACT-2",
        "the raw value's own whitespace survived into the canonical content unmodified",
        result.rows[0].candidate["content"]["messages"][0]["content"][0]["text"] == " Hello ",
    )


def test_comparison_cells_message_content_role_is_fixed_vocabulary() -> None:
    # "system"/"user"/"assistant" are hardcoded literals this module itself passes to
    # `message_content`, never derived from raw data -- confirms there is no row-controlled
    # value on this path a case/whitespace fault could ever reach.
    mapping = ic.mapping_from_object(
        {
            "shape": "prompt_completion",
            "prompt": {"key": "prompt"},
            "completion": {"key": "completion"},
        }
    )
    data = b'{"prompt": "p", "completion": "c"}\n'
    result = rev.run_mapping_preview(
        reader_for(data),
        mapping,
        verified_content_type="application/jsonl",
        dataset_sha256=sha(data),
        tool_schemas=None,
        reveal=True,
    )
    check(
        "CMPROLE-1",
        "prompt/completion/system cells are produced for exactly the three fixed roles",
        {c.slot for c in result.rows[0].comparison} == {"prompt", "completion"},
    )


def test_comparison_cells_messages_list_type_and_dict_type_both_required() -> None:
    # A row whose "messages"/"metadata" shape is somehow malformed never reaches
    # _comparison_cells in practice (to_dataset_record_candidate always builds both as a list
    # and a dict) -- this pins that both parts of the guard are real assertions, not vacuous,
    # by calling the function directly with a deliberately broken candidate.
    mapping = ic.mapping_from_object(
        {
            "shape": "prompt_completion",
            "prompt": {"key": "prompt"},
            "completion": {"key": "completion"},
        }
    )
    raised = None
    try:
        rev._comparison_cells(
            mapping, {"prompt": "p"}, None, {"content": {"messages": "not-a-list", "metadata": {}}}
        )
    except AssertionError:
        raised = "messages"
    check("CMPGUARD-1", "a non-list messages value trips the guard", raised == "messages")
    raised = None
    try:
        rev._comparison_cells(
            mapping, {"prompt": "p"}, None, {"content": {"messages": [], "metadata": "not-a-dict"}}
        )
    except AssertionError:
        raised = "metadata"
    check("CMPGUARD-2", "a non-dict metadata value trips the guard", raised == "metadata")


def test_comparison_cells_message_content_requires_exactly_one_text_part() -> None:
    mapping = ic.mapping_from_object(
        {
            "shape": "prompt_completion",
            "prompt": {"key": "prompt"},
            "completion": {"key": "completion"},
        }
    )
    multi_part_candidate = {
        "content": {
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "a"},
                        {"type": "text", "text": "b"},
                    ],
                },
                {"role": "assistant", "content": [{"type": "text", "text": "c"}]},
            ],
            "metadata": {},
        }
    }
    cells = rev._comparison_cells(
        mapping, {"prompt": "p", "completion": "c"}, None, multi_part_candidate
    )
    prompt_cell = next(c for c in cells if c.slot == "prompt")
    check(
        "CMPMULTI-1",
        "a message with more than one content part is never collapsed into a single string",
        prompt_cell.canonical_type != "str",
    )
    empty_part_candidate = {
        "content": {
            "messages": [
                {"role": "user", "content": []},
                {"role": "assistant", "content": [{"type": "text", "text": "c"}]},
            ],
            "metadata": {},
        }
    }
    cells2 = rev._comparison_cells(
        mapping, {"prompt": "p", "completion": "c"}, None, empty_part_candidate
    )
    prompt_cell2 = next(c for c in cells2 if c.slot == "prompt")
    check(
        "CMPMULTI-2",
        "an empty content-part list never crashes and is never treated as a single string",
        prompt_cell2.canonical_type != "str",
    )


def test_comparison_cells_messages_slot_shape() -> None:
    mapping = ic.mapping_from_object(
        {
            "shape": "messages_json",
            "messages": {"key": "msgs"},
            "role_key": "role",
            "content_key": "content",
            "role_map": {"u": "user", "a": "assistant"},
            "on_unmapped_message_keys": "ignore",
        }
    )
    data = b'{"msgs": [{"role": "u", "content": "hi"}, {"role": "a", "content": "yo"}]}\n'
    result = rev.run_mapping_preview(
        reader_for(data),
        mapping,
        verified_content_type="application/jsonl",
        dataset_sha256=sha(data),
        tool_schemas=None,
        reveal=True,
    )
    check(
        "CMPMSG-1",
        "exactly one 'messages' comparison cell is produced",
        len(result.rows[0].comparison) == 1,
    )
    cell = result.rows[0].comparison[0]
    check(
        "CMPMSG-2",
        "the whole-array cell is identical when the lengths agree",
        cell.slot == "messages"
        and cell.raw_type == "list"
        and cell.canonical_type == "list"
        and cell.identical,
    )

    short = b'{"msgs": [{"role": "u", "content": "hi"}]}\n'
    # one message alone has no assistant turn; apply_mapping still maps it (no odd/even check at
    # this layer), so this stays a fair same-length comparison -- pin a *genuine* length mismatch
    # by comparing against a row carrying extra non-message content instead:
    result_single = rev.run_mapping_preview(
        reader_for(short),
        mapping,
        verified_content_type="application/jsonl",
        dataset_sha256=sha(short),
        tool_schemas=None,
        reveal=True,
    )
    check(
        "CMPMSG-3",
        "a single-message array compares 1-for-1",
        result_single.rows[0].comparison[0].identical,
    )


def test_comparison_cells_messages_slot_string_cell_is_never_falsely_identical() -> None:
    # `_read_top_level` echoes the raw slot value verbatim (it never parses JSON text); when the
    # messages_json slot holds a JSON-encoded *string* (apply_mapping parses it into a real list
    # for the canonical candidate), the raw value here is still that string, not a list -- the
    # comparison must say so, never collapse "present" and "actually a matching list" into one.
    mapping = ic.mapping_from_object(
        {
            "shape": "messages_json",
            "messages": {"key": "msgs"},
            "role_key": "role",
            "content_key": "content",
            "role_map": {"human": "user"},
            "on_unmapped_message_keys": "ignore",
        }
    )
    data = b'{"msgs": "[{\\"role\\": \\"human\\", \\"content\\": \\"hi\\"}]"}\n'
    result = rev.run_mapping_preview(
        reader_for(data),
        mapping,
        verified_content_type="application/jsonl",
        dataset_sha256=sha(data),
        tool_schemas=None,
        reveal=True,
    )
    check("CMPMSG-4", "the row was in fact accepted", result.rows[0].outcome == "accepted")
    cell = result.rows[0].comparison[0]
    check(
        "CMPMSG-5",
        f"a string-encoded messages cell is present but never falsely identical to the parsed "
        f"list ({cell})",
        cell.raw_type == "str" and not cell.identical,
    )


def test_comparison_cells_missing_messages_slot() -> None:
    mapping = ic.mapping_from_object(
        {
            "shape": "messages_json",
            "messages": {"key": "msgs"},
            "role_key": "role",
            "content_key": "content",
            "role_map": {"u": "user"},
            "on_unmapped_message_keys": "ignore",
        }
    )
    data = b'{"msgs": "not a list"}\n'
    result = rev.run_mapping_preview(
        reader_for(data),
        mapping,
        verified_content_type="application/jsonl",
        dataset_sha256=sha(data),
        tool_schemas=None,
        reveal=True,
    )
    check(
        "CMPMSG-4",
        "a malformed messages slot rejects the row",
        result.rows[0].outcome == "rejected",
    )


def test_run_mapping_probe_headers_none_for_dict_rows() -> None:
    probe = rev.run_mapping_probe(
        reader_for(b'{"prompt": "p", "completion": "c"}\n'),
        PROMPT_COMPLETION,
        verified_content_type="application/jsonl",
        dataset_sha256=sha(b'{"prompt": "p", "completion": "c"}\n'),
        reveal=True,
    )
    check("PROBEHDR-1", "a dict-row source has no headers", probe.headers is None)


def test_present_row_headers_length_mismatch_falls_back_to_tuple() -> None:
    result = rev._present_row(("a", "b"), ("only-one",), reveal=True)
    check(
        "PRESENT-1",
        "a mismatched header/row length falls back to a plain tuple",
        result == ("a", "b"),
    )
    masked_result = rev._present_row(("a", "b"), ("only-one",), reveal=False)
    check(
        "PRESENT-2",
        "the masked fallback is also a plain tuple of masked values",
        isinstance(masked_result, tuple) and all(_is_masked_marker(v) for v in masked_result),
    )
    check(
        "PRESENT-3",
        "matching headers/row length, revealed, pairs by name into a real dict",
        rev._present_row(("a", "b"), ("q", "r"), reveal=True) == {"q": "a", "r": "b"},
    )
    check(
        "PRESENT-4",
        "no headers at all, revealed, is a plain tuple -- never a crash",
        rev._present_row(("a", "b"), None, reveal=True) == ("a", "b"),
    )


def test_source_schema_sha256_is_the_real_csv_header_digest() -> None:
    data = b"q,a\nhello,world\n"
    result = rev.run_mapping_preview(
        reader_for(data),
        CSV_PROMPT_COMPLETION,
        verified_content_type="text/csv",
        dataset_sha256=sha(data),
        tool_schemas=None,
        reveal=True,
    )
    import hashlib as _hashlib
    import json as _json

    expected = _hashlib.sha256(
        _json.dumps(["q", "a"], ensure_ascii=True, separators=(",", ":")).encode("ascii")
    ).hexdigest()
    check(
        "SCHEMA-1",
        "source_schema_sha256 is the real sha256 of the exact header list",
        result.source_schema_sha256 == expected,
    )


def test_run_mapping_preview_row_headers_none_branch() -> None:
    # A JSONL source never sets stream.source_schema_sha256; pin that explicitly so a mutant
    # that always treats it as present cannot survive unnoticed.
    data = b'{"prompt": "p", "completion": "c"}\n'
    result = rev.run_mapping_preview(
        reader_for(data),
        PROMPT_COMPLETION,
        verified_content_type="application/jsonl",
        dataset_sha256=sha(data),
        tool_schemas=None,
        reveal=True,
    )
    check(
        "SCHEMA-2",
        "a JSONL source has no header schema digest",
        result.source_schema_sha256 is None,
    )


# --- APPLYMAP: full-coverage pins for apply_mapping's own pre-existing body --------------------
# (this task's own change sits at the very top of `apply_mapping`, which pulls the whole function
# into mutation scope -- its sibling importer suites already prove this body for real; these pins
# make this task's own test file independently prove it too, rather than relying on a mutation
# run that only ever includes files this task's own diff touches.)


def test_applymap_row_observer_copy_leaves_a_string_row_unwrapped() -> None:
    seen: list[object] = []
    tapped = dataclasses.replace(PROMPT_COMPLETION, row_observer=lambda r, h: seen.append(r))
    try:
        ic.apply_mapping(tapped, "plain-string-row")
    except ic.RowMappingError:
        pass
    check(
        "APPLYMAP-1",
        "a str row is never list()-copied by the observer tap (it is excluded from the "
        "Sequence-but-not-str/bytes copy branch)",
        seen == ["plain-string-row"],
    )


def test_applymap_messages_pointer_shape_non_mapping_row_is_type_mismatch() -> None:
    mapping = ic.mapping_from_object(
        {
            "shape": "messages",
            "messages_pointer": "",
            "role_key": "role",
            "content_key": "content",
            "role_map": {"u": "user"},
            "on_unmapped_message_keys": "ignore",
        }
    )
    raised = None
    try:
        ic.apply_mapping(mapping, ("not", "a", "mapping"))
    except ic.RowMappingError as exc:
        raised = exc
    check(
        "APPLYMAP-2",
        "a pointer-based 'messages' shape over a non-Mapping row is mapping_type_mismatch",
        raised is not None and raised.code == "mapping_type_mismatch",
    )


def test_applymap_messages_pointer_or_fallback_uses_the_real_pointer() -> None:
    mapping = ic.mapping_from_object(
        {
            "shape": "messages",
            "messages_pointer": "/conversation",
            "role_key": "role",
            "content_key": "content",
            "role_map": {"u": "user"},
            "on_unmapped_message_keys": "ignore",
        }
    )
    row = {
        "conversation": [{"role": "u", "content": "hi"}],
        "role": "wrong-top-level-key-must-not-be-used",
    }
    mapped = ic.apply_mapping(mapping, row)
    check(
        "APPLYMAP-3",
        "a real, non-root pointer is actually followed (not silently replaced by the root)",
        mapped.raw_record["messages"] == [{"role": "user", "content": "hi"}],
    )
    raised = None
    try:
        ic.apply_mapping(mapping, {"role": "u", "content": "hi"})  # no "conversation" key
    except ic.RowMappingError as exc:
        raised = exc
    check(
        "APPLYMAP-4",
        "the pointer target missing entirely is required_field_missing",
        raised is not None
        and raised.code == "required_field_missing"
        and raised.slot == "messages",
    )


def test_applymap_messages_json_slot_missing_or_none_is_required_field_missing() -> None:
    raised = None
    try:
        ic.apply_mapping(MESSAGES_JSON, {"not_msgs": []})
    except ic.RowMappingError as exc:
        raised = exc
    check(
        "APPLYMAP-5",
        "a messages_json slot entirely absent from the row is required_field_missing",
        raised is not None
        and raised.code == "required_field_missing"
        and raised.slot == "messages",
    )
    raised = None
    try:
        ic.apply_mapping(MESSAGES_JSON, {"messages": None})
    except ic.RowMappingError as exc:
        raised = exc
    check(
        "APPLYMAP-6",
        "a messages_json slot explicitly None is also required_field_missing",
        raised is not None and raised.code == "required_field_missing",
    )


def test_applymap_messages_json_string_cell_is_parsed_as_json() -> None:
    mapped = ic.apply_mapping(MESSAGES_JSON, {"messages": '[{"role": "human", "content": "hi"}]'})
    check(
        "APPLYMAP-7",
        "a messages_json slot holding a JSON-text string is parsed, not rejected",
        mapped.raw_record["messages"] == [{"role": "user", "content": "hi"}],
    )
    raised = None
    try:
        ic.apply_mapping(MESSAGES_JSON, {"messages": "not valid json"})
    except ic.RowMappingError as exc:
        raised = exc
    check(
        "APPLYMAP-8",
        "malformed JSON text in a messages_json slot surfaces strict_loads's own real code",
        raised is not None and raised.code == "invalid_json" and raised.slot == "messages",
    )


def test_applymap_messages_empty_string_cell_tabular_vs_mapping() -> None:
    tabular_mapping = ic.mapping_from_object(
        {
            "shape": "messages_json",
            "messages": {"index": 0},
            "role_key": "role",
            "content_key": "content",
            "role_map": {"human": "user"},
            "on_unmapped_message_keys": "ignore",
        }
    )
    raised = None
    try:
        ic.apply_mapping(tabular_mapping, ("",))
    except ic.RowMappingError as exc:
        raised = exc
    check(
        "APPLYMAP-14",
        "an empty-string messages cell from a tabular (non-Mapping) row is required_field_empty",
        raised is not None and raised.code == "required_field_empty",
    )

    raised2 = None
    try:
        ic.apply_mapping(MESSAGES_JSON, {"messages": ""})
    except ic.RowMappingError as exc:
        raised2 = exc
    check(
        "APPLYMAP-15",
        "the same empty string from a Mapping row is never required_field_empty -- it reaches "
        "strict_loads instead, which refuses it as invalid_json",
        raised2 is not None and raised2.code == "invalid_json",
    )


def test_applymap_messages_array_must_be_a_non_empty_list() -> None:
    for bad_array, label in (({}, "an object"), ([], "an empty list")):
        raised = None
        try:
            ic.apply_mapping(MESSAGES_JSON, {"messages": bad_array})
        except ic.RowMappingError as exc:
            raised = exc
        check(
            f"APPLYMAP-9-{label}",
            f"{label} as the resolved messages value is mapping_type_mismatch",
            raised is not None and raised.code == "mapping_type_mismatch",
        )


def test_applymap_metadata_event_timestamp_check_actually_runs() -> None:
    mapping = ic.mapping_from_object(
        {
            "shape": "prompt_completion",
            "prompt": {"key": "prompt"},
            "completion": {"key": "completion"},
            "metadata": {"event_timestamp": {"key": "ts"}},
        }
    )
    mapped = ic.apply_mapping(
        mapping, {"prompt": "p", "completion": "c", "ts": "2026-01-01T00:00:00+00:00"}
    )
    check(
        "APPLYMAP-10",
        "a valid event_timestamp passes the check and is kept verbatim",
        mapped.raw_record["metadata"]["event_timestamp"] == "2026-01-01T00:00:00+00:00",
    )
    raised = None
    try:
        ic.apply_mapping(mapping, {"prompt": "p", "completion": "c", "ts": "not-a-timestamp"})
    except ic.RowMappingError as exc:
        raised = exc
    check(
        "APPLYMAP-11",
        "the event_timestamp check genuinely runs and refuses a bad value, proving it is "
        "not a no-op",
        raised is not None and raised.code == "invalid_event_timestamp",
    )


def test_applymap_group_key_present_and_absent() -> None:
    with_key = ic.mapping_from_object(
        {
            "shape": "prompt_completion",
            "prompt": {"key": "prompt"},
            "completion": {"key": "completion"},
            "group_key": {"key": "gid"},
        }
    )
    mapped = ic.apply_mapping(with_key, {"prompt": "p", "completion": "c", "gid": "g-1"})
    check("APPLYMAP-12", "a present group_key slot is extracted", mapped.group_key == "g-1")

    without_key = ic.mapping_from_object(
        {
            "shape": "prompt_completion",
            "prompt": {"key": "prompt"},
            "completion": {"key": "completion"},
        }
    )
    mapped2 = ic.apply_mapping(without_key, {"prompt": "p", "completion": "c"})
    check(
        "APPLYMAP-13", "no group_key slot at all leaves group_key None", mapped2.group_key is None
    )


# --- MASK-LEAK-NESTED / canaries (round 3) -------------------------------------------------

CANARIES = ("CANARY-NESTED-KEY", "CANARY-ARG-KEY", "CANARY-TYPE-VALUE", "CANARY-JSON-KEY")

TOOL_MAPPING_R3 = ic.mapping_from_object(
    {
        "shape": "messages_json",
        "messages": {"key": "messages"},
        "role_key": "role",
        "content_key": "content",
        "tool_calls_key": "tool_calls",
        "tool_call_id_key": "tool_call_id",
        "name_key": "name",
        "role_map": {"user": "user", "assistant": "assistant", "tool": "tool"},
        "on_unmapped_message_keys": "ignore",
    }
)
TOOL_SCHEMA_R3 = {
    "type": "object",
    "properties": {
        "CANARY-ARG-KEY": {"type": "string"},
        "type": {"type": "string"},
    },
    "additionalProperties": False,
}
CANARY_ROW = (
    b'{"messages": [{"role": "user", "content": "hi", '
    b'"CANARY-NESTED-KEY": {"CANARY-JSON-KEY": 1}}, '
    b'{"role": "assistant", "content": "ok", "tool_calls": [{"id": "call_1", "name": "lookup", '
    b'"arguments": {"CANARY-ARG-KEY": "v", "type": "CANARY-TYPE-VALUE"}}]}, '
    b'{"role": "tool", "content": [{"type": "json", "value": {"CANARY-JSON-KEY": "x"}}], '
    b'"tool_call_id": "call_1", "name": "lookup"}]}\n'
)


def _whole(value: object) -> str:
    return repr(value) + json.dumps(value, default=repr, sort_keys=True)


def _leaks(value: object) -> list[str]:
    text = _whole(value)
    return [c for c in CANARIES if c in text]


def _canary_preview(reveal: bool) -> rev.MappingPreviewResult:
    return rev.run_mapping_preview(
        reader_for(CANARY_ROW),
        TOOL_MAPPING_R3,
        verified_content_type="application/jsonl",
        dataset_sha256=sha(CANARY_ROW),
        tool_schemas={"lookup": TOOL_SCHEMA_R3},
        tool_argument_validator=validate_instance,
        reveal=reveal,
    )


def test_mask_nested_keys_whole_result_canary_preview_and_probe() -> None:
    preview = _canary_preview(False)
    check(
        "MASKNEST-0",
        "the canary row is accepted, so its tool arguments reach the masked candidate",
        preview.rows[0].outcome == "accepted",
    )
    check(
        "MASKNEST-1",
        "no nested raw key or tool-argument value survives anywhere in the masked preview "
        "(repr and asdict of the whole result)",
        _leaks(preview) == [] and _leaks(dataclasses.asdict(preview)) == [],
    )
    probe = rev.run_mapping_probe(
        reader_for(CANARY_ROW),
        TOOL_MAPPING_R3,
        verified_content_type="application/jsonl",
        dataset_sha256=sha(CANARY_ROW),
        reveal=False,
    )
    check(
        "MASKNEST-2",
        "no nested raw key survives anywhere in the masked probe (repr and asdict)",
        _leaks(probe) == [] and _leaks(dataclasses.asdict(probe)) == [],
    )
    revealed = _canary_preview(True)
    check(
        "MASKNEST-3",
        "positive control: with reveal=True the same scan finds every canary",
        set(_leaks(revealed)) == set(CANARIES),
    )


def test_mask_type_exemption_only_on_content_parts() -> None:
    preview = _canary_preview(False)
    candidate = preview.rows[0].candidate
    assert candidate is not None
    messages = candidate["content"]["messages"]
    assistant = messages[1]
    args = assistant["tool_calls"][0]["arguments"]
    check(
        "MASKTYPE-1",
        "a raw tool argument named 'type' is masked (key and value), not exempted",
        isinstance(args, tuple)
        and all(_is_masked_marker(p["key"]) and _is_masked_marker(p["value"]) for p in args),
    )
    tool_part = messages[2]["content"][0]
    check(
        "MASKTYPE-2",
        "a real ContentPart keeps its own 'type' tag in clear and masks its json value's keys",
        tool_part["type"] == "json"
        and isinstance(tool_part["value"], tuple)
        and _is_masked_marker(tool_part["value"][0]["key"]),
    )
    metadata_mapping = ic.mapping_from_object(
        {
            "shape": "prompt_completion",
            "prompt": {"key": "prompt"},
            "completion": {"key": "completion"},
            "metadata": {"language": {"key": "meta"}},
        }
    )
    data = b'{"prompt": "p", "completion": "c", "meta": "CANARY-TYPE-VALUE"}\n'
    result = rev.run_mapping_preview(
        reader_for(data),
        metadata_mapping,
        verified_content_type="application/jsonl",
        dataset_sha256=sha(data),
        tool_schemas=None,
        reveal=False,
    )
    check(
        "MASKTYPE-3",
        "a raw metadata value is masked",
        result.rows[0].outcome == "accepted" and _leaks(result) == [],
    )


# --- UNPINNED faults (round 3): F2/F2b, F6, F11, F14, F15, F16 -----------------------------

JSONL = "application/jsonl"


def _preview(data: bytes, mapping: ic.ImportMapping = PROMPT_COMPLETION, **kw: object):
    kw.setdefault("reveal", True)
    return rev.run_mapping_preview(
        reader_for(data),
        mapping,
        verified_content_type=kw.pop("verified_content_type", JSONL),
        dataset_sha256=sha(data),
        tool_schemas=None,
        **kw,
    )


def test_observer_never_part_of_equality_or_hashes() -> None:
    # F2: the tap must not take part in mapping equality or hashing; F2b: it must not reach
    # mapping_sha256 or the preview digest.
    tapped = dataclasses.replace(PROMPT_COMPLETION, row_observer=lambda r, h: None)
    other = dataclasses.replace(PROMPT_COMPLETION, row_observer=lambda r, h: None)
    check("OBSEQ-1", "a tapped mapping equals the untapped one", tapped == PROMPT_COMPLETION)
    check("OBSEQ-2", "two different observers compare equal", tapped == other)
    check("OBSEQ-4", "repr never shows the observer", "lambda" not in repr(tapped))
    expected = ic._canonical_sha256(
        {
            "shape": "prompt_completion",
            "prompt": {"key": "prompt"},
            "completion": {"key": "completion"},
        }
    )
    check(
        "OBSEQ-5",
        "mapping_sha256 equals an independent recompute over the document",
        tapped.mapping_sha256 == PROMPT_COMPLETION.mapping_sha256
        and PROMPT_COMPLETION.mapping_sha256 == expected,
    )
    data = b'{"prompt": "p", "completion": "c"}\n'
    check(
        "OBSEQ-6",
        "preview_sha256 is identical whether the caller's mapping already carries an observer",
        _preview(data).preview_sha256 == _preview(data, tapped).preview_sha256,
    )
    check(
        "OBSEQ-7",
        "the module's own tap leaves the caller's mapping without an observer",
        PROMPT_COMPLETION.row_observer is None and tapped.row_observer is not None,
    )


def test_raw_display_is_never_normalized() -> None:
    odd_prompt = " Cafe\u0301 \u00a0\t\u200b"
    odd_completion = "c\r\nd  "
    line = json.dumps(
        {" Spaced Key ": "kv", "prompt": odd_prompt, "completion": odd_completion}
    ).encode()
    result = _preview(line + b"\n")
    row = result.rows[0]
    check(
        "RAWDISP-1",
        "revealed JSONL raw_row is exactly the parsed line (no strip, NFC, newline or case change)",
        row.raw_row == json.loads(line) and list(row.raw_row) == list(json.loads(line)),
    )
    check(
        "RAWDISP-2",
        "revealed not_mapped names are the raw key strings, unnormalized",
        row.not_mapped == (" Spaced Key ",),
    )
    csv_data = "q;a;Extra Col \n ask ;Reply\u0301 ;  x\n".encode()
    csv_mapping = ic.mapping_from_object(
        {
            "shape": "prompt_completion",
            "csv": {"encoding": "utf-8", "delimiter": ";", "has_header": True, "header_row": 0},
            "prompt": {"header": "q"},
            "completion": {"header": "a"},
        }
    )
    csv_result = _preview(csv_data, csv_mapping, verified_content_type="text/csv")
    csv_row = csv_result.rows[0]
    check(
        "RAWDISP-3",
        "revealed CSV raw_row keeps every cell and header byte-for-byte",
        csv_row.raw_row == {"q": " ask ", "a": "Reply\u0301 ", "Extra Col ": "  x"},
    )
    check(
        "RAWDISP-4",
        "revealed CSV not_mapped keeps the raw header, trailing space included",
        csv_row.not_mapped == ("Extra Col ",),
    )
    probe = rev.run_mapping_probe(
        reader_for(line + b"\n"),
        PROMPT_COMPLETION,
        verified_content_type=JSONL,
        dataset_sha256=sha(line + b"\n"),
        reveal=True,
    )
    check(
        "RAWDISP-5", "revealed probe rows are unnormalized", probe.raw_rows[0] == json.loads(line)
    )


def _oracle_preview_sha(result: rev.MappingPreviewResult, data: bytes, mapping) -> str:
    rows = [
        {
            "locator": r.locator,
            "outcome": r.outcome,
            "code": r.code,
            "record_sha256": r.candidate["record_sha256"] if r.candidate else None,
            "normalized_sha256": r.candidate["normalized_sha256"] if r.candidate else None,
        }
        for r in result.rows
    ]
    document = {
        "dataset_sha256": sha(data),
        "mapping_sha256": mapping.mapping_sha256,
        "tool_definitions_sha256": rev.compute_tool_definitions_sha256(None),
        "rows": rows,
        "counts": dict(result.counts),
    }
    text = json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(text.encode("ascii")).hexdigest()


def test_preview_sha256_binds_rejections_and_matches_an_oracle() -> None:
    data = b'{"prompt": "p", "completion": "c"}\n{"prompt": "p2"}\n{"completion": "c3"}\n'
    result = _preview(data)
    codes = [r.code for r in result.rows]
    check("PSHA-1", "fixture holds one accepted and two rejected rows", codes.count(None) == 1)
    check(
        "PSHA-2",
        "preview_sha256 equals an independent oracle recompute (rejections included)",
        result.preview_sha256 == _oracle_preview_sha(result, data, PROMPT_COMPLETION),
    )
    masked = _preview(data, reveal=False)
    check(
        "PSHA-3",
        "the digest is the same masked or revealed",
        masked.preview_sha256 == result.preview_sha256,
    )
    one = b'{"prompt": "p", "completion": "c"}\n{"prompt": "p2"}\n'
    two = b'{"prompt": "p", "completion": "c"}\n{"completion": "c3"}\n'
    a, b = _preview(one), _preview(two)
    check(
        "PSHA-4",
        "two sources whose only difference is which field a rejected row lacks differ in digest "
        "once the dataset hash is held equal",
        a.rows[1].outcome == b.rows[1].outcome == "rejected"
        and a.rows[1].slot != b.rows[1].slot
        and a.preview_sha256 != b.preview_sha256,
    )
    c = _preview(b'{"prompt": "p", "completion": "c"}\n{"prompt": "p2"}\n')
    d = _preview(b'{"prompt": "p", "completion": "c"}\n{"prompt": "p2"}\n\n{"prompt": "z"}\n')
    check("PSHA-5", "an extra row changes the digest", c.preview_sha256 != d.preview_sha256)


def _scenarios() -> list[object]:
    """Every public preview/probe path, over the canary rows, for the meta-leak helpers."""
    canary_jsonl = (
        b'{"prompt": "CANARY-RAW-P", "completion": "CANARY-RAW-C", '
        b'"CANARY-RAW-K": "CANARY-RAW-V"}\n'
    )
    bad = b'{"prompt": "CANARY-RAW-P"\n'
    runs: list[object] = []
    for reveal in (True, False):
        runs.append(_preview(canary_jsonl, reveal=reveal))
        runs.append(_preview(bad, reveal=reveal))
        runs.append(
            rev.run_mapping_probe(
                reader_for(canary_jsonl),
                PROMPT_COMPLETION,
                verified_content_type=JSONL,
                dataset_sha256=sha(canary_jsonl),
                reveal=reveal,
            )
        )
    try:
        _preview(canary_jsonl, verified_content_type="application/CANARY-RAW-X")
    except rev.MappingPreviewUnavailableError as exc:
        runs.append(str(exc))
    wrong = ic.ChunkReader(
        [canary_jsonl],
        expected_size=len(canary_jsonl) + 1,
        expected_sha256="0" * 64,
        limits=rev.PREVIEW_LIMITS,
    )
    runs.append(
        rev.run_mapping_preview(
            wrong,
            PROMPT_COMPLETION,
            verified_content_type=JSONL,
            dataset_sha256="0" * 64,
            tool_schemas=None,
            reveal=False,
        )
    )
    return runs


def _meta_leaks(fn, needle: str) -> list[str]:
    """Shared META-LEAK helper: runs `fn` with every side channel captured (root logging at
    DEBUG, warnings, stdout, stderr) and returns the channels that contain `needle`."""
    import contextlib
    import io
    import logging
    import warnings

    records: list[str] = []

    class Capture(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record.getMessage() + repr(record.args) + repr(record.exc_info))

    root = logging.getLogger()
    handler, level = Capture(level=logging.DEBUG), root.level
    root.addHandler(handler)
    root.setLevel(logging.DEBUG)
    out, err = io.StringIO(), io.StringIO()
    try:
        with (
            warnings.catch_warnings(record=True) as caught,
            contextlib.redirect_stdout(out),
            contextlib.redirect_stderr(err),
        ):
            warnings.simplefilter("always")
            fn()
    finally:
        root.removeHandler(handler)
        root.setLevel(level)
    channels = {
        "logging": "".join(records),
        "warnings": "".join(str(w.message) for w in caught),
        "stdout": out.getvalue(),
        "stderr": err.getvalue(),
    }
    return [name for name, text in channels.items() if needle in text]


def test_no_raw_row_in_logs_stdout_stderr_warnings_or_exception_text() -> None:
    leaks = _meta_leaks(_scenarios, "CANARY-RAW")
    check("METALEAK-1", f"no raw row text reaches any side channel: {leaks}", leaks == [])

    def positive_control() -> None:
        import logging

        logging.getLogger("x").warning("CANARY-RAW logged")
        print("CANARY-RAW printed")

    check(
        "METALEAK-2",
        "positive control: the helper does see a canary logged or printed",
        set(_meta_leaks(positive_control, "CANARY-RAW")) == {"logging", "stdout"},
    )
    masked_texts = [
        _whole(r) for r in _scenarios() if isinstance(r, rev.MappingPreviewResult | rev.ProbeResult)
    ]
    check("METALEAK-3", "scenarios actually ran", len(masked_texts) == 7)


def test_no_socket_and_no_eval_compile_in_the_preview_path() -> None:
    import sys as _sys

    _scenarios()  # warm every lazy import first: module import itself is not the preview path
    events: list[str] = []

    def hook(event: str, args: tuple) -> None:
        if events is not None and (event.startswith("socket.") or event in ("compile", "exec")):
            events.append(event)

    active = [True]

    def guarded(event: str, args: tuple) -> None:
        if active[0]:
            hook(event, args)

    _sys.addaudithook(guarded)
    try:
        _scenarios()
        clean = list(events)
        events.clear()
        socket_module = __import__("socket")
        with __import__("contextlib").suppress(OSError):
            socket_module.getaddrinfo("localhost", 1)
        eval("1 + 1")  # noqa: S307 - positive control for the audit hook
        control = list(events)
    finally:
        active[0] = False
    check(
        "NOEXEC-1",
        f"no socket, compile or exec audit event in any preview/probe path: {clean}",
        clean == [],
    )
    check(
        "NOEXEC-2",
        "positive control: the same hook sees a socket call and an eval",
        any(e.startswith("socket.") for e in control) and "compile" in control,
    )


# --- OBSERVER-DIFF at importer level (round 3) ---------------------------------------------


def _mutating_observer(row: object, headers: object) -> None:
    """Deep mutation of everything the tap is handed: nested lists/objects cleared, keys added."""

    def wreck(value: object) -> None:
        if isinstance(value, dict):
            for inner in list(value.values()):
                wreck(inner)
            value.clear()
            value["WRECKED"] = "x"
        elif isinstance(value, list):
            for inner in list(value):
                wreck(inner)
            value.clear()

    wreck(row)


def _run_importer(kind: str, mapping: ic.ImportMapping, data: bytes):
    from workers.data import csv_importer, json_importer, jsonl_importer

    accepted: list[object] = []
    common = {
        "verified_content_type": {
            "jsonl": "application/jsonl",
            "json": "application/json",
            "csv": "text/csv",
        }[kind],
        "dataset_purpose": "training",
    }
    runner: Callable[..., Any] = {
        "jsonl": jsonl_importer.run_jsonl_import,
        "json": json_importer.run_json_import,
        "csv": csv_importer.run_csv_import,
    }[kind]
    outcome = runner(
        reader_for(data),
        mapping,
        expected_sha256=sha(data),
        on_accepted=lambda c: accepted.append(json.loads(json.dumps(c))),
        **common,
    )
    return outcome.report, accepted


def test_importer_level_observer_differential_across_jsonl_json_csv() -> None:
    messages = (
        '{"messages": [{"role": "user", "content": "q1", "extra": {"k": [1, 2]}}, '
        '{"role": "assistant", "content": "a1"}]}'
    )
    bad = '{"messages": [{"role": "nobody", "content": "q"}]}'
    jsonl = f"{messages}\n{bad}\n{messages}\n".encode()
    json_doc = f"[{messages}, {bad}, {messages}]".encode()
    json_mapping = ic.mapping_from_object(
        {
            "shape": "messages_json",
            "json": {"container": "", "ignored_top_level_keys": []},
            "messages": {"key": "messages"},
            "role_key": "role",
            "content_key": "content",
            "role_map": {"user": "user", "assistant": "assistant"},
            "on_unmapped_message_keys": "ignore",
        }
    )
    jsonl_mapping = ic.mapping_from_object(
        {
            "shape": "messages_json",
            "messages": {"key": "messages"},
            "role_key": "role",
            "content_key": "content",
            "role_map": {"user": "user", "assistant": "assistant"},
            "on_unmapped_message_keys": "ignore",
        }
    )
    csv_data = b"q,a\np1,c1\n,c2\np3,c3\n"
    cases = (
        ("jsonl", jsonl_mapping, jsonl),
        ("json", json_mapping, json_doc),
        ("csv", CSV_PROMPT_COMPLETION, csv_data),
    )
    for kind, mapping, data in cases:
        plain_report, plain_accepted = _run_importer(kind, mapping, data)
        tapped = dataclasses.replace(mapping, row_observer=_mutating_observer)
        tapped_report, tapped_accepted = _run_importer(kind, tapped, data)
        check(
            f"OBSDIFF-{kind}-1",
            "the import ran and accepted and rejected rows (the fixture is not vacuous)",
            plain_report.records_accepted == 2 and plain_report.records_rejected == 1,
        )
        check(
            f"OBSDIFF-{kind}-2",
            "report_sha256 is identical with a nested-mutating observer attached",
            tapped_report.report_sha256 == plain_report.report_sha256,
        )
        check(
            f"OBSDIFF-{kind}-3",
            "every accepted candidate is identical with the observer attached",
            tapped_accepted == plain_accepted and len(plain_accepted) == 2,
        )
        check(
            f"OBSDIFF-{kind}-4",
            "the preview over the same source reports the same accepted/rejected split",
            _preview(data, mapping, verified_content_type=_run_ct(kind)).counts["accepted"]
            == plain_report.records_accepted,
        )


def _run_ct(kind: str) -> str:
    return {"jsonl": "application/jsonl", "json": "application/json", "csv": "text/csv"}[kind]


def main() -> None:
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
            except Exception as exc:  # noqa: BLE001 - a crash is a failed test, reported by name
                FAILURES.append(f"FAIL [{name}] raised {type(exc).__name__}: {exc}")

    if FAILURES:
        print(f"FTF-H011-P1 dataset mapping review: {len(FAILURES)} failed")
        for failure in FAILURES[:200]:
            print(f"  {failure}")
        sys.exit(1)

    print("FTF-H011-P1 dataset mapping review: all checks passed")
    sys.exit(0)


if __name__ == "__main__":
    main()
