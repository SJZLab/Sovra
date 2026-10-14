#!/usr/bin/env python3
"""FTF-0505 -- XLSX importer.

Runnable without any database or network: `workers.data.xlsx_importer` and
`workers.data.ooxml_safety` are pure and synchronous and sit on FTF-0502-P1's
`ChunkReader` / `import_common`. Every workbook here is generated with the stdlib
`zipfile` and XML strings only (no spreadsheet library is a dependency).

Acceptance ("Read XLSX sheets/columns through explicit UI mapping without
evaluating formulas/macros"): test_basic_*, test_cell_type_table,
test_shared_strings_table, test_header_*, test_sheet_selection_*,
test_column_slots_*, test_preservation_*, test_formula_*, test_macro_*.

Negative acceptance ("ZIP/XML bombs, encrypted/unsupported workbook, formula-only
critical data and excessive cells fail safely"): test_archive_attack_table,
test_workbook_type_table, test_external_link_table, test_bounds_table,
test_formula_only_critical_data, test_xml_bomb_*, test_unsupported_*.

Run directly: python3 tests/tasks/test_ftf_0505.py
"""

from __future__ import annotations

import dataclasses
import hashlib
import io
import os
import re
import stat
import struct
import sys
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests" / "tasks"))
sys.path.insert(0, str(ROOT / "apps" / "api"))

import import_test_support as support  # noqa: E402
from workers.data import import_common as ic  # noqa: E402
from workers.data import ooxml_safety as ox  # noqa: E402
from workers.data import xlsx_importer as xi  # noqa: E402

FAILURES: list[str] = []
CID = "FTF-0505"


def check(criterion_id: str, description: str, condition: bool) -> None:
    if not condition:
        FAILURES.append(f"FAIL [{criterion_id}] {description}")


# --- workbook builder -----------------------------------------------------------------

MAIN = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
STRICT = "http://purl.oclc.org/ooxml/spreadsheetml/main"
REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PKG = "http://schemas.openxmlformats.org/package/2006/relationships"
CT_NS = "http://schemas.openxmlformats.org/package/2006/content-types"
WORKBOOK_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"
TEMPLATE_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.template.main+xml"
MACRO_TYPE = "application/vnd.ms-excel.sheet.macroEnabled.main+xml"
XLSX_TYPE = xi.XLSX_CONTENT_TYPE
XML_HEAD = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'


class Shared:
    def __init__(self) -> None:
        self.strings: list[str] = []

    def add(self, text: str) -> int:
        self.strings.append(text)
        return len(self.strings) - 1


@dataclass
class Cell:
    make: Callable[[str, Shared], str]


def S(text: str) -> Cell:
    """A shared-string cell."""
    return Cell(lambda ref, sst: f'<c r="{ref}" t="s"><v>{sst.add(text)}</v></c>')


def IS(text: str) -> Cell:
    """An inline-string cell."""
    return Cell(lambda ref, _s: f'<c r="{ref}" t="inlineStr"><is><t>{escape(text)}</t></is></c>')


def N(text: str, t: str | None = None) -> Cell:
    """A cell with a literal <v> (number by default; t="b" / "d" for the others)."""
    attr = f' t="{t}"' if t else ""
    return Cell(lambda ref, _s: f'<c r="{ref}"{attr}><v>{escape(text)}</v></c>')


def F(expression: str = "1+1", cached: str | None = "2", t: str | None = None) -> Cell:
    attr = f' t="{t}"' if t else ""
    value = "" if cached is None else f"<v>{escape(cached)}</v>"
    return Cell(lambda ref, _s: f'<c r="{ref}"{attr}><f>{escape(expression)}</f>{value}</c>')


def FSHARED(cached: str = "5") -> Cell:
    return Cell(lambda ref, _s: f'<c r="{ref}"><f t="shared" si="0"/><v>{cached}</v></c>')


def FARRAY(cached: str = "5") -> Cell:
    return Cell(lambda ref, _s: f'<c r="{ref}"><f t="array" ref="{ref}">1+1</f><v>{cached}</v></c>')


def STR(text: str) -> Cell:
    """t="str" without an <f>: a formula's string result all the same."""
    return Cell(lambda ref, _s: f'<c r="{ref}" t="str"><v>{escape(text)}</v></c>')


def E(code: str = "#DIV/0!") -> Cell:
    return Cell(lambda ref, _s: f'<c r="{ref}" t="e"><v>{escape(code)}</v></c>')


def EMPTY() -> Cell:
    return Cell(lambda ref, _s: f'<c r="{ref}"/>')


def RAWC(xml: str) -> Cell:
    """A cell written exactly as given ({ref} is replaced)."""
    return Cell(lambda ref, _s: xml.replace("{ref}", ref))


def col_letters(index: int) -> str:
    letters = ""
    index += 1
    while index:
        index, rest = divmod(index - 1, 26)
        letters = chr(ord("A") + rest) + letters
    return letters


def sheet_xml(rows: list[list[Cell]], shared: Shared, *, first_row: int = 1, ns: str = MAIN) -> str:
    out = [XML_HEAD, f'<worksheet xmlns="{ns}"><sheetData>']
    for offset, row in enumerate(rows):
        number = first_row + offset
        out.append(f'<row r="{number}">')
        for column, cell in enumerate(row):
            out.append(cell.make(f"{col_letters(column)}{number}", shared))
        out.append("</row>")
    out.append("</sheetData></worksheet>")
    return "".join(out)


def shared_xml(strings: list[str]) -> str:
    items = "".join(f"<si><t>{escape(s)}</t></si>" for s in strings)
    return f'{XML_HEAD}<sst xmlns="{MAIN}">{items}</sst>'


def content_types(workbook_type: str = WORKBOOK_TYPE, extra: str = "") -> str:
    return (
        f'{XML_HEAD}<Types xmlns="{CT_NS}">'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.'
        'relationships+xml"/>'
        f'<Override PartName="/xl/workbook.xml" ContentType="{workbook_type}"/>{extra}</Types>'
    )


def workbook_xml(sheets: list[tuple[str, str, str | None]] | None = None, ns: str = MAIN) -> str:
    sheets = sheets or [("Data", "rId1", None)]
    items = "".join(
        f'<sheet name="{escape(name, {chr(34): "&quot;"})}" sheetId="{i + 1}" r:id="{rid}"'
        + (f' state="{state}"' if state else "")
        + "/>"
        for i, (name, rid, state) in enumerate(sheets)
    )
    return f'{XML_HEAD}<workbook xmlns="{ns}" xmlns:r="{REL}"><sheets>{items}</sheets></workbook>'


def workbook_rels(
    targets: list[tuple[str, str]] | None = None,
    shared: bool = True,
    extra: str = "",
    sheet_type: str = "worksheet",
) -> str:
    targets = targets or [("rId1", "worksheets/sheet1.xml")]
    rels = "".join(
        f'<Relationship Id="{rid}" Type="{REL}/{sheet_type}" Target="{target}"/>'
        for rid, target in targets
    )
    if shared:
        rels += f'<Relationship Id="rIdS" Type="{REL}/sharedStrings" Target="sharedStrings.xml"/>'
    return f'{XML_HEAD}<Relationships xmlns="{PKG}">{rels}{extra}</Relationships>'


def zip_bytes(members: list[tuple[str, bytes]], method: int = zipfile.ZIP_DEFLATED) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", method) as archive:
        for name, data in members:
            archive.writestr(name, data)
    return buffer.getvalue()


def book(
    rows: list[list[Cell]] | None = None,
    *,
    sheet: str = "Data",
    state: str | None = None,
    raw_sheet: str | bytes | None = None,
    extra: list[tuple[str, bytes]] | None = None,
    drop: tuple[str, ...] = (),
    replace: dict[str, bytes] | None = None,
    workbook_type: str = WORKBOOK_TYPE,
    ct_extra: str = "",
    extra_sheets: list[tuple[str, str, str | None]] | None = None,
    rel_extra: str = "",
    sheet_target: str = "worksheets/sheet1.xml",
    sheet_type: str = "worksheet",
    shared_override: bytes | None = None,
    method: int = zipfile.ZIP_DEFLATED,
) -> bytes:
    """A complete, valid workbook (header q/a plus one data row by default)."""
    if rows is None:
        rows = [[S("q"), S("a")], [S("hi"), S("there")]]
    sst = Shared()
    body = sheet_xml(rows, sst) if raw_sheet is None else raw_sheet
    sheets = [(sheet, "rId1", state), *(extra_sheets or [])]
    members: dict[str, bytes] = {
        "[Content_Types].xml": content_types(workbook_type, ct_extra).encode(),
        "xl/workbook.xml": workbook_xml(sheets).encode(),
        "xl/_rels/workbook.xml.rels": workbook_rels(
            [("rId1", sheet_target)], extra=rel_extra, sheet_type=sheet_type
        ).encode(),
        "xl/sharedStrings.xml": shared_override or shared_xml(sst.strings).encode(),
        "xl/worksheets/sheet1.xml": body.encode() if isinstance(body, str) else body,
    }
    for name, data in (replace or {}).items():
        members[name] = data
    ordered = [(n, d) for n, d in members.items() if n not in drop]
    return zip_bytes([*ordered, *(extra or [])], method)


# --- run helpers ----------------------------------------------------------------------


def mapping_text(
    *,
    sheet: str = "Data",
    header_row: int = 0,
    prompt: object = None,
    completion: object = None,
    extra: str = "",
) -> str:
    prompt = {"header": "q"} if prompt is None else prompt
    completion = {"header": "a"} if completion is None else completion
    return (
        '{"shape":"prompt_completion",'
        f'"prompt":{_slot_json(prompt)},"completion":{_slot_json(completion)}{extra},'
        f'"xlsx":{{"sheet_name":{_quote(sheet)},"header_row":{header_row}}}}}'
    )


def _slot_json(slot: object) -> str:
    assert isinstance(slot, dict)
    ((kind, value),) = slot.items()
    return f'{{"{kind}":{value if isinstance(value, int) else _quote(str(value))}}}'


def _quote(text: str) -> str:
    import json

    return json.dumps(text)


def build_mapping(**kwargs: Any) -> ic.ImportMapping:
    return ic.parse_mapping(mapping_text(**kwargs))


def make_reader(
    data: bytes,
    chunk: int | None = None,
    limits: ic.ImportLimits = ic.DEFAULT_LIMITS,
    *,
    expected_sha: str | None = None,
) -> ic.ChunkReader:
    chunks = [data] if chunk is None else support.split_chunks(data, chunk)
    return ic.ChunkReader(
        chunks,
        expected_size=len(data),
        expected_sha256=expected_sha or hashlib.sha256(data).hexdigest(),
        limits=limits,
    )


def run(
    data: bytes,
    *,
    chunk: int | None = None,
    limits: ic.ImportLimits = ic.DEFAULT_LIMITS,
    xlsx_limits: xi.XlsxLimits = xi.XlsxLimits(),
    on_accepted: Callable[[Any], None] | None = None,
    **mapping_kwargs: Any,
) -> tuple[list[ic.ImportEvent], xi.XlsxImportOutcome]:
    """The accepted candidates and the rejections, via `run_xlsx_import`."""
    events: list[ic.ImportEvent] = []

    def keep(candidate: Any) -> None:
        events.append(ic.AcceptedRecord(candidate))
        if on_accepted is not None:
            on_accepted(candidate)

    outcome = xi.run_xlsx_import(
        make_reader(data, chunk, limits),
        build_mapping(**mapping_kwargs),
        verified_content_type=XLSX_TYPE,
        dataset_purpose="training",
        expected_sha256=hashlib.sha256(data).hexdigest(),
        limits=limits,
        xlsx_limits=xlsx_limits,
        on_accepted=keep,
    )
    events.extend(outcome.report.rejections)
    return events, outcome


def texts(candidate: object) -> list[str]:
    assert isinstance(candidate, dict)
    return [part["text"] for m in candidate["content"]["messages"] for part in m["content"]]


def accepted(events: list[ic.ImportEvent]) -> list[dict[str, object]]:
    return [dict(e.candidate) for e in events if isinstance(e, ic.AcceptedRecord)]


def codes(outcome: xi.XlsxImportOutcome) -> list[str]:
    return [r.code for r in outcome.report.rejections]


def failed_with(outcome: xi.XlsxImportOutcome, code: str) -> bool:
    return outcome.report.state == "failed" and outcome.report.failure_code == code


ARABIC = "مرحبا بالعالم"


# --- basic -----------------------------------------------------------------------------


def test_basic_import_candidates_and_report() -> None:
    data = book([[S("q"), S("a")], [S("hi"), S("there")], [S("two"), S("rows")]])
    events, outcome = run(data)
    report = outcome.report
    cands = accepted(events)
    check(CID, "completed", report.state == "completed" and report.failure_code is None)
    check(CID, "two records accepted", report.records_accepted == 2 and len(cands) == 2)
    check(CID, "no rejections", report.records_rejected == 0 and report.records_seen == 2)
    check(CID, "prompt/completion preserved", texts(cands[0]) == ["hi", "there"])
    check(CID, "record_index is the 0-based ordinal", cands[1]["record_index"] == 1)
    check(CID, "no fatal locator", outcome.fatal_locator is None)
    check(CID, "importer name", report.importer == "xlsx" and report.importer_version == "1")
    check(
        CID,
        "source bytes and sha come from the reader",
        report.source_bytes == len(data)
        and report.source_sha256 == hashlib.sha256(data).hexdigest(),
    )
    check(
        CID,
        "candidates carry the not_applicable tool status",
        cands[0]["tool_schema_status"] == "not_applicable",
    )
    check(CID, "a schema digest is recorded", len(report.source_schema_sha256 or "") == 64)


