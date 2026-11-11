#!/usr/bin/env python3
# mypy: disable-error-code="arg-type,attr-defined,assignment,call-arg,index,var-annotated,misc"
"""FTF-H011-P6 -- XLSX mapping probe and preview, pure domain.

Runnable without a database or network. FTF-H011-P1 answered `importer_unavailable` for an
XLSX source; P6 (after FTF-0505 CERTIFIED) dispatches it through 0505's own event stream and adds
the worksheet probe (exact names, hidden sheets flagged). Criteria:

  DISP   an XLSX verified content type now previews and probes; anything else unrecognised is
         still `importer_unavailable`, never a fallback
  PREV   accepted rows pair raw cells with the canonical candidate; a formula cell is shown as
         a rejected `formula_cell` row; an Arabic sheet name works
  PROBE  headers and raw rows of the named sheet
  SHEET  `run_worksheet_probe`: exact names in workbook order, hidden/veryHidden flagged, Arabic
         names, masked names when not revealed (hidden flag stays), unsafe/non-zip/changed/
         outage sources are a visible state and failure code, never an exception
  CLOSE  a truncated preview closes the XLSX stream (its spool) at once
  SHA    `preview_sha256` is the same for reveal True and False

Run directly: python3 tests/tasks/test_ftf_h011_p6.py
"""

from __future__ import annotations

import hashlib
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests" / "tasks"))
sys.path.insert(0, str(ROOT / "apps" / "api"))

import test_ftf_0505 as t505  # noqa: E402  (the FTF-0505 workbook builders)
import workers.data.dataset_mapping_review as rev  # noqa: E402
from workers.data import import_common as ic  # noqa: E402
from workers.data import xlsx_importer as xi  # noqa: E402

FAILURES: list[str] = []
XLSX = xi.XLSX_CONTENT_TYPE


def check(criterion_id: str, description: str, condition: bool) -> None:
    if not condition:
        FAILURES.append(f"FAIL [{criterion_id}] {description}")


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def reader_for(data: bytes) -> ic.ChunkReader:
    return ic.ChunkReader(
        [data], expected_size=len(data), expected_sha256=sha(data), limits=rev.PREVIEW_LIMITS
    )


def preview(data: bytes, *, reveal: bool = True, **mapping_kwargs: Any) -> Any:
    return rev.run_mapping_preview(
        reader_for(data),
        t505.build_mapping(**mapping_kwargs),
        verified_content_type=XLSX,
        dataset_sha256=sha(data),
        tool_schemas=None,
        reveal=reveal,
    )


def probe(data: bytes, *, reveal: bool = True, **mapping_kwargs: Any) -> Any:
    return rev.run_mapping_probe(
        reader_for(data),
        t505.build_mapping(**mapping_kwargs),
        verified_content_type=XLSX,
        dataset_sha256=sha(data),
        reveal=reveal,
    )


def sheets(data: bytes, *, reveal: bool = True, content_type: str = XLSX) -> Any:
    return rev.run_worksheet_probe(
        reader_for(data),
        verified_content_type=content_type,
        dataset_sha256=sha(data),
        reveal=reveal,
    )


S = t505.S
GOOD = t505.book([[S("q"), S("a")], [S("hi"), S("there")], [S("yo"), S("ok")]])


# --- DISP --------------------------------------------------------------------------------


def test_xlsx_is_dispatched_and_unknown_still_unavailable() -> None:
    check("DISP-1", "XLSX is in the preview content types", XLSX in rev.PREVIEW_CONTENT_TYPES)
    result = preview(GOOD)
    check(
        "DISP-2",
        "an XLSX source previews to completed",
        result.state == "completed" and result.counts["accepted"] == 2,
    )
    for content_type in ("application/octet-stream", "application/vnd.ms-excel", ""):
        for call in ("preview", "probe", "sheets"):
            raised = None
            try:
                if call == "preview":
                    rev.run_mapping_preview(
                        reader_for(GOOD),
                        t505.build_mapping(),
                        verified_content_type=content_type,
                        dataset_sha256=sha(GOOD),
                        tool_schemas=None,
                        reveal=True,
                    )
                elif call == "probe":
                    rev.run_mapping_probe(
                        reader_for(GOOD),
                        t505.build_mapping(),
                        verified_content_type=content_type,
                        dataset_sha256=sha(GOOD),
                        reveal=True,
                    )
                else:
                    sheets(GOOD, content_type=content_type)
            except rev.MappingPreviewUnavailableError as exc:
                raised = exc
            check(
                "DISP-3",
                f"{call} of {content_type!r} is importer_unavailable",
                raised is not None and raised.code == "importer_unavailable",
            )


# --- PREV --------------------------------------------------------------------------------


