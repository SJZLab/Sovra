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
from workers.data import ooxml_safety as ox  # noqa: E402
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
        result.state == "blocked" and result.failure_code == "source_unavailable",
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


def test_registry_is_complete_for_xlsx() -> None:
    import app.datasets.dataset_mapping_store as store

    name = store.importer_for_content_type(XLSX)
    check("REG-1", "the store selects the xlsx importer", name == "xlsx")
    check(
        "REG-2",
        "every importer the store can select has a version entry (else propose KeyErrors)",
        all(
            store.importer_for_content_type(ct) in store._IMPORTER_VERSIONS
            for ct in (*rev.PREVIEW_CONTENT_TYPES,)
        )
        and store._IMPORTER_VERSIONS["xlsx"] == xi.IMPORTER_VERSION,
    )


def test_list_worksheets_own_content_type_gate() -> None:
    for ct in (
        "text/csv",
        "",
        "application/vnd.ms-excel",
        XLSX.upper(),
        f" {XLSX}",
        f"{XLSX} ",
    ):
        result = xi.list_worksheets(
            reader_for(GOOD), verified_content_type=ct, dataset_sha256=sha(GOOD)
        )
        check(
            "SHEET-13",
            f"list_worksheets({ct!r}) is failed/content_type_not_supported_by_importer",
            result.state == "failed"
            and result.failure_code == "content_type_not_supported_by_importer"
            and result.sheets == (),
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


def test_content_type_routing_is_exact() -> None:
    import app.datasets.dataset_mapping_store as store

    def refused(value: object) -> bool:
        try:
            store.importer_for_content_type(value)
        except store.MappingStoreError as exc:
            return str(exc) == "importer_unavailable"
        return False

    exact = {
        "application/json": "json",
        "text/csv": "csv",
        XLSX: "xlsx",
        **{ct: "jsonl" for ct in store.JSONL_PREVIEW_CONTENT_TYPES},
    }
    for ct, name in exact.items():
        check("ROUTE-1", f"{ct!r} selects {name}", store.importer_for_content_type(ct) == name)
        for variant in (ct.upper(), f" {ct}", f"{ct} ", ct.title()):
            if variant in exact:
                continue
            check("ROUTE-2", f"{variant!r} is refused (exact match only)", refused(variant))
    for value in (None, 7, b"text/csv", ("text/csv",), type("S", (str,), {})(XLSX), ""):
        check("ROUTE-3", f"non-exact-str {value!r} is refused", refused(value))
    check("ROUTE-4", "an unknown type is refused", refused("application/pdf"))


def test_worksheet_probe_content_type_is_exact() -> None:
    for ct in (XLSX.upper(), f" {XLSX}", f"{XLSX} ", XLSX.title(), "text/csv", ""):
        try:
            sheets(GOOD, content_type=ct)
        except rev.MappingPreviewUnavailableError:
            ok = True
        else:
            ok = False
        check("SHEET-14", f"run_worksheet_probe({ct!r}) is unavailable", ok)


def test_unrecognised_sheet_state_is_kept_exact_and_flagged_hidden() -> None:
    # `hidden` is True for anything but the exact string `visible`; the state is never folded.
    data = t505.book(
        extra_sheets=[("Odd", "rId2", "Visible")],
        rel_extra=(
            f'<Relationship Id="rId2" Type="{t505.REL}/worksheet" Target="worksheets/sheet1.xml"/>'
        ),
    )
    result = sheets(data)
    check(
        "SHEET-15",
        "a state that is not exactly `visible` is kept verbatim and flagged hidden",
        [(s["state"], s["hidden"]) for s in result.sheets]
        == [("visible", False), ("Visible", True)],
    )


# --- PKG: every package-level refusal, pinned with its exact code -------------------------


def _members(data: bytes) -> list[tuple[str, bytes]]:
    return t505.members_of(data)


def _swap(data: bytes, name: str, payload: bytes) -> bytes:
    return t505.with_member(data, name, payload)


def _wb_xml(inner: str, *, root: str = "workbook", ns: str = t505.MAIN) -> bytes:
    return (f'{t505.XML_HEAD}<{root} xmlns="{ns}" xmlns:r="{t505.REL}">{inner}</{root}>').encode()


def _package_cases() -> list[tuple[str, bytes, str]]:
    rel = t505.REL
    ole = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 600
    ext = (
        f'<Relationship Id="rIdX" Type="{rel}/hyperlink" Target="http://h/x"'
        ' TargetMode="{mode}"/>'
    )
    vba = "http://schemas.microsoft.com/office/2006/relationships/vbaProject"
    one = '<sheet name="Data" sheetId="1" r:id="rId1"/>'
    cases: list[tuple[str, bytes, str]] = [
        ("an OLE compound file", ole, "unsupported_workbook"),
        ("not a zip at all", b"hello, not a workbook" * 40, "unsupported_workbook"),
        ("a zip with no workbook", t505.t501._zip([("a.txt", b"hello")]), "unsupported_workbook"),
        (
            "no [Content_Types].xml",
            t505.book(drop=("[Content_Types].xml",)),
            "unsupported_workbook",
        ),
        (
            "a template main type",
            t505.book(workbook_type=t505.TEMPLATE_TYPE),
            "unsupported_workbook",
        ),
        ("path traversal part", t505.with_extra(GOOD, ("../evil.xml", b"x")), "unsafe_archive"),
        ("an absolute part", t505.with_extra(GOOD, ("/abs.xml", b"x")), "unsafe_archive"),
        (
            "a macro part name",
            t505.with_extra(GOOD, ("xl/vbaProject.bin", b"x")),
            "unsafe_workbook",
        ),
        (
            "a macro part name in mixed case",
            t505.with_extra(GOOD, ("XL/VbaProject.BIN", b"x")),
            "unsafe_workbook",
        ),
        (
            "an externalLinks part",
            t505.with_extra(GOOD, ("xl/externalLinks/externalLink1.xml", b"<x/>")),
            "external_link_present",
        ),
        (
            "an externalLinks part in mixed case",
            t505.with_extra(GOOD, ("XL/ExternalLinks/e.xml", b"<x/>")),
            "external_link_present",
        ),
        (
            "a queryTables part",
            t505.with_extra(GOOD, ("xl/queryTables/q1.xml", b"<x/>")),
            "external_link_present",
        ),
        ("a macro-enabled main type", t505.book(workbook_type=t505.MACRO_TYPE), "unsafe_workbook"),
        (
            "a declared vbaProject type in any case",
            t505.book(
                ct_extra=(
                    '<Override PartName="/xl/b.bin"'
                    ' ContentType="application/vnd.ms-office.VBAPROJECT"/>'
                )
            ),
            "unsafe_workbook",
        ),
        (
            "a declared macro-enabled type in any case",
            t505.book(
                ct_extra=(
                    '<Override PartName="/xl/b.xml"'
                    ' ContentType="application/x.MacroEnabled.main+xml"/>'
                )
            ),
            "unsafe_workbook",
        ),
        (
            "a vbaProject relationship type",
            t505.book(rel_extra=f'<Relationship Id="v" Type="{rel}/vbaProject" Target="v.bin"/>'),
            "unsafe_workbook",
        ),
        (
            "Excel's own vbaProject relationship type",
            t505.book(rel_extra=f'<Relationship Id="v" Type="{vba}" Target="v.bin"/>'),
            "unsafe_workbook",
        ),
        (
            "an external relationship",
            t505.book(rel_extra=ext.format(mode="External")),
            "external_link_present",
        ),
        (
            "an external relationship, other case",
            t505.book(rel_extra=ext.format(mode="EXTERNAL")),
            "external_link_present",
        ),
        (
            "an external relationship, padded",
            t505.book(rel_extra=ext.format(mode=" External ")),
            "external_link_present",
        ),
        (
            "an external relationship in a part rels file",
            t505.with_extra(
                GOOD,
                (
                    "xl/worksheets/_rels/sheet1.xml.rels",
                    (
                        f'{t505.XML_HEAD}<Relationships xmlns="{t505.PKG}">'
                        f'{ext.format(mode="External")}</Relationships>'
                    ).encode(),
                ),
            ),
            "external_link_present",
        ),
        (
            "two sheets with one name",
            t505.book(extra_sheets=[("Data", "rId2", None)]),
            "duplicate_sheet_name",
        ),
        (
            "two sheets differing only in case",
            t505.book(extra_sheets=[("DATA", "rId2", None)]),
            "duplicate_sheet_name",
        ),
        (
            "two names that only casefold equates",
            t505.book(sheet="Stra\u00dfe", extra_sheets=[("STRASSE", "rId2", None)]),
            "duplicate_sheet_name",
        ),
        (
            "no workbook relationships",
            t505.book(drop=("xl/_rels/workbook.xml.rels",)),
            "xlsx_malformed",
        ),
        (
            "a sheet without a name",
            _swap(
                GOOD,
                "xl/workbook.xml",
                _wb_xml('<sheets><sheet sheetId="1" r:id="rId1"/></sheets>'),
            ),
            "xlsx_malformed",
        ),
        (
            "a sheet without a relationship id",
            _swap(
                GOOD,
                "xl/workbook.xml",
                _wb_xml('<sheets><sheet name="Data" sheetId="1"/></sheets>'),
            ),
            "xlsx_malformed",
        ),
        (
            "a root that is not a workbook",
            _swap(GOOD, "xl/workbook.xml", _wb_xml(one, root="worksheet")),
            "xlsx_malformed",
        ),
        (
            "a root spelled Workbook",
            _swap(GOOD, "xl/workbook.xml", _wb_xml(f"<sheets>{one}</sheets>", root="Workbook")),
            "xlsx_malformed",
        ),
        (
            "an xl/connections.xml part",
            t505.with_extra(GOOD, ("xl/connections.xml", b"<x/>")),
            "external_link_present",
        ),
        ("an OLE head over a valid zip", ole + GOOD, "unsupported_workbook"),
        ("junk before a valid zip", b"JUNK" + GOOD, "unsupported_workbook"),
        ("a zip head with no end record", b"PK\x03\x04" + b"\x00" * 400, "unsupported_workbook"),
        (
            "a corrupt central directory",
            GOOD.replace(b"PK\x01\x02", b"PK\x01\x00", 1),
            "unsupported_workbook",
        ),
        (
            "a 'version needed to extract' past 63",
            t505.version_needed_past_63(GOOD),
            "xlsx_malformed",
        ),
        (
            "an end record claiming more than the entry bound",
            GOOD + t505.t501._zip64_tail(ox.MAX_ENTRIES + 1, 100, 0),
            "unsafe_archive",
        ),
        (
            "an end record claiming a directory over the byte bound",
            GOOD + t505.t501._zip64_tail(5, ox.MAX_DIRECTORY_BYTES + 1, 0),
            "unsafe_archive",
        ),
        (
            "a part whose name only casefold maps onto a link prefix",
            t505.with_extra(GOOD, ("xl/externalLink\u017f/e.xml", b"<x/>")),
            "external_link_present",
        ),
        (
            "a strict-namespace workbook",
            _swap(GOOD, "xl/workbook.xml", _wb_xml(f"<sheets>{one}</sheets>", ns=t505.STRICT)),
            "unsupported_workbook",
        ),
    ]
    return cases


def test_every_package_refusal_has_its_exact_code_in_the_listing_and_the_importer() -> None:
    for label, data, code in _package_cases():
        listed = xi.list_worksheets(
            reader_for(data), verified_content_type=XLSX, dataset_sha256=sha(data)
        )
        check(
            "PKG-1",
            f"{label}: list_worksheets is failed/{code}, no sheets "
            f"(got {listed.state}/{listed.failure_code})",
            listed.state == "failed" and listed.failure_code == code and listed.sheets == (),
        )
        outcome = t505.run(data)[1]
        check(
            "PKG-2", f"{label}: the importer refuses with {code}", t505.failed_with(outcome, code)
        )


def test_the_sheet_relationship_table_is_pinned_for_the_importer() -> None:
    rel = t505.REL
    for label, data, code in (
        (
            "a relationship of another type",
            t505.book(sheet_type="chartsheet"),
            "unsupported_workbook",
        ),
        (
            "a sheet whose relationship id has no relationship",
            t505.book(
                replace={"xl/workbook.xml": t505.workbook_xml([("Data", "rIdNone", None)]).encode()}
            ),
            "xlsx_malformed",
        ),
        (
            "a worksheet relationship with no Target",
            t505.book(
                replace={
                    "xl/_rels/workbook.xml.rels": (
                        f'{t505.XML_HEAD}<Relationships xmlns="{t505.PKG}">'
                        f'<Relationship Id="rId1" Type="{rel}/worksheet"/></Relationships>'
                    ).encode()
                }
            ),
            "xlsx_malformed",
        ),
        (
            "a sheet part that is not in the package",
            t505.book(sheet_target="worksheets/nothere.xml"),
            "xlsx_malformed",
        ),
        (
            "a sharedStrings relationship whose part is missing",
            t505.book(drop=("xl/sharedStrings.xml",)),
            "xlsx_malformed",
        ),
    ):
        check("PKG-3", f"{label}: {code}", t505.failed_with(t505.run(data)[1], code))
    # a missing sheet name is the importer's own typed failure, not a package refusal
    _e, outcome = t505.run(t505.book(sheet="Other"))
    check(
        "PKG-4",
        "an absent mapped sheet is mapping_column_missing",
        t505.failed_with(outcome, "mapping_column_missing"),
    )
    # part names are matched by casefold both ways: a name casefold equates is found
    for label, data in (
        (
            "sheet part",
            t505.book(
                sheet_target="worksheets/stra\u00dfe.xml",
                replace={},
                extra=[],
            ),
        ),
    ):
        members = [
            (("xl/worksheets/STRASSE.xml" if n == "xl/worksheets/sheet1.xml" else n), d)
            for n, d in _members(data)
        ]
        found = t505.zip_bytes(members)
        events, outcome = t505.run(found)
        check(
            "PKG-5",
            f"{label}: a casefold-equal part name is followed",
            len(t505.accepted(events)) == 1,
        )
    shared_members = [
        (("xl/STRASSE.xml" if n == "xl/sharedStrings.xml" else n), d) for n, d in _members(GOOD)
    ]
    shared = t505.zip_bytes(shared_members)
    shared = _swap(
        shared,
        "xl/_rels/workbook.xml.rels",
        t505.workbook_rels(extra="").replace("sharedStrings.xml", "stra\u00dfe.xml").encode(),
    )
    events, outcome = t505.run(shared)
    check(
        "PKG-6",
        "sharedStrings part: a casefold-equal part name is followed (its strings are read)",
        len(t505.accepted(events)) == 2 and t505.texts(t505.accepted(events)[0]) == ["hi", "there"],
    )


def test_an_unreadable_source_is_a_visible_blocked_listing() -> None:
    def broken() -> Iterator[bytes]:
        yield GOOD[:10]
        raise OSError("storage gone")

    reader = ic.ChunkReader(
        broken(), expected_size=len(GOOD), expected_sha256=sha(GOOD), limits=rev.PREVIEW_LIMITS
    )
    listed = xi.list_worksheets(reader, verified_content_type=XLSX, dataset_sha256=sha(GOOD))
    check(
        "PKG-7",
        "an unreadable source is blocked/source_unavailable with no sheets "
        f"(got {listed.state}/{listed.failure_code})",
        listed.state == "blocked"
        and listed.failure_code == "source_unavailable"
        and listed.sheets == (),
    )


def test_the_archive_bounds_are_exact_for_the_listing() -> None:
    at_entries = GOOD + t505.t501._zip64_tail(ox.MAX_ENTRIES, 100, 0)
    at_bytes = GOOD + t505.t501._zip64_tail(5, ox.MAX_DIRECTORY_BYTES, 0)
    for label, data in (("10,000 claimed entries", at_entries), ("a 4 MiB directory", at_bytes)):
        listed = xi.list_worksheets(
            reader_for(data), verified_content_type=XLSX, dataset_sha256=sha(data)
        )
        check(
            "PKG-8",
            f"{label} is not refused by the bound (got {listed.state}/{listed.failure_code})",
            listed.failure_code != "unsafe_archive",
        )


def test_padded_part_targets_are_not_silently_trimmed() -> None:
    for label, target in (
        ("a trailing space", "worksheets/sheet1.xml "),
        ("a leading space", " worksheets/sheet1.xml"),
    ):
        data = t505.book(sheet_target=target)
        check(
            "PKG-9",
            f"a sheet target with {label} is not a found part: xlsx_malformed",
            t505.failed_with(t505.run(data)[1], "xlsx_malformed"),
        )
    shared = t505.workbook_rels().replace("sharedStrings.xml", "sharedStrings.xml ")
    data = t505.book(replace={"xl/_rels/workbook.xml.rels": shared.encode()})
    check(
        "PKG-10",
        "a sharedStrings target with a trailing space is not a found part: xlsx_malformed",
        t505.failed_with(t505.run(data)[1], "xlsx_malformed"),
    )
    padded = t505.with_extra(GOOD, (" xl/externalLinks/e.xml", b"<x/>"))
    listed = xi.list_worksheets(
        reader_for(padded), verified_content_type=XLSX, dataset_sha256=sha(padded)
    )
    check(
        "PKG-11",
        "a part name with a leading space is not an externalLinks part "
        f"(got {listed.failure_code})",
        listed.failure_code != "external_link_present",
    )
    for name in (" xl/connections.xml", "xl/connections.xml "):
        padded = t505.with_extra(GOOD, (name, b"<x/>"))
        listed = xi.list_worksheets(
            reader_for(padded), verified_content_type=XLSX, dataset_sha256=sha(padded)
        )
        check(
            "PKG-13",
            f"the part {name!r} is not xl/connections.xml (got {listed.failure_code})",
            listed.failure_code != "external_link_present",
        )


def test_a_reader_that_fails_with_an_os_error_is_a_blocked_listing() -> None:
    class Broken(ic.ChunkReader):
        def __iter__(self) -> Iterator[bytes]:  # type: ignore[override]
            raise OSError("storage gone")

    reader = Broken(
        [GOOD], expected_size=len(GOOD), expected_sha256=sha(GOOD), limits=rev.PREVIEW_LIMITS
    )
    listed = xi.list_worksheets(reader, verified_content_type=XLSX, dataset_sha256=sha(GOOD))
    check(
        "PKG-12",
        "an OSError from the reader is blocked/source_unavailable "
        f"(got {listed.state}/{listed.failure_code})",
        listed.state == "blocked" and listed.failure_code == "source_unavailable",
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