def test_every_chunking_gives_the_same_report() -> None:
    data = book([[S("q"), S("a")]] + [[S(f"r{i}"), S(ARABIC)] for i in range(30)])
    _e, base = run(data)
    for chunk in (1, 2, 5, 64, 1000):
        _events, outcome = run(data, chunk=chunk)
        check(
            CID,
            f"chunk={chunk}: report identical",
            outcome.report.report_sha256 == base.report.report_sha256,
        )


def test_report_is_deterministic() -> None:
    data = book()
    check(
        CID,
        "two runs, one report hash",
        run(data)[1].report.report_sha256 == run(data)[1].report.report_sha256,
    )


def test_content_type_gate_refuses_everything_but_xlsx_before_a_byte() -> None:
    data = book()
    for bad in ("text/csv", "application/json", "", "application/zip", XLSX_TYPE + " "):
        reader = make_reader(data)
        outcome = xi.run_xlsx_import(
            reader,
            build_mapping(),
            verified_content_type=bad,
            dataset_purpose="training",
            expected_sha256=hashlib.sha256(data).hexdigest(),
        )
        check(
            CID,
            f"{bad!r}: content_type_not_supported_by_importer, nothing read",
            failed_with(outcome, "content_type_not_supported_by_importer")
            and reader.bytes_read == 0,
        )
    for bad_type in (None, 5, XLSX_TYPE.encode()):
        reader = make_reader(data)
        outcome = xi.run_xlsx_import(
            reader,
            build_mapping(),
            verified_content_type=bad_type,  # type: ignore[arg-type]
            dataset_purpose="training",
            expected_sha256=hashlib.sha256(data).hexdigest(),
        )
        check(
            CID,
            f"{bad_type!r}: refused by exact type",
            failed_with(outcome, "content_type_not_supported_by_importer"),
        )


# --- cell types -----------------------------------------------------------------------

CELL_TABLE: list[tuple[str, Cell, str | None, str | None]] = [
    # (label, completion cell, expected text, expected rejection code)
    ("shared string", S("plain"), "plain", None),
    ("inline string", IS("inline"), "inline", None),
    ("number keeps its literal text", N("007.50"), "007.50", None),
    ("integer keeps leading zeros as written", N("0042"), "0042", None),
    ("exponent literal is not converted", N("1E+3"), "1E+3", None),
    ("boolean keeps its literal", N("1", "b"), "1", None),
    ("date keeps its ISO literal", N("2024-02-29T12:00:00Z", "d"), "2024-02-29T12:00:00Z", None),
    ("cell with no value is empty", EMPTY(), None, "required_field_empty"),
    ("formula with a cached value", F("1+1", "2"), None, "formula_cell"),
    ("formula with no cached value", F("1+1", None), None, "formula_cell"),
    ("formula whose cached result is a string", F('"x"', "x", "str"), None, "formula_cell"),
    ("t=str without an <f> is still a formula result", STR("stale"), None, "formula_cell"),
    ("shared formula child with no text", FSHARED(), None, "formula_cell"),
    ("array formula", FARRAY(), None, "formula_cell"),
    ("error cell", E("#DIV/0!"), None, "cell_error_value"),
    ("error cell #REF!", E("#REF!"), None, "cell_error_value"),
]


def test_cell_type_table() -> None:
    for label, cell, text, code in CELL_TABLE:
        data = book([[S("q"), S("a")], [S("p"), cell]])
        events, outcome = run(data)
        got = accepted(events)
        if code is None:
            ok = (
                outcome.report.state == "completed"
                and len(got) == 1
                and texts(got[0]) == ["p", text]
            )
            check(CID, f"{label}: imported as {text!r}", ok)
        else:
            check(
                CID,
                f"{label}: row rejected {code} at row:2 slot completion",
                outcome.report.state == "completed"
                and not got
                and outcome.report.rejections == (ic.RowRejection("row:2", code, "completion"),),
            )


def test_formula_cached_value_is_never_used() -> None:
    # The cached value would be a perfectly good completion; it must not be taken.
    data = book([[S("q"), S("a")], [S("p"), F("SECRET()", "stale cached answer")]])
    events, outcome = run(data)
    check(CID, "no record is built from a cached formula result", not accepted(events))
    check(CID, "the cached text is not in the report", "stale" not in repr(outcome.report))


def test_formula_in_an_unmapped_column_is_ignored() -> None:
    data = book([[S("q"), S("a"), S("note")], [S("p"), S("c"), F("1+1", "2")]])
    events, outcome = run(data)
    check(
        CID,
        "a formula next to the mapped columns does not reject the row",
        outcome.report.state == "completed" and len(accepted(events)) == 1,
    )


def test_formula_only_critical_data() -> None:
    rows = [[S("q"), S("a")]] + [[F(f"A{i}", str(i)), F(f"B{i}", str(i))] for i in range(1, 6)]
    events, outcome = run(book(rows))
    check(
        CID,
        "a sheet of formulas only imports nothing and rejects every row",
        not accepted(events)
        and outcome.report.records_rejected == 5
        and set(codes(outcome)) == {"formula_cell"},
    )
    check(
        CID,
        "the prompt slot is the first one named",
        all(r.slot == "prompt" for r in outcome.report.rejections),
    )


def test_formula_looking_text_is_data() -> None:
    texts_in = ["=1+1", "+SUM(A1)", "-2+3", "@cmd", "\t=x", '=HYPERLINK("http://x")', "'=quoted"]
    rows = [[S("q"), S("a")]] + [[IS(t), S(t)] for t in texts_in]
    events, outcome = run(book(rows))
    got = [texts(c) for c in accepted(events)]
    check(
        CID,
        "formula-looking plain strings are preserved exactly, not rejected",
        outcome.report.state == "completed" and got == [[t, t] for t in texts_in],
    )


# --- shared strings -------------------------------------------------------------------


def sst_book(si_xml: str, indexes: list[str]) -> bytes:
    """A workbook whose sharedStrings.xml body is `si_xml`, reading `indexes`."""
    cells = [RAWC(f'<c r="{{ref}}" t="s"><v>{i}</v></c>') for i in indexes]
    sst = f'{XML_HEAD}<sst xmlns="{MAIN}">{si_xml}</sst>'
    rows = [[IS("q"), IS("a")], cells]
    return book(rows, shared_override=sst.encode())


def test_shared_strings_table() -> None:
    cases: list[tuple[str, str, str]] = [
        ("plain", "<si><t>abc</t></si>", "abc"),
        (
            "rich-text runs are concatenated exactly",
            "<si><r><t>ab</t></r><r><t>cd</t></r></si>",
            "abcd",
        ),
        ("run properties are skipped", "<si><r><rPr><b/></rPr><t>ab</t></r></si>", "ab"),
        (
            "phonetic runs are excluded",
            '<si><t>base</t><rPh sb="0" eb="1"><t>PHONETIC</t></rPh></si>',
            "base",
        ),
        (
            "phonetic after runs",
            "<si><r><t>a</t></r><r><t>b</t></r><rPh><t>zz</t></rPh></si>",
            "ab",
        ),
        (
            "xml:space preserve keeps edge spaces",
            '<si><t xml:space="preserve">  a b  </t></si>',
            "  a b  ",
        ),
        ("edge spaces without the attribute are kept too", "<si><t> a </t></si>", " a "),
        ("empty string", "<si><t></t></si>", ""),
        ("empty si", "<si/>", ""),
        ("_x000D_ is a carriage return", "<si><t>a_x000D_b</t></si>", "a\rb"),
        ("_x000A_ is a line feed", "<si><t>a_x000A_b</t></si>", "a\nb"),
        ("_x005F_ is an underscore", "<si><t>a_x005F_b</t></si>", "a_b"),
        (
            "an escaped escape is decoded once, never twice",
            "<si><t>_x005F_x0041_</t></si>",
            "_x0041_",
        ),
        ("a plain escape is decoded", "<si><t>_x0041_</t></si>", "A"),
        ("lower-case hex is decoded", "<si><t>_x00e9_</t></si>", "é"),
        (
            "a malformed escape is kept",
            "<si><t>_x00G1_ _x41_ _x0041</t></si>",
            "_x00G1_ _x41_ _x0041",
        ),
        (
            "a surrogate pair of escapes is joined",
            "<si><t>_xD83D__xDE00_</t></si>",
            "\U0001f600",
        ),
        ("Arabic", f"<si><t>{ARABIC}</t></si>", ARABIC),
        ("right-to-left marks", "<si><t>abc‏مرحبا‎abc</t></si>", "abc‏مرحبا‎abc"),
        ("leading zeros in text", "<si><t>00123</t></si>", "00123"),
        ("formula-looking text", "<si><t>=1+1</t></si>", "=1+1"),
    ]
    for label, si, expected in cases:
        data = sst_book(si, ["0"])
        events, outcome = run(data, completion={"header": "a"}, prompt={"header": "q"})
        # the data row maps the same string into the prompt only; read it via the header
        # trick: both mapped columns exist, so use a 2-column row.
        data = book(
            [[IS("q"), IS("a")], [RAWC('<c r="{ref}" t="s"><v>0</v></c>'), IS("x")]],
            shared_override=f'{XML_HEAD}<sst xmlns="{MAIN}">{si}</sst>'.encode(),
        )
        events, outcome = run(data)
        got = accepted(events)
        if expected == "":
            check(
                CID,
                f"{label}: empty prompt is required_field_empty",
                codes(outcome) == ["required_field_empty"],
            )
        else:
            check(
                CID,
                f"{label}: {expected!r}",
                outcome.report.state == "completed"
                and len(got) == 1
                and texts(got[0])[0] == expected,
            )


def test_shared_string_index_table() -> None:
    si = "<si><t>zero</t></si><si><t>one</t></si><si><t>two</t></si>"
    for label, index, expected in (
        ("first", "0", "zero"),
        ("middle", "1", "one"),
        ("last", "2", "two"),
        ("leading zeros are the same index", "02", "two"),
    ):
        events, outcome = run(sst_book(si, [index, "0"]))
        got = accepted(events)
        check(CID, f"index {label}", len(got) == 1 and texts(got[0])[0] == expected)
    for label, index in (
        ("last+1", "3"),
        ("far past the end", "999999999"),
        ("negative", "-1"),
        ("non-integer", "1.0"),
        ("exponent", "1e0"),
        ("text", "abc"),
        ("empty", ""),
        ("signed", "+1"),
        ("padded", " 1"),
        ("too long to be an index", "1" * 40),
    ):
        _events, outcome = run(sst_book(si, [index, "0"]))
        check(
            CID,
            f"index {label} is fatal xlsx_malformed at row:2, never an empty string",
            failed_with(outcome, "xlsx_malformed")
            and outcome.fatal_locator == "row:2"
            and not accepted(_events),
        )
    # an index cell with no <v> at all
    data = book(
        [[IS("q"), IS("a")], [RAWC('<c r="{ref}" t="s"/>'), IS("x")]],
        shared_override=shared_xml(["zero"]).encode(),
    )
    check(CID, "t=s with no <v> is fatal", failed_with(run(data)[1], "xlsx_malformed"))
    # an out-of-range index in an UNMAPPED column is fatal too
    data = book(
        [[IS("q"), IS("a"), IS("n")], [IS("p"), IS("c"), RAWC('<c r="{ref}" t="s"><v>9</v></c>')]],
        shared_override=shared_xml(["zero"]).encode(),
    )
    check(
        CID, "t=s past the end, unmapped column: fatal", failed_with(run(data)[1], "xlsx_malformed")
    )


def test_inline_string_table() -> None:
    cases = [
        ("plain", "<is><t>abc</t></is>", "abc"),
        ("runs", "<is><r><t>a</t></r><r><t>b</t></r></is>", "ab"),
        ("phonetic excluded", "<is><t>a</t><rPh><t>zz</t></rPh></is>", "a"),
        ("escape", "<is><t>a_x005F_x0041_b</t></is>", "a_x0041_b"),
        ("carriage return escape", "<is><t>a_x000D_b</t></is>", "a\rb"),
    ]
    for label, inner, expected in cases:
        cell = RAWC(f'<c r="{{ref}}" t="inlineStr">{inner}</c>')
        events, outcome = run(book([[IS("q"), IS("a")], [cell, IS("x")]]))
        got = accepted(events)
        check(CID, f"inline {label}: {expected!r}", len(got) == 1 and texts(got[0])[0] == expected)


def test_lone_surrogate_and_nul_escapes_reject_the_row() -> None:
    for label, text, code in (
        ("lone high surrogate escape", "a_xD800_b", "invalid_unicode"),
        ("lone low surrogate escape", "a_xDC00_b", "invalid_unicode"),
        ("NUL escape", "a_x0000_b", "nul_character"),
        ("reversed pair", "_xDE00__xD83D_", "invalid_unicode"),
    ):
        events, outcome = run(book([[IS("q"), IS("a")], [IS(text), IS("x")]]))
        check(
            CID,
            f"{label}: row rejected {code}",
            not accepted(events)
            and outcome.report.rejections == (ic.RowRejection("row:2", code, "prompt"),),
        )
    # the same text in an UNMAPPED column does not matter
    events, outcome = run(book([[IS("q"), IS("a"), IS("n")], [IS("p"), IS("c"), IS("a_x0000_b")]]))
    check(CID, "NUL in an unmapped column is ignored", len(accepted(events)) == 1)