def test_preview_pairs_raw_cells_with_candidates() -> None:
    result = preview(GOOD)
    first = result.rows[0]
    check("PREV-1", "first row is accepted", first.outcome == "accepted")
    check("PREV-2", "locator is record:1", first.locator == "record:1")
    check(
        "PREV-3",
        "the raw row is keyed by the sheet's own header cells",
        dict(first.raw_row) == {"q": "hi", "a": "there"},
    )
    check(
        "PREV-4",
        "a comparison cell exists per mapped slot",
        {cell.slot for cell in first.comparison} == {"prompt", "completion"},
    )
    check(
        "PREV-5",
        "the source schema digest is the importer's own",
        isinstance(result.source_schema_sha256, str) and len(result.source_schema_sha256) == 64,
    )
    check("PREV-6", "complete when the sheet is exhausted", result.complete is True)


def test_formula_cell_is_shown_rejected() -> None:
    data = t505.book([[S("q"), S("a")], [S("hi"), t505.F("1+1", "2")], [S("ok"), S("fine")]])
    result = preview(data)
    check(
        "PREV-7",
        "a formula in a mapped column is a rejected formula_cell row, shown",
        result.rows[0].outcome == "rejected" and result.rows[0].code == "formula_cell",
    )
    check(
        "PREV-8",
        "the formula row is rejected before the mapping sees it, so it carries no raw row -- "
        "and is never paired with a neighbour's",
        result.rows[0].raw_row is None and dict(result.rows[1].raw_row) == {"q": "ok", "a": "fine"},
    )
    check(
        "PREV-9",
        "the next row is unaffected",
        result.rows[1].outcome == "accepted" and result.counts["rejected:formula_cell"] == 1,
    )


def test_arabic_sheet_name_and_cells() -> None:
    data = t505.book(
        [[S("q"), S("a")], [S("مرحبا"), S("أهلا")]],
        sheet="البيانات",
    )
    result = preview(data, sheet="البيانات")
    check(
        "PREV-10",
        "an Arabic sheet name previews and the Arabic cells survive",
        result.state == "completed" and dict(result.rows[0].raw_row) == {"q": "مرحبا", "a": "أهلا"},
    )


def test_missing_sheet_is_a_visible_failure() -> None:
    result = preview(GOOD, sheet="Nope")
    check(
        "PREV-11",
        "a sheet that does not exist is failed/mapping_column_missing, never another sheet",
        result.state == "failed" and result.failure_code == "mapping_column_missing",
    )
    check("PREV-12", "no rows are invented", result.rows == () and result.complete is False)


def test_hash_mismatch_is_source_changed() -> None:
    reader = ic.ChunkReader(
        [GOOD], expected_size=len(GOOD), expected_sha256="0" * 64, limits=rev.PREVIEW_LIMITS
    )
    result = rev.run_mapping_preview(
        reader,
        t505.build_mapping(),
        verified_content_type=XLSX,
        dataset_sha256=sha(GOOD),
        tool_schemas=None,
        reveal=True,
    )
    check(
        "PREV-13",
        "a hash mismatch is failed/source_changed, not an exception",
        result.state == "failed" and result.failure_code == "source_changed",
    )


def test_preview_sha_is_independent_of_reveal() -> None:
    check(
        "SHA-1",
        "preview_sha256 is the same masked and unmasked",
        preview(GOOD, reveal=True).preview_sha256 == preview(GOOD, reveal=False).preview_sha256,
    )
    masked = preview(GOOD, reveal=False)
    text = repr(masked)
    check(
        "SHA-2",
        "no cell text survives masking",
        "hi" not in text.replace("hidden", "") and "there" not in text and "yo" not in text,
    )


# --- PROBE -------------------------------------------------------------------------------


def test_probe_returns_headers_and_raw_rows() -> None:
    result = probe(GOOD)
    check(
        "PROBE-1",
        "headers are the header row's cells",
        result.state == "completed" and tuple(result.headers) == ("q", "a"),
    )
    check(
        "PROBE-2",
        "raw rows are keyed by header",
        [dict(r) for r in result.raw_rows] == [{"q": "hi", "a": "there"}, {"q": "yo", "a": "ok"}],
    )
    masked = probe(GOOD, reveal=False)
    check(
        "PROBE-3",
        "headers and values are masked when not revealed",
        "there" not in repr(masked) and "'q'" not in repr(masked.headers),
    )


# --- SHEET -------------------------------------------------------------------------------


def _multi() -> bytes:
    return t505.book(
        [[S("q"), S("a")], [S("hi"), S("there")]],
        sheet="Data",
        extra_sheets=[
            ("مخفي", "rId2", "hidden"),
            ("Deep", "rId3", "veryHidden"),
            ("Z", "rId4", None),
        ],
        rel_extra=(
            f'<Relationship Id="rId2" Type="{t505.REL}/worksheet" Target="worksheets/sheet1.xml"/>'
            f'<Relationship Id="rId3" Type="{t505.REL}/worksheet" Target="worksheets/sheet1.xml"/>'
            f'<Relationship Id="rId4" Type="{t505.REL}/worksheet" Target="worksheets/sheet1.xml"/>'
        ),
    )


