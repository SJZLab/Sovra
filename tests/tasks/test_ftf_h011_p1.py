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
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

import workers.data.dataset_mapping_review as rev  # noqa: E402
from workers.data import import_common as ic  # noqa: E402

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


def test_mask_every_value_headers_in_clear() -> None:
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
    assert isinstance(raw, dict)
    check("MASK-1", "the key names stay in clear", set(raw) == {"prompt", "completion", "pii"})
    for key, value in raw.items():
        check(f"MASK-2-{key}", f"{key}'s value is masked", rev._MASK_PREFIX in str(value))
    check(
        "MASK-3",
        "none of the real text leaks through a masked value",
        "secret-question" not in str(raw) and "123-45-6789" not in str(raw),
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
    masked_list = rev._mask_value([1, "x", None, True, {"k": "v"}])
    check("MASK-5", "a list's own values are each masked", isinstance(masked_list, list))
    check("MASK-6", "None is never masked (not sensitive)", masked_list[2] is None)
    check("MASK-7", "a bool is never masked (not sensitive)", masked_list[3] is True)
    check("MASK-8", "a string is masked", rev._MASK_PREFIX in str(masked_list[1]))
    check("MASK-9", "a nested dict's own key stays in clear", "k" in masked_list[4])
    check(
        "MASK-10",
        "a nested dict's own value is masked",
        rev._MASK_PREFIX in str(masked_list[4]["k"]),
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
    check(
        "MASK-11",
        "a tabular raw row is keyed by its real header names",
        set(row.raw_row) == {"q", "a"},
    )
    check(
        "MASK-12",
        "its values are masked",
        all(rev._MASK_PREFIX in str(v) for v in row.raw_row.values()),  # type: ignore[union-attr]
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

    rejected = build(outcome="rejected", candidate=None, code="required_field_missing")
    check("ROW-5", "a valid rejected row builds", rejected.code == "required_field_missing")


def test_size_bucket_boundaries() -> None:
    check("SIZE-1", "0 is its own bucket", rev._size_bucket(0) == ">=0")
    check("SIZE-2", "1..8 share a bucket", rev._size_bucket(1) == rev._size_bucket(8) == ">=1")
    check("SIZE-3", "9 starts the next bucket", rev._size_bucket(9) == ">=9")
    check("SIZE-4", "8 and 9 are different buckets", rev._size_bucket(8) != rev._size_bucket(9))


def test_mask_value_distinguishes_str_from_numeric() -> None:
    masked_str = rev._mask_value("hi")
    masked_int = rev._mask_value(7)
    masked_float = rev._mask_value(1.5)
    check("MASKV-1", "a str mask carries a size bucket", ":str:" in str(masked_str))
    check("MASKV-2", "an int mask names its type, no size bucket", str(masked_int).endswith(":int"))
    check(
        "MASKV-3",
        "a float mask names its own type, not int's",
        str(masked_float).endswith(":float"),
    )
    check(
        "MASKV-4",
        "str/int/float masks are all distinct",
        len({masked_str, masked_int, masked_float}) == 3,
    )


def test_mask_row_none_passthrough() -> None:
    check("MASKROW-1", "_mask_row(None, ...) is None", rev._mask_row(None, None) is None)
    check(
        "MASKROW-2",
        "a tabular row whose length does not match headers falls back to a plain masked tuple",
        rev._mask_row(("a", "b", "c"), ("only-one-header",))
        == tuple(rev._mask_row(("a", "b", "c"), ("only-one-header",))),
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
        isinstance(masked_result, tuple) and all(rev._MASK_PREFIX in str(v) for v in masked_result),
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