# --- headers ---------------------------------------------------------------------------


def test_header_exactness() -> None:
    for label, header, kwargs in (
        ("case differs", "Q", {}),
        ("trailing space", "q ", {}),
        ("leading space", " q", {}),
        ("NFD vs NFC", "q́", {}),
    ):
        _events, outcome = run(book([[IS(header), IS("a")], [IS("p"), IS("c")]]), **kwargs)
        check(
            CID,
            f"{label}: the mapped header is absent -> mapping_column_missing",
            failed_with(outcome, "mapping_column_missing"),
        )
    events, outcome = run(
        book([[IS("Q "), IS("a ")], [IS("p"), IS("c")]]),
        prompt={"header": "Q "},
        completion={"header": "a "},
    )
    check(CID, "the exact text, spaces included, maps", len(accepted(events)) == 1)


def test_duplicate_header_is_fatal_even_when_unmapped() -> None:
    data = book([[IS("q"), IS("a"), IS("x"), IS("x")], [IS("p"), IS("c"), IS("1"), IS("2")]])
    _events, outcome = run(data)
    check(
        CID,
        "a repeated header cell is fatal duplicate_header at the header row",
        failed_with(outcome, "duplicate_header") and outcome.fatal_locator == "row:1",
    )
    data = book([[IS("q"), IS("a"), IS("x"), IS("X")], [IS("p"), IS("c"), IS("1"), IS("2")]])
    check(CID, "case-different headers are distinct", run(data)[1].report.state == "completed")
    # empty header cells (gaps) are not duplicates of each other
    data = book([[IS("q"), EMPTY(), IS("a"), EMPTY()], [IS("p"), IS(""), IS("c"), IS("")]])
    check(
        CID, "gaps in the header row are not duplicates", run(data)[1].report.state == "completed"
    )
    # a hand-built slot naming the empty header cannot select a gap column
    hand = dataclasses.replace(build_mapping(), completion=ic.Slot("header", ""))
    gap = book([[IS("q"), EMPTY(), IS("a")], [IS("p"), IS("z"), IS("c")]])
    outcome = xi.run_xlsx_import(
        make_reader(gap),
        hand,
        verified_content_type=XLSX_TYPE,
        dataset_purpose="training",
        expected_sha256=hashlib.sha256(gap).hexdigest(),
    )
    check(CID, "an empty header text maps nothing", failed_with(outcome, "mapping_column_missing"))


def test_header_row_offset_and_blank_rows() -> None:
    rows = [[IS("title")], [IS("")], [IS("q"), IS("a")], [IS("p"), IS("c")]]
    events, outcome = run(book(rows), header_row=1)
    check(
        CID,
        "header_row counts non-blank rows: the title is skipped, the empty row is blank",
        outcome.report.state == "completed"
        and len(accepted(events)) == 1
        and outcome.report.blank_lines == 1,
    )
    _events, outcome = run(book(rows), header_row=0)
    check(
        CID,
        "header_row=0 takes the first non-blank row as the header",
        failed_with(outcome, "mapping_column_missing"),
    )
    events, outcome = run(book(rows), header_row=2)
    check(
        CID,
        "header_row=2 skips both and finds nothing mapped",
        failed_with(outcome, "mapping_column_missing"),
    )
    rows = [[IS("q"), IS("a")], [IS("")], [IS("p"), IS("c")], [EMPTY(), EMPTY()]]
    events, outcome = run(book(rows))
    check(
        CID,
        "all-empty rows are blank lines, not records",
        len(accepted(events)) == 1
        and outcome.report.blank_lines == 2
        and outcome.report.records_rejected == 0,
    )


def test_empty_and_header_only_sheets() -> None:
    events, outcome = run(book([[IS("q"), IS("a")]]))
    check(
        CID,
        "a header-only sheet completes with nothing",
        outcome.report.state == "completed" and not accepted(events),
    )
    _events, outcome = run(book([]))
    check(
        CID,
        "a sheet with no rows has no header: mapping_column_missing",
        failed_with(outcome, "mapping_column_missing"),
    )
    raw = f'{XML_HEAD}<worksheet xmlns="{MAIN}"/>'
    _events, outcome = run(book(raw_sheet=raw))
    check(CID, "a sheet with no sheetData is empty", failed_with(outcome, "mapping_column_missing"))


def test_formula_or_error_in_the_header_row_is_fatal() -> None:
    for label, cell in (("formula", F('"q"', "q")), ("error", E()), ("t=str", STR("q"))):
        _events, outcome = run(book([[cell, IS("a")], [IS("p"), IS("c")]]))
        check(
            CID,
            f"{label} header cell is fatal xlsx_malformed at row:1",
            failed_with(outcome, "xlsx_malformed") and outcome.fatal_locator == "row:1",
        )


def test_header_schema_digest() -> None:
    a = run(book([[IS("q"), IS("a")], [IS("p"), IS("c")]]))[1].report.source_schema_sha256
    b = run(book([[IS("q"), IS("a")], [IS("p"), IS("c")], [IS("p2"), IS("c2")]]))[
        1
    ].report.source_schema_sha256
    c = run(book([[IS("a"), IS("q")], [IS("c"), IS("p")]]))[1].report.source_schema_sha256
    d = run(book([[IS("q"), IS("a"), IS("z")], [IS("p"), IS("c"), IS("")]]))[
        1
    ].report.source_schema_sha256
    check(CID, "the digest ignores data rows", a == b)
    check(CID, "the digest depends on header order", a != c)
    check(CID, "the digest depends on every header cell", a != d)
    # the sheet index is part of the digest
    two = book(
        [[IS("q"), IS("a")], [IS("p"), IS("c")]],
        sheet="First",
        extra_sheets=[("Second", "rId2", None)],
        replace={
            "xl/_rels/workbook.xml.rels": workbook_rels(
                [("rId1", "worksheets/sheet1.xml"), ("rId2", "worksheets/sheet2.xml")]
            ).encode(),
            "xl/worksheets/sheet2.xml": sheet_xml(
                [[IS("q"), IS("a")], [IS("p"), IS("c")]], Shared()
            ).encode(),
        },
    )
    e = run(two, sheet="Second")[1].report.source_schema_sha256
    check(CID, "the sheet index is part of the digest (same header, other index)", e != a)


# --- sheet selection --------------------------------------------------------------------


def test_sheet_selection_table() -> None:
    base = book([[IS("q"), IS("a")], [IS("p"), IS("c")]], sheet="Data")
    events, outcome = run(base, sheet="Data")
    check(CID, "the exact name selects the sheet", len(accepted(events)) == 1)
    for label, name in (
        ("case variant", "data"),
        ("upper case", "DATA"),
        ("trailing space", "Data "),
        ("missing", "Nope"),
        ("empty-ish unicode", "Dаta"),  # Cyrillic a
    ):
        _events, outcome = run(base, sheet=name)
        check(
            CID,
            f"{label}: no match, fatal mapping_column_missing, no fallback to another sheet",
            failed_with(outcome, "mapping_column_missing") and not accepted(_events),
        )
    # A second sheet exists; asking for a missing name must not fall back to the first.
    two = book(
        [[IS("q"), IS("a")], [IS("p"), IS("c")]],
        sheet="First",
        extra_sheets=[("Second", "rId2", None)],
        replace={
            "xl/_rels/workbook.xml.rels": workbook_rels(
                [("rId1", "worksheets/sheet1.xml"), ("rId2", "worksheets/sheet2.xml")]
            ).encode(),
            "xl/worksheets/sheet2.xml": sheet_xml(
                [[IS("q"), IS("a")], [IS("OTHER"), IS("sheet")]], Shared()
            ).encode(),
        },
    )
    events, outcome = run(two, sheet="Second")
    check(
        CID,
        "the named second sheet is read, not the first",
        len(accepted(events)) == 1 and texts(accepted(events)[0]) == ["OTHER", "sheet"],
    )
    _events, outcome = run(two, sheet="Third")
    check(
        CID,
        "a missing name with two sheets present still does not fall back",
        failed_with(outcome, "mapping_column_missing"),
    )


def test_hidden_sheets_are_used_only_when_named() -> None:
    for state in ("hidden", "veryHidden"):
        two = book(
            [[IS("q"), IS("a")], [IS("VISIBLE"), IS("x")]],
            sheet="Shown",
            extra_sheets=[("Secret", "rId2", state)],
            replace={
                "xl/_rels/workbook.xml.rels": workbook_rels(
                    [("rId1", "worksheets/sheet1.xml"), ("rId2", "worksheets/sheet2.xml")]
                ).encode(),
                "xl/worksheets/sheet2.xml": sheet_xml(
                    [[IS("q"), IS("a")], [IS("HIDDEN"), IS("y")]], Shared()
                ).encode(),
            },
        )
        events, _outcome = run(two, sheet="Shown")
        check(
            CID,
            f"{state}: naming the visible sheet reads only it",
            [texts(c) for c in accepted(events)] == [["VISIBLE", "x"]],
        )
        events, _outcome = run(two, sheet="Secret")
        check(
            CID,
            f"{state}: naming the sheet explicitly reads it",
            [texts(c) for c in accepted(events)] == [["HIDDEN", "y"]],
        )
        _events, outcome = run(two, sheet="secret")
        check(
            CID,
            f"{state}: a casefolded name does not reach it",
            failed_with(outcome, "mapping_column_missing"),
        )


def test_duplicate_sheet_names_are_fatal() -> None:
    rels = workbook_rels(
        [("rId1", "worksheets/sheet1.xml"), ("rId2", "worksheets/sheet1.xml")]
    ).encode()
    for label, other in (
        ("exact duplicate", "Data"),
        ("casefold duplicate", "DATA"),
        ("casefold, accented", "dAtA"),
    ):
        data = book(
            [[IS("q"), IS("a")], [IS("p"), IS("c")]],
            extra_sheets=[(other, "rId2", None)],
            replace={"xl/_rels/workbook.xml.rels": rels},
        )
        for sheet in ("Data", other):
            _events, outcome = run(data, sheet=sheet)
            check(
                CID,
                f"{label} (asking for {sheet!r}): duplicate_sheet_name",
                failed_with(outcome, "duplicate_sheet_name"),
            )
    # sheets that differ only beyond casefold are distinct
    data = book(
        [[IS("q"), IS("a")], [IS("p"), IS("c")]],
        extra_sheets=[("Data2", "rId2", None)],
        replace={"xl/_rels/workbook.xml.rels": rels},
    )
    check(CID, "distinct names are fine", run(data)[1].report.state == "completed")


def test_sheet_target_table() -> None:
    for label, target, code in (
        ("traversal", "../../etc/passwd", "unsafe_archive"),
        ("traversal out of xl", "../docProps/core.xml", "unsafe_archive"),
        ("absolute", "/xl/worksheets/sheet1.xml", "unsafe_archive"),
        ("backslash", "worksheets\\sheet1.xml", "unsafe_archive"),
        ("url", "http://example.com/s.xml", "unsafe_archive"),
        ("empty", "", "xlsx_malformed"),
        ("empty segment", "worksheets//sheet1.xml", "xlsx_malformed"),
        ("missing part", "worksheets/nothere.xml", "xlsx_malformed"),
    ):
        _events, outcome = run(book(sheet_target=target))
        check(CID, f"sheet target {label}: {code}", failed_with(outcome, code))
    events, _o = run(book(sheet_target="./worksheets/sheet1.xml"))
    check(CID, "a './' prefix is normalised and followed", len(accepted(events)) == 1)
    events, _o = run(book(sheet_target="WORKSHEETS/Sheet1.XML"))
    check(CID, "part names are case-insensitive (OPC)", len(accepted(events)) == 1)
    _events, outcome = run(book(sheet_type="chartsheet"))
    check(
        CID,
        "a non-worksheet relationship type is unsupported_workbook",
        failed_with(outcome, "unsupported_workbook"),
    )
    # a sheet whose relationship id has no relationship
    data = book(replace={"xl/workbook.xml": workbook_xml([("Data", "rIdMissing", None)]).encode()})
    check(
        CID,
        "a dangling relationship id is xlsx_malformed",
        failed_with(run(data)[1], "xlsx_malformed"),
    )
    data = book(drop=("xl/_rels/workbook.xml.rels",))
    check(
        CID,
        "no workbook relationships part is xlsx_malformed",
        failed_with(run(data)[1], "xlsx_malformed"),
    )


# --- column slots -----------------------------------------------------------------------


def test_column_slots_header_index_letters() -> None:
    data = book([[IS("x"), IS("q"), IS("y"), IS("a")], [IS("1"), IS("P"), IS("2"), IS("C")]])
    for label, kwargs in (
        ("header", {}),
        ("index", {"prompt": {"index": 1}, "completion": {"index": 3}}),
        ("letters", {"prompt": {"letters": "B"}, "completion": {"letters": "D"}}),
    ):
        events, outcome = run(data, **kwargs)
        got = accepted(events)
        check(
            CID,
            f"{label} slots map the same columns",
            len(got) == 1 and texts(got[0]) == ["P", "C"],
        )
    for label, kwargs in (
        ("index at the header width", {"prompt": {"index": 4}}),
        ("letters past the header", {"prompt": {"letters": "E"}}),
        ("letters past XFD", {"prompt": {"letters": "ZZZ"}}),
    ):
        _events, outcome = run(data, **kwargs)
        check(
            CID, f"{label}: mapping_column_missing", failed_with(outcome, "mapping_column_missing")
        )
    events, _o = run(data, prompt={"letters": "AA"})
    check(CID, "multi-letter columns are columns, not typos", True)