def test_worksheet_probe_lists_names_and_flags_hidden() -> None:
    result = sheets(_multi())
    check("SHEET-1", "state completed", result.state == "completed" and result.failure_code is None)
    check(
        "SHEET-2",
        "exact names in workbook order",
        [s["name"] for s in result.sheets] == ["Data", "مخفي", "Deep", "Z"],
    )
    check(
        "SHEET-3",
        "hidden and veryHidden are flagged, visible sheets are not",
        [s["hidden"] for s in result.sheets] == [False, True, True, False],
    )
    check(
        "SHEET-4",
        "the raw workbook state is kept",
        [s["state"] for s in result.sheets] == ["visible", "hidden", "veryHidden", "visible"],
    )


def test_worksheet_probe_masks_names_but_keeps_the_hidden_flag() -> None:
    result = sheets(_multi(), reveal=False)
    text = repr(result)
    check(
        "SHEET-5",
        "no sheet name survives masking",
        all(name not in text for name in ("Data", "مخفي", "Deep")),
    )
    check(
        "SHEET-6",
        "the hidden flag and count survive masking",
        [s["hidden"] for s in result.sheets] == [False, True, True, False],
    )


def test_worksheet_probe_refuses_unsafe_and_unreadable_sources() -> None:
    macro = t505.book(extra=[("xl/vbaProject.bin", b"x")])
    result = sheets(macro)
    check(
        "SHEET-7",
        "a macro workbook is failed/unsafe_workbook with no sheets",
        result.state == "failed"
        and result.failure_code == "unsafe_workbook"
        and result.sheets == (),
    )
    junk = b"not a zip file at all"
    result = sheets(junk)
    check(
        "SHEET-8",
        "a non-zip source is failed/unsupported_workbook",
        result.state == "failed" and result.failure_code == "unsupported_workbook",
    )
    reader = ic.ChunkReader(
        [GOOD], expected_size=len(GOOD), expected_sha256="0" * 64, limits=rev.PREVIEW_LIMITS
    )
    result = rev.run_worksheet_probe(
        reader, verified_content_type=XLSX, dataset_sha256=sha(GOOD), reveal=True
    )
    check(
        "SHEET-9",
        "a changed source is failed/source_changed",
        result.state == "failed" and result.failure_code == "source_changed",
    )

    pulled: list[int] = []

    def watched() -> Iterator[bytes]:
        pulled.append(1)
        yield GOOD

    reader = ic.ChunkReader(watched(), expected_size=len(GOOD), expected_sha256="0" * 64)
    result = rev.run_worksheet_probe(
        reader, verified_content_type=XLSX, dataset_sha256=sha(GOOD), reveal=True
    )
    check(
        "SHEET-9b",
        "a declared-hash mismatch is refused before a single byte is read",
        result.failure_code == "source_changed" and pulled == [],
    )

    def failing() -> Iterator[bytes]:
        yield GOOD[:20]
        raise ConnectionError("storage dropped")

    reader = ic.ChunkReader(failing(), expected_size=len(GOOD), expected_sha256=sha(GOOD))
    result = rev.run_worksheet_probe(
        reader, verified_content_type=XLSX, dataset_sha256=sha(GOOD), reveal=True
    )
    check(
        "SHEET-10",
        "a storage outage is a visible non-completed state, never an exception",
        result.state in ("blocked", "failed") and result.failure_code is not None,
    )
    dup = t505.book(extra_sheets=[("data", "rId2", None)])
    result = sheets(dup)
    check(
        "SHEET-11",
        "casefold-duplicate sheet names are refused like the importer refuses them",
        result.state == "failed" and result.failure_code == "duplicate_sheet_name",
    )


def test_worksheet_probe_lists_zero_sheets_as_empty_not_failed() -> None:
    # A workbook the importer would open is listed; the listing never claims a sheet exists
    # that the workbook part does not name.
    result = sheets(GOOD)
    check(
        "SHEET-12",
        "a one-sheet workbook lists exactly that sheet",
        [s["name"] for s in result.sheets] == ["Data"],
    )


# --- CLOSE -------------------------------------------------------------------------------


def test_truncated_preview_closes_the_stream() -> None:
    rows = [[S("q"), S("a")]] + [[S(f"p{i}"), S(f"c{i}")] for i in range(40)]
    data = t505.book(rows)
    closed: list[int] = []
    original = xi.XlsxEventStream.close

    def counting(self: Any) -> None:
        closed.append(1)
        original(self)

    xi.XlsxEventStream.close = counting
    try:
        result = preview(data)
        probed = probe(data)
    finally:
        xi.XlsxEventStream.close = original
    check(
        "CLOSE-1",
        "a truncated preview and a truncated probe each close the XLSX stream",
        result.complete is False and probed.complete is False and len(closed) == 2,
    )


def main() -> int:
    tests = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_") and callable(f)]
    for name, fn in tests:
        try:
            fn()
        except Exception as exc:  # noqa: BLE001 -- a crash is a failed test, reported by name
            FAILURES.append(f"FAIL [FTF-H011-P6] {name} raised {type(exc).__name__}: {exc}")
    if FAILURES:
        for failure in FAILURES:
            print(failure)
        print(f"{len(FAILURES)} check(s) failed")
        return 1
    print("all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
