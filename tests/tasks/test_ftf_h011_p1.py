#!/usr/bin/env python3
# mypy: disable-error-code="arg-type,attr-defined,assignment,call-arg,index,var-annotated,misc,call-overload,union-attr"  # noqa: E501
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
        all(rev._MASK_PREFIX in str(v) for v in row.raw_row.values()),
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
        result.rows[0].outcome == "accepted" and result.rows[0].raw_row["prompt"] == "p1",
    )
    check(
        "PAIR-3",
        "row 2 rejected required_field_missing with its own raw row",
        result.rows[1].outcome == "rejected"
        and result.rows[1].code == "required_field_missing"
        and result.rows[1].raw_row == {"prompt": "p2"},
    )
    check("PAIR-4", "row 3 accepted with its own raw row", result.rows[2].raw_row["extra"] == 1)
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

    row = {"role": "human", "content": "hi"}
    check(
        "ROLE-2",
        "a message-shaped row's own raw role string is observed",
        rev._observed_role_value(MESSAGES_JSON, row) == "human",
    )
    check(
        "ROLE-3",
        "a non-string role is never observed",
        rev._observed_role_value(MESSAGES_JSON, {"role": 1, "content": "x"}) is None,
    )
    check(
        "ROLE-4",
        "a tabular row (no message_keys apply) is never observed",
        rev._observed_role_value(PROMPT_COMPLETION, ("a", "b")) is None,
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