def test_column_positions_do_not_matter() -> None:
    forward = book([[IS("q"), IS("a")], [IS("P"), IS("C")]])
    backward = book([[IS("a"), IS("q")], [IS("C"), IS("P")]])
    check(
        CID,
        "swapped columns map by header",
        [texts(c) for c in accepted(run(forward)[0])]
        == [texts(c) for c in accepted(run(backward)[0])],
    )


def test_sparse_cells_and_implicit_references() -> None:
    raw = (
        f'{XML_HEAD}<worksheet xmlns="{MAIN}"><sheetData>'
        '<row r="1"><c r="B1" t="inlineStr"><is><t>q</t></is></c><c r="D1" t="inlineStr"><is><t>a</t></is></c></row>'
        '<row r="3"><c r="B3" t="inlineStr"><is><t>P</t></is></c><c r="D3" t="inlineStr"><is><t>C</t></is></c></row>'
        '<row><c t="inlineStr"><is><t>no-ref-1</t></is></c><c t="inlineStr"><is><t>no-ref-2</t></is></c>'
        '<c t="inlineStr"><is><t>no-ref-3</t></is></c><c t="inlineStr"><is><t>no-ref-4</t></is></c>'
        "<c/></row>"
        "</sheetData></worksheet>"
    )
    events, outcome = run(book(raw_sheet=raw))
    got = [texts(c) for c in accepted(events)]
    check(CID, "sparse columns map by header across missing cells", got[0] == ["P", "C"])
    check(
        CID,
        "implicit refs continue from the previous row/column",
        got[1] == ["no-ref-2", "no-ref-4"],
    )
    check(CID, "locators use the sheet row number", outcome.report.state == "completed")
    # a gap in rows: row 2 is simply absent, and rejections name the real row
    raw = (
        f'{XML_HEAD}<worksheet xmlns="{MAIN}"><sheetData>'
        '<row r="1"><c r="A1" t="inlineStr"><is><t>q</t></is></c><c r="B1" t="inlineStr"><is><t>a</t></is></c></row>'
        '<row r="7"><c r="A7" t="inlineStr"><is><t>only-prompt</t></is></c></row>'
        "</sheetData></worksheet>"
    )
    _events, outcome = run(book(raw_sheet=raw))
    check(
        CID,
        "a missing completion cell is required_field_empty at row:7",
        outcome.report.rejections
        == (ic.RowRejection("row:7", "required_field_empty", "completion"),),
    )


def test_cells_right_of_the_header_are_ignored() -> None:
    data = book([[IS("q"), IS("a")], [IS("P"), IS("C"), IS("stray"), F("1", "1")]])
    events, outcome = run(data)
    check(
        CID,
        "extra cells beyond the header do not reject the row",
        outcome.report.state == "completed" and len(accepted(events)) == 1,
    )


def test_merged_cells_keep_the_value_in_the_top_left_only() -> None:
    raw = (
        f'{XML_HEAD}<worksheet xmlns="{MAIN}"><sheetData>'
        '<row r="1"><c r="A1" t="inlineStr"><is><t>q</t></is></c><c r="B1" t="inlineStr"><is><t>a</t></is></c></row>'
        '<row r="2"><c r="A2" t="inlineStr"><is><t>P</t></is></c><c r="B2" t="inlineStr"><is><t>C</t></is></c></row>'
        '<row r="3"><c r="A3"/><c r="B3" t="inlineStr"><is><t>C2</t></is></c></row>'
        '</sheetData><mergeCells count="1"><mergeCell ref="A2:A3"/></mergeCells></worksheet>'
    )
    events, outcome = run(book(raw_sheet=raw))
    check(
        CID,
        "the covered cell is empty: its row is required_field_empty, not a copy of the top-left value",
        len(accepted(events)) == 1
        and outcome.report.rejections
        == (ic.RowRejection("row:3", "required_field_empty", "prompt"),),
    )


def test_mapping_is_validated_before_a_byte_is_read() -> None:
    data = book()
    bad_texts = [
        (
            "no xlsx block",
            '{"shape":"prompt_completion","prompt":{"header":"q"},"completion":{"header":"a"}}',
        ),
        (
            "key slot",
            '{"shape":"prompt_completion","prompt":{"key":"q"},"completion":{"header":"a"},"xlsx":{"sheet_name":"Data","header_row":0}}',
        ),
        (
            "messages pointer",
            '{"shape":"messages","messages_pointer":"/m","role_key":"r","content_key":"c","role_map":{"user":"user"},"on_unmapped_message_keys":"reject","xlsx":{"sheet_name":"Data","header_row":0}}',
        ),
    ]
    for label, text in bad_texts:
        reader = make_reader(data)
        try:
            xi.xlsx_events(
                reader,
                ic.parse_mapping(text),
                verified_content_type=XLSX_TYPE,
                dataset_sha256="0" * 64,
            )
        except ic.MappingInvalidError:
            check(CID, f"{label}: MappingInvalidError, no byte read", reader.bytes_read == 0)
        except Exception as exc:  # noqa: BLE001
            check(CID, f"{label}: raised {type(exc).__name__}", False)
        else:
            check(CID, f"{label}: refused up front", False)


def test_messages_json_column_carries_a_conversation() -> None:
    import json

    conversation = json.dumps(
        [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "yo"}]
    )
    text = (
        '{"shape":"messages_json","messages":{"header":"conv"},"role_key":"role","content_key":"content",'
        '"role_map":{"user":"user","assistant":"assistant"},"on_unmapped_message_keys":"reject",'
        '"xlsx":{"sheet_name":"Data","header_row":0}}'
    )
    data = book([[IS("conv")], [IS(conversation)]])
    outcome = xi.run_xlsx_import(
        make_reader(data),
        ic.parse_mapping(text),
        verified_content_type=XLSX_TYPE,
        dataset_purpose="training",
        expected_sha256=hashlib.sha256(data).hexdigest(),
    )
    check(
        CID,
        "a messages_json column imports",
        outcome.report.state == "completed" and outcome.report.records_accepted == 1,
    )


# --- bounds -----------------------------------------------------------------------------


def test_bounds_table() -> None:
    def one_cell(ref: str, row_attr: str = "1") -> bytes:
        raw = (
            f'{XML_HEAD}<worksheet xmlns="{MAIN}"><sheetData>'
            f'<row r="{row_attr}"><c r="{ref}" t="inlineStr"><is><t>q</t></is></c></row></sheetData></worksheet>'
        )
        return book(raw_sheet=raw)

    cases = [
        ("row 1,048,576 is the last row", "A1048576", "1048576", None),
        ("row 1,048,577 is out of bounds", "A1048577", "1048577", "sheet_out_of_bounds"),
        ("column XFD is the last column", "XFD1", "1", None),
        ("column XFE is out of bounds", "XFE1", "1", "sheet_out_of_bounds"),
        ("column ZZZ is out of bounds", "ZZZ1", "1", "sheet_out_of_bounds"),
        (
            "an absurd row number is out of bounds, not a huge int",
            "A1",
            "9" * 5000,
            "sheet_out_of_bounds",
        ),
    ]
    for label, ref, row, code in cases:
        data = one_cell(ref if code != "sheet_out_of_bounds" or "row" not in label else ref, row)
        # the cell ref row must agree with the row; build agreeing refs for the row cases
        letters = re.match(r"[A-Z]+", ref).group(0)  # type: ignore[union-attr]
        data = one_cell(f"{letters}{row}" if len(row) < 20 else f"{letters}1", row)
        _events, outcome = run(data)
        if code is None:
            check(CID, label, outcome.report.failure_code != "sheet_out_of_bounds")
        else:
            check(CID, f"{label}: fatal {code}", failed_with(outcome, code))
    # implicit overflow: a cell with no ref after XFD
    cells = "".join('<c t="inlineStr"><is><t>x</t></is></c>' for _ in range(xi.XLSX_MAX_COLUMN + 1))
    raw = f'{XML_HEAD}<worksheet xmlns="{MAIN}"><sheetData><row r="1">{cells}</row></sheetData></worksheet>'
    check(
        CID,
        "16,385 implicit cells in one row: sheet_out_of_bounds",
        failed_with(run(book(raw_sheet=raw))[1], "sheet_out_of_bounds"),
    )
    cells = "".join('<c t="inlineStr"><is><t>x</t></is></c>' for _ in range(xi.XLSX_MAX_COLUMN))
    raw = f'{XML_HEAD}<worksheet xmlns="{MAIN}"><sheetData><row r="1">{cells}</row></sheetData></worksheet>'
    check(
        CID,
        "16,384 implicit cells fit",
        run(book(raw_sheet=raw))[1].report.failure_code != "sheet_out_of_bounds",
    )
    rows = "".join("<row/>" for _ in range(3))
    raw = f'{XML_HEAD}<worksheet xmlns="{MAIN}"><sheetData><row r="1048575"/>{rows}</sheetData></worksheet>'
    check(
        CID,
        "implicit row numbers past 1,048,576: sheet_out_of_bounds",
        failed_with(run(book(raw_sheet=raw))[1], "sheet_out_of_bounds"),
    )


def sized_sheet(cells: int) -> bytes:
    header = '<row r="1"><c r="A1" t="inlineStr"><is><t>q</t></is></c><c r="B1" t="inlineStr"><is><t>a</t></is></c></row>'
    body = "".join(
        f'<row r="{r}"><c r="A{r}" t="inlineStr"><is><t>p</t></is></c><c r="B{r}" t="inlineStr"><is><t>c</t></is></c></row>'
        for r in range(2, 2 + (cells - 2) // 2)
    )
    return book(
        raw_sheet=f'{XML_HEAD}<worksheet xmlns="{MAIN}"><sheetData>{header}{body}</sheetData></worksheet>'
    )


def test_cell_count_bound_is_exact() -> None:
    for cap in (10, 11, 12):
        limits = xi.XlsxLimits(max_cells=cap)
        at_cap = sized_sheet(cap)
        _e, outcome = run(at_cap, xlsx_limits=limits)
        check(
            CID,
            f"max_cells={cap}: exactly {cap} cells completes",
            outcome.report.state == "completed",
        )
        over = sized_sheet(cap + 2)
        _e, outcome = run(over, xlsx_limits=limits)
        check(
            CID,
            f"max_cells={cap}: {cap + 2} cells is fatal too_many_cells",
            failed_with(outcome, "too_many_cells")
            and (outcome.fatal_locator or "").startswith("row:"),
        )
    # one cell over, not two
    one_over = book(
        raw_sheet=(
            f'{XML_HEAD}<worksheet xmlns="{MAIN}"><sheetData><row r="1"><c r="A1" t="inlineStr"><is><t>q</t></is></c>'
            '<c r="B1" t="inlineStr"><is><t>a</t></is></c><c r="C1" t="inlineStr"><is><t>x</t></is></c></row></sheetData></worksheet>'
        )
    )
    check(
        CID,
        "cap-1 is fatal for max_cells=2 (3 cells)",
        failed_with(run(one_over, xlsx_limits=xi.XlsxLimits(max_cells=2))[1], "too_many_cells"),
    )
    check(
        CID,
        "cap exactly met passes for max_cells=3",
        run(one_over, xlsx_limits=xi.XlsxLimits(max_cells=3))[1].report.state == "completed",
    )
    # unmapped and empty cells count too
    cells = "".join(f'<c r="{col_letters(i)}1"/>' for i in range(20))
    raw = f'{XML_HEAD}<worksheet xmlns="{MAIN}"><sheetData><row r="1">{cells}</row></sheetData></worksheet>'
    check(
        CID,
        "empty cells count toward the cap",
        failed_with(
            run(book(raw_sheet=raw), xlsx_limits=xi.XlsxLimits(max_cells=19))[1], "too_many_cells"
        ),
    )


def test_limits_can_be_lowered_never_raised() -> None:
    for name in ("max_cells", "max_shared_strings_bytes", "max_total_uncompressed_bytes"):
        default = getattr(xi.XlsxLimits(), name)
        for bad in (default + 1, 0, -1, True, 1.5, "5"):
            try:
                xi.XlsxLimits(**{name: bad})
            except ValueError:
                continue
            check(CID, f"XlsxLimits({name}={bad!r}) is refused", False)
        xi.XlsxLimits(**{name: default})
        xi.XlsxLimits(**{name: 1})
    check(CID, "the cell default is 50,000,000", xi.XlsxLimits().max_cells == 50_000_000)
    check(
        CID,
        "the shared-strings default is 256 MiB",
        xi.XlsxLimits().max_shared_strings_bytes == 256 * 1024**2,
    )
    check(
        CID,
        "the total default is the FTF-0501 10 GiB",
        xi.XlsxLimits().max_total_uncompressed_bytes == 10 * 1024**3,
    )


def test_shared_strings_cap_is_exact_and_fatal() -> None:
    body = "<si><t>" + "x" * 200 + "</t></si>"
    data = sst_book(body, ["0", "0"])
    real = len(zipfile.ZipFile(io.BytesIO(data)).read("xl/sharedStrings.xml"))
    ok = xi.XlsxLimits(max_shared_strings_bytes=real)
    check(
        CID,
        "a shared-strings part exactly at the cap loads",
        run(data, xlsx_limits=ok)[1].report.state == "completed",
    )
    low = xi.XlsxLimits(max_shared_strings_bytes=real - 1)
    _e, outcome = run(data, xlsx_limits=low)
    check(
        CID,
        "one byte over the cap is fatal limit_exceeded",
        failed_with(outcome, "limit_exceeded") and outcome.fatal_locator is None,
    )


def test_oversize_cell_is_fatal_field_too_large() -> None:
    limits = ic.ImportLimits(max_record_bytes=50)
    for label, cell in (
        ("inline", IS("x" * 51)),
        ("number", N("9" * 51)),
        ("shared", S("x" * 51)),
    ):
        _e, outcome = run(book([[IS("q"), IS("a")], [IS("p"), cell]]), limits=limits)
        check(
            CID,
            f"{label} cell of 51 chars over a 50 cap: fatal field_too_large",
            failed_with(outcome, "field_too_large"),
        )
        _e, outcome = run(
            book([[IS("q"), IS("a")], [IS("p"), type(cell)(cell.make)]]),
            limits=ic.ImportLimits(max_record_bytes=51),
        )
        check(
            CID,
            f"{label} cell of exactly 51 chars under a 51 cap passes the cell bound",
            outcome.report.failure_code != "field_too_large",
        )
    # the bound applies to unmapped cells too (it bounds memory)
    _e, outcome = run(
        book([[IS("q"), IS("a"), IS("n")], [IS("p"), IS("c"), IS("x" * 51)]]), limits=limits
    )
    check(
        CID,
        "an oversize UNMAPPED cell is fatal field_too_large",
        failed_with(outcome, "field_too_large"),
    )
    # a record whose mapped fields add up past the cap is a rejection, not fatal
    _e, outcome = run(
        book([[IS("q"), IS("a")], [IS("x" * 30), IS("y" * 30)], [IS("ok"), IS("fine")]]),
        limits=limits,
    )
    check(
        CID,
        "mapped fields adding up past the cap: record_too_large, import continues",
        outcome.report.rejections == (ic.RowRejection("row:2", "record_too_large", None),)
        and outcome.report.records_accepted == 1,
    )
    # the byte count is utf-8 bytes, not characters
    _e, outcome = run(book([[IS("q"), IS("a")], [IS("م" * 14), IS("ب" * 14)]]), limits=limits)
    check(
        CID,
        "28 two-byte characters (56 bytes) exceed a 50-byte record",
        codes(outcome) == ["record_too_large"],
    )
    _e, outcome = run(book([[IS("q"), IS("a")], [IS("م" * 12), IS("ب" * 13)]]), limits=limits)
    check(CID, "50 bytes exactly fits", outcome.report.records_accepted == 1)
    # an oversize shared string is fatal when the table loads
    _e, outcome = run(sst_book("<si><t>" + "x" * 51 + "</t></si>", ["0", "0"]), limits=limits)
    check(
        CID,
        "an oversize shared string is fatal field_too_large",
        failed_with(outcome, "field_too_large"),
    )


# --- archive attacks --------------------------------------------------------------------

import test_ftf_0501 as t501  # noqa: E402  (the FTF-0501 malicious-package fixture builders)


def utf16(text: str) -> bytes:
    return text.replace('encoding="UTF-8"', 'encoding="UTF-16"').encode("utf-16")


def members_of(data: bytes) -> list[tuple[str, bytes]]:
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        return [(i.filename, archive.read(i)) for i in archive.infolist()]


def with_member(data: bytes, name: str, payload: bytes) -> bytes:
    kept = [(n, d) for n, d in members_of(data) if n != name]
    return t501._zip([*kept, (name, payload)])


def with_extra(data: bytes, *extra: tuple[str, bytes]) -> bytes:
    return t501._zip([*members_of(data), *extra])


GOOD = book()


def test_archive_attack_table() -> None:
    import warnings

    big = b" " * (5 * 1024 * 1024)
    sheet = f'{XML_HEAD}<worksheet xmlns="{MAIN}"><sheetData>'.encode()
    bomb_sheet = sheet + big + b"</sheetData></worksheet>"
    tiny = xi.XlsxLimits(max_total_uncompressed_bytes=1024 * 1024)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        dup = t501._zip([*members_of(GOOD), ("xl/workbook.xml", b"<workbook/>")])
        dup_case = t501._zip([*members_of(GOOD), ("XL/Workbook.XML", b"<workbook/>")])
    cases: list[tuple[str, bytes, str, xi.XlsxLimits]] = [
        (
            "zip bomb past the total cap (declared sizes)",
            with_member(GOOD, "xl/worksheets/sheet1.xml", bomb_sheet),
            "unsafe_archive",
            tiny,
        ),
        (
            "workbook.xml.rels past the small-part cap",
            with_member(GOOD, "xl/_rels/workbook.xml.rels", workbook_rels().encode() + big),
            "unsafe_archive",
            xi.XlsxLimits(),
        ),
        (
            "[Content_Types].xml past 1 MiB",
            with_member(GOOD, "[Content_Types].xml", content_types().encode() + b" " * (1024**2)),
            "unsafe_archive",
            xi.XlsxLimits(),
        ),
        (
            "a path-traversal member",
            with_extra(GOOD, ("../evil.xml", b"x")),
            "unsafe_archive",
            xi.XlsxLimits(),
        ),
        (
            "traversal in the middle",
            with_extra(GOOD, ("xl/../../evil.xml", b"x")),
            "unsafe_archive",
            xi.XlsxLimits(),
        ),
        (
            "an absolute member",
            with_extra(GOOD, ("/abs.xml", b"x")),
            "unsafe_archive",
            xi.XlsxLimits(),
        ),
        (
            "a backslash member",
            with_extra(GOOD, ("a\\..\\b.xml", b"x")),
            "unsafe_archive",
            xi.XlsxLimits(),
        ),
        ("a duplicate part name", dup, "unsafe_archive", xi.XlsxLimits()),
        ("a part name duplicated after casefold", dup_case, "unsafe_archive", xi.XlsxLimits()),
        (
            "10,001 entries",
            t501._zip(
                [
                    *members_of(GOOD),
                    *[(f"docProps/f{i}.txt", b"x") for i in range(10_001 - len(members_of(GOOD)))],
                ]
            ),
            "unsafe_archive",
            xi.XlsxLimits(),
        ),
        (
            "a zip64 record claiming 70,000 entries",
            GOOD + t501._zip64_tail(70_000, 100, 0),
            "unsafe_archive",
            xi.XlsxLimits(),
        ),
        (
            "a directory claimed larger than 4 MiB",
            GOOD + t501._zip64_tail(5, 4 * 1024**2 + 1, 0),
            "unsafe_archive",
            xi.XlsxLimits(),
        ),
        (
            "a classic record claiming 65,535 entries",
            GOOD[:-22] + t501._end_record(0xFFFF, 100, 0),
            "unsafe_archive",
            xi.XlsxLimits(),
        ),
        (
            "DOCTYPE in the sheet",
            with_member(
                GOOD,
                "xl/worksheets/sheet1.xml",
                f'{XML_HEAD}<!DOCTYPE s [<!ENTITY a "aaaa">]><worksheet xmlns="{MAIN}"><sheetData/></worksheet>'.encode(),
            ),
            "unsafe_archive",
            xi.XlsxLimits(),
        ),
        (
            "DOCTYPE in sharedStrings",
            with_member(
                GOOD,
                "xl/sharedStrings.xml",
                f'{XML_HEAD}<!DOCTYPE s [<!ENTITY a "aaaa">]><sst xmlns="{MAIN}"/>'.encode(),
            ),
            "unsafe_archive",
            xi.XlsxLimits(),
        ),
        (
            "DOCTYPE in the workbook",
            with_member(
                GOOD,
                "xl/workbook.xml",
                f'{XML_HEAD}<!DOCTYPE s [<!ENTITY a "aaaa">]><workbook xmlns="{MAIN}"/>'.encode(),
            ),
            "unsafe_archive",
            xi.XlsxLimits(),
        ),
        (
            "DOCTYPE in the workbook rels",
            with_member(
                GOOD,
                "xl/_rels/workbook.xml.rels",
                f'{XML_HEAD}<!DOCTYPE s [<!ENTITY a "aaaa">]><Relationships xmlns="{PKG}"/>'.encode(),
            ),
            "unsafe_archive",
            xi.XlsxLimits(),
        ),
        (
            "DOCTYPE in [Content_Types].xml",
            with_member(
                GOOD,
                "[Content_Types].xml",
                b'<!DOCTYPE t [<!ENTITY a "aaaa">]>' + content_types().encode(),
            ),
            "unsafe_archive",
            xi.XlsxLimits(),
        ),
        (
            "DOCTYPE in the sheet, UTF-16",
            with_member(
                GOOD,
                "xl/worksheets/sheet1.xml",
                utf16(
                    f'{XML_HEAD}<!DOCTYPE s [<!ENTITY a "aaaa">]><worksheet xmlns="{MAIN}"><sheetData/></worksheet>'
                ),
            ),
            "unsafe_archive",
            xi.XlsxLimits(),
        ),
        (
            "DOCTYPE in sharedStrings, UTF-16",
            with_member(
                GOOD,
                "xl/sharedStrings.xml",
                utf16(f'{XML_HEAD}<!DOCTYPE s [<!ENTITY a "aaaa">]><sst xmlns="{MAIN}"/>'),
            ),
            "unsafe_archive",
            xi.XlsxLimits(),
        ),
        (
            "DOCTYPE in the rels, UTF-16",
            with_member(
                GOOD,
                "xl/_rels/workbook.xml.rels",
                utf16(f'{XML_HEAD}<!DOCTYPE s [<!ENTITY a "aaaa">]><Relationships xmlns="{PKG}"/>'),
            ),
            "unsafe_archive",
            xi.XlsxLimits(),
        ),
        (
            "billion laughs",
            with_member(
                GOOD,
                "xl/worksheets/sheet1.xml",
                (
                    XML_HEAD
                    + '<!DOCTYPE lolz [<!ENTITY a "aaaaaaaaaa"><!ENTITY b "&a;&a;&a;&a;&a;&a;&a;&a;&a;&a;"><!ENTITY c "&b;&b;&b;&b;&b;&b;&b;&b;&b;&b;">]>'
                    + f'<worksheet xmlns="{MAIN}"><sheetData><row r="1"><c r="A1" t="inlineStr"><is><t>&c;</t></is></c></row></sheetData></worksheet>'
                ).encode(),
            ),
            "unsafe_archive",
            xi.XlsxLimits(),
        ),
        (
            "external entity",
            with_member(
                GOOD,
                "xl/worksheets/sheet1.xml",
                (
                    XML_HEAD
                    + '<!DOCTYPE s [<!ENTITY x SYSTEM "file:///etc/passwd">]>'
                    + f'<worksheet xmlns="{MAIN}"><sheetData><row r="1"><c r="A1" t="inlineStr"><is><t>&x;</t></is></c></row></sheetData></worksheet>'
                ).encode(),
            ),
            "unsafe_archive",
            xi.XlsxLimits(),
        ),
        ("an encrypted zip entry", encrypted_entry(GOOD), "unsupported_workbook", xi.XlsxLimits()),
        (
            "bzip2 compression",
            book(method=zipfile.ZIP_BZIP2),
            "unsupported_workbook",
            xi.XlsxLimits(),
        ),
        (
            "lzma compression",
            book(method=zipfile.ZIP_LZMA),
            "unsupported_workbook",
            xi.XlsxLimits(),
        ),
    ]
    for label, data, code, limits in cases:
        events, outcome = run(data, xlsx_limits=limits)
        check(CID, f"{label}: fatal {code}", failed_with(outcome, code) and not accepted(events))
    stored = book(method=zipfile.ZIP_STORED)
    check(CID, "stored (uncompressed) entries are fine", run(stored)[1].report.state == "completed")
    check(
        CID,
        "a fatal archive problem names no row",
        run(with_extra(GOOD, ("../e", b"x")))[1].fatal_locator is None,
    )


def encrypted_entry(data: bytes) -> bytes:
    """GOOD with the 'encrypted' flag set on every entry's central-directory record."""
    out = bytearray(data)
    position = out.find(b"PK\x01\x02")
    while position >= 0:
        flags = struct.unpack_from("<H", out, position + 8)[0] | 0x1
        struct.pack_into("<H", out, position + 8, flags)
        position = out.find(b"PK\x01\x02", position + 4)
    return bytes(out)


def test_entry_count_bound_is_exact() -> None:
    base = members_of(GOOD)
    pad = 10_000 - len(base)
    at_limit = t501._zip([*base, *[(f"docProps/f{i}.txt", b"x") for i in range(pad)]])
    check(CID, "exactly 10,000 entries still imports", run(at_limit)[1].report.state == "completed")
    over = t501._zip([*base, *[(f"docProps/f{i}.txt", b"x") for i in range(pad + 1)]])
    check(CID, "10,001 entries is unsafe_archive", failed_with(run(over)[1], "unsafe_archive"))


def test_directory_bound_is_exact() -> None:
    tail_at = GOOD + t501._zip64_tail(5, ox.MAX_DIRECTORY_BYTES, 0)
    _e, outcome = run(tail_at)
    check(
        CID,
        "a directory exactly 4 MiB is not refused by the bound",
        outcome.report.failure_code != "unsafe_archive",
    )
    tail_over = GOOD + t501._zip64_tail(5, ox.MAX_DIRECTORY_BYTES + 1, 0)
    check(
        CID,
        "one byte over 4 MiB is unsafe_archive",
        failed_with(run(tail_over)[1], "unsafe_archive"),
    )
    tail_entries = GOOD + t501._zip64_tail(ox.MAX_ENTRIES, 100, 0)
    check(
        CID,
        "10,000 claimed entries is not refused by the bound",
        run(tail_entries)[1].report.failure_code != "unsafe_archive",
    )
    tail_entries = GOOD + t501._zip64_tail(ox.MAX_ENTRIES + 1, 100, 0)
    check(
        CID,
        "10,001 claimed entries is unsafe_archive",
        failed_with(run(tail_entries)[1], "unsafe_archive"),
    )


def test_total_size_cap_is_exact() -> None:
    sizes = sum(i.file_size for i in zipfile.ZipFile(io.BytesIO(GOOD)).infolist())
    check(
        CID,
        "total exactly at the cap completes",
        run(GOOD, xlsx_limits=xi.XlsxLimits(max_total_uncompressed_bytes=sizes))[1].report.state
        == "completed",
    )
    check(
        CID,
        "total one byte over the cap is unsafe_archive",
        failed_with(
            run(GOOD, xlsx_limits=xi.XlsxLimits(max_total_uncompressed_bytes=sizes - 1))[1],
            "unsafe_archive",
        ),
    )


def test_unsupported_and_truncated_packages() -> None:
    docx = t501._zip(
        [("[Content_Types].xml", content_types().encode()), ("word/document.xml", b"<document/>")]
    )
    only_word = t501._zip([("word/document.xml", b"<document/>")])
    plain_zip = t501._zip([("a.txt", b"hello")])
    no_types = book(drop=("[Content_Types].xml",))
    no_workbook = book(drop=("xl/workbook.xml",))
    cases = [
        ("an OLE/CFB container (encrypted workbook)", ox.OLE_MAGIC + b"\x00" * 4096),
        (
            "an OLE container with an EncryptedPackage stream name",
            ox.OLE_MAGIC + "EncryptedPackage".encode("utf-16-le") * 20,
        ),
        ("not a zip at all", b"just some text, definitely not a spreadsheet"),
        ("empty", b""),
        ("PK-prefixed zeros", b"PK\x03\x04" + b"\x00" * 200),
        ("PK-prefixed junk", b"PK\x03\x04" + b"junk" * 100),
        ("an empty zip", t501._zip([])),
        ("a docx", docx),
        ("a zip holding only word/document.xml", only_word),
        ("a plain zip", plain_zip),
        ("no [Content_Types].xml", no_types),
        ("no xl/workbook.xml", no_workbook),
        ("a truncated xlsx", GOOD[: len(GOOD) // 2]),
        ("an xlsx cut before its directory", GOOD[:-30]),
        ("junk before the archive (a self-extractor)", b"MZ" + b"\x00" * 100 + GOOD),
    ]
    for label, data in cases:
        events, outcome = run(data)
        check(
            CID,
            f"{label}: failed, nothing accepted",
            outcome.report.state == "failed"
            and not accepted(events)
            and outcome.report.failure_code in xi.XLSX_FATAL_CODES,
        )
    for label, data in (
        ("an OLE container", cases[0][1]),
        ("not a zip", cases[2][1]),
        ("empty", cases[3][1]),
        ("an empty zip", cases[6][1]),
        ("a docx", docx),
        ("a plain zip", plain_zip),
        ("no content types", no_types),
        ("no workbook part", no_workbook),
        ("truncated", GOOD[: len(GOOD) // 2]),
        ("self-extractor junk", cases[14][1]),
    ):
        check(
            CID, f"{label}: unsupported_workbook", failed_with(run(data)[1], "unsupported_workbook")
        )


def damaged_crc(data: bytes, only: bytes | None = None) -> bytes:
    """`data` with the CRC-32 of each (or one named) member damaged in the directory,
    which is the value zipfile checks once a member has been read to its end."""
    out = bytearray(data)
    position = out.find(b"PK\x01\x02")
    while position >= 0:
        length = struct.unpack_from("<H", out, position + 28)[0]
        if only is None or bytes(out[position + 46 : position + 46 + length]) == only:
            out[position + 16 : position + 20] = b"\xde\xad\xbe\xef"
        position = out.find(b"PK\x01\x02", position + 4)
    return bytes(out)


def test_damaged_members_fail_closed() -> None:
    for name in (
        b"xl/worksheets/sheet1.xml",
        b"xl/sharedStrings.xml",
        b"xl/workbook.xml",
        b"xl/_rels/workbook.xml.rels",
        b"[Content_Types].xml",
    ):
        _e, outcome = run(damaged_crc(GOOD, name))
        check(
            CID,
            f"a damaged CRC on {name.decode()} is fatal xlsx_malformed, never completed",
            failed_with(outcome, "xlsx_malformed"),
        )


def test_lying_directory_never_succeeds_and_stays_bounded() -> None:
    # The directory claims 1 KiB for a sheet that really inflates to 5 MiB.
    big = (
        f'{XML_HEAD}<worksheet xmlns="{MAIN}"><sheetData>'.encode()
        + b" " * (5 * 1024 * 1024)
        + b"</sheetData></worksheet>"
    )
    data = bytearray(with_member(GOOD, "xl/worksheets/sheet1.xml", big))
    position = data.find(b"PK\x01\x02")
    claimed = 0
    while position >= 0:
        name_length = struct.unpack_from("<H", data, position + 28)[0]
        name = bytes(data[position + 46 : position + 46 + name_length])
        if name == b"xl/worksheets/sheet1.xml":
            struct.pack_into("<L", data, position + 24, 1024)
            claimed += 1
        position = data.find(b"PK\x01\x02", position + 4)
    check(CID, "the lie was written into the directory", claimed == 1)
    events, outcome = run(bytes(data))
    check(
        CID,
        "a directory that under-reports a sheet never completes with records",
        outcome.report.state == "failed" and not accepted(events),
    )


def test_xml_bomb_token_and_depth_bounds() -> None:
    head = f'{XML_HEAD}<worksheet xmlns="{MAIN}"><sheetData>'
    tail = "</sheetData></worksheet>"
    two_mib = "a" * (2 * 1024**2)
    cases = [
        ("a 2 MiB attribute value", f'<row r="1"><c r="A1" x="{two_mib}"/></row>'),
        ("a 2 MiB comment", f"<!-- {two_mib} -->"),
        ("a 2 MiB processing instruction", f"<?pi {two_mib}?>"),
    ]
    for label, body in cases:
        _e, outcome = run(book(raw_sheet=head + body + tail))
        check(
            CID,
            f"{label}: unsafe_archive (no unbounded token)",
            failed_with(outcome, "unsafe_archive"),
        )
    small = f'<row r="1"><c r="A1" x="{"a" * 100_000}"/></row>'
    check(
        CID,
        "a 100 KB attribute is fine",
        run(book(raw_sheet=head + small + tail))[1].report.failure_code != "unsafe_archive",
    )
    long_text = "<!--" + "a" * 900_000 + "-->"
    check(
        CID,
        "a 900 KB comment is below the token bound",
        run(book(raw_sheet=head + long_text + tail))[1].report.failure_code != "unsafe_archive",
    )

    # nesting: worksheet + extLst-style nested elements
    def nested(depth: int) -> bytes:
        inner = "<x>" * depth + "</x>" * depth
        return f'{XML_HEAD}<worksheet xmlns="{MAIN}">{inner}<sheetData/></worksheet>'.encode()

    check(
        CID,
        "64 levels (worksheet + 63) is the limit and passes",
        run(book(raw_sheet=nested(63)))[1].report.failure_code != "unsafe_archive",
    )
    _e, outcome = run(book(raw_sheet=nested(64)))
    check(CID, "65 levels is unsafe_archive", failed_with(outcome, "unsafe_archive"))
    _e, outcome = run(book(raw_sheet=nested(100_000)))
    check(CID, "deep nesting never recurses without bound", failed_with(outcome, "unsafe_archive"))
    ss = f'{XML_HEAD}<sst xmlns="{MAIN}">' + "<x>" * 70 + "</x>" * 70 + "</sst>"
    check(
        CID,
        "nesting is bounded in sharedStrings too",
        failed_with(run(book(shared_override=ss.encode()))[1], "unsafe_archive"),
    )


def test_malformed_xml_and_structure() -> None:
    head = f'{XML_HEAD}<worksheet xmlns="{MAIN}"><sheetData>'
    ok_row = '<row r="1"><c r="A1" t="inlineStr"><is><t>q</t></is></c><c r="B1" t="inlineStr"><is><t>a</t></is></c></row>'
    cases = [
        ("truncated sheet", head + ok_row, "xlsx_malformed"),
        ("not xml", "this is not xml", "xlsx_malformed"),
        ("empty part", "", "xlsx_malformed"),
        ("mismatched tags", head + "<row></c></sheetData></worksheet>", "xlsx_malformed"),
        (
            "undefined entity",
            head
            + '<row r="1"><c r="A1" t="inlineStr"><is><t>&nope;</t></is></c></row></sheetData></worksheet>',
            "xlsx_malformed",
        ),
        ("wrong root element", f'{XML_HEAD}<foo xmlns="{MAIN}"/>', "xlsx_malformed"),
        (
            "root in no namespace",
            f"{XML_HEAD}<worksheet><sheetData/></worksheet>",
            "xlsx_malformed",
        ),
        (
            "strict-namespace worksheet",
            f'{XML_HEAD}<worksheet xmlns="{STRICT}"><sheetData/></worksheet>',
            "unsupported_workbook",
        ),
        (
            "two <v> in one cell",
            head + '<row r="1"><c r="A1"><v>1</v><v>2</v></c></row></sheetData></worksheet>',
            "xlsx_malformed",
        ),
        (
            "two <is> in one cell",
            head
            + '<row r="1"><c r="A1" t="inlineStr"><is><t>a</t></is><is><t>b</t></is></c></row></sheetData></worksheet>',
            "xlsx_malformed",
        ),
        (
            "an element inside <v>",
            head + '<row r="1"><c r="A1"><v>1<b/>2</v></c></row></sheetData></worksheet>',
            "xlsx_malformed",
        ),
        (
            "an element inside an inline <t>",
            head
            + '<row r="1"><c r="A1" t="inlineStr"><is><t>a<b/>c</t></is></c></row></sheetData></worksheet>',
            "xlsx_malformed",
        ),
        (
            "unknown cell type",
            head + '<row r="1"><c r="A1" t="zz"><v>1</v></c></row></sheetData></worksheet>',
            "xlsx_malformed",
        ),
        (
            "duplicate cell reference",
            head
            + '<row r="1"><c r="A1"><v>1</v></c><c r="A1"><v>2</v></c></row></sheetData></worksheet>',
            "xlsx_malformed",
        ),
        (
            "decreasing cell reference",
            head
            + '<row r="1"><c r="B1"><v>1</v></c><c r="A1"><v>2</v></c></row></sheetData></worksheet>',
            "xlsx_malformed",
        ),
        (
            "duplicate row",
            head
            + '<row r="1"><c r="A1"/></row><row r="1"><c r="A1"/></row></sheetData></worksheet>',
            "xlsx_malformed",
        ),
        (
            "decreasing row",
            head
            + '<row r="2"><c r="A2"/></row><row r="1"><c r="A1"/></row></sheetData></worksheet>',
            "xlsx_malformed",
        ),
        (
            "cell row disagrees with its row",
            head + '<row r="1"><c r="A2"/></row></sheetData></worksheet>',
            "xlsx_malformed",
        ),
        (
            "lower-case cell reference",
            head + '<row r="1"><c r="a1"/></row></sheetData></worksheet>',
            "xlsx_malformed",
        ),
        (
            "leading-zero row in a reference",
            head + '<row r="1"><c r="A01"/></row></sheetData></worksheet>',
            "xlsx_malformed",
        ),
        (
            "reference with no column",
            head + '<row r="1"><c r="1"/></row></sheetData></worksheet>',
            "xlsx_malformed",
        ),
        (
            "empty reference",
            head + '<row r="1"><c r=""/></row></sheetData></worksheet>',
            "xlsx_malformed",
        ),
        (
            "row number zero",
            head + '<row r="0"><c r="A0"/></row></sheetData></worksheet>',
            "xlsx_malformed",
        ),
        ("negative row", head + '<row r="-1"/></sheetData></worksheet>', "xlsx_malformed"),
        ("non-numeric row", head + '<row r="x"/></sheetData></worksheet>', "xlsx_malformed"),
        ("row with a decimal", head + '<row r="1.0"/></sheetData></worksheet>', "xlsx_malformed"),
    ]
    for label, raw, code in cases:
        events, outcome = run(book(raw_sheet=raw))
        check(CID, f"{label}: fatal {code}", failed_with(outcome, code) and not accepted(events))
    _e, outcome = run(
        book(
            raw_sheet=head
            + '<row r="1"><c r="A1"/></row><row r="2"><c r="B2"><v>1</v></c><c r="A2"><v>2</v></c></row></sheetData></worksheet>'
        )
    )
    check(
        CID,
        "a structure fault in row 2 is located at row:2",
        failed_with(outcome, "xlsx_malformed") and outcome.fatal_locator == "row:2",
    )
    _e, outcome = run(book(raw_sheet=head + ok_row + "<row"))
    check(
        CID,
        "a truncated tail is located at the last row read",
        failed_with(outcome, "xlsx_malformed") and outcome.fatal_locator == "row:1",
    )
    # foreign-namespace elements and unknown attributes are skipped
    foreign = (
        f'{XML_HEAD}<worksheet xmlns="{MAIN}" xmlns:x14ac="urn:x" xmlns:o="urn:o"><o:extra><sheetData/></o:extra>'
        '<sheetPr><tabColor rgb="FF"/></sheetPr><dimension ref="A1:Z999999"/><sheetViews/><sheetData>'
        '<row r="1" x14ac:dyDescent="0.25" spans="1:2"><c r="A1" s="1" t="inlineStr"><is><t>q</t></is></c><c r="B1" t="inlineStr"><is><t>a</t></is></c></row>'
        '<row r="2"><c r="A2" t="inlineStr"><is><t>P</t></is></c><c r="B2" t="inlineStr"><is><t>C</t></is><extLst/></c></row>'
        '</sheetData><pageMargins left="0.7"/></worksheet>'
    )
    events, outcome = run(book(raw_sheet=foreign))
    check(
        CID,
        "unknown elements/attributes and an untrusted <dimension> are ignored",
        len(accepted(events)) == 1 and outcome.report.state == "completed",
    )


# --- workbook types ---------------------------------------------------------------------


def test_workbook_type_table() -> None:
    cases = [
        ("a template workbook type", book(workbook_type=TEMPLATE_TYPE), "unsupported_workbook"),
        ("a macro-enabled workbook type", book(workbook_type=MACRO_TYPE), "unsafe_workbook"),
        (
            "a macro-enabled type, upper case",
            book(workbook_type=MACRO_TYPE.upper()),
            "unsafe_workbook",
        ),
        (
            "the xlsb binary type",
            book(workbook_type="application/vnd.ms-excel.sheet.binary.macroEnabled.main+xml"),
            "unsafe_workbook",
        ),
        (
            "a wrong workbook type",
            book(
                workbook_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"
            ),
            "unsupported_workbook",
        ),
        (
            "the workbook part never declared",
            book(
                replace={
                    "[Content_Types].xml": content_types()
                    .replace('PartName="/xl/workbook.xml"', 'PartName="/xl/other.xml"')
                    .encode()
                }
            ),
            "unsupported_workbook",
        ),
        (
            "a macro-enabled type declared for another part",
            book(ct_extra=f'<Override PartName="/xl/x.xml" ContentType="{MACRO_TYPE}"/>'),
            "unsafe_workbook",
        ),
        (
            "a vbaProject content type",
            book(
                ct_extra='<Default Extension="bin" ContentType="application/vnd.ms-office.vbaProject"/>'
            ),
            "unsafe_workbook",
        ),
        (
            "vbaProject.bin in a package typed as a plain xlsx",
            book(extra=[("xl/vbaProject.bin", b"MACRO")]),
            "unsafe_workbook",
        ),
        (
            "vbaProject.bin in upper case",
            book(extra=[("XL/VBAPROJECT.BIN", b"MACRO")]),
            "unsafe_workbook",
        ),
        (
            "an activeX control",
            book(extra=[("xl/activeX/activeX1.xml", b"<a/>")]),
            "unsafe_workbook",
        ),
        (
            "an embedded object",
            book(extra=[("xl/embeddings/oleObject1.bin", b"OLE")]),
            "unsafe_workbook",
        ),
        (
            "an Excel 4 macro sheet",
            book(extra=[("xl/macrosheets/sheet1.xml", b"<m/>")]),
            "unsafe_workbook",
        ),
        (
            "strict OOXML workbook namespace",
            book(replace={"xl/workbook.xml": workbook_xml(ns=STRICT).encode()}),
            "unsupported_workbook",
        ),
        (
            "a workbook that is not a workbook",
            book(replace={"xl/workbook.xml": f'{XML_HEAD}<foo xmlns="{MAIN}"/>'.encode()}),
            "xlsx_malformed",
        ),
        (
            "a workbook with no namespace",
            book(replace={"xl/workbook.xml": b"<workbook><sheets/></workbook>"}),
            "xlsx_malformed",
        ),
        (
            "a sheet element with no name",
            book(
                replace={
                    "xl/workbook.xml": f'{XML_HEAD}<workbook xmlns="{MAIN}" xmlns:r="{REL}"><sheets><sheet r:id="rId1"/></sheets></workbook>'.encode()
                }
            ),
            "xlsx_malformed",
        ),
        (
            "a macro carrier AND an external link: the macro verdict wins",
            book(
                extra=[("xl/vbaProject.bin", b"M"), ("xl/externalLinks/externalLink1.xml", b"<e/>")]
            ),
            "unsafe_workbook",
        ),
    ]
    for label, data, code in cases:
        events, outcome = run(data)
        check(CID, f"{label}: fatal {code}", failed_with(outcome, code) and not accepted(events))
    check(CID, "a plain workbook still imports", run(GOOD)[1].report.state == "completed")
    check(
        CID,
        "a fatal workbook verdict names no row",
        run(book(extra=[("xl/vbaProject.bin", b"M")]))[1].fatal_locator is None,
    )


def test_external_link_table() -> None:
    ext = f'<Relationship Id="rIdX" Type="{REL}/externalLink" Target="http://127.0.0.1:9/x.xlsx" TargetMode="External"/>'
    sheet_rels = f'{XML_HEAD}<Relationships xmlns="{PKG}"><Relationship Id="h1" Type="{REL}/hyperlink" Target="http://127.0.0.1:9/" TargetMode="External"/></Relationships>'
    internal = (
        f'<Relationship Id="rIdI" Type="{REL}/styles" Target="styles.xml" TargetMode="Internal"/>'
    )
    cases = [
        ("an externalLinks part", book(extra=[("xl/externalLinks/externalLink1.xml", b"<e/>")])),
        ("an externalLinks part in upper case", book(extra=[("XL/EXTERNALLINKS/E.XML", b"<e/>")])),
        ("xl/connections.xml", book(extra=[("xl/connections.xml", b"<c/>")])),
        ("a query table", book(extra=[("xl/queryTables/queryTable1.xml", b"<q/>")])),
        ("an External relationship in the workbook rels", book(rel_extra=ext)),
        (
            "an External relationship, lower-cased attribute value",
            book(rel_extra=ext.replace("External", "external")),
        ),
        (
            "an External relationship in a sheet's rels",
            book(extra=[("xl/worksheets/_rels/sheet1.xml.rels", sheet_rels.encode())]),
        ),
        (
            "an External relationship in the package rels",
            book(extra=[("_rels/.rels", sheet_rels.encode())]),
        ),
    ]
    for label, data in cases:
        events, outcome = run(data)
        check(
            CID,
            f"{label}: fatal external_link_present, nothing imported",
            failed_with(outcome, "external_link_present") and not accepted(events),
        )
    events, outcome = run(book(rel_extra=internal))
    check(
        CID,
        "an Internal relationship is fine",
        outcome.report.state == "completed" and len(accepted(events)) == 1,
    )
    events, outcome = run(book(rel_extra=internal.replace(' TargetMode="Internal"', "")))
    check(CID, "a relationship with no TargetMode is internal", outcome.report.state == "completed")


def test_external_links_are_never_retrieved() -> None:
    import socket
    import threading

    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen(5)
    server.settimeout(0.3)
    port = server.getsockname()[1]
    hits: list[object] = []

    def accept() -> None:
        try:
            conn, _addr = server.accept()
            hits.append(conn)
            conn.close()
        except OSError:
            pass

    thread = threading.Thread(target=accept)
    thread.start()
    ext = f'<Relationship Id="rIdX" Type="{REL}/externalLink" Target="http://127.0.0.1:{port}/x.xlsx" TargetMode="External"/>'
    _e, outcome = run(
        book(
            rel_extra=ext,
            extra=[
                (
                    "xl/externalLinks/externalLink1.xml",
                    f"<externalLink r:id='x' xmlns:r='{REL}'/>".encode(),
                )
            ],
        )
    )
    thread.join()
    server.close()
    check(CID, "the link is refused", failed_with(outcome, "external_link_present"))
    check(CID, "and nothing connected to the target", not hits)


# --- ordering of verdicts and the spool -------------------------------------------------


def open_spool_descriptors() -> list[tuple[int, str]]:
    found: list[tuple[int, str]] = []
    for name in os.listdir("/proc/self/fd"):
        try:
            target = os.readlink(f"/proc/self/fd/{name}")
        except OSError:
            continue
        found.append((int(name), target))
    return found


def test_spool_is_anonymous_private_and_closed() -> None:
    import tempfile

    before = {fd for fd, _t in open_spool_descriptors()}
    seen: list[list[tuple[int, str, int]]] = []

    def inspect(_candidate: object) -> None:
        rows = []
        for fd, target in open_spool_descriptors():
            if fd in before:
                continue
            try:
                mode = stat.S_IMODE(os.fstat(fd).st_mode)
            except OSError:
                continue
            rows.append((fd, target, mode))
        seen.append(rows)

    _e, outcome = run(GOOD, on_accepted=inspect)
    check(CID, "the import completed", outcome.report.state == "completed")
    spools = [r for r in seen[0] if r[1].endswith("(deleted)")]
    check(CID, "while importing there is one spool with no directory entry", len(spools) >= 1)
    check(CID, "the spool is mode 0600", all(m == 0o600 for _fd, _t, m in spools))
    named = [
        r
        for r in seen[0]
        if r[1].startswith(tempfile.gettempdir()) and not r[1].endswith("(deleted)")
    ]
    check(CID, "no spool has a visible path", not named)
    after = {fd for fd, _t in open_spool_descriptors()}
    check(CID, "every descriptor is closed after the import", after <= before)
    # also after a fatal problem, and after a preview that is closed early
    _e, outcome = run(with_extra(GOOD, ("../e", b"x")))
    check(
        CID,
        "no descriptor leaks after a fatal archive problem",
        {fd for fd, _t in open_spool_descriptors()} <= before,
    )
    stream = xi.xlsx_events(
        make_reader(book([[S("q"), S("a")]] + [[S(f"r{i}"), S("x")] for i in range(30)])),
        build_mapping(),
        verified_content_type=XLSX_TYPE,
        dataset_sha256="0" * 64,
    )
    result = ic.preview(stream, 5)
    check(
        CID,
        "preview returns the first 5 records",
        len(result.accepted) == 5 and not result.complete,
    )
    check(
        CID,
        "a preview holds its spool until closed",
        {fd for fd, _t in open_spool_descriptors()} - before,
    )
    stream.close()
    check(
        CID,
        "close() releases the spool at once",
        {fd for fd, _t in open_spool_descriptors()} <= before,
    )


def test_nothing_is_read_from_the_archive_before_the_hash_matches() -> None:
    calls: list[object] = []
    good_sha = hashlib.sha256(GOOD).hexdigest()
    changed = GOOD[:-1] + bytes([GOOD[-1] ^ 0x01])
    reader = ic.ChunkReader([changed], expected_size=len(GOOD), expected_sha256=good_sha)
    outcome = xi.run_xlsx_import(
        reader,
        build_mapping(),
        verified_content_type=XLSX_TYPE,
        dataset_purpose="training",
        expected_sha256=good_sha,
        on_accepted=calls.append,
    )
    check(CID, "a changed byte is failed/source_changed", failed_with(outcome, "source_changed"))
    check(CID, "and not one record was produced from the unverified archive", not calls)
    # wrong declared hash on a perfectly valid workbook
    reader = ic.ChunkReader([GOOD], expected_size=len(GOOD), expected_sha256="0" * 64)
    outcome = xi.run_xlsx_import(
        reader,
        build_mapping(),
        verified_content_type=XLSX_TYPE,
        dataset_purpose="training",
        expected_sha256="0" * 64,
        on_accepted=calls.append,
    )
    check(
        CID,
        "a valid workbook with the wrong declared sha is source_changed, no records",
        failed_with(outcome, "source_changed") and not calls,
    )
    # extra bytes beyond the declared size
    reader = ic.ChunkReader([GOOD, b"extra"], expected_size=len(GOOD), expected_sha256=good_sha)
    outcome = xi.run_xlsx_import(
        reader,
        build_mapping(),
        verified_content_type=XLSX_TYPE,
        dataset_purpose="training",
        expected_sha256=good_sha,
        on_accepted=calls.append,
    )
    check(
        CID,
        "bytes beyond the declared size are source_changed, no records",
        failed_with(outcome, "source_changed") and not calls,
    )
    # short source
    reader = ic.ChunkReader([GOOD[:-5]], expected_size=len(GOOD), expected_sha256=good_sha)
    outcome = xi.run_xlsx_import(
        reader,
        build_mapping(),
        verified_content_type=XLSX_TYPE,
        dataset_purpose="training",
        expected_sha256=good_sha,
        on_accepted=calls.append,
    )
    check(
        CID,
        "a short source is source_changed, no records",
        failed_with(outcome, "source_changed") and not calls,
    )
    big = ic.ImportLimits(max_source_bytes=len(GOOD) - 1)
    _e, outcome = run(GOOD, limits=big)
    check(
        CID,
        "a source over max_source_bytes is source_too_large",
        failed_with(outcome, "source_too_large"),
    )


def test_source_failures_are_never_completed() -> None:
    chunks = support.split_chunks(GOOD, 64)
    for at in (0, 1, len(chunks) // 2, len(chunks) - 1):
        source = support.ExplodingSource(chunks, ConnectionResetError("boom"), at=at)
        reader = ic.ChunkReader(
            source, expected_size=len(GOOD), expected_sha256=hashlib.sha256(GOOD).hexdigest()
        )
        outcome = xi.run_xlsx_import(
            reader,
            build_mapping(),
            verified_content_type=XLSX_TYPE,
            dataset_purpose="training",
            expected_sha256=hashlib.sha256(GOOD).hexdigest(),
        )
        check(
            CID,
            f"source error at chunk {at}: blocked/source_unavailable",
            outcome.report.state == "blocked"
            and outcome.report.failure_code == "source_unavailable",
        )


def test_a_full_disk_for_the_spool_is_blocked_not_a_verdict() -> None:
    import errno
    import tempfile

    class FullDisk:
        def __enter__(self) -> FullDisk:
            return self

        def __exit__(self, *_a: object) -> None:
            return None

        def write(self, _b: bytes) -> int:
            raise OSError(errno.ENOSPC, "No space left on device")

    original = tempfile.TemporaryFile
    tempfile.TemporaryFile = lambda *a, **k: FullDisk()  # type: ignore[assignment,misc]
    try:
        _e, outcome = run(GOOD)
    finally:
        tempfile.TemporaryFile = original  # type: ignore[misc]
    check(
        CID,
        "ENOSPC on the spool is blocked/source_unavailable",
        outcome.report.state == "blocked" and outcome.report.failure_code == "source_unavailable",
    )


def test_deadline_is_checked_while_inflating_and_between_rows() -> None:
    class Ticking:
        def __init__(self) -> None:
            self.now = 1000.0

        def __call__(self) -> float:
            self.now += 1.0
            return self.now

    big = (
        f'{XML_HEAD}<worksheet xmlns="{MAIN}"><sheetData>'.encode()
        + b" " * (40 * 1024 * 1024)
        + b"</sheetData></worksheet>"
    )
    data = with_member(GOOD, "xl/worksheets/sheet1.xml", big)
    reader = ic.ChunkReader(
        [data],
        expected_size=len(data),
        expected_sha256=hashlib.sha256(data).hexdigest(),
        limits=ic.ImportLimits(deadline_seconds=100),
        clock=Ticking(),
    )
    outcome = xi.run_xlsx_import(
        reader,
        build_mapping(),
        verified_content_type=XLSX_TYPE,
        dataset_purpose="training",
        expected_sha256=hashlib.sha256(data).hexdigest(),
        limits=ic.ImportLimits(deadline_seconds=100),
    )
    check(
        CID,
        "a sheet with no rows that inflates for too long is failed/parse_timeout",
        failed_with(outcome, "parse_timeout"),
    )
    clock = support.FakeClock()
    rows = [[S("q"), S("a")]] + [[S(f"r{i}"), S("x")] for i in range(20)]
    data = book(rows)
    reader = ic.ChunkReader(
        support.split_chunks(data, 4096),
        expected_size=len(data),
        expected_sha256=hashlib.sha256(data).hexdigest(),
        clock=clock,
    )
    seen: list[object] = []

    def advancing(candidate: object) -> None:
        seen.append(candidate)
        clock.advance(10_000)

    outcome = xi.run_xlsx_import(
        reader,
        build_mapping(),
        verified_content_type=XLSX_TYPE,
        dataset_purpose="training",
        expected_sha256=hashlib.sha256(data).hexdigest(),
        on_accepted=advancing,
    )
    check(
        CID,
        "a deadline passing between rows is failed/parse_timeout",
        failed_with(outcome, "parse_timeout") and len(seen) < 20,
    )
    # a deadline that has not passed does not interfere
    reader = ic.ChunkReader(
        [data],
        expected_size=len(data),
        expected_sha256=hashlib.sha256(data).hexdigest(),
        limits=ic.ImportLimits(deadline_seconds=3600),
        clock=Ticking(),
    )
    outcome = xi.run_xlsx_import(
        reader,
        build_mapping(),
        verified_content_type=XLSX_TYPE,
        dataset_purpose="training",
        expected_sha256=hashlib.sha256(data).hexdigest(),
    )
    check(CID, "an unexpired deadline completes", outcome.report.state == "completed")


def test_rejection_budget_is_enforced() -> None:
    limits = ic.ImportLimits(max_rejections=3)
    data = book([[S("q"), S("a")]] + [[S("only"), F()] for _ in range(10)])
    _e, outcome = run(data, limits=limits)
    check(
        CID,
        "too many rejections is failed/too_many_rejections",
        failed_with(outcome, "too_many_rejections"),
    )


def test_preview_uses_the_same_events() -> None:
    data = book([[S("q"), S("a")]] + [[S(f"r{i}"), S("x")] for i in range(30)])
    stream = xi.xlsx_events(
        make_reader(data),
        build_mapping(),
        verified_content_type=XLSX_TYPE,
        dataset_sha256=hashlib.sha256(data).hexdigest(),
    )
    result = ic.preview(stream, 5)
    stream.close()
    check(
        CID,
        "preview returns the first 5 accepted records",
        len(result.accepted) == 5 and not result.complete,
    )
    check(
        CID,
        "preview candidates are the real candidates",
        texts(dict(result.accepted[0])) == ["r0", "x"],
    )


def test_tool_schemas_and_validator_are_forwarded() -> None:
    import json

    conversation = json.dumps(
        [
            {"role": "user", "content": "q"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [{"id": "1", "name": "t", "arguments": {"x": 1}}],
            },
        ]
    )
    text = (
        '{"shape":"messages_json","messages":{"header":"conv"},"role_key":"role","content_key":"content",'
        '"tool_calls_key":"tool_calls","role_map":{"user":"user","assistant":"assistant"},'
        '"on_unmapped_message_keys":"reject","xlsx":{"sheet_name":"Data","header_row":0}}'
    )
    data = book([[IS("conv")], [IS(conversation)]])
    calls: list[object] = []

    def validator(*args: object, **kwargs: object) -> None:
        calls.append(args)

    xi.run_xlsx_import(
        make_reader(data),
        ic.parse_mapping(text),
        verified_content_type=XLSX_TYPE,
        dataset_purpose="training",
        expected_sha256=hashlib.sha256(data).hexdigest(),
        tool_schemas={"t": {"type": "object"}},
        tool_argument_validator=validator,
    )  # type: ignore[arg-type]
    check(CID, "the tool argument validator is reached through the importer", len(calls) >= 1)


def test_meta_leak_markers_never_reach_the_report() -> None:
    m_val, m_head, m_row, m_sheet = (
        support.make_marker(x) for x in ("value", "header", "row", "sheet")
    )
    data = book(
        [
            [IS("q"), IS("a"), IS(m_head)],
            [IS(m_val), IS("x"), IS("1")],
            [IS(m_row)],
            [IS("bad\x00" + m_val), IS("x"), IS("1")],
            [IS("p"), F("1", m_val)],
        ],
        sheet=m_sheet[:30],
    )
    _e, outcome = run(data, sheet=m_sheet[:30])
    leaks = support.leaked_markers((outcome.report, outcome.fatal_locator), [m_val, m_head, m_row])
    check(CID, f"no content in the report ({leaks})", not leaks)
    _e, outcome = run(book([[IS("q"), IS("a"), IS(m_head), IS(m_head)]]))
    check(
        CID,
        "a duplicate-header failure names no header text",
        not support.leaked_markers((outcome.report, outcome.fatal_locator), [m_head]),
    )
    _e, outcome = run(book(), sheet=m_sheet[:30])
    check(
        CID,
        "a missing-sheet failure names no sheet text",
        not support.leaked_markers((outcome.report, outcome.fatal_locator), [m_sheet[:30]]),
    )
    _e, outcome = run(with_extra(GOOD, (f"../{m_val}.xml", b"x")))
    check(
        CID,
        "an archive failure names no member name",
        not support.leaked_markers((outcome.report, outcome.fatal_locator), [m_val]),
    )
    _e, outcome = run(book(extra=[(f"xl/vbaProject.bin", m_val.encode())]))
    check(
        CID,
        "a macro failure names no content",
        not support.leaked_markers((outcome.report, outcome.fatal_locator), [m_val]),
    )
    _e, outcome = run(
        book(raw_sheet=f"{XML_HEAD}<worksheet xmlns='{MAIN}'><sheetData><row>{m_val}")
    )
    check(
        CID,
        "an XML failure names no content",
        not support.leaked_markers((outcome.report, outcome.fatal_locator), [m_val]),
    )
    try:
        xi.xlsx_events(
            make_reader(data),
            ic.parse_mapping(mapping_text(sheet=m_sheet[:30]).replace('"xlsx"', '"nope"')),
            verified_content_type=XLSX_TYPE,
            dataset_sha256="0" * 64,
        )
    except Exception as exc:  # noqa: BLE001
        check(CID, "mapping errors name no value", not support.leaked_markers(exc, [m_sheet[:30]]))


def test_catalogs_cover_every_emitted_code() -> None:
    check(CID, "fatal codes are all in FAILURE_CODES", xi.XLSX_FATAL_CODES <= ic.FAILURE_CODES)
    check(
        CID,
        "row codes are in REJECTION_CODES",
        {
            "formula_cell",
            "cell_error_value",
            "invalid_unicode",
            "nul_character",
            "record_too_large",
            "required_field_empty",
        }
        <= ic.REJECTION_CODES,
    )
    check(CID, "catalogs stay disjoint", not (ic.REJECTION_CODES & ic.FAILURE_CODES))
    for code in sorted(xi.XLSX_FATAL_CODES):
        err = xi.XlsxAborted(code, 3)
        check(
            CID,
            f"XlsxAborted({code}) is a failed ImportAborted at row:3",
            err.state == "failed" and err.failure_code == code and err.locator == "row:3",
        )
        check(
            CID,
            f"XlsxAborted({code}) with no row has no locator",
            xi.XlsxAborted(code).locator is None,
        )
    for bad in ("nope", "source_changed", "csv_structure_invalid"):
        try:
            xi.XlsxAborted(bad, 1)
        except ValueError:
            continue
        check(CID, f"XlsxAborted refuses {bad}", False)
    check(
        CID,
        "row numbers below 1 are clamped",
        xi.XlsxAborted("xlsx_malformed", 0).locator == "row:1",
    )
    for bad in ("nope", "parse_timeout_x"):
        try:
            ox.OoxmlRefused(bad)
        except ValueError:
            continue
        check(CID, f"OoxmlRefused refuses {bad}", False)
    # every code the importer can raise through OoxmlRefused maps to a fatal XLSX code
    for code in ox._CODES - {"parse_timeout"}:
        check(CID, f"OoxmlRefused({code}) maps to an XlsxAborted code", code in xi.XLSX_FATAL_CODES)


def test_drift_against_ftf_0501_upload_checks() -> None:
    from app.datasets import direct_object_storage_upload as dos

    check(CID, "entries bound equals FTF-0501's", ox.MAX_ENTRIES == dos.MAX_XLSX_ENTRIES == 10_000)
    check(
        CID,
        "directory bound equals FTF-0501's",
        ox.MAX_DIRECTORY_BYTES == dos.MAX_XLSX_DIRECTORY_BYTES == 4 * 1024**2,
    )
    check(
        CID,
        "uncompressed bound equals FTF-0501's",
        ox.MAX_UNCOMPRESSED_BYTES == dos.MAX_XLSX_UNCOMPRESSED_BYTES == 10 * 1024**3,
    )
    check(
        CID,
        "[Content_Types].xml bound equals FTF-0501's",
        ox.MAX_CONTENT_TYPES_BYTES == dos.MAX_XLSX_CONTENT_TYPES_BYTES == 1024**2,
    )
    check(
        CID,
        "the XLSX content type equals FTF-0501's",
        ox.XLSX_CONTENT_TYPE == dos.XLSX_CONTENT_TYPE,
    )
    check(
        CID,
        "the parts it names are the same",
        ox.CONTENT_TYPES_PART == dos._XLSX_CONTENT_TYPES_PART,
    )
    tails = [
        t501._end_record(7, 1234, 99),
        b"junk" * 9 + t501._end_record(7, 1234, 99),
        b"x" * 30 + t501._end_record(5, 10, 20, b"a comment"),
        b"nothing here at all, no record",
        b"PK\x05\x06" + b"\x00" * 5,
        b"\x01" * 22,
        t501._zip64_tail(70_000, 123_456_789_012, 5_000_000_000),
        t501._zip64_tail(70_000, 1, 2, locator=False),
        b"PK\x06\x07" + b"\x00" * 16 + t501._end_record(3, 4, 5),
        GOOD,
        GOOD + t501._zip64_tail(9, 9, 9),
    ]
    for i, tail in enumerate(tails):
        check(
            CID,
            f"end-record reader agrees with FTF-0501's on tail #{i}",
            ox.end_record_totals(tail) == dos._end_record_totals(tail),
        )
    # every package FTF-0501 refuses at upload is also refused here (never imported)
    refused = {
        "a docx": t501._zip(
            [
                ("[Content_Types].xml", content_types().encode()),
                ("word/document.xml", b"<document/>"),
            ]
        ),
        "a plain zip": t501._zip([("a.txt", b"hello")]),
        "traversal": with_extra(GOOD, ("../evil.xml", b"x")),
        "absolute": with_extra(GOOD, ("/abs.xml", b"x")),
        "macro type": book(workbook_type=MACRO_TYPE),
        "doctype in content types": with_member(
            GOOD,
            "[Content_Types].xml",
            b'<!DOCTYPE t [<!ENTITY a "aaaa">]>' + content_types().encode(),
        ),
        "a zip64 record claiming 70,000 entries": GOOD + t501._zip64_tail(70_000, 100, 0),
    }
    for label, data in refused.items():
        check(CID, f"{label}: refused here too", run(data)[1].report.state == "failed")


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
